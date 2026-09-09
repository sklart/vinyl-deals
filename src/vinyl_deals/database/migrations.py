"""Small explicit SQLite migration registry for the MVP."""
from __future__ import annotations
import sqlite3

CURRENT_VERSION = 1

def migrate(connection: sqlite3.Connection, schema: str) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version == 0:
        connection.executescript(schema)
        connection.execute(f"PRAGMA user_version = {CURRENT_VERSION}")
        return
    if version != CURRENT_VERSION:
        raise RuntimeError(f"Unsupported database schema version: {version}")
