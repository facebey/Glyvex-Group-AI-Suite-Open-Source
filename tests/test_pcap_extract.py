"""Tests de L7: digester de .pcap (backend/pcap_extract.py) + su enlace en
POST /api/chat/attachments. pcaps sintéticos armados acá: sin Wireshark,
sin red real.
"""

import struct

import pytest

import attachments as attachments_module
import pcap_extract

TS = 1_730_000_000


# ---------------------------------------------------------------------------
# Constructores de pcap sintético
# ---------------------------------------------------------------------------


def global_header(linktype=1, snaplen=65535, magic=b"\xd4\xc3\xb2\xa1"):
    # d4c3b2a1 = little-endian microsecond (el que escribe tcpdump en x86)
    return magic + struct.pack("<HHIIII", 2, 4, 0, 0, snaplen, linktype)


def frame(data, ts_sec=TS, incl_len=None):
    incl = len(data) if incl_len is None else incl_len
    return struct.pack("<IIII", ts_sec, 0, incl, len(data)) + data


def eth(dst=b"\x02" * 6, src=b"\x01" * 6, ethertype=0x0800, body=b""):
    return dst + src + struct.pack(">H", ethertype) + body


def ipv4(src="10.0.0.1", dst="10.0.0.2", proto=6, payload=b"", frag=0x4000):
    octets = [int(p) for p in src.split(".")], [int(p) for p in dst.split(".")]
    total = 20 + len(payload)
    header = (
        bytes([0x45, 0x00]) + struct.pack(">H", total)
        + struct.pack(">HH", 1, frag) + bytes([64, proto]) + b"\x00\x00"
        + bytes(octets[0]) + bytes(octets[1])
    )
    return header + payload


def ipv6(src="2001:db8::1", dst="2001:db8::2", proto=6, payload=b""):
    import ipaddress

    header = (
        bytes([0x60, 0x00, 0x00, 0x00]) + struct.pack(">H", len(payload))
        + bytes([proto, 64])
        + ipaddress.IPv6Address(src).packed + ipaddress.IPv6Address(dst).packed
    )
    return header + payload


def tcp(sport=5000, dport=80, flags=0x02, payload=b""):
    return struct.pack(">HHIIBBHHH", sport, dport, 0, 0, (5 << 4) | 0, flags, 64240, 0, 0) + payload


def udp(sport=5000, dport=53, payload=b""):
    return struct.pack(">HHHH", sport, dport, 8 + len(payload), 0) + payload


def dns_query(*names, compressed_at=None):
    """Header DNS con QDCOUNT = len(names). compressed_at: tras el primer
    nombre, un QNAME que es solo un puntero de compresión."""
    body = struct.pack(">HHHHHH", 1, 0x0100, len(names), 0, 0, 0)
    for name in names:
        for label in name.split("."):
            body += bytes([len(label)]) + label.encode()
        body += b"\x00"
        body += struct.pack(">HH", 1, 1)  # QTYPE=A QCLASS=IN
    if compressed_at is not None:
        body += struct.pack(">HH", 0xC000 | compressed_at, 0) + struct.pack(">HH", 1, 1)
    return body


def dns_pointer_offset(name="glyvex.example.com"):
    """Offset del primer byte del QNAME dentro del body de dns_query()."""
    return 12  # tras el header de 12 bytes


def build(packets, **header_kwargs):
    return global_header(**header_kwargs) + b"".join(packets)


# ---------------------------------------------------------------------------
# extract_pcap: casos de una captura TCP normal
# ---------------------------------------------------------------------------


def test_tcp_capture_digests_flows_and_flags():
    pcap = build([
        frame(eth(body=ipv4(payload=tcp(flags=0x02)))),                      # A→B SYN
        frame(eth(body=ipv4("10.0.0.2", "10.0.0.1", payload=tcp(flags=0x12, sport=80, dport=5000)))),  # B→A SYN-ACK
        frame(eth(body=ipv4(payload=tcp(flags=0x18, payload=b"hola")))),    # A→B PSH-ACK con datos
    ])
    text, note = pcap_extract.extract_pcap(pcap)
    assert "Captura de red (pcap)" in text
    assert "Paquetes leídos: 3" in text
    assert "TCP: 3" in text
    assert "10.0.0.1:5000 ⇄ 10.0.0.2:80" in text
    # SYN + SYN-ACK sin FIN → establecida sin cierre limpio
    assert "conexión establecida sin cierre limpio" in text
    assert "Top hosts" in text


def test_syn_without_answer_is_flagged():
    pcap = build([frame(eth(body=ipv4(payload=tcp(flags=0x02))))])
    text, _ = pcap_extract.extract_pcap(pcap)
    assert "intento de conexión sin respuesta (SYN sin ACK)" in text


def test_rst_is_read_as_rejected():
    pcap = build([
        frame(eth(body=ipv4(payload=tcp(flags=0x02)))),
        frame(eth(body=ipv4("10.0.0.2", "10.0.0.1", payload=tcp(flags=0x04, sport=80, dport=5000)))),
    ])
    text, _ = pcap_extract.extract_pcap(pcap)
    assert "conexión rechazada (RST)" in text


def test_http_plaintext_is_quoted_as_sample():
    request = b"GET /index.html HTTP/1.1\r\nHost: local.test\r\n\r\n"
    pcap = build([frame(eth(body=ipv4(payload=tcp(flags=0x18, payload=request))))])
    text, _ = pcap_extract.extract_pcap(pcap)
    assert "GET /index.html HTTP/1.1" in text
    assert "HTTP" in text


# ---------------------------------------------------------------------------
# Capas y variantes
# ---------------------------------------------------------------------------


def test_vlan_tagged_frames_are_unwrapped():
    inner = eth(body=ipv4(payload=tcp()))
    # 802.1Q real: dst+src + 0x8100 + TCI + ethertype interno (0x0800) + body
    tagged = inner[:12] + struct.pack(">HH", 0x8100, 100) + inner[12:]
    pcap = build([frame(tagged)])
    text, _ = pcap_extract.extract_pcap(pcap)
    assert "10.0.0.1:5000 ⇄ 10.0.0.2:80" in text


def test_ipv6_capture_uses_compressed_addresses():
    pcap = build([frame(eth(body=ipv6(payload=tcp())), )])
    text, _ = pcap_extract.extract_pcap(pcap)
    assert "IPv6" in text
    assert "2001:db8::1" in text


def test_dns_query_names_appear_in_digest():
    body = dns_query("glyvex.example.com")
    pcap = build([frame(eth(body=ipv4(proto=17, payload=udp(payload=body))))])
    text, _ = pcap_extract.extract_pcap(pcap)
    assert "glyvex.example.com" in text
    assert "DNS" in text or "UDP" in text


def test_dns_compressed_pointer_name_is_followed():
    offset = dns_pointer_offset()
    body = dns_query("glyvex.example.com", compressed_at=offset)
    # QDCOUNT debe ser 2: dns_query lo pone por len(names); lo parcheamos.
    body = body[:4] + struct.pack(">H", 2) + body[6:]
    pcap = build([frame(eth(body=ipv4(proto=17, payload=udp(payload=body))))])
    text, _ = pcap_extract.extract_pcap(pcap)
    assert text.count("glyvex.example.com (2 consultas)") == 1


def test_arp_is_counted_as_protocol():
    arp_payload = b"\x00\x01\x08\x00\x06\x04\x00\x01" + b"\x01" * 6 + b"\x0a\x00\x00\x01" + b"\x00" * 6 + b"\x0a\x00\x00\x02"
    pcap = build([frame(eth(ethertype=0x0806, body=arp_payload))])
    text, _ = pcap_extract.extract_pcap(pcap)
    assert "ARP: 1" in text


def test_truncated_snaplen_is_reported():
    data = eth(body=ipv4(payload=tcp(payload=b"x" * 100)))
    pcap = global_header(snaplen=40) + frame(data, incl_len=40)
    text, _ = pcap_extract.extract_pcap(pcap)
    assert "truncado por snaplen" in text


def test_unusual_linklayer_digests_with_note_not_error():
    # linktype 12 (ATM RFC-1483): no lo desglosamos, pero no es un error.
    pcap = build([frame(b"\x00" * 8)], linktype=12)
    text, _ = pcap_extract.extract_pcap(pcap)
    assert "sin análisis de red" in text


def test_packet_cap_reports_and_stops(monkeypatch):
    monkeypatch.setattr(pcap_extract, "MAX_PACKETS", 5)
    pcap = build([frame(eth(body=ipv4(payload=tcp(flags=0x18)))) for _ in range(10)])
    text, _ = pcap_extract.extract_pcap(pcap)
    assert "tope de análisis: 5" in text


def test_big_endian_magic_is_accepted():
    data = eth(body=ipv4(payload=tcp()))
    pcap = global_header(magic=b"\xa1\xb2\xc3\xd4") + struct.pack(">IIII", TS, 0, len(data), len(data)) + data
    text, _ = pcap_extract.extract_pcap(pcap)
    assert "Paquetes leídos: 1" in text


# ---------------------------------------------------------------------------
# Errores de formato (los que attachments.py traduce a descriptor explicativo)
# ---------------------------------------------------------------------------


def test_bad_magic_raises():
    with pytest.raises(ValueError, match="no es un pcap"):
        pcap_extract.extract_pcap(b"NOT A PCAP" + b"\x00" * 64)


def test_pcapng_magic_raises_special_case():
    with pytest.raises(ValueError, match="pcapng"):
        pcap_extract.extract_pcap(b"\x0a\x0d\x0d\x0a" + b"\x00" * 64)


def test_header_too_short_raises():
    with pytest.raises(ValueError, match="más corto"):
        pcap_extract.extract_pcap(b"\xd4\xc3\xb2\xa1" + b"\x00" * 8)


def test_empty_capture_raises():
    with pytest.raises(ValueError, match="ningún paquete"):
        pcap_extract.extract_pcap(global_header())


# ---------------------------------------------------------------------------
# Integración: POST /api/chat/attachments
# ---------------------------------------------------------------------------


async def test_chat_attachment_upload_digests_pcap(client):
    pcap = build([
        frame(eth(body=ipv4(payload=tcp(flags=0x02)))),
        frame(eth(body=ipv4(payload=tcp(flags=0x18, payload=b"GET /ping HTTP/1.1\r\n\r\n")))),
    ])
    res = await client.post(
        "/api/chat/attachments",
        files={"files": ("captura.pcap", pcap, "application/vnd.tcpdump.pcap")},
    )
    assert res.status_code == 200
    attachment = res.json()["attachments"][0]
    assert attachment["kind"] == "text"
    assert attachment["status"] == "ready"
    assert "Captura de red (pcap)" in attachment["text"]
    assert "GET /ping HTTP/1.1" in attachment["text"]
    assert attachment["tokens_estimate"] > 0


async def test_chat_attachment_pcapng_is_explained_not_error(client):
    pcapng = b"\x0a\x0d\x0d\x0a" + b"\x00" * 120
    res = await client.post(
        "/api/chat/attachments",
        files={"files": ("captura.pcapng", pcapng, "application/octet-stream")},
    )
    assert res.status_code == 200
    attachment = res.json()["attachments"][0]
    assert attachment["kind"] == "unsupported"
    assert attachment["status"] == "ready"
    assert not attachment["error"]
    assert "PCAPNG" in attachment["text"]


async def test_chat_attachment_extension_is_optional_when_magic_matches(client):
    # Un pcap renombrado sin extensión se detecta igual por magic bytes.
    pcap = build([frame(eth(body=ipv4(payload=tcp())))])
    res = await client.post(
        "/api/chat/attachments",
        files={"files": ("volcado.bin", pcap, "application/octet-stream")},
    )
    attachment = res.json()["attachments"][0]
    assert attachment["kind"] == "text"
    assert "Captura de red (pcap)" in attachment["text"]


async def test_chat_attachment_broken_pcap_explains(client):
    res = await client.post(
        "/api/chat/attachments",
        files={"files": ("roto.pcap", b"\xd4\xc3\xb2\xa1" + b"\x00" * 8, "application/vnd.tcpdump.pcap")},
    )
    attachment = res.json()["attachments"][0]
    assert attachment["kind"] == "unsupported"
    assert "no se pudo leer como pcap" in attachment["text"]
