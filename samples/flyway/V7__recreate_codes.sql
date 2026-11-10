DROP TABLE codes;
CREATE TABLE codes (
    code varchar(20) PRIMARY KEY,
    label varchar(100) NOT NULL,
    sort_order int NOT NULL DEFAULT 0
);
DO $$
BEGIN
    EXECUTE 'ALTER TABLE members RENAME COLUMN phone TO mobile';
END
$$;
