package com.scalar.migrate.runtime;

import com.scalar.db.api.ConditionBuilder;
import com.scalar.db.api.ConditionSetBuilder;
import com.scalar.db.api.ConditionalExpression;
import com.scalar.db.api.ConditionalExpression.Operator;
import com.scalar.db.api.DistributedTransaction;
import com.scalar.db.api.DistributedTransactionAdmin;
import com.scalar.db.api.DistributedTransactionManager;
import com.scalar.db.api.Result;
import com.scalar.db.api.Scan;
import com.scalar.db.api.ScanBuilder;
import com.scalar.db.api.TableMetadata;
import com.scalar.db.io.Column;
import com.scalar.db.io.DataType;
import com.scalar.db.io.Key;
import com.scalar.db.service.TransactionFactory;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.Set;
import java.util.List;
import java.util.Map;

/**
 * Fetch through the ScalarDB Core transactional API (Apache 2, no license). Only the pushdown predicates that the
 * Core API can express (partition key equality, AND of simple conditions) are used; the residual engine re-applies
 * the full predicate anyway, so the fetch only has to be a superset.
 */
public class CoreFetcher implements Fetcher {
  private final DistributedTransactionManager manager;  // null when joining the caller's transaction
  private final DistributedTransactionAdmin admin;
  private final boolean ownsTransaction;
  private DistributedTransaction tx;

  /** Own everything: open a manager from the properties file and start a read-only transaction in {@link #begin()}. */
  public CoreFetcher(String propertiesPath) throws Exception {
    TransactionFactory factory = TransactionFactory.create(propertiesPath);
    manager = factory.getTransactionManager();
    admin = factory.getTransactionAdmin();
    ownsTransaction = true;
  }

  private CoreFetcher(DistributedTransaction tx, DistributedTransactionAdmin admin) {
    this.manager = null;
    this.admin = admin;
    this.tx = tx;
    this.ownsTransaction = false;
  }

  /**
   * Run the plan's fetches inside a transaction the caller already began, so that the reads share the caller's
   * snapshot and rollback scope. Neither the transaction nor the admin is closed by this fetcher.
   *
   * <p>ScalarDB's Consensus Commit refuses to scan rows the same transaction has already written or deleted
   * ("Scanning data already-written or already-deleted by the same transaction is not allowed"), so a plan whose
   * fetch overlaps the routine's own writes fails at run time. {@link PlanRunner} turns that into a
   * {@link ScanAfterWriteException} naming the table.
   */
  public static CoreFetcher joining(DistributedTransaction tx, DistributedTransactionAdmin admin) {
    return new CoreFetcher(
        java.util.Objects.requireNonNull(tx, "tx"), java.util.Objects.requireNonNull(admin, "admin"));
  }

  @Override
  public boolean ownsTransaction() {
    return ownsTransaction;
  }

  @Override
  public void begin() throws Exception {
    if (!ownsTransaction) throw new IllegalStateException("this fetcher joined a transaction the caller owns");
    // plans only read: a read-only transaction skips the Coordinator write at commit (ScalarDB 3.16+)
    tx = manager.startReadOnly();
  }

  @Override
  public Rows fetch(Plan.Fetch spec, Map<String, Object> params) throws Exception {
    String ns = spec.namespace;
    TableMetadata md = admin.getTableMetadata(ns, spec.table);
    if (md == null) throw new IllegalStateException("table not found in ScalarDB: " + ns + "." + spec.table);

    Scan scan = buildScan(ns, spec, md, params);

    List<String> cols = spec.columns == null || spec.columns.isEmpty() ? new ArrayList<>(md.getColumnNames()) : spec.columns;
    Rows out = new Rows();
    for (String c : cols) {
      out.columns.add(c);
      out.types.put(c, md.getColumnDataType(c).name());
    }
    List<Result> results = tx.scan(scan);
    if (results.size() > spec.max_rows) throw new RowLimitExceededException(spec.table, spec.max_rows);
    for (Result r : results) {
      Object[] row = new Object[cols.size()];
      for (int i = 0; i < cols.size(); i++) row[i] = Values.read(r, cols.get(i), md.getColumnDataType(cols.get(i)));
      out.rows.add(row);
    }
    return out;
  }

  /**
   * The scan for one fetch: the narrowest access ScalarDB offers for the plan's predicates.
   *
   * <p>It used to be one of two things. With the partition key covered, a bare partition scan -- every other
   * predicate dropped, so a wide partition was read whole and tripped the row limit even when the filtered
   * result was small. Otherwise {@code Scan.all().where(...)}, even with an {@code =} on an indexed column: the
   * converter labels that access INDEX and splits key lists into per-value index fetches for Cassandra, and each
   * of those ran as a filtered scan of the whole table -- the one access pattern that is not to be used there.
   */
  static Scan buildScan(String ns, Plan.Fetch spec, TableMetadata md, Map<String, Object> params) {
    Map<String, Object> eq = new LinkedHashMap<>();
    List<Plan.Predicate> ands = new ArrayList<>();
    for (Object p : spec.predicates == null ? List.of() : spec.predicates) {
      if (p instanceof Map) { // simple predicate (Gson parses unknown Object as Map / List)
        Plan.Predicate pr = Runner.GSON.fromJson(Runner.GSON.toJson(p), Plan.Predicate.class);
        Object v = resolve(pr.value, params);
        if ("=".equals(pr.op) && v != null && Values.representable(md.getColumnDataType(pr.column), v)) {
          eq.putIfAbsent(pr.column, v);
        }
        ands.add(pr);
      } // OR groups are left to the residual engine
    }
    int limit = spec.max_rows + 1;
    java.util.function.Function<Set<String>, Set<ConditionalExpression>> others = keyed -> {
      Set<ConditionalExpression> conds = new LinkedHashSet<>();
      for (Plan.Predicate pr : ands) {
        if ("=".equals(pr.op) && keyed.contains(pr.column)) continue;   // already the key of the scan
        ConditionalExpression cond = condition(pr, md, params);
        if (cond != null) conds.add(cond);
      }
      return conds;
    };

    if (md.getPartitionKeyNames().stream().allMatch(eq::containsKey)) {
      Key.Builder kb = Key.newBuilder();
      for (String c : md.getPartitionKeyNames()) kb.add(Values.column(c, md.getColumnDataType(c), eq.get(c)));
      var scan = Scan.newBuilder().namespace(ns).table(spec.table).partitionKey(kb.build());
      // The first clustering column bounds the scan through start / end -- the range the storage can seek to.
      // Conditions on key columns are not put into where(): the range says it, and the residual SQL applies
      // every predicate again anyway, so leaving one out only costs rows, never correctness.
      Set<String> keyed = new java.util.HashSet<>(md.getPartitionKeyNames());
      keyed.addAll(md.getClusteringKeyNames());
      String first = md.getClusteringKeyNames().isEmpty() ? null : md.getClusteringKeyNames().iterator().next();
      // only for an ascending column: how start / end read on a descending one has not been checked against a
      // cluster, and a bound that is wrong there would drop rows rather than cost some
      if (first != null && md.getClusteringOrder(first) != Scan.Ordering.Order.ASC) first = null;
      for (Plan.Predicate pr : ands) {
        if (first == null || !first.equals(pr.column)) continue;
        Object v = resolve(pr.value, params);
        if (v == null || !Values.representable(md.getColumnDataType(first), v)) continue;
        Key bound = Key.newBuilder().add(Values.column(first, md.getColumnDataType(first), v)).build();
        switch (pr.op) {
          case "=": scan = scan.start(bound, true).end(bound, true); break;
          case ">": scan = scan.start(bound, false); break;
          case ">=": scan = scan.start(bound, true); break;
          case "<": scan = scan.end(bound, false); break;
          case "<=": scan = scan.end(bound, true); break;
          default: break;
        }
      }
      Set<ConditionalExpression> conds = new LinkedHashSet<>();
      for (Plan.Predicate pr : ands) {
        if (keyed.contains(pr.column)) continue;
        ConditionalExpression cond = condition(pr, md, params);
        if (cond != null) conds.add(cond);
      }
      return conds.isEmpty() ? scan.limit(limit).build()
          : scan.where(ConditionSetBuilder.andConditionSet(conds).build()).limit(limit).build();
    }
    String indexed = md.getSecondaryIndexNames().stream().filter(eq::containsKey).findFirst().orElse(null);
    if (indexed != null) {
      Key key = Key.newBuilder().add(Values.column(indexed, md.getColumnDataType(indexed), eq.get(indexed))).build();
      var scan = Scan.newBuilder().namespace(ns).table(spec.table).indexKey(key);
      Set<ConditionalExpression> conds = others.apply(Set.of(indexed));
      return conds.isEmpty() ? scan.limit(limit).build()
          : scan.where(ConditionSetBuilder.andConditionSet(conds).build()).limit(limit).build();
    }
    ScanBuilder.BuildableScanAll all = Scan.newBuilder().namespace(ns).table(spec.table).all();
    Set<ConditionalExpression> conds = others.apply(Set.of());
    return conds.isEmpty() ? all.limit(limit).build()
        : all.where(ConditionSetBuilder.andConditionSet(conds).build()).limit(limit).build();
  }

  private static ConditionalExpression condition(Plan.Predicate pr, TableMetadata md, Map<String, Object> params) {
    DataType type = md.getColumnDataType(pr.column);
    Object v = resolve(pr.value, params);
    // The fetch has to be a superset of what the residual SQL needs. `qty < 1.5` narrowed to the INT column's
    // type became `qty < 1`, and the rows with qty = 1 were never fetched. A value the column type cannot hold
    // exactly is not pushed down; the residual engine still applies the predicate.
    if (v != null && !Values.representable(type, v)) return null;
    switch (pr.op) {
      case "=": return ConditionBuilder.buildConditionalExpression(Values.column(pr.column, type, v), Operator.EQ);
      case "<>": return ConditionBuilder.buildConditionalExpression(Values.column(pr.column, type, v), Operator.NE);
      case ">": return ConditionBuilder.buildConditionalExpression(Values.column(pr.column, type, v), Operator.GT);
      case ">=": return ConditionBuilder.buildConditionalExpression(Values.column(pr.column, type, v), Operator.GTE);
      case "<": return ConditionBuilder.buildConditionalExpression(Values.column(pr.column, type, v), Operator.LT);
      case "<=": return ConditionBuilder.buildConditionalExpression(Values.column(pr.column, type, v), Operator.LTE);
      case "LIKE": return ConditionBuilder.column(pr.column).isLikeText(v.toString());
      case "NOT LIKE": return ConditionBuilder.column(pr.column).isNotLikeText(v.toString());
      case "IS NULL": return ConditionBuilder.buildConditionalExpression(Values.column(pr.column, type, null), Operator.IS_NULL);
      case "IS NOT NULL": return ConditionBuilder.buildConditionalExpression(Values.column(pr.column, type, null), Operator.IS_NOT_NULL);
      default: return null; // BETWEEN etc.: residual engine handles it
    }
  }

  @SuppressWarnings("unchecked")
  static Object resolve(Object value, Map<String, Object> params) {
    if (value instanceof Map && ((Map<String, Object>) value).containsKey("param")) {
      String name = ((Map<String, Object>) value).get("param").toString();
      if (!params.containsKey(name)) throw new IllegalArgumentException("missing bind parameter: " + name);
      return params.get(name);
    }
    return value;
  }

  @Override
  public void commit() throws Exception {
    if (!ownsTransaction) throw new IllegalStateException("this fetcher joined a transaction the caller owns");
    tx.commit();
  }

  @Override
  public void rollback() {
    if (!ownsTransaction) throw new IllegalStateException("this fetcher joined a transaction the caller owns");
    try { if (tx != null) tx.abort(); } catch (Exception ignored) { }
  }

  @Override
  public void close() {
    if (!ownsTransaction) return;  // the caller owns the transaction and the admin
    manager.close();
    admin.close();
  }

  /** Used by the loader: upsert JSON rows (transactional, through ScalarDB). */
  public int load(String ns, String table, List<Map<String, Object>> rows) throws Exception {
    TableMetadata md = admin.getTableMetadata(ns, table);
    if (md == null) throw new IllegalStateException("table not found in ScalarDB: " + ns + "." + table);
    DistributedTransaction t = manager.start();
    try {
      for (Map<String, Object> row : rows) {
        Key.Builder pk = Key.newBuilder();
        for (String c : md.getPartitionKeyNames()) pk.add(Values.column(c, md.getColumnDataType(c), row.get(c)));
        com.scalar.db.api.UpsertBuilder.Buildable b = com.scalar.db.api.Upsert.newBuilder().namespace(ns).table(table).partitionKey(pk.build());
        if (!md.getClusteringKeyNames().isEmpty()) {
          Key.Builder ck = Key.newBuilder();
          for (String c : md.getClusteringKeyNames()) ck.add(Values.column(c, md.getColumnDataType(c), row.get(c)));
          b = b.clusteringKey(ck.build());
        }
        for (String c : md.getColumnNames()) {
          if (md.getPartitionKeyNames().contains(c) || md.getClusteringKeyNames().contains(c)) continue;
          Column<?> col = Values.column(c, md.getColumnDataType(c), row.get(c));
          b = b.value(col);
        }
        t.upsert(b.build());
      }
      t.commit();
      return rows.size();
    } catch (Exception e) {
      try { t.abort(); } catch (Exception ignored) { }
      throw e;
    }
  }
}
