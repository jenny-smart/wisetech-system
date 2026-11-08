"""讀取舊 Excel（年度合約表、合約追蹤、績效獎金表），轉成中介資料結構。

不碰資料庫；寫入由 scripts/import_excel.py 負責。
每一個無法對應的欄位都會原樣留在 raw，不會遺失。
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

from .parse import (
    clean_company_name, clean_text, extract_tax_id, find_contract_refs, find_email, find_phone,
    classify_contact_cell, fmt_pct, header_key, name_key, normalize_contract_no, parse_amount,
    parse_check, parse_date, short_key,
)

# ───────────────────────── 中介資料結構 ─────────────────────────


@dataclass
class Payment:
    stage: str
    grant_amount: Decimal | None = None
    invoice_amount: Decimal | None = None
    invoice_date: date | None = None
    invoice_no: str | None = None
    received_date: date | None = None
    is_received: bool = False
    note: str | None = None
    raw: dict = field(default_factory=dict)

    def empty(self) -> bool:
        return not any([self.grant_amount, self.invoice_amount, self.invoice_date, self.invoice_no,
                        self.received_date, self.is_received, self.note, self.raw])


@dataclass
class ContractRec:
    sheet: str
    row: int
    contract_no: str | None
    company_raw: str | None
    company_name: str | None
    name_note: str | None
    is_terminated: bool
    is_nda: bool
    fields: dict = field(default_factory=dict)
    payments: list[Payment] = field(default_factory=list)
    bonus: list[dict] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


@dataclass
class ShipmentRec:
    row: int
    company_raw: str | None
    company_name: str | None
    fields: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


@dataclass
class PayoutRec:
    sheet: str
    person: str
    period: str
    category: str
    amount: Decimal | None
    note: str | None = None


@dataclass
class ParseResult:
    contracts: list[ContractRec] = field(default_factory=list)
    shipments: list[ShipmentRec] = field(default_factory=list)
    payouts: list[PayoutRec] = field(default_factory=list)
    skipped_sheets: list[tuple[str, str]] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)

    def issue(self, sheet, row, column, value, problem):
        self.issues.append({"sheet": sheet, "row": row, "column": column, "value": value, "problem": problem})


# ───────────────────────── 讀工作表 ─────────────────────────


def sheet_values(ws: Worksheet) -> list[list]:
    """回傳二維值陣列；百分比格式轉成 '15%' 字串。"""
    out = []
    for row in ws.iter_rows():
        vals = []
        for c in row:
            v = c.value
            if isinstance(v, (int, float)) and not isinstance(v, bool) and c.number_format and "%" in c.number_format:
                v = fmt_pct(v, c.number_format)
            vals.append(v)
        out.append(vals)
    return out


def _merged_fill(ws: Worksheet, rows: list[list], r: int) -> list:
    """第 r 列（0-based）的表頭，合併儲存格的值填滿整個範圍。"""
    vals = list(rows[r])
    for rng in ws.merged_cells.ranges:
        if rng.min_row <= r + 1 <= rng.max_row:
            top = ws.cell(rng.min_row, rng.min_col).value
            for c in range(rng.min_col, rng.max_col + 1):
                if c - 1 < len(vals) and (vals[c - 1] is None or rng.min_row < r + 1):
                    vals[c - 1] = top
    return vals


GROUPS_WITH_SUBS = ("前金", "期中", "期末", "外部佣金", "績效結算", "獎金分配")


def build_headers(ws: Worksheet, rows: list[list], h: int) -> tuple[list[tuple[str, str]], int]:
    """回傳每欄 (group, sub) 以及資料起始列。"""
    group = _merged_fill(ws, rows, h)
    sub = rows[h + 1] if h + 1 < len(rows) else []
    sub_keys = [header_key(v) for v in sub]
    has_sub = any(k in ("合約條件", "第一期金額", "撥款金額", "SALES", "外部佣金%", "發放日") for k in sub_keys)
    width = max(len(group), len(sub))
    heads = []
    last_group = ""
    for i in range(width):
        g = header_key(group[i] if i < len(group) else None)
        s = sub_keys[i] if (has_sub and i < len(sub_keys)) else ""
        if g:
            last_group = g
        elif s and any(last_group.startswith(x) for x in GROUPS_WITH_SUBS):
            g = last_group
        heads.append((g, s))
    return heads, h + (2 if has_sub else 1)


def find_header_row(rows: list[list], must: tuple[str, ...], limit: int = 15) -> int | None:
    for i, row in enumerate(rows[:limit]):
        keys = {header_key(v) for v in row}
        if all(any(m in k for k in keys) for m in must):
            return i
    return None


# ───────────────────────── 年度合約表 ─────────────────────────

SIMPLE_FIELDS = [
    ("業務", "partner_sales"), ("起始日", "start_date"), ("終止日", "end_date"),
    ("合約類型", "contract_type"), ("前金扣", "deposit_deduct"), ("含稅", "tax_mode"),
    ("送案別", "program"), ("狀態", "status"), ("過案金額", "approved_amount"),
    ("特別約定", "special_terms"),
]
PERSON_FIELDS = {"SALES": "sales", "主寫": "writer", "委外": "outsource",
                 "期中管考": "mid_review", "期末管考": "final_review", "管考": "mid_review"}
SKIP_COMPANY = {"PASS", "已結案", "公司名稱"}
_RECEIVED_WORDS = re.compile(r"入帳|已收|已付|收|付款|轉帳|已入")
_MD = re.compile(r"^(\d{1,2})/(\d{1,2})")


def _received(text: str | None, invoice_date: date | None) -> tuple[date | None, bool, str | None]:
    """入帳日欄：完整日期、'1/8 入帳'（補年份）、'已收'、'作廢'…"""
    if text is None:
        return None, False, None
    d, raw = parse_date(text)
    if d:
        return d, True, None
    m = _MD.match(raw or "")
    if m and invoice_date:
        mo, dd = int(m.group(1)), int(m.group(2))
        y = invoice_date.year + (1 if mo < invoice_date.month - 6 else 0)
        try:
            return date(y, mo, dd), True, raw
        except ValueError:
            pass
    if raw and re.search(r"作廢|折讓|未|退", raw):
        return None, False, raw
    return None, bool(raw and _RECEIVED_WORDS.search(raw)), raw


def _set_date(target: dict, key: str, value, res: ParseResult, sheet: str, row: int, col: str):
    d, raw = parse_date(value)
    target[key] = d
    if raw:
        target.setdefault("_raw", {})[col] = raw
        res.issue(sheet, row, col, raw, "日期無法解析，已保留原文")


def parse_contract_sheet(ws: Worksheet, res: ParseResult) -> int:
    rows = sheet_values(ws)
    h = find_header_row(rows, ("起始日", "狀態"))
    if h is None:
        return -1
    heads, start = build_headers(ws, rows, h)
    count = 0
    for r in range(start, len(rows)):
        vals = rows[r] + [None] * (len(heads) - len(rows[r]))
        rownum = r + 1
        cno_raw = clean_text(vals[0])
        comp_raw = clean_text(vals[1]) if len(vals) > 1 else None
        if comp_raw is None and cno_raw is None:
            continue
        if comp_raw and comp_raw.upper() in SKIP_COMPANY:
            continue
        others = [clean_text(v) for v in vals[2:]]
        if comp_raw is None and not any(others):
            continue                                       # 預留空號
        cn = clean_company_name(comp_raw)
        rec = ContractRec(ws.title, rownum, normalize_contract_no(cno_raw), comp_raw, cn["name"],
                          cn["note"], cn["terminated"], cn["nda"])
        if cno_raw and rec.contract_no != cno_raw:
            rec.raw["合約編號原文"] = cno_raw
        f = rec.fields
        pay: dict[str, Payment] = {}
        period = "前金1"
        last_person_role = None

        def P(stage):
            return pay.setdefault(stage, Payment(stage))

        for i, (g, s) in enumerate(heads):
            if i < 2:
                continue
            v = vals[i]
            if clean_text(v) is None:
                continue
            col = f"{g}|{s}" if s else g
            text = clean_text(v)

            if g.startswith("前金") and "扣" not in g:
                if s == "合約條件" or not s:
                    amt, raw = parse_amount(v)
                    f["deposit_total"] = amt
                    if raw:
                        rec.raw["前金原文"] = raw
                    period = "前金1"
                elif s.startswith("第一期"):
                    period = "前金1"
                elif s.startswith("第二期"):
                    period = "前金2"
                if s.endswith("金額"):
                    amt, raw = parse_amount(v)
                    P(period).invoice_amount = amt
                    if raw:
                        P(period).raw["金額"] = raw
                elif s.endswith("發票日"):
                    d, raw = parse_date(v)
                    P(period).invoice_date = d
                    if raw:
                        P(period).raw["發票日"] = raw
                elif s == "發票號碼":
                    P(period).invoice_no = text
                elif s == "入帳日":
                    d, ok, raw = _received(text, P(period).invoice_date)
                    P(period).received_date, P(period).is_received = d, ok
                    if raw:
                        P(period).raw["入帳日"] = raw
                continue

            if g.startswith("期中") or g.startswith("期末"):
                stage = "期中" if g.startswith("期中") else "期末"
                if s == "合約條件" or not s:
                    f["mid_rate" if stage == "期中" else "final_rate"] = text
                elif s == "撥款金額":
                    amt, raw = parse_amount(v)
                    P(stage).grant_amount = amt
                    if raw:
                        P(stage).raw["撥款金額"] = raw
                elif s == "發票金額":
                    amt, raw = parse_amount(v)
                    P(stage).invoice_amount = amt
                    if raw:
                        P(stage).raw["發票金額"] = raw
                elif s == "發票日":
                    d, raw = parse_date(v)
                    P(stage).invoice_date = d
                    if raw:
                        P(stage).raw["發票日"] = raw
                elif s == "發票號碼":
                    P(stage).invoice_no = text
                elif s == "入帳日":
                    d, ok, raw = _received(text, P(stage).invoice_date)
                    P(stage).received_date, P(stage).is_received = d, ok
                    if raw:
                        P(stage).raw["入帳日"] = raw
                else:
                    P(stage).raw[s] = text
                continue

            if g.startswith("外部佣金"):
                if s in ("", "外部佣金%", "外部佣金", "%"):
                    f["ext_commission_rate"] = text
                elif s == "發放日":
                    _set_date(f, "ext_commission_paid", v, res, ws.title, rownum, col)
                elif s == "來源":
                    f["ext_source"] = text
                else:
                    rec.raw[col] = text
                continue

            if g.startswith("績效結算") or g.startswith("獎金分配"):
                role = PERSON_FIELDS.get(s)
                if role:
                    f[role] = text
                    last_person_role = s
                    continue
                if s == "%" and last_person_role:
                    who = f.get(PERSON_FIELDS[last_person_role])
                    if who:
                        rec.bonus.append({"person": who, "role": "主寫" if last_person_role == "主寫" else "Sales",
                                          "pct_text": text})
                    continue
                rec.raw[col] = text
                continue

            for key, name in SIMPLE_FIELDS:
                if g.startswith(key):
                    if name in ("start_date", "end_date"):
                        _set_date(f, name, v, res, ws.title, rownum, col)
                    elif name == "approved_amount":
                        amt, raw = parse_amount(v)
                        f[name] = amt
                        if raw:
                            rec.raw["過案金額原文"] = raw
                    else:
                        f[name] = text
                    break
            else:
                rec.raw[col] = text

        # 只有一期前金、沒填第一期金額時，用合約條件當發票金額
        p1 = pay.get("前金1")
        if p1 and p1.invoice_amount is None and "前金2" not in pay and (p1.invoice_date or p1.invoice_no):
            p1.invoice_amount = f.get("deposit_total")
        rec.payments = [p for p in pay.values() if not p.empty()]
        if "_raw" in f:
            rec.raw.update(f.pop("_raw"))
        res.contracts.append(rec)
        count += 1
    return count


# ───────────────────────── 合約追蹤 ─────────────────────────

TRACK_COLS = {
    "公司名稱": "company", "SALES": "sales", "合約取得方式": "method", "甲方用印": "party_a_stamp",
    "我方用印": "our_stamp", "合約寄出份數": "copies", "寄出/用印日期": "sent_date",
    "寄回/面交日期": "return_status", "發票開立": "invoice_status", "備註": "purpose", "窗口": "contact",
}


def parse_tracking_sheet(ws: Worksheet, res: ParseResult) -> int:
    rows = sheet_values(ws)
    h = find_header_row(rows, ("公司名稱", "寄出"))
    if h is None:
        return -1
    heads = [header_key(v) for v in rows[h]]
    colmap = {}
    for i, k in enumerate(heads):
        for name, field_ in TRACK_COLS.items():
            if k == header_key(name) or (field_ == "sent_date" and k.startswith("寄出")) \
                    or (field_ == "return_status" and k.startswith("寄回")):
                colmap.setdefault(field_, i)
    ci = colmap.get("company")
    count = 0
    for r in range(h + 1, len(rows)):
        vals = rows[r]
        rownum = r + 1
        comp = clean_text(vals[ci]) if ci is not None and ci < len(vals) else None
        texts = [clean_text(v) for v in vals]
        if not any(texts):
            continue
        cn = clean_company_name(comp)
        rec = ShipmentRec(rownum, comp, cn["name"])
        f = rec.fields
        f["flag"] = texts[0] if ci and ci > 0 else None
        for field_, i in colmap.items():
            if field_ == "company" or i >= len(vals):
                continue
            v, t = vals[i], texts[i]
            if t is None:
                continue
            if field_ in ("party_a_stamp", "our_stamp"):
                f[field_] = parse_check(v)
            elif field_ == "sent_date":
                d, raw = parse_date(v)
                f["sent_date"] = d
                if raw:
                    rec.raw["寄出日期原文"] = raw
                    res.issue(ws.title, rownum, "寄出/用印日期", raw, "日期無法解析，已保留原文")
            else:
                f[field_] = t
        # 窗口之後的欄位：分類成電話／地址／Email
        contact_cells = []
        start = colmap.get("contact", max(colmap.values()) + 1 if colmap else len(vals))
        contact_cells = [t for t in texts[start:] if t]
        if f.get("purpose") and classify_contact_cell(f["purpose"]) in ("address", "email"):
            contact_cells.insert(0, f.pop("purpose"))
        names, phones, addrs, emails = [], [], [], []
        for t in contact_cells:
            kind = classify_contact_cell(t)
            if kind == "email":
                emails.append(find_email(t))
                rest = t.replace(find_email(t), "").strip(" ,，/")
                if rest:
                    names.append(rest)
            elif kind == "address":
                addrs.append(t)
            elif kind == "phone":
                phones.append(t)
            else:
                ph = find_phone(t)
                if ph:
                    phones.append(ph)
                    t = t.replace(ph, "").strip(" /，,")
                if t:
                    names.append(t)
        f["contact"] = "；".join(dict.fromkeys(names)) or None
        f["phone"] = "；".join(dict.fromkeys(phones)) or None
        f["address"] = "；".join(dict.fromkeys(addrs)) or None
        f["email"] = "；".join(dict.fromkeys(emails)) or None
        refs = find_contract_refs(f.get("return_status")) or find_contract_refs(f.get("purpose"))
        f["contract_ref"] = refs[0] if refs else None
        tid = extract_tax_id(" ".join(t for t in texts if t))
        if tid:
            f["tax_id"] = tid
        res.shipments.append(rec)
        count += 1
    return count


# ───────────────────────── 績效報表（每季個人獎金） ─────────────────────────

_PERIOD = re.compile(r"(20\d{2})\s*([1-4](?:Q[1-4]?)?Q)")


def parse_payout_sheet(ws: Worksheet, res: ParseResult) -> int:
    """「績效報表」：第一欄是人名，表頭有「2020 3Q過案獎金」「獎金池分配」等欄位。"""
    rows = sheet_values(ws)
    h = None
    for i, row in enumerate(rows[:15]):
        keys = [header_key(v) for v in row]
        if any(_PERIOD.search(k) for k in keys) or any(k.endswith("獎金池分配") for k in keys):
            h = i
            break
    if h is None:
        return -1
    group = _merged_fill(ws, rows, h)
    heads = []
    last = None
    for v in group:
        k = clean_text(v)
        if k:
            m = _PERIOD.search(k.replace(" ", ""))
            if m:
                last = f"{m.group(1)} {m.group(2)}"
            heads.append((last, k))
        else:
            heads.append((last, None))
    count = 0
    for r in range(h + 1, len(rows)):
        vals = rows[r]
        person = clean_text(vals[0]) if vals else None
        if not person or person.upper() in ("NAME", "SALES", "TOTAL", "合計", "總計"):
            continue
        for i in range(1, min(len(vals), len(heads))):
            period, label = heads[i]
            if not period or not label:
                continue
            amt, raw = parse_amount(vals[i])
            if amt is None and raw is None:
                continue
            cat = "獎金池分配" if "獎金池" in label else ("過案獎金" if "獎金" in label else label)
            res.payouts.append(PayoutRec(ws.title, person, period, cat, amt, raw))
            count += 1
    return count


# ───────────────────────── 入口 ─────────────────────────


def parse_workbook(path: str, res: ParseResult | None = None) -> ParseResult:
    res = res or ParseResult()
    wb = load_workbook(path, data_only=True)
    for ws in wb.worksheets:
        if ws.sheet_state != "visible" and ws.max_row < 2:
            continue
        n = parse_contract_sheet(ws, res)
        if n >= 0:
            continue
        n = parse_tracking_sheet(ws, res)
        if n >= 0:
            continue
        n = parse_payout_sheet(ws, res)
        if n >= 0:
            continue
        res.skipped_sheets.append((f"{path.split('/')[-1]} / {ws.title}", "認不出表頭"))
    return res


# ───────────────────────── 公司歸戶 ─────────────────────────


@dataclass
class CompanyGroup:
    key: str
    names: Counter = field(default_factory=Counter)        # 清洗後名稱 → 次數
    aliases: dict = field(default_factory=dict)            # 原始寫法 → match_type
    tax_id: str | None = None

    @property
    def display(self) -> str:
        # 出現最多次；同次數取最長（較可能是全名）
        return max(self.names.items(), key=lambda kv: (kv[1], len(kv[0])))[0]


def group_companies(res: ParseResult) -> tuple[dict[str, CompanyGroup], dict[str, str], list[dict]]:
    """回傳 (groups, raw/clean 名稱 → group key, 合併紀錄)。"""
    groups: dict[str, CompanyGroup] = {}
    to_key: dict[str, str] = {}
    merges: list[dict] = []

    def add(raw: str | None, clean: str | None, tax_id: str | None = None):
        if not clean:
            return
        k = name_key(clean)
        g = groups.setdefault(k, CompanyGroup(k))
        g.names[clean] += 1
        if raw:
            g.aliases.setdefault(raw, "exact")
        g.aliases.setdefault(clean, "exact")
        if tax_id and not g.tax_id:
            g.tax_id = tax_id

    for c in res.contracts:
        add(c.company_raw, c.company_name, extract_tax_id(c.company_raw))
    for s in res.shipments:
        add(s.company_raw, s.company_name, s.fields.get("tax_id"))

    # 簡稱 → 全名（「睿元」→「睿元國際股份有限公司」），只有唯一候選時才合併
    full_keys = [k for k in groups if short_key(k) != k]
    for k in list(groups):
        if short_key(k) != k or len(k) < 2:
            continue
        cands = [fk for fk in full_keys if fk != k and short_key(fk).startswith(k)]
        if len(cands) == 1:
            target = groups[cands[0]]
            src = groups.pop(k)
            for a in src.aliases:
                target.aliases.setdefault(a, "prefix")
            target.tax_id = target.tax_id or src.tax_id
            merges.append({"簡稱": src.display, "合併到": target.display, "方式": "簡稱前綴唯一相符"})

    # 同統編的分組合併
    by_tid: dict[str, str] = {}
    for k in list(groups):
        g = groups.get(k)
        if not g or not g.tax_id:
            continue
        if g.tax_id in by_tid and by_tid[g.tax_id] in groups:
            target = groups[by_tid[g.tax_id]]
            for a, t in g.aliases.items():
                target.aliases.setdefault(a, t)
            target.names.update(g.names)
            merges.append({"簡稱": g.display, "合併到": target.display, "方式": f"同統編 {g.tax_id}"})
            groups.pop(k)
        else:
            by_tid[g.tax_id] = k

    for k, g in groups.items():
        for a in g.aliases:
            to_key[a] = k
        for n in g.names:
            to_key[n] = k
    return groups, to_key, merges


def staff_names(res: ParseResult) -> list[str]:
    """從 Sales／主寫／管考欄收集人名（排除「May等3人」這類描述）。"""
    names: set[str] = set()
    for c in res.contracts:
        for k in ("sales", "partner_sales", "writer", "outsource", "mid_review", "final_review"):
            v = c.fields.get(k)
            if not v:
                continue
            for part in re.split(r"[、,，/&＆]|\s+", v):
                part = part.strip()
                if part and len(part) <= 12 and not re.search(r"\d|等|人$|免|無|解約|Pending|N/A", part, re.I):
                    names.add(part)
    for p in res.payouts:
        names.add(p.person)
    return sorted(names)


def payments_by_stage(c: ContractRec) -> dict[str, Payment]:
    return {p.stage: p for p in c.payments}


def summary(res: ParseResult) -> dict:
    by_sheet = defaultdict(int)
    for c in res.contracts:
        by_sheet[c.sheet] += 1
    return {"contracts": len(res.contracts), "by_sheet": dict(by_sheet), "shipments": len(res.shipments),
            "payouts": len(res.payouts), "issues": len(res.issues), "skipped": res.skipped_sheets}
