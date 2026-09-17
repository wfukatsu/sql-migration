package com.scalar.migrate.plsql;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/**
 * 採番（計画 §9、2026-09-17）。
 *
 * <p>2 つの方式の違いは「1 回に何個取るか」だけだが、そこから出る性質が正反対である。hi/lo は DB へ行く
 * 回数が減る代わりに<b>欠番が出る</b>。counters は欠番が出ない代わりに<b>毎回同じ行を触る</b>。移行元の
 * DDL がどちらを選んでいたかは `CACHE` / `NOCACHE` に書いてあり、ここで確かめるのは<b>その性質が実際に
 * 出ること</b>である。
 *
 * <p>H2 で動かす。方式の性質を見るのに ScalarDB は要らず、要らないものを要求するテストは動かなくなる。
 */
class SequencesTest {
  private Connection connection;

  @BeforeEach
  void setUp() throws Exception {
    connection = open();
    try (Statement statement = connection.createStatement()) {
      statement.execute("DROP ALL OBJECTS");
      statement.execute("CREATE TABLE counters (counter_name VARCHAR(40) PRIMARY KEY, next_value BIGINT)");
      statement.execute("INSERT INTO counters VALUES ('seq_audit_id', 1)");
      statement.execute("INSERT INTO counters VALUES ('seq_order_id', 1000)");
    }
  }

  @AfterEach
  void tearDown() throws Exception {
    if (connection != null) connection.close();
  }

  private static Connection open() throws SQLException {
    return DriverManager.getConnection("jdbc:h2:mem:sequences;DB_CLOSE_DELAY=-1");
  }

  private CountersSequences sequences(Map<String, Integer> blocks) {
    return new CountersSequences(() -> {
      try {
        return open();
      } catch (SQLException e) {
        throw new IllegalStateException(e);
      }
    }, "counters", blocks);
  }

  private long stored(String name) throws Exception {
    try (Statement statement = connection.createStatement();
         var rows = statement.executeQuery(
             "SELECT next_value FROM counters WHERE counter_name = '" + name + "'")) {
      rows.next();
      return rows.getLong(1);
    }
  }

  // --- 共通の性質 ---------------------------------------------------------------------------------

  @Test
  void numbersDoNotRepeat() {
    Sequences numbering = sequences(Map.of("seq_audit_id", 100));
    List<Long> taken = new ArrayList<>();
    for (int i = 0; i < 250; i++) taken.add(numbering.next("seq_audit_id"));
    assertEquals(250, taken.stream().distinct().count(), "同じ番号を 2 度配った");
    assertTrue(taken.get(0) < taken.get(taken.size() - 1), "番号が単調増加していない");
  }

  @Test
  void numberingStartsWhereTheSourceLeftOff() {
    // 1 から始め直すのは移行ではなく破壊である
    assertEquals(1000L, sequences(Map.of("seq_order_id", 1)).next("seq_order_id"));
  }

  @Test
  void anUnknownSequenceIsRefusedRatherThanStartedAtOne() {
    IllegalArgumentException raised = assertThrows(IllegalArgumentException.class,
        () -> sequences(Map.of()).next("seq_nobody_configured"));
    assertTrue(raised.getMessage().contains("知らない sequence"));
  }

  @Test
  void aMissingCounterRowIsRefusedRatherThanCreated() throws Exception {
    try (Statement statement = connection.createStatement()) {
      statement.execute("DELETE FROM counters WHERE counter_name = 'seq_audit_id'");
    }
    IllegalStateException raised = assertThrows(IllegalStateException.class,
        () -> sequences(Map.of("seq_audit_id", 100)).next("seq_audit_id"));
    assertTrue(raised.getMessage().contains("移行時に初期値を入れること"));
  }

  // --- hi/lo: DB へ行く回数が減り、欠番が出る -----------------------------------------------------

  @Test
  void hiLoGoesToTheDatabaseOncePerBlock() throws Exception {
    Sequences numbering = sequences(Map.of("seq_audit_id", 100));
    for (int i = 0; i < 100; i++) numbering.next("seq_audit_id");
    assertEquals(101L, stored("seq_audit_id"), "100 個配るのに確保は 1 回のはず");
  }

  @Test
  void hiLoLosesTheUnusedPartOfItsBlock() throws Exception {
    // これが hi/lo の代償であり、移行元が CACHE 100 と書いていたのは、それを受け入れていたということ
    sequences(Map.of("seq_audit_id", 100)).next("seq_audit_id");   // 1 個しか使わない
    assertEquals(101L, stored("seq_audit_id"));

    Sequences afterRestart = sequences(Map.of("seq_audit_id", 100));
    assertEquals(101L, afterRestart.next("seq_audit_id"), "2 から 100 が捨てられている（欠番）");
  }

  // --- counters: 欠番が出ない代わりに毎回 DB へ行く -------------------------------------------------

  @Test
  void theCounterSchemeLeavesNoGap() throws Exception {
    Sequences numbering = sequences(Map.of("seq_order_id", 1));
    List<Long> taken = new ArrayList<>();
    for (int i = 0; i < 5; i++) taken.add(numbering.next("seq_order_id"));
    assertEquals(List.of(1000L, 1001L, 1002L, 1003L, 1004L), taken);

    // 再起動しても続きから。捨てる範囲を持っていないため
    assertEquals(1005L, sequences(Map.of("seq_order_id", 1)).next("seq_order_id"));
  }

  @Test
  void theCounterSchemeTouchesTheRowEveryTime() throws Exception {
    // だから採番行は高衝突点になり、業務トランザクションと同じにしてはいけない
    Sequences numbering = sequences(Map.of("seq_order_id", 1));
    for (int i = 0; i < 3; i++) numbering.next("seq_order_id");
    assertEquals(1003L, stored("seq_order_id"));
  }
}
