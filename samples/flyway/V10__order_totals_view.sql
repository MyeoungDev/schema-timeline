CREATE VIEW order_totals AS
SELECT member_id, count(*) AS cnt, sum(total) AS amount
FROM orders
GROUP BY member_id;
