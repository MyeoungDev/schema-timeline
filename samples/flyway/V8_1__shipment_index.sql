CREATE INDEX idx_shipments_tracking ON shipments(tracking_no) WHERE tracking_no IS NOT NULL;
ALTER TABLE members ALTER COLUMN mobile SET NOT NULL;
