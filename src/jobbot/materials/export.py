"""导出已批准正文。DOCX 用标准库写 Office Open XML，不嵌入字体文件。
PDF 用标准库写最小文件：正文以带 BOM 的 UTF-16BE 放进十六进制字符串，再用 Tj 显示。
不下载、不嵌入字体。汉字留在文件字节里，可从十六进制解回。

字体名：Noto Sans CJK SC。
许可证：SIL Open Font License 1.1。
本轮不打包字体文件。DOCX 汉字以 Unicode 写入 word/document.xml，回读不依赖本机是否安装该字体。
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree

from jobbot.contracts import ApprovalBlocked, JobBotError
from jobbot.materials.store import exports_dir, load_material
from jobbot.materials.verify import claims_block_message, reload_claims

EXPORT_FONT_NAME = "Noto Sans CJK SC"
EXPORT_FONT_LICENSE = "SIL Open Font License 1.1"
_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""

_PACKAGE_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""

_DOCUMENT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>
"""

_PDF_HEADER = b"%PDF-1.4\n"
_PDF_UTF16_BOM = b"\xfe\xff"
_PDF_SHOWN_HEX = re.compile(rb"<([0-9A-Fa-f]+)>\s*Tj")


def export(material_id: str, fmt: Literal["docx", "pdf", "txt"]) -> Path:
    material = load_material(material_id)
    if material.status != "approved":
        raise ApprovalBlocked(
            "材料尚未批准，不能导出终稿。",
            saved=True,
            next_step="先处理校验问题并批准。",
        )
    blocked = claims_block_message(reload_claims(material_id))
    if blocked:
        raise ApprovalBlocked(blocked, saved=True, next_step="修改正文并重新校验后再批准。")
    digest = hashlib.sha256(material.content.encode("utf-8")).hexdigest()
    if digest != material.content_hash:
        raise JobBotError(
            "正文和 content_hash 对不上，没有导出。",
            saved=True,
            next_step="不要使用这份材料，重新生成一个版本。",
        )
    if fmt not in {"docx", "txt", "pdf"}:
        raise JobBotError(
            "导出格式只能是 docx、txt 或 pdf。没有生成文件。",
            saved=True,
            next_step="改用 docx、txt 或 pdf。",
        )
    directory = exports_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{material.content_hash}.{fmt}"
    if fmt == "txt":
        path.write_text(material.content, encoding="utf-8")
        extracted = path.read_text(encoding="utf-8")
    elif fmt == "docx":
        _write_docx(path, material.content)
        extracted = extract_docx_text(path)
    else:
        _write_pdf(path, material.content)
        try:
            extracted = extract_pdf_text(path)
        except (ValueError, UnicodeDecodeError):
            extracted = None
    if extracted != material.content:
        path.unlink(missing_ok=True)
        raise JobBotError(
            "导出的文字与正文不一致，已删除该文件。",
            saved=True,
            next_step="重新导出；若仍失败，不要使用该文件。",
        )
    return path


def extract_docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        xml_bytes = archive.read("word/document.xml")
    root = ElementTree.fromstring(xml_bytes)
    namespace = {"w": _WORD_NS}
    lines: list[str] = []
    for paragraph in root.findall("w:body/w:p", namespace):
        texts = [node.text or "" for node in paragraph.findall(".//w:t", namespace)]
        lines.append("".join(texts))
    return "\n".join(lines)


def _write_docx(path: Path, content: str) -> None:
    paragraphs = "".join(_paragraph(line) for line in content.split("\n"))
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_WORD_NS}">'
        "<w:body>"
        f"{paragraphs}"
        "<w:sectPr>"
        '<w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/>'
        "</w:sectPr>"
        "</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("_rels/.rels", _PACKAGE_RELS)
        archive.writestr("word/_rels/document.xml.rels", _DOCUMENT_RELS)
        archive.writestr("word/document.xml", document.encode("utf-8"))


def _paragraph(line: str) -> str:
    safe = _xml_text(_xml_safe(line))
    font = EXPORT_FONT_NAME
    return (
        "<w:p><w:r><w:rPr>"
        f'<w:rFonts w:ascii="{font}" w:hAnsi="{font}" w:eastAsia="{font}" w:cs="{font}"/>'
        "</w:rPr>"
        f'<w:t xml:space="preserve">{safe}</w:t>'
        "</w:r></w:p>"
    )


def _xml_safe(value: str) -> str:
    return "".join(char for char in value if char in "\t" or ord(char) >= 32)


def _xml_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def extract_pdf_text(path: Path) -> str:
    data = path.read_bytes()
    return "".join(_decode_utf16_hex(match.group(1)) for match in _PDF_SHOWN_HEX.finditer(data))


def _write_pdf(path: Path, content: str) -> None:
    stream = _pdf_content_stream(content)
    length = str(len(stream)).encode("ascii")
    objects = (
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Count 1 /Kids [3 0 R] >> endobj\n",
        b"3 0 obj << /Type /Page /Parent 2 0 R /Contents 4 0 R >> endobj\n",
        b"4 0 obj << /Length " + length + b" >> stream\n" + stream + b"\nendstream endobj\n",
    )
    path.write_bytes(_PDF_HEADER + b"".join(objects) + b"trailer << /Root 1 0 R >>\n%%EOF\n")


def _pdf_content_stream(content: str) -> bytes:
    encoded = _PDF_UTF16_BOM + content.encode("utf-16-be")
    return b"BT <" + encoded.hex().encode("ascii") + b"> Tj ET"


def _decode_utf16_hex(raw: bytes) -> str:
    if len(raw) % 2 != 0:
        raise ValueError("PDF 十六进制长度不是偶数")
    blob = bytes.fromhex(raw.decode("ascii"))
    if not blob.startswith(_PDF_UTF16_BOM):
        raise ValueError("PDF 正文不是带 BOM 的 UTF-16BE")
    return blob[len(_PDF_UTF16_BOM) :].decode("utf-16-be")
