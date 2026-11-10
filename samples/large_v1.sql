-- 규모 점검용 샘플: 회원·상품·주문·결제·배송·재고·고객지원 도메인 28개 테이블.

CREATE TABLE members (id bigserial PRIMARY KEY, email varchar(255) NOT NULL UNIQUE, name varchar(50) NOT NULL, phone varchar(20), status varchar(20) NOT NULL DEFAULT 'ACTIVE', created_at timestamp NOT NULL DEFAULT now());
COMMENT ON TABLE members IS '회원';
CREATE TABLE member_addresses (id bigserial PRIMARY KEY, member_id bigint NOT NULL REFERENCES members(id), label varchar(30), zipcode varchar(10) NOT NULL, address1 varchar(200) NOT NULL, address2 varchar(200), is_default boolean NOT NULL DEFAULT false);
CREATE TABLE member_grades (id serial PRIMARY KEY, code varchar(20) NOT NULL UNIQUE, name varchar(50) NOT NULL, discount_rate numeric(5,2) NOT NULL DEFAULT 0);
CREATE TABLE member_grade_histories (id bigserial PRIMARY KEY, member_id bigint NOT NULL REFERENCES members(id), grade_id int NOT NULL REFERENCES member_grades(id), changed_at timestamp NOT NULL DEFAULT now());
CREATE TABLE login_histories (id bigserial PRIMARY KEY, member_id bigint NOT NULL REFERENCES members(id), ip varchar(45), user_agent text, logged_in_at timestamp NOT NULL DEFAULT now());

CREATE TABLE categories (id serial PRIMARY KEY, parent_id int REFERENCES categories(id), name varchar(100) NOT NULL, sort_order int NOT NULL DEFAULT 0);
CREATE TABLE brands (id serial PRIMARY KEY, name varchar(100) NOT NULL, country varchar(2));
CREATE TABLE products (id bigserial PRIMARY KEY, category_id int NOT NULL REFERENCES categories(id), brand_id int REFERENCES brands(id), name varchar(200) NOT NULL, price numeric(12,2) NOT NULL, description text, created_at timestamp NOT NULL DEFAULT now());
COMMENT ON TABLE products IS '상품';
CREATE TABLE product_options (id bigserial PRIMARY KEY, product_id bigint NOT NULL REFERENCES products(id), name varchar(100) NOT NULL, extra_price numeric(12,2) NOT NULL DEFAULT 0);
CREATE TABLE product_images (id bigserial PRIMARY KEY, product_id bigint NOT NULL REFERENCES products(id), url varchar(500) NOT NULL, sort_order int NOT NULL DEFAULT 0);
CREATE TABLE product_reviews (id bigserial PRIMARY KEY, product_id bigint NOT NULL REFERENCES products(id), member_id bigint NOT NULL REFERENCES members(id), rating smallint NOT NULL, body text, created_at timestamp NOT NULL DEFAULT now());
CREATE TABLE wishlists (member_id bigint NOT NULL REFERENCES members(id), product_id bigint NOT NULL REFERENCES products(id), created_at timestamp NOT NULL DEFAULT now(), PRIMARY KEY (member_id, product_id));

CREATE TABLE carts (id bigserial PRIMARY KEY, member_id bigint NOT NULL REFERENCES members(id), created_at timestamp NOT NULL DEFAULT now());
CREATE TABLE cart_items (id bigserial PRIMARY KEY, cart_id bigint NOT NULL REFERENCES carts(id), option_id bigint NOT NULL REFERENCES product_options(id), qty int NOT NULL DEFAULT 1);
CREATE TABLE orders (id bigserial PRIMARY KEY, member_id bigint NOT NULL REFERENCES members(id), address_id bigint REFERENCES member_addresses(id), state varchar(20) NOT NULL, total numeric(12,2) NOT NULL, memo varchar(500), created_at timestamp NOT NULL DEFAULT now());
COMMENT ON TABLE orders IS '주문';
CREATE TABLE order_items (id bigserial PRIMARY KEY, order_id bigint NOT NULL REFERENCES orders(id), option_id bigint NOT NULL REFERENCES product_options(id), qty int NOT NULL, unit_price numeric(12,2) NOT NULL);
CREATE TABLE coupons (id serial PRIMARY KEY, code varchar(30) NOT NULL UNIQUE, discount numeric(12,2) NOT NULL, expires_at timestamp);
CREATE TABLE order_coupons (order_id bigint NOT NULL REFERENCES orders(id), coupon_id int NOT NULL REFERENCES coupons(id), PRIMARY KEY (order_id, coupon_id));

CREATE TABLE payments (id bigserial PRIMARY KEY, order_id bigint NOT NULL REFERENCES orders(id), method varchar(20) NOT NULL, amount numeric(12,2) NOT NULL, paid_at timestamp);
CREATE TABLE payment_cancels (id bigserial PRIMARY KEY, payment_id bigint NOT NULL REFERENCES payments(id), amount numeric(12,2) NOT NULL, reason varchar(200), canceled_at timestamp NOT NULL DEFAULT now());
CREATE TABLE refunds (id bigserial PRIMARY KEY, payment_id bigint NOT NULL REFERENCES payments(id), amount numeric(12,2) NOT NULL, bank_account varchar(50));

CREATE TABLE shipments (id bigserial PRIMARY KEY, order_id bigint NOT NULL REFERENCES orders(id), carrier varchar(30), tracking_no varchar(50), shipped_at timestamp);
CREATE TABLE shipment_events (id bigserial PRIMARY KEY, shipment_id bigint NOT NULL REFERENCES shipments(id), status varchar(30) NOT NULL, occurred_at timestamp NOT NULL);

CREATE TABLE warehouses (id serial PRIMARY KEY, name varchar(100) NOT NULL, region varchar(30));
CREATE TABLE stocks (id bigserial PRIMARY KEY, warehouse_id int NOT NULL REFERENCES warehouses(id), option_id bigint NOT NULL REFERENCES product_options(id), qty int NOT NULL DEFAULT 0);
CREATE TABLE stock_movements (id bigserial PRIMARY KEY, stock_id bigint NOT NULL REFERENCES stocks(id), delta int NOT NULL, reason varchar(30), moved_at timestamp NOT NULL DEFAULT now());

CREATE TABLE inquiries (id bigserial PRIMARY KEY, member_id bigint NOT NULL REFERENCES members(id), order_id bigint REFERENCES orders(id), title varchar(200) NOT NULL, body text NOT NULL, created_at timestamp NOT NULL DEFAULT now());
CREATE TABLE inquiry_answers (id bigserial PRIMARY KEY, inquiry_id bigint NOT NULL REFERENCES inquiries(id), body text NOT NULL, answered_at timestamp NOT NULL DEFAULT now());

CREATE INDEX idx_orders_member ON orders(member_id);
CREATE INDEX idx_order_items_order ON order_items(order_id);
CREATE INDEX idx_payments_order ON payments(order_id);
CREATE INDEX idx_login_member ON login_histories(member_id, logged_in_at);

CREATE VIEW order_totals AS SELECT member_id, count(*) AS cnt, sum(total) AS amount FROM orders GROUP BY member_id;
