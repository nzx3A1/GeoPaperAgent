"""使用本地 Ollama 向 ``paper``、``section`` 和文本 Chunk 表写入向量。

默认模型为用户指定的 ``qwen3-embedding:0.6b``。脚本会先校验 PostgreSQL
向量列维度，再仅处理 ``embedding`` 为空的记录；可通过 ``--force`` 重新生成。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncpg
import httpx

# 支持从 backend 目录外直接执行本脚本。
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from app.infrastructure.postgres_client import PostgresClient, get_postgres_client

LOGGER = logging.getLogger(__name__)
DEFAULT_MODEL = "qwen3-embedding:0.6b"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"


@dataclass(frozen=True, slots=True)
class TableSpec:
    """描述一个待向量化表的读取 SQL 与可检索文本拼接函数。"""

    table_name: str
    select_sql: str
    to_text: Callable[[Mapping[str, Any]], str]


@dataclass(slots=True)
class EmbeddingStats:
    """累计一个表的读取、写入、跳过和失败记录数。"""

    read: int = 0
    embedded: int = 0
    skipped: int = 0
    failed: int = 0

    def as_dict(self) -> dict[str, int]:
        """返回 JSON 兼容的统计字典。"""

        return {"read": self.read, "embedded": self.embedded, "skipped": self.skipped, "failed": self.failed}


def _clean_text(value: Any) -> str:
    """将可空数据库字段转换为干净的可嵌入文本。"""

    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value).strip()


def _join_labeled_fields(fields: Sequence[tuple[str, Any]], max_chars: int) -> str:
    """拼接带字段标签的文本，并在模型调用前按字符数截断。"""

    text = "\n".join(f"{label}：{cleaned}" for label, value in fields if (cleaned := _clean_text(value)))
    return text[:max_chars].strip()


def _paper_text(row: Mapping[str, Any], max_chars: int) -> str:
    """构造包含题名、摘要、关键词和作者的论文级向量文本。"""

    return _join_labeled_fields(
        (("题名", row["title"]), ("摘要", row["abstract"]), ("关键词", row["keywords"]), ("作者", row["authors"])),
        max_chars,
    )


def _section_text(row: Mapping[str, Any], max_chars: int) -> str:
    """构造论文名和一级、二级、三级完整章节标题组成的章节向量文本。"""

    raw_path = row.get("section_path")
    if isinstance(raw_path, str):
        try:
            raw_path = json.loads(raw_path)
        except json.JSONDecodeError:
            raw_path = []
    path = list(raw_path) if isinstance(raw_path, (list, tuple)) else []
    if not path and _clean_text(row.get("title")):
        path = [_clean_text(row["title"])]
    labels = ("一级章节", "二级章节", "三级章节")
    hierarchy = [(labels[index] if index < len(labels) else f"第{index + 1}级章节", value) for index, value in enumerate(path)]
    return _join_labeled_fields((("论文", row.get("paper_title")), *hierarchy), max_chars)


def _text_chunk_text(row: Mapping[str, Any], max_chars: int) -> str:
    """返回文本 Chunk 的正文作为向量输入。"""

    return _clean_text(row["content"])[:max_chars]


def _image_chunk_text(row: Mapping[str, Any], max_chars: int) -> str:
    """构造图片标题、类型和视觉描述组成的向量文本。"""

    return _join_labeled_fields(
        (("图题", row["caption"]), ("图片类型", row["image_type"]), ("图片描述", row["description"])), max_chars
    )


def _table_chunk_text(row: Mapping[str, Any], max_chars: int) -> str:
    """构造表格标题、描述和 HTML 内容组成的向量文本。"""

    return _join_labeled_fields(
        (("表题", row["caption"]), ("表格描述", row["description"]), ("表格内容", row["table_html"])), max_chars
    )


def build_table_specs(max_chars: int) -> tuple[TableSpec, ...]:
    """创建当前启用的论文、章节和文本 Chunk 读取定义。"""

    # 图片和表格暂不嵌入，待其描述/结构化数据策略确定后再加入。
    return (
        TableSpec(
            "paper",
            "SELECT id, embedding, title, abstract, keywords, authors FROM paper",
            lambda row: _paper_text(row, max_chars),
        ),
        TableSpec(
            "section",
            "SELECT s.id, s.embedding, p.title AS paper_title, s.title, s.section_path "
            "FROM section AS s JOIN paper AS p ON p.id = s.paper_id",
            lambda row: _section_text(row, max_chars),
        ),
        TableSpec(
            "text_chunk",
            "SELECT id, embedding, content FROM text_chunk",
            lambda row: _text_chunk_text(row, max_chars),
        ),
    )


class OllamaEmbeddingClient:
    """封装 Ollama ``/api/embed`` 批量向量接口和返回值校验。"""

    def __init__(self, base_url: str, model: str, timeout_seconds: float) -> None:
        """保存本地服务地址、模型名和单次调用超时参数。"""

        self.url = f"{base_url.rstrip('/')}/api/embed"
        self.model = model
        self.timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 10.0))

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """批量请求 Ollama，并验证每条返回向量均是有限浮点数。"""

        if not texts:
            return []
        payload = {"model": self.model, "input": list(texts)}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(self.url, json=payload)
                response.raise_for_status()
            except httpx.HTTPStatusError as error:
                raise RuntimeError(f"Ollama 向量请求失败 HTTP {error.response.status_code}: {error.response.text[:500]}") from error
            except httpx.HTTPError as error:
                raise RuntimeError(f"无法连接 Ollama 向量服务 {self.url}: {error}") from error
        try:
            vectors = response.json()["embeddings"]
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeError(f"Ollama 返回中没有 embeddings：{response.text[:500]}") from error
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise RuntimeError(f"Ollama 返回向量数与输入不一致：输入 {len(texts)}，返回 {len(vectors) if isinstance(vectors, list) else 0}")
        normalized: list[list[float]] = []
        for vector in vectors:
            if not isinstance(vector, list) or not vector or not all(isinstance(item, (int, float)) and math.isfinite(item) for item in vector):
                raise RuntimeError("Ollama 返回了空向量或非有限数值")
            normalized.append([float(item) for item in vector])
        return normalized


async def _vector_dimension(connection: asyncpg.Connection, table_name: str) -> int:
    """读取 pgvector 列维度，确保模型输出能写入目标表。"""

    dimension = await connection.fetchval(
        """
        SELECT a.atttypmod
        FROM pg_attribute AS a
        JOIN pg_class AS c ON c.oid = a.attrelid
        JOIN pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = current_schema() AND c.relname = $1 AND a.attname = 'embedding'
              AND a.attnum > 0 AND NOT a.attisdropped
        """,
        table_name,
    )
    if not isinstance(dimension, int) or dimension <= 0:
        raise RuntimeError(f"{table_name}.embedding 不存在或不是定长 vector 列")
    return dimension


def _vector_literal(vector: Sequence[float]) -> str:
    """将浮点向量编码为 pgvector 可接受的文本字面量。"""

    return "[" + ",".join(format(value, ".9g") for value in vector) + "]"


async def _fetch_batch(
    connection: asyncpg.Connection,
    spec: TableSpec,
    last_id: int,
    batch_size: int,
    force: bool,
) -> list[Mapping[str, Any]]:
    """按主键游标读取一批待处理记录，避免一次加载整张表。"""

    predicate = "id > $1" if force else "id > $1 AND embedding IS NULL"
    sql = f"SELECT * FROM ({spec.select_sql}) AS source WHERE {predicate} ORDER BY id LIMIT $2"
    return list(await connection.fetch(sql, last_id, batch_size))


async def embed_table(
    connection: asyncpg.Connection,
    client: OllamaEmbeddingClient,
    spec: TableSpec,
    *,
    batch_size: int,
    limit: int,
    force: bool,
    dry_run: bool,
) -> EmbeddingStats:
    """分批向量化一个表，并在每批通过维度校验后写回 PostgreSQL。"""

    stats = EmbeddingStats()
    expected_dimension = await _vector_dimension(connection, spec.table_name)
    pending_total = await connection.fetchval(
        f"SELECT count(*) FROM {spec.table_name} " + ("" if force else "WHERE embedding IS NULL")
    )
    started_at = time.monotonic()
    last_id = 0
    while True:
        remaining = batch_size if limit < 0 else min(batch_size, limit - stats.read)
        if remaining <= 0:
            break
        rows = await _fetch_batch(connection, spec, last_id, remaining, force)
        if not rows:
            break
        last_id = int(rows[-1]["id"])
        stats.read += len(rows)
        usable_rows: list[Mapping[str, Any]] = []
        texts: list[str] = []
        for row in rows:
            text = spec.to_text(row)
            if text:
                usable_rows.append(row)
                texts.append(text)
            else:
                stats.skipped += 1
        if not texts:
            continue
        try:
            vectors = await client.embed(texts)
            if any(len(vector) != expected_dimension for vector in vectors):
                dimensions = sorted({len(vector) for vector in vectors})
                raise RuntimeError(f"{spec.table_name} 期望 {expected_dimension} 维，Ollama 返回 {dimensions} 维")
            if not dry_run:
                await connection.executemany(
                    f"UPDATE {spec.table_name} SET embedding = $1::vector WHERE id = $2",
                    [(_vector_literal(vector), int(row["id"])) for row, vector in zip(usable_rows, vectors, strict=True)],
                )
            stats.embedded += len(vectors)
            elapsed = time.monotonic() - started_at
            completed = min(stats.read, pending_total)
            eta = (elapsed / completed * (pending_total - completed)) if completed else 0.0
            LOGGER.info(
                "%s：进度 %s/%s，本批写入 %s，跳过 %s，耗时 %.1fs，预计剩余 %.1fs",
                spec.table_name,
                completed,
                pending_total,
                len(vectors),
                stats.skipped,
                elapsed,
                eta,
            )
        except Exception as error:  # 一批失败后继续，让长任务尽可能完成其他记录。
            stats.failed += len(usable_rows)
            LOGGER.exception("%s：ID 至 %s 的批次向量化失败：%s", spec.table_name, last_id, error)
    return stats


async def embed_database(
    *,
    base_url: str,
    model: str,
    batch_size: int,
    limit: int,
    timeout: float,
    max_chars: int,
    force: bool,
    dry_run: bool,
    create_indexes: bool,
    postgres: PostgresClient | None = None,
) -> dict[str, EmbeddingStats]:
    """依次为论文、章节和文本 Chunk 表生成并写入向量。"""

    db = postgres or get_postgres_client()
    embedding_client = OllamaEmbeddingClient(base_url, model, timeout)
    results: dict[str, EmbeddingStats] = {}
    try:
        async with db.connection() as connection:
            for spec in build_table_specs(max_chars):
                results[spec.table_name] = await embed_table(
                    connection,
                    embedding_client,
                    spec,
                    batch_size=batch_size,
                    limit=limit,
                    force=force,
                    dry_run=dry_run,
                )
            if create_indexes and not dry_run:
                await ensure_vector_indexes(connection, tuple(results))
        return results
    finally:
        await db.close()


async def ensure_vector_indexes(connection: asyncpg.Connection, table_names: Sequence[str]) -> None:
    """为已写入向量的固定业务表创建 HNSW 余弦距离索引。"""

    for table_name in table_names:
        if table_name not in {"paper", "section", "text_chunk", "image_chunk", "table_chunk"}:
            raise ValueError(f"不支持创建向量索引的数据表：{table_name}")
        index_name = f"idx_{table_name}_embedding_hnsw"
        await connection.execute(
            f"CREATE INDEX IF NOT EXISTS {index_name} ON {table_name} "
            "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
        )
        LOGGER.info("已确认向量索引：%s", index_name)


def build_argument_parser() -> argparse.ArgumentParser:
    """创建数据库向量化脚本的命令行参数。"""

    parser = argparse.ArgumentParser(description="使用本地 qwen3-embedding:0.6b 向量化论文数据库")
    parser.add_argument("--base-url", default=DEFAULT_OLLAMA_URL, help="Ollama 服务根地址")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Ollama 向量模型名")
    parser.add_argument("--batch-size", type=int, default=32, help="一次发送给 Ollama 的记录数")
    parser.add_argument("--limit", type=int, default=-1, help="每张表最多处理的记录数；-1 表示全部")
    parser.add_argument("--timeout", type=float, default=120.0, help="单批 Ollama 请求超时秒数")
    parser.add_argument("--max-chars", type=int, default=6_000, help="每条记录最长向量输入字符数")
    parser.add_argument("--force", action="store_true", help="即使已有向量也重新生成")
    parser.add_argument("--dry-run", action="store_true", help="调用模型和校验维度，但不写入 PostgreSQL")
    parser.add_argument("--skip-index", action="store_true", help="写入向量后不创建 HNSW 余弦索引")
    return parser


async def async_main(argv: Sequence[str] | None = None) -> int:
    """执行 CLI 主流程并打印每张表的 JSON 统计。"""

    args = build_argument_parser().parse_args(argv)
    if args.batch_size <= 0 or args.max_chars <= 0 or args.timeout <= 0 or args.limit < -1:
        raise ValueError("--batch-size、--max-chars、--timeout 必须大于 0，--limit 只能为 -1 或非负整数")
    results = await embed_database(
        base_url=args.base_url,
        model=args.model,
        batch_size=args.batch_size,
        limit=args.limit,
        timeout=args.timeout,
        max_chars=args.max_chars,
        force=args.force,
        dry_run=args.dry_run,
        create_indexes=not args.skip_index,
    )
    output = {table_name: stats.as_dict() for table_name, stats in results.items()}
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if all(stats.failed == 0 for stats in results.values()) else 1


def main(argv: Sequence[str] | None = None) -> int:
    """提供可从命令行调用的同步入口。"""

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
