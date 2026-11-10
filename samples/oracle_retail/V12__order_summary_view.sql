CREATE OR REPLACE VIEW customer_order_summary AS
SELECT c.customer_id, c.email, COUNT(o.order_id) AS order_count, MAX(o.placed_at) AS last_order_at
FROM customers c
LEFT JOIN orders o ON o.customer_id = c.customer_id
GROUP BY c.customer_id, c.email;
