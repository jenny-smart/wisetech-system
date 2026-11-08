"""登入與權限。

角色：
- admin：全部功能＋帳號管理＋所有人的獎金
- staff：公司、合約、款項、寄件、統編確認（看不到獎金）
- sales：查詢公司與合約（唯讀），獎金只看得到自己的
"""
from __future__ import annotations

import bcrypt
import streamlit as st

from .db import DB

ROLE_LABEL = {"admin": "管理員", "staff": "行政", "sales": "業務"}


def hash_pw(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def check_pw(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except ValueError:
        return False


def current_user() -> dict | None:
    return st.session_state.get("user")


def can_edit() -> bool:
    u = current_user()
    return bool(u and u["role"] in ("admin", "staff"))


def is_admin() -> bool:
    u = current_user()
    return bool(u and u["role"] == "admin")


def login_screen(db: DB):
    st.markdown("## 🔐 Wisetech 合約帳務系統")
    if not db.val("select exists(select 1 from app_users)"):
        st.info("第一次使用：請建立管理員帳號。")
        with st.form("bootstrap"):
            username = st.text_input("帳號")
            display = st.text_input("顯示名稱")
            pw = st.text_input("密碼", type="password")
            pw2 = st.text_input("再輸入一次密碼", type="password")
            if st.form_submit_button("建立管理員", type="primary"):
                if not username or len(pw) < 8:
                    st.error("帳號必填，密碼至少 8 碼")
                elif pw != pw2:
                    st.error("兩次密碼不一致")
                else:
                    db.insert("app_users", {"username": username.strip(), "display_name": display or username,
                                            "password_hash": hash_pw(pw), "role": "admin"})
                    st.success("已建立，請登入")
                    st.rerun()
        return
    with st.form("login"):
        username = st.text_input("帳號")
        pw = st.text_input("密碼", type="password")
        if st.form_submit_button("登入", type="primary", width="stretch"):
            u = db.one("select * from app_users where username = %s and active", (username.strip(),))
            if u and check_pw(pw, u["password_hash"]):
                st.session_state.user = {k: u[k] for k in ("id", "username", "display_name", "role", "staff_name")}
                db.log(u["username"], "login")
                st.rerun()
            else:
                st.error("帳號或密碼錯誤")


def logout():
    st.session_state.pop("user", None)
    st.rerun()
