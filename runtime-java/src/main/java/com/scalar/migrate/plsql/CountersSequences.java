package com.scalar.migrate.plsql;

import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.util.HashMap;
import java.util.Map;
import java.util.function.Supplier;

/**
 * `counters` 表を使う既定の採番。hi/lo と counters + 再試行の両方をこの 1 表で賄う。
 *
 * <p><b>採番は業務トランザクションとは別のトランザクションで走る。</b> 同じトランザクションに入れると、
 * 採番行が高衝突点になって業務の更新まで巻き込む（P3-4 で測ったとおり、Consensus Commit は待たずに弾く）。
 * だから接続は呼び出し側から {@code Supplier} で受け取り、1 回の確保ごとに開いて閉じる。
 *
 * <p>2 つの方式の違いは、1 回の確保で何個取るかだけである。
 *
 * <ul>
 *   <li><b>hi/lo</b>（{@code block > 1}）: まとめて確保してメモリから配る。DB へ行くのは block 回に
 *       1 度なので衝突が激減する。代償は<b>欠番</b>——プロセスが落ちると未使用分が捨てられる。
 *       移行元が {@code CACHE n} と書いていた sequence は、元からその性質を受け入れている。
 *   <li><b>counters + 再試行</b>（{@code block == 1}）: 毎回 1 個ずつ確保する。欠番は出ないが、
 *       採番のたびに同じ行を触るので<b>衝突しやすい</b>。移行元が {@code NOCACHE} と書いていたもの。
 * </ul>
 *
 * <p>ここでの再試行は採番トランザクションだけのもので、業務トランザクションの再試行
 * （use case 境界が持つ、計画 §9）とは別物である。
 */
public final class CountersSequences implements Sequences {
  /** 採番の衝突は普通に起きる。回数は控えめで、超えたら諦めて呼び出し側へ返す。 */
  static final int MAX_ATTEMPTS = 10;

  private final Supplier<Connection> connections;
  private final String table;
  private final Map<String, Integer> blocks;
  private final Map<String, long[]> held = new HashMap<>();   // name -> {次に配る値, この範囲の終わり}

  /**
   * @param connections 採番専用の接続を返すもの。呼ばれるたびに新しいものを返すこと——
   *     業務トランザクションの接続を渡すと、分けた意味が無くなる
   * @param blocks sequence 名 -> 1 回に確保する個数。1 なら counters + 再試行
   */
  public CountersSequences(Supplier<Connection> connections, String table, Map<String, Integer> blocks) {
    this.connections = connections;
    this.table = table;
    this.blocks = Map.copyOf(blocks);
  }

  @Override
  public synchronized long next(String name) {
    Integer block = blocks.get(name);
    if (block == null) {
      throw new IllegalArgumentException(
          "知らない sequence: " + name + "。移行元の DDL から導いた方式を渡していない可能性がある。"
              + "黙って 1 から始めると、採番の連続性が壊れたことに気づけない");
    }
    long[] range = held.get(name);
    if (range != null && range[0] < range[1]) {
      return range[0]++;
    }
    long from = allocate(name, block);
    held.put(name, new long[] {from + 1, from + block});
    return from;
  }

  /** `next_value` を block だけ進め、進める前の値を返す。衝突したら再試行する。 */
  private long allocate(String name, int block) {
    RuntimeException last = null;
    for (int attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
      try (Connection connection = connections.get()) {
        connection.setAutoCommit(false);
        long current = read(connection, name);
        update(connection, name, current + block);
        connection.commit();
        return current;
      } catch (NotRetryable e) {
        // 行が無いのは衝突ではない。10 回試しても行は現れず、本当の理由が再試行の失敗に化けるだけである
        throw e.getCause() instanceof RuntimeException r ? r : new IllegalStateException(e.getMessage(), e);
      } catch (Exception e) {
        // 衝突なら別のトランザクションが同じ行を取った。読み直して取り直す
        last = e instanceof RuntimeException r ? r : new IllegalStateException(e);
      }
    }
    throw new IllegalStateException(
        name + ": " + MAX_ATTEMPTS + " 回試しても採番できなかった。採番行が高衝突点になっている可能性が"
            + "ある（NOCACHE の sequence は 1 個ずつしか取らない）", last);
  }

  private long read(Connection connection, String name) throws Exception {
    try (PreparedStatement statement = connection.prepareStatement(
        "SELECT next_value FROM " + table + " WHERE counter_name = ?")) {
      statement.setString(1, name);
      try (ResultSet rows = statement.executeQuery()) {
        if (!rows.next()) {
          throw new NotRetryable(new IllegalStateException(
              table + " に " + name + " の行が無い。移行時に初期値を入れること——"
                  + "無い状態で 1 から始めると、既存の番号と衝突する"));
        }
        return ((Number) rows.getObject(1)).longValue();
      }
    }
  }

  /** 再試行しても結果が変わらない失敗。衝突と混ぜると、本当の理由が「10 回失敗した」に化ける。 */
  private static final class NotRetryable extends Exception {
    NotRetryable(Throwable cause) {
      super(cause.getMessage(), cause);
    }
  }

  private void update(Connection connection, String name, long value) throws Exception {
    try (PreparedStatement statement = connection.prepareStatement(
        "UPDATE " + table + " SET next_value = ? WHERE counter_name = ?")) {
      statement.setLong(1, value);
      statement.setString(2, name);
      statement.executeUpdate();
    }
  }
}
