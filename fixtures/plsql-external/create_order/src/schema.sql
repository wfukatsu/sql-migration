-- create_order が使う表。**元の DDL は受け取っていない**: 列と型は routine の使い方から置いた推定である
-- （%TYPE の参照先、INSERT の列、採番）。本物の DDL が来たら置き換えて、取り直すこと。
CREATE TABLE customers (
  customer_id NUMBER(10) NOT NULL,
  name        VARCHAR2(100),
  CONSTRAINT pk_x_customers PRIMARY KEY (customer_id)
);

CREATE TABLE products (
  product_id     NUMBER(10)   NOT NULL,
  stock_quantity NUMBER(10)   NOT NULL,
  unit_price     NUMBER(12,2) NOT NULL,
  updated_at     TIMESTAMP,
  CONSTRAINT pk_x_products PRIMARY KEY (product_id)
);

CREATE TABLE orders (
  order_id     NUMBER(12)   NOT NULL,
  customer_id  NUMBER(10)   NOT NULL,
  order_date   DATE         NOT NULL,
  status       VARCHAR2(20) NOT NULL,
  total_amount NUMBER(18,2),
  CONSTRAINT pk_x_orders PRIMARY KEY (order_id)
);

CREATE TABLE order_items (
  order_id   NUMBER(12)   NOT NULL,
  line_no    NUMBER(4)    NOT NULL,
  product_id NUMBER(10)   NOT NULL,
  quantity   NUMBER(10)   NOT NULL,
  unit_price NUMBER(12,2) NOT NULL,
  CONSTRAINT pk_x_order_items PRIMARY KEY (order_id, line_no)
);

CREATE SEQUENCE order_seq START WITH 1 INCREMENT BY 1 NOCACHE;
