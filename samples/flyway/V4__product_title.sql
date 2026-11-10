-- 이름과 타입을 한 번에 바꾼다. 두 스냅샷 비교만으로는 짝을 찾을 수 없는 경우다.
ALTER TABLE products RENAME COLUMN name TO title;
ALTER TABLE products ALTER COLUMN title TYPE varchar(200);
ALTER TABLE orders ALTER COLUMN total TYPE numeric(14,2);
