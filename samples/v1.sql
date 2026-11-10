-- 샘플 스키마 변경 전
CREATE TABLE users (
    id          BIGSERIAL PRIMARY KEY,
    email       VARCHAR(100) NOT NULL UNIQUE,
    name        VARCHAR(50),
    created_at  TIMESTAMP DEFAULT now()
);

CREATE TABLE orders (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users (id),
    state       VARCHAR(20) NOT NULL DEFAULT 'NEW',
    total       NUMERIC(10, 2),
    memo        TEXT,
    created_at  TIMESTAMP DEFAULT now()
);

CREATE INDEX idx_orders_user ON orders (user_id);

CREATE TABLE order_items (
    id            BIGSERIAL PRIMARY KEY,
    order_id      BIGINT NOT NULL REFERENCES orders (id),
    product_name  VARCHAR(100) NOT NULL,
    qty           INTEGER NOT NULL,
    price         NUMERIC(10, 2) NOT NULL
);

CREATE TABLE legacy_coupons (
    id    BIGSERIAL PRIMARY KEY,
    code  VARCHAR(20) NOT NULL
);

COMMENT ON TABLE orders IS '주문';
