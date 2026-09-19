package com.scalar.migrate.plsql;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.reflect.TypeToken;
import java.io.Reader;
import java.io.Writer;
import java.math.BigDecimal;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.ResultSetMetaData;
import java.sql.Statement;
import java.time.LocalDateTime;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import java.util.TreeMap;

/**
 * P3-1: run a generated routine against a real ScalarDB and describe what happened, in the shape P0-4 chose.
 *
 * <p>The Oracle half of the comparison already exists -- P0-5 captured what each scenario does, normalised. This
 * is the other half, and producing the same shape is the whole job: a comparison between two encodings compares
 * the encodings, not the behaviour. So the encoding here deliberately repeats {@code difftest/plsql_run.py}:
 * {@code $dec} for exact numbers, {@code $ts} for timestamps, rows sorted by their encoded form with duplicates
 * kept, and a {@code masked} list naming every column whose value comes from a clock this harness cannot pin.
 *
 * <p>Generated repositories take a plain {@link Connection} (P2-7), so that is what this hands them. The
 * connection carries {@code scalar.db.sql.default_namespace_name}, which is why the generated SQL -- which names
 * tables unqualified, as the PL/SQL did -- resolves without being rewritten.
 */
public final class ScalarDbRunner implements AutoCloseable {
  private static final Gson GSON = new GsonBuilder().serializeNulls().create();

  /** The Oracle side says this too, in {@code difftest/plsql_run.py}; the strings have to agree to compare. */
  static final String MASKED_REASON = "clock the harness cannot pin";

  private final Connection connection;
  private final Map<String, List<String>> primaryKeys;
  private final Map<String, List<String>> columnOrder;

  /**
   * ScalarDB SQL begins a transaction at the first statement and refuses a commit before one (DB-SQL-10015),
   * unlike a plain JDBC driver where an empty commit is a no-op. A scenario with no setup rows would otherwise
   * fail here for a reason that has nothing to do with the routine under test.
   */
  private static final String NO_TRANSACTION = "DB-SQL-10015";

  /**
   * @param propertiesPath ScalarDB SQL client properties (the cluster endpoint, the transaction manager)
   * @param namespace      the namespace the corpus was loaded into, used as the connection's default
   * @param schemaJson     the Schema Loader JSON the corpus was loaded from, read for primary keys
   */
  public ScalarDbRunner(Path propertiesPath, String namespace, Path schemaJson) throws Exception {
    this.connection = DriverManager.getConnection("jdbc:scalardb:" + withNamespace(propertiesPath, namespace));
    this.connection.setAutoCommit(false);
    Map<String, Map<String, Object>> schema = readSchema(schemaJson, namespace);
    this.primaryKeys = primaryKeysOf(schema);
    this.columnOrder = columnOrderOf(schema);
  }

  /**
   * The generated SQL names tables unqualified, because the PL/SQL did. Rather than rewrite that SQL -- which
   * would mean the harness, not ScalarDB, decided what the generated code means -- point the connection at the
   * corpus namespace and let it resolve the names itself.
   */
  private static Path withNamespace(Path propertiesPath, String namespace) throws Exception {
    Properties properties = new Properties();
    try (Reader reader = Files.newBufferedReader(propertiesPath)) {
      properties.load(reader);
    }
    properties.setProperty("scalar.db.sql.default_namespace_name", namespace);
    Path derived = Files.createTempFile("plsql-scalardb-", ".properties");
    derived.toFile().deleteOnExit();
    try (Writer writer = Files.newBufferedWriter(derived)) {
      properties.store(writer, "derived from " + propertiesPath + " with the corpus namespace");
    }
    return derived;
  }

  public Connection connection() {
    return connection;
  }

  /**
   * The corpus tables: what a scenario that names no {@code capture_tables} compares. A table in another
   * namespace (what a DB link led to, {@code limits.yaml: dbLinks}) is compared only when the scenario asks for
   * it by its qualified name, as the Oracle side does.
   */
  public List<String> tables() {
    List<String> local = new ArrayList<>();
    for (String table : primaryKeys.keySet()) {
      if (!table.contains(".")) local.add(table);
    }
    return local;
  }

  /**
   * Empty every corpus table, so a scenario starts from exactly the state its setup describes.
   *
   * <p>Reading and deleting are separated into two transactions on purpose. Consensus Commit refuses to scan
   * data the same transaction has already written (DB-CORE-10106) -- the very rule the capability checker
   * enforces on generated code (P2-4) -- so a harness that deleted from one table and then scanned the next
   * inside one transaction would trip over it. Collect every key first, commit, then delete.
   */
  public void reset() throws Exception {
    Map<String, List<Map<String, Object>>> rows = new LinkedHashMap<>();
    for (String table : primaryKeys.keySet()) {
      rows.put(table, select("SELECT * FROM " + table));
    }
    commit();
    for (Map.Entry<String, List<Map<String, Object>>> entry : rows.entrySet()) {
      for (Map<String, Object> row : entry.getValue()) {
        deleteByKey(entry.getKey(), row);
      }
    }
    commit();
  }

  public void execute(String sql) throws Exception {
    try (Statement statement = connection.createStatement()) {
      statement.execute(sql);
    }
  }

  /**
   * Commit, unless nothing has begun a transaction.
   *
   * <p>Asking the connection rather than tracking a flag, because the generated repository holds the same
   * connection and uses it directly: a flag set only by this class's own statements says nothing about what
   * the code under test did, and a commit skipped on that basis silently discards its writes.
   */
  public void commit() throws Exception {
    try {
      connection.commit();
    } catch (Exception e) {
      if (!isNoTransaction(e)) throw e;
    }
  }

  public void rollback() throws Exception {
    try {
      connection.rollback();
    } catch (Exception e) {
      if (!isNoTransaction(e)) throw e;
    }
  }

  private static boolean isNoTransaction(Throwable e) {
    for (Throwable cause = e; cause != null && cause != cause.getCause(); cause = cause.getCause()) {
      if (cause.getMessage() != null && cause.getMessage().contains(NO_TRANSACTION)) return true;
    }
    return false;
  }

  public List<Map<String, Object>> select(String sql) throws Exception {
    try (Statement statement = connection.createStatement();
         ResultSet rows = statement.executeQuery(sql)) {
      ResultSetMetaData meta = rows.getMetaData();
      List<Map<String, Object>> out = new ArrayList<>();
      while (rows.next()) {
        Map<String, Object> row = new LinkedHashMap<>();
        for (int i = 1; i <= meta.getColumnCount(); i++) {
          row.put(meta.getColumnLabel(i).toLowerCase(), rows.getObject(i));
        }
        out.add(row);
      }
      return out;
    }
  }

  /**
   * Run one invocation in its own transaction and capture the outcome, key for key with the Oracle capture.
   *
   * <p>A routine that raises leaves nothing behind: the transaction rolls back, and the tables are dumped after
   * that rollback. Oracle's capture records the same thing, because a routine that propagates out of the
   * generated service boundary (P2-9) is not allowed to have committed anything either.
   *
   * <p>{@code pinned} is copied from the scenario rather than measured. On Oracle it is an instruction -- the
   * harness moves the database clock and resets the sequences to it. Here it is a claim about the fixture the
   * caller seeded, and P3-2 compares the two captures' {@code pinned} blocks so that a scenario run against a
   * clock or a counter the ScalarDB side never actually pinned cannot be mistaken for agreement.
   */
  public Map<String, Object> capture(Scenario scenario, Invocation invocation) throws Exception {
    return capture(scenario, invocation, "scalardb");
  }

  public Map<String, Object> capture(Scenario scenario, Invocation invocation, String source) throws Exception {
    Map<String, Object> capture = new LinkedHashMap<>();
    capture.put("scenario", scenario.name());
    capture.put("unit", scenario.unit());
    capture.put("routine", scenario.routine());
    capture.put("source", source);
    capture.put("pinned", scenario.pinned());

    Map<String, Object> result = new LinkedHashMap<>();
    Map<String, Object> raised = null;
    try {
      Object returned = invocation.run();
      Map<String, String> projection = scenario.projection();
      boolean carrier = returned != null && returned.getClass().isRecord();
      result.put("returned", carrier || !projection.isEmpty() ? null : encode(returned));
      result.put("out", projection.isEmpty() ? outOf(returned) : project(returned, projection));
      commit();
    } catch (Exception e) {
      rollback();
      raised = new LinkedHashMap<>();
      raised.put("code", errorCode(e));
      raised.put("message", e.getMessage());
      result.put("returned", null);
      result.put("out", new LinkedHashMap<>());
    }
    capture.put("result", result);
    capture.put("exception", raised);

    Map<String, Object> tables = new LinkedHashMap<>();
    Map<String, List<String>> masked = new LinkedHashMap<>();
    for (String table : scenario.captureTables().isEmpty() ? tables() : scenario.captureTables()) {
      List<String> mask = scenario.mask().getOrDefault(table, List.of());
      tables.put(table, dump(table, mask));
      if (!mask.isEmpty()) masked.put(table, mask);
    }
    capture.put("tables", tables);
    capture.put("masked", masked);
    commit();  // the dump read through a transaction of its own; nothing in it should outlive the capture
    return capture;
  }

  public String toJson(Map<String, Object> capture) {
    return GSON.toJson(capture);
  }

  // --- internals ----------------------------------------------------------------------------------------

  /**
   * The Oracle capture keys an exception by its ORA/user error number. A generated routine raises
   * {@code MigratedException}, which carries the same number through {@code code()} (P2-5), so the two captures
   * are comparable. Anything else is not a business outcome and is recorded under its Java class name, which
   * will not match Oracle -- and that mismatch is the correct signal.
   */
  private static Object errorCode(Exception e) {
    try {
      return e.getClass().getMethod("code").invoke(e);
    } catch (ReflectiveOperationException notMigrated) {
      return e.getClass().getName();
    }
  }

  /**
   * PL/SQL writes through OUT arguments; the generator returns a record instead (P2-5). Unpacking it back into
   * the argument names is what makes the two captures comparable -- the Oracle side never saw a record.
   */
  private static Map<String, Object> outOf(Object returned) {
    Map<String, Object> out = new LinkedHashMap<>();
    if (returned == null || !returned.getClass().isRecord()) return out;
    for (java.lang.reflect.RecordComponent component : returned.getClass().getRecordComponents()) {
      try {
        out.put(snakeCase(component.getName()), encode(component.getAccessor().invoke(returned)));
      } catch (ReflectiveOperationException e) {
        throw new IllegalStateException("cannot read " + component.getName(), e);
      }
    }
    return out;
  }

  /**
   * Take the fields a block scenario named out of the record the routine returned.
   *
   * <p>The record's components are named after the columns (P2-5), so the field the PL/SQL wrote as
   * {@code v.status} is the component {@code status}. A field the record does not have is a difference worth
   * seeing, so it is left out rather than filled with null.
   */
  private static Map<String, Object> project(Object returned, Map<String, String> projection) {
    Map<String, Object> out = new LinkedHashMap<>();
    // 返った値が NULL かどうかだけを観る射影（`CASE WHEN v IS NULL THEN 1 ELSE 0 END`）。値は record で
    // なくてもよい——TIMESTAMP を返す関数の「NULL かどうか」がこれである
    projection.forEach((bind, field) -> {
      if (Scenario.IS_NULL_OF_RESULT.equals(field)) out.put(bind, encode(returned == null ? 1 : 0));
    });
    if (returned == null || !returned.getClass().isRecord()) return out;
    Map<String, java.lang.reflect.RecordComponent> components = new LinkedHashMap<>();
    for (java.lang.reflect.RecordComponent component : returned.getClass().getRecordComponents()) {
      components.put(snakeCase(component.getName()), component);
    }
    projection.forEach((bind, field) -> {
      java.lang.reflect.RecordComponent component = components.get(field.toLowerCase());
      if (component == null) return;
      try {
        out.put(bind, encode(component.getAccessor().invoke(returned)));
      } catch (ReflectiveOperationException e) {
        throw new IllegalStateException("cannot read " + field, e);
      }
    });
    return out;
  }

  static String snakeCase(String camel) {
    return camel.replaceAll("([a-z0-9])([A-Z])", "$1_$2").toLowerCase();
  }

  private Map<String, Object> dump(String table, List<String> mask) throws Exception {
    List<String> columns = columnOrder.get(table);
    List<List<Object>> encoded = new ArrayList<>();
    for (Map<String, Object> row : select("SELECT * FROM " + table)) {
      List<Object> values = new ArrayList<>();
      for (String column : columns) {
        values.add(mask.contains(column) && row.get(column) != null
            ? Map.of("$masked", MASKED_REASON) : encode(row.get(column)));
      }
      encoded.add(values);
    }
    // the same normalisation the Oracle side applies: row order is not specified, multiplicity is
    encoded.sort((a, b) -> GSON.toJson(a).compareTo(GSON.toJson(b)));
    Map<String, Object> dumped = new LinkedHashMap<>();
    dumped.put("columns", columns);
    dumped.put("rows", encoded);
    return dumped;
  }

  private void deleteByKey(String table, Map<String, Object> row) throws Exception {
    List<String> predicates = new ArrayList<>();
    for (String key : primaryKeys.get(table)) {
      predicates.add(key + " = " + literal(row.get(key)));
    }
    execute("DELETE FROM " + table + " WHERE " + String.join(" AND ", predicates));
  }

  private static String literal(Object value) {
    if (value instanceof Number || value instanceof Boolean) return String.valueOf(value);
    return "'" + String.valueOf(value).replace("'", "''") + "'";
  }

  /**
   * The corpus tables, keyed by bare name, in the order the Schema Loader JSON declares them. A table of another
   * namespace keeps its qualified name: the generated SQL names it that way too, and {@link #reset()} has to
   * empty it like any other.
   */
  private static Map<String, Map<String, Object>> readSchema(Path schemaJson, String namespace) throws Exception {
    try (Reader reader = Files.newBufferedReader(schemaJson)) {
      Map<String, Map<String, Object>> schema = GSON.fromJson(
          reader, new TypeToken<LinkedHashMap<String, Map<String, Object>>>() {}.getType());
      Map<String, Map<String, Object>> out = new LinkedHashMap<>();
      for (Map.Entry<String, Map<String, Object>> entry : schema.entrySet()) {
        if (entry.getKey().startsWith(namespace + ".")) {
          out.put(entry.getKey().substring(namespace.length() + 1), entry.getValue());
        } else {
          out.put(entry.getKey(), entry.getValue());
        }
      }
      if (out.keySet().stream().allMatch(table -> table.contains("."))) {
        throw new IllegalArgumentException(schemaJson + " declares no table in namespace " + namespace);
      }
      return out;
    }
  }

  /** Partition key then clustering keys, in declaration order -- a ScalarDB delete needs the whole key. */
  private static Map<String, List<String>> primaryKeysOf(Map<String, Map<String, Object>> schema) {
    Map<String, List<String>> out = new LinkedHashMap<>();
    schema.forEach((table, definition) -> {
      List<String> keys = new ArrayList<>(strings(definition.get("partition-key")));
      for (String clustering : strings(definition.get("clustering-key"))) {
        keys.add(clustering.split("\\s+")[0]);  // "line_no ASC" names the column and then its order
      }
      out.put(table, keys);
    });
    return out;
  }

  /**
   * Oracle dumps {@code SELECT *} in DDL order. ScalarDB does not promise any column order, so the capture takes
   * its order from the schema the corpus was loaded from -- which the converter derived from that same DDL.
   */
  @SuppressWarnings("unchecked")
  private static Map<String, List<String>> columnOrderOf(Map<String, Map<String, Object>> schema) {
    Map<String, List<String>> out = new LinkedHashMap<>();
    schema.forEach((table, definition) ->
        out.put(table, new ArrayList<>(((Map<String, Object>) definition.get("columns")).keySet())));
    return out;
  }

  @SuppressWarnings("unchecked")
  private static List<String> strings(Object value) {
    return value == null ? List.of() : (List<String>) value;
  }

  static Object encode(Object value) {
    if (value == null) return null;
    if (value instanceof BigDecimal d) return Map.of("$dec", d.toPlainString());
    if (value instanceof LocalDateTime d) return Map.of("$ts", isoformat(d));
    if (value instanceof OffsetDateTime d) return Map.of("$ts", isoformat(d.toLocalDateTime()));
    if (value instanceof java.sql.Timestamp t) return Map.of("$ts", isoformat(t.toLocalDateTime()));
    if (value instanceof byte[] b) return Map.of("$raw", java.util.HexFormat.of().formatHex(b));
    if (value instanceof Number || value instanceof String || value instanceof Boolean) return value;
    return Map.of("$str", String.valueOf(value));
  }

  /**
   * The same text Python's {@code datetime.isoformat()} produces, which is what the Oracle capture holds.
   *
   * <p>{@code LocalDateTime.toString()} drops the seconds when they are zero, so the same instant would be
   * written two ways and every timestamp would look like a difference. Comparing encodings instead of values is
   * the failure mode this whole capture format exists to avoid.
   */
  static String isoformat(LocalDateTime value) {
    String text = value.format(java.time.format.DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss"));
    int micros = value.getNano() / 1000;
    return micros == 0 ? text : text + String.format(".%06d", micros);
  }

  @Override
  public void close() throws Exception {
    connection.close();
  }

  /** One call into a generated service. */
  @FunctionalInterface
  public interface Invocation {
    Object run() throws Exception;
  }
}
