ALTER TABLE products ADD COLUMN sku varchar(40) NOT NULL;
ALTER TABLE products ADD CONSTRAINT uq_products_sku UNIQUE (sku);
ALTER TABLE products ALTER COLUMN price TYPE numeric(12,2);
