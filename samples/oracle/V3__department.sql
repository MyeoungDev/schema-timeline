RENAME dept TO department;
ALTER TABLE emp DROP COLUMN hired;
ALTER TABLE department ADD CONSTRAINT ck_department_loc CHECK (loc IS NULL OR LENGTH(loc) > 1);

CREATE VIEW v_emp_dept AS
SELECT e.id, e.ename, d.dname
FROM emp e JOIN department d ON d.id = e.dept_id;

INSERT INTO department (dname, loc) VALUES ('본사', 'Seoul');
