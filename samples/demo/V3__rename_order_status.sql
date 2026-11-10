ALTER TABLE orders RENAME COLUMN status TO state;
ALTER TABLE orders ALTER COLUMN state TYPE varchar(30);
