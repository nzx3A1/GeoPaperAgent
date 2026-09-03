"""A–E 论文解析入库管线的核心契约测试。"""

from __future__ import annotations

import io
import zipfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx

from app.parsers.AsshDownload import build_paper_uid, calculate_sha256
from app.parsers.BminerU_pipeline import MinerUClient, run_mineru_pipeline
from app.parsers.CMarkdownParser import AMarkdownParser
from app.parsers.Ddocument_tree_builder import build_document_tree, document_tree_to_dict
from app.parsers.EwriteSQL import DatabaseWriter


def sample_stage03(tmp_path: Path) -> dict[str, Any]:
    """构造同时包含四类模态和子章节的阶段 C 示例数据。"""

    markdown = tmp_path / "full.md"
    markdown.write_text("# 示例", encoding="utf-8")
    image = tmp_path / "images" / "figure.png"
    image.parent.mkdir()
    image.write_bytes(b"image")
    return {
        "paper_uid": "paper-1234567890abcdef1234567890abcdef",
        "title": "示例论文",
        "input_file": str(markdown),
        "basicInformation": {
            "authors": ["张三", "李四"],
            "affiliations": ["地质大学"],
            "keywords": ["储层"],
            "journal": "地质学报",
            "volume": "12",
            "issue": "3",
            "publish_year": 2026,
            "language": "zh",
        },
        "toc": [
            {
                "id": "1",
                "title": "引言",
                "content": "第一段正文。" * 12,
                "raw_text": "原始章节正文",
                "images": [{"path": [str(image)], "caption": "图1 地质图"}],
                "table": [{"content": "<table><tr><td>1</td></tr></table>", "caption": "表1 数据"}],
                "formulas": [{"content": "E=mc^2"}],
                "children": [{"id": "1.1", "title": "背景", "content": "子章节正文"}],
            }
        ],
    }


def test_stage_a_generates_content_stable_uid(tmp_path: Path) -> None:
    """验证阶段 A 使用文件内容哈希生成稳定 Paper ID。"""

    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"same-pdf-content")

    digest = calculate_sha256(pdf)

    assert build_paper_uid(digest) == build_paper_uid(calculate_sha256(pdf))
    assert len(build_paper_uid(digest)) <= 64


def test_stage_b_reuses_existing_markdown_without_network(tmp_path: Path) -> None:
    """验证已有 MinerU 产物可直接复用且不会创建网络客户端。"""

    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"pdf")
    markdown = tmp_path / "mineru" / "paper" / "nested" / "full.md"
    markdown.parent.mkdir(parents=True)
    markdown.write_text("# 已解析", encoding="utf-8")

    results = run_mineru_pipeline([pdf], tmp_path / "mineru")

    assert results[0].markdown_path == markdown.resolve()
    assert results[0].batch_id is None


def test_stage_b_retries_transient_result_download(tmp_path: Path) -> None:
    """验证 MinerU 结果 CDN 首次失败后会重试并正常整理 Markdown。"""

    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w") as archive:
        archive.writestr("nested/full.md", "# 标题\nH<sub>2</sub>O")
    request_count = 0

    def handle_request(_request: httpx.Request) -> httpx.Response:
        """第一次返回临时错误，第二次返回有效的 MinerU ZIP。"""

        nonlocal request_count
        request_count += 1
        if request_count == 1:
            return httpx.Response(503, text="temporary unavailable")
        return httpx.Response(200, content=archive_buffer.getvalue())

    http_client = httpx.Client(transport=httpx.MockTransport(handle_request))
    client = MinerUClient(
        api_key="test-token",
        http_client=http_client,
        download_max_attempts=2,
        download_retry_backoff=0,
    )
    source_pdf = tmp_path / "paper.pdf"
    source_pdf.write_bytes(b"pdf")

    result = client.download_result(
        {"state": "done", "full_zip_url": "https://example.test/result.zip"},
        source_pdf,
        tmp_path / "mineru",
    )

    assert request_count == 2
    assert result.markdown_path.read_text(encoding="utf-8") == "# 标题\nH2O"
    http_client.close()


def test_stage_c_merges_project_llm_metadata() -> None:
    """验证阶段 C 将 LLM 返回的期刊、卷期和作者信息并入基础信息。"""

    markdown = """# 储层研究
张三，李四
（地质大学）
摘要：规则摘要
关键词：储层；成岩作用

## 1 引言
正文。
"""
    parser = AMarkdownParser(
        use_llm_basic_info=True,
        metadata_extractor=lambda _header: {
            "authors": ["张三", "李四"],
            "affiliations": ["地质大学"],
            "journal": "地质学报",
            "volume": "12",
            "issue": "3",
            "publish_year": 2026,
        },
    )

    result = parser.parse_text(markdown)

    assert result["basicInformation"]["journal"] == "地质学报"
    assert result["basicInformation"]["volume"] == "12"
    assert result["basicInformation"]["authors"] == ["张三", "李四"]
    assert result["_stage"] == 3


def test_stage_d_builds_sections_and_all_modalities(tmp_path: Path) -> None:
    """验证阶段 D 生成递归章节和文本、图片、表格、公式 Chunk。"""

    document = build_document_tree(
        sample_stage03(tmp_path),
        max_text_chunk_length=40,
        text_chunk_overlap=5,
        asset_uri_resolver=lambda _path: "minio://papers/figure.png",
    )
    sections = list(document.iter_sections())
    chunks = list(document.iter_chunks())

    assert [section.level for section in sections] == [1, 2]
    assert [section.sort_order for section in sections] == [0, 1]
    assert {chunk.modality for chunk in chunks} == {"text", "image", "table", "formula"}
    assert next(chunk for chunk in chunks if chunk.modality == "image").data["image_path"].startswith("minio://")
    assert document_tree_to_dict(document)["statistics"]["section_count"] == 2


class FakeTransaction:
    """提供 asyncpg 事务上下文所需的最小测试替身。"""

    async def __aenter__(self) -> FakeTransaction:
        """进入伪事务。"""

        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        """退出伪事务。"""


class FakeWriterConnection:
    """记录阶段 E SQL 调用并为 INSERT 返回递增主键。"""

    def __init__(self) -> None:
        """初始化 SQL 记录和主键计数器。"""

        self.calls: list[tuple[str, str, tuple[object, ...]]] = []
        self.next_id = 10

    def transaction(self) -> FakeTransaction:
        """返回伪事务上下文。"""

        return FakeTransaction()

    async def fetchrow(self, sql: str, *args: object) -> dict[str, object]:
        """记录查询并按表类型返回测试主键。"""

        self.calls.append(("fetchrow", sql, args))
        if "INSERT INTO paper" in sql:
            return {"id": 1}
        self.next_id += 1
        return {"id": self.next_id}

    async def execute(self, sql: str, *args: object) -> str:
        """记录执行语句并返回 asyncpg 风格状态。"""

        self.calls.append(("execute", sql, args))
        return "OK"


class FakeWriterClient:
    """向 DatabaseWriter 提供单个伪连接。"""

    def __init__(self, connection: FakeWriterConnection) -> None:
        """保存待复用的伪连接。"""

        self.postgres_connection = connection

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[FakeWriterConnection]:
        """以异步上下文形式返回伪连接。"""

        yield self.postgres_connection


async def test_stage_e_persists_document_in_one_transaction(tmp_path: Path) -> None:
    """验证阶段 E 写入全部记录并生成文本前后链更新。"""

    document = build_document_tree(sample_stage03(tmp_path), max_text_chunk_length=40, text_chunk_overlap=5)
    connection = FakeWriterConnection()
    writer = DatabaseWriter(FakeWriterClient(connection))  # type: ignore[arg-type]

    result = await writer.persist_document(
        document,
        pdf_path="minio://papers/source.pdf",
        markdown_path="minio://papers/full.md",
        document_json_path="minio://papers/document.json",
    )

    assert result.section_count == 2
    assert result.text_chunk_count >= 3
    assert result.image_chunk_count == 1
    assert result.table_chunk_count == 1
    assert any("DELETE FROM section" in sql for _operation, sql, _args in connection.calls)
    assert any("UPDATE text_chunk SET previous_chunk_id" in sql for _operation, sql, _args in connection.calls)
