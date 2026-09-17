package com.scalar.migrate.plsql;

/**
 * 採番。ScalarDB に順序オブジェクトが無いので、生成コードはこの口を通す。
 *
 * <p><b>インタフェースにしてあるのは、採番がどのトランザクションで走るかを移行先が決めるべきだから</b>
 * である。生成コードはトランザクションを開始も commit もしない（計画 §9）ので、ここで実装を固定すると
 * その決定を生成器が奪うことになる。既定の実装は {@link CountersSequences} にあるが、既存の採番基盤が
 * あるならそれを繋げばよい。
 *
 * <p>方式は移行元の DDL から決まる（計画 §9、2026-09-17）。`CACHE n` は「停止時に未使用の n 個を捨てて
 * よい」と書いてあるのと同じなので hi/lo、`NOCACHE` は欠番を避けたいという意思表示なので counters 表 +
 * 再試行になる。
 */
public interface Sequences {

  /**
   * 次の番号。
   *
   * @param name 移行元の sequence 名（`seq_audit_id` など）。移行後も同じ名前で引く
   * @throws IllegalArgumentException 知らない sequence を引いたとき。黙って 1 から始めない——
   *     採番の連続性が壊れるのは、壊れたことに気づけないのが最も高くつく
   */
  long next(String name);
}
