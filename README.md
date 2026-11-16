# 出其不意经营看板 · 自动构建

本仓库由 GitHub Actions 每日 07:30（北京时间）自动执行：

1. 采集街顺（外卖邦）19 店全量库存/销量 + 星火 ERP 各平台经营数据
2. `build_v2.py` 注入 `template_v2.html` 生成 `dist/index.html`
3. 自动部署到 GitHub Pages：https://gaoxijia.github.io/cyby-board/

凭据通过 Actions Secret `BOARD_SECRETS_JSON` 在运行时写入 `secrets.json`，**不入库**。

- 手动触发：Actions → board-refresh → Run workflow
- 退出码 10 = 街顺 access_token 过期（需短信验证码重新登录后更新 Secret）
- 退出码 20 = ERP 账号密码登录失败

## 推广费 / 费率比（ad_spend.json）

看板「渠道结构」卡片里的 `推广费`、`广告成交`、`费率比` 来自各平台广告后台，
**不在云端采集**，由本机脚本采集后提交入库：

```bash
# 前提：本机 Chrome 开着调试端口，且已登录 美团 KA / 京东 jzt（淘闪可选）
node collect_ad_spend.js /path/to/cyby-board/ad_spend.json
# 提交 → push 触发云端重建
```

口径：

| 平台 | 接口 | 推广费 | 交易额 |
|---|---|---|---|
| 美团 | `ka.waimai.meituan.com/.../history/list`（联合营销，按天汇总） | `cost`（分） | `arrangeOrdAmount`（分，广告归因成交额） |
| 京东 | `atoms-api.jd.com/reweb/common/index/indicator`（竞价广告） | `cost` | `totalOrderSum`（广告归因成交额） |
| 淘宝闪购 | 全站推广 + 斗金推广（待接入） | — | 全站交易额 |

`费率比 = 推广费 ÷ 该平台广告归因成交额`。注意分母是**广告归因成交额**（≈ 渠道 GMV 的一部分），
所以费率比会明显高于「推广费 ÷ 渠道 GMV」，两者不可混用。

为什么不能放云端跑：

- 美团接口有 WAF + 登录态，非浏览器环境直接 `403 Forbidden`（需要 `mtgsig` 浏览器签名）；
- 京东接口必须带 `loginMode: 0` / `siteId: 0` / `language: zh_CN` 三个自定义头，且依赖登录 cookie；
- 淘宝闪购走 mtop 网关，需要 `_m_h5_tk` 令牌 + 有效零售商家版会话。

`build_v2.py` 只读 `ad_spend.json`；文件缺失时看板对应位置显示「推广费 —」。

周期自动按「昨天」推算，与 ERP 的 p7 / p30 / p90 / mtd 对齐。
美团广告账户自 2026-10-21 起才有数据，早于此的周期会复用同一段（页面上会标注起始日）。
