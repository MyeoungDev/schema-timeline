CREATE VIEW v_member_purchase AS
SELECT m.id, m.email, COUNT(p.id) AS purchases
FROM member m LEFT JOIN purchase p ON p.member_id = m.id
GROUP BY m.id, m.email;

INSERT INTO member (email, name) VALUES ('admin@example.com', '관리자');
