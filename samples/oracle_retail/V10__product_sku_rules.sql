ALTER TABLE products MODIFY (sku VARCHAR2(40));
ALTER TABLE products ADD CONSTRAINT ck_products_sku CHECK (sku = UPPER(sku));
