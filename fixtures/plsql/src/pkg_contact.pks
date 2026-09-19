-- カテゴリ: simple-crud（Issue #29 の 23。オーバーロード。実装と一緒に書いたので holdout ではない）
CREATE OR REPLACE PACKAGE pkg_contact AS
  PROCEDURE set_email(p_customer_id IN NUMBER, p_email IN VARCHAR2);
  PROCEDURE set_email(p_customer_id IN NUMBER, p_email IN VARCHAR2, p_name IN VARCHAR2);
  PROCEDURE clear_email(p_customer_id IN NUMBER);
END pkg_contact;
/
