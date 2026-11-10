#!/usr/bin/env bash
# samples/demo 를 한국어·영어로 재생하고, docs/demo/summary.<언어>.json 요약을 넣어 README 화면과 GIF 를 docs/images/<언어>/ 에 다시 만든다.
# DDL 두 개 비교 화면은 samples/v1.sql·v2.sql 로 만든다.
# 사용법: bash docs/build_demo.sh [작업 디렉터리, 기본 $TMPDIR/schema-timeline-demo]
# 필요한 것: docker(postgres:16-alpine), python3 와 Pillow, Node 22 이상, Chrome.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
work="${1:-${TMPDIR:-/tmp}/schema-timeline-demo}"

for lang in ko en; do
  out="$work/$lang"
  bash "$root/scripts/timeline.sh" "$root/samples/demo" "$out" --lang "$lang"
  cp "$root/docs/demo/summary.$lang.json" "$out/summary.json"
  python3 "$root/scripts/render_timeline.py" "$out" --out "$out/timeline.html" --lang "$lang"
  bash "$root/scripts/compare.sh" "$root/samples/v1.sql" "$root/samples/v2.sql" "$work/$lang-compare" --lang "$lang"
  node "$root/docs/capture.mjs" "$out/timeline.html" "$root/docs/images/$lang" \
    --compare "$work/$lang-compare/timeline.html" --gif "$root/docs/images/$lang/tour.gif"
done
