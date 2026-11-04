"""简历上传。服务器生成文件名；失败也保留原字节。"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from jobbot.config import load_config
from jobbot.contracts import MAX_TEXT_CHARS, JobBotError, ParseError
from jobbot.db.repository import get_by_id, insert, list_rows
from jobbot.profile._db import open_db
from jobbot.profile.text_extract import MAX_PDF_PAGES, extract_text

logger = logging.getLogger("jobbot.profile")

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
ALLOWED_SUFFIXES = {".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".txt": "text/plain", ".md": "text/markdown"}

_PROBLEM_TEXT = {
    "disguised": "文件扩展名和内容对不上，按伪装扩展名拒绝。没有抽取事实。",
    "corrupt": "文件已损坏，无法读取正文。没有抽取事实。",
    "scanned": "这是扫描件或没有可提取文字的 PDF，暂不支持。没有编造正文，也没有抽取事实。",
    "too_many_pages": f"PDF 页数超过 {MAX_PDF_PAGES} 页，已拒绝。没有抽取事实。",
    "timeout": "解析超时，已停止。没有抽取事实。",
    "extension": "扩展名不在 pdf、docx、txt、md 之内。没有打开文件内容去执行。",
}


class StoredDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    path: str
    sha256: str
    parse_status: str
    parse_error: str | None = None
    text: str | None = None
    size: int | None = None


def list_documents() -> list[StoredDocument]:
    with open_db() as connection:
        rows = [row for row in list_rows(connection, "document") if row["kind"] == "resume"]
    return [_document_from_row(row) for row in rows]


def get_document(document_id: str) -> StoredDocument:
    with open_db() as connection:
        row = get_by_id(connection, "document", document_id)
    if row is None:
        raise JobBotError(
            f"找不到简历文档 {document_id}。没有读取到正文。",
            saved=False,
            next_step="重新上传，或从列表里选择仍在的文档。",
        )
    return _document_from_row(row)


def ingest_resume(filename: str, data: bytes) -> StoredDocument:
    settings = load_config()
    suffix = _suffix(filename)
    stored_suffix = suffix if suffix in ALLOWED_SUFFIXES else ".bin"
    directory = settings.data_dir / "raw" / "resumes"
    directory.mkdir(parents=True, exist_ok=True)
    stored_path = directory / f"{uuid4().hex}{stored_suffix}"
    stored_path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    logger.info("resume_saved bytes=%s suffix=%s", len(data), stored_suffix)

    with open_db() as connection:
        existing = _find_sha(connection, digest)
        if existing is not None:
            raise JobBotError(
                f"这份文件的 sha256 与已有文档 {existing['id']} 相同。新副本已另存，原记录和原文件都没有覆盖。没有再次抽取。",
                saved=True,
                next_step="继续使用已有文档，或上传内容不同的文件。",
            )
        problem, detail, text = _inspect(suffix, data)
        if problem is not None:
            parse_error = detail or _PROBLEM_TEXT.get(problem, "无法读取这份简历。")
            document_id = insert(
                connection,
                "document",
                {
                    "kind": "resume",
                    "path": str(stored_path),
                    "sha256": digest,
                    "mime": ALLOWED_SUFFIXES.get(suffix),
                    "size": len(data),
                    "text": None,
                    "parse_status": "failed",
                    "parse_error": parse_error,
                },
            )
            raise ParseError(
                f"{parse_error} 服务器文件名为 {stored_path.name}。",
                saved=True,
                next_step="换一份可选中文字的 pdf、docx、txt 或 md。扫描件请先转成文字后再上传。",
            )
        assert text is not None
        if len(text) > MAX_TEXT_CHARS:
            insert(
                connection,
                "document",
                {
                    "kind": "resume",
                    "path": str(stored_path),
                    "sha256": digest,
                    "mime": ALLOWED_SUFFIXES[suffix],
                    "size": len(data),
                    "text": None,
                    "parse_status": "failed",
                    "parse_error": f"正文长度为 {len(text)}，超过 {MAX_TEXT_CHARS} 字符。已拒绝，没有截断。",
                },
            )
            raise ParseError(
                f"正文有 {len(text)} 个字符，超过 {MAX_TEXT_CHARS}。原文已保留，没有截断，也没有抽取事实。",
                saved=True,
                next_step="把简历拆短到 10 万字符以内后再上传。",
            )
        document_id = insert(
            connection,
            "document",
            {
                "kind": "resume",
                "path": str(stored_path),
                "sha256": digest,
                "mime": ALLOWED_SUFFIXES[suffix],
                "size": len(data),
                "text": text,
                "parse_status": "ok",
                "parse_error": None,
            },
        )
        row = get_by_id(connection, "document", document_id)
    if row is None:
        raise JobBotError(
            "简历写入数据库后立刻读不到。磁盘上的原件还在。",
            saved=True,
            next_step="查看数据目录里的 raw/resumes，然后重试。",
        )
    return _document_from_row(row)


def _suffix(filename: str) -> str:
    name = Path(filename).name
    return Path(name).suffix.lower()


def _inspect(suffix: str, data: bytes) -> tuple[str | None, str, str | None]:
    if len(data) == 0:
        return "corrupt", "文件是空的。没有抽取事实。", None
    if len(data) > MAX_UPLOAD_BYTES:
        return "oversize", f"文件大小为 {len(data)} 字节，超过 20MB。已拒绝，没有抽取事实。", None
    if suffix not in ALLOWED_SUFFIXES:
        return "extension", f"不允许的扩展名 {suffix or '（无）'}。没有抽取事实。", None
    extracted = extract_text(suffix, data)
    if extracted.problem:
        detail = extracted.detail or _PROBLEM_TEXT.get(extracted.problem, "无法读取")
        if extracted.problem == "disguised":
            detail = f"这是伪装扩展名。{detail} 没有抽取事实。"
        elif "没有抽取" not in detail and "暂不支持" not in detail:
            detail = f"{detail} 没有抽取事实。"
        return extracted.problem, detail, None
    return None, "", extracted.text


def _find_sha(connection, digest: str) -> dict | None:
    for row in list_rows(connection, "document"):
        if row["sha256"] == digest:
            return row
    return None


def _document_from_row(row: dict) -> StoredDocument:
    return StoredDocument(
        id=row["id"],
        path=row["path"],
        sha256=row["sha256"],
        parse_status=row["parse_status"],
        parse_error=row["parse_error"],
        text=row["text"],
        size=row["size"],
    )
