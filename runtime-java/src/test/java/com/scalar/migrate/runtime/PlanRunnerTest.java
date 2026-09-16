package com.scalar.migrate.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

/**
 * P2-9: the transaction lifecycle, without a database.
 *
 * <p>What matters for generated repository code is who ends the transaction. A joining fetcher must never begin,
 * commit, roll back or close the caller's transaction -- otherwise a plan buried inside a routine would silently
 * commit the routine's own writes half-way through.
 */
class PlanRunnerTest {

  /** Records the lifecycle calls a runner makes, and serves one canned row. */
  private static final class RecordingFetcher implements Fetcher {
    final List<String> calls = new ArrayList<>();
    final boolean owns;
    Exception failWith;

    RecordingFetcher(boolean owns) {
      this.owns = owns;
    }

    @Override public boolean ownsTransaction() { return owns; }
    @Override public void begin() { calls.add("begin"); }
    @Override public void commit() { calls.add("commit"); }
    @Override public void rollback() { calls.add("rollback"); }
    @Override public void close() { calls.add("close"); }

    @Override
    public Rows fetch(Plan.Fetch spec, Map<String, Object> params) throws Exception {
      calls.add("fetch:" + spec.table);
      if (failWith != null) throw failWith;
      Rows r = new Rows();
      r.columns.add("id");
      r.types.put("id", "INT");
      r.rows.add(new Object[] {1});
      return r;
    }
  }

  private static Plan plan() {
    Plan p = new Plan();
    Plan.Fetch f = new Plan.Fetch();
    f.table = "orders";
    f.column_types = Map.of("id", "INT");
    f.columns = List.of("id");
    f.max_rows = 100;
    p.fetch = List.of(f);
    Plan.Residual r = new Plan.Residual();
    r.engine = "h2";
    r.mode = "Oracle";
    r.sql = "SELECT id FROM orders";
    p.residual = Map.of("java", r);
    p.source_sql = "SELECT id FROM orders";
    return p;
  }

  @Test
  void owningFetcherBeginsAndCommitsItsOwnTransaction() throws Exception {
    RecordingFetcher f = new RecordingFetcher(true);
    PlanRunner.Result result = PlanRunner.execute(plan(), f, Map.of());
    assertEquals(List.of("begin", "fetch:orders", "commit"), f.calls);
    assertEquals(1, result.fetchedRows);
    assertEquals(List.of("id"), result.columns());
  }

  @Test
  void joiningFetcherNeverTouchesTheCallersTransaction() throws Exception {
    RecordingFetcher f = new RecordingFetcher(false);
    PlanRunner.Result result = PlanRunner.execute(plan(), f, Map.of());
    assertEquals(List.of("fetch:orders"), f.calls, "begin/commit belong to the caller");
    assertEquals(1, result.fetchedRows);
  }

  @Test
  void owningFetcherRollsBackOnFailure() {
    RecordingFetcher f = new RecordingFetcher(true);
    f.failWith = new IllegalStateException("boom");
    assertThrows(IllegalStateException.class, () -> PlanRunner.execute(plan(), f, Map.of()));
    assertEquals(List.of("begin", "fetch:orders", "rollback"), f.calls);
  }

  @Test
  void joiningFetcherLetsTheFailurePropagateWithoutRollingBack() {
    RecordingFetcher f = new RecordingFetcher(false);
    f.failWith = new IllegalStateException("boom");
    assertThrows(IllegalStateException.class, () -> PlanRunner.execute(plan(), f, Map.of()));
    assertEquals(List.of("fetch:orders"), f.calls, "the caller's rollback covers its writes and this read");
  }

  @Test
  void scanOverOwnWriteIsReportedAsItsOwnFailure() {
    RecordingFetcher f = new RecordingFetcher(false);
    // the message ScalarDB 3.19.1 raises (CoreError CONSENSUS_COMMIT_SCANNING_ALREADY_WRITTEN_OR_DELETED_DATA_NOT_ALLOWED)
    f.failWith = new IllegalArgumentException(
        "Scanning data already-written or already-deleted by the same transaction is not allowed");
    ScanAfterWriteException e =
        assertThrows(ScanAfterWriteException.class, () -> PlanRunner.execute(plan(), f, Map.of()));
    assertEquals("orders", e.table());
    assertTrue(e.getMessage().contains("already written"));
  }

  @Test
  void joiningFetchersRefuseToEndTheTransaction() throws Exception {
    java.sql.Connection conn = java.sql.DriverManager.getConnection("jdbc:h2:mem:p29;DB_CLOSE_DELAY=-1");
    try {
      JdbcFetcher f = JdbcFetcher.joining(conn);
      assertFalse(f.ownsTransaction());
      assertThrows(IllegalStateException.class, f::commit);
      assertThrows(IllegalStateException.class, f::rollback);
      f.close();
      assertFalse(conn.isClosed(), "close() must leave the caller's connection open");
    } finally {
      conn.close();
    }
  }

  @Test
  void aPlanWithNoFetchNeedsNoFetcher() throws Exception {
    Plan p = plan();
    p.fetch = List.of();
    p.residual.get("java").sql = "SELECT 1 AS n FROM DUAL";
    PlanRunner.Result r = PlanRunner.execute(p, null, Map.of());
    assertEquals(0, r.fetchedRows);
    assertEquals(1, r.rows().size());
  }
}
