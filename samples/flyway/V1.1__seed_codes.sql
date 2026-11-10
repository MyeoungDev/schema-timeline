CREATE TABLE codes (
    code varchar(20) PRIMARY KEY,
    label varchar(50) NOT NULL
);
INSERT INTO codes (code, label) VALUES ('NEW', '신규'), ('PAID', '결제 완료');
