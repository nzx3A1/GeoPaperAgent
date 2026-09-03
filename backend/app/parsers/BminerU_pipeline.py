"""阶段 B：把本地 PDF 提交 MinerU，并下载、整理 Markdown 解析产物。

本模块提供有明确返回值和超时控制的同步 API，便于异步总管线通过
``asyncio.to_thread`` 调用；也支持在 ``backend`` 目录中直接或以模块方式运行。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

# 支持直接执行 ``python app/parsers/BminerU_pipeline.py``。
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx

from app.config import get_settings
from app.config.model_config import load_model_settings

BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PDF_DIR = BACKEND_ROOT / "data" / "raw_pdf"
DEFAULT_OUTPUT_DIR = BACKEND_ROOT / "data" / "mineru_output"
MAX_FILES_PER_BATCH = 200
DEFAULT_DOWNLOAD_MAX_ATTEMPTS = 4
DEFAULT_DOWNLOAD_RETRY_BACKOFF = 2.0
TERMINAL_STATES = {"done", "failed"}
MARKUP_TAG_RE = re.compile(r"</?(?:sup|sub)>", re.IGNORECASE)


class MinerUError(RuntimeError):
    """表示 MinerU 提交、轮询、下载或产物校验失败。"""


@dataclass(frozen=True, slots=True)
class MinerUResult:
    """保存一篇 PDF 对应的 MinerU 结果目录和 Markdown 路径。"""

    source_pdf: Path
    output_dir: Path
    markdown_path: Path
    batch_id: str | None

    def to_dict(self) -> dict[str, str | None]:
        """将 MinerU 结果转换为 JSON 兼容字典。"""

        return {
            "source_pdf": str(self.source_pdf),
            "output_dir": str(self.output_dir),
            "markdown_path": str(self.markdown_path),
            "batch_id": self.batch_id,
        }


class MinerUClient:
    """封装 MinerU 批量上传、状态轮询和结果下载。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model_version: str | None = None,
        poll_interval: float | None = None,
        timeout: float | None = None,
        max_wait_seconds: int | None = None,
        download_max_attempts: int = DEFAULT_DOWNLOAD_MAX_ATTEMPTS,
        download_retry_backoff: float = DEFAULT_DOWNLOAD_RETRY_BACKOFF,
        enable_table: bool = True,
        http_client: httpx.Client | None = None,
    ) -> None:
        """从显式参数或项目配置创建 MinerU 客户端。"""

        settings = get_settings()
        legacy_token = load_model_settings().mineru.token
        self.api_key = (api_key or settings.mineru_api_key or legacy_token).strip()
        if not self.api_key:
            raise ValueError("请通过 MINERU_API_KEY 或 MINERU_TOKEN 配置 MinerU 密钥")
        self.base_url = (base_url or settings.mineru_base_url).rstrip("/")
        self.model_version = model_version or settings.mineru_model_version
        self.poll_interval = poll_interval if poll_interval is not None else settings.mineru_poll_interval
        self.timeout = timeout if timeout is not None else settings.mineru_timeout
        self.max_wait_seconds = max_wait_seconds if max_wait_seconds is not None else settings.mineru_max_wait_seconds
        if self.poll_interval <= 0 or self.timeout <= 0 or self.max_wait_seconds <= 0:
            raise ValueError("MinerU 的轮询、请求和总等待超时必须大于 0")
        if download_max_attempts <= 0 or download_retry_backoff < 0:
            raise ValueError("MinerU 下载重试次数必须大于 0，退避时间不能小于 0")
        self.download_max_attempts = download_max_attempts
        self.download_retry_backoff = download_retry_backoff
        self.enable_table = enable_table
        self._owns_http_client = http_client is None
        self.http = http_client or httpx.Client(
            timeout=self.timeout,
            follow_redirects=True,
            trust_env=False,
        )

    def close(self) -> None:
        """关闭由当前对象创建的 HTTP 连接池。"""

        if self._owns_http_client:
            self.http.close()

    def __enter__(self) -> MinerUClient:
        """进入上下文并返回当前 MinerU 客户端。"""

        return self

    def __exit__(self, *_exc_info: object) -> None:
        """退出上下文时关闭内部 HTTP 连接池。"""

        self.close()

    @property
    def headers(self) -> dict[str, str]:
        """返回 MinerU JSON API 所需的鉴权请求头。"""

        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def submit(self, pdf_paths: Sequence[Path]) -> tuple[str, dict[str, Path]]:
        """创建 MinerU 批次并上传 PDF，返回批次 ID 与上传文件名映射。"""

        if not pdf_paths:
            raise ValueError("提交 MinerU 的 PDF 列表不能为空")
        upload_map = {_upload_name(path, index): path.resolve() for index, path in enumerate(pdf_paths)}
        payload = {
            "files": [{"name": name, "data_id": str(uuid.uuid4())} for name in upload_map],
            "model_version": self.model_version,
            "enable_table": self.enable_table,
        }
        response = self.http.post(f"{self.base_url}/api/v4/file-urls/batch", headers=self.headers, json=payload)
        data = _read_api_data(response, "创建 MinerU 批次")
        batch_id = str(data.get("batch_id") or "").strip()
        upload_urls = data.get("file_urls")
        if not batch_id or not isinstance(upload_urls, list) or len(upload_urls) != len(upload_map):
            raise MinerUError("MinerU 返回的 batch_id 或上传 URL 数量不正确")

        for (upload_name, source), upload_url in zip(upload_map.items(), upload_urls, strict=True):
            with source.open("rb") as pdf_file:
                upload_response = self.http.put(str(upload_url), content=pdf_file)
            if upload_response.status_code not in {200, 201, 204}:
                raise MinerUError(
                    f"上传 {upload_name} 失败：HTTP {upload_response.status_code} {upload_response.text[:300]}"
                )
        return batch_id, upload_map

    def query(self, batch_id: str) -> list[dict[str, object]]:
        """查询一个 MinerU 批次并返回文件级状态列表。"""

        response = self.http.get(
            f"{self.base_url}/api/v4/extract-results/batch/{batch_id}",
            headers=self.headers,
        )
        data = _read_api_data(response, f"查询 MinerU 批次 {batch_id}")
        results = data.get("extract_result", [])
        if not isinstance(results, list):
            raise MinerUError(f"MinerU 批次 {batch_id} 的 extract_result 不是列表")
        return [dict(item) for item in results if isinstance(item, Mapping)]

    def wait_until_finished(self, batch_id: str) -> list[dict[str, object]]:
        """轮询批次直到全部完成或失败，并在超过总时限时终止。"""

        deadline = time.monotonic() + self.max_wait_seconds
        last_results: list[dict[str, object]] = []
        while time.monotonic() < deadline:
            last_results = self.query(batch_id)
            if last_results and all(str(item.get("state", "")).lower() in TERMINAL_STATES for item in last_results):
                return last_results
            time.sleep(min(self.poll_interval, max(0.0, deadline - time.monotonic())))
        states = {str(item.get("file_name")): str(item.get("state")) for item in last_results}
        raise TimeoutError(f"MinerU 批次 {batch_id} 在 {self.max_wait_seconds} 秒内未完成：{states}")

    def download_result(self, item: Mapping[str, object], source_pdf: Path, output_root: Path) -> MinerUResult:
        """下载并安全解压一个已完成结果，返回实际的 ``full.md`` 路径。"""

        state = str(item.get("state") or "").lower()
        if state != "done":
            message = item.get("err_msg") or item.get("error") or "未知原因"
            raise MinerUError(f"MinerU 解析失败：{source_pdf.name}，{message}")
        zip_url = str(item.get("full_zip_url") or "").strip()
        if not zip_url:
            raise MinerUError(f"MinerU 未返回 {source_pdf.name} 的 full_zip_url")

        target_dir = output_root / source_pdf.stem
        target_dir.mkdir(parents=True, exist_ok=True)
        archive_path = output_root / f".{source_pdf.stem}.{uuid.uuid4().hex}.zip"
        try:
            self._download_archive(zip_url, archive_path)
            _safe_extract_zip(archive_path, target_dir)
        except (OSError, zipfile.BadZipFile) as exc:
            raise MinerUError(f"下载或解压 {source_pdf.name} 的 MinerU 结果失败：{exc}") from exc
        finally:
            archive_path.unlink(missing_ok=True)

        markdown_path = _find_full_markdown(target_dir)
        _clean_markdown_tags(markdown_path)
        return MinerUResult(
            source_pdf=source_pdf.resolve(),
            output_dir=markdown_path.parent.resolve(),
            markdown_path=markdown_path.resolve(),
            batch_id=None,
        )

    def _download_archive(self, zip_url: str, archive_path: Path) -> None:
        """下载结果 ZIP，并对 CDN 瞬时网络错误执行有限次数的指数退避重试。"""

        last_error: httpx.HTTPError | OSError | None = None
        for attempt in range(1, self.download_max_attempts + 1):
            try:
                with self.http.stream("GET", zip_url) as response:
                    response.raise_for_status()
                    with archive_path.open("wb") as archive:
                        for block in response.iter_bytes(1024 * 1024):
                            archive.write(block)
                return
            except (httpx.HTTPError, OSError) as exc:
                last_error = exc
                archive_path.unlink(missing_ok=True)
                if attempt < self.download_max_attempts:
                    delay = self.download_retry_backoff * (2 ** (attempt - 1))
                    time.sleep(delay)
        raise MinerUError(
            f"下载 MinerU 结果失败，已重试 {self.download_max_attempts} 次：{last_error}"
        ) from last_error


def _read_api_data(response: httpx.Response, operation: str) -> dict[str, object]:
    """校验 MinerU HTTP/业务状态并返回 data 对象。"""

    try:
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise MinerUError(f"{operation}失败：HTTP {response.status_code} {response.text[:300]}") from exc
    if not isinstance(payload, Mapping) or payload.get("code") != 0:
        message = payload.get("msg") if isinstance(payload, Mapping) else "响应不是 JSON 对象"
        raise MinerUError(f"{operation}失败：{message}")
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise MinerUError(f"{operation}失败：响应缺少 data 对象")
    return dict(data)


def _upload_name(path: Path, index: int) -> str:
    """生成批次内不重复且仍便于识别来源的 PDF 上传文件名。"""

    safe_stem = re.sub(r"[^0-9A-Za-z._\-\u4e00-\u9fff]+", "_", path.stem).strip("._") or "paper"
    stable_suffix = uuid.uuid5(uuid.NAMESPACE_URL, str(path.resolve())).hex[:8]
    return f"{safe_stem}-{index:03d}-{stable_suffix}.pdf"


def _safe_extract_zip(archive_path: Path, destination: Path) -> None:
    """校验 ZIP 成员目标路径后解压，阻止目录穿越覆盖工作区文件。"""

    root = destination.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            target = (root / member.filename).resolve()
            if not target.is_relative_to(root):
                raise MinerUError(f"MinerU ZIP 包含不安全路径：{member.filename}")
        archive.extractall(root)


def _find_full_markdown(output_dir: Path) -> Path:
    """在解压目录中定位唯一的首选 Markdown 文件。"""

    direct = output_dir / "full.md"
    if direct.is_file():
        return direct
    candidates = sorted(output_dir.rglob("full.md"))
    if not candidates:
        candidates = sorted(output_dir.rglob("*.md"))
    if not candidates:
        raise MinerUError(f"MinerU 结果中没有 Markdown 文件：{output_dir}")
    return candidates[0]


def _clean_markdown_tags(markdown_path: Path) -> None:
    """清理 MinerU Markdown 中影响后续元数据规则解析的上下标标签。"""

    text = markdown_path.read_text(encoding="utf-8")
    cleaned = MARKUP_TAG_RE.sub("", text)
    if cleaned != text:
        markdown_path.write_text(cleaned, encoding="utf-8")


def find_pdf_files(input_path: str | Path) -> list[Path]:
    """把单个 PDF 或目录解析为按路径稳定排序的 PDF 列表。"""

    path = Path(input_path).expanduser().resolve()
    if path.is_file() and path.suffix.lower() == ".pdf":
        return [path]
    if path.is_dir():
        return sorted((item.resolve() for item in path.rglob("*.pdf") if item.is_file()), key=str)
    raise FileNotFoundError(f"没有找到 PDF 输入：{path}")


def find_existing_result(source_pdf: Path, output_root: Path) -> MinerUResult | None:
    """查找可复用的本地 MinerU 结果，支持产物中存在额外嵌套目录。"""

    target_dir = output_root / source_pdf.stem
    if not target_dir.is_dir():
        return None
    try:
        markdown_path = _find_full_markdown(target_dir)
    except MinerUError:
        return None
    return MinerUResult(
        source_pdf=source_pdf.resolve(),
        output_dir=markdown_path.parent.resolve(),
        markdown_path=markdown_path.resolve(),
        batch_id=None,
    )


def _chunks(items: Sequence[Path], size: int) -> Iterable[Sequence[Path]]:
    """把 PDF 列表按 MinerU 单批上限分组。"""

    for start in range(0, len(items), size):
        yield items[start : start + size]


def run_mineru_pipeline(
    pdf_paths: Sequence[str | Path],
    output_root: str | Path = DEFAULT_OUTPUT_DIR,
    *,
    reuse_existing: bool = True,
    client: MinerUClient | None = None,
) -> list[MinerUResult]:
    """批量解析 PDF 并按输入顺序返回每篇论文的 MinerU 结果。"""

    sources = [Path(path).expanduser().resolve() for path in pdf_paths]
    if not sources:
        return []
    missing = [str(path) for path in sources if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"以下 PDF 不存在：{missing}")
    output_dir = Path(output_root).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    results_by_source: dict[Path, MinerUResult] = {}
    pending: list[Path] = []
    for source in sources:
        existing = find_existing_result(source, output_dir) if reuse_existing else None
        if existing is not None:
            results_by_source[source] = existing
        else:
            pending.append(source)

    if not pending:
        return [results_by_source[source] for source in sources]

    mineru = client or MinerUClient()
    owns_client = client is None
    try:
        for group in _chunks(pending, MAX_FILES_PER_BATCH):
            batch_id, upload_map = mineru.submit(group)
            batch_results = mineru.wait_until_finished(batch_id)
            snapshot_path = output_dir / f"extract_results_{batch_id}.json"
            snapshot_path.write_text(json.dumps(batch_results, ensure_ascii=False, indent=2), encoding="utf-8")
            returned_by_name = {str(item.get("file_name")): item for item in batch_results}
            for upload_name, source in upload_map.items():
                item = returned_by_name.get(upload_name)
                if item is None:
                    raise MinerUError(f"MinerU 批次 {batch_id} 缺少文件结果：{upload_name}")
                result = mineru.download_result(item, source, output_dir)
                results_by_source[source] = MinerUResult(
                    source_pdf=result.source_pdf,
                    output_dir=result.output_dir,
                    markdown_path=result.markdown_path,
                    batch_id=batch_id,
                )
    finally:
        if owns_client:
            mineru.close()
    return [results_by_source[source] for source in sources]


def build_argument_parser() -> argparse.ArgumentParser:
    """创建阶段 B 命令行参数解析器。"""

    parser = argparse.ArgumentParser(description="提交 PDF 到 MinerU 并下载 Markdown 解析结果")
    parser.add_argument("--input", type=Path, default=DEFAULT_PDF_DIR, help="PDF 文件或目录")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR, help="MinerU 结果目录")
    parser.add_argument("--force", action="store_true", help="忽略已有 full.md 并重新解析")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """运行阶段 B CLI 并打印结果 JSON。"""

    args = build_argument_parser().parse_args(argv)
    pdf_paths = find_pdf_files(args.input)
    results = run_mineru_pipeline(pdf_paths, args.output, reuse_existing=not args.force)
    print(json.dumps([result.to_dict() for result in results], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
