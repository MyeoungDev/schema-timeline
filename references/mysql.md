# MySQL·MariaDB 재생

`--db mysql`(기본 이미지 `mysql:8.4`) 또는 `--db mariadb`(`mariadb:11.4`). Flyway·sqlfiles·git-snapshots·Prisma(provider `mysql`)·
Drizzle(dialect `mysql`) 이력에 쓴다. `--schema` 는 데이터베이스 이름이다(기본 `replay`). 이력이 `USE 다른DB` 로 데이터베이스를 바꾸면
그 뒤 객체는 결과에 나오지 않는다.

## 재생 방식

- 각 파일을 `mysql`(MariaDB 는 `mariadb`) 클라이언트로 적용한다. `DELIMITER` 로 프로시저·트리거를 정의한 파일도 그대로 된다.
- DDL 은 문장마다 자동 커밋된다. 단계가 실패하면 실패한 문장 앞까지는 적용된 상태로 끝나고, 결과에는 그 단계 스냅샷이 없다.
- MySQL 은 테이블을 다시 만드는 ALTER 에서 내부 id 가 바뀌므로 카탈로그로 이름 변경을 알 수 없다. 대신 단계 SQL 의
  `RENAME TABLE a TO b`, `ALTER TABLE ... RENAME TO`, `RENAME COLUMN x TO y`, `CHANGE [COLUMN] x y` 를 읽어,
  변경 전에 옛 이름이 있고 변경 후에 새 이름이 있을 때 "이름 변경(확정)"으로 낸다. 같은 단계에서 DROP 한 뒤 같은 이름으로 다시 만들면 삭제+추가다.
  다른 데이터베이스 이름을 붙인 문장(`RENAME TABLE other.a TO other.b`)은 무시한다.

## 결과를 전할 때

- 컬럼 타입은 `COLUMN_TYPE` 그대로다(`int unsigned`, `enum('A','B')`). MariaDB 는 `bigint(20)` 처럼 표시 폭이 붙는다.
- enum 값 추가는 컬럼 타입 변경으로 나온다.
- FK 를 만들면 MySQL 이 같은 이름의 인덱스를 자동으로 만든다. 이 인덱스도 결과에 나온다.
- 시점별 전체 DDL 은 `SHOW CREATE TABLE` 결과이고 `SET FOREIGN_KEY_CHECKS = 0/1` 로 감싼다. `AUTO_INCREMENT=` 카운터는 뺀다.
- 트리거·프로시저·함수·이벤트는 변경 목록과 전체 DDL 에는 없고, 단계의 `objects` 에 추가·삭제·정의 변경으로 이름만 나온다(본문 diff 는 없다). 뷰의 ALGORITHM·SQL SECURITY, 인덱스 INVISIBLE 도 구분하지 않는다.
- `DELIMITER` 로 정의한 루틴 본문은 한 문장으로 읽으므로, 본문 안의 INSERT 때문에 단계가 DML 로 표시되지는 않는다.
- 기본 문자셋·콜레이션은 이미지 버전마다 다르다(MySQL 8.4 `utf8mb4_0900_ai_ci`, MariaDB 11.4 `utf8mb4_uca1400_ai_ci`).
  운영과 맞추려면 `--image` 로 같은 버전을 쓴다.

## 흔한 실패

| 오류 | 원인과 대응 |
|---|---|
| `ERROR 1046 No database selected` | 파일이 데이터베이스를 지정하지 않는 문장을 쓴다. 보통은 나오지 않는다. 그대로 설명한다 |
| `ERROR 1227 Access denied; you need (at least one of) the SUPER ...` | `DEFINER=` 가 붙은 뷰·트리거. root 로 적용하므로 드물다. 오류 문장을 그대로 설명한다 |
| `ERROR 1064 ... near 'DELIMITER'` | 클라이언트 지시문이 아닌 위치에 DELIMITER 가 있다. 그 파일의 구문 문제다 |
