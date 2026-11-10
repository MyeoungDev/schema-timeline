DROP TABLE stock_levels;
CREATE TABLE stock_levels (
    warehouse_id NUMBER NOT NULL REFERENCES warehouses(warehouse_id),
    product_id   NUMBER NOT NULL REFERENCES products(product_id),
    on_hand      NUMBER(10) DEFAULT 0 NOT NULL,
    reserved     NUMBER(10) DEFAULT 0 NOT NULL,
    updated_at   TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT pk_stock_levels PRIMARY KEY (warehouse_id, product_id),
    CONSTRAINT ck_stock_levels_reserved CHECK (reserved <= on_hand)
);
