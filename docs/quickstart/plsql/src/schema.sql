-- クイックスタートの題材（図書の貸出）の表。このリポジトリの作者が書いた合成のもので、実在のシステムのものではない。
-- ../../library.sql の DDL と同じ 2 表と採番。
CREATE TABLE books (
  book_id    NUMBER(10)    NOT NULL,
  title      VARCHAR2(200) NOT NULL,
  shelf      VARCHAR2(10)  NOT NULL,
  status     VARCHAR2(10)  NOT NULL,
  updated_at DATE,
  CONSTRAINT pk_books PRIMARY KEY (book_id)
);

CREATE TABLE loans (
  loan_id     NUMBER(10) NOT NULL,
  book_id     NUMBER(10) NOT NULL,
  member_id   NUMBER(10) NOT NULL,
  lent_at     DATE       NOT NULL,
  due_at      DATE       NOT NULL,
  returned_at DATE,
  CONSTRAINT pk_loans PRIMARY KEY (loan_id)
);

CREATE INDEX idx_loans_member ON loans (member_id);

CREATE SEQUENCE loan_seq START WITH 1;
