"""GeoPaperAgent 论文解析入库总管线。

本文件依次编排 A–E：SSH 下载、PDF/产物上传 MinIO、MinerU 解析、Markdown
章节化、文档树分片以及 PostgreSQL 事务入库。默认处理远程目录中的前 3 篇 PDF。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

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
    try:
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
        return _success_result(paper, markdown_uri, stage04_uri, persisted)
    except Exception as exc:
        try:
            await writer.mark_failed(paper.paper_uid, exc)
        except Exception:
            # 保留原始解析错误，避免状态回写错误遮蔽真正失败原因。
            pass
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
    use_llm_metadata: bool = True,
    ssh_config: SSHConfig | None = None,
    storage: MinioClient | None = None,
    database: DatabaseService | None = None,
) -> list[PipelinePaperResult]:
    """执行默认 3 篇论文的 A–E 全流程，并隔离单篇论文失败。"""

    selected_storage = storage or get_minio_client()
    selected_database = database or get_database_service()
    bucket_name = get_settings().minio_paper_bucket
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
    )
    results: list[PipelinePaperResult] = []
    for paper in downloaded:
        results.append(
            await process_downloaded_paper(
                paper,
                mineru_output_root=Path(mineru_output_root).expanduser().resolve(),
                output_root=Path(output_root).expanduser().resolve(),
                storage=selected_storage,
                database=selected_database,
                bucket_name=bucket_name,
                use_llm_metadata=use_llm_metadata,
                force_mineru=force_mineru,
            )
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
    parser.add_argument("--count", type=int, default=DEFAULT_DOWNLOAD_COUNT, help="处理论文数量，默认 3")
    parser.add_argument("--remote-directory", default=DEFAULT_REMOTE_PDF_DIR, help="远程 PDF 根目录")
    parser.add_argument("--local-pdf-directory", type=Path, default=DEFAULT_LOCAL_PDF_DIR, help="本地 PDF 目录")
    parser.add_argument("--mineru-output", type=Path, default=DEFAULT_MINERU_OUTPUT_DIR, help="MinerU 本地产物目录")
    parser.add_argument("--output", type=Path, default=DEFAULT_PIPELINE_OUTPUT_ROOT, help="结构化 JSON 输出目录")
    parser.add_argument("--recursive", action="store_true", help="递归扫描远程 PDF 子目录")
    parser.add_argument("--overwrite", action="store_true", help="覆盖本地已有 PDF")
    parser.add_argument("--force-mineru", action="store_true", help="忽略本地 MinerU 结果并重新解析")
    parser.add_argument("--skip-llm", action="store_true", help="仅用规则提取首页元数据")
    return parser


async def async_main(argv: Sequence[str] | None = None) -> int:
    """运行总管线 CLI、写出摘要并按失败情况返回退出码。"""

    args = build_argument_parser().parse_args(argv)
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
            use_llm_metadata=not args.skip_llm,
            storage=storage,
            database=database,
        )
        summary_path = write_pipeline_summary(results, args.output)
        print(json.dumps([result.to_dict() for result in results], ensure_ascii=False, indent=2))
        print(f"流程摘要：{summary_path}")
        return 1 if any(result.status == "failed" for result in results) else 0
    finally:
        await database.close()
        await storage.close()


def main(argv: Sequence[str] | None = None) -> int:
    """提供同步总管线入口。"""

    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
