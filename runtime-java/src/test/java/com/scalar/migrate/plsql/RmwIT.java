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
 * #9 の RMW（`SET c = c + x`）を「読んでから書く」へ移してよいかを、**測ってから決める**。
 *
 * <p>Oracle の `UPDATE products SET stock_qty = stock_qty + r.qty` は行ロックの下で原子的である。
 * ScalarDB SQL は列を読む式を受け付けない（`ERROR RMW`）ので、置き換えるなら「同じトランザクションの
 * 中で読み、アプリで計算して書き戻す」しかない。そこで効いてくるのが次の 2 つで、どちらも
 * 仮定してはいけない:
 *
 * <ol>
 *   <li><b>自分の書き込みが読めるか。</b> 1 つのループが同じ行を 2 回触ったとき（注文明細に同じ
 *       商品が 2 行ある、など）、2 回目の読みが 1 回目の書き込みを見なければ、**1 つの
 *       トランザクションの中で更新が失われる**。
 *   <li><b>ロックが無くても衝突が検出されるか。</b> これは P3-4 で測ってある（`TransactionIT`）。
 * </ol>
 *
 * <pre>
 *   SCALARDB_IT=1 gradle test -Dplsql.generated=1 --tests '*RmwIT*'
 * </pre>
 */
@EnabledIfEnvironmentVariable(named = "SCALARDB_IT", matches = "1")
@EnabledIfSystemProperty(named = "plsql.generated", matches = "1")
class RmwIT {
  private ScalarDbRunner runner;

  @BeforeEach
  void setUp() throws Exception {
    Variant.assertGeneratedForThisVariant();
    runner = new ScalarDbRunner(Variant.PROPERTIES, Variant.NAMESPACE, Variant.SCHEMA);
    runner.reset();
  }

  @AfterEach
  void tearDown() throws Exception {
    if (runner != null) runner.close();
  }

  private void seedProduct(int id, long stock) throws Exception {
    runner.execute("INSERT INTO products (product_id, name, unit_price, stock_qty, discontinued) "
        + "VALUES (" + id + ", 'P" + id + "', " + Variant.money("10.00") + ", " + stock + ", 'N')");
  }

  private long stockOf(int id) throws Exception {
    List<Map<String, Object>> rows =
        runner.select("SELECT stock_qty FROM products WHERE product_id = " + id);
    return ((Number) rows.get(0).get("stock_qty")).longValue();
  }

  @Test
  void aReadSeesTheWriteTheSameTransactionMadeBefore() throws Exception {
    seedProduct(10, 100);
    runner.commit();

    runner.execute("UPDATE products SET stock_qty = 150 WHERE product_id = 10");
    long readBack = stockOf(10);
    runner.commit();

    assertEquals(150L, readBack,
        "同じトランザクションの中で、自分の書き込みが読めていない。"
            + "「読んでから書く」に移すと、同じ行を 2 回触ったときに更新が失われる");
  }

  @Test
  void twoIncrementsOfTheSameRowInOneTransactionBothCount() throws Exception {
    // 注文明細に同じ商品が 2 行あるとき、`cancel` は同じ行を 2 回戻す。Oracle は
    // `SET stock_qty = stock_qty + r.qty` を 2 回実行して両方数える
    seedProduct(10, 100);
    runner.commit();

    for (long delta : new long[] {3, 4}) {
      long current = stockOf(10);
      runner.execute("UPDATE products SET stock_qty = " + (current + delta) + " WHERE product_id = 10");
    }
    runner.commit();

    assertEquals(107L, stockOf(10), "2 回の加算のうち 1 回が失われている");
    runner.commit();
  }
}
