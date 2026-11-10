// 타임라인 HTML 을 헤드리스 Chrome 으로 열어 선택한 단계·구간의 collectRisks() 결과를 JSON 으로 낸다.
//
// 사용법: node tests/cdp_risks.mjs <timeline.html> <장면> [...] [--shot 디렉터리]
// 장면: 7 (단계) 또는 1-17 (구간). 출력: {"7": {risks, headline, kpi}, "1-17": ...}
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const argv = process.argv.slice(2);
const shotAt = argv.indexOf('--shot');
const shotDir = shotAt >= 0 ? argv.splice(shotAt, 2)[1] : null;
const [file, ...scenes] = argv;
if (!file || !existsSync(file) || !scenes.length) { console.error('사용법: node tests/cdp_risks.mjs <timeline.html> <단계|a-b> ... [--shot 디렉터리]'); process.exit(2); }
if (shotDir) mkdirSync(shotDir, { recursive: true });
const pageUrl = pathToFileURL(resolve(file)).href;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const chromePath = [process.env.CHROME_PATH, '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '/usr/bin/google-chrome', '/usr/bin/chromium'].find(p => p && existsSync(p));
if (!chromePath) { console.error('Chrome 을 찾지 못했습니다'); process.exit(2); }

const profile = mkdtempSync(join(tmpdir(), 'schema-risks-'));
const chrome = spawn(chromePath, ['--headless=new', '--disable-gpu', '--no-first-run', '--hide-scrollbars', '--force-color-profile=srgb',
  `--user-data-dir=${profile}`, '--remote-debugging-port=0', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
const wsUrl = await new Promise((res, rej) => {
  let tail = '';
  chrome.stderr.on('data', d => { tail += d; const m = /DevTools listening on (ws:\/\/\S+)/.exec(tail); if (m) res(m[1]); });
  setTimeout(() => rej(new Error('Chrome 이 뜨지 않았습니다')), 20000);
});
const ws = new WebSocket(wsUrl);
await new Promise(r => { ws.onopen = r; });
let seq = 0;
const pending = new Map(), listeners = new Set();
ws.onmessage = ev => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) { const p = pending.get(m.id); pending.delete(m.id); m.error ? p.rej(new Error(m.error.message)) : p.res(m.result); }
  else if (m.method) for (const l of listeners) l(m);
};
const send = (method, params = {}, sessionId) => new Promise((res, rej) => {
  const id = ++seq; pending.set(id, { res, rej }); ws.send(JSON.stringify({ id, method, params, sessionId }));
});
const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
const S = (m, p) => send(m, p, sessionId);
await S('Page.enable');
await S('Runtime.enable');
const evalJs = async expr => {
  const r = await S('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
  return r.result.value;
};
const frameReady = `(async () => {
  for (let k = 0; k < 100; k++) {
    const f = document.getElementById('frame'), d = f?.contentDocument;
    if (f?.hidden || (d?.readyState === 'complete' && d.getElementById('note')?.textContent)) return true;
    await new Promise(r => setTimeout(r, 100));
  }
  return false; })()`;

const out = {};
for (const sc of scenes) {
  const hash = sc.includes('-') ? `from=${sc.split('-')[0]}&to=${sc.split('-')[1]}` : `step=${sc}`;
  await S('Emulation.setDeviceMetricsOverride', { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });
  await S('Page.navigate', { url: 'about:blank' });
  await sleep(100);
  const loaded = new Promise(r => { const l = m => { if (m.method === 'Page.loadEventFired') { listeners.delete(l); r(); } }; listeners.add(l); });
  await S('Page.navigate', { url: `${pageUrl}#${hash}` });
  await loaded;
  await evalJs(frameReady);
  out[sc] = await evalJs(`(() => {
    const w = document.getElementById('frame').contentWindow, d = w.document;
    return { risks: w.collectRisks().map(r => ({ level: r.level, kind: r.kind || null, what: r.what, lead: r.lead || null, why: r.why })),
      headline: d.getElementById('headline').textContent,
      kpi: [...d.querySelectorAll('#stats .kpi')].map(k => k.innerText.replace(/\\s+/g, ' ').trim()),
      objects: [...d.querySelectorAll('#objs .obj')].map(k => k.innerText.replace(/\\s+/g, ' ').trim()) };
  })()`);
  if (shotDir) {
    await sleep(300);
    const { data } = await S('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
    writeFileSync(join(shotDir, `shot-${sc}.png`), Buffer.from(data, 'base64'));
  }
}
console.log(JSON.stringify(out, null, 2));
ws.close();
chrome.kill('SIGKILL');
for (let i = 0; i < 10; i++) { try { rmSync(profile, { recursive: true, force: true }); break; } catch (e) { await sleep(200); } }
