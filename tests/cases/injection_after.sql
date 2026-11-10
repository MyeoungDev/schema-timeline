CREATE TABLE "Users" (id BIGINT PRIMARY KEY);
CREATE TABLE notes (id BIGINT PRIMARY KEY, body TEXT DEFAULT '</script><img src=x onerror=alert(1)>', user_id BIGINT REFERENCES "Users" (id));
COMMENT ON COLUMN notes.body IS '<!--<script>';
