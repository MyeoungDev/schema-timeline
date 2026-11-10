"""고정 식별자(oid·attnum)를 카탈로그에서 얻을 수 없는 DB 의 테이블·컬럼 id 를 단계마다 이어 붙인다.

단계에서 실행한 SQL 의 이름 변경·삭제 문(RENAME TABLE, ALTER TABLE ... RENAME [COLUMN], CHANGE, DROP)을 읽고,
변경 전 스냅샷에 옛 이름이 있고 변경 후 스냅샷에 새 이름이 있을 때만 같은 id 를 준다.
그 밖에는 같은 이름이면 같은 id, 삭제 문이 있었으면 새 id 다. 결과는 테이블의 oid, 컬럼의 ordinal 에 넣는다.
"""
import re

from scriptsplit import normalize
from sqltext import statements

IDENT = r'(?:`(?:[^`]|``)+`|"(?:[^"]|"")+"|\[[^\]]+\]|[\w$#]+)'
QNAME = rf"{IDENT}(?:\s*\.\s*{IDENT})*"
NOT_TABLE_RENAME = re.compile(r"^(COLUMN|INDEX|KEY|CONSTRAINT|PARTITION)\b", re.I)
NOT_COLUMN_DROP = re.compile(r"^(INDEX|KEY|PRIMARY|FOREIGN|CONSTRAINT|CHECK|PARTITION|DEFAULT|UNUSED)\b", re.I)


def unquote(raw, fold):
    """따옴표를 벗긴다. 따옴표 없는 이름은 fold 로 바꾼다."""
    if raw[0] in '`"[':
        return raw[1:-1].replace('``', '`').replace('""', '"')
    return fold(raw)


def last_name(qname, fold):
    """schema.table 의 마지막 이름."""
    parts = re.findall(IDENT, qname)
    return unquote(parts[-1] if parts else qname, fold)


def other_schema(qname, fold, schema):
    """schema.table 의 스키마가 재생 스키마와 다른지. 스키마를 안 붙였거나 schema 를 모르면 False."""
    parts = re.findall(IDENT, qname)
    return bool(schema) and len(parts) > 1 and unquote(parts[-2], fold) != schema


def split_specs(text):
    """ALTER TABLE 뒤의 변경 목록을 괄호 밖 쉼표로 나눈다."""
    out, depth, cur = [], 0, []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur).strip()); cur = []
        else:
            cur.append(ch)
    out.append("".join(cur).strip())
    return [x for x in out if x]


def hints(sql, fold=lambda s: s, dialect="postgres", schema=None):
    """SQL 에서 이름 변경·삭제를 뽑는다. 다른 스키마의 객체를 가리키는 문장은 무시한다.

    @param fold 따옴표 없는 이름을 카탈로그 표기로 바꾸는 함수(Oracle 은 대문자)
    @param dialect DELIMITER·SQL*Plus 지시문과 문자열 규칙(scriptsplit.normalize)
    @param schema 재생 스키마 이름(카탈로그 표기)
    @return {"tables": [(옛, 새)], "columns": [(테이블, 옛, 새)], "dropped_tables": set, "dropped_columns": set((테이블, 컬럼))}
    """
    h = {"tables": [], "columns": [], "dropped_tables": set(), "dropped_columns": set()}
    name = lambda q: last_name(q, fold)
    mine = lambda q: not other_schema(q, fold, schema)
    for st in statements(normalize(sql, dialect), dialect):
        s = " ".join(st.split())
        m = re.match(rf"RENAME\s+TABLE\s+(.+)$", s, re.I)
        if m:
            for pair in split_specs(m.group(1)):
                mm = re.match(rf"({QNAME})\s+TO\s+({QNAME})$", pair, re.I)
                if mm and mine(mm.group(1)):
                    h["tables"].append((name(mm.group(1)), name(mm.group(2))))
            continue
        m = re.match(rf"RENAME\s+({QNAME})\s+TO\s+({QNAME})$", s, re.I)
        if m and mine(m.group(1)):
            h["tables"].append((name(m.group(1)), name(m.group(2))))
            continue
        m = re.match(rf"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?(.+?)(?:\s+(?:CASCADE|RESTRICT|PURGE|CASCADE\s+CONSTRAINTS))*$", s, re.I)
        if m:
            h["dropped_tables"] |= {name(x) for x in split_specs(m.group(1)) if mine(x)}
            continue
        m = re.match(rf"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?({QNAME})\s+(.+)$", s, re.I)
        if not m or not mine(m.group(1)):
            continue
        table = name(m.group(1))
        for spec in split_specs(m.group(2)):
            mm = re.match(rf"RENAME\s+COLUMN\s+({IDENT})\s+TO\s+({IDENT})", spec, re.I)
            if mm:
                h["columns"].append((table, name(mm.group(1)), name(mm.group(2))))
                continue
            mm = re.match(rf"CHANGE\s+(?:COLUMN\s+)?(?:IF\s+EXISTS\s+)?({IDENT})\s+({IDENT})\s", spec + " ", re.I)
            if mm and name(mm.group(1)) != name(mm.group(2)):
                h["columns"].append((table, name(mm.group(1)), name(mm.group(2))))
                continue
            mm = re.match(r"RENAME\s+(?:TO\s+|AS\s+)?(.+)$", spec, re.I)
            if mm and not NOT_TABLE_RENAME.match(mm.group(1)):
                h["tables"].append((table, name(mm.group(1))))
                continue
            mm = re.match(r"DROP\s+(?:COLUMN\s+)?(?:IF\s+EXISTS\s+)?(.+)$", spec, re.I)
            if mm and not NOT_COLUMN_DROP.match(mm.group(1)):
                cols = mm.group(1).strip()
                cols = cols[1:-1] if cols.startswith("(") and cols.endswith(")") else re.match(IDENT, cols).group(0)
                h["dropped_columns"] |= {(table, name(c)) for c in split_specs(cols)}
    return h


class IdMap:
    """단계를 따라가며 테이블 id(oid)와 컬럼 id(ordinal)를 이어 준다. 새 DB 에서 다시 시작할 때는 새 IdMap 을 쓴다.

    @param case_insensitive_columns 컬럼 이름을 대소문자 구분 없이 짝지을지(MySQL·MariaDB)
    """

    def __init__(self, case_insensitive_columns=False):
        self.next = 1
        self.prev = {}
        self.ci = case_insensitive_columns

    def new_id(self):
        self.next += 1
        return self.next - 1

    def assign(self, doc, h=None):
        """doc 의 테이블·컬럼에 id 를 넣는다. h 는 이번 단계 SQL 의 hints() 결과다."""
        h = h or {"tables": [], "columns": [], "dropped_tables": set(), "dropped_columns": set()}
        ck = (lambda s: s.lower()) if self.ci else (lambda s: s)
        renamed = {}
        for old, new in h["tables"]:
            src = next((k for k, v in renamed.items() if v == old), old)
            renamed[src] = new
        now = {t["name"] for t in doc["tables"]}
        source = {new: old for old, new in renamed.items() if old in self.prev and new in now}
        moved = set(source.values())
        result = {}
        for t in doc["tables"]:
            old = source.get(t["name"])
            if old is None and t["name"] in self.prev and t["name"] not in moved and t["name"] not in h["dropped_tables"]:
                old = t["name"]
            prev = self.prev.get(old) if old else None
            t["oid"] = prev["id"] if prev else self.new_id()
            names = {old, t["name"]} - {None}
            col_ren = {}
            for tb, a, b in h["columns"]:
                if tb in names:
                    src = next((k for k, v in col_ren.items() if v == ck(a)), ck(a))
                    col_ren[src] = ck(b)
            dropped = {ck(c) for tb, c in h["dropped_columns"] if tb in names}
            cols_now = {ck(c["name"]) for c in t["columns"]}
            pcols = prev["columns"] if prev else {}
            csource = {new: old for old, new in col_ren.items() if old in pcols and new in cols_now}
            cmoved = set(csource.values())
            cols = {}
            for c in t["columns"]:
                key = ck(c["name"])
                cold = csource.get(key)
                if cold is None and key in pcols and key not in cmoved and key not in dropped:
                    cold = key
                c["ordinal"] = pcols[cold] if cold else self.new_id()
                cols[key] = c["ordinal"]
            result[t["name"]] = {"id": t["oid"], "columns": cols}
        self.prev = result
        return doc
