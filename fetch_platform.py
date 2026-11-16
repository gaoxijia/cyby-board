#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
街顺平台维度每日绩效采集：美团 / 京东 / 淘宝闪购
接口：/saas/WebBoard/platformSaleData（区间聚合，故按天逐日拉取）
口径：valid_order=有效订单数，order_amount=交易额（元）
范围：本月 1 日 ~ 昨日（与「时间进度」对齐）
输出：platform_daily.json
  {"month":"2026-11","from":"2026-11-01","to":"2026-11-04","days":4,
   "dates":[...],"platforms":{"美团":{"o":[...],"g":[...]}, ...},
   "monthly":{"美团":{"orders":x,"gmv":y}, ...}}
"""
import json
import os
import urllib.request
from datetime import date, timedelta

import cloud_auth

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
VENDORS = (6168, 6305)                      # 云南、广西
API = "https://api.waisongbang.com/saas/WebBoard/platformSaleData"
H = {"Referer": "https://s.waisongbang.com/", "User-Agent": "Mozilla/5.0"}
PLATFORMS = ["淘宝闪购", "京东", "美团"]


def num(s):
    try:
        return float(str(s or 0).replace(",", ""))
    except ValueError:
        return 0.0


def main():
    tok = cloud_auth.js_token()
    y = date.today() - timedelta(days=1)
    start = y.replace(day=1)
    dates = [(start + timedelta(days=i)).isoformat() for i in range((y - start).days + 1)]

    data = {p: {"o": [0] * len(dates), "g": [0.0] * len(dates)} for p in PLATFORMS}
    for i, d in enumerate(dates):
        for vid in VENDORS:
            u = (f"{API}?access_token={tok}&vendor_id={vid}&store_id=-1"
                 f"&date_type=day&start_date={d}&end_date={d}")
            try:
                with urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=60) as r:
                    obj = json.loads(r.read().decode()).get("obj") or []
            except Exception as e:
                raise SystemExit(f"街顺平台接口失败（{d} / vendor {vid}）：{e}")
            for row in obj:
                name = row.get("platform_name") or ""
                key = "京东" if "京东" in name else ("美团" if "美团" in name else ("淘宝闪购" if "淘宝" in name else name))
                if key not in data:
                    data[key] = {"o": [0] * len(dates), "g": [0.0] * len(dates)}
                data[key]["o"][i] += int(row.get("valid_order") or 0)
                data[key]["g"][i] += num(row.get("order_amount"))
        print(f"  {d}: " + " | ".join(f"{p} {data[p]['o'][i]}单/{data[p]['g'][i]:,.0f}元" for p in PLATFORMS))

    monthly = {p: {"orders": sum(v["o"]), "gmv": round(sum(v["g"]), 2)} for p, v in data.items()}
    out = {"month": y.strftime("%Y-%m"), "from": dates[0], "to": dates[-1], "days": len(dates),
           "generated_at": y.isoformat(), "dates": dates, "platforms": data, "monthly": monthly}
    path = os.path.join(OUT_DIR, "platform_daily.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"\n✅ {path} · {dates[0]} ~ {dates[-1]}（{len(dates)} 天）")
    for p in PLATFORMS:
        m = monthly[p]
        print(f"   {p}: {m['orders']:,} 单 · 交易额 {m['gmv']:,.2f} 元")


if __name__ == "__main__":
    main()
