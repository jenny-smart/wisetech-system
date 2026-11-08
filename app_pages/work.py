"""合約總覽、待辦（收款／過案／重寫）、寄件用印。"""
from datetime import date

import streamlit as st

from wisetech import services as sv
from wisetech.auth import can_edit, current_user
from wisetech.parse import parse_check
from wisetech.ui import MONEY_COL, company_picker, frame, get_db, go_contract


def contracts_page():
    db = get_db()
    st.title("📄 合約總覽")
    c1, c2, c3, c4, c5 = st.columns([2, 1, 1, 1, 1])
    q = c1.text_input("搜尋（公司、統編、合約編號、送案別）")
    years = [None] + list(range(date.today().year, 2018, -1))
    year = c2.selectbox("年度", years, format_func=lambda y: "全部" if y is None else f"{y}（{y - 1911}）")
    status = c3.selectbox("狀態", [None, "In Process", "Pass", "Fail", "Closed", "Pending"],
                          format_func=lambda s: s or "全部")
    ctype = c4.selectbox("類型", [None] + sv.CONTRACT_TYPES, format_func=lambda s: s or "全部")
    person = c5.selectbox("人員", [None] + sv.staff(db), format_func=lambda s: s or "全部")
    rows = sv.list_contracts(db, year=year, status=status, ctype=ctype, person=person, q=q or None)
    st.caption(f"{len(rows)} 份")
    df = frame(rows, {"contract_no": "合約編號", "company": "公司", "tax_id": "統編", "contract_type": "類型",
                      "program": "送案別", "status": "狀態", "start_date": "起始日", "deposit_total": "前金",
                      "approved_amount": "過案金額", "sales": "業務", "writer": "主寫"})
    ev = st.dataframe(df, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                      column_config={"前金": MONEY_COL, "過案金額": MONEY_COL})
    if ev.selection.rows:
        go_contract(rows[ev.selection.rows[0]]["id"])
    if can_edit() and st.button("➕ 新增合約", type="primary"):
        st.session_state.contract_id = None
        st.session_state.new_contract_company = None
        from wisetech.ui import PAGES
        st.switch_page(PAGES["contract"])


def todo_page():
    db = get_db()
    st.title("✅ 待辦")
    out = sv.outstanding(db)
    passed = sv.passed_without_followup(db)
    failed = sv.failed_open(db)
    t = st.tabs([f"💰 已開發票未入帳（{len(out)}）", f"🎉 過案待收後續款（{len(passed)}）",
                 f"✍️ 未過待重寫（{len(failed)}）"])
    with t[0]:
        df = frame(out, {"contract_no": "合約", "company": "公司", "stage": "期別", "invoice_amount": "金額",
                         "invoice_date": "發票日", "invoice_no": "發票號碼", "days": "天數"})
        if not df.empty:
            df.insert(0, "入帳", False)
        if can_edit() and not df.empty:
            c1, c2 = st.columns([1, 3])
            recv_date = c1.date_input("入帳日", date.today())
            edited = st.data_editor(df, hide_index=True, width="stretch", disabled=list(df.columns[1:]),
                                    column_config={"金額": MONEY_COL})
            picked = [out[i]["id"] for i, v in enumerate(edited["入帳"]) if v]
            if picked and st.button(f"標記 {len(picked)} 筆已入帳", type="primary"):
                for pid in picked:
                    db.update("payments", pid, {"is_received": True, "received_date": recv_date})
                db.log(current_user()["username"], "mark_received", "payments", None, {"ids": picked})
                st.rerun()
        else:
            st.dataframe(df, hide_index=True, width="stretch", column_config={"金額": MONEY_COL})
    with t[1]:
        st.caption("狀態為 Pass、但還沒有開期中／期末發票的合約。")
        _pick_table(passed, {"contract_no": "合約", "company": "公司", "program": "送案別",
                             "approved_amount": "過案金額", "mid_rate": "期中%", "final_rate": "期末%"}, "passed")
    with t[2]:
        st.caption("最後一次送案結果為 Fail 的合約；點進去按「重寫再送」。")
        _pick_table(failed, {"contract_no": "合約", "company": "公司", "program": "送案別",
                             "attempt_no": "已送次數", "result_date": "結果日"}, "failed")


def _pick_table(rows, cols, key):
    ev = st.dataframe(frame(rows, cols), hide_index=True, width="stretch", on_select="rerun",
                      selection_mode="single-row", key=key, column_config={"過案金額": MONEY_COL})
    if ev.selection.rows:
        go_contract(rows[ev.selection.rows[0]]["id"])


def shipments_page():
    db = get_db()
    st.title("📮 寄件／用印")
    if can_edit():
        with st.expander("➕ 新增寄件紀錄", expanded=False):
            cid = company_picker(db, "ship")
            if cid:
                ks = sv.contracts_of(db, cid)
                kopts = {None: "（不指定）"} | {k["id"]: f"{k['contract_no']} {k['program'] or ''}" for k in ks}
                with st.form("ship", clear_on_submit=True):
                    c1, c2, c3 = st.columns(3)
                    kid = c1.selectbox("合約", list(kopts), format_func=kopts.get)
                    sent = c2.date_input("寄出／用印日期", date.today())
                    method = c3.selectbox("方式", ["郵寄", "面交", "快遞", "親送"])
                    c1, c2, c3, c4 = st.columns(4)
                    a_stamp = c1.checkbox("甲方用印")
                    our = c2.checkbox("我方用印", True)
                    copies = c3.text_input("份數", "2")
                    inv = c4.selectbox("發票", ["", "發票已開出", "發票待寄", "不用開發票"])
                    purpose = st.text_input("用途（SBIR簽約、北創第一期…）")
                    c1, c2, c3 = st.columns(3)
                    contact = c1.text_input("收件人")
                    phone = c2.text_input("電話")
                    address = c3.text_input("地址")
                    if st.form_submit_button("新增", type="primary"):
                        ref = next((k["contract_no"] for k in ks if k["id"] == kid), None)
                        db.insert("shipments", {"company_id": cid, "contract_id": kid, "contract_ref": ref,
                                                "sent_date": sent, "method": method, "party_a_stamp": a_stamp,
                                                "our_stamp": our, "copies": copies or None,
                                                "invoice_status": inv or None, "purpose": purpose or None,
                                                "contact": contact or None, "phone": phone or None,
                                                "address": address or None, "return_status": "待寄回"})
                        st.rerun()
    pending = st.toggle("只看待寄回")
    sql = """select s.*, c.name as company from shipments s left join companies c on c.id = s.company_id
             where (%s = false or s.return_status ilike '%%待%%' or s.return_status ilike '%%未回%%')
             order by s.sent_date desc nulls last, s.id desc limit 500"""
    rows = db.all(sql, (pending,))
    df = frame(rows, {"id": "id", "sent_date": "寄出日", "company": "公司", "contract_ref": "合約", "method": "方式",
                      "purpose": "用途", "party_a_stamp": "甲方用印", "our_stamp": "我方用印", "copies": "份數",
                      "return_status": "寄回狀態", "invoice_status": "發票", "contact": "收件人"})
    if can_edit() and not df.empty:
        edited = st.data_editor(df, hide_index=True, width="stretch", column_config={"id": None},
                                disabled=["寄出日", "公司", "合約", "方式", "用途", "份數", "收件人"])
        if st.button("💾 儲存寄回狀態"):
            for (_, r), o in zip(edited.iterrows(), rows):
                data = {"return_status": r["寄回狀態"] or None, "invoice_status": r["發票"] or None,
                        "party_a_stamp": parse_check("V" if r["甲方用印"] else None),
                        "our_stamp": parse_check("V" if r["我方用印"] else None)}
                if any(data[k] != o[k] for k in data):
                    db.update("shipments", o["id"], data)
            st.rerun()
    else:
        st.dataframe(df.drop(columns="id", errors="ignore"), hide_index=True, width="stretch")
