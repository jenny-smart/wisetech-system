import streamlit as st

from wisetech import services as sv
from wisetech.auth import can_edit, current_user
from wisetech.parse import valid_tax_id
from wisetech.ui import MONEY_COL, TAX_STATUS_LABEL, frame, get_db, go_company, go_contract, money


def search_page():
    db = get_db()
    st.title("🔎 公司查詢")
    q = st.text_input("公司名稱、統編、合約編號", placeholder="例如：谷奧、82808896、11503-07",
                      key="company_search")
    rows = sv.search_companies(db, q)
    st.caption(f"{len(rows)} 家" + ("（最近有合約的前 50 家）" if not q else ""))
    df = frame(rows, {"name": "公司名稱", "tax_id": "統編", "tax_id_status": "統編狀態", "contract_count": "合約數",
                      "pass_count": "過案數", "last_contract_date": "最近合約", "outstanding_total": "未入帳"})
    if not df.empty:
        df["統編狀態"] = df["統編狀態"].map(TAX_STATUS_LABEL)
    ev = st.dataframe(df, hide_index=True, width="stretch", on_select="rerun",
                      selection_mode="single-row", column_config={"未入帳": MONEY_COL})
    if ev.selection.rows:
        go_company(rows[ev.selection.rows[0]]["id"])

    if can_edit():
        with st.expander("➕ 新增公司"):
            with st.form("new_company", clear_on_submit=True):
                c1, c2 = st.columns([2, 1])
                name = c1.text_input("公司名稱 *")
                tax_id = c2.text_input("統一編號", max_chars=8)
                address = st.text_input("地址")
                if st.form_submit_button("新增", type="primary"):
                    try:
                        if not name.strip():
                            raise ValueError("公司名稱必填")
                        cid = sv.create_company(db, name, tax_id, address or None)
                        db.log(current_user()["username"], "create_company", "companies", cid, {"name": name})
                        go_company(cid)
                    except ValueError as e:
                        st.error(str(e))


def detail_page():
    db = get_db()
    cid = st.session_state.get("company_id")
    if not cid:
        st.info("請先在「公司查詢」選一家公司")
        return
    c = sv.company(db, cid)
    if not c:
        st.warning("找不到這家公司（可能已被合併）")
        return

    st.title(c["name"])
    st.caption(f"統編 **{c['tax_id'] or '—'}**　{TAX_STATUS_LABEL.get(c['tax_id_status'], '')}"
               + (f"　商工登記：{c['gcis_name']}（{c['gcis_status']}）" if c.get("gcis_name") else ""))
    m = st.columns(5)
    m[0].metric("合約數", c["contract_count"])
    m[1].metric("過案", c["pass_count"])
    m[2].metric("已開發票", money(c["invoiced_total"]))
    m[3].metric("已入帳", money(c["received_total"]))
    m[4].metric("未入帳", money(c["outstanding_total"]))

    tabs = st.tabs(["📄 合約", "💰 款項", "📮 寄件／用印", "👤 聯絡人", "⚙️ 公司資料"])

    with tabs[0]:
        ks = sv.contracts_of(db, cid)
        df = frame(ks, {"contract_no": "合約編號", "contract_type": "類型", "program": "送案別", "status": "狀態",
                        "attempts": "送案次數", "start_date": "起始日", "deposit_total": "前金",
                        "approved_amount": "過案金額", "mid_rate": "期中%", "final_rate": "期末%",
                        "name_note": "註記"})
        ev = st.dataframe(df, hide_index=True, width="stretch", on_select="rerun",
                          selection_mode="single-row",
                          column_config={"前金": MONEY_COL, "過案金額": MONEY_COL})
        if ev.selection.rows:
            go_contract(ks[ev.selection.rows[0]]["id"])
        if can_edit() and st.button("➕ 為這家公司新增合約"):
            st.session_state.contract_id = None
            st.session_state.new_contract_company = cid
            st.switch_page(__import__("wisetech.ui", fromlist=["PAGES"]).PAGES["contract"])

    with tabs[1]:
        ps = sv.payments_of_company(db, cid)
        df = frame(ps, {"contract_no": "合約", "stage": "期別", "grant_amount": "撥款金額",
                        "invoice_amount": "發票金額", "invoice_date": "發票日", "invoice_no": "發票號碼",
                        "is_received": "已入帳", "received_date": "入帳日", "note": "備註"})
        st.dataframe(df, hide_index=True, width="stretch",
                     column_config={"撥款金額": MONEY_COL, "發票金額": MONEY_COL})

    with tabs[2]:
        sh = sv.shipments_of(db, cid)
        st.dataframe(frame(sh, {"sent_date": "寄出日", "method": "方式", "purpose": "用途", "contract_ref": "合約",
                                "party_a_stamp": "甲方用印", "our_stamp": "我方用印", "copies": "份數",
                                "return_status": "寄回狀態", "invoice_status": "發票", "contact": "收件人",
                                "address": "地址"}), hide_index=True, width="stretch")

    with tabs[3]:
        rows = sv.contacts(db, cid)
        df = frame(rows, {"id": "id", "name": "姓名", "phone": "電話", "email": "Email", "address": "地址",
                          "note": "備註", "last_seen": "最近往來"})
        if can_edit():
            edited = st.data_editor(df, hide_index=True, width="stretch", num_rows="dynamic",
                                    disabled=["id", "最近往來"], column_config={"id": None}, key=f"contacts_{cid}")
            if st.button("儲存聯絡人"):
                _save_contacts(db, cid, rows, edited)
                st.success("已儲存")
                st.rerun()
        else:
            st.dataframe(df.drop(columns=["id"], errors="ignore"), hide_index=True, width="stretch")

    with tabs[4]:
        _company_form(db, c)
        st.markdown("**Excel 上出現過的寫法**")
        st.write("、".join(a["alias"] for a in sv.aliases(db, cid)) or "—")


def _save_contacts(db, cid, original, edited):
    from wisetech.ui import none_if_blank
    keep = set()
    for _, r in edited.iterrows():
        data = {"name": none_if_blank(r.get("姓名")), "phone": none_if_blank(r.get("電話")),
                "email": none_if_blank(r.get("Email")), "address": none_if_blank(r.get("地址")),
                "note": none_if_blank(r.get("備註"))}
        rid = none_if_blank(r.get("id"))
        if rid:
            keep.add(int(rid))
            db.update("contacts", int(rid), data)
        elif any(data.values()):
            db.insert("contacts", {"company_id": cid, **data})
    for o in original:
        if o["id"] not in keep:
            db.run("delete from contacts where id = %s", (o["id"],))


def _company_form(db, c):
    if not can_edit():
        st.write(f"地址：{c['address'] or '—'}")
        st.write(f"備註：{c['note'] or '—'}")
        return
    with st.form("company_edit"):
        name = st.text_input("公司名稱", c["name"])
        tax_id = st.text_input("統一編號", c["tax_id"] or "", max_chars=8)
        address = st.text_input("地址", c["address"] or "")
        note = st.text_area("備註", c["note"] or "")
        if st.form_submit_button("儲存", type="primary"):
            tid = tax_id.strip() or None
            if tid and not valid_tax_id(tid):
                st.error("統一編號檢查碼不正確")
                return
            other = sv.company_by_tax_id(db, tid) if tid else None
            if other and other["id"] != c["id"]:
                st.error(f"統編 {tid} 已經屬於「{other['name']}」。如果是同一家公司，請到「統編確認」頁合併。")
                return
            data = {"name": name.strip(), "tax_id": tid, "address": address or None, "note": note or None}
            if tid != c["tax_id"]:
                data["tax_id_status"] = "confirmed" if tid else "pending"
            db.update("companies", c["id"], data)
            db.log(current_user()["username"], "update_company", "companies", c["id"], data)
            st.success("已儲存")
            st.rerun()
