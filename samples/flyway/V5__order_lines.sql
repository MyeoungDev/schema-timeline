ALTER TABLE order_items RENAME TO order_lines;
ALTER TABLE orders DROP COLUMN memo;
UPDATE orders SET status = 'NEW' WHERE status IS NULL;
