import asyncio
import hashlib
import shutil
import uuid
from pathlib import Path

from app.core.config import get_settings
from app.parsers.mineru_output import parse_mineru_output
from app.repositories.papers import ingest_document
from app.schemas.paper import IngestionJobRead, IngestionStatus, PaperMetadata
from app.services.mineru import MinerUClient

_jobs: dict[str, IngestionJobRead] = {}
_payloads: dict[str, tuple[Path | None, str | None, PaperMetadata, bool, bool]] = {}


def get_job(job_id: str) -> IngestionJobRead | None:
    return _jobs.get(job_id)


def _update(job_id: str, status: IngestionStatus, progress: int, message: str) -> None:
    _jobs[job_id] = _jobs[job_id].model_copy(update={"status": status, "progress": progress, "message": message})


def create_job(path: Path | None, url: str | None, file_name: str, metadata: PaperMetadata,
               enable_table: bool, enable_formula: bool) -> IngestionJobRead:
    job_id = uuid.uuid4().hex
    identity = path.read_bytes() if path else url.encode()  # type: ignore[union-attr]
    paper_uid = "P" + hashlib.sha256(identity).hexdigest()[:16].upper()
    job = IngestionJobRead(id=job_id, paper_uid=paper_uid, file_name=file_name,
                           status=IngestionStatus.PENDING, progress=0, message="等待处理")
    _jobs[job_id] = job
    _payloads[job_id] = (path, url, metadata, enable_table, enable_formula)
    asyncio.create_task(run_job(job_id))
    return job


async def run_job(job_id: str) -> None:
    settings = get_settings()
    path, url, metadata, enable_table, enable_formula = _payloads[job_id]
    client = MinerUClient(settings.mineru_base_url, settings.mineru_api_key, settings.mineru_timeout)
    try:
        if not settings.mineru_api_key:
            raise RuntimeError("minerU_API_KEY 未配置")
        _update(job_id, IngestionStatus.UPLOADING, 10, "正在提交 MinerU")
        batch = await (client.submit_file(path, _jobs[job_id].paper_uid, settings.mineru_model_version,
                                          enable_table, enable_formula) if path else
                       client.submit_url(url or "", _jobs[job_id].paper_uid, settings.mineru_model_version,
                                         enable_table, enable_formula))
        _jobs[job_id] = _jobs[job_id].model_copy(update={"mineru_batch_id": batch})
        _update(job_id, IngestionStatus.PARSING, 30, "MinerU 正在解析")
        result = await client.wait(batch, settings.mineru_poll_interval, settings.mineru_max_wait_seconds)
        _update(job_id, IngestionStatus.DOWNLOADING, 65, "正在下载解析结果")
        output = settings.mineru_output_dir / _jobs[job_id].paper_uid
        if output.exists():
            shutil.rmtree(output)
        await client.download_and_extract(result["full_zip_url"], output)
        _update(job_id, IngestionStatus.INGESTING, 80, "正在构建章节和 Chunk 并入库")
        document = parse_mineru_output(output)
        paper_id = await ingest_document(_jobs[job_id].paper_uid, path, output, document, metadata,
                                         settings.chunk_size, settings.chunk_overlap)
        _jobs[job_id] = _jobs[job_id].model_copy(update={"status": IngestionStatus.COMPLETED,
            "progress": 100, "message": "解析入库完成", "paper_id": paper_id})
    except Exception as exc:
        _jobs[job_id] = _jobs[job_id].model_copy(update={"status": IngestionStatus.FAILED,
            "message": "解析入库失败", "error": str(exc)})


def retry_job(job_id: str) -> IngestionJobRead | None:
    if job_id not in _jobs or _jobs[job_id].status != IngestionStatus.FAILED:
        return None
    _jobs[job_id] = _jobs[job_id].model_copy(update={"status": IngestionStatus.PENDING, "progress": 0, "error": None})
    asyncio.create_task(run_job(job_id))
    return _jobs[job_id]
