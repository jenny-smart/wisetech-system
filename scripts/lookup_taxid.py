"""用公司名稱查商工登記，自動補統編。

    DATABASE_URL=postgresql://... python scripts/lookup_taxid.py          # 查所有 pending
    python scripts/lookup_taxid.py --all                                  # 連 not_found / candidate 也重查
    python scripts/lookup_taxid.py --limit 20                             # 先試 20 家

結果：
- 名稱完全相符且唯一 → 直接寫入統編（tax_id_status = auto）
- 有多個候選或名稱不完全相同 → 存進 taxid_candidates（candidate），到系統「統編確認」頁人工挑選
- 查不到（行號、工作室、社團法人…）→ not_found，到系統手動輸入
- 查到的統編已經屬於另一家公司 → 標成 candidate，「統編確認」頁可以一鍵合併
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wisetech.db import DB, connect  # noqa: E402
from wisetech.taxid import decide, search_by_name  # noqa: E402

NON_COMPANY_HINTS = ("工作室", "社團法人", "財團法人", "協會", "大學", "學院", "學系", "工研院", "診所", "農場",
                     "行號", "食品行", "餐坊", "企業社", "浮潛行", "研究社", "委員會")


def run(db: DB, statuses: tuple[str, ...], limit: int | None, delay: float, log=print, progress=None):
    rows = db.all("select id, name from companies where tax_id is null and tax_id_status = any(%s) order by id"
                  + (f" limit {int(limit)}" if limit else ""), (list(statuses),))
    stats = {"auto": 0, "candidate": 0, "not_found": 0, "error": 0}
    for i, r in enumerate(rows, 1):
        name = r["name"]
        try:
            hits = search_by_name(name)
        except Exception as e:  # noqa: BLE001
            log(f"[{i}/{len(rows)}] {name}：查詢失敗 {e}")
            stats["error"] += 1
            continue
        verdict, hit = decide(hits)
        with db.tx():
            db.run("delete from taxid_candidates where company_id = %s", (r["id"],))
            for h in hits[:10]:
                db.run("insert into taxid_candidates (company_id, tax_id, gcis_name, gcis_status, score) "
                       "values (%s,%s,%s,%s,%s) on conflict do nothing",
                       (r["id"], h.tax_id, h.name, h.status, h.score))
            if verdict == "auto":
                owner = db.one("select id, name from companies where tax_id = %s", (hit.tax_id,))
                if owner:
                    verdict = "candidate"
                    db.update("companies", r["id"], {"tax_id_status": "candidate",
                                                     "note": f"統編 {hit.tax_id} 已屬於「{owner['name']}」，可能需要合併"})
                else:
                    db.update("companies", r["id"], {"tax_id": hit.tax_id, "tax_id_status": "auto",
                                                     "gcis_name": hit.name, "gcis_status": hit.status})
            elif verdict == "candidate":
                db.update("companies", r["id"], {"tax_id_status": "candidate"})
            else:
                status = "not_applicable" if any(x in name for x in NON_COMPANY_HINTS) else "not_found"
                db.update("companies", r["id"], {"tax_id_status": status})
                verdict = "not_found"
        stats[verdict] += 1
        shown = f"{hit.tax_id} {hit.name}" if hit else f"{len(hits)} 個候選"
        log(f"[{i}/{len(rows)}] {name} → {verdict}（{shown}）")
        if progress:
            progress(i, len(rows))
        time.sleep(delay)
    log(f"完成：自動 {stats['auto']}、待確認 {stats['candidate']}、查無 {stats['not_found']}、失敗 {stats['error']}")
    log("請到系統的「統編確認」頁處理待確認與查無的公司。")
    return stats


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true", help="連 candidate / not_found 也重新查詢")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--delay", type=float, default=0.4, help="每次查詢間隔秒數（避免被限流）")
    a = ap.parse_args()
    statuses = ("pending", "candidate", "not_found") if a.all else ("pending",)
    run(DB(connect()), statuses, a.limit, a.delay)


if __name__ == "__main__":
    main()
