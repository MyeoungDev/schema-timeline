CREATE VIEW customer_order_summary AS
SELECT c.id AS customer_id, c.email, count(o.id) AS order_count, coalesce(sum(o.total), 0) AS amount
FROM customers c
LEFT JOIN orders o ON o.customer_id = c.id
GROUP BY c.id, c.email;
