ALTER TABLE customers ADD COLUMN phone varchar(20);
COMMENT ON COLUMN customers.phone IS 'Phone number';
CREATE INDEX idx_customers_phone ON customers(phone);
