"""스냅샷 두 개를 비교해 테이블·컬럼·제약·인덱스·뷰 단위의 변경과 이벤트 목록을 만든다.

사용법: python3 diff.py before.json after.json --out diff.json
두 스냅샷의 replay_id 가 같으면(같은 DB 를 이어서 뜬 것) 테이블은 oid, 컬럼은 attnum 으로 짝짓고
이름이 다르면 confidence="confirmed" 이름 변경이다. 두 스냅샷이 모두 linked 면(timeline.py 가 이름으로 이어
oid·attnum 을 맞춘 전체 스키마 스냅샷) 같은 방식으로 짝짓되 이름 변경은 "suspected" 다.
그 밖에는 이름으로 짝짓고, 남은 것 중 시그니처가 하나씩만 맞는 쌍을 "suspected" 이름 변경으로 본다.
"""
import argparse
import json
import re
from pathlib import Path

from i18n import L, add_argument, early, init

COLUMN_FIELDS = ("type", "nullable", "default", "comment", "identity", "generated", "collation")
SEQUENCE_DEFAULT = re.compile(r"^nextval\('[^']*'::regclass\)$")
FK_PREFIX = re.compile(r"^FOREIGN KEY \(.*?\) REFERENCES .*?\(.*?\)\s*")
INDEX_PREFIX = re.compile(r"^CREATE ((?:UNIQUE|FULLTEXT|SPATIAL|BITMAP) )?INDEX \S+ ON \S+ ")


def by_name(items):
    return {x["name"]: x for x in items}


def pair_renames(dropped, added, signature):
    """signature 가 사라진 쪽과 새로 생긴 쪽에 정확히 하나씩만 있을 때 둘을 이름 변경 의심으로 짝짓는다.

    같은 signature 가 어느 한쪽에 둘 이상이면 어느 것이 짝인지 알 수 없으므로 짝짓지 않는다.
    @return {이전 이름: 새 이름}
    """
    def group(items):
        out = {}
        for x in items:
            out.setdefault(signature(x), []).append(x["name"])
        return out

    gd, ga = group(dropped), group(added)
    return {gd[s][0]: ga[s][0] for s in gd.keys() & ga.keys() if len(gd[s]) == 1 and len(ga[s]) == 1}


def match_items(before, after, ident, signature, sure=True):
    """변경 전·후 항목을 짝짓는다.

    @param ident 같은 객체를 가리키는 고정 키 필드(oid, ordinal). None 이면 이름과 signature 로 짝짓는다.
    @return (pairs, dropped, added). pairs 는 (변경 전, 변경 후, 근거) 목록이고 근거는
            "same"(같은 이름), "confirmed"(고정 키가 같고 이름이 다름), "suspected"(시그니처로 추정),
            "recreated"(고정 키는 다르지만 이름이 같음: 지웠다가 다시 만든 것)다.
            sure 가 False 면 고정 키가 같고 이름이 다른 쌍도 "suspected" 다.
    """
    pairs, used_b, used_a = [], set(), set()
    if ident:
        bk = {x[ident]: x for x in before}
        for a in after:
            b = bk.get(a[ident])
            if b is not None:
                pairs.append((b, a, "same" if b["name"] == a["name"] else "confirmed" if sure else "suspected"))
                used_b.add(id(b)); used_a.add(id(a))
    bn = {x["name"]: x for x in before if id(x) not in used_b}
    for a in after:
        if id(a) in used_a or a["name"] not in bn:
            continue
        b = bn[a["name"]]
        pairs.append((b, a, "recreated" if ident else "same"))
        used_b.add(id(b)); used_a.add(id(a))
    dropped = [x for x in before if id(x) not in used_b]
    added = [x for x in after if id(x) not in used_a]
    if not ident:
        guess = pair_renames(dropped, added, signature)
        bmap, amap = {x["name"]: x for x in dropped}, {x["name"]: x for x in added}
        for old, new in guess.items():
            pairs.append((bmap[old], amap[new], "suspected"))
        dropped = [x for x in dropped if x["name"] not in guess]
        added = [x for x in added if x["name"] not in guess.values()]
    return pairs, dropped, added


def rename_words(text, mapping):
    """text 안의 식별자를 단어 단위로 한 번에 바꾼다. 부분 문자열과 연쇄 치환(a→b, b→c)은 일어나지 않는다."""
    if not mapping or not text:
        return text
    pattern = re.compile(r"(?<![\w$])(" + "|".join(re.escape(k) for k in mapping) + r")(?![\w$])")
    return pattern.sub(lambda m: mapping[m.group(1)], text)


def column_signature(c):
    return (c["type"], c["nullable"])


def comparable(field, value):
    """비교용 값. 시퀀스 기본값은 시퀀스 이름이 테이블 이름을 따라 바뀌므로 이름을 지운다."""
    if field == "default" and value and SEQUENCE_DEFAULT.match(value):
        return "nextval(<sequence>)"
    return value


def constraint_key(k, rename, table_rename, ref_renames):
    """제약을 이름이 아니라 내용으로 식별한다. 자동 생성 이름은 테이블·컬럼 이름 변경에 따라 바뀌기 때문이다.

    @param rename 이 테이블의 {이전 컬럼: 새 컬럼}
    @param table_rename {이전 테이블: 새 테이블}
    @param ref_renames {새 테이블: {이전 컬럼: 새 컬럼}}. FK 가 참조하는 컬럼의 이름 변경을 반영한다.
    """
    cols = tuple(rename.get(c, c) for c in (k["columns"] or []))
    if k["kind"] == "f":
        options = FK_PREFIX.sub("", k["definition"])
        ref = table_rename.get(k["ref_table"], k["ref_table"])
        ref_cols = tuple(ref_renames.get(ref, {}).get(c, c) for c in (k["ref_columns"] or []))
        return (k["kind"], cols, ref, ref_cols, options)
    return (k["kind"], cols, rename_words(k["definition"], rename))


def index_key(i, rename):
    """인덱스를 이름 대신 정의(메서드, 컬럼, 정렬, WHERE)로 식별한다."""
    return (i["unique"], rename_words(INDEX_PREFIX.sub("", i["definition"]), rename))


def column_pairs(before_cols, after_cols, ident, sure=True):
    return match_items(before_cols, after_cols, "ordinal" if ident else None, column_signature, sure)


def diff_columns(tname, before_cols, after_cols, events, ident=False, oids=(None, None), sure=True):
    """컬럼 목록을 비교한다. 결과 순서는 변경 후 순서이고, 삭제된 컬럼은 변경 전 위치 근처에 끼워 넣는다.

    이벤트에는 대상 컬럼의 테이블 oid 와 attnum 을 붙인다. 삭제는 변경 전, 나머지는 변경 후 값이다.
    @param oids (변경 전 테이블 oid, 변경 후 테이블 oid)
    @return (행 목록, {이전 이름: 새 이름})
    """
    boid, aoid = oids
    pairs, dropped, _ = column_pairs(before_cols, after_cols, ident, sure)
    rename = {b["name"]: a["name"] for b, a, _ in pairs if b["name"] != a["name"]}
    paired = {a["name"]: (b, how) for b, a, how in pairs}

    rows = {}
    for c in after_cols:
        name = c["name"]
        if name not in paired:
            rows[name] = {"name": name, "status": "added", "before": None, "after": c, "changes": []}
            events.append({"kind": "column_added", "table": tname, "column": name,
                           "before": None, "after": c["type"], "oid": aoid, "attnum": c.get("ordinal")})
            continue
        old, how = paired[name]
        at = {"oid": aoid, "attnum": c.get("ordinal")}
        status = "renamed" if old["name"] != name else "same"
        if status == "renamed":
            events.append({"kind": "column_renamed", "table": tname, "column": name,
                           "before": old["name"], "after": name, "confidence": how, **at})
        if how == "recreated":
            events.append({"kind": "column_recreated", "table": tname, "column": name,
                           "before": old["type"], "after": c["type"], **at})
        changes = [f for f in COLUMN_FIELDS if comparable(f, old.get(f)) != comparable(f, c.get(f))]
        for f in changes:
            events.append({"kind": f"column_{f}", "table": tname, "column": name,
                           "before": old.get(f), "after": c.get(f), **at})
        if status == "same" and (changes or how == "recreated"):
            status = "changed"
        row = {"name": name, "status": status, "before": old, "after": c, "changes": changes}
        if status == "renamed":
            row["before_name"] = old["name"]
            row["confidence"] = how
        if how == "recreated":
            row["recreated"] = True
        rows[name] = row

    ordered = [rows[c["name"]] for c in after_cols]
    for c in dropped:
        events.append({"kind": "column_dropped", "table": tname, "column": c["name"],
                       "before": c["type"], "after": None, "oid": boid, "attnum": c.get("ordinal")})
        row = {"name": c["name"], "status": "dropped", "before": c, "after": None, "changes": []}
        prev = [p["name"] for p in before_cols[: before_cols.index(c)]]
        anchor = None
        for p in reversed(prev):
            target = rename.get(p, p)
            if target in rows:
                anchor = target
                break
        pos = 0 if anchor is None else next(i for i, r in enumerate(ordered) if r["name"] == anchor) + 1
        ordered.insert(pos, row)
    return ordered, rename


def diff_keyed(tname, before, after, keyfn_before, keyfn_after, kind, events, oid=None):
    """내용 키가 같은 항목은 유지, 나머지는 추가·삭제로 본다. 이벤트의 oid 는 테이블 oid 다."""
    bk = {keyfn_before(x): x for x in before}
    ak = {keyfn_after(x): x for x in after}
    out = []
    for k, x in ak.items():
        status = "same" if k in bk else "added"
        out.append(dict(x, status=status))
        if status == "added":
            events.append({"kind": f"{kind}_added", "table": tname, "column": None,
                           "before": None, "after": x.get("definition"), "oid": oid})
    for k, x in bk.items():
        if k not in ak:
            out.append(dict(x, status="dropped"))
            events.append({"kind": f"{kind}_dropped", "table": tname, "column": None,
                           "before": x.get("definition"), "after": None, "oid": oid})
    return out


def whole_table(t, status, events):
    """추가되거나 삭제된 테이블은 모든 하위 항목에 같은 상태를 붙인다."""
    side = "after" if status == "added" else "before"
    events.append({"kind": f"table_{status}", "table": t["name"], "column": None,
                   "before": None if status == "added" else t["name"],
                   "after": t["name"] if status == "added" else None, "oid": t.get("oid")})
    return {
        "name": t["name"], "status": status,
        "ddl": {"before": t.get("ddl") if side == "before" else None, "after": t.get("ddl") if side == "after" else None},
        "comment": {"before": t["comment"] if side == "before" else None,
                    "after": t["comment"] if side == "after" else None},
        "columns": [{"name": c["name"], "status": status,
                     "before": c if side == "before" else None,
                     "after": c if side == "after" else None, "changes": []} for c in t["columns"]],
        "constraints": [dict(k, status=status) for k in t["constraints"]],
        "indexes": [dict(i, status=status) for i in t["indexes"]],
    }


def diff_table(bt, at, events, table_rename, ref_renames, ident=False, sure=True):
    name = at["name"]
    columns, rename = diff_columns(name, bt["columns"], at["columns"], events, ident, (bt.get("oid"), at.get("oid")), sure)
    if bt["comment"] != at["comment"]:
        events.append({"kind": "table_comment", "table": name, "column": None,
                       "before": bt["comment"], "after": at["comment"], "oid": at.get("oid")})
    constraints = diff_keyed(name, bt["constraints"], at["constraints"],
                             lambda k: constraint_key(k, rename, table_rename, ref_renames),
                             lambda k: constraint_key(k, {}, {}, {}),
                             "constraint", events, at.get("oid"))
    indexes = diff_keyed(name, bt["indexes"], at["indexes"],
                         lambda i: index_key(i, rename), lambda i: index_key(i, {}),
                         "index", events, at.get("oid"))
    touched = (bt["comment"] != at["comment"]
               or any(c["status"] != "same" for c in columns)
               or any(x["status"] != "same" for x in constraints + indexes))
    return {
        "name": name, "status": "changed" if touched else "same",
        "comment": {"before": bt["comment"], "after": at["comment"]},
        "ddl": {"before": bt.get("ddl"), "after": at.get("ddl")},
        "columns": columns, "constraints": constraints, "indexes": indexes,
    }


def table_order(tables):
    """참조되는 테이블을 먼저 놓는다. 순환이 있으면 남은 것을 이름 순으로 붙인다."""
    deps = {t["name"]: {k["ref_table"] for k in t["constraints"]
                        if k["kind"] == "f" and k["ref_table"] != t["name"]} for t in tables}
    order, done = [], set()
    while len(order) < len(deps):
        ready = sorted(n for n, d in deps.items() if n not in done and d <= done | (d - deps.keys()))
        if not ready:
            ready = sorted(n for n in deps if n not in done)[:1]
        for n in ready:
            order.append(n)
            done.add(n)
    return order


def diff(before, after):
    events = []
    sure = bool(before.get("replay_id")) and before.get("replay_id") == after.get("replay_id")
    ident = sure or bool(before.get("linked") and after.get("linked"))
    pairs, dropped, added = match_items(
        before["tables"], after["tables"], "oid" if ident else None,
        lambda t: tuple(sorted((c["name"], c["type"]) for c in t["columns"])), sure,
    )
    table_rename = {b["name"]: a["name"] for b, a, _ in pairs if b["name"] != a["name"]}
    paired = {a["name"]: (b, how) for b, a, how in pairs}
    bt = {t["name"]: t for t in before["tables"]}
    ref_renames = {}
    for b, a, how in pairs:
        cp, _, _ = column_pairs(b["columns"], a["columns"], ident and how != "recreated", sure)
        ref_renames[a["name"]] = {x["name"]: y["name"] for x, y, _ in cp if x["name"] != y["name"]}

    tables = []
    for t in after["tables"]:
        if t["name"] not in paired:
            tables.append(whole_table(t, "added", events))
            continue
        old, how = paired[t["name"]]
        if how == "recreated":
            # 다시 만든 테이블은 화면에서 이전 정의와 비교해 보여 주되, 컬럼 단위 이벤트는 계보를 잇지 않으므로 내지 않는다.
            row = diff_table(old, t, [], table_rename, ref_renames, ident=False)
            row.update(status="changed", recreated=True)
            events.append({"kind": "table_recreated", "table": t["name"], "column": None,
                           "before": old["name"], "after": t["name"], "oid": t.get("oid")})
        else:
            row = diff_table(old, t, events, table_rename, ref_renames, ident, sure)
        if old["name"] != t["name"]:
            row.update(status="renamed", before_name=old["name"], confidence=how)
            events.append({"kind": "table_renamed", "table": t["name"], "column": None,
                           "before": old["name"], "after": t["name"], "confidence": how, "oid": t.get("oid")})
        tables.append(row)
    for t in dropped:
        tables.append(whole_table(t, "dropped", events))

    order = table_order(before["tables"] + [t for t in after["tables"] if t["name"] not in bt])
    rank = {n: i for i, n in enumerate(order)}
    tables.sort(key=lambda t: (t["status"] == "dropped",
                               rank.get(t["name"], rank.get(t.get("before_name"), len(rank)))))

    bv, av = by_name(before["views"]), by_name(after["views"])
    views = []
    for name in sorted(bv.keys() | av.keys()):
        b, a = bv.get(name), av.get(name)
        status = "added" if b is None else "dropped" if a is None else \
            "same" if b["definition"] == a["definition"] else "changed"
        views.append({"name": name, "status": status,
                      "before": b and b["definition"], "after": a and a["definition"],
                      "ddl": {"before": b and b.get("ddl"), "after": a and a.get("ddl")}})
        if status != "same":
            events.append({"kind": f"view_{status}", "table": name, "column": None,
                           "before": b and b["definition"], "after": a and a["definition"]})

    counts = {}
    for e in events:
        counts[e["kind"]] = counts.get(e["kind"], 0) + 1
    return {
        "before": {"source": before.get("source")},
        "after": {"source": after.get("source")},
        "tables": tables, "views": views, "events": events, "counts": counts,
    }


def main():
    early()
    parser = argparse.ArgumentParser()
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    add_argument(parser)
    args = parser.parse_args()
    init(args)
    result = diff(json.loads(args.before.read_text()), json.loads(args.after.read_text()))
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(L(f"이벤트 {len(result['events'])}건 -> {args.out}", f"{len(result['events'])} events -> {args.out}"))
    for kind, n in sorted(result["counts"].items()):
        print(f"  {kind}: {n}")


if __name__ == "__main__":
    main()
