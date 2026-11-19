#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock_hist.py —— 累积「门店×SKU 是否有库存」的每日快照，用于计算在售时长。

为什么需要它：
  街顺 / 星火 ERP 都没有「历史逐日库存」接口（只有当前库存快照）：
    · ERP BI 只支持 topic=sales（stock/inventory 一律 400）；
    · 街顺 storeProduct/list 只给当前 stock + 累计销量；
    · 街顺「库存流水」/api/storeProduct/stockDetail 带 pre_stock/stock/时间戳，可还原库存，
      但只保留最近约 6-7 天（2026-11-13 起），不足以覆盖 30 天口径。
  所以：用「库存流水」回填已覆盖的最近几天 + 用当天 jieshun.json 快照补今天，
  把结果并入 stock_hist.json 逐日累积；满 30 天后「30 天在售时长」即为精确值。

输出：stock_hist.json
  {"updated":"2026-11-19", "days":["2026-11-13",...],
   "bySku":{"SKU":[2,0,1,...]}}          # 与 days 对齐，值为当天有库存的门店数（0=全部断货）
"""
import json
import os
import urllib.request
import urllib.parse
from datetime import date, datetime, timedelta

import cloud_auth

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
H = {"Referer": "https://s.waisongbang.com/", "User-Agent": "Mozilla/5.0"}
HIST = os.path.join(OUT_DIR, "stock_hist.json")
KEEP_DAYS = 90          # 历史保留天数
RECOMPUTE_DAYS = 8      # 每次重算最近 N 天（覆盖流水修正）
VENDORS = (6168, 6305)


def get(u, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(u, headers=H)
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            last = e
    raise RuntimeError(f"街顺接口连续 {tries} 次失败: {last}")


def stores_of(tok, vid):
    d = get(f"https://api.waisongbang.com/saas/web_board/store_search?access_token={tok}"
            f"&keyword=&page=1&vendor_id={vid}&is_show_all_store=1&is_member=1")
    return [s for s in (d.get("obj") or {}).get("lists") or [] if str(s.get("id")) not in ("-1", "")]


def ledger(tok, vid, sid, max_pages=30):
    """拉某门店的库存流水（倒序），返回 [(ts, sku, pre_stock, stock)]"""
    out, page = [], 1
    while page <= max_pages:
        u = ("https://product-service.waisongbang.com/api/storeProduct/stockDetail?"
             + urllib.parse.urlencode({"vendor_id": vid, "store_id": sid, "page": page,
                                       "pageSize": 100, "access_token": tok}))
        o = get(u).get("obj") or {}
        for r in o.get("lists") or []:
            ts = (r.get("created_at") or "")[:19]
            if not ts:
                continue
            out.append((ts, str(r.get("sku_id") or ""), int(r.get("pre_stock") or 0), int(r.get("stock") or 0)))
        if o.get("isLastPage") or not (o.get("lists") or []):
            break
        page += 1
    return out


def main():
    tok = cloud_auth.js_token()
    js = json.load(open(os.path.join(OUT_DIR, "jieshun.json"), encoding="utf-8"))
    today = date.today()
    prev = {"days": [], "bySku": {}}
    if os.path.exists(HIST):
        try:
            prev = json.load(open(HIST, encoding="utf-8"))
        except Exception:
            pass
    keep_from = today - timedelta(days=KEEP_DAYS - 1)

    # 1) 当前库存（街顺快照，作为今天及无流水时的兜底）
    cur = {}            # (store, sku) -> stock
    for st in js.get("stores") or []:
        sid = str(st.get("id"))
        for k in st.get("skus") or []:
            cur[(sid, str(k.get("sku") or ""))] = int(k.get("stock") or 0)
    store_names = {str(st.get("id")): st.get("name") for st in js.get("stores") or []}

    # 2) 各门店库存流水 → 每店每 SKU 的按日收盘库存
    #    daily[date_str][sku] = 有库存的门店数
    daily = {}
    filled_by_ledger = 0
    for vid in VENDORS:
        try:
            stores = stores_of(tok, vid)
        except Exception as e:
            print(f"⚠️ vendor {vid} 门店列表失败：{str(e)[:80]}，跳过流水回填")
            continue
        for s in stores:
            sid = str(s.get("id"))
            if sid not in store_names:          # 只统计看板里的门店（排除中心仓等）
                continue
            try:
                recs = ledger(tok, vid, sid)
            except Exception as e:
                print(f"⚠️ {s.get('name')} 流水拉取失败：{str(e)[:60]}")
                continue
            if recs:
                filled_by_ledger += 1
            by_sku = {}
            for ts, sku, pre, after in recs:
                by_sku.setdefault(sku, []).append((ts, pre, after))
            for sku, arr in by_sku.items():
                arr.sort()
                first_ts = arr[0][0]
                for d in range(1, RECOMPUTE_DAYS):        # 今天用快照，流水只回填历史
                    dd = today - timedelta(days=d)
                    ds = dd.isoformat()
                    if dd < keep_from:
                        break
                    st_after = None
                    for ts, pre, after in reversed(arr):     # 该日最后一条流水的 after
                        if ts[:10] <= ds:
                            st_after = after
                            break
                    if st_after is None:
                        if ds < first_ts[:10]:
                            st_after = arr[0][1]             # 首条流水的 pre_stock
                        else:
                            st_after = arr[-1][2]
                    if st_after > 0:
                        daily.setdefault(ds, {})
                        daily[ds][sku] = daily[ds].get(sku, 0) + 1
            # 该店其余 SKU（窗口内没有任何流水）：视为库存未变，按当前库存计
            for (s2, sku), stock in cur.items():
                if s2 != sid or sku in by_sku:
                    continue
                if stock <= 0:
                    continue
                for d in range(1, RECOMPUTE_DAYS):        # 同上：不含今天
                    dd = today - timedelta(days=d)
                    if dd < keep_from:
                        break
                    ds = dd.isoformat()
                    daily.setdefault(ds, {})
                    daily[ds][sku] = daily[ds].get(sku, 0) + 1

    # 3) 今天用当前库存快照（比流水更准，且覆盖没有流水的门店）
    ds_today = today.isoformat()
    for (sid, sku), stock in cur.items():
        if stock > 0:
            daily.setdefault(ds_today, {})
            daily[ds_today][sku] = daily[ds_today].get(sku, 0) + 1

    # 4) 合并进历史：老日期保留，最近 RECOMPUTE_DAYS 天用新算的覆盖
    days = [d for d in prev.get("days") or [] if d >= keep_from.isoformat()]
    by_sku = {k: list(v) for k, v in (prev.get("bySku") or {}).items()}
    new_days = sorted(set(days) | set(daily.keys()))
    all_sku = set(by_sku.keys()) | {k for v in daily.values() for k in v}
    merged = {}
    for sku in all_sku:
        vals = []
        for ds in new_days:
            if ds in daily:                                  # 本次重算过的天
                vals.append(daily[ds].get(sku, 0))
            else:                                            # 沿用历史
                if ds in days:
                    i = days.index(ds)
                    old = by_sku.get(sku) or []
                    vals.append(old[i] if i < len(old) else 0)
                else:
                    vals.append(0)
        merged[sku] = vals

    out = {"updated": ds_today, "days": new_days, "bySku": merged}
    json.dump(out, open(HIST, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    cov = len(new_days)
    instock_today = sum(1 for v in daily.get(ds_today, {}).values() if v > 0)
    print(f"✅ stock_hist.json · 覆盖 {cov} 天（{new_days[0] if new_days else '-'} ~ {new_days[-1] if new_days else '-'}）"
          f" · 今日在售 SKU {instock_today} · 流水回填门店 {filled_by_ledger}")


if __name__ == "__main__":
    main()
