ALTER TABLE employees MODIFY (email NOT NULL);
ALTER TABLE employees ADD CONSTRAINT uq_employees_email UNIQUE (email);
