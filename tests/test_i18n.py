"""출력 언어(ko/en) 결정과 페이지·라벨·점검 도구에 반영되는지 검증한다."""
import langpin  # noqa: F401  (SCHEMA_TIMELINE_LANG=ko)
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import i18n  # noqa: E402
from compare_timeline import build  # noqa: E402

HANGUL = re.compile("[가-힣]")


def env_for(lang):
    return {**os.environ, "SCHEMA_TIMELINE_LANG": lang}


def snap(source, tables):
    return {"engine": "postgres", "source": source, "schema_name": "public", "image": "postgres:16-alpine", "tables": tables,
            "views": [], "types": [], "schema": []}


def table(name, cols):
    return {"oid": 1, "name": name, "comment": None, "constraints": [], "indexes": [], "ddl": "",
            "columns": [{"name": c, "ordinal": i, "type": "integer", "nullable": True, "default": None, "comment": None}
                        for i, c in enumerate(cols, 1)]}


class ResolveTest(unittest.TestCase):
    def test_flag_beats_env_and_locale(self):
        self.assertEqual(i18n.resolve("en", {"SCHEMA_TIMELINE_LANG": "ko", "LANG": "ko_KR.UTF-8"}), "en")
        self.assertEqual(i18n.resolve("ko", {"SCHEMA_TIMELINE_LANG": "en", "LANG": "en_US"}), "ko")

    def test_env_beats_locale(self):
        self.assertEqual(i18n.resolve(None, {"SCHEMA_TIMELINE_LANG": "en", "LANG": "ko_KR.UTF-8"}), "en")
        self.assertEqual(i18n.resolve(None, {"SCHEMA_TIMELINE_LANG": "ko", "LANG": "en_US.UTF-8"}), "ko")

    def test_locale_order_and_fallback(self):
        self.assertEqual(i18n.resolve(None, {"LANG": "ko_KR.UTF-8"}), "ko")
        self.assertEqual(i18n.resolve(None, {"LC_MESSAGES": "ko_KR.UTF-8", "LANG": "en_US"}), "ko")
        self.assertEqual(i18n.resolve(None, {"LC_ALL": "en_US.UTF-8", "LANG": "ko_KR.UTF-8"}), "en")
        self.assertEqual(i18n.resolve(None, {"LC_ALL": "", "LANG": "ko_KR"}), "ko")
        self.assertEqual(i18n.resolve(None, {"LANG": "C"}), "en")
        self.assertEqual(i18n.resolve(None, {}), "en")
        self.assertEqual(i18n.resolve(None, {"SCHEMA_TIMELINE_LANG": "fr", "LANG": "ko_KR"}), "ko")

    def test_bad_flag_rejected(self):
        with self.assertRaises(ValueError):
            i18n.resolve("fr", {})

    def test_L_follows_set_lang(self):
        try:
            i18n.set_lang("en")
            self.assertEqual(i18n.L("가", "a"), "a")
            i18n.set_lang("ko")
            self.assertEqual(i18n.L("가", "a"), "가")
        finally:
            i18n.set_lang(None)

    def test_cli_resolves(self):
        run = lambda *a, env: subprocess.run([sys.executable, str(ROOT / "scripts" / "i18n.py"), *a], capture_output=True, text=True, env=env)
        self.assertEqual(run("en", env=env_for("ko")).stdout.strip(), "en")
        self.assertEqual(run(env=env_for("en")).stdout.strip(), "en")
        self.assertNotEqual(run("zz", env=env_for("en")).returncode, 0)


class LabelsTest(unittest.TestCase):
    def build(self, lang):
        i18n.set_lang(lang)
        try:
            with tempfile.TemporaryDirectory() as d:
                return build(snap(None, [table("a", ["x"])]), snap(None, [table("a", ["x", "y"])]), Path(d) / "o")
        finally:
            i18n.set_lang(None)

    def test_compare_labels(self):
        en, ko = self.build("en"), self.build("ko")
        self.assertEqual((en["base_label"], en["steps"][0]["label"], en["base_source"], en["source"]), ("Before", "After", "Before", "Before → After"))
        self.assertEqual((ko["base_label"], ko["steps"][0]["label"]), ("변경 전", "변경 후"))

    def test_scriptsplit_warning_language(self):
        from scriptsplit import oracle_script
        sql = "CREATE TYPE t AS OBJECT (a NUMBER);\nCREATE TABLE x (a NUMBER);\n"
        for lang, word in (("en", "no closing /"), ("ko", "/ 가 없어")):
            i18n.set_lang(lang)
            try:
                _, warnings = oracle_script(sql)
            finally:
                i18n.set_lang(None)
            self.assertIn(word, " ".join(warnings))

    def test_source_errors_follow_language(self):
        from sources import load
        with tempfile.TemporaryDirectory() as d:
            msgs = {}
            for lang in ("en", "ko"):
                i18n.set_lang(lang)
                try:
                    load("prisma", Path(d), None, "postgres")
                except SystemExit as e:
                    msgs[lang] = str(e)
                finally:
                    i18n.set_lang(None)
        self.assertIn("No prisma/migrations", msgs["en"])
        self.assertIn("찾지 못했습니다", msgs["ko"])


class RenderLangTest(unittest.TestCase):
    def render(self, lang):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d)
        i18n.set_lang(lang)
        try:
            build(snap(None, [table("a", ["x"])]), snap(None, [table("a", ["x", "y"])]), d / "o")
        finally:
            i18n.set_lang(None)
        env = env_for(lang)
        py = lambda s, *a: subprocess.run([sys.executable, str(ROOT / "scripts" / s), *a], check=True, capture_output=True, text=True, env=env)
        py("timeline.py", str(d / "o"), "--lang", lang)
        py("render_timeline.py", str(d / "o"), "--out", str(d / "o" / "timeline.html"), "--lang", lang)
        html = (d / "o" / "timeline.html").read_text()
        return html, None

    def test_html_lang_and_data(self):
        for lang in ("en", "ko"):
            html, _ = self.render(lang)
            self.assertTrue(html.startswith("<!DOCTYPE html>\n<html lang=\"%s\">" % lang) or html.lstrip().startswith("<!doctype html>\n<html lang=\"%s\">" % lang), html[:80])
            self.assertIn(f'"lang": "{lang}"', html)

    def test_title_follows_lang(self):
        en, _ = self.render("en")
        ko, _ = self.render("ko")
        self.assertIn("<title>Schema history</title>", en)
        self.assertIn("<title>스키마 이력</title>", ko)

    def test_embedded_compare_template_stays_ko(self):
        html, _ = self.render("en")
        self.assertIn('html lang=\\"ko\\"', html)


class SummaryLineTest(unittest.TestCase):
    def test_check_function_text_in_shells(self):
        for name in ("timeline.sh", "compare.sh"):
            text = (ROOT / "scripts" / name).read_text()
            self.assertIn("Check: passed", text)
            self.assertIn("점검: 통과", text)
            self.assertIn('--lang "$lang"', text)

    def test_compare_sh_usage_follows_lang(self):
        for lang, word in (("en", "Usage"), ("ko", "사용법")):
            r = subprocess.run(["bash", str(ROOT / "scripts" / "compare.sh"), "--lang", lang], capture_output=True, text=True)
            self.assertEqual(r.returncode, 2)
            self.assertIn(word, r.stderr)


class CheckHtmlEnglishTest(unittest.TestCase):
    def test_english_check_is_defined(self):
        text = (ROOT / "scripts" / "check_html.mjs").read_text()
        self.assertIn("'english-no-hangul'", text)
        self.assertIn("영어 화면에 한글 문구가 남지 않음", text)
        self.assertIn("contentDocument", text)


@unittest.skipUnless(shutil.which("node"), "node 필요")
class CheckHtmlEnglishRunTest(unittest.TestCase):
    def test_english_page_reports_check_and_korean_page_skips(self):
        sys.path.insert(0, str(Path(__file__).parent))
        from test_export_check import chrome
        if not chrome():
            self.skipTest("Chrome 필요")
        for lang in ("ko", "en"):
            d = Path(tempfile.mkdtemp())
            self.addCleanup(shutil.rmtree, d)
            i18n.set_lang(lang)
            try:
                build(snap(None, [table("a", ["x"])]), snap(None, [table("a", ["x", "y"])]), d / "o")
            finally:
                i18n.set_lang(None)
            for s, a in (("timeline.py", [str(d / "o"), "--lang", lang]),
                         ("render_timeline.py", [str(d / "o"), "--out", str(d / "o" / "timeline.html"), "--lang", lang])):
                subprocess.run([sys.executable, str(ROOT / "scripts" / s), *a], check=True, capture_output=True, env=env_for(lang))
            r = subprocess.run(["node", str(ROOT / "scripts" / "check_html.mjs"), str(d / "o" / "timeline.html")],
                               capture_output=True, text=True, timeout=300)
            checks = {c["id"]: c for c in json.loads(r.stdout)["checks"]}
            c = checks["english-no-hangul"]
            if lang == "ko":
                self.assertIsNone(c["ok"])
            else:
                self.assertIsNotNone(c["ok"])


def run_py(script, *args, lang=None):
    env = env_for(lang) if lang else None
    return subprocess.run([sys.executable, str(ROOT / "scripts" / script), *map(str, args)], capture_output=True, text=True, env=env)


class PluralTest(unittest.TestCase):
    def test_helper(self):
        self.assertEqual(i18n.plural(1, "step", "steps"), "1 step")
        self.assertEqual(i18n.plural(0, "step", "steps"), "0 steps")
        self.assertEqual(i18n.plural(2, "step", "steps"), "2 steps")

    def test_timeline_output_singular_and_korean(self):
        with tempfile.TemporaryDirectory() as d:
            i18n.set_lang("en")
            try:
                build(snap(None, [table("a", ["x"])]), snap(None, [table("a", ["x", "y"])]), Path(d) / "o")
            finally:
                i18n.set_lang(None)
            en = run_py("timeline.py", Path(d) / "o", "--lang", "en").stdout
            self.assertRegex(en, r"^1 step, 1 event, \d+ lineages")
            ko = run_py("timeline.py", Path(d) / "o", "--lang", "ko").stdout
            self.assertRegex(ko, r"^단계 1개, 이벤트 1건, 계보 \d+개")

    def test_docker_scripts_use_helper(self):
        for name in ("replay.py", "snapshot.py"):
            self.assertIn("plural(", (ROOT / "scripts" / name).read_text())

    def test_meta_line_plural_in_template(self):
        self.assertIn("${N === 1 ? 'step' : 'steps'} replayed", (ROOT / "templates" / "timeline.html").read_text())


class DetectLangTest(unittest.TestCase):
    def detect(self, path, lang):
        r = run_py("detect.py", path, "--lang", lang)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_no_history_warning_follows_lang(self):
        with tempfile.TemporaryDirectory() as d:
            en, ko = self.detect(d, "en"), self.detect(d, "ko")
        self.assertIn("No migration history found", " ".join(en["warnings"]))
        self.assertIn("찾지 못했습니다", " ".join(ko["warnings"]))
        self.assertFalse(HANGUL.search(" ".join(en["warnings"])))
        self.assertEqual(en["supported"], ko["supported"])
        self.assertEqual(en["steps"], ko["steps"])

    def test_flyway_notes_follow_lang(self):
        with tempfile.TemporaryDirectory() as d:
            m = Path(d) / "db" / "migration"
            m.mkdir(parents=True)
            (m / "V1__a.sql").write_text("CREATE TABLE t (id int);\n")
            (m / "R__v.sql").write_text("CREATE VIEW v AS SELECT 1;\n")
            (m / "V2__b.sql").write_text("ALTER TABLE t ADD c int; -- ${x}\n")
            en, ko = self.detect(d, "en"), self.detect(d, "ko")
        self.assertEqual(len(en["warnings"]), len(ko["warnings"]))
        self.assertTrue(any("--ignore-repeatable" in w and "R__" in w and not HANGUL.search(w) for w in en["warnings"]))
        self.assertTrue(any("placeholders" in w for w in en["warnings"]))
        self.assertTrue(any("R__ 또는 SQL 콜백" in w for w in ko["warnings"]))
        self.assertTrue(any("placeholder" in w and "재생이 실패" in w for w in ko["warnings"]))
        for key in ("format", "steps", "migration_dirs", "placeholders_used"):
            self.assertEqual(en[key], ko[key])

    def test_missing_path_error(self):
        self.assertIn("does not exist", run_py("detect.py", "/nonexistent-dir-xyz", "--lang", "en").stderr)
        self.assertIn("가 없습니다", run_py("detect.py", "/nonexistent-dir-xyz", "--lang", "ko").stderr)

    def test_bad_lang_rejected(self):
        self.assertEqual(run_py("detect.py", ".", "--lang", "fr").returncode, 2)


class SummaryInputLangTest(unittest.TestCase):
    def make(self, d):
        steps = [
            {"index": 1, "version": "1", "label": "V1", "description": "init", "file": "V1__init.sql", "dml": False,
             "status": "applied", "sql": "CREATE TABLE a (id int);", "warnings": ["w1"]},
            {"index": 2, "version": "2", "label": "V2", "description": "noop", "file": "V2__noop.sql", "dml": True,
             "status": "applied", "sql": "SELECT 1;", "data_loss": True},
            {"index": 3, "version": "3", "label": "V3", "description": "bad", "file": "V3__bad.sql", "dml": False,
             "status": "failed", "sql": "x\n" * 50, "error": "boom\nmore"},
            {"index": 4, "version": "4", "label": "V4", "description": "later", "file": "V4__later.sql", "dml": False,
             "status": "not_run", "sql": "SELECT 2;"},
        ]
        events = [
            {"step": 1, "kind": "table_added", "table": "a", "column": None},
            {"step": 1, "kind": "column_renamed", "table": "a", "column": "n", "before": "m", "after": "n", "confidence": "confirmed"},
            {"step": 1, "kind": "column_nullable", "table": "a", "column": "n", "before": True, "after": False},
            {"step": 1, "kind": "column_type", "table": "a", "column": "n", "before": "varchar(20)", "after": "varchar(10)"},
        ]
        (Path(d) / "steps.json").write_text(json.dumps({"source": "demo", "steps": steps}))
        (Path(d) / "events.json").write_text(json.dumps(events))

    def test_english(self):
        with tempfile.TemporaryDirectory() as d:
            self.make(d)
            r = run_py("summary_input.py", d, "--with-sql", "--lang", "en")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        self.assertFalse(HANGUL.search(out), HANGUL.findall(out))
        for word in ("# demo: 4 steps", "Table added `a`", "(confirmed)", "to NOT NULL", "(narrowed)", "[contains DML, may lose data]",
                     "[failed to apply]", "[not applied after an earlier failure]", "Error: boom", "- Warning: w1", "-- ... (truncated)"):
            self.assertIn(word, out)

    def test_korean_markers(self):
        with tempfile.TemporaryDirectory() as d:
            self.make(d)
            out = run_py("summary_input.py", d, "--with-sql", "--lang", "ko").stdout
        for word in ("# demo 단계 4개", "테이블 추가 `a`", "(확정)", "NOT NULL 로", "(크기 축소)", "[DML 포함, 데이터 유실 가능]",
                     "[적용 실패]", "[앞 단계 실패로 적용 안 함]", "오류: boom", "- 경고: w1", "-- ... (생략)"):
            self.assertIn(word, out)

    def test_every_kind_has_both_languages(self):
        from summary_input import KIND
        for ko, en in KIND.values():
            self.assertTrue(HANGUL.search(ko))
            self.assertFalse(HANGUL.search(en))


class ShellLangArgTest(unittest.TestCase):
    def run_sh(self, name, *args, env=None):
        return subprocess.run(["bash", str(ROOT / "scripts" / name), *args], capture_output=True, text=True, env=env or env_for("ko"))

    def test_missing_lang_value_fails_with_usage(self):
        for name in ("timeline.sh", "compare.sh"):
            for args in (("a", "b", "--lang"), ("a", "b", "--lang="), ("a", "--lang", "--db", "postgres")):
                r = self.run_sh(name, *args)
                self.assertEqual(r.returncode, 2, (name, args, r.stderr))
                self.assertIn("사용법", r.stderr)

    def test_invalid_lang_value_fails(self):
        for name in ("timeline.sh", "compare.sh"):
            for arg in (("--lang", "xx"), ("--lang=xx",)):
                r = self.run_sh(name, "a", "b", *arg)
                self.assertEqual(r.returncode, 2)
                self.assertIn("--lang must be one of", r.stderr)

    def test_invalid_env_warns_once_and_falls_back(self):
        env = {**os.environ, "SCHEMA_TIMELINE_LANG": "fr", "LC_ALL": "en_US.UTF-8"}
        r = self.run_sh("compare.sh", env=env)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stderr.count("SCHEMA_TIMELINE_LANG='fr'"), 1)
        self.assertIn("Warning: ignored SCHEMA_TIMELINE_LANG", r.stderr)
        self.assertIn("Usage", r.stderr)
        env["LC_ALL"] = "ko_KR.UTF-8"
        r = self.run_sh("timeline.sh", env=env)
        self.assertIn("경고: 환경 변수 SCHEMA_TIMELINE_LANG='fr'", r.stderr)
        self.assertIn("사용법", r.stderr)

    def test_explicit_lang_suppresses_env_warning(self):
        env = {**os.environ, "SCHEMA_TIMELINE_LANG": "fr"}
        r = self.run_sh("compare.sh", "--lang", "en", env=env)
        self.assertNotIn("SCHEMA_TIMELINE_LANG", r.stderr)

    def test_python_cli_warns_on_invalid_env(self):
        env = {**os.environ, "SCHEMA_TIMELINE_LANG": "fr", "LC_ALL": "en_US.UTF-8"}
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "detect.py"), "/nonexistent-dir-xyz"], capture_output=True, text=True, env=env)
        self.assertIn("Warning: ignored SCHEMA_TIMELINE_LANG='fr'", r.stderr)


if __name__ == "__main__":
    unittest.main()
