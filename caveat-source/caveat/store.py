"""Opening the Relate store and creating its schema.

The store is one Relate single-file database (`.rl`). Connections are opened
per operation and closed straight after: a Relate connection belongs to the
thread that opened it, and a writer holds the file lock while it is open.
Any number of read-only connections can be open at once.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

import relate

from . import DEFAULT_STORE

UNIQUE = {
    "recall_id": ("Recall", "id"),
    "product_id": ("Product", "id"),
    "company_key": ("Company", "key"),
    "hazard_name": ("Hazard", "name"),
    "retailer_name": ("Retailer", "name"),
    "country_name": ("Country", "name"),
    "upc_code": ("Upc", "code"),
    "remedy_name": ("Remedy", "name"),
    "ptype_name": ("ProductType", "name"),
    "entity_lei": ("LegalEntity", "lei"),
    "item_id": ("Item", "id"),
    "decision_id": ("Decision", "id"),
    "sync_id": ("Sync", "id"),
}

FULLTEXT = {
    "product_ft": "FOR (p:Product) ON EACH [p.name]",
    "recall_ft": "FOR (r:Recall) ON EACH [r.title]",
    "company_ft": "FOR (c:Company) ON EACH [c.name, c.alias]",
}


def store_path(path: str | None = None) -> str:
    return path or os.environ.get("CAVEAT_STORE") or DEFAULT_STORE


@contextmanager
def open_store(path: str | None = None, read_only: bool = False) -> Iterator[relate.Connection]:
    """Open the store; commit on success; always close."""
    target = store_path(path)
    if read_only and not os.path.exists(target):
        raise FileNotFoundError(f"No store at {target}. Create it with: caveat sync")
    if not read_only:
        parent = os.path.dirname(target)
        if parent:
            os.makedirs(parent, exist_ok=True)
    conn = relate.connect(target, read_only=read_only)
    try:
        yield conn
        if not read_only:
            conn.commit()
    finally:
        conn.close()


def create_schema(conn: relate.Connection) -> None:
    for name, (label, prop) in UNIQUE.items():
        conn.execute(
            f"CREATE CONSTRAINT {name} IF NOT EXISTS FOR (n:{label}) REQUIRE n.{prop} IS UNIQUE"
        ).consume()
    for name, spec in FULLTEXT.items():
        conn.execute(f"CREATE FULLTEXT INDEX {name} IF NOT EXISTS {spec}").consume()
    conn.execute("CREATE INDEX recall_date IF NOT EXISTS FOR (r:Recall) ON (r.date)").consume()
