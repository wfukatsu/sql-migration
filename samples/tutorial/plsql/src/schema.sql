-- チュートリアル用の表（ポイントカード）。samples/tutorial/sql/points.sql の DDL と同じ 2 表。
CREATE TABLE members (
  member_id  NUMBER(10)    NOT NULL,
  name       VARCHAR2(100) NOT NULL,
  rank       VARCHAR2(10)  NOT NULL,
  balance    NUMBER(10)    NOT NULL,
  last_seq   NUMBER(6)     NOT NULL,
  updated_at DATE,
  CONSTRAINT pk_t_members PRIMARY KEY (member_id)
);

CREATE TABLE point_history (
  member_id  NUMBER(10)    NOT NULL,
  seq_no     NUMBER(6)     NOT NULL,
  points     NUMBER(10)    NOT NULL,
  reason     VARCHAR2(100),
  created_at DATE          NOT NULL,
  CONSTRAINT pk_t_point_history PRIMARY KEY (member_id, seq_no)
);
