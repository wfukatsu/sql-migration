package com.scalar.migrate.runtime;

import java.util.Map;

/**
 * Executes the fetch part of a plan through ScalarDB. Both implementations go through ScalarDB only.
 *
 * <p>A fetcher either <em>owns</em> its transaction (it opened one and must close it) or <em>joins</em> one the
 * caller already opened. Generated repository code joins, so that the plan's reads run inside the same transaction
 * as the routine's own writes and share its rollback scope. {@link PlanRunner} drives both cases.
 */
public interface Fetcher extends AutoCloseable {
  /**
   * True when this fetcher opened the transaction and is responsible for ending it. False when it joined a
   * transaction the caller owns, in which case {@link #begin()}, {@link #commit()} and {@link #rollback()} must not
   * be called and {@link #close()} leaves the caller's transaction alone.
   */
  default boolean ownsTransaction() {
    return true;
  }

  /** Begin one ScalarDB transaction; every fetch of a plan runs inside it (consistent snapshot). */
  void begin() throws Exception;

  Rows fetch(Plan.Fetch spec, Map<String, Object> params) throws Exception;

  void commit() throws Exception;

  void rollback();
}
