"""👥 個人進度：撰寫／送案、收款、管考、業績與獎金。一般人只看自己，管理員看全部。"""
from datetime import date

import pandas as pd
import streamlit as st

from wisetech import services as sv
from wisetech.auth import current_user, is_admin
from wisetech.ui import MONEY_COL, frame, get_db, go_contract, money


def _yn(invoiced, received):
    if received:
        return "✅ 已入帳"
    if invoiced:
        return "🧾 已開票"
    return "⏳ 未開票"


def page():
    db = get_db()
    u = current_user()
    st.title("👥 個人進度")

    if is_admin():
        team = sv.team_overview(db)
        with st.expander("全員總表", expanded=True):
            df = frame(team, {"person": "人員", "writing": "撰寫中", "reviewing": "審查中", "fail_open": "Fail待重寫",
                              "pass_ytd": "今年過案", "outstanding": "待收款", "review_open": "管考未結",
                              "bonus_due": "應發未發獎金", "total": "參與合約"})
            st.dataframe(df, hide_index=True, width="stretch",
                         column_config={"待收款": MONEY_COL, "應發未發獎金": MONEY_COL})
        names = [t["person"] for t in team] or sv.staff(db)
        default = names.index(u["staff_name"]) if u.get("staff_name") in names else 0
        person = st.selectbox("查看", names, index=default) if names else None
    else:
        person = u.get("staff_name")
        if not person:
            st.warning("你的帳號還沒對應到人員名單，請管理員到「帳號與人員」設定。")
            return
    if not person:
        st.info("人員名單是空的")
        return

    live_only = st.toggle("只看進行中（近 3 年或尚有待收款；排除解約、撤案、已全部結清）", True)
    rows = sv.person_contracts(db, person)
    if live_only:
        rows = [r for r in rows if sv.is_live(r)]

    writer = [r for r in rows if r["is_writer"]]
    sales = [r for r in rows if r["is_sales"]]
    review = [r for r in rows if r["is_review"] and r["result"] == "Pass"]

    m = st.columns(5)
    m[0].metric("撰寫中", sum(r["result"] == "撰寫中" for r in writer))
    m[1].metric("審查中", sum(r["result"] == "審查中" for r in writer))
    m[2].metric("Fail 待重寫", len({r["id"] for r in writer + sales if r["result"] == "Fail"}))
    m[3].metric("待收款", money(sum(r["outstanding"] or 0 for r in sales)))
    m[4].metric("管考未結", sum(not (r["mid_received"] and r["fin_received"]) for r in review))

    tabs = ["✍️ 撰寫／送案", "💼 業務收款", "📋 管考"]
    show_bonus = is_admin() or u["role"] == "sales"
    if show_bonus:
        tabs.append("🏆 業績與獎金")
    t = st.tabs(tabs)

    with t[0]:
        st.caption(f"{person} 擔任主寫或主筆的案件，依最後一次送案結果分組。")
        _table(writer, {"contract_no": "合約", "company": "公司", "program": "送案別", "attempt_no": "第幾次",
                        "result": "結果", "submitted_date": "送件日", "result_date": "結果日"},
               "w", order=["撰寫中", "審查中", "Fail", "Pass", "撤案"])

    with t[1]:
        st.caption(f"{person} 擔任 Sales／業務的合約：前金與過案款的開票、入帳狀況。")
        for r in sales:
            r["前金"] = ("✅ 已入帳" if r["dep_received"] else "🧾 已開票" if r["dep_invoiced"] else
                       ("⏳ 未開票" if r["deposit_total"] else "—"))
            r["期中"] = _yn(r["mid_invoiced"], r["mid_received"]) if r["result"] == "Pass" else "—"
            r["期末"] = _yn(r["fin_invoiced"], r["fin_received"]) if r["result"] == "Pass" else "—"
        _table(sales, {"contract_no": "合約", "company": "公司", "program": "送案別", "result": "送案結果",
                       "deposit_total": "前金", "前金": "前金狀態", "期中": "期中款", "期末": "期末款",
                       "outstanding": "待收款"}, "s")

    with t[2]:
        st.caption(f"{person} 擔任期中／期末管考、已過案的案件。以期中／期末款是否入帳判斷進度。")
        for r in review:
            r["期中"] = _yn(r["mid_invoiced"], r["mid_received"])
            r["期末"] = _yn(r["fin_invoiced"], r["fin_received"])
        _table(review, {"contract_no": "合約", "company": "公司", "program": "送案別",
                        "approved_amount": "過案金額", "期中": "期中", "期末": "期末"}, "r")

    if show_bonus:
        with t[3]:
            y0 = date(date.today().year, 1, 1)
            perf = sv.performance(db, person=person, start=y0)
            due = sv.bonus_due(db, person=person, unpaid_only=True)
            paid = sv.disbursements(db, person=person, start=y0)
            c = st.columns(3)
            c[0].metric(f"{date.today().year} 績效", money(sum(p["amount"] or 0 for p in perf)))
            c[1].metric("應發未發獎金", money(sum(d["amount"] or 0 for d in due)))
            c[2].metric(f"{date.today().year} 已發獎金", money(sum(d["amount"] or 0 for d in paid)))
            st.markdown("**今年績效明細**")
            st.dataframe(frame(perf, {"perf_date": "公文日", "contract_no": "合約", "company": "公司",
                                      "role": "角色", "pct": "%", "amount": "績效"}),
                         hide_index=True, width="stretch", column_config={"績效": MONEY_COL})
            if due:
                st.markdown("**應發未發**")
                st.dataframe(frame(due, {"received_date": "入帳日", "contract_no": "合約", "company": "公司",
                                         "kind": "類別", "amount": "應發"}),
                             hide_index=True, width="stretch", column_config={"應發": MONEY_COL})


def _table(rows, cols, key, order=None):
    if not rows:
        st.info("沒有資料")
        return
    if order:
        rank = {v: i for i, v in enumerate(order)}
        rows = sorted(rows, key=lambda r: rank.get(r.get("result"), 99))
    df = frame(rows, cols)
    ev = st.dataframe(df, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                      key=f"prog_{key}", column_config={"前金": MONEY_COL, "待收款": MONEY_COL,
                                                        "過案金額": MONEY_COL})
    if ev.selection.rows:
        go_contract(rows[ev.selection.rows[0]]["id"])
