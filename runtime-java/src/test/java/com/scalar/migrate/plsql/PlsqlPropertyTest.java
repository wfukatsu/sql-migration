package com.scalar.migrate.plsql;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import java.math.BigDecimal;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import org.junit.jupiter.api.DynamicTest;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestFactory;

/**
 * P3-3: every expression semantic the generated code relies on, checked against what Oracle actually did.
 *
 * <p>{@link Plsql} exists because PL/SQL operators do not mean in Java what they mean in Oracle. Asserting those
 * differences from memory tests the memory, so {@code difftest/plsql_semantics.py} ran each one on a real Oracle
 * and recorded the answers in {@code fixtures/plsql/semantics.json}. This replays them. Oracle decides; this
 * file only checks that the runtime agrees, and it needs no database to do it.
 *
 * <p>The cases sit on the edges on purpose -- NULL, the empty string, a blank string, zero, negatives, the
 * precision limits the type mapper draws INT and BIGINT at, values past 64 bits, midnight, a leap day. A
 * property that only holds away from the edges is not a property, and each of those edges is somewhere a
 * migration has gone wrong.
 */
class PlsqlPropertyTest {
  private static final Path FIXTURE = Path.of("..", "fixtures", "plsql", "semantics.json");
  private static final Gson GSON = new Gson();

  /**
   * Families of Oracle behaviour the runtime does not claim to reproduce, and why.
   *
   * <p>Named rather than skipped quietly: a case that matches nothing fails the coverage test below, so an
   * operation added to the fixture cannot slip through unchecked.
   */
  private static final Map<String, String> UNCHECKED = Map.of(
      "length", "no helper: generated code emits String.length(), which differs on NULL and is not reached "
          + "because a NULL length is only ever compared, never stored",
      "trunc_number", "no helper: TRUNC(n, scale) does not appear in the corpus");

  private static JsonObject fixture() throws Exception {
    return GSON.fromJson(Files.readString(FIXTURE), JsonObject.class);
  }

  // --- the replay ---------------------------------------------------------------------------------------

  @TestFactory
  List<DynamicTest> everyRecordedOracleAnswerIsReproduced() throws Exception {
    List<DynamicTest> tests = new ArrayList<>();
    Map<String, List<JsonObject>> byOperation = new TreeMap<>();
    for (JsonElement element : fixture().getAsJsonArray("cases")) {
      JsonObject one = element.getAsJsonObject();
      byOperation.computeIfAbsent(one.get("op").getAsString(), k -> new ArrayList<>()).add(one);
    }
    byOperation.forEach((operation, group) -> {
      if (UNCHECKED.containsKey(operation)) return;
      tests.add(DynamicTest.dynamicTest(operation + " (" + group.size() + " cases)", () -> {
        for (JsonObject one : group) check(one);
      }));
    });
    return tests;
  }

  private void check(JsonObject one) {
    String operation = one.get("op").getAsString();
    List<Object> args = new ArrayList<>();
    for (JsonElement argument : one.getAsJsonArray("args")) args.add(decode(argument));
    JsonObject oracle = one.getAsJsonObject("oracle");
    String where = operation + show(args);

    Object actual;
    try {
      actual = apply(operation, args);
    } catch (ArithmeticException e) {
      // Oracle refuses a value that does not fit the column (ORA-01438); so does the runtime, and that
      // agreement is the property -- a value silently truncated instead would be the failure
      assertTrue(oracle.has("error"), where + ": the runtime refused the value but Oracle accepted it");
      return;
    }
    assertTrue(oracle.has("value"), where + ": Oracle refused the value (" + oracle.get("error")
        + ") but the runtime produced " + actual);
    Object expected = decode(oracle.get("value"));

    if ("bool".equals(one.get("kind").getAsString())) {
      // Oracle has no boolean in SQL, so the fixture holds 1 for true and 0 for anything else -- which is
      // exactly PL/SQL's own rule that an unknown condition does not run its branch
      boolean wanted = new BigDecimal("1").compareTo((BigDecimal) expected) == 0;
      assertEquals(wanted, actual, where);
      return;
    }
    assertSameValue(expected, actual, where);
  }

  /**
   * Oracle's NUMBER holds 38 significant decimal digits, and that is what the runtime computes to.
   *
   * <p>A division can come back from the client with more than 38 -- Oracle divides at a wider internal
   * precision before it rounds to NUMBER on store. Those extra digits exist only in a value that was never
   * stored, so both sides are compared at the precision NUMBER actually guarantees. Anything that reaches a
   * column has already been rounded to it.
   */
  private static final java.math.MathContext NUMBER = new java.math.MathContext(38);

  /** Compare by value, not by representation: Oracle's 1.0 and Java's 1.00 are the same number. */
  private static void assertSameValue(Object expected, Object actual, String where) {
    if (expected instanceof BigDecimal a && actual instanceof BigDecimal b) {
      assertEquals(0, a.round(NUMBER).compareTo(b.round(NUMBER)),
          where + ": expected " + a.toPlainString() + ", got " + b.toPlainString());
      return;
    }
    assertEquals(expected, actual, where);
  }

  private static Object apply(String operation, List<Object> args) {
    Object a = args.get(0);
    Object b = args.size() > 1 ? args.get(1) : null;
    return switch (operation) {
      case "eq" -> Plsql.eq(a, b);
      case "ne" -> Plsql.ne(a, b);
      case "lt" -> Plsql.lt(a, b);
      case "le" -> Plsql.le(a, b);
      case "gt" -> Plsql.gt(a, b);
      case "ge" -> Plsql.ge(a, b);
      case "is_null" -> Plsql.isNull(a);
      case "nvl" -> Plsql.nvl(a, b);
      case "concat" -> Plsql.concat(a, b);
      case "rtrim" -> Plsql.rtrim(a);
      case "add" -> Plsql.add(a, b);
      case "sub" -> a instanceof LocalDateTime ? Plsql.sub(a, b) : Plsql.add(a, negate(b));
      case "mul" -> Plsql.mul(a, b);
      case "div" -> Plsql.div(a, b);
      case "mod" -> Plsql.mod(a, b);
      case "round" -> Plsql.round(a, ((BigDecimal) b).intValue());
      case "trunc" -> Plsql.trunc((LocalDateTime) a);
      case "to_char_date" -> Plsql.text(a, "YYYY-MM-DD");
      case "to_char_datetime" -> Plsql.text(a, "YYYY-MM-DD HH24:MI:SS");
      // the migration's stand-in for storing into a NUMBER(14,2): scale in, scale out
      case "cast_number_14_2" -> Plsql.read(Plsql.bind(a, "BIGINT", 2), "BIGINT", 2);
      default -> throw new IllegalArgumentException("unmapped operation: " + operation);
    };
  }

  private static Object negate(Object value) {
    return value == null ? null : ((BigDecimal) value).negate();
  }

  private static Object decode(JsonElement element) {
    if (element == null || element.isJsonNull()) return null;
    if (element.isJsonObject()) {
      JsonObject tagged = element.getAsJsonObject();
      if (tagged.has("$dec")) return new BigDecimal(tagged.get("$dec").getAsString());
      if (tagged.has("$ts")) return LocalDateTime.parse(tagged.get("$ts").getAsString());
      if (tagged.has("$date")) return LocalDateTime.parse(tagged.get("$date").getAsString() + "T00:00:00");
      return tagged.toString();
    }
    var primitive = element.getAsJsonPrimitive();
    if (primitive.isNumber()) return new BigDecimal(primitive.getAsString());
    if (primitive.isBoolean()) return primitive.getAsBoolean();
    return primitive.getAsString();
  }

  private static String show(List<Object> args) {
    List<String> parts = new ArrayList<>();
    for (Object arg : args) {
      parts.add(arg == null ? "NULL" : arg instanceof String s ? "'" + s + "'" : String.valueOf(arg));
    }
    return "(" + String.join(", ", parts) + ")";
  }

  // --- coverage -----------------------------------------------------------------------------------------

  @Test
  void everyOperationInTheFixtureIsCheckedOrNamedAsUnchecked() throws Exception {
    JsonArray cases = fixture().getAsJsonArray("cases");
    Map<String, Integer> unmapped = new LinkedHashMap<>();
    for (JsonElement element : cases) {
      String operation = element.getAsJsonObject().get("op").getAsString();
      if (UNCHECKED.containsKey(operation)) continue;
      try {
        apply(operation, List.of(BigDecimal.ONE, BigDecimal.ONE));
      } catch (IllegalArgumentException e) {
        unmapped.merge(operation, 1, Integer::sum);
      } catch (RuntimeException ignored) {
        // the operation is mapped; it simply did not like these arguments, which this test is not about
      }
    }
    assertTrue(unmapped.isEmpty(),
        "the fixture records operations the runtime is neither checked against nor listed as skipping: "
            + unmapped.keySet() + ". Add them to apply(), or to UNCHECKED with the reason.");
  }

  @Test
  void theFixtureStillCoversEveryFamilyTheRuntimeDependsOn() throws Exception {
    Set<String> required = Set.of("comparison", "null", "arithmetic", "rounding", "date", "storage");
    List<String> families = new ArrayList<>();
    fixture().getAsJsonArray("families").forEach(f -> families.add(f.getAsString()));
    assertTrue(families.containsAll(required),
        "semantics.json no longer covers " + required + "; it has " + families);
  }

  @Test
  void theFixtureSaysWhichOracleAnsweredIt() throws Exception {
    // the answers belong to a version, and a fixture that does not say which is not evidence about anything
    assertTrue(fixture().get("source").getAsString().toLowerCase().contains("oracle"));
  }
}
