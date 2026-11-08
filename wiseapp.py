"""Wisetech 合約及帳務管理系統 — Streamlit 入口。

    streamlit run wiseapp.py
"""
import streamlit as st

from app_pages import admin, companies, contract, data_import, progress, work
from wisetech import ui
from wisetech.auth import ROLE_LABEL, current_user, login_screen, logout

st.set_page_config(page_title="Wisetech 合約帳務", page_icon="📑", layout="wide")


def main():
    try:
        db = ui.get_db()
        db.val("select 1")
    except Exception as e:  # noqa: BLE001
        st.error(f"資料庫連線失敗：{e}")
        st.info("請在 .streamlit/secrets.toml 設定 DATABASE_URL（Supabase → Connect → Session pooler）。")
        st.stop()

    user = current_user()
    if not user:
        login_screen(db)
        return

    P = ui.PAGES
    P["search"] = st.Page(companies.search_page, title="公司查詢", icon="🔎", url_path="companies", default=True)
    P["company"] = st.Page(companies.detail_page, title="公司資料", icon="🏢", url_path="company")
    P["contracts"] = st.Page(work.contracts_page, title="合約總覽", icon="📄", url_path="contracts")
    P["contract"] = st.Page(contract.page, title="合約", icon="📝", url_path="contract")
    P["progress"] = st.Page(progress.page, title="個人進度", icon="👥", url_path="progress")
    P["todo"] = st.Page(work.todo_page, title="待辦", icon="✅", url_path="todo")
    P["shipments"] = st.Page(work.shipments_page, title="寄件／用印", icon="📮", url_path="shipments")
    P["taxid"] = st.Page(admin.taxid_page, title="統編確認", icon="🧾", url_path="taxid")
    P["bonus"] = st.Page(admin.bonus_page, title="獎金績效", icon="🏆", url_path="bonus")
    P["users"] = st.Page(admin.users_page, title="帳號與人員", icon="👤", url_path="users")
    P["import"] = st.Page(data_import.page, title="資料匯入", icon="📥", url_path="import")

    role = user["role"]
    nav = {"查詢": [P["search"], P["company"], P["contracts"], P["contract"], P["progress"]]}
    if role in ("admin", "staff"):
        nav["作業"] = [P["todo"], P["shipments"], P["taxid"]]
    if role in ("admin", "sales"):
        nav.setdefault("獎金", []).append(P["bonus"])
    if role == "admin":
        nav["管理"] = [P["import"], P["users"]]

    with st.sidebar:
        st.markdown(f"**{user['display_name']}**　{ROLE_LABEL[role]}")
        if st.button("登出", width="stretch"):
            logout()

    st.navigation(nav).run()


main()
