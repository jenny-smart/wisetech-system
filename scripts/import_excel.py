"""把舊 Excel 匯入資料庫（一次性；系統上線後 Excel 不再使用）。

用法：
    # 先試跑：只產生對照報表，不寫資料庫
    python scripts/import_excel.py 合約及帳務管理.xlsx 績效及獎金報表.xlsx --dry-run

    # 正式匯入（第一次）
    DATABASE_URL=postgresql://... python scripts/import_excel.py 合約.xlsx 績效.xlsx

    # 清空業務資料後重新匯入（帳號與人員名單保留）
    python scripts/import_excel.py 合約.xlsx 績效.xlsx --reset

每次都會輸出 output/import_report.xlsx：公司歸戶、名稱合併、無法解析的值、略過的工作表，
匯入前請先打開確認。
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from wisetech.db import DB, connect  # noqa: E402
from wisetech.excel_import import (  # noqa: E402
    ParseResult, group_companies, parse_workbook, staff_names, summary,
)
from wisetech.parse import name_key, parse_pct, valid_tax_id  # noqa: E402

BUSINESS_TABLES = ["audit_log", "bonus_payouts", "bonus_splits", "shipments", "payments", "applications", "contracts",
                   "contacts", "taxid_candidates", "company_aliases", "companies"]


def dedupe_contracts(res: ParseResult) -> list[dict]:
    """兩份檔都有的合約（同編號＋同公司）合併成一筆，第二份只補空欄位與獎金。"""
    merged = []
    seen: dict[tuple, object] = {}
    for c in res.contracts:
        key = (c.contract_no, name_key(c.company_name)) if c.contract_no else None
        if key and key in seen:
            base = seen[key]
            for k, v in c.fields.items():
                if base.fields.get(k) in (None, "") and v not in (None, ""):
                    base.fields[k] = v
            have = {p.stage for p in base.payments}
            base.payments += [p for p in c.payments if p.stage not in have]
            base.bonus += c.bonus
            for k, v in c.raw.items():
                base.raw[f"{c.sheet}:{k}"] = v
            merged.append({"合約編號": c.contract_no, "公司": c.company_name,
                           "主要來源": f"{base.sheet} 第{base.row}列", "併入": f"{c.sheet} 第{c.row}列"})
            continue
        if key:
            seen[key] = c
    res.contracts = [c for c in res.contracts
                     if not (c.contract_no and seen.get((c.contract_no, name_key(c.company_name))) is not c)]
    return merged


def write_report(path: Path, res: ParseResult, groups, merges, dup_merges):
    path.parent.mkdir(parents=True, exist_ok=True)
    counts = defaultdict(int)
    ship_counts = defaultdict(int)
    _, to_key, _ = group_companies(res) if not groups else (None, None, None)
    key_of = {}
    for k, g in groups.items():
        for a in g.aliases:
            key_of[a] = k
    for c in res.contracts:
        counts[key_of.get(c.company_raw) or key_of.get(c.company_name)] += 1
    for s in res.shipments:
        ship_counts[key_of.get(s.company_raw) or key_of.get(s.company_name)] += 1
    comp_rows = [{"公司名稱": g.display, "統編(檔內找到)": g.tax_id, "合約數": counts[k], "寄件筆數": ship_counts[k],
                  "Excel上的寫法": "｜".join(g.aliases)} for k, g in sorted(groups.items(), key=lambda kv: kv[1].display)]
    contract_rows = [{"工作表": c.sheet, "列": c.row, "合約編號": c.contract_no, "公司": c.company_name,
                      "名稱註記": c.name_note, "解約": c.is_terminated, "保密": c.is_nda,
                      **{k: v for k, v in c.fields.items()}, "款項期數": len(c.payments),
                      "未對應欄位": "; ".join(f"{k}={v}" for k, v in c.raw.items())} for c in res.contracts]
    with pd.ExcelWriter(path) as xw:
        pd.DataFrame([summary(res) | {"skipped": str(res.skipped_sheets)}]).T.to_excel(xw, sheet_name="摘要", header=False)
        pd.DataFrame(comp_rows).to_excel(xw, sheet_name="公司歸戶", index=False)
        pd.DataFrame(merges or [{"說明": "無"}]).to_excel(xw, sheet_name="名稱合併", index=False)
        pd.DataFrame(dup_merges or [{"說明": "無"}]).to_excel(xw, sheet_name="重複合約合併", index=False)
        pd.DataFrame(contract_rows).to_excel(xw, sheet_name="合約", index=False)
        pd.DataFrame(res.issues or [{"說明": "無"}]).to_excel(xw, sheet_name="無法解析", index=False)
        pd.DataFrame(res.skipped_sheets or [("無", "")], columns=["工作表", "原因"]).to_excel(
            xw, sheet_name="略過的工作表", index=False)


def load(db: DB, res: ParseResult, groups, reset: bool):
    with db.tx():
        if reset:
            db.run("truncate " + ", ".join(BUSINESS_TABLES) + " restart identity cascade")
        elif db.val("select count(*) from contracts where source = 'excel'"):
            raise SystemExit("資料庫已經有 Excel 匯入的資料。要重新匯入請加 --reset（會清空所有業務資料）。")

        for n in staff_names(res):
            db.run("insert into staff (name) values (%s) on conflict do nothing", (n,))

        company_id: dict[str, int] = {}
        used_tid = {r["tax_id"] for r in db.all("select tax_id from companies where tax_id is not null")}
        for k, g in groups.items():
            tid = g.tax_id if g.tax_id and valid_tax_id(g.tax_id) and g.tax_id not in used_tid else None
            if tid:
                used_tid.add(tid)
            cid = db.insert("companies", {"name": g.display, "tax_id": tid,
                                          "tax_id_status": "confirmed" if tid else "pending",
                                          "note": "統編取自 Excel 文字" if tid else None})
            company_id[k] = cid
            for alias, mtype in g.aliases.items():
                db.run("insert into company_aliases (alias, company_id, match_type) values (%s,%s,%s) "
                       "on conflict (alias) do nothing", (alias, cid, mtype))

        key_of = {a: k for k, g in groups.items() for a in g.aliases}

        def cid_for(raw, clean):
            k = key_of.get(raw) or key_of.get(clean) or (name_key(clean) if clean else None)
            return company_id.get(k)

        contract_ids: dict[str, list[tuple[int, int | None]]] = defaultdict(list)
        for c in res.contracts:
            cid = cid_for(c.company_raw, c.company_name)
            data = {"contract_no": c.contract_no, "company_id": cid, "company_raw": c.company_raw,
                    "name_note": c.name_note, "is_terminated": c.is_terminated, "is_nda": c.is_nda,
                    "raw": c.raw, "source": "excel", "source_sheet": c.sheet, "source_row": c.row, **c.fields}
            kid = db.insert("contracts", data)
            if c.contract_no:
                contract_ids[c.contract_no].append((kid, cid))
            st = (c.fields.get("status") or "").strip()
            if c.fields.get("program") or st:
                result = {"PASS": "Pass", "FAIL": "Fail"}.get(st.upper(), "撤案" if "解約" in st else "審查中")
                db.insert("applications", {"contract_id": kid, "attempt_no": 1, "program": c.fields.get("program"),
                                           "submitted_date": c.fields.get("start_date"), "result": result,
                                           "approved_amount": c.fields.get("approved_amount"),
                                           "writer": c.fields.get("writer"),
                                           "note": None if st in ("Pass", "Fail", "In Process", "") else f"原狀態：{st}"})
            for p in c.payments:
                db.insert("payments", {"contract_id": kid, "stage": p.stage, "grant_amount": p.grant_amount,
                                       "invoice_amount": p.invoice_amount, "invoice_date": p.invoice_date,
                                       "invoice_no": p.invoice_no, "received_date": p.received_date,
                                       "is_received": p.is_received, "note": p.note, "raw": p.raw})
            for b in c.bonus:
                pct = parse_pct(b.get("pct_text"))
                if pct is None:
                    continue
                db.insert("bonus_splits", {"contract_id": kid, "scope": "全部", "person": b["person"],
                                           "role": "業務" if b["role"] == "Sales" else b["role"],
                                           "pct": pct, "note": f"Excel：{b.get('pct_text')}", "source": "excel"})

        contacts: dict[tuple, dict] = {}
        for s in res.shipments:
            cid = cid_for(s.company_raw, s.company_name)
            f = dict(s.fields)
            kid = None
            ref = f.pop("contract_ref", None)
            if ref and ref in contract_ids:
                cands = contract_ids[ref]
                same = [k for k, c in cands if c == cid]
                kid = (same or [cands[0][0]])[0]
            email = f.pop("email", None)
            f.pop("tax_id", None)
            db.insert("shipments", {"company_id": cid, "contract_id": kid, "company_raw": s.company_raw,
                                    "contract_ref": ref, "raw": s.raw | ({"email": email} if email else {}),
                                    "source": "excel", "source_row": s.row, **f})
            if cid and (f.get("contact") or f.get("phone") or email):
                key = (cid, f.get("contact") or f.get("phone") or email)
                prev = contacts.get(key)
                if not prev or (f.get("sent_date") and (prev["last_seen"] or f["sent_date"]) <= f["sent_date"]):
                    contacts[key] = {"company_id": cid, "name": f.get("contact"), "phone": f.get("phone"),
                                     "email": email, "address": f.get("address"), "last_seen": f.get("sent_date"),
                                     "source": "excel"}
        for c in contacts.values():
            db.insert("contacts", c)

        for p in res.payouts:
            db.insert("bonus_payouts", {"person": p.person, "period": p.period, "category": p.category,
                                        "amount": p.amount, "note": p.note, "source": "excel",
                                        "source_sheet": p.sheet})
        db.log("import", "excel_import", detail=summary(res))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", help="Excel 檔（合約帳務檔放第一個）")
    ap.add_argument("--dry-run", action="store_true", help="只產生報表，不寫資料庫")
    ap.add_argument("--reset", action="store_true", help="清空業務資料後重新匯入")
    ap.add_argument("--report", default="output/import_report.xlsx")
    args = ap.parse_args()

    res = ParseResult()
    for f in args.files:
        parse_workbook(f, res)
    dup = dedupe_contracts(res)
    groups, _, merges = group_companies(res)
    write_report(Path(args.report), res, groups, merges, dup)

    s = summary(res)
    print(f"合約 {s['contracts']} 筆（{s['by_sheet']}）、寄件 {s['shipments']} 筆、季獎金 {s['payouts']} 筆、"
          f"公司 {len(groups)} 家、名稱合併 {len(merges)} 筆、重複合約合併 {len(dup)} 筆、無法解析 {s['issues']} 個值")
    if s["skipped"]:
        print("略過的工作表：", s["skipped"])
    print(f"報表：{args.report}")
    if args.dry_run:
        return
    if not os.environ.get("DATABASE_URL"):
        raise SystemExit("請設定 DATABASE_URL 環境變數")
    if args.reset and input("--reset 會清空所有合約／款項／公司資料，確定嗎？輸入 yes：").strip() != "yes":
        raise SystemExit("已取消")
    load(DB(connect()), res, groups, args.reset)
    print("匯入完成。下一步：python scripts/lookup_taxid.py 自動比對統編")


if __name__ == "__main__":
    main()
