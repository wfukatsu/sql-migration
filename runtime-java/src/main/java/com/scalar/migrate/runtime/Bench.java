package com.scalar.migrate.runtime;

import com.google.gson.reflect.TypeToken;
import com.scalar.migrate.appside.AppSideQuery;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.ResultSetMetaData;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Benchmark: run the same workload against the migration-source Oracle database and against ScalarDB, from one JVM.
 *
 *   residual-runner bench --spec bench-spec.json --out bench-result.json
 *
 * Three execution paths are timed per query:
 *   oracle        -- the original SQL on Oracle Database through the Oracle JDBC thin driver (the baseline)
 *   scalardb_sql  -- the converted statement on ScalarDB SQL through the ScalarDB JDBC driver (licensed cluster)
 *   plan          -- the app-side plan: fetch through ScalarDB (Core API or SQL) + residual SQL in in-memory H2
 *
 * Connections are opened once and reused (a pooled application is the realistic model); the H2 residual database is
 * created per iteration, because it only holds one request's rows. Warm-up iterations are excluded from the numbers.
 * The row count of every measured iteration is kept (`rows_min` / `rows_max`: they differ for a statement that does
 * not return the same thing each time, which is expected for an `${i}` statement and a finding for any other), and
 * the last iteration's first `verify_rows` rows are returned so that the harness can check the values -- the
 * benchmark doubles as a compatibility test at data-set scale. (This comment used to say every iteration was
 * compared; only the last result was kept.)
 *
 * What the numbers do not control for: each query runs its Oracle leg to completion before its ScalarDB leg, so the
 * two are not interleaved, and the plan leg converts every row to JSON where the JDBC legs convert `verify_rows`.
 */
public class Bench {

  public static void main(String[] argv) throws Exception {
    Map<String, String> opt = new LinkedHashMap<>();
    for (int i = 0; i < argv.length; i++) if (argv[i].startsWith("--")) opt.put(argv[i].substring(2), argv[++i]);
    run(opt);
  }

  @SuppressWarnings("unchecked")
  static void run(Map<String, String> opt) throws Exception {
    Map<String, Object> spec = Runner.GSON.fromJson(Files.readString(Path.of(opt.get("spec"))),
        new TypeToken<Map<String, Object>>() {}.getType());
    int iterations = num(spec.get("iterations"), 20);
    int warmup = num(spec.get("warmup"), 3);
    int verifyRows = num(spec.get("verify_rows"), 200);
    // "h2_indexes": build every plan's H2 indexes (a plan can also ask for them with residual.java.build_indexes)
    boolean h2Indexes = Boolean.TRUE.equals(spec.get("h2_indexes"));
    // "source" is any JDBC source database (Oracle / PostgreSQL / MySQL); "oracle" is the older name of the same key
    Map<String, Object> ora = (Map<String, Object>) (spec.containsKey("source") ? spec.get("source") : spec.get("oracle"));
    String sqlProps = (String) spec.get("scalardb_sql_properties");
    String coreProps = (String) spec.get("core_properties");

    List<Map<String, Object>> out = new ArrayList<>();
    // the harness names the environment variable holding the password, so that the spec file carries no secret
    String sourcePassword = ora.containsKey("password_env") ? System.getenv((String) ora.get("password_env"))
        : (String) ora.get("password");
    try (Connection oracle = DriverManager.getConnection((String) ora.get("url"), (String) ora.get("user"), sourcePassword);
         Connection scalar = DriverManager.getConnection("jdbc:scalardb:" + sqlProps);
         Fetcher core = coreProps == null ? null : new CoreFetcher(coreProps);
         Fetcher sqlFetcher = new JdbcFetcher(sqlProps)) {
      oracle.setAutoCommit(false);
      scalar.setAutoCommit(false);

      for (Map<String, Object> q : (List<Map<String, Object>>) spec.get("queries")) {
        String id = (String) q.get("id");
        Map<String, Object> rec = new LinkedHashMap<>();
        rec.put("id", id);
        rec.put("label", q.get("label"));
        rec.put("path", q.get("path"));
        boolean write = Boolean.TRUE.equals(q.get("write"));
        System.err.println("bench " + id + " ...");
        Object base = q.get("iterate_base");
        Reset resetSource = resetWith(oracle, (List<String>) q.get("reset_source"));
        Reset resetScalar = resetWith(scalar, (List<String>) q.get("reset_scalardb"));
        rec.put("oracle", measure(() -> new Exec() {
          public Map<String, Object> call(int i) throws Exception {
            return write ? jdbcUpdate(oracle, iterate((String) q.get("oracle_sql"), i, base))
                         : jdbcQuery(oracle, iterate((String) q.get("oracle_sql"), i, base), verifyRows);
          }
        }, iterations, warmup, resetSource));

        Map<String, Object> scalarResult;
        if ("plan".equals(q.get("path"))) {
          Plan plan = Runner.GSON.fromJson(Files.readString(Path.of((String) q.get("plan_file"))), Plan.class);
          Fetcher fetcher = "core".equals(q.getOrDefault("fetcher", "jdbc")) ? core : sqlFetcher;
          scalarResult = measure(() -> new Exec() {
            public Map<String, Object> call(int i) throws Exception {
              return planRun(fetcher, plan, verifyRows, h2Indexes);
            }
          }, iterations, warmup);
        } else if ("appside".equals(q.get("path"))) {
          AppSideQuery impl = (AppSideQuery) Class.forName((String) q.get("appside_class"))
              .getDeclaredConstructor().newInstance();
          List<Map<String, Object>> fetches = (List<Map<String, Object>>) q.get("fetch");
          scalarResult = measure(() -> new Exec() {
            public Map<String, Object> call(int i) throws Exception {
              return appsideRun(scalar, impl, fetches, verifyRows);
            }
          }, iterations, warmup);
        } else {
          scalarResult = measure(() -> new Exec() {
            public Map<String, Object> call(int i) throws Exception {
              return write ? jdbcUpdate(scalar, iterate((String) q.get("scalardb_sql"), i, base))
                           : jdbcQuery(scalar, iterate((String) q.get("scalardb_sql"), i, base), verifyRows);
            }
          }, iterations, warmup, resetScalar);
        }
        rec.put("scalardb", scalarResult);
        out.add(rec);
      }
    }
    String json = Runner.GSON.toJson(Map.of("iterations", iterations, "warmup", warmup, "queries", out));
    if (opt.get("out") != null) Files.writeString(Path.of(opt.get("out")), json);
    else System.out.println(json);
    System.err.println("bench done");
  }

  interface Exec { Map<String, Object> call(int iteration) throws Exception; }

  interface ExecFactory { Exec get(); }

  /** Restores the data set before an iteration (untimed), so that a write applies to the same state every time. */
  interface Reset { void run() throws Exception; }

  static Reset resetWith(Connection c, List<String> statements) {
    if (statements == null || statements.isEmpty()) return null;
    return () -> {
      try (Statement st = c.createStatement()) {
        for (String sql : statements) st.execute(sql);
        c.commit();
      } catch (Exception e) {
        c.rollback();
        throw e;
      }
    };
  }

  static Map<String, Object> measure(ExecFactory factory, int iterations, int warmup) {
    return measure(factory, iterations, warmup, null);
  }

  /** Run warmup + measured iterations, collecting per-iteration wall-clock milliseconds and the last result. */
  static Map<String, Object> measure(ExecFactory factory, int iterations, int warmup, Reset reset) {
    Exec exec = factory.get();
    Map<String, Object> res = new LinkedHashMap<>();
    List<Double> ms = new ArrayList<>();
    Map<String, Object> last = null;
    long rowsMin = Long.MAX_VALUE;
    long rowsMax = Long.MIN_VALUE;
    try {
      for (int i = 0; i < warmup; i++) {
        if (reset != null) reset.run();
        exec.call(i);
      }
      for (int i = 0; i < iterations; i++) {
        if (reset != null) reset.run();
        long t0 = System.nanoTime();
        last = exec.call(warmup + i);
        ms.add((System.nanoTime() - t0) / 1_000_000.0);
        if (last.get("rows") instanceof Number n) {
          rowsMin = Math.min(rowsMin, n.longValue());
          rowsMax = Math.max(rowsMax, n.longValue());
        }
      }
    } catch (Exception e) {
      res.put("error", e.getClass().getSimpleName() + ": " + String.valueOf(e.getMessage()).split("\n")[0]);
    }
    res.put("ms", ms);
    if (last != null) res.putAll(last);
    if (rowsMax >= rowsMin) {
      res.put("rows_min", rowsMin);
      res.put("rows_max", rowsMax);
    }
    return res;
  }

  /** Substitute ${i} by (iterate_base + iteration), so that each iteration touches a different row. */
  static String iterate(String sql, int i, Object base) {
    return sql.replace("${i}", Integer.toString((base == null ? 0 : ((Number) base).intValue()) + i));
  }

  // Oracle's thin driver fetches 10 rows per round trip unless told otherwise, so a large result paid n/10 round
  // trips that the ScalarDB legs (scan_fetch_size 1000 in the bench configuration) did not. Same size for both.
  static final int FETCH_SIZE = 1000;

  static Map<String, Object> jdbcQuery(Connection c, String sql, int verifyRows) throws Exception {
    try (PreparedStatement ps = c.prepareStatement(sql)) {
      ps.setFetchSize(FETCH_SIZE);
      return readAll(c, ps, verifyRows);
    }
  }

  private static Map<String, Object> readAll(Connection c, PreparedStatement ps, int verifyRows) throws Exception {
    try (ResultSet rs = ps.executeQuery()) {
      ResultSetMetaData m = rs.getMetaData();
      List<String> columns = new ArrayList<>();
      for (int i = 1; i <= m.getColumnCount(); i++) columns.add(m.getColumnLabel(i));
      List<List<Object>> sample = new ArrayList<>();
      int n = 0;
      while (rs.next()) {
        if (n < verifyRows) {
          List<Object> row = new ArrayList<>();
          for (int i = 1; i <= columns.size(); i++) row.add(Values.toJson(rs.getObject(i)));
          sample.add(row);
        }
        n++;
      }
      c.commit();
      return map("columns", columns, "rows", n, "sample", sample);
    }
  }

  static Map<String, Object> jdbcUpdate(Connection c, String sql) throws Exception {
    try (PreparedStatement ps = c.prepareStatement(sql)) {
      int n = ps.executeUpdate();
      c.commit();
      return map("columns", List.of(), "rows", n, "sample", List.of());
    }
  }

  /** One request of the app-side path: fetch through ScalarDB inside a transaction, then the residual SQL in H2. */
  static Map<String, Object> planRun(Fetcher fetcher, Plan plan, int verifyRows, boolean h2Indexes) throws Exception {
    Plan.Residual residual = plan.residual.get("java");
    long t0 = System.nanoTime();
    int fetched = 0;
    try (Residual h2 = new Residual(residual.mode, residual.build_indexes || h2Indexes)) {
      fetcher.begin();
      try {
        for (Plan.Fetch f : plan.fetch) {
          Rows rows = fetcher.fetch(f, Map.of());
          fetched += rows.rows.size();
          h2.load(f, rows);
        }
        fetcher.commit();
      } catch (Exception e) {
        fetcher.rollback();
        throw e;
      }
      long t1 = System.nanoTime();
      Map<String, Object> r = h2.query(residual.sql, Map.of());
      long t2 = System.nanoTime();
      List<List<Object>> rows = (List<List<Object>>) r.get("rows");
      Map<String, Object> res = map("columns", r.get("columns"), "rows", rows.size(),
          "sample", rows.subList(0, Math.min(verifyRows, rows.size())));
      res.put("fetched_rows", fetched);
      res.put("fetch_ms", (t1 - t0) / 1_000_000.0);
      res.put("residual_ms", (t2 - t1) / 1_000_000.0);
      return res;
    }
  }

  /**
   * One request of the hand-written app-side path: fetch every input table through ScalarDB SQL in one transaction,
   * then run the AppSideQuery implementation on the rows. fetch_ms / residual_ms split the time the same way as a plan.
   */
  static Map<String, Object> appsideRun(Connection scalar, AppSideQuery impl, List<Map<String, Object>> fetches,
      int verifyRows) throws Exception {
    long t0 = System.nanoTime();
    int fetched = 0;
    Map<String, List<Map<String, Object>>> tables = new LinkedHashMap<>();
    try {
      for (Map<String, Object> f : fetches) {
        List<Map<String, Object>> rows = new ArrayList<>();
        try (PreparedStatement ps = scalar.prepareStatement((String) f.get("sql")); ResultSet rs = ps.executeQuery()) {
          ResultSetMetaData m = rs.getMetaData();
          while (rs.next()) {
            Map<String, Object> row = new LinkedHashMap<>();
            for (int c = 1; c <= m.getColumnCount(); c++) {
              row.put(m.getColumnLabel(c).toLowerCase(), javaTime(rs.getObject(c)));
            }
            rows.add(row);
          }
        }
        fetched += rows.size();
        tables.put(((String) f.get("table")).toLowerCase(), rows);
      }
      scalar.commit();
    } catch (Exception e) {
      scalar.rollback();
      throw e;
    }
    long t1 = System.nanoTime();
    List<Map<String, Object>> result = impl.run(tables);
    long t2 = System.nanoTime();
    List<String> columns = result.isEmpty() ? List.of() : new ArrayList<>(result.get(0).keySet());
    List<List<Object>> sample = new ArrayList<>();
    for (Map<String, Object> r : result.subList(0, Math.min(verifyRows, result.size()))) {
      List<Object> row = new ArrayList<>();
      for (String c : columns) row.add(Values.toJson(r.get(c)));
      sample.add(row);
    }
    Map<String, Object> res = map("columns", columns, "rows", result.size(), "sample", sample);
    res.put("fetched_rows", fetched);
    res.put("fetch_ms", (t1 - t0) / 1_000_000.0);
    res.put("residual_ms", (t2 - t1) / 1_000_000.0);
    return res;
  }

  /** JDBC temporal values as the java.time types AppSideQuery implementations work with. */
  static Object javaTime(Object v) {
    if (v instanceof java.sql.Timestamp) return ((java.sql.Timestamp) v).toLocalDateTime();
    if (v instanceof java.sql.Date) return ((java.sql.Date) v).toLocalDate();
    return v;
  }

  static Map<String, Object> map(String k1, Object v1, String k2, Object v2, String k3, Object v3) {
    Map<String, Object> m = new LinkedHashMap<>();
    m.put(k1, v1);
    m.put(k2, v2);
    m.put(k3, v3);
    return m;
  }

  static int num(Object v, int dflt) {
    return v == null ? dflt : ((Number) v).intValue();
  }
}
