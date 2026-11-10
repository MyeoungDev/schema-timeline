"""DDL 두 개의 스냅샷을 1단계 타임라인 입력으로 묶는다. 이후 timeline.py → render_timeline.py 로 timeline.html 을 만든다.

사용법: python3 compare_timeline.py before.json after.json --out <디렉터리> [--after-sql after.sql] [--lang ko|en]
결과: <디렉터리>/steps.json, <디렉터리>/snapshots/000.json(변경 전), 001.json(변경 후)
스냅샷의 objects(트리거·함수 등)는 단계의 objects 로 비교해 넣고, 저장하는 스냅샷에서는 뺀다.
두 스냅샷은 서로 다른 DB 에서 뜬 것이므로 timeline.py 는 이름으로 계보를 잇고, 이름 변경은 "의심"으로만 나온다.
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from engines import diff_objects, no_objects  # noqa: E402
from i18n import L, add_argument, early, init  # noqa: E402
from replay import OUTPUTS, analyze  # noqa: E402
from sqltext import mask_secrets  # noqa: E402

DIALECT = {"mysql": "mysql", "mariadb": "mysql", "oracle": "oracle"}


def build(before, after, out, after_sql=None):
    """스냅샷 두 개와 (있으면) 변경 후 DDL 원문으로 타임라인 입력을 쓴다. 원문은 단계의 SQL 보기에 쓴다.

    @param before 변경 전 스냅샷 dict, @param after 변경 후 스냅샷 dict
    @param out 출력 디렉터리. 이전 산출물(OUTPUTS)은 지운다
    """
    engine = after.get("engine", "postgres")
    before_label, after_label = L("변경 전", "Before"), L("변경 후", "After")
    bname, aname = before.get("source") or before_label, after.get("source") or after_label
    raw = Path(after_sql).read_text(encoding="utf-8-sig") if after_sql else ""
    masked, _ = mask_secrets(raw, DIALECT.get(engine, "postgres"))
    dml = analyze(raw, engine)[0] if raw else False
    objects = diff_objects(before["objects"], after["objects"]) if "objects" in before and "objects" in after else no_objects()
    out.mkdir(parents=True, exist_ok=True)
    for name in OUTPUTS:
        target = out / name
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
    (out / "snapshots").mkdir()
    for i, doc in enumerate((before, after)):
        doc = dict(doc)
        doc.pop("replay_id", None)
        doc.pop("objects", None)
        (out / "snapshots" / f"{i:03d}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2))
    meta = {
        "source": f"{bname} → {aname}", "input": None, "engine": engine, "image": after.get("image") or "",
        "schema": after.get("schema_name"), "format": "ddl-compare", "kind": "compare",
        "base_label": before_label, "base_source": bname, "prelude": None, "roles_created": [], "skipped_files": [],
        "warnings": [], "steps": [{
            "index": 1, "version": "after", "label": after_label, "description": f"{bname} → {aname}", "file": aname,
            "sql": masked, "dml": dml, "status": "applied", "error": None, "warnings": [],
            "objects": objects,
        }],
    }
    (out / "steps.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    return meta


def main():
    early()
    parser = argparse.ArgumentParser()
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--after-sql", type=Path)
    add_argument(parser)
    args = parser.parse_args()
    init(args)
    before, after = (json.loads(p.read_text()) for p in (args.before, args.after))
    build(before, after, args.out, args.after_sql)
    print(f"{before.get('source')} → {after.get('source')} -> {args.out}")


if __name__ == "__main__":
    main()
