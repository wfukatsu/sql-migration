package com.scalar.migrate.runtime;

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
import java.util.Set;
import java.util.UUID;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Per-request in-memory H2 database holding the fetched rows; runs the original SQL in the source dialect's
 * compatibility mode. Never persisted: the database disappears when the session is closed.
 */
public class Residual implements AutoCloseable {
  private static final List<String> MODES = List.of("Oracle", "PostgreSQL", "MySQL");
  // A table or column name from a plan goes into DDL as text. H2 runs several statements per execute, so a
  // "table" spelled `t(a INT); CREATE ALIAS x AS '...java...'; CREATE TABLE u` was code execution from a plan file
  // -- and `validate`, the command one runs on a plan one did not write, got there too.
  private static final Pattern IDENTIFIER = Pattern.compile("[A-Za-z_][A-Za-z0-9_$#]*");
  private static final String READER = "RESIDUAL_READER";
  private final Connection h2;
  // The residual SQL is the source application's SQL, carried in the plan. It runs as a user that can only
  // SELECT: H2 keeps FILE_READ, CSVWRITE, LINK_SCHEMA, RUNSCRIPT and CREATE ALIAS for admins.
  private final Connection reader;
  private final String mode;
  // table (lower-cased) -> the columns it was created with, and the namespace it came from
  private final Map<String, List<String>> created = new LinkedHashMap<>();
  private final Map<String, String> namespaces = new LinkedHashMap<>();
  // table -> indexes from the plan (primary key, join columns); built once, after every fetch is loaded
  private final Map<String, List<List<String>>> indexes = new LinkedHashMap<>();
  private final boolean buildIndexes;
  private boolean indexed;

  /** Without indexes: the right default for small requests, where building them costs more than it saves. */
  public Residual(String mode) throws Exception {
    this(mode, false);
  }

  /**
   * @param buildIndexes build the plan's index_columns before the first query. Joins over tens of thousands of fetched
   *     rows (batch jobs) go from nested-loop scans to index lookups; single-table plans only pay the build time and
   *     the index memory (about 1.6x the rows, docs/reports/dml-followup-research.md).
   */
  public Residual(String mode, boolean buildIndexes) throws Exception {
    this.buildIndexes = buildIndexes;
    // the mode comes from a plan file and goes into a JDBC URL, where `;INIT=...` would run whatever it says
    this.mode = MODES.stream().filter(m -> m.equalsIgnoreCase(mode)).findFirst()
        .orElseThrow(() -> new IllegalArgumentException("unknown H2 mode " + mode + " (expected one of " + MODES + ")"));
    // CASE_INSENSITIVE_IDENTIFIERS: the tables are created with the names ScalarDB has, the residual SQL is the
    //   source application's, and the source folds case -- `FROM ORDERS` has to find `orders`.
    // NON_KEYWORDS: `key` and `value` are ordinary column names that H2 reserves. (Words H2 needs to parse SQL at
    //   all -- ORDER, USER, END, OFFSET -- cannot be released this way; a column named so still fails, loudly.)
    // TIME ZONE: TIMESTAMPTZ values arrive as UTC instants. With the session in the JVM's zone, CAST(ts AS DATE)
    //   or EXTRACT(HOUR ...) in the residual SQL gave a different answer on a different host.
    String url = "jdbc:h2:mem:" + UUID.randomUUID() + ";MODE=" + this.mode + ";DATABASE_TO_UPPER=FALSE"
        + ";CASE_INSENSITIVE_IDENTIFIERS=TRUE;NON_KEYWORDS=KEY,VALUE;TIME ZONE=UTC";
    h2 = DriverManager.getConnection(url);
    if ("Oracle".equals(this.mode)) OracleFunctions.register(h2);
    String password = UUID.randomUUID().toString();
    try (Statement s = h2.createStatement()) {
      s.execute("CREATE USER " + READER + " PASSWORD '" + password + "'");
      s.execute("GRANT SELECT ON SCHEMA PUBLIC TO " + READER);
    }
    reader = DriverManager.getConnection(url, READER, password);
  }

  static String identifier(String kind, String name) {
    if (name == null || !IDENTIFIER.matcher(name).matches()) {
      throw new IllegalArgumentException(kind + " name in the plan is not a plain identifier: " + name);
    }
    return name;
  }

  /**
   * The H2 type of a fetched column. ScalarDB has no DECIMAL, so an Oracle {@code NUMBER(9)} or a MySQL
   * {@code INT} arrives as INT / BIGINT -- and H2 divides two integers as integers in every mode: {@code qty / 2}
   * was 3 where Oracle and MySQL answer 3.5, with no error. In those two modes whole-number columns are NUMERIC,
   * which divides exactly. PostgreSQL truncates integer division itself, so there the integer types are right.
   */
  String columnType(String scalardbType) {
    if (!"PostgreSQL".equals(mode)) {
      if ("INT".equals(scalardbType)) return "NUMERIC(10)";
      if ("BIGINT".equals(scalardbType)) return "NUMERIC(19)";
    }
    return Values.h2Type(scalardbType);
  }

  private static final java.util.regex.Pattern EXACT_DECIMAL = java.util.regex.Pattern.compile("NUMERIC\\(\\d{1,2},\\d{1,2}\\)");

  /**
   * The source's own type of a column ScalarDB stores as DOUBLE ({@code NUMBER(7,2)}). As an H2 DOUBLE the value
   * turns into text as {@code 2450.0} where Oracle answers {@code 2450}, and sums in binary. The type goes into DDL,
   * so nothing but {@code NUMERIC(p,s)} is accepted from a plan file.
   */
  private static String residualType(Plan.Fetch spec, String column) {
    if (spec.residual_types == null) return null;
    for (Map.Entry<String, String> e : spec.residual_types.entrySet()) {
      if (!e.getKey().equalsIgnoreCase(column)) continue;
      if (!EXACT_DECIMAL.matcher(e.getValue()).matches()) {
        throw new IllegalArgumentException("residual type of " + column + " is not NUMERIC(p,s): " + e.getValue());
      }
      return e.getValue();
    }
    return null;
  }

  /** The plan's exact type of a column, and only of a DOUBLE one: a column stored as a scaled BIGINT stays whole. */
  private static String exactType(Plan.Fetch spec, Rows rows, String column) {
    String declared = rows.types.containsKey(column) ? rows.types.get(column)
        : spec.column_types != null ? spec.column_types.get(column) : null;
    return "DOUBLE".equals(declared) ? residualType(spec, column) : null;
  }

  /**
   * A DOUBLE as the source database would hold it in a {@code NUMERIC(p,s)} column. H2 keeps the scale a value comes
   * with, and the scale is what shows when the value becomes text: Oracle writes a NUMBER without trailing zeros
   * (2450), PostgreSQL and MySQL write every declared place (2450.00).
   */
  java.math.BigDecimal decimal(Number value, String type) {
    int scale = Integer.parseInt(type.substring(type.indexOf(',') + 1, type.length() - 1));
    java.math.BigDecimal d = new java.math.BigDecimal(value.toString()).setScale(scale, java.math.RoundingMode.HALF_UP);
    if (!"Oracle".equals(mode)) return d;
    d = d.stripTrailingZeros();
    return d.scale() < 0 ? d.setScale(0) : d;
  }

  /** Create the table on first use (typed from ScalarDB types when known, else from the Java values) and load rows. */
  public void load(Plan.Fetch spec, Rows rows) throws Exception {
    String table = identifier("table", spec.table);
    for (String c : rows.columns) identifier("column", c);
    String key = table.toLowerCase();
    boolean firstLoad = !created.containsKey(key);
    // H2 holds one table per name and the residual SQL names tables without a namespace. Two fetches of `t` from
    // different namespaces used to be merged into one table without a word
    String namespace = spec.namespace == null ? "" : spec.namespace;
    if (!namespaces.computeIfAbsent(key, k -> namespace).equals(namespace)) {
      throw new IllegalStateException("the plan fetches table " + table + " from two namespaces ("
          + namespaces.get(key) + ", " + namespace + "); the residual engine holds one table per name");
    }
    if (!firstLoad && !created.get(key).equals(lower(rows.columns))) {
      throw new IllegalStateException("two fetches into " + table + " return different columns: "
          + created.get(key) + " and " + lower(rows.columns));
    }
    if (firstLoad) {
      StringBuilder ddl = new StringBuilder("CREATE TABLE " + table + " (");
      for (int i = 0; i < rows.columns.size(); i++) {
        String c = rows.columns.get(i);
        String declared = rows.types.containsKey(c) ? rows.types.get(c)
            : spec.column_types != null && spec.column_types.containsKey(c) ? spec.column_types.get(c)
            : Values.typeOfValues(rows.rows, i);
        // every value NULL and no declared type: any type gives the same answers, since NULLs compare alike
        String type = declared == null ? "VARCHAR" : columnType(declared);
        if (exactType(spec, rows, c) != null) type = exactType(spec, rows, c);
        ddl.append(i > 0 ? ", " : "").append(c).append(' ').append(type);
      }
      ddl.append(')');
      try (Statement s = h2.createStatement()) { s.execute(ddl.toString()); }
      created.put(key, lower(rows.columns));
    }
    if (spec.index_columns != null) {
      List<List<String>> ixs = indexes.computeIfAbsent(table, k -> new ArrayList<>());
      for (List<String> cols : spec.index_columns) {
        for (String c : cols) identifier("index column", c);
        if (!cols.isEmpty() && !ixs.contains(cols)) ixs.add(cols);
      }
    }
    if (rows.rows.isEmpty()) return;
    String cols = String.join(", ", rows.columns);
    String marks = String.join(", ", java.util.Collections.nCopies(rows.columns.size(), "?"));
    // One fetch returns distinct rows, so the first load of a table is a plain INSERT. A second fetch into the same
    // table (several scopes referencing it, or a key list split into one fetch per value) may bring rows that are
    // already there. They used to go in with MERGE ... KEY (every column): KEY compares with `=`, NULL never
    // equals NULL, and every row with a NULL in it was inserted again -- counts, sums and joins came out inflated.
    // The rows are staged and only those not already present are added, compared with IS NOT DISTINCT FROM.
    String target = firstLoad ? table : table + "_staging";
    if (!firstLoad) {
      try (Statement s = h2.createStatement()) {
        s.execute("CREATE TABLE " + target + " AS SELECT * FROM " + table + " WHERE FALSE");
      }
    }
    try (PreparedStatement ps = h2.prepareStatement("INSERT INTO " + target + " (" + cols + ") VALUES (" + marks + ")")) {
      String[] exact = new String[rows.columns.size()];
      for (int i = 0; i < exact.length; i++) exact[i] = exactType(spec, rows, rows.columns.get(i));
      for (Object[] r : rows.rows) {
        for (int i = 0; i < r.length; i++) {
          ps.setObject(i + 1, exact[i] != null && r[i] instanceof Number n ? decimal(n, exact[i]) : Values.toH2(r[i]));
        }
        ps.addBatch();
      }
      ps.executeBatch();
    }
    if (!firstLoad) {
      List<String> same = new ArrayList<>();
      for (String c : rows.columns) same.add("t." + c + " IS NOT DISTINCT FROM s." + c);
      try (Statement s = h2.createStatement()) {
        s.execute("INSERT INTO " + table + " (" + cols + ") SELECT DISTINCT " + cols + " FROM " + target + " s"
            + " WHERE NOT EXISTS (SELECT 1 FROM " + table + " t WHERE " + String.join(" AND ", same) + ")");
        s.execute("DROP TABLE " + target);
      }
    }
  }

  private static List<String> lower(List<String> names) {
    List<String> out = new ArrayList<>();
    for (String n : names) out.add(n.toLowerCase());
    return out;
  }

  /** Run the residual SQL and return the result as columns + rows (JSON-friendly values). */
  public Map<String, Object> query(String sql, Map<String, Object> params) throws Exception {
    ensureIndexes();
    List<Object> binds = new ArrayList<>();
    String bound = bindNamed(sql, params, binds);
    try (PreparedStatement ps = reader.prepareStatement(bound)) {
      for (int i = 0; i < binds.size(); i++) ps.setObject(i + 1, binds.get(i));
      try (ResultSet rs = ps.executeQuery()) {
        ResultSetMetaData m = rs.getMetaData();
        List<String> columns = new ArrayList<>();
        for (int i = 1; i <= m.getColumnCount(); i++) columns.add(m.getColumnLabel(i));
        List<List<Object>> out = new ArrayList<>();
        while (rs.next()) {
          List<Object> row = new ArrayList<>();
          for (int i = 1; i <= columns.size(); i++) row.add(Values.toJson(read(rs, m, i)));
          out.add(row);
        }
        return Map.of("columns", columns, "rows", out);
      }
    }
  }

  /**
   * A date or time as the java.time value H2 holds. {@code getObject} alone gives java.sql.Timestamp, which is an
   * instant read in the JVM's zone while the session is UTC: on a JST host every Oracle DATE came back nine hours
   * late (and CI, which runs in UTC, saw nothing).
   */
  private static Object read(ResultSet rs, ResultSetMetaData m, int i) throws Exception {
    switch (m.getColumnType(i)) {
      case java.sql.Types.TIMESTAMP: return rs.getObject(i, java.time.LocalDateTime.class);
      case java.sql.Types.DATE: return rs.getObject(i, java.time.LocalDate.class);
      case java.sql.Types.TIME: return rs.getObject(i, java.time.LocalTime.class);
      default: return rs.getObject(i);
    }
  }

  /**
   * Build the plan's indexes once, after the rows are in (building them before the bulk INSERT would slow the load).
   * An index that cannot be built -- a column the fetch did not return -- only costs speed, so it is skipped.
   */
  void ensureIndexes() {
    if (!buildIndexes || indexed) return;
    indexed = true;
    int n = 0;
    for (Map.Entry<String, List<List<String>>> e : indexes.entrySet()) {
      for (List<String> cols : e.getValue()) {
        String sql = "CREATE INDEX " + e.getKey() + "_ix" + (n++) + " ON " + e.getKey() + " (" + String.join(", ", cols) + ")";
        try (Statement s = h2.createStatement()) {
          s.execute(sql);
        } catch (Exception ignored) {
          // fall back to the unindexed table
        }
      }
    }
  }

  /** Compile-only check used by `validate`: prepares the statement against the (empty) tables. */
  public void prepareOnly(String sql) throws Exception {
    List<Object> binds = new ArrayList<>();
    String bound = scan(sql, null, binds);
    reader.prepareStatement(bound).close();
  }

  /** Replace :name markers by ? and collect the bind values in order (positional ? are taken from params "1","2",...). */
  /**
   * Rewrite named placeholders as positional ones, collecting the values in order.
   *
   * <p>Public because generated repositories need it: the converter emits {@code :name} and JDBC only
   * understands {@code ?}, and having the generator reimplement the rewrite would give two versions of
   * one rule.
   */
  public static String bindNamed(String sql, Map<String, Object> params, List<Object> binds) {
    return scan(sql, params, binds);
  }

  /**
   * One pass over the SQL that knows what is code and what is not. `:name` and `?` are placeholders only outside
   * string literals, quoted identifiers and comments.
   *
   * <p>It used to be a regex for `:name` over the whole text plus a quote counter for `?`: `SELECT ' :x'` failed
   * with "missing bind parameter: x", and an apostrophe in a comment (`-- don't`) flipped the quote state, so the
   * `?` after it was never bound. With {@code params} null nothing is bound: placeholders only become `?`
   * (the compile-only check of `validate`).
   */
  static String scan(String sql, Map<String, Object> params, List<Object> binds) {
    StringBuilder out = new StringBuilder(sql.length());
    int positional = 0;
    int i = 0;
    int n = sql.length();
    while (i < n) {
      char c = sql.charAt(i);
      char next = i + 1 < n ? sql.charAt(i + 1) : '\0';
      if (c == '\'' || c == '"') {                       // literal or quoted identifier; a doubled quote stays inside
        int j = i + 1;
        while (j < n) {
          if (sql.charAt(j) == c) {
            if (j + 1 < n && sql.charAt(j + 1) == c) { j += 2; continue; }
            break;
          }
          j++;
        }
        j = Math.min(j + 1, n);
        out.append(sql, i, j);
        i = j;
      } else if (c == '-' && next == '-') {               // line comment
        int j = sql.indexOf('\n', i);
        j = j < 0 ? n : j;
        out.append(sql, i, j);
        i = j;
      } else if (c == '/' && next == '*') {               // block comment
        int j = sql.indexOf("*/", i + 2);
        j = j < 0 ? n : j + 2;
        out.append(sql, i, j);
        i = j;
      } else if (c == ':' && (Character.isLetterOrDigit(next) || next == '_')
          && (i == 0 || (sql.charAt(i - 1) != ':' && !Character.isLetterOrDigit(sql.charAt(i - 1))
              && sql.charAt(i - 1) != '_'))) {          // `:name`, but not the `::type` of a PostgreSQL cast
        int j = i + 1;
        while (j < n && (Character.isLetterOrDigit(sql.charAt(j)) || sql.charAt(j) == '_')) j++;
        String name = sql.substring(i + 1, j);
        if (params != null) {
          if (!params.containsKey(name)) throw new IllegalArgumentException("missing bind parameter: " + name);
          binds.add(params.get(name));
        }
        out.append('?');
        i = j;
      } else if (c == '?') {
        positional++;
        if (params != null) {
          String key = Integer.toString(positional);
          if (!params.containsKey(key)) throw new IllegalArgumentException("missing positional parameter " + key);
          binds.add(params.get(key));
        }
        out.append('?');
        i++;
      } else {
        out.append(c);
        i++;
      }
    }
    return out.toString();
  }

  @Override
  public void close() throws Exception {
    try { reader.close(); } catch (Exception ignored) { }
    try (Statement s = h2.createStatement()) { s.execute("SHUTDOWN"); } catch (Exception ignored) { }
    h2.close();
  }
}
