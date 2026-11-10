// HTML 파일을 헤드리스 Chrome 으로 열고 JS 식을 평가해 결과를 JSON 으로 낸다.
//
// 사용법: node tests/cdp_eval.mjs <html> <식>
// 종료 코드: 0 성공, 1 식 오류, 2 Chrome 없음.
import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const [file, expr] = process.argv.slice(2);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const chromePath = [process.env.CHROME_PATH, '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '/usr/bin/google-chrome', '/usr/bin/chromium'].find(p => p && existsSync(p));
if (!chromePath) { console.error('Chrome 을 찾지 못했습니다'); process.exit(2); }
const profile = mkdtempSync(join(tmpdir(), 'schema-eval-'));
const chrome = spawn(chromePath, ['--headless=new', '--disable-gpu', '--no-first-run', `--user-data-dir=${profile}`, '--remote-debugging-port=0', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
let code = 0;
try {
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
  const send = (method, params = {}, sessionId) => new Promise((res, rej) => { const id = ++seq; pending.set(id, { res, rej }); ws.send(JSON.stringify({ id, method, params, sessionId })); });
  const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
  const S = (m, p) => send(m, p, sessionId);
  await S('Page.enable');
  await S('Runtime.enable');
  const loaded = new Promise(r => { const l = m => { if (m.method === 'Page.loadEventFired') { listeners.delete(l); r(); } }; listeners.add(l); });
  await S('Page.navigate', { url: pathToFileURL(resolve(file)).href });
  await loaded;
  await sleep(300);
  const r = await S('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) { console.error(r.exceptionDetails.exception?.description || r.exceptionDetails.text); code = 1; }
  else console.log(JSON.stringify(r.result.value));
  ws.close();
} finally {
  chrome.kill('SIGKILL');
  for (let i = 0; i < 10; i++) { try { rmSync(profile, { recursive: true, force: true }); break; } catch (e) { await sleep(200); } }
}
process.exit(code);
