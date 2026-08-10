from __future__ import annotations

import hashlib
import io
import logging
import struct
import threading
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import olefile
from pypdf import PdfReader

from .sources import PoliteHttpClient, RefreshCancelled


logger = logging.getLogger(__name__)

PDF_EMPTY_TEXT = "empty_text"
PDF_TEMP_STALE_DAYS = 1

# Attachment kinds the backfill will download and extract.  Anything else
# (XLS/XLSX, image scans, ...) stays metadata-only and keeps its original link.
SUPPORTED_ATTACHMENT_TYPES = ("PDF", "DOC", "DOCX")

_ACCEPT_BY_TYPE = {
    "PDF": "application/pdf,*/*",
    "DOC": "application/msword,*/*",
    "DOCX": (
        "application/vnd.openxmlformats-officedocument."
        "wordprocessingml.document,*/*"
    ),
}

_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


@dataclass(frozen=True, slots=True)
class PdfExtractionResult:
    """Outcome of one attachment download + extraction attempt.

    ``error`` is ``None`` for a parsed document, :data:`PDF_EMPTY_TEXT` when
    the attachment contains no extractable text, and a message for corrupt or
    unsupported documents.  ``page_count`` is only meaningful for PDFs and may
    be unknown for corrupt documents.  ``format`` mirrors the source
    attachment kind (``pdf``/``doc``/``docx``/...).
    """

    content_hash: str
    text: str
    page_count: int | None
    error: str | None
    format: str = "pdf"


# Kept as an alias for callers that only care about the extraction contract.
AttachmentExtractionResult = PdfExtractionResult


def sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def extract_pdf_text(content: bytes) -> PdfExtractionResult:
    """Extract plain text from PDF bytes and compute the SHA-256 hash.

    Failures (corrupt streams, unsupported encryption) return an error result
    instead of raising; a PDF with no extractable text (scanned/empty
    documents) is reported as :data:`PDF_EMPTY_TEXT`.  The raw document is
    never OCR'd.
    """

    content_hash = sha256_hex(content)
    try:
        reader = PdfReader(io.BytesIO(content))
    except Exception as exc:  # pypdf raises a family of PdfReadError subclasses
        return PdfExtractionResult(content_hash, "", None, str(exc)[:500])
    page_count = None
    try:
        page_count = len(reader.pages)
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                return PdfExtractionResult(
                    content_hash, "", page_count, "加密文档无法读取"
                )
        pages: list[str] = []
        for page in reader.pages:
            try:
                pages.append(page.extract_text() or "")
            except Exception:
                continue
        text = "\n".join(pages).strip()
    except Exception as exc:
        return PdfExtractionResult(content_hash, "", page_count, str(exc)[:500])
    if not text:
        return PdfExtractionResult(content_hash, "", page_count, PDF_EMPTY_TEXT)
    return PdfExtractionResult(content_hash, text, page_count, None)


def extract_docx_text(content: bytes) -> PdfExtractionResult:
    """Extract paragraphs and table cells from a .docx (OOXML) attachment.

    Uses only the standard library (zipfile + ElementTree) so the packaged app
    does not pull in a heavyweight XML dependency.  Table cells are joined
    with `` | `` so downstream structured extraction can keep row semantics.
    """

    content_hash = sha256_hex(content)
    try:
        text = _extract_docx_text_bytes(content)
    except Exception as exc:
        return PdfExtractionResult(
            content_hash, "", None, str(exc)[:500], format="docx"
        )
    if not text:
        return PdfExtractionResult(
            content_hash, "", None, PDF_EMPTY_TEXT, format="docx"
        )
    return PdfExtractionResult(content_hash, text, None, None, format="docx")


def _extract_docx_text_bytes(content: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        if "word/document.xml" not in archive.namelist():
            raise ValueError("缺少 word/document.xml")
        xml = archive.read("word/document.xml")
    root = ET.fromstring(xml)
    body = root.find(_W_NS + "body")
    if body is None:
        raise ValueError("缺少 body 节点")

    parts: list[str] = []

    def paragraph_text(node: ET.Element) -> str:
        return "".join(item.text or "" for item in node.iter(_W_NS + "t")).strip()

    def table_text(table: ET.Element) -> None:
        for row in table.iter(_W_NS + "tr"):
            cells: list[str] = []
            for cell in row.findall(_W_NS + "tc"):
                lines = [
                    paragraph_text(paragraph)
                    for paragraph in cell.iter(_W_NS + "p")
                ]
                lines = [line for line in lines if line]
                if lines:
                    cells.append(" ".join(lines))
            cells = [cell for cell in cells if cell]
            if cells:
                parts.append(" | ".join(cells))

    def walk(node: ET.Element) -> None:
        for child in node:
            if child.tag == _W_NS + "p":
                text = paragraph_text(child)
                if text:
                    parts.append(text)
            elif child.tag == _W_NS + "tbl":
                table_text(child)
            else:
                walk(child)

    walk(body)
    return "\n".join(parts).strip()


def extract_doc_text(content: bytes) -> PdfExtractionResult:
    """Extract plain text from a legacy Word 97-2003 ``.doc`` attachment.

    The ``WordDocument`` stream and its CLX/piece table are read through the
    pure-Python ``olefile`` package.  Both uncompressed (UTF-16LE) and
    compressed (ANSI code page) pieces are decoded; corrupt structures fail
    closed with an error message instead of producing partial text.
    """

    content_hash = sha256_hex(content)
    try:
        with olefile.OleFileIO(io.BytesIO(content)) as ole:
            if not ole.exists("WordDocument"):
                return PdfExtractionResult(
                    content_hash, "", None, "WordDocument 流缺失", format="doc"
                )
            word = ole.openstream("WordDocument").read()
            table_name = (
                "1Table"
                if ole.exists("1Table")
                else "0Table"
                if ole.exists("0Table")
                else None
            )
            if table_name is None:
                return PdfExtractionResult(
                    content_hash, "", None, "缺少 0Table/1Table 流", format="doc"
                )
            table = ole.openstream(table_name).read()
            text = _doc_piece_table_text(word, table)
    except Exception as exc:
        return PdfExtractionResult(content_hash, "", None, str(exc)[:500], format="doc")
    if not text.strip():
        return PdfExtractionResult(content_hash, "", None, PDF_EMPTY_TEXT, format="doc")
    return PdfExtractionResult(content_hash, text, None, None, format="doc")


def _doc_piece_table_text(word: bytes, table: bytes) -> str:
    """Decode main-document text from the Word FIB + CLX piece table."""

    w_ident = struct.unpack_from("<H", word, 0)[0]
    if w_ident != 0xA5EC:
        raise ValueError("非 Word 文档（wIdent 不匹配）")
    n_fib = struct.unpack_from("<H", word, 2)[0]
    if n_fib < 0x00C1:
        raise ValueError(f"不支持的 Word 版本（nFib=0x{n_fib:04X}）")
    ccp_text = struct.unpack_from("<I", word, 0x4C)[0]
    fc_clx, lcb_clx = struct.unpack_from("<II", word, 0x1A2)
    if lcb_clx <= 0 or fc_clx + lcb_clx > len(table):
        raise ValueError("CLX 越界或缺失")
    clx = table[fc_clx : fc_clx + lcb_clx]

    pos = 0
    plcpcd: bytes | None = None
    while pos < len(clx):
        kind = clx[pos]
        if kind == 0x01:  # Prc (skipped)
            pos += 9
        elif kind == 0x02:  # Pcdt
            lcb = struct.unpack_from("<I", clx, pos + 1)[0]
            plcpcd = clx[pos + 5 : pos + 5 + lcb]
            pos += 5 + lcb
        else:
            break
    if plcpcd is None:
        raise ValueError("CLX 中缺少 Pcdt")
    if len(plcpcd) < 4 or (len(plcpcd) - 4) % 12 != 0:
        raise ValueError("PlcPcd 长度异常")

    piece_count = (len(plcpcd) - 4) // 12
    cps = struct.unpack_from(f"<{piece_count + 1}I", plcpcd, 0)
    pcd_offset = 4 * (piece_count + 1)
    chunks: list[str] = []
    for index in range(piece_count):
        fc = struct.unpack_from("<I", plcpcd, pcd_offset + index * 8 + 2)[0]
        char_count = cps[index + 1] - cps[index]
        if char_count < 0:
            raise ValueError("分片字符数为负")
        if fc & 0x40000000:
            fc &= 0x3FFFFFFF
            if fc + char_count > len(word):
                raise ValueError("压缩分片越界")
            chunks.append(word[fc : fc + char_count].decode("cp936", errors="replace"))
        else:
            if fc + char_count * 2 > len(word):
                raise ValueError("分片越界")
            chunks.append(
                word[fc : fc + char_count * 2].decode("utf-16-le", errors="replace")
            )
    text = "".join(chunks)[:ccp_text]
    # Paragraph marks, cell marks and soft line breaks become newlines so the
    # rest of the pipeline sees the same line semantics as PDF/HTML text.
    text = text.replace("\r", "\n").replace("\x07", "\n").replace("\x0b", "\n")
    return text.strip()


def extract_attachment_text(content: bytes, attachment_type: str | None) -> PdfExtractionResult:
    """Dispatch one attachment's bytes to the right text extractor."""

    kind = (attachment_type or "").upper()
    if kind == "PDF":
        return extract_pdf_text(content)
    if kind == "DOCX":
        return extract_docx_text(content)
    if kind == "DOC":
        return extract_doc_text(content)
    return PdfExtractionResult(
        sha256_hex(content),
        "",
        None,
        f"不支持的附件格式：{attachment_type}",
        format=kind.lower() or "unknown",
    )


def pdf_parse_status(result: PdfExtractionResult) -> tuple[str, str | None]:
    """Map an extraction result onto SourceDocument parse status fields."""

    if result.error is None:
        return "parsed", None
    if result.error == PDF_EMPTY_TEXT:
        label = {"pdf": "PDF", "doc": "DOC", "docx": "DOCX"}.get(
            result.format, "附件"
        )
        return "empty_text", f"{label} 未提取到文本（可能为扫描件或空文档）"
    return "failed", result.error


def fetch_and_extract_attachment(
    client: PoliteHttpClient,
    url: str,
    temp_dir: Path,
    cancel_event: threading.Event,
    attachment_type: str | None = "PDF",
) -> PdfExtractionResult:
    """Download an attachment, hash it and extract text via a temp file.

    The original bytes are only written to ``temp_dir`` and are deleted
    immediately after extraction; failures never leave the raw file behind.
    """

    kind = (attachment_type or "PDF").upper()
    temp_dir.mkdir(parents=True, exist_ok=True)
    accept = _ACCEPT_BY_TYPE.get(kind, "*/*")
    content = client.get_bytes(url, accept=accept)
    if cancel_event.is_set():
        raise RefreshCancelled("刷新已取消")
    content_hash = sha256_hex(content)
    extension = "pdf" if kind == "PDF" else kind.lower() or "bin"
    temp_path = temp_dir / f"{content_hash}.{extension}"
    temp_path.write_bytes(content)
    try:
        return extract_attachment_text(temp_path.read_bytes(), kind)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("无法删除临时附件：%s", temp_path)


def fetch_and_extract_pdf(
    client: PoliteHttpClient,
    url: str,
    temp_dir: Path,
    cancel_event: threading.Event,
) -> PdfExtractionResult:
    """Backwards-compatible wrapper for callers that only handle PDFs."""

    return fetch_and_extract_attachment(client, url, temp_dir, cancel_event, "PDF")


def cleanup_stale_pdf_temp(temp_dir: Path, now: datetime) -> int:
    """Best-effort removal of stale temp attachments left by a crashed run."""

    if not temp_dir.exists():
        return 0
    cutoff = now - timedelta(days=PDF_TEMP_STALE_DAYS)
    removed = 0
    for pattern in ("*.pdf", "*.doc", "*.docx"):
        for path in temp_dir.glob(pattern):
            try:
                if path.stat().st_mtime < cutoff.timestamp():
                    path.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                logger.warning("无法清理临时附件：%s", path)
    return removed
