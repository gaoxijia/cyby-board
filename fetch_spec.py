#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
街顺 SKU 规格采集：按 vendor 拉全部门店商品，输出 sku -> 规格名(k.name) 映射
用途：中心仓明细表「规格名称」列（源数据规格此前只拼进全名、未单独保存）
输出：sku_spec.json  {sku: "规格名", ...}（空规格不记）
"""
import json, os, time, urllib.request
import cloud_auth

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
VENDORS = {6168: "云南", 6305: "广西"}
H = {"Referer": "https://s.waisongbang.com/", "User-Agent": "Mozilla/5.0"}


def get(u, tries=4):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(u, headers=H)
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError:
            raise
        except Exception as e:
            last = e
            if i < tries - 1:
                time.sleep(2 * (i + 1))
    raise RuntimeError(f"街顺接口连续 {tries} 次失败: {last}")


def main():
    tok = cloud_auth.js_token()
    spec = {}
    for vid, vname in VENDORS.items():
        u = (f"https://api.waisongbang.com/saas/web_board/store_search?access_token={tok}"
             f"&keyword=&page=1&vendor_id={vid}&is_show_all_store=1&is_member=1")
        d = get(u)
        stores = [s for s in (d.get("obj") or {}).get("lists") or [] if str(s.get("id")) not in ("-1", "")]
        print(f"{vname}: {len(stores)} 家门店")
        page_cache = {}
        for s in stores:
            sid = str(s["id"])
            page = 1
            while True:
                key = (vid, page)
                if key not in page_cache:
                    u2 = (f"https://product-service.waisongbang.com/api/storeProduct/list?access_token={tok}"
                          f"&vendor_id={vid}&store_id={sid}&flag=all&page={page}&pageSize=100")
                    o = get(u2).get("obj") or {}
                    page_cache[key] = o
                    time.sleep(0.3)
                o = page_cache[key]
                for g in o.get("lists") or []:
                    for k in g.get("skus") or []:
                        sp = (k.get("name") or "").strip()
                        sku = k.get("sku_id") or ""
                        if sku and sp and sku not in spec:
                            spec[sku] = sp
                if o.get("isLastPage") or not (o.get("lists") or []):
                    break
                page += 1
            print(f"  {s.get('name')}: 累计 {len(spec)}")
    json.dump(spec, open(os.path.join(OUT_DIR, "sku_spec.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=0)
    print(f"sku_spec.json 写入完成，共 {len(spec)} 个 SKU 规格")


if __name__ == "__main__":
    main()
