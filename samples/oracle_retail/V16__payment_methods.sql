CREATE TABLE payment_methods (
    method_code VARCHAR2(20) PRIMARY KEY,
    label       VARCHAR2(60) NOT NULL
);
ALTER TABLE payments ADD (method_code VARCHAR2(20) REFERENCES payment_methods(method_code));
ALTER TABLE payments MODIFY (amount NUMBER(12,2));
