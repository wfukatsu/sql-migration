-- points.sql の読み取り文を、実 DB（移行元の Oracle と ScalarDB Cluster）で突き合わせるためのケース（difftest/run.py の形式）。
-- 表の DDL と SELECT だけを入れる。データは points-check.data.json。
CREATE TABLE members (
  member_id  NUMBER(10)    NOT NULL,
  name       VARCHAR2(100) NOT NULL,
  rank       VARCHAR2(10)  DEFAULT 'REGULAR' NOT NULL,
  balance    NUMBER(10)    NOT NULL,
  last_seq   NUMBER(6)     NOT NULL,
  updated_at DATE,
  CONSTRAINT pk_members PRIMARY KEY (member_id)
);

CREATE TABLE point_history (
  member_id  NUMBER(10)    NOT NULL,
  seq_no     NUMBER(6)     NOT NULL,
  points     NUMBER(10)    NOT NULL,
  reason     VARCHAR2(100),
  created_at DATE          NOT NULL,
  CONSTRAINT pk_point_history PRIMARY KEY (member_id, seq_no)
);

CREATE INDEX idx_members_rank ON members (rank);

-- ---- 読み取り ----
SELECT member_id, name, balance FROM members WHERE member_id = 1;

SELECT seq_no, points, reason FROM point_history WHERE member_id = 1 AND seq_no >= 10 ORDER BY seq_no DESC;

SELECT member_id, name FROM members WHERE rank = 'GOLD';

-- ROWNUM は ORDER BY より先に効く（任意の 3 行を取ってから並べる）。変換後の LIMIT は並べたあとに効くので、結果が変わる
SELECT member_id, name, balance FROM members WHERE ROWNUM <= 3 ORDER BY balance DESC;

-- 「残高の多い 3 人」のつもりなら、Oracle でもこう書く。こちらは同じ結果になる
SELECT member_id, name, balance FROM members ORDER BY balance DESC FETCH FIRST 3 ROWS ONLY;

SELECT member_id, NVL(name, '(no name)') AS name, balance FROM members WHERE member_id = 1;

SELECT m.member_id, m.name, h.seq_no, h.points
  FROM members m, point_history h
 WHERE m.member_id = h.member_id(+)
   AND m.member_id = 1;

SELECT rank, COUNT(*) AS members, SUM(balance) AS total FROM members GROUP BY rank;

SELECT member_id, points,
       SUM(points) OVER (PARTITION BY member_id ORDER BY seq_no) AS running_total
  FROM point_history
 WHERE member_id = 1;

SELECT member_id, name, balance FROM members WHERE balance > (SELECT AVG(balance) FROM members);
