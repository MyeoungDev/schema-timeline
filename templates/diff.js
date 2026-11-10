/*
 * scripts/diff.py 의 diff() 를 그대로 옮긴 것. 타임라인에서 임의의 두 버전을 브라우저에서 비교할 때 쓴다.
 * 결과는 diff.py 와 같아야 한다(tests/test_diff_parity.py 가 연속 단계 전체로 확인한다).
 */
const DIFF_COLUMN_FIELDS = ['type', 'nullable', 'default', 'comment', 'identity', 'generated', 'collation'];
const DIFF_SEQUENCE_DEFAULT = /^nextval\('[^']*'::regclass\)$/;
const DIFF_FK_PREFIX = /^FOREIGN KEY \(.*?\) REFERENCES .*?\(.*?\)\s*/;
const DIFF_INDEX_PREFIX = /^CREATE ((?:UNIQUE|FULLTEXT|SPATIAL|BITMAP) )?INDEX \S+ ON \S+ /;

const dkey = v => JSON.stringify(v);
const nz = v => v === undefined ? null : v;
const reEscape = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
/** Python 의 문자열 정렬과 같은 코드포인트 순 비교. */
function cmpStr(a, b) {
  const x = Array.from(String(a)), y = Array.from(String(b));
  for (let i = 0; i < Math.min(x.length, y.length); i++) {
    const d = x[i].codePointAt(0) - y[i].codePointAt(0);
    if (d) return d;
  }
  return x.length - y.length;
}
const sortStr = arr => arr.sort(cmpStr);

function diffPairRenames(dropped, added, signature) {
  const group = items => {
    const out = new Map();
    for (const x of items) {
      const k = dkey(signature(x));
      if (!out.has(k)) out.set(k, []);
      out.get(k).push(x.name);
    }
    return out;
  };
  const gd = group(dropped), ga = group(added), out = new Map();
  for (const [k, names] of gd) {
    if (ga.has(k) && names.length === 1 && ga.get(k).length === 1) out.set(names[0], ga.get(k)[0]);
  }
  return out;
}

/** @return [pairs, dropped, added]. pairs 는 [변경 전, 변경 후, 근거] 목록. */
function diffMatchItems(before, after, ident, signature, sure = true) {
  const pairs = [], usedB = new Set(), usedA = new Set();
  if (ident) {
    const bk = new Map(before.map(x => [x[ident], x]));
    for (const a of after) {
      const b = bk.get(a[ident]);
      if (b !== undefined && b !== null) {
        pairs.push([b, a, b.name === a.name ? 'same' : sure ? 'confirmed' : 'suspected']);
        usedB.add(b); usedA.add(a);
      }
    }
  }
  const bn = new Map(before.filter(x => !usedB.has(x)).map(x => [x.name, x]));
  for (const a of after) {
    if (usedA.has(a) || !bn.has(a.name)) continue;
    const b = bn.get(a.name);
    pairs.push([b, a, ident ? 'recreated' : 'same']);
    usedB.add(b); usedA.add(a);
  }
  let dropped = before.filter(x => !usedB.has(x));
  let added = after.filter(x => !usedA.has(x));
  if (!ident) {
    const guess = diffPairRenames(dropped, added, signature);
    const bmap = new Map(dropped.map(x => [x.name, x])), amap = new Map(added.map(x => [x.name, x]));
    for (const [o, n] of guess) pairs.push([bmap.get(o), amap.get(n), 'suspected']);
    const news = new Set(guess.values());
    dropped = dropped.filter(x => !guess.has(x.name));
    added = added.filter(x => !news.has(x.name));
  }
  return [pairs, dropped, added];
}

/** Python 의 유니코드 \b 와 같은 경계로 식별자를 한 번에 바꾼다. */
function diffRenameWords(text, mapping) {
  if (!mapping.size || !text) return text;
  const alt = [...mapping.keys()].map(reEscape).join('|');
  const re = new RegExp(`(?<![\\p{L}\\p{N}_$])(${alt})(?![\\p{L}\\p{N}_$])`, 'gu');
  return text.replace(re, (_, k) => mapping.get(k));
}

const diffColumnSignature = c => [c.type, c.nullable];
const diffComparable = (field, value) => field === 'default' && value && DIFF_SEQUENCE_DEFAULT.test(value) ? 'nextval(<sequence>)' : nz(value);

function diffConstraintKey(k, rename, tableRename, refRenames) {
  const cols = (k.columns || []).map(c => rename.has(c) ? rename.get(c) : c);
  if (k.kind === 'f') {
    const options = k.definition.replace(DIFF_FK_PREFIX, '');
    const ref = tableRename.has(k.ref_table) ? tableRename.get(k.ref_table) : k.ref_table;
    const rr = refRenames.get(ref) || new Map();
    const refCols = (k.ref_columns || []).map(c => rr.has(c) ? rr.get(c) : c);
    return dkey([k.kind, cols, ref, refCols, options]);
  }
  return dkey([k.kind, cols, diffRenameWords(k.definition, rename)]);
}

const diffIndexKey = (i, rename) => dkey([i.unique, diffRenameWords(i.definition.replace(DIFF_INDEX_PREFIX, ''), rename)]);
const diffColumnPairs = (b, a, ident, sure = true) => diffMatchItems(b, a, ident ? 'ordinal' : null, diffColumnSignature, sure);

function diffColumns(tname, beforeCols, afterCols, events, ident, [boid, aoid], sure = true) {
  const [pairs, dropped] = diffColumnPairs(beforeCols, afterCols, ident, sure);
  const rename = new Map(pairs.filter(([b, a]) => b.name !== a.name).map(([b, a]) => [b.name, a.name]));
  const paired = new Map(pairs.map(([b, a, how]) => [a.name, [b, how]]));
  const rows = new Map();
  for (const c of afterCols) {
    const name = c.name;
    if (!paired.has(name)) {
      rows.set(name, { name, status: 'added', before: null, after: c, changes: [] });
      events.push({ kind: 'column_added', table: tname, column: name, before: null, after: c.type, oid: nz(aoid), attnum: nz(c.ordinal) });
      continue;
    }
    const [old, how] = paired.get(name);
    const at = { oid: nz(aoid), attnum: nz(c.ordinal) };
    let status = old.name !== name ? 'renamed' : 'same';
    if (status === 'renamed') events.push({ kind: 'column_renamed', table: tname, column: name, before: old.name, after: name, confidence: how, ...at });
    if (how === 'recreated') events.push({ kind: 'column_recreated', table: tname, column: name, before: old.type, after: c.type, ...at });
    const changes = DIFF_COLUMN_FIELDS.filter(f => diffComparable(f, old[f]) !== diffComparable(f, c[f]));
    for (const f of changes) events.push({ kind: `column_${f}`, table: tname, column: name, before: nz(old[f]), after: nz(c[f]), ...at });
    if (status === 'same' && (changes.length || how === 'recreated')) status = 'changed';
    const row = { name, status, before: old, after: c, changes };
    if (status === 'renamed') { row.before_name = old.name; row.confidence = how; }
    if (how === 'recreated') row.recreated = true;
    rows.set(name, row);
  }
  const ordered = afterCols.map(c => rows.get(c.name));
  for (const c of dropped) {
    events.push({ kind: 'column_dropped', table: tname, column: c.name, before: c.type, after: null, oid: nz(boid), attnum: nz(c.ordinal) });
    const row = { name: c.name, status: 'dropped', before: c, after: null, changes: [] };
    const prev = beforeCols.slice(0, beforeCols.indexOf(c)).map(p => p.name);
    let anchor = null;
    for (const p of prev.reverse()) {
      const target = rename.has(p) ? rename.get(p) : p;
      if (rows.has(target)) { anchor = target; break; }
    }
    const pos = anchor === null ? 0 : ordered.findIndex(r => r.name === anchor) + 1;
    ordered.splice(pos, 0, row);
  }
  return [ordered, rename];
}

function diffKeyed(tname, before, after, keyBefore, keyAfter, kind, events, oid) {
  const bk = new Map(), ak = new Map();
  for (const x of before) bk.set(keyBefore(x), x);
  for (const x of after) ak.set(keyAfter(x), x);
  const out = [];
  for (const [k, x] of ak) {
    const status = bk.has(k) ? 'same' : 'added';
    out.push({ ...x, status });
    if (status === 'added') events.push({ kind: `${kind}_added`, table: tname, column: null, before: null, after: nz(x.definition), oid: nz(oid) });
  }
  for (const [k, x] of bk) {
    if (!ak.has(k)) {
      out.push({ ...x, status: 'dropped' });
      events.push({ kind: `${kind}_dropped`, table: tname, column: null, before: nz(x.definition), after: null, oid: nz(oid) });
    }
  }
  return out;
}

function diffWholeTable(t, status, events) {
  const after = status === 'added';
  events.push({ kind: `table_${status}`, table: t.name, column: null, before: after ? null : t.name, after: after ? t.name : null, oid: nz(t.oid) });
  return {
    name: t.name, status,
    ddl: { before: after ? null : nz(t.ddl), after: after ? nz(t.ddl) : null },
    comment: { before: after ? null : t.comment, after: after ? t.comment : null },
    columns: t.columns.map(c => ({ name: c.name, status, before: after ? null : c, after: after ? c : null, changes: [] })),
    constraints: t.constraints.map(k => ({ ...k, status })),
    indexes: t.indexes.map(i => ({ ...i, status })),
  };
}

function diffTable(bt, at, events, tableRename, refRenames, ident, sure = true) {
  const name = at.name;
  const [columns, rename] = diffColumns(name, bt.columns, at.columns, events, ident, [bt.oid, at.oid], sure);
  if (nz(bt.comment) !== nz(at.comment)) events.push({ kind: 'table_comment', table: name, column: null, before: nz(bt.comment), after: nz(at.comment), oid: nz(at.oid) });
  const empty = new Map();
  const constraints = diffKeyed(name, bt.constraints, at.constraints,
    k => diffConstraintKey(k, rename, tableRename, refRenames), k => diffConstraintKey(k, empty, empty, empty), 'constraint', events, at.oid);
  const indexes = diffKeyed(name, bt.indexes, at.indexes, i => diffIndexKey(i, rename), i => diffIndexKey(i, empty), 'index', events, at.oid);
  const touched = nz(bt.comment) !== nz(at.comment) || columns.some(c => c.status !== 'same') || [...constraints, ...indexes].some(x => x.status !== 'same');
  return {
    name, status: touched ? 'changed' : 'same',
    comment: { before: nz(bt.comment), after: nz(at.comment) },
    ddl: { before: nz(bt.ddl), after: nz(at.ddl) },
    columns, constraints, indexes,
  };
}

function diffTableOrder(tables) {
  const deps = new Map();
  for (const t of tables) deps.set(t.name, new Set(t.constraints.filter(k => k.kind === 'f' && k.ref_table !== t.name).map(k => k.ref_table)));
  const order = [], done = new Set();
  while (order.length < deps.size) {
    let ready = sortStr([...deps].filter(([n, d]) => !done.has(n) && [...d].every(x => done.has(x) || !deps.has(x))).map(([n]) => n));
    if (!ready.length) ready = sortStr([...deps.keys()].filter(n => !done.has(n))).slice(0, 1);
    for (const n of ready) { order.push(n); done.add(n); }
  }
  return order;
}


/** 스냅샷 두 개를 비교한다. 반환 모양은 diff.py 의 diff() 와 같다. */
function schemaDiff(before, after) {
  const events = [];
  const sure = !!before.replay_id && before.replay_id === after.replay_id;
  const ident = sure || !!(before.linked && after.linked);
  const tsig = t => t.columns.map(c => [c.name, c.type]).sort((x, y) => cmpStr(x[0], y[0]) || cmpStr(String(x[1]), String(y[1])));
  const [pairs, dropped] = diffMatchItems(before.tables, after.tables, ident ? 'oid' : null, tsig, sure);
  const tableRename = new Map(pairs.filter(([b, a]) => b.name !== a.name).map(([b, a]) => [b.name, a.name]));
  const paired = new Map(pairs.map(([b, a, how]) => [a.name, [b, how]]));
  const bt = new Set(before.tables.map(t => t.name));
  const refRenames = new Map();
  for (const [b, a, how] of pairs) {
    const [cp] = diffColumnPairs(b.columns, a.columns, ident && how !== 'recreated', sure);
    refRenames.set(a.name, new Map(cp.filter(([x, y]) => x.name !== y.name).map(([x, y]) => [x.name, y.name])));
  }
  const tables = [];
  for (const t of after.tables) {
    if (!paired.has(t.name)) { tables.push(diffWholeTable(t, 'added', events)); continue; }
    const [old, how] = paired.get(t.name);
    let row;
    if (how === 'recreated') {
      row = diffTable(old, t, [], tableRename, refRenames, false);
      row.status = 'changed'; row.recreated = true;
      events.push({ kind: 'table_recreated', table: t.name, column: null, before: old.name, after: t.name, oid: nz(t.oid) });
    } else {
      row = diffTable(old, t, events, tableRename, refRenames, ident, sure);
    }
    if (old.name !== t.name) {
      row.status = 'renamed'; row.before_name = old.name; row.confidence = how;
      events.push({ kind: 'table_renamed', table: t.name, column: null, before: old.name, after: t.name, confidence: how, oid: nz(t.oid) });
    }
    tables.push(row);
  }
  for (const t of dropped) tables.push(diffWholeTable(t, 'dropped', events));

  const order = diffTableOrder([...before.tables, ...after.tables.filter(t => !bt.has(t.name))]);
  const rank = new Map(order.map((n, i) => [n, i]));
  const rk = t => rank.has(t.name) ? rank.get(t.name) : rank.has(t.before_name) ? rank.get(t.before_name) : rank.size;
  tables.sort((x, y) => ((x.status === 'dropped') - (y.status === 'dropped')) || (rk(x) - rk(y)));

  const bv = new Map(before.views.map(v => [v.name, v])), av = new Map(after.views.map(v => [v.name, v]));
  const views = [];
  for (const name of sortStr([...new Set([...bv.keys(), ...av.keys()])])) {
    const b = bv.get(name), a = av.get(name);
    const status = !b ? 'added' : !a ? 'dropped' : b.definition === a.definition ? 'same' : 'changed';
    views.push({ name, status, before: b ? b.definition : null, after: a ? a.definition : null,
      ddl: { before: b ? nz(b.ddl) : null, after: a ? nz(a.ddl) : null } });
    if (status !== 'same') events.push({ kind: `view_${status}`, table: name, column: null, before: b ? b.definition : null, after: a ? a.definition : null });
  }
  const counts = {};
  for (const e of events) counts[e.kind] = (counts[e.kind] || 0) + 1;
  return { before: { source: nz(before.source) }, after: { source: nz(after.source) }, tables, views, events, counts };
}

/** diff 의 테이블·컬럼 행에 계보 id 를 붙인다(timeline.py 의 attach_ids 와 같은 규칙). */
function attachIds(d, before, after) {
  const index = snap => {
    const t = new Map(), c = new Map();
    for (const x of snap.tables) { t.set(x.name, x.oid); for (const y of x.columns) c.set(`${x.name}\u0000${y.name}`, [x.oid, y.ordinal]); }
    return [t, c];
  };
  const [bt, bc] = index(before), [at, ac] = index(after);
  for (const t of d.tables) {
    const gone = t.status === 'dropped';
    const oid = (gone ? bt : at).get(t.name);
    t.id = oid ? `t${oid}` : null;
    const oldName = t.before_name || t.name;
    for (const c of t.columns) {
      const key = gone ? bc.get(`${t.name}\u0000${c.name}`) : c.status === 'dropped' ? bc.get(`${oldName}\u0000${c.name}`) : ac.get(`${t.name}\u0000${c.name}`);
      c.id = key ? `c${key[0]}.${key[1]}` : null;
    }
  }
  return d;
}

if (typeof module !== 'undefined') module.exports = { schemaDiff, attachIds };
