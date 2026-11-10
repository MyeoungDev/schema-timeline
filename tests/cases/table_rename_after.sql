CREATE TABLE accounts (id BIGSERIAL PRIMARY KEY, email TEXT NOT NULL);
CREATE INDEX accounts_email_idx ON accounts (email);
CREATE TABLE posts (id BIGSERIAL PRIMARY KEY, member_id BIGINT REFERENCES accounts (id));
