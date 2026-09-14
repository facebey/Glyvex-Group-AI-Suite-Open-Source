"""
attachments.py — Procesamiento de adjuntos del chat (Bloque 1 / módulo M3).

Recibe N archivos en un mismo request multipart y los procesa en paralelo,
devolviendo para cada uno un "descriptor" que el frontend guarda en el
mensaje y que chat.py sabe expandir a content blocks antes de mandar al
upstream.

Criterios de diseño:

- Ningún adjunto se rechaza por tipo. Los formatos que no se pueden leer
  como texto ni como imagen se incluyen igual con su metadata y un bloque
  que se lo explica al modelo: quien declara que no puede leer el archivo
  es el modelo, no el backend.

- Las bibliotecas de extracción (pypdf, python-docx, python-pptx, openpyxl)
  se importan lazy dentro de cada handler. Si falta alguna, ese tipo de
  archivo degrada al bloque de "no interpretable" con la instrucción de
  instalación, en vez de tirar ImportError al arrancar la app.

- La extracción es CPU-bound y bloqueante, así que cada handler corre en
  `asyncio.to_thread`. Sin eso `asyncio.gather` sobre funciones sync sería
  serie disfrazada de paralelo.

- El texto extraído viaja al frontend y se guarda dentro del mensaje: la
  conversación queda self-contained y reabrir el historial no re-procesa
  nada. Las imágenes NO: se escriben a disco en data/attachments/<id> y el
  mensaje solo guarda la referencia, para no meter base64 en la DB.
"""

from __future__ import annotations

import asyncio
import csv
import io
import mimetypes
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from config import config

BASE_DIR = Path(__file__).resolve().parent.parent
ATTACHMENTS_DIR = BASE_DIR / "data" / "attachments"

IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

# Extensiones que se leen como texto plano sin intentar ningún parser.
TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env",
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".c", ".h", ".cpp", ".hpp",
    ".cs", ".go", ".rs", ".rb", ".php", ".swift", ".kt", ".scala", ".lua",
    ".sh", ".bash", ".zsh", ".ps1", ".bat", ".sql", ".r", ".m",
    ".html", ".htm", ".css", ".scss", ".xml", ".svg", ".vue", ".svelte",
    ".dockerfile", ".gitignore", ".editorconfig",
}

# Un PDF con menos de esto por página casi siempre es un escaneo sin OCR.
SCANNED_PDF_CHARS_PER_PAGE = 40

# Filas máximas que se vuelcan a markdown de una planilla o CSV.
MAX_TABLE_ROWS = 200

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _limits() -> dict[str, Any]:
    return {
        "max_file_bytes": int(config.get("attachments.max_file_mb", 16)) * 1024 * 1024,
        "max_files": int(config.get("attachments.max_files_per_message", 10)),
        "max_text_chars": int(config.get("attachments.max_text_chars", 50_000)),
        "image_tokens": int(config.get("attachments.image_tokens_estimate", 1024)),
    }


def _human_size(num: int) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def _guess_mime(filename: str, declared: str | None) -> str:
    if declared and declared not in ("application/octet-stream", ""):
        return declared
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def _is_image(filename: str, mime: str) -> bool:
    return mime in IMAGE_MIMES or Path(filename).suffix.lower() in IMAGE_EXTS


def _decode_text(raw: bytes) -> str:
    """Decodifica probando encodings habituales antes de rendirse a UTF-8 lossy."""
    for encoding in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _looks_binary(raw: bytes) -> bool:
    """Heurística: NUL byte en los primeros KB = no es texto."""
    return b"\x00" in raw[:4096]


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def _rows_to_markdown(rows: list[list[str]]) -> str:
    """Tabla markdown simple. La primera fila se usa como header."""
    if not rows:
        return "_(sin filas)_"
    width = max(len(r) for r in rows)
    padded = [list(r) + [""] * (width - len(r)) for r in rows]
    header = padded[0]
    body = padded[1:]
    out = ["| " + " | ".join(str(c) for c in header) + " |"]
    out.append("|" + "|".join([" --- "] * width) + "|")
    for row in body:
        out.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(out)


def _estimate_tokens(text: str) -> int:
    """
    Estimación local, sin tokenizer: ~3.6 chars por token para castellano y
    código mezclados. chat.py la reemplaza por /tokenize del upstream cuando
    el endpoint lo soporta.
    """
    return max(1, round(len(text) / 3.6))


# ---------------------------------------------------------------------------
# Handlers por tipo (sync — corren en thread)
# ---------------------------------------------------------------------------


def _extract_pdf(raw: bytes) -> tuple[str, str | None]:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise _MissingLib("pypdf", "pip install pypdf")

    reader = PdfReader(io.BytesIO(raw))
    pages = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:  # pypdf tira de todo con PDFs raros
            text = ""
        pages.append(f"[página {index}]\n{text.strip()}")

    body = "\n\n".join(pages).strip()
    total_chars = sum(len(p) for p in pages)
    note = None
    if reader.pages and total_chars / max(1, len(reader.pages)) < SCANNED_PDF_CHARS_PER_PAGE:
        note = (
            "El PDF casi no tiene texto extraíble: probablemente sea un escaneo "
            "sin OCR. Lo que sigue puede estar vacío o incompleto."
        )
    return body, note


def _extract_docx(raw: bytes) -> tuple[str, str | None]:
    try:
        import docx
    except ImportError:
        raise _MissingLib("python-docx", "pip install python-docx")

    document = docx.Document(io.BytesIO(raw))
    parts: list[str] = []

    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        style = (paragraph.style.name or "").lower()
        if style.startswith("heading"):
            level = "".join(ch for ch in style if ch.isdigit()) or "1"
            parts.append("#" * min(6, int(level)) + f" {text}")
        else:
            parts.append(text)

    for index, table in enumerate(document.tables, start=1):
        rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
        parts.append(f"\n**Tabla {index}**\n" + _rows_to_markdown(rows[:MAX_TABLE_ROWS]))

    return "\n\n".join(parts).strip(), None


def _extract_pptx(raw: bytes) -> tuple[str, str | None]:
    try:
        from pptx import Presentation
    except ImportError:
        raise _MissingLib("python-pptx", "pip install python-pptx")

    presentation = Presentation(io.BytesIO(raw))
    slides: list[str] = []
    for index, slide in enumerate(presentation.slides, start=1):
        lines = [f"## Diapositiva {index}"]
        for shape in slide.shapes:
            if shape.has_text_frame:
                text = shape.text_frame.text.strip()
                if text:
                    lines.append(text)
            if getattr(shape, "has_table", False):
                rows = [[cell.text.strip() for cell in row.cells] for row in shape.table.rows]
                lines.append(_rows_to_markdown(rows[:MAX_TABLE_ROWS]))
        if slide.has_notes_slide:
            notes = (slide.notes_slide.notes_text_frame.text or "").strip()
            if notes:
                lines.append(f"_Notas del orador:_ {notes}")
        slides.append("\n\n".join(lines))
    return "\n\n".join(slides).strip(), None


def _extract_xlsx(raw: bytes) -> tuple[str, str | None]:
    try:
        import openpyxl
    except ImportError:
        raise _MissingLib("openpyxl", "pip install openpyxl")

    workbook = openpyxl.load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
    sheets: list[str] = []
    truncated = False

    for worksheet in workbook.worksheets:
        rows: list[list[str]] = []
        for row in worksheet.iter_rows(values_only=True):
            if len(rows) >= MAX_TABLE_ROWS:
                truncated = True
                break
            rows.append(["" if cell is None else str(cell) for cell in row])
        while rows and not any(cell.strip() for cell in rows[-1]):
            rows.pop()
        if not rows:
            continue
        sheets.append(f"## Hoja: {worksheet.title}\n\n" + _rows_to_markdown(rows))

    workbook.close()
    note = (
        f"Se volcaron las primeras {MAX_TABLE_ROWS} filas de cada hoja."
        if truncated
        else None
    )
    return "\n\n".join(sheets).strip() or "_(planilla vacía)_", note


def _extract_csv(raw: bytes) -> tuple[str, str | None]:
    text = _decode_text(raw)
    try:
        dialect = csv.Sniffer().sniff(text[:4096])
    except csv.Error:
        dialect = csv.excel

    reader = csv.reader(io.StringIO(text), dialect)
    rows: list[list[str]] = []
    truncated = False
    for row in reader:
        if len(rows) >= MAX_TABLE_ROWS:
            truncated = True
            break
        rows.append([str(cell) for cell in row])

    note = f"Se volcaron las primeras {MAX_TABLE_ROWS} filas." if truncated else None
    return _rows_to_markdown(rows), note


class _MissingLib(Exception):
    """La biblioteca de extracción para este tipo no está instalada."""

    def __init__(self, package: str, install_hint: str) -> None:
        super().__init__(package)
        self.package = package
        self.install_hint = install_hint


# ---------------------------------------------------------------------------
# Orquestación por archivo
# ---------------------------------------------------------------------------


def _process_sync(filename: str, mime: str, raw: bytes, max_chars: int) -> dict[str, Any]:
    """
    Devuelve el descriptor de un adjunto de texto/binario. Las imágenes no
    pasan por acá (se resuelven en `_process_one`, que sí es async).
    """
    suffix = Path(filename).suffix.lower()
    note: str | None = None

    try:
        if suffix == ".pdf" or mime == "application/pdf":
            text, note = _extract_pdf(raw)
        elif suffix == ".docx":
            text, note = _extract_docx(raw)
        elif suffix == ".pptx":
            text, note = _extract_pptx(raw)
        elif suffix in (".xlsx", ".xlsm", ".xltx"):
            text, note = _extract_xlsx(raw)
        elif suffix in (".csv", ".tsv"):
            text, note = _extract_csv(raw)
        elif suffix in TEXT_EXTS or mime.startswith("text/") or _is_texty_mime(mime):
            if _looks_binary(raw):
                return _unsupported_descriptor(
                    filename, mime, len(raw),
                    reason="tiene contenido binario aunque su extensión sugiera texto",
                )
            text = _decode_text(raw)
        else:
            return _unsupported_descriptor(filename, mime, len(raw))
    except _MissingLib as exc:
        return _unsupported_descriptor(
            filename, mime, len(raw),
            reason=(
                f"el servidor no tiene instalada la biblioteca `{exc.package}` "
                f"necesaria para leerlo"
            ),
            note=f"Instalalo con: {exc.install_hint}",
        )
    except Exception as exc:  # noqa: BLE001 — cualquier parser puede romper
        return {
            "kind": "text",
            "status": "error",
            "text": "",
            "note": None,
            "error": f"No se pudo procesar el archivo: {type(exc).__name__}: {exc}",
        }

    text, was_truncated = _truncate(text.strip(), max_chars)
    if was_truncated:
        suffix_note = f"Contenido truncado a {max_chars:,} caracteres.".replace(",", ".")
        note = f"{note} {suffix_note}".strip() if note else suffix_note

    return {
        "kind": "text",
        "status": "ready",
        "text": text,
        "note": note,
        "error": None,
    }


def _is_texty_mime(mime: str) -> bool:
    return mime in (
        "application/json", "application/xml", "application/x-yaml",
        "application/javascript", "application/x-sh", "application/sql",
    )


def _unsupported_descriptor(
    filename: str,
    mime: str,
    size: int,
    *,
    reason: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """
    Bloque para formatos que no se pueden interpretar. Deliberadamente le
    habla al modelo: la decisión de "no puedo leer esto" es suya.
    """
    tail = f" porque {reason}" if reason else ""
    text = (
        f"El usuario adjuntó `{filename}` ({mime}, {_human_size(size)}) que no pudo "
        f"ser interpretado como texto ni como imagen{tail}."
    )
    return {
        "kind": "unsupported",
        "status": "ready",
        "text": text,
        "note": note,
        "error": None,
    }


async def _process_one(
    upload: UploadFile,
    *,
    vision: bool,
    max_file_bytes: int,
    max_text_chars: int,
    image_tokens: int,
) -> dict[str, Any]:
    filename = Path(upload.filename or "archivo").name
    raw = await upload.read()
    size = len(raw)
    mime = _guess_mime(filename, upload.content_type)

    base: dict[str, Any] = {
        "id": uuid.uuid4().hex,
        "filename": filename,
        "mime": mime,
        "size": size,
        "kind": "text",
        "status": "ready",
        "text": "",
        "note": None,
        "error": None,
        "tokens_estimate": 0,
    }

    if size == 0:
        return {**base, "status": "error", "error": "El archivo está vacío."}

    if size > max_file_bytes:
        return {
            **base,
            "status": "error",
            "error": (
                f"Pesa {_human_size(size)} y el límite es "
                f"{_human_size(max_file_bytes)}."
            ),
        }

    if _is_image(filename, mime):
        if not vision:
            return {
                **base,
                "kind": "image",
                "status": "vision_unsupported",
                "note": "El modelo activo no acepta imágenes, así que no se envía.",
            }
        try:
            await asyncio.to_thread(_write_image, base["id"], raw)
        except OSError as exc:
            return {
                **base,
                "kind": "image",
                "status": "error",
                "error": f"No se pudo guardar la imagen en disco: {exc}",
            }
        return {
            **base,
            "kind": "image",
            "status": "ready",
            "url": f"/api/chat/attachments/{base['id']}/raw",
            "tokens_estimate": image_tokens,
        }

    result = await asyncio.to_thread(_process_sync, filename, mime, raw, max_text_chars)
    merged = {**base, **result}
    merged["tokens_estimate"] = _estimate_tokens(merged.get("text") or "")
    return merged


def _write_image(attachment_id: str, raw: bytes) -> None:
    ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
    (ATTACHMENTS_DIR / attachment_id).write_bytes(raw)


# ---------------------------------------------------------------------------
# Endpoints (montados por chat.py bajo /api/chat)
# ---------------------------------------------------------------------------


@router.post("/attachments")
async def upload_attachments(
    files: list[UploadFile] = File(...),
    vision: bool = Form(False),
) -> dict[str, Any]:
    """
    Procesa una tanda de adjuntos en paralelo.

    `vision` lo manda el frontend según lo que haya respondido
    /api/chat/capabilities para el endpoint activo.
    """
    limits = _limits()

    if len(files) > limits["max_files"]:
        raise HTTPException(
            status_code=413,
            detail=f"Máximo {limits['max_files']} archivos por mensaje.",
        )

    results = await asyncio.gather(
        *(
            _process_one(
                upload,
                vision=vision,
                max_file_bytes=limits["max_file_bytes"],
                max_text_chars=limits["max_text_chars"],
                image_tokens=limits["image_tokens"],
            )
            for upload in files
        ),
        return_exceptions=True,
    )

    descriptors: list[dict[str, Any]] = []
    for upload, result in zip(files, results):
        if isinstance(result, BaseException):
            descriptors.append(
                {
                    "id": uuid.uuid4().hex,
                    "filename": Path(upload.filename or "archivo").name,
                    "mime": upload.content_type or "application/octet-stream",
                    "size": 0,
                    "kind": "text",
                    "status": "error",
                    "text": "",
                    "note": None,
                    "error": f"Falló el procesamiento: {result}",
                    "tokens_estimate": 0,
                }
            )
        else:
            descriptors.append(result)

    return {"attachments": descriptors}


@router.get("/attachments/{attachment_id}/raw")
async def get_attachment_raw(attachment_id: str) -> FileResponse:
    """Sirve la imagen guardada. Solo hex: corta cualquier path traversal."""
    if not attachment_id.isalnum() or len(attachment_id) != 32:
        raise HTTPException(status_code=400, detail="Identificador inválido")

    path = ATTACHMENTS_DIR / attachment_id
    if not path.is_file():
        raise HTTPException(status_code=404, detail="El adjunto ya no está en disco")
    return FileResponse(path)


# ---------------------------------------------------------------------------
# Resolución de referencias para el payload upstream (usado por chat.py)
# ---------------------------------------------------------------------------


def resolve_image_ref(attachment_id: str) -> str | None:
    """
    Devuelve el data URL base64 de una imagen guardada, o None si no está.

    chat.py llama a esto para expandir los bloques {"type": "image_ref"} justo
    antes de mandar al upstream — así el base64 nunca toca la DB ni el
    navegador.
    """
    import base64

    if not attachment_id.isalnum() or len(attachment_id) != 32:
        return None

    path = ATTACHMENTS_DIR / attachment_id
    if not path.is_file():
        return None

    raw = path.read_bytes()
    kind = _sniff_image_mime(raw)
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{kind};base64,{encoded}"


def _sniff_image_mime(raw: bytes) -> str:
    """Mime por magic bytes: el archivo en disco se guarda sin extensión."""
    if raw.startswith(b"\x89PNG"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw.startswith(b"GIF8"):
        return "image/gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def purge_orphans(keep_ids: set[str]) -> int:
    """
    Borra imágenes que ya no referencia ninguna conversación. No se llama
    automáticamente todavía; queda disponible para una tarea de limpieza.
    """
    if not ATTACHMENTS_DIR.is_dir():
        return 0
    removed = 0
    for path in ATTACHMENTS_DIR.iterdir():
        if path.is_file() and path.name not in keep_ids:
            try:
                path.unlink()
                removed += 1
            except OSError:
                continue
    return removed


__all__ = ["router", "resolve_image_ref", "purge_orphans", "ATTACHMENTS_DIR"]
