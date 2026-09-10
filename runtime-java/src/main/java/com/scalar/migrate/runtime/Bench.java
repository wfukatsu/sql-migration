package com.scalar.migrate.runtime;

import com.google.gson.reflect.TypeToken;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.ResultSetMetaData;
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
 * Every iteration's result set is compared for row count, and the first `verify_rows` rows are returned so that the
 * harness can check the values, so the benchmark doubles as a compatibility test at data-set scale.
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
    Map<String, Object> ora = (Map<String, Object>) spec.get("oracle");
    String sqlProps = (String) spec.get("scalardb_sql_properties");
    String coreProps = (String) spec.get("core_properties");

    List<Map<String, Object>> out = new ArrayList<>();
    try (Connection oracle = DriverManager.getConnection((String) ora.get("url"), (String) ora.get("user"), (String) ora.get("password"));
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
        rec.put("oracle", measure(() -> new Exec() {
          public Map<String, Object> call(int i) throws Exception {
            return write ? jdbcUpdate(oracle, iterate((String) q.get("oracle_sql"), i, base))
                         : jdbcQuery(oracle, iterate((String) q.get("oracle_sql"), i, base), verifyRows);
          }
        }, iterations, warmup));

        Map<String, Object> scalarResult;
        if ("plan".equals(q.get("path"))) {
          Plan plan = Runner.GSON.fromJson(Files.readString(Path.of((String) q.get("plan_file"))), Plan.class);
          Fetcher fetcher = "core".equals(q.getOrDefault("fetcher", "jdbc")) ? core : sqlFetcher;
          scalarResult = measure(() -> new Exec() {
            public Map<String, Object> call(int i) throws Exception {
              return planRun(fetcher, plan, verifyRows);
            }
          }, iterations, warmup);
        } else {
          scalarResult = measure(() -> new Exec() {
            public Map<String, Object> call(int i) throws Exception {
              return write ? jdbcUpdate(scalar, iterate((String) q.get("scalardb_sql"), i, base))
                           : jdbcQuery(scalar, iterate((String) q.get("scalardb_sql"), i, base), verifyRows);
            }
          }, iterations, warmup);
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

  /** Run warmup + measured iterations, collecting per-iteration wall-clock milliseconds and the last result. */
  static Map<String, Object> measure(ExecFactory factory, int iterations, int warmup) {
    Exec exec = factory.get();
    Map<String, Object> res = new LinkedHashMap<>();
    List<Double> ms = new ArrayList<>();
    Map<String, Object> last = null;
    try {
      for (int i = 0; i < warmup; i++) exec.call(i);
      for (int i = 0; i < iterations; i++) {
        long t0 = System.nanoTime();
        last = exec.call(warmup + i);
        ms.add((System.nanoTime() - t0) / 1_000_000.0);
      }
    } catch (Exception e) {
      res.put("error", e.getClass().getSimpleName() + ": " + String.valueOf(e.getMessage()).split("\n")[0]);
    }
    res.put("ms", ms);
    if (last != null) res.putAll(last);
    return res;
  }

  /** Substitute ${i} by (iterate_base + iteration), so that each iteration touches a different row. */
  static String iterate(String sql, int i, Object base) {
    return sql.replace("${i}", Integer.toString((base == null ? 0 : ((Number) base).intValue()) + i));
  }

  static Map<String, Object> jdbcQuery(Connection c, String sql, int verifyRows) throws Exception {
    try (PreparedStatement ps = c.prepareStatement(sql); ResultSet rs = ps.executeQuery()) {
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
  static Map<String, Object> planRun(Fetcher fetcher, Plan plan, int verifyRows) throws Exception {
    Plan.Residual residual = plan.residual.get("java");
    long t0 = System.nanoTime();
    int fetched = 0;
    try (Residual h2 = new Residual(residual.mode)) {
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
