// README 스크린샷과 GIF 를 만든다. samples/demo 를 재생한 timeline.html 을 헤드리스 Chrome 으로 열고 장면마다 캡처한다.
//
// 사용법: node docs/capture.mjs <timeline.html> [출력 디렉터리(기본 docs/images)] [--compare <DDL 비교 timeline.html>] [--gif <출력 .gif>]
// 캡처는 Node 22 이상의 WebSocket 과 Chrome DevTools 프로토콜만 쓴다. --gif 는 프레임을 이어 붙일 때 Pillow 가 있는 python3 가 필요하다.
import { execFileSync, spawn } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const args = process.argv.slice(2);
const opt = name => { const k = args.indexOf(name); return k < 0 ? null : (args.splice(k, 2)[1] ?? ''); };
const compareFile = opt('--compare'), gifOut = opt('--gif');
const [file, outDir = join(import.meta.dirname, 'images')] = args;
if (!file || !existsSync(file) || compareFile === '' || gifOut === '' || (compareFile && !existsSync(compareFile))) {
  console.error('사용법: node docs/capture.mjs <timeline.html> [출력 디렉터리] [--compare <timeline.html>] [--gif <출력 .gif>]'); process.exit(2);
}
const urls = { demo: pathToFileURL(resolve(file)).href, compare: compareFile && pathToFileURL(resolve(compareFile)).href };
mkdirSync(outDir, { recursive: true });

const sleep = ms => new Promise(r => setTimeout(r, ms));
const chromePath = [process.env.CHROME_PATH, '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/usr/bin/google-chrome', '/usr/bin/chromium'].find(p => p && existsSync(p));
if (!chromePath) { console.error('Chrome 을 찾지 못했습니다. CHROME_PATH 로 지정하세요.'); process.exit(2); }

const profile = mkdtempSync(join(tmpdir(), 'schema-capture-'));
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
/** 변경 화면 iframe 이 다 그려질 때까지 기다린다. */
const frameReady = `(async () => {
  for (let k = 0; k < 100; k++) {
    const f = document.getElementById('frame'), d = f?.contentDocument;
    if (f?.hidden || (d?.readyState === 'complete' && d.getElementById('note')?.textContent)) return true;
    await new Promise(r => setTimeout(r, 100));
  }
  return false; })()`;

/** 화면 크기를 정하고 page(기본 demo)의 hash 로 연 뒤 변경 화면이 다 그려질 때까지 기다린다. */
async function open(sc) {
  await S('Emulation.setDeviceMetricsOverride', { width: sc.w, height: sc.h, deviceScaleFactor: sc.dpr || 2, mobile: !!sc.mobile });
  await S('Page.navigate', { url: 'about:blank' });
  await sleep(100);
  const loaded = new Promise(r => { const l = m => { if (m.method === 'Page.loadEventFired') { listeners.delete(l); r(); } }; listeners.add(l); });
  await S('Page.navigate', { url: `${urls[sc.page || 'demo']}#${sc.hash}` });
  await loaded;
  await evalJs(frameReady);
  await evalJs(`document.querySelector('.theme [data-theme="${sc.theme || 'light'}"]').click()`);
}

/** 페이지에서 code 를 실행하고 변경 화면이 다시 그려질 때까지 기다린다. code 안의 F 는 변경 화면 iframe 의 document, to(el) 는 el 을 iframe 맨 위로 올린다. */
async function run(code) {
  await evalJs(`(() => { const F = document.getElementById('frame').contentDocument;
    const to = (el, off = 16) => F.defaultView.scrollTo({ top: el.getBoundingClientRect().top + F.defaultView.scrollY - off, behavior: 'instant' });
    ${code} })()`);
  await sleep(300);
  await evalJs(frameReady);
}

const shot = async () => Buffer.from((await S('Page.captureScreenshot', { format: 'png' })).data, 'base64');

/**
 * 장면 목록. hash 로 단계·보기를 열고 theme(기본 light)을 고른 뒤, setup 을 페이지에서 실행하고 캡처한다.
 * page: 'compare' 는 --compare 로 받은 DDL 비교 화면이고, 주지 않으면 건너뛴다.
 */
const SCENES = [
  { name: 'overview-dark', hash: 'step=7', w: 1440, h: 900, theme: 'dark' },
  { name: 'table-drop', hash: 'step=7', w: 1440, h: 1450, setup: `to(F.querySelector('.blk[data-table="customers"]'));` },
  { name: 'full-sql', hash: 'step=4', w: 1440, h: 900,
    setup: `F.querySelector('.blk[data-table="products"] [data-bview="ddl"]').click(); F.getElementById('diff-sec').scrollIntoView();` },
  { name: 'lineage', hash: 'step=3', w: 1440, h: 900,
    setup: `const e = Object.values(LIN).find(x => x.kind === 'column' && lastName(x) === 'state'); openLineage(e.id); document.activeElement.blur();` },
  { name: 'search', hash: 'step=7', w: 1440, h: 900,
    setup: `openPal(); const q = document.getElementById('pal-q'); q.value = 'order'; q.dispatchEvent(new Event('input'));` },
  { name: 'schema-ddl', hash: 'step=8&view=ddl', w: 1440, h: 900 },
  { name: 'range', hash: 'from=1&to=8', w: 1440, h: 900 },
  { name: 'diagram', hash: 'step=5', w: 1440, h: 1000, setup: `to(F.getElementById('map-sec'));` },
  { name: 'checks', hash: 'step=4', w: 1440, h: 960,
    setup: `F.querySelector('#checks-sec details').open = true; to(F.getElementById('risk-title').closest('.risks'));` },
  { name: 'compare', page: 'compare', hash: 'step=1', w: 1440, h: 900 },
  { name: 'mobile', hash: 'step=7', w: 390, h: 844, mobile: true },
];

for (const sc of SCENES) {
  if (sc.page && !urls[sc.page]) continue;
  await open(sc);
  if (sc.setup) await run(sc.setup);
  await sleep(500);
  const path = join(outDir, `${sc.name}.png`);
  writeFileSync(path, await shot());
  console.log(path);
}

/**
 * GIF 프레임 목록. 단계를 V1 부터 V8 까지 넘기고, 관계도에서 테이블을 찾고, 검색에서 컬럼 계보를 연다.
 * after 는 code 로 변경 화면이 다시 그려진 뒤 실행한다. ms 는 그 프레임을 보여 주는 시간이다.
 */
const TOUR = [
  ...[1, 2, 3, 4, 5, 6, 7, 8].map(i => ({ code: `select(${i})`, ms: i === 7 ? 1800 : 1100 })),
  { code: `select(5)`, after: `to(F.getElementById('map-sec'));`, ms: 1400 },
  { code: `const q = F.getElementById('map-find'); q.value = 'ship'; q.dispatchEvent(new Event('input'));`, ms: 1600 },
  { code: `F.defaultView.scrollTo(0, 0); openPal(); const q = document.getElementById('pal-q'); q.value = 'state'; q.dispatchEvent(new Event('input'));`, ms: 1400 },
  { code: `document.getElementById('pal').close(); const e = Object.values(LIN).find(x => x.kind === 'column' && lastName(x) === 'state'); openLineage(e.id); document.activeElement.blur();`, ms: 2600 },
];

if (gifOut) {
  const dir = mkdtempSync(join(tmpdir(), 'schema-gif-'));
  await open({ hash: 'step=1', w: 1280, h: 800, dpr: 1 });
  const ms = [];
  for (const [k, f] of TOUR.entries()) {
    await run(f.code);
    if (f.after) await run(f.after);
    await sleep(400);
    writeFileSync(join(dir, `${String(k).padStart(3, '0')}.png`), await shot());
    ms.push(f.ms);
  }
  execFileSync('python3', [join(import.meta.dirname, 'make_gif.py'), dir, gifOut, ms.join(',')], { stdio: 'inherit' });
  rmSync(dir, { recursive: true, force: true });
  console.log(gifOut);
}

ws.close();
chrome.kill('SIGKILL');
for (let i = 0; i < 10; i++) { try { rmSync(profile, { recursive: true, force: true }); break; } catch (e) { await sleep(200); } }
