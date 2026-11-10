# Prisma Migrate 이력 재생

입력: 레포 루트, `prisma/`, `prisma/migrations` 중 아무것이나. `--format prisma`.
파일: `prisma/migrations/<타임스탬프>_<이름>/migration.sql` 을 디렉터리 이름 순으로 적용한다.
`migration_lock.toml` 의 `provider` 와 `--db` 가 맞아야 한다. `mysql` 이면 `--db mysql`(MariaDB 면 `--db mariadb`)로 재생한다.
엔진이 어긋나면 맞는 `--db` 를 알려 주고 멈춘다. sqlite, sqlserver 등 지원하지 않는 provider 도 멈춘다.

## 결과를 전할 때

- Prisma 는 필드 이름 변경을 DROP + ADD 로 만든다. 마이그레이션 파일을 손으로 고쳐 `RENAME COLUMN` 을 쓴 경우에만
  "이름 변경(확정)"으로 나온다. DROP + ADD 로 남은 단계는 "삭제 + 추가"와 데이터 유실 경고로 나오는 것이 실제 동작과 같다.
- 모델 이름이 대소문자를 섞으므로(`"User"`, `"createdAt"`) DDL 에 따옴표가 붙는다.
- enum(`CREATE TYPE "Role" AS ENUM`)은 시점별 전체 DDL 맨 앞에 나온다. 값 추가는 변경 목록에 나오지 않는다.
- `prisma db push` 로만 바꾼 스키마는 마이그레이션 파일이 없으므로 이력에 없다.
- `_prisma_migrations` 테이블은 결과에서 뺀다.
