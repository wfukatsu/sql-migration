import java.sql.*;
import java.util.*;
import java.util.concurrent.*;

/** Parallel fetch experiment against the running ScalarDB Cluster (bench namespace, S04's three tables). */
public class ParFetch {
  static String PROPS;
  static final int WARMUP = 2, ITER = Integer.getInteger("iter", 7);

  static int fetch(Connection c, String sql) throws Exception {
    int n = 0;
    try (PreparedStatement ps = c.prepareStatement(sql); ResultSet rs = ps.executeQuery()) {
      int cols = rs.getMetaData().getColumnCount();
      while (rs.next()) { for (int i = 1; i <= cols; i++) rs.getObject(i); n++; }
    }
    return n;
  }

  static Connection open() throws Exception {
    Connection c = DriverManager.getConnection("jdbc:scalardb:" + PROPS);
    c.setAutoCommit(false);
    return c;
  }

  interface Body { int run() throws Exception; }

  static void time(String label, Body b) throws Exception {
    for (int i = 0; i < WARMUP; i++) b.run();
    List<Double> ms = new ArrayList<>();
    int rows = 0;
    for (int i = 0; i < ITER; i++) {
      long t = System.nanoTime();
      rows = b.run();
      ms.add((System.nanoTime() - t) / 1e6);
    }
    Collections.sort(ms);
    System.out.printf("%-48s rows=%-7d p50=%8.1f ms  min=%8.1f  max=%8.1f%n", label, rows, ms.get(ms.size() / 2), ms.get(0), ms.get(ms.size() - 1));
  }

  /** Each SQL in its own thread, connection and transaction. */
  static int parallel(List<Connection> conns, List<String> sqls, ExecutorService pool) throws Exception {
    List<Future<Integer>> fs = new ArrayList<>();
    for (int i = 0; i < sqls.size(); i++) {
      Connection c = conns.get(i);
      String sql = sqls.get(i);
      fs.add(pool.submit(() -> { int n = fetch(c, sql); c.commit(); return n; }));
    }
    int n = 0;
    for (Future<Integer> f : fs) n += f.get();
    return n;
  }

  public static void main(String[] a) throws Exception {
    PROPS = a[0];
    String customers = "SELECT customer_id, region FROM customers";
    String orders = "SELECT order_id, customer_id, status FROM orders WHERE status <> 'CANCELLED'";
    String items = "SELECT order_id, line_no, qty, unit_price FROM order_items";
    List<Connection> conns = new ArrayList<>();
    for (int i = 0; i < 8; i++) conns.add(open());
    ExecutorService pool = Executors.newFixedThreadPool(8);
    Connection c0 = conns.get(0);

    // the key range of order_items, for the range-split scans
    int max = 0;
    try (PreparedStatement ps = c0.prepareStatement("SELECT order_id FROM orders"); ResultSet rs = ps.executeQuery()) {
      while (rs.next()) max = Math.max(max, rs.getInt(1));
    }
    c0.commit();
    System.out.println("max order_id = " + max);

    time("customers alone", () -> { int n = fetch(c0, customers); c0.commit(); return n; });
    time("orders alone", () -> { int n = fetch(c0, orders); c0.commit(); return n; });
    time("order_items alone", () -> { int n = fetch(c0, items); c0.commit(); return n; });
    time("S04 3 tables, sequential, 1 transaction", () -> {
      int n = fetch(c0, customers) + fetch(c0, orders) + fetch(c0, items);
      c0.commit();
      return n;
    });
    time("S04 3 tables, parallel, 3 transactions", () -> parallel(conns, List.of(customers, orders, items), pool));

    final int top = max;
    for (int k : new int[] {1, 2, 4, 8}) {
      List<String> parts = new ArrayList<>();
      int step = top / k + 1;
      for (int p = 0; p < k; p++) {
        parts.add(items + " WHERE order_id >= " + (p * step) + " AND order_id < " + ((p + 1) * step));
      }
      time("order_items split by order_id into " + k + " (parallel)", () -> parallel(conns, parts, pool));
    }
    time("order_items split into 4, sequential 1 tx", () -> {
      int n = 0, step = top / 4 + 1;
      for (int p = 0; p < 4; p++) n += fetch(c0, items + " WHERE order_id >= " + (p * step) + " AND order_id < " + ((p + 1) * step));
      c0.commit();
      return n;
    });
    pool.shutdown();
    for (Connection c : conns) c.close();
    System.exit(0);
  }
}
