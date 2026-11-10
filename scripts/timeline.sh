#!/usr/bin/env bash
# 마이그레이션 이력(Flyway, Supabase, Prisma, Drizzle 등)을 재생해 타임라인 HTML 까지 만든다. 재생이 중간에 실패해도 그 전까지로 HTML 을 만든다.
# 사용법: scripts/timeline.sh <레포 또는 마이그레이션 디렉터리> <출력 디렉터리> [--format 형식] [--lang ko|en] [--no-sql] [replay.py 옵션...]
# --no-sql 은 HTML 에 SQL 원문을 넣지 않는다. --step-timeout 등 나머지 옵션은 replay.py 로 넘어간다.
# 출력 디렉터리에 summary.json 이 있으면 HTML 에 함께 넣는다.
# 종료 코드: 0 성공, 1 재생 중 실패(그 전까지로 HTML 은 만듦), 3 재생은 됐지만 점검에 실패 항목이 있음.
set -uo pipefail

# --lang 을 뽑아 언어를 정하고(--lang > SCHEMA_TIMELINE_LANG > 로케일 > en) 나머지 인자만 남긴다.
lang_flag=""; lang_missing=0; no_sql=(); rest=()
while [ $# -gt 0 ]; do
  case "$1" in
    --lang)
      if [ $# -lt 2 ] || [[ "$2" == --* ]]; then lang_missing=1; shift
      else lang_flag="$2"; shift 2; fi ;;
    --lang=)  lang_missing=1; shift ;;
    --lang=*) lang_flag="${1#--lang=}"; shift ;;
    --no-sql) no_sql=(--no-sql); shift ;;
    *) rest+=("$1"); shift ;;
  esac
done
dir="$(cd "$(dirname "$0")" && pwd)"
lang="$(python3 "$dir/i18n.py" "$lang_flag")" || exit 2
set -- ${rest[@]+"${rest[@]}"}
# t "한국어" "English": 현재 언어의 문구를 출력한다.
t() { if [ "$lang" = ko ]; then printf '%s' "$1"; else printf '%s' "$2"; fi; }

usage() {
  echo "$(t "사용법" "Usage"): $0 $(t "<레포 또는 마이그레이션 디렉터리> <출력 디렉터리>" "<repo or migrations directory> <output directory>") [--lang ko|en] [--format flyway|supabase|prisma|drizzle|sqlfiles|git-snapshots] [--image $(t "이미지" "image")] [--ignore-repeatable] [--step-timeout $(t "초" "seconds")] [--no-sql]" >&2
  exit 2
}
if [ "$lang_missing" = 1 ] || [ $# -lt 2 ]; then usage; fi
# 결과 HTML 을 헤드리스 Chrome 으로 점검한다(node 와 Chrome 이 있을 때). 영수증은 check.json, 화면은 *.check.png.
# check_rc: 0 통과, 1 실패 항목 있음(페이지 멈춤 포함), 2 점검 불가(node·Chrome 없음, 영수증 없음).
check() {
  rm -f "$2"
  if ! command -v node >/dev/null 2>&1; then echo "  $(t "점검: 하지 못함(node 없음)" "Check: not run (node not found)")"; check_rc=2; return 0; fi
  node "$dir/check_html.mjs" "$1" > "$2" && check_rc=0 || check_rc=$?
  node -e '
    const ko = process.argv[2] === "ko";
    const r = JSON.parse(require("fs").readFileSync(process.argv[1], "utf8"));
    const s = r.summary || {}, bad = (r.checks || []).filter(c => c.ok === false).map(c => c.name);
    const skip = (r.checks || []).filter(c => c.ok === null).map(c => c.name);
    if (r.ok === null) console.error(ko ? `  점검: 하지 못함 - ${r.reason}` : `  Check: not run - ${r.reason}`);
    else if (r.ok) console.log(ko
      ? `  점검: 통과 ${s.passed}개${skip.length ? `, 건너뜀 ${skip.length}개(${skip.join(", ")})` : ""} (${process.argv[1]})`
      : `  Check: passed ${s.passed}${skip.length ? `, skipped ${skip.length} (${skip.join(", ")})` : ""} (${process.argv[1]})`);
    else console.error(ko ? `  점검: 실패 ${bad.length}개 - ${bad.join(", ")} (${process.argv[1]})` : `  Check: failed ${bad.length} - ${bad.join(", ")} (${process.argv[1]})`);
  ' "$2" "$lang" 2>&1 || { echo "  $(t "점검: 하지 못함 - 영수증을 읽지 못함" "Check: not run - cannot read the receipt") ($2)"; check_rc=2; }
}

src="$1"; out="$2"; shift 2

rm -f "$out/steps.json"
python3 "$dir/replay.py" "$src" --out "$out" --lang "$lang" "$@"
replay_status=$?
if [ ! -f "$out/steps.json" ]; then
  exit "$replay_status"
fi
python3 "$dir/timeline.py" "$out" --lang "$lang" || exit 1
python3 "$dir/render_timeline.py" "$out" --out "$out/timeline.html" --lang "$lang" ${no_sql[@]+"${no_sql[@]}"} || exit 1
check "$out/timeline.html" "$out/check.json"
[ "$replay_status" = 0 ] && [ "$check_rc" = 1 ] && exit 3
exit "$replay_status"
