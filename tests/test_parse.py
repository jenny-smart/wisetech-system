from datetime import date, datetime
from decimal import Decimal

from wisetech.parse import (
    clean_company_name, extract_tax_id, find_contract_refs, fmt_pct, name_key,
    normalize_contract_no, parse_amount, parse_date, parse_pct, short_key, valid_tax_id,
)


def test_parse_date_formats():
    assert parse_date("2/22/2021") == (date(2021, 2, 22), None)
    assert parse_date("2025/2/5") == (date(2025, 2, 5), None)
    assert parse_date("111/01/18") == (date(2022, 1, 18), None)
    assert parse_date("0111/1/17") == (date(2022, 1, 17), None)
    assert parse_date("1080314") == (date(2019, 3, 14), None)
    assert parse_date(1090105) == (date(2020, 1, 5), None)
    assert parse_date(datetime(2024, 3, 6)) == (date(2024, 3, 6), None)
    assert parse_date("N/A") == (None, "N/A")
    assert parse_date("1/2/1900") == (None, "1/2/1900")
    assert parse_date(None) == (None, None)


def test_parse_amount():
    assert parse_amount('"142,857"'.strip('"')) == (Decimal("142857"), None)
    assert parse_amount("$72,200") == (Decimal("72200"), None)
    assert parse_amount(160000) == (Decimal("160000"), None)
    assert parse_amount("4,600,000(複宷未通過)") == (Decimal("4600000"), "4,600,000(複宷未通過)")
    assert parse_amount("100,000~200,000") == (None, "100,000~200,000")
    assert parse_amount("無前金") == (None, "無前金")
    assert parse_amount("N/A") == (None, "N/A")


def test_pct():
    assert fmt_pct(0.15, "0%") == "15%"
    assert fmt_pct(0.108, "0.00%") == "10.8%"
    assert fmt_pct("15+3%") == "15+3%"
    assert parse_pct("15%") == Decimal("15")
    assert parse_pct("0.2") == Decimal("20")
    assert parse_pct("10%~20%") is None


def test_clean_company_name():
    r = clean_company_name("菜霸子股份有限公司(解約)")
    assert r["name"] == "菜霸子股份有限公司" and r["terminated"] and r["note"] == "解約"

    r = clean_company_name("大綜電腦系統股份有限公司 for 昇達彈簧工業有限公司")
    assert r["name"] == "大綜電腦系統股份有限公司" and r["note"] == "for 昇達彈簧工業有限公司"

    r = clean_company_name("英晟電子股份有限公司 & 保密合約")
    assert r["name"] == "英晟電子股份有限公司" and r["nda"]

    r = clean_company_name("亞家管理顧問(股)有限公司")
    assert r["name"] == "亞家管理顧問(股)有限公司" and r["note"] is None

    r = clean_company_name("睿元 新約")
    assert r["name"] == "睿元" and r["note"] == "新約"

    r = clean_company_name("饗樂纖農生技股份有限公司\n郁創科技股份有限公司")
    assert r["name"] == "饗樂纖農生技股份有限公司" and "郁創" in r["note"]

    r = clean_company_name("薩玞音樂餐坊 (31880923) (解約退20000)")
    assert r["name"] == "薩玞音樂餐坊" and "31880923" in r["note"]


def test_keys():
    assert name_key("台灣醫智股份有限公司") == name_key("臺灣醫智股份有限公司")
    assert short_key("睿元國際股份有限公司") == "睿元國際"
    assert name_key("股感媒體股分有限公司") == name_key("股感媒體股份有限公司")


def test_tax_id():
    assert valid_tax_id("82808896")
    assert valid_tax_id("90322869")
    assert not valid_tax_id("12345678")
    assert extract_tax_id("臺北市中山區… 統編:82808896") == "82808896"


def test_contract_no():
    assert normalize_contract_no("11110_07") == "11110-07"
    assert normalize_contract_no(" 11503-07 ") == "11503-07"
    assert find_contract_refs("合約11203-04/05") == ["11203-04", "11203-05"]
    assert find_contract_refs("na/寄發票") == []
