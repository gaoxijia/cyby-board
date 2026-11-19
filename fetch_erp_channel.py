#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
按「渠道 × 门店」拉星火 ERP 销售数据（门店经营榜平台切换用）：
对 4 个时间段 × 3 个渠道（美团 / 京东 / 淘宝闪购）分别 groupBy=store 查询。
输出：erp_store_channel.json
  {"generated_at":"2026-11-13",
   "periods":{"p30":{"range":{...},"channels":{"美团":{"<门店名>":{"gmv":x,"orders":y,"qty":z}}}}}}
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
CHANNELS = ["美团", "京东", "淘宝闪购"]
TODAY = date.today()
END = TODAY - timedelta(days=1)

PERIODS = {
    "p7":  ("近7个完整日", 7),
    "p30": ("近30个完整日", 30),
    "p90": ("近90个完整日", 90),
    "mtd": ("本月至昨日", None),
}

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


def query_store_channel(f, t, channel):
    """某渠道下按门店聚合，分页拉全"""
    items, page = [], 1
    while True:
        pl = {"partnerIds": KEEP, "cities": [], "storeIds": [], "channels": [channel],
              "keyword": "", "dateFrom": f, "dateTo": t, "grain": "day", "groupBy": "store",
              "page": page, "pageSize": 100, "sortBy": "gmv", "sortOrder": "descending",
              "risk": "", "topic": "sales"}
        d = call(pl)
        tb = d.get("table") or {}
        items += tb.get("items") or []
        if page * 100 >= (tb.get("total") or 0):
            break
        page += 1
    out = {}
    for r in items:
        name = r.get("dimension_name") or ""
        cur = r.get("current") or r
        out[name] = {
            "gmv": round(float(cur.get("gmv") or 0), 2),
            "orders": int(cur.get("order_count") or 0),
            "qty": int(cur.get("sales_quantity") or 0),
        }
    return out


# 上次快照兜底：ERP BI 偶发 500（2026-11-19 channel 接口连续 500 导致云端构建整轮失败），
# 渠道×门店数据拿不到时沿用上次快照，不让整轮不发布。
# 快照来源优先仓库内的 erp_store_channel.json；没有的话从当前线上页面的 payload 里提取 storeCh。
PREV_PATH = os.path.join(OUT_DIR, "erp_store_channel.json")
prev_periods = {}
if os.path.exists(PREV_PATH):
    try:
        prev_periods = json.load(open(PREV_PATH, encoding="utf-8")).get("periods") or {}
    except Exception:
        prev_periods = {}
if not prev_periods:
    try:
        LIVE = "https://gaoxijia.github.io/cyby-board/index.html"
        req = urllib.request.Request(LIVE, headers={"User-Agent": "Mozilla/5.0"}, method="GET")
        with urllib.request.urlopen(req, timeout=60) as r:
            html = r.read().decode("utf-8", "ignore")
        marker = "const D = "
        i = html.find(marker)
        if i >= 0:
            payload, _ = json.JSONDecoder().raw_decode(html[i + len(marker):])
            prev_periods = payload.get("storeCh") or {}
            print(f"⚠️ 渠道兜底快照来自当前线上页面（{len(prev_periods)} 个周期）")
    except Exception as e:
        print(f"⚠️ 无法读取线上兜底快照: {str(e)[:100]}")

periods = {}
for key, (label, days) in PERIODS.items():
    f = (date(TODAY.year, TODAY.month, 1) if days is None else END - timedelta(days=days - 1)).isoformat()
    t = END.isoformat()
    try:
        ch = {}
        for c in CHANNELS:
            ch[c] = query_store_channel(f, t, c)
            tot_g = sum(v["gmv"] for v in ch[c].values())
            tot_o = sum(v["orders"] for v in ch[c].values())
            print(f"{label} {f}~{t} · {c}: {len(ch[c])} 店 · GMV {tot_g:,.0f} · 订单 {tot_o}")
        periods[key] = {"label": label, "range": {"from": f, "to": t}, "channels": ch}
    except Exception as e:
        old = prev_periods.get(key)
        if not old or not (old.get("channels") or {}):
            raise
        print(f"⚠️ {label} 渠道接口不可用（{str(e)[:80]}），沿用上次快照（{old.get('range',{}).get('from')}~{old.get('range',{}).get('to')}）")
        periods[key] = {"label": label, "range": old.get("range") or {"from": f, "to": t}, "channels": old["channels"]}

path = os.path.join(OUT_DIR, "erp_store_channel.json")
json.dump({"generated_at": TODAY.isoformat(), "periods": periods},
          open(path, "w", encoding="utf-8"), ensure_ascii=False)
print("✅", path)
