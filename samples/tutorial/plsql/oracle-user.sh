#!/bin/sh
# チュートリアルの PL/SQL を配備する Oracle のユーザ `points` を作る（docs/guide/tutorial.md の 3.5）。何度流してもよい。
# SYSTEM で 1 度だけ流す準備で、difftest/plsql-warehouse-init.sh と同じ形。表と package は plsql_run.py deploy が作る。
set -e
cd "$(dirname "$0")/../../../difftest"
docker compose exec -T source-oracle sqlplus -s system/oracle@//localhost:1521/FREEPDB1 <<'SQL'
WHENEVER SQLERROR CONTINUE
CREATE USER points IDENTIFIED BY points QUOTA UNLIMITED ON users;
WHENEVER SQLERROR EXIT FAILURE
GRANT CREATE SESSION, CREATE TABLE, CREATE PROCEDURE, CREATE SEQUENCE, CREATE TRIGGER, CREATE TYPE TO points;
SELECT 'user points ready' AS status FROM dual;
SQL
