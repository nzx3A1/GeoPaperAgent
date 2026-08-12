import json

from app.parsers.mineru_output import parse_mineru_output, split_text


def test_parse_content_list(tmp_path) -> None:
    (tmp_path / "full.md").write_text("# 测试论文\n正文", encoding="utf-8")
    (tmp_path / "content_list.json").write_text(json.dumps([
        {"type": "title", "text": "测试论文", "text_level": 1, "page_idx": 0},
        {"type": "text", "text": "第一段", "page_idx": 0},
        {"type": "table", "table_body": "<table></table>", "page_idx": 1},
    ]), encoding="utf-8")
    document = parse_mineru_output(tmp_path)
    assert document.title == "测试论文"
    assert document.page_count == 2
    assert [block.kind for block in document.blocks] == ["heading", "text", "table"]


def test_split_text_keeps_overlap() -> None:
    chunks = split_text("a" * 250, 100, 20)
    assert len(chunks) == 3
    assert chunks[0][-20:] == chunks[1][:20]
