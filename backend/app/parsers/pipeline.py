# ruff: noqa: E402

"""GeoPaperAgent 论文解析入库总管线。

本文件依次编排 A–E：SSH 下载、PDF/产物上传 MinIO、MinerU 解析、Markdown
章节化、文档树分片以及 PostgreSQL 事务入库。默认处理远程目录中的全部 PDF。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
import warnings
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

# 限制数值库线程数，避免批量解析数千篇 PDF 时创建过多线程导致内存峰值过高。
for _thread_env_name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_thread_env_name, "1")

# 支持直接执行 ``python app/parsers/pipeline.py``。
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config import get_settings
from app.infrastructure.minio_client import MinioClient, get_minio_client
from app.infrastructure.sshClient import DEFAULT_REMOTE_PDF_DIR, SSHConfig
from app.parsers.AsshDownload import (
    DEFAULT_DOWNLOAD_COUNT,
    DEFAULT_LOCAL_PDF_DIR,
    DownloadedPaper,
    download_store_and_register,
)
from app.parsers.BminerU_pipeline import DEFAULT_OUTPUT_DIR as DEFAULT_MINERU_OUTPUT_DIR
from app.parsers.BminerU_pipeline import MinerUResult, run_mineru_pipeline
from app.parsers.CMarkdownParser import AMarkdownParser, write_json
from app.parsers.Ddocument_tree_builder import build_document_tree, write_document_tree
from app.parsers.EwriteSQL import DatabaseWriter, PersistResult
from app.repositories import DatabaseService, get_database_service

BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PIPELINE_OUTPUT_ROOT = BACKEND_ROOT / "output" / "ingestion"
LOGGER = logging.getLogger(__name__)


def _format_elapsed(seconds: float) -> str:
    """把秒数格式化为适合进度日志展示的时长文本。"""

    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, remaining_seconds = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m{remaining_seconds:02d}s"
    hours, remaining_minutes = divmod(minutes, 60)
    return f"{hours}h{remaining_minutes:02d}m{remaining_seconds:02d}s"


def configure_logging(level: str = "INFO") -> None:
    """配置命令行流水线的统一日志格式和日志级别。"""

    if level.upper() != "DEBUG":
        warnings.filterwarnings("ignore", message=r"Multiple definitions in dictionary.*")
        logging.getLogger("pypdf").setLevel(logging.ERROR)
        logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )


@dataclass(frozen=True, slots=True)
class PipelinePaperResult:
    """保存单篇论文全流程的结果、产物地址或错误。"""

    paper_uid: str
    status: str
    pdf_uri: str
    markdown_uri: str | None = None
    document_json_uri: str | None = None
    section_count: int = 0
    text_chunk_count: int = 0
    image_chunk_count: int = 0
    table_chunk_count: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        """将单篇处理结果转换为 JSON 兼容字典。"""

        return asdict(self)


def build_asset_resolver(uploaded_assets: dict[Path, str]):
    """创建把 MinerU 本地资源绝对路径替换为 MinIO URI 的解析函数。"""

    normalised = {path.resolve(): uri for path, uri in uploaded_assets.items()}

    def resolve(path: str) -> str:
        """查找单个本地资源对应的 MinIO URI，未上传时保留原路径。"""

        return normalised.get(Path(path).resolve(), path)

    return resolve


async def store_mineru_outputs(
    paper_uid: str,
    result: MinerUResult,
    storage: MinioClient,
    bucket_name: str,
) -> tuple[str, dict[Path, str]]:
    """上传 MinerU Markdown 及同目录资源，并返回 Markdown URI 和路径映射。"""

    uploaded = await storage.upload_directory(
        bucket_name,
        f"papers/{paper_uid}/mineru",
        result.output_dir,
    )
    markdown_uri = uploaded.get(result.markdown_path.resolve())
    if markdown_uri is None:
        raise RuntimeError(f"MinerU Markdown 未进入 MinIO 上传结果：{result.markdown_path}")
    return markdown_uri, uploaded


async def process_downloaded_paper(
    paper: DownloadedPaper,
    *,
    mineru_output_root: Path,
    output_root: Path,
    storage: MinioClient,
    database: DatabaseService,
    bucket_name: str,
    use_llm_metadata: bool,
    force_mineru: bool,
) -> PipelinePaperResult:
    """依次完成单篇论文的 B–E 阶段，并在失败时更新 Paper 状态。"""

    writer = DatabaseWriter(database.client)
    started_at = time.perf_counter()
    LOGGER.info("论文开始 | paper_uid=%s | pdf=%s", paper.paper_uid, paper.local_pdf_path.name)
    try:
        LOGGER.info(
            "论文 %s | 阶段 B MinerU 开始 | %s",
            paper.paper_uid,
            "强制重新解析" if force_mineru else "优先复用已有产物",
        )
        mineru_results = await asyncio.to_thread(
            run_mineru_pipeline,
            [paper.local_pdf_path],
            mineru_output_root,
            reuse_existing=not force_mineru,
        )
        mineru_result = mineru_results[0]
        markdown_uri, uploaded_assets = await store_mineru_outputs(
            paper.paper_uid,
            mineru_result,
            storage,
            bucket_name,
        )
        await writer.update_paper_stage(
            paper.paper_uid,
            "mineru_parsed",
            markdown_path=markdown_uri,
            metadata={
                "mineru_batch_id": mineru_result.batch_id,
                "mineru_output_file_count": len(uploaded_assets),
            },
        )
        LOGGER.info(
            "论文 %s | 阶段 B MinerU 完成 | batch_id=%s | 上传产物=%d | 耗时=%s",
            paper.paper_uid,
            mineru_result.batch_id or "reused",
            len(uploaded_assets),
            _format_elapsed(time.perf_counter() - started_at),
        )

        LOGGER.info("论文 %s | 阶段 C Markdown 解析开始", paper.paper_uid)
        markdown_parser = AMarkdownParser(use_llm_basic_info=use_llm_metadata)
        stage03 = await asyncio.to_thread(markdown_parser.parse_file, mineru_result.markdown_path)
        stage03["paper_uid"] = paper.paper_uid
        paper_output = output_root / paper.paper_uid
        stage03_path = paper_output / "stage_03_markdown_sections.json"
        write_json(stage03_path, stage03)
        stage03_uri = await storage.upload_file(
            bucket_name,
            f"papers/{paper.paper_uid}/derived/{stage03_path.name}",
            stage03_path,
            content_type="application/json",
        )
        await writer.update_paper_stage(
            paper.paper_uid,
            "structured",
            metadata={"stage03_json_uri": stage03_uri},
        )
        LOGGER.info(
            "论文 %s | 阶段 C Markdown 解析完成 | 输出=%s | 耗时=%s",
            paper.paper_uid,
            stage03_path.name,
            _format_elapsed(time.perf_counter() - started_at),
        )

        LOGGER.info("论文 %s | 阶段 D 文档树分片开始", paper.paper_uid)
        settings = get_settings()
        document = build_document_tree(
            stage03,
            source_json=stage03_path,
            max_text_chunk_length=settings.chunk_size,
            text_chunk_overlap=settings.chunk_overlap,
            asset_uri_resolver=build_asset_resolver(uploaded_assets),
        )
        stage04_path = paper_output / "stage_04_document_tree.json"
        write_document_tree(document, stage04_path)
        stage04_uri = await storage.upload_file(
            bucket_name,
            f"papers/{paper.paper_uid}/derived/{stage04_path.name}",
            stage04_path,
            content_type="application/json",
        )
        await writer.update_paper_stage(
            paper.paper_uid,
            "chunked",
            document_json_path=stage04_uri,
            metadata={"stage04_json_uri": stage04_uri},
        )
        LOGGER.info(
            "论文 %s | 阶段 D 文档树分片完成 | 输出=%s | 耗时=%s",
            paper.paper_uid,
            stage04_path.name,
            _format_elapsed(time.perf_counter() - started_at),
        )

        LOGGER.info("论文 %s | 阶段 E PostgreSQL 入库开始", paper.paper_uid)
        existing_paper = await database.papers.get_by_uid(paper.paper_uid)
        persisted = await writer.persist_document(
            document,
            pdf_path=paper.pdf_uri,
            markdown_path=markdown_uri,
            document_json_path=stage04_uri,
            source_url=existing_paper.source_url if existing_paper else None,
            page_count=paper.page_count,
            file_metadata={
                **(existing_paper.file_metadata if existing_paper and existing_paper.file_metadata else {}),
                "stage03_json_uri": stage03_uri,
                "stage04_json_uri": stage04_uri,
            },
        )
        LOGGER.info(
            "论文完成 | paper_uid=%s | sections=%d | text_chunks=%d | image_chunks=%d | table_chunks=%d | 耗时=%s",
            paper.paper_uid,
            persisted.section_count,
            persisted.text_chunk_count,
            persisted.image_chunk_count,
            persisted.table_chunk_count,
            _format_elapsed(time.perf_counter() - started_at),
        )
        return _success_result(paper, markdown_uri, stage04_uri, persisted)
    except Exception as exc:
        LOGGER.error(
            "论文失败 | paper_uid=%s | %s: %s | 耗时=%s",
            paper.paper_uid,
            type(exc).__name__,
            exc,
            _format_elapsed(time.perf_counter() - started_at),
        )
        LOGGER.debug("论文失败堆栈 | paper_uid=%s", paper.paper_uid, exc_info=True)
        try:
            await writer.mark_failed(paper.paper_uid, exc)
        except Exception as status_exc:
            # 保留原始解析错误，避免状态回写错误遮蔽真正失败原因。
            LOGGER.warning("失败状态回写失败 | paper_uid=%s | %s", paper.paper_uid, status_exc)
        return PipelinePaperResult(
            paper_uid=paper.paper_uid,
            status="failed",
            pdf_uri=paper.pdf_uri,
            error=f"{type(exc).__name__}: {exc}",
        )


def _success_result(
    paper: DownloadedPaper,
    markdown_uri: str,
    document_json_uri: str,
    persisted: PersistResult,
) -> PipelinePaperResult:
    """把阶段 E 入库统计转换为全流程成功结果。"""

    return PipelinePaperResult(
        paper_uid=paper.paper_uid,
        status="completed",
        pdf_uri=paper.pdf_uri,
        markdown_uri=markdown_uri,
        document_json_uri=document_json_uri,
        section_count=persisted.section_count,
        text_chunk_count=persisted.text_chunk_count,
        image_chunk_count=persisted.image_chunk_count,
        table_chunk_count=persisted.table_chunk_count,
    )


async def run_ingestion_pipeline(
    *,
    count: int = DEFAULT_DOWNLOAD_COUNT,
    remote_directory: str = DEFAULT_REMOTE_PDF_DIR,
    local_pdf_directory: str | Path = DEFAULT_LOCAL_PDF_DIR,
    mineru_output_root: str | Path = DEFAULT_MINERU_OUTPUT_DIR,
    output_root: str | Path = DEFAULT_PIPELINE_OUTPUT_ROOT,
    recursive: bool = False,
    overwrite: bool = False,
    force_mineru: bool = False,
    skip_completed: bool = True,
    use_llm_metadata: bool = True,
    ssh_config: SSHConfig | None = None,
    storage: MinioClient | None = None,
    database: DatabaseService | None = None,
) -> list[PipelinePaperResult]:
    """执行论文的 A–E 全流程并隔离单篇论文失败，同时输出批处理进度日志。"""

    pipeline_started_at = time.perf_counter()
    LOGGER.info(
        "流水线开始 | count=%s | remote=%s | recursive=%s | overwrite=%s | force_mineru=%s | skip_completed=%s | use_llm=%s",
        count,
        remote_directory,
        recursive,
        overwrite,
        force_mineru,
        skip_completed,
        use_llm_metadata,
    )
    selected_storage = storage or get_minio_client()
    selected_database = database or get_database_service()
    bucket_name = get_settings().minio_paper_bucket
    LOGGER.info("阶段 A 开始 | 扫描、下载并登记 PDF")
    try:
        downloaded = await download_store_and_register(
            count=count,
            local_directory=local_pdf_directory,
            remote_directory=remote_directory,
            recursive=recursive,
            overwrite=overwrite,
            ssh_config=ssh_config,
            minio_client=selected_storage,
            database=selected_database,
            bucket_name=bucket_name,
            skip_completed=skip_completed,
        )
    except Exception as exc:
        LOGGER.error(
            "阶段 A 失败 | %s: %s | 已耗时=%s",
            type(exc).__name__,
            exc,
            _format_elapsed(time.perf_counter() - pipeline_started_at),
        )
        LOGGER.debug("阶段 A 失败堆栈", exc_info=True)
        raise
    total = len(downloaded)
    LOGGER.info(
        "阶段 A 完成 | 待处理论文=%d | 耗时=%s",
        total,
        _format_elapsed(time.perf_counter() - pipeline_started_at),
    )
    if not downloaded:
        LOGGER.warning("未找到可处理的 PDF，流水线结束")
        return []

    results: list[PipelinePaperResult] = []
    completed_count = 0
    failed_count = 0
    processing_started_at = time.perf_counter()
    for index, paper in enumerate(downloaded, start=1):
        result = await process_downloaded_paper(
            paper,
            mineru_output_root=Path(mineru_output_root).expanduser().resolve(),
            output_root=Path(output_root).expanduser().resolve(),
            storage=selected_storage,
            database=selected_database,
            bucket_name=bucket_name,
            use_llm_metadata=use_llm_metadata,
            force_mineru=force_mineru,
        )
        results.append(result)
        if result.status == "completed":
            completed_count += 1
        elif result.status == "failed":
            failed_count += 1
        processed_elapsed = time.perf_counter() - processing_started_at
        average_elapsed = processed_elapsed / index
        eta_seconds = average_elapsed * (total - index)
        LOGGER.info(
            "进度 %d/%d (%.1f%%) | paper_uid=%s | status=%s | completed=%d | failed=%d | 已耗时=%s | 预计剩余=%s",
            index,
            total,
            index / total * 100,
            paper.paper_uid,
            result.status,
            completed_count,
            failed_count,
            _format_elapsed(processed_elapsed),
            _format_elapsed(eta_seconds),
        )
    LOGGER.info(
        "流水线完成 | total=%d | completed=%d | failed=%d | 总耗时=%s",
        total,
        completed_count,
        failed_count,
        _format_elapsed(time.perf_counter() - pipeline_started_at),
    )
    return results


def write_pipeline_summary(results: Sequence[PipelinePaperResult], output_root: str | Path) -> Path:
    """写出全流程结果摘要，便于排查单篇失败和断点重跑。"""

    target = Path(output_root).expanduser().resolve() / "stage_05_ingestion_summary.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps([result.to_dict() for result in results], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target


def build_argument_parser() -> argparse.ArgumentParser:
    """创建全流程命令行参数解析器。"""

    parser = argparse.ArgumentParser(description="运行 SSH -> MinIO -> MinerU -> 章节/Chunk -> PostgreSQL 全流程")
    parser.add_argument("--count", type=int, default=DEFAULT_DOWNLOAD_COUNT, help="处理论文数量，默认 -1（全量处理）")
    parser.add_argument("--remote-directory", default=DEFAULT_REMOTE_PDF_DIR, help="远程 PDF 根目录")
    parser.add_argument("--local-pdf-directory", type=Path, default=DEFAULT_LOCAL_PDF_DIR, help="本地 PDF 目录")
    parser.add_argument("--mineru-output", type=Path, default=DEFAULT_MINERU_OUTPUT_DIR, help="MinerU 本地产物目录")
    parser.add_argument("--output", type=Path, default=DEFAULT_PIPELINE_OUTPUT_ROOT, help="结构化 JSON 输出目录")
    parser.add_argument("--recursive", action="store_true", help="递归扫描远程 PDF 子目录")
    parser.add_argument("--overwrite", action="store_true", help="覆盖本地已有 PDF")
    parser.add_argument("--force-mineru", action="store_true", help="忽略本地 MinerU 结果并重新解析")
    parser.add_argument("--reparse-completed", action="store_true", help="重新解析数据库中已完整入库的论文")
    parser.add_argument("--skip-llm", action="store_true", help="仅用规则提取首页元数据")
    parser.add_argument("--log-level", type=str.upper, choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")
    parser.add_argument("--print-results", action="store_true", help="结束后打印每篇结果 JSON，默认只打印汇总")
    return parser


async def async_main(argv: Sequence[str] | None = None) -> int:
    """运行总管线 CLI、写出摘要并按失败情况返回退出码。"""

    args = build_argument_parser().parse_args(argv)
    configure_logging(args.log_level)
    database = get_database_service()
    storage = get_minio_client()
    try:
        results = await run_ingestion_pipeline(
            count=args.count,
            remote_directory=args.remote_directory,
            local_pdf_directory=args.local_pdf_directory,
            mineru_output_root=args.mineru_output,
            output_root=args.output,
            recursive=args.recursive,
            overwrite=args.overwrite,
            force_mineru=args.force_mineru,
            skip_completed=not args.reparse_completed,
            use_llm_metadata=not args.skip_llm,
            storage=storage,
            database=database,
        )
        summary_path = write_pipeline_summary(results, args.output)
        completed_count = sum(result.status == "completed" for result in results)
        failed_count = sum(result.status == "failed" for result in results)
        if args.print_results:
            print(json.dumps([result.to_dict() for result in results], ensure_ascii=False, indent=2))
        else:
            print(
                json.dumps(
                    {
                        "total": len(results),
                        "completed": completed_count,
                        "failed": failed_count,
                        "summary_path": str(summary_path),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        print(f"流程摘要：{summary_path}")
        return 1 if any(result.status == "failed" for result in results) else 0
    except Exception as exc:
        LOGGER.error("流水线异常终止 | %s: %s", type(exc).__name__, exc)
        LOGGER.debug("流水线异常堆栈", exc_info=True)
        return 1
    finally:
        await database.close()
        await storage.close()


def main(argv: Sequence[str] | None = None) -> int:
    """提供同步总管线入口。"""

    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
