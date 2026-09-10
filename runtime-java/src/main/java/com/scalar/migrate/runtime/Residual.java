package com.scalar.migrate.runtime;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.ResultSetMetaData;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.HashSet;
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
  private static final Pattern NAMED = Pattern.compile("(?<![:\\w])[:](\\w+)");
  private final Connection h2;
  private final Set<String> created = new HashSet<>();

  public Residual(String mode) throws Exception {
    h2 = DriverManager.getConnection("jdbc:h2:mem:" + UUID.randomUUID() + ";MODE=" + mode + ";DATABASE_TO_UPPER=FALSE");
    if ("Oracle".equalsIgnoreCase(mode)) OracleFunctions.register(h2);
  }

  /** Create the table on first use (typed from ScalarDB types when known, else from the Java values) and load rows. */
  public void load(Plan.Fetch spec, Rows rows) throws Exception {
    String table = spec.table;
    boolean firstLoad = !created.contains(table.toLowerCase());
    if (firstLoad) {
      StringBuilder ddl = new StringBuilder("CREATE TABLE " + table + " (");
      for (int i = 0; i < rows.columns.size(); i++) {
        String c = rows.columns.get(i);
        String type = rows.types.containsKey(c) ? Values.h2Type(rows.types.get(c))
            : spec.column_types != null && spec.column_types.containsKey(c) ? Values.h2Type(spec.column_types.get(c))
            : Values.h2TypeOf(rows.rows.isEmpty() ? null : rows.rows.get(0)[i]);
        ddl.append(i > 0 ? ", " : "").append(c).append(' ').append(type);
      }
      ddl.append(')');
      try (Statement s = h2.createStatement()) { s.execute(ddl.toString()); }
      created.add(table.toLowerCase());
    }
    if (rows.rows.isEmpty()) return;
    String cols = String.join(", ", rows.columns);
    String marks = String.join(", ", java.util.Collections.nCopies(rows.columns.size(), "?"));
    // One fetch returns distinct rows, so the first load of a table is a plain INSERT. Only a second fetch into the
    // same table (several scopes referencing it) needs MERGE to de-duplicate, and MERGE keyed on every column has
    // to scan the table per row -- quadratic in the row count, so it must not be used for the bulk load.
    String sql = firstLoad
        ? "INSERT INTO " + table + " (" + cols + ") VALUES (" + marks + ")"
        : "MERGE INTO " + table + " (" + cols + ") KEY (" + cols + ") VALUES (" + marks + ")";
    try (PreparedStatement ps = h2.prepareStatement(sql)) {
      for (Object[] r : rows.rows) {
        for (int i = 0; i < r.length; i++) ps.setObject(i + 1, Values.toH2(r[i]));
        ps.addBatch();
      }
      ps.executeBatch();
    }
  }

  /** Run the residual SQL and return the result as columns + rows (JSON-friendly values). */
  public Map<String, Object> query(String sql, Map<String, Object> params) throws Exception {
    List<Object> binds = new ArrayList<>();
    String bound = bindNamed(sql, params, binds);
    try (PreparedStatement ps = h2.prepareStatement(bound)) {
      for (int i = 0; i < binds.size(); i++) ps.setObject(i + 1, binds.get(i));
      try (ResultSet rs = ps.executeQuery()) {
        ResultSetMetaData m = rs.getMetaData();
        List<String> columns = new ArrayList<>();
        for (int i = 1; i <= m.getColumnCount(); i++) columns.add(m.getColumnLabel(i));
        List<List<Object>> out = new ArrayList<>();
        while (rs.next()) {
          List<Object> row = new ArrayList<>();
          for (int i = 1; i <= columns.size(); i++) row.add(Values.toJson(rs.getObject(i)));
          out.add(row);
        }
        return Map.of("columns", columns, "rows", out);
      }
    }
  }

  /** Compile-only check used by `validate`: prepares the statement against the (empty) tables. */
  public void prepareOnly(String sql) throws Exception {
    List<Object> binds = new ArrayList<>();
    String bound = NAMED.matcher(sql).replaceAll("?");
    h2.prepareStatement(bound).close();
  }

  /** Replace :name markers by ? and collect the bind values in order (positional ? are taken from params "1","2",...). */
  static String bindNamed(String sql, Map<String, Object> params, List<Object> binds) {
    StringBuilder sb = new StringBuilder();
    Matcher m = NAMED.matcher(sql);
    int last = 0;
    int positional = 0;
    while (m.find()) {
      String before = sql.substring(last, m.start());
      positional = countPositional(before, positional, params, binds);
      sb.append(before).append('?');
      String name = m.group(1);
      if (!params.containsKey(name)) throw new IllegalArgumentException("missing bind parameter: " + name);
      binds.add(params.get(name));
      last = m.end();
    }
    String tail = sql.substring(last);
    countPositional(tail, positional, params, binds);
    sb.append(tail);
    return sb.toString();
  }

  private static int countPositional(String fragment, int positional, Map<String, Object> params, List<Object> binds) {
    boolean inString = false;
    for (char ch : fragment.toCharArray()) {
      if (ch == '\'') inString = !inString;
      else if (ch == '?' && !inString) {
        positional++;
        String key = Integer.toString(positional);
        if (!params.containsKey(key)) throw new IllegalArgumentException("missing positional parameter " + key);
        binds.add(params.get(key));
      }
    }
    return positional;
  }

  @Override
  public void close() throws Exception {
    try (Statement s = h2.createStatement()) { s.execute("SHUTDOWN"); } catch (Exception ignored) { }
    h2.close();
  }
}
