package com.scalar.migrate.runtime;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.reflect.TypeToken;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * CLI for the residual runtime.
 *
 *   run      --plan p.json --properties scalardb.properties [--fetcher core|jdbc] [--h2-indexes] [--param name=value]...
 *            --h2-indexes builds the plan's H2 indexes even when the plan does not ask for them (residual.java.build_indexes)
 *   load     --properties scalardb.properties --namespace ns --table t --rows rows.json      (through ScalarDB Core)
 *   validate --plan p.json                       (compile the residual SQL against empty H2 tables, no database needed)
 *   bench    --spec bench-spec.json --out result.json   (time Oracle vs ScalarDB for the same workload; see Bench)
 *
 * All data access goes through ScalarDB (Core API or SQL JDBC); the runtime never connects to the backend database.
 *
 * Results go to stdout as JSON; logs (ScalarDB's, through slf4j-simple) go to stderr at WARN. For more detail:
 * RESIDUAL_RUNNER_OPTS=-Dorg.slf4j.simpleLogger.defaultLogLevel=info (src/main/resources/simplelogger.properties).
 */
public class Runner {
  /**
   * Whole numbers stay whole. Gson's default reads every JSON number as a double, so an 18-digit id in a plan or
   * in the rows to load lost its last digits (9007199254740993 became ...992) and the key lookup found nothing.
   */
  static final Gson GSON = new GsonBuilder().serializeNulls()
      .setObjectToNumberStrategy(com.google.gson.ToNumberPolicy.LONG_OR_DOUBLE).create();

  public static void main(String[] argv) throws Exception {
    if (argv.length == 0) { usage(); System.exit(2); }
    Map<String, String> opt = new LinkedHashMap<>();
    Map<String, Object> params = new LinkedHashMap<>();
    for (int i = 1; i < argv.length; i++) {
      if ("--param".equals(argv[i])) {
        String[] kv = argv[++i].split("=", 2);
        if (kv.length != 2) throw new IllegalArgumentException("--param expects name=value, got: " + kv[0]);
        params.put(kv[0], parseParam(kv[1]));
      } else if ("--h2-indexes".equals(argv[i])) {
        opt.put("h2-indexes", "true");  // a flag, takes no value
      } else if (argv[i].startsWith("--")) {
        opt.put(argv[i].substring(2), argv[++i]);
      }
    }
    switch (argv[0]) {
      case "run": run(opt, params); break;
      case "load": load(opt); break;
      case "validate": validate(opt); break;
      case "sql": sql(opt, params); break;
      case "bench": Bench.run(opt); break;
      default: usage(); System.exit(2);
    }
  }

  /** The CLI owns its transaction; generated repositories join the caller's instead (see PlanRunner). */
  static void run(Map<String, String> opt, Map<String, Object> params) throws Exception {
    Plan plan = PlanRunner.load(Path.of(opt.get("plan")));
    Plan.Residual residual = plan.residual.get("java");
    String fetcherKind = opt.getOrDefault("fetcher", "core");
    boolean needsFetch = plan.fetch != null && !plan.fetch.isEmpty();
    try (Fetcher fetcher = !needsFetch ? null : "jdbc".equals(fetcherKind) ? new JdbcFetcher(opt.get("properties")) : new CoreFetcher(opt.get("properties"))) {
      PlanRunner.Result r = PlanRunner.execute(plan, fetcher, params, opt.containsKey("h2-indexes"));
      Map<String, Object> result = new LinkedHashMap<>(r.query());
      result.put("stats", r.stats(fetcherKind, residual.mode));
      System.out.println(GSON.toJson(result));
    }
  }

  /** Execute one ScalarDB SQL statement through the JDBC driver (cluster) and print the rows as JSON. */
  static void sql(Map<String, String> opt, Map<String, Object> params) throws Exception {
    try (java.sql.Connection c = java.sql.DriverManager.getConnection("jdbc:scalardb:" + opt.get("properties"))) {
      c.setAutoCommit(false);
      List<Object> binds = new ArrayList<>();
      String bound = Residual.bindNamed(opt.get("sql"), params, binds);
      try (java.sql.PreparedStatement ps = c.prepareStatement(bound)) {
        for (int i = 0; i < binds.size(); i++) ps.setObject(i + 1, binds.get(i));
        try (java.sql.ResultSet rs = ps.executeQuery()) {
          java.sql.ResultSetMetaData m = rs.getMetaData();
          List<String> columns = new ArrayList<>();
          for (int i = 1; i <= m.getColumnCount(); i++) columns.add(m.getColumnLabel(i));
          List<List<Object>> rows = new ArrayList<>();
          while (rs.next()) {
            List<Object> row = new ArrayList<>();
            for (int i = 1; i <= columns.size(); i++) row.add(Values.toJson(rs.getObject(i)));
            rows.add(row);
          }
          c.commit();
          System.out.println(GSON.toJson(Map.of("columns", columns, "rows", rows)));
        }
      }
    }
  }

  static void load(Map<String, String> opt) throws Exception {
    List<Map<String, Object>> rows = GSON.fromJson(Files.readString(Path.of(opt.get("rows"))),
        new TypeToken<List<Map<String, Object>>>() {}.getType());
    int chunk = Integer.parseInt(opt.getOrDefault("chunk", "0"));  // rows per transaction (0 = all in one)
    try (CoreFetcher core = new CoreFetcher(opt.get("properties"))) {
      int n = 0;
      int size = chunk > 0 ? chunk : rows.size();
      for (int i = 0; i < rows.size(); i += size) n += core.load(opt.get("namespace"), opt.get("table"), rows.subList(i, Math.min(rows.size(), i + size)));
      System.out.println(GSON.toJson(Map.of("loaded", n, "table", opt.get("namespace") + "." + opt.get("table"))));
    }
  }

  static void validate(Map<String, String> opt) throws Exception {
    Plan plan = GSON.fromJson(Files.readString(Path.of(opt.get("plan"))), Plan.class);
    Plan.Residual residual = plan.residual.get("java");
    List<String> problems = new ArrayList<>();
    try (Residual h2 = new Residual(residual.mode)) {
      for (Plan.Fetch f : plan.fetch) {
        if (f.column_types == null || f.column_types.isEmpty()) {
          problems.add("table " + f.table + ": column types unknown (no schema), cannot validate offline");
          continue;
        }
        Rows empty = new Rows();
        List<String> cols = f.columns == null || f.columns.isEmpty() ? new ArrayList<>(f.column_types.keySet()) : f.columns;
        for (String c : cols) { empty.columns.add(c); empty.types.put(c, f.column_types.get(c)); }
        h2.load(f, empty);
      }
      if (problems.isEmpty()) {
        try { h2.prepareOnly(residual.sql); } catch (Exception e) { problems.add("residual SQL rejected by H2 " + residual.mode + " mode: " + e.getMessage().split("\n")[0]); }
      }
    }
    System.out.println(GSON.toJson(Map.of("ok", problems.isEmpty(), "problems", problems, "unresolved", plan.unresolved == null ? List.of() : plan.unresolved)));
    if (!problems.isEmpty()) System.exit(1);
  }

  private static final java.util.regex.Pattern WHOLE = java.util.regex.Pattern.compile("-?(0|[1-9]\\d*)");
  private static final java.util.regex.Pattern DECIMAL = java.util.regex.Pattern.compile("-?(0|[1-9]\\d*)\\.\\d+");

  /**
   * A `--param` value as the number it is, or as text. Only the canonical spelling of a number is a number:
   * `00123` is a zero-padded code, and reading it as 123 made a lookup on a TEXT key search for "123". Whole
   * numbers are exact at any size ({@code Long}, then {@code BigDecimal}); a double cannot hold an 18-digit id.
   */
  static Object parseParam(String v) {
    if (WHOLE.matcher(v).matches()) {
      try { return Long.parseLong(v); } catch (NumberFormatException tooLong) { return new java.math.BigDecimal(v); }
    }
    if (DECIMAL.matcher(v).matches()) return new java.math.BigDecimal(v);
    if ("true".equalsIgnoreCase(v) || "false".equalsIgnoreCase(v)) return Boolean.parseBoolean(v);
    return v;
  }

  static void usage() {
    System.err.println("usage: residual-runner run|load|validate|sql|bench [--plan f] [--properties f] [--fetcher core|jdbc] [--param k=v]... [--namespace ns --table t --rows f]");
  }
}
