"""使用本地 Ollama 视觉语言模型为数据库中的图片和表格生成描述。

本脚本读取 ``image_chunk`` 和 ``table_chunk`` 中尚未生成描述的记录，调用本地
Ollama 的 ``qwen3-vl:2b`` 模型，并将模型返回的中文描述写回对应表的
``description`` 字段。图片既支持本地路径，也支持项目约定的 ``minio://`` 地址；
表格优先使用其图片资源，否则使用 HTML 或原始表格内容进行描述。

直接执行示例（PowerShell）：

    cd backend
    uv run python app/parsers/vlm/_descriptionUtil.py

覆盖已有描述：

    uv run python app/parsers/vlm/_descriptionUtil.py --overwrite
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import os
import re
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import asyncpg
import httpx

# 支持从 backend 目录直接执行本文件：python app/parsers/vlm/_descriptionUtil.py。
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from app.config import get_settings

DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "qwen3-vl:2b"
DEFAULT_TIMEOUT_SECONDS = 300.0
MAX_TABLE_TEXT_LENGTH = 16_000
SUPPORTED_IMAGE_SUFFIXES = {
    ".apng",
    ".bmp",
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}


@dataclass(frozen=True, slots=True)
class DescriptionResult:
    """保存单条记录的处理结果，便于命令行汇总。"""

    table_name: str
    record_id: int
    status: str
    message: str = ""


class OllamaVisionClient:
    """封装 Ollama ``/api/chat`` 接口，统一处理多模态请求和响应解析。"""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_OLLAMA_BASE_URL,
        model: str = DEFAULT_OLLAMA_MODEL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        """初始化 Ollama 地址、模型名称和单次请求超时时间。"""

        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = httpx.Timeout(timeout_seconds, connect=10.0)

    async def describe(
        self,
        *,
        prompt: str,
        image_bytes: list[bytes] | None = None,
        image_media_types: list[str] | None = None,
    ) -> str:
        """调用 Ollama 生成描述，并返回清理后的文本结果。"""

        images = [base64.b64encode(item).decode("ascii") for item in image_bytes or []]
        content: dict[str, Any] = {"role": "user", "content": prompt}
        if images:
            content["images"] = images

        payload = {
            "model": self.model,
            "messages": [content],
            "stream": False,
            "options": {"temperature": 0.0},
        }
        # media type 仅用于调用方记录和调试；Ollama 的 images 字段接收裸 base64。
        _ = image_media_types
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(f"{self.base_url}/api/chat", json=payload)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                detail = exc.response.text[:500]
                raise RuntimeError(f"Ollama 请求失败 HTTP {exc.response.status_code}: {detail}") from exc
            except httpx.HTTPError as exc:
                raise RuntimeError(f"无法连接 Ollama {self.base_url}: {exc}") from exc

        try:
            data = response.json()
            message = data["message"]
            result = message["content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise RuntimeError(f"Ollama 返回格式异常：{response.text[:500]}") from exc
        return clean_model_text(str(result))


def clean_model_text(text: str) -> str:
    """去除模型可能返回的思考标签、代码围栏和多余空白。"""

    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
    cleaned = re.sub(r"^```(?:text|markdown)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned


def _env_float(name: str, default: float) -> float:
    """读取浮点型环境变量，并在值非法时给出明确错误。"""

    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ValueError(f"环境变量 {name} 必须是数字：{raw_value!r}") from exc
    if value <= 0:
        raise ValueError(f"环境变量 {name} 必须大于 0：{value}")
    return value


def _as_mapping(value: Any) -> Mapping[str, Any]:
    """将数据库 JSONB 值安全转换为映射，避免异常数据中断整个批次。"""

    return value if isinstance(value, Mapping) else {}


def _as_strings(value: Any) -> list[str]:
    """从列表或单个字符串中提取非空字符串。"""

    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray, Mapping)):
        return [str(item) for item in value if str(item).strip()]
    return []


def _normalise_source_path(source: str) -> str:
    """规范本地路径或 MinIO URI，保留可直接访问的来源格式。"""

    return source.strip().replace("\\", "/")


def _media_type(source: str) -> str:
    """根据图片路径后缀推断 Ollama 图片对应的 MIME 类型。"""

    suffix = Path(urlparse(source).path).suffix.lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
        ".apng": "image/apng",
    }.get(suffix, "application/octet-stream")


def _read_minio_object_sync(client: Any, bucket: str, object_name: str) -> bytes:
    """同步读取一个 MinIO 对象，并确保响应连接得到释放。"""

    response = client.get_object(bucket, object_name)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def create_minio_client() -> Any:
    """按项目配置创建轻量 MinIO 客户端，避免加载不相关的 Milvus 组件。"""

    from minio import Minio

    settings = get_settings()
    return Minio(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
        region=settings.minio_region,
    )


async def read_binary_source(source: str, minio_client: Any | None = None) -> bytes:
    """读取本地图片或 ``minio://bucket/object`` 对象的二进制内容。"""

    normalised = _normalise_source_path(source)
    if normalised.startswith("minio://"):
        parsed = urlparse(normalised)
        bucket = parsed.netloc
        object_name = parsed.path.lstrip("/")
        if not bucket or not object_name:
            raise ValueError(f"非法 MinIO 图片地址：{source!r}")
        client = minio_client or create_minio_client()
        return await asyncio.to_thread(_read_minio_object_sync, client, bucket, object_name)

    path = Path(source).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"图片文件不存在：{path}")
    return await asyncio.to_thread(path.read_bytes)


def extract_table_image_sources(row: Mapping[str, Any]) -> list[str]:
    """从表格记录的结构化数据和元数据中提取图片资源路径。"""

    structured_data = _as_mapping(row.get("structured_data"))
    metadata = _as_mapping(row.get("metadata"))
    candidates: list[str] = []
    candidates.extend(_as_strings(structured_data.get("asset_paths")))
    candidates.extend(_as_strings(metadata.get("asset_paths")))
    candidates.extend(_as_strings(metadata.get("image_paths")))
    # 去重但保持数据库中资源的原始顺序。
    return list(
        dict.fromkeys(
            path
            for path in candidates
            if Path(urlparse(_normalise_source_path(path)).path).suffix.lower() in SUPPORTED_IMAGE_SUFFIXES
        )
    )


def build_image_prompt(row: Mapping[str, Any]) -> str:
    """为图片记录构造要求模型忠实描述的中文提示词。"""

    caption = str(row.get("caption") or "无")
    image_type = str(row.get("image_type") or "未分类")
    figure_number = str(row.get("figure_number") or "无")
    return (
        "请用中文描述这张科研论文图片，严格依据图片中可见内容，不要臆测图片外的信息。\n"
        "请覆盖：图片类型或主题、关键对象/文字、空间或流程关系、坐标轴/图例/单位（如有）、"
        "以及对论文读者有帮助的主要结论。无法辨认的内容请明确写“无法辨认”，不要编造。\n"
        f"数据库图号：{figure_number}\n"
        f"已有图注：{caption}\n"
        f"已有图片类型：{image_type}\n"
        "只返回一段可直接写入数据库的描述文本，不要返回 JSON、标题或 Markdown 代码块。"
    )


def build_table_prompt(row: Mapping[str, Any]) -> str:
    """为表格记录构造结合表格内容和上下文的中文提示词。"""

    structured_data = _as_mapping(row.get("structured_data"))
    metadata = _as_mapping(row.get("metadata"))
    table_content = str(row.get("table_html") or metadata.get("raw_content") or "").strip()
    context = str(metadata.get("context") or "").strip()
    if len(table_content) > MAX_TABLE_TEXT_LENGTH:
        table_content = table_content[:MAX_TABLE_TEXT_LENGTH] + "\n[表格内容已截断]"
    if len(context) > 4_000:
        context = context[:4_000] + "\n[上下文已截断]"
    source_type = str(structured_data.get("source_type") or "未知")
    return (
        "请用中文描述这张科研论文表格，严格依据给出的表格图片或文本内容，不要臆测缺失数值。\n"
        "请覆盖：表格主题、行列字段、数据范围或单位、显著的比较/趋势，以及对论文读者有帮助的结论。"
        "若图片或文本无法读取，明确说明无法读取；不要编造数据。\n"
        f"表号：{row.get('table_number') or '无'}\n"
        f"表题：{row.get('caption') or '无'}\n"
        f"来源类型：{source_type}\n"
        f"表格文本：\n{table_content or '无'}\n"
        f"相关上下文：\n{context or '无'}\n"
        "只返回一段可直接写入数据库的描述文本，不要返回 JSON、标题或 Markdown 代码块。"
    )


async def describe_image_row(
    client: OllamaVisionClient,
    row: Mapping[str, Any],
    minio_client: Any | None = None,
) -> str:
    """读取图片记录对应的图片并调用模型生成描述。"""

    source = str(row.get("image_path") or "").strip()
    if not source:
        raise ValueError("image_path 为空")
    image = await read_binary_source(source, minio_client)
    return await client.describe(
        prompt=build_image_prompt(row),
        image_bytes=[image],
        image_media_types=[_media_type(source)],
    )


async def describe_table_row(
    client: OllamaVisionClient,
    row: Mapping[str, Any],
    minio_client: Any | None = None,
) -> str:
    """读取表格资源并调用模型生成表格描述。"""

    sources = extract_table_image_sources(row)
    images: list[bytes] = []
    media_types: list[str] = []
    for source in sources:
        try:
            images.append(await read_binary_source(source, minio_client))
            media_types.append(_media_type(source))
        except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
            # 表格仍可能有可用 HTML；记录失败资源并继续让模型处理文本。
            print(f"警告：表格图片读取失败，继续使用表格文本：{source}，原因：{exc}", file=sys.stderr)
    return await client.describe(
        prompt=build_table_prompt(row),
        image_bytes=images,
        image_media_types=media_types,
    )


async def fetch_pending_rows(
    table_name: str,
    *,
    pool: asyncpg.Pool,
    limit: int | None,
    record_id: int | None,
    overwrite: bool,
) -> list[dict[str, Any]]:
    """查询待处理的图片或表格记录，并按数据库主键稳定排序。"""

    if table_name not in {"image_chunk", "table_chunk"}:
        raise ValueError(f"不支持的表名：{table_name}")
    id_clause = " AND id = $1" if record_id is not None else ""
    description_clause = "" if overwrite else " AND (description IS NULL OR BTRIM(description) = '')"
    limit_clause = f" LIMIT {int(limit)}" if limit is not None else ""
    sql = (
        f"SELECT * FROM {table_name} WHERE TRUE{id_clause}{description_clause} "
        f"ORDER BY id ASC{limit_clause}"
    )
    async with pool.acquire() as connection:
        rows = await connection.fetch(sql, record_id) if record_id is not None else await connection.fetch(sql)
    return [dict(row) for row in rows]


async def update_description(
    table_name: str,
    record_id: int,
    description: str,
    *,
    pool: asyncpg.Pool,
) -> None:
    """将模型生成的描述写回指定表的 description 字段。"""

    if table_name not in {"image_chunk", "table_chunk"}:
        raise ValueError(f"不支持的表名：{table_name}")
    async with pool.acquire() as connection:
        await connection.execute(
            f"UPDATE {table_name} SET description = $1 WHERE id = $2",
            description,
            record_id,
        )


async def process_table(
    client: OllamaVisionClient,
    table_name: str,
    rows: list[dict[str, Any]],
    *,
    pool: asyncpg.Pool,
    minio_client: Any,
    dry_run: bool,
) -> list[DescriptionResult]:
    """逐条处理一张表，单条失败时保留错误并继续处理其他记录。"""

    results: list[DescriptionResult] = []
    describe = describe_image_row if table_name == "image_chunk" else describe_table_row
    for row in rows:
        record_id = int(row["id"])
        try:
            description = await describe(client, row, minio_client)
            if not description:
                raise RuntimeError("模型返回空描述")
            if not dry_run:
                await update_description(table_name, record_id, description, pool=pool)
            status = "dry-run" if dry_run else "updated"
            print(f"{table_name} id={record_id}: {status}")
            results.append(DescriptionResult(table_name, record_id, status))
        except Exception as exc:  # 单条数据失败不应阻塞整个批次。
            message = f"{type(exc).__name__}: {exc}"
            print(f"{table_name} id={record_id}: failed - {message}", file=sys.stderr)
            results.append(DescriptionResult(table_name, record_id, "failed", message))
    return results


def build_argument_parser() -> argparse.ArgumentParser:
    """创建命令行参数解析器。"""

    parser = argparse.ArgumentParser(description="使用本地 Ollama qwen3-vl:2b 为图片和表格生成数据库描述")
    parser.add_argument("--model", default=os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL), help="Ollama 模型名")
    parser.add_argument(
        "--base-url",
        default=os.getenv("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL),
        help="Ollama 服务地址，默认 http://127.0.0.1:11434",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=_env_float("OLLAMA_TIMEOUT_SECS", DEFAULT_TIMEOUT_SECONDS),
        help="单张图片请求超时时间（秒）",
    )
    parser.add_argument("--limit", type=int, default=None, help="每张表最多处理多少条记录")
    parser.add_argument("--image-id", type=int, default=None, help="只处理指定 image_chunk.id")
    parser.add_argument("--table-id", type=int, default=None, help="只处理指定 table_chunk.id")
    parser.add_argument("--overwrite", action="store_true", help="覆盖已有 description，默认只处理空描述")
    parser.add_argument("--dry-run", action="store_true", help="调用模型但不写入数据库")
    return parser


async def async_main(args: argparse.Namespace) -> int:
    """执行查询、模型调用、描述写回和结果汇总。"""

    if args.limit is not None and args.limit < 1:
        raise ValueError("--limit 必须大于 0")
    if args.timeout <= 0:
        raise ValueError("--timeout 必须大于 0")
    client = OllamaVisionClient(base_url=args.base_url, model=args.model, timeout_seconds=args.timeout)
    settings = get_settings()
    pool = await asyncpg.create_pool(
        dsn=settings.async_database_url,
        min_size=settings.postgres_pool_min_size,
        max_size=settings.postgres_pool_max_size,
        timeout=settings.infrastructure_connect_timeout,
        command_timeout=settings.infrastructure_connect_timeout,
    )
    minio_client = create_minio_client()
    all_results: list[DescriptionResult] = []
    try:
        for table_name, record_id in (("image_chunk", args.image_id), ("table_chunk", args.table_id)):
            if args.image_id is not None and table_name != "image_chunk":
                continue
            if args.table_id is not None and table_name != "table_chunk":
                continue
            rows = await fetch_pending_rows(
                table_name,
                pool=pool,
                limit=args.limit,
                record_id=record_id,
                overwrite=args.overwrite,
            )
            print(f"{table_name}: 待处理 {len(rows)} 条")
            all_results.extend(
                await process_table(
                    client,
                    table_name,
                    rows,
                    pool=pool,
                    minio_client=minio_client,
                    dry_run=args.dry_run,
                )
            )
    finally:
        await pool.close()

    updated = sum(result.status in {"updated", "dry-run"} for result in all_results)
    failed = sum(result.status == "failed" for result in all_results)
    print(f"处理完成：成功 {updated} 条，失败 {failed} 条，总计 {len(all_results)} 条")
    return 1 if failed else 0


def main() -> int:
    """解析命令行参数并运行异步主流程。"""

    args = build_argument_parser().parse_args()
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
