#!/usr/bin/env bash
# DDL 두 개를 각각 빈 DB 에 적용해 스냅샷을 뜨고, 1단계 타임라인(변경 전 → 변경 후) HTML 로 만든다.
# 사용법: scripts/compare.sh before.sql after.sql [출력 디렉터리, 기본 out] [--db postgres|mysql|mariadb|oracle] [--image 이미지] [--schema 스키마] [--step-timeout 초] [--no-sql] [--lang ko|en]
# 종료 코드: 0 성공, 3 HTML 은 만들었지만 점검에 실패 항목이 있음, 그 밖은 생성 실패.
set -euo pipefail

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
  echo "$(t "사용법" "Usage"): $0 before.sql after.sql [$(t "출력 디렉터리" "output directory")] [--db postgres|mysql|mariadb|oracle] [--image $(t "이미지" "image")] [--schema $(t "스키마" "schema")] [--step-timeout $(t "초" "seconds")] [--no-sql] [--lang ko|en]" >&2
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

out="${3:-out}"

python3 "$dir/snapshot.py" "$1" "$2" --out "$out/ddl" "${@:4}" --lang "$lang"
python3 "$dir/compare_timeline.py" "$out/ddl/00.json" "$out/ddl/01.json" --out "$out" --after-sql "$2" --lang "$lang"
python3 "$dir/timeline.py" "$out" --lang "$lang"
python3 "$dir/render_timeline.py" "$out" --out "$out/timeline.html" --lang "$lang" ${no_sql[@]+"${no_sql[@]}"}
check "$out/timeline.html" "$out/check.json"
[ "$check_rc" = 1 ] && exit 3
exit 0
