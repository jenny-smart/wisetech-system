"""產生和原始 Excel 結構相同的測試活頁簿（內容取自原檔的幾列）。"""
from datetime import datetime

from openpyxl import Workbook

NEW_H1 = ["合約編號", "公司名稱", "業務 (非ALLEN & ANDY)", "起始日", "終止日", "合約類型", "前金扣/不扣", "含稅/未稅",
          "送案別", "狀態", "過案金額", "前金(未稅)", None, None, None, None, None, None, None, None,
          "期中(未稅)", None, None, None, None, "期末(未稅)", None, None, None, None, None,
          "特別約定事項", "外部佣金", None, None, "績效結算", None, None, None, None]
NEW_H2 = [None] * 11 + ["合約條件", "第一期金額", "第一期發票日", "發票號碼", "入帳日", "第二期金額", "第二期發票日",
                        "發票號碼", "入帳日", "合約條件", "撥款金額", "發票日", "發票號碼", "入帳日",
                        "合約條件", "撥款金額", "發票金額", "發票日", "發票號碼", "入帳日", None,
                        "外部佣金%", "發放日", "來源", "Sales", "主寫", "委外", "期中管考", "期末管考"]


def merge_groups(ws, h1, row=1):
    i = 0
    while i < len(h1):
        if h1[i] is None:
            i += 1
            continue
        j = i + 1
        while j < len(h1) and h1[j] is None:
            j += 1
        if j - i > 1:
            ws.merge_cells(start_row=row, start_column=i + 1, end_row=row, end_column=j)
        elif NEW_H2[i] is None if len(NEW_H2) > i else True:
            ws.merge_cells(start_row=row, start_column=i + 1, end_row=row + 1, end_column=i + 1)
        i = j


def pct(ws, row, col, v):
    c = ws.cell(row, col, v)
    c.number_format = "0%"


def build(path):
    wb = Workbook()
    ws = wb.active
    ws.title = "115"
    ws.append(NEW_H1)
    ws.append(NEW_H2)
    merge_groups(ws, NEW_H1)
    ws.append(["11501-01", "晧飛思科技股份有限公司 ", None, datetime(2026, 1, 1), datetime(2026, 12, 31), "年約",
               "扣 (50%)", "未稅", "科專", "In Process", None, 160000, 80000, datetime(2025, 12, 26), "AB12345678",
               "1/5 入帳", 80000, None, None, None, None])
    pct(ws, 3, 21, 0.15)
    ws.append(["11503-07", "谷奧科技股份有限公司", None, "3/23/2026", None, "個案", "扣 (50%)", "未稅", "SBIR",
               "In Process", None, "60,000"])
    pct(ws, 4, 21, 0.18)
    ws.append(["11504-05"] + [None] * 30)  # 預留空號
    ws2 = wb.create_sheet("110")
    h1 = [x for k, x in enumerate(NEW_H1) if k != 2]
    h2 = [x for k, x in enumerate(NEW_H2) if k != 2]
    ws2.append(h1)
    ws2.append(h2)
    ws2.append(["11001-04", "睡羅漢有限公司", "110/01/21", None, "個案", "扣", "含稅", "SIIR", "Pass", "1,400,000",
                "57,142", None, "1/28/2021", "JC52183763", "2/2付款", None, None, None, None,
                "17%", None, None, None, None, "17%", None, None, None, None, None, None, None, None, None,
                "Andy", "Andy"])
    ws2.append(["11001-06", "小寶股份有限公司", "110/01/10", None, "個案", "扣", "含稅", "SIIR", "Pass", "1,600,000",
                "50,000", None, "2/22/2021", "JC52183782", "3/25入帳", None, None, None, None,
                "12%", "640,000", "10/6/2021", "RG53956866", "10/25/2021", "12%", "960000"])
    ws2.append(["11003-07", "吉爾科技股份有限公司", "110/03/29", None, "個案", "扣", "未稅", "北市創業", "Pass",
                "1,000,000", "60,000", None, "4/7/2021", "KX52735418", "4/15 入帳", None, None, None, None,
                "20%", "500,000", "11/16/2021", "TB54126758", "12/1/2021", "20%", "500,000", "100,000",
                "5/1/2022", "P54306452"])
    ws2.append([None, "PASS"])
    ws2.append(["11312-03", "菜霸子股份有限公司(解約)", "12/16/2024", None, "個案", "不扣", "未稅", "跨域研發",
                "In Process", None, "無前金"])
    ws2.append(["11303-02", "英晟電子股份有限公司 & 保密合約", "2/16/2024", None, "個案", "扣", "未稅", "SBIR",
                "In Process", None, "60,000"])

    t = wb.create_sheet("合約追蹤")
    t.append([None, "合約/發票追蹤清單"])
    t.append([None, "公司名稱", "Sales", "合約取\n得方式", "甲方\n用印", "我方\n用印", "合約寄\n出份數", "寄出/用印日期",
              "寄回/面交日期", "發票開立", "備註", "窗口"])
    t.append([None, "睿元 新約", "Allen", "郵寄", None, "V", 2, "4/7/2022", None, "發票已開出", None, "會計:張禎庭",
              "八德路4段111號8樓"])
    t.append([None, "睿元國際股份有限公司", "Allen", "郵寄", None, "V", 2, "4/7/2022", None, "發票已開出"])
    t.append([None, "谷奧科技股份有限公司", None, "郵寄", None, None, None, "3/23/2026", "11503-07", "發票已開出", "SBIR",
              "吳政勳", "(104614) 臺北市中山區林森北路380號5樓之1"])
    t.append([None, "思言科技股份有限公司", None, "郵寄", None, None, None, "3/15/2023", "na/寄發票", "發票已開出",
              "震豪年約for威許", "吳怡穎 Chloe", "臺北市中山區中山北路二段106之2號9樓 統編:82808896"])
    t.append([None, "帝圖文化股份有限公司", None, "郵寄", "V", "V", 1, "4/20/2023", "合約11204-04", "發票已開出",
              "111投抵", "劉家蓉2658-1788#108", "台北市大安區仁愛路三段136號3樓303室(芙蓉大樓)"])
    t.append(["Review", "樂多", "Allen"])
    t.append([None, "樂多科技股份有限公司", None, "面交", None, None, None, "9/4/2024", "na/寄發票", "發票已開出", "SBIR結案"])

    p = wb.create_sheet("績效報表_Item")
    p.append([None, None, "109年過案", None, None, "2020 3Q過案獎金", "獎金池分配", "2020 4Q過案獎金", "獎金池分配"])
    p.append(["Name", "前金", "個案", None, None, None, None, None, None])
    p.append(["Allen", 100, 3, None, None, "12,000", 3000, 8000, None])
    p.append(["Andy", 50, 1, None, None, 5000, None, None, 1500])
    wb.save(path)


if __name__ == "__main__":
    import sys
    build(sys.argv[1] if len(sys.argv) > 1 else "sample.xlsx")
