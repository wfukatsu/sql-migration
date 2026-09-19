package com.scalar.migrate.runtime;

import com.google.gson.Gson;
import com.scalar.db.api.DistributedTransaction;
import com.scalar.db.api.DistributedTransactionAdmin;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Runs an execution plan as a library call, for generated repository code.
 *
 * <p>The plan is the fetch + residual pair that {@code scalardb_migrate} emits for a statement ScalarDB SQL cannot
 * run on its own: rows are fetched through ScalarDB, then the original SQL runs over them in H2.
 *
 * <p>Two lifecycles:
 *
 * <ul>
 *   <li><b>joining</b> -- {@link #join} runs the fetches inside a transaction the caller already began. This is what
 *       generated repositories use: the plan's reads see the routine's own writes and share its rollback scope, and
 *       the transaction boundary stays in the one place the application service put it.
 *   <li><b>owning</b> -- {@link #execute} with a fetcher that opened its own transaction. This is the CLI path
 *       ({@link Runner}) and stays for benchmarks and offline runs.
 * </ul>
 *
 * <p>Joining has one hard limit that is ScalarDB's, not this runtime's: Consensus Commit refuses to scan rows the
 * same transaction has already written or deleted. A plan whose fetch overlaps the routine's earlier writes
 * therefore fails with {@link ScanAfterWriteException} rather than silently reading a stale image.
 */
public final class PlanRunner {
  private static final Gson GSON = Runner.GSON;   // whole numbers stay whole (see there)

  private PlanRunner() {}

  /** Read a plan from JSON (generated repositories embed the plan file as a resource). */
  public static Plan parse(String json) {
    return GSON.fromJson(json, Plan.class);
  }

  public static Plan load(Path path) throws Exception {
    return parse(Files.readString(path));
  }

  /** Read a plan from the classpath, e.g. {@code PlanRunner.resource("plans/pkg_order.create_order.12.json")}. */
  public static Plan resource(String name) throws Exception {
    try (InputStream in = PlanRunner.class.getClassLoader().getResourceAsStream(name)) {
      if (in == null) throw new IllegalArgumentException("plan not found on the classpath: " + name);
      return parse(new String(in.readAllBytes(), StandardCharsets.UTF_8));
    }
  }

  /** Run a plan inside a ScalarDB Core transaction the caller owns. */
  public static Result join(DistributedTransaction tx, DistributedTransactionAdmin admin, Plan plan,
      Map<String, Object> params) throws Exception {
    return execute(plan, CoreFetcher.joining(tx, admin), params);
  }

  /** Run a plan inside a ScalarDB SQL (JDBC) transaction the caller owns. */
  public static Result join(Connection conn, Plan plan, Map<String, Object> params) throws Exception {
    return execute(plan, JdbcFetcher.joining(conn), params);
  }

  public static Result execute(Plan plan, Fetcher fetcher, Map<String, Object> params) throws Exception {
    return execute(plan, fetcher, params, false);
  }

  /**
   * Fetch through ScalarDB, then run the residual SQL in H2.
   *
   * <p>When the fetcher owns its transaction this begins and commits it (aborting on failure). When the fetcher
   * joined the caller's transaction, none of those are called: ending it is the caller's job, so a failure here
   * propagates and the caller's rollback covers both its writes and this read.
   */
  public static Result execute(Plan plan, Fetcher fetcher, Map<String, Object> params, boolean forceH2Indexes)
      throws Exception {
    Plan.Residual residual = plan.residual.get("java");
    if (residual == null) throw new IllegalArgumentException("plan has no java residual: " + plan.source_sql);
    Map<String, Object> binds = params == null ? Map.of() : params;
    boolean needsFetch = plan.fetch != null && !plan.fetch.isEmpty();
    boolean owns = fetcher != null && fetcher.ownsTransaction();

    long t0 = System.nanoTime();
    int fetched = 0;
    try (Residual h2 = new Residual(residual.mode, residual.build_indexes || forceH2Indexes)) {
      if (needsFetch) {
        if (fetcher == null) throw new IllegalArgumentException("plan needs a fetcher: " + plan.source_sql);
        if (owns) fetcher.begin();
        try {
          for (Plan.Fetch f : plan.fetch) {
            Rows rows = fetch(fetcher, f, binds);
            fetched += rows.rows.size();
            h2.load(f, rows);
          }
          if (owns) fetcher.commit();
        } catch (Exception e) {
          if (owns) fetcher.rollback();
          throw e;
        }
      }
      long t1 = System.nanoTime();
      Map<String, Object> query = h2.query(residual.sql, binds);
      return new Result(query, fetched, (t1 - t0) / 1_000_000, (System.nanoTime() - t1) / 1_000_000);
    }
  }

  /** ScalarDB's own restriction gets a named exception so the diagnostic points at the statement to change. */
  private static Rows fetch(Fetcher fetcher, Plan.Fetch spec, Map<String, Object> params) throws Exception {
    try {
      return fetcher.fetch(spec, params);
    } catch (Exception e) {
      if (isScanAfterWrite(e)) throw new ScanAfterWriteException(spec.table, e);
      throw e;
    }
  }

  static boolean isScanAfterWrite(Throwable e) {
    for (Throwable t = e; t != null; t = t.getCause()) {
      String m = t.getMessage();
      // CoreError.CONSENSUS_COMMIT_SCANNING_ALREADY_WRITTEN_OR_DELETED_DATA_NOT_ALLOWED (ScalarDB 3.19.1)
      if (m != null && m.contains("already-written") && m.contains("not allowed")) return true;
    }
    return false;
  }

  /** The residual query result, plus what it cost. */
  public static final class Result {
    private final Map<String, Object> query;
    public final int fetchedRows;
    public final long fetchMs;
    public final long residualMs;

    Result(Map<String, Object> query, int fetchedRows, long fetchMs, long residualMs) {
      this.query = query;
      this.fetchedRows = fetchedRows;
      this.fetchMs = fetchMs;
      this.residualMs = residualMs;
    }

    /** {@code {"columns": [...], "rows": [[...], ...]}} as the residual engine produced it. */
    public Map<String, Object> query() {
      return query;
    }

    @SuppressWarnings("unchecked")
    public java.util.List<String> columns() {
      return (java.util.List<String>) query.get("columns");
    }

    @SuppressWarnings("unchecked")
    public java.util.List<java.util.List<Object>> rows() {
      return (java.util.List<java.util.List<Object>>) query.get("rows");
    }

    public Map<String, Object> stats(String fetcherKind, String h2Mode) {
      Map<String, Object> s = new LinkedHashMap<>();
      s.put("fetched_rows", fetchedRows);
      s.put("fetch_ms", fetchMs);
      s.put("residual_ms", residualMs);
      s.put("fetcher", fetcherKind);
      s.put("h2_mode", h2Mode);
      return s;
    }
  }
}
