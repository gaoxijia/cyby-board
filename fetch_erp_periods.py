#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
按 4 个时间段拉星火 ERP 经营侧数据（云桂口径）：
近7个完整日 / 近30个完整日 / 近90个完整日 / 本月至昨日
每个时间段：overview（KPI/趋势/渠道/图表）+ sales×store（门店GMV/订单/件数）
输出：erp_periods.json
"""
import json
import time
import os
import urllib.request
import urllib.error
from datetime import date, timedelta

import cloud_auth

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
API = "https://erp.torras.work/api/bi/query"
HOST = "https://erp.torras.work"
KEEP = ["5157", "5159"]
TODAY = date.today()                     # 运行日（凌晨跑时即当天）
END = TODAY - timedelta(days=1)          # 昨日（最近一个完整日）

PERIODS = {
    "p7":  ("近7个完整日", 7),
    "p30": ("近30个完整日", 30),
    "p90": ("近90个完整日", 90),
    "mtd": ("本月至昨日", None),
}


def dstr(d):
    return d.isoformat()


TOKEN = cloud_auth.erp_token()
print("ERP 登录成功（账号密码换新 token）")


def call(pl):
    """带重试：ERP 偶发 500 / 网络抖动时退避重试，避免云端无人值守时整轮失败"""
    last = None
    for i in range(4):
        try:
            req = urllib.request.Request(API, data=json.dumps(pl).encode(), headers={
                "Authorization": "Bearer " + TOKEN, "Content-Type": "application/json",
                "Referer": HOST + "/", "User-Agent": "Mozilla/5.0"}, method="POST")
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code < 500:
                raise
            last = e
        except Exception as e:
            last = e
        if i < 3:
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"ERP 接口连续 4 次失败: {last}")


def base(topic, group, f, t):
    return {"partnerIds": KEEP, "cities": [], "storeIds": [], "channels": [], "keyword": "",
            "dateFrom": f, "dateTo": t, "grain": "day", "groupBy": group, "page": 1,
            "pageSize": 100, "sortBy": "gmv", "sortOrder": "descending", "risk": "", "topic": topic}


# 上次快照兜底：ERP BI 偶发 500（2026-11-18 overview/partner 连续几个小时 500），
# 不能因为一个子接口挂掉就整轮不发布。
# 快照来源优先仓库内的 erp_periods.json；没有的话从当前线上页面的 payload 里提取 periods。
PREV_PATH = os.path.join(OUT_DIR, "erp_periods.json")
prev = json.load(open(PREV_PATH, encoding="utf-8")) if os.path.exists(PREV_PATH) else {}
if not prev:
    try:
        LIVE = "https://gaoxijia.github.io/cyby-board/index.html"
        req = urllib.request.Request(LIVE, headers={"User-Agent": "Mozilla/5.0"}, method="GET")
        with urllib.request.urlopen(req, timeout=60) as r:
            html = r.read().decode("utf-8", "ignore")
        marker = "const D = "
        i = html.find(marker)
        if i >= 0:
            payload, _ = json.JSONDecoder().raw_decode(html[i + len(marker):])
            prev = payload.get("periods") or {}
            print(f"⚠️ ERP 兜底快照来自当前线上页面（{len(prev)} 个周期）")
    except Exception as e:
        print(f"⚠️ 无法读取线上兜底快照: {str(e)[:100]}")

out = {}
for key, (label, days) in PERIODS.items():
    f = dstr(date(TODAY.year, TODAY.month, 1)) if days is None else dstr(END - timedelta(days=days - 1))
    t = dstr(END)
    try:
        ov = call(base("overview", "partner", f, t))
    except Exception as e:
        old = prev.get(key)
        if not old:
            raise
        ov = old
        print(f"⚠️ {label} overview 接口不可用（{str(e)[:80]}），沿用上次快照")
    # 门店销售（分页拉全）；失败同样沿用上次快照
    if ov is not prev.get(key):
        try:
            items, page = [], 1
            while True:
                d = call(base("sales", "store", f, t) | {"page": page})
                tb = d.get("table") or {}
                items += tb.get("items") or []
                if page * 100 >= (tb.get("total") or 0):
                    break
                page += 1
        except Exception as e:
            items = (prev.get(key) or {}).get("storeSales") or []
            print(f"⚠️ {label} 门店销售接口不可用（{str(e)[:80]}），沿用上次快照")
    else:
        items = (prev.get(key) or {}).get("storeSales") or []
    out[key] = {
        "label": label, "range": {"from": f, "to": t,
                                  "days": (date.fromisoformat(t) - date.fromisoformat(f)).days + 1},
        "summary": ov.get("summary"), "summaries": ov.get("summaries"),
        "comparisons": ov.get("comparisons"), "trend": ov.get("trend"),
        "heatmap": ov.get("heatmap"), "charts": ov.get("charts"),
        "storeSales": items,
    }
    s = ov.get("summary") or {}
    print(f"{label} {f}~{t}: GMV {s.get('gmv', 0):,.0f} | 订单 {s.get('order_count')} | "
          f"件数 {s.get('sales_quantity')} | 门店 {len(items)}")

path = os.path.join(OUT_DIR, "erp_periods.json")
json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False)
print("✅", path)
