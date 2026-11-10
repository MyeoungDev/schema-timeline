-- 샘플 스키마 변경 후
CREATE TABLE users (
    id          BIGSERIAL PRIMARY KEY,
    email       VARCHAR(255) NOT NULL UNIQUE,
    name        VARCHAR(50) NOT NULL,
    phone       VARCHAR(20),
    created_at  TIMESTAMP DEFAULT now()
);

CREATE TABLE products (
    id     BIGSERIAL PRIMARY KEY,
    name   VARCHAR(100) NOT NULL,
    price  NUMERIC(10, 2) NOT NULL
);

CREATE TABLE orders (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users (id),
    status      VARCHAR(20) NOT NULL DEFAULT 'NEW',
    total       NUMERIC(12, 2),
    created_at  TIMESTAMP DEFAULT now(),
    shipped_at  TIMESTAMPTZ
);

CREATE INDEX idx_orders_user ON orders (user_id);
CREATE INDEX idx_orders_status ON orders (status);

CREATE TABLE order_items (
    id          BIGSERIAL PRIMARY KEY,
    order_id    BIGINT NOT NULL REFERENCES orders (id),
    product_id  BIGINT NOT NULL REFERENCES products (id),
    qty         INTEGER NOT NULL DEFAULT 1 CHECK (qty > 0),
    price       NUMERIC(10, 2) NOT NULL
);

CREATE VIEW order_summary AS
SELECT o.id, o.user_id, o.status, count(i.id) AS item_count
FROM orders o
LEFT JOIN order_items i ON i.order_id = o.id
GROUP BY o.id, o.user_id, o.status;

COMMENT ON TABLE orders IS '주문';
COMMENT ON COLUMN orders.status IS '주문 상태';
