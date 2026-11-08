"""經濟部商工登記公示資料（GCIS）查詢統一編號。

API 說明：https://data.gcis.nat.gov.tw/od/rule
- 公司名稱關鍵字查詢：6BBA2268-1367-4B42-9CCA-BC17499EBE8C
- 以統編查公司基本資料：5F64D864-61CB-4D0D-8AD9-492047CC1EA6
只涵蓋「公司」；行號、工作室、社團法人、學校、外商分公司等查不到，需人工輸入。
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import requests

from .parse import name_key, short_key

BASE = "https://data.gcis.nat.gov.tw/od/data/api"
NAME_API = f"{BASE}/6BBA2268-1367-4B42-9CCA-BC17499EBE8C"
BAN_API = f"{BASE}/5F64D864-61CB-4D0D-8AD9-492047CC1EA6"
HEADERS = {"User-Agent": "wisetech-system/1.0"}

# 公司狀態代碼：01 核准設立；其他（解散、廢止…）也查，讓已結束的舊客戶也能補到統編
STATUS_CODES = ("01", "02", "03", "04", "05", "06", "07", "08", "09", "10")


@dataclass
class Hit:
    tax_id: str
    name: str
    status: str | None
    score: float


def _get(url: str, params: dict, retries: int = 2):
    for i in range(retries + 1):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=15)
            if r.status_code == 200 and r.text.strip():
                return r.json()
            return []
        except (requests.RequestException, ValueError):
            if i == retries:
                raise
            time.sleep(1.5 * (i + 1))
    return []


def search_by_name(name: str, top: int = 20, active_only: bool = False) -> list[Hit]:
    """依名稱查詢，回傳依相似度排序的候選。"""
    query = name.replace("(股)", "股份").replace("（股）", "股份").strip()
    codes = ("01",) if active_only else STATUS_CODES
    hits: dict[str, Hit] = {}
    for code in codes:
        data = _get(NAME_API, {
            "$format": "json",
            "$filter": f"Company_Name like {query} and Company_Status eq {code}",
            "$skip": 0, "$top": top,
        })
        for row in data or []:
            ban = str(row.get("Business_Accounting_NO", "")).strip()
            cname = (row.get("Company_Name") or "").strip()
            if not ban or ban in hits:
                continue
            hits[ban] = Hit(ban, cname, row.get("Company_Status_Desc") or code, score_match(name, cname))
        if code == "01" and any(h.score >= 1.0 for h in hits.values()):
            break               # 核准設立中已有完全相符，就不用再查其他狀態
    return sorted(hits.values(), key=lambda h: -h.score)


def lookup_by_tax_id(tax_id: str) -> Hit | None:
    data = _get(BAN_API, {"$format": "json", "$filter": f"Business_Accounting_NO eq {tax_id}",
                          "$skip": 0, "$top": 1})
    if not data:
        return None
    row = data[0]
    return Hit(tax_id, (row.get("Company_Name") or "").strip(), row.get("Company_Status_Desc"), 1.0)


def score_match(ours: str, theirs: str) -> float:
    """1.0 完全相同；0.9 去掉公司型態後相同；其餘依字元重疊比例。"""
    a, b = name_key(ours), name_key(theirs)
    if a == b:
        return 1.0
    sa, sb = short_key(ours), short_key(theirs)
    if sa and sa == sb:
        return 0.9
    if sa and sb.startswith(sa):
        return 0.7 * len(sa) / max(len(sb), 1) + 0.1
    common = len(set(sa) & set(sb))
    return round(0.6 * common / max(len(set(sa) | set(sb)), 1), 3)


def decide(hits: list[Hit]) -> tuple[str, Hit | None]:
    """自動判定：('auto', hit) 可直接採用；('candidate', None) 需人工；('not_found', None)。"""
    if not hits:
        return "not_found", None
    exact = [h for h in hits if h.score >= 1.0]
    if len(exact) == 1:
        return "auto", exact[0]
    near = [h for h in hits if h.score >= 0.9]
    if len(near) == 1 and len(hits) == 1:
        return "auto", near[0]
    return "candidate", None
