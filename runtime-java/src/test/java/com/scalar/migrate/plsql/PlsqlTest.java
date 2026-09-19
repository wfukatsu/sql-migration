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
  void bindHandsATimestamptzColumnTheSameInstant() {
    // 以前は「TIMESTAMPTZ はタイムゾーンを保てるので素通し」としていたが、ScalarDB SQL のドライバは
    // OffsetDateTime を型ごと拒否する（DB-SQL-10016。`record_payment` で実測、2026-09-19）。瞬間として
    // 渡す。元の offset は失われるが、瞬間は同じである（AuditBindIT が実クラスタで往復を確かめている）
    java.time.OffsetDateTime moment =
        java.time.OffsetDateTime.parse("2026-01-15T09:30:00-05:00");
    assertEquals(moment.toInstant(), Plsql.bind(moment, "TIMESTAMPTZ", 0));
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
      Plsql.resetClock();
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
      Plsql.resetClock();
    }
  }
  // --- 日時の算術（2026-09-19 / prc_purge_audit）--------------------------------------------------

  @Test
  void aTimestampMinusDaysIsADate() {
    // `SYSTIMESTAMP - 30` は 30 日前の DATE。秒未満は落ち、ゾーンは変換しない（castDate と同じ規則）
    var now = java.time.OffsetDateTime.of(2026, 1, 15, 9, 30, 0, 123_000_000, java.time.ZoneOffset.ofHours(9));
    assertEquals(LocalDateTime.of(2025, 12, 16, 9, 30, 0), Plsql.sub(now, 30));
  }

  @Test
  void aDatePlusAFractionOfADayMovesBySeconds() {
    // DATE は秒までしか持たないので、日数の端数は秒に丸める
    assertEquals(LocalDateTime.of(2026, 1, 15, 21, 30, 0),
        Plsql.add(LocalDateTime.of(2026, 1, 15, 9, 30, 0), new BigDecimal("0.5")));
  }

  @Test
  void twoDatesStillSubtractIntoDays() {
    assertEquals(0, new BigDecimal("1.5").compareTo((BigDecimal) Plsql.sub(
        LocalDateTime.of(2026, 1, 16, 21, 0, 0), LocalDateTime.of(2026, 1, 15, 9, 0, 0))));
  }

  @Test
  void numbersAreUnchanged() {
    assertEquals(0, new BigDecimal("3").compareTo((BigDecimal) Plsql.sub(5, 2)));
    assertNull(Plsql.add(null, 1));
  }
  // --- TIMESTAMPTZ を読む（2026-09-19 / last_paid_at）------------------------------------------------

  @Test
  void anInstantReadBackIsTheSameMomentAtUtc() {
    // ScalarDB の TIMESTAMPTZ は Instant で返る。cast すると ClassCastException で落ちていた
    java.time.Instant instant = java.time.Instant.parse("2026-01-15T00:30:00Z");
    assertEquals(java.time.OffsetDateTime.parse("2026-01-15T00:30:00Z"), Plsql.zoned(instant));
    assertEquals(instant, Plsql.zoned(instant).toInstant());
  }

  @Test
  void anOffsetDateTimeIsKeptAsItIs() {
    var moment = java.time.OffsetDateTime.parse("2026-01-15T09:30:00+09:00");
    assertEquals(moment, Plsql.zoned(moment));
    assertNull(Plsql.zoned(null));
  }

  @Test
  void negatedPredicatesAreNotTrueWhenTheAnswerIsUnknown() {
    // `!in(x, ...)` is true for a null x; Oracle's `x NOT IN (...)` is unknown, and an IF does not take it.
    assertTrue(Plsql.notIn(3, 1, 2));
    assertFalse(Plsql.notIn(1, 1, 2));
    assertFalse(Plsql.notIn(null, 1, 2));
    assertFalse(Plsql.notIn(3, 1, null), "x NOT IN (1, NULL) is never true");
    assertTrue(Plsql.notBetween(5, 1, 3));
    assertFalse(Plsql.notBetween(2, 1, 3));
    assertFalse(Plsql.notBetween(null, 1, 3));
    assertTrue(Plsql.notBetween(0, 1, null), "0 < 1 already decides it");
    assertFalse(Plsql.notBetween(2, 1, null));
    assertTrue(Plsql.notLike("BCD", "A%"));
    assertFalse(Plsql.notLike("ABC", "A%"));
    assertFalse(Plsql.notLike(null, "A%"));
  }

  @Test
  void aBooleanKeepsItsThirdValue() {
    assertTrue(Plsql.isTrue(true));
    assertFalse(Plsql.isTrue(null));
    assertTrue(Plsql.isFalse(false));
    assertFalse(Plsql.isFalse(null), "NOT NULL is unknown, not true");
    assertEquals(Boolean.TRUE, Plsql.bool3(true, false));
    assertEquals(Boolean.FALSE, Plsql.bool3(false, true));
    assertNull(Plsql.bool3(false, false));
    // v_ok := a > 1, with a NULL a
    assertNull(Plsql.bool3(Plsql.gt(null, 1), Plsql.le(null, 1)));
  }

  @Test
  void divisionByZeroIsItsOwnException() {
    // generated code turns this one into the migrated ZERO_DIVIDE; any other ArithmeticException stays a bug
    org.junit.jupiter.api.Assertions.assertThrows(Plsql.ZeroDivide.class, () -> Plsql.div(1, 0));
    org.junit.jupiter.api.Assertions.assertThrows(Plsql.ZeroDivide.class, () -> Plsql.div(new BigDecimal("2.5"), new BigDecimal("0.00")));
    assertNull(Plsql.div(null, 0), "NULL / 0 is NULL, not an error");
    assertNull(Plsql.div(1, null));
    assertEquals(0, new BigDecimal("3.5").compareTo(Plsql.div(7, 2)));
  }

  @Test
  void theUnpinnedClockIsUtcWhateverZoneTheJvmRunsIn() {
    // the pinned tests above could not see this: the default was the JVM's wall clock, labelled as UTC
    java.util.TimeZone before = java.util.TimeZone.getDefault();
    java.util.TimeZone.setDefault(java.util.TimeZone.getTimeZone("Asia/Tokyo"));
    try {
      Plsql.resetClock();
      long skew = Math.abs(java.time.Duration.between(java.time.Instant.now(), Plsql.systimestamp().toInstant()).toSeconds());
      assertTrue(skew < 5, "systimestamp() is " + skew + "s away from the real instant");
      assertEquals(0, Plsql.sysdate().getNano(), "an Oracle DATE has whole seconds");
    } finally {
      java.util.TimeZone.setDefault(before);
    }
  }

  @Test
  void aNumberBelowOneHasNoLeadingZeroAsText() {
    // Oracle's implicit TO_CHAR: 'x' || 0.5 is 'x.5'
    assertEquals(".5", Plsql.text(new BigDecimal("0.50")));
    assertEquals("-.5", Plsql.text(new BigDecimal("-0.5")));
    assertEquals("0", Plsql.text(new BigDecimal("0.00")));
    assertEquals("10.5", Plsql.text(new BigDecimal("10.50")));
    assertEquals("x.5", Plsql.concat("x", new BigDecimal("0.5")));
  }

  @Test
  void aDateFormatAppliesToEveryKindOfDateOrFails() {
    // it used to return the value's default rendering for anything but a LocalDateTime -- a wrong string, no error
    assertEquals("2026-01-02", Plsql.text(java.time.LocalDate.of(2026, 1, 2), "YYYY-MM-DD"));
    assertEquals("2026", Plsql.text(java.time.OffsetDateTime.parse("2026-01-02T03:04:05Z"), "YYYY"));
    assertEquals("202601", Plsql.text(LocalDateTime.of(2026, 1, 2, 3, 4), "YYYYMM"));
    org.junit.jupiter.api.Assertions.assertThrows(UnsupportedOperationException.class,
        () -> Plsql.text(new BigDecimal("1"), "YYYY"));
  }

  @Test
  void likeMatchesAcrossLineBreaks() {
    assertTrue(Plsql.like("a\nb", "a%"));
    assertTrue(Plsql.like("a\nb", "a_b"));
    assertFalse(Plsql.like("a\nb", "b%"));
  }

  // review #27, 17a: the constraint of a declaration is behaviour
  @Test
  void aConstrainedNumberRoundsToItsScaleAndRefusesPastItsPrecision() {
    assertEquals(new BigDecimal("1.01"), Plsql.fit(new BigDecimal("1.005"), 5, 2));
    assertEquals(new BigDecimal("999.99"), Plsql.fit("999.99", 5, 2));
    assertEquals(new BigDecimal("0.00"), Plsql.fit(0, 5, 2));
    assertNull(Plsql.fit(null, 5, 2));
    org.junit.jupiter.api.Assertions.assertThrows(
        Plsql.ValueError.class, () -> Plsql.fit(new BigDecimal("999.995"), 5, 2));
    org.junit.jupiter.api.Assertions.assertThrows(Plsql.ValueError.class, () -> Plsql.fit(1000, 3, 0));
    assertEquals(Long.valueOf(3), Plsql.fitLong(new BigDecimal("2.5"), 10));
    assertEquals(Integer.valueOf(-3), Plsql.fitInt(new BigDecimal("-2.5"), 5));
    assertNull(Plsql.fitLong(null, 10));
    org.junit.jupiter.api.Assertions.assertThrows(Plsql.ValueError.class, () -> Plsql.fitInt(100000L, 5));
  }

  @Test
  void aConstrainedStringRefusesWhatDoesNotFit() {
    assertEquals("abc", Plsql.fit("abc", 3, false));
    assertNull(Plsql.fit("", 3, false));
    assertEquals("日本語", Plsql.fit("日本語", 3, true));
    org.junit.jupiter.api.Assertions.assertThrows(
        Plsql.ValueError.class, () -> Plsql.fit("日本語", 3, false));   // 9 bytes
    org.junit.jupiter.api.Assertions.assertThrows(Plsql.ValueError.class, () -> Plsql.fit("abcd", 3, true));
  }

  @Test
  void anEmptyStringCrossesTheWriteBoundaryAsNull() {
    // #5 (decided 2026-09-19): '' and NULL stay one thing during the migration; blanks are not empty
    assertNull(Plsql.bind(""));
    assertNull(Plsql.bind("", "TEXT", 0));
    assertEquals(" ", Plsql.bind(" "));
    assertEquals(" ", Plsql.bind(" ", "TEXT", 0));
    assertEquals(7, Plsql.bind(7));
  }

  @Test
  void textThatIsNotANumberIsAValueErrorNotAJavaException() {
    // Issue #29 (14, 17a): `v_n := 'abc';` and TO_NUMBER('12x') are ORA-06502, which WHEN VALUE_ERROR catches
    assertEquals(new BigDecimal("12.5"), Plsql.toNumber(" 12.5 "));
    assertNull(Plsql.toNumber(null));
    assertNull(Plsql.toNumber(""));
    org.junit.jupiter.api.Assertions.assertThrows(Plsql.ValueError.class, () -> Plsql.toNumber("12x"));
    org.junit.jupiter.api.Assertions.assertThrows(Plsql.ValueError.class, () -> Plsql.dec("abc"));
    org.junit.jupiter.api.Assertions.assertThrows(Plsql.ValueError.class, () -> Plsql.fit("abc", 5, 2));
    org.junit.jupiter.api.Assertions.assertThrows(Plsql.ValueError.class, () -> Plsql.add("1", "x"));
    org.junit.jupiter.api.Assertions.assertThrows(
        UnsupportedOperationException.class, () -> Plsql.toNumber("1,234", "9,999"));
  }

  @Test
  void aDoubleBelowOneIsWrittenTheWayOracleWritesIt() {
    assertEquals(".5", Plsql.text(0.5));
    assertEquals("-.5", Plsql.text(-0.5d));
    assertEquals("2", Plsql.text(2.0));
  }
}
