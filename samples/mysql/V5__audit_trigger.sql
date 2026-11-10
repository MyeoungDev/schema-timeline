CREATE TABLE purchase_audit (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  purchase_id BIGINT NOT NULL,
  changed_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
);

DELIMITER $$
CREATE TRIGGER trg_purchase_audit AFTER UPDATE ON purchase
FOR EACH ROW
BEGIN
  INSERT INTO purchase_audit (purchase_id) VALUES (NEW.id);
END$$
DELIMITER ;

ALTER TABLE purchase RENAME COLUMN amount TO total_amount;
