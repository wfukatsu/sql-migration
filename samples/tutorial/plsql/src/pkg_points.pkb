CREATE OR REPLACE PACKAGE BODY pkg_points AS

  FUNCTION rank_of(p_balance IN NUMBER) RETURN VARCHAR2 IS
  BEGIN
    IF p_balance >= 1000 THEN
      RETURN 'GOLD';
    ELSIF p_balance >= 300 THEN
      RETURN 'SILVER';
    END IF;
    RETURN 'REGULAR';
  END rank_of;

  FUNCTION get_balance(p_member_id IN members.member_id%TYPE) RETURN NUMBER IS
    v_balance members.balance%TYPE;
  BEGIN
    SELECT balance INTO v_balance FROM members WHERE member_id = p_member_id;
    RETURN v_balance;
  EXCEPTION
    WHEN NO_DATA_FOUND THEN
      RAISE_APPLICATION_ERROR(-20101, '会員が見つかりません');
  END get_balance;

  PROCEDURE add_points(p_member_id IN members.member_id%TYPE,
                       p_points    IN NUMBER,
                       p_reason    IN VARCHAR2) IS
    v_balance members.balance%TYPE;
    v_seq     members.last_seq%TYPE;
    v_rank    members.rank%TYPE;
    v_now     DATE := SYSDATE;
  BEGIN
    IF p_points <= 0 THEN
      RAISE_APPLICATION_ERROR(-20102, '付与するポイントは1以上で指定してください');
    END IF;

    BEGIN
      SELECT balance, last_seq INTO v_balance, v_seq FROM members WHERE member_id = p_member_id;
    EXCEPTION
      WHEN NO_DATA_FOUND THEN
        RAISE_APPLICATION_ERROR(-20101, '会員が見つかりません');
    END;

    v_balance := v_balance + p_points;
    v_seq := v_seq + 1;
    v_rank := rank_of(v_balance);

    INSERT INTO point_history (member_id, seq_no, points, reason, created_at)
    VALUES (p_member_id, v_seq, p_points, p_reason, v_now);

    UPDATE members
       SET balance = v_balance,
           last_seq = v_seq,
           rank = v_rank,
           updated_at = v_now
     WHERE member_id = p_member_id;
  END add_points;

  PROCEDURE use_points(p_member_id IN members.member_id%TYPE,
                       p_points    IN NUMBER,
                       p_reason    IN VARCHAR2) IS
    v_balance members.balance%TYPE;
    v_seq     members.last_seq%TYPE;
    v_now     DATE := SYSDATE;
  BEGIN
    IF p_points <= 0 THEN
      RAISE_APPLICATION_ERROR(-20102, '使うポイントは1以上で指定してください');
    END IF;

    -- 同時に使われても残高がマイナスにならないよう、会員の行をロックする
    SELECT balance, last_seq INTO v_balance, v_seq
      FROM members
     WHERE member_id = p_member_id
       FOR UPDATE;

    IF v_balance < p_points THEN
      RAISE_APPLICATION_ERROR(-20103, 'ポイントが不足しています');
    END IF;

    v_seq := v_seq + 1;

    INSERT INTO point_history (member_id, seq_no, points, reason, created_at)
    VALUES (p_member_id, v_seq, -p_points, p_reason, v_now);

    -- 使ってもランクは下げない（業務ルール）
    UPDATE members
       SET balance = v_balance - p_points,
           last_seq = v_seq,
           updated_at = v_now
     WHERE member_id = p_member_id;
  END use_points;

END pkg_points;
/
