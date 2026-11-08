"""Excel 舊資料的清洗工具：日期、金額、公司名稱、統編。

全部是純函式，匯入腳本和 Streamlit 介面共用；測試在 tests/test_parse.py。
"""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

# ───────────────────────── 文字 ─────────────────────────

_WS = re.compile(r"\s+")


def clean_text(v) -> str | None:
    """None / 空白 / 只有符號的儲存格 → None；其餘去頭尾空白、合併連續空白。"""
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v).replace("　", " ").strip()
    s = _WS.sub(" ", s)
    if s in ("", "-", "--", "—", ".", "/"):
        return None
    if set(s) <= {"#"}:
        return None
    return s


def header_key(v) -> str:
    """表頭比對用：去掉所有空白與換行、轉大寫。"""
    s = clean_text(v) or ""
    return re.sub(r"\s", "", s).upper()


# ───────────────────────── 日期 ─────────────────────────

_YMD = re.compile(r"^(\d{2,4})\s*[/.\-年]\s*(\d{1,2})\s*[/.\-月]\s*(\d{1,2})\s*日?$")
_MDY = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_ROC7 = re.compile(r"^(\d{2,3})(\d{2})(\d{2})$")
_BLANK_WORDS = {"N/A", "NA", "NIL", "無", "NONE", "-"}
_DATE_PREFIX = re.compile(r"^(\d{2,4})\s*[/.\-]\s*(\d{1,2})\s*[/.\-]\s*(\d{1,2})|^(\d{3})(\d{2})(\d{2})(?!\d)")


def parse_date_prefix(text: str | None) -> date | None:
    """「115.1.26 入帳$146,970」「2020/1/31已升」→ 開頭的日期。"""
    if not text:
        return None
    m = _DATE_PREFIX.match(text.strip())
    if not m:
        return None
    if m.group(1):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        y, mo, d = int(m.group(4)), int(m.group(5)), int(m.group(6))
    if y < 1900:
        y += 1911
    return _mk(y, mo, d)


def _mk(y: int, m: int, d: int) -> date | None:
    try:
        dt = date(y, m, d)
    except ValueError:
        return None
    # 1900/1/2 這種是 Excel 把數字誤設成日期格式，當作無效
    if not (1990 <= dt.year <= 2100):
        return None
    return dt


def parse_date(v) -> tuple[date | None, str | None]:
    """回傳 (日期, 無法解析時的原始文字)。

    支援：Excel 日期、2/22/2021、2025/2/5、111/01/18、0111/1/17、1080314（民國七碼）。
    """
    if v is None:
        return None, None
    if isinstance(v, datetime):
        d = v.date()
        return (d, None) if 1990 <= d.year <= 2100 else (None, v.strftime("%Y/%m/%d"))
    if isinstance(v, date):
        return v, None
    s = clean_text(v)
    if s is None:
        return None, None
    if isinstance(v, (int, float)):
        s = str(int(v))
    if s.upper().replace(" ", "") in _BLANK_WORDS:
        return None, None

    m = _YMD.match(s)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        if y < 1900:          # 民國年
            y += 1911
        dt = _mk(y, mo, d)
        return (dt, None) if dt else (None, s)
    m = _MDY.match(s)
    if m:
        mo, d, y = (int(x) for x in m.groups())
        dt = _mk(y, mo, d)
        return (dt, None) if dt else (None, s)
    m = _ROC7.match(s)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        dt = _mk(y + 1911, mo, d)
        return (dt, None) if dt else (None, s)
    return None, s


# ───────────────────────── 金額 ─────────────────────────

_NUM_PREFIX = re.compile(r"^\$?\s*([\d,]+(?:\.\d+)?)")


def parse_amount(v) -> tuple[Decimal | None, str | None]:
    """回傳 (金額, 原始文字)。

    完全是數字時原始文字為 None；"4,600,000(複審未通過)" 取前面的數字並保留原文；
    "100,000~200,000"、"N/A"、"無前金" 等無法確定的只保留原文。
    """
    if v is None:
        return None, None
    if isinstance(v, bool):
        return None, str(v)
    if isinstance(v, (int, float, Decimal)):
        return Decimal(str(v)), None
    s = clean_text(v)
    if s is None:
        return None, None
    compact = s.replace(" ", "")
    plain = compact.lstrip("$").replace(",", "")
    try:
        return Decimal(plain), None
    except InvalidOperation:
        pass
    if "~" in compact or "～" in compact:
        return None, s
    m = _NUM_PREFIX.match(compact)
    if m and len(m.group(1).replace(",", "")) >= 3:
        try:
            return Decimal(m.group(1).replace(",", "")), s
        except InvalidOperation:
            pass
    return None, s


def fmt_pct(v, number_format: str | None = None) -> str | None:
    """百分比儲存格（0.15 + 格式 0%）→ "15%"；文字原樣保留。"""
    if v is None:
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if number_format and "%" in number_format:
            n = round(v * 100, 4)
            return f"{n:g}%"
        return f"{v:g}"
    return clean_text(v)


def parse_pct(text: str | None) -> Decimal | None:
    """"15%" → 15；"0.2" → 20；"15+3%"、"10%~20%" → None。"""
    if not text:
        return None
    s = text.replace(" ", "").rstrip("%")
    try:
        n = Decimal(s)
    except InvalidOperation:
        return None
    return n * 100 if n <= 1 else n


# ───────────────────────── 布林（用印欄的 V） ─────────────────────────

def parse_check(v) -> bool | None:
    s = clean_text(v)
    if s is None:
        return None
    return s.upper() in ("V", "Y", "YES", "✓", "✔", "O", "TRUE", "1")


# ───────────────────────── 公司名稱 ─────────────────────────

_PAREN = re.compile(r"[（(]([^（）()]*)[)）]")
_TERMINATED = re.compile(r"解約|作廢")
_NDA = re.compile(r"保密|NDA", re.I)
_TRAILING_NOTES = re.compile(r"\s*(新約|搬家|增補合約|勞務承覽)$")
_FOR_SPLIT = re.compile(r"\s+for\s+", re.I)


def clean_company_name(raw) -> dict:
    """把 Excel 公司欄拆成 {name, note, terminated, nda}。

    - 括號內的註記一律移到 note，只保留「(股)」。
      例：「菜霸子股份有限公司(解約)」→ name=菜霸子股份有限公司, note=解約, terminated=True
    - 「大綜電腦系統股份有限公司 for 昇達彈簧」→ name=大綜電腦…, note=for 昇達彈簧
    - 一格多家公司（換行）→ 取第一家，其餘放 note
    """
    s = clean_text(raw)
    if s is None:
        return {"name": None, "note": None, "terminated": False, "nda": False}
    full = s
    notes: list[str] = []

    lines = [ln.strip() for ln in str(raw).replace("\r", "\n").split("\n") if ln.strip()]
    if len(lines) > 1:
        s = clean_text(lines[0]) or s
        notes.append("同約公司：" + "、".join(lines[1:]))

    def _strip(m: re.Match) -> str:
        inner = m.group(1).strip()
        if inner == "股":
            return m.group(0)
        if inner:
            notes.append(inner)
        return ""

    prev = None
    while prev != s:                                   # 處理巢狀括號
        prev = s
        s = _PAREN.sub(_strip, s)

    parts = _FOR_SPLIT.split(s, maxsplit=1)
    if len(parts) == 2:
        s = parts[0]
        notes.append("for " + parts[1].strip())

    for sep in (" & 保密合約", "&保密合約", " & ", "&"):
        if sep in s and _NDA.search(s.split(sep, 1)[1]):
            s, rest = s.split(sep, 1)
            notes.append(rest.strip())
            break

    m = _TRAILING_NOTES.search(s)
    if m:
        notes.append(m.group(1))
        s = s[: m.start()]

    s = s.replace("\\_", "_").strip(" ,，、-—_")
    return {
        "name": s or full,
        "note": "；".join(n for n in notes if n) or None,
        "terminated": bool(_TERMINATED.search(full)),
        "nda": bool(_NDA.search(full)),
    }


_SUFFIXES = ("股份有限公司", "股分有限公司", "有限公司", "(股)公司", "公司")


def name_key(name: str | None) -> str:
    """分組用的比對鍵：去空白、臺→台、(股)→股份、全形英數轉半形、大小寫不分。"""
    if not name:
        return ""
    s = name.translate(str.maketrans("ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ０１２３４５６７８９",
                                     "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"))
    s = re.sub(r"\s", "", s).replace("臺", "台").replace("(股)", "股份").replace("（股）", "股份")
    s = s.replace("股分有限公司", "股份有限公司")
    return s.upper()


def short_key(name: str | None) -> str:
    """去掉公司型態字尾的鍵，用來把「睿元」對到「睿元國際股份有限公司」。"""
    k = name_key(name)
    for suf in ("股份有限公司", "有限公司", "股份公司", "公司"):
        if k.endswith(suf):
            return k[: -len(suf)]
    return k


# ───────────────────────── 統一編號 ─────────────────────────

_TAXID = re.compile(r"(?<!\d)(\d{8})(?!\d)")


def valid_tax_id(t: str | None) -> bool:
    """統一編號檢查碼（財政部 2023 起的 %5 規則，相容舊的 %10）。"""
    if not t or not re.fullmatch(r"\d{8}", t):
        return False
    weights = (1, 2, 1, 2, 1, 2, 4, 1)
    total = 0
    for d, w in zip(t, weights):
        p = int(d) * w
        total += p // 10 + p % 10
    if total % 5 == 0:
        return True
    return t[6] == "7" and (total + 1) % 5 == 0


def extract_tax_id(text: str | None) -> str | None:
    """在文字中找出第一個通過檢查碼的 8 位數字。"""
    if not text:
        return None
    for m in _TAXID.finditer(str(text)):
        if valid_tax_id(m.group(1)):
            return m.group(1)
    return None


# ───────────────────────── 合約編號 ─────────────────────────

_CONTRACT_NO = re.compile(r"(?<!\d)(1\d{4})[-_](\d{2})(?:/(\d{2}))?(?!\d)")


def normalize_contract_no(v) -> str | None:
    s = clean_text(v)
    if not s:
        return None
    m = _CONTRACT_NO.search(s)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return s


def find_contract_refs(text: str | None) -> list[str]:
    """「合約11203-04/05」→ ['11203-04', '11203-05']"""
    if not text:
        return []
    out = []
    for m in _CONTRACT_NO.finditer(str(text)):
        out.append(f"{m.group(1)}-{m.group(2)}")
        if m.group(3):
            out.append(f"{m.group(1)}-{m.group(3)}")
    return out


def roc_prefix(d: date) -> str:
    """2026/3/5 → '11503'（新合約編號前綴）"""
    return f"{d.year - 1911}{d.month:02d}"


# ───────────────────────── 聯絡資訊 ─────────────────────────

_PHONE = re.compile(r"(09\d{2}[-\s]?\d{3}[-\s]?\d{3}|0\d{1,2}[-\s]?\d{3,4}[-\s]?\d{4}(?:\s*(?:#|ext\.?|分機)\s*\d+)?|\d{4}-\d{4}(?:\s*#\s*\d+)?)", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_ADDRESS_HINT = re.compile(r"\d+\s*號|[路街道].{0,4}段|[市縣].{1,4}[區鄉鎮市]")


def classify_contact_cell(text: str | None) -> str | None:
    """猜一個儲存格是 email / address / phone / name。"""
    if not text:
        return None
    if _EMAIL.search(text):
        return "email"
    if _ADDRESS_HINT.search(text):
        return "address"
    if _PHONE.fullmatch(text.strip()):
        return "phone"
    return "name"


def find_phone(text: str | None) -> str | None:
    if not text:
        return None
    m = _PHONE.search(text)
    return m.group(1).strip() if m else None


def find_email(text: str | None) -> str | None:
    if not text:
        return None
    m = _EMAIL.search(text)
    return m.group(0) if m else None
