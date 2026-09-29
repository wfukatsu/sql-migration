CREATE OR REPLACE PACKAGE pkg_library AS
  -- 会員がまだ返していない貸出の数
  FUNCTION active_loans(p_member_id IN loans.member_id%TYPE) RETURN NUMBER;

  -- 本を 1 冊貸す。本が無ければ -20201、貸出中なら -20202、5 冊を超えるなら -20203
  PROCEDURE lend_book(p_book_id   IN  books.book_id%TYPE,
                      p_member_id IN  loans.member_id%TYPE,
                      p_loan_id   OUT loans.loan_id%TYPE);

  -- 返却を記録し、本を棚に戻す。貸出が無いか返却済みなら -20204
  PROCEDURE return_book(p_loan_id IN loans.loan_id%TYPE);
END pkg_library;
/
