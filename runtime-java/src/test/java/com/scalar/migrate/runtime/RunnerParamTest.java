package com.scalar.migrate.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.scalar.db.io.DataType;
import java.math.BigDecimal;
import java.util.Map;
import org.junit.jupiter.api.Test;

/** Numbers on their way into a fetch: from `--param`, from the plan JSON, and into a ScalarDB column. */
class RunnerParamTest {

  @Test
  void aParamIsANumberOnlyInItsCanonicalSpelling() {
    assertEquals(42L, Runner.parseParam("42"));
    assertEquals(-7L, Runner.parseParam("-7"));
    assertEquals(9007199254740993L, Runner.parseParam("9007199254740993"), "a double cannot hold this id");
    assertEquals(new BigDecimal("12345678901234567890"), Runner.parseParam("12345678901234567890"));
    assertEquals(new BigDecimal("1.50"), Runner.parseParam("1.50"));
    assertEquals("00123", Runner.parseParam("00123"), "a zero-padded code is text, not 123");
    assertEquals("+5", Runner.parseParam("+5"));
    assertEquals("1e3", Runner.parseParam("1e3"));
    assertEquals(Boolean.TRUE, Runner.parseParam("true"));
    assertEquals("NEW", Runner.parseParam("NEW"));
  }

  @Test
  @SuppressWarnings("unchecked")
  void wholeNumbersInAPlanStayWhole() {
    Map<String, Object> parsed = Runner.GSON.fromJson("{\"id\": 9007199254740993, \"rate\": 0.5}", Map.class);
    assertEquals(9007199254740993L, parsed.get("id"));
    assertEquals(0.5, parsed.get("rate"));
  }

  @Test
  void aColumnValueIsExactOrRefused() {
    assertEquals(9007199254740993L, Values.whole("id", 9007199254740993L));
    assertEquals(3L, Values.whole("id", new BigDecimal("3.0")));
    assertThrows(IllegalArgumentException.class, () -> Values.whole("qty", 1.5));
    assertThrows(ArithmeticException.class, () -> Values.column("qty", DataType.INT, 3_000_000_000L));
  }

  @Test
  void aPredicateValueTheColumnCannotHoldIsNotPushedDown() {
    // `qty < 1.5` narrowed to INT was `qty < 1`: the rows with qty = 1 were never fetched
    assertFalse(Values.representable(DataType.INT, 1.5));
    assertFalse(Values.representable(DataType.INT, 3_000_000_000L));
    assertTrue(Values.representable(DataType.BIGINT, 3_000_000_000L));
    assertTrue(Values.representable(DataType.INT, new BigDecimal("2.0")));
    assertTrue(Values.representable(DataType.DOUBLE, 1.5));
    assertTrue(Values.representable(DataType.TEXT, "00123"));
  }
}
