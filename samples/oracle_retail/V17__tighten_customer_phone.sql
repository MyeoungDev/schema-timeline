ALTER TABLE customers MODIFY (phone VARCHAR2(20));
CREATE INDEX ix_customers_phone ON customers (phone);
