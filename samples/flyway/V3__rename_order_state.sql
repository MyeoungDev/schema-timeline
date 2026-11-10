ALTER TABLE orders RENAME state TO status;
COMMENT ON COLUMN orders.status IS '주문 상태';
