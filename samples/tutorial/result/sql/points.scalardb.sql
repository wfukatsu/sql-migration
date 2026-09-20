-- converted to scalardb
CREATE TABLE members (
  member_id BIGINT PRIMARY KEY,
  name TEXT,
  rank TEXT,
  balance BIGINT,
  last_seq INT,
  updated_at DATE
);

CREATE TABLE point_history (
  member_id BIGINT,
  seq_no INT,
  points BIGINT,
  reason TEXT,
  created_at DATE,
  PRIMARY KEY (member_id, seq_no)
);

CREATE INDEX ON members (rank);

-- [NOT CONVERTED #4] CREATE SEQUENCE is not supported (no views, sequences, triggers, procedures in ScalarDB)
-- CREATE SEQUENCE member_seq START WITH 1000

/* ---- 書き込み ---- */ INSERT INTO members (member_id, name, rank, balance, last_seq, updated_at) VALUES (1, 'Sato', 'REGULAR', 0, 0, '2026-01-15');

-- [NOT CONVERTED #6] VALUES column member_id: sequences are not supported; generate keys in the application (e.g. UUID)
-- INSERT INTO members (member_id, name, rank, balance, last_seq, updated_at)
-- VALUES (member_seq.NEXTVAL, 'Suzuki', 'REGULAR', 0, 0, SYSDATE)

UPDATE members SET rank = 'GOLD', updated_at = '2026-02-01' WHERE member_id = 1;

-- [NOT CONVERTED #8] SET balance = balance + 100: expressions referencing columns are not allowed; do SELECT -> compute -> UPDATE with a literal inside one ScalarDB transaction
-- UPDATE members SET balance = balance + 100 WHERE member_id = 1

DELETE FROM point_history WHERE member_id = 1 AND seq_no = 3;

UPSERT INTO members (member_id, name, rank, balance, last_seq) VALUES (2, 'Tanaka', 'REGULAR', 0, 0);

/* ---- 読み取り ---- */ SELECT member_id, name, balance FROM members WHERE member_id = 1;

SELECT seq_no, points, reason FROM point_history WHERE member_id = 1 AND seq_no >= 10 ORDER BY seq_no DESC;

SELECT member_id, name FROM members WHERE rank = 'GOLD';

/* ROWNUM は ORDER BY より先に効く（任意の 3 行を取ってから並べる）。変換後の LIMIT は並べたあとに効くので、結果が変わる */ SELECT member_id, name, balance FROM members ORDER BY balance DESC LIMIT 3;

/* 「残高の多い 3 人」のつもりなら、Oracle でもこう書く。こちらは同じ結果になる */ SELECT member_id, name, balance FROM members ORDER BY balance DESC LIMIT 3;

-- [APP-SIDE PLAN #16] ScalarDB から取得して H2 で実行する
--   SELECT member_id, name, balance FROM members WHERE member_id = 1;
-- SELECT member_id, NVL(name, '(no name)') AS name, balance FROM members WHERE member_id = 1

-- [APP-SIDE PLAN #17] ScalarDB から取得して H2 で実行する
--   SELECT member_id, name FROM members WHERE member_id = 1;
--   SELECT member_id, seq_no, points FROM point_history;
-- SELECT m.member_id, m.name, h.seq_no, h.points
--   FROM members m, point_history h
--  WHERE m.member_id = h.member_id(+)
--    AND m.member_id = 1

SELECT rank, COUNT(*) AS members, SUM(balance) AS total FROM members GROUP BY rank;

-- [APP-SIDE PLAN #19] ScalarDB から取得して H2 で実行する
--   SELECT member_id, seq_no, points FROM point_history WHERE member_id = 1;
-- SELECT member_id, points,
--        SUM(points) OVER (PARTITION BY member_id ORDER BY seq_no) AS running_total
--   FROM point_history
--  WHERE member_id = 1

-- [APP-SIDE PLAN #20] ScalarDB から取得して H2 で実行する
--   SELECT member_id, name, balance FROM members;
-- SELECT member_id, name, balance FROM members WHERE balance > (SELECT AVG(balance) FROM members)
