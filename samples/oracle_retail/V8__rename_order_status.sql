ALTER TABLE orders RENAME COLUMN status TO order_status;
ALTER TABLE orders MODIFY (order_status VARCHAR2(30));
COMMENT ON COLUMN orders.order_status IS 'NEW, PAID, SHIPPED, CANCELLED';
