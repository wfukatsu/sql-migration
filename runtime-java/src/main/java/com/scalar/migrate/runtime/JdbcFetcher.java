package com.scalar.migrate.runtime;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.ResultSetMetaData;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * Fetch through ScalarDB SQL (JDBC driver -> ScalarDB Cluster). This is the production path: the plan's
 * scalardb_sql is executed verbatim inside one JDBC transaction. Requires a licensed ScalarDB Cluster.
 */
public class JdbcFetcher implements Fetcher {
  private final Connection conn;
  private final boolean ownsTransaction;

  /** Own everything: open a connection from the properties file and end its transaction here. */
  public JdbcFetcher(String propertiesPath) throws Exception {
    conn = DriverManager.getConnection("jdbc:scalardb:" + propertiesPath);
    conn.setAutoCommit(false);
    ownsTransaction = true;
  }

  private JdbcFetcher(Connection conn) {
    this.conn = conn;
    this.ownsTransaction = false;
  }

  /**
   * Run the plan's fetches on a connection whose transaction the caller owns, so that the reads see the routine's
   * own writes and share its rollback scope. The connection is neither committed nor closed by this fetcher.
   *
   * <p>The same Consensus Commit restriction as {@link CoreFetcher#joining} applies underneath: a fetch that scans
   * rows already written by this transaction fails.
   */
  public static JdbcFetcher joining(Connection conn) {
    return new JdbcFetcher(java.util.Objects.requireNonNull(conn, "conn"));
  }

  @Override
  public boolean ownsTransaction() {
    return ownsTransaction;
  }

  @Override
  public void begin() {
    // a JDBC transaction starts implicitly with the first statement
  }

  @Override
  public Rows fetch(Plan.Fetch spec, Map<String, Object> params) throws Exception {
    List<Object> binds = new ArrayList<>();
    String sql = Residual.bindNamed(spec.scalardb_sql, params, binds);
    try (PreparedStatement ps = conn.prepareStatement(sql)) {
      for (int i = 0; i < binds.size(); i++) ps.setObject(i + 1, binds.get(i));
      try (ResultSet rs = ps.executeQuery()) {
        ResultSetMetaData m = rs.getMetaData();
        Rows out = new Rows();
        for (int i = 1; i <= m.getColumnCount(); i++) {
          out.columns.add(m.getColumnLabel(i));
          if (spec.column_types != null && spec.column_types.containsKey(m.getColumnLabel(i)))
            out.types.put(m.getColumnLabel(i), spec.column_types.get(m.getColumnLabel(i)));
        }
        while (rs.next()) {
          if (out.rows.size() >= spec.max_rows) throw new RowLimitExceededException(spec.table, spec.max_rows);
          Object[] row = new Object[out.columns.size()];
          for (int i = 0; i < row.length; i++) row[i] = rs.getObject(i + 1);
          out.rows.add(row);
        }
        return out;
      }
    }
  }

  @Override
  public void commit() throws Exception {
    if (!ownsTransaction) throw new IllegalStateException("this fetcher joined a transaction the caller owns");
    conn.commit();
  }

  @Override
  public void rollback() {
    if (!ownsTransaction) throw new IllegalStateException("this fetcher joined a transaction the caller owns");
    try { conn.rollback(); } catch (Exception ignored) { }
  }

  @Override
  public void close() throws Exception {
    if (!ownsTransaction) return;  // the caller owns the connection
    conn.close();
  }
}
