"""單一合約：基本資料 → 送案紀錄（可重寫）→ 款項 → 分潤。"""
from datetime import date

import streamlit as st

from wisetech import services as sv
from wisetech.auth import can_edit, current_user, is_admin
from wisetech.parse import parse_pct
from wisetech.ui import (
    MONEY_COL, company_picker, frame, get_db, go_company, money, none_if_blank, save_editor,
)


def page():
    db = get_db()
    kid = st.session_state.get("contract_id")
    if kid:
        k = sv.contract(db, kid)
        if not k:
            st.warning("找不到這份合約")
            return
        _existing(db, k)
    elif can_edit():
        _new(db)
    else:
        st.info("請先從「公司查詢」或「合約總覽」選一份合約")


# ───────────────────────── 新增 ─────────────────────────

def _new(db):
    st.title("➕ 新增合約")
    cid = st.session_state.get("new_contract_company")
    if cid:
        c = sv.company(db, cid)
        st.markdown(f"公司：**{c['name']}**（{c['tax_id'] or '無統編'}）")
        if st.button("換一家公司"):
            st.session_state.new_contract_company = None
            st.rerun()
    else:
        cid = company_picker(db, "newk")
    if not cid:
        return
    staff = [""] + sv.staff(db)
    with st.form("new_contract"):
        c1, c2, c3 = st.columns(3)
        start = c1.date_input("起始日", date.today())
        ctype = c2.selectbox("合約類型", sv.CONTRACT_TYPES)
        end = c3.date_input("終止日（年約才填）", None)
        c1, c2, c3 = st.columns(3)
        contract_no = c1.text_input("合約編號（留空自動編號）")
        program = c2.text_input("送案別（SBIR、SIIR…）")
        deposit = c3.number_input("前金（未稅）", min_value=0, step=1000)
        c1, c2, c3, c4 = st.columns(4)
        deduct = c1.selectbox("前金扣／不扣", sv.DEPOSIT_DEDUCT)
        tax = c2.selectbox("含稅／未稅", sv.TAX_MODES)
        mid_rate = c3.text_input("期中抽成（例 15%）")
        final_rate = c4.text_input("期末抽成（例 15%）")
        c1, c2, c3 = st.columns(3)
        sales = c1.selectbox("Sales", staff)
        partner = c2.selectbox("業務（非 Allen & Andy）", staff)
        writer = c3.selectbox("主寫", staff)
        special = st.text_area("特別約定事項")
        two_phase = st.checkbox("前金分兩期")
        if st.form_submit_button("建立合約", type="primary"):
            no = contract_no.strip() or sv.next_contract_no(db, start)
            with db.tx():
                kid = sv.save_contract(db, {
                    "contract_no": no, "company_id": cid, "start_date": start, "end_date": end,
                    "contract_type": ctype, "deposit_deduct": deduct, "tax_mode": tax, "program": program or None,
                    "status": "In Process", "deposit_total": deposit or None, "mid_rate": mid_rate or None,
                    "final_rate": final_rate or None, "sales": sales or None, "partner_sales": partner or None,
                    "writer": writer or None, "special_terms": special or None})
                db.insert("applications", {"contract_id": kid, "attempt_no": 1, "program": program or None,
                                           "result": "撰寫中", "writer": writer or None})
                if deposit:
                    if two_phase:
                        half = round(deposit / 2)
                        db.insert("payments", {"contract_id": kid, "stage": "前金1", "invoice_amount": half})
                        db.insert("payments", {"contract_id": kid, "stage": "前金2",
                                               "invoice_amount": deposit - half})
                    else:
                        db.insert("payments", {"contract_id": kid, "stage": "前金1", "invoice_amount": deposit})
                db.log(current_user()["username"], "create_contract", "contracts", kid, {"contract_no": no})
            st.session_state.contract_id = kid
            st.session_state.new_contract_company = None
            st.rerun()


# ───────────────────────── 既有合約 ─────────────────────────

def _existing(db, k):
    edit = can_edit()
    st.title(f"📄 {k['contract_no'] or '（無編號）'}")
    c1, c2 = st.columns([4, 1])
    c1.markdown(f"**{k['company'] or k['company_raw']}**　統編 {k['tax_id'] or '—'}"
                + ("　🛑 已解約" if k["is_terminated"] else "") + ("　🔒 保密合約" if k["is_nda"] else ""))
    if k["company_id"] and c2.button("← 回公司頁"):
        go_company(k["company_id"])

    m = st.columns(4)
    m[0].metric("狀態", k["status"] or "—")
    m[1].metric("送案別", k["program"] or "—")
    m[2].metric("前金", money(k["deposit_total"]))
    m[3].metric("過案金額", money(k["approved_amount"]))

    tabs = ["📝 送案紀錄", "💰 款項", "📋 合約內容"]
    if is_admin():
        tabs.append("🏆 獎金")
    t = st.tabs(tabs)
    with t[0]:
        _applications(db, k, edit)
    with t[1]:
        _payments(db, k, edit)
    with t[2]:
        _contract_form(db, k, edit)
    if is_admin():
        with t[3]:
            _bonus(db, k)


def _applications(db, k, edit):
    apps = sv.applications(db, k["id"])
    st.caption("同一份合約重寫再送不另收前金；合約狀態跟著最後一次送案。")
    cols = {"id": "id", "attempt_no": "次數", "program": "送案別", "submitted_date": "送件日",
            "result": "結果", "result_date": "結果日", "approved_amount": "過案金額", "writer": "主寫", "note": "備註"}
    df = frame(apps, cols)
    if not edit:
        st.dataframe(df.drop(columns="id", errors="ignore"), hide_index=True, width="stretch",
                     column_config={"過案金額": MONEY_COL})
        return
    edited = st.data_editor(
        df, hide_index=True, width="stretch", num_rows="dynamic", key=f"apps_{k['id']}",
        column_config={"id": None,
                       "次數": st.column_config.NumberColumn(min_value=1, step=1, required=True),
                       "結果": st.column_config.SelectboxColumn(options=sv.APP_RESULTS, required=True),
                       "送件日": st.column_config.DateColumn(), "結果日": st.column_config.DateColumn(),
                       "過案金額": MONEY_COL,
                       "主寫": st.column_config.SelectboxColumn(options=sv.staff(db))})
    c1, c2 = st.columns(2)
    if c1.button("💾 儲存送案紀錄", key="save_apps"):
        save_editor(db, "applications", "contract_id", k["id"], apps, edited,
                    {v: c for c, v in cols.items() if c != "id"}, required=("attempt_no",))
        sv.sync_contract_status(db, k["id"])
        db.log(current_user()["username"], "update_applications", "contracts", k["id"])
        st.rerun()
    last = apps[-1] if apps else None
    if last and last["result"] == "Fail" and c2.button("✍️ 重寫再送（新增下一次送案）", type="primary"):
        db.insert("applications", {"contract_id": k["id"], "attempt_no": last["attempt_no"] + 1,
                                   "program": last["program"], "result": "撰寫中", "writer": last["writer"]})
        sv.sync_contract_status(db, k["id"])
        st.rerun()


def _payments(db, k, edit):
    pays = sv.payments(db, k["id"])
    cols = {"id": "id", "stage": "期別", "grant_amount": "撥款金額", "invoice_amount": "發票金額",
            "invoice_date": "發票日", "invoice_no": "發票號碼", "is_received": "已入帳", "received_date": "入帳日",
            "note": "備註"}
    df = frame(pays, cols)
    rates = {"期中": parse_pct(k["mid_rate"]), "期末": parse_pct(k["final_rate"])}
    if not df.empty:
        df.insert(3, "應收(估)", [
            float(g) * float(rates[s]) / 100 if g and s in rates and rates[s] else None
            for g, s in zip(df["撥款金額"], df["期別"])])
    st.caption(f"期中抽成 {k['mid_rate'] or '—'}／期末抽成 {k['final_rate'] or '—'}。"
               "「應收(估)」= 撥款金額 × 抽成，開發票時請把實際金額填到「發票金額」。")
    if not edit:
        st.dataframe(df.drop(columns="id", errors="ignore"), hide_index=True, width="stretch",
                     column_config={"撥款金額": MONEY_COL, "發票金額": MONEY_COL, "應收(估)": MONEY_COL})
        return
    edited = st.data_editor(
        df, hide_index=True, width="stretch", num_rows="dynamic", key=f"pays_{k['id']}",
        disabled=["應收(估)"],
        column_config={"id": None,
                       "期別": st.column_config.SelectboxColumn(options=sv.STAGES, required=True),
                       "撥款金額": MONEY_COL, "發票金額": MONEY_COL, "應收(估)": MONEY_COL,
                       "發票日": st.column_config.DateColumn(), "入帳日": st.column_config.DateColumn(),
                       "已入帳": st.column_config.CheckboxColumn(default=False)})
    c1, c2 = st.columns(2)
    if c1.button("💾 儲存款項", key="save_pays"):
        # 填了入帳日就自動勾已入帳
        if not edited.empty:
            edited.loc[edited["入帳日"].notna(), "已入帳"] = True
        save_editor(db, "payments", "contract_id", k["id"], pays, edited,
                    {v: c for c, v in cols.items() if c != "id"}, required=("stage",))
        db.log(current_user()["username"], "update_payments", "contracts", k["id"])
        st.rerun()
    stages = {p["stage"] for p in pays}
    if (k["status"] or "").lower().startswith("pass") and not {"期中", "期末"} <= stages:
        if c2.button("➕ 過案了：建立期中／期末款項", type="primary"):
            for s in ("期中", "期末"):
                if s not in stages:
                    db.insert("payments", {"contract_id": k["id"], "stage": s})
            st.rerun()


def _contract_form(db, k, edit):
    staff = [""] + sv.staff(db)

    def pick(label, val, col):
        opts = staff if (val or "") in staff else staff + [val]
        return col.selectbox(label, opts, index=opts.index(val or ""), disabled=not edit)

    with st.form(f"contract_{k['id']}"):
        c1, c2, c3 = st.columns(3)
        no = c1.text_input("合約編號", k["contract_no"] or "", disabled=not edit)
        ctype_opts = sv.CONTRACT_TYPES if (k["contract_type"] or "個案") in sv.CONTRACT_TYPES \
            else sv.CONTRACT_TYPES + [k["contract_type"]]
        ctype = c2.selectbox("合約類型", ctype_opts, index=ctype_opts.index(k["contract_type"] or "個案"),
                             disabled=not edit)
        deposit = c3.number_input("前金（未稅）", value=float(k["deposit_total"] or 0), step=1000.0, disabled=not edit)
        c1, c2, c3, c4 = st.columns(4)
        start = c1.date_input("起始日", k["start_date"], disabled=not edit)
        end = c2.date_input("終止日", k["end_date"], disabled=not edit)
        deduct = c3.text_input("前金扣／不扣", k["deposit_deduct"] or "", disabled=not edit)
        tax = c4.text_input("含稅／未稅", k["tax_mode"] or "", disabled=not edit)
        c1, c2, c3, c4 = st.columns(4)
        mid = c1.text_input("期中抽成", k["mid_rate"] or "", disabled=not edit)
        fin = c2.text_input("期末抽成", k["final_rate"] or "", disabled=not edit)
        ext = c3.text_input("外部佣金 %", k["ext_commission_rate"] or "", disabled=not edit)
        ext_src = c4.text_input("外部佣金來源", k["ext_source"] or "", disabled=not edit)
        c1, c2, c3 = st.columns(3)
        sales = pick("Sales", k["sales"], c1)
        partner = pick("業務（非 Allen & Andy）", k["partner_sales"], c2)
        writer = pick("主寫", k["writer"], c3)
        c1, c2, c3 = st.columns(3)
        outsource = pick("委外", k["outsource"], c1)
        mrev = pick("期中管考", k["mid_review"], c2)
        frev = pick("期末管考", k["final_review"], c3)
        special = st.text_area("特別約定事項", k["special_terms"] or "", disabled=not edit)
        c1, c2 = st.columns(2)
        term = c1.checkbox("已解約", k["is_terminated"], disabled=not edit)
        nda = c2.checkbox("保密合約", k["is_nda"], disabled=not edit)
        if edit and st.form_submit_button("💾 儲存合約", type="primary"):
            data = {"contract_no": no.strip() or None, "contract_type": ctype, "deposit_total": deposit or None,
                    "start_date": start, "end_date": end, "deposit_deduct": deduct or None, "tax_mode": tax or None,
                    "mid_rate": mid or None, "final_rate": fin or None, "ext_commission_rate": ext or None,
                    "ext_source": ext_src or None, "sales": sales or None, "partner_sales": partner or None,
                    "writer": writer or None, "outsource": outsource or None, "mid_review": mrev or None,
                    "final_review": frev or None, "special_terms": special or None, "is_terminated": term,
                    "is_nda": nda}
            sv.save_contract(db, data, k["id"])
            db.log(current_user()["username"], "update_contract", "contracts", k["id"], data)
            st.success("已儲存")
            st.rerun()
    if k.get("name_note") or k.get("raw"):
        with st.expander("Excel 原始資料"):
            if k.get("name_note"):
                st.write("名稱註記：", k["name_note"])
            st.caption(f"來源：{k['source_sheet']} 第 {k['source_row']} 列" if k.get("source_sheet") else "")
            st.json(k.get("raw") or {})


def _bonus(db, k):
    st.markdown("**① 主筆過案獎金與獎金池**　過案款（期中／期末／其他）入帳後，依下面比例計算。")
    staff = [""] + sv.staff(db)
    mw = k.get("main_writer") or ""
    with st.form(f"bonus_pct_{k['id']}"):
        c1, c2, c3 = st.columns(3)
        opts = staff if mw in staff else staff + [mw]
        main_writer = c1.selectbox("主筆（空白＝用主寫）", opts, index=opts.index(mw))
        wpct = c2.number_input("主筆獎金 %", 0.0, 100.0, float(k.get("writer_bonus_pct") or 0), 0.5)
        ppct = c3.number_input("獎金池 %", 0.0, 100.0, float(k.get("pool_pct") or 0), 0.5)
        c1, c2 = st.columns(2)
        perf_amt = c1.number_input("績效基數（空白＝過案金額 × 期末抽成）", 0.0,
                                   value=float(k["performance_amount"]) if k.get("performance_amount") else None,
                                   step=1000.0)
        perf_date = c2.date_input("公文日", k.get("performance_date"))
        if st.form_submit_button("💾 儲存"):
            data = {"main_writer": main_writer or None, "writer_bonus_pct": wpct, "pool_pct": ppct,
                    "performance_amount": perf_amt or None, "performance_date": perf_date}
            sv.save_contract(db, data, k["id"])
            db.log(current_user()["username"], "update_bonus_rule", "contracts", k["id"], data)
            st.rerun()
    due = sv.bonus_due(db, contract_id=k["id"])
    if due:
        st.dataframe(frame(due, {"received_date": "入帳日", "stage": "期別", "invoice_amount": "款項",
                                 "kind": "類別", "person": "對象", "pct": "%", "amount": "應發",
                                 "is_paid": "已發", "paid_date": "發放日"}),
                     hide_index=True, width="stretch", column_config={"款項": MONEY_COL, "應發": MONEY_COL})

    st.markdown("**② 績效分配**　角色與比例自由設定；合約狀態為 Pass 才計入。")
    rows = sv.bonus_splits(db, k["id"])
    cols = {"id": "id", "role": "角色", "person": "對象", "pct": "%", "note": "備註"}
    c1, c2 = st.columns([3, 1])
    tpl = c1.selectbox("常用分法", list(sv.PERF_TEMPLATES), key="perf_tpl")
    if c2.button("帶入（取代目前設定）"):
        who = {"Sales": k["partner_sales"] or k["sales"], "主寫": k["writer"],
               "管考": k["mid_review"] or k["final_review"]}
        sv.replace_bonus_splits(db, k["id"], [{"role": r, "person": who.get(r) or "（未指定）", "pct": p}
                                              for r, p in sv.PERF_TEMPLATES[tpl]])
        st.rerun()
    edited = st.data_editor(
        frame(rows, cols), hide_index=True, width="stretch", num_rows="dynamic", key=f"perf_{k['id']}",
        column_config={"id": None,
                       "角色": st.column_config.TextColumn(help="可自訂，例如：" + "、".join(sv.ROLE_SUGGESTIONS),
                                                         required=True),
                       "對象": st.column_config.TextColumn(required=True),
                       "%": st.column_config.NumberColumn(min_value=0, max_value=100, step=0.5, required=True)})
    if not edited.empty:
        total = edited["%"].fillna(0).sum()
        (st.success if abs(total - 100) < 0.01 else st.info)(f"合計 {total:g}%")
    if st.button("💾 儲存績效分配"):
        save_editor(db, "bonus_splits", "contract_id", k["id"], rows, edited,
                    {v: c for c, v in cols.items() if c != "id"}, required=("role", "person", "pct"))
        db.log(current_user()["username"], "update_bonus_splits", "contracts", k["id"])
        st.rerun()
    perf = sv.performance(db, contract_id=k["id"])
    if perf:
        st.caption(f"績效基數 {money(perf[0]['base'])}")
        st.dataframe(frame(perf, {"role": "角色", "person": "對象", "pct": "%", "amount": "績效"}),
                     hide_index=True, width="stretch", column_config={"績效": MONEY_COL})

    hist = sv.disbursements(db, contract_id=k["id"])
    if hist:
        with st.expander(f"獎金發放紀錄（{len(hist)}）"):
            st.dataframe(frame(hist, {"paid_date": "發放日", "kind": "類別", "person": "對象", "amount": "金額",
                                      "paid_text": "發放日原文", "note": "說明"}),
                         hide_index=True, width="stretch", column_config={"金額": MONEY_COL})
