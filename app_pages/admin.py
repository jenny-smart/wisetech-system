"""統編確認、獎金績效、帳號管理。"""
from datetime import date

import pandas as pd
import streamlit as st

from wisetech import services as sv
from wisetech.auth import ROLE_LABEL, current_user, hash_pw, is_admin
from wisetech.parse import valid_tax_id
from wisetech.taxid import decide, lookup_by_tax_id, search_by_name
from wisetech.ui import MONEY_COL, TAX_STATUS_LABEL, frame, get_db, go_company


# ───────────────────────── 統編確認 ─────────────────────────

def taxid_page():
    db = get_db()
    st.title("🧾 統編確認")
    counts = {r["tax_id_status"]: r["n"] for r in
              db.all("select tax_id_status, count(*) n from companies group by 1")}
    cols = st.columns(6)
    for col, s in zip(cols, ["pending", "candidate", "not_found", "auto", "confirmed", "not_applicable"]):
        col.metric(TAX_STATUS_LABEL[s], counts.get(s, 0))
    status = st.radio("顯示", ["candidate", "not_found", "pending", "auto"], horizontal=True,
                      format_func=TAX_STATUS_LABEL.get)
    if status == "pending":
        st.caption("批次比對請在電腦上執行 `python scripts/lookup_taxid.py`；也可以在下面逐家查詢。")
    if status == "auto":
        st.caption("名稱完全相符自動帶入的統編，確認沒問題後按「確認」。")
    rows = db.all("select * from companies where tax_id_status = %s order by name limit 200", (status,))
    if not rows:
        st.success("這個分類沒有需要處理的公司 🎉")
        return
    for c in rows:
        with st.container(border=True):
            _one(db, c)


def _one(db, c):
    h1, h2 = st.columns([4, 1])
    h1.markdown(f"**{c['name']}**" + (f"　→ {c['tax_id']}（{c['gcis_name']}）" if c["tax_id"] else "")
                + (f"\n\n<small>{c['note']}</small>" if c["note"] else ""), unsafe_allow_html=True)
    if h2.button("開啟公司", key=f"open_{c['id']}"):
        go_company(c["id"])
    if _merge_prompt(db, c):
        return
    if c["tax_id_status"] == "auto":
        if st.button("✅ 確認", key=f"ok_{c['id']}"):
            sv.set_tax_id(db, c["id"], c["tax_id"], "confirmed", c["gcis_name"], c["gcis_status"])
            st.rerun()
        return
    cands = db.all("select * from taxid_candidates where company_id = %s order by score desc", (c["id"],))
    if cands:
        opts = {x["tax_id"]: f"{x['tax_id']}　{x['gcis_name']}（{x['gcis_status'] or ''}）" for x in cands}
        pick = st.radio("商工登記候選", list(opts), format_func=opts.get, key=f"pick_{c['id']}", index=None)
        if pick and st.button("採用這個統編", key=f"use_{c['id']}", type="primary"):
            hit = next(x for x in cands if x["tax_id"] == pick)
            _apply(db, c, pick, hit["gcis_name"], hit["gcis_status"])
    c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
    manual = c1.text_input("手動輸入統編", key=f"man_{c['id']}", max_chars=8, label_visibility="collapsed",
                           placeholder="手動輸入 8 碼統編")
    if c2.button("套用", key=f"apply_{c['id']}") and manual:
        if not valid_tax_id(manual):
            st.error("統編檢查碼不正確")
        else:
            hit = None
            try:
                hit = lookup_by_tax_id(manual)
            except Exception:  # noqa: BLE001
                pass
            _apply(db, c, manual, hit.name if hit else None, hit.status if hit else None)
    if c3.button("🔍 重新查詢", key=f"q_{c['id']}"):
        try:
            hits = search_by_name(c["name"])
        except Exception as e:  # noqa: BLE001
            st.error(f"查詢失敗：{e}")
            return
        db.run("delete from taxid_candidates where company_id = %s", (c["id"],))
        for h in hits[:10]:
            db.run("insert into taxid_candidates (company_id, tax_id, gcis_name, gcis_status, score) "
                   "values (%s,%s,%s,%s,%s) on conflict do nothing", (c["id"], h.tax_id, h.name, h.status, h.score))
        verdict, _ = decide(hits)
        db.update("companies", c["id"], {"tax_id_status": "candidate" if hits else "not_found"})
        st.rerun()
    if c4.button("非公司／不需要", key=f"na_{c['id']}"):
        db.update("companies", c["id"], {"tax_id_status": "not_applicable"})
        st.rerun()


def _merge_prompt(db, c) -> bool:
    """這家公司要採用的統編已屬於別家 → 顯示合併確認。回傳 True 表示正在等待確認。"""
    owner_id = st.session_state.get(f"merge_{c['id']}")
    if not owner_id:
        return False
    owner = sv.company(db, owner_id)
    st.warning(f"統編 {owner['tax_id']} 已經屬於「{owner['name']}」，應該是同一家公司。")
    c1, c2 = st.columns(2)
    if c1.button(f"合併進「{owner['name']}」", key=f"mg_{c['id']}", type="primary"):
        sv.merge_companies(db, owner["id"], c["id"])
        db.log(current_user()["username"], "merge_company", "companies", owner["id"], {"merged": c["id"]})
        st.session_state.pop(f"merge_{c['id']}")
        st.rerun()
    if c2.button("取消", key=f"mgc_{c['id']}"):
        st.session_state.pop(f"merge_{c['id']}")
        st.rerun()
    return True


def _apply(db, c, tax_id, gname, gstatus):
    owner = sv.company_by_tax_id(db, tax_id)
    if owner and owner["id"] != c["id"]:
        st.session_state[f"merge_{c['id']}"] = owner["id"]
        st.rerun()
    sv.set_tax_id(db, c["id"], tax_id, "confirmed", gname, gstatus)
    db.log(current_user()["username"], "confirm_tax_id", "companies", c["id"], {"tax_id": tax_id})
    st.rerun()


# ───────────────────────── 獎金績效 ─────────────────────────

def bonus_page():
    db = get_db()
    u = current_user()
    st.title("🏆 獎金績效")
    if is_admin():
        person = st.selectbox("人員", [None] + sv.staff(db, active_only=False) + ["獎金池"],
                              format_func=lambda s: s or "全部")
    else:
        person = u.get("staff_name")
        if not person:
            st.warning("你的帳號還沒對應到人員名單，請管理員設定。")
            return
        st.caption(f"只顯示 {person} 的資料")
    c1, c2 = st.columns(2)
    today = date.today()
    q_start = date(today.year, 3 * ((today.month - 1) // 3) + 1, 1)
    start = c1.date_input("入帳日起", q_start)
    end = c2.date_input("入帳日迄", today)

    t = st.tabs(["本期分潤（系統計算）", "歷史季獎金（Excel 匯入）"] + (["⚠️ 未設定分潤"] if is_admin() else []))
    with t[0]:
        rows = sv.bonus_allocations(db, person, start, end)
        df = frame(rows, {"received_date": "入帳日", "contract_no": "合約", "company": "公司", "stage": "期別",
                          "invoice_amount": "款項", "role": "角色", "person": "對象", "pct": "%", "amount": "分潤"})
        if not df.empty:
            summ = df.groupby(["對象", "角色"], as_index=False)["分潤"].sum()
            st.dataframe(summ.pivot_table(index="對象", columns="角色", values="分潤", aggfunc="sum", margins=True,
                                          margins_name="合計").fillna(0),
                         width="stretch", column_config={c: MONEY_COL for c in summ["角色"].unique()} |
                         {"合計": MONEY_COL})
        st.dataframe(df, hide_index=True, width="stretch",
                     column_config={"款項": MONEY_COL, "分潤": MONEY_COL})
    with t[1]:
        sql = "select person, period, category, amount, note from bonus_payouts"
        rows = db.all(sql + (" where person = %s" if person else "") + " order by period desc, person",
                      (person,) if person else None)
        df = frame(rows, {"person": "人員", "period": "期間", "category": "類別", "amount": "金額", "note": "備註"})
        if not df.empty:
            st.dataframe(df.pivot_table(index="期間", columns="人員", values="金額", aggfunc="sum").fillna(0)
                         .sort_index(ascending=False), width="stretch")
        with st.expander("明細"):
            st.dataframe(df, hide_index=True, width="stretch", column_config={"金額": MONEY_COL})
    if is_admin():
        with t[2]:
            miss = sv.contracts_missing_splits(db)
            st.caption("這些合約已有入帳款項，但還沒設定分潤，所以不會出現在分潤計算中。")
            ev = st.dataframe(frame(miss, {"contract_no": "合約", "company": "公司", "sales": "業務",
                                           "writer": "主寫"}),
                              hide_index=True, width="stretch", on_select="rerun",
                              selection_mode="single-row")
            if ev.selection.rows:
                from wisetech.ui import go_contract
                go_contract(miss[ev.selection.rows[0]]["id"])


# ───────────────────────── 帳號管理 ─────────────────────────

def users_page():
    db = get_db()
    st.title("👤 帳號與人員")
    t = st.tabs(["帳號", "人員名單"])
    with t[0]:
        users = db.all("select id, username, display_name, role, staff_name, active from app_users order by id")
        df = frame(users, {"id": "id", "username": "帳號", "display_name": "名稱", "role": "角色",
                           "staff_name": "對應人員", "active": "啟用"})
        edited = st.data_editor(df, hide_index=True, width="stretch", disabled=["帳號"],
                                column_config={"id": None,
                                               "角色": st.column_config.SelectboxColumn(options=list(ROLE_LABEL)),
                                               "對應人員": st.column_config.SelectboxColumn(
                                                   options=sv.staff(db, active_only=False))})
        st.caption("角色：admin 管理員（全部）／staff 行政（不看獎金）／sales 業務（唯讀，只看自己的獎金）")
        if st.button("💾 儲存帳號"):
            for _, r in edited.iterrows():
                if int(r["id"]) == current_user()["id"] and (r["角色"] != "admin" or not r["啟用"]):
                    st.error("不能取消自己的管理員權限")
                    st.stop()
                db.update("app_users", int(r["id"]), {"display_name": r["名稱"], "role": r["角色"],
                                                      "staff_name": r["對應人員"] or None,
                                                      "active": bool(r["啟用"])})
            st.rerun()
        with st.form("new_user", clear_on_submit=True):
            st.markdown("**新增帳號**")
            c1, c2, c3, c4 = st.columns(4)
            username = c1.text_input("帳號")
            display = c2.text_input("名稱")
            role = c3.selectbox("角色", list(ROLE_LABEL), format_func=ROLE_LABEL.get, index=1)
            staff_name = c4.selectbox("對應人員", [""] + sv.staff(db))
            pw = st.text_input("初始密碼（至少 8 碼）", type="password")
            if st.form_submit_button("新增"):
                if not username or len(pw) < 8:
                    st.error("帳號必填、密碼至少 8 碼")
                elif db.val("select 1 from app_users where username = %s", (username,)):
                    st.error("帳號已存在")
                else:
                    db.insert("app_users", {"username": username, "display_name": display or username,
                                            "role": role, "staff_name": staff_name or None,
                                            "password_hash": hash_pw(pw)})
                    st.rerun()
        with st.form("reset_pw", clear_on_submit=True):
            st.markdown("**重設密碼**")
            c1, c2 = st.columns(2)
            who = c1.selectbox("帳號", [u["username"] for u in users])
            pw = c2.text_input("新密碼", type="password")
            if st.form_submit_button("重設") and len(pw) >= 8:
                db.run("update app_users set password_hash = %s where username = %s", (hash_pw(pw), who))
                st.success("已重設")
    with t[1]:
        rows = db.all("select name, active from staff order by name")
        df = pd.DataFrame(rows, columns=["name", "active"]).rename(columns={"name": "姓名", "active": "在職"})
        edited = st.data_editor(df, hide_index=True, num_rows="dynamic", width="stretch")
        if st.button("💾 儲存人員"):
            for _, r in edited.iterrows():
                if r["姓名"]:
                    db.run("insert into staff (name, active) values (%s,%s) on conflict (name) do update "
                           "set active = excluded.active", (r["姓名"].strip(), bool(r["在職"]) if pd.notna(r["在職"]) else True))
            st.rerun()
