-- チュートリアル用の Oracle SQL（ポイントカード）。docs/guide/tutorial.md が、この 1 ファイルを sql-transpile で ScalarDB SQL に変換する。
-- 変換できる文、注意つきで変換できる文、実行計画になる文、自動では移せない文が、ひととおり入っている。

-- ---- DDL ----
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

CREATE SEQUENCE member_seq START WITH 1000;

-- ---- 書き込み ----
INSERT INTO members (member_id, name, rank, balance, last_seq, updated_at)
VALUES (1, 'Sato', 'REGULAR', 0, 0, DATE '2026-01-15');

INSERT INTO members (member_id, name, rank, balance, last_seq, updated_at)
VALUES (member_seq.NEXTVAL, 'Suzuki', 'REGULAR', 0, 0, SYSDATE);

UPDATE members SET rank = 'GOLD', updated_at = DATE '2026-02-01' WHERE member_id = 1;

UPDATE members SET balance = balance + 100 WHERE member_id = 1;

DELETE FROM point_history WHERE member_id = 1 AND seq_no = 3;

MERGE INTO members m
USING (SELECT 2 AS member_id, 'Tanaka' AS name FROM dual) s
   ON (m.member_id = s.member_id)
 WHEN MATCHED THEN UPDATE SET m.name = s.name
 WHEN NOT MATCHED THEN INSERT (member_id, name, rank, balance, last_seq) VALUES (s.member_id, s.name, 'REGULAR', 0, 0);

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
