-- CUR-001 の見本。明示 cursor のうち、**どの書き換えの形にも当たらない**もの。
-- 2026-09-18 に「読むだけのループ」（A）を cursor FOR ループへ書き換えるようになり、corpus の
-- 明示 cursor はすべてそちらへ移った。規則は残す——当たらない形は実案件にいくらでもあり、
-- それは今も人が見るべきものである。ここは 1 反復に 2 回 FETCH するので、行を配るループでは
-- 同じにならない。
CREATE OR REPLACE PROCEDURE prc_pairwise_lines(p_total OUT NUMBER) IS
  CURSOR c IS SELECT qty FROM order_lines ORDER BY order_id, line_no;
  v_first  NUMBER;
  v_second NUMBER;
BEGIN
  p_total := 0;
  OPEN c;
  LOOP
    FETCH c INTO v_first;
    EXIT WHEN c%NOTFOUND;
    FETCH c INTO v_second;
    EXIT WHEN c%NOTFOUND;
    p_total := p_total + v_first * v_second;
  END LOOP;
  CLOSE c;
END prc_pairwise_lines;
/
