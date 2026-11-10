RENAME TABLE orders TO purchase;
CREATE TABLE member_address (
  member_id BIGINT NOT NULL,
  seq INT NOT NULL,
  zip CHAR(5) NOT NULL,
  line1 VARCHAR(200) NOT NULL,
  PRIMARY KEY (member_id, seq),
  CONSTRAINT fk_address_member FOREIGN KEY (member_id) REFERENCES member (id) ON DELETE CASCADE
);
ALTER TABLE member DROP COLUMN created_at, ADD COLUMN joined_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3);
