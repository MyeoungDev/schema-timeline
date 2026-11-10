CREATE TABLE shipments (
    id bigserial PRIMARY KEY,
    order_id bigint NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    carrier varchar(30),
    tracking_no varchar(50),
    shipped_at timestamp
);
