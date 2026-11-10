CREATE TABLE customers (
    id bigserial PRIMARY KEY,
    email varchar(255) NOT NULL UNIQUE,
    name varchar(50) NOT NULL,
    fax varchar(20),
    created_at timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE customers IS 'Customers';

CREATE TABLE products (
    id bigserial PRIMARY KEY,
    name varchar(100) NOT NULL,
    price numeric(10,2) NOT NULL CHECK (price >= 0)
);
COMMENT ON TABLE products IS 'Products';

CREATE TABLE orders (
    id bigserial PRIMARY KEY,
    customer_id bigint NOT NULL REFERENCES customers(id),
    status varchar(20) NOT NULL DEFAULT 'NEW',
    total numeric(10,2) NOT NULL,
    memo text,
    created_at timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE orders IS 'Orders';
CREATE INDEX idx_orders_customer ON orders(customer_id);

CREATE TABLE order_items (
    id bigserial PRIMARY KEY,
    order_id bigint NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id bigint NOT NULL REFERENCES products(id),
    qty int NOT NULL
);

CREATE TABLE legacy_coupons (
    code varchar(30) PRIMARY KEY,
    customer_id bigint REFERENCES customers(id),
    discount int NOT NULL,
    expires_at date
);
COMMENT ON TABLE legacy_coupons IS 'Legacy coupons';

CREATE TABLE tags (
    id serial PRIMARY KEY,
    label varchar(30) NOT NULL
);
