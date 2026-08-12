import json
import re
import uuid
from pathlib import Path

import aiofiles
from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from app.core.config import get_settings
from app.repositories.papers import get_paper
from app.schemas.paper import IngestionJobRead, PaperMetadata, PaperRead, UrlIngestRequest
from app.services.ingestion import create_job, get_job, retry_job

router = APIRouter()


@router.post("/ingestions/upload", response_model=IngestionJobRead, status_code=status.HTTP_202_ACCEPTED)
async def upload_paper(file: UploadFile = File(...), metadata: str = Form("{}"), enable_table: bool = Form(True),
                       enable_formula: bool = Form(True)) -> IngestionJobRead:
    if file.content_type != "application/pdf" and not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(415, "第一阶段仅支持 PDF 文件")
    try:
        meta = PaperMetadata.model_validate(json.loads(metadata))
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(422, f"metadata 不是有效 JSON: {exc}") from exc
    settings = get_settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^\w.\-]+", "_", Path(file.filename or "paper.pdf").name)
    target = settings.upload_dir / f"{uuid.uuid4().hex}_{safe_name}"
    async with aiofiles.open(target, "wb") as output:
        while chunk := await file.read(1024 * 1024):
            await output.write(chunk)
    return create_job(target, None, safe_name, meta, enable_table, enable_formula)


@router.post("/ingestions/url", response_model=IngestionJobRead, status_code=status.HTTP_202_ACCEPTED)
async def ingest_url(request: UrlIngestRequest) -> IngestionJobRead:
    file_name = Path(request.url.path or "").name or "remote.pdf"
    return create_job(None, str(request.url), file_name, request.metadata, request.enable_table, request.enable_formula)


@router.get("/ingestions/{job_id}", response_model=IngestionJobRead)
async def ingestion_status(job_id: str) -> IngestionJobRead:
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "入库任务不存在")
    return job


@router.post("/ingestions/{job_id}/retry", response_model=IngestionJobRead, status_code=status.HTTP_202_ACCEPTED)
async def retry_ingestion(job_id: str) -> IngestionJobRead:
    job = retry_job(job_id)
    if not job:
        raise HTTPException(409, "仅失败任务可以重试")
    return job


@router.get("/{paper_uid}", response_model=PaperRead)
async def paper_detail(paper_uid: str) -> PaperRead:
    paper = await get_paper(paper_uid)
    if not paper:
        raise HTTPException(404, "论文不存在")
    return PaperRead.model_validate(paper)
