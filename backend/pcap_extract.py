"""pcap_extract.py — L7: lectura de capturas .pcap para el Chat (M3).

Convierte una captura libpcap/clásica en un DIGEST de texto que el modelo
puede razonar: contagem de protocolos, top conversaciones con señales TCP
(handshake incompleto, RST/FIN), DNS consultado y ejemplos HTTP. No es un
analizador de red: no reensambla streams ni descifra TLS — da lo que un
humano lee primero en Wireshark (estadísticas + flows + qué nombres se
pidieron), sin dependencias externas (ni scapy, ni tshark).

Los datos raros NO son error: un pcap truncado, con snaplen corto o con
link-layer raro se digesta con lo que se pudo leer y una nota lo dice.
Quien decide si alcanza o hay que re-capturar es el modelo, igual que con
los otros adjuntos.

Formatos: pcap clásico (magia A1B2C3D4 y sus variantes de endianness y
nanosegundos). PCAPNG (magia 0A0D0D0A) se detecta y se explica: pedir la
exportación en .pcap.
"""

from __future__ import annotations

import struct
from collections import Counter
from datetime import datetime, timezone
from typing import Any

# Capes del digest: un pcap de 16 MB puede tener cientos de miles de
# paquetes; el trabajo y el texto deben quedar acotados aunque el archivo
# sea grande.
MAX_PACKETS = 50_000
MAX_FLOWS = 20
MAX_HOSTS = 15
MAX_DNS_NAMES = 80
MAX_HTTP_SAMPLES = 15
MAX_LINE_SAMPLES = 25  # paquetes no-IP que no entran en ningún flow

PCAP_MAGICS: dict[bytes, tuple[str, str]] = {
    b"\xa1\xb2\xc3\xd4": ("microseconds", "big"),
    b"\xd4\xc3\xb2\xa1": ("microseconds", "little"),
    b"\xa1\xb2\x3c\x4d": ("nanoseconds", "big"),
    b"\x4d\x3c\xb2\xa1": ("nanoseconds", "little"),
}
PCAPNG_MAGIC = b"\x0a\x0d\x0d\x0a"

# Link-layer types que se desglosan; los demás se digestan como "enlace no
# interpretado" con la métrica de red perdida, no como error.
LINK_ETHERNET = {0, 1, 113}  # BSD loop, Ethernet, Linux SLL
LINK_RAW = {101, 102}  # raw IPv4 / IPv6 sin capa 2

ETHER_IPV4 = 0x0800
ETHER_IPV6 = 0x86DD
ETHER_ARP = 0x0806
ETHER_VLAN = {0x8100, 0x88A8, 0x9100}

PROTO_NAMES = {
    1: "ICMP",
    2: "IGMP",
    6: "TCP",
    17: "UDP",
    41: "IPv6-encap",
    47: "GRE",
    50: "ESP",
    51: "AH",
    58: "ICMPv6",
    132: "SCTP",
}

DNS_PAYLOAD_PORTS = {53, 5353}
HTTP_PORTS = {80, 8080, 8000, 5000, 3000, 8443}
HTTP_METHODS = (b"GET ", b"POST ", b"PUT ", b"HEAD ", b"DELETE ", b"OPTIONS ", b"PATCH ", b"TRACE ", b"CONNECT ")

TCP_FLAG_LETTERS = (
    (0x01, "F"),  # FIN
    (0x02, "S"),  # SYN
    (0x04, "R"),  # RST
    (0x08, "P"),  # PSH
    (0x10, "A"),  # ACK
    (0x20, "U"),  # URG
)


# ---------------------------------------------------------------------------
# Cabecera global
# ---------------------------------------------------------------------------


def _parse_global(raw: bytes) -> dict[str, Any]:
    """Cabecera de 24 bytes. Lanza ValueError solo si el formato no es pcap."""
    if len(raw) < 24:
        raise ValueError("el archivo es más corto que la cabecera de un pcap")
    magic = raw[:4]
    if magic == PCAPNG_MAGIC:
        raise ValueError("pcapng")
    if magic not in PCAP_MAGICS:
        raise ValueError("formato no reconocido (no es un pcap libpcap clásico)")
    timeunit, endian = PCAP_MAGICS[magic]
    fmt = ">IHHIIII" if endian == "big" else "<IHHIIII"
    _, major, minor, _thiszone, _sigfigs, snaplen, linktype = struct.unpack(fmt, raw[:24])
    return {
        "timeunit": timeunit,
        "endian": endian,
        "version": f"{major}.{minor}",
        "snaplen": snaplen,
        "linktype": linktype,
    }


# ---------------------------------------------------------------------------
# Capa 2 → IP
# ---------------------------------------------------------------------------


def _ethertype(payload: bytes) -> tuple[int | None, bytes]:
    """Devuelve (ethertype, payload-network). Salta VLANs encadenadas."""
    offset = 12  # en Ethernet el ethertype vive tras los dos MAC
    for _ in range(4):
        if len(payload) < offset + 2:
            return None, b""
        ethertype = struct.unpack(">H", payload[offset:offset + 2])[0]
        body = payload[offset + 2:]
        if ethertype in ETHER_VLAN:
            if len(body) < 2:
                return None, b""
            # tras el TCI (2 bytes) sigue el ethertype interno, sin MAC de nuevo
            payload, offset = body[2:], 0
            continue
        return ethertype, body
    return None, b""


def _net_offset(linktype: int, packet: bytes) -> tuple[str, bytes]:
    """(etiqueta-enlace, bytes desde la cabecera de red)."""
    if linktype in LINK_RAW:
        version = (packet[0] >> 4) if packet else 0
        return ("raw", packet) if version in (4, 6) else ("raw", b"")
    if linktype == 0:  # BSD loopback: 4 bytes de AF
        if len(packet) < 4:
            return ("loop", b"")
        af = struct.unpack(">I", packet[:4])[0]
        return ("loop", packet[4:]) if af in (2, 24, 10) else ("loop", b"")
    if linktype == 113:  # Linux SLL: 14 bytes, protocol al final
        if len(packet) < 14:
            return ("sll", b"")
        return ("sll", packet[14:])
    if linktype != 1:
        # capa de enlace que no interpretamos (ATM, WiFi con FCS, etc.): se
        # cuenta y se nombra, no se fuerza como Ethernet
        return (f"linktype {linktype}", b"")
    ethertype, body = _ethertype(packet)
    if ethertype is None:
        return ("eth", b"")
    if ethertype == ETHER_ARP:
        return ("arp", b"")
    if ethertype not in (ETHER_IPV4, ETHER_IPV6):
        return (f"eth:0x{ethertype:04x}", b"")
    return ("eth", body)


# ---------------------------------------------------------------------------
# Capa 3
# ---------------------------------------------------------------------------


def _parse_ip(data: bytes) -> dict[str, Any] | None:
    if not data:
        return None
    version = data[0] >> 4
    if version == 4:
        if len(data) < 20:
            return None
        ihl = (data[0] & 0x0F) * 4
        if ihl < 20 or len(data) < ihl:
            return None
        total_len = struct.unpack(">H", data[2:4])[0]
        frag = struct.unpack(">H", data[6:8])[0]
        proto = data[9]
        src = ".".join(str(b) for b in data[12:16])
        dst = ".".join(str(b) for b in data[16:20])
        first_fragment = (frag & 0x1FFF) == 0
        fragmented = bool(frag & 0x1FFF) or bool(frag & 0x2000)
        payload = data[ihl:total_len] if total_len >= ihl else data[ihl:]
        return {
            "family": "ip",
            "proto": proto,
            "src": src,
            "dst": dst,
            "l3": "IPv4",
            "payload": payload,
            "first_fragment": first_fragment,
            "fragmented": fragmented,
        }
    if version == 6:
        if len(data) < 40:
            return None
        proto = data[6]
        src = _fmt_ipv6(data[8:24])
        dst = _fmt_ipv6(data[24:40])
        # Extensión de cabeceras: chain corta y con tope para no ciclar.
        offset = 40
        for _ in range(4):
            if proto in (0, 43, 60):  # hop-by-hop, routing, dst options
                if len(data) < offset + 2:
                    return None
                nxt, ext_len = data[offset], (data[offset + 1] + 1) * 8
                proto, offset = nxt, offset + ext_len
                continue
            break
        return {
            "family": "ip",
            "proto": proto,
            "src": src,
            "dst": dst,
            "l3": "IPv6",
            "payload": data[offset:],
            "first_fragment": True,
            "fragmented": False,
        }
    return None


def _fmt_ipv6(raw: bytes) -> str:
    import ipaddress

    return str(ipaddress.IPv6Address(raw))


# ---------------------------------------------------------------------------
# Capa 4 y heurísticas de aplicación
# ---------------------------------------------------------------------------


def _tcp_info(payload: bytes) -> dict[str, Any] | None:
    if len(payload) < 20:
        return None
    sport, dport = struct.unpack(">HH", payload[:4])
    flags = payload[13]
    data_off = (payload[12] >> 4) * 4
    body = payload[data_off:] if data_off >= 20 else b""
    letters = "".join(ch for bit, ch in TCP_FLAG_LETTERS if flags & bit)
    return {
        "proto": "TCP",
        "sport": sport,
        "dport": dport,
        "flags": letters or "-",
        "payload_len": len(body),
        "body": body,
    }


def _udp_info(payload: bytes) -> dict[str, Any] | None:
    if len(payload) < 8:
        return None
    sport, dport, length, _ = struct.unpack(">HHHH", payload[:8])
    body = payload[8:length] if 8 <= length <= len(payload) else payload[8:]
    return {
        "proto": "UDP",
        "sport": sport,
        "dport": dport,
        "flags": "",
        "payload_len": len(body),
        "body": body,
    }


def _parse_dns_name(body: bytes, offset: int) -> tuple[str | None, int]:
    """QNAME con compresión (punteros 0xC0). Devuelve (nombre, offset_final)."""
    labels: list[str] = []
    jumps = 0
    pos = offset
    while pos < len(body):
        length = body[pos]
        if length == 0:
            return (".".join(labels) if labels else None, pos + 1)
        if length & 0xC0 == 0xC0:
            if pos + 1 >= len(body) or jumps >= 8:
                return (None, pos)
            pointer = struct.unpack(">H", body[pos:pos + 2])[0] & 0x3FFF
            if pointer == pos:  # puntero a sí mismo: bucle
                return (None, pos)
            pos = pointer
            jumps += 1
            continue
        if length > 63:
            return (None, pos)
        pos += 1
        if pos + length > len(body):
            return (None, pos)
        label = body[pos:pos + length]
        try:
            labels.append(label.decode("ascii"))
        except UnicodeDecodeError:
            labels.append(label.decode("ascii", "replace"))
        pos += length
    return (None, pos)


def _dns_questions(body: bytes) -> list[str]:
    """Nombres del section de pregunta. Un header DNS son 12 bytes."""
    if len(body) < 12:
        return []
    qdcount = struct.unpack(">H", body[4:6])[0]
    names: list[str] = []
    offset = 12
    for _ in range(min(qdcount, 4)):
        name, offset = _parse_dns_name(body, offset)
        if not name:
            break
        names.append(name)
        offset += 4  # QTYPE + QCLASS tras cada QNAME
    return names


def _http_line(body: bytes) -> str | None:
    if not body.startswith(HTTP_METHODS):
        return None
    head = body[:512].split(b"\r\n", 1)[0]
    return head.decode("latin-1", "replace")


# ---------------------------------------------------------------------------
# Digest
# ---------------------------------------------------------------------------


def _iter_packets(raw: bytes, header: dict[str, Any]):
    endian = "<" if header["endian"] == "little" else ">"
    offset = 24
    while offset + 16 <= len(raw):
        ts_sec, ts_frac, incl_len, orig_len = struct.unpack(f"{endian}IIII", raw[offset:offset + 16])
        offset += 16
        if incl_len == 0 or offset + incl_len > len(raw):
            break  # truncado al final: se digesta lo completo que había
        yield ts_sec, ts_frac, incl_len, orig_len, raw[offset:offset + incl_len]
        offset += incl_len


def _flow_key(ip: dict[str, Any], l4: dict[str, Any] | None) -> tuple:
    a = (ip["src"], l4["sport"] if l4 else 0)
    b = (ip["dst"], l4["dport"] if l4 else 0)
    return (a, b) if (a[0], a[1]) <= (b[0], b[1]) else (b, a)


def extract_pcap(raw: bytes) -> tuple[str, str | None]:
    """(digest de texto, nota|None). ValueError solo si NO es un pcap leíble."""
    header = _parse_global(raw)
    linktype = header["linktype"]

    notes: list[str] = []
    total = 0
    truncated_packets = 0
    unparsed = 0
    protocols: Counter[str] = Counter()
    flows: dict[tuple, dict[str, Any]] = {}
    hosts: dict[str, dict[str, int]] = {}
    dns_names: Counter[str] = Counter()
    http_samples: list[str] = []
    misc_samples: list[str] = []
    other_link: Counter[str] = Counter()
    first_ts: tuple[int, int] | None = None
    last_ts: tuple[int, int] | None = None
    saw_packet_cap = False

    for ts_sec, ts_frac, incl_len, orig_len, packet in _iter_packets(raw, header):
        total += 1
        if total > MAX_PACKETS:
            saw_packet_cap = True
            total -= 1
            break
        if first_ts is None:
            first_ts = (ts_sec, ts_frac)
        last_ts = (ts_sec, ts_frac)
        if orig_len > incl_len:
            truncated_packets += 1

        link_label, net_data = _net_offset(linktype, packet)
        if link_label not in ("eth", "raw", "loop", "sll", "arp"):
            other_link[link_label] += 1
            continue
        ip = _parse_ip(net_data) if net_data else None
        if ip is None:
            if link_label == "arp":
                protocols["ARP"] += 1
            else:
                unparsed += 1
            continue

        protocols[ip["l3"]] += 1
        if ip["fragmented"] and not ip["first_fragment"]:
            protocols["fragmento"] += 1

        l4: dict[str, Any] | None = None
        proto_name = PROTO_NAMES.get(ip["proto"], f"IP-proto-{ip['proto']}")
        if ip["first_fragment"]:
            if ip["proto"] == 6:
                l4 = _tcp_info(ip["payload"])
            elif ip["proto"] == 17:
                l4 = _udp_info(ip["payload"])
            if l4 is None and ip["proto"] in (6, 17):
                protocols["capa4-truncada"] += 1
        if l4 is not None:
            protocols[l4["proto"]] += 1
            if l4["proto"] == "UDP" and l4["dport"] in DNS_PAYLOAD_PORTS:
                for name in _dns_questions(l4["body"]):
                    dns_names[name.lower()] += 1
            if l4["proto"] == "TCP" and (l4["dport"] in HTTP_PORTS or l4["sport"] in HTTP_PORTS):
                line = _http_line(l4["body"])
                if line and len(http_samples) < MAX_HTTP_SAMPLES:
                    http_samples.append(line)
                    protocols["HTTP"] += 1

        host_src = hosts.setdefault(ip["src"], {"packets": 0, "bytes": 0})
        host_src["packets"] += 1
        host_src["bytes"] += orig_len
        host_dst = hosts.setdefault(ip["dst"], {"packets": 0, "bytes": 0})
        host_dst["packets"] += 1
        host_dst["bytes"] += orig_len

        key = _flow_key(ip, l4)
        flow = flows.setdefault(key, {
            "packets": 0, "bytes": 0, "flags": Counter(), "l3": ip["l3"], "proto": proto_name,
        })
        flow["packets"] += 1
        flow["bytes"] += orig_len
        if l4 is not None and l4["flags"]:
            flow["flags"].update(l4["flags"])
        elif len(misc_samples) < MAX_LINE_SAMPLES:
            misc_samples.append(f"{ip['src']} → {ip['dst']}  {proto_name}")

    if total == 0:
        raise ValueError("el pcap no contiene ningún paquete completo")

    # ---------- armado del digest ----------
    lines: list[str] = []
    link_name = {
        0: "BSD loopback", 1: "Ethernet", 101: "raw IPv4", 102: "raw IPv6", 113: "Linux SLL",
    }.get(linktype, f"linktype {linktype}")
    lines.append("# Captura de red (pcap)")
    if saw_packet_cap:
        lines.append(f"Paquetes leídos: {total} (tope de análisis: {MAX_PACKETS}; el resto no se procesó)")
    else:
        lines.append(f"Paquetes leídos: {total}")
    lines.append(f"Bytes totales (declarados en las cabeceras): {_human_bytes(sum(f['bytes'] for f in flows.values()))}")
    if first_ts and last_ts:
        start = _fmt_ts(first_ts, header["timeunit"])
        end = _fmt_ts(last_ts, header["timeunit"])
        duration = last_ts[0] - first_ts[0]
        lines.append(f"Ventana temporal: {start} → {end} ({_fmt_duration(duration)})")
    lines.append(f"Capa de enlace: {link_name} · versión pcap {header['version']} · snaplen {header['snaplen']}")
    if truncated_packets:
        lines.append(f"Paquetes con payload truncado por snaplen: {truncated_packets}")
    if unparsed:
        lines.append(f"Paquetes sin capa de red interpretable: {unparsed}")
    for label, count in other_link.most_common():
        lines.append(f"Paquetes con {label} (sin análisis de red): {count}")

    lines.append("")
    lines.append("## Protocolos (conteo de paquetes)")
    for name, count in protocols.most_common(20):
        pct = (count / total * 100) if total else 0
        lines.append(f"- {name}: {count} ({pct:.1f}%)")

    lines.append("")
    lines.append("## Top conversaciones (agrupadas por endpoints, bidireccional)")
    top_flows = sorted(flows.items(), key=lambda kv: kv[1]["bytes"], reverse=True)[:MAX_FLOWS]
    for (a, b), flow in top_flows:
        flags = "".join(sorted(flow["flags"])) if flow["flags"] else ""
        signal = _flow_signal(flow["flags"])
        port_note = f" ({flow['proto']})" if flow["proto"] != "unknown" else ""
        lines.append(
            f"- {a[0]}:{a[1]} ⇄ {b[0]}:{b[1]}{port_note} — "
            f"{flow['packets']} pkts, {_human_bytes(flow['bytes'])}"
            + (f", flags TCP [{flags}]" if flags else "")
            + (f" — {signal}" if signal else "")
        )

    lines.append("")
    lines.append("## Top hosts (bytes enviados + recibidos)")
    for host, stats in sorted(hosts.items(), key=lambda kv: kv[1]["bytes"], reverse=True)[:MAX_HOSTS]:
        lines.append(f"- {host}: {_human_bytes(stats['bytes'])} · {stats['packets']} pkts")

    if dns_names:
        lines.append("")
        lines.append("## Nombres consultados (DNS)")
        for name, count in dns_names.most_common(MAX_DNS_NAMES):
            lines.append(f"- {name} ({count} consultas)")

    if http_samples:
        lines.append("")
        lines.append("## Ejemplos de peticiones HTTP (sin descifrar TLS)")
        for line in http_samples:
            lines.append(f"- {line}")

    if misc_samples:
        lines.append("")
        lines.append("Otros paquetes (muestra)")
        lines.extend(f"- {sample}" for sample in misc_samples)

    if header["snaplen"] < 65535 and truncated_packets == 0 and total > 0:
        notes.append(
            "El snaplen declarado es corto: si hace falta analizar payloads, "
            "capturar de nuevo sin límite de bytes por paquete."
        )
    note = " ".join(notes) if notes else None
    return "\n".join(lines), note


def _flow_signal(flags: Counter) -> str:
    """Lectura humana de los flags TCP de un flow."""
    if flags.get("R", 0):
        return "conexión rechazada (RST)"
    if flags.get("S", 0) and not flags.get("A", 0):
        return "intento de conexión sin respuesta (SYN sin ACK)"
    if flags.get("S", 0) and flags.get("A", 0) and not flags.get("F", 0):
        return "conexión establecida sin cierre limpio"
    return ""


def _fmt_ts(ts: tuple[int, int], unit: str) -> str:
    dt = datetime.fromtimestamp(ts[0], tz=timezone.utc)
    frac = ts[1] / (1e9 if unit == "nanoseconds" else 1e6)
    return f"{dt.isoformat(timespec='seconds')} +{frac:.3f}s UTC"


def _fmt_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min"
    return f"{seconds // 3600} h {(seconds % 3600) // 60} min"


def _human_bytes(num: int) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"
