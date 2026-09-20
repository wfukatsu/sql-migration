#!/bin/sh
# Migration Explorer の fixture を、検証用の Oracle（difftest/docker-compose.yml の source-oracle）に入れる。
# 何度流してもよい: ユーザ `explorer` を作り直して、表・PL/SQL・DB にしか無い view と trigger・合成データを入れる。
#   sh fixtures/explorer/db/setup.sh          統計は取らない（snapshot-no-stats.json 用）
#   sh fixtures/explorer/db/setup.sh stats    そのあとで統計を取る（snapshot.json / snapshot-with-values.json 用）
# 統計を取るのはこの準備の側で、収集スクリプト（difftest/catalog_snapshot.py）は SELECT しかしない。
set -e
here="$(cd "$(dirname "$0")" && pwd)"
cd "$here/../../../difftest"
sys() { docker compose exec -T source-oracle sqlplus -s system/oracle@//localhost:1521/FREEPDB1; }
app() { docker compose exec -T source-oracle sqlplus -s explorer/explorer@//localhost:1521/FREEPDB1; }

if [ "$1" = "stats" ]; then
  app <<'SQL'
WHENEVER SQLERROR EXIT FAILURE
BEGIN
  DBMS_STATS.GATHER_SCHEMA_STATS(ownname => USER, method_opt => 'FOR ALL COLUMNS SIZE 1');
  -- ヒストグラムは、値が偏っている列にだけ作る（本番では Oracle が自分で選ぶ。fixture では決め打ちにする）
  DBMS_STATS.GATHER_TABLE_STATS(USER, 'ORDERS', method_opt => 'FOR ALL COLUMNS SIZE 1 FOR COLUMNS SIZE 254 STATUS');
  DBMS_STATS.GATHER_TABLE_STATS(USER, 'ORDER_ITEMS', method_opt => 'FOR ALL COLUMNS SIZE 1 FOR COLUMNS SIZE 254 PRODUCT_ID');
  DBMS_STATS.GATHER_TABLE_STATS(USER, 'SHIPMENTS', method_opt => 'FOR ALL COLUMNS SIZE 1 FOR COLUMNS SIZE 254 CARRIER');
  -- audit_log は「一度も統計を取っていない表」として残す
  DBMS_STATS.DELETE_TABLE_STATS(ownname => USER, tabname => 'AUDIT_LOG');
END;
/
SELECT 'statistics gathered' AS status FROM dual;
SQL
  exit 0
fi

sys <<'SQL'
WHENEVER SQLERROR CONTINUE
DROP USER explorer CASCADE;
WHENEVER SQLERROR EXIT FAILURE
CREATE USER explorer IDENTIFIED BY explorer QUOTA UNLIMITED ON users;
GRANT CREATE SESSION, CREATE TABLE, CREATE VIEW, CREATE PROCEDURE, CREATE SEQUENCE, CREATE TRIGGER TO explorer;
SELECT 'user explorer ready' AS status FROM dual;
SQL

{ echo "WHENEVER SQLERROR EXIT FAILURE"; cat "$here/../src/schema.sql"; } | app
for f in create_order.prc cancel_order.prc purge_table.prc trg_orders_audit.trg; do
  { echo "WHENEVER SQLERROR EXIT FAILURE"; cat "$here/../src/$f"; } | app
done
{ echo "WHENEVER SQLERROR EXIT FAILURE"; cat "$here/db-only.sql"; } | app
{ echo "WHENEVER SQLERROR EXIT FAILURE"; cat "$here/../src/report_open_orders.prc"; } | app
{ echo "WHENEVER SQLERROR EXIT FAILURE"; cat "$here/data.sql"; } | app
