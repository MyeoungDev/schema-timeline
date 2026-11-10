CREATE TYPE shipment_status AS ENUM ('READY', 'SHIPPED', 'DELIVERED');

CREATE TABLE shipments (
    id bigserial PRIMARY KEY,
    order_id bigint NOT NULL REFERENCES orders(id),
    status shipment_status NOT NULL DEFAULT 'READY',
    tracking_no varchar(40),
    shipped_at timestamptz
);
COMMENT ON TABLE shipments IS 'Shipments';
CREATE INDEX idx_shipments_order ON shipments(order_id);
