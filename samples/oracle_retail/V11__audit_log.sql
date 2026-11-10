CREATE TABLE audit_log (
    audit_id   NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    table_name VARCHAR2(128) NOT NULL,
    row_key    VARCHAR2(200) NOT NULL,
    action     VARCHAR2(10) NOT NULL,
    changed_at TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL
);
COMMENT ON TABLE audit_log IS 'Row change audit trail';

CREATE OR REPLACE TRIGGER trg_orders_audit
AFTER UPDATE OF order_status ON orders
FOR EACH ROW
BEGIN
  INSERT INTO audit_log (table_name, row_key, action)
  VALUES ('ORDERS', TO_CHAR(:NEW.order_id), 'UPDATE');
END;
/
