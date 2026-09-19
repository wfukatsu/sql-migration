package com.scalar.migrate.plsql;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.lang.reflect.Constructor;
import java.lang.reflect.Method;
import java.math.BigDecimal;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.concurrent.atomic.AtomicLong;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.junit.jupiter.api.condition.EnabledIfSystemProperty;

/**
 * #12 §0「検証で追う」の照合を、実クラスタで確かめる（2026-09-19）。
 *
 * <p>生成したコードを通さずに表へ直接書き（移行先の trigger が掛からない経路）、生成した照合
 * （`TriggerChecks`）がそれを見つけるかを見る。補完（A）が、拒否されるはずの値（B）を監査済みに
 * 見せないことも見る——補うと「最後に監査した値」が今の値になり、拒否の照合から消えるからである。
 *
 * <pre>
 *   python difftest/plsql_capture.py --variant scaled   # generated/ を作る
 *   SCALARDB_IT=1 gradle test --rerun -Dplsql.generated=1 --tests '*TriggerChecksIT*'
 * </pre>
 */
@EnabledIfEnvironmentVariable(named = "SCALARDB_IT", matches = "1")
@EnabledIfSystemProperty(named = "plsql.generated", matches = "1")
class TriggerChecksIT {
  private static final String PACKAGE = "com.example.migrated";
  private ScalarDbRunner runner;
  private Object checks;

  @BeforeEach
  void setUp() throws Exception {
    Variant.assertGeneratedForThisVariant();
    runner = new ScalarDbRunner(Variant.PROPERTIES, Variant.NAMESPACE, Variant.SCHEMA);
    runner.reset();
    for (String sql : List.of(
        "INSERT INTO customers (customer_id, name, tier) VALUES (1, 'A', 'GOLD')",
        "INSERT INTO orders (order_id, customer_id, status) VALUES (1001, 1, 'NEW')",
        "INSERT INTO orders (order_id, customer_id, status) VALUES (1002, 1, 'CANCELLED')",
        "INSERT INTO payments (payment_id, order_id, amount, method) VALUES (5001, 1002, 100, 'CARD')",
        // 価格は scaled の規約で整数（NUMBER(12,2) -> 100 倍）
        "INSERT INTO products (product_id, name, unit_price, stock_qty, discontinued) VALUES (10, 'W', 10000, 5, 'N')",
        "INSERT INTO products (product_id, name, unit_price, stock_qty, discontinued) VALUES (20, 'G', 5000, 5, 'N')",
        "INSERT INTO audit_log (audit_id, table_name, key_value, action, old_value, new_value, changed_at, changed_by) "
            + "VALUES (1, 'ORDERS', '1001', 'UPDATE', NULL, 'NEW', '2026-01-01 00:00:00', 'SOURCE')",
        "INSERT INTO audit_log (audit_id, table_name, key_value, action, old_value, new_value, changed_at, changed_by) "
            + "VALUES (2, 'PRODUCTS', '10', 'PRICE', '120', '100', '2026-01-01 00:00:00', 'SOURCE')",
        "INSERT INTO audit_log (audit_id, table_name, key_value, action, old_value, new_value, changed_at, changed_by) "
            + "VALUES (3, 'PRODUCTS', '20', 'PRICE', '60', '50', '2026-01-01 00:00:00', 'SOURCE')")) {
      runner.execute(sql);
    }
    runner.commit();
    // 生成したコードを通らない直接の書き込み（移行先の trigger は掛からない）
    runner.execute("UPDATE orders SET status = 'SHIPPED' WHERE order_id = 1001");
    runner.execute("UPDATE products SET unit_price = 1000 WHERE product_id = 10");   // 100 -> 10: 90% 下げ
    runner.execute("UPDATE products SET unit_price = 4500 WHERE product_id = 20");   // 50 -> 45: 10% 下げ
    runner.commit();
    checks = build();
  }

  @AfterEach
  void tearDown() throws Exception {
    if (runner != null) runner.close();
  }

  private Object build() throws Exception {
    AtomicLong next = new AtomicLong(100);
    Sequences sequences = name -> next.getAndIncrement();
    Class<?> repository = Class.forName(PACKAGE + ".infrastructure.TrgPaymentsGuardRepository");
    Object guard = Class.forName(PACKAGE + ".application.TrgPaymentsGuardService")
        .getConstructor(repository).newInstance(newRepository(repository, sequences));
    Class<?> type = Class.forName(PACKAGE + ".application.TriggerChecks");
    Constructor<?> constructor = type.getConstructors()[0];
    return constructor.newInstance(runner.connection(), sequences, guard);
  }

  private Object newRepository(Class<?> type, Sequences sequences) throws Exception {
    for (Constructor<?> constructor : type.getConstructors()) {
      if (constructor.getParameterCount() == 1) return constructor.newInstance(runner.connection());
      if (constructor.getParameterCount() == 2) return constructor.newInstance(runner.connection(), sequences);
    }
    throw new IllegalStateException(type.getName());
  }

  @SuppressWarnings("unchecked")
  private Map<String, String> drifts(String method) throws Exception {
    List<Object> found = (List<Object>) checks.getClass().getMethod(method).invoke(checks);
    runner.commit();
    Map<String, String> out = new TreeMap<>();
    for (Object drift : found) {
      String key = (String) drift.getClass().getMethod("key").invoke(drift);
      out.put(key, (String) drift.getClass().getMethod("kind").invoke(drift));
    }
    return out;
  }

  private int backfill(String trigger, String unaudited) throws Exception {
    Object found = checks.getClass().getMethod(unaudited).invoke(checks);
    Method backfill = checks.getClass().getMethod(trigger + "Backfill", List.class, AuditContext.class);
    AuditContext audit = AuditContext.of("OPS", OffsetDateTime.of(2026, 1, 20, 0, 0, 0, 0, ZoneOffset.UTC));
    int written = (int) backfill.invoke(checks, found, audit);
    runner.commit();
    return written;
  }

  @Test
  void anUnauditedStatusChangeIsFoundAndBackfilled() throws Exception {
    assertEquals(Map.of("1001", "A"), drifts("trgOrdersAuditUnaudited"));
    assertEquals(1, backfill("trgOrdersAudit", "trgOrdersAuditUnaudited"));
    assertEquals(Map.of(), drifts("trgOrdersAuditUnaudited"), "補ったあとも、ずれが残っている");
    List<Map<String, Object>> rows = runner.select(
        "SELECT changed_by, new_value FROM audit_log WHERE table_name = 'ORDERS' AND key_value = '1001' "
            + "AND changed_by = 'BACKFILL'");
    runner.commit();
    assertEquals(1, rows.size(), "補った行に印が付いていない");
    assertEquals("SHIPPED", rows.get(0).get("new_value"));
  }

  @Test
  void aRejectedPriceIsFoundAndNotLaunderedByTheBackfill() throws Exception {
    assertEquals(Map.of("10", "A", "20", "A"), drifts("trgProductsAuditUnaudited"));
    assertEquals(Map.of("10", "B"), drifts("trgProductsAuditRejected"), "50% を超える値下げを見つけていない");
    // 補うのは 20（10% 下げ）だけ。10 を補うと、拒否されるはずの値が監査済みに見える
    assertEquals(1, backfill("trgProductsAudit", "trgProductsAuditUnaudited"));
    assertEquals(Map.of("10", "A"), drifts("trgProductsAuditUnaudited"));
    assertEquals(Map.of("10", "B"), drifts("trgProductsAuditRejected"), "補完が拒否の証拠を消した");
  }

  @Test
  void aPaymentOnACancelledOrderIsFound() throws Exception {
    assertEquals(Map.of("5001", "D"), drifts("trgPaymentsGuardViolations"));
  }

  @Test
  void theLargestKeyInUseIsReported() throws Exception {
    Object max = checks.getClass().getMethod("trgOrdersSeqMaxKey").invoke(checks);
    runner.commit();
    assertEquals(0, new BigDecimal("1002").compareTo((BigDecimal) max));
  }
}
