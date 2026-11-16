#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
街顺（外卖邦）全量库存/销量采集：17 家云桂门店 × SKU
接口：product-service /api/storeProduct/list（按门店 flag=all）
鉴权：access_token 直连（secrets.json，短信登录一次 14 天有效），无浏览器依赖
字段：stock / s3 / s7 / s14 / s30（近3/7/14/30天销量）/ day_avg_sale（日均）/
     retail_price / status（上下架）/ location_code（货架号）
输出：jieshun.json
"""
import json
import time
import os
import urllib.request
from datetime import date, datetime

import cloud_auth

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
VENDORS = {6168: "图拉斯-云南1", 6305: "图拉斯-广西1"}
H = {"Referer": "https://s.waisongbang.com/", "User-Agent": "Mozilla/5.0"}


def get(u, tries=4):
    """带重试的 GET：网络读超时/连接断开时退避重试，避免整轮刷新被一次抖动打断"""
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(u, headers=H)
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError:
            raise
        except Exception as e:            # TimeoutError / URLError / 分块传输中断
            last = e
            if i < tries - 1:
                time.sleep(2 * (i + 1))
    raise RuntimeError(f"街顺接口连续 {tries} 次失败: {last}")


def main():
    tok = cloud_auth.js_token()

    out = {"date": date.today().isoformat(),
           "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
           "source": "街顺 API · storeProduct/list", "stores": []}
    for vid, vname in VENDORS.items():
        u = (f"https://api.waisongbang.com/saas/web_board/store_search?access_token={tok}"
             f"&keyword=&page=1&vendor_id={vid}&is_show_all_store=1&is_member=1")
        try:
            d = get(u)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise SystemExit("街顺 access_token 已失效，请短信验证码重新登录后更新 secrets.json 的 js_token")
            raise
        body = d.get("obj") or {}
        if not (body.get("lists") or []):
            # token 失效时部分接口返回 200 但空数据，再探测一次
            raise SystemExit(f"街顺返回空门店列表（vendor {vid}），大概率 token 已失效，请重新登录更新 secrets.json")
        stores = [s for s in body.get("lists") or [] if str(s.get("id")) not in ("-1", "")]
        print(f"{vname}: {len(stores)} 家门店")
        for s in stores:
            sid = str(s["id"])
            page, skus = 1, []
            while True:
                u = (f"https://product-service.waisongbang.com/api/storeProduct/list?access_token={tok}"
                     f"&vendor_id={vid}&store_id={sid}&flag=all&page={page}&pageSize=50")
                o = get(u).get("obj") or {}
                for g in o.get("lists") or []:
                    on = g.get("status") == 1
                    pname = g.get("name") or ""
                    for k in g.get("skus") or []:
                        spec = k.get("name") or ""
                        skus.append({
                            "sku": k.get("sku_id") or "",
                            "name": (pname + (" " + spec if spec and spec not in pname else "")).strip(),
                            "upc": (k.get("upc") or "").strip(),
                            "upcs": [u for u in (k.get("upcs") or []) if u],
                            "stock": int(k.get("stock") or 0),
                            "s3": int(round(float(k.get("day_avg_sale") or 0) * 3)),
                            "s7": int(k.get("weeks_sale") or 0),
                            "s14": int(k.get("fourteen_day_sale") or 0),
                            "s30": int(k.get("thirty_day_sale") or 0),
                            "dag": round(float(k.get("day_avg_sale") or 0), 3),
                            "price": float(k.get("retail_price") or 0),
                            "on": 1 if on else 0,
                            "loc": k.get("location_code") or "",
                        })
                if o.get("isLastPage") or not (o.get("lists") or []):
                    break
                page += 1
            out["stores"].append({"id": sid, "name": s.get("name"), "vendor": vname, "skus": skus})
            print(f"  {s.get('name')}: {len(skus)} SKU")

    tot_s = sum(k["stock"] for st in out["stores"] for k in st["skus"])
    tot_q = {n: sum(k[f"s{n}"] for st in out["stores"] for k in st["skus"]) for n in (3, 7, 14, 30)}
    if tot_s == 0 and tot_q[30] == 0:
        raise SystemExit("街顺采集异常：17 店全部 0 SKU/0 库存（接口可能变更或返回空），中止，不写文件不发布")

    path = os.path.join(OUT_DIR, "jieshun.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"\n✅ {path} · {len(out['stores'])} 店 · 库存 {tot_s:,} 件")
    print(f"   近3天 {tot_q[3]:,} | 近7天 {tot_q[7]:,} | 近14天 {tot_q[14]:,} | 30天 {tot_q[30]:,}")


if __name__ == "__main__":
    main()
