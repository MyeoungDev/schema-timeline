"""클라이언트 지시문이 섞인 SQL 스크립트를 문장 단위로 나눈다. MySQL 의 DELIMITER, Oracle SQL*Plus 의 / 와 PL/SQL 블록.

oracle_script() 는 sqlplus 에 넘길 정규형을 만든다. SQL 문은 줄 끝 ; 로, PL/SQL 블록은 / 줄로 끝나게 하고
SQL*Plus 명령 줄(SET, PROMPT 등)은 뺀다. 마지막 문장에 ; 나 / 가 없어 sqlplus 가 조용히 실행하지 않는 일을 막는다.
normalize() 는 분석용이다. 문장마다 ; 로 끝나고, 프로시저·트리거 본문 안의 ; 는 공백으로 바꿔 본문이 한 문장이 된다.
"""
import re

from i18n import L
from sqltext import statement_spans, tokens

SQLPLUS_COMMAND = re.compile(
    r"^\s*(?:SET(?!\s+(?:TRANSACTION|CONSTRAINTS?|ROLE)\b)|PRO(?:MPT)?|REM(?:ARK)?|SPO(?:OL)?|WHENEVER|SHO(?:W)?|COL(?:UMN)?"
    r"|TTITLE|BTITLE|BREAK|CLEAR|DEF(?:INE)?|UNDEF(?:INE)?|ACCEPT|PAUSE|EXIT|QUIT|CONN(?:ECT)?|DISC(?:ONNECT)?)\b|^\s*@",
    re.I)
SQLPLUS_EXEC = re.compile(r"^\s*EXEC(?:UTE)?\s+(.+?)\s*;?\s*$", re.I)
PLSQL_START = re.compile(r"^\s*(?:CREATE\s+(?:OR\s+REPLACE\s+)?(?:(?:NON)?EDITIONABLE\s+)?"
                         r"(?:TRIGGER|PROCEDURE|FUNCTION|PACKAGE|TYPE|LIBRARY|JAVA)\b|DECLARE\b|BEGIN\b)", re.I)
TYPE_SPEC = re.compile(r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:(?:NON)?EDITIONABLE\s+)?TYPE\s+(?!BODY\b)", re.I)
# PL/SQL 본문 안에 직접 올 수 없는 문장(DDL). 이것이 END; 뒤에 오면 앞 블록의 / 가 빠진 것이다.
TOP_LEVEL = re.compile(r"^\s*(?:CREATE\s+(?:OR\s+REPLACE\s+)?(?:TABLE|VIEW|FORCE|INDEX|UNIQUE|BITMAP|SEQUENCE|SYNONYM|GLOBAL)\b"
                       r"|ALTER|DROP|RENAME|COMMENT|GRANT|REVOKE|TRUNCATE)\b", re.I)
SLASH = re.compile(r"^\s*/\s*$")
END_LINE = re.compile(r"^\s*END(?!\s+(?:IF|LOOP|CASE)\b)\b[^;]*;\s*$", re.I)
DELIMITER = re.compile(r"^\s*DELIMITER\s+(\S+)\s*$", re.I)


def ends_statement(text, dialect):
    """text 의 마지막 코드가 ; 로 끝나는지(문자열·주석 안은 제외)."""
    for kind, t in reversed(tokens(text, dialect)):
        if kind == "comment" or not t.strip():
            continue
        return kind == "code" and t.rstrip().endswith(";")
    return False


def has_code(text):
    return any(k != "comment" and t.strip() for k, t in tokens(text, "oracle"))


def code_head(text):
    """주석을 뺀 앞부분 코드. 블록 시작 판정에 쓴다."""
    return "".join(" " if k == "comment" else t for k, t in tokens(text, "oracle")).lstrip()


def may_end(line):
    """이 줄로 문장이 끝날 수 있는지 빠르게 거른다(; 가 줄 끝이나 주석 앞에 있을 때만 전체를 다시 읽는다)."""
    return ";" in line


def oracle_chunks(sql):
    """Oracle 스크립트를 [(종류, 텍스트)] 와 경고로 나눈다. 종류는 sql(; 로 끝남), plsql(/ 없이 본문만)."""
    chunks, warnings, buf, mode = [], [], [], None
    for lineno, line in enumerate(sql.splitlines(), 1):
        if mode is None:
            if not line.strip() or line.strip().startswith("--"):
                continue
            if SLASH.match(line):
                continue
            m = SQLPLUS_EXEC.match(line)
            if m:
                chunks.append(("plsql", f"BEGIN {m.group(1).rstrip(';')}; END;"))
                continue
            if SQLPLUS_COMMAND.match(line):
                continue
            mode, buf = ("plsql" if PLSQL_START.match(code_head(line)) else "sql"), [line]
            if mode == "sql" and ends_statement(line, "oracle"):
                chunks += split_sql("\n".join(buf)); mode, buf = None, []
        elif mode == "sql":
            if len(buf) < 6 and PLSQL_START.match(code_head("\n".join(buf + [line]))):
                mode, buf = "plsql", buf + [line]
                continue
            if SLASH.match(line):
                chunks += split_sql("\n".join(buf)); mode, buf = None, []
                continue
            buf.append(line)
            if may_end(line) and ends_statement("\n".join(buf), "oracle"):
                chunks += split_sql("\n".join(buf)); mode, buf = None, []
        else:
            if SLASH.match(line):
                chunks.append(("plsql", "\n".join(buf).rstrip())); mode, buf = None, []
                continue
            prev = next((l for l in reversed(buf) if l.strip()), "")
            type_closed = TYPE_SPEC.match(code_head("\n".join(buf[:4]))) and prev.rstrip().endswith(";")
            started = line.strip() and not line.strip().startswith("--")
            if (type_closed and started) or (END_LINE.match(prev) and (TOP_LEVEL.match(line) or PLSQL_START.match(line))):
                warnings.append(L(f"PL/SQL 블록 뒤에 / 가 없어 그 다음 문장(파일 {lineno}번째 줄)부터 블록 본문이 됐습니다.",
                                  f"A PL/SQL block has no closing /, so the statement that follows (file line {lineno}) became part of the block body."))
            buf.append(line)
    if buf:
        chunks += split_sql("\n".join(buf)) if mode == "sql" else [("plsql", "\n".join(buf).rstrip())]
    return chunks, warnings


def split_sql(text):
    """SQL 문 묶음을 문장마다 하나로. 마지막 문장에 ; 가 없으면 붙인다."""
    out = []
    for start, end, _ in statement_spans(text, "oracle"):
        stmt = text[start:end].rstrip()
        out.append(("sql", stmt if stmt.endswith(";") else stmt + ";"))
    return out


def oracle_script(sql):
    """sqlplus 에 넘길 정규형과 경고. @return (script, warnings)"""
    chunks, warnings = oracle_chunks(sql)
    parts = [text if kind == "sql" else text + "\n/" for kind, text in chunks]
    return "\n".join(parts) + "\n", warnings


def blank_inner(text, dialect):
    """본문 안의 ; 를 공백으로(문자열·주석 밖만)."""
    return "".join(t.replace(";", " ") if k == "code" else t for k, t in tokens(text, dialect))


def mysql_chunks(sql):
    """DELIMITER 를 반영해 [(종류, 텍스트)] 로. 종류는 sql(; 로 나뉘는 보통 구간), routine(바꾼 구분자로 끝난 문장)."""
    chunks, delim, buf, plain = [], ";", [], []
    for line in sql.splitlines():
        m = DELIMITER.match(line)
        if m:
            if plain:
                chunks.append(("sql", "\n".join(plain))); plain = []
            if buf:
                chunks.append(("routine", "\n".join(buf))); buf = []
            delim = m.group(1)
            continue
        if delim == ";":
            plain.append(line)
            continue
        buf.append(line)
        if line.rstrip().endswith(delim):
            buf[-1] = line.rstrip()[:-len(delim)]
            chunks.append(("routine", "\n".join(buf))); buf = []
    if plain:
        chunks.append(("sql", "\n".join(plain)))
    if buf:
        chunks.append(("routine", "\n".join(buf)))
    return chunks


def normalize(sql, dialect="postgres"):
    """분석용 SQL. 문장마다 ; 로 끝나고, 루틴·PL/SQL 블록 본문 안의 ; 는 공백이 되어 한 문장으로 읽힌다."""
    if dialect == "oracle":
        chunks, _ = oracle_chunks(sql)
        return "\n".join(t if k == "sql" else blank_inner(t, dialect) + ";" for k, t in chunks)
    if dialect == "mysql":
        return "\n".join(t if k == "sql" else blank_inner(t, dialect) + ";" for k, t in mysql_chunks(sql))
    return sql
