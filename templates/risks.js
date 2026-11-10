/*
 * 비교 화면(compare.html)의 위험 판정. DOM 을 쓰지 않는 순수 함수라 브라우저와 Node(scripts/report.mjs)가 같은 규칙을 쓴다.
 * 브라우저에서는 화면 언어 함수 L(sqlview.js)을, Node 에서는 computeRisks 의 opts.lang 으로 만든 함수를 쓴다.
 */
/** 영어 복수형. many 를 안 주면 one 뒤에 s 를 붙인다. */
const plural = (n, one, many = one + 's') => `${n} ${n === 1 ? one : many}`;
/** 이름 변경이 근거(confidence)로 확정됐는지. */
const confirmed = x => x.confidence === 'confirmed';
/** 테이블이 통째로 추가·삭제됐는지. */
const isWhole = t => t.status === 'added' || t.status === 'dropped';
const objKey = o => `${o.type}|${o.name}`;
const isTrigger = o => String(o.type).toUpperCase() === 'TRIGGER';
const isCallable = o => ['FUNCTION', 'PROCEDURE', 'PACKAGE'].includes(String(o.type).toUpperCase());
/** 테이블·뷰 비교 밖의 객체(트리거·함수 등). 값이 없는 데이터(구버전)는 빈 목록이다. */
const normObjects = o => ({ added: o?.added || [], dropped: o?.dropped || [], changed: o?.changed || [], invalid: o?.invalid || [] });

const shortType = t => t == null ? '' : t
  .replace(/character varying/g, 'varchar').replace(/^character\(/, 'char(')
  .replace(/timestamp without time zone/g, 'timestamp').replace(/timestamp with time zone/g, 'timestamptz')
  .replace(/^integer$/, 'int').replace(/^boolean$/, 'bool').replace(/^double precision$/, 'float8');

function typeParts(t) {
  const m = /^(.*?)\((\d+)(?:,(\d+))?\)(.*)$/.exec(t || '');
  return m ? { base: m[1] + m[4], len: +m[2], scale: m[3] == null ? null : +m[3] } : { base: t, len: null, scale: null };
}

/** 값을 잃지 않고 바꿀 수 있는 타입 계열 변경. */
const WIDEN = {
  smallint: ['integer', 'bigint', 'numeric', 'real', 'double precision'],
  integer: ['bigint', 'numeric', 'double precision'],
  bigint: ['numeric'],
  real: ['double precision'],
  character: ['character varying', 'text'],
  'character varying': ['text'],
};

/** 정밀도·소수 자릿수를 가진 숫자 계열 이름(PostgreSQL numeric, Oracle NUMBER, MySQL decimal). */
const NUMERIC_BASE = /^(numeric|number|decimal|dec)$/i;

/** 소수초 정밀도를 가진 시간 타입과 엔진별 기본 정밀도(자릿수를 적지 않았을 때). */
const FRACTIONAL_BASE = /^(timestamp|timestamptz|time|datetime)\b/i;
const FRACTION_DEFAULT = { postgres: 6, oracle: 6, mysql: 0, mariadb: 0 };

/**
 * 타입 변경의 위험 수준. scripts/timeline.py 의 type_narrows 와 같은 기준이다.
 * @return {'fail'|'round'|'family'|null} fail 은 크기 축소(더 큰 값이 있으면 적용 실패), round 는 소수 자릿수·소수초 정밀도 축소로 기존 값이 반올림됨(Oracle 은 fail),
 *   family 는 변환 확인이 필요한 계열 변경, null 은 안전한 확장
 */
function typeRisk(before, after, engine = 'postgres') {
  const b = typeParts(before), a = typeParts(after), oracle = engine === 'oracle';
  if (b.base !== a.base) return (WIDEN[b.base] || []).includes(a.base) && (a.base !== 'numeric' || a.len == null) ? null : 'family';
  if (FRACTIONAL_BASE.test(b.base || '')) {
    const dflt = FRACTION_DEFAULT[engine] ?? 6, fb = b.len ?? dflt, fa = a.len ?? dflt;
    return fa >= fb ? null : oracle ? 'fail' : 'round';
  }
  if (b.len == null) return a.len == null ? null : 'fail';
  if (a.len == null) return null;
  if (NUMERIC_BASE.test(b.base)) {
    const bs = b.scale || 0, as = a.scale || 0;
    if (as < bs && !oracle) return 'round';
    return a.len - as < b.len - bs || as < bs ? 'fail' : null;
  }
  return a.len < b.len || (a.scale != null && b.scale != null && a.scale < b.scale) ? 'fail' : null;
}


const PG_RESERVED = new Set(('all analyse analyze and any array as asc asymmetric both case cast check collate column constraint create current_catalog current_date '
  + 'current_role current_time current_timestamp current_user default deferrable desc distinct do else end except false fetch for foreign from grant group having in '
  + 'initially intersect into lateral leading limit localtime localtimestamp not null offset on only or order placing primary references returning select '
  + 'session_user some symmetric table then to trailing true union unique user using variadic when where window with '
  + 'authorization binary collation concurrently cross current_schema freeze full ilike inner is isnull join left like natural notnull outer overlaps '
  + 'right similar tablesample verbose between bigint bit boolean char character coalesce dec decimal exists extract float greatest grouping inout int '
  + 'integer interval least national nchar none normalize nullif numeric out overlay position precision real row setof smallint substring time timestamp '
  + 'treat trim values varchar xmlattributes xmlconcat xmlelement xmlexists xmlforest xmlnamespaces xmlparse xmlpi xmlroot xmlserialize xmltable').split(' '));
const ORA_RESERVED = new Set(('ACCESS ADD ALL ALTER AND ANY AS ASC AUDIT BETWEEN BY CHAR CHECK CLUSTER COLUMN COMMENT COMPRESS CONNECT CREATE CURRENT DATE DECIMAL '
  + 'DEFAULT DELETE DESC DISTINCT DROP ELSE EXCLUSIVE EXISTS FILE FLOAT FOR FROM GRANT GROUP HAVING IDENTIFIED IMMEDIATE IN INCREMENT INDEX INITIAL INSERT INTEGER '
  + 'INTERSECT INTO IS LEVEL LIKE LOCK LONG MAXEXTENTS MINUS MLSLABEL MODE MODIFY NOAUDIT NOCOMPRESS NOT NOWAIT NULL NUMBER OF OFFLINE ON ONLINE OPTION OR ORDER '
  + 'PCTFREE PRIOR PUBLIC RAW RENAME RESOURCE REVOKE ROW ROWID ROWNUM ROWS SELECT SESSION SET SHARE SIZE SMALLINT START SUCCESSFUL SYNONYM SYSDATE TABLE THEN TO '
  + 'TRIGGER UID UNION UNIQUE UPDATE USER VALIDATE VALUES VARCHAR VARCHAR2 VIEW WHENEVER WHERE WITH').split(' '));

/** 식별자를 엔진 규칙대로 인용한다. 따옴표 없이 써도 같은 이름이 되는 경우는 그대로 둔다. MySQL·MariaDB 는 늘 백틱으로 감싼다. */
function quoteIdent(name, engine) {
  const n = String(name);
  if (engine === 'mysql' || engine === 'mariadb') return '`' + n.replace(/`/g, '``') + '`';
  if (engine === 'oracle') return /^[A-Z][A-Z0-9_$#]*$/.test(n) && !ORA_RESERVED.has(n) ? n : `"${n.replace(/"/g, '""')}"`;
  return /^[a-z_][a-z0-9_$]*$/.test(n) && !PG_RESERVED.has(n) ? n : `"${n.replace(/"/g, '""')}"`;
}

/** PostgreSQL 에서 테이블을 다시 쓰지 않고 바꿀 수 있는 타입 변경(길이·정밀도를 늘리거나 제한을 없애는 경우). */
function pgNoRewrite(before, after) {
  const b = typeParts(before), a = typeParts(after);
  if (b.base === 'character varying' && a.base === 'text') return true;
  if (b.base !== a.base) return false;
  if (b.base === 'character varying') return a.len == null || (b.len != null && a.len >= b.len);
  if (NUMERIC_BASE.test(b.base || '')) return a.len == null || (b.len != null && a.len >= b.len && (a.scale || 0) === (b.scale || 0));
  if (FRACTIONAL_BASE.test(b.base || '') || /^interval\b/i.test(b.base || '')) return (a.len ?? 6) >= (b.len ?? 6);
  return false;
}

/** PostgreSQL 에서 컬럼 추가 시 모든 행을 채워야 해 테이블을 다시 쓰게 하는 기본값(휘발성 함수)·identity·저장 생성 컬럼. */
const pgRewritingAdd = c => !!c.identity || c.generated === 's' || /\b(nextval|random|gen_random_uuid|uuid_generate_v\d\w*|clock_timestamp|timeofday)\s*\(/i.test(c.default || '');

/**
 * 인덱스가 컬럼만으로 된 일반 인덱스면 그 컬럼 이름들, 부분(WHERE)·식·함수 인덱스면 null. 정렬 방향(ASC·DESC)은 뗀다.
 * PostgreSQL 은 정의 문(USING … (…))에서, 그 밖은 columns 에서 읽는다.
 */
function plainIndexColumns(i) {
  const def = i.definition || '';
  if (/\bWHERE\b/i.test(def)) return null;
  const m = /USING \w+ \((.*?)\)(?: INCLUDE \(|$)/.exec(def);
  if (m && m[1].includes('(')) return null;
  const parts = m ? m[1].split(',') : (i.columns || []);
  const cols = parts.map(p => String(p).trim().replace(/\s+(ASC|DESC)(\s+NULLS\s+(FIRST|LAST))?$/i, '').replace(/^"(.*)"$/, '$1'));
  if (!cols.length || cols.some(c => !/^[A-Za-z_][\w$#]*$/.test(c))) return null;
  if (m && i.columns && i.columns.length !== cols.length) return null;
  return cols;
}

/** CHECK 정의에서 괄호 안 조건식만 꺼낸다. NOT VALID·NOVALIDATE 같은 뒤 꼬리는 버린다. 찾지 못하면 null. */
function checkExpr(def) {
  const at = /\bCHECK\s*\(/i.exec(def || '');
  if (!at) return null;
  let depth = 0, start = at.index + at[0].length - 1;
  for (let j = start; j < def.length; j++) {
    if (def[j] === '(') depth++;
    else if (def[j] === ')' && --depth === 0) {
      let e = def.slice(start + 1, j).trim();
      while (/^\(.*\)$/s.test(e) && checkExpr(`CHECK ${e}`) === e.slice(1, -1).trim()) e = e.slice(1, -1).trim();
      return e;
    }
  }
  return null;
}

/**
 * MySQL·MariaDB 에서 테이블 복사 없이 바꿀 수 있는 타입 변경. VARCHAR 를 늘리되 길이 접두 바이트 수가 그대로인 경우(utf8mb4 기준
 * 63자 이하끼리, 64자 이상끼리)와 ENUM·SET 끝에 값을 덧붙이는 경우다. 문자 집합이 한 바이트짜리면 실제로는 더 넓게 제자리 변경된다.
 */
function mysqlInPlace(before, after) {
  const list = t => { const m = /^(enum|set)\((.*)\)$/i.exec(t || ''); return m ? [m[1].toLowerCase(), m[2]] : null; };
  const lb = list(before), la = list(after);
  if (lb && la) return lb[0] === la[0] && la[1].startsWith(lb[1]) && (la[1].length === lb[1].length || la[1][lb[1].length] === ',');
  const b = typeParts(before), a = typeParts(after);
  if (!/^varchar$/i.test(b.base || '') || b.base.toLowerCase() !== (a.base || '').toLowerCase() || b.len == null || a.len == null || a.len < b.len) return false;
  return (b.len <= 63) === (a.len <= 63);
}

/** 이 컬럼들이 모두 같은 단계에서 추가됐고 기본값이 없어 기존 행에 값이 없는지. 제약이 기존 데이터에 걸리지 않는다. */
const allNewEmpty = (t, names) => names?.length > 0 && names.every(n => t.columns.some(c => c.name === n && c.status === 'added' && c.after.default == null && !c.after.identity));

const LOSS_KINDS = new Set(['table_dropped', 'column_dropped', 'table_recreated', 'column_recreated']);

function constraintText(k) {
  if (k.kind === 'p' || k.kind === 'u') return k.columns.join(', ');
  if (k.kind === 'f') {
    const opt = k.definition.replace(/^FOREIGN KEY \(.*?\) REFERENCES .*?\(.*?\)\s*/, '');
    return `${k.columns.join(', ')} → ${k.ref_table}.${(k.ref_columns || []).join(', ')}${opt ? ' ' + opt : ''}`;
  }
  if (k.kind === 'c') return k.definition.replace(/^CHECK \(\((.*)\)\)$/, '$1').replace(/^CHECK \((.*)\)$/, '$1');
  return k.definition;
}

function indexText(i) {
  const m = /USING (\w+) \((.*?)\)(?: INCLUDE \((.*?)\))?(?: WHERE (.*))?$/.exec(i.definition) || [];
  let s = m[2] || (i.columns || []).join(', ');
  if (m[1] && m[1] !== 'btree') s += ` · ${m[1]}`;
  if (m[3]) s += ` · INCLUDE ${m[3]}`;
  if (m[4]) s += ` · WHERE ${m[4]}`;
  return s;
}


const OBJ_NOUN = { TRIGGER: ['트리거', 'Trigger'], FUNCTION: ['함수', 'Function'], PROCEDURE: ['프로시저', 'Procedure'], PACKAGE: ['패키지', 'Package'], SEQUENCE: ['시퀀스', 'Sequence'],
  TYPE: ['타입', 'Type'], SYNONYM: ['시노님', 'Synonym'] };

/** 언어 함수 L 로 만든 위험 문구 도우미: 이름 변경·재생성 안내와 객체 종류 이름. */
function riskTexts(L) {
  return {
    RENAME_HINT: L('DDL 두 개만으로는 이름 변경인지, 삭제 후 추가인지 확정할 수 없습니다.', 'A DDL pair alone cannot tell a rename from a drop plus an add.'),
    RECREATE_HINT: L('지웠다가 같은 이름으로 다시 만들었습니다. 기존 데이터는 사라집니다.', 'Dropped and recreated under the same name. Existing data is lost.'),
    objNoun: t => { const n = OBJ_NOUN[String(t || '').toUpperCase()]; return n ? L(n[0], n[1]) : String(t || 'object').toLowerCase().replace(/^./, c => c.toUpperCase()); },
  };
}

/** opts 로 언어 함수를 고른다. opts.L, opts.lang('en'|'ko'), 둘 다 없으면 전역 L(브라우저), 그것도 없으면 한국어. */
function riskLang(opts = {}) {
  if (opts.L) return opts.L;
  if (opts.lang) return (ko, en) => opts.lang === 'en' ? en : ko;
  return typeof L === 'function' ? L : ko => ko;
}

/**
 * 리뷰어가 먼저 봐야 할 변경 목록. level 은 danger(데이터 유실·컴파일 오류) 또는 attention(실패·오판 가능).
 * @param D 비교 결과(tables, views, events, engine, objects, range_steps 를 가진 compare.html 의 데이터)
 * @param opts {lang, L} 문구 언어
 * @return [{level, kind, lead, why, what, table, col, step, ...}] 위험한 순서(테이블 전체 > 데이터 유실 > 그 밖)
 */
function computeRisks(D, opts = {}) {
  const L = riskLang(opts);
  const ENGINE = D.engine || 'postgres';
  const OBJS = normObjects(D.objects);
  const RANGE_STEPS = D.range_steps || [];
  const { RENAME_HINT, RECREATE_HINT, objNoun } = riskTexts(L);
  const objOn = o => o.table ? L(` (${o.table})`, ` on ${o.table}`) : '';
  const isLossEvent = e => LOSS_KINDS.has(e.kind) || (e.kind === 'column_type' && typeRisk(e.before, e.after, ENGINE) === 'round');
  const Q = n => quoteIdent(n, ENGINE);
  const count = (tb, where) => `SELECT COUNT(*) FROM ${Q(tb)}${where ? ` WHERE ${where}` : ''};`;
  /** 확인 쿼리. expect 가 zero 면 결과가 0 이어야 안전하고, affected 면 결과가 영향받는 행 수다. */
  const check = (sql, expect = 'zero') => sql ? { sql, expect } : undefined;
  const refOf = name => D.tables.find(x => x.name === name) || D.tables.find(x => x.before_name === name);
  /** 변경 전 이름. 같은 단계에서 이름이 바뀌었으면 옛 이름을 쓴다. */
  const beforeCol = (t, n) => t.columns.find(c => c.name === n)?.before?.name || n;
  const isNewCol = (t, n) => t.columns.some(c => c.name === n && c.status === 'added');
  const nn = (cols, alias = '') => cols.map(x => `${alias}${Q(x)} IS NOT NULL`).join(' AND ');
  /** 키 컬럼 묶음의 중복 값. NULL 이 섞인 행은 엔진마다 다르게 다룬다(Oracle 은 모두 NULL 인 행만 뺀다). */
  const dupCheck = (tb, cols) => {
    const list = cols.map(Q).join(', ');
    const where = ENGINE === 'oracle' ? cols.map(x => `${Q(x)} IS NOT NULL`).join(' OR ') : nn(cols);
    return `SELECT ${list}, COUNT(*) FROM ${Q(tb)} WHERE ${where} GROUP BY ${list} HAVING COUNT(*) > 1;`;
  };
  /** 크기를 줄이는 타입 변경에서 걸리는 행. Oracle NUMBER 는 컬럼이 비어 있어야 정밀도·소수 자릿수를 줄일 수 있다. */
  const shrinkCheck = (tb, col, before, after) => {
    const b = typeParts(before), a = typeParts(after), C = Q(col);
    if (NUMERIC_BASE.test(b.base || '')) {
      if (ENGINE === 'oracle') return count(tb, `${C} IS NOT NULL`);
      return count(tb, `ABS(${C}) >= POWER(10, ${(a.len ?? 38) - (a.scale || 0)})${(a.scale || 0) < (b.scale || 0) ? ` OR ${C} <> ROUND(${C}, ${a.scale || 0})` : ''}`);
    }
    if (FRACTIONAL_BASE.test(b.base || '') || a.len == null) return null;
    const len = ENGINE === 'oracle' ? (/\bCHAR\s*\)/i.test(after) || /^N(VAR)?CHAR/i.test(after) ? 'LENGTH' : 'LENGTHB') : ENGINE === 'postgres' ? 'char_length' : 'CHAR_LENGTH';
    return count(tb, `${len}(${C}) > ${a.len}`);
  };
  /** 반올림되는 타입 변경에서 값이 바뀌는 행. */
  const roundCheck = (tb, col, after) => {
    const target = ENGINE === 'postgres' ? after : String(after).replace(/^timestamp/i, 'datetime');
    return count(tb, `${Q(col)} <> CAST(${Q(col)} AS ${target})`);
  };

  function objectRisks() {
    const out = [], noun = objNoun;
    const base = (o, extra) => ({ table: o.table || undefined, what: o.name, ...extra });
    for (const o of OBJS.added) {
      const n = noun(o.type);
      out.push(base(o, { level: 'attention', lead: L(`${n} 추가`, `${n} added`), why: isTrigger(o)
        ? L(`${n} ${o.name} 가 ${o.table || '테이블'}에 추가됩니다. 행이 기록되는 방식이 바뀝니다. 스키마 비교에는 나오지 않습니다.`, `${n} ${o.name} added${objOn(o)}. Changes how rows are written. Not part of the schema diff.`)
        : L(`${n} ${o.name} 가 추가됩니다. 스키마 비교에는 나오지 않습니다.`, `${n} ${o.name} added. Not part of the schema diff.`) }));
    }
    for (const o of OBJS.dropped) {
      const n = noun(o.type);
      out.push(base(o, { level: 'attention', lead: L(`${n} 삭제`, `${n} dropped`), why: isTrigger(o)
        ? L(`${n} ${o.name} 가 삭제됩니다. 이 ${n}가 하던 처리가 더 이상 일어나지 않습니다. 스키마 비교에는 나오지 않습니다.`, `${n} ${o.name} dropped${o.table ? ` from ${o.table}` : ''}. What it enforced no longer happens. Not part of the schema diff.`)
        : isCallable(o) ? L(`${n} ${o.name} 가 삭제됩니다. 이를 호출하는 코드는 실패합니다. 스키마 비교에는 나오지 않습니다.`, `${n} ${o.name} dropped. Code that calls it fails. Not part of the schema diff.`)
        : L(`${n} ${o.name} 가 삭제됩니다. 이를 쓰는 코드·기본값은 실패합니다. 스키마 비교에는 나오지 않습니다.`, `${n} ${o.name} dropped. Code or defaults that use it fail. Not part of the schema diff.`) }));
    }
    for (const o of OBJS.changed) {
      const n = noun(o.type);
      out.push(base(o, { level: 'attention', lead: L(`${n} 정의 변경`, `${n} changed`), why: L(`${n} ${o.name}${objOn(o)} 의 정의가 바뀝니다${o.table_before ? ` (테이블 이름 변경: 이전 ${o.table_before})` : ''}${o.step ? ` (${o.step})` : ''}. 스키마 비교에는 나오지 않습니다.`,
        `${n} ${o.name} definition changed${objOn(o)}${o.table_before ? ` (table renamed from ${o.table_before})` : ''}${o.step ? ` (${o.step})` : ''}. Not part of the schema diff.`) }));
    }
    for (const o of OBJS.invalid) {
      const n = noun(o.type);
      out.push(base(o, { level: 'danger', kind: 'invalid', tag: L('컴파일 오류', 'Compile errors'), lead: L(`${n} 컴파일 오류`, `${n} invalid`), why: isTrigger(o)
        ? L(`${n} ${o.name} 가 오류로 컴파일됩니다. ${o.table ? o.table + ' 에 쓰는' : '이 테이블에 쓰는'} 문장이 실패합니다${o.step ? ` (${o.step})` : ''}.`, `${n} ${o.name} compiles with errors; writes to ${o.table || 'its table'} fail${o.step ? ` (${o.step})` : ''}.`)
        : isCallable(o) ? L(`${n} ${o.name} 가 오류로 컴파일됩니다. 호출하면 실패합니다${o.step ? ` (${o.step})` : ''}.`, `${n} ${o.name} compiles with errors; calls to it fail${o.step ? ` (${o.step})` : ''}.`)
        : L(`${n} ${o.name} 가 오류로 컴파일됩니다. 쓰는 곳에서 실패합니다${o.step ? ` (${o.step})` : ''}.`, `${n} ${o.name} is invalid; anything that uses it fails${o.step ? ` (${o.step})` : ''}.`) }));
    }
    return out;
  }

  const out = [];
  const existing = new Set(D.tables.filter(t => !isWhole(t)).map(t => t.name));
  for (const t of D.tables) {
    const tables = [t.name, t.before_name].filter(Boolean);
    const tb = t.before_name || t.name;
    if (t.status === 'dropped') out.push({ level: 'danger', whole: true, tables, table: t.name, what: t.name, lead: L('테이블 전체 삭제', 'Whole table dropped'),
      why: L(`컬럼 ${t.columns.length}개와 데이터가 테이블과 함께 사라집니다.`, `Its ${plural(t.columns.length, 'column')} and data are lost with the table.`)
        + (ENGINE === 'oracle' ? L(' 휴지통(recycle bin)이 켜져 있고 PURGE 하지 않았다면 FLASHBACK TABLE 로 되살릴 수 있습니다.', ' If the recycle bin is on and the table was not purged, FLASHBACK TABLE can restore it.') : ''),
      check: check(count(t.name), 'affected') });
    if (t.status === 'renamed' && !confirmed(t)) out.push({ level: 'attention', table: t.name, what: `${t.before_name} → ${t.name}`, why: L(`테이블 이름 변경으로 보입니다. ${RENAME_HINT}`, `Looks like a table rename. ${RENAME_HINT}`) });
    if (t.recreated) out.push({ level: 'danger', whole: true, tables, table: t.name, what: t.name, lead: L('테이블 다시 만듦', 'Table recreated'), why: L('지웠다가 같은 이름으로 다시 만들어 기존 행이 모두 사라집니다.', 'Dropped and recreated under the same name, so all existing rows are lost.'),
      check: check(count(tb), 'affected') });
    if (t.recreated || (!existing.has(t.name) && t.status !== 'renamed')) continue;
    for (const c of t.columns) {
      const at = { table: t.name, col: c.name, tables, cols: [c.name, c.before?.name].filter(Boolean) };
      const bc = c.before?.name || c.name;
      if (c.status === 'dropped') out.push({ level: 'danger', ...at, what: `${t.name}.${c.name}`, lead: L('컬럼 삭제', 'Column dropped'), why: L('테이블은 남고 이 컬럼의 값만 사라집니다.', 'The table stays; only the values in this column are lost.'),
        check: check(count(tb, `${Q(bc)} IS NOT NULL`), 'affected') });
      if (c.status === 'renamed' && !confirmed(c)) out.push({ level: 'attention', ...at, what: `${t.name}.${c.before.name} → ${c.name}`, why: L(`이름 변경으로 보입니다. ${RENAME_HINT} 삭제 후 추가라면 값이 비워집니다.`, `Looks like a rename. ${RENAME_HINT} If it is a drop plus an add, the values are emptied.`) });
      if (c.recreated && !t.recreated) out.push({ level: 'danger', ...at, what: `${t.name}.${c.name}`, why: L(`컬럼을 ${RECREATE_HINT}`, `The column was dropped and recreated under the same name. Existing data is lost.`),
        check: check(count(tb, `${Q(c.name)} IS NOT NULL`), 'affected') });
      if (c.status === 'added' && !c.after.nullable && c.after.default == null && !c.after.identity) {
        out.push({ level: 'attention', ...at, what: `${t.name}.${c.name}`, why: L('NOT NULL 이고 기본값이 없습니다. 기존 행이 있으면 컬럼 추가가 실패합니다.', 'NOT NULL with no default. Adding the column fails if rows already exist.'),
          check: check(count(tb)) });
      }
      if (c.changes.includes('nullable') && c.before.nullable && !c.after.nullable) {
        out.push({ level: 'attention', ...at, what: `${t.name}.${c.name}`, why: L('NOT NULL 로 바뀝니다. 기존 행에 NULL 이 있으면 실패합니다.', 'Becomes NOT NULL. Fails if existing rows contain NULL.'),
          check: check(count(tb, `${Q(bc)} IS NULL`)) });
      }
      if (c.changes.includes('type')) {
        const risk = typeRisk(c.before.type, c.after.type, ENGINE), bt = shortType(c.before.type), atp = shortType(c.after.type);
        if (risk === 'family') {
          out.push({ level: 'attention', ...at, what: `${t.name}.${c.name}`, why: L(`타입이 ${bt} → ${atp} 로 바뀝니다. 기존 값을 변환할 수 있는지 확인이 필요합니다.`, `Type changes from ${bt} → ${atp}. Check that existing values can be converted.`) });
        } else if (risk === 'round') {
          const frac = FRACTIONAL_BASE.test(typeParts(c.before.type).base || '');
          out.push({ level: 'danger', kind: 'round', tag: L('값 반올림', 'Values rounded'), ...at, what: `${t.name}.${c.name}`,
            lead: frac ? L('소수초 정밀도 축소', 'Fractional seconds lowered') : L('소수 자릿수 축소', 'Decimal places lowered'),
            why: frac ? L(`소수초 정밀도가 ${bt} → ${atp} 로 줄어듭니다. 기존 값의 소수초가 반올림되어 정밀도를 잃습니다.`, `Lowers fractional-second precision ${bt} → ${atp}. Existing values lose sub-second precision.`)
              : L(`소수 자릿수가 ${bt} → ${atp} 로 줄어듭니다. 기존 값이 반올림됩니다.`, `Lowers decimal places ${bt} → ${atp}. Existing values are rounded.`),
            check: check(roundCheck(tb, bc, c.after.type), 'affected') });
        } else if (risk === 'fail') {
          const num = NUMERIC_BASE.test(typeParts(c.before.type).base || '');
          out.push({ level: 'attention', ...at, what: `${t.name}.${c.name}`, why: num && ENGINE === 'oracle'
            ? L(`${bt} → ${atp} 로 줄어듭니다. Oracle 은 컬럼이 비어 있을 때만 NUMBER 의 정밀도·소수 자릿수를 줄일 수 있어, 값이 하나라도 있으면 실패합니다.`, `Shrinks ${bt} → ${atp}. Oracle lowers NUMBER precision or scale only on an empty column, so this fails if any row has a value.`)
            : L(`${bt} → ${atp} 로 줄어듭니다. 더 ${num ? '큰' : '긴'} 값이 있으면 적용이 실패합니다.`, `Shrinks ${bt} → ${atp}. Fails if any existing value is ${num ? 'larger' : 'longer'}.`),
            check: check(shrinkCheck(tb, bc, c.before.type, c.after.type)) });
        }
      }
    }
    for (const k of t.constraints) {
      if (k.status !== 'added' || allNewEmpty(t, k.columns)) continue;
      const kind = { p: 'PK', f: 'FK', c: 'CHECK', u: 'UNIQUE' }[k.kind] || L('제약', 'Constraint');
      out.push({ level: 'attention', table: t.name, col: k.columns?.[0], what: `${t.name} ${kind}`, why: L(`${kind} ${constraintText(k)} 가 추가됩니다. 기존 행이 조건을 어기면 실패합니다.`, `${kind} ${constraintText(k)} is added. Fails if existing rows violate it.`),
        check: check(constraintCheck(t, tb, k), k.kind === 'p' ? 'pk' : k.kind === 'u' ? 'none' : 'zero') });
    }
    for (const i of t.indexes) {
      if (i.status !== 'added' || !i.unique || allNewEmpty(t, i.columns)) continue;
      const cols = plainIndexColumns(i);
      out.push({ level: 'attention', table: t.name, col: (cols || i.columns || [])[0], what: `${t.name} UNIQUE IDX`, why: L(`UNIQUE 인덱스 ${indexText(i)} 가 추가됩니다. 중복 값이 있으면 실패합니다.`, `UNIQUE index ${indexText(i)} is added. Fails if duplicate values exist.`),
        check: check(cols && !cols.some(n => isNewCol(t, n)) ? dupCheck(tb, cols.map(n => beforeCol(t, n))) : null, 'none') });
    }
  }
  /** 제약을 추가하기 전에 어기는 행을 찾는 쿼리. 같은 단계에서 추가된 컬럼이 끼어 있으면 만들지 않는다. */
  function constraintCheck(t, tb, k) {
    const cols = k.columns || [];
    if (cols.some(n => isNewCol(t, n))) return null;
    const bcols = cols.map(n => beforeCol(t, n));
    if (k.kind === 'p') return bcols.length ? `${count(tb, bcols.map(x => `${Q(x)} IS NULL`).join(' OR '))}\n${dupCheck(tb, bcols)}` : null;
    if (k.kind === 'u') return bcols.length ? dupCheck(tb, bcols) : null;
    if (k.kind === 'c') {
      const expr = checkExpr(k.definition);
      if (!expr) return null;
      const mentions = n => new RegExp(`(^|[^\\w$#])"?${n.replace(/[$]/g, '\\$&')}"?([^\\w$#]|$)`, 'i').test(expr);
      const unsafe = t.columns.some(c => (c.status === 'added' || (c.before && c.before.name !== c.name)) && mentions(c.name));
      return unsafe || cols.some(n => beforeCol(t, n) !== n) ? null : count(tb, `NOT (${expr})`);
    }
    if (k.kind !== 'f' || !bcols.length) return null;
    const rt = refOf(k.ref_table);
    if (rt?.status === 'added') return count(tb, nn(bcols));
    const rcols = (k.ref_columns || []).map(n => rt ? beforeCol(rt, n) : n);
    if (rcols.length !== bcols.length) return null;
    const rtb = rt?.before_name || rt?.name || k.ref_table;
    return `SELECT COUNT(*) FROM ${Q(tb)} c WHERE ${nn(bcols, 'c.')} AND NOT EXISTS (SELECT 1 FROM ${Q(rtb)} p WHERE ${bcols.map((x, j) => `p.${Q(rcols[j])} = c.${Q(x)}`).join(' AND ')});`;
  }

  const lossRisks = out.filter(r => r.level === 'danger');
  const shown = ev => lossRisks.some(r => r.tables?.includes(ev.table) && (r.whole || (ev.column != null && r.cols?.includes(ev.column))));
  for (const s of RANGE_STEPS) {
    if (!s.data_loss || (s.events || []).filter(isLossEvent).some(shown)) continue;
    out.push({ level: 'danger', kind: 'step', tag: L('데이터 유실 가능', 'May lose data'), step: s.step, what: s.label, lead: L('구간 안의 단계', 'Step in range'),
      why: L('이 단계에서 데이터가 사라질 수 있습니다. 합친 비교에는 구간 안에서 생겼다가 사라진 객체가 나오지 않습니다.', 'This step may lose data. The combined diff hides objects created and dropped inside the range.') });
  }
  out.push(...objectRisks());
  const rank = r => r.whole ? 0 : r.level === 'danger' ? 1 : 2;
  return out.sort((x, y) => rank(x) - rank(y));
}

/**
 * 위험 목록의 확인 쿼리를 하나의 SQL 스크립트로 묶는다. 항목마다 무엇을 확인하는지와 안전한 결과를 주석으로 단다.
 * @return 확인 쿼리가 없으면 빈 문자열
 */
function checkScript(risks, opts = {}) {
  const L = riskLang(opts);
  const EXPECT = { zero: L('결과가 0 이면 안전합니다', 'Safe when the result is 0'), none: L('결과 행이 없으면 안전합니다', 'Safe when no rows are returned'),
    pk: L('첫 결과가 0 이고 둘째 결과 행이 없으면 안전합니다', 'Safe when the first result is 0 and the second returns no rows'),
    affected: L('결과는 이 변경으로 값이 사라지거나 바뀌는 행 수입니다', 'The result is the number of rows whose values are lost or changed') };
  const flat = x => String(x || '').replace(/\s+/g, ' ').replace(/\*\//g, '* /');
  const first = x => flat(x).replace(/^(.*?[.。])\s.*$/, '$1');
  return risks.filter(r => r.check).map((r, k) => [`-- ${k + 1}. ${flat(r.what)} · ${r.lead ? flat(r.lead) : first(r.why)}`, `--    ${EXPECT[r.check.expect] || ''}`, r.check.sql].join('\n')).join('\n\n');
}

/**
 * 운영 중 실행하면 테이블을 잠그거나 다시 쓰는 변경(잠금 힌트). 위험 목록과 따로 세며 위험 합계에 들어가지 않는다.
 * 엔진 문서의 일반 동작을 따르며 버전·설정·테이블 크기에 따라 실제 영향은 다르다. 구간 비교에서는 빈 목록이다.
 * @param D computeRisks 와 같은 비교 결과. D.sql 이 문자열이면 그 단계 SQL 원문에서 CONCURRENTLY·NOT VALID·ONLINE 등을 확인하고, 없으면 조건부 문구를 쓴다.
 * @return [{level:'hint', table, col, what, lead, why}]
 */
function computeHints(D, opts = {}) {
  const L = riskLang(opts), E = D.engine || 'postgres';
  if (D.range_steps) return [];
  const sql = typeof D.sql === 'string'
    ? D.sql.replace(/--[^\n]*/g, ' ').replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/["`]/g, '').replace(/\s+/g, ' ').toUpperCase() : null;
  const stmts = sql ? sql.split(';') : [];
  const reEsc = x => x.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  /** 이름이 들어 있는 문장들(대문자, 따옴표 없이). */
  const stmtsWith = name => { const re = new RegExp(`(^|[\\s.(,])${reEsc(String(name).toUpperCase())}([\\s(,]|$)`); return stmts.filter(x => re.test(x)); };
  /**
   * SQL 원문이 있으면 이름이 든 문장 중 re 에 맞는 것이 있는지, 원문이 없으면 null.
   * 이름이 원문에 없으면(엔진이 지은 제약 이름 등) table 이 들어 있고 about 에 맞는 문장에서 찾는다.
   */
  const said = (name, re, table, about) => {
    if (sql == null) return null;
    const found = stmtsWith(name);
    if (found.length) return found.some(x => re.test(x));
    const near = table && about ? stmtsWith(table).filter(x => about.test(x)) : [];
    return near.length > 0 && near.every(x => re.test(x));
  };
  /** 이 이름의 인덱스를 만드는 CREATE INDEX 문이 re 에 맞는지. 원문이 없으면 null. */
  const createdWith = (name, re) => {
    if (sql == null) return null;
    const n = reEsc(String(name).toUpperCase());
    const mk = new RegExp(`^\\s*CREATE (UNIQUE )?INDEX (CONCURRENTLY )?(IF NOT EXISTS )?([\\w$#]+\\.)?${n}(\\s|$)`);
    return stmts.some(x => mk.test(x) && re.test(x));
  };
  const ABOUT = { f: /FOREIGN KEY|REFERENCES/, c: /\bCHECK\b/, p: /PRIMARY KEY/, u: /\bUNIQUE\b/ };
  const out = [];
  const hint = (t, extra) => out.push({ level: 'hint', table: t.name, ...extra });
  const LEAD = { write: L('쓰기 잠금', 'Blocks writes'), all: L('읽기·쓰기 잠금', 'Blocks reads and writes'), rewrite: L('테이블 다시 쓰기', 'Table rewrite'), copy: L('테이블 복사', 'Table copy') };
  for (const t of D.tables) {
    if (isWhole(t) || t.recreated) continue;
    const backing = new Set(t.constraints.map(k => k.name));
    if (E === 'postgres') {
      for (const i of t.indexes) {
        if (i.status !== 'added' || backing.has(i.name)) continue;
        const conc = createdWith(i.name, /^\s*CREATE (UNIQUE )?INDEX CONCURRENTLY/);
        if (conc) continue;
        hint(t, { col: (plainIndexColumns(i) || [])[0], what: `${t.name} INDEX ${i.name}`, lead: LEAD.write, why: conc == null
          ? L(`CONCURRENTLY 없이 만들면 인덱스를 다 만들 때까지 ${t.name} 의 INSERT·UPDATE·DELETE 가 막힙니다. 큰 테이블이면 CREATE INDEX CONCURRENTLY 로 만드세요(트랜잭션 블록 밖에서만 실행됩니다).`, `Without CONCURRENTLY, INSERT, UPDATE and DELETE on ${t.name} wait until the index is built. For a large table use CREATE INDEX CONCURRENTLY (it cannot run inside a transaction block).`)
          : L(`CREATE INDEX 는 인덱스를 다 만들 때까지 ${t.name} 의 INSERT·UPDATE·DELETE 를 막습니다. 큰 테이블이면 CREATE INDEX CONCURRENTLY 로 만드세요(트랜잭션 블록 밖에서만 실행됩니다).`, `CREATE INDEX blocks INSERT, UPDATE and DELETE on ${t.name} until the index is built. For a large table use CREATE INDEX CONCURRENTLY (it cannot run inside a transaction block).`) });
      }
      for (const k of t.constraints) {
        if (k.status !== 'added') continue;
        const ref = k.kind === 'f' ? k.ref_table : null;
        if (k.kind === 'p' || k.kind === 'u') {
          if (said(k.name, /USING INDEX/, t.name, ABOUT[k.kind])) continue;
          hint(t, { col: k.columns?.[0], what: `${t.name} ${k.kind === 'p' ? 'PK' : 'UNIQUE'} ${k.name}`, lead: LEAD.all,
            why: L(`제약을 추가하면 인덱스를 만드는 동안 ${t.name} 의 읽기·쓰기가 모두 막힙니다(ACCESS EXCLUSIVE). 큰 테이블이면 CREATE UNIQUE INDEX CONCURRENTLY 로 인덱스를 먼저 만들고 ADD CONSTRAINT … USING INDEX 로 붙이세요.`,
              `Adding the constraint blocks reads and writes on ${t.name} while its index is built (ACCESS EXCLUSIVE). For a large table build it first with CREATE UNIQUE INDEX CONCURRENTLY, then ADD CONSTRAINT … USING INDEX.`) });
        } else if (k.kind === 'f' || k.kind === 'c') {
          if (/NOT VALID\s*$/i.test(k.definition || '') || said(k.name, /NOT VALID|VALIDATE CONSTRAINT/, t.name, ABOUT[k.kind])) continue;
          hint(t, { col: k.columns?.[0], what: `${t.name} ${k.kind === 'f' ? 'FK' : 'CHECK'} ${k.name}`, lead: k.kind === 'f' ? LEAD.write : LEAD.all,
            why: k.kind === 'f'
              ? L(`FK 를 추가하면 기존 행 검사가 끝날 때까지 ${t.name}${ref ? `·${ref}` : ''} 의 쓰기가 막힙니다. NOT VALID 로 추가한 뒤 VALIDATE CONSTRAINT 를 따로 실행하면 검사 중에도 쓰기를 막지 않습니다.`,
                `Adding the FK blocks writes to ${t.name}${ref ? ` and ${ref}` : ''} until existing rows are checked. Add it NOT VALID, then run VALIDATE CONSTRAINT separately to check without blocking writes.`)
              : L(`CHECK 를 추가하면 기존 행 검사가 끝날 때까지 ${t.name} 의 읽기·쓰기가 막힙니다. NOT VALID 로 추가한 뒤 VALIDATE CONSTRAINT 를 따로 실행하면 막지 않습니다.`,
                `Adding the CHECK blocks reads and writes on ${t.name} until existing rows are checked. Add it NOT VALID, then run VALIDATE CONSTRAINT separately to avoid that.`) });
        }
      }
      for (const c of t.columns) {
        if (c.status === 'added') {
          if (pgRewritingAdd(c.after)) hint(t, { col: c.name, what: `${t.name}.${c.name}`, lead: LEAD.rewrite,
            why: L(`모든 행에 값을 채워야 하는 컬럼이라 ${t.name} 전체를 다시 쓰고, 그동안 읽기·쓰기가 막힙니다.`, `Every row needs a value for this column, so ${t.name} is rewritten while reads and writes are blocked.`) });
          continue;
        }
        if (c.changes?.includes('type') && !pgNoRewrite(c.before.type, c.after.type)) hint(t, { col: c.name, what: `${t.name}.${c.name}`, lead: LEAD.rewrite,
          why: L(`타입 ${shortType(c.before.type)} → ${shortType(c.after.type)} 변경은 ${t.name} 전체와 인덱스를 다시 쓰고, 그동안 읽기·쓰기가 막힙니다.`, `Changing ${shortType(c.before.type)} → ${shortType(c.after.type)} rewrites ${t.name} and its indexes while reads and writes are blocked.`) });
        const inNewPk = t.constraints.some(k => k.kind === 'p' && k.status === 'added' && (k.columns || []).includes(c.name));
        if (!inNewPk && c.changes?.includes('nullable') && c.before.nullable && !c.after.nullable) hint(t, { col: c.name, what: `${t.name}.${c.name}`, lead: LEAD.all,
          why: L(`SET NOT NULL 은 모든 행을 검사하는 동안 ${t.name} 의 읽기·쓰기를 막습니다. PostgreSQL 12 이상에서는 CHECK (${c.name} IS NOT NULL) NOT VALID 를 추가·검증해 두면 이 검사를 건너뜁니다.`,
            `SET NOT NULL blocks reads and writes on ${t.name} while every row is checked. On PostgreSQL 12+, a validated CHECK (${c.name} IS NOT NULL) lets it skip the scan.`) });
      }
    } else if (E === 'mysql' || E === 'mariadb') {
      for (const k of t.constraints) {
        if (k.status === 'added' && k.kind === 'f') hint(t, { col: k.columns?.[0], what: `${t.name} FK ${k.name}`, lead: LEAD.copy,
          why: L(`foreign_key_checks 가 켜진 상태에서 FK 를 추가하면 ${t.name} 을 복사해 다시 만들고(ALGORITHM=COPY), 그동안 쓰기가 막힙니다.`, `With foreign_key_checks on, adding an FK copies ${t.name} (ALGORITHM=COPY) and blocks writes meanwhile.`) });
        if (k.status === 'dropped' && k.kind === 'p' && !t.constraints.some(x => x.kind === 'p' && x.status === 'added')) hint(t, { what: `${t.name} PK`, lead: LEAD.copy,
          why: L(`새 기본키 없이 기본키만 지우면 ${t.name} 을 복사해 다시 만들고, 그동안 쓰기가 막힙니다.`, `Dropping the primary key without adding a new one copies ${t.name} and blocks writes meanwhile.`) });
      }
      for (const c of t.columns) {
        if (!c.changes?.includes('type') || c.status === 'added') continue;
        if (mysqlInPlace(c.before.type, c.after.type)) continue;
        const grow = /^varchar$/i.test(typeParts(c.before.type).base || '') && typeParts(c.after.type).len >= typeParts(c.before.type).len;
        hint(t, { col: c.name, what: `${t.name}.${c.name}`, lead: LEAD.copy,
          why: L(`타입 ${c.before.type} → ${c.after.type} 변경은 ${t.name} 을 복사해 다시 만들고(ALGORITHM=COPY), 그동안 쓰기가 막힙니다.`, `Changing ${c.before.type} → ${c.after.type} copies ${t.name} (ALGORITHM=COPY) and blocks writes meanwhile.`)
            + (grow ? L(' VARCHAR 길이 접두가 1바이트에서 2바이트로 바뀌는 경우(utf8mb4 기준 64자 이상)라서입니다. 한 바이트 문자 집합이면 제자리에서 끝날 수 있습니다.', ' The length prefix grows from 1 to 2 bytes (64+ characters in utf8mb4); with a single-byte charset it may run in place.') : '') });
      }
    } else if (E === 'oracle') {
      for (const i of t.indexes) {
        if (i.status !== 'added' || backing.has(i.name)) continue;
        const online = createdWith(i.name, /\bONLINE\b/);
        if (online) continue;
        hint(t, { col: (plainIndexColumns(i) || [])[0], what: `${t.name} INDEX ${i.name}`, lead: LEAD.write,
          why: online == null ? L(`ONLINE 없이 만들면 인덱스를 다 만들 때까지 ${t.name} 의 DML 이 막힙니다. Enterprise Edition 이면 CREATE INDEX … ONLINE 을 쓸 수 있습니다.`, `Without ONLINE, DML on ${t.name} waits until the index is built. Enterprise Edition supports CREATE INDEX … ONLINE.`)
            : L(`CREATE INDEX 는 인덱스를 다 만들 때까지 ${t.name} 의 DML 을 막습니다. Enterprise Edition 이면 CREATE INDEX … ONLINE 을 쓸 수 있습니다.`, `CREATE INDEX blocks DML on ${t.name} until the index is built. Enterprise Edition supports CREATE INDEX … ONLINE.`) });
      }
      for (const k of t.constraints) {
        if (k.status !== 'added' || /\b(NOVALIDATE|DISABLE)\b/i.test(k.definition || '') || said(k.name, /NOVALIDATE/, t.name, ABOUT[k.kind])) continue;
        const kind = { p: 'PK', f: 'FK', c: 'CHECK', u: 'UNIQUE' }[k.kind] || k.kind;
        hint(t, { col: k.columns?.[0], what: `${t.name} ${kind} ${k.name}`, lead: LEAD.write,
          why: L(`제약을 검증하며 추가하는 동안 ${t.name} 의 DML 이 막힙니다.${k.kind === 'f' || k.kind === 'c' ? ' ENABLE NOVALIDATE 로 추가한 뒤 ENABLE VALIDATE 를 따로 실행하면 검증 중에도 DML 을 막지 않습니다.' : ''}`,
            `DML on ${t.name} is blocked while the constraint is added and validated.${k.kind === 'f' || k.kind === 'c' ? ' Add it ENABLE NOVALIDATE, then run ENABLE VALIDATE separately to validate without blocking DML.' : ''}`) });
      }
      for (const c of t.columns) {
        if (c.status !== 'added' && c.changes?.includes('nullable') && c.before.nullable && !c.after.nullable) hint(t, { col: c.name, what: `${t.name}.${c.name}`, lead: LEAD.write,
          why: L(`NOT NULL 로 바꾸면 모든 행을 검사하는 동안 ${t.name} 의 DML 이 막힙니다.`, `Changing to NOT NULL blocks DML on ${t.name} while every row is checked.`) });
      }
    }
  }
  return out;
}

/**
 * 구간(a~b 단계)의 객체 변경을 합친다. 구간 안에서 생겼다가 사라진 객체는 뺀다. 객체 정보가 없는 구간이면 null.
 * Node 의 report.mjs 가 쓰며, templates/timeline.html 의 mergeObjects 와 같은 결과를 낸다(tests/test_report.py 가 비교한다).
 * @param steps steps.json 의 steps @param vlabel 단계 번호 → 버전 이름
 */
function mergeObjects(steps, a, b, vlabel) {
  const key = o => `${o.type}|${o.name}`;
  const added = new Map(), dropped = new Map(), invalid = new Map();
  let changed = [], any = false;
  for (let i = a; i <= b; i++) {
    const o = steps[i - 1].objects;
    if (!o) continue;
    any = true;
    const tag = x => ({ ...x, step: vlabel(i) });
    const status = x => { if (x.status === 'VALID') invalid.delete(key(x)); else if (x.status === 'INVALID') invalid.set(key(x), tag(x)); };
    for (const x of o.added || []) {
      if (dropped.has(key(x))) { dropped.delete(key(x)); changed.push(tag(x)); } else added.set(key(x), tag(x));
      status(x);
    }
    for (const x of o.dropped || []) {
      invalid.delete(key(x));
      changed = changed.filter(c => key(c) !== key(x));
      if (added.has(key(x))) added.delete(key(x)); else dropped.set(key(x), tag(x));
    }
    for (const x of o.changed || []) { changed.push(tag(x)); status(x); }
    for (const x of o.invalid || []) invalid.set(key(x), tag(x));
  }
  if (!any) return null;
  changed = changed.filter(x => !added.has(key(x)));
  return { added: [...added.values()], dropped: [...dropped.values()], changed, invalid: [...invalid.values()] };
}

/** 구간 안 단계별 range_steps 항목. eventsOf(i) 는 단계 i 의 diff 이벤트 배열. */
function rangeStepsOf(steps, a, b, vlabel, eventsOf) {
  return steps.slice(a - 1, b).map((s, k) => ({ step: a + k, label: vlabel(a + k), data_loss: !!s.data_loss, event_count: s.event_count || 0,
    events: (eventsOf(a + k) || []).map(({ kind, table, column, before, after }) => ({ kind, table, column, before, after })) }));
}

if (typeof module !== 'undefined') module.exports = { computeRisks, computeHints, checkScript, quoteIdent, pgNoRewrite, mysqlInPlace, checkExpr, plainIndexColumns, mergeObjects, rangeStepsOf, riskTexts, typeRisk, typeParts, shortType, constraintText, indexText, plural, confirmed, isWhole, normObjects };
