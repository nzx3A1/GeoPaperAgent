"""阶段 A：通过 SSH/SFTP 下载论文 PDF，上传 MinIO 并登记 Paper 记录。

本文件既可以被总管线调用，也可以在 ``backend`` 目录下使用
``python -m app.parsers.AsshDownload`` 单独执行；默认全量处理远程目录中的 PDF。
``--count -1`` 表示不限制下载数量。
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import hashlib
import json
import logging
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import quote

# 支持直接执行 ``python app/parsers/AsshDownload.py``。
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pypdf import PdfReader

from app.config import get_settings
from app.infrastructure.minio_client import MinioClient, get_minio_client
from app.infrastructure.sshClient import (
    DEFAULT_REMOTE_PDF_DIR,
    SSHClient,
    SSHConfig,
    _local_destination,
)
from app.repositories import DatabaseService, get_database_service
from app.schemas import PaperCreate, PaperUpdate
from app.schemas.base import JsonObject

BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOCAL_PDF_DIR = BACKEND_ROOT / "data" / "raw_pdf"
DEFAULT_DOWNLOAD_COUNT = -1
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DownloadedPaper:
    """保存阶段 A 交给后续阶段使用的论文文件与数据库标识。"""

    paper_uid: str
    database_id: int
    local_pdf_path: Path
    remote_pdf_path: str
    pdf_uri: str
    sha256: str
    page_count: int | None

    def to_dict(self) -> dict[str, object]:
        """将下载结果转换为可写入 JSON 的字典。"""

        data = asdict(self)
        data["local_pdf_path"] = str(self.local_pdf_path)
        return data


def calculate_sha256(path: str | Path) -> str:
    """分块计算文件 SHA-256，避免读取大 PDF 时占用过多内存。"""

    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_paper_uid(sha256: str) -> str:
    """根据 PDF 内容哈希生成稳定且满足数据库长度限制的论文业务 ID。"""

    if len(sha256) < 32:
        raise ValueError("SHA-256 字符串长度不足，无法生成 paper_uid")
    return f"paper-{sha256[:32]}"


def read_pdf_page_count(path: str | Path) -> int | None:
    """读取 PDF 页数；损坏或加密文件无法读取时返回 ``None``。"""

    reader: PdfReader | None = None
    try:
        with Path(path).open("rb") as file:
            reader = PdfReader(file, strict=False)
            return len(reader.pages)
    except Exception:
        return None
    finally:
        if reader is not None:
            reader.close()


def build_sftp_source_url(config: SSHConfig, remote_path: str) -> str:
    """构造不包含账号密码的 SFTP 来源地址，供 Paper 记录追踪来源。"""

    encoded_path = quote(remote_path, safe="/")
    return f"sftp://{config.host}:{config.port}{encoded_path}"


async def register_downloaded_pdf(
    *,
    local_path: Path,
    remote_path: str,
    ssh_config: SSHConfig,
    minio_client: MinioClient,
    database: DatabaseService,
    bucket_name: str,
    skip_completed: bool = True,
) -> DownloadedPaper | None:
    """上传一篇 PDF 到 MinIO；已完整入库的论文默认不重复登记和解析。"""

    sha256 = await asyncio.to_thread(calculate_sha256, local_path)
    page_count = await asyncio.to_thread(read_pdf_page_count, local_path)
    paper_uid = build_paper_uid(sha256)
    object_name = f"papers/{paper_uid}/source/{local_path.name}"
    existing = await database.papers.get_by_uid(paper_uid)
    if skip_completed and existing is not None and existing.ingestion_status == "completed":
        return None
    existing_metadata = existing.file_metadata if existing is not None else None
    can_reuse_pdf = (
        existing is not None
        and bool(existing.pdf_path)
        and isinstance(existing_metadata, dict)
        and existing_metadata.get("sha256") == sha256
    )
    if can_reuse_pdf:
        # 同一内容已有 MinIO 地址时直接复用，避免全量重跑产生数千次重复上传。
        pdf_uri = existing.pdf_path
    else:
        pdf_uri = await minio_client.upload_file(bucket_name, object_name, local_path, content_type="application/pdf")
    file_metadata: JsonObject = {
        "sha256": sha256,
        "file_size": local_path.stat().st_size,
        "source_filename": local_path.name,
        "remote_pdf_path": remote_path,
        "minio_bucket": bucket_name,
        "minio_pdf_object": object_name,
        "pipeline_stage": "downloaded",
    }
    if existing is None:
        paper = await database.papers.create(
            PaperCreate(
                paper_uid=paper_uid,
                title=local_path.stem,
                source_url=build_sftp_source_url(ssh_config, remote_path),
                pdf_path=pdf_uri,
                page_count=page_count,
                file_metadata=file_metadata,
                ingestion_status="downloaded",
            )
        )
    else:
        merged_metadata: JsonObject = {**(existing.file_metadata or {}), **file_metadata}
        updated = await database.papers.update(
            existing.id,
            PaperUpdate(
                source_url=build_sftp_source_url(ssh_config, remote_path),
                pdf_path=pdf_uri,
                page_count=page_count,
                file_metadata=merged_metadata,
                ingestion_status="downloaded",
                error_message=None,
            ),
        )
        if updated is None:
            raise RuntimeError(f"Paper 记录在更新前被删除：{paper_uid}")
        paper = updated
    return DownloadedPaper(
        paper_uid=paper_uid,
        database_id=paper.id,
        local_pdf_path=local_path.resolve(),
        remote_pdf_path=remote_path,
        pdf_uri=pdf_uri,
        sha256=sha256,
        page_count=page_count,
    )


async def download_store_and_register(
    *,
    count: int = DEFAULT_DOWNLOAD_COUNT,
    local_directory: str | Path = DEFAULT_LOCAL_PDF_DIR,
    remote_directory: str = DEFAULT_REMOTE_PDF_DIR,
    recursive: bool = False,
    overwrite: bool = False,
    ssh_config: SSHConfig | None = None,
    minio_client: MinioClient | None = None,
    database: DatabaseService | None = None,
    bucket_name: str | None = None,
    skip_completed: bool = True,
) -> list[DownloadedPaper]:
    """执行 SSH 下载、MinIO 上传和 Paper 初始登记，并默认跳过完整入库论文。"""

    if count == 0 or count < -1:
        raise ValueError("下载数量 count 必须为 -1 或大于 0")
    config = ssh_config or SSHConfig.from_env()
    storage = minio_client or get_minio_client()
    db = database or get_database_service()
    selected_bucket = bucket_name or get_settings().minio_paper_bucket
    local_root = Path(local_directory).expanduser().resolve()

    with SSHClient(config) as ssh:
        all_remote_paths = ssh.list_pdf_files(remote_directory, recursive=recursive)
        remote_paths = all_remote_paths if count == -1 else all_remote_paths[:count]
        if not remote_paths:
            return []
        existing_count = 0
        if not overwrite:
            existing_count = sum(
                _local_destination(local_root, remote_directory, remote_path).is_file() for remote_path in remote_paths
            )
        to_download_count = len(remote_paths) if overwrite else len(remote_paths) - existing_count
        LOGGER.info(
            "阶段 A 文件准备 | 远程 PDF=%d | 本地复用=%d | 需要下载=%d",
            len(remote_paths),
            existing_count,
            to_download_count,
        )
        downloaded_progress = 0

        def log_download_progress(remote_path: str, completed: int, total_bytes: int) -> None:
            """在单个 PDF 下载完成时按批次输出下载进度，避免数千条日志刷屏。"""

            nonlocal downloaded_progress
            if total_bytes <= 0 or completed < total_bytes:
                return
            downloaded_progress += 1
            if downloaded_progress == 1 or downloaded_progress % 50 == 0 or downloaded_progress == to_download_count:
                LOGGER.info(
                    "阶段 A 下载进度 | %d/%d | %s",
                    downloaded_progress,
                    to_download_count,
                    Path(remote_path).name,
                )

        # 下载器会跳过本地已有文件；随后仍会把这些文件纳入幂等重跑。
        downloaded_paths = ssh.download_pdf_files(
            local_root,
            count=len(remote_paths),
            remote_directory=remote_directory,
            recursive=recursive,
            overwrite=overwrite,
            progress=log_download_progress,
        )
        LOGGER.info(
            "阶段 A 下载完成 | 新下载=%d | 本地复用=%d",
            len(downloaded_paths),
            len(remote_paths) - len(downloaded_paths),
        )

    results: list[DownloadedPaper] = []
    skipped_completed_count = 0
    for index, remote_path in enumerate(remote_paths, start=1):
        local_path = _local_destination(local_root, remote_directory, remote_path)
        if not local_path.is_file():
            raise FileNotFoundError(f"SSH 下载结束后未找到本地 PDF：{local_path}")
        registered = await register_downloaded_pdf(
            local_path=local_path,
            remote_path=remote_path,
            ssh_config=config,
            minio_client=storage,
            database=db,
            bucket_name=selected_bucket,
            skip_completed=skip_completed,
        )
        if registered is None:
            skipped_completed_count += 1
            continue
        results.append(registered)
        if index == 1 or index % 50 == 0 or index == len(remote_paths):
            LOGGER.info("阶段 A 数据库登记进度 | %d/%d | paper_uid=%s", index, len(remote_paths), registered.paper_uid)
            gc.collect()
    if skipped_completed_count:
        LOGGER.info("阶段 A 跳过已完整入库论文=%d", skipped_completed_count)
    return results


def build_argument_parser() -> argparse.ArgumentParser:
    """创建阶段 A 命令行参数解析器。"""

    parser = argparse.ArgumentParser(description="通过 SSH 下载 PDF，上传 MinIO 并登记 Paper")
    parser.add_argument("--count", type=int, default=DEFAULT_DOWNLOAD_COUNT, help="下载论文数量，默认 -1（全量下载）")
    parser.add_argument("--local-directory", type=Path, default=DEFAULT_LOCAL_PDF_DIR, help="本地 PDF 目录")
    parser.add_argument("--remote-directory", default=DEFAULT_REMOTE_PDF_DIR, help="远程 PDF 目录")
    parser.add_argument("--recursive", action="store_true", help="递归查找远程子目录")
    parser.add_argument("--overwrite", action="store_true", help="覆盖本地同名 PDF")
    parser.add_argument("--reparse-completed", action="store_true", help="重新登记已完整入库的论文")
    return parser


async def async_main(argv: Sequence[str] | None = None) -> int:
    """运行阶段 A 命令行流程并输出 JSON 摘要。"""

    args = build_argument_parser().parse_args(argv)
    database = get_database_service()
    storage = get_minio_client()
    try:
        papers = await download_store_and_register(
            count=args.count,
            local_directory=args.local_directory,
            remote_directory=args.remote_directory,
            recursive=args.recursive,
            overwrite=args.overwrite,
            minio_client=storage,
            database=database,
            skip_completed=not args.reparse_completed,
        )
        print(json.dumps([paper.to_dict() for paper in papers], ensure_ascii=False, indent=2))
        return 0
    finally:
        await database.close()
        await storage.close()


def main(argv: Sequence[str] | None = None) -> int:
    """提供同步 CLI 入口。"""

    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
