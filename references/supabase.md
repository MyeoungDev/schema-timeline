# Supabase 이력 재생

입력: 레포 루트, `supabase/`, `supabase/migrations` 중 아무것이나. `--format supabase`.
파일: `supabase/migrations/<타임스탬프>_<이름>.sql` 을 타임스탬프 순으로 적용한다. `seed.sql` 은 데이터라 적용하지 않는다.

## 재생 전에 하는 일

빈 PostgreSQL 에는 Supabase 플랫폼이 미리 만들어 두는 객체가 없으므로, 첫 단계 전에 `scripts/preludes/supabase.sql` 을 적용한다.

- 역할: `anon`, `authenticated`, `service_role` 등
- 스키마: `auth`, `storage`, `extensions`. DB 의 search_path 에 `extensions` 를 넣는다
- `auth.users`(주요 컬럼만), `auth.uid()`·`auth.role()`·`auth.jwt()`·`auth.email()`
- `storage.buckets`, `storage.objects`, `storage.foldername()`·`filename()`·`extension()`
- 확장 `uuid-ossp`, `pgcrypto`(`extensions` 스키마), publication `supabase_realtime`

이미지에 없는 확장(`pg_graphql`, `pgsodium`, `supabase_vault`, `pg_net`, `pg_cron` 등)의 CREATE/COMMENT/ALTER/DROP EXTENSION 문은
건너뛰고 그 단계에 경고를 남긴다. 화면의 SQL 원문은 건너뛰기 전 그대로다.

## 결과를 전할 때

- 스냅샷은 `public` 만 뜬다. `auth`·`storage` 의 대역 테이블은 결과에 나오지 않는다.
- `profiles.id → auth.users` 같은 FK 는 `auth.users` 로 보이고 관계도에는 그리지 않는다.
- 트리거·함수는 단계의 `objects` 에 이름만 나온다(`public` 안의 것만). RLS 정책, realtime publication 은 재생은 되지만 결과에 나오지 않는다.
- 대역은 이름과 시그니처만 맞춘 것이다. 결과가 실제 프로젝트와 다를 수 있다고 함께 알린다.

## 흔한 실패

| 오류 | 원인과 대응 |
|---|---|
| `relation "auth.xxx" does not exist`, `function vault.xxx` 등 | 대역에 없는 플랫폼 객체를 쓴다. 그 단계까지만 결과가 나온다고 알린다 |
| `schema "graphql" does not exist` 등 건너뛴 확장의 스키마·함수 참조 | 건너뛴 확장에 기대는 문장. 위와 같다 |
| `schema "supabase_functions" does not exist`, `vault`·`net`·`cron` 스키마 참조 | 데이터베이스 웹훅·Vault·pg_net·pg_cron 은 대역에 없다. 그 단계까지만 결과가 나온다고 알린다 |

이력이 참조하지만 만들지 않은 역할(대역에 없는 것)은 재생 전에 NOLOGIN 역할로 자동으로 만든다.
