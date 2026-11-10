"""replay.py·timeline.py 결과를 타임라인 HTML 한 장으로 만든다. 외부 요청은 없다.

사용법: python3 render_timeline.py out/timeline --out out/timeline/timeline.html [--no-sql] [--lang ko|en]
--no-sql 은 단계의 SQL 원문(steps[].sql)을 비우고 실패 단계의 오류 문구를 첫 줄만 남겨 따옴표 안 값을 가린다. 데이터에 sql_omitted 가 true 로 들어간다.
스키마 DDL(카탈로그에서 다시 만든 것)은 그대로 담긴다.
--lang 은 페이지의 <html lang> 과 데이터의 lang 으로 들어가고, 화면 언어를 정한다.
summary.json({"V4__product_title.sql": "한 줄 요약", ...})이 있으면 파일 이름이 맞는 단계에 붙인다.
각 단계의 변경 화면은 비교 화면(compare.html) 템플릿을 그대로 써서 iframe 으로 띄운다.
단계마다 반복되는 변경 없는 테이블 정의는 한 번만 담고 단계 diff 에서는 참조로 가리킨다.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from i18n import L, add_argument, early, init  # noqa: E402
from render import embed, now, template  # noqa: E402

WARN_BYTES = 30 * 1024 * 1024
LITERAL = re.compile(r"'(?:[^']|'')*'")


def redact_error(text):
    """오류 문구의 첫 줄만 남기고 작은따옴표 안 값(데이터일 수 있다)은 '…' 로 바꾼다."""
    first = next((l for l in (text or "").splitlines() if l.strip()), "")
    return LITERAL.sub("'…'", first)


def omit_sql(steps):
    """단계 목록에서 SQL 원문과 오류 문구 속 값을 뺀 사본."""
    return [dict(s, sql="", error=redact_error(s["error"]) if s.get("error") else s.get("error")) for s in steps]


def dedupe(diffs):
    """변경 없는 테이블 행과 전체 DDL 객체를 {"$ref": 키} 로 바꾸고, 키 → 원래 값 사전을 돌려준다."""
    defs = {}

    def ref(x):
        key = hashlib.sha1(json.dumps(x, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
        defs[key] = x
        return {"$ref": key}

    for d in diffs:
        d["schema"] = [ref(o) for o in d.get("schema", [])]
        for i, t in enumerate(d["tables"]):
            if t["status"] != "same":
                continue
            d["tables"][i] = ref(t)
    return defs


def main():
    early()
    parser = argparse.ArgumentParser()
    parser.add_argument("dir", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--no-sql", action="store_true",
                        help=L("페이지에 SQL 원문을 넣지 않는다(⌘K 의 SQL 검색도 비어 있다)", "Leave SQL source text out of the page (the SQL search in ⌘K finds nothing)"))
    add_argument(parser)
    args = parser.parse_args()
    lang = init(args)

    meta = json.loads((args.dir / "steps.json").read_text())
    applied = sum(s["status"] == "applied" for s in meta["steps"])
    diffs = [json.loads((args.dir / "diffs" / f"{i:03d}.json").read_text()) for i in range(1, applied + 1)]
    defs = dedupe(diffs)
    view_path = args.dir / "snapshots_view.json"
    snaps = json.loads(view_path.read_text()) if view_path.exists() else []
    for s in snaps:
        for k, t in enumerate(s["tables"]):
            key = hashlib.sha1(json.dumps(t, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
            defs[key] = t
            s["tables"][k] = {"$ref": key}
    summary_path = args.dir / "summary.json"
    raw = json.loads(summary_path.read_text()) if summary_path.exists() else {}
    files = {s["file"] for s in meta["steps"]}
    unknown = sorted(set(raw) - files)
    if unknown:
        print(L("  summary.json 에서 단계와 맞지 않는 키를 무시했습니다: ", "  Ignored summary.json keys that match no step: ") + ", ".join(unknown), file=sys.stderr)
    doc = {
        "source": meta["source"], "image": meta["image"], "schema": meta.get("schema", "public"),
        "lang": lang, "kind": meta.get("kind", "history"), "base_label": meta.get("base_label"),
        "base_source": meta.get("base_source") or L("V0 (빈 DB)", "V0 (empty database)"),
        "roles_created": meta["roles_created"],
        "skipped_files": meta["skipped_files"], "warnings": meta.get("warnings", []), "notes": meta.get("notes", []),
        "steps": omit_sql(meta["steps"]) if args.no_sql else meta["steps"], "diffs": diffs, "defs": defs, "snaps": snaps,
        "lineage": json.loads((args.dir / "lineage.json").read_text()),
        "summary": {k: v for k, v in raw.items() if k in files},
        "sql_omitted": args.no_sql, "sql_omitted_note": L("SQL 원문을 생략했습니다(--no-sql)", "SQL omitted (--no-sql)"),
        "generated_at": now(),
        "compare_template": template("compare.html"),
    }
    page = template("timeline.html").replace('<html lang="ko">', f'<html lang="{lang}">', 1)
    page = page.replace("<title>스키마 이력</title>", f"<title>{L('스키마 이력', 'Schema history')}</title>", 1)
    html = embed(page, doc)
    args.out.write_text(html)
    size = len(html.encode())
    print(f"-> {args.out} ({size:,} bytes)")
    if size > WARN_BYTES:
        print(L(f"  경고: HTML 이 {size // 1024 // 1024}MB 입니다. 브라우저에서 느릴 수 있습니다.",
                f"  Warning: the HTML is {size // 1024 // 1024} MB and may be slow in the browser."), file=sys.stderr)


if __name__ == "__main__":
    main()
