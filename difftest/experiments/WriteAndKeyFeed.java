import java.sql.*;
import java.util.*;
import java.util.concurrent.*;

/**
 * 1. Confirm which DML forms ScalarDB SQL rejects (on the running cluster).
 * 2. Cost of the read-compute-write rewrite (single row, and N rows by key in one transaction).
 * 3. Key-feed fetch: order_items by partition key for K orders, sequential vs parallel.
 */
public class WriteAndKeyFeed {
  static String PROPS;
  static final int WARMUP = 2, ITER = 9;

  static Connection open() throws Exception {
    Connection c = DriverManager.getConnection("jdbc:scalardb:" + PROPS);
    c.setAutoCommit(false);
    return c;
  }

  interface Body { int run() throws Exception; }

  static void time(String label, Body b) throws Exception {
    for (int i = 0; i < WARMUP; i++) b.run();
    List<Double> ms = new ArrayList<>();
    int n = 0;
    for (int i = 0; i < ITER; i++) {
      long t = System.nanoTime();
      n = b.run();
      ms.add((System.nanoTime() - t) / 1e6);
    }
    Collections.sort(ms);
    System.out.printf("%-56s n=%-6d p50=%8.1f ms  min=%8.1f  max=%8.1f%n", label, n, ms.get(ms.size() / 2), ms.get(0), ms.get(ms.size() - 1));
  }

  static void probe(Connection c, String sql) {
    try (Statement s = c.createStatement()) {
      s.execute(sql);
      c.rollback();
      System.out.println("  ACCEPTED  " + sql);
    } catch (Exception e) {
      try { c.rollback(); } catch (Exception ignored) { }
      String m = String.valueOf(e.getMessage()).replace('\n', ' ');
      System.out.println("  REJECTED  " + sql + "\n            -> " + m.substring(0, Math.min(150, m.length())));
    }
  }

  public static void main(String[] a) throws Exception {
    PROPS = a[0];
    Connection c = open();

    System.out.println("== 1. forms ScalarDB SQL rejects");
    probe(c, "UPDATE stock SET qty = qty - 5 WHERE warehouse_id = 1 AND product_id = 103");
    probe(c, "UPDATE stock SET qty = 45 WHERE warehouse_id = 1 AND product_id = 103");
    probe(c, "DELETE FROM order_items WHERE order_id IN (SELECT order_id FROM orders WHERE status = 'CANCELLED')");
    probe(c, "INSERT INTO stock (warehouse_id, product_id, qty) SELECT 9, product_id, 0 FROM products");
    probe(c, "SELECT o.order_id, i.line_no FROM orders o JOIN order_items i ON i.order_id = o.order_id WHERE o.order_id = 1001");
    probe(c, "SELECT customer_id, COUNT(*) FROM orders GROUP BY customer_id");

    System.out.println("== 2. read-compute-write");
    time("U04 as read + UPDATE literal (1 row, 1 tx)", () -> {
      int qty;
      try (PreparedStatement ps = c.prepareStatement("SELECT qty FROM stock WHERE warehouse_id = ? AND product_id = ?")) {
        ps.setInt(1, 1); ps.setInt(2, 103);
        try (ResultSet rs = ps.executeQuery()) { rs.next(); qty = rs.getInt(1); }
      }
      try (PreparedStatement ps = c.prepareStatement("UPDATE stock SET qty = ? WHERE warehouse_id = ? AND product_id = ?")) {
        ps.setInt(1, qty - 5 < 0 ? 1000 : qty - 5); ps.setInt(2, 1); ps.setInt(3, 103);
        ps.executeUpdate();
      }
      c.commit();
      return 1;
    });
    time("UPDATE literal only (1 row, 1 tx; the U01 shape)", () -> {
      try (PreparedStatement ps = c.prepareStatement("UPDATE stock SET qty = ? WHERE warehouse_id = ? AND product_id = ?")) {
        ps.setInt(1, 500); ps.setInt(2, 1); ps.setInt(3, 104);
        ps.executeUpdate();
      }
      c.commit();
      return 1;
    });
    // N rows: read the rows of a key range (cross-partition scan), then update each by primary key
    int[] firstOrders = new int[2000];
    int k0 = 0;
    try (PreparedStatement ps = c.prepareStatement("SELECT order_id FROM orders"); ResultSet rs = ps.executeQuery()) {
      while (rs.next() && k0 < firstOrders.length) firstOrders[k0++] = rs.getInt(1);
    }
    c.commit();
    for (int orders : new int[] {4, 40, 400}) {
      final int nOrders = orders;
      time("read by key + UPDATE by key, " + orders + " orders' items (1 tx)", () -> {
        List<int[]> rows = new ArrayList<>();
        try (PreparedStatement ps = c.prepareStatement("SELECT order_id, line_no, qty FROM order_items WHERE order_id = ?")) {
          for (int i = 0; i < nOrders; i++) {
            ps.setInt(1, firstOrders[i]);
            try (ResultSet rs = ps.executeQuery()) { while (rs.next()) rows.add(new int[] {rs.getInt(1), rs.getInt(2), rs.getInt(3)}); }
          }
        }
        try (PreparedStatement ps = c.prepareStatement("UPDATE order_items SET qty = ? WHERE order_id = ? AND line_no = ?")) {
          for (int[] r : rows) { ps.setInt(1, r[2]); ps.setInt(2, r[0]); ps.setInt(3, r[1]); ps.executeUpdate(); }
        }
        c.commit();
        return rows.size();
      });
    }

    System.out.println("== 3. key-feed fetch of order_items by partition key");
    List<Connection> conns = new ArrayList<>();
    for (int i = 0; i < 8; i++) conns.add(open());
    ExecutorService pool = Executors.newFixedThreadPool(8);
    for (int keys : new int[] {100, 1000}) {
      final int nKeys = keys;
      time("sequential, 1 tx, " + keys + " keys", () -> {
        int n = 0;
        try (PreparedStatement ps = c.prepareStatement("SELECT order_id, line_no, qty, unit_price FROM order_items WHERE order_id = ?")) {
          for (int i = 0; i < nKeys; i++) {
            ps.setInt(1, firstOrders[i]);
            try (ResultSet rs = ps.executeQuery()) { while (rs.next()) n++; }
          }
        }
        c.commit();
        return n;
      });
      for (int threads : new int[] {4, 8}) {
        final int t = threads;
        time("parallel " + threads + " threads/tx, " + keys + " keys", () -> {
          List<Future<Integer>> fs = new ArrayList<>();
          for (int w = 0; w < t; w++) {
            final int part = w;
            Connection cw = conns.get(w);
            fs.add(pool.submit(() -> {
              int n = 0;
              try (PreparedStatement ps = cw.prepareStatement("SELECT order_id, line_no, qty, unit_price FROM order_items WHERE order_id = ?")) {
                for (int i = 0; i < nKeys; i++) {
                  if (i % t != part) continue;
                  ps.setInt(1, firstOrders[i]);
                  try (ResultSet rs = ps.executeQuery()) { while (rs.next()) n++; }
                }
              }
              cw.commit();
              return n;
            }));
          }
          int n = 0;
          for (Future<Integer> f : fs) n += f.get();
          return n;
        });
      }
    }
    pool.shutdown();
    for (Connection x : conns) x.close();
    c.close();
    System.exit(0);
  }
}
