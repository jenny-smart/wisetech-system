"""共用的 Streamlit 元件。"""
from __future__ import annotations

from decimal import Decimal

import pandas as pd
import streamlit as st

from .db import DB, connect

PAGES: dict = {}          # wiseapp.py 註冊後放這裡，讓頁面之間可以互相跳轉

TAX_STATUS_LABEL = {"pending": "⏳ 未比對", "auto": "🤖 自動比對", "candidate": "❓ 待確認",
                    "confirmed": "✅ 已確認", "not_found": "⚠️ 查無", "not_applicable": "➖ 非公司"}


def get_db() -> DB:
    """每個瀏覽器分頁（session）各用一條連線。

    不能用 st.cache_resource 共用：兩個人同時寫入（例如匯入），交易會在同一條連線上互相干擾。
    """
    db = st.session_state.get("_db")
    if db is None:
        db = DB(connect())
        st.session_state["_db"] = db
    else:
        db._ensure()                      # 連線斷掉時自動重連（DB 物件內部替換）
    return db


def money(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return f"${Decimal(v):,.0f}"


def frame(rows: list[dict], columns: dict[str, str] | None = None) -> pd.DataFrame:
    """list[dict] → DataFrame，Decimal 轉 float，欄位改中文。"""
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=list((columns or {}).values()))
    for c in df.columns:
        if df[c].map(lambda x: isinstance(x, Decimal)).any():
            df[c] = df[c].map(lambda x: float(x) if x is not None else None)
    if columns:
        df = df[[c for c in columns if c in df.columns]].rename(columns=columns)
    return df


MONEY_COL = st.column_config.NumberColumn(format="$%,d")


def go_company(cid: int):
    st.session_state.company_id = cid
    st.switch_page(PAGES["company"])


def go_contract(kid: int):
    st.session_state.contract_id = kid
    st.switch_page(PAGES["contract"])


def company_picker(db: DB, key: str, label: str = "公司") -> int | None:
    """輸入名稱或統編 → 選公司。"""
    q = st.text_input(f"{label}（名稱或統編）", key=f"{key}_q")
    if not q:
        return None
    from .services import search_companies
    rows = search_companies(db, q, limit=20)
    if not rows:
        st.caption("找不到，請先到「公司查詢」新增公司")
        return None
    opts = {r["id"]: f"{r['name']}（{r['tax_id'] or '無統編'}）" for r in rows}
    return st.selectbox(label, list(opts), format_func=opts.get, key=f"{key}_sel")


def save_editor(db: DB, table: str, fk: str, fk_val: int, original: list[dict], edited: pd.DataFrame,
                mapping: dict[str, str], required: tuple[str, ...] = ()) -> None:
    """把 st.data_editor 的結果寫回資料表：有 id 的更新、沒有 id 的新增、被刪掉的列刪除。

    mapping：{中文欄名: 資料庫欄位}
    """
    keep = set()
    with db.tx():
        for _, r in edited.iterrows():
            data = {col: _to_db(none_if_blank(r.get(label))) for label, col in mapping.items()}
            if required and any(data.get(c) is None for c in required):
                continue
            rid = none_if_blank(r.get("id"))
            if rid:
                keep.add(int(rid))
                db.update(table, int(rid), data)
            elif any(v not in (None, False) for v in data.values()):
                db.insert(table, {fk: fk_val, **data})
        for o in original:
            if o["id"] not in keep:
                db.run(f"delete from {table} where id = %s", (o["id"],))


def _to_db(v):
    if isinstance(v, pd.Timestamp):
        return v.date()
    if hasattr(v, "item"):          # numpy 型別
        return v.item()
    return v


def none_if_blank(v):
    if v is None:
        return None
    if isinstance(v, str) and not v.strip():
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v
