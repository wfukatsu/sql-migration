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

  // --- the bind boundary (P3-1) -------------------------------------------------------------------------

  @Test
  void bindTurnsABigDecimalIntoTheColumnsOwnType() {
    // ScalarDB's driver refuses a BigDecimal outright (DB-SQL-10016), so nothing may reach it as one
    assertEquals(1500L, Plsql.bind(new BigDecimal("15.00"), "BIGINT", 2));
    assertEquals(15L, Plsql.bind(new BigDecimal("15"), "BIGINT", 0));
    assertEquals(15.5d, Plsql.bind(new BigDecimal("15.5"), "DOUBLE", 2));
    assertEquals("15.50", Plsql.bind(new BigDecimal("15.50"), "TEXT", 2));
  }

  @Test
  void bindDropsTheOffsetForATimestampColumnTheWayOracleDoes() {
    // Oracle が TIMESTAMP WITH TIME ZONE を TIMESTAMP 列へ入れるときは offset を落とし、日時の
    // フィールドはそのまま残す（セッションのタイムゾーンへ換算しない）。Oracle 23ai で実測した (#23)。
    // 落とさずに渡すとドライバが型ごと拒否する: DB-SQL-10016 java.time.OffsetDateTime is not supported
    java.time.OffsetDateTime moment =
        java.time.OffsetDateTime.parse("2026-01-15T09:30:00-05:00");
    assertEquals(java.time.LocalDateTime.parse("2026-01-15T09:30:00"),
        Plsql.bind(moment, "TIMESTAMP", 0));
  }

  @Test
  void bindLeavesAMomentAloneForAColumnThatKeepsTheZone() {
    // TIMESTAMPTZ はタイムゾーンを保てるので、落とすと情報が減る
    java.time.OffsetDateTime moment =
        java.time.OffsetDateTime.parse("2026-01-15T09:30:00-05:00");
    assertEquals(moment, Plsql.bind(moment, "TIMESTAMPTZ", 0));
  }

  @Test
  void bindRoundsToTheColumnScaleTheWayOracleDoes() {
    // Oracle stores 1234.565 into a NUMBER(14,2) as 1234.57: half-up, not half-even, and never truncated
    assertEquals(123457L, Plsql.bind(new BigDecimal("1234.565"), "BIGINT", 2));
    assertEquals(123456L, Plsql.bind(new BigDecimal("1234.564"), "BIGINT", 2));
  }

  @Test
  void bindLeavesNonNumbersAlone() {
    assertEquals("GOLD", Plsql.bind("GOLD", "TEXT", 0));
    assertNull(Plsql.bind(null, "BIGINT", 2));
  }

  @Test
  void readUnscalesWhatBindScaled() {
    assertEquals(0, new BigDecimal("15.00").compareTo(Plsql.read(1500L, "BIGINT", 2)));
    assertEquals(0, new BigDecimal("15").compareTo(Plsql.read(15L, "BIGINT", 0)));
    assertNull(Plsql.read(null, "BIGINT", 2));
  }

  @Test
  void bindAndReadRoundTripEveryMoneyValueTheCorpusUses() {
    for (String value : new String[] {"0", "0.01", "15.00", "100000.00", "1234.57", "-42.50"}) {
      BigDecimal original = new BigDecimal(value);
      Object stored = Plsql.bind(original, "BIGINT", 2);
      assertEquals(0, original.compareTo(Plsql.read(stored, "BIGINT", 2)),
          value + " did not survive the bind boundary");
    }
  }

  @Test
  void doubleStorageDoesNotPretendToBeExact() {
    // the other money convention: this is the loss the PoC is measuring, not a bug to hide
    Object stored = Plsql.bind(new BigDecimal("0.10"), "DOUBLE", 2);
    assertEquals(0.10d, stored);
  }

  // --- the zone the migration fixed (plan §9, 2026-09-17) -----------------------------------------------

  @Test
  void systimestampIsUtcNotTheMachinesZone() {
    // the JVM default would make the same code write different instants depending on where it runs
    LocalDateTime pinned = LocalDateTime.of(2026, 1, 15, 9, 30, 0);
    Plsql.setClock(() -> pinned);
    try {
      assertEquals(java.time.ZoneOffset.UTC, Plsql.systimestamp().getOffset());
      assertEquals(pinned, Plsql.systimestamp().toLocalDateTime());
    } finally {
      Plsql.setClock(LocalDateTime::now);
    }
  }

  @Test
  void systimestampAgreesWithSysdateOnTheInstant() {
    LocalDateTime pinned = LocalDateTime.of(2026, 7, 1, 0, 0, 0);
    Plsql.setClock(() -> pinned);
    try {
      assertEquals(Plsql.sysdate(), Plsql.systimestamp().toLocalDateTime(),
          "the two must not disagree about what time it is");
    } finally {
      Plsql.setClock(LocalDateTime::now);
    }
  }
}
