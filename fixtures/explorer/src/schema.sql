-- Migration Explorer の fixture。create_order のサンプルの表に、外部キー・UNIQUE・CHECK を足したもの。
-- 画面のテストが「制約」「つながるテーブル」「付いているもの」を確かめるのに使う。データは合成である。
CREATE TABLE customers (
  customer_id NUMBER(10)    NOT NULL,
  name        VARCHAR2(100) NOT NULL,
  email       VARCHAR2(200),
  CONSTRAINT pk_e_customers PRIMARY KEY (customer_id),
  CONSTRAINT uq_e_customers_email UNIQUE (email)
);

CREATE TABLE products (
  product_id     NUMBER(10)   NOT NULL,
  stock_quantity NUMBER(10)   NOT NULL,
  unit_price     NUMBER(12,2) NOT NULL,
  updated_at     TIMESTAMP,
  CONSTRAINT pk_e_products PRIMARY KEY (product_id),
  CONSTRAINT ck_e_products_stock CHECK (stock_quantity >= 0)
);

CREATE TABLE orders (
  order_id     NUMBER(12)   NOT NULL,
  customer_id  NUMBER(10)   NOT NULL,
  order_date   DATE         NOT NULL,
  status       VARCHAR2(20) NOT NULL,
  total_amount NUMBER(18,2),
  CONSTRAINT pk_e_orders PRIMARY KEY (order_id),
  CONSTRAINT fk_e_orders_customer FOREIGN KEY (customer_id) REFERENCES customers (customer_id)
);

CREATE TABLE order_items (
  order_id   NUMBER(12)   NOT NULL,
  line_no    NUMBER(4)    NOT NULL,
  product_id NUMBER(10)   NOT NULL,
  quantity   NUMBER(10)   NOT NULL,
  unit_price NUMBER(12,2) NOT NULL,
  CONSTRAINT pk_e_order_items PRIMARY KEY (order_id, line_no),
  CONSTRAINT fk_e_items_order FOREIGN KEY (order_id) REFERENCES orders (order_id) ON DELETE CASCADE,
  CONSTRAINT fk_e_items_product FOREIGN KEY (product_id) REFERENCES products (product_id),
  CONSTRAINT ck_e_items_quantity CHECK (quantity > 0)
);

CREATE TABLE shipments (
  shipment_id NUMBER(12)   NOT NULL,
  order_id    NUMBER(12)   NOT NULL,
  shipped_at  DATE,
  carrier     VARCHAR2(40),
  CONSTRAINT pk_e_shipments PRIMARY KEY (shipment_id),
  CONSTRAINT fk_e_shipments_order FOREIGN KEY (order_id) REFERENCES orders (order_id)
);

CREATE TABLE audit_log (
  log_id     NUMBER(12)    NOT NULL,
  table_name VARCHAR2(30)  NOT NULL,
  detail     VARCHAR2(400),
  logged_at  DATE          NOT NULL,
  CONSTRAINT pk_e_audit_log PRIMARY KEY (log_id)
);

CREATE SEQUENCE order_seq START WITH 100000 INCREMENT BY 1 NOCACHE;
CREATE SEQUENCE audit_seq START WITH 1 INCREMENT BY 1 NOCACHE;
