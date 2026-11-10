CREATE TABLE categories (
    id bigserial PRIMARY KEY,
    parent_id bigint REFERENCES categories(id),
    name varchar(80) NOT NULL UNIQUE,
    slug varchar(100) NOT NULL UNIQUE
);
COMMENT ON TABLE categories IS 'Product categories';

CREATE TABLE product_categories (
    product_id bigint NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    category_id bigint NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    PRIMARY KEY (product_id, category_id)
);

CREATE TABLE suppliers (
    id bigserial PRIMARY KEY,
    name varchar(120) NOT NULL,
    email varchar(255) UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE supplier_products (
    supplier_id bigint NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    product_id bigint NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    supplier_sku varchar(60),
    unit_cost numeric(12,2) NOT NULL CHECK (unit_cost >= 0),
    PRIMARY KEY (supplier_id, product_id)
);

CREATE TABLE warehouses (
    id bigserial PRIMARY KEY,
    name varchar(100) NOT NULL,
    code varchar(20) NOT NULL UNIQUE,
    city varchar(100) NOT NULL,
    country_code char(2) NOT NULL
);

CREATE TABLE inventory (
    product_id bigint NOT NULL REFERENCES products(id),
    warehouse_id bigint NOT NULL REFERENCES warehouses(id),
    quantity integer NOT NULL DEFAULT 0 CHECK (quantity >= 0),
    reorder_level integer NOT NULL DEFAULT 10 CHECK (reorder_level >= 0),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (product_id, warehouse_id)
);
CREATE INDEX idx_inventory_reorder ON inventory(warehouse_id, quantity)
    WHERE quantity <= 10;
