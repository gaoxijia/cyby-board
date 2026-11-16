#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
云端一键刷新编排：登录采集 → 建板 → 输出关键数字
退出码：0 成功 | 10 街顺 token 失效（需短信续期） | 20 ERP 登录失败 | 1 其他错误
"""
import json
import os
import shutil
import subprocess
import sys

DIR = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable


def run(script):
    print(f"\n===== {script} =====", flush=True)
    try:
        r = subprocess.run([PY, os.path.join(DIR, script)], capture_output=True, text=True, timeout=1800)
    except subprocess.TimeoutExpired:
        print(f"❌ {script} 超过 30 分钟仍未完成（接口疑似挂死），中止本轮不发布", flush=True)
        sys.exit(1)
    tail = (r.stdout or "").strip().splitlines()[-6:]
    for line in tail:
        print(line, flush=True)
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()[-500:]
        print(f"❌ {script} 失败: {err}", flush=True)
        if "access_token 已失效" in err or "token 已失效" in err or "街顺" in err and "失败" in err:
            sys.exit(10)
        if "ERP 登录失败" in err or "登录失败" in err:
            sys.exit(20)
        sys.exit(1)


def main():
    run("fetch_jieshun.py")
    run("fetch_platform.py")
    run("fetch_cat_platform.py")
    run("fetch_erp_periods.py")
    run("fetch_erp_channel.py")
    run("fetch_erp_daily.py")
    run("fetch_recent.py")
    # 新鲜度闸门：街顺快照必须是今天，否则不建板/不发布（防止旧库存被当成最新推出去）
    js_chk = json.load(open(os.path.join(DIR, "jieshun.json"), encoding="utf-8"))
    today = __import__("datetime").date.today().isoformat()
    if (js_chk.get("generated_at") or "")[:10] != today:
        print(f"❌ 街顺快照是 {js_chk.get('generated_at')}，不是今天（{today}），中止不发布", flush=True)
        sys.exit(1)
    run("build_v2.py")
    dist = os.path.join(DIR, "dist")
    os.makedirs(dist, exist_ok=True)
    shutil.copy(os.path.join(DIR, "index.html"), os.path.join(dist, "index.html"))

    # 汇总关键数字
    js = json.load(open(os.path.join(DIR, "jieshun.json"), encoding="utf-8"))
    pe = json.load(open(os.path.join(DIR, "erp_periods.json"), encoding="utf-8"))
    stock = sum(k["stock"] for st in js["stores"] for k in st["skus"])
    g30 = pe["p30"]["summary"]["gmv"]
    o30 = pe["p30"]["summary"]["order_count"]
    extra = ""
    pf_path = os.path.join(DIR, "platform_daily.json")
    if os.path.exists(pf_path):
        pf = json.load(open(pf_path, encoding="utf-8"))
        pg = sum(v["gmv"] for v in pf["monthly"].values())
        po = sum(v["orders"] for v in pf["monthly"].values())
        extra = f" · 11月绩效(1~{pf['days']}日) 订单 {po:,} / 交易额 {pg:,.0f}"
    cf_path = os.path.join(DIR, "cat_platform.json")
    if os.path.exists(cf_path):
        cf = json.load(open(cf_path, encoding="utf-8"))
        extra += f" · 品类×平台30天 {cf['plats']['全部']['qty']:,}件（剔除赠品/打包袋 {cf['excluded']['qty']}件）"
    print(f"\nREFRESH_OK 采集日 {js['date']} · 街顺库存 {stock:,} 件 · 近30天 GMV {g30:,.0f} · 订单 {o30:,}{extra}")


if __name__ == "__main__":
    main()
