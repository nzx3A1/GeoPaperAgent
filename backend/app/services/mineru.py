import asyncio
import io
import zipfile
from pathlib import Path
from typing import Any

import httpx


class MinerUError(RuntimeError):
    pass


class MinerUClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 120) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {api_key}"}
        self.timeout = timeout

    async def _json(self, response: httpx.Response) -> dict[str, Any]:
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            raise MinerUError(payload.get("msg") or "MinerU request failed")
        return payload["data"]

    async def submit_file(self, path: Path, data_id: str, model: str, enable_table: bool, enable_formula: bool) -> str:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/api/v4/file-urls/batch",
                headers=self.headers,
                json={"files": [{"name": path.name, "data_id": data_id}], "model_version": model,
                      "enable_table": enable_table, "enable_formula": enable_formula},
            )
            data = await self._json(response)
            upload = await client.put(data["file_urls"][0], content=path.read_bytes())
            upload.raise_for_status()
            return str(data["batch_id"])

    async def submit_url(self, url: str, data_id: str, model: str, enable_table: bool, enable_formula: bool) -> str:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/api/v4/extract/task/batch",
                headers=self.headers,
                json={"files": [{"url": url, "data_id": data_id}], "model_version": model,
                      "enable_table": enable_table, "enable_formula": enable_formula},
            )
            return str((await self._json(response))["batch_id"])

    async def wait(self, batch_id: str, interval: float, max_wait: int) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + max_wait
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            while asyncio.get_running_loop().time() < deadline:
                data = await self._json(await client.get(
                    f"{self.base_url}/api/v4/extract-results/batch/{batch_id}", headers=self.headers
                ))
                results = data.get("extract_result") or []
                if results and results[0].get("state") == "done":
                    return results[0]
                if results and results[0].get("state") == "failed":
                    raise MinerUError(results[0].get("err_msg") or "MinerU parsing failed")
                await asyncio.sleep(interval)
        raise MinerUError(f"MinerU parsing timed out after {max_wait} seconds")

    async def download_and_extract(self, url: str, destination: Path) -> None:
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
        destination.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            root = destination.resolve()
            for member in archive.infolist():
                target = (destination / member.filename).resolve()
                if target != root and root not in target.parents:
                    raise MinerUError("Unsafe path found in MinerU archive")
            archive.extractall(destination)
