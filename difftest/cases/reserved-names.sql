-- Issue #29 (6): names that are keywords of ScalarDB SQL and plain names in PostgreSQL
CREATE TABLE roles (key INT PRIMARY KEY, type VARCHAR(10), data VARCHAR(10), deptno INT);
CREATE INDEX idx_roles_type ON roles (type);
SELECT key, type, data FROM roles WHERE key = 2;
SELECT key, data FROM roles WHERE type = 'a';
SELECT r.key, r.type AS mode FROM roles r WHERE r.key = 3;
SELECT UPPER(data) AS d, type FROM roles WHERE key = 1;
SELECT type, COUNT(*) AS n FROM roles GROUP BY type ORDER BY type;
