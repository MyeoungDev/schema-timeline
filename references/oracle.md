# Oracle 재생

`--db oracle`(기본 이미지 `gvenzl/oracle-free:23-slim`, Oracle Database Free). Flyway·sqlfiles·git-snapshots 이력에 쓴다.
`--schema` 는 재생용 사용자 이름이다(기본 `REPLAY`). 이력이 다른 스키마 이름을 붙여 객체를 만들면(`APP.TABLE`) 실패하므로,
그 이름을 `--schema` 로 준다. 다른 이미지(container-registry.oracle.com 등)는 환경변수가 달라 그대로는 뜨지 않는다.

## 재생 방식

- Oracle 23 이상은 사설 IP 가 없으면 뜨지 않는다. 재생마다 외부 경로가 없는 docker 내부 네트워크(`docker network create --internal`)를
  만들어 쓰고, 끝나면 컨테이너와 함께 지운다. 이 점을 사용자에게 한 줄로 알린다.
- 각 파일을 `sqlplus` 로 적용한다. `SET DEFINE OFF`(& 치환 없음), `SET SQLBLANKLINES ON`(문장 안 빈 줄 허용)을 켠다.
- 적용 전에 스크립트를 정리한다(`scriptsplit.py`). 마지막 문장에 `;` 가 없으면 붙이고, SQL 문 뒤의 `/` 는 뺀다(sqlplus 에선 재실행이다).
  `SET`·`PROMPT` 같은 SQL*Plus 명령 줄은 빼고, `EXEC 프로시저(..)` 는 `BEGIN ..; END;` 로 바꾼다.
- PL/SQL 블록(프로시저·트리거·패키지·타입)은 `/` 줄까지가 한 블록이다. `/` 가 빠져 다음 문장까지 블록이 되면,
  Oracle 은 그 객체를 컴파일 오류 상태로 만들고 뒤 문장은 실행하지 않는다. 이때 단계에 경고 두 개("블록 뒤에 / 가 없어",
  "컴파일 오류가 있는 객체")가 붙는다. Flyway 도 블록을 `/` 까지로 읽으므로 실제 마이그레이션도 같은 상태가 된다.
- DDL 은 문장마다 자동 커밋된다. 실패한 단계는 실패한 문장 앞까지 적용된 상태로 끝난다.
- 이름 변경은 단계 SQL 의 `RENAME a TO b`, `ALTER TABLE ... RENAME TO`, `ALTER TABLE ... RENAME COLUMN x TO y` 로 확정한다.
  따옴표 없는 이름은 대문자로 맞춘다.

## 결과를 전할 때

- 이름은 Oracle 딕셔너리 표기(대문자)로 나온다. 따옴표로 만든 소문자·대소문자 섞인 이름은 그대로다.
- `NOT NULL` 은 시스템 이름(SYS_C...)의 CHECK 제약이 아니라 컬럼의 NULL 허용 여부로 나온다.
- 이름 없이 만든 제약은 `SYS_C...` 이름이다. 변경 여부는 이름이 아니라 내용으로 판정하므로 이름이 달라도 변경으로 나오지 않는다.
- identity 컬럼은 `identity BY DEFAULT/ALWAYS` 로 나오고, 시퀀스 기본값은 숨긴다.
- 함수 기반·DESC 인덱스는 숨은 가상 컬럼의 식으로 정의를 복원한다.
- 시점별 전체 DDL 은 `DBMS_METADATA.GET_DDL` 결과(저장 속성 제외)에 인덱스·코멘트 문을 덧붙인 것이다. 트리거·시퀀스·패키지·프로시저는 담지 않는다. 이 객체들은 변경 목록 대신 단계의 `objects` 에 추가·삭제·정의 변경·INVALID 로 나온다(본문 diff 는 없다).
- identity 의 `ON NULL`, 제약의 `DEFERRABLE`, 구체화 뷰(MV)는 변경 목록에서 구분하지 않는다.

## 흔한 실패

| 오류 | 원인과 대응 |
|---|---|
| `ORA-00942: table or view does not exist` 가 첫 단계부터 | baseline 이전 이력이 없다. 운영 구조 덤프를 V0 으로 받아야 한다 |
| `ORA-01918`, `ORA-01031` | 다른 스키마·사용자를 다룬다. 재생용 사용자에겐 DB_DEVELOPER_ROLE 만 있다. 그대로 설명한다 |
| `SP2-` 로 시작하는 오류 | sqlplus 가 읽지 못한 줄. 그 파일의 구문 문제다 |
| 컨테이너가 준비되지 않았다 | docker 에 할당한 메모리가 부족하거나 이미지가 다르다. 오류에 붙은 컨테이너 로그 끝을 보여 주고 docker 설정을 확인하라고 알린다 |
| 경고 "컴파일 오류가 있는 객체" | 프로시저·트리거 본문 오류. 재생은 계속되지만 그 객체는 INVALID 다. 위 `/` 누락이 흔한 원인이다 |
