"""replay.py 결과로 단계별 diff, 변경 이벤트, 테이블·컬럼 계보를 만든다.

사용법: python3 timeline.py out/timeline

입력  steps.json, snapshots/NNN.json
결과  diffs/NNN.json  단계 NNN(1부터)의 변경. 테이블·컬럼 행에 계보 id 가 붙는다.
      events.json     모든 단계의 이벤트. step 은 단계 순번, id 는 계보 id 다.
      lineage.json    계보 id 별 이름 이력, 존재 구간, 이벤트.
      snapshots_view.json  구간 비교용 스냅샷 목록(계보 id 를 맞추고 DDL 을 붙인 것).
계보 id 는 테이블 t<oid>, 컬럼 c<oid>.<attnum> 이다. 같은 DB 에서 재생했으므로 이름이 바뀌어도 유지된다.
지웠다가 같은 이름으로 다시 만든 테이블·컬럼은 새 id 를 받는다.
"""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from engines import add_ddl  # noqa: E402
from diff import diff  # noqa: E402
from i18n import L, add_argument, early, init, plural  # noqa: E402

DATA_LOSS = {"table_dropped", "column_dropped", "table_recreated", "column_recreated"}
SIZED = re.compile(r"^(.*?)\((\d+)(?:,(\d+))?\)(.*)$")
NUMERIC = re.compile(r"^(numeric|number|decimal|dec)\b", re.I)
FRACTIONAL = re.compile(r"^(timestamp|timestamptz|time|datetime)\b", re.I)
FRACTION_DEFAULT = {"postgres": 6, "oracle": 6, "mysql": 0, "mariadb": 0}


def type_narrows(before, after, engine="postgres"):
    """같은 계열 안에서 길이·자릿수가 줄어드는지와 그 결과. 비교 화면의 typeRisk 와 같은 기준이다.

    @return 'round' 소수 자릿수·소수초 정밀도가 줄어 기존 값이 반올림된다(PostgreSQL·MySQL·MariaDB, 데이터 변경).
            'fail' 길이·정수부 자릿수가 줄거나 Oracle 에서 정밀도가 줄어, 더 큰 값이 있으면 적용이 실패한다.
            None 축소가 아니다.
    """
    pb, pa = SIZED.match(before or ""), SIZED.match(after or "")
    base_b = pb.group(1) + pb.group(4) if pb else before
    base_a = pa.group(1) + pa.group(4) if pa else after
    if base_b != base_a:
        return None
    oracle = engine == "oracle"
    if FRACTIONAL.match(base_b or ""):
        default = FRACTION_DEFAULT.get(engine, 6)
        fb, fa = int(pb.group(2)) if pb else default, int(pa.group(2)) if pa else default
        return None if fa >= fb else "fail" if oracle else "round"
    if not pb:
        return "fail" if pa else None
    if not pa:
        return None
    lb, sb, la, sa = int(pb.group(2)), int(pb.group(3) or 0), int(pa.group(2)), int(pa.group(3) or 0)
    if NUMERIC.match(base_b):
        if sa < sb and not oracle:
            return "round"
        return "fail" if la - sa < lb - sb or sa < sb else None
    return "fail" if la < lb else None


def event_id(e):
    """diff 이벤트의 oid·attnum 으로 만든 계보 id."""
    if e.get("oid") is None:
        return None
    return f"c{e['oid']}.{e['attnum']}" if e.get("attnum") is not None else f"t{e['oid']}"


def ids_by_name(snap):
    """스냅샷의 (테이블 이름) → oid, (테이블 이름, 컬럼 이름) → (oid, attnum)."""
    tables, cols = {}, {}
    for t in snap["tables"]:
        tables[t["name"]] = t["oid"]
        for c in t["columns"]:
            cols[(t["name"], c["name"])] = (t["oid"], c["ordinal"])
    return tables, cols


def attach_ids(d, before, after):
    """diff 의 테이블·컬럼 행에 계보 id 를 붙인다. 삭제된 행은 변경 전, 나머지는 변경 후 스냅샷에서 찾는다."""
    (bt, bc), (at, ac) = ids_by_name(before), ids_by_name(after)
    for t in d["tables"]:
        gone = t["status"] == "dropped"
        oid = (bt if gone else at).get(t["name"])
        t["id"] = f"t{oid}" if oid else None
        old_name = t.get("before_name") or t["name"]
        for c in t["columns"]:
            if gone:
                key = bc.get((t["name"], c["name"]))
            elif c["status"] == "dropped":
                key = bc.get((old_name, c["name"]))
            else:
                key = ac.get((t["name"], c["name"]))
            c["id"] = f"c{key[0]}.{key[1]}" if key else None


def build_lineage(snaps, steps_events):
    """스냅샷마다 존재하는 id 와 이름을 따라가며 계보를 만든다."""
    lin = {}
    for i, snap in enumerate(snaps):
        for t in snap["tables"]:
            tid = f"t{t['oid']}"
            e = lin.setdefault(tid, {"id": tid, "kind": "table", "table": None, "names": [],
                                     "created": i, "dropped": None, "events": []})
            if not e["names"] or e["names"][-1]["name"] != t["name"]:
                e["names"].append({"step": i, "name": t["name"]})
            e["last_seen"] = i
            for c in t["columns"]:
                cid = f"c{t['oid']}.{c['ordinal']}"
                ce = lin.setdefault(cid, {"id": cid, "kind": "column", "table": tid, "names": [],
                                          "created": i, "dropped": None, "events": [], "type": c["type"]})
                if not ce["names"] or ce["names"][-1]["name"] != c["name"]:
                    ce["names"].append({"step": i, "name": c["name"]})
                ce["last_seen"] = i
                ce["type"] = c["type"]
    last = len(snaps) - 1
    for e in lin.values():
        if e["last_seen"] < last:
            e["dropped"] = e["last_seen"] + 1
        del e["last_seen"]
    for _, events in steps_events:
        for ev in events:
            if ev["id"] in lin:
                lin[ev["id"]]["events"].append({k: v for k, v in ev.items() if k != "id"})
    for e in lin.values():
        gone = f"{e['kind']}_dropped"
        if e["dropped"] is not None and not any(x["step"] == e["dropped"] and x["kind"] == gone for x in e["events"]):
            e["events"].append({"kind": gone, "step": e["dropped"], "before": e["names"][-1]["name"], "after": None,
                                "implied": True})
    return lin


def link_by_name(snaps):
    """단계마다 DB 를 새로 만든 스냅샷(전체 스키마 방식)에서 oid·attnum 을 이전 단계 것으로 바꿔 계보를 잇는다.

    같은 이름, 또는 diff 가 이름 변경 의심으로 짝지은 테이블·컬럼을 같은 객체로 본다. 짝이 없는 것은 새 번호를 받는다.
    """
    next_oid = [10 ** 9]

    def fresh():
        next_oid[0] += 1
        return next_oid[0]

    for t in snaps[0]["tables"]:
        t["oid"] = fresh()
    for i in range(1, len(snaps)):
        prev, cur = snaps[i - 1], snaps[i]
        d = diff(prev, cur)
        prev_t = {t["name"]: t for t in prev["tables"]}
        for row in d["tables"]:
            if row["status"] == "dropped":
                continue
            t = next(x for x in cur["tables"] if x["name"] == row["name"])
            old = prev_t.get(row.get("before_name") or row["name"]) if row["status"] != "added" else None
            t["oid"] = old["oid"] if old else fresh()
            old_cols = {c["name"]: c for c in (old["columns"] if old else [])}
            used = max([c["ordinal"] for c in old_cols.values()] + [0])
            mapping = {}
            for r in row["columns"]:
                if r["status"] == "dropped" or r.get("after") is None:
                    continue
                src = old_cols.get(r.get("before_name") or r["name"]) if r["status"] != "added" else None
                if src:
                    mapping[r["name"]] = src["ordinal"]
            for c in t["columns"]:
                if c["name"] in mapping:
                    c["ordinal"] = mapping[c["name"]]
                else:
                    used += 1
                    c["ordinal"] = used
        for t in cur["tables"]:
            t.setdefault("oid", fresh())
    return snaps


def step_data_loss(events, engine="postgres"):
    """테이블·컬럼 삭제나 재생성, 반올림이 일어나는 타입 축소가 있는 단계. 적용 실패 위험(fail)인 축소는 포함하지 않는다."""
    return any(e["kind"] in DATA_LOSS or (e["kind"] == "column_type" and type_narrows(e["before"], e["after"], engine) == "round")
               for e in events)


def main():
    early()
    parser = argparse.ArgumentParser()
    parser.add_argument("dir", type=Path)
    add_argument(parser)
    args = parser.parse_args()
    init(args)
    meta = json.loads((args.dir / "steps.json").read_text())
    applied = sum(s["status"] == "applied" for s in meta["steps"])
    snaps = [json.loads((args.dir / "snapshots" / f"{i:03d}.json").read_text()) for i in range(applied + 1)]
    engine = meta.get("engine") or snaps[0].get("engine") or "postgres"
    git_mode = meta.get("format") in ("git-snapshots", "ddl-compare")
    if git_mode:
        link_by_name(snaps)
        for snap in snaps:
            snap["linked"] = True
    for snap in snaps:
        # 제약·인덱스의 카탈로그 oid 는 비교에 쓰지 않는다. 지우면 단계 사이에 같은 정의가 같은 값이 되어 중복 제거가 된다.
        for t in snap["tables"]:
            for k in t["constraints"] + t["indexes"]:
                k.pop("oid", None)
    for snap in snaps:
        add_ddl(snap)

    if (args.dir / "diffs").exists():
        shutil.rmtree(args.dir / "diffs")
    (args.dir / "diffs").mkdir()
    all_events, steps_events = [], []
    for i in range(1, applied + 1):
        d = diff(snaps[i - 1], snaps[i])
        attach_ids(d, snaps[i - 1], snaps[i])
        events = [dict({k: v for k, v in e.items() if k not in ("oid", "attnum")}, step=i, id=event_id(e))
                  for e in d["events"]]
        steps_events.append((i, events))
        step = meta["steps"][i - 1]
        d["before"]["source"] = meta.get("base_source") or L("V0 (빈 DB)", "V0 (empty database)") if i == 1 else meta["steps"][i - 2]["file"]
        d["after"]["source"] = step["file"]
        d["engine"] = engine
        d["mode"] = "snapshots" if git_mode else "replay"
        d["schema"] = snaps[i].get("schema", [])
        d["events"] = [{k: v for k, v in e.items() if k not in ("oid", "attnum")} for e in d["events"]]
        step["event_count"] = len(d["events"])
        step["data_loss"] = step_data_loss(d["events"], engine)
        (args.dir / "diffs" / f"{i:03d}.json").write_text(json.dumps(d, ensure_ascii=False, indent=2))
        all_events += events

    view = [{k: v for k, v in s.items() if k != "schema"} for s in snaps]
    (args.dir / "snapshots_view.json").write_text(json.dumps(view, ensure_ascii=False))
    lineage = build_lineage(snaps, steps_events)
    (args.dir / "events.json").write_text(json.dumps(all_events, ensure_ascii=False, indent=2))
    (args.dir / "lineage.json").write_text(json.dumps(lineage, ensure_ascii=False, indent=2))
    (args.dir / "steps.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    print(L(f"단계 {applied}개, 이벤트 {len(all_events)}건, 계보 {len(lineage)}개 -> {args.dir}",
            f"{plural(applied, 'step', 'steps')}, {plural(len(all_events), 'event', 'events')}, {plural(len(lineage), 'lineage', 'lineages')} -> {args.dir}"))


if __name__ == "__main__":
    main()
