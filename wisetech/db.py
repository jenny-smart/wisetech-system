"""資料庫連線（Supabase Postgres）。

連線字串從環境變數 DATABASE_URL 或 Streamlit secrets 的 DATABASE_URL 讀取。
Supabase：Project Settings → Database → Connection string → Session pooler（port 5432）。
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    try:
        import streamlit as st
        return st.secrets["DATABASE_URL"]
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("找不到 DATABASE_URL：請設定環境變數或 .streamlit/secrets.toml") from e


def connect(url: str | None = None) -> psycopg.Connection:
    # prepare_threshold=None：Supabase pooler 不支援 prepared statements
    return psycopg.connect(url or database_url(), row_factory=dict_row,
                           autocommit=True, prepare_threshold=None)


class DB:
    """包一層小工具，讓頁面程式碼保持簡短。"""

    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    def _ensure(self):
        if self.conn.closed or self.conn.broken:
            self.conn = connect()

    def all(self, sql: str, params=None) -> list[dict]:
        self._ensure()
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def one(self, sql: str, params=None) -> dict | None:
        rows = self.all(sql, params)
        return rows[0] if rows else None

    def val(self, sql: str, params=None):
        row = self.one(sql, params)
        return next(iter(row.values())) if row else None

    def run(self, sql: str, params=None) -> int:
        self._ensure()
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.rowcount

    @contextmanager
    def tx(self):
        self._ensure()
        with self.conn.transaction():
            yield self

    # ─── 通用 insert / update ───
    def insert(self, table: str, data: dict, returning: str = "id"):
        data = {k: _adapt(v) for k, v in data.items()}
        cols = ", ".join(data)
        ph = ", ".join(["%s"] * len(data))
        return self.val(f"insert into {table} ({cols}) values ({ph}) returning {returning}",
                        list(data.values()))

    def update(self, table: str, id_: int, data: dict) -> int:
        if not data:
            return 0
        data = {k: _adapt(v) for k, v in data.items()}
        sets = ", ".join(f"{k} = %s" for k in data)
        return self.run(f"update {table} set {sets} where id = %s", [*data.values(), id_])

    def log(self, username: str | None, action: str, table: str | None = None,
            record_id: int | None = None, detail: dict | None = None):
        self.run("insert into audit_log (username, action, table_name, record_id, detail) values (%s,%s,%s,%s,%s)",
                 (username, action, table, record_id, Jsonb(json.loads(json.dumps(detail or {}, default=str)))))


def _adapt(v):
    if isinstance(v, dict):
        return Jsonb(json.loads(json.dumps(v, default=str)))
    return v
