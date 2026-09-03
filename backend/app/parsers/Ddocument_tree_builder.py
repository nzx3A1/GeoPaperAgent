"""阶段 D：把章节 JSON 转换为 Document -> Section -> 多模态 Chunk 文档树。

本文件不依赖项目外的 ``model`` 包，直接提供可序列化的数据类和稳定 ID，输出可由
阶段 E 原子写入 PostgreSQL。公式保留为 ``formula`` 模态，并在入库时落到文本 Chunk 表。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# 支持直接执行 ``python app/parsers/Ddocument_tree_builder.py``。
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT_PATH = BACKEND_ROOT / "output" / "stage_03_markdown_sections.json"
DEFAULT_OUTPUT_PATH = BACKEND_ROOT / "output" / "stage_04_document_tree.json"
DEFAULT_TEXT_CHUNK_MAX_LENGTH = 1800
DEFAULT_TEXT_CHUNK_OVERLAP = 200
AssetUriResolver = Callable[[str], str]


@dataclass(slots=True)
class DocumentMetadata:
    """保存 Paper 表可接收的论文元数据。"""

    title_en: str | None = None
    abstract: str | None = None
    authors: list[Any] = field(default_factory=list)
    affiliations: list[Any] = field(default_factory=list)
    keywords: list[Any] = field(default_factory=list)
    doi: str | None = None
    journal: str | None = None
    volume: str | None = None
    issue: str | None = None
    publish_year: int | None = None
    publish_date: str | None = None
    language: str | None = None
    references: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """将论文元数据转换为 JSON 兼容字典。"""

        return asdict(self)


@dataclass(slots=True)
class ChunkNode:
    """表示文本、公式、图片或表格中的一个统一 Chunk 节点。"""

    chunk_uid: str
    modality: str
    order: int
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """将 Chunk 节点转换为 JSON 兼容字典。"""

        return {
            "chunk_uid": self.chunk_uid,
            "modality": self.modality,
            "order": self.order,
            **self.data,
        }


@dataclass(slots=True)
class DocumentSection:
    """表示一篇论文中的递归章节节点。"""

    section_uid: str
    source_section_id: str
    section_number: str | None
    title: str
    level: int
    sort_order: int
    parent_uid: str | None
    section_path: list[str]
    raw_text: str
    chunks: list[ChunkNode] = field(default_factory=list)
    children: list[DocumentSection] = field(default_factory=list)

    def iter_sections(self) -> Iterator[DocumentSection]:
        """按前序遍历返回当前章节及全部子章节。"""

        yield self
        for child in self.children:
            yield from child.iter_sections()

    def iter_chunks(self) -> Iterator[ChunkNode]:
        """按章节树顺序返回当前章节及子章节的全部 Chunk。"""

        yield from sorted(self.chunks, key=lambda item: item.order)
        for child in self.children:
            yield from child.iter_chunks()

    def to_dict(self) -> dict[str, Any]:
        """递归序列化章节及其 Chunk。"""

        return {
            "section_uid": self.section_uid,
            "source_section_id": self.source_section_id,
            "section_number": self.section_number,
            "title": self.title,
            "level": self.level,
            "sort_order": self.sort_order,
            "parent_uid": self.parent_uid,
            "section_path": self.section_path,
            "raw_text": self.raw_text,
            "chunks": [chunk.to_dict() for chunk in sorted(self.chunks, key=lambda item: item.order)],
            "children": [child.to_dict() for child in self.children],
        }


@dataclass(slots=True)
class Document:
    """表示可入库的一篇完整论文及其内存检索索引。"""

    paper_uid: str
    title: str
    metadata: DocumentMetadata
    source_file: str | None
    sections: list[DocumentSection]
    _section_index: dict[str, DocumentSection] = field(default_factory=dict, init=False, repr=False)
    _chunk_index: dict[str, ChunkNode] = field(default_factory=dict, init=False, repr=False)

    @property
    def id(self) -> str:
        """保留旧调用方使用的 ``document.id`` 兼容属性。"""

        return self.paper_uid

    def iter_sections(self) -> Iterator[DocumentSection]:
        """按前序遍历返回整篇论文的全部章节。"""

        for section in self.sections:
            yield from section.iter_sections()

    def iter_chunks(self) -> Iterator[ChunkNode]:
        """按章节树顺序返回整篇论文的全部 Chunk。"""

        for section in self.sections:
            yield from section.iter_chunks()

    def rebuild_indexes(self) -> None:
        """重建章节和 Chunk 的业务 ID 内存索引，并检查重复 ID。"""

        self._section_index = {}
        self._chunk_index = {}
        for section in self.iter_sections():
            if section.section_uid in self._section_index:
                raise ValueError(f"章节 ID 重复：{section.section_uid}")
            self._section_index[section.section_uid] = section
            for chunk in section.chunks:
                if chunk.chunk_uid in self._chunk_index:
                    raise ValueError(f"Chunk ID 重复：{chunk.chunk_uid}")
                self._chunk_index[chunk.chunk_uid] = chunk

    def to_dict(self) -> dict[str, Any]:
        """将整篇文档树转换为 JSON 兼容字典。"""

        return {
            "paper_uid": self.paper_uid,
            "title": self.title,
            "metadata": self.metadata.to_dict(),
            "source_file": self.source_file,
            "sections": [section.to_dict() for section in self.sections],
        }


def _as_mapping(value: Any) -> Mapping[str, Any]:
    """把任意资源条目规范为只读映射。"""

    return value if isinstance(value, Mapping) else {"content": str(value)}


def _as_items(value: Any) -> list[Any]:
    """把空值、单值或序列规范为列表，避免字符串被逐字符拆分。"""

    if value is None:
        return []
    if isinstance(value, (str, Mapping)):
        return [value]
    return list(value) if isinstance(value, Sequence) else [value]


def _as_json_list(value: Any) -> list[Any]:
    """把元数据值规范为可序列化列表。"""

    return [item for item in _as_items(value) if item is not None]


def _document_id(stage03: Mapping[str, Any], source_json: Path | None) -> str:
    """优先复用 paper_uid，否则依据源文件路径生成稳定文档 ID。"""

    existing = stage03.get("paper_uid") or stage03.get("document_id") or stage03.get("paper_id")
    source = str(existing or stage03.get("input_file") or source_json or stage03.get("title") or "document")
    if existing and len(str(existing)) <= 64:
        return str(existing)
    return f"paper-{hashlib.sha256(source.encode('utf-8')).hexdigest()[:32]}"


def _stable_uid(prefix: str, *parts: str) -> str:
    """根据业务片段生成不超过数据库长度限制的稳定 ID。"""

    digest = hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()[:20]
    return f"{prefix}-{digest}"


def _split_text(content: str, max_length: int, overlap: int) -> list[str]:
    """优先在段落或句末切分正文，并为相邻文本块保留指定重叠字符。"""

    if max_length <= 0:
        raise ValueError("文本 Chunk 长度上限必须大于 0")
    if overlap < 0 or overlap >= max_length:
        raise ValueError("文本 Chunk 重叠长度必须大于等于 0 且小于长度上限")
    text = re.sub(r"\n{3,}", "\n\n", content.strip())
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        hard_end = min(len(text), start + max_length)
        end = hard_end
        if hard_end < len(text):
            search_start = start + max(max_length // 2, 1)
            candidates = [text.rfind(marker, search_start, hard_end) for marker in ("\n\n", "。", "！", "？", ". ")]
            boundary = max(candidates)
            if boundary >= search_start:
                end = boundary + (2 if text[boundary : boundary + 2] == "\n\n" else 1)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return chunks


def _resolve_asset_path(path: str, source_file: str | None, resolver: AssetUriResolver | None) -> str:
    """把 Markdown 资源路径解析为绝对路径，并按需替换为 MinIO 地址。"""

    candidate = Path(path)
    if not candidate.is_absolute() and source_file:
        candidate = Path(source_file).parent / candidate
    resolved = str(candidate.resolve()) if source_file or candidate.is_absolute() else path
    return resolver(resolved) if resolver else resolved


def _extract_labeled_number(caption: Any, label_pattern: str) -> str | None:
    """从图题或表题中提取带标签的编号。"""

    match = re.search(rf"(?:{label_pattern})\s*[.．:]?\s*(\d+(?:[.．]\d+)*)", str(caption or ""), re.IGNORECASE)
    return match.group(1).replace("．", ".") if match else None


def _build_chunks(
    section_uid: str,
    raw_section: Mapping[str, Any],
    source_file: str | None,
    max_text_chunk_length: int,
    text_chunk_overlap: int,
    asset_uri_resolver: AssetUriResolver | None,
) -> list[ChunkNode]:
    """将单个章节转换为文本、图片、表格和公式四类 Chunk。"""

    chunks: list[ChunkNode] = []
    content = str(raw_section.get("content") or "").strip()
    for index, text in enumerate(_split_text(content, max_text_chunk_length, text_chunk_overlap)):
        chunks.append(
            ChunkNode(
                chunk_uid=f"{section_uid}:txt:{index}",
                modality="text",
                order=len(chunks),
                data={"chunk_index": index, "content": text, "char_count": len(text), "metadata": {}},
            )
        )

    image_index = 0
    for group_index, raw_image in enumerate(_as_items(raw_section.get("images"))):
        image = _as_mapping(raw_image)
        paths = [str(item) for item in _as_items(image.get("path")) if item]
        for path in paths:
            resolved_path = _resolve_asset_path(path, source_file, asset_uri_resolver)
            caption = str(image.get("caption") or "") or None
            chunks.append(
                ChunkNode(
                    chunk_uid=f"{section_uid}:img:{image_index}",
                    modality="image",
                    order=len(chunks),
                    data={
                        "image_index": image_index,
                        "figure_number": _extract_labeled_number(caption, r"图|Fig(?:ure)?"),
                        "image_path": resolved_path,
                        "caption": caption,
                        "image_type": None,
                        "description": None,
                        "structured_data": {},
                        "metadata": {"group_index": group_index, "references": _as_json_list(image.get("references"))},
                    },
                )
            )
            image_index += 1

    tables = raw_section.get("table") or raw_section.get("tables") or []
    for index, raw_table in enumerate(_as_items(tables)):
        table = _as_mapping(raw_table)
        content_value = str(table.get("markdown") or table.get("content") or table.get("table") or "")
        caption = str(table.get("caption") or "") or None
        raw_paths = [str(item) for item in _as_items(table.get("path")) if item]
        paths = [_resolve_asset_path(path, source_file, asset_uri_resolver) for path in raw_paths]
        chunks.append(
            ChunkNode(
                chunk_uid=f"{section_uid}:tbl:{index}",
                modality="table",
                order=len(chunks),
                data={
                    "table_index": index,
                    "table_number": _extract_labeled_number(caption, r"表|Table"),
                    "caption": caption,
                    "page_number": table.get("page_number"),
                    "bbox": table.get("bbox"),
                    "table_html": content_value if content_value.lstrip().lower().startswith("<table") else None,
                    "structured_data": {"source_type": table.get("source_type", "html"), "asset_paths": paths},
                    "description": None,
                    "metadata": {"raw_content": content_value, "context": table.get("context")},
                },
            )
        )

    for index, raw_formula in enumerate(_as_items(raw_section.get("formulas"))):
        formula = _as_mapping(raw_formula)
        latex = str(formula.get("latex") or formula.get("content") or formula.get("formula") or "").strip()
        if not latex:
            continue
        chunks.append(
            ChunkNode(
                chunk_uid=f"{section_uid}:fml:{index}",
                modality="formula",
                order=len(chunks),
                data={
                    "chunk_index": index,
                    "content": latex,
                    "char_count": len(latex),
                    "metadata": {"modality": "formula", "raw": formula.get("raw"), "context": formula.get("context")},
                },
            )
        )
    return chunks


def _build_section(
    raw_section: Mapping[str, Any],
    *,
    paper_uid: str,
    parent_uid: str | None,
    parent_level: int,
    path_titles: list[str],
    sort_counter: list[int],
    source_file: str | None,
    max_text_chunk_length: int,
    text_chunk_overlap: int,
    asset_uri_resolver: AssetUriResolver | None,
) -> DocumentSection:
    """递归构建章节节点，并使用共享计数器生成全文稳定顺序。"""

    source_section_id = str(raw_section.get("id") or raw_section.get("section_number") or "section")
    title = str(raw_section.get("title") or source_section_id or "未命名章节")
    sort_order = sort_counter[0]
    sort_counter[0] += 1
    section_uid = _stable_uid("section", paper_uid, parent_uid or "root", source_section_id, str(sort_order))
    number = source_section_id if re.fullmatch(r"\d+(?:\.\d+)*", source_section_id) else None
    current_path = [*path_titles, title]
    section = DocumentSection(
        section_uid=section_uid,
        source_section_id=source_section_id,
        section_number=number,
        title=title,
        level=parent_level + 1,
        sort_order=sort_order,
        parent_uid=parent_uid,
        section_path=current_path,
        raw_text=str(raw_section.get("raw_text") or raw_section.get("content") or ""),
    )
    section.chunks = _build_chunks(
        section_uid,
        raw_section,
        source_file,
        max_text_chunk_length,
        text_chunk_overlap,
        asset_uri_resolver,
    )
    section.children = [
        _build_section(
            _as_mapping(child),
            paper_uid=paper_uid,
            parent_uid=section_uid,
            parent_level=section.level,
            path_titles=current_path,
            sort_counter=sort_counter,
            source_file=source_file,
            max_text_chunk_length=max_text_chunk_length,
            text_chunk_overlap=text_chunk_overlap,
            asset_uri_resolver=asset_uri_resolver,
        )
        for child in _as_items(raw_section.get("children"))
    ]
    return section


def build_document_tree(
    stage03: Mapping[str, Any],
    source_json: Path | None = None,
    max_text_chunk_length: int = DEFAULT_TEXT_CHUNK_MAX_LENGTH,
    text_chunk_overlap: int = DEFAULT_TEXT_CHUNK_OVERLAP,
    asset_uri_resolver: AssetUriResolver | None = None,
) -> Document:
    """将一篇阶段 C 章节 JSON 转换为可检索、可入库的文档树。"""

    if not stage03.get("toc") and isinstance(stage03.get("documents"), Sequence):
        documents = stage03["documents"]
        if len(documents) != 1:
            raise ValueError("build_document_tree 一次只能构建一篇文档")
        stage03 = _as_mapping(documents[0])
    paper_uid = _document_id(stage03, source_json)
    source_file = str(stage03.get("input_file")) if stage03.get("input_file") else None
    basic_info = _as_mapping(stage03.get("basicInformation"))
    metadata = DocumentMetadata(
        title_en=basic_info.get("title_en"),
        abstract=basic_info.get("abstract"),
        authors=_as_json_list(basic_info.get("authors")),
        affiliations=_as_json_list(basic_info.get("affiliations")),
        keywords=_as_json_list(basic_info.get("keywords")),
        doi=basic_info.get("doi"),
        journal=basic_info.get("journal"),
        volume=basic_info.get("volume"),
        issue=basic_info.get("issue"),
        publish_year=basic_info.get("publish_year"),
        publish_date=basic_info.get("publish_date"),
        language=basic_info.get("language"),
        references=basic_info.get("references"),
    )
    sort_counter = [0]
    document = Document(
        paper_uid=paper_uid,
        title=str(stage03.get("title") or basic_info.get("title") or "未命名文档"),
        metadata=metadata,
        source_file=source_file,
        sections=[
            _build_section(
                _as_mapping(raw_section),
                paper_uid=paper_uid,
                parent_uid=None,
                parent_level=0,
                path_titles=[],
                sort_counter=sort_counter,
                source_file=source_file,
                max_text_chunk_length=max_text_chunk_length,
                text_chunk_overlap=text_chunk_overlap,
                asset_uri_resolver=asset_uri_resolver,
            )
            for raw_section in _as_items(stage03.get("toc"))
        ],
    )
    document.rebuild_indexes()
    return document


def load_stage03_document(
    path: str | Path,
    *,
    max_text_chunk_length: int = DEFAULT_TEXT_CHUNK_MAX_LENGTH,
    text_chunk_overlap: int = DEFAULT_TEXT_CHUNK_OVERLAP,
) -> Document:
    """读取阶段 C JSON，并构建完成内存索引的文档树。"""

    source_json = Path(path).expanduser().resolve()
    stage03 = json.loads(source_json.read_text(encoding="utf-8"))
    if not isinstance(stage03, Mapping):
        raise ValueError(f"阶段 C JSON 根节点必须是对象：{source_json}")
    return build_document_tree(
        stage03,
        source_json=source_json,
        max_text_chunk_length=max_text_chunk_length,
        text_chunk_overlap=text_chunk_overlap,
    )


def load_stage01_document(
    path: str | Path,
    max_text_chunk_length: int = DEFAULT_TEXT_CHUNK_MAX_LENGTH,
) -> Document:
    """兼容旧函数名并转发到阶段 C 文档加载器。"""

    return load_stage03_document(path, max_text_chunk_length=max_text_chunk_length)


def document_tree_to_dict(document: Document) -> dict[str, Any]:
    """序列化阶段 D 文档树并附加章节与各模态 Chunk 统计。"""

    chunks = list(document.iter_chunks())
    modality_counts = {
        modality: sum(chunk.modality == modality for chunk in chunks)
        for modality in ("text", "formula", "image", "table")
    }
    return {
        "_stage": 4,
        "_description": "Document -> Section -> 多模态 Chunk 文档树",
        "_produced_by": "app.parsers.Ddocument_tree_builder.build_document_tree",
        "document": document.to_dict(),
        "statistics": {
            "section_count": sum(1 for _ in document.iter_sections()),
            "chunk_count": len(chunks),
            "modality_counts": modality_counts,
        },
    }


def write_document_tree(document: Document, path: str | Path) -> None:
    """将阶段 D 文档树写为 UTF-8 JSON 文件。"""

    output_path = Path(path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(document_tree_to_dict(document), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_argument_parser() -> argparse.ArgumentParser:
    """创建阶段 D 命令行参数解析器。"""

    parser = argparse.ArgumentParser(description="构建 Document -> Section -> 多模态 Chunk 文档树")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH, help="阶段 C 章节 JSON")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="阶段 D 文档树 JSON")
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_TEXT_CHUNK_MAX_LENGTH, help="文本块最大字符数")
    parser.add_argument("--chunk-overlap", type=int, default=DEFAULT_TEXT_CHUNK_OVERLAP, help="相邻文本块重叠字符数")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """运行阶段 D CLI 并打印构建统计。"""

    args = build_argument_parser().parse_args(argv)
    document = load_stage03_document(
        args.input,
        max_text_chunk_length=args.chunk_size,
        text_chunk_overlap=args.chunk_overlap,
    )
    write_document_tree(document, args.output)
    print(json.dumps(document_tree_to_dict(document)["statistics"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
