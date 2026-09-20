CREATE OR REPLACE PACKAGE pkg_points AS
  -- 会員のポイント残高を返す。会員がいなければ -20101
  FUNCTION get_balance(p_member_id IN members.member_id%TYPE) RETURN NUMBER;

  -- ポイントを付与し、履歴を 1 行足し、残高に応じてランクを付け直す
  PROCEDURE add_points(p_member_id IN members.member_id%TYPE,
                       p_points    IN NUMBER,
                       p_reason    IN VARCHAR2);

  -- ポイントを使う。残高が足りなければ -20103。同時の利用は行ロックで直列にしている
  PROCEDURE use_points(p_member_id IN members.member_id%TYPE,
                       p_points    IN NUMBER,
                       p_reason    IN VARCHAR2);
END pkg_points;
/
