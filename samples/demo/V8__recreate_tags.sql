DROP TABLE tags;
CREATE TABLE tags (
    id serial PRIMARY KEY,
    label varchar(50) NOT NULL UNIQUE,
    color char(7)
);
