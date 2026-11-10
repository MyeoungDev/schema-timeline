"""템플릿 조립 도구. 공용 CSS·JS 를 템플릿에 끼우고 데이터를 내장해 외부 요청 없는 단일 HTML 을 만든다(render_timeline.py 가 쓴다)."""
import json
from datetime import datetime
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
TOKENS = TEMPLATES / "tokens.css"


def template(name):
    """색 토큰과 SQL 보기 모듈을 끼워 넣은 템플릿 텍스트."""
    return ((TEMPLATES / name).read_text()
            .replace("/*__TOKENS__*/", TOKENS.read_text())
            .replace("/*__SQLVIEW_CSS__*/", (TEMPLATES / "sqlview.css").read_text())
            .replace("/*__SQLVIEW_JS__*/", (TEMPLATES / "sqlview.js").read_text())
            .replace("/*__DIFF_JS__*/", (TEMPLATES / "diff.js").read_text())
            .replace("/*__EXPORT_JS__*/", (TEMPLATES / "export.js").read_text())
            .replace("/*__RISKS_JS__*/", (TEMPLATES / "risks.js").read_text()))


def embed(html, doc):
    """doc 을 JSON 으로 /*__DATA__*/ 자리에 넣는다.

    <script> 블록 안에 들어가므로 < 를 모두 유니코드 이스케이프한다. </script> 와 <!--<script> 가 HTML 파서를 흔들지 않게 된다.
    """
    data = json.dumps(doc, ensure_ascii=False).replace("<", "\\u003c")
    return html.replace("/*__DATA__*/", data)


def now():
    return datetime.now().astimezone().isoformat(timespec="minutes").replace("T", " ")
