CREATE TABLE members (id BIGSERIAL PRIMARY KEY, email TEXT NOT NULL);
CREATE INDEX members_email_idx ON members (email);
CREATE TABLE posts (id BIGSERIAL PRIMARY KEY, member_id BIGINT REFERENCES members (id));
