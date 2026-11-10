DROP TABLE legacy_fax_log;
ALTER TABLE customers DROP COLUMN fax;
ALTER TABLE products DROP COLUMN legacy_code;
