import com.scalar.migrate.runtime.Plan;
import com.scalar.migrate.runtime.Residual;
import com.scalar.migrate.runtime.Rows;
import java.util.*;

/** Cost / benefit of Residual's H2 indexes at several data sizes (synthetic S04-like tables). */
public class H2Index {
  static final Random R = new Random(42);

  static Plan.Fetch spec(String table, List<List<String>> ix) {
    Plan.Fetch f = new Plan.Fetch();
    f.table = table;
    f.column_types = Map.of();
    f.index_columns = ix;
    return f;
  }

  static Rows rows(List<String> cols, List<String> types) {
    Rows r = new Rows();
    r.columns.addAll(cols);
    for (int i = 0; i < cols.size(); i++) r.types.put(cols.get(i), types.get(i));
    return r;
  }

  static Rows customers, orders, items;

  static void gen(int nOrders) {
    int nCust = Math.max(1, nOrders / 10);
    customers = rows(List.of("customer_id", "region", "name"), List.of("INT", "TEXT", "TEXT"));
    String[] regions = {"EAST", "WEST", "NORTH", "SOUTH"};
    for (int i = 1; i <= nCust; i++) customers.rows.add(new Object[] {i, regions[i % 4], "customer-" + i});
    orders = rows(List.of("order_id", "customer_id", "status", "total"), List.of("INT", "INT", "TEXT", "DOUBLE"));
    items = rows(List.of("order_id", "line_no", "qty", "unit_price"), List.of("INT", "INT", "INT", "DOUBLE"));
    String[] st = {"NEW", "PAID", "SHIPPED", "CANCELLED"};
    for (int o = 1; o <= nOrders; o++) {
      orders.rows.add(new Object[] {o, 1 + R.nextInt(nCust), st[R.nextInt(4)], R.nextInt(100000) / 100.0});
      int lines = R.nextInt(5); // 0..4 lines, some orders have none
      for (int l = 1; l <= lines; l++) items.rows.add(new Object[] {o, l, 1 + R.nextInt(9), R.nextInt(10000) / 100.0});
    }
  }

  static long usedHeap() {
    for (int i = 0; i < 3; i++) System.gc();
    Runtime rt = Runtime.getRuntime();
    return rt.totalMemory() - rt.freeMemory();
  }

  static final String S04 = "SELECT c.region, COUNT(DISTINCT o.order_id) AS orders, SUM(i.qty * i.unit_price) AS amount "
      + "FROM customers c JOIN orders o ON o.customer_id = c.customer_id JOIN order_items i ON i.order_id = o.order_id "
      + "WHERE o.status <> 'CANCELLED' GROUP BY c.region HAVING SUM(i.qty * i.unit_price) > 0";
  static final String S05 = "SELECT o.order_id FROM orders o LEFT JOIN order_items i ON i.order_id = o.order_id WHERE i.order_id IS NULL";
  static final String S06 = "SELECT c.customer_id, (SELECT COUNT(*) FROM orders o WHERE o.customer_id = c.customer_id) AS n FROM customers c";
  static final String S08 = "SELECT customer_id, SUM(CASE WHEN status = 'PAID' THEN total ELSE 0 END) AS paid FROM orders GROUP BY customer_id";
  static final String S03 = "SELECT order_id, total FROM orders ORDER BY order_id OFFSET 100 ROWS FETCH NEXT 20 ROWS ONLY";

  /** One request: load the fetched tables, (maybe) build indexes, run the query. Returns ms per phase. */
  static long[] run(boolean indexed, String sql, boolean join) throws Exception {
    long t0 = System.nanoTime(), t1, t2, t3;
    try (Residual h2 = new Residual("PostgreSQL")) {
      if (join) {
        h2.load(spec("customers", indexed ? List.of(List.of("customer_id")) : null), customers);
        h2.load(spec("order_items", indexed ? List.of(List.of("order_id", "line_no")) : null), items);
      }
      h2.load(spec("orders", indexed ? (join ? List.of(List.of("order_id"), List.of("customer_id")) : List.of(List.of("order_id"))) : null), orders);
      t1 = System.nanoTime();
      h2.query("SELECT 1", Map.of()); // builds the indexes
      t2 = System.nanoTime();
      h2.query(sql, Map.of());
      t3 = System.nanoTime();
    }
    return new long[] {(t1 - t0) / 1_000_000, (t2 - t1) / 1_000_000, (t3 - t2) / 1_000_000};
  }

  static void report(String label, boolean indexed, String sql, boolean join) throws Exception {
    run(indexed, sql, join); // warmup
    long[] best = null;
    for (int i = 0; i < 3; i++) {
      long[] r = run(indexed, sql, join);
      if (best == null || r[0] + r[1] + r[2] < best[0] + best[1] + best[2]) best = r;
    }
    System.out.printf("  %-26s %-9s load=%6d  index=%6d  query=%7d  total=%7d ms%n", label, indexed ? "index" : "no-index",
        best[0], best[1], best[2], best[0] + best[1] + best[2]);
  }

  /** Heap held by one loaded H2 database, before and after building the indexes. */
  static void memory() throws Exception {
    long base = usedHeap();
    try (Residual h2 = new Residual("PostgreSQL")) {
      h2.load(spec("customers", List.of(List.of("customer_id"))), customers);
      h2.load(spec("order_items", List.of(List.of("order_id", "line_no"))), items);
      h2.load(spec("orders", List.of(List.of("order_id"), List.of("customer_id"))), orders);
      long loaded = usedHeap();
      h2.query("SELECT 1", Map.of());
      long indexed = usedHeap();
      System.out.printf("  heap: rows in H2 %,d KB, +indexes %,d KB (%.0f%%)%n", (loaded - base) / 1024, (indexed - loaded) / 1024,
          100.0 * (indexed - loaded) / Math.max(1, loaded - base));
    }
  }

  public static void main(String[] a) throws Exception {
    for (String s : a[0].split(",")) {
      int n = Integer.parseInt(s);
      gen(n);
      System.out.printf("== orders %,d  items %,d  customers %,d%n", orders.rows.size(), items.rows.size(), customers.rows.size());
      memory();
      report("S08 single-table agg", false, S08, false);
      report("S08 single-table agg", true, S08, false);
      report("S03 OFFSET paging", false, S03, false);
      report("S03 OFFSET paging", true, S03, false);
      if (n <= 5000) report("S04 3-table join", false, S04, true);
      report("S04 3-table join", true, S04, true);
      if (n <= 5000) report("S05 anti-join", false, S05, true);
      report("S05 anti-join", true, S05, true);
      if (n <= 20000) report("S06 correlated subquery", false, S06, true);
      report("S06 correlated subquery", true, S06, true);
    }
  }
}
