from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest

from app.repositories import (
    DatabaseService,
    ImageChunkRepository,
    PaperRepository,
    SectionRepository,
    TableChunkRepository,
    TextChunkRepository,
)
from app.schemas import PaperCreate, PaperUpdate

NOW = datetime.now(UTC)


def paper_row(**changes: object) -> dict[str, object]:
    row: dict[str, object] = {
        **PaperCreate(paper_uid="paper-1", title="原始标题").model_dump(),
        "id": 1,
        "created_at": NOW,
        "updated_at": NOW,
    }
    for column in ("authors", "keywords", "affiliations", "file_metadata"):
        row[column] = json.dumps(row[column], ensure_ascii=False)
    row.update(changes)
    return row


def section_row() -> dict[str, object]:
    return {
        "id": 2,
        "section_uid": "section-1",
        "paper_id": 1,
        "parent_id": None,
        "section_number": "1",
        "title": "引言",
        "level": 1,
        "sort_order": 0,
        "section_path": "[]",
        "raw_text": None,
        "summary": None,
        "created_at": NOW,
    }


class FakeConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, tuple[object, ...]]] = []
        self.fetchrow_results: list[dict[str, object] | None] = []
        self.fetch_results: list[list[dict[str, object]]] = []
        self.fetchval_results: list[object] = []

    async def fetchrow(self, sql: str, *args: object) -> dict[str, object] | None:
        self.calls.append(("fetchrow", sql, args))
        return self.fetchrow_results.pop(0)

    async def fetch(self, sql: str, *args: object) -> list[dict[str, object]]:
        self.calls.append(("fetch", sql, args))
        return self.fetch_results.pop(0)

    async def fetchval(self, sql: str, *args: object) -> object:
        self.calls.append(("fetchval", sql, args))
        return self.fetchval_results.pop(0)


class FakePostgresClient:
    def __init__(self, connection: FakeConnection) -> None:
        self.postgres_connection = connection
        self.closed = False

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[FakeConnection]:
        yield self.postgres_connection

    async def connect(self) -> Any:
        return "pool"

    async def close(self) -> None:
        self.closed = True

    async def healthcheck(self) -> bool:
        return True


def make_paper_repository(connection: FakeConnection) -> PaperRepository:
    return PaperRepository(FakePostgresClient(connection))  # type: ignore[arg-type]


async def test_create_uses_bound_parameters_and_converts_jsonb() -> None:
    connection = FakeConnection()
    connection.fetchrow_results.append(paper_row(authors='[{"name": "张三"}]'))
    repository = make_paper_repository(connection)

    paper = await repository.create(PaperCreate(paper_uid="paper-1", title="原始标题", authors=[{"name": "张三"}]))

    operation, sql, args = connection.calls[0]
    assert operation == "fetchrow"
    assert sql.startswith("INSERT INTO paper")
    assert "$5::jsonb" in sql
    assert json.loads(args[4]) == [{"name": "张三"}]
    assert paper.authors == [{"name": "张三"}]


async def test_get_by_uid_returns_none_when_record_does_not_exist() -> None:
    connection = FakeConnection()
    connection.fetchrow_results.append(None)

    result = await make_paper_repository(connection).get_by_uid("missing")

    assert result is None
    assert connection.calls[0][1] == "SELECT * FROM paper WHERE paper_uid = $1"
    assert connection.calls[0][2] == ("missing",)


async def test_update_only_writes_explicit_fields_and_touches_timestamp() -> None:
    connection = FakeConnection()
    connection.fetchrow_results.append(paper_row(title="修改后的标题"))

    paper = await make_paper_repository(connection).update(1, PaperUpdate(title="修改后的标题"))

    _, sql, args = connection.calls[0]
    assert sql == "UPDATE paper SET title = $1, updated_at = NOW() WHERE id = $2 RETURNING *"
    assert args == ("修改后的标题", 1)
    assert paper is not None and paper.title == "修改后的标题"


async def test_update_rejects_null_for_database_not_null_column() -> None:
    connection = FakeConnection()

    with pytest.raises(ValueError, match="title"):
        await make_paper_repository(connection).update(1, PaperUpdate(title=None))

    assert connection.calls == []


async def test_delete_and_count_return_simple_values() -> None:
    connection = FakeConnection()
    connection.fetchval_results.extend([1, 7])
    repository = make_paper_repository(connection)

    assert await repository.delete(1) is True
    assert await repository.count() == 7
    assert connection.calls[0][1] == "DELETE FROM paper WHERE id = $1 RETURNING id"
    assert connection.calls[1][1] == "SELECT COUNT(*) FROM paper"


async def test_section_list_by_paper_uses_pagination_and_ordering() -> None:
    connection = FakeConnection()
    connection.fetch_results.append([section_row()])
    repository = SectionRepository(FakePostgresClient(connection))  # type: ignore[arg-type]

    sections = await repository.list_by_paper(1, limit=20, offset=5)

    _, sql, args = connection.calls[0]
    assert "WHERE paper_id = $1 ORDER BY sort_order ASC, id ASC" in sql
    assert args == (1, 20, 5)
    assert sections[0].title == "引言"


@pytest.mark.parametrize("limit, offset", [(0, 0), (1001, 0), (100, -1)])
async def test_invalid_pagination_is_rejected(limit: int, offset: int) -> None:
    connection = FakeConnection()

    with pytest.raises(ValueError):
        await make_paper_repository(connection).list(limit=limit, offset=offset)

    assert connection.calls == []


def test_database_service_exposes_all_table_repositories() -> None:
    client = FakePostgresClient(FakeConnection())
    service = DatabaseService(client)  # type: ignore[arg-type]

    assert isinstance(service.papers, PaperRepository)
    assert isinstance(service.sections, SectionRepository)
    assert isinstance(service.text_chunks, TextChunkRepository)
    assert isinstance(service.image_chunks, ImageChunkRepository)
    assert isinstance(service.table_chunks, TableChunkRepository)
    assert all(repository.client is client for repository in (
        service.papers,
        service.sections,
        service.text_chunks,
        service.image_chunks,
        service.table_chunks,
    ))
