CREATE TABLE members (
    id bigserial PRIMARY KEY,
    email varchar(255) NOT NULL UNIQUE,
    name varchar(50) NOT NULL,
    created_at timestamp NOT NULL DEFAULT now()
);
COMMENT ON TABLE members IS '회원';

CREATE TABLE products (
    id bigserial PRIMARY KEY,
    name varchar(100) NOT NULL,
    price numeric(10,2) NOT NULL
);

CREATE TABLE orders (
    id bigserial PRIMARY KEY,
    member_id bigint NOT NULL REFERENCES members(id),
    state varchar(20) NOT NULL,
    total numeric(10,2) NOT NULL,
    memo text,
    created_at timestamp NOT NULL DEFAULT now()
);
COMMENT ON TABLE orders IS '주문';

CREATE TABLE order_items (
    id bigserial PRIMARY KEY,
    order_id bigint NOT NULL REFERENCES orders(id),
    product_id bigint NOT NULL REFERENCES products(id),
    qty int NOT NULL
);
CREATE INDEX idx_orders_member ON orders(member_id);
