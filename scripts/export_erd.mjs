// 스냅샷의 스키마를 Mermaid erDiagram 으로 내보낸다. 위키·PR 설명에 붙일 때 쓴다.
//
// 사용법:
//   node export_erd.mjs <스냅샷 JSON>                 예: out/snapshots/01.json
//   node export_erd.mjs <타임라인 출력 디렉터리> [--step N]   N 단계 적용 후(기본: 마지막 적용 단계)
//   [--tables a,b,c] 이 테이블만, [--out 파일.mmd] 파일로 저장(없으면 표준 출력)
//   [--compat legacy] Mermaid 10.5 미만용: 엔티티 별칭 없이 영문·숫자·_ 이름만 쓴다(바꾼 테이블 이름은 %% 주석, 바꾼 컬럼 이름은 설명에 원래 이름을 남긴다)
import { createRequire } from 'node:module';
import { readFileSync, writeFileSync, statSync, existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const { mermaidER } = require(join(dirname(fileURLToPath(import.meta.url)), '..', 'templates', 'export.js'));

const args = process.argv.slice(2);
const opt = name => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : null; };
const src = args.find((a, i) => !a.startsWith('--') && !(i > 0 && args[i - 1].startsWith('--')));
if (!src) {
  console.error('사용법: node export_erd.mjs <스냅샷 JSON | 타임라인 출력 디렉터리> [--step N] [--tables a,b] [--out 파일.mmd]');
  process.exit(2);
}

if (!existsSync(src)) {
  console.error(`없는 경로: ${src}`);
  process.exit(2);
}
let snap, title;
if (statSync(src).isDirectory()) {
  const meta = JSON.parse(readFileSync(join(src, 'steps.json'), 'utf8'));
  const applied = meta.steps.filter(s => s.status === 'applied').length;
  const step = opt('--step') ? Number(opt('--step')) : applied;
  if (!(Number.isInteger(step) && step >= 0 && step <= applied)) {
    console.error(`--step 은 0 ~ ${applied} 사이여야 합니다`);
    process.exit(2);
  }
  const file = join(src, 'snapshots', `${String(step).padStart(3, '0')}.json`);
  snap = JSON.parse(readFileSync(existsSync(file) ? file : join(src, 'snapshots', `${String(step).padStart(2, '0')}.json`), 'utf8'));
  const s = meta.steps[step - 1];
  title = `${meta.source} · ${step ? `${s.file} 적용 후` : '빈 DB'}`;
} else {
  snap = JSON.parse(readFileSync(src, 'utf8'));
  title = snap.source || src;
}
let tables = snap.tables;
if (opt('--tables')) {
  const want = new Set(opt('--tables').split(',').map(x => x.trim()).filter(Boolean));
  const missing = [...want].filter(n => !tables.some(t => t.name === n));
  if (missing.length) console.error(`없는 테이블: ${missing.join(', ')}`);
  tables = tables.filter(t => want.has(t.name));
  if (!tables.length) process.exit(2);
}
const text = mermaidER(tables, { title, compat: opt('--compat') === 'legacy' ? 'legacy' : 'modern' });
if (opt('--out')) {
  writeFileSync(opt('--out'), text);
  console.error(`-> ${opt('--out')} (테이블 ${tables.length}개)`);
} else {
  process.stdout.write(text);
}
