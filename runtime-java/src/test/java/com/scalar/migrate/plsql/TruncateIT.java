package com.scalar.migrate.plsql;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.junit.jupiter.api.condition.EnabledIfSystemProperty;

/**
 * `TRUNCATE TABLE` はトランザクションに入るか（2026-09-19、動的 SQL の許可表名で `truncate_staging` を
 * 動かしたときに測った）。
 *
 * <p>Oracle の TRUNCATE は DDL で、**直前の作業ごと暗黙に commit し、rollback できない**。移行先で同じ
 * 文を呼び出し側のトランザクションの中で走らせたとき、rollback で行が戻るのか戻らないのかは、
 * ドライバに聞く以外に確かめようがない。
 *
 * <pre>
 *   SCALARDB_IT=1 gradle test -Dplsql.generated=1 --tests '*TruncateIT*'
 * </pre>
 */
@EnabledIfEnvironmentVariable(named = "SCALARDB_IT", matches = "1")
@EnabledIfSystemProperty(named = "plsql.generated", matches = "1")
class TruncateIT {
  private ScalarDbRunner runner;

  @BeforeEach
  void setUp() throws Exception {
    Variant.assertGeneratedForThisVariant();
    runner = new ScalarDbRunner(Variant.PROPERTIES, Variant.NAMESPACE, Variant.SCHEMA);
    runner.reset();
    runner.execute("INSERT INTO inventory_tx (entry_id, product_id, delta_qty, reason, created_at) "
        + "VALUES (1, 10, 5, 'SEED', '2026-01-01 00:00:00')");
    runner.commit();
  }

  @AfterEach
  void tearDown() throws Exception {
    if (runner != null) runner.close();
  }

  @Test
  void whatARollbackDoesToATruncate() throws Exception {
    runner.execute("TRUNCATE TABLE inventory_tx");
    runner.rollback();
    List<Map<String, Object>> rows = runner.select("SELECT entry_id FROM inventory_tx");
    runner.commit();
    // 結果をそのまま記録する。0 なら Oracle と同じ（rollback で戻らない）、1 なら違う
    System.out.println("TRUNCATE then ROLLBACK leaves " + rows.size() + " row(s)");
    assertEquals(0, rows.size(),
        "TRUNCATE がトランザクションに入っている——rollback で行が戻った。Oracle では戻らない");
  }
}
