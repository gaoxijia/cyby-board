#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_cwhub.py — 用线上已采集 payload + 新模板生成 dist/index.html（不重新采集）"""
import json, os, re

ROOT = os.path.dirname(os.path.abspath(__file__))
pub = open("/tmp/pub_board.html", encoding="utf-8").read()
m = re.search(r"const D = (\{.*?\});\nwindow\.D = D;", pub, re.S)
assert m, "payload not found in published html"
payload = json.loads(m.group(1))

# 注入规格名称（sku_spec.json 由 fetch_spec.py 生成）
spec_path = os.path.join(ROOT, "sku_spec.json")
if os.path.exists(spec_path):
    sp = json.load(open(spec_path, encoding="utf-8"))
    n = sum(1 for k in payload["skus"] if sp.get(k))
    for k, v in payload["skus"].items():
        v["spec"] = sp.get(k, "")
    print(f"SPEC 注入: {n}/{len(payload['skus'])} SKU 有规格")
else:
    for k, v in payload["skus"].items():
        v["spec"] = ""

tpl = open(os.path.join(ROOT, "template_v2.html"), encoding="utf-8").read()
assert "/*__PAYLOAD__*/" in tpl
blob = "const D = " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + ";\nwindow.D = D;"
out = tpl.replace("/*__PAYLOAD__*/", blob, 1)

os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
dst = os.path.join(ROOT, "dist", "index.html")
open(dst, "w", encoding="utf-8").write(out)
print(f"BUILD_OK {os.path.getsize(dst)/1024/1024:.2f} MB · gen={payload['meta'].get('gen')} jsDate={payload['meta'].get('jsDate')}")
