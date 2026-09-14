"""从论文 Markdown 首页提取元数据，并回填 PostgreSQL ``paper`` 表。

本工具只处理 ``abstract`` 为空的论文。它支持本地 Markdown 路径和
``minio://bucket/object`` 地址；源文件或可提取摘要不存在时不写入任何推测值。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# 支持从 backend 目录外直接执行本脚本。
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from app.infrastructure.minio_client import MinioClient, get_minio_client
from app.infrastructure.postgres_client import PostgresClient, get_postgres_client
from app.parsers.CMarkdownParser import LLMPaperMetadata, extract_metadata_with_project_llm, read_text

LOGGER = logging.getLogger(__name__)
DEFAULT_HEADER_CHARS = 20_000
JSON_FIELDS = frozenset({"authors", "affiliations", "keywords"})
METADATA_FIELDS = (
    "abstract",
    "title_en",
    "authors",
    "affiliations",
    "keywords",
    "doi",
    "journal",
    "volume",
    "issue",
    "publish_year",
    "publish_date",
    "language",
)


@dataclass(slots=True)
class BackfillStats:
    """汇总元数据回填过程的论文数量和异常数量。"""

    scanned: int = 0
    updated: int = 0
    no_markdown: int = 0
    no_abstract: int = 0
    failed: int = 0

    def as_dict(self) -> dict[str, int]:
        """将统计值转换为便于 CLI 输出的字典。"""

        return {
            "scanned": self.scanned,
            "updated": self.updated,
            "no_markdown": self.no_markdown,
            "no_abstract": self.no_abstract,
            "failed": self.failed,
        }


def _is_blank(value: Any) -> bool:
    """判断文本、JSON 数组或可空字段是否尚未保存有效值。"""

    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict)):
        return not value
    return False


def _metadata_value(metadata: LLMPaperMetadata, field: str) -> Any:
    """读取并清洗 LLM 元数据字段，过滤空字符串和空数组。"""

    value = getattr(metadata, field)
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return value


def _parse_minio_uri(uri: str) -> tuple[str, str]:
    """把 ``minio://bucket/object`` 地址拆成桶名和对象键。"""

    parsed = urlparse(uri)
    bucket = parsed.netloc.strip()
    object_name = parsed.path.lstrip("/")
    if parsed.scheme != "minio" or not bucket or not object_name:
        raise ValueError(f"不是有效的 MinIO Markdown 地址：{uri}")
    return bucket, object_name


async def _read_minio_front(minio: MinioClient, uri: str, max_chars: int) -> str:
    """读取 MinIO Markdown 的开头，始终释放底层 HTTP 响应连接。"""

    bucket, object_name = _parse_minio_uri(uri)
    client = await minio.connect()
    response = await asyncio.to_thread(client.get_object, bucket, object_name)
    try:
        raw = await asyncio.to_thread(response.read, max_chars * 4)
    finally:
        response.close()
        response.release_conn()
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(encoding)[:max_chars]
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")[:max_chars]


async def read_markdown_front(
    minio: MinioClient,
    markdown_path: str | None,
    max_chars: int,
) -> str | None:
    """从本地或 MinIO Markdown 地址读取指定长度的首页文本。"""

    if not markdown_path or not markdown_path.strip():
        return None
    source = markdown_path.strip()
    if source.startswith("minio://"):
        return await _read_minio_front(minio, source, max_chars)
    path = Path(source).expanduser()
    if not path.is_file():
        return None
    return read_text(path)[:max_chars]


async def _load_candidates(connection: Any, limit: int) -> list[Mapping[str, Any]]:
    """查询摘要为空且待回填的论文，并保留其已有元数据供合并。"""

    sql = """
        SELECT id, paper_uid, title, markdown_path, abstract, title_en, authors, affiliations,
               keywords, doi, journal, volume, issue, publish_year, publish_date, language
        FROM paper
        WHERE abstract IS NULL OR BTRIM(abstract) = ''
        ORDER BY id
    """
    if limit >= 0:
        sql += " LIMIT $1"
        return list(await connection.fetch(sql, limit))
    return list(await connection.fetch(sql))


def _build_updates(row: Mapping[str, Any], metadata: LLMPaperMetadata) -> dict[str, Any]:
    """仅为数据库中为空且 LLM 确实返回的字段构造回填数据。"""

    abstract = _metadata_value(metadata, "abstract")
    # 抽取不到摘要即视为来源中没有可确认的论文首页元数据，避免回填猜测信息。
    if not abstract:
        return {}
    updates: dict[str, Any] = {"abstract": abstract}
    for field in METADATA_FIELDS[1:]:
        value = _metadata_value(metadata, field)
        if _is_blank(row[field]) and not _is_blank(value):
            updates[field] = value
    return updates


async def _update_paper(connection: Any, paper_id: int, updates: Mapping[str, Any]) -> None:
    """用参数化 SQL 写入一篇论文的确认元数据，不覆盖原有非空字段。"""

    assignments = []
    values: list[Any] = []
    for index, (field, value) in enumerate(updates.items(), start=1):
        cast = "::jsonb" if field in JSON_FIELDS else ""
        assignments.append(f"{field} = ${index}{cast}")
        values.append(json.dumps(value, ensure_ascii=False) if field in JSON_FIELDS else value)
    values.append(paper_id)
    sql = f"UPDATE paper SET {', '.join(assignments)}, updated_at = NOW() WHERE id = ${len(values)}"
    await connection.execute(sql, *values)


async def backfill_metadata(
    *,
    limit: int,
    header_chars: int,
    dry_run: bool,
    postgres: PostgresClient | None = None,
    minio: MinioClient | None = None,
) -> BackfillStats:
    """调用项目 LLM 回填缺失摘要论文，并返回每种处理结果的数量。"""

    db = postgres or get_postgres_client()
    object_store = minio or get_minio_client()
    stats = BackfillStats()
    try:
        # 先做一次服务探活，避免对象存储离线时对每篇论文重复报错。
        await object_store.healthcheck()
        async with db.connection() as connection:
            candidates = await _load_candidates(connection, limit)
            for index, row in enumerate(candidates, start=1):
                stats.scanned += 1
                paper_uid = str(row["paper_uid"])
                try:
                    header = await read_markdown_front(object_store, row["markdown_path"], header_chars)
                    if not header:
                        stats.no_markdown += 1
                        LOGGER.warning("[%s/%s] %s 没有可读取的 Markdown，跳过", index, len(candidates), paper_uid)
                        continue
                    metadata = await asyncio.to_thread(extract_metadata_with_project_llm, header)
                    updates = _build_updates(row, metadata)
                    if not updates:
                        stats.no_abstract += 1
                        LOGGER.info("[%s/%s] %s 未在 Markdown 首页提取到摘要，保留为空", index, len(candidates), paper_uid)
                        continue
                    if not dry_run:
                        await _update_paper(connection, int(row["id"]), updates)
                    stats.updated += 1
                    LOGGER.info("[%s/%s] %s %s：%s", index, len(candidates), paper_uid, "将回填" if dry_run else "已回填", ", ".join(updates))
                except Exception as error:  # 单篇失败不阻断其余论文。
                    stats.failed += 1
                    LOGGER.error("[%s/%s] %s 回填失败：%s", index, len(candidates), paper_uid, error)
        return stats
    finally:
        await db.close()
        await object_store.close()


def build_argument_parser() -> argparse.ArgumentParser:
    """创建元数据回填脚本的命令行参数。"""

    parser = argparse.ArgumentParser(description="用 LLM 从 Markdown 首页回填 paper 表缺失元数据")
    parser.add_argument("--limit", type=int, default=-1, help="最多处理的论文数；-1 表示全部")
    parser.add_argument("--header-chars", type=int, default=DEFAULT_HEADER_CHARS, help="传给 LLM 的 Markdown 首页字符数")
    parser.add_argument("--dry-run", action="store_true", help="只抽取和统计，不写入 PostgreSQL")
    return parser


async def async_main(argv: Sequence[str] | None = None) -> int:
    """执行 CLI 主流程并打印 JSON 统计结果。"""

    args = build_argument_parser().parse_args(argv)
    if args.limit < -1:
        raise ValueError("--limit 只能是 -1 或非负整数")
    if args.header_chars <= 0:
        raise ValueError("--header-chars 必须大于 0")
    stats = await backfill_metadata(limit=args.limit, header_chars=args.header_chars, dry_run=args.dry_run)
    print(json.dumps(stats.as_dict(), ensure_ascii=False, indent=2))
    return 0 if stats.failed == 0 else 1


def main(argv: Sequence[str] | None = None) -> int:
    """提供可从命令行调用的同步入口。"""

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
