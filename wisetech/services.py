"""資料存取：頁面只呼叫這裡的函式，不直接寫 SQL。"""
from __future__ import annotations

from datetime import date

from .db import DB
from .parse import name_key, roc_prefix, valid_tax_id

CONTRACT_TYPES = ["個案", "年約", "特案", "募資", "其他"]
DEPOSIT_DEDUCT = ["扣 (50%)", "扣 (100%)", "扣", "不扣", "其他附註"]
TAX_MODES = ["未稅", "含稅", "其他附註"]
APP_RESULTS = ["撰寫中", "審查中", "Pass", "Fail", "撤案"]
STAGES = ["前金1", "前金2", "期中", "期末", "其他"]


# ───────────────────────── 公司 ─────────────────────────

def search_companies(db: DB, q: str, limit: int = 50) -> list[dict]:
    q = (q or "").strip()
    if not q:
        return db.all("select * from company_summary order by last_contract_date desc nulls last, name limit %s",
                      (limit,))
    like = f"%{q}%"
    key = f"%{name_key(q)}%"
    return db.all("""
        select distinct s.* from company_summary s
        left join company_aliases a on a.company_id = s.id
        left join contracts k on k.company_id = s.id
        where s.name ilike %s or s.tax_id like %s or a.alias ilike %s
           or replace(replace(s.name,' ',''),'臺','台') ilike %s or k.contract_no ilike %s
        order by s.last_contract_date desc nulls last, s.name limit %s
    """, (like, f"{q}%", like, key, like, limit))


def company(db: DB, cid: int) -> dict | None:
    return db.one("select c.*, s.contract_count, s.pass_count, s.invoiced_total, s.received_total, "
                  "s.outstanding_total from companies c join company_summary s on s.id = c.id where c.id = %s",
                  (cid,))


def company_by_tax_id(db: DB, tax_id: str) -> dict | None:
    return db.one("select * from companies where tax_id = %s", (tax_id,))


def create_company(db: DB, name: str, tax_id: str | None, address: str | None = None) -> int:
    tax_id = (tax_id or "").strip() or None
    if tax_id and not valid_tax_id(tax_id):
        raise ValueError("統一編號檢查碼不正確")
    if tax_id and company_by_tax_id(db, tax_id):
        raise ValueError("這個統編已經存在")
    cid = db.insert("companies", {"name": name.strip(), "tax_id": tax_id, "address": address,
                                  "tax_id_status": "confirmed" if tax_id else "pending"})
    db.run("insert into company_aliases (alias, company_id, match_type) values (%s,%s,'manual') "
           "on conflict do nothing", (name.strip(), cid))
    return cid


def set_tax_id(db: DB, cid: int, tax_id: str | None, status: str, gcis_name: str | None = None,
               gcis_status: str | None = None):
    db.update("companies", cid, {"tax_id": tax_id, "tax_id_status": status, "gcis_name": gcis_name,
                                 "gcis_status": gcis_status})


def merge_companies(db: DB, keep_id: int, drop_id: int):
    """把 drop 的合約、寄件、聯絡人、別名全部移到 keep，然後刪掉 drop。"""
    with db.tx():
        drop = db.one("select * from companies where id = %s", (drop_id,))
        for t in ("contracts", "shipments", "contacts"):
            db.run(f"update {t} set company_id = %s where company_id = %s", (keep_id, drop_id))
        db.run("update company_aliases set company_id = %s where company_id = %s", (keep_id, drop_id))
        db.run("insert into company_aliases (alias, company_id, match_type) values (%s,%s,'manual') "
               "on conflict do nothing", (drop["name"], keep_id))
        db.run("delete from companies where id = %s", (drop_id,))


def contacts(db: DB, cid: int) -> list[dict]:
    return db.all("select * from contacts where company_id = %s order by last_seen desc nulls last, id desc", (cid,))


def aliases(db: DB, cid: int) -> list[dict]:
    return db.all("select alias, match_type from company_aliases where company_id = %s order by alias", (cid,))


# ───────────────────────── 合約 ─────────────────────────

def contracts_of(db: DB, cid: int) -> list[dict]:
    return db.all("""
        select k.*, (select count(*) from applications a where a.contract_id = k.id) as attempts
        from contracts k where company_id = %s
        order by k.start_date desc nulls last, k.contract_no desc""", (cid,))


def list_contracts(db: DB, *, year: int | None = None, status: str | None = None, ctype: str | None = None,
                   person: str | None = None, q: str | None = None, limit: int = 500) -> list[dict]:
    where, params = ["true"], []
    if year:
        where.append("(k.contract_no like %s or extract(year from k.start_date) = %s)")
        params += [f"{year - 1911}%", year]
    if status:
        where.append("k.status = %s")
        params.append(status)
    if ctype:
        where.append("k.contract_type = %s")
        params.append(ctype)
    if person:
        where.append("(k.sales = %s or k.partner_sales = %s or k.writer = %s)")
        params += [person] * 3
    if q:
        where.append("(c.name ilike %s or k.contract_no ilike %s or k.program ilike %s or c.tax_id like %s)")
        params += [f"%{q}%"] * 4
    return db.all(f"""
        select k.id, k.contract_no, c.name as company, c.tax_id, k.contract_type, k.program, k.status,
               k.start_date, k.end_date, k.deposit_total, k.approved_amount, k.mid_rate, k.final_rate,
               coalesce(k.partner_sales, k.sales) as sales, k.writer, k.is_terminated, k.company_id
        from contracts k left join companies c on c.id = k.company_id
        where {' and '.join(where)}
        order by k.contract_no desc nulls last, k.id desc limit {int(limit)}""", params)


def contract(db: DB, kid: int) -> dict | None:
    return db.one("select k.*, c.name as company, c.tax_id from contracts k "
                  "left join companies c on c.id = k.company_id where k.id = %s", (kid,))


def next_contract_no(db: DB, d: date | None = None) -> str:
    prefix = roc_prefix(d or date.today())
    last = db.val("select max(split_part(contract_no,'-',2)) from contracts where contract_no like %s "
                  "and split_part(contract_no,'-',2) ~ '^[0-9]+$'", (f"{prefix}-%",))
    return f"{prefix}-{int(last or 0) + 1:02d}"


def save_contract(db: DB, data: dict, kid: int | None = None) -> int:
    if kid:
        db.update("contracts", kid, data)
        return kid
    return db.insert("contracts", data)


def sync_contract_status(db: DB, kid: int):
    """合約的送案別／狀態／過案金額跟著最後一次送案走。"""
    last = db.one("select * from applications where contract_id = %s order by attempt_no desc limit 1", (kid,))
    if not last:
        return
    status = {"Pass": "Pass", "Fail": "Fail", "撤案": "Closed"}.get(last["result"], "In Process")
    db.update("contracts", kid, {"status": status, "program": last["program"],
                                 "approved_amount": last["approved_amount"]})


def applications(db: DB, kid: int) -> list[dict]:
    return db.all("select * from applications where contract_id = %s order by attempt_no", (kid,))


def payments(db: DB, kid: int) -> list[dict]:
    return db.all("select * from payments where contract_id = %s order by "
                  "array_position(array['前金1','前金2','期中','期末','其他'], stage), id", (kid,))


def payments_of_company(db: DB, cid: int) -> list[dict]:
    return db.all("""select k.contract_no, p.* from payments p join contracts k on k.id = p.contract_id
                     where k.company_id = %s order by coalesce(p.invoice_date, k.start_date) desc nulls last""",
                  (cid,))


def outstanding(db: DB) -> list[dict]:
    """已開發票未入帳。"""
    return db.all("""select p.id, k.id as contract_id, k.contract_no, c.name as company, c.tax_id, p.stage,
                            p.invoice_amount, p.invoice_date, p.invoice_no, current_date - p.invoice_date as days
                     from payments p join contracts k on k.id = p.contract_id
                     left join companies c on c.id = k.company_id
                     where p.invoice_date is not null and not p.is_received
                     order by p.invoice_date""")


def passed_without_followup(db: DB) -> list[dict]:
    """過案了但還沒有期中／期末款項紀錄 → 提醒去收過案 %。"""
    return db.all("""select k.id, k.contract_no, c.name as company, k.program, k.approved_amount,
                            k.mid_rate, k.final_rate
                     from contracts k left join companies c on c.id = k.company_id
                     where k.status ilike 'pass%%' and not k.is_terminated
                       and not exists (select 1 from payments p where p.contract_id = k.id
                                       and p.stage in ('期中','期末') and p.invoice_date is not null)
                     order by k.start_date desc nulls last""")


def failed_open(db: DB) -> list[dict]:
    """最後一次送案 Fail、尚未重寫 → 提醒安排重寫。"""
    return db.all("""select k.id, k.contract_no, c.name as company, a.program, a.attempt_no, a.result_date
                     from contracts k join companies c on c.id = k.company_id
                     join lateral (select * from applications a where a.contract_id = k.id
                                   order by attempt_no desc limit 1) a on true
                     where a.result = 'Fail' and not k.is_terminated
                     order by a.result_date desc nulls last""")


# ───────────────────────── 分潤 ─────────────────────────

BONUS_ROLES = ["業務", "主寫", "管考", "行政", "委外", "外部佣金", "獎金池"]
BONUS_SCOPES = ["頭款", "過案款", "全部"]
BONUS_TEMPLATES = {
    "業務 30／主寫 60／管考 10": [("業務", 30), ("主寫", 60), ("管考", 10)],
    "業務 60／主寫 40": [("業務", 60), ("主寫", 40)],
    "業務 100": [("業務", 100)],
}


def bonus_splits(db: DB, kid: int) -> list[dict]:
    return db.all("select * from bonus_splits where contract_id = %s order by "
                  "array_position(array['頭款','過案款','全部'], scope), id", (kid,))


def replace_bonus_splits(db: DB, kid: int, rows: list[dict]):
    with db.tx():
        db.run("delete from bonus_splits where contract_id = %s", (kid,))
        for r in rows:
            db.insert("bonus_splits", {"contract_id": kid, **r})


def bonus_allocations(db: DB, person: str | None = None, start=None, end=None,
                      contract_id: int | None = None) -> list[dict]:
    where, params = ["true"], []
    if contract_id:
        where.append("contract_id = %s")
        params.append(contract_id)
    if person:
        where.append("person = %s")
        params.append(person)
    if start:
        where.append("received_date >= %s")
        params.append(start)
    if end:
        where.append("received_date <= %s")
        params.append(end)
    return db.all(f"select * from bonus_allocations where {' and '.join(where)} "
                  "order by received_date desc nulls last, contract_no", params)


def contracts_missing_splits(db: DB) -> list[dict]:
    """有已入帳款項、但還沒設定分潤的合約。"""
    return db.all("""select distinct k.id, k.contract_no, c.name as company, coalesce(k.partner_sales,k.sales) as sales,
                            k.writer
                     from contracts k left join companies c on c.id = k.company_id
                     join payments p on p.contract_id = k.id and p.is_received
                     where not exists (select 1 from bonus_splits b where b.contract_id = k.id)
                     order by k.contract_no desc nulls last""")


def invoices_of(db: DB, cid: int) -> list[dict]:
    return db.all("""select i.*, k.contract_no from invoices i
                     left join payments p on p.id = i.payment_id left join contracts k on k.id = p.contract_id
                     where i.company_id = %s order by i.invoice_date desc nulls last""", (cid,))


def shipments_of(db: DB, cid: int) -> list[dict]:
    return db.all("select * from shipments where company_id = %s order by sent_date desc nulls last, id desc", (cid,))


def staff(db: DB, active_only: bool = True) -> list[str]:
    return [r["name"] for r in db.all("select name from staff" + (" where active" if active_only else "")
                                      + " order by name")]
