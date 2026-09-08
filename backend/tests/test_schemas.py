from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from app.schemas import ImageChunk, Paper, PaperCreate, Section, TableChunk, TextChunk

NOW = datetime.now(UTC)


def test_complete_models_match_database_columns() -> None:
    expected_fields = {
        Paper: {
            "id",
            "paper_uid",
            "title",
            "title_en",
            "abstract",
            "authors",
            "keywords",
            "affiliations",
            "doi",
            "journal",
            "volume",
            "issue",
            "publish_year",
            "publish_date",
            "language",
            "source_url",
            "pdf_path",
            "markdown_path",
            "document_json_path",
            "ingestion_status",
            "error_message",
            "page_count",
                "file_metadata",
                "embedding",
                "created_at",
            "updated_at",
        },
        Section: {
            "id",
            "section_uid",
            "paper_id",
            "parent_id",
            "section_number",
            "title",
            "level",
            "sort_order",
            "section_path",
                "raw_text",
                "summary",
                "embedding",
                "created_at",
        },
        TextChunk: {
            "id",
            "chunk_uid",
            "paper_id",
            "section_id",
            "chunk_index",
            "content",
            "char_count",
            "previous_chunk_id",
                "next_chunk_id",
                "metadata",
                "embedding",
                "created_at",
        },
        ImageChunk: {
            "id",
            "chunk_uid",
            "paper_id",
            "section_id",
            "image_index",
            "figure_number",
            "image_path",
            "caption",
            "image_type",
            "description",
                "structured_data",
                "metadata",
                "embedding",
                "created_at",
        },
        TableChunk: {
            "id",
            "chunk_uid",
            "paper_id",
            "section_id",
            "table_index",
            "table_number",
            "caption",
            "page_number",
            "bbox",
            "table_html",
                "structured_data",
                "description",
                "metadata",
                "embedding",
                "created_at",
        },
    }
    for model, fields in expected_fields.items():
        assert set(model.model_fields) == fields


def test_paper_create_has_database_compatible_defaults() -> None:
    paper = PaperCreate(paper_uid="paper-1", title="Example", publish_date=date(2026, 9, 3))

    assert paper.authors == []
    assert paper.keywords == []
    assert paper.affiliations == []
    assert paper.file_metadata == {}
    assert "id" not in paper.model_fields_set
    assert "created_at" not in type(paper).model_fields


def test_complete_paper_accepts_asyncpg_record_shape() -> None:
    paper = Paper.model_validate(
        {
            "id": 1,
            "paper_uid": "paper-1",
            "title": "Example",
            "created_at": NOW,
            "updated_at": NOW,
        }
    )

    assert paper.id == 1
    assert paper.created_at.tzinfo is UTC


def test_varchar_limits_are_validated() -> None:
    with pytest.raises(ValidationError):
        PaperCreate(paper_uid="x" * 65, title="Example")


def test_unknown_database_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        PaperCreate(paper_uid="paper-1", title="Example", embedding=[0.1])


def test_all_database_fields_have_chinese_descriptions() -> None:
    models = (Paper, Section, TextChunk, ImageChunk, TableChunk)
    for model in models:
        for field_name, field in model.model_fields.items():
            assert field.description, f"{model.__name__}.{field_name} 缺少字段描述"
            assert any("\u4e00" <= character <= "\u9fff" for character in field.description)
