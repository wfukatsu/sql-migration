-- P0-1: corpus が参照する表・順序・索引の Oracle DDL。
--
-- 合成データのみ。実案件のスキーマも実データも含まない（§9 の決定 2026-09-17）。
-- 受注管理の最小ドメインにしてあるのは、11 カテゴリすべてを 1 つのスキーマで書けるようにするため。
-- ScalarDB 側のキー設計は P0-3 が この DDL から起こす。

CREATE TABLE customers (
  customer_id   NUMBER(19)    NOT NULL,
  name          VARCHAR2(100) NOT NULL,
  email         VARCHAR2(200),
  tier          VARCHAR2(10)  DEFAULT 'BRONZE' NOT NULL,
  credit_limit  NUMBER(12,2),
  registered_on DATE          NOT NULL,
  CONSTRAINT pk_customers PRIMARY KEY (customer_id)
);

CREATE TABLE products (
  product_id  NUMBER(19)    NOT NULL,
  name        VARCHAR2(200) NOT NULL,
  unit_price  NUMBER(12,2)  NOT NULL,
  stock_qty   NUMBER(10)    DEFAULT 0 NOT NULL,
  discontinued CHAR(1)      DEFAULT 'N' NOT NULL,
  CONSTRAINT pk_products PRIMARY KEY (product_id)
);

CREATE TABLE orders (
  order_id    NUMBER(19)   NOT NULL,
  customer_id NUMBER(19)   NOT NULL,
  status      VARCHAR2(20) NOT NULL,
  ordered_at  DATE         NOT NULL,      -- Oracle DATE: 時刻成分を持つ
  shipped_at  TIMESTAMP(6),
  total_amount NUMBER(14,2),
  note        VARCHAR2(400),
  CONSTRAINT pk_orders PRIMARY KEY (order_id),
  CONSTRAINT fk_orders_customer FOREIGN KEY (customer_id) REFERENCES customers (customer_id)
);

CREATE INDEX ix_orders_customer ON orders (customer_id);
CREATE INDEX ix_orders_status   ON orders (status, ordered_at);

CREATE TABLE order_lines (
  order_id   NUMBER(19)   NOT NULL,
  line_no    NUMBER(5)    NOT NULL,
  product_id NUMBER(19)   NOT NULL,
  qty        NUMBER(10)   NOT NULL,
  unit_price NUMBER(12,2) NOT NULL,
  CONSTRAINT pk_order_lines PRIMARY KEY (order_id, line_no),
  CONSTRAINT fk_lines_order FOREIGN KEY (order_id) REFERENCES orders (order_id)
);

CREATE TABLE payments (
  payment_id NUMBER(19)   NOT NULL,
  order_id   NUMBER(19)   NOT NULL,
  amount     NUMBER(14,2) NOT NULL,
  method     VARCHAR2(20) NOT NULL,
  paid_at    TIMESTAMP(6) WITH TIME ZONE,
  CONSTRAINT pk_payments PRIMARY KEY (payment_id)
);

CREATE INDEX ix_payments_order ON payments (order_id);

CREATE TABLE inventory_tx (
  entry_id      NUMBER(19) NOT NULL,
  product_id NUMBER(19) NOT NULL,
  delta_qty  NUMBER(10) NOT NULL,
  reason     VARCHAR2(40),
  created_at DATE       NOT NULL,
  CONSTRAINT pk_inventory_tx PRIMARY KEY (entry_id)
);

CREATE TABLE audit_log (
  audit_id   NUMBER(19)    NOT NULL,
  table_name VARCHAR2(30)  NOT NULL,
  key_value  VARCHAR2(100) NOT NULL,
  action     VARCHAR2(10)  NOT NULL,
  old_value  VARCHAR2(400),
  new_value  VARCHAR2(400),
  changed_at TIMESTAMP(6)  NOT NULL,
  changed_by VARCHAR2(60)  NOT NULL,
  CONSTRAINT pk_audit_log PRIMARY KEY (audit_id)
);

-- 採番。ScalarDB に順序オブジェクトがないため、移行時は REDESIGN 判定になる（設計書 §6.4）
CREATE SEQUENCE seq_order_id  START WITH 1000 INCREMENT BY 1 NOCACHE;
CREATE SEQUENCE seq_payment_id START WITH 5000 INCREMENT BY 1 NOCACHE;
CREATE SEQUENCE seq_audit_id  START WITH 1 INCREMENT BY 1 CACHE 100;
CREATE SEQUENCE seq_tx_id     START WITH 1 INCREMENT BY 1 CACHE 100;

-- アプリ側採番の受け皿（同時更新・lock 依存カテゴリで使う）
CREATE TABLE counters (
  counter_name VARCHAR2(40) NOT NULL,
  next_value   NUMBER(19)   NOT NULL,
  CONSTRAINT pk_counters PRIMARY KEY (counter_name)
);

CREATE TABLE batch_control (
  batch_name  VARCHAR2(40) NOT NULL,
  last_run_at DATE,
  status      VARCHAR2(20) NOT NULL,
  CONSTRAINT pk_batch_control PRIMARY KEY (batch_name)
);
