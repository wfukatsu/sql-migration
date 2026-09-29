CREATE OR REPLACE PACKAGE BODY pkg_library AS

  FUNCTION active_loans(p_member_id IN loans.member_id%TYPE) RETURN NUMBER IS
    v_count NUMBER;
  BEGIN
    SELECT COUNT(*) INTO v_count
      FROM loans
     WHERE member_id = p_member_id
       AND returned_at IS NULL;
    RETURN v_count;
  END active_loans;

  PROCEDURE lend_book(p_book_id   IN  books.book_id%TYPE,
                      p_member_id IN  loans.member_id%TYPE,
                      p_loan_id   OUT loans.loan_id%TYPE) IS
    v_status books.status%TYPE;
    v_now    DATE := SYSDATE;
  BEGIN
    -- 同じ本を 2 人に貸さないよう、本の行をロックしてから状態を見る
    BEGIN
      SELECT status INTO v_status FROM books WHERE book_id = p_book_id FOR UPDATE;
    EXCEPTION
      WHEN NO_DATA_FOUND THEN
        RAISE_APPLICATION_ERROR(-20201, '本が見つかりません');
    END;

    IF v_status <> 'ON_SHELF' THEN
      RAISE_APPLICATION_ERROR(-20202, '貸出中の本です');
    END IF;

    IF active_loans(p_member_id) >= 5 THEN
      RAISE_APPLICATION_ERROR(-20203, '貸出は 5 冊までです');
    END IF;

    p_loan_id := loan_seq.NEXTVAL;

    INSERT INTO loans (loan_id, book_id, member_id, lent_at, due_at)
    VALUES (p_loan_id, p_book_id, p_member_id, v_now, v_now + 14);

    UPDATE books
       SET status = 'LENT',
           updated_at = v_now
     WHERE book_id = p_book_id;
  END lend_book;

  PROCEDURE return_book(p_loan_id IN loans.loan_id%TYPE) IS
    v_book_id  loans.book_id%TYPE;
    v_returned loans.returned_at%TYPE;
    v_now      DATE := SYSDATE;
  BEGIN
    BEGIN
      SELECT book_id, returned_at INTO v_book_id, v_returned
        FROM loans
       WHERE loan_id = p_loan_id;
    EXCEPTION
      WHEN NO_DATA_FOUND THEN
        RAISE_APPLICATION_ERROR(-20204, '貸出が見つかりません');
    END;

    IF v_returned IS NOT NULL THEN
      RAISE_APPLICATION_ERROR(-20204, '返却済みの貸出です');
    END IF;

    UPDATE loans SET returned_at = v_now WHERE loan_id = p_loan_id;

    UPDATE books
       SET status = 'ON_SHELF',
           updated_at = v_now
     WHERE book_id = v_book_id;
  END return_book;

END pkg_library;
/
