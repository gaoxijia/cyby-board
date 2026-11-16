#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
公共鉴权模块（纯 HTTP，无浏览器依赖，云端沙箱可用）
- erp_token(): 用 secrets.json 里的账号密码登录星火 ERP 换新 token（2小时有效）
- js_token():  读街顺 access_token（短信登录一次，14天有效；到期需人工更新 secrets.json）
"""
import json
import os
import urllib.request

_DIR = os.path.dirname(os.path.abspath(__file__))
SECRETS = os.path.join(_DIR, "secrets.json")


def _load():
    return json.load(open(SECRETS, encoding="utf-8"))


def erp_token():
    """账号密码登录 ERP，返回新 token"""
    s = _load()
    req = urllib.request.Request(
        "https://erp.torras.work/api/auth/login",
        data=json.dumps({"username": s["erp_user"], "password": s["erp_pass"]}).encode(),
        method="POST",
        headers={"Content-Type": "application/json",
                 "Origin": "https://erp.torras.work", "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.loads(r.read().decode())
    tok = d.get("token") or ""
    if not tok:
        raise SystemExit(f"ERP 登录失败：{str(d)[:200]}")
    return tok


def js_token():
    """读街顺 access_token；不校验有效期（调用方遇到 401 自行报错提示续期）"""
    return _load()["js_token"]
