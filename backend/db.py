from __future__ import annotations

import os
from typing import Any

PG_HOST = os.getenv("DATAMID_DB_HOST", "localhost")
PG_PORT = int(os.getenv("DATAMID_DB_PORT", "5432"))
PG_NAME = os.getenv("DATAMID_DB_NAME", "datamid")
PG_USER = os.getenv("DATAMID_DB_USER", "datamid")
PG_PASSWORD = os.getenv("DATAMID_DB_PASSWORD", "")


def get_db_type() -> str:
    return "postgres"


class _PgCursor:
    def __init__(self, raw_cursor):
        self._cur = raw_cursor

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount


class _PgConnection:
    __slots__ = ("_conn",)

    def __init__(self, raw_conn):
        self._conn = raw_conn

    @staticmethod
    def _translate(sql: str) -> str:
        return sql.replace("?", "%s")

    def execute(self, sql: str, params: Any = ()) -> _PgCursor:
        cur = self._conn.cursor()
        cur.execute(self._translate(sql), params)
        return _PgCursor(cur)

    def executemany(self, sql: str, seq_of_params) -> _PgCursor:
        cur = self._conn.cursor()
        cur.executemany(self._translate(sql), seq_of_params)
        return _PgCursor(cur)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()

    def cursor(self):
        return self._conn.cursor()

    def __enter__(self):
        self._conn.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        return self._conn.__exit__(exc_type, exc, tb)


def get_connection() -> _PgConnection:
    import psycopg2
    import psycopg2.extras

    raw = psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        dbname=PG_NAME,
        user=PG_USER,
        password=PG_PASSWORD,
        cursor_factory=psycopg2.extras.RealDictCursor,
    )
    return _PgConnection(raw)
