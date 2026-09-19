#!/bin/sh
# The other end of the corpus's DB link (`warehouse_link`, prc_remote_sync). Idempotent; run once as SYSTEM, like
# oracle-backend-init.sh. The "remote" database is a second schema of the same Oracle instance reached through a
# loopback link: what the corpus needs is a real distributed transaction to compare against, not a second server.
#
#   warehouse            the remote side: orders(order_id, status), shipment_queue(order_id, requested_at)
#   source.warehouse_link  the link prc_remote_sync writes through
set -e
cd "$(dirname "$0")"
docker compose exec -T source-oracle sqlplus -s system/oracle@//localhost:1521/FREEPDB1 <<'SQL'
WHENEVER SQLERROR CONTINUE
CREATE USER warehouse IDENTIFIED BY warehouse QUOTA UNLIMITED ON users;
WHENEVER SQLERROR EXIT FAILURE
GRANT CREATE SESSION, CREATE TABLE TO warehouse;
GRANT CREATE DATABASE LINK TO source;
SQL
docker compose exec -T source-oracle sqlplus -s warehouse/warehouse@//localhost:1521/FREEPDB1 <<'SQL'
WHENEVER SQLERROR CONTINUE
DROP TABLE shipment_queue;
DROP TABLE orders;
WHENEVER SQLERROR EXIT FAILURE
CREATE TABLE orders (order_id NUMBER(19) NOT NULL, status VARCHAR2(20), CONSTRAINT pk_wh_orders PRIMARY KEY (order_id));
CREATE TABLE shipment_queue (order_id NUMBER(19) NOT NULL, requested_at DATE NOT NULL, CONSTRAINT pk_wh_queue PRIMARY KEY (order_id));
SQL
docker compose exec -T source-oracle sqlplus -s source/source@//localhost:1521/FREEPDB1 <<'SQL'
WHENEVER SQLERROR CONTINUE
DROP DATABASE LINK warehouse_link;
WHENEVER SQLERROR EXIT FAILURE
CREATE DATABASE LINK warehouse_link CONNECT TO warehouse IDENTIFIED BY warehouse USING '//localhost:1521/FREEPDB1';
SELECT 'warehouse_link ready: ' || COUNT(*) || ' remote orders' AS status FROM orders@warehouse_link;
SQL
