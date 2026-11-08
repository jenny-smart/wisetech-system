from datetime import date
from decimal import Decimal

from tests.make_sample import build
from wisetech.excel_import import group_companies, parse_workbook


def test_sample_workbook(tmp_path):
    path = tmp_path / "sample.xlsx"
    build(str(path))
    res = parse_workbook(str(path))

    assert len(res.contracts) == 7                     # 空號與 PASS 列不算
    k = {c.contract_no: c for c in res.contracts}

    c = k["11003-07"]
    assert c.fields["approved_amount"] == Decimal("1000000")
    assert c.fields["mid_rate"] == "20%"
    stages = {p.stage: p for p in c.payments}
    assert stages["前金1"].received_date == date(2021, 4, 15)
    assert stages["期中"].grant_amount == Decimal("500000")
    assert stages["期末"].invoice_amount == Decimal("100000")

    assert k["11501-01"].payments[0].received_date == date(2026, 1, 5)   # "1/5 入帳" 補年份
    assert k["11312-03"].is_terminated
    assert k["11303-02"].is_nda

    groups, _, merges = group_companies(res)
    names = {g.display for g in groups.values()}
    assert "睿元國際股份有限公司" in names and "睿元" not in names
    assert any(g.tax_id == "82808896" for g in groups.values())

    refs = [s.fields.get("contract_ref") for s in res.shipments]
    assert "11503-07" in refs and "11204-04" in refs
    assert len(res.payouts) == 5
