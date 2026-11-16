#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
品类/动销 × 平台 × 时间段 采集（街顺 product-analysis 服务）
逐日 × 双 vendor 分页拉取 storeSku/list（深分页排序不稳定，单日拉取稳定），
聚合 90 天「SKU×平台×日」稀疏立方（支持前端任意时间段重算），
另聚合最近 30 天窗口供默认视图（plats），剔除赠品/打包袋，按关键词归类品类。
输出 cat_platform.json：plats(30天汇总) + cube(90天逐日稀疏立方) + excluded。
"""
import json, os, re, sys, time, datetime, urllib.request, urllib.parse, ssl

HERE = os.path.dirname(os.path.abspath(__file__))
ssl._create_default_https_context = ssl._create_unverified_context
BASE = "https://product-analysis.waisongbang.com/api/productAnalysis"
VENDORS = [6168, 6305]
PAGE_SIZE = 100
DAYS = 90                     # 立方覆盖天数（支持「近90个完整日」及自定义区间）
DEF_DAYS = 30                 # 默认视图（plats 汇总）窗口
EX_KW = ("赠", "打包袋", "包装袋")          # 剔除：赠品/打包袋/包装袋
PLAT_MAP = {1: "淘宝", 4: "淘宝", 3: "美团", 7: "美团", 6: "京东"}
PLAT_CODE = {"淘宝": 1, "美团": 3, "京东": 6}   # 其他 → 0

def cat_of(n):
    s = (n or "").replace(" ", "")
    if re.search(r"充电宝|移动电源", s): return "充电宝"
    if re.search(r"充电器|充电头|插头", s): return "充电器"
    if re.search(r"数据线|充电线|编织线|快充线|线", s): return "数据线"
    if re.search(r"壳|保护套|皮套", s): return "手机壳"
    if re.search(r"膜|贴", s): return "手机膜"
    return "其他"

def get(params):
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{BASE}/storeSku/list?{qs}",
                                 headers={"Authorization": f"Bearer {tok}"})
    for attempt in range(3):
        try:
            return json.loads(urllib.request.urlopen(req, timeout=25).read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(1.5 * (attempt + 1)); continue
            print(f"[!] HTTP {e.code}: {params}", file=sys.stderr)
            return None
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return None

def day_rows(vid, day):
    """单日全部分页（有销量行集中在按 valid_sales_qty 降序的前几页）"""
    out, page = [], 1
    while page <= 15:
        d = get({"page": page, "page_size": PAGE_SIZE, "vendor_id": vid,
                 "begin_date": day, "end_date": day})
        if d is None: return None
        rs = (d.get("data") or {}).get("lists") or []
        out.extend(rs)
        if len(rs) < PAGE_SIZE: break
        if all((x.get("sales_qty") or 0) == 0 for x in rs): break
        page += 1
        time.sleep(0.12)
    return out

def main():
    global tok
    sec = json.load(open(os.path.join(HERE, "secrets.json"), encoding="utf-8"))
    tok = sec.get("js_token", "")
    if not tok:
        print("[!] secrets.json 缺 js_token", file=sys.stderr); sys.exit(10)

    today = datetime.date.today()
    to = today - datetime.timedelta(days=1)
    frm = to - datetime.timedelta(days=DAYS - 1)
    days = [(frm + datetime.timedelta(days=i)).isoformat() for i in range(DAYS)]
    def_days = set(days[DAYS - DEF_DAYS:])          # 最近30天（默认视图窗口）

    cube = {}          # (sku, plat) -> {"name","cat","days": {dayIso: [q, g, set(store_ids)]}}
    excluded = {}      # name -> [qty, gmv]（最近30天窗口，脚注展示）
    plat_unknown = 0
    t0 = time.time()
    for di, day in enumerate(days):
        for vid in VENDORS:
            rows = day_rows(vid, day)
            if rows is None:
                print(f"[!] {day} vendor{vid} 拉取失败，跳过", file=sys.stderr); continue
            for x in rows:
                q = x.get("sales_qty") or 0
                if q <= 0: continue
                name = x.get("product_name") or ""
                g = x.get("gmv_valid") or 0
                if any(w in name for w in EX_KW):
                    if day in def_days:
                        e = excluded.setdefault(name, [0, 0])
                        e[0] += q; e[1] += g
                    continue
                plat = PLAT_MAP.get(x.get("platform"), "其他")
                if plat == "其他": plat_unknown += q
                key = (x.get("sku") or x.get("spu") or name, plat)
                a = cube.setdefault(key, {"name": name, "cat": cat_of(name), "days": {}})
                cell = a["days"].setdefault(day, [0, 0.0, set()])
                cell[0] += q; cell[1] += g
                if x.get("store_id"): cell[2].add(str(x["store_id"]))
        el = time.time() - t0
        cells = sum(len(v["days"]) for v in cube.values())
        print(f"  [{di+1}/{DAYS}] {day} done, cells={cells}, {el:.0f}s", file=sys.stderr)

    # ---- 门店位映射（位序 = store_ids 列表下标）----
    store_ids = sorted({sid for a in cube.values() for c in a["days"].values() for sid in c[2]})
    bit_of = {sid: i for i, sid in enumerate(store_ids)}

    # ---- 90 天稀疏立方 ----
    skus, sku_idx = [], {}                     # [sku, name, cat]
    recs = []                                  # [skuIdx, platCode, [[dayIdx,q,g,mask], ...]]
    for (sku, plat), a in sorted(cube.items()):
        si = sku_idx.get(sku)
        if si is None:
            si = len(skus); sku_idx[sku] = si
            skus.append([sku, a["name"][:60], a["cat"]])
        pc = PLAT_CODE.get(plat, 0)
        day_cells = []
        for di, day in enumerate(days):
            c = a["days"].get(day)
            if not c: continue
            mask = 0
            for sid in c[2]: mask |= 1 << bit_of[sid]
            day_cells.append([di, c[0], round(c[1], 2), mask])
        recs.append([si, pc, day_cells])

    # ---- 最近30天汇总（默认视图，与原口径一致）----
    def pack(rows30):
        cats = {}
        for a in rows30:
            c = cats.setdefault(a["cat"], {"c": a["cat"], "q": 0, "g": 0.0, "n": set()})
            c["q"] += a["q"]; c["g"] += a["g"]; c["n"].add(a["sku"])
        out = []
        for c in cats.values():
            c["n"] = len(c["n"]); out.append(c)
        out.sort(key=lambda x: -x["q"])
        tq = sum(c["q"] for c in out); tg = sum(c["g"] for c in out)
        sku_rows = [[a["sku"], a["name"][:60], a["q"], round(a["g"], 2), len(a["st"])]
                    for a in rows30]
        sku_rows.sort(key=lambda x: -x[2])
        return {"qty": tq, "gmv": round(tg, 2), "skus": sum(c["n"] for c in out),
                "cats": out, "rows": sku_rows}

    d30_from = days[DAYS - DEF_DAYS]
    agg30 = {}                                 # (sku, plat) -> 30天汇总
    for (sku, plat), a in cube.items():
        q = sum(c[0] for d, c in a["days"].items() if d in def_days)
        if q <= 0: continue
        g = sum(c[1] for d, c in a["days"].items() if d in def_days)
        st = set()
        for d, c in a["days"].items():
            if d in def_days: st |= c[2]
        agg30[(sku, plat)] = {"sku": sku, "plat": plat, "name": a["name"],
                              "cat": a["cat"], "q": q, "g": g, "st": st}
    rows30 = list(agg30.values())
    plats = {"全部": pack(rows30)}
    for p in ["美团", "淘宝", "京东", "其他"]:
        plats[p] = pack([a for a in rows30 if a["plat"] == p])

    top_ex = sorted(excluded.items(), key=lambda kv: -kv[1][0])[:10]
    payload = {
        "from": d30_from, "to": to.isoformat(), "days": DEF_DAYS,
        "gen": datetime.datetime.now().isoformat(timespec="seconds"),
        "excluded": {"qty": sum(v[0] for v in excluded.values()),
                     "gmv": round(sum(v[1] for v in excluded.values()), 2),
                     "top": [{"n": n[:40], "q": v[0], "g": round(v[1], 2)} for n, v in top_ex]},
        "plat_unknown_qty": plat_unknown,
        "plats": plats,
        "cube": {
            "dates": days,
            "stores": store_ids,
            "skus": skus,
            "recs": recs,
        },
    }
    with open(os.path.join(HERE, "cat_platform.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    print("== 品类×平台 30天汇总 ==")
    for p, d in plats.items():
        if d["qty"] or p == "全部":
            top = ", ".join(f"{c['c']} {c['q']}" for c in d["cats"][:5])
            print(f"  {p}: {d['qty']}件 / ¥{d['gmv']:.0f} / {d['skus']}SKU | {top}")
    print(f"  剔除赠品/打包袋(30天): {payload['excluded']['qty']}件 / ¥{payload['excluded']['gmv']:.0f}"
          f"（{len(excluded)}种）")
    print(f"== 立方: {DAYS}天 × {len(skus)} SKU × {len(store_ids)} 门店, "
          f"recs={len(recs)}, json={os.path.getsize(os.path.join(HERE,'cat_platform.json'))//1024}KB ==")

if __name__ == "__main__":
    main()
