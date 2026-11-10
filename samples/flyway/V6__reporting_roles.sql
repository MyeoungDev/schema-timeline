CREATE USER app_reader WITH PASSWORD 'p@ss-w0rd';
GRANT SELECT ON orders TO app_reader;
GRANT SELECT ON orders, order_lines TO report_role;
