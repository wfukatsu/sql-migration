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
