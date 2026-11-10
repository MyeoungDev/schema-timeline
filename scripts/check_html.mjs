// 만든 timeline.html(이력 타임라인, DDL 두 개 비교)을 헤드리스 Chrome 으로 열어 점검하고 영수증을 JSON 으로 낸다.
//
// 사용법: node check_html.mjs <HTML 파일> [--shot 스크린샷.png] [--mermaid mermaid.min.js] [--timeout 초]
// 출력 언어: 페이지의 D.lang(ko|en). 페이지를 열기 전에는 SCHEMA_TIMELINE_LANG, 로케일 순으로 정한다. 항목마다 고정된 id(kebab-case)가 있다.
// 점검: 스크립트·콘솔 오류, 외부 요청(다른 로컬 파일·WebSocket 포함), 핵심 영역, 1280px·375px 가로 넘침,
//       타임라인이면 모든 적용 단계·전체 구간의 변경 화면, 관계도 확대·축소, 전체 검색(⌘K: 테이블→계보, SQL 원문→줄 표시, 변경 화면 안에서 열기),
//       관계도 내보내기(보기 3개 × 범위 2개의 SVG·PNG·Mermaid),
//       Mermaid 파싱(로컬 mermaid 가 있을 때. 쓴 버전을 영수증에 적는다),
//       영어 화면이면 모든 단계·전체 DDL·구간·⌘K 화면에서 UI 한글 잔존 여부.
// 종료 코드: 0 통과(건너뜀은 있을 수 있음), 1 실패 항목 있음(페이지가 멈추거나 죽은 경우 포함), 2 점검 불가(Chrome·node 문제).
// 패키지 설치 없이 Node 의 fetch·WebSocket 과 Chrome DevTools 프로토콜만 쓴다.
import { spawn, execFileSync } from 'node:child_process';
import { existsSync, mkdtempSync, rmSync, writeFileSync, readdirSync, readFileSync } from 'node:fs';
import { tmpdir, homedir } from 'node:os';
import { join, resolve, dirname, basename } from 'node:path';
import { pathToFileURL } from 'node:url';

/** SCHEMA_TIMELINE_LANG, 로케일(LC_ALL, LC_MESSAGES, LANG 중 처음 값이 있는 것이 ko 로 시작하면 ko) 순으로 정한 언어. */
function envLang() {
  const v = (process.env.SCHEMA_TIMELINE_LANG || '').trim().toLowerCase();
  if (v === 'ko' || v === 'en') return v;
  for (const k of ['LC_ALL', 'LC_MESSAGES', 'LANG']) if (process.env[k]) return process.env[k].toLowerCase().startsWith('ko') ? 'ko' : 'en';
  return 'en';
}
let LANG = envLang();
/** 현재 언어의 문구. */
const T = (ko, en) => (LANG === 'ko' ? ko : en);

const args = process.argv.slice(2);
const opt = n => { const i = args.indexOf(n); return i >= 0 ? args[i + 1] : null; };
const file = args.find((a, i) => !a.startsWith('--') && !(i > 0 && args[i - 1].startsWith('--')));
if (!file || !existsSync(file)) {
  console.error(T('사용법: node check_html.mjs <HTML 파일> [--shot 파일.png] [--mermaid mermaid.min.js] [--timeout 초]',
    'Usage: node check_html.mjs <HTML file> [--shot file.png] [--mermaid mermaid.min.js] [--timeout seconds]'));
  process.exit(2);
}
const pageUrl = pathToFileURL(resolve(file)).href;
const shotPath = opt('--shot') || join(dirname(resolve(file)), basename(file, '.html') + '.check.png');
const timeoutMs = Number(opt('--timeout') || 240) * 1000;
rmSync(shotPath, { force: true });

const checks = [];
const check = (id, name, ok, detail = '') => checks.push({ id, name, ok: ok === null ? null : !!ok, detail: String(detail) });
let chrome = null, profile = null, ws = null, screenshot = null, finished = false, pageOpened = false;

/** 영수증을 출력하고 끝낸다. infraReason 이 있으면 점검 불가(2)다. */
function finish(infraReason = null) {
  if (finished) return;
  finished = true;
  try { ws?.close(); } catch (e) { /* 닫힘 */ }
  try { chrome?.kill('SIGKILL'); } catch (e) { /* 끝남 */ }
  if (profile) {
    for (let i = 0; i < 10; i++) {
      try { rmSync(profile, { recursive: true, force: true }); break; } catch (e) { execFileSync('sleep', ['0.2']); }
    }
  }
  const failed = checks.filter(c => c.ok === false), skipped = checks.filter(c => c.ok === null);
  const ok = infraReason ? null : failed.length === 0;
  console.log(JSON.stringify({ file, ok, reason: infraReason, screenshot,
    summary: { passed: checks.filter(c => c.ok === true).length, failed: failed.length, skipped: skipped.length }, checks }, null, 2));
  process.exit(infraReason ? 2 : ok ? 0 : 1);
}
/** 페이지를 연 뒤의 시간 초과·크래시는 페이지 문제(실패), 그 전은 환경 문제(점검 불가)로 본다. */
function pageFault(id, name, detail) {
  if (!pageOpened) return finish(detail);
  check(id, name, false, detail);
  finish();
}
setTimeout(() => pageFault('time-limit', T('제한 시간 안에 점검을 마침', 'Check finished within the time limit'),
  T(`전체 점검이 ${timeoutMs / 1000}초를 넘었습니다. 페이지가 멈췄거나 너무 큽니다.`, `The whole check exceeded ${timeoutMs / 1000}s. The page hung or is too large.`)), timeoutMs).unref();
process.on('uncaughtException', e => finish(T(`점검 도구 오류: ${e.message}`, `Check tool error: ${e.message}`)));
process.on('unhandledRejection', e => finish(T(`점검 도구 오류: ${e?.message || e}`, `Check tool error: ${e?.message || e}`)));

function findChrome() {
  const cands = [process.env.CHROME_PATH,
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
    '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge'];
  for (const name of ['google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser', 'microsoft-edge']) {
    try { cands.push(execFileSync('which', [name], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim()); } catch (e) { /* 없음 */ }
  }
  return cands.find(p => p && existsSync(p));
}

/** 로컬 mermaid 번들과 버전. 후보가 여럿이면 가장 높은 버전. */
function findMermaid() {
  const explicit = opt('--mermaid') || process.env.MERMAID_JS;
  const cands = explicit ? [explicit] : [];
  const npx = join(homedir(), '.npm', '_npx');
  if (!explicit && existsSync(npx)) {
    for (const d of readdirSync(npx)) cands.push(join(npx, d, 'node_modules', 'mermaid', 'dist', 'mermaid.min.js'));
  }
  const found = cands.filter(existsSync).map(p => {
    let version = T('버전 모름', 'version unknown');
    try { version = JSON.parse(readFileSync(join(dirname(dirname(p)), 'package.json'), 'utf8')).version; } catch (e) { /* 단일 파일 */ }
    return { path: p, version };
  });
  const num = v => (/^(\d+)\.(\d+)\.(\d+)/.exec(v) || [0, 0, 0, 0]).slice(1).map(Number);
  const cmp = (a, b) => { const x = num(a.version), y = num(b.version); return x[0] - y[0] || x[1] - y[1] || x[2] - y[2]; };
  return found.sort((a, b) => cmp(b, a))[0] || null;
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

const chromePath = findChrome();
if (!chromePath) finish(T('Chrome/Chromium 을 찾지 못해 점검하지 못했습니다. CHROME_PATH 로 지정할 수 있습니다.', 'Chrome/Chromium was not found, so the check could not run. Set CHROME_PATH to point to it.'));
if (typeof WebSocket === 'undefined') finish(T('Node 에 WebSocket 이 없습니다. Node 22 이상을 쓰거나 --experimental-websocket 으로 실행하세요.', 'This Node has no WebSocket. Use Node 22 or later, or run with --experimental-websocket.'));

profile = mkdtempSync(join(tmpdir(), 'schema-check-'));
const flags = ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check', '--disable-extensions',
  '--allow-file-access-from-files', `--user-data-dir=${profile}`, '--remote-debugging-port=0'];
if (process.platform === 'linux') flags.push('--disable-dev-shm-usage');
if (process.getuid?.() === 0 || process.env.CI) flags.push('--no-sandbox');
let stderrTail = '';
const wsUrl = await new Promise(res => {
  chrome = spawn(chromePath, [...flags, 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  const t = setTimeout(() => finish(T(`Chrome 이 20초 안에 뜨지 않았습니다. ${stderrTail.slice(-300)}`, `Chrome did not start within 20 seconds. ${stderrTail.slice(-300)}`)), 20000);
  chrome.stderr.on('data', d => {
    stderrTail = (stderrTail + d).slice(-2000);
    const m = /DevTools listening on (ws:\/\/\S+)/.exec(stderrTail);
    if (m) { clearTimeout(t); res(m[1]); }
  });
  chrome.on('error', e => finish(T(`Chrome 을 실행하지 못했습니다: ${e.message}`, `Could not launch Chrome: ${e.message}`)));
  chrome.on('exit', code => finish(T(`Chrome 이 종료됐습니다(코드 ${code}). ${stderrTail.slice(-300)}`, `Chrome exited (code ${code}). ${stderrTail.slice(-300)}`)));
});

ws = new WebSocket(wsUrl);
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error(T('DevTools 연결 실패', 'DevTools connection failed'))); });
let seq = 0;
const pending = new Map(), listeners = new Set();
ws.onmessage = ev => {
  const msg = JSON.parse(ev.data);
  if (msg.id && pending.has(msg.id)) {
    const { res, rej } = pending.get(msg.id);
    pending.delete(msg.id);
    msg.error ? rej(new Error(msg.error.message)) : res(msg.result);
  } else if (msg.method) {
    for (const l of listeners) l(msg);
  }
};
const send = (method, params = {}, sessionId) => new Promise((res, rej) => {
  const id = ++seq;
  pending.set(id, { res, rej });
  ws.send(JSON.stringify({ id, method, params, sessionId }));
});
const once = (method, ms) => new Promise(res => {
  const l = m => { if (m.method === method) { listeners.delete(l); res(true); } };
  listeners.add(l);
  setTimeout(() => { listeners.delete(l); res(false); }, ms);
});

const errors = [], requests = [];
listeners.add(m => {
  if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
  if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') errors.push(m.params.args.map(a => a.value ?? a.description).join(' '));
  if (m.method === 'Log.entryAdded' && m.params.entry.level === 'error') errors.push(m.params.entry.text);
  if (m.method === 'Network.requestWillBeSent') requests.push(m.params.request.url);
  if (m.method === 'Network.webSocketCreated') requests.push(m.params.url);
  if (m.method === 'Inspector.targetCrashed') pageFault('page-alive', T('페이지가 죽지 않음', 'Page did not crash'), T('렌더러가 중단됐습니다(메모리 부족이나 무한 재귀 등)', 'The renderer stopped (out of memory, infinite recursion, etc.)'));
});

const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
const S = (m, p) => send(m, p, sessionId);
await S('Runtime.enable'); await S('Log.enable'); await S('Network.enable'); await S('Page.enable'); await S('Inspector.enable');
const evalJs = async expr => {
  const r = await S('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
  return r.result.value;
};
const open = async (url, width, height, mobile) => {
  await S('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile });
  const loaded = once('Page.loadEventFired', 20000);
  pageOpened = true;
  await S('Page.navigate', { url });
  const ok = await loaded;
  await sleep(600);
  return ok;
};

/** 페이지 안에서 돌릴 한글 잔존 검사기를 window.__leakScan(label) 로 설치한다(evalJs 로 보내므로 외부 변수를 쓰지 않는다).
 *  위 페이지와 검토 iframe 의 보이는 글자 노드·placeholder·title·aria-label 중 D 의 데이터 문자열(compare_template 제외)과
 *  통째로 같지 않은 한글을 window.__leaks 에 모은다. 따옴표로 감싼 조각이 데이터와 같으면 그 조각은 뺀다. 코드 보기(.cl code, .sql-src, .ddl-all .code-plain, .code)는 데이터로 본다. */
function leakSetup() {
  const HAN = /[가-힣ㄱ-ㆎ]+/g, HAS = /[가-힣ㄱ-ㆎ]/;
  const norm = s => s.replace(/\s+/g, ' ').trim();
  const EDGE = /^[\s"'`“”‘’(\[{<…:·,.;-]+|[\s"'`“”‘’)\]}>…:·,.;-]+$/g;
  const exact = new Set(), bigParts = [];
  const add = s => {
    if (!HAS.test(s)) return;
    bigParts.push(s);
    exact.add(norm(s));
    for (const line of s.split('\n')) if (HAS.test(line)) exact.add(norm(line));
  };
  const walk = v => {
    if (typeof v === 'string') add(v);
    else if (Array.isArray(v)) v.forEach(walk);
    else if (v && typeof v === 'object') for (const k of Object.keys(v)) if (k !== 'compare_template') walk(v[k]);
  };
  walk(D);
  const big = bigParts.map(norm).join('\n');
  const isData = text => {
    const t = norm(text), t2 = t.replace(EDGE, '');
    if (exact.has(t) || exact.has(t2)) return true;
    if (t2.endsWith('...') || t2.endsWith('…')) { const h = t2.replace(/(\.\.\.|…)$/, ''); if (h.length >= 4 && big.includes(h)) return true; }
    return t2.length >= 8 && big.includes(t2);
  };
  const CODE = '.cl code, .sql-src, .ddl-all .code-plain, .code';
  const seen = new Set();
  window.__leaks = [];
  const report = (label, where, text) => {
    let t = norm(text);
    if (!HAS.test(t) || isData(t)) return;
    t = norm(t.replace(/[“"‘`]([^”"’`]*)[”"’`]/g, (m, q) => (isData(q) ? ' ' : m)));
    if (!HAS.test(t)) return;
    const key = where + '|' + t.slice(0, 80);
    if (seen.has(key)) return;
    seen.add(key);
    const runs = t.match(HAN);
    const at = t.indexOf(runs[0]);
    window.__leaks.push(label + '/' + where + ': ' + runs.slice(0, 3).join(' ') + ' in "' + (at > 20 ? '…' : '') + t.slice(Math.max(0, at - 20), at + 40) + '"');
  };
  const shown = el => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
  const scan = (doc, where, label) => {
    if (!doc || !doc.body) return;
    const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      if (!HAS.test(n.textContent)) continue;
      const p = n.parentElement;
      if (!p || /^(SCRIPT|STYLE|TEMPLATE|NOSCRIPT)$/.test(p.tagName) || p.closest(CODE) || !shown(p)) continue;
      report(label, where, n.textContent);
    }
    for (const el of doc.querySelectorAll('[placeholder],[title],[aria-label]'))
      for (const a of ['placeholder', 'title', 'aria-label']) {
        const v = el.getAttribute(a);
        if (v && HAS.test(v) && !el.closest(CODE)) report(label, where + '@' + a, v);
      }
  };
  window.__leakScan = label => {
    scan(document, 'page', label);
    const f = document.getElementById('frame');
    if (f && !f.hidden) scan(f.contentDocument, 'review', label);
  };
}

const mermaidTexts = [];
/** 내보내기 자체 점검 결과(exportSelfTest)를 항목으로 옮긴다. */
const exportChecks = (res, where, tag) => {
  if (!res) { check(`export-${tag}`, T(`관계도 내보내기${where}`, `ERD export${where}`), null, T('이 화면에는 내보내기 점검 함수가 없음(다시 렌더하면 생김)', 'This page has no export self-test (re-render to add it)')); return; }
  const bad = res.filter(r => r.svgError || r.clips !== r.nodes || (typeof r.png === 'string' && r.png !== 'too-large'));
  check(`export-svg-png-${tag}`, T(`관계도 SVG·PNG 내보내기${where}`, `ERD SVG/PNG export${where}`), bad.length === 0, bad.length
    ? bad.map(r => `${r.mode}/${r.scope}: ${r.svgError || (r.clips !== r.nodes ? T(`SVG 노드 ${r.clips} / 화면 노드 ${r.nodes}`, `SVG nodes ${r.clips} / page nodes ${r.nodes}`) : r.png)}`).join(' | ')
    : T(`${res.length}개 조합`, `${res.length} combinations`));
  const big = res.filter(r => r.png === 'too-large');
  if (big.length) check(`export-png-${tag}`, T(`관계도 PNG${where}`, `ERD PNG${where}`), null,
    T(`너무 커서 PNG 를 만들지 않음(${big.map(r => `${r.mode}/${r.scope}`).join(', ')}). SVG 는 정상`, `PNG skipped because it is too large (${big.map(r => `${r.mode}/${r.scope}`).join(', ')}). SVG is fine`));
  mermaidTexts.push(...res.map(r => ({ where: `${where} ${r.mode}/${r.scope}`.trim(), text: r.mermaid })));
};

try {
  const loaded = await open(pageUrl, 1280, 900, false);
  const pageLang = (await evalJs(`typeof D !== 'undefined' && D.lang ? D.lang : 'ko'`)) === 'en' ? 'en' : 'ko';
  LANG = pageLang;
  check('page-load', T('페이지 로드', 'Page load'), loaded, '');
  const kind = await evalJs(`document.getElementById('steps') ? 'timeline' : 'unknown'`);
  check('page-kind', T('화면 종류를 알아봄', 'Page type recognized'), kind !== 'unknown', kind === 'unknown'
    ? T('timeline.html 이 아님(단독 compare.html 은 compare.sh 로 다시 만들면 timeline.html 이 됨)', 'Not a timeline.html (rebuild a standalone compare.html with compare.sh)') : kind);
  if (pageLang === 'en') await evalJs(`(${leakSetup})()`);

  const LAB = pageLang === 'en' ? { en: true, drop: 'Table dropped', rec: 'Table recreated' } : { en: false, drop: '테이블 삭제', rec: '테이블 다시 만듦' };

  if (kind === 'timeline') {
    const r = await evalJs(`(async () => {
      const LAB = ${JSON.stringify(LAB)};
      const frame = document.getElementById('frame');
      /** 이전 iframe 창에 표시를 남긴다. srcdoc 이 바뀌면 새 창이 생겨 표시가 사라진다. */
      const markStale = () => { try { frame.contentWindow.__checkStale = true; } catch (e) { /* 아직 없음 */ } };
      /** iframe 이 새 창으로 바뀌고 하단 안내의 '변경 전 → 변경 후' 출처가 기대값이 될 때까지 기다린 뒤 결론 문장을 돌려준다. */
      const frameHead = async expect => {
        for (let k = 0; k < 150; k++) {
          await new Promise(res => setTimeout(res, 100));
          const w = frame.contentWindow;
          if (!w || w.__checkStale || frame.hidden || w.document.readyState !== 'complete') continue;
          const note = w.document.getElementById('note')?.textContent || '';
          if (note.includes(' → ' + expect)) return w.document.getElementById('headline')?.textContent.trim() || '';
        }
        return null;
      };
      const out = { steps: D.steps.length, bars: document.querySelectorAll('.st').length, applied: APPLIED, empty: [], range: null };
      let lastOk = false;
      /** 단계마다: 테이블째 삭제는 "테이블 삭제" 라벨에 컬럼 단위 삭제 줄이 없어야 하고, 다시 만든 테이블은 "테이블 다시 만듦" 라벨이어야 한다. */
      const WHOLE = "(() => { const lab = t => document.getElementById(tableKey(t.name))?.querySelector('.blk-head .label')?.textContent || '';"
        + " const drop = D.tables.filter(t => t.status === 'dropped'), rec = D.tables.filter(t => t.recreated && t.status !== 'dropped');"
        + " return { n: drop.length + rec.length, bad: drop.filter(t => lab(t) !== " + JSON.stringify(LAB.drop) + " || document.getElementById(tableKey(t.name))?.querySelectorAll('.ln.dropped').length)"
        + ".concat(rec.filter(t => lab(t) !== " + JSON.stringify(LAB.rec) + ")).map(t => t.name) }; })()";
      out.whole = { seen: 0, bad: [] };
      const stride = APPLIED > 40 ? Math.ceil(APPLIED / 40) : 1;
      for (let i = 1; i <= APPLIED; i++) {
        markStale(); select(i); lastOk = !!(await frameHead(D.steps[i - 1].file));
        if (!lastOk) { out.empty.push(i); continue; }
        if (i % stride === 0 || i === APPLIED) window.__leakScan?.(vlabel(i));
        const w = frame.contentWindow.eval(WHOLE);
        out.whole.seen += w.n;
        w.bad.forEach(n => out.whole.bad.push(vlabel(i) + ' ' + n));
      }
      out.canRange = canRange();
      if (lastOk) {
        out.review = frame.contentWindow.eval(\`({
          changed: D.tables.filter(t => t.status !== 'same').length + D.views.filter(v => v.status !== 'same').length,
          blocks: document.querySelectorAll('#blocks .blk').length, none: !!document.querySelector('#blocks .none'),
          expected: POS.size - document.querySelectorAll('#map .node.lonely').length,
          nodes: [...document.querySelectorAll('#map .node')].filter(n => getComputedStyle(n).display !== 'none').length,
          toggle: (() => {
            const blk = [...document.querySelectorAll('#blocks .blk')].find(b => b.querySelector('[data-bview="ddl"]'));
            if (!blk) return null;
            const was = blk.dataset.view;
            blk.querySelector('[data-bview="ddl"]').click();
            const shown = getComputedStyle(blk.querySelector('.ddlv')).display !== 'none' && getComputedStyle(blk.querySelector('.dl')).display === 'none';
            blk.querySelector(\\\`[data-bview="\\\${was}"]\\\`).click();
            return shown && blk.dataset.view === was;
          })() })\`);
        out.zoom = frame.contentWindow.eval(\`(() => {
          const lab = () => document.getElementById('zoom-fit')?.textContent || '';
          const v = document.getElementById('map-view');
          if (!v || !document.querySelector('[data-zoom="in"]')) return null;
          const a = lab(); document.querySelector('[data-zoom="in"]').click(); const b = lab();
          document.querySelector('[data-zoom="1"]').click(); document.querySelector('[data-zoom="fit"]').click();
          return { a, b, back: lab(), changed: a !== b && lab() === a };
        })()\`);
        out.lastExport = frame.contentWindow.exportSelfTest ? await frame.contentWindow.exportSelfTest() : null;
        if (window.__leakScan && typeof setView === 'function') {
          setView('ddl');
          await new Promise(res => setTimeout(res, 300));
          window.__leakScan(vlabel(APPLIED) + ' full DDL');
          markStale(); setView('diff');
          await frameHead(D.steps[APPLIED - 1].file);
        }
      }
      if (out.canRange) {
        markStale();
        selectRange(1, APPLIED, 1);
        out.range = await frameHead(LAB.en ? 'After ' + vlabel(APPLIED) : vlabel(APPLIED) + ' 적용 후');
        if (out.range) window.__leakScan?.('range');
        out.rangeExport = out.range && frame.contentWindow.exportSelfTest ? await frame.contentWindow.exportSelfTest() : null;
      }
      return out; })()`);
    check('version-bars', T('버전 막대가 단계 수만큼 그려짐', 'Version bars match the step count'), r.bars === r.steps, T(`막대 ${r.bars} / 단계 ${r.steps}`, `bars ${r.bars} / steps ${r.steps}`));
    const stepsName = T('적용한 모든 단계의 변경 화면이 그려짐', 'Review renders for every applied step');
    if (!r.applied) check('steps-render', stepsName, null, T('적용한 단계가 없음', 'No applied steps'));
    else check('steps-render', stepsName, r.empty.length === 0, r.empty.length ? T(`비어 있는 단계: ${r.empty.join(', ')}`, `Empty steps: ${r.empty.join(', ')}`) : T(`${r.applied}단계`, `${r.applied} steps`));
    const rangeName = T('전체 구간 비교 화면이 그려짐', 'Full-range comparison renders');
    if (r.applied >= 2 && !r.canRange) check('range-render', rangeName, null, T('구간 비교용 스냅샷이 없어 건너뜀(timeline.py 를 다시 돌리면 생김)', 'Skipped: no range snapshots (re-run timeline.py to create them)'));
    else if (r.applied >= 2) check('range-render', rangeName, !!r.range, r.range ? r.range.slice(0, 80) : T('비어 있음', 'Empty'));
    if (r.review) {
      const v = r.review;
      check('change-blocks', T('변경 블록이 변경 수만큼 그려짐 (마지막 단계)', 'Change blocks match the change count (last step)'), v.none ? v.changed === 0 : v.blocks === v.changed, T(`블록 ${v.blocks} / 변경 ${v.changed}`, `blocks ${v.blocks} / changes ${v.changed}`));
      check('erd-nodes', T('관계도에 보일 테이블이 모두 그려짐 (마지막 단계)', 'All tables to show are drawn in the ERD (last step)'), v.nodes === v.expected, T(`노드 ${v.nodes} / 기대 ${v.expected}`, `nodes ${v.nodes} / expected ${v.expected}`));
      const toggleName = T('블록별 구조 ↔ 전체 SQL 전환이 동작함 (마지막 단계)', 'Per-block structure ↔ full SQL toggle works (last step)');
      if (v.toggle === null) check('block-toggle', toggleName, null, T('전환할 DDL 이 있는 블록이 없어 건너뜀', 'Skipped: no block has DDL to toggle'));
      else if (v.toggle !== undefined) check('block-toggle', toggleName, v.toggle, v.toggle ? '' : T('전환 후 보기가 바뀌지 않음', 'The view did not change after toggling'));
    }
    if (r.whole) {
      const name = T('테이블째 삭제·다시 만듦이 컬럼 변경과 구분돼 그려짐 (모든 단계)', 'Whole-table drops and recreations are drawn apart from column changes (all steps)');
      if (!r.whole.seen) check('whole-table', name, null, T('이 이력에는 테이블째 삭제·다시 만듦이 없음', 'This history has no whole-table drops or recreations'));
      else check('whole-table', name, r.whole.bad.length === 0, r.whole.bad.length ? T(`잘못 그려짐: ${r.whole.bad.slice(0, 5).join(', ')}`, `Drawn wrongly: ${r.whole.bad.slice(0, 5).join(', ')}`) : T(`${r.whole.seen}건 확인`, `${r.whole.seen} verified`));
      const zoomName = T('관계도 확대·축소가 동작함 (마지막 단계)', 'ERD zoom works (last step)');
      if (r.zoom) check('erd-zoom', zoomName, r.zoom.changed, T(`${r.zoom.a} → 확대 ${r.zoom.b} → 맞춤 ${r.zoom.back}`, `${r.zoom.a} → zoom in ${r.zoom.b} → fit ${r.zoom.back}`));
      else check('erd-zoom', zoomName, null, T('이 화면에는 확대·축소 버튼이 없음(다시 렌더하면 생김)', 'This page has no zoom buttons (re-render to add them)'));
    }
    if (r.applied >= 1 && !r.empty.includes(r.applied)) exportChecks(r.lastExport, T(' (마지막 단계)', ' (last step)'), 'last');
    if (r.rangeExport) exportChecks(r.rangeExport, T(' (전체 구간)', ' (full range)'), 'range');
    const p = await evalJs(`(async () => {
      const dlg = document.getElementById('pal');
      if (!dlg || typeof openPal !== 'function') return null;
      const mac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
      const keyIn = (doc, KE) => doc.dispatchEvent(new KE('keydown', { key: 'k', code: 'KeyK', metaKey: mac, ctrlKey: !mac, bubbles: true }));
      const input = document.getElementById('pal-q');
      const search = q => { input.value = q; input.dispatchEvent(new Event('input')); };
      const enter = () => input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
      const out = {};
      keyIn(document, KeyboardEvent);
      out.opened = dlg.open;
      search('');
      window.__leakScan?.('palette');
      const counts = new Map();
      for (const e of Object.values(LIN)) if (e.kind === 'table') counts.set(lastName(e), (counts.get(lastName(e)) || 0) + 1);
      const t = Object.values(LIN).find(e => e.kind === 'table' && counts.get(lastName(e)) === 1);
      if (t) {
        search(lastName(t));
        out.name = lastName(t);
        out.first = document.querySelector('#pal-list .po .pt')?.textContent || '';
        enter();
        out.lin = state.lin === t.id;
        out.closed = !dlg.open;
      }
      const s = D.steps.findIndex(x => (x.sql || '').split('\\n').some(l => l.trim().length >= 8));
      if (s >= 0) {
        const lines = D.steps[s].sql.split('\\n'), k = lines.findIndex(l => l.trim().length >= 8);
        const needle = lines[k].trim().slice(0, 40).toLowerCase();
        if (!dlg.open) openPal();
        search(needle);
        const sqlOpts = () => [...(document.getElementById('pal-g-sql')?.parentElement.querySelectorAll('.po') || [])];
        let opt = sqlOpts().find(o => !o.classList.contains('more') && o.querySelector('.pr')?.textContent === vlabel(s + 1));
        const more = sqlOpts().find(o => o.classList.contains('more'));
        if (!opt && more) { palRun(+more.dataset.k); opt = sqlOpts().find(o => !o.classList.contains('more') && o.querySelector('.pr')?.textContent === vlabel(s + 1)); }
        const idx = opt ? +opt.dataset.k : -1;
        if (idx >= 0) {
          const frame = document.getElementById('frame');
          const loaded = new Promise(r => { frame.addEventListener('load', r, { once: true }); setTimeout(r, 15000); });
          palActivate(idx); enter();
          if (!frame.hidden) await loaded;
        }
        const found = [...document.querySelectorAll('#detail .sql-src .cl.found code')].map(c => c.textContent);
        const want = D.steps[s].sql.split('\\n').map((l, j) => [l, j]).filter(([l]) => l.toLowerCase().includes(needle)).map(([l]) => l);
        out.sql = { step: s + 1, idx, at: state.step, found: found.length, ok: idx >= 0 && state.step === s + 1 && found.length === want.length && found.every((f, j) => f.trim() === want[j].trim()) };
      }
      const frame = document.getElementById('frame');
      for (let k = 0; k < 100 && !frame.hidden; k++) {
        const d = frame.contentDocument;
        if (d?.readyState === 'complete' && d.getElementById('note')?.textContent) break;
        await new Promise(r => setTimeout(r, 100));
      }
      const fw = frame.contentWindow;
      if (fw && !frame.hidden) {
        if (dlg.open) dlg.close();
        keyIn(fw.document, fw.KeyboardEvent);
        await new Promise(r => setTimeout(r, 200));
        out.fromFrame = dlg.open;
        if (dlg.open) dlg.close();
      }
      return out; })()`);
    const palName = T('전체 검색(⌘K)이 열리고 결과로 이동함', 'Global search (⌘K) opens and navigates to results');
    if (!p) check('palette', palName, null, T('이 화면에는 전체 검색이 없음(다시 렌더하면 생김)', 'This page has no global search (re-render to add it)'));
    else {
      const parts = [T(`열림 ${p.opened}`, `opened ${p.opened}`)];
      let ok = p.opened;
      if (p.name) { ok &&= p.first.includes(p.name) && p.lin && p.closed; parts.push(T(`"${p.name}" 첫 결과 "${p.first}" · 계보 ${p.lin ? '열림' : '안 열림'}`, `"${p.name}" first result "${p.first}" · lineage ${p.lin ? 'opened' : 'not opened'}`)); }
      if (p.sql) { ok &&= p.sql.ok; parts.push(T(`SQL 원문 V${p.sql.step} 줄 표시 ${p.sql.ok ? '맞음' : `틀림(항목 ${p.sql.idx}, 단계 ${p.sql.at}, 표시 ${p.sql.found}줄)`}`, `SQL source V${p.sql.step} line highlight ${p.sql.ok ? 'correct' : `wrong (item ${p.sql.idx}, step ${p.sql.at}, ${p.sql.found} lines shown)`}`)); }
      if (p.fromFrame !== undefined) { ok &&= p.fromFrame; parts.push(T(`변경 화면 안에서 열림 ${p.fromFrame}`, `opens from inside the review ${p.fromFrame}`)); }
      check('palette', palName, ok, parts.join(' · '));
    }
  }

  const wide = await evalJs(`document.documentElement.scrollWidth - innerWidth`);
  check('overflow-1280', T('1280px 에서 가로 넘침 없음', 'No horizontal overflow at 1280px'), wide <= 1, T(`${wide}px 넘침`, `${wide}px overflow`));
  {
    const name = T('영어 화면에 한글 문구가 남지 않음', 'No Korean text left on the English page');
    if (pageLang !== 'en') check('english-no-hangul', name, null, T('화면 언어가 영어가 아니라 건너뜀', 'Skipped: the page language is not English'));
    else {
      const left = await evalJs(`(() => { window.__leakScan('final'); return window.__leaks; })()`);
      check('english-no-hangul', name, left.length === 0, left.length ? `${left.length} left: ${left.slice(0, 10).join(' | ')}` : '');
    }
  }

  try {
    const png = await S('Page.captureScreenshot', { format: 'png' });
    writeFileSync(shotPath, Buffer.from(png.data, 'base64'));
    screenshot = shotPath;
  } catch (e) { check('screenshot', T('스크린샷', 'Screenshot'), false, e.message); }

  await open(pageUrl, 375, 812, true);
  const narrow = await evalJs(`document.documentElement.scrollWidth - innerWidth`);
  check('overflow-375', T('375px 에서 가로 넘침 없음', 'No horizontal overflow at 375px'), narrow <= 1, T(`${narrow}px 넘침`, `${narrow}px overflow`));
  if (kind === 'timeline') {
    const inner = await evalJs(`(async () => {
      const frame = document.getElementById('frame');
      for (let k = 0; k < 50; k++) {
        const d = frame?.contentDocument;
        if (d && d.readyState === 'complete' && d.getElementById('stats')) {
          await new Promise(r => setTimeout(r, 200));
          return d.documentElement.scrollWidth - frame.contentWindow.innerWidth;
        }
        await new Promise(r => setTimeout(r, 100));
      }
      return null; })()`);
    const innerName = T('375px 에서 변경 화면 안 가로 넘침 없음', 'No horizontal overflow inside the review at 375px');
    if (inner === null) check('overflow-375-review', innerName, null, T('변경 화면을 띄우지 못해 건너뜀', 'Skipped: the review could not be shown'));
    else check('overflow-375-review', innerName, inner <= 1, T(`${inner}px 넘침`, `${inner}px overflow`));
  }

  const outside = [...new Set(requests.filter(u => u !== pageUrl && !/^(data|blob|about|chrome|devtools):/.test(u)))];
  check('no-external-requests', T('외부 요청·다른 파일 참조 없음', 'No external requests or other file references'), outside.length === 0, outside.slice(0, 5).join(', '));
  check('no-console-errors', T('스크립트·콘솔 오류 없음', 'No script or console errors'), errors.length === 0, errors.slice(0, 3).join(' | ').slice(0, 300));

  if (mermaidTexts.length) {
    const mm = findMermaid();
    if (!mm) {
      check('mermaid-parse', T('Mermaid ERD 가 파싱됨', 'Mermaid ERD parses'), null, T('mermaid.min.js 를 찾지 못해 건너뜀(--mermaid 또는 MERMAID_JS 로 지정)', 'Skipped: mermaid.min.js not found (set it with --mermaid or MERMAID_JS)'));
    } else {
      await open('about:blank', 1280, 900, false);
      await evalJs(readFileSync(mm.path, 'utf8') + '\n;0');
      const unique = [...new Map(mermaidTexts.map(m => [m.text, m])).values()];
      const results = await evalJs(`(async () => { const out = []; for (const m of ${JSON.stringify(unique)}) {
        try { await Promise.resolve().then(() => mermaid.parse(m.text)); out.push(null); } catch (e) { out.push(m.where + ': ' + String(e.message || e).slice(0, 160)); } }
        return out; })()`);
      const bad = results.filter(Boolean);
      check('mermaid-parse', T('Mermaid ERD 가 파싱됨', 'Mermaid ERD parses'), bad.length === 0, bad.length ? bad.join(' | ') : T(`mermaid ${mm.version}, ${unique.length}개`, `mermaid ${mm.version}, ${unique.length}`));
    }
  }
} catch (e) {
  check('check-run', T('점검 실행', 'Check run'), false, e.message);
}
finish();
