// 타임라인 출력 디렉터리에서 단계별 위험 목록(비교 화면의 "핵심 변경")을 브라우저 없이 만든다. CI 에서 PR 을 막거나 PR 댓글을 붙일 때 쓴다.
//
// 사용법:
//   node report.mjs <출력 디렉터리> [--step N | --from A --to B | --all] [--format json|md] [--lang en|ko] [--fail-on danger|attention|none] [--help]
//   --all(기본)      적용된 모든 단계를 단계별로 판정한다.
//   --step N         N 단계 하나만 판정한다.
//   --from A --to B  A 단계 적용 전부터 B 단계 적용 후까지를 하나로 합쳐 판정한다(snapshots_view.json 필요).
//   --format         json(기본) 또는 md(PR 댓글용 Markdown). 표준 출력으로 낸다.
//   --lang           문구 언어. 없으면 SCHEMA_TIMELINE_LANG, 로케일(ko 로 시작하면 ko), en 순이다.
//   --help, -h       사용법을 표준 출력으로 내고 0 으로 끝낸다.
//   --fail-on        종료 코드 4 를 내는 기준. danger(기본)는 위험, attention 은 위험과 주의, none 은 내지 않는다.
//
// JSON: {source, engine, lang, steps:[{index, label, file, status, data_loss, risks, hints, checks}], totals:{danger, attention, hint}, failed_steps:[{index, label, file, error}]}
//   risks[].check 는 배포 전 확인 쿼리 {sql, expect}, checks 는 그 쿼리들을 묶은 SQL 스크립트, hints 는 잠금 힌트다(--fail-on 에 들어가지 않는다).
//   구간이면 steps 에 항목 하나가 들어가고 index 대신 from·to 가 있으며 hints 는 빈 목록이다.
//
// 판정 규칙은 templates/risks.js 의 computeRisks 이고 비교 화면(templates/compare.html)과 같은 함수를 쓴다.
//
// 종료 코드: 0 정상, 1 재생에 실패한 단계가 있음(위험 기준보다 우선), 2 사용법·입출력 오류, 4 --fail-on 기준 이상의 위험이 있음.
import { createRequire } from 'node:module';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const TEMPLATES = join(dirname(fileURLToPath(import.meta.url)), '..', 'templates');
const { computeRisks, computeHints, checkScript, mergeObjects, rangeStepsOf } = require(join(TEMPLATES, 'risks.js'));
const { schemaDiff, attachIds } = require(join(TEMPLATES, 'diff.js'));

/** --lang > SCHEMA_TIMELINE_LANG > 로케일 > en (scripts/i18n.py 와 같은 순서). flag 가 ko·en 이 아니면 무시한다. */
function resolveLang(flag, env = process.env) {
  if (flag === 'ko' || flag === 'en') return flag;
  const v = (env.SCHEMA_TIMELINE_LANG || '').trim().toLowerCase();
  if (v === 'ko' || v === 'en') return v;
  for (const k of ['LC_ALL', 'LC_MESSAGES', 'LANG']) if (env[k]) return env[k].toLowerCase().startsWith('ko') ? 'ko' : 'en';
  return 'en';
}

const args = process.argv.slice(2);
let langFlag = null;
for (let i = 0; i < args.length; i++) {
  if (args[i] === '--lang' && i + 1 < args.length) langFlag = args[i + 1];
  else if (args[i].startsWith('--lang=')) langFlag = args[i].slice('--lang='.length);
}
const lang = resolveLang(langFlag);
const T = (ko, en) => lang === 'en' ? en : ko;

const USAGE = T('사용법: node report.mjs <출력 디렉터리> [--step N | --from A --to B | --all] [--format json|md] [--lang en|ko] [--fail-on danger|attention|none]',
  'Usage: node report.mjs <output dir> [--step N | --from A --to B | --all] [--format json|md] [--lang en|ko] [--fail-on danger|attention|none] [--help]');
const die = msg => { console.error(msg); process.exit(2); };

if (args.includes('--help') || args.includes('-h')) { console.log(USAGE); process.exit(0); }

const VALUE_FLAGS = new Set(['--step', '--from', '--to', '--format', '--lang', '--fail-on']);
const BOOL_FLAGS = new Set(['--all']);
const opts = {};
let dir = null;
const once = a => { if (a.slice(2) in opts) die(`${T(`${a} 이(가) 여러 번 지정되었습니다`, `${a} was given more than once`)}\n${USAGE}`); };
for (let i = 0; i < args.length; i++) {
  const a = args[i];
  if (VALUE_FLAGS.has(a)) {
    once(a);
    if (i + 1 >= args.length || args[i + 1].startsWith('--')) die(`${T(`${a} 에 값이 필요합니다`, `${a} needs a value`)}\n${USAGE}`);
    opts[a.slice(2)] = args[++i];
  } else if (BOOL_FLAGS.has(a)) { once(a); opts[a.slice(2)] = true; }
  else if (a.startsWith('--')) die(`${T(`알 수 없는 옵션: ${a}`, `Unknown option: ${a}`)}\n${USAGE}`);
  else if (dir === null) dir = a;
  else die(`${T(`인자가 많습니다: ${a}`, `Too many arguments: ${a}`)}\n${USAGE}`);
}
if (dir === null) die(USAGE);

if (opts.lang && !['ko', 'en'].includes(opts.lang)) die(T(`--lang 은 ko, en 중 하나여야 합니다 (받은 값: ${opts.lang})`, `--lang must be ko or en (got: ${opts.lang})`));
const format = opts.format || 'json';
if (!['json', 'md'].includes(format)) die(T(`--format 은 json, md 중 하나여야 합니다 (받은 값: ${format})`, `--format must be json or md (got: ${format})`));
const failOn = opts['fail-on'] || 'danger';
if (!['danger', 'attention', 'none'].includes(failOn)) die(T(`--fail-on 은 danger, attention, none 중 하나여야 합니다 (받은 값: ${failOn})`, `--fail-on must be danger, attention or none (got: ${failOn})`));
const modes = ['step', 'from', 'all'].filter(k => opts[k] !== undefined);
if (opts.to !== undefined && opts.from === undefined) die(`${T('--to 는 --from 과 함께 써야 합니다', '--to must be used with --from')}\n${USAGE}`);
if (opts.from !== undefined && opts.to === undefined) die(`${T('--from 은 --to 와 함께 써야 합니다', '--from must be used with --to')}\n${USAGE}`);
if (modes.length > 1) die(`${T('--step, --from/--to, --all 은 함께 쓸 수 없습니다', '--step, --from/--to and --all cannot be combined')}\n${USAGE}`);
const asInt = (name, v) => { if (!/^\d+$/.test(String(v))) die(T(`${name} 은 정수여야 합니다 (받은 값: ${v})`, `${name} must be an integer (got: ${v})`)); return Number(v); };

const readJson = file => {
  if (!existsSync(file)) die(T(`없는 파일: ${file}`, `File not found: ${file}`));
  try { return JSON.parse(readFileSync(file, 'utf8')); } catch (e) { die(T(`JSON 을 읽지 못했습니다: ${file} (${e.message})`, `Cannot read JSON: ${file} (${e.message})`)); }
};
if (!existsSync(dir)) die(T(`없는 경로: ${dir}`, `Path not found: ${dir}`));
const meta = readJson(join(dir, 'steps.json'));
const steps = meta.steps || [];
const applied = steps.filter(s => s.status === 'applied').length;

const vlabel = i => i === 0 ? (meta.base_label || 'V0') : (steps[i - 1].label || `V${steps[i - 1].version}`);
const pad = i => String(i).padStart(3, '0');
const diffCache = new Map();
/** 단계 i 의 diff. 파일이 없으면 IO 오류로 끝낸다. */
const diffOf = i => {
  if (!diffCache.has(i)) {
    const d = readJson(join(dir, 'diffs', `${pad(i)}.json`));
    diffCache.set(i, d);
  }
  return diffCache.get(i);
};
let snaps = null;
const loadSnaps = () => snaps || (snaps = readJson(join(dir, 'snapshots_view.json')));
/** 구간 비교용 스냅샷(snapshots_view.json). 인덱스 0 은 첫 단계 적용 전. */
const snapOf = i => {
  loadSnaps();
  if (!snaps.checked) {
    snaps.checked = true;
    if (snaps.length !== applied + 1) die(T(`snapshots_view.json 이 단계 수와 맞지 않아 구간을 비교할 수 없습니다 (스냅샷 ${snaps.length}개, 적용 ${applied}단계)`, `snapshots_view.json does not match the step count, so the range cannot be compared (${snaps.length} snapshots, ${applied} steps applied)`));
  }
  return snaps[i];
};
const engineOf = () => {
  const view = existsSync(join(dir, 'snapshots_view.json')) ? loadSnaps()[0]?.engine : null;
  return view || (applied ? diffOf(1).engine : null) || meta.engine || 'postgres';
};

const docOfStep = i => ({ ...diffOf(i), engine: engineOf(), objects: steps[i - 1].objects || null, sql: meta.kind === 'compare' ? null : steps[i - 1].sql ?? null });
const risksOfStep = i => computeRisks(docOfStep(i), { lang });
const hintsOfStep = i => computeHints(docOfStep(i), { lang });
const risksOfRange = (a, b) => {
  const before = snapOf(a - 1), after = snapOf(b);
  const d = attachIds(schemaDiff(before, after), before, after);
  const doc = { ...d, engine: engineOf(), objects: mergeObjects(steps, a, b, vlabel), range_steps: rangeStepsOf(steps, a, b, vlabel, k => diffOf(k).events) };
  return computeRisks(doc, { lang });
};

const FIELDS = ['level', 'kind', 'lead', 'why', 'what', 'table', 'col', 'step', 'tag', 'check'];
const pick = r => Object.fromEntries(FIELDS.filter(k => r[k] !== undefined && r[k] !== null).map(k => [k, r[k]]));
const stepEntry = (s, i, risks, hints = []) => ({ index: i, label: vlabel(i), file: s.file, status: s.status, data_loss: !!s.data_loss, risks: risks.map(pick), hints: hints.map(pick), checks: checkScript(risks, { lang }) });

const entries = [];
if (opts.from !== undefined) {
  const a = asInt('--from', opts.from), b = asInt('--to', opts.to);
  if (!(a >= 1 && a <= b && b <= applied)) die(T(`구간은 1 ≤ A ≤ B ≤ ${applied} 이어야 합니다 (받은 값: ${a}~${b})`, `Range must satisfy 1 <= A <= B <= ${applied} (got: ${a}~${b})`));
  const loss = steps.slice(a - 1, b).some(s => s.data_loss);
  const risks = risksOfRange(a, b);
  entries.push({ from: a, to: b, label: `${vlabel(a)} ~ ${vlabel(b)}`, status: 'applied', data_loss: loss, risks: risks.map(pick), hints: [], checks: checkScript(risks, { lang }) });
} else if (opts.step !== undefined) {
  const n = asInt('--step', opts.step);
  if (!(n >= 1 && n <= steps.length)) die(T(`--step 은 1 ~ ${steps.length} 사이여야 합니다 (받은 값: ${n})`, `--step must be between 1 and ${steps.length} (got: ${n})`));
  const ok = steps[n - 1].status === 'applied';
  entries.push(stepEntry(steps[n - 1], n, ok ? risksOfStep(n) : [], ok ? hintsOfStep(n) : []));
} else {
  steps.forEach((s, k) => { const ok = s.status === 'applied'; entries.push(stepEntry(s, k + 1, ok ? risksOfStep(k + 1) : [], ok ? hintsOfStep(k + 1) : [])); });
}

const failed = steps.map((s, k) => ({ s, i: k + 1 })).filter(({ s, i }) => s.status === 'failed' && entries.some(e => e.index === i))
  .map(({ s, i }) => ({ index: i, label: vlabel(i), file: s.file, error: String(s.error || '').trim().slice(0, 500) }));
const totals = { danger: 0, attention: 0, hint: 0 };
for (const e of entries) { for (const r of e.risks) totals[r.level] += 1; totals.hint += e.hints.length; }
const report = { source: meta.source, engine: engineOf(), lang, steps: entries, totals, failed_steps: failed };

const shown = e => e.risks.length > 0 || e.hints.length > 0;
/** PR 댓글용 Markdown. 위험이 있는 단계만 소제목으로 낸다. */
function markdown() {
  const name = { danger: T('위험', 'Danger'), attention: T('주의', 'Attention'), hint: T('잠금', 'Lock') };
  const code = s => '`' + String(s).replace(/`/g, "'") + '`';
  const lines = [`## ${T('스키마 변경 검토', 'Schema change review')}: ${meta.source} (${report.engine})`, ''];
  lines.push(T(`위험 ${totals.danger}건, 주의 ${totals.attention}건, 적용 ${applied}단계`, `${totals.danger} danger, ${totals.attention} attention, ${applied} steps applied`)
    + (totals.hint ? T(`, 잠금 힌트 ${totals.hint}건`, `, ${totals.hint} locking ${totals.hint === 1 ? 'hint' : 'hints'}`) : '')
    + (failed.length ? T(`, 재생 실패 ${failed.length}단계`, `, ${failed.length} failed replay steps`) : '') + '.', '');
  if (failed.length) {
    lines.push(`### ${T('재생 실패', 'Replay failed')}`, '');
    for (const f of failed) lines.push(`- **${f.label}** ${code(f.file)}${f.error ? `: ${f.error.split('\n')[0]}` : ''}`);
    lines.push('');
  }
  for (const e of entries.filter(shown)) {
    lines.push(`### ${e.label}${e.file ? ` ${code(e.file)}` : ''}${e.data_loss ? ` (${T('데이터 유실 가능', 'may lose data')})` : ''}`, '');
    for (const r of e.risks) lines.push(`- **${name[r.level]}**${r.lead ? ` ${r.lead}` : ''} ${code(r.what)}: ${r.why}`);
    for (const h of e.hints) lines.push(`- **${name.hint}** ${h.lead} ${code(h.what)}: ${h.why}`);
    lines.push('');
    if (e.checks) lines.push(`<details><summary>${T('배포 전 확인 쿼리', 'Pre-deploy checks')}</summary>`, '', '```sql', e.checks, '```', '', '</details>', '');
  }
  if (!totals.danger && !totals.attention && !totals.hint && !failed.length) lines.push(T('검토가 필요한 변경이 없습니다.', 'No changes need review.'), '');
  return lines.join('\n');
}

process.stdout.write(format === 'md' ? markdown() : JSON.stringify(report, null, 2) + '\n');
const hit = failOn === 'none' ? 0 : failOn === 'danger' ? totals.danger : totals.danger + totals.attention;
process.exitCode = failed.length ? 1 : hit ? 4 : 0;
