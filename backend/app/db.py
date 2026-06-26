import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = Path.home() / ".local" / "share" / "loomsystem" / "loomsystem.db"
MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _ensure_private(path: Path) -> None:
    """Create parent directory and restrict DB file to owner-only access."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch(mode=0o600)
    else:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


class Database:
    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH

    def _connect(self) -> sqlite3.Connection:
        _ensure_private(self.db_path)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def connect(self) -> sqlite3.Connection:
        """Return a connection with foreign keys enabled."""
        return self._connect()

    def init_migrations(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.commit()

    def applied_versions(self) -> set[int]:
        self.init_migrations()
        with self._connect() as conn:
            rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
            return {row["version"] for row in rows}

    def migrate(self, migrations: list[Migration]) -> None:
        """Apply pending migrations in version order."""
        self.init_migrations()
        applied = self.applied_versions()
        pending = [m for m in migrations if m.version not in applied]
        pending.sort(key=lambda m: m.version)
        for migration in pending:
            with self._connect() as conn:
                try:
                    conn.executescript(migration.sql)
                    conn.execute(
                        "INSERT INTO schema_migrations (version) VALUES (?)",
                        (migration.version,),
                    )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise

    def reset(self) -> None:
        """Drop all user tables. Intended for tests only."""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
            tables = [row["name"] for row in cursor.fetchall()]
            for table in tables:
                conn.execute(f"DROP TABLE IF EXISTS {table}")
            conn.commit()


def load_migrations(migrations_dir: Path) -> list[Migration]:
    """Load SQL migration files named <version>_<name>.sql."""
    migrations: list[Migration] = []
    for path in sorted(migrations_dir.glob("*.sql")):
        stem = path.stem
        version_str, _, name = stem.partition("_")
        try:
            version = int(version_str)
        except ValueError:
            continue
        migrations.append(Migration(version=version, name=name, sql=path.read_text()))
    return migrations


def get_db(db_path: Path | str | None = None) -> Database:
    """Return a Database with all bundled migrations applied."""
    db = Database(db_path)
    db.migrate(load_migrations(MIGRATIONS_DIR))
    return db


def dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"))


def loads(value: str | None) -> Any:
    if value is None:
        return None
    return json.loads(value)
