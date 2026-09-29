-- クイックスタートの題材（図書の貸出）。このリポジトリの作者が書いた合成の SQL で、実在のシステムのものではない。
CREATE TABLE books (
  book_id    NUMBER(10)    PRIMARY KEY,
  title      VARCHAR2(200) NOT NULL,
  shelf      VARCHAR2(10)  NOT NULL,
  status     VARCHAR2(10)  NOT NULL,
  updated_at DATE
);
CREATE TABLE loans (
  loan_id     NUMBER(10) PRIMARY KEY,
  book_id     NUMBER(10) NOT NULL,
  member_id   NUMBER(10) NOT NULL,
  lent_at     DATE       NOT NULL,
  due_at      DATE       NOT NULL,
  returned_at DATE
);
CREATE INDEX idx_loans_member ON loans (member_id);
CREATE SEQUENCE loan_seq START WITH 1;
SELECT book_id, title, status FROM books WHERE book_id = :book_id;
SELECT loan_id, book_id, due_at FROM loans WHERE member_id = :member_id;
SELECT title, NVL(updated_at, DATE '2000-01-01') AS last_update FROM books WHERE shelf = 'A1';
SELECT b.title, l.due_at FROM books b JOIN loans l ON b.book_id = l.book_id WHERE l.returned_at IS NULL;
SELECT member_id, COUNT(*) AS n FROM loans WHERE returned_at IS NULL GROUP BY member_id HAVING COUNT(*) >= 3;
SELECT title FROM books WHERE shelf = 'A1' AND ROWNUM <= 10;
INSERT INTO books (book_id, title, shelf, status) VALUES (42, 'はじめての SQL', 'A1', 'ON_SHELF');
INSERT INTO loans (loan_id, book_id, member_id, lent_at, due_at) VALUES (loan_seq.NEXTVAL, 42, 7, SYSDATE, SYSDATE + 14);
UPDATE books SET status = 'LENT' WHERE book_id = 42;
UPDATE loans SET returned_at = SYSDATE WHERE member_id = 7 AND returned_at IS NULL;
DELETE FROM loans WHERE loan_id = 1001;
COMMIT;
