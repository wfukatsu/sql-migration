#!/bin/sh
# Oracle 側にこのプロジェクト専用のユーザ `hrs` を作る（samples/tutorial/plsql/oracle-user.sh と同じ形）。SYSTEM で 1 度だけ。何度流してもよい。
set -e
cd "$(dirname "$0")/../../../difftest"
docker compose exec -T source-oracle sqlplus -s system/oracle@//localhost:1521/FREEPDB1 <<'SQL'
WHENEVER SQLERROR CONTINUE
CREATE USER hrs IDENTIFIED BY hrs QUOTA UNLIMITED ON users;
WHENEVER SQLERROR EXIT FAILURE
GRANT CREATE SESSION, CREATE TABLE, CREATE PROCEDURE, CREATE SEQUENCE, CREATE TRIGGER, CREATE TYPE, CREATE VIEW, CREATE JOB TO hrs;
SELECT 'user hrs ready' AS status FROM dual;
SQL
