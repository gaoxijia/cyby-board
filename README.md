# 出其不意经营看板 · 自动构建

本仓库由 GitHub Actions 每日 07:30（北京时间）自动执行：

1. 采集街顺（外卖邦）17 店全量库存/销量 + 星火 ERP 各平台经营数据
2. `build_v2.py` 注入 `template_v2.html` 生成 `dist/index.html`
3. 自动部署到 GitHub Pages：https://gaoxijia.github.io/cyby-board/

凭据通过 Actions Secret `BOARD_SECRETS_JSON` 在运行时写入 `secrets.json`，**不入库**。

- 手动触发：Actions → board-refresh → Run workflow
- 退出码 10 = 街顺 access_token 过期（需短信验证码重新登录后更新 Secret）
- 退出码 20 = ERP 账号密码登录失败
