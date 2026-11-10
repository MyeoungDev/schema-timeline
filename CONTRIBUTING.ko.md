# 기여 안내

[English](CONTRIBUTING.md) · **한국어**

버그 신고, 재생이 안 되는 마이그레이션 이력 제보, 풀 리퀘스트 모두 환영합니다. 보안 문제는 공개 이슈 대신 [SECURITY.md](SECURITY.md) 절차를 따라 주세요.

## 버그 신고

버그 신고 양식을 써 주세요. 가장 도움이 되는 것은 **문제를 다시 일으키는 가장 작은 마이그레이션 이력**과 사용한 엔진·이미지입니다. 운영 SQL 을 그대로 붙이지 마세요. 테이블 이름을 바꾸고 데이터 행은 지운 뒤 올려 주세요.

## 준비

[필요한 것](README.ko.md#필요한-것)의 도구가 필요합니다. 테스트를 모두 돌리려면 Node.js 22 이상, Google Chrome 또는 Chromium 을 설치하고 시험할 엔진 이미지를 받아 두세요.

일부 테스트는 `out/` 아래 재생 결과를 읽습니다. 테스트 전에 한 번 만들어 두세요(CI 와 같은 순서입니다).

```bash
bash scripts/timeline.sh samples/demo out/demo --lang ko
cp docs/demo/summary.ko.json out/demo/summary.json
python3 scripts/render_timeline.py out/demo --out out/demo/timeline.html --lang ko
bash scripts/timeline.sh samples/flyway out/timeline --lang ko
```

Oracle 이미지가 있으면 `bash scripts/timeline.sh samples/oracle_retail out/oracle_retail --db oracle` 도 만들 수 있습니다.

`out/` 은 git 에서 제외됩니다. 커밋하지 마세요.

## 테스트

```bash
python3 -m unittest discover -s tests            # 전체
python3 -m unittest discover -s tests -p test_hints.py -v  # 파일 하나
node scripts/check_html.mjs out/demo/timeline.html  # 만든 페이지를 헤드리스로 점검
```

Docker, Node.js, Chrome, 엔진 이미지가 없으면 해당 테스트는 이유를 남기고 건너뜁니다. CI 는 MySQL·MariaDB·Oracle 이미지나 `out/oracle_retail` 이 필요한 테스트를 뺀 나머지를 돌립니다. 엔진별 코드를 고쳤다면 그 엔진 테스트를 로컬에서 돌리고 풀 리퀘스트에 적어 주세요.

## 규칙

- **두 언어.** 화면·출력에 보이는 문구는 한국어와 영어를 나란히 씁니다. Python 은 `L("한국어", "English")`(`scripts/i18n.py`), 페이지 스크립트는 `L('한국어', 'English')`(`templates/sqlview.js`)입니다. 둘 다 넣어 주세요. `tests/test_i18n.py` 와 `scripts/check_html.mjs` 가 영어 화면에 한글이 남았는지 검사합니다.
- **의존성 추가 없음.** Python 스크립트는 표준 라이브러리만 씁니다. 만든 페이지는 외부 요청이 없어야 합니다. 모두 인라인으로 넣고 CDN 을 쓰지 않습니다.
- **주석은 코드가 하는 일만.** 짧게 씁니다. 판단 근거와 이력은 코드가 아니라 풀 리퀘스트 설명에 적습니다.
- **파일의 언어를 따릅니다.** `SKILL.md`, `references/`, 코드 주석은 한국어입니다. 고치는 파일의 언어로 쓰세요. `README.md` 와 `README.ko.md` 는 함께 고칩니다.

## 자주 하는 변경

| 변경 | 같이 고칠 곳 |
|---|---|
| 위험 규칙, 확인 쿼리, 잠금 힌트 | `templates/risks.js`, `tests/test_hints.py`, `tests/test_risk_judgement.py`. 확인 쿼리를 엔진에서 돌려야 하면 `tests/test_checks_live.py` |
| diff 로직 | `scripts/diff.py` 와 `templates/diff.js` 가 같은 결과를 내야 합니다. `tests/test_diff_parity.py` 가 비교합니다 |
| 새 마이그레이션 도구 | `scripts/sources.py` 의 `FORMATS` 와 `load()`, `scripts/detect.py`, `--format` 목록(`scripts/timeline.sh`, `SKILL.md`, 버그 신고 양식), `samples/` 샘플, `references/` 메모, `tests/test_sources.py`, 두 README 의 입력 표. 기존 도구가 연결된 곳은 `grep -rl drizzle` 로 볼 수 있습니다 |
| 새 엔진 | 새 `scripts/engine_<이름>.py` 와 `scripts/engines.py` 의 `get`/`NAMES`, 그리고 기존 엔진 이름이 나오는 모든 곳: `scripts/replay.py`, `scripts/sources.py`(`engine_of`, `engine_family`), `scripts/detect.py`, `scripts/snapshot.py`, `scripts/timeline.py`, `templates/risks.js`(식별자 인용, 확인 쿼리, 잠금 힌트), `scripts/timeline.sh`·`scripts/compare.sh` 의 `--db` 사용법, `SKILL.md`, 두 README, 버그 신고 양식. `grep -rli mariadb scripts templates tests` 로 목록을 볼 수 있습니다. `references/<이름>.md`, 샘플, 이미지가 없으면 건너뛰는 `tests/test_engines.py`·`tests/test_checks_live.py` 테스트도 추가합니다 |
| 페이지에 보이는 모든 것 | `bash docs/build_demo.sh` 로 README 스크린샷과 GIF 를 다시 만들고(Docker, Node.js, Chrome, Pillow 필요) `docs/images/` 를 커밋 |

## 풀 리퀘스트

- 풀 리퀘스트 하나에 변경 하나만 담아 주세요.
- 변경이 없으면 실패하는 테스트를 추가하거나 고쳐 주세요.
- 풀 리퀘스트 템플릿의 확인 목록을 채워 주세요.
