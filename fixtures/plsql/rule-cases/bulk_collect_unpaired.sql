-- 配列へ読むだけで、その配列を FORALL で回さない BULK COLLECT。#14 の書き換えは
-- 「SELECT ... BULK COLLECT INTO」と「その配列を回す FORALL」の**組**だけを走査ループにするので、
-- この形は書き換えられずに残り、BULK-001（行数上限とメモリ上限）が引き続き捕まえる。
CREATE OR REPLACE PROCEDURE prc_bulk_collect_unpaired(p_order_id IN NUMBER, p_first OUT NUMBER) IS
  TYPE t_qty_list IS TABLE OF NUMBER INDEX BY PLS_INTEGER;
  v_qtys t_qty_list;
BEGIN
  SELECT qty BULK COLLECT INTO v_qtys FROM order_lines WHERE order_id = p_order_id;
  IF v_qtys.COUNT > 0 THEN
    p_first := v_qtys(1);
  END IF;
END prc_bulk_collect_unpaired;
/
