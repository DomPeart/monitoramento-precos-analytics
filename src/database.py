from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator
import pymysql
from pymysql.cursors import DictCursor

from src.config import settings


def get_connection() -> pymysql.connections.Connection:
    """Retorna conexão UTF8MB4 com o banco MariaDB/MySQL (XAMPP)."""
    return pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=settings.mysql_database,
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=False,
    )


@contextmanager
def db_cursor(commit: bool = False) -> Iterator[DictCursor]:
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            yield cursor
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def fetch_all(query: str, params: tuple[Any, ...] | list[Any] | None = None) -> list[dict[str, Any]]:
    with db_cursor(commit=False) as cur:
        cur.execute(query, params or ())
        return list(cur.fetchall())


def fetch_one(query: str, params: tuple[Any, ...] | list[Any] | None = None) -> dict[str, Any] | None:
    with db_cursor(commit=False) as cur:
        cur.execute(query, params or ())
        return cur.fetchone()
