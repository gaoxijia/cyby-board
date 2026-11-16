#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
组装 v2 看板数据并生成 index.html
数据源：
  · 库存 / 销量(近3·7·14·30天) / 日均 / 上下架 = 街顺 API（jieshun.json）
  · GMV / 订单 / 渠道 / 趋势 = 星火 ERP（erp_periods.json，4 个时间段）
  · 商品全名与品类标签 = ERP SKU 清单（erp_payload.json 的 skuAll）
输出：index.html（模板 template_v2.html，payload 注入 /*__PAYLOAD__*/）
"""
import json
import os
import re
from datetime import date

OUT = os.path.dirname(os.path.abspath(__file__))
js = json.load(open(os.path.join(OUT, "jieshun.json"), encoding="utf-8"))
ep = json.load(open(os.path.join(OUT, "erp_periods.json"), encoding="utf-8"))
# SKU 全名索引：优先读精简的 erp_sku_names.json（云端包只带这个，约 100KB）；
# 退化为历史大文件 erp_payload.json（2.2MB，仅本地存在）
_sku_names_path = os.path.join(OUT, "erp_sku_names.json")
if os.path.exists(_sku_names_path):
    erp_name = json.load(open(_sku_names_path, encoding="utf-8"))
else:
    old = json.load(open(os.path.join(OUT, "erp_payload.json"), encoding="utf-8"))
    erp_name = {r["id"]: r["name"] for r in old.get("skuAll", []) if r.get("id")}

CITY = ("昆明", "南宁", "柳州", "桂林")

# 非商品库存：赠品 / 打包袋 / 包装袋（不计入库存件数、库存金额、周转与补货）
EX_KW = ("赠", "打包袋", "包装袋", "购物袋", "手提袋")


def is_excluded(*names):
    return any(w in (n or "") for n in names for w in EX_KW)


def city_of(name):
    for c in CITY:
        if name.startswith(c):
            return c + "市"
    return "其他"


def vendor_of(v):
    return "云南" if "云南" in (v or "") else ("广西" if "广西" in (v or "") else v)


def cat_of(n):
    s = (n or "").replace(" ", "")
    if re.search(r"充电宝|移动电源", s):
        return "充电宝"
    if re.search(r"充电器|充电头|插头", s):
        return "充电器"
    if re.search(r"数据线|充电线|编织线|快充线|线", s):
        return "数据线"
    if re.search(r"壳|保护套|皮套", s):
        return "手机壳"
    if re.search(r"膜|贴", s):
        return "手机膜"
    return "其他"


# ERP 全名 / 价格索引（erp_name 已在文件头部加载，见 erp_sku_names.json / erp_payload.json）

# 近3天实际销量（ERP 出库，街顺无该字段）
recent_path = os.path.join(OUT, "recent_qty.json")
recent = json.load(open(recent_path, encoding="utf-8")) if os.path.exists(recent_path) else {}

stores_meta, stores_data, skus_meta = [], {}, {}
# 规格名称：优先 jieshun.json 的 spec 字段，旧采集数据用 sku_spec.json（fetch_spec.py）兜底
_spec_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sku_spec.json")
spec_fallback = json.load(open(_spec_path, encoding="utf-8")) if os.path.exists(_spec_path) else {}
total_sku_rows = 0
excl = {"qty": 0, "amt": 0.0, "rows": 0, "stores": set(), "skus": {}}
for st in js["stores"]:
    sid = st["id"]
    rows, stock = [], 0
    s3 = s7 = s14 = s30 = q3 = 0
    dag = amt = dead = sold = 0.0
    for k in st["skus"]:
        name = erp_name.get(k["sku"]) or k["name"]
        price = k.get("price") or 0
        if is_excluded(k["name"], name):
            # 赠品/包装袋：不进库存、金额、销量与补货口径
            e = excl["skus"].setdefault(k["sku"], {"n": name[:30], "q": 0, "a": 0.0})
            e["q"] += k["stock"]
            e["a"] += k["stock"] * price
            excl["qty"] += k["stock"]
            excl["amt"] += k["stock"] * price
            excl["rows"] += 1
            excl["stores"].add(sid)
            continue
        q3row = ((recent.get(sid) or {}).get(k["sku"]) or {}).get("d3", 0) or 0
        rows.append([k["sku"], name, k["stock"], k["s3"], k["s7"], k["s14"], k["s30"],
                     round(k["dag"], 3), price, k["on"], k["loc"] or "", q3row,
                     k.get("upc") or "", ",".join(k.get("upcs") or [])])
        stock += k["stock"]
        q3 += q3row
        s3 += k["s3"]; s7 += k["s7"]; s14 += k["s14"]; s30 += k["s30"]
        dag += k["dag"]; amt += k["stock"] * price
        if k["s30"] == 0 and k["stock"] > 0:
            dead += k["stock"]
        if k["s30"] > 0:
            sold += 1
        if k["sku"] not in skus_meta:
            skus_meta[k["sku"]] = {"name": name, "cat": cat_of(name), "price": price,
                                   "spec": k.get("spec") or spec_fallback.get(k["sku"], "")}
    total_sku_rows += len(rows)
    stores_meta.append({"id": sid, "name": st["name"], "city": city_of(st["name"]),
                        "vendor": vendor_of(st["vendor"])})
    stores_data[sid] = {"stock": stock, "q3": q3, "s3": s3, "s7": s7, "s14": s14, "s30": s30,
                        "dag": round(dag, 3), "amt": round(amt, 2), "deadStock": int(dead),
                        "sold": int(sold), "skus": rows}

periods = {}
for key, p in ep.items():
    periods[key] = {
        "label": p["label"], "range": p["range"], "summary": p["summary"],
        "trend": p.get("trend") or [], "heatmap": p.get("heatmap") or [],
        "charts": p.get("charts") or {},
        "storeSales": [{"name": r["dimension_name"],
                        "gmv": (r.get("current") or {}).get("gmv") or 0,
                        "orders": (r.get("current") or {}).get("order_count") or 0,
                        "qty": (r.get("current") or {}).get("sales_quantity") or 0}
                       for r in p.get("storeSales") or []],
    }

ex_top = sorted(excl["skus"].items(), key=lambda kv: -kv[1]["q"])[:6]
excluded_meta = {
    "qty": int(excl["qty"]),
    "amt": round(excl["amt"], 2),
    "rows": int(excl["rows"]),
    "skuN": len(excl["skus"]),
    "storeN": len(excl["stores"]),
    "top": [{"n": v["n"], "q": int(v["q"]), "a": round(v["a"], 2)} for _, v in ex_top],
}

payload = {
    "meta": {"gen": date.today().isoformat(), "jsDate": js.get("generated_at", js.get("date")),
             "skuN": total_sku_rows, "stores": stores_meta,
             "excluded": excluded_meta,
             "cities": sorted({s["city"] for s in stores_meta})},
    "periods": periods,
    "stores": stores_data,
    "skus": skus_meta,
}

# 每日数据立方（自定义日期区间前端聚合用）
daily_path = os.path.join(OUT, "erp_daily.json")
if os.path.exists(daily_path):
    payload["daily"] = json.load(open(daily_path, encoding="utf-8"))

# 11月绩效：街顺平台维度每日订单/交易额
perf_path = os.path.join(OUT, "platform_daily.json")
if os.path.exists(perf_path):
    payload["perf"] = json.load(open(perf_path, encoding="utf-8"))

# 品类×平台 30天销量（商品分析服务，已剔除赠品/打包袋）
catpf_path = os.path.join(OUT, "cat_platform.json")
if os.path.exists(catpf_path):
    payload["catpf"] = json.load(open(catpf_path, encoding="utf-8"))

# 门店 × 平台（门店经营榜平台切换）：各时间段 · 美团/京东/淘宝闪购
sch_path = os.path.join(OUT, "erp_store_channel.json")
if os.path.exists(sch_path):
    payload["storeCh"] = json.load(open(sch_path, encoding="utf-8")).get("periods") or {}

# 推广费 / 费率比（三平台广告后台，本地浏览器采集后入库；云端只读不采）
# 美团、淘闪接口有登录态+风控限制，无法在 GitHub 云端调用；京东可云端刷新后覆盖
ad_path = os.path.join(OUT, "ad_spend.json")
if os.path.exists(ad_path):
    payload["ad"] = json.load(open(ad_path, encoding="utf-8"))

html = open(os.path.join(OUT, "template_v2.html"), encoding="utf-8").read()
blob = "const D = " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + \
       ";\nwindow.D = D;"
assert "/*__PAYLOAD__*/" in html
html = html.replace("/*__PAYLOAD__*/", blob, 1)

out = os.path.join(OUT, "index.html")
open(out, "w", encoding="utf-8").write(html)
print(f"✅ index.html {os.path.getsize(out)/1024/1024:.2f} MB")
print(f"   门店 {len(stores_meta)} · 店-SKU {total_sku_rows:,} · SKU {len(skus_meta)}")
print(f"   已剔除赠品/包装袋 {excluded_meta['qty']:,} 件 / ¥{excluded_meta['amt']:,.0f}"
      f"（{excluded_meta['rows']} 个店-SKU，{excluded_meta['storeN']} 家门店）")
for k, p in periods.items():
    print(f"   {p['label']}: GMV {p['summary']['gmv']:,.0f} · 订单 {p['summary']['order_count']}")
if "daily" in payload:
    dl = payload["daily"]
    print(f"   每日立方 {dl['from']} ~ {dl['to']}（{len(dl['dates'])} 天）· 门店 {len(dl['stores'])} · 渠道 {len(dl['channels'])}")
if "perf" in payload:
    pf = payload["perf"]
    print(f"   11月绩效 {pf['from']} ~ {pf['to']}（{pf['days']} 天）· " +
          " | ".join(f"{k} {v['orders']}单/{v['gmv']:,.0f}元" for k, v in pf["monthly"].items()))
if "catpf" in payload:
    cf = payload["catpf"]
    print(f"   品类×平台 {cf['from']} ~ {cf['to']} · " +
          " | ".join(f"{k} {v['qty']}件" for k, v in cf["plats"].items() if v["qty"]) +
          f" · 剔除赠品/打包袋 {cf['excluded']['qty']}件")
if "ad" in payload:
    ad = payload["ad"]
    print(f"   推广费 {str(ad.get('generated_at'))[:16]} · " +
          " | ".join(f"{k} " + ",".join(f"{pl} ¥{v['spend']:,.0f}/{v.get('rate')}%" for pl, v in p["platforms"].items())
                     for k, p in ad["periods"].items()))
