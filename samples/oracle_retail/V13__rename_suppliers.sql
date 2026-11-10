ALTER TABLE suppliers RENAME TO vendors;
ALTER TABLE products RENAME COLUMN supplier_id TO vendor_id;
COMMENT ON TABLE vendors IS 'Product vendors';
