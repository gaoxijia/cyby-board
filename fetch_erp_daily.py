#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
星火 ERP 每日数据立方（供看板「自定义日期区间」前端聚合）
说明：ERP 的 grain=day 只影响趋势序列、不能按天返回明细行，
      因此按「一天一次 × 两个维度」逐日拉取：groupBy=store（门店 GMV/订单/件数）、
      groupBy=channel（渠道 GMV），拼成日期对齐的数组。
输出：erp_daily.json
  {"from","to","dates":[...],
   "stores":{sid:{"n":名称,"g":[gmv...],"o":[订单...],"q":[件数...]}},
   "channels":{渠道:{"g":[gmv...]}}}
"""
import json
import time
import os
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
TOKENS = os.path.join(OUT_DIR, "tokens.json")
API = "https://erp.torras.work/api/bi/query"
HOST = "https://erp.torras.work"
KEEP = ["5157", "5159"]
DAYS = 120                                  # 立方窗口：近 120 天（够自定义区间 + 上期对比）


# ---------- token：文件优先，失效则回退浏览器 ----------
def _load_tok():
    try:
        return json.load(open(TOKENS, encoding="utf-8")).get("erp_token") or ""
    except Exception:
        return ""


def _save_tok(tok):
    old = {}
    if os.path.exists(TOKENS):
        try:
            old = json.load(open(TOKENS, encoding="utf-8"))
        except Exception:
            pass
    old["erp_token"] = tok
    from datetime import datetime
    old["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    json.dump(old, open(TOKENS, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def _browser_tok():
    """账号密码登录换新 token（纯 HTTP，云端可用）"""
    import cloud_auth
    return cloud_auth.erp_token()


def call(pl, tok):
    """带重试：ERP 偶发 500 / 网络抖动时退避重试，避免云端无人值守时整轮失败"""
    last = None
    for i in range(4):
        try:
            req = urllib.request.Request(API, data=json.dumps(pl).encode(), headers={
                "Authorization": "Bearer " + tok, "Content-Type": "application/json",
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


def main():
    tok = _load_tok()
    probe = base("sales", "channel", "2026-11-04", "2026-11-04")
    try:
        if not tok:
            raise RuntimeError("no token")
        call(probe, tok)
        print("token: 复用 tokens.json")
    except Exception as e:
        print(f"token 失效（{type(e).__name__}），账号密码重新登录…")
        tok = _browser_tok()
        assert tok, "ERP 账号密码登录失败，请检查 secrets.json"
        _save_tok(tok)
        call(probe, tok)

    end = date.today() - timedelta(days=1)
    win_start = end - timedelta(days=DAYS - 1)
    # 先用 overview 趋势定位「数据实际起点」，避免对空数据日发请求
    ov = call(base("overview", "partner", win_start.isoformat(), end.isoformat()), tok)
    tr = [x.get("date") for x in (ov.get("trend") or []) if x.get("period") == "current" and x.get("date")]
    data_start = date.fromisoformat(min(tr)) if tr else win_start
    start = max(win_start, data_start)
    dates = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    print(f"数据起点 {data_start} · 实际拉取 {dates[0]} ~ {dates[-1]}（{len(dates)} 天）")
    N = len(dates)
    pos = {d: i for i, d in enumerate(dates)}
    stores, channels = {}, {}
    lock_err = []

    def one(d):
        """拉一天的门店 + 渠道数据"""
        try:
            ds = call(base("sales", "store", d, d), tok).get("table", {}).get("items") or []
            dc = call(base("sales", "channel", d, d), tok).get("table", {}).get("items") or []
            return d, ds, dc
        except Exception as e:
            lock_err.append(f"{d}: {e}")
            return d, [], []

    with ThreadPoolExecutor(max_workers=8) as ex:
        for d, ds, dc in ex.map(one, dates):
            i = pos[d]
            for r in ds:
                cur = r.get("current") or {}
                sid = str(r.get("dimension_id") or "")
                if not sid:
                    continue
                s = stores.setdefault(sid, {"n": r.get("dimension_name") or sid,
                                            "g": [0.0] * N, "o": [0] * N, "q": [0] * N})
                s["g"][i] = round(cur.get("gmv") or 0, 2)
                s["o"][i] = int(cur.get("order_count") or 0)
                s["q"][i] = int(cur.get("sales_quantity") or 0)
            for r in dc:
                ch = r.get("dimension_name") or "未知"
                c = channels.setdefault(ch, {"g": [0.0] * N, "o": [0] * N})
                cur = r.get("current") or {}
                c["g"][i] = round(cur.get("gmv") or 0, 2)
                c["o"][i] = int(cur.get("order_count") or 0)

    if lock_err:
        print(f"⚠️ {len(lock_err)} 天拉取失败，示例：{lock_err[:2]}")

    out = {"from": dates[0], "to": dates[-1], "dates": dates, "dataFrom": data_start.isoformat(),
           "stores": stores, "channels": channels}
    path = os.path.join(OUT_DIR, "erp_daily.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))

    g = sum(sum(v["g"]) for v in stores.values())
    o = sum(sum(v["o"]) for v in stores.values())
    q = sum(sum(v["q"]) for v in stores.values())
    print(f"✅ {path} · {dates[0]} ~ {dates[-1]}（{N} 天）")
    print(f"   门店 {len(stores)} · 渠道 {len(channels)} · 区间 GMV {g:,.0f} · 订单 {o:,} · 件数 {q:,}")


if __name__ == "__main__":
    main()
