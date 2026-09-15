package com.scalar.migrate.examples;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

/** Same fixed cases as difftest/golden/area-sales/setup.sql; expected values computed by hand below. */
class AreaSalesReportTest {
  static final String KICHI = "𠮷"; // U+20BB7

  static final Object[][] NODES = {
      {1, null, "本部"}, {2, 1, "関東"}, {3, 1, "関西"}, {4, 1, "閉鎖エリア"},
      {10, 2, "新宿店"}, {11, 2, "渋谷店"}, {12, 2, "ｱ店"}, {13, 2, KICHI + "野家前店"},
      {20, 3, "梅田店"}, {21, 3, "難波店"}, {5, 10, "新宿店直営コーナー"},
      {100, null, "第2本部"}, {101, 100, "九州"}, {110, 101, "博多店"},
      {999, 998, "孤立店"},
  };

  static final Object[][] SALES = {
      {10, "2026-01-15T10:00:00", 1000}, {10, "2026-02-10T10:00:00", 1200}, {10, "2026-04-01T10:00:00", 900},
      {10, "2025-12-31T23:59:59", 5000}, {10, "2026-12-31T23:59:59", 700}, {10, "2027-01-01T00:00:00", 800},
      {11, "2026-01-20T10:00:00", 1000}, {11, "2026-02-10T11:00:00", null}, {11, "2026-03-05T10:00:00", 300},
      {12, "2026-02-01T00:00:00", null}, {12, "2026-03-01T00:00:00", 500},
      {13, "2026-01-01T00:00:00", 10},
      {20, "2026-01-10T00:00:00", 32}, {20, "2026-02-10T00:00:00", 1}, {20, "2026-03-10T00:00:00", -32},
      {21, "2026-01-10T00:00:00", 5}, {21, "2026-02-10T00:00:00", -10},
      {2, "2026-01-01T00:00:00", 99999}, {5, "2026-01-01T00:00:00", 99999}, {999, "2026-01-01T00:00:00", 99999},
      {110, "2026-06-30T23:00:00", 100}, {110, "2026-07-01T00:00:00", 200},
  };

  /*
   * Level-3 shops: 10-13 under 関東, 20-21 under 関西, 110 under 九州. Excluded: 2 (level 2), 5 (level 4), 999 (orphan),
   * 2025-12-31 23:59:59 and 2027-01-01 00:00:00 (date range).
   *
   * shop 10: 01=1000 02=1200 04=900 12=700 (LAG is the previous ROW, so 04 compares with 02)
   *   mom: null, 1200/1000=120, 900/1200=75, 700/900=77.777..->77.78
   *   avg: 1000, 1100, 3100/3=1033.3->1033, 2800/3=933.3->933
   * shop 11: 01=1000 02=NULL (only a NULL amount) 03=300
   *   mom: null, NULL/1000=null, 300/NULL=null;  avg: 1000, 1000 (NULL ignored), 1300/2=650
   * shop 12: 02=NULL 03=500 -> mom null,null; avg null, 500
   * shop 13: 01=10 -> mom null; avg 10
   * shop 20: 01=32 02=1 03=-32 -> mom null, 3.125->3.13, -3200; avg 32, 16.5->17, 1/3->0
   * shop 21: 01=5 02=-10 -> mom null, -200; avg 5, -2.5->-3 (half away from zero)
   * shop 110: 06=100 07=200 -> mom null, 200; avg 100, 150
   *
   * DENSE_RANK by (parent, month), total DESC NULLS FIRST:
   *   関東 01: 10=1000,11=1000 ->1, 13=10 ->2 | 02: 11=NULL,12=NULL ->1, 10=1200 ->2 | 03: 12=500 ->1, 11=300 ->2
   *   関西 01: 20=32 ->1, 21=5 ->2 | 02: 20=1 ->1, 21=-10 ->2
   *
   * ORDER BY area_path (code points): 本 U+672C < 第 U+7B2C; 東 U+6771 < 西 U+897F;
   *   新 U+65B0 < 渋 U+6E0B < ｱ U+FF71 < 𠮷 U+20BB7 (String.compareTo would put 𠮷 before ｱ); 梅 U+6885 < 難 U+96E3
   */
  static final Object[][] EXPECTED = {
      {" > 本部 > 関東 > 新宿店", "新宿店", "2026-01", "1000", null, "1000", 1},
      {" > 本部 > 関東 > 新宿店", "新宿店", "2026-02", "1200", "120", "1100", 2},
      {" > 本部 > 関東 > 新宿店", "新宿店", "2026-04", "900", "75", "1033", 1},
      {" > 本部 > 関東 > 新宿店", "新宿店", "2026-12", "700", "77.78", "933", 1},
      {" > 本部 > 関東 > 渋谷店", "渋谷店", "2026-01", "1000", null, "1000", 1},
      {" > 本部 > 関東 > 渋谷店", "渋谷店", "2026-02", null, null, "1000", 1},
      {" > 本部 > 関東 > 渋谷店", "渋谷店", "2026-03", "300", null, "650", 2},
      {" > 本部 > 関東 > ｱ店", "ｱ店", "2026-02", null, null, null, 1},
      {" > 本部 > 関東 > ｱ店", "ｱ店", "2026-03", "500", null, "500", 1},
      {" > 本部 > 関東 > " + KICHI + "野家前店", KICHI + "野家前店", "2026-01", "10", null, "10", 2},
      {" > 本部 > 関西 > 梅田店", "梅田店", "2026-01", "32", null, "32", 1},
      {" > 本部 > 関西 > 梅田店", "梅田店", "2026-02", "1", "3.13", "17", 1},
      {" > 本部 > 関西 > 梅田店", "梅田店", "2026-03", "-32", "-3200", "0", 1},
      {" > 本部 > 関西 > 難波店", "難波店", "2026-01", "5", null, "5", 2},
      {" > 本部 > 関西 > 難波店", "難波店", "2026-02", "-10", "-200", "-3", 2},
      {" > 第2本部 > 九州 > 博多店", "博多店", "2026-06", "100", null, "100", 1},
      {" > 第2本部 > 九州 > 博多店", "博多店", "2026-07", "200", "200", "150", 1},
  };

  static Map<String, List<Map<String, Object>>> tables(Object[][] extraSales) {
    List<Map<String, Object>> org = new ArrayList<>();
    for (Object[] n : NODES) {
      Map<String, Object> r = new HashMap<>();
      r.put("node_id", new BigDecimal(n[0].toString()));
      r.put("parent_id", n[1] == null ? null : new BigDecimal(n[1].toString()));
      r.put("node_name", n[2]);
      org.add(r);
    }
    List<Map<String, Object>> sales = new ArrayList<>();
    long orderId = 1;
    List<Object[]> all = new ArrayList<>(List.of(SALES));
    all.addAll(List.of(extraSales));
    for (Object[] s : all) {
      Map<String, Object> r = new HashMap<>();
      r.put("shop_id", new BigDecimal(s[0].toString()));
      r.put("sales_date", LocalDateTime.parse((String) s[1]));
      r.put("order_id", BigDecimal.valueOf(orderId++));
      r.put("amount", s[2] == null ? null : new BigDecimal(s[2].toString()));
      sales.add(r);
    }
    Map<String, List<Map<String, Object>>> t = new LinkedHashMap<>();
    t.put("organization_master", org);
    t.put("sales_transactions", sales);
    return t;
  }

  static void num(String expected, Object actual, String what) {
    if (expected == null) assertNull(actual, what);
    else assertEquals(0, new BigDecimal(expected).compareTo((BigDecimal) actual), what + ": " + actual);
  }

  @Test
  void reproducesOracleResult() {
    List<Map<String, Object>> out = new AreaSalesReport().run(tables(new Object[0][]));
    assertEquals(EXPECTED.length, out.size());
    for (int i = 0; i < EXPECTED.length; i++) {
      Object[] e = EXPECTED[i];
      Map<String, Object> a = out.get(i);
      String at = "row " + i + " " + a;
      assertEquals(List.of("area_path", "shop_name", "sales_month", "total_amount", "mom_ratio_pct", "moving_avg_3m",
          "rank_in_area"), List.copyOf(a.keySet()));
      assertEquals(e[0], a.get("area_path"), at);
      assertEquals(e[1], a.get("shop_name"), at);
      assertEquals(e[2], a.get("sales_month"), at);
      num((String) e[3], a.get("total_amount"), at);
      num((String) e[4], a.get("mom_ratio_pct"), at);
      num((String) e[5], a.get("moving_avg_3m"), at);
      assertEquals(((Integer) e[6]).longValue(), a.get("rank_in_area"), at);
    }
  }

  @Test
  void previousMonthTotalZeroIsOra01476() {
    // shop 13: 2026-02 totals 10 + -10 = 0, so the 2026-03 ratio divides by zero (Oracle fails the whole query)
    Object[][] extra = {{13, "2026-02-01T00:00:00", 10}, {13, "2026-02-02T00:00:00", -10}, {13, "2026-03-01T00:00:00", 5}};
    ArithmeticException e = assertThrows(ArithmeticException.class, () -> new AreaSalesReport().run(tables(extra)));
    assertTrue(e.getMessage().contains("ORA-01476"));
  }
}
