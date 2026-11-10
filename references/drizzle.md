# Drizzle Kit 이력 재생

입력: 레포 루트나 drizzle.config 의 `out` 디렉터리(기본 `drizzle/`). `--format drizzle`.
파일: `<out>/meta/_journal.json` 의 `entries` 순서대로 `<tag>.sql` 을 적용한다. journal 에 없는 `.sql` 은 Drizzle 도 적용하지 않으므로
재생하지 않고 경고만 남긴다. journal 의 `dialect` 와 `--db` 가 맞아야 한다(`mysql` 이면 `--db mysql`). 어긋나면 맞는 `--db` 를 알려 주고 멈춘다.
버전 막대의 날짜는 journal 의 `when`(생성 시각, UTC)이다.

## 결과를 전할 때

- `--> statement-breakpoint` 는 SQL 주석이라 그대로 적용된다.
- 레포 루트를 주면 `drizzle/`, `migrations/`, `src/drizzle/`, `db/migrations/`, `src/db/migrations/` 에서 찾는다. 그 밖에 두었으면 out 디렉터리를 입력으로 준다.
- journal 이 없고 `<이름>/migration.sql` 디렉터리로 된 이력이면 디렉터리 이름 순으로 적용한다. 이때 dialect 는 확인하지 못하므로 어느 DB 인지 사용자에게 확인하고 `--db` 로 준다.
- `drizzle-kit push` 로만 바꾼 스키마는 마이그레이션 파일이 없으므로 이력에 없다.
- 이름 변경은 drizzle-kit generate 가 물어볼 때 rename 을 고른 경우에만 `RENAME COLUMN` 으로 남아 "이름 변경(확정)"으로 나온다.
