package com.scalar.migrate.plsql;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import org.junit.jupiter.api.Test;

/** The Oracle-versus-Java differences the generated code relies on. */
class PlsqlTest {

  @Test
  void comparingWithNullIsNeverTrue() {
    // In SQL an unknown condition does not run its branch, and `NULL <> x` is unknown too.
    assertFalse(Plsql.eq(null, null));
    assertFalse(Plsql.eq("a", null));
    assertFalse(Plsql.ne(null, "a"));
    assertFalse(Plsql.lt(null, 1));
  }

  @Test
  void numbersCompareByValueNotByScale() {
    assertTrue(Plsql.eq(new BigDecimal("1.0"), new BigDecimal("1.00")));
    assertTrue(Plsql.lt(new BigDecimal("1.5"), 2));
    assertTrue(Plsql.ge(3, new BigDecimal("3.000")));
  }

  @Test
  void theEmptyStringIsNull() {
    assertTrue(Plsql.isNull(""));
    assertFalse(Plsql.isNotNull(""));
    assertEquals("x", Plsql.nvl("", "x"));
  }

  @Test
  void concatenationTreatsNullAsEmptyAndEmptyAsNull() {
    assertEquals("ab", Plsql.concat("a", null, "b"));
    assertNull(Plsql.concat(null, null), "'' is NULL in Oracle");
  }

  @Test
  void roundingIsHalfUpNotHalfEven() {
    // 1.225 is where the two disagree: HALF_UP goes to 1.23, HALF_EVEN keeps the even 2 and gives 1.22.
    assertEquals(new BigDecimal("1.23"), Plsql.round(new BigDecimal("1.225"), 2));
    assertEquals(new BigDecimal("1.22"), new BigDecimal("1.225").setScale(2, java.math.RoundingMode.HALF_EVEN),
        "this is what Java would do by default, and why the helper exists");
  }

  @Test
  void truncDropsTheTimeOfDay() {
    assertEquals(LocalDateTime.of(2026, 1, 15, 0, 0),
        Plsql.trunc(LocalDateTime.of(2026, 1, 15, 23, 59, 59)));
  }

  @Test
  void textRendersNumbersWithoutTrailingZeros() {
    assertEquals("1.5", Plsql.text(new BigDecimal("1.50")));
    assertEquals("", Plsql.text(null));
  }
}
