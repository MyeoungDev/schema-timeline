"""SQL 텍스트를 실행하지 않고 읽는 도구: 문장 나누기, 첫 키워드, 비밀번호 가림.

문자열('..', E'..', U&'..'), 따옴표 식별자, 달러 인용($$..$$, $tag$..$tag$), 주석(--, /* */)을
왼쪽에서 오른쪽으로 한 번에 읽는다. 달러 인용 본문(함수·DO 블록)은 문장의 일부로만 보고 안을 해석하지 않는다.
dialect 가 mysql 이면 문자열 안의 백슬래시 이스케이프, "..." 문자열, `..` 식별자를 쓰고 달러 인용은 없다. oracle 은 달러 인용이 없다.
"""
import re

DOLLAR = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$")
# 문장 앞부분이 이렇게 끝나면 다음 리터럴은 비밀번호다: PASSWORD '..', PASSWORD = '..', PASSWORD('..'),
# IDENTIFIED BY / IDENTIFIED WITH 플러그인 BY|AS, ALTER USER .. REPLACE '옛 값', SET PASSWORD [FOR ..] = '..'
SECRET_CONTEXT = re.compile(r"(?:(?<![A-Za-z0-9])PASSWORD\s*(?:=|\()?|\bIDENTIFIED\s+(?:WITH\s+\S+\s+)?(?:BY|AS)|\bREPLACE)\s*$"
                            r"|^\s*SET\s+PASSWORD\b[^=]*=\s*(?:PASSWORD\s*\(\s*)?$", re.IGNORECASE)
# Oracle 의 따옴표 없는 IDENTIFIED BY 값
BARE_IDENTIFIED = re.compile(r"(\bIDENTIFIED\s+BY\s+)(?!VALUES\b)([^\s'\";]+)", re.IGNORECASE)
BARE_REPLACE = re.compile(r"(\bIDENTIFIED\s+BY\s+\S+\s+REPLACE\s+)([^\s'\";]+)", re.IGNORECASE)
# 줄 전체가 자유 텍스트인 SQL*Plus 명령(따옴표 짝을 맞추지 않는다)
SQLPLUS_TEXT_LINE = re.compile(r"[ \t]*(?:REM(?:ARK)?|PRO(?:MPT)?)(?:[ \t]|$)", re.IGNORECASE)
Q_QUOTE_CLOSE = {"[": "]", "(": ")", "{": "}", "<": ">"}
# 문자열 리터럴 안의 PASSWORD ''값''(따옴표 두 겹)과, 일반 텍스트 안의 PASSWORD '값'
QUOTED_PASSWORD = re.compile(r"(\bPASSWORD\s+)''((?:[^']|'{4})*)''", re.IGNORECASE)
PLAIN_PASSWORD = re.compile(r"(\bPASSWORD\s+)'([^']*)'", re.IGNORECASE)
CONN_SECRET = re.compile(r"(\bpassword\s*=\s*)(''(?:[^']|'''')*?''|'[^']*'|[^\s'\";]+)", re.IGNORECASE)


def tokens(sql, dialect="postgres"):
    """(종류, 텍스트) 목록. 종류는 code, string, ident, dollar, comment 다."""
    mysql = dialect == "mysql"
    out, i, n = [], 0, len(sql)
    code_start = 0

    def flush(end):
        if end > code_start:
            out.append(("code", sql[code_start:end]))

    while i < n:
        c = sql[i]
        line_start = i == 0 or sql[i - 1] == "\n"
        if (mysql and c == "#") or (dialect == "oracle" and line_start and SQLPLUS_TEXT_LINE.match(sql, i)
                                    and _between_statements(out, sql[code_start:i])):
            flush(i)
            j = sql.find("\n", i)
            j = n if j < 0 else j
            out.append(("comment", sql[i:j])); i = code_start = j
        elif dialect == "oracle" and c in "qQnN" and not _word_before(sql, i) and re.match(r"[nN]?[qQ]'", sql[i:i + 3]):
            flush(i)
            k = sql.index("'", i) + 1
            close = Q_QUOTE_CLOSE.get(sql[k:k + 1], sql[k:k + 1])
            j = sql.find(close + "'", k + 1)
            j = n if j < 0 or not close else j + 2
            out.append(("string", sql[i:j])); i = code_start = j
        elif c == "-" and sql.startswith("--", i):
            flush(i)
            j = sql.find("\n", i)
            j = n if j < 0 else j
            out.append(("comment", sql[i:j])); i = code_start = j
        elif c == "/" and sql.startswith("/*", i):
            flush(i)
            depth, j = 1, i + 2
            while j < n and depth:
                if sql.startswith("/*", j): depth += 1; j += 2
                elif sql.startswith("*/", j): depth -= 1; j += 2
                else: j += 1
            out.append(("comment", sql[i:j])); i = code_start = j
        elif c == "'" or (mysql and c == '"') \
                or (not mysql and c in "Ee" and sql.startswith("'", i + 1) and not _word_before(sql, i)) \
                or (not mysql and c in "Uu" and sql.startswith("&'", i + 1) and not _word_before(sql, i)):
            flush(i)
            start = i
            escapes = mysql or c in "Ee"
            quote = '"' if c == '"' else "'"
            i = sql.index(quote, i) + 1
            while i < n:
                if escapes and sql[i] == "\\":
                    i += 2; continue
                if sql[i] == quote:
                    if sql.startswith(quote * 2, i): i += 2; continue
                    i += 1; break
                i += 1
            out.append(("string", sql[start:i])); code_start = i
        elif mysql and c == "`":
            flush(i)
            j = i + 1
            while j < n:
                if sql[j] == "`":
                    if sql.startswith("``", j): j += 2; continue
                    j += 1; break
                j += 1
            out.append(("ident", sql[i:j])); i = code_start = j
        elif c == '"':
            flush(i)
            j = i + 1
            while j < n:
                if sql[j] == '"':
                    if sql.startswith('""', j): j += 2; continue
                    j += 1; break
                j += 1
            out.append(("ident", sql[i:j])); i = code_start = j
        elif c == "$" and dialect == "postgres" and not _word_before(sql, i) and DOLLAR.match(sql, i):
            tag = DOLLAR.match(sql, i).group(0)
            flush(i)
            j = sql.find(tag, i + len(tag))
            j = n if j < 0 else j + len(tag)
            out.append(("dollar", sql[i:j])); i = code_start = j
        else:
            i += 1
    flush(n)
    return out


PLSQL_HEAD = re.compile(r"\s*(?:CREATE\s+(?:OR\s+REPLACE\s+)?(?:(?:NON)?EDITIONABLE\s+)?"
                        r"(?:TRIGGER|PROCEDURE|FUNCTION|PACKAGE|TYPE|LIBRARY|JAVA)\b|DECLARE\b|BEGIN\b)", re.I)


def _between_statements(out, pending):
    """지금 위치가 문장 사이인지. 앞 코드가 없거나 ; 또는 / 줄로 끝나야 하고, / 로 닫히지 않은 PL/SQL 블록 안이면 아니다.

    SQL*Plus 명령 줄(REM, PROMPT) 판정에 쓴다.
    """
    if pending.strip():
        kind, text = "code", pending
    else:
        kind, text = next(((k, t) for k, t in reversed(out) if k != "comment" and t.strip()), (None, None))
    if kind is None:
        return True
    if kind != "code":
        return False
    tail = text.rstrip()
    if tail.split("\n")[-1].strip() == "/":
        return True
    if not tail.endswith(";"):
        return False
    code = "".join(" " if k == "comment" else "''" if k in ("string", "dollar") else t for k, t in out) + pending
    since_slash = re.split(r"(?m)^[ \t]*/[ \t]*$", code)[-1]
    return not any(PLSQL_HEAD.match(piece) for piece in since_slash.split(";"))


def _word_before(sql, i):
    return i > 0 and (sql[i - 1].isalnum() or sql[i - 1] in "_$")


def statements(sql, dialect="postgres"):
    """주석을 빼고 문자열·달러 인용을 자리표시('')로 바꾼 문장 목록. 식별자는 그대로 둔다."""
    parts, cur = [], []
    for kind, text in tokens(sql, dialect):
        if kind == "comment":
            cur.append(" ")
        elif kind in ("string", "dollar"):
            cur.append("''")
        elif kind == "ident":
            cur.append(text)
        else:
            pieces = text.split(";")
            for k, p in enumerate(pieces):
                cur.append(p)
                if k < len(pieces) - 1:
                    parts.append("".join(cur).strip()); cur = []
    tail = "".join(cur).strip()
    if tail:
        parts.append(tail)
    return [p for p in parts if p]


def statement_spans(sql, dialect="postgres"):
    """문장마다 (시작, 끝, 정규화한 문장). 끝은 마침 ; 다음 위치다. 정규화는 statements() 와 같다."""
    spans, cur, start, pos = [], [], None, 0
    for kind, text in tokens(sql, dialect):
        if kind == "code":
            k = 0
            for piece in text.split(";")[:-1]:
                if start is None and piece.strip():
                    start = pos + k + (len(piece) - len(piece.lstrip()))
                cur.append(piece)
                k += len(piece) + 1
                if start is not None:
                    spans.append((start, pos + k, "".join(cur).strip()))
                cur, start = [], None
            rest = text.split(";")[-1]
            if start is None and rest.strip():
                start = pos + k + (len(rest) - len(rest.lstrip()))
            cur.append(rest)
        else:
            if start is None and kind != "comment":
                start = pos
            cur.append(" " if kind == "comment" else "''" if kind in ("string", "dollar") else text)
        pos += len(text)
    if start is not None and "".join(cur).strip():
        spans.append((start, len(sql), "".join(cur).strip()))
    return spans


def head(stmt, n=3):
    """문장 앞 n 개 키워드(대문자)."""
    return " ".join(re.findall(r"[A-Za-z_]+", stmt)[:n]).upper()


def mask_secrets(sql, dialect="postgres"):
    """PASSWORD·IDENTIFIED BY 등 뒤의 값과 연결 문자열의 password= 값을 가린다.

    @return (가린 SQL, 가린 값 목록). 값 목록은 오류 메시지에서 같은 문자열을 지우는 데 쓴다.
    """
    out, secrets, ctx = [], [], ""
    for kind, text in tokens(sql, dialect):
        secret_here = bool(SECRET_CONTEXT.search(ctx))
        if kind == "ident" and secret_here and text[0] == '"':
            secrets.append(text[1:-1].replace('""', '"'))
            out.append('"********"')
        elif kind in ("string", "dollar") and secret_here:
            secrets += _literal_values(kind, text, dialect)
            prefix = re.match(r"^(E|U&)?", text, re.I).group(0) if kind == "string" and dialect == "postgres" else ""
            out.append(f"{prefix}'********'")
        elif kind == "dollar":
            tag = DOLLAR.match(text).group(0)
            closed = text.endswith(tag) and len(text) >= 2 * len(tag)
            body, found = mask_secrets(text[len(tag):len(text) - len(tag)], dialect) if closed else (text, [])
            if found:
                secrets += found
                out.append(tag + body + tag)
            else:
                out.append(mask_plain(text, secrets))
        elif kind == "string":
            secrets += [m.group(2).replace("''''", "''") for m in QUOTED_PASSWORD.finditer(text)]
            out.append(mask_plain(QUOTED_PASSWORD.sub(lambda m: m.group(1) + "''********''", text), secrets))
        elif kind == "code":
            secrets += [m.group(2) for r in (BARE_IDENTIFIED, BARE_REPLACE) for m in r.finditer(text)]
            text = BARE_REPLACE.sub(lambda m: m.group(1) + "********", text)
            out.append(BARE_IDENTIFIED.sub(lambda m: m.group(1) + "********", text))
        else:
            out.append(text)
        if kind == "code":
            ctx = (ctx + text).split(";")[-1]
        elif kind != "comment":
            ctx += " '' "
    return "".join(out), [s for s in secrets if s]


def _literal_values(kind, text, dialect="postgres"):
    """리터럴의 값. MySQL 의 백슬래시 이스케이프 문자열은 원문 표기와 푼 값을 함께 돌려준다(오류 메시지가 원문을 되풀이한다)."""
    if kind == "dollar":
        tag = DOLLAR.match(text).group(0)
        return [text[len(tag):-len(tag)]]
    q = text[-1] if text[-1] in "'\"" else "'"
    body = text[text.index(q) + 1:-1]
    values = [body.replace(q * 2, q)]
    if dialect == "mysql" and "\\" in body:
        values += [body, re.sub(r"\\(.)", r"\1", body)]
    return values


def scrub(text, secrets):
    """오류 메시지에서 비밀번호 값을 지운다. 잘린 SQL 발췌(LINE n: ...)는 비밀번호가 있었던 단계면 통째로 뺀다."""
    for s in sorted(set(secrets), key=len, reverse=True):
        text = text.replace(s, "********")
    if secrets:
        text = "\n".join(l for l in text.splitlines() if not re.match(r"^\s*(LINE \d+:|\^)", l))
    return text


def mask_plain(text, found=None):
    """SQL 이 아닌 문자열(코멘트, 연결 문자열)에서 password= 값과 PASSWORD '값' 을 가린다.

    @param found 가린 값을 모을 목록. 주면 여기에 덧붙인다.
    """
    def conn(m):
        if found is not None:
            found.append(m.group(2).strip("'"))
        return m.group(1) + ("''********''" if m.group(2).startswith("''") else "********")
    text = CONN_SECRET.sub(conn, text)
    if text.startswith("'"):
        return text
    return PLAIN_PASSWORD.sub(lambda m: m.group(1) + "'********'", text)


def mask_snapshot(doc, secrets=()):
    """카탈로그 스냅샷 안의 텍스트(뷰 정의, 기본값, 코멘트, 제약·인덱스 정의)에서 비밀값을 가린다.

    @param secrets 마이그레이션 SQL 에서 찾아 둔 비밀번호 값. SQL 텍스트의 문자열 리터럴 안에서만 지운다.
    """
    known = sorted({s for s in secrets if s}, key=len, reverse=True)
    dialect = {"mysql": "mysql", "mariadb": "mysql", "oracle": "oracle"}.get(doc.get("engine"), "postgres")

    def literal(text):
        for s in known:
            text = text.replace(s, "********")
        return text

    def clean(v, sql):
        if not isinstance(v, str):
            return v
        if not sql:
            return mask_plain(v)
        v = mask_secrets(v, dialect)[0]
        return "".join(literal(t) if k == "string" else t for k, t in tokens(v, dialect)) if known else v

    for t in doc["tables"]:
        t["comment"] = clean(t.get("comment"), False)
        for c in t["columns"]:
            c["comment"] = clean(c.get("comment"), False)
            c["default"] = clean(c.get("default"), True)
        for k in t["constraints"] + t["indexes"]:
            k["definition"] = clean(k.get("definition"), True)
        if "ddl" in t:
            t["ddl"] = clean(t["ddl"], True)
    for v in doc["views"]:
        v["definition"] = clean(v.get("definition"), True)
    return doc
