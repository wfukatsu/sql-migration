package com.scalar.migrate.plsql;

import java.nio.file.Path;

/**
 * Which money convention the tree was generated for, and where that convention's tables and schema live.
 *
 * <p>ScalarDB has no DECIMAL, so an Oracle `NUMBER(12,2)` becomes either a scaled BIGINT or a DOUBLE, and the
 * generated repository converts at the bind boundary using whichever the schema says. Code generated for one
 * convention cannot be run against the other's namespace -- it would bind a long where a double is expected --
 * so every test that touches a cluster has to name the same variant the generator was last run with.
 *
 * <p>Selected with {@code -Dplsql.variant=scaled|double}; {@code difftest/plsql_capture.py} passes it.
 */
final class Variant {
  private Variant() {}

  static final String NAME = System.getProperty("plsql.variant", "scaled");
  static final boolean DOUBLE = NAME.equals("double");

  static final String NAMESPACE = DOUBLE ? "plsqlpoc_dbl" : "plsqlpoc";

  static final Path PROPERTIES = Path.of("..", "difftest", "conf", "scalardb-sql-jdbc.properties");

  static final Path SCHEMA = DOUBLE
      ? Path.of("..", "difftest", "work", "plsql-schema-double.json")
      : Path.of("..", "fixtures", "plsql", "scalardb-schema.json");

  static final Path SETUP = DOUBLE
      ? Path.of("..", "difftest", "work", "plsql-setup-double.json")
      : Path.of("..", "difftest", "work", "plsql-setup.json");

  static final Path CAPTURES = Path.of("..", "difftest", "work", "plsql-scalardb-" + NAME);

  /** A generated file that binds a money column, used to tell which variant the tree was built for. */
  private static final Path WITNESS = Path.of("..", "generated", "src", "main", "java", "com", "example",
      "migrated", "infrastructure", "PkgTierAdminRepository.java");

  /**
   * Fail now, with the reason, if the generated tree was built for the other money convention.
   *
   * <p>Running code generated for one convention against the other's namespace produces
   * {@code DB-SQL-10060: Unmatched column type}, several layers below anything that names the cause. The tree
   * is rewritten by {@code difftest/plsql_capture.py --variant ...}, so generating for one variant and then
   * testing the other is an easy mistake to make and a slow one to diagnose.
   */
  static void assertGeneratedForThisVariant() {
    String source;
    try {
      source = java.nio.file.Files.readString(WITNESS);
    } catch (java.io.IOException notGenerated) {
      throw new IllegalStateException("the generated tree is missing; run `python -m plsql.generate`",
          notGenerated);
    }
    boolean builtForDouble = source.contains("\"DOUBLE\"");
    if (builtForDouble != DOUBLE) {
      throw new IllegalStateException(String.format(
          "generated/ was built for the %s money convention but the tests run against %s (%s). "
              + "Run: python difftest/plsql_capture.py --variant %s",
          builtForDouble ? "double" : "scaled", NAME, NAMESPACE, NAME));
    }
  }

  /** A money amount, written the way this variant's columns hold it. */
  static Object money(String amount) {
    java.math.BigDecimal value = new java.math.BigDecimal(amount);
    // an if, not a ternary: a conditional whose branches are a Double and a Long is promoted to double, so the
    // scaled branch would come back floating-point and ScalarDB would reject it against a BIGINT column. The
    // compiler warns about none of it.
    if (DOUBLE) {
      return value.doubleValue();
    }
    return value.movePointRight(2).longValueExact();
  }
}
