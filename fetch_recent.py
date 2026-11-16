#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
拉取云桂 17 家门店的近 3 / 7 / 14 天店-SKU 销量，供补货清单显示短周期动销。
输出：recent_qty.json  {store_id: {sku_id: {d3,d7,d14}}}
"""
import json
import time
import os
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
API = "https://erp.torras.work/api/bi/query"
HOST = "https://erp.torras.work"
KEEP = {"5157", "5159"}
DATE_TO = (date.today() - timedelta(days=1)).isoformat()   # 昨日（最近一个完整日）

import cloud_auth
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


def payload(sid, dfrom, page=1):
    return {"partnerIds": sorted(KEEP), "cities": [], "storeIds": [str(sid)], "channels": [],
            "keyword": "", "dateFrom": dfrom, "dateTo": DATE_TO, "grain": "day",
            "groupBy": "sku", "page": page, "pageSize": 100, "sortBy": "gmv",
            "sortOrder": "descending", "risk": "", "topic": "movement"}


def fetch_store_range(sid, days):
    dfrom = (date.fromisoformat(DATE_TO) - timedelta(days=days - 1)).isoformat()
    first = call(payload(sid, dfrom))
    tb = first.get("table") or {}
    items = list(tb.get("items") or [])
    total = tb.get("total") or 0
    pages = (total + 99) // 100
    for pg in range(2, pages + 1):
        items += (call(payload(sid, dfrom, pg)).get("table") or {}).get("items") or []
    return {r["js_sku_id"]: r.get("sales_quantity") or 0 for r in items}


def load_stores():
    """门店清单：优先精简的 erp_stores.json（云端包只带这个，2KB），
    退化为历史大文件 erp_data.json（13MB，仅在本地存在）。"""
    slim = os.path.join(OUT_DIR, "erp_stores.json")
    if os.path.exists(slim):
        rows = json.load(open(slim))["stores"]
    else:
        rows = json.load(open(os.path.join(OUT_DIR, "erp_data.json")))["stores"]
    return [s for s in rows if str(s["third_partner_id"]) in KEEP]


stores = load_stores()
print(f"门店 {len(stores)} 家 · 区间末日 {DATE_TO}")

out, done = {}, 0
tasks = [(s["id"], d) for s in stores for d in (3, 7, 14)]
with ThreadPoolExecutor(max_workers=6) as ex:
    futs = {ex.submit(fetch_store_range, sid, d): (sid, d) for sid, d in tasks}
    for f in as_completed(futs):
        sid, d = futs[f]
        try:
            data = f.result()
        except Exception as e:
            print(f"  ! 店 {sid} {d}天 失败: {e}")
            data = {}
        out.setdefault(sid, {})
        key = f"d{d}"
        for sku, q in data.items():
            out[sid].setdefault(sku, {})[key] = q
        done += 1
        if done % 10 == 0:
            print(f"  [{done}/{len(tasks)}] 店 {sid} · {d}天 · {len(data)} SKU")

path = os.path.join(OUT_DIR, "recent_qty.json")
json.dump(out, open(path, "w"), ensure_ascii=False)
tot = {d: sum(v.get(f"d{d}", 0) for s in out.values() for v in s.values()) for d in (3, 7, 14)}
print(f"✅ {path}")
print(f"   近3天 {tot[3]:,} 件 | 近7天 {tot[7]:,} 件 | 近14天 {tot[14]:,} 件")
