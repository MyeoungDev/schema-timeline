CREATE TABLE customer_addresses (
    id bigserial PRIMARY KEY,
    customer_id bigint NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    label varchar(40) NOT NULL,
    recipient varchar(100) NOT NULL,
    address_line1 varchar(160) NOT NULL,
    address_line2 varchar(160),
    city varchar(100) NOT NULL,
    postal_code varchar(20) NOT NULL,
    country_code char(2) NOT NULL,
    is_default boolean NOT NULL DEFAULT false
);
CREATE INDEX idx_customer_addresses_customer ON customer_addresses(customer_id);

CREATE TABLE payments (
    id bigserial PRIMARY KEY,
    order_id bigint NOT NULL REFERENCES orders(id),
    amount numeric(12,2) NOT NULL CHECK (amount > 0),
    method varchar(30) NOT NULL,
    status varchar(20) NOT NULL DEFAULT 'PENDING',
    provider_ref varchar(100) UNIQUE,
    paid_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_payments_order_created ON payments(order_id, created_at DESC);

CREATE TABLE returns (
    id bigserial PRIMARY KEY,
    order_id bigint NOT NULL REFERENCES orders(id),
    customer_id bigint NOT NULL REFERENCES customers(id),
    status varchar(20) NOT NULL DEFAULT 'REQUESTED',
    reason varchar(500),
    requested_at timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz
);

CREATE TABLE return_items (
    return_id bigint NOT NULL REFERENCES returns(id) ON DELETE CASCADE,
    order_item_id bigint NOT NULL REFERENCES order_items(id),
    quantity integer NOT NULL CHECK (quantity > 0),
    refund_amount numeric(12,2) NOT NULL CHECK (refund_amount >= 0),
    PRIMARY KEY (return_id, order_item_id)
);

CREATE TABLE order_events (
    id bigserial PRIMARY KEY,
    order_id bigint NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    state varchar(30) NOT NULL,
    note text,
    occurred_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_order_events_order_time ON order_events(order_id, occurred_at DESC);
