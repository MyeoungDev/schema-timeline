"""단계별 한 줄 요약을 쓰기 위한 입력을 Markdown 으로 출력한다. summary.json 의 키(파일 이름)도 함께 보여 준다.

사용법: python3 summary_input.py out/timeline [--with-sql] [--lang ko|en]
기본은 이벤트만 출력한다. --with-sql 이면 비밀번호를 가린 SQL 원문(단계당 앞 40줄)을 덧붙인다.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from i18n import L, add_argument, early, init, plural  # noqa: E402
from timeline import type_narrows  # noqa: E402

KIND = {
    "table_added": ("테이블 추가", "Table added"),
    "table_dropped": ("테이블 삭제", "Table dropped"),
    "table_renamed": ("테이블 이름 변경", "Table renamed"),
    "table_recreated": ("테이블을 지우고 같은 이름으로 다시 만듦", "Table dropped and recreated under the same name"),
    "table_comment": ("테이블 코멘트", "Table comment"),
    "column_added": ("컬럼 추가", "Column added"),
    "column_dropped": ("컬럼 삭제", "Column dropped"),
    "column_renamed": ("컬럼 이름 변경", "Column renamed"),
    "column_recreated": ("컬럼을 지우고 다시 만듦", "Column dropped and recreated"),
    "column_type": ("타입 변경", "Type changed"),
    "column_nullable": ("NULL 허용 변경", "Nullability changed"),
    "column_default": ("기본값 변경", "Default changed"),
    "column_comment": ("컬럼 코멘트", "Column comment"),
    "column_identity": ("identity 변경", "Identity changed"),
    "column_generated": ("생성 컬럼 여부 변경", "Generated-column flag changed"),
    "column_collation": ("COLLATE 변경", "COLLATE changed"),
    "constraint_added": ("제약 추가", "Constraint added"),
    "constraint_dropped": ("제약 삭제", "Constraint dropped"),
    "index_added": ("인덱스 추가", "Index added"),
    "index_dropped": ("인덱스 삭제", "Index dropped"),
    "view_added": ("뷰 추가", "View added"),
    "view_dropped": ("뷰 삭제", "View dropped"),
    "view_changed": ("뷰 정의 변경", "View definition changed"),
}


OBJECT_TYPE = {
    "TRIGGER": ("트리거", "Trigger"),
    "FUNCTION": ("함수", "Function"),
    "PROCEDURE": ("프로시저", "Procedure"),
    "PACKAGE": ("패키지", "Package"),
    "PACKAGE BODY": ("패키지 본문", "Package body"),
    "TYPE": ("타입", "Type"),
    "TYPE BODY": ("타입 본문", "Type body"),
    "SEQUENCE": ("시퀀스", "Sequence"),
    "SYNONYM": ("시노님", "Synonym"),
    "MATERIALIZED VIEW": ("구체화 뷰", "Materialized view"),
    "EVENT": ("이벤트", "Event"),
}


def object_lines(objects):
    """스키마 diff 에 나오지 않는 객체(트리거·함수 등)의 추가·삭제·변경·INVALID 줄."""
    out = []
    for group, ko, en in (("added", "추가", "added"), ("dropped", "삭제", "dropped"), ("changed", "정의 변경", "definition changed")):
        for o in (objects or {}).get(group, []):
            kind = L(*OBJECT_TYPE[o["type"]]) if o["type"] in OBJECT_TYPE else o["type"].title()
            if group == "added" and o.get("table"):
                out.append(L(f"- {kind} {ko} `{o['name']}` (`{o['table']}`, 스키마 diff 에 나오지 않음)",
                             f"- {kind} {en} `{o['name']}` on `{o['table']}` (not in the schema diff)"))
            elif group == "added":
                out.append(L(f"- {kind} {ko} `{o['name']}` (스키마 diff 에 나오지 않음)", f"- {kind} {en} `{o['name']}` (not in the schema diff)"))
            else:
                on = f" (`{o['table']}`)" if o.get("table") else ""
                if o.get("table_before"):
                    on = L(f" (`{o['table']}`, 테이블 이름 변경: 이전 `{o['table_before']}`)", f" (`{o['table']}`, table renamed from `{o['table_before']}`)")
                out.append(L(f"- {kind} {ko} `{o['name']}`{on}", f"- {kind} {en} `{o['name']}`{on}"))
    for o in (objects or {}).get("invalid", []):
        kind = L(*OBJECT_TYPE[o["type"]]) if o["type"] in OBJECT_TYPE else o["type"].title()
        out.append(L(f"- {kind} `{o['name']}` 이(가) INVALID 상태가 됨(컴파일 오류 등)", f"- {kind} `{o['name']}` became INVALID (e.g. compile error)"))
    return out


def line(e, engine="postgres"):
    target = e["table"] + (f".{e['column']}" if e.get("column") else "")
    text = f"- {L(*KIND[e['kind']]) if e['kind'] in KIND else e['kind']} `{target}`"
    if e["kind"] in ("column_renamed", "table_renamed"):
        text += f" {e['before']} → {e['after']} ({L('확정', 'confirmed') if e.get('confidence') == 'confirmed' else L('의심', 'suspected')})"
    elif e["kind"] == "column_type":
        kind = type_narrows(e["before"], e["after"], engine)
        text += f" {e['before']} → {e['after']}" + {"round": L(" (반올림으로 데이터 유실)", " (rounds existing values: data loss)"),
                                                   "fail": L(" (크기 축소)", " (narrowed)")}.get(kind, "")
    elif e["kind"] in ("column_nullable",):
        text += L(" NOT NULL 로", " to NOT NULL") if not e["after"] else L(" NULL 허용으로", " to nullable")
    elif e["kind"] in ("column_default", "table_comment", "column_comment"):
        text += f" {e['before']!r} → {e['after']!r}"
    elif e["kind"] in ("constraint_added", "constraint_dropped", "index_added", "index_dropped"):
        text += f" {e['after'] or e['before']}"
    elif e["kind"] == "column_added":
        text += f" {e['after']}"
    return text


def main():
    early()
    parser = argparse.ArgumentParser()
    parser.add_argument("dir", type=Path)
    parser.add_argument("--with-sql", action="store_true")
    add_argument(parser)
    args = parser.parse_args()
    init(args)
    meta = json.loads((args.dir / "steps.json").read_text())
    engine = meta.get("engine") or "postgres"
    events = json.loads((args.dir / "events.json").read_text()) if (args.dir / "events.json").exists() else []
    print(L(f"# {meta['source']} 단계 {len(meta['steps'])}개\n", f"# {meta['source']}: {plural(len(meta['steps']), 'step', 'steps')}\n"))
    print(L("summary.json 은 {\"<파일 이름>\": \"한두 문장 요약\"} 형식이다. 키는 아래 각 단계의 파일 이름을 그대로 쓴다.\n",
            "summary.json has the form {\"<file name>\": \"one or two sentence summary\"}. Use each step's file name below as the key.\n"))
    for s in meta["steps"]:
        flags = [f for f, on in ((L("DML 포함", "contains DML"), s["dml"]), (L("데이터 유실 가능", "may lose data"), s.get("data_loss"))) if on]
        state = {"failed": L("적용 실패", "failed to apply"), "not_run": L("앞 단계 실패로 적용 안 함", "not applied after an earlier failure")}.get(s["status"], "")
        print(f"## {s['index']}. {s.get('label') or 'V' + s['version']} {s['description']} — `{s['file']}`"
              + (f" [{', '.join(flags + ([state] if state else []))}]" if flags or state else ""))
        evs = [e for e in events if e["step"] == s["index"]]
        if s["status"] == "failed":
            print(L("오류: ", "Error: ") + f"{s['error'].splitlines()[0] if s['error'] else ''}")
        elif not evs and s["status"] == "applied":
            print(L("- 스키마 구조 변경 없음", "- No schema structure change"))
        for e in evs:
            print(line(e, engine))
        if s["status"] == "applied" and s.get("objects") is None:
            print(L("- 기타 객체(트리거·함수 등)는 이 출력에서 확인하지 않았음. 다시 실행하면 포함됨", "- Other objects (triggers, functions…) were not checked for this output. Re-run to include them."))
        for text in object_lines(s.get("objects")):
            print(text)
        for w in s.get("warnings", []):
            print(L("- 경고: ", "- Warning: ") + w)
        if args.with_sql:
            sql = s["sql"].splitlines()
            print("```sql\n" + "\n".join(sql[:40]) + (L("\n-- ... (생략)", "\n-- ... (truncated)") if len(sql) > 40 else "") + "\n```")
        print()


if __name__ == "__main__":
    main()
