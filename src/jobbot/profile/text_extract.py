"""从上传字节提取正文。不调用 OCR，扫描件不编造文字。"""

from __future__ import annotations

import io
import re
import time
import zipfile
import zlib
from dataclasses import dataclass
from xml.etree import ElementTree

MAX_PDF_PAGES = 50
PARSE_TIMEOUT_SECONDS = 5.0
_W_P = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"
_W_T = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
_PAGE_TYPE = re.compile(rb"/Type\s*/Page(?!s)")
_PAGES_TYPE = re.compile(rb"/Type\s*/Pages\b")
_COUNT = re.compile(rb"/Count\s+(\d+)")
_OBJECT = re.compile(rb"(\d+)\s+\d+\s+obj\b(.*?)endobj", re.S)


@dataclass(frozen=True)
class TextExtract:
    text: str | None
    problem: str | None
    detail: str


def extract_text(suffix: str, data: bytes) -> TextExtract:
    started = time.monotonic()
    if suffix == ".pdf":
        return _extract_pdf(data, started)
    if suffix == ".docx":
        return _extract_docx(data)
    if suffix in {".txt", ".md"}:
        return _extract_plain(data)
    return TextExtract(None, "extension", "扩展名不在 pdf、docx、txt、md 之内")


def _extract_plain(data: bytes) -> TextExtract:
    if data.startswith(b"%PDF") or data.startswith(b"PK\x03\x04") or data.startswith(b"\x89PNG"):
        return TextExtract(None, "disguised", "扩展名是文本，内容却是 PDF、压缩包或 PNG")
    if b"\x00" in data[:4096]:
        return TextExtract(None, "disguised", "文本扩展名下含有二进制空字节")
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return TextExtract(data.decode(encoding), None, "")
        except UnicodeDecodeError:
            continue
    return TextExtract(None, "corrupt", "无法用 UTF-8 或 GB18030 解码")


def _extract_docx(data: bytes) -> TextExtract:
    if not data.startswith(b"PK"):
        return TextExtract(None, "disguised", "扩展名是 docx，内容不是 ZIP")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if "word/document.xml" not in archive.namelist():
                return TextExtract(None, "corrupt", "DOCX 里没有 word/document.xml")
            info = archive.getinfo("word/document.xml")
            if info.file_size > 5_000_000:
                return TextExtract(None, "corrupt", "DOCX 正文过大，已停止解压")
            xml_bytes = archive.read("word/document.xml")
    except zipfile.BadZipFile:
        return TextExtract(None, "corrupt", "DOCX 压缩包已损坏")
    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError:
        return TextExtract(None, "corrupt", "DOCX 的 document.xml 无法解析")
    paragraphs: list[str] = []
    for paragraph in root.iter(_W_P):
        parts = [node.text or "" for node in paragraph.iter(_W_T)]
        paragraphs.append("".join(parts))
    return TextExtract("\n".join(paragraphs), None, "")


def _extract_pdf(data: bytes, started: float) -> TextExtract:
    if not data.startswith(b"%PDF"):
        return TextExtract(None, "disguised", "扩展名是 pdf，文件头不是 %PDF")
    objects = _pdf_objects(data)
    if not objects:
        return TextExtract(None, "corrupt", "PDF 里没有可读取的对象")
    page_ids = [object_id for object_id, body in objects.items() if _PAGE_TYPE.search(_dict_part(body))]
    declared = []
    for body in objects.values():
        header = _dict_part(body)
        if _PAGES_TYPE.search(header):
            count = _COUNT.search(header)
            if count:
                declared.append(int(count.group(1)))
    page_count = max([len(page_ids), *declared], default=0)
    if page_count <= 0:
        return TextExtract(None, "corrupt", "PDF 里没有页面")
    if page_count > MAX_PDF_PAGES:
        return TextExtract(
            None,
            "too_many_pages",
            f"PDF 有 {page_count} 页，超过 {MAX_PDF_PAGES} 页",
        )
    if time.monotonic() - started > PARSE_TIMEOUT_SECONDS:
        return TextExtract(None, "timeout", "PDF 解析超时")
    ordered = _ordered_pages(objects, page_ids)
    pages: list[str] = []
    for page_id in ordered:
        if time.monotonic() - started > PARSE_TIMEOUT_SECONDS:
            return TextExtract(None, "timeout", "PDF 解析超时")
        chunks: list[str] = []
        for content_id in _content_ids(objects.get(page_id, b"")):
            stream = _stream_bytes(objects.get(content_id, b""))
            chunks.append(_shown_text(stream))
        pages.append("".join(chunks).strip())
    text = "\n".join(pages).strip()
    if not text:
        if _has_image(objects):
            return TextExtract(None, "scanned", "扫描件暂不支持。页面没有可提取文字，且含有图像。不会编造正文")
        return TextExtract(None, "scanned", "扫描件暂不支持。页面没有可提取文字，不会编造正文")
    return TextExtract(text, None, "")


def _pdf_objects(data: bytes) -> dict[int, bytes]:
    found: dict[int, bytes] = {}
    for match in _OBJECT.finditer(data):
        found[int(match.group(1))] = match.group(2)
    return found


def _dict_part(body: bytes) -> bytes:
    marker = body.find(b"stream")
    if marker < 0:
        return body
    return body[:marker]


def _ordered_pages(objects: dict[int, bytes], page_ids: list[int]) -> list[int]:
    for body in objects.values():
        header = _dict_part(body)
        if not _PAGES_TYPE.search(header):
            continue
        kids = _refs(header, b"/Kids")
        ordered = [kid for kid in kids if kid in page_ids]
        if ordered:
            return ordered
    return sorted(page_ids)


def _content_ids(body: bytes) -> list[int]:
    return _refs(_dict_part(body), b"/Contents")


def _refs(header: bytes, name: bytes) -> list[int]:
    array = re.search(name + rb"\s*\[([^\]]*)\]", header)
    if array:
        return [int(item) for item in re.findall(rb"(\d+)\s+\d+\s+R", array.group(1))]
    single = re.search(name + rb"\s+(\d+)\s+\d+\s+R", header)
    if single:
        return [int(single.group(1))]
    return []


def _has_image(objects: dict[int, bytes]) -> bool:
    for body in objects.values():
        header = _dict_part(body)
        if b"/Image" in header and b"/Subtype" in header:
            return True
    return False


def _stream_bytes(body: bytes) -> bytes:
    marker = body.find(b"stream")
    if marker < 0:
        return b""
    start = marker + len(b"stream")
    if body[start : start + 2] == b"\r\n":
        start += 2
    elif start < len(body) and body[start : start + 1] == b"\n":
        start += 1
    end = body.find(b"endstream", start)
    raw = body[start:] if end < 0 else body[start:end]
    raw = raw.rstrip(b"\r\n")
    header = body[:marker]
    if b"FlateDecode" in header:
        try:
            return zlib.decompress(raw)
        except zlib.error:
            return b""
    return raw


def _shown_text(data: bytes) -> str:
    pieces: list[str] = []
    index = 0
    length = len(data)
    last_string: str | None = None
    last_array: list[str] | None = None
    while index < length:
        index = _skip_ws(data, index)
        if index >= length:
            break
        current = data[index]
        if current == ord("%"):
            while index < length and data[index] not in b"\r\n":
                index += 1
            continue
        if current == ord("("):
            last_string, index = _parse_literal(data, index)
            last_array = None
            continue
        if current == ord("<"):
            if index + 1 < length and data[index + 1] == ord("<"):
                index = _skip_dict(data, index)
                continue
            last_string, index = _parse_hex(data, index)
            last_array = None
            continue
        if current == ord("["):
            last_array, index = _parse_array(data, index)
            last_string = None
            continue
        if current == ord("/"):
            index += 1
            while index < length and data[index] not in b" \t\r\n\x0c[]()<>{}/%":
                index += 1
            continue
        start = index
        while index < length and data[index] not in b" \t\r\n\x0c[]()<>{}/%":
            index += 1
        token = data[start:index]
        if token in {b"Tj", b"'", b'"'} and last_string:
            pieces.append(last_string)
            last_string = None
        elif token == b"TJ" and last_array:
            pieces.extend(last_array)
            last_array = None
    return "".join(pieces)


def _skip_ws(data: bytes, index: int) -> int:
    while index < len(data) and data[index] in b" \t\r\n\x0c\x00":
        index += 1
    return index


def _parse_literal(data: bytes, index: int) -> tuple[str, int]:
    index += 1
    out = bytearray()
    depth = 1
    while index < len(data) and depth:
        current = data[index]
        if current == ord("\\"):
            index += 1
            if index >= len(data):
                break
            escaped = data[index]
            mapped = {
                ord("n"): b"\n",
                ord("r"): b"\r",
                ord("t"): b"\t",
                ord("b"): b"\b",
                ord("f"): b"\f",
                ord("("): b"(",
                ord(")"): b")",
                ord("\\"): b"\\",
            }
            if escaped in mapped:
                out.extend(mapped[escaped])
                index += 1
                continue
            if 48 <= escaped <= 55:
                octal = bytearray([escaped])
                index += 1
                for _ in range(2):
                    if index < len(data) and 48 <= data[index] <= 55:
                        octal.append(data[index])
                        index += 1
                    else:
                        break
                out.append(int(bytes(octal), 8) & 0xFF)
                continue
            if escaped not in b"\r\n":
                out.append(escaped)
            index += 1
            continue
        if current == ord("("):
            depth += 1
            out.append(current)
            index += 1
            continue
        if current == ord(")"):
            depth -= 1
            index += 1
            if depth == 0:
                break
            out.append(current)
            continue
        out.append(current)
        index += 1
    try:
        text = out.decode("utf-8")
    except UnicodeDecodeError:
        text = out.decode("latin-1")
    return text, index


def _parse_hex(data: bytes, index: int) -> tuple[str, int]:
    end = data.find(b">", index + 1)
    if end < 0:
        return "", len(data)
    raw = bytes(char for char in data[index + 1 : end] if chr(char) in "0123456789abcdefABCDEF")
    if len(raw) % 2 == 1:
        raw += b"0"
    try:
        blob = bytes.fromhex(raw.decode("ascii"))
    except ValueError:
        return "", end + 1
    if blob.startswith(b"\xfe\xff"):
        return blob[2:].decode("utf-16-be", errors="replace"), end + 1
    if blob and len(blob) % 2 == 0:
        units = [int.from_bytes(blob[offset : offset + 2], "big") for offset in range(0, len(blob), 2)]
        if units and all(unit > 0x7F for unit in units):
            return blob.decode("utf-16-be", errors="replace"), end + 1
    try:
        return blob.decode("utf-8"), end + 1
    except UnicodeDecodeError:
        return blob.decode("latin-1"), end + 1


def _parse_array(data: bytes, index: int) -> tuple[list[str], int]:
    index += 1
    strings: list[str] = []
    while index < len(data):
        index = _skip_ws(data, index)
        if index >= len(data):
            break
        if data[index] == ord("]"):
            return strings, index + 1
        if data[index] == ord("("):
            text, index = _parse_literal(data, index)
            strings.append(text)
            continue
        if data[index] == ord("<"):
            text, index = _parse_hex(data, index)
            strings.append(text)
            continue
        index += 1
    return strings, index


def _skip_dict(data: bytes, index: int) -> int:
    depth = 0
    while index < len(data):
        if data[index : index + 2] == b"<<":
            depth += 1
            index += 2
            continue
        if data[index : index + 2] == b">>":
            depth -= 1
            index += 2
            if depth <= 0:
                return index
            continue
        index += 1
    return index
