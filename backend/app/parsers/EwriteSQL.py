"""阶段 E：把 Paper、Section 和多模态 Chunk 在单个事务中写入 PostgreSQL。

重复处理同一 ``paper_uid`` 时会保留 Paper 主键、替换其旧章节与 Chunk，确保数据库中
不会累积重复分片。公式作为带 ``modality=formula`` 元数据的文本 Chunk 保存。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# 支持直接执行 ``python app/parsers/EwriteSQL.py``。
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.infrastructure.postgres_client import PostgresClient, get_postgres_client
from app.repositories import PaperRepository
from app.schemas import (
    ImageChunkCreate,
    Paper,
    PaperCreate,
    PaperUpdate,
    SectionCreate,
    TableChunkCreate,
    TextChunkCreate,
)


@dataclass(frozen=True, slots=True)
class PersistResult:
    """保存一篇文档事务入库后的主键和记录数量。"""

    paper_id: int
    paper_uid: str
    section_count: int
    text_chunk_count: int
    image_chunk_count: int
    table_chunk_count: int

    def to_dict(self) -> dict[str, int | str]:
        """将入库结果转换为 JSON 兼容字典。"""

        return asdict(self)


class DatabaseWriter:
    """提供论文阶段状态更新和整棵文档树的事务写入。"""

    def __init__(self, client: PostgresClient | None = None) -> None:
        """使用显式 PostgreSQL 客户端或项目级默认客户端。"""

        self.client = client or get_postgres_client()
        self.papers = PaperRepository(self.client)

    async def update_paper_stage(
        self,
        paper_uid: str,
        status: str,
        *,
        markdown_path: str | None = None,
        document_json_path: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        error_message: str | None = None,
    ) -> Paper:
        """合并文件元数据并更新 Paper 的阶段状态和产物地址。"""

        paper = await self.papers.get_by_uid(paper_uid)
        if paper is None:
            raise LookupError(f"Paper 记录不存在：{paper_uid}")
        merged_metadata = {**(paper.file_metadata or {}), **dict(metadata or {}), "pipeline_stage": status}
        changes = PaperUpdate(
            ingestion_status=status,
            markdown_path=markdown_path if markdown_path is not None else paper.markdown_path,
            document_json_path=(document_json_path if document_json_path is not None else paper.document_json_path),
            file_metadata=merged_metadata,
            error_message=error_message,
        )
        updated = await self.papers.update(paper.id, changes)
        if updated is None:
            raise RuntimeError(f"Paper 记录在阶段更新前被删除：{paper_uid}")
        return updated

    async def mark_failed(self, paper_uid: str, error: Exception | str) -> Paper:
        """把单篇论文标记为失败，并限制数据库错误文本长度。"""

        message = str(error).strip() or type(error).__name__
        return await self.update_paper_stage(paper_uid, "failed", error_message=message[:4000])

    async def persist_document(
        self,
        document: Any,
        *,
        pdf_path: str | None = None,
        markdown_path: str | None = None,
        document_json_path: str | None = None,
        source_url: str | None = None,
        page_count: int | None = None,
        file_metadata: Mapping[str, Any] | None = None,
    ) -> PersistResult:
        """在一个事务中写入 Paper、章节、Chunk 和文本前后链。"""

        document_data = _document_mapping(document)
        metadata = _mapping(document_data.get("metadata"))
        paper_create = PaperCreate(
            paper_uid=str(document_data.get("paper_uid") or document_data.get("id") or ""),
            title=str(document_data.get("title") or "未命名文档"),
            title_en=_optional_text(metadata.get("title_en")),
            abstract=_optional_text(metadata.get("abstract")),
            authors=_json_list(metadata.get("authors")),
            affiliations=_json_list(metadata.get("affiliations")),
            keywords=_json_list(metadata.get("keywords")),
            doi=_optional_text(metadata.get("doi")),
            journal=_optional_text(metadata.get("journal")),
            volume=_optional_text(metadata.get("volume")),
            issue=_optional_text(metadata.get("issue")),
            publish_year=_optional_int(metadata.get("publish_year")),
            publish_date=metadata.get("publish_date") or None,
            language=_optional_text(metadata.get("language")),
            source_url=source_url,
            pdf_path=pdf_path,
            markdown_path=markdown_path,
            document_json_path=document_json_path,
            ingestion_status="completed",
            error_message=None,
            page_count=page_count,
            file_metadata={**dict(file_metadata or {}), "pipeline_stage": "completed"},
        )

        async with self.client.connection() as connection:
            async with connection.transaction():
                paper_row = await _upsert_paper(connection, paper_create)
                paper_id = int(paper_row["id"])
                # Section 外键级联删除旧 Chunk，使同一论文重跑保持幂等。
                await connection.execute("DELETE FROM section WHERE paper_id = $1", paper_id)
                counts = await _insert_document_records(connection, paper_id, document_data)
        return PersistResult(
            paper_id=paper_id,
            paper_uid=paper_create.paper_uid,
            section_count=counts["section"],
            text_chunk_count=counts["text"],
            image_chunk_count=counts["image"],
            table_chunk_count=counts["table"],
        )


def _document_mapping(document: Any) -> Mapping[str, Any]:
    """兼容 Document 对象、阶段 D 包装对象和裸 document 字典。"""

    if hasattr(document, "to_dict"):
        value = document.to_dict()
    elif isinstance(document, Mapping):
        value = document.get("document", document)
    else:
        raise TypeError("document 必须是 Document 对象或映射")
    if not isinstance(value, Mapping):
        raise ValueError("阶段 D JSON 的 document 字段必须是对象")
    return value


def _mapping(value: Any) -> Mapping[str, Any]:
    """将可空值规范为映射。"""

    return value if isinstance(value, Mapping) else {}


def _items(value: Any) -> list[Any]:
    """将可空单值或序列规范为列表。"""

    if value is None:
        return []
    if isinstance(value, (str, Mapping)):
        return [value]
    return list(value) if isinstance(value, Sequence) else [value]


def _json_list(value: Any) -> list[Any]:
    """将 Paper 的 JSON 数组字段规范为列表。"""

    return [item for item in _items(value) if item is not None]


def _optional_text(value: Any) -> str | None:
    """把空字符串规范为空值，其余值转换为字符串。"""

    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    """把可空数字值转换为整数。"""

    if value in {None, ""}:
        return None
    return int(value)


def _json(value: Any) -> str:
    """把 Python JSON 值编码为 asyncpg 可绑定的字符串。"""

    return json.dumps(value, ensure_ascii=False)


async def _upsert_paper(connection: Any, paper: PaperCreate) -> Mapping[str, Any]:
    """按 paper_uid 幂等新增或更新 Paper，并保留已有非空产物地址。"""

    values = paper.model_dump(mode="python")
    columns = tuple(values)
    json_columns = {"authors", "keywords", "affiliations", "file_metadata"}
    placeholders = [f"${index}{'::jsonb' if column in json_columns else ''}" for index, column in enumerate(columns, 1)]
    update_columns = (
        "title",
        "title_en",
        "abstract",
        "doi",
        "journal",
        "volume",
        "issue",
        "publish_year",
        "publish_date",
        "language",
        "source_url",
        "pdf_path",
        "markdown_path",
        "document_json_path",
        "page_count",
    )
    assignments = [f"{column} = COALESCE(EXCLUDED.{column}, paper.{column})" for column in update_columns]
    assignments.extend(
        [
            "authors = CASE WHEN jsonb_array_length(EXCLUDED.authors) > 0 THEN EXCLUDED.authors ELSE paper.authors END",
            "keywords = CASE WHEN jsonb_array_length(EXCLUDED.keywords) > 0 "
            "THEN EXCLUDED.keywords ELSE paper.keywords END",
            "affiliations = CASE WHEN jsonb_array_length(EXCLUDED.affiliations) > 0 "
            "THEN EXCLUDED.affiliations ELSE paper.affiliations END",
            "file_metadata = COALESCE(paper.file_metadata, '{}'::jsonb) || EXCLUDED.file_metadata",
            "ingestion_status = EXCLUDED.ingestion_status",
            "error_message = EXCLUDED.error_message",
            "updated_at = NOW()",
        ]
    )
    sql = (
        f"INSERT INTO paper ({', '.join(columns)}) VALUES ({', '.join(placeholders)}) "
        f"ON CONFLICT (paper_uid) DO UPDATE SET {', '.join(assignments)} RETURNING *"
    )
    parameters = [_json(values[column]) if column in json_columns else values[column] for column in columns]
    row = await connection.fetchrow(sql, *parameters)
    if row is None:
        raise RuntimeError(f"写入 Paper 后未返回记录：{paper.paper_uid}")
    return row


def _walk_sections(sections: Any, parent_uid: str | None = None) -> Iterator[tuple[Mapping[str, Any], str | None]]:
    """按前序遍历阶段 D 章节，并附带父章节业务 ID。"""

    for value in _items(sections):
        section = _mapping(value)
        current_parent = _optional_text(section.get("parent_uid")) or parent_uid
        yield section, current_parent
        section_uid = str(section.get("section_uid") or "")
        yield from _walk_sections(section.get("children"), section_uid)


async def _insert_document_records(
    connection: Any,
    paper_id: int,
    document: Mapping[str, Any],
) -> dict[str, int]:
    """依次写入章节与多模态 Chunk，并在最后补齐文本前后链。"""

    section_ids: dict[str, int] = {}
    text_rows: list[tuple[int, int]] = []
    counts = {"section": 0, "text": 0, "image": 0, "table": 0}
    flattened = list(_walk_sections(document.get("sections")))
    for raw_section, parent_uid in flattened:
        section_uid = str(raw_section.get("section_uid") or "")
        parent_id = section_ids.get(parent_uid) if parent_uid else None
        section = SectionCreate(
            section_uid=section_uid,
            paper_id=paper_id,
            parent_id=parent_id,
            section_number=_optional_text(raw_section.get("section_number")),
            title=str(raw_section.get("title") or "未命名章节"),
            level=int(raw_section.get("level") or 1),
            sort_order=int(raw_section.get("sort_order") or 0),
            section_path=_json_list(raw_section.get("section_path")),
            raw_text=_optional_text(raw_section.get("raw_text")),
            summary=_optional_text(raw_section.get("summary")),
        )
        section_row = await _insert_section(connection, section)
        section_id = int(section_row["id"])
        section_ids[section_uid] = section_id
        counts["section"] += 1

        text_index = image_index = table_index = 0
        for raw_chunk in sorted(
            (_mapping(item) for item in _items(raw_section.get("chunks"))), key=lambda item: int(item.get("order") or 0)
        ):
            modality = str(raw_chunk.get("modality") or "text")
            if modality in {"text", "formula"}:
                metadata = {**dict(_mapping(raw_chunk.get("metadata"))), "modality": modality}
                text_chunk = TextChunkCreate(
                    chunk_uid=str(raw_chunk.get("chunk_uid") or ""),
                    paper_id=paper_id,
                    section_id=section_id,
                    chunk_index=text_index,
                    content=str(raw_chunk.get("content") or ""),
                    char_count=int(raw_chunk.get("char_count") or len(str(raw_chunk.get("content") or ""))),
                    metadata=metadata,
                )
                row = await _insert_text_chunk(connection, text_chunk)
                text_rows.append((int(row["id"]), section_id))
                text_index += 1
                counts["text"] += 1
            elif modality == "image":
                image_chunk = ImageChunkCreate(
                    chunk_uid=str(raw_chunk.get("chunk_uid") or ""),
                    paper_id=paper_id,
                    section_id=section_id,
                    image_index=image_index,
                    figure_number=_optional_text(raw_chunk.get("figure_number")),
                    image_path=str(raw_chunk.get("image_path") or ""),
                    caption=_optional_text(raw_chunk.get("caption")),
                    image_type=_optional_text(raw_chunk.get("image_type")),
                    description=_optional_text(raw_chunk.get("description")),
                    structured_data=dict(_mapping(raw_chunk.get("structured_data"))),
                    metadata=dict(_mapping(raw_chunk.get("metadata"))),
                )
                await _insert_image_chunk(connection, image_chunk)
                image_index += 1
                counts["image"] += 1
            elif modality == "table":
                table_chunk = TableChunkCreate(
                    chunk_uid=str(raw_chunk.get("chunk_uid") or ""),
                    paper_id=paper_id,
                    section_id=section_id,
                    table_index=table_index,
                    table_number=_optional_text(raw_chunk.get("table_number")),
                    caption=_optional_text(raw_chunk.get("caption")),
                    page_number=_optional_int(raw_chunk.get("page_number")),
                    bbox=raw_chunk.get("bbox"),
                    table_html=_optional_text(raw_chunk.get("table_html")),
                    structured_data=dict(_mapping(raw_chunk.get("structured_data"))),
                    description=_optional_text(raw_chunk.get("description")),
                    metadata=dict(_mapping(raw_chunk.get("metadata"))),
                )
                await _insert_table_chunk(connection, table_chunk)
                table_index += 1
                counts["table"] += 1
            else:
                raise ValueError(f"不支持的 Chunk 模态：{modality}")

    for index, (chunk_id, _section_id) in enumerate(text_rows):
        previous_id = text_rows[index - 1][0] if index > 0 else None
        next_id = text_rows[index + 1][0] if index + 1 < len(text_rows) else None
        await connection.execute(
            "UPDATE text_chunk SET previous_chunk_id = $1, next_chunk_id = $2 WHERE id = $3",
            previous_id,
            next_id,
            chunk_id,
        )
    return counts


async def _insert_section(connection: Any, section: SectionCreate) -> Mapping[str, Any]:
    """写入一个章节并返回数据库记录。"""

    row = await connection.fetchrow(
        """INSERT INTO section
        (section_uid, paper_id, parent_id, section_number, title, level, sort_order, section_path, raw_text, summary)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10) RETURNING *""",
        section.section_uid,
        section.paper_id,
        section.parent_id,
        section.section_number,
        section.title,
        section.level,
        section.sort_order,
        _json(section.section_path),
        section.raw_text,
        section.summary,
    )
    if row is None:
        raise RuntimeError(f"写入章节后未返回记录：{section.section_uid}")
    return row


async def _insert_text_chunk(connection: Any, chunk: TextChunkCreate) -> Mapping[str, Any]:
    """写入一个文本或公式 Chunk 并返回数据库记录。"""

    row = await connection.fetchrow(
        """INSERT INTO text_chunk
        (chunk_uid, paper_id, section_id, chunk_index, content, char_count, previous_chunk_id, next_chunk_id, metadata)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb) RETURNING *""",
        chunk.chunk_uid,
        chunk.paper_id,
        chunk.section_id,
        chunk.chunk_index,
        chunk.content,
        chunk.char_count,
        chunk.previous_chunk_id,
        chunk.next_chunk_id,
        _json(chunk.metadata),
    )
    if row is None:
        raise RuntimeError(f"写入文本 Chunk 后未返回记录：{chunk.chunk_uid}")
    return row


async def _insert_image_chunk(connection: Any, chunk: ImageChunkCreate) -> None:
    """写入一个图片 Chunk。"""

    await connection.execute(
        """INSERT INTO image_chunk
        (chunk_uid, paper_id, section_id, image_index, figure_number, image_path, caption, image_type,
         description, structured_data, metadata)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11::jsonb)""",
        chunk.chunk_uid,
        chunk.paper_id,
        chunk.section_id,
        chunk.image_index,
        chunk.figure_number,
        chunk.image_path,
        chunk.caption,
        chunk.image_type,
        chunk.description,
        _json(chunk.structured_data),
        _json(chunk.metadata),
    )


async def _insert_table_chunk(connection: Any, chunk: TableChunkCreate) -> None:
    """写入一个表格 Chunk。"""

    await connection.execute(
        """INSERT INTO table_chunk
        (chunk_uid, paper_id, section_id, table_index, table_number, caption, page_number, bbox,
         table_html, structured_data, description, metadata)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10::jsonb, $11, $12::jsonb)""",
        chunk.chunk_uid,
        chunk.paper_id,
        chunk.section_id,
        chunk.table_index,
        chunk.table_number,
        chunk.caption,
        chunk.page_number,
        _json(chunk.bbox),
        chunk.table_html,
        _json(chunk.structured_data),
        chunk.description,
        _json(chunk.metadata),
    )


def load_document_tree(path: str | Path) -> Mapping[str, Any]:
    """读取阶段 D JSON 并返回根映射。"""

    source = Path(path).expanduser().resolve()
    data = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        raise ValueError(f"阶段 D JSON 根节点必须是对象：{source}")
    return data


def build_argument_parser() -> argparse.ArgumentParser:
    """创建阶段 E 命令行参数解析器。"""

    parser = argparse.ArgumentParser(description="将阶段 D 文档树统一写入 PostgreSQL")
    parser.add_argument("--input", type=Path, required=True, help="阶段 D 文档树 JSON")
    parser.add_argument("--pdf-path", help="Paper.pdf_path，可填写 MinIO URI")
    parser.add_argument("--markdown-path", help="Paper.markdown_path，可填写 MinIO URI")
    parser.add_argument("--document-json-path", help="Paper.document_json_path，可填写 MinIO URI")
    return parser


async def async_main(argv: Sequence[str] | None = None) -> int:
    """运行阶段 E 命令行流程并输出入库统计。"""

    args = build_argument_parser().parse_args(argv)
    client = get_postgres_client()
    writer = DatabaseWriter(client)
    try:
        result = await writer.persist_document(
            load_document_tree(args.input),
            pdf_path=args.pdf_path,
            markdown_path=args.markdown_path,
            document_json_path=args.document_json_path,
        )
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0
    finally:
        await client.close()


def main(argv: Sequence[str] | None = None) -> int:
    """提供同步 CLI 入口。"""

    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
