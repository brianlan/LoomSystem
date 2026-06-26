import sqlite3
from collections.abc import Generator
from pathlib import Path

from fastapi import Request

from app.db import get_db


class DBState:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path
        self._db = None

    def get_connection(self) -> sqlite3.Connection:
        db = get_db(self.db_path)
        return db.connect()


def get_db_conn(request: Request) -> Generator[sqlite3.Connection, None, None]:
    state: DBState = request.app.state.db
    conn = state.get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
