-- カテゴリ: simple-crud（Issue #29 の 23。オーバーロード。実装と一緒に書いたので holdout ではない）
CREATE OR REPLACE PACKAGE BODY pkg_contact AS

  PROCEDURE set_email(p_customer_id IN NUMBER, p_email IN VARCHAR2) IS
  BEGIN
    UPDATE customers SET email = p_email WHERE customer_id = p_customer_id;
  END set_email;

  PROCEDURE set_email(p_customer_id IN NUMBER, p_email IN VARCHAR2, p_name IN VARCHAR2) IS
  BEGIN
    UPDATE customers SET email = p_email, name = p_name WHERE customer_id = p_customer_id;
  END set_email;

  PROCEDURE clear_email(p_customer_id IN NUMBER) IS
  BEGIN
    -- 引数の数で 1 つ目の版に決まる
    set_email(p_customer_id, NULL);
  END clear_email;

END pkg_contact;
/
