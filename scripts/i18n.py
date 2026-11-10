"""출력 언어(ko/en) 결정과 문구 선택.

우선순위: 명령줄 --lang > 환경 변수 SCHEMA_TIMELINE_LANG > 로케일(LC_ALL, LC_MESSAGES, LANG 중 처음 값이 있는 것이 ko 로 시작하면 ko) > en.
set_lang() 으로 정한 값이 없으면 L() 은 호출할 때마다 환경에서 다시 결정한다.

셸에서: python3 i18n.py [ko|en]  → 결정된 언어를 한 줄로 출력한다.
"""
import os
import sys

LANGS = ("ko", "en")
ENV = "SCHEMA_TIMELINE_LANG"
_current = None


def resolve(flag=None, environ=None):
    """flag > SCHEMA_TIMELINE_LANG > 로케일 > en 순으로 언어를 정한다.

    @param flag --lang 값(없으면 None). ko·en 이 아니면 ValueError
    @param environ 환경 변수 사전(기본 os.environ)
    """
    env = os.environ if environ is None else environ
    if flag:
        if flag not in LANGS:
            raise ValueError(f"--lang must be one of: {', '.join(LANGS)} (got {flag!r})")
        return flag
    value = (env.get(ENV) or "").strip().lower()
    if value in LANGS:
        return value
    for key in ("LC_ALL", "LC_MESSAGES", "LANG"):
        locale = env.get(key)
        if locale:
            return "ko" if locale.lower().startswith("ko") else "en"
    return "en"


def set_lang(lang):
    """현재 언어를 정한다. None 이면 정한 값을 지워 환경에서 다시 결정하게 한다."""
    global _current
    if lang is not None and lang not in LANGS:
        raise ValueError(f"lang must be one of: {', '.join(LANGS)}")
    _current = lang


def get_lang():
    return _current or resolve()


def L(ko, en):
    """현재 언어의 문구를 돌려준다."""
    return ko if get_lang() == "ko" else en


def plural(n, one, many):
    """영어 단수·복수 표기: plural(1, "step", "steps") → "1 step"."""
    return f"{n} {one if n == 1 else many}"


def warn_bad_env(flag=None, environ=None):
    """--lang 이 없고 SCHEMA_TIMELINE_LANG 값이 ko·en 이 아니면 무시했다는 경고 한 줄을 stderr 에 낸다."""
    env = os.environ if environ is None else environ
    value = (env.get(ENV) or "").strip()
    if flag or not value or value.lower() in LANGS:
        return
    lang = resolve(None, env)
    msg = (f"경고: 환경 변수 {ENV}={value!r} 은 ko·en 이 아니라 무시했습니다." if lang == "ko"
           else f"Warning: ignored {ENV}={value!r} (expected ko or en).")
    print(msg, file=sys.stderr)


def add_argument(parser):
    """--lang 옵션을 argparse 파서에 붙인다."""
    parser.add_argument("--lang", choices=LANGS, help="ko|en. Output language (default: SCHEMA_TIMELINE_LANG, then locale, then en)")


def early(argv=None):
    """파서를 만들기 전에 argv 의 --lang 을 보고 현재 언어를 정한다. 도움말·사용법 문구에 쓴다."""
    argv = sys.argv[1:] if argv is None else argv
    flag = None
    for i, a in enumerate(argv):
        if a == "--lang" and i + 1 < len(argv):
            flag = argv[i + 1]
        elif a.startswith("--lang="):
            flag = a[len("--lang="):]
    try:
        lang = resolve(flag)
    except ValueError:
        lang = resolve()
    set_lang(lang)
    return lang


def init(args):
    """파싱한 args.lang 으로 현재 언어를 정하고 그 값을 돌려준다."""
    flag = getattr(args, "lang", None)
    lang = resolve(flag)
    set_lang(lang)
    warn_bad_env(flag)
    return lang


if __name__ == "__main__":
    try:
        flag = sys.argv[1] if len(sys.argv) > 1 else None
        print(resolve(flag))
        warn_bad_env(flag)
    except ValueError as e:
        print(e, file=sys.stderr)
        sys.exit(2)
