#!/usr/bin/env node
/**
 * collect_ad_spend.js —— 三平台推广费采集（本机运行，依赖已登录的 Chrome 调试端口）
 *
 * 为什么必须在本机跑：
 *   · 美团 KA（ka.waimai.meituan.com）接口有 WAF + 登录态，非浏览器环境直接 403；
 *   · 淘宝闪购（nr.ele.me）走 mtop 网关，需要 `_m_h5_tk` 令牌 + 浏览器会话；
 *   · 京东 jzt（atoms-api.jd.com）加 `loginMode/siteId` 头后可纯 HTTP，但 cookie 仍来自登录态。
 *   因此 GitHub Actions 里只“读” ad_spend.json，不负责采集。
 *
 * 用法：
 *   1) Chrome 以调试端口启动并登录美团 KA / 京东 jzt（淘闪可选）
 *   2) node collect_ad_spend.js            # 默认写到当前目录 ad_spend.json
 *   3) git add ad_spend.json && git commit -m "推广费 $(date +%F)" && git push   # 触发云端重建
 *
 * 输出结构：
 *   { generated_at, periods: { p7|p30|p90|mtd: { range, platforms: { 美团:{spend,trade,rate,src}, 京东:{...}, 淘宝闪购:{...} } } }, meta }
 *   费率比 = spend / trade * 100（各平台同口径：广告花费 ÷ 该平台归因成交额）
 */
const fs = require('fs');
const path = require('path');

const CDP = 'http://127.0.0.1:9222';
const OUT = path.resolve(process.argv.slice(2).filter(a => a.indexOf('--') !== 0)[0] || 'ad_spend.json');

/* ---------------- 周期推算：与 ERP 口径一致（end = 昨天） ---------------- */
const pad = n => String(n).padStart(2, '0');
const iso = d => d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
const shift = (d, n) => { const x = new Date(d.getTime()); x.setDate(x.getDate() + n); return x; };

const today = new Date();
const endD = shift(today, -1);
const PERIODS = {
  p7:  {from: iso(shift(endD, -6)),  to: iso(endD), label: '近7个完整日'},
  p30: {from: iso(shift(endD, -29)), to: iso(endD), label: '近30个完整日'},
  p90: {from: iso(shift(endD, -89)), to: iso(endD), label: '近90个完整日'},
  mtd: {from: iso(new Date(endD.getFullYear(), endD.getMonth(), 1)), to: iso(endD), label: '本月至昨日'},
};

/* ---------------- CDP 小工具 ---------------- */
async function attach(matchFn) {
  const tabs = await (await fetch(CDP + '/json/list')).json();
  const tab = tabs.find(t => (t.url || '').indexOf('http') === 0 && matchFn(t.url));
  if (!tab) throw new Error('未找到标签页：' + matchFn.toString());
  const WebSocket = require('ws');
  const ws = new WebSocket(tab.webSocketDebuggerUrl);
  let nid = 0; const pending = {};
  ws.on('message', m => { const o = JSON.parse(m.toString()); if (o.id && pending[o.id]) { pending[o.id](o); delete pending[o.id]; } });
  await new Promise((res, rej) => { ws.on('open', res); ws.on('error', rej); });
  const send = (method, params) => new Promise(res => { const i = ++nid; pending[i] = res; ws.send(JSON.stringify({id: i, method, params: params || {}})); });
  const ev = async expr => {
    const r = await send('Runtime.evaluate', {expression: expr, returnByValue: true, awaitPromise: true});
    if (r.result && r.result.exceptionDetails) throw new Error('EVAL: ' + JSON.stringify(r.result.exceptionDetails).slice(0, 200));
    return r.result && r.result.result ? r.result.result.value : null;
  };
  return {ws, ev, send};
}

const r2 = n => Math.round(n * 100) / 100;

/* ---------------- 美团：分段拉日明细，再按周期聚合（接口上限 61 天） ---------------- */
async function fetchMeituan() {
  const {ws, ev, send} = await attach(u => u.indexOf('https://ka.waimai.meituan.com/') === 0);
  // 先刷新页面：后台 tab 挂久后 fetch 包装器会异常（返回 TypeError / r.text 不存在），刷新即恢复
  await send('Page.enable');
  await send('Page.reload', {ignoreCache: true});
  await new Promise(r => setTimeout(r, 12000));
  const start = new Date(PERIODS.p90.from), stop = new Date(PERIODS.p90.to);
  let rows = [];
  for (let cur = start; cur <= stop;) {
    const chunkEnd = new Date(Math.min(shift(cur, 44).getTime(), stop.getTime()));
    const body = 'startDate=' + iso(cur).replace(/-/g, '') + '&endDate=' + iso(chunkEnd).replace(/-/g, '') +
      '&cityIds=-1&wmPoiIds=-1&dpPoiIds=-1&prodTypes=36%2C38%2C35&searchRange=0&platformType=-1&fundTypes=-1' +
      '&acctBizType=0&promotionType=1&pageSize=200&pageNum=1&queryType=1';
    const code = "(async function(){var r=await fetch('https://ka.waimai.meituan.com/kepler/datacenter/api/ka/data/v2/history/list'," +
      "{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'" + body + "',credentials:'include'});" +
      "var j; try { j = (r && typeof r.json === 'function') ? await r.json() : JSON.parse(await r.text()); } catch(e){ return JSON.stringify({err:'parse:'+e.message}); } if(!j||!j.data||!j.data.list) return JSON.stringify({err:(j&&j.msg)||'no-data'});" +
      "return JSON.stringify(j.data.list.map(function(x){return {d:x.time,cost:x.cost,amt:x.arrangeOrdAmount};}));})()";
    let part; try { part = JSON.parse(await ev(code)); } catch (e) { part = {err: String(e.message).slice(0, 120)}; }
    if (Array.isArray(part)) { rows = rows.concat(part); console.log('  美团', iso(cur), '~', iso(chunkEnd), part.length, '行'); }
    else console.log('  美团', iso(cur), '~', iso(chunkEnd), '失败', JSON.stringify(part).slice(0, 100));
    cur = shift(chunkEnd, 1);
  }
  ws.close();
  const dates = rows.map(x => x.d).sort();
  const agg = (f, t) => {
    let c = 0, a = 0;
    rows.forEach(x => { if (x.d >= f && x.d <= t) { c += x.cost || 0; a += x.amt || 0; } });
    return {spend: r2(c / 100), trade: r2(a / 100)};
  };
  return {agg, since: dates[0] || null, n: rows.length};
}

/* ---------------- 京东：每周期一次 indicator（必须带 loginMode/siteId/language 头） ---------------- */
async function fetchJd() {
  const {ws, ev} = await attach(u => u.indexOf('jzt.jd.com') >= 0);
  const one = async (f, t) => {
    const payload = {startDay: f + ' 00:00:00', endDay: t + ' 23:59:59', businessType: -4, mediaResourceType: null,
      interactiveType: null, clickOrOrderCaliber: 0, clickOrOrderDay: 15, giftFlag: 0, orderStatusCategory: 1,
      marketingScenarios: [], marketingTargetingTypes: []};
    const code = "(async function(){var r=await fetch('https://atoms-api.jd.com/reweb/common/index/indicator'," +
      "{method:'POST',headers:{'Content-Type':'application/json','loginMode':'0','siteId':'0','language':'zh_CN'," +
      "'Referer':'https://jzt.jd.com/'},credentials:'include',body:JSON.stringify(" + JSON.stringify(payload) + ")});" +
      "var j=await r.json(); return JSON.stringify(j.code===1?j.data:{err:j.msg});})()";
    try { return JSON.parse(await ev(code)); } catch (e) { return {err: 'parse'}; }
  };
  const res = {};
  for (const k of Object.keys(PERIODS)) {
    const d = await one(PERIODS[k].from, PERIODS[k].to);
    if (d && d.cost !== undefined) res[k] = {spend: r2(+d.cost), trade: r2(+(d.totalOrderSum || 0))};
    else console.log('  京东', k, '失败', JSON.stringify(d).slice(0, 100));
  }
  ws.close();
  return res;
}

/* ---------------- 淘宝闪购：推广数据页（全站推广 + 斗金推广）
   路径：#/pc/promotionDataPc/ → 内容在 doujin-isv-manage iframe 里
   只支持页内固定口径「过去7天」「过去30天」，自定义日期控件不接受程序化输入，
   因此 mtd / p90 不写数（页面上会标注）。 ---------------- */
async function fetchEleme() {
  let tab;
  try {
    const tabs = await (await fetch(CDP + '/json/list')).json();
    tab = tabs.find(t => t.type === 'page' && (t.url || '').indexOf('https://nr.ele.me/') === 0);
  } catch (e) { return null; }
  if (!tab) { console.log('  淘闪：未打开 nr.ele.me 标签页，跳过'); return null; }

  const WebSocket = require('ws');
  const ws = new WebSocket(tab.webSocketDebuggerUrl);
  let nid = 0; const pending = {}; const ctxs = [];
  ws.on('message', m => { const o = JSON.parse(m.toString());
    if (o.id && pending[o.id]) { pending[o.id](o); delete pending[o.id]; return; }
    if (o.method === 'Runtime.executionContextCreated') ctxs.push(o.params.context);
  });
  await new Promise(r => ws.on('open', r));
  const send = (m, p) => new Promise(res => { const i = ++nid; pending[i] = res; ws.send(JSON.stringify({id: i, method: m, params: p || {}})); });
  const evIn = async (e, c) => {
    const r = await send('Runtime.evaluate', {expression: e, returnByValue: true, awaitPromise: true, contextId: c});
    if (r.result && r.result.exceptionDetails) return null;
    return r.result && r.result.result ? r.result.result.value : null;
  };
  await send('Runtime.enable'); await send('Page.enable');
  // 同样先整页刷新，清掉旧的执行上下文/会话缓存
  await send('Page.reload', {ignoreCache: true});

  // 等 iframe 上下文就绪
  let cid = null;
  for (let i = 0; i < 30; i++) {
    await new Promise(r => setTimeout(r, 2000));
    const dc = ctxs.filter(c => /doujin-isv-manage/.test(c.origin || ''));
    if (dc.length) {
      const c = dc[dc.length - 1].id;
      const txt = await evIn('document.body.innerText', c);
      // 必须等「推广消费」和「已选时间」都渲染出来；只有头部时读到的是旧数据/trade 有值但 spend 为空
      if (typeof txt === 'string' && txt.indexOf('推广消费') >= 0 && txt.indexOf('已选时间') >= 0) { cid = c; break; }
    }
  }
  if (!cid) { ws.close(); console.log('  淘闪：内容未加载（可能登录态失效），跳过'); return null; }

  const summary = () => evIn(`(function(){var t=document.body.innerText.replace(/\\s+/g,' ');
    var m1=t.match(/已选时间[:：]?\\s*([\\d-]+)\\s*-\\s*([\\d-]+)/);
    var m2=t.match(/推广消费\\(元\\)\\s*([\\d,\\.]+)/);
    var m3=t.match(/全站交易额\\(元\\)\\s*([\\d,\\.]+)/);
    return JSON.stringify({from:m1?m1[1]:null,to:m1?m1[2]:null,spend:m2?m2[1]:null,trade:m3?m3[1]:null});})()`, cid);
  const clickTab = t => evIn(`(function(){var n=document.querySelectorAll('*');
    for(var i=0;i<n.length;i++){ if((n[i].innerText||'').trim()==='${t}'&&n[i].children.length===0){
      var el=n[i],k=0; while(el&&k<3){ el.click(); el=el.parentElement; k++; } return 'ok'; } } return 'nf';})()`, cid);

  const out = {};
  for (const [tabName, key] of [['过去7天', 'p7'], ['过去30天', 'p30']]) {
    await clickTab(tabName);
    let j = null;
    // 切换周期后轮询等数据刷新（最长 24 秒），避免拿到上一周期的旧值
    for (let i = 0; i < 12; i++) {
      await new Promise(r => setTimeout(r, 2000));
      try {
        const cand = JSON.parse(await summary());
        if (cand.spend != null && cand.trade != null) { j = cand; break; }
      } catch (e) {}
    }
    if (!j) { console.log('  淘闪 ' + tabName + ' 未读到推广消费，跳过'); continue; }
    const spend = parseFloat(String(j.spend).replace(/,/g, ''));
    const trade = parseFloat(String(j.trade).replace(/,/g, ''));
    if (isFinite(spend) && isFinite(trade)) {
      out[key] = {spend: r2(spend), trade: r2(trade), rateAd: trade ? r2(spend / trade * 100) : null,
        src: '淘闪 全站推广+斗金推广', range: {from: j.from, to: j.to}};
      console.log('  淘闪 ' + tabName + ' ' + (j.from || '') + '~' + (j.to || '') + ' 消费 ' + spend + ' 交易额 ' + trade);
    }
  }
  ws.close();
  return Object.keys(out).length ? out : null;
}

/* ---------------- 主流程 ---------------- */
(async () => {
  console.log('周期：' + Object.keys(PERIODS).map(k => k + ' ' + PERIODS[k].from + '~' + PERIODS[k].to).join(' | '));
  const out = {generated_at: iso(today) + 'T' + pad(today.getHours()) + ':' + pad(today.getMinutes()) + ':' + pad(today.getSeconds()) + '+08:00',
    periods: {}, meta: {note: '推广费=各平台广告后台花费；交易额=该平台同口径归因成交额；费率比=推广费÷交易额'}};
  for (const k of Object.keys(PERIODS)) out.periods[k] = {range: PERIODS[k], platforms: {}};

  console.log('· 美团联合营销');
  const mt = await fetchMeituan();
  console.log('· 京东竞价广告');
  const jd = await fetchJd();
  console.log('· 淘宝闪购');
  const tb = await fetchEleme();

  for (const k of Object.keys(PERIODS)) {
    const f = PERIODS[k].from, t = PERIODS[k].to;
    const m = mt.agg(f, t);
    if (m.spend > 0 || m.trade > 0) {
      out.periods[k].platforms['美团'] = {spend: m.spend, trade: m.trade,
        rateAd: m.trade ? r2(m.spend / m.trade * 100) : null, src: 'ka.waimai 联合营销'};
    }
    const j = jd[k];
    if (j) out.periods[k].platforms['京东'] = {spend: j.spend, trade: j.trade,
      rateAd: j.trade ? r2(j.spend / j.trade * 100) : null, src: 'jzt 竞价广告'};
    if (tb && tb[k]) out.periods[k].platforms['淘宝闪购'] = tb[k];
  }
  out.meta.plat_from = {美团: mt.since};
  out.meta.eleme_periods = tb ? Object.keys(tb) : [];
  out.meta.missing = tb ? (out.meta.eleme_periods.length < 4 ? ['淘宝闪购仅覆盖近7天/近30天（推广数据页只提供固定口径）'] : [])
    : ['淘宝闪购（全站推广+斗金推广）需 nr.ele.me 登录后补采'];

  // 安全闸门：少于 2 个平台有数据时，绝不覆盖上次快照（避免抗爬失败把别的平台数据抹掉）
  const okPlats = Object.keys(out.periods.p7.platforms).filter(k => out.periods.p7.platforms[k].spend > 0);
  if (okPlats.length < 2) {
    console.error('数据不足：只有 ' + okPlats.join('/') + ' 采到推广费，保留上次 ad_spend.json，不覆盖不上传');
    process.exit(2);
  }
  fs.writeFileSync(OUT, JSON.stringify(out, null, 1));
  console.log('\n已写入 ' + OUT);
  for (const k of Object.keys(PERIODS)) {
    const p = out.periods[k].platforms;
    console.log('  ' + k + ': ' + Object.keys(p).map(k2 => k2 + ' ¥' + p[k2].spend).join('  '));
  }

  // --upload：采集完立刻走浏览器网页上传到仓库（一条命令完成，避免自动化在中途被重启打断）
  if (process.argv.indexOf('--upload') >= 0) {
    const {spawnSync} = require('child_process');
    const uploader = path.resolve(__dirname, '..', 'cyby-tools', 'gh_upload.js');
    if (!fs.existsSync(uploader)) { console.error('上传脚本不存在：' + uploader); process.exit(2); }
    const r = spawnSync(process.execPath, [uploader, OUT, '--msg', '推广费 ' + iso(today)], {stdio: 'inherit'});
    if (r.status !== 0) { console.error('上传失败（exit ' + r.status + '）'); process.exit(3); }
    console.log('采集 + 上传完成，GitHub Actions 将自动重建看板');
  }
})().catch(e => { console.error('失败：' + e.message); process.exit(1); });
