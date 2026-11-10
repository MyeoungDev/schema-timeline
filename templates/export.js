/*
 * 내보내기: 테이블 목록을 Mermaid erDiagram 으로, 텍스트·Blob 을 파일로.
 * 비교 화면(compare.html)과 명령(scripts/export_erd.mjs)이 함께 쓴다.
 * 기본 출력은 Mermaid 10.5 이상(엔티티 별칭, 유니코드 이름)용이다. compat 'legacy' 는 별칭 없이 영문·숫자·_ 이름만 쓰고
 * 바꾼 이름의 원래 이름은 %% 주석으로 남긴다.
 */

/** format_type 결과를 짧게. 비교 화면의 shortType 과 같은 규칙이다. */
function erType(t) {
  return t == null ? '' : String(t)
    .replace(/character varying/g, 'varchar').replace(/^character\(/, 'char(')
    .replace(/timestamp without time zone/g, 'timestamp').replace(/timestamp with time zone/g, 'timestamptz')
    .replace(/^integer$/, 'int').replace(/^boolean$/, 'bool').replace(/^double precision$/, 'float8');
}

/** erDiagram 문법에서 이름으로 쓰면 해석이 깨지는 낱말(대소문자 무관). */
const ER_RESERVED = new Set(['erdiagram', 'style', 'classdef', 'class', 'one', 'many', 'to', 'only', 'zero', 'or', 'more',
  'optionally', 'direction', 'title', 'acctitle', 'accdescr', 'u']);
const ER_KEYS = new Set(['pk', 'fk', 'uk']);
const ER_WORD = { modern: /^[\p{L}_][\p{L}\p{N}_-]*$/u, legacy: /^[A-Za-z_][A-Za-z0-9_]*$/ };

/** 설명 문자열에 넣을 텍스트. 따옴표·줄바꿈·제어문자를 정리한다. */
const erQuote = s => String(s).replace(/"/g, "'").replace(/[\u0000-\u001f\u007f]+/g, ' ').replace(/\s+/g, ' ').trim();
/** 엔티티 별칭에 넣을 텍스트. 별칭 안에서는 \ 와 % 가 해석을 깨므로 전각 문자로 바꾼다. */
const erAlias = s => erQuote(s).replace(/\\/g, '\uff3c').replace(/%/g, '\uff05');

/**
 * 테이블 목록을 Mermaid erDiagram 텍스트로 만든다.
 * 타입 토큰에 쉼표·공백을 쓸 수 없으므로 numeric(14,2) 처럼 그런 타입은 토큰을 numeric 으로 줄이고 전체 타입을 설명에 적는다.
 * 관계선은 FK 컬럼이 PK 에 들어 있으면 실선(식별), 아니면 점선이다.
 * @param tables [{name, comment, columns: [{name, type, nullable, comment}], constraints: [{kind, columns, ref_table}], indexes?: [{unique, columns, definition}]}]
 * @param opts.title 맨 위 주석, opts.compat 'modern'(기본) | 'legacy'
 */
function mermaidER(tables, opts = {}) {
  const legacy = opts.compat === 'legacy';
  const word = ER_WORD[legacy ? 'legacy' : 'modern'];
  const safe = s => String(s).replace(new RegExp(`[^${legacy ? 'A-Za-z0-9_' : '\\p{L}\\p{N}_'}]`, 'gu'), '_').replace(/^(?=[\p{N}-])/u, '_');
  const names = new Set(tables.map(t => t.name));
  const ids = new Map(), used = new Set();
  for (const t of tables) {
    let base = word.test(t.name) && !ER_RESERVED.has(t.name.toLowerCase()) ? t.name : `e_${safe(t.name)}`;
    let id = base, k = 2;
    while (used.has(id.toLowerCase())) id = `${base}_${k++}`;
    used.add(id.toLowerCase()); ids.set(t.name, id);
  }
  const uniqueSets = t => [
    ...(t.constraints || []).filter(k => k.kind === 'p' || k.kind === 'u').map(k => k.columns || []),
    ...(t.indexes || []).filter(i => i.unique && !/\bWHERE\b/i.test(i.definition || '')).map(i => i.columns || []),
  ].filter(s => s.length);
  const lines = [];
  if (opts.title) lines.push(`%% ${erQuote(opts.title)}`);
  lines.push('erDiagram');
  for (const t of tables) {
    const id = ids.get(t.name);
    const keys = new Map();
    const add = (col, key) => { if (!keys.has(col)) keys.set(col, []); if (!keys.get(col).includes(key)) keys.get(col).push(key); };
    for (const k of t.constraints || []) {
      if (k.kind === 'p') (k.columns || []).forEach(c => add(c, 'PK'));
      if (k.kind === 'f') (k.columns || []).forEach(c => add(c, 'FK'));
    }
    for (const s of uniqueSets(t)) if (s.length === 1 && !(keys.get(s[0]) || []).includes('PK')) add(s[0], 'UK');
    if (t.comment) lines.push(`  %% ${erQuote(t.comment)}`);
    if (legacy && id !== t.name) lines.push(`  %% ${id}: ${erQuote(t.name)}`);
    lines.push(`  ${id === t.name || legacy ? id : `${id}["${erAlias(t.name)}"]`} {`);
    const colUsed = new Set();
    for (const c of t.columns) {
      let nameTok = word.test(c.name) && !ER_KEYS.has(c.name.toLowerCase()) ? c.name : `${safe(c.name)}_`;
      while (colUsed.has(nameTok.toLowerCase())) nameTok += '_';
      colUsed.add(nameTok.toLowerCase());
      const full = erType(c.type);
      const simple = /^[A-Za-z_][A-Za-z0-9_()[\]-]*$/.test(full) && !ER_KEYS.has(full.toLowerCase());
      let typeTok = simple ? full : (full.replace(/\(.*$/, '').replace(/\[\]$/, '_array').replace(/[^A-Za-z0-9_]/g, '_') || 'type');
      if (ER_KEYS.has(typeTok.toLowerCase())) typeTok += '_';
      const notes = [nameTok === c.name ? '' : `${typeof L === 'function' ? L('이름', 'Name') : '이름'} ${c.name}`, simple ? '' : full,
        c.nullable === false && !(keys.get(c.name) || []).includes('PK') ? 'NOT NULL' : '', c.comment || '']
        .filter(Boolean).map(erQuote);
      const keyTok = (keys.get(c.name) || []).join(', ');
      lines.push(`    ${typeTok} ${nameTok}${keyTok ? ` ${keyTok}` : ''}${notes.length ? ` "${notes.join(' · ')}"` : ''}`);
    }
    lines.push('  }');
  }
  for (const t of tables) {
    const pk = (t.constraints || []).find(k => k.kind === 'p')?.columns || [];
    for (const k of t.constraints || []) {
      if (k.kind !== 'f' || !names.has(k.ref_table)) continue;
      const cols = k.columns || [];
      const nullable = cols.some(c => t.columns.find(x => x.name === c)?.nullable !== false);
      const unique = uniqueSets(t).some(s => s.every(c => cols.includes(c)));
      const identifying = cols.length > 0 && cols.every(c => pk.includes(c));
      const line = identifying ? '--' : '..';
      lines.push(`  ${ids.get(k.ref_table)} ${nullable ? '|o' : '||'}${line}${unique ? 'o|' : 'o{'} ${ids.get(t.name)} : "${erQuote(cols.join(', '))}"`);
    }
  }
  return lines.join('\n') + '\n';
}

/** 텍스트나 Blob 을 파일로 내려받게 한다. */
function saveFile(name, data, type = 'text/plain;charset=utf-8') {
  const blob = data instanceof Blob ? data : new Blob([data], { type });
  const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: name });
  a.dataset.export = '';
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

if (typeof module !== 'undefined') module.exports = { mermaidER, erType };
