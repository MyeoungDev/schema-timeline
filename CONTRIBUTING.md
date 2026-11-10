# Contributing

**English** · [한국어](CONTRIBUTING.ko.md)

Bug reports, sample histories that fail to replay, and pull requests are welcome. For a security problem, follow [SECURITY.md](SECURITY.md) instead of opening a public issue.

## Reporting a bug

Use the bug report form. The most useful thing you can attach is the **smallest migration history that reproduces the problem**, with the engine and image you used. Do not paste production SQL as is: rename tables and drop seed rows first.

## Setting up

You need the tools listed under [Requirements](README.md#requirements). For the full test suite also install Node.js 22+, Google Chrome or Chromium, and pull the engine images you want to test.

Some tests read replay outputs under `out/`. Build them once before running the suite (the same steps CI runs):

```bash
bash scripts/timeline.sh samples/demo out/demo --lang ko
cp docs/demo/summary.ko.json out/demo/summary.json
python3 scripts/render_timeline.py out/demo --out out/demo/timeline.html --lang ko
bash scripts/timeline.sh samples/flyway out/timeline --lang ko
```

Optional, with the Oracle image: `bash scripts/timeline.sh samples/oracle_retail out/oracle_retail --db oracle`.

`out/` is ignored by git. Never commit it.

## Running the tests

```bash
python3 -m unittest discover -s tests            # whole suite
python3 -m unittest discover -s tests -p test_hints.py -v  # one file
node scripts/check_html.mjs out/demo/timeline.html  # headless self-check of a generated page
```

A test skips itself, with the reason, when Docker, Node.js, Chrome or an engine image is missing. CI runs everything except the tests that need the MySQL, MariaDB or Oracle image or `out/oracle_retail`. If you change engine-specific code, run that engine's tests locally and say so in the pull request.

## Conventions

- **Two languages.** Every user-facing string has a Korean and an English text side by side: `L("한국어", "English")` in Python (`scripts/i18n.py`) and `L('한국어', 'English')` in the page scripts (`templates/sqlview.js`). Add both. `tests/test_i18n.py` and `scripts/check_html.mjs` check that the English page has no Korean text left.
- **No new dependencies.** Python scripts use the standard library only. The generated page must make no external requests: inline everything, load no CDN.
- **Comments say what the code does.** Keep them short. Put the reasoning and history in the pull request description, not in the code.
- **Keep the file's language.** `SKILL.md`, `references/` and the code comments are written in Korean. Write new text in the language of the file you edit. Update `README.md` and `README.ko.md` together.

## Common changes

| Change | Also touch |
|---|---|
| Risk rule, check query or locking hint | `templates/risks.js`, `tests/test_hints.py`, `tests/test_risk_judgement.py`; `tests/test_checks_live.py` if the check query runs on an engine |
| Diff logic | `scripts/diff.py` and `templates/diff.js` must agree; `tests/test_diff_parity.py` compares them |
| New migration tool | `FORMATS` and `load()` in `scripts/sources.py`, `scripts/detect.py`, the `--format` lists (`scripts/timeline.sh`, `SKILL.md`, the bug report form), a sample under `samples/`, a note under `references/`, `tests/test_sources.py`, the inputs table in both READMEs. `grep -rl drizzle` shows where an existing tool is wired |
| New engine | A new `scripts/engine_<name>.py` and `get`/`NAMES` in `scripts/engines.py`, then every place an existing engine is named: `scripts/replay.py`, `scripts/sources.py` (`engine_of`, `engine_family`), `scripts/detect.py`, `scripts/snapshot.py`, `scripts/timeline.py`, `templates/risks.js` (quoting, check queries, locking hints), the `--db` usage in `scripts/timeline.sh` and `scripts/compare.sh`, `SKILL.md`, both READMEs, the bug report form. `grep -rli mariadb scripts templates tests` lists them. Add `references/<name>.md`, a sample, and tests in `tests/test_engines.py` and `tests/test_checks_live.py` that skip without the image |
| Anything visible on the page | Rebuild the README screenshots and GIF with `bash docs/build_demo.sh` (needs Docker, Node.js, Chrome and Pillow) and commit `docs/images/` |

## Pull requests

- Keep one change per pull request.
- Add or update a test that fails without your change.
- Fill in the checklist in the pull request template.
