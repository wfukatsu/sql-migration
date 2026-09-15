#!/bin/sh
# Create the Oracle user ScalarDB connects as when Oracle Database is the ScalarDB backend
# (docs/oracle-backend-verification-plan.md). Idempotent. This is database provisioning, run once as SYSTEM; the
# harness itself never connects to ScalarDB's schemas. Privileges are exactly those listed in the ScalarDB 3.19
# Requirements for Oracle Database.
set -e
cd "$(dirname "$0")"
docker compose exec -T source-oracle sqlplus -s system/oracle@//localhost:1521/FREEPDB1 <<'SQL'
WHENEVER SQLERROR CONTINUE
CREATE USER scalardb IDENTIFIED BY scalardb QUOTA UNLIMITED ON users;
WHENEVER SQLERROR EXIT FAILURE
GRANT CREATE SESSION, CREATE USER, DROP USER, ALTER USER, CREATE ANY TABLE, DROP ANY TABLE, CREATE ANY INDEX,
      ALTER ANY INDEX, DROP ANY INDEX, ALTER ANY TABLE, SELECT ANY TABLE, INSERT ANY TABLE, UPDATE ANY TABLE,
      DELETE ANY TABLE, CREATE ANY VIEW, DROP ANY VIEW TO scalardb;
SELECT 'scalardb user ready: ' || COUNT(*) || ' system privileges' AS status FROM dba_sys_privs WHERE grantee = 'SCALARDB';
SQL
