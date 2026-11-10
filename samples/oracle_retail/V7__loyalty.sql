CREATE TABLE loyalty_accounts (
    customer_id NUMBER PRIMARY KEY REFERENCES customers(customer_id),
    points      NUMBER(12) DEFAULT 0 NOT NULL,
    joined_on   DATE DEFAULT SYSDATE NOT NULL
);
ALTER TABLE customers ADD (loyalty_tier VARCHAR2(10) DEFAULT 'BASIC' NOT NULL);
CREATE INDEX ix_customers_tier ON customers (loyalty_tier);
