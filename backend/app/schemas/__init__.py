"""与 PostgreSQL 数据表对应的公共 Pydantic 数据模型。"""

from app.schemas.image_chunk import ImageChunk, ImageChunkBase, ImageChunkCreate, ImageChunkUpdate
from app.schemas.paper import Paper, PaperBase, PaperCreate, PaperUpdate
from app.schemas.section import Section, SectionBase, SectionCreate, SectionUpdate
from app.schemas.table_chunk import TableChunk, TableChunkBase, TableChunkCreate, TableChunkUpdate
from app.schemas.text_chunk import TextChunk, TextChunkBase, TextChunkCreate, TextChunkUpdate

__all__ = [
    "ImageChunk",
    "ImageChunkBase",
    "ImageChunkCreate",
    "ImageChunkUpdate",
    "Paper",
    "PaperBase",
    "PaperCreate",
    "PaperUpdate",
    "Section",
    "SectionBase",
    "SectionCreate",
    "SectionUpdate",
    "TableChunk",
    "TableChunkBase",
    "TableChunkCreate",
    "TableChunkUpdate",
    "TextChunk",
    "TextChunkBase",
    "TextChunkCreate",
    "TextChunkUpdate",
]
