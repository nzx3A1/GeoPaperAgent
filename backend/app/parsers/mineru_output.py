import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Block:
    kind: str
    text: str = ""
    level: int = 0
    page: int | None = None
    path: str | None = None
    caption: str | None = None
    bbox: Any = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedDocument:
    title: str
    markdown_path: Path
    blocks: list[Block]
    page_count: int | None = None


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "".join(_text(item) for item in value).strip()
    if isinstance(value, dict):
        return _text(value.get("content") or value.get("text") or value.get("body") or "")
    return ""


def _from_content_list(items: list[dict[str, Any]], root: Path) -> list[Block]:
    blocks: list[Block] = []
    for item in items:
        kind = str(item.get("type", "text")).lower()
        page = item.get("page_idx", item.get("page_number"))
        page = int(page) + 1 if isinstance(page, int) and "page_idx" in item else page
        if kind in {"title", "heading", "header"}:
            level = int(item.get("text_level") or item.get("level") or 1)
            blocks.append(Block("heading", _text(item), max(1, min(level, 6)), page, raw=item))
        elif kind in {"image", "figure"}:
            rel = item.get("img_path") or item.get("image_path") or item.get("path")
            blocks.append(Block("image", page=page, path=str(root / rel) if rel else None,
                                caption=_text(item.get("image_caption") or item.get("caption")), raw=item))
        elif kind == "table":
            body = item.get("table_body") or item.get("table_html") or _text(item)
            blocks.append(Block("table", str(body), page=page,
                                caption=_text(item.get("table_caption") or item.get("caption")),
                                bbox=item.get("bbox"), raw=item))
        else:
            value = _text(item)
            if value:
                blocks.append(Block("text", value, page=page, raw=item))
    return blocks


def parse_mineru_output(root: Path) -> ParsedDocument:
    content_files = sorted(root.rglob("*_content_list.json")) or sorted(root.rglob("content_list.json"))
    markdown_files = sorted(root.rglob("full.md")) or sorted(root.rglob("*.md"))
    if not markdown_files:
        raise ValueError("MinerU result does not contain a Markdown file")
    markdown_path = markdown_files[0]
    markdown = markdown_path.read_text(encoding="utf-8", errors="replace")
    if content_files:
        data = json.loads(content_files[0].read_text(encoding="utf-8"))
        items = data if isinstance(data, list) else data.get("content_list", [])
        blocks = _from_content_list(items, content_files[0].parent)
    else:
        blocks = []
        for part in re.split(r"(?m)(?=^#{1,6}\s+)", markdown):
            match = re.match(r"^(#{1,6})\s+(.+?)\s*$", part.splitlines()[0] if part else "")
            if match:
                blocks.append(Block("heading", match.group(2), len(match.group(1))))
                body = "\n".join(part.splitlines()[1:]).strip()
            else:
                body = part.strip()
            if body:
                blocks.append(Block("text", body))
    title = next((b.text for b in blocks if b.kind == "heading"), markdown_path.stem)
    pages = [b.page for b in blocks if b.page]
    return ParsedDocument(title=title, markdown_path=markdown_path, blocks=blocks, page_count=max(pages, default=None))


def split_text(text: str, size: int, overlap: int) -> list[str]:
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundary = max(text.rfind("\n\n", start, end), text.rfind("。", start, end))
            if boundary > start + size // 2:
                end = boundary + 1
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return [item for item in chunks if item]
