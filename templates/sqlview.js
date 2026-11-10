/* SQL 읽기 전용 보기: 구문 강조, 줄 단위 diff, 바뀐 단어 강조, 변경 없는 줄 접기. compare.html 과 timeline.html 이 함께 쓴다. */

/** 화면 언어. <html lang> 이 en 이면 영어, 그 밖은 한국어다. 이 파일이 두 화면에서 가장 먼저 실행되므로 아래 도우미를 여기 둔다. */
const UI_LANG = typeof document !== 'undefined' && document.documentElement.lang === 'en' ? 'en' : 'ko';
/** 화면 언어에 맞는 문구. @param ko 한국어 @param en 영어 */
const L = (ko, en) => UI_LANG === 'en' ? en : ko;
/**
 * 영어 화면에서 정적 HTML 의 영어 문구를 적용한다. data-en 은 textContent 를 바꾸므로 자식 요소가 없는 요소에만 단다.
 * data-en-placeholder, data-en-title, data-en-aria-label 은 같은 이름의 속성을 바꾼다.
 */
function applyLang(root = document) {
  if (UI_LANG !== 'en') return;
  root.querySelectorAll('[data-en]').forEach(el => { el.textContent = el.dataset.en; });
  for (const a of ['placeholder', 'title', 'aria-label']) {
    root.querySelectorAll(`[data-en-${a}]`).forEach(el => el.setAttribute(a, el.getAttribute(`data-en-${a}`)));
  }
}
const SQL_KW = new Set(('ACTION ADD ALL ALTER ALWAYS AND AS ASC BEGIN BY CASCADE CASE CHECK COLUMN COMMENT COMMIT CONCURRENTLY '
  + 'CONSTRAINT CREATE CROSS DEFAULT DEFERRABLE DEFERRED DELETE DESC DISTINCT DO DROP ELSE END EXISTS FALSE FOREIGN FROM FULL '
  + 'GENERATED GRANT GROUP HAVING IDENTITY IF IN INCLUDE INDEX INITIALLY INNER INSERT INTO IS JOIN KEY LEFT LIKE LIMIT '
  + 'MATERIALIZED NO NOT NULL OFFSET ON OR ORDER OUTER OWNER PRIMARY REFERENCES RENAME RESTRICT RETURNING REVOKE RIGHT '
  + 'SELECT SEQUENCE SET TABLE THEN TO TRUE TYPE UNION UNIQUE UPDATE USING VALUES VIEW WHEN WHERE WITH').split(' '));
const SQL_TYPES = new Set(('BIGINT BIGSERIAL BIT BOOL BOOLEAN BYTEA CHAR CHARACTER CIDR DATE DECIMAL DOUBLE FLOAT INET INT '
  + 'INTEGER INTERVAL JSON JSONB MONEY NUMERIC PRECISION REAL SERIAL SMALLINT SMALLSERIAL TEXT TIME TIMESTAMP TIMESTAMPTZ '
  + 'TIMETZ TSVECTOR UUID VARCHAR VARYING XML ZONE').split(' '));
const SQL_TOKEN = new RegExp([
  /(--[^\n]*)/, /(\/\*[\s\S]*?(?:\*\/|$))/,
  /([Ee]'(?:[^'\\]|''|\\[\s\S])*(?:'|$))/, /((?:[Uu]&)?'(?:[^']|'')*(?:'|$))/,
  /("(?:[^"]|"")*(?:"|$))/, /(\$([A-Za-z_]\w*)?\$[\s\S]*?(?:\$\7\$|$))/,
  /(\b0[xX][0-9a-fA-F]+\b|\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)/,
  /([A-Za-z_][A-Za-z0-9_$]*)(?=(\()?)/, /(\s+)/, /([\s\S])/,
].map(r => r.source).join('|'), 'g');

/** 이 키워드 바로 뒤의 이름(괄호가 붙어도)은 함수가 아니라 테이블·인덱스 이름이다. */
const NOT_FUNC_AFTER = new Set(['TABLE', 'REFERENCES', 'ON', 'INTO', 'INDEX', 'VIEW', 'EXISTS', 'ONLY', 'UPDATE', 'JOIN', 'FROM', 'COPY']);

const sqlEsc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

/**
 * 텍스트 전체를 한 번에 토큰으로 나눈 뒤 줄별로 자른다. 여러 줄에 걸친 문자열·주석도 같은 종류로 칠해진다.
 * 종류: k 키워드, y 타입, s 문자열, c 주석, n 숫자, f 함수, '' 그 밖.
 * @return 줄마다 [{c, t}] 배열
 */
function sqlTokenLines(text) {
  const lines = [[]];
  SQL_TOKEN.lastIndex = 0;
  let m, prev = '';
  while ((m = SQL_TOKEN.exec(text)) && m[0] !== '') {
    let c = '';
    if (m[1] || m[2]) c = 'c';
    else if (m[3] || m[4] || m[6]) c = 's';
    else if (m[8]) c = 'n';
    else if (m[9]) {
      const u = m[9].toUpperCase();
      c = SQL_KW.has(u) ? 'k' : SQL_TYPES.has(u) ? 'y' : m[10] && !NOT_FUNC_AFTER.has(prev) ? 'f' : '';
    }
    // 공백·주석, 스키마 한정자(public.)와 점은 건너뛰어 직전 키워드를 유지한다.
    const qualifier = m[9] && text[SQL_TOKEN.lastIndex] === '.';
    if (!m[11] && !m[1] && !m[2] && m[0] !== '.' && !qualifier) prev = m[9] ? m[9].toUpperCase() : m[0];
    m[0].split('\n').forEach((piece, k) => {
      if (k) lines.push([]);
      if (piece) lines[lines.length - 1].push({ c, t: piece });
    });
  }
  return lines;
}

const sqlTokHtml = toks => toks.map(x => x.c ? `<span class="sx-${x.c}">${sqlEsc(x.t)}</span>` : sqlEsc(x.t)).join('');

/** 두 줄에서 앞뒤로 같은 토큰을 빼고 가운데만 del/ins 로 감싼다. */
function sqlWordDiff(x, y) {
  let p = 0;
  while (p < x.length && p < y.length && x[p].t === y[p].t) p++;
  let q = 0;
  while (q < x.length - p && q < y.length - p && x[x.length - 1 - q].t === y[y.length - 1 - q].t) q++;
  const wrap = (toks, tag) => {
    const mid = toks.slice(p, toks.length - q);
    return sqlTokHtml(toks.slice(0, p)) + (mid.length ? `<${tag} class="w">${sqlTokHtml(mid)}</${tag}>` : '') + sqlTokHtml(toks.slice(toks.length - q));
  };
  return [wrap(x, 'del'), wrap(y, 'ins')];
}

/**
 * 줄 단위 LCS diff. 줄 끝 쉼표는 비교에서 뺀다(마지막 컬럼 뒤에 컬럼이 붙어도 그 줄은 같은 줄로 본다).
 * @return [{st: 'same'|'added'|'dropped', a: 변경 전 줄 번호, b: 변경 후 줄 번호, html}]
 */
function sqlLineDiff(before, after) {
  const trim = t => t == null ? null : String(t).replace(/\s+$/, '');
  const a = trim(before), b = trim(after);
  const A = a == null ? [] : a.split('\n'), B = b == null ? [] : b.split('\n');
  const TA = a == null ? [] : sqlTokenLines(a), TB = b == null ? [] : sqlTokenLines(b);
  const key = l => l.replace(/,\s*$/, '');
  const L = Array.from({ length: A.length + 1 }, () => new Int32Array(B.length + 1));
  for (let i = A.length - 1; i >= 0; i--) for (let j = B.length - 1; j >= 0; j--) {
    L[i][j] = key(A[i]) === key(B[j]) ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  }
  const out = [];
  let i = 0, j = 0, dels = [], adds = [];
  const flush = () => {
    const n = Math.min(dels.length, adds.length);
    for (let k = 0; k < n; k++) [dels[k].html, adds[k].html] = sqlWordDiff(dels[k].toks, adds[k].toks);
    out.push(...dels, ...adds);
    dels = []; adds = [];
  };
  while (i < A.length || j < B.length) {
    if (i < A.length && j < B.length && key(A[i]) === key(B[j])) {
      flush();
      out.push({ st: 'same', a: i + 1, b: j + 1, toks: TB[j] });
      i++; j++;
    } else if (j < B.length && (i >= A.length || L[i][j + 1] >= L[i + 1][j])) {
      adds.push({ st: 'added', a: null, b: j + 1, toks: TB[j] }); j++;
    } else {
      dels.push({ st: 'dropped', a: i + 1, b: null, toks: TA[i] }); i++;
    }
  }
  flush();
  out.forEach(l => { if (l.html == null) l.html = sqlTokHtml(l.toks); });
  return out;
}

/**
 * diff 줄 목록을 코드 보기 HTML 로. 변경에서 context 줄보다 먼 같은 줄은 접는다.
 * @param opts.single true 면 줄 번호 열을 하나(변경 후)만 둔다.
 */
function sqlDiffHtml(lines, opts = {}) {
  const ctx = opts.context ?? 3;
  const changed = lines.map(l => l.st !== 'same');
  const near = lines.map((_, k) => !changed.some(x => x) || lines.some((__, m) => changed[m] && Math.abs(m - k) <= ctx));
  const sign = { same: ' ', added: '+', dropped: '−' };
  const row = l => `<div class="cl cl-${l.st}"><span class="cn">${l.a ?? ''}</span>${opts.single ? '' : `<span class="cn">${l.b ?? ''}</span>`}`
    + `<span class="cs">${sign[l.st]}</span><code>${l.html || ' '}</code></div>`;
  let html = '', k = 0;
  while (k < lines.length) {
    if (near[k]) { html += row(lines[k++]); continue; }
    const start = k;
    while (k < lines.length && !near[k]) k++;
    const run = lines.slice(start, k);
    html += run.length < 4 ? run.map(row).join('')
      : `<div class="cfold"><button type="button" class="cfold-btn">⋯ ${L(`변경 없는 ${run.length}줄 펼치기`, `Show ${run.length} unchanged lines`)}</button><template>${run.map(row).join('')}</template></div>`;
  }
  return `<div class="code${opts.single ? ' single' : ''}">${html}</div>`;
}

/** 변경 표시 없이 줄 번호와 구문 강조만 있는 코드 보기. */
function sqlPlainHtml(text, start = 1) {
  return sqlTokenLines(String(text)).map((toks, k) => `<div class="cl"><span class="cn">${start + k}</span><code>${sqlTokHtml(toks) || ' '}</code></div>`).join('');
}

/** 접어 둔 줄을 펼친다. 펼친 첫 줄을 돌려준다. */
function sqlUnfold(fold) {
  const frag = fold.querySelector('template').content.cloneNode(true);
  const first = frag.firstElementChild;
  fold.replaceWith(frag);
  return first;
}
const sqlUnfoldAll = root => root.querySelectorAll('.cfold').forEach(sqlUnfold);
document.addEventListener('click', e => {
  const btn = e.target.closest?.('.cfold-btn');
  if (!btn) return;
  const first = sqlUnfold(btn.parentElement);
  if (first) { first.tabIndex = -1; first.focus({ preventScroll: true }); }
});

/** 클립보드에 복사한다. 실패하면 false. 포커스는 호출 전 요소로 돌려놓는다. */
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch (e) {}
  const back = document.activeElement;
  const ta = Object.assign(document.createElement('textarea'), { value: text, readOnly: true });
  ta.style.cssText = 'position:fixed;opacity:0';
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand('copy'); } catch (e) {}
  ta.remove();
  back?.focus?.();
  return ok;
}
