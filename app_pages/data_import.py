"""📥 資料匯入：上傳舊 Excel → 預覽 → 匯入；批次比對統編。"""
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from scripts.import_excel import dedupe_contracts, load, write_report
from scripts.lookup_taxid import run as lookup_run
from wisetech.excel_import import ParseResult, group_companies, parse_workbook, summary
from wisetech.ui import get_db


def page():
    db = get_db()
    st.title("📥 資料匯入")
    t1, t2 = st.tabs(["① 匯入舊 Excel", "② 批次比對統編"])
    with t1:
        _import(db)
    with t2:
        _lookup(db)


def _import(db):
    st.caption("從 Google Sheet 下載成 .xlsx（檔案 → 下載 → Microsoft Excel），合約帳務檔與績效獎金檔一起上傳。")
    files = st.file_uploader("Excel 檔", type=["xlsx"], accept_multiple_files=True)
    if not files:
        return
    if st.button("🔍 預覽", type="primary"):
        with st.spinner("解析中…"):
            tmp = Path(tempfile.mkdtemp())
            res = ParseResult()
            # 合約帳務檔先讀（同編號合約以它為主）
            for f in sorted(files, key=lambda f: "績效" in f.name or "獎金" in f.name):
                p = tmp / f.name
                p.write_bytes(f.getvalue())
                parse_workbook(str(p), res)
            dup = dedupe_contracts(res)
            groups, _, merges = group_companies(res)
            report = tmp / "import_report.xlsx"
            write_report(report, res, groups, merges, dup)
            st.session_state.import_preview = {"res": res, "groups": groups, "merges": merges, "dup": dup,
                                               "report": report.read_bytes()}
    pv = st.session_state.get("import_preview")
    if not pv:
        return
    s = summary(pv["res"])
    c = st.columns(5)
    c[0].metric("合約", s["contracts"])
    c[1].metric("發票", s["invoices"])
    c[2].metric("寄件", s["shipments"])
    c[3].metric("公司", len(pv["groups"]))
    c[4].metric("無法解析的值", s["issues"])
    st.caption("各年度合約數：" + "、".join(f"{k}：{v}" for k, v in s["by_sheet"].items()))
    if s["skipped"]:
        with st.expander(f"略過的工作表（{len(s['skipped'])}）"):
            st.dataframe(pd.DataFrame(s["skipped"], columns=["工作表", "原因"]), hide_index=True, width="stretch")
    with st.expander(f"簡稱自動歸戶（{len(pv['merges'])}）— 請確認沒有歸錯"):
        st.dataframe(pd.DataFrame(pv["merges"]), hide_index=True, width="stretch")
    st.download_button("⬇️ 下載完整對照報表（公司歸戶、合約、無法解析…）", pv["report"],
                       file_name="import_report.xlsx")

    st.divider()
    existing = db.val("select count(*) from contracts")
    reset = False
    if existing:
        st.warning(f"資料庫目前已有 {existing} 份合約。")
        reset = st.checkbox("清空所有業務資料後重新匯入（帳號與人員名單保留，無法復原）")
    confirm = st.text_input("確認匯入請輸入「匯入」")
    if st.button("📥 開始匯入", type="primary", disabled=confirm.strip() != "匯入" or (existing and not reset)):
        bar = st.progress(0.0, text="準備寫入…")
        st.caption("寫入中請不要關閉或重新整理頁面；萬一中斷，資料會整批取消，可以重新匯入。")
        load(db, pv["res"], pv["groups"], reset, progress=lambda f, msg: bar.progress(f, text=f"寫入{msg}"))
        st.session_state.pop("import_preview")
        st.success("匯入完成！下一步：到「② 批次比對統編」。")
        st.balloons()


def _lookup(db):
    n = db.val("select count(*) from companies where tax_id is null and tax_id_status = 'pending'")
    st.write(f"尚未比對統編的公司：**{n}** 家")
    st.caption("用公司名稱查經濟部商工登記。完全相符的自動帶入，其他到「統編確認」頁處理。"
               "一家約 1 秒，可以分批跑，中斷了再按一次會從沒查過的繼續。")
    limit = st.number_input("這次比對幾家", 10, 1000, min(100, max(n, 10)), step=10)
    if st.button("🔍 開始比對", type="primary", disabled=not n):
        bar = st.progress(0.0)
        box = st.empty()
        lines: list[str] = []

        def log(msg):
            lines.append(msg)
            box.code("\n".join(lines[-12:]))

        stats = lookup_run(db, ("pending",), int(limit), 0.3, log=log,
                           progress=lambda i, total: bar.progress(i / total))
        st.success(f"自動帶入 {stats['auto']}、待確認 {stats['candidate']}、查無 {stats['not_found']}、"
                   f"失敗 {stats['error']}")
