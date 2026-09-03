"""基于 asyncpg 的通用异步 CRUD 数据持久层。"""

from __future__ import annotations

import builtins
import json
import re
from collections.abc import Mapping
from typing import Generic, TypeVar

from pydantic import BaseModel, ValidationError

from app.infrastructure.postgres_client import PostgresClient, get_postgres_client

CreateModelT = TypeVar("CreateModelT", bound=BaseModel)
UpdateModelT = TypeVar("UpdateModelT", bound=BaseModel)
RowModelT = TypeVar("RowModelT", bound=BaseModel)

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")
_ORDER_TERM = re.compile(r"^[a-z_][a-z0-9_]* (?:ASC|DESC)(?: NULLS (?:FIRST|LAST))?$")


class RepositoryError(RuntimeError):
    """数据持久层无法正确处理数据库数据时抛出的异常。"""


class BaseRepository(Generic[CreateModelT, UpdateModelT, RowModelT]):
    """为单张表提供创建、查询、更新、删除和分页列表能力。"""

    def __init__(
        self,
        *,
        table: str,
        row_model: type[RowModelT],
        create_columns: tuple[str, ...],
        update_columns: tuple[str, ...],
        json_columns: frozenset[str] = frozenset(),
        non_nullable_columns: frozenset[str] = frozenset(),
        uid_column: str | None = None,
        order_by: tuple[str, ...] = ("id ASC",),
        touch_updated_at: bool = False,
        client: PostgresClient | None = None,
    ) -> None:
        self.table = table
        self.row_model = row_model
        self.create_columns = create_columns
        self.update_columns = update_columns
        self.json_columns = json_columns
        self.non_nullable_columns = non_nullable_columns
        self.uid_column = uid_column
        self.order_by = order_by
        self.touch_updated_at = touch_updated_at
        self.client = client or get_postgres_client()
        self._validate_configuration()

    async def create(self, data: CreateModelT) -> RowModelT:
        """新增一条记录，并返回数据库生成主键和时间后的完整数据。"""
        values_by_column = data.model_dump(mode="python")
        values = [self._encode_value(column, values_by_column[column]) for column in self.create_columns]
        placeholders = [self._placeholder(index, column) for index, column in enumerate(self.create_columns, start=1)]
        sql = (
            f"INSERT INTO {self.table} ({', '.join(self.create_columns)}) "
            f"VALUES ({', '.join(placeholders)}) RETURNING *"
        )
        async with self.client.connection() as connection:
            row = await connection.fetchrow(sql, *values)
        if row is None:
            raise RepositoryError(f"新增 {self.table} 记录后未返回数据")
        return self._to_model(row)

    async def get_by_id(self, record_id: int) -> RowModelT | None:
        """按数据库主键查询一条记录。"""
        sql = f"SELECT * FROM {self.table} WHERE id = $1"
        async with self.client.connection() as connection:
            row = await connection.fetchrow(sql, record_id)
        return None if row is None else self._to_model(row)

    async def get_by_uid(self, uid: str) -> RowModelT | None:
        """按业务唯一标识查询一条记录。"""
        if self.uid_column is None:
            raise RepositoryError(f"{self.table} 未配置业务唯一标识字段")
        sql = f"SELECT * FROM {self.table} WHERE {self.uid_column} = $1"
        async with self.client.connection() as connection:
            row = await connection.fetchrow(sql, uid)
        return None if row is None else self._to_model(row)

    async def list(self, *, limit: int = 100, offset: int = 0) -> list[RowModelT]:
        """按表的默认顺序分页查询记录。"""
        self._validate_pagination(limit, offset)
        sql = f"SELECT * FROM {self.table} ORDER BY {', '.join(self.order_by)} LIMIT $1 OFFSET $2"
        async with self.client.connection() as connection:
            rows = await connection.fetch(sql, limit, offset)
        return [self._to_model(row) for row in rows]

    async def count(self) -> int:
        """返回表中的记录总数。"""
        sql = f"SELECT COUNT(*) FROM {self.table}"
        async with self.client.connection() as connection:
            value = await connection.fetchval(sql)
        return int(value or 0)

    async def update(self, record_id: int, data: UpdateModelT) -> RowModelT | None:
        """按主键部分更新记录；记录不存在时返回 ``None``。"""
        changes = data.model_dump(mode="python", exclude_unset=True)
        unknown_columns = changes.keys() - set(self.update_columns)
        if unknown_columns:
            names = ", ".join(sorted(unknown_columns))
            raise RepositoryError(f"{self.table} 不允许更新字段：{names}")
        null_columns = {name for name, value in changes.items() if value is None} & self.non_nullable_columns
        if null_columns:
            names = ", ".join(sorted(null_columns))
            raise ValueError(f"非空字段不能更新为 None：{names}")
        if not changes:
            return await self.get_by_id(record_id)

        columns = [column for column in self.update_columns if column in changes]
        assignments = [
            f"{column} = {self._placeholder(index, column)}"
            for index, column in enumerate(columns, start=1)
        ]
        if self.touch_updated_at:
            assignments.append("updated_at = NOW()")
        values = [self._encode_value(column, changes[column]) for column in columns]
        values.append(record_id)
        sql = f"UPDATE {self.table} SET {', '.join(assignments)} WHERE id = ${len(values)} RETURNING *"
        async with self.client.connection() as connection:
            row = await connection.fetchrow(sql, *values)
        return None if row is None else self._to_model(row)

    async def delete(self, record_id: int) -> bool:
        """按主键删除记录，返回是否实际删除了数据。"""
        sql = f"DELETE FROM {self.table} WHERE id = $1 RETURNING id"
        async with self.client.connection() as connection:
            deleted_id = await connection.fetchval(sql, record_id)
        return deleted_id is not None

    async def _list_by(
        self,
        column: str,
        value: object,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> builtins.list[RowModelT]:
        """供具体 Repository 使用的单字段分页查询。"""
        if not _IDENTIFIER.fullmatch(column):
            raise RepositoryError(f"非法查询字段：{column}")
        self._validate_pagination(limit, offset)
        sql = (
            f"SELECT * FROM {self.table} WHERE {column} = $1 "
            f"ORDER BY {', '.join(self.order_by)} LIMIT $2 OFFSET $3"
        )
        async with self.client.connection() as connection:
            rows = await connection.fetch(sql, value, limit, offset)
        return [self._to_model(row) for row in rows]

    def _to_model(self, row: Mapping[str, object]) -> RowModelT:
        values = dict(row)
        try:
            for column in self.json_columns:
                value = values.get(column)
                if isinstance(value, str):
                    values[column] = json.loads(value)
            return self.row_model.model_validate(values)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise RepositoryError(f"{self.table} 返回的数据不符合 {self.row_model.__name__}：{exc}") from exc

    def _encode_value(self, column: str, value: object) -> object:
        if column in self.json_columns and value is not None:
            return json.dumps(value, ensure_ascii=False)
        return value

    def _placeholder(self, index: int, column: str) -> str:
        suffix = "::jsonb" if column in self.json_columns else ""
        return f"${index}{suffix}"

    def _validate_configuration(self) -> None:
        identifiers = {self.table, *self.create_columns, *self.update_columns, *self.json_columns}
        if self.uid_column:
            identifiers.add(self.uid_column)
        if any(not _IDENTIFIER.fullmatch(identifier) for identifier in identifiers):
            raise RepositoryError(f"{self.table} 的 Repository 配置包含非法 SQL 标识符")
        if any(not _ORDER_TERM.fullmatch(term) for term in self.order_by):
            raise RepositoryError(f"{self.table} 的默认排序配置无效")
        if not self.json_columns <= set(self.create_columns):
            raise RepositoryError(f"{self.table} 的 JSONB 字段未包含在新增字段中")

    @staticmethod
    def _validate_pagination(limit: int, offset: int) -> None:
        if not 1 <= limit <= 1000:
            raise ValueError("limit 必须在 1 到 1000 之间")
        if offset < 0:
            raise ValueError("offset 不能小于 0")
