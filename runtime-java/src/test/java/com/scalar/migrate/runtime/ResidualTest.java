package com.scalar.migrate.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

class ResidualTest {

  private static Plan.Fetch fetch(String table, List<List<String>> indexes) {
    Plan.Fetch f = new Plan.Fetch();
    f.table = table;
    f.column_types = Map.of();
    f.index_columns = indexes;
    return f;
  }

  private static Rows rows(List<String> columns, Map<String, String> types, Object[]... data) {
    Rows r = new Rows();
    r.columns.addAll(columns);
    r.types.putAll(types);
    for (Object[] row : data) r.rows.add(row);
    return r;
  }

  private static void load(Residual residual) throws Exception {
    residual.load(fetch("orders", List.of(List.of("order_id"), List.of("customer_id"), List.of("no_such_column"))),
        rows(List.of("order_id", "customer_id"), Map.of("order_id", "INT", "customer_id", "INT"),
            new Object[] {1, 10}, new Object[] {2, 20}, new Object[] {3, 10}));
    residual.load(fetch("customers", List.of(List.of("customer_id"))),
        rows(List.of("customer_id", "name"), Map.of("customer_id", "INT", "name", "TEXT"),
            new Object[] {10, "a"}, new Object[] {20, "b"}));
  }

  private static long planIndexes(Residual residual) throws Exception {
    Map<String, Object> built = residual.query(
        "SELECT COUNT(DISTINCT INDEX_NAME) FROM INFORMATION_SCHEMA.INDEXES WHERE INDEX_NAME LIKE '%_ix%'", Map.of());
    return ((Number) ((List<?>) ((List<?>) built.get("rows")).get(0)).get(0)).longValue();
  }

  private static final String JOIN =
      "SELECT c.name FROM orders o JOIN customers c ON c.customer_id = o.customer_id ORDER BY o.order_id";

  @Test
  void buildsNoIndexesByDefault() throws Exception {
    try (Residual residual = new Residual("PostgreSQL")) {
      load(residual);
      assertEquals(List.of(List.of("a"), List.of("b"), List.of("a")), residual.query(JOIN, Map.of()).get("rows"));
      assertEquals(0L, planIndexes(residual));
    }
  }

  @Test
  void buildsThePlanIndexesWhenAskedAndSkipsOnesThatCannotBeBuilt() throws Exception {
    try (Residual residual = new Residual("PostgreSQL", true)) {
      load(residual);
      assertEquals(List.of(List.of("a"), List.of("b"), List.of("a")), residual.query(JOIN, Map.of()).get("rows"));
      // orders_ix0, orders_ix1, customers_ix3; the index on no_such_column (orders_ix2) was skipped
      assertEquals(3L, planIndexes(residual));
    }
  }

  private static Object ratio(String mode) throws Exception {
    try (Residual residual = new Residual(mode)) {
      residual.load(fetch("lines", null), rows(List.of("id", "qty"), Map.of("id", "BIGINT", "qty", "INT"),
          new Object[] {1L, 7}));
      return ((List<List<Object>>) residual.query("SELECT qty / 2 FROM lines", Map.of()).get("rows")).get(0).get(0);
    }
  }

  @Test
  @SuppressWarnings("unchecked")
  void wholeNumberColumnsDivideAsTheSourceDatabaseDoes() throws Exception {
    // ScalarDB has no DECIMAL, so NUMBER(9) arrives as INT. H2 divides INT by INT as integers in every mode.
    assertEquals(0, new java.math.BigDecimal("3.5").compareTo(new java.math.BigDecimal(ratio("Oracle").toString())));
    assertEquals(0, new java.math.BigDecimal("3.5").compareTo(new java.math.BigDecimal(ratio("MySQL").toString())));
    assertEquals(0, new java.math.BigDecimal("3").compareTo(new java.math.BigDecimal(ratio("PostgreSQL").toString())),
        "PostgreSQL truncates integer division itself");
  }

  @Test
  void wholeNumberColumnsStillJoinCompareAndSum() throws Exception {
    try (Residual residual = new Residual("Oracle")) {
      load(residual);
      assertEquals(List.of(List.of("a"), List.of("b"), List.of("a")), residual.query(JOIN, Map.of()).get("rows"));
    }
  }

  @Test
  void aModeThatIsNotOneOfTheThreeIsRefused() {
    // the mode is concatenated into the JDBC URL; `;INIT=` there runs SQL of the plan author's choosing
    org.junit.jupiter.api.Assertions.assertThrows(IllegalArgumentException.class,
        () -> new Residual("Oracle;INIT=CREATE TABLE pwned(x INT)"));
  }

  @Test
  void aNameFromThePlanThatIsNotAnIdentifierIsRefused() throws Exception {
    // H2 runs several statements per execute: this "table" used to create an alias holding Java of the plan
    // author's choosing
    String smuggled = "u(a INT); CREATE ALIAS pwn AS 'String f() { return System.getProperty(\"user.name\"); }'; CREATE TABLE v";
    try (Residual residual = new Residual("PostgreSQL")) {
      org.junit.jupiter.api.Assertions.assertThrows(IllegalArgumentException.class,
          () -> residual.load(fetch(smuggled, null), rows(List.of("id"), Map.of("id", "INT"), new Object[] {1})));
      org.junit.jupiter.api.Assertions.assertThrows(IllegalArgumentException.class,
          () -> residual.load(fetch("t", null), rows(List.of("id INT); DROP ALL OBJECTS; --"), Map.of(), new Object[] {1})));
      org.junit.jupiter.api.Assertions.assertThrows(IllegalArgumentException.class,
          () -> residual.load(fetch("t", List.of(List.of("id); SHUTDOWN; --"))),
              rows(List.of("id"), Map.of("id", "INT"), new Object[] {1})));
    }
  }

  @Test
  void theResidualSqlCanReadTheFetchedRowsAndNothingElse() throws Exception {
    for (String mode : List.of("Oracle", "PostgreSQL", "MySQL")) {
      try (Residual residual = new Residual(mode, true)) {
        load(residual);
        assertEquals(List.of(List.of("a"), List.of("b"), List.of("a")), residual.query(JOIN, Map.of()).get("rows"), mode);
        for (String sql : List.of(
            "SELECT FILE_READ('/etc/hosts')",
            "SELECT CSVWRITE('/tmp/residual-should-not-write.csv', 'SELECT 1')",
            "SELECT * FROM LINK_SCHEMA('x', '', 'jdbc:h2:mem:other', 'sa', '', 'PUBLIC')")) {
          org.junit.jupiter.api.Assertions.assertThrows(java.sql.SQLException.class,
              () -> residual.query(sql, Map.of()), mode + ": " + sql);
        }
      }
    }
    org.junit.jupiter.api.Assertions.assertFalse(
        java.nio.file.Files.exists(java.nio.file.Path.of("/tmp/residual-should-not-write.csv")));
  }

  @Test
  void oracleFunctionsAreStillCallableFromTheResidualSql() throws Exception {
    try (Residual residual = new Residual("Oracle")) {
      load(residual);
      assertEquals(List.of(List.of("Abc Def")), residual.query("SELECT INITCAP('abc def') FROM DUAL", Map.of()).get("rows"));
    }
  }

  @Test
  @SuppressWarnings("unchecked")
  void anUndeclaredColumnIsTypedFromAllItsValuesNotTheFirst() throws Exception {
    // typed from the first row, a leading NULL made the column VARCHAR: MAX answered 9 over 10
    try (Residual residual = new Residual("PostgreSQL")) {
      residual.load(fetch("amounts", null), rows(List.of("id", "amt"), Map.of("id", "INT"),
          new Object[] {1, null}, new Object[] {2, 9}, new Object[] {3, 10L}));
      List<List<Object>> max = (List<List<Object>>) residual.query("SELECT MAX(amt), SUM(amt) FROM amounts", Map.of()).get("rows");
      assertEquals("10", max.get(0).get(0).toString());
      assertEquals("19", max.get(0).get(1).toString());
      List<List<Object>> ordered = (List<List<Object>>) residual.query(
          "SELECT id FROM amounts WHERE amt IS NOT NULL ORDER BY amt", Map.of()).get("rows");
      assertEquals(List.of(List.of(2), List.of(3)), ordered);
    }
  }

  @Test
  void aColumnOfNothingButNullsStillLoads() throws Exception {
    try (Residual residual = new Residual("Oracle")) {
      residual.load(fetch("t", null), rows(List.of("id", "note"), Map.of("id", "INT"), new Object[] {1, null}));
      assertEquals(1, ((List<?>) residual.query("SELECT id FROM t WHERE note IS NULL", Map.of()).get("rows")).size());
    }
  }

  @Test
  void jdbcColumnTypesMapToScalarDbTypes() {
    assertEquals("BIGINT", Values.typeOfJdbc(java.sql.Types.BIGINT));
    assertEquals("TEXT", Values.typeOfJdbc(java.sql.Types.VARCHAR));
    assertEquals("TIMESTAMPTZ", Values.typeOfJdbc(java.sql.Types.TIMESTAMP_WITH_TIMEZONE));
    org.junit.jupiter.api.Assertions.assertNull(Values.typeOfJdbc(java.sql.Types.ARRAY));
  }
}
