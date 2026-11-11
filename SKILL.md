---
name: schema-timeline
description: DB 스키마 변경사항을 시각화한다(visualize database schema changes). Flyway, Supabase, Prisma, Drizzle, 번호 붙은 SQL·Liquibase formatted SQL, git 에 쌓인 스키마 파일의 마이그레이션 이력을 임시 DB 컨테이너(PostgreSQL, MySQL, MariaDB, Oracle)에 실제로 재생해 HTML 한 장(외부 요청 0건)으로 낸다. 버전 타임라인, 단계별 스키마 diff·위험 리뷰·관계도(ERD), 테이블·컬럼 계보(옛 이름 포함), 시점별 전체 DDL 을 담고, DDL 파일 두 개 비교도 같은 화면으로 낸다. "마이그레이션 이력 보여줘", "스키마가 언제 어떻게 바뀌었나", "V12 에서 뭐가 바뀌었어", "스키마 diff", "DDL 두 개 비교", "ERD 변경 확인", "show me the migration history", "what changed in the schema between V3 and V7", "schema diff", "schema evolution", "ERD changes over time" 같은 요청에 쓴다. "이 컬럼 언제 생겼어", "when was this column added or renamed" 처럼 테이블·컬럼 하나의 이력을 묻는 짧은 질문에도 쓴다. 파일 grep 으로는 이름 변경 전후를 잇지 못한다. Skill 이름을 말하지 않아도 마이그레이션 디렉터리나 DDL 의 변화를 보고 싶어 하면 쓴다.
---

# schema-timeline

계산(재생, 스냅샷, diff, 계보, 렌더)은 스크립트가 한다. Claude 가 하는 일은 네 가지다.
감지 결과를 사용자와 확인하고, 모호한 값을 묻고, 단계 요약을 쓰고, 실패 원인을 설명한다.
스냅샷은 실제 DB 에 적용한 결과에서만 뜬다. SQL 을 읽고 추론한 스키마를 결과로 쓰지 않는다.

스크립트 경로는 이 SKILL.md 가 있는 디렉터리 기준이다. 아래 명령의 `${CLAUDE_SKILL_DIR}` 는 그 디렉터리다(직접 받은 설치와 플러그인 설치 모두 같다).
필요한 것: docker, python3 3.9 이상. 외부 패키지는 쓰지 않는다. 엔진별 기본 이미지는 아래와 같다. 로컬에 없으면 받아야 하므로
사용자에게 이미지 이름을 알리고 승인을 받은 뒤 받는다(`docker pull`).

| `--db` | 기본 이미지 | 비고 |
|---|---|---|
| `postgres` (기본) | `postgres:16-alpine` | |
| `mysql` | `mysql:8.4` | |
| `mariadb` | `mariadb:11.4` | |
| `oracle` | `gvenzl/oracle-free:23-slim` | 기동에 수십 초. 아래 "지키는 것"의 네트워크 예외 참고 |

엔진별로 재생 방식과 결과를 설명할 때 알아야 할 점은 `references/mysql.md`, `references/oracle.md` 에 있다. 그 엔진이면 재생 전에 읽는다.

## 지키는 것

- 사용자 DB 에는 접속하지 않는다. 재생은 네트워크를 끊은 임시 컨테이너에서만 하고, 끝나면 컨테이너를 지운다.
  Oracle 은 사설 IP 없이는 뜨지 않으므로 외부 경로가 없는 docker 내부 네트워크(`--internal`)를 재생마다 만들고 끝나면 지운다.
  `detect.py` 는 프로젝트 설정(`flyway.conf`, `application*.yml` 등)의 접속 URL 에서 DB 종류와 스키마 이름만 뽑는다.
  URL·계정·비밀번호는 결과에 넣지도, 다른 곳에 넘기지도 않는다.
- 사용자 레포의 파일은 읽기만 한다. 결과는 레포 밖(기본 `~/.cache/schema-timeline/<레포 이름>/`)에 둔다.
  사용자가 위치를 정하면 그곳에 둔다.
- SQL 과 카탈로그 텍스트의 비밀번호는 스크립트가 가린다. 요약을 쓸 때도 비밀번호·접속 정보를 옮겨 적지 않는다.

## 출력 언어

화면·터미널 출력은 `--lang ko|en` 으로 정한다. Claude 는 사용자가 쓰는 언어를 보고 고른다(한국어면 `ko`, 그 밖이면 `en`).
`detect.py`·`summary_input.py`·`timeline.sh`·`compare.sh`·`render_timeline.py` 에 같은 값을 넘기고, `summary.json` 의 단계 요약도 그 언어로 쓴다.
`--lang` 이 없으면 환경 변수 `SCHEMA_TIMELINE_LANG`, 그다음 로케일(`LC_ALL`·`LC_MESSAGES`·`LANG` 이 `ko` 로 시작하면 `ko`), 모두 없으면 `en` 이다.
`timeline.sh`·`compare.sh` 는 정한 언어를 하위 스크립트에 모두 넘긴다. 따로 돌리는 `replay.py`·`timeline.py`·`render_timeline.py`·`snapshot.py` 에도 같은 `--lang` 을 준다.
`--lang` 값은 `ko`·`en` 만 받는다. 값이 없거나 그 밖의 값이면 셸 스크립트는 사용법을 내고 종료 코드 2 로 끝난다. 환경 변수 값이 잘못됐으면 무시하고 로케일로 넘어가며 경고 한 줄을 낸다.
DB 가 내는 오류 문구와 스키마 주석·SQL 은 언어와 무관하게 원문 그대로다.

## 이력 타임라인

### 1. 감지

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/detect.py <레포 또는 마이그레이션 디렉터리> --lang ko|en
```

JSON 의 `format`, `migration_dirs`(Flyway), `tool_dirs`(Supabase·Prisma·Drizzle), `sql_file_dirs`(번호 붙은 SQL), `db_type`,
`schema_candidates`, `steps`, `warnings`, `notes`, `suggested_images`, `placeholders_used`, `templates_used` 를 본다.
`warnings` 에는 재생하지 않고 넘어가는 파일(Flyway undo `U<버전>__*.sql`, 소문자 `v2__*.sql`, `.SQL` 확장자, `V<버전>__<설명>.sql` 형식이 아닌 후보)이,
`notes` 에는 정수 버전의 공백(`V4 이(가) V3 와 V5 사이에 없습니다`)과 타임스탬프 자릿수 혼용이 나온다. 재생 결과 `steps.json` 에도 같은 `warnings`·`notes` 가 들어간다. 이력 형식에 따라 재생 방식(`--format`)을 고른다.
Supabase·Prisma·Drizzle 이면 재생 전에 `references/<형식>.md` 를 읽는다. 그 도구에서 재생이 실패하는 흔한 경우와 설명할 점이 있다.

| 이력 모양 | `--format` | 입력 |
|---|---|---|
| `V<버전>__<설명>.sql` (Flyway) | `flyway` | 마이그레이션 디렉터리 |
| `<번호·타임스탬프>__<설명>.sql`, 앞에 `init` 파일·디렉터리. Liquibase formatted SQL 을 Liquibase 없이 파일 단위로 적용하는 이력도 여기다. `.up.sql`/`.down.sql` 짝은 `.up.sql` 만 재생한다 | `sqlfiles` | 그 디렉터리 |
| `supabase/migrations/<타임스탬프>_<이름>.sql` (Supabase CLI) | `supabase` | 레포 루트 또는 그 디렉터리 |
| `prisma/migrations/<타임스탬프>_<이름>/migration.sql` (Prisma Migrate) | `prisma` | 레포 루트 또는 그 디렉터리 |
| `<out>/NNNN_<이름>.sql` + `<out>/meta/_journal.json` (Drizzle Kit) | `drizzle` | 레포 루트 또는 out 디렉터리 |
| 전체 스키마 파일 하나(`init.sql`, `schema.sql`)를 계속 고쳐 온 이력 | `git-snapshots` + `--file <레포 기준 경로>` | 레포 루트 |

`--format` 을 빼면 replay.py 가 고른다. `--file` 이 있으면 git-snapshots, 아니면 입력 아래 Flyway·Prisma·Drizzle·Supabase 중 한 형식,
없으면 번호 붙은 SQL 디렉터리 한 곳(seed·test·node_modules·build 제외), 아무것도 없으면 flyway 다. 형식이 섞였거나 후보 디렉터리가 여러 곳이면 목록과 함께 멈춘다.
감지 결과에 이력이 여러 개면 자동 선택에 맡기지 말고 `--format` 과 해당 디렉터리를 준다. Supabase·Prisma·Drizzle 은 레포 루트를 줘도 아래 디렉터리에서 찾는다.

`sqlfiles` 로 재생했는데 init 파일이 이미 최신 전체 스키마라서 뒤 파일과 겹치거나(이미 있는 컬럼 추가 오류),
init 파일 자체가 빈 DB 에 적용되지 않으면, 그 init 파일의 git 이력을 `git-snapshots` 로 보는 편이 실제 이력에 맞다.
Liquibase changelog(`changelog-master.xml`), Alembic·Rails·Django 처럼 마이그레이션이 코드인 이력은 아직 재생하지 않는다.
이런 레포에 덤프한 전체 스키마 파일(`structure.sql`, `schema.sql`)이 git 에 있으면 `git-snapshots` 로 볼 수 있다.

### 2. 사용자와 확인

다음 중 해당하는 것만 한 번에 묻는다(AskUserQuestion). 해당하는 게 없으면 묻지 않고 진행한다.

- `supported` 가 false → 재생할 수 없는 이유(지원하지 않는 DB, 이력 없음)를 알리고 멈춘다.
- `db` 가 재생할 엔진이다(`--db`). null 이면 어느 DB 인지 묻는다. MySQL 설정인데 compose 이미지가 MariaDB 처럼 둘이 어긋나면 묻는다.
- 그 엔진의 이미지가 로컬에 없음(`docker images`) → 받아도 되는지 묻는다.
- `schema_candidates` 에 public 이 아닌 스키마가 하나 있음 → 그 스키마로 재생한다(`--schema`). 여러 개면 묻는다.
  재생 후 지정한 스키마에 테이블이 없다는 안내(한국어 `<스키마> 스키마에 테이블이 없습니다`, 영어 `schema <스키마> has no tables`)가 나오면 안내된 스키마로 다시 돌린다.
- 이력이 여러 곳(디렉터리 여러 개, Flyway 와 Prisma 가 함께 있음 등) → 어느 것을 재생할지.
- R__·SQL 콜백 있음 → 빼고 재생할지(`--ignore-repeatable`). 빼면 그 파일이 만드는 뷰·함수는 결과에 없다.
- `suggested_images` 에 DB 이미지 버전이 있음 → 그 이미지로 재생할지(`--image`). 운영과 버전이 같아야 카탈로그 표기가 같다.
- `placeholders_used`(Flyway `${...}`) → 설정 파일(`flyway.conf`, `application.yml` 의 `placeholders`)에서 값을 찾아 `--placeholder 이름=값` 으로 준다. 찾지 못하면 사용자에게 묻는다.
- `templates_used`(`{{ ... }}` 등) → 그 도구가 넣는 값(예: GoTrue 의 Namespace 는 스키마 이름)을 사용자에게 확인하고 `--replace '원문=값'` 으로 준다.
- Java 마이그레이션 → 그 단계가 빠진다고 알린다.

### 3. 재생과 렌더

```bash
bash ${CLAUDE_SKILL_DIR}/scripts/timeline.sh <입력> <출력 디렉터리> [--lang ko|en] [--db postgres|mysql|mariadb|oracle]
                                 [--format flyway|supabase|prisma|drizzle|sqlfiles|git-snapshots] [--file <경로>]
                                 [--schema <스키마>] [--image <이미지>] [--ignore-repeatable]
                                 [--placeholder <이름>=<값>] [--replace '<원문>=<값>'] [--step-timeout <초>] [--no-sql]
```

Flyway baseline(`B<버전>__*.sql`)이 있으면 가장 높은 B 를 먼저 적용하고 그 버전 이하 V 는 건너뛴다(경고에 목록).

`replay.py`(재생·스냅샷) → `timeline.py`(단계 diff·이벤트·계보) → `render_timeline.py`(HTML) 를 차례로 돈다.
형식마다 단계 목록을 만드는 일은 `sources.py` 의 어댑터가, DB 마다 다른 기동·적용·카탈로그는 `engines.py`·`engine_*.py` 가 한다.
`--schema` 는 PostgreSQL 에선 스키마(기본 public), MySQL·MariaDB 에선 데이터베이스(기본 replay), Oracle 에선 사용자(기본 REPLAY)다.
단계 수에 비례해 오래 걸리므로(단계당 1초 안팎) 백그라운드로 돌려도 된다.
종료 코드 1 은 중간 단계 실패다. 그 전까지의 HTML 은 만들어진다. 종료 코드 3 은 재생은 성공했지만 결과 점검(6절)에 실패 항목이 있다는 뜻이다.

재생·비교용 컨테이너(PostgreSQL·MySQL·MariaDB·Oracle, `snapshot.py` 의 두 DDL 비교 포함)는 네트워크 없이 자원 제한을 걸고 띄운다.
기본은 메모리 1g(Oracle 3g), CPU 2개, 프로세스 512개(Oracle 2048개)다. 환경 변수로 바꾼다.

| 환경 변수 | 뜻 | 기본 |
|---|---|---|
| `SCHEMA_TIMELINE_MEMORY` | `docker run --memory`(스왑 포함 같은 값) | 1g, Oracle 3g |
| `SCHEMA_TIMELINE_CPUS` | `--cpus` | 2 |
| `SCHEMA_TIMELINE_PIDS` | `--pids-limit` | 512, Oracle 2048 |
| `SCHEMA_TIMELINE_STEP_TIMEOUT` | 단계(DDL 파일) 하나를 적용하는 제한 시간(초). `--step-timeout` 이 우선. 0 은 제한 없음 | 300 |
| `SCHEMA_TIMELINE_RUN` | 컨테이너에 붙는 실행 id(라벨 `schema-timeline.run`). 정하지 않으면 실행마다 무작위 | 무작위 |

단계가 제한 시간을 넘기면 그 단계를 실패로 표시하고 재생을 멈춘다(`단계 V7 이(가) 300초 안에 끝나지 않았습니다(재생 중단). 오래 걸리는 문장이나 락 대기가 아닌지 확인하세요.`).
그 전까지의 HTML 은 만들어진다. 컨테이너 시작 대기는 PostgreSQL 60초, MySQL·MariaDB 120초, Oracle 약 6분이다.

컨테이너와 Oracle 의 내부 네트워크에는 라벨 `schema-timeline=1` 과 `schema-timeline.run=<실행 id>` 가 붙는다.
정상 종료, 실패, Ctrl+C, SIGTERM·SIGHUP 모두 컨테이너와 네트워크를 지운다. SIGKILL 이나 docker 중단으로 남았으면 다음으로 지운다.

```bash
docker ps -a --filter label=schema-timeline            # 남은 것 확인
python3 ${CLAUDE_SKILL_DIR}/scripts/replay.py --cleanup             # 라벨이 붙은 컨테이너·네트워크(라벨 없는 옛 schema-timeline-* 네트워크 포함)를 모두 지운다
```

`--cleanup` 은 다른 곳에서 돌고 있는 재생의 컨테이너도 지우므로 재생이 돌지 않을 때 쓴다.

`--no-sql`(`timeline.sh`·`compare.sh`, 또는 `render_timeline.py`)은 HTML 에 SQL 원문을 넣지 않는다. 단계의 "SQL 원문" 칸은 비고(`SQL 원문을 생략했습니다(--no-sql)`),
⌘K 의 SQL 검색은 결과가 없다. 실패 단계의 오류 문구는 첫 줄만 남기고 작은따옴표 안 값을 `'…'` 로 가린다(DB 오류에 데이터 값이 들어 있을 수 있다).
시점별 전체 DDL·구조 diff 는 카탈로그에서 만든 것이라 그대로 담긴다. `<출력>/steps.json` 과 `snapshots/` 는 로컬 파일이라 원문이 남아 있으니, 출력 디렉터리째 공유하지 않는다.
`summary_input.py --with-sql` 도 steps.json 의 원문을 읽는다. 사용자가 SQL 을 공유하지 않겠다고 했으면 5절의 `render_timeline.py` 재렌더에도 `--no-sql` 을 준다.

### 4. 실패했다면 원인 설명

`<출력>/steps.json` 에서 `status: "failed"` 인 단계의 `error` 를 읽고 원인을 한두 문장으로 설명한다. 흔한 원인:

| 오류 | 원인과 대응 |
|---|---|
| `relation ... does not exist` 가 첫 단계부터 | baseline 이후 이력만 있다. 운영 스키마 덤프(구조만)를 V0 으로 받아야 한다 |
| `function/view ... does not exist` | R__ 를 빼고 재생해서 그 객체가 없다 |
| `syntax error at or near "$"`·`"{"` | 자리표시자가 치환되지 않았다. 오류 끝 `조치:` 대로 `--placeholder`·`--replace` 로 다시 재생 |
| `extension ... is not available`, `type "vector" does not exist` | 이미지에 없는 확장. 오류 끝 `조치:` 의 이미지(pgvector·PostGIS·TimescaleDB)를 받아도 되는지 사용자에게 묻고 `--image` 로 재생 |
| `cannot run inside a transaction block` | 트랜잭션 밖에서만 되는 문장. 판정 누락이므로 사용자에게 알린다 |

role 이 없어서 나는 오류는 스크립트가 role 을 만들고 다시 재생하므로 보통 보이지 않는다.

### 5. 단계 요약 쓰기

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/summary_input.py <출력 디렉터리> --lang ko|en            # 이벤트만
python3 ${CLAUDE_SKILL_DIR}/scripts/summary_input.py <출력 디렉터리> --lang ko|en --with-sql # SQL 원문도
```

기본은 이벤트만 읽는다. 이벤트만으로 의도를 알 수 없는 단계(DML 단계, 구조 변경이 없는 단계)가 있을 때만
`--with-sql` 로 SQL 을 본다. 사용자가 SQL 을 Claude 에 넘기지 말라고 했으면 이벤트만 쓴다.

`<출력>/summary.json` 에 `{"<파일 이름>": "요약"}` 으로 쓴다. 키는 출력에 나온 파일 이름 그대로다.

- 사용자가 쓰는 언어(`--lang` 과 같은 언어)로 쓴다.
- 한두 문장. 무엇이 바뀌었고, 데이터에 어떤 영향이 있는지를 쓴다. 예: "order_items 를 order_lines 로 바꾸고 orders.memo 를 지웁니다. memo 값은 사라집니다."
- 이름 변경은 이벤트의 확정/의심을 따른다. 의심을 확정처럼 쓰지 않는다.
- 이벤트에 없는 의도·배경은 지어내지 않는다. 구조 변경이 없는 단계는 SQL 로 한 일을 짧게 쓴다(예: 권한 부여, 데이터 보정).
- 단계가 많으면(50개 이상) 이벤트가 없는 단계는 비워 두어도 된다.

요약을 쓴 뒤 HTML 만 다시 만든다.

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/render_timeline.py <출력 디렉터리> --out <출력 디렉터리>/timeline.html --lang ko|en [--no-sql]
```

### 6. 결과 점검

`timeline.sh`·`compare.sh` 는 끝에 결과 HTML 을 헤드리스 Chrome 으로 열어 점검하고 `<출력>/check.json`(영수증)과
`<출력>/*.check.png`(1280px 화면)를 남긴다. 요약을 넣고 HTML 만 다시 만들었다면 직접 돌린다.

```bash
node ${CLAUDE_SKILL_DIR}/scripts/check_html.mjs <출력>/timeline.html
```

점검 항목: 스크립트·콘솔 오류, 외부 요청, 핵심 영역(결론·블록·관계도, 또는 모든 단계와 전체 구간의 변경 화면),
1280px·375px 가로 넘침, 관계도 확대·축소, 전체 검색(⌘K)으로 테이블을 찾아 계보로 이동, 관계도 SVG·PNG 내보내기, Mermaid ERD 파싱(로컬에 mermaid 가 있을 때), 영어 화면(`lang` 이 `en`)에 데이터 밖의 한글 문구가 남지 않았는지.
`check.json` 의 `ok` 가 false 면 실패 항목을 고치거나, 고칠 수 없으면 그대로 사용자에게 알린다. 실패를 통과처럼 전하지 않는다.
스크린샷을 Read 로 한 번 열어 화면이 비거나 깨지지 않았는지 눈으로도 확인한다.
`check_html.mjs` 의 종료 코드: 0 통과, 1 실패 항목 있음(페이지가 제한 시간 안에 끝나지 않거나 렌더러가 죽은 경우 포함),
2 점검하지 못함(Chrome·node 없음, Chrome 이 뜨지 않음). 2 는 점검했다고 말하지 않는다.
래퍼는 점검 실패면 종료 코드 3 으로 끝나고, 점검하지 못했으면 0 으로 끝나되 "점검: 하지 못함" 을 출력한다.
`ok: null` 인 항목은 건너뛴 것이다(예: mermaid 번들이 없음). 건너뛴 것도 그대로 알린다.

### 7. 전달

HTML 경로를 알려 주고(`open <경로>` 로 열어도 된다), 아래를 짧게 덧붙인다.
몇 단계 중 몇 단계를 재생했는지, 데이터 유실 가능 단계, 확정 이름 변경 수, 경고(빠진 R__, placeholder 등), 점검 결과.

## 내보내기

- 화면: 관계도 툴바의 "내보내기"에서 Mermaid ERD 복사·`.mmd` 저장, 관계도 그림 SVG·PNG 저장. 지금 보이는 범위(변경 주변/전체)와
  보기(변경 전/합쳐 보기/변경 후) 그대로 내보낸다. 타임라인에서는 단계·구간 화면 안의 같은 메뉴를 쓴다.
- 명령: 위키·PR 에 붙일 ERD 를 달라는 요청에는 파일로 만들어 준다.

```bash
node ${CLAUDE_SKILL_DIR}/scripts/export_erd.mjs <출력 디렉터리> [--step N] [--tables a,b] [--out erd.mmd]   # N 단계 적용 후(DDL 비교는 1)
node ${CLAUDE_SKILL_DIR}/scripts/export_erd.mjs <스냅샷 JSON> [--out erd.mmd]                               # 스냅샷 하나
```

기본 출력은 Mermaid 10.5 이상용이다(엔티티 별칭 `id["원래 이름"]` 을 쓴다). 붙여 넣을 곳(사내 위키 등)의 Mermaid 가
그보다 오래됐거나 버전을 모르면 `--compat legacy` 를 붙인다. 별칭 없이 영문·숫자·`_` 이름만 쓰고, 바꾼 테이블 이름은
`%%` 주석에 원래 이름을 남긴다. 화면의 내보내기 메뉴는 기본 출력만 만든다.

Mermaid 는 타입에 쉼표가 들어가면(`numeric(14,2)`) 타입을 `numeric` 으로 줄이고 전체 타입을 설명에 적는다.
Mermaid 식별자로 쓸 수 없는 이름(한글, 공백, `style`·`class` 같은 문법 낱말, 컬럼 `PK`·`FK`·`UK`)은 `e_`·`_` 를 붙여 바꾸고
원래 이름을 별칭·설명에 남긴다.

## 검토 리포트·배포 전 확인 쿼리

"V12 배포 전에 운영에서 뭘 확인해야 해?", "이 단계 잠금 걸려?", "PR 댓글로 검토 결과 줘" 같은 요청에는 화면 대신 리포트를 낸다.
화면의 주요 변경사항·배포 전 확인 쿼리·잠금 힌트와 같은 규칙(`templates/risks.js`)이다.

```bash
node ${CLAUDE_SKILL_DIR}/scripts/report.mjs <출력 디렉터리> --step N --format md --lang ko|en --fail-on none   # N 단계
node ${CLAUDE_SKILL_DIR}/scripts/report.mjs <출력 디렉터리> --from A --to B --format md --lang ko|en           # 구간(잠금 힌트 없음)
```

- 확인 쿼리는 읽기 전용 SELECT 다. 사용자에게 운영 DB에서 직접 돌려 보라고 건네기만 하고, 이 스킬이 운영 DB 에 접속하거나 쿼리를 대신 실행하지 않는다.
- 잠금 힌트는 엔진의 일반 동작이다. 실제 잠금 시간은 버전·설정·테이블 크기에 따라 다르다고 함께 말한다.
- `--format json` 이면 위험마다 `check`(`sql`, `expect`), 단계마다 `checks`(묶은 스크립트)·`hints` 가 들어간다.

## DDL 두 개 비교

마이그레이션 이력이 아니라 DDL 파일 두 개(변경 전·후 스키마 덤프 등)를 비교할 때 쓴다.

```bash
bash ${CLAUDE_SKILL_DIR}/scripts/compare.sh before.sql after.sql <출력 디렉터리> [--lang ko|en] [--db postgres|mysql|mariadb|oracle] [--image <이미지>] [--schema <스키마>]
                         [--step-timeout <초>] [--no-sql]
```

결과는 이력 타임라인과 같은 `<출력>/timeline.html` 이다. 변경 전 DDL 을 시작점, 변경 후 DDL 을 한 단계로 둔 1단계 타임라인이고,
화면 제목은 "스키마 비교", 버전 막대는 숨긴다. 변경 리뷰·관계도·내보내기·전체 DDL·계보·검색을 그대로 쓴다.
내부 순서는 `snapshot.py`(두 DDL 스냅샷, `<출력>/ddl/`) → `compare_timeline.py`(1단계 타임라인 입력) → `timeline.py` → `render_timeline.py`.
두 파일을 각각 빈 DB 에 적용하므로 이름 변경은 "의심"으로만 나온다.
의심도 시그니처(타입·NULL 허용)가 양쪽에 하나씩일 때만 잡히므로, 이름과 함께 다른 것도 바뀌었으면 삭제+추가로 나온다.
결과를 전할 때 이 점을 넘어 이름 변경을 단정하지 않는다.
이 점을 결과와 함께 알린다. 확정이 필요하면 이력 재생을 권한다.

## 결과 화면

- 타임라인(`timeline.html`): 버전 막대(높이는 변경 건수, 빨강은 데이터 유실), 구간 비교(Shift+클릭·Shift+화살표 또는
  상단 선택 상자로 두 버전을 골라 그 사이 변경을 한 번에 본다. 브라우저에서 계산하며 결과는 diff.py 와 같다), 그 시점 테이블 목록,
  전 버전 이름 검색(옛 이름 포함), 계보, 단계 요약·SQL 원문, 단계 변경 리뷰, "이 시점 전체 DDL"(복사·.sql 저장).
- 전체 검색(⌘K / Ctrl+K, 상단 "전체 검색" 버튼, 변경 리뷰 안에서도 열린다): 테이블·컬럼(옛 이름, `테이블.컬럼` 포함), 버전(번호·설명·파일·요약),
  SQL 원문(고르면 그 단계의 원문을 펼치고 해당 줄을 표시), 명령(보기 전환·전체 구간·첫/마지막 버전·테마·글자 크기)을 한 곳에서 찾는다.
  빈 검색어에서는 명령과 데이터 유실 가능 버전을 보여 준다.
- 변경 리뷰(단계·구간을 고르면 타임라인 안에 뜬다. `templates/compare.html` 템플릿): 요약 띠(주요 변경사항 건수, 종류별 변경 건수와 구성 막대), 주요 변경사항 목록(데이터 유실·확인 필요), 배포 전 확인 쿼리(운영 DB에서 돌려 볼 읽기 전용 SELECT 묶음, 복사), 잠금 힌트(엔진별로 테이블을 잠그거나 다시 쓰는 변경, 위험 건수에는 안 셈),
  관계도(변경 주변/전체, 테이블 찾기, 확대·축소·끌어서 이동·두 손가락 확대·전체 화면),
  테이블별 구조 diff ↔ 전체 SQL(블록마다 또는 한꺼번에 전환. CREATE 문 전체를 접지 않고 보여 주며 추가·삭제된 줄에 색을 입힌다), 리뷰 요약 Markdown 복사.
  테이블째 지우는 경우는 "테이블 삭제" 머리말과 안내 띠, 취소선 줄로 표시해 컬럼 단위 삭제(빨간 − 줄)와 구분한다.
  주요 변경사항 목록도 "테이블 전체 삭제"를 맨 앞에, "컬럼 삭제"와 다른 표시로 둔다.
- 주소 끝 `#step=5&view=ddl`, `#from=5&to=20`(구간), `#tb-orders` 로 특정 단계·구간·테이블을 바로 연다.
  "V5 부터 V20 사이에 무엇이 바뀌었나" 같은 요청에는 이 주소를 만들어 알려 준다.

## 한계

- DB 는 PostgreSQL, MySQL, MariaDB, Oracle. SQL Server 등은 아직 없다. Prisma·Drizzle 은 provider/dialect 가 이 중 하나일 때, Supabase 는 PostgreSQL 로만 재생한다.
- 이름 변경 판정: PostgreSQL 은 카탈로그의 oid·attnum 으로, MySQL·MariaDB·Oracle 은 단계 SQL 의 RENAME·CHANGE 문으로 확정한다.
  동적 SQL(EXECUTE IMMEDIATE, PREPARE) 안의 이름 변경은 못 읽어 삭제+추가로 나온다.
- MySQL·MariaDB·Oracle 의 DDL 은 트랜잭션으로 묶이지 않는다. 실패한 단계는 실패한 문장 앞까지 적용된 상태다(결과에는 그 단계의 스냅샷이 없다).
- Liquibase CLI 재생, 코드로 된 마이그레이션(Java, Alembic 등), R__·SQL 콜백은 아직 없다. placeholder 는 `--placeholder` 로 준 값만 치환한다.
- Supabase 는 플랫폼 기본 스키마(auth·storage·역할)를 최소 대역으로 흉내 낸다. 실제 플랫폼과 다를 수 있다.
- `git-snapshots` 는 리비전마다 DB 를 새로 만들므로 이름 변경이 "의심"으로만 나온다. 실패한 리비전 뒤는 재생하지 않는다.
- 스냅샷 대상은 스키마 하나(`--schema`)의 테이블·컬럼·제약·인덱스·뷰·코멘트. PostgreSQL 은 enum 타입도 담는데, enum 값 변경은
  변경 목록에 나오지 않고 시점별 전체 DDL 에만 보인다(MySQL 의 enum 은 컬럼 타입이라 타입 변경으로 나온다).
  함수·프로시저·트리거·패키지·시퀀스·시노님·구체화 뷰·이벤트는 변경 목록이나 전체 DDL 에는 없고, 단계마다 `objects`(추가·삭제·정의 변경·INVALID)로 이름만 나온다.
  본문 diff 와 시퀀스 옵션·domain·RLS 정책은 어느 엔진에서도 없다. MySQL·MariaDB 시퀀스·Oracle 의 일부 객체(JOB 등)는 변경 여부를 판정하지 않는다.
- 다른 스키마의 테이블을 가리키는 FK(예: Supabase 의 `auth.users`)는 `auth.users` 처럼 스키마를 붙여 보이고, 관계도에는 그리지 않는다.
- 재생은 PostgreSQL 에선 각 파일을 psql 트랜잭션 하나로, MySQL·MariaDB 는 mysql 클라이언트로, Oracle 은 sqlplus 로 적용한다.
  Flyway CLI 와 동작이 다를 수 있는 기능(설정 파일의 placeholder 값 자동 적용, 스크립트 설정 파일)은 반영하지 않는다.
- 시점별 DDL 은 원본과 글자 단위로 같지 않다. PostgreSQL 은 카탈로그에서 다시 만들고, MySQL·MariaDB 는 SHOW CREATE TABLE,
  Oracle 은 DBMS_METADATA 결과에 인덱스·코멘트 문을 붙인 것이다.

## 검증

```bash
cd ${CLAUDE_SKILL_DIR} && python3 -m unittest discover -s tests
```

docker 가 있으면 재생과 DDL 왕복 테스트까지 돈다. MySQL·MariaDB·Oracle 재생 테스트는 그 이미지가 로컬에 있을 때만 돈다.
예제 이력: `samples/demo`(PostgreSQL, README 화면), `samples/flyway`(PostgreSQL), `samples/mysql`(MySQL·MariaDB), `samples/oracle`, `samples/supabase`, `samples/prisma`, `samples/drizzle`.
