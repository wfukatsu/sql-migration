package com.scalar.migrate.plsql;

import com.scalar.migrate.appside.OracleNumbers;
import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.Objects;

/**
 * Expression semantics the generated code depends on.
 *
 * <p>PL/SQL operators do not mean in Java what they mean in Oracle, and the differences are exactly the ones a
 * migration gets wrong quietly. Rather than emitting {@code a == b} and hoping, the generator emits a call here,
 * so the behaviour is written down once, tested once, and visible to a reviewer.
 *
 * <ul>
 *   <li><b>Comparison is three-valued.</b> In SQL, {@code NULL = NULL} is unknown, and a condition that is
 *       unknown does not run its branch. Every comparison here returns false when either side is null, which is
 *       how an {@code IF} behaves in PL/SQL.
 *   <li><b>Numbers compare by value.</b> {@code BigDecimal.equals} distinguishes 1.0 from 1.00;
 *       {@code compareTo} does not, and Oracle does not either.
 *   <li><b>The empty string is NULL.</b> Oracle stores {@code ''} as NULL, so code that migrated a column
 *       cannot tell them apart afterwards -- {@link #isNull} treats both as null rather than pretending.
 * </ul>
 */
public final class Plsql {
  private Plsql() {}

  public static boolean isNull(Object value) {
    return value == null || (value instanceof String s && s.isEmpty());
  }

  public static boolean isNotNull(Object value) {
    return !isNull(value);
  }

  /** Oracle's {@code =}: false when either side is null, value comparison for numbers. */
  public static boolean eq(Object a, Object b) {
    if (isNull(a) || isNull(b)) return false;
    if (a instanceof Number && b instanceof Number) return compare(a, b) == 0;
    if (a instanceof java.util.List<?> x && b instanceof java.util.List<?> y) return Boolean.TRUE.equals(sameMultiset(x, y));
    return Objects.equals(a, b);
  }

  public static boolean ne(Object a, Object b) {
    if (isNull(a) || isNull(b)) return false;  // NULL <> x is unknown, not true
    if (a instanceof java.util.List<?> x && b instanceof java.util.List<?> y) return Boolean.FALSE.equals(sameMultiset(x, y));
    return !eq(a, b);
  }

  /**
   * {@code =} on two nested tables: the same elements the same number of times, in any order -- Oracle compares
   * them as multisets. A NULL element makes it unknown unless the sizes already differ (samples/oracle-plsql-docs
   * 5-15, #63). Only a nested table has {@code =}; a VARRAY or an associative array does not compile in PL/SQL.
   */
  static Boolean sameMultiset(java.util.List<?> a, java.util.List<?> b) {
    if (a.size() != b.size()) return Boolean.FALSE;
    if (a.stream().anyMatch(Plsql::isNull) || b.stream().anyMatch(Plsql::isNull)) return null;
    java.util.List<Object> rest = new java.util.ArrayList<>(b);
    for (Object element : a) {
      int at = -1;
      for (int i = 0; i < rest.size(); i++) {
        if (eq(element, rest.get(i))) {
          at = i;
          break;
        }
      }
      if (at < 0) return Boolean.FALSE;
      rest.remove(at);
    }
    return Boolean.TRUE;
  }

  public static boolean lt(Object a, Object b) {
    return !isNull(a) && !isNull(b) && compare(a, b) < 0;
  }

  public static boolean le(Object a, Object b) {
    return !isNull(a) && !isNull(b) && compare(a, b) <= 0;
  }

  public static boolean gt(Object a, Object b) {
    return !isNull(a) && !isNull(b) && compare(a, b) > 0;
  }

  public static boolean ge(Object a, Object b) {
    return !isNull(a) && !isNull(b) && compare(a, b) >= 0;
  }

  @SuppressWarnings({"unchecked", "rawtypes"})
  private static int compare(Object a, Object b) {
    if (a instanceof Number && b instanceof Number) {
      return num(a).compareTo(num(b));
    }
    return ((Comparable) a).compareTo(b);
  }

  /** Oracle's {@code ||}: NULL behaves as an empty string, which Java's {@code +} does not. */
  public static String concat(Object... parts) {
    StringBuilder out = new StringBuilder();
    for (Object part : parts) {
      if (part != null) out.append(text(part));
    }
    return out.length() == 0 ? null : out.toString();  // '' is NULL in Oracle
  }

  public static String text(Object value) {
    if (value == null) return "";
    if (value instanceof BigDecimal d) {
      // Oracle's implicit TO_CHAR writes no zero before the point: 0.5 is '.5' and -0.5 is '-.5'. With Java's
      // "0.5", every `'...' || number` below one came out one character longer than Oracle's
      String plain = d.stripTrailingZeros().toPlainString();
      if (plain.startsWith("0.")) return plain.substring(1);
      if (plain.startsWith("-0.")) return "-" + plain.substring(2);
      return plain;
    }
    // a BINARY_DOUBLE / FLOAT value, or a Double a driver handed back: the same rule, not Java's "0.5"
    if (value instanceof Double || value instanceof Float) {
      double d = ((Number) value).doubleValue();
      if (!Double.isNaN(d) && !Double.isInfinite(d)) return text(BigDecimal.valueOf(d));
    }
    return String.valueOf(value);
  }

  public static <T> T nvl(T value, T fallback) {
    return isNull(value) ? fallback : value;
  }

  /**
   * Overloads for the shapes generated code produces.
   *
   * <p>`NVL(x, 0)` mixes a {@code BigDecimal} with an {@code int}, and the generic method cannot infer a type
   * from that. Naming the combinations the generator emits is simpler than making it wrap every literal.
   */
  public static BigDecimal nvl(BigDecimal value, long fallback) {
    return isNull(value) ? BigDecimal.valueOf(fallback) : value;
  }

  public static BigDecimal nvl(Object value, long fallback) {
    return isNull(value) ? BigDecimal.valueOf(fallback) : num(value);
  }

  /** Oracle's ROUND is half-up; BigDecimal's default is half-even. */
  /** `ROUND(x)`: to a whole number, half away from zero (samples/oracle-samples b06_5, #55). */
  public static BigDecimal round(Object value) {
    return round(value, 0);
  }

  public static BigDecimal round(Object value, int scale) {
    return value == null ? null : OracleNumbers.round(num(value), scale);
  }

  /** Oracle's TRUNC on a DATE drops the time of day. */
  public static LocalDateTime trunc(LocalDateTime value) {
    return value == null ? null : value.toLocalDate().atStartOfDay();
  }

  /**
   * Arithmetic, with Oracle's NULL rule: any operand null makes the result null.
   *
   * <p>Java's {@code +} on a boxed null throws, and on a {@code BigDecimal} does not compile at all. Routing
   * arithmetic through here is what lets a generated expression mix a literal, an {@code Integer} and a
   * {@code BigDecimal} the way the PL/SQL did.
   */
  /**
   * `a + b`。**日時 + 数値は「日数を足した DATE」**である（Oracle は TIMESTAMP を DATE に変えてから
   * 足す）。数値どうしは NUMBER の足し算。
   *
   * <p>戻り値が {@code Object} なのは、日時の算術が日時を返すからである。以前は BigDecimal に
   * 決め打ちしていて、`SYSTIMESTAMP - p_keep_days` を WHERE の値として持ち上げた瞬間に、日時を
   * 数値に直そうとして落ちた（`prc_purge_audit` / 2026-09-19）。
   */
  public static Object add(Object a, Object b) {
    if (isTemporal(a) && b instanceof Number days) return shiftDays(a, days, 1);
    if (a instanceof Number days && isTemporal(b)) return shiftDays(b, days, 1);
    return arith(a, b, BigDecimal::add);
  }

  private static boolean isTemporal(Object value) {
    return value instanceof LocalDateTime || value instanceof java.time.OffsetDateTime;
  }

  /**
   * DATE に日数を足し引きする。TIMESTAMP WITH TIME ZONE は、その値自身の時刻のまま DATE になる
   * （ゾーン変換はしない、秒未満は落ちる——`castDate` と同じ規則。#13 で実測）。日数の端数は
   * 秒に丸める。DATE が秒までしか持たないからである。
   */
  private static LocalDateTime shiftDays(Object value, Number days, int sign) {
    LocalDateTime base = castDate(value);
    long seconds = new BigDecimal(days.toString()).multiply(BigDecimal.valueOf(86400))
        .setScale(0, java.math.RoundingMode.HALF_UP).longValueExact();
    return base.plusSeconds(sign * seconds);
  }

  /**
   * 単項マイナス。`NULL` は `NULL` のまま——Oracle の算術は NULL を伝播する。
   *
   * <p>`sub(0, x)` で代用しない: `sub` は DATE 同士の引き算を「日数」に解釈するので、意味の違う
   * ものを 1 つの入口に押し込むことになる。
   */
  /**
   * `CAST(x AS DATE)`。Oracle の DATE は秒までしか持たないので、**秒未満は切り捨てる**
   * （四捨五入ではない——Oracle 23ai に `.999999` を渡して確かめた。#13）。
   *
   * <p>タイムゾーンつきの値からは zone も落ちる。Oracle の DATE が持てないからである。
   */
  public static LocalDateTime castDate(Object value) {
    if (isNull(value)) return null;
    if (value instanceof LocalDateTime moment) return moment.withNano(0);
    if (value instanceof java.time.OffsetDateTime moment) return moment.toLocalDateTime().withNano(0);
    if (value instanceof java.time.LocalDate day) return day.atStartOfDay();
    throw new IllegalArgumentException("CAST(... AS DATE) を日付でない値に適用した: " + value.getClass());
  }

  public static BigDecimal neg(Object value) {
    if (isNull(value)) return null;
    BigDecimal decimal = value instanceof BigDecimal d ? d
        : value instanceof Number n ? num(n) : null;
    if (decimal == null) {
      throw new IllegalArgumentException("単項マイナスを数値でない値に適用した: " + value.getClass());
    }
    return decimal.negate();
  }

  public static Object sub(Object a, Object b) {
    if (isTemporal(a) && isTemporal(b)) {
      // Oracle subtracts two DATEs into a number of days, fraction included
      // (the division rounds as Oracle's NUMBER does: #64)
      return OracleNumbers.divide(BigDecimal.valueOf(java.time.Duration.between(castDate(b), castDate(a)).toSeconds()),
          BigDecimal.valueOf(86400));
    }
    // `SYSTIMESTAMP - 30` は 30 日前の DATE である。日数として数値に直すと落ちる
    if (isTemporal(a) && b instanceof Number days) return shiftDays(a, days, -1);
    return arith(a, b, BigDecimal::subtract);
  }

  public static BigDecimal mul(Object a, Object b) {
    return arith(a, b, OracleNumbers::multiply);
  }

  /** ORA-01476. Its own type so that generated code can tell it from Java's other ArithmeticExceptions. */
  public static final class ZeroDivide extends ArithmeticException {
    public ZeroDivide() {
      super("ORA-01476: divisor is equal to zero");
    }
  }

  public static BigDecimal div(Object a, Object b) {
    if (!isNull(a) && !isNull(b) && num(b).signum() == 0) throw new ZeroDivide();
    return arith(a, b, OracleNumbers::divide);
  }

  private static BigDecimal arith(Object a, Object b,
      java.util.function.BinaryOperator<BigDecimal> operator) {
    if (a == null || b == null) return null;
    return operator.apply(num(a), num(b));
  }

  /** {@code TO_CHAR(value, format)}. Only the formats the corpus uses are mapped; the rest raise. */
  public static String text(Object value, String format) {
    if (value == null) return null;
    String pattern = switch (format.toUpperCase()) {
      case "YYYY-MM-DD" -> "yyyy-MM-dd";
      case "YYYY-MM-DD HH24:MI:SS" -> "yyyy-MM-dd HH:mm:ss";
      case "YYYYMM" -> "yyyyMM";
      case "YYYY" -> "yyyy";
      default -> throw new UnsupportedOperationException("TO_CHAR format not mapped: " + format);
    };
    java.time.format.DateTimeFormatter formatter = java.time.format.DateTimeFormatter.ofPattern(pattern);
    if (value instanceof LocalDateTime d) return d.format(formatter);
    if (value instanceof java.time.LocalDate d) return d.atStartOfDay().format(formatter);
    if (value instanceof java.time.OffsetDateTime d) return d.toLocalDateTime().format(formatter);
    if (value instanceof java.time.Instant d) return d.atOffset(java.time.ZoneOffset.UTC).toLocalDateTime().format(formatter);
    // A date format applied to something that is not a date. It used to fall through to text(value) and return
    // the value's default rendering -- `TO_CHAR(SYSTIMESTAMP, 'YYYY-MM-DD')` gave `2026-09-19T15:11:33.746771Z`,
    // a wrong string and no error
    throw new UnsupportedOperationException(
        "TO_CHAR(" + value.getClass().getSimpleName() + ", '" + format + "') is not mapped");
  }

  /**
   * A value read as a NUMBER. Text that is not a number is ORA-06502 in PL/SQL (`v_n := 'abc';`,
   * `TO_NUMBER('12x')`), which a VALUE_ERROR handler catches. It used to leave as Java's NumberFormatException,
   * which no migrated handler names.
   */
  private static BigDecimal num(Object value) {
    try {
      return OracleNumbers.toBigDecimal(value);
    } catch (NumberFormatException e) {
      throw new ValueError("character to number conversion error");
    }
  }

  /** TO_NUMBER(value): NULL for NULL, ORA-06502 for text that is not a number. */
  public static BigDecimal toNumber(Object value) {
    return isNull(value) ? null : num(value);
  }

  /** TO_NUMBER(value, format): a format model is not mapped, and guessing one would read '1,234' two ways. */
  public static BigDecimal toNumber(Object value, Object format) {
    throw new UnsupportedOperationException("TO_NUMBER(value, '" + format + "') is not mapped");
  }

  /** Coerce to Oracle's NUMBER. Generated code uses it wherever a literal or a ternary lands in a NUMBER. */
  public static BigDecimal dec(Object value) {
    return isNull(value) ? null : num(value);
  }

  public static BigDecimal number(long value) {
    return BigDecimal.valueOf(value);
  }

  // --- constrained declarations ---------------------------------------------------------------------------
  //
  // `v NUMBER(5,2)` and `v VARCHAR2(3)` are part of the behaviour: Oracle rounds 1.005 to 1.01 on the way into the
  // first and raises VALUE_ERROR for 'abcd' on the way into the second. A BigDecimal and a String hold anything,
  // so the generated code passes every value assigned to such a variable through here.

  /** ORA-06502. Its own type so that generated code can turn it into the migrated VALUE_ERROR. */
  public static final class ValueError extends RuntimeException {
    public ValueError(String detail) {
      super("ORA-06502: PL/SQL: numeric or value error: " + detail);
    }
  }

  /** A value going into NUMBER(precision, scale): rounded half-up to the scale, refused past the precision. */
  public static BigDecimal fit(Object value, int precision, int scale) {
    if (isNull(value)) return null;
    BigDecimal rounded = num(value).setScale(scale, java.math.RoundingMode.HALF_UP);
    if (rounded.signum() != 0 && rounded.precision() - rounded.scale() > precision - scale) {
      throw new ValueError("number precision too large");
    }
    return rounded;
  }

  /** {@link #fit(Object, int, int)} for a NUMBER(p) the generated code holds as a Long (p up to 18). */
  public static Long fitLong(Object value, int precision) {
    BigDecimal fitted = fit(value, precision, 0);
    return fitted == null ? null : fitted.longValueExact();
  }

  /** {@link #fit(Object, int, int)} for a NUMBER(p) the generated code holds as an Integer (p up to 9). */
  public static Integer fitInt(Object value, int precision) {
    BigDecimal fitted = fit(value, precision, 0);
    return fitted == null ? null : fitted.intValueExact();
  }

  /** A value going into VARCHAR2(size): refused when longer. {@code chars} is VARCHAR2(n CHAR); else bytes. */
  public static String fit(Object value, int size, boolean chars) {
    if (isNull(value)) return null;
    String text = text(value);
    int length = chars ? text.codePointCount(0, text.length())
        : text.getBytes(java.nio.charset.StandardCharsets.UTF_8).length;
    if (length > size) throw new ValueError("character string buffer too small");
    return text;
  }

  /**
   * A value going into CHAR(size): refused past the size as {@link #fit(Object, int, boolean)} does, then padded
   * with blanks up to it. {@code first_name CHAR(10) := 'John '} holds {@code 'John      '} in Oracle, and a
   * concatenation or a LENGTH shows it (samples/oracle-plsql-docs 3-1, #62).
   */
  public static String pad(Object value, int size, boolean chars) {
    String text = fit(value, size, chars);
    if (text == null) return null;
    int length = chars ? text.codePointCount(0, text.length())
        : text.getBytes(java.nio.charset.StandardCharsets.UTF_8).length;
    return length >= size ? text : text + " ".repeat(size - length);
  }

  /**
   * One side of a blank-padded comparison (a CHAR(n) local against a literal or another CHAR): the trailing
   * blanks do not count, and all blanks is still a value -- {@code rtrim} would make it NULL.
   */
  public static Object unpad(Object value) {
    if (!(value instanceof String text)) return value;
    int end = text.length();
    while (end > 0 && text.charAt(end - 1) == ' ') end--;
    return text.substring(0, end);
  }

  /** ORA-01426. A PLS_INTEGER computation or assignment past 32 bits; its own type, like {@link ZeroDivide}. */
  public static final class NumericOverflow extends ArithmeticException {
    public NumericOverflow() {
      super("ORA-01426: numeric overflow");
    }
  }

  /**
   * A value that has to fit PLS_INTEGER: {@code p1 + p2} of two PLS_INTEGERs is computed in 32 bits, and
   * 2147483647 + 1 is ORA-01426 even on its way into a NUMBER (samples/oracle-plsql-docs 3-4, #60). The value
   * comes back as it was given, so the caller's type does not change.
   */
  public static <T> T plsInteger(T value) {
    if (isNull(value)) return value;
    BigDecimal n = num(value);
    if (n.compareTo(BigDecimal.valueOf(Integer.MIN_VALUE)) < 0 || n.compareTo(BigDecimal.valueOf(Integer.MAX_VALUE)) > 0) {
      throw new NumericOverflow();
    }
    return value;
  }

  /** {@code SUBTYPE Digit IS PLS_INTEGER RANGE 0..9}, NATURAL, POSITIVE, SIGNTYPE: VALUE_ERROR outside the range (#59). */
  public static <T> T inRange(T value, long low, long high) {
    if (isNull(value)) return value;
    BigDecimal n = num(value);
    if (n.compareTo(BigDecimal.valueOf(low)) < 0 || n.compareTo(BigDecimal.valueOf(high)) > 0) {
      throw new ValueError("value " + n.toPlainString() + " is outside " + low + ".." + high);
    }
    return value;
  }

  /** A NOT NULL variable, SIMPLE_INTEGER, NATURALN, POSITIVEN: assigning NULL is VALUE_ERROR (3-6, #60). */
  public static <T> T notNull(T value) {
    if (isNull(value)) throw new ValueError("NULL assigned to a NOT NULL variable");
    return value;
  }

  // --- the bind boundary (P3-1) -------------------------------------------------------------------------
  //
  // PL/SQL NUMBER becomes BigDecimal in the generated code, and ScalarDB's JDBC driver refuses a BigDecimal
  // outright (DB-SQL-10016). So a value crossing into ScalarDB has to become the column's own type, and a value
  // read back has to become a BigDecimal again. H2 accepted the BigDecimal, which is why this only showed up
  // once the generated code met a real cluster.
  //
  // `scale` is how many decimal places the column keeps when it is stored as an integer. Oracle NUMBER(12,2) in
  // a BIGINT column is scale 2 -- the column holds cents. Scale 0 means the column holds the value as it is.

  /** A PL/SQL value on its way into a ScalarDB column of the given type. */
  /**
   * A value whose column the analysis could not name, at the write boundary: '' goes in as NULL.
   *
   * <p>Decision of 2026-09-19 (#5): during the migration '' and NULL stay one thing, as they are in Oracle, and
   * the boundary is where that is kept -- an empty string must not reach ScalarDB, which would store it as one and
   * make `x IS NULL` false for a row Oracle would have found. A string of blanks is not empty and is kept.
   */
  public static Object bind(Object value) {
    return isNull(value) ? null : value;
  }

  public static Object bind(Object value, String scalarDbType, int scale) {
    if (isNull(value)) return null;
    String type = scalarDbType == null ? "" : scalarDbType.toUpperCase();
    if (value instanceof java.time.OffsetDateTime moment && type.equals("TIMESTAMP")) {
      // `SYSTIMESTAMP` と `AuditContext.now()` はタイムゾーンつきだが、行き先は TIMESTAMP 列である。
      // Oracle が TIMESTAMP WITH TIME ZONE を TIMESTAMP 列へ入れるときと同じことをする:
      // **offset を落とし、日時のフィールドはそのまま**——セッションのタイムゾーンへ換算はしない。
      // Oracle 23ai で実測して確かめた（#23）:
      //
      //   セッション +09:00、SYSTIMESTAMP が 2026-09-18 02:55:09 +00:00
      //     -> TIMESTAMP 列には 2026-09-18 02:55:09（11:55:09 ではない）
      //   '2026-01-15 09:30:00 -05:00' -> 2026-01-15 09:30:00
      //
      // 換算しないので、ここで渡す値は「Oracle が書いたはずの壁時計」である。落とさずに渡すと
      // ScalarDB SQL のドライバが型ごと拒否する（DB-SQL-10016）。TIMESTAMPTZ 列はタイムゾーンを
      // 保てるので、この変換の対象ではない。
      return moment.toLocalDateTime();
    }
    if (value instanceof java.time.OffsetDateTime moment && type.equals("TIMESTAMPTZ")) {
      // TIMESTAMPTZ 列へは**瞬間**として渡す。ScalarDB SQL のドライバは OffsetDateTime を型ごと
      // 拒否する（DB-SQL-10016。`record_payment` の `paid_at = SYSTIMESTAMP` で実際に落ちた）。
      //
      // **失われるものが 1 つある: 元の offset である。** Oracle の TIMESTAMP WITH TIME ZONE は
      // 瞬間と offset の両方を持つが、ScalarDB の TIMESTAMPTZ は瞬間だけを持つ。読み戻すと同じ
      // 瞬間の別の表記（UTC）になる。瞬間は同じなので、比較・並べ替え・差は変わらない。
      return moment.toInstant();
    }
    BigDecimal decimal = value instanceof BigDecimal d ? d
        : value instanceof Number n ? num(n) : null;
    if (decimal == null) return value;  // TEXT, DATE, TIMESTAMPTZ and the like pass through untouched
    switch (type) {
      case "BIGINT":
        return scaled(decimal, scale).longValueExact();
      case "INT":
        return scaled(decimal, scale).intValueExact();
      case "DOUBLE":
        // A NUMBER(p,s) column rounds what is written into it to s decimals, half-up, and a DOUBLE column rounds
        // nothing: 1234.565 into NUMBER(14,2) is 1234.57 in Oracle and stayed 1234.565 here. `scale` is the Oracle
        // column's; 0 means the column declares none (NUMBER, BINARY_DOUBLE) -- a NUMBER(p,0) is never a DOUBLE.
        return (scale > 0 ? decimal.setScale(scale, java.math.RoundingMode.HALF_UP) : decimal).doubleValue();
      case "FLOAT":
        return decimal.floatValue();
      case "TEXT":
        return decimal.toPlainString();
      default:
        return decimal;
    }
  }

  /**
   * Round to the column's scale the way Oracle does when a value is stored into a NUMBER(p,s): half-up, not
   * half-even, and never silently truncated. Refusing a value Oracle would have accepted would make the
   * migrated code stricter than the database it is reproducing.
   */
  private static java.math.BigInteger scaled(BigDecimal value, int scale) {
    return OracleNumbers.round(value, scale).movePointRight(scale).toBigIntegerExact();
  }

  /** A value read out of a ScalarDB column of the given type, on its way back into PL/SQL NUMBER. */
  public static BigDecimal read(Object value, String scalarDbType, int scale) {
    if (isNull(value)) return null;
    if (value instanceof Long l) return BigDecimal.valueOf(l, scale);
    if (value instanceof Integer i) return BigDecimal.valueOf(i, scale);
    if (value instanceof BigDecimal d) return scale == 0 ? d : d.movePointLeft(scale);
    if (value instanceof Number n) return num(n);
    return num(value);
  }

  /**
   * The database clock, which is not the JVM clock.
   *
   * <p>Oracle's SYSDATE comes from the server and its time zone. Generated code calls this so the difference is
   * visible and a test can pin it; leaving {@code LocalDateTime.now()} inline would make it neither.
   */
  public static LocalDateTime sysdate() {
    // an Oracle DATE has whole seconds; SYSTIMESTAMP is the one with a fraction
    return CLOCK.get().truncatedTo(java.time.temporal.ChronoUnit.SECONDS);
  }

  /**
   * Oracle's SYSTIMESTAMP, in the zone the migration fixed.
   *
   * <p>UTC, by decision (plan §9, 2026-09-17): everything is stored in UTC and converted for display. Using
   * the JVM's default zone instead -- which this did -- makes the value depend on where the process happens to
   * run, so two deployments of the same code would write different instants for the same moment, and a
   * comparison against Oracle would pass or fail by accident of the machine.
   */
  public static java.time.OffsetDateTime systimestamp() {
    return CLOCK.get().atOffset(java.time.ZoneOffset.UTC);
  }

  /**
   * Pin the clock, for a test or for a run that has to be reproducible. The supplier gives the **UTC** wall
   * clock: that is what {@link #systimestamp()} labels it as.
   */
  public static void setClock(java.util.function.Supplier<LocalDateTime> clock) {
    CLOCK = clock;
  }

  /** Back to the real clock. */
  public static void resetClock() {
    CLOCK = UTC_NOW;
  }

  // The wall clock *in UTC*. This was `LocalDateTime::now` -- the JVM's zone -- which systimestamp() then labelled
  // as UTC: on a machine in Asia/Tokyo every stored instant was nine hours in the future. The decision above was
  // taken, the tests pinned the clock, and the unpinned default never met it.
  private static final java.util.function.Supplier<LocalDateTime> UTC_NOW =
      () -> LocalDateTime.now(java.time.ZoneOffset.UTC);
  private static java.util.function.Supplier<LocalDateTime> CLOCK = UTC_NOW;

  /**
   * Oracle's RTRIM / LTRIM, including on the empty string.
   *
   * <p>Two places `''` bites, both found by replaying the recorded Oracle answers (P3-3). `RTRIM('')` is NULL
   * because `''` already is one, which testing `value == null` misses. And `RTRIM(' ')` is NULL as well: the
   * result is the empty string, and an empty string in Oracle is NULL however it was arrived at. {@link
   * #concat} has always had the second rule; these did not.
   */
  /** `INITCAP`: the first letter of each word upper-cased, the rest lower-cased (words split on non-letters/digits). */
  public static String initcap(Object value) {
    if (isNull(value)) return null;
    StringBuilder out = new StringBuilder();
    boolean start = true;
    for (int cp : text(value).codePoints().toArray()) {
      boolean word = Character.isLetterOrDigit(cp);
      out.appendCodePoint(word ? (start ? Character.toUpperCase(cp) : Character.toLowerCase(cp)) : cp);
      start = !word;
    }
    return out.toString();
  }

  /** `TRIM(x)`: both ends, spaces only; an empty result is NULL as in Oracle. */
  public static String trim(Object value) {
    return isNull(value) ? null : emptyIsNull(text(value).strip());
  }

  /** A PLS_INTEGER target: `i := i + 1` goes through {@link #add} (which returns Object) and lands in an Integer. */
  /**
   * A value going into a PLS_INTEGER (an Integer here): Oracle rounds a fraction half away from zero, and a value
   * past 32 bits is ORA-01426 -- not Java's ArithmeticException, which no handler of the migrated code names (#60).
   */
  public static Integer toInt(Object value) {
    if (isNull(value)) return null;
    return plsInteger(num(value).setScale(0, java.math.RoundingMode.HALF_UP)).intValue();
  }

  public static Long toLong(Object value) {
    return isNull(value) ? null : num(value).longValueExact();
  }

  /** `SQLERRM` inside a handler: Oracle's text is `ORA-nnnnn: message` (the code is negative in PL/SQL). */
  public static String sqlerrm(int code, String message) {
    if (code == 0) return "ORA-0000: normal, successful completion";
    if (code == 100) return "ORA-01403: no data found";
    return String.format("ORA-%05d: %s", Math.abs(code), message == null ? "" : message);
  }

  /** `DBMS_UTILITY.FORMAT_ERROR_BACKTRACE`: where the exception came from, one `ORA-06512: at` line per frame. */
  public static String errorBacktrace(Throwable error) {
    if (error == null) return null;
    StringBuilder out = new StringBuilder();
    for (StackTraceElement frame : error.getStackTrace()) {
      if (frame.getClassName().startsWith("java.") || frame.getClassName().startsWith("jdk.")) continue;
      out.append("ORA-06512: at \"").append(frame.getClassName()).append('.').append(frame.getMethodName())
          .append("\", line ").append(frame.getLineNumber()).append('\n');
      if (out.length() > 2000) break;
    }
    return out.length() == 0 ? null : out.toString();
  }

  // ---- PL/SQL collections (#45). A nested table / VARRAY is a 1-based List; an associative array (INDEX BY
  // VARCHAR2) is a TreeMap, which walks its keys in order the way FIRST / NEXT do. `DELETE(i)` leaves a gap in
  // a nested table (COUNT drops, EXISTS(i) is false, NEXT skips it): the gap is the GAP marker in the List.
  private static final Object GAP = new Object() {
    @Override public String toString() { return "<deleted element>"; }
  };
  @SafeVarargs
  public static <E> java.util.List<E> table(E... elements) {
    return new java.util.ArrayList<>(java.util.Arrays.asList(elements));
  }

  public static <K, E> java.util.Map<K, E> indexBy() {
    return new java.util.TreeMap<>();
  }

  private static RuntimeException collectionIsNull() {
    return new IllegalStateException("COLLECTION_IS_NULL (ORA-06531): the collection was never initialised");
  }

  private static Object key(Object collection, Object key) {
    // PLS_INTEGER keys arrive as Integer, Long or BigDecimal depending on the arithmetic that produced them
    if (collection instanceof java.util.List || key instanceof Number) return key == null ? null : num(key).intValueExact();
    return key;
  }

  public static int count(Object collection) {
    if (collection == null) throw collectionIsNull();
    if (collection instanceof java.util.List<?> list) {
      int n = 0;
      for (Object e : list) if (e != GAP) n++;
      return n;
    }
    return ((java.util.Map<?, ?>) collection).size();
  }

  public static Object first(Object collection) {
    if (collection == null) throw collectionIsNull();
    if (collection instanceof java.util.List<?> list) return next(list, 0);
    java.util.TreeMap<?, ?> map = (java.util.TreeMap<?, ?>) collection;
    return map.isEmpty() ? null : map.firstKey();
  }

  public static Object last(Object collection) {
    if (collection == null) throw collectionIsNull();
    if (collection instanceof java.util.List<?> list) return prior(list, list.size() + 1);
    java.util.TreeMap<?, ?> map = (java.util.TreeMap<?, ?>) collection;
    return map.isEmpty() ? null : map.lastKey();
  }

  @SuppressWarnings("unchecked")
  public static Object next(Object collection, Object at) {
    if (collection == null) throw collectionIsNull();
    if (at == null) return null;
    if (collection instanceof java.util.List<?> list) {
      for (int i = num(at).intValueExact() + 1; i <= list.size(); i++) if (list.get(i - 1) != GAP) return i;
      return null;
    }
    return ((java.util.TreeMap<Object, ?>) collection).higherKey(key(collection, at));
  }

  @SuppressWarnings("unchecked")
  public static Object prior(Object collection, Object at) {
    if (collection == null) throw collectionIsNull();
    if (at == null) return null;
    if (collection instanceof java.util.List<?> list) {
      for (int i = Math.min(num(at).intValueExact(), list.size() + 1) - 1; i >= 1; i--) if (list.get(i - 1) != GAP) return i;
      return null;
    }
    return ((java.util.TreeMap<Object, ?>) collection).lowerKey(key(collection, at));
  }

  public static boolean exists(Object collection, Object at) {
    if (collection == null) return false;   // Oracle: EXISTS on a null collection is FALSE, not an error
    if (at == null) return false;
    if (collection instanceof java.util.List<?> list) {
      int i = num(at).intValueExact();
      return i >= 1 && i <= list.size() && list.get(i - 1) != GAP;
    }
    return ((java.util.Map<?, ?>) collection).containsKey(key(collection, at));
  }

  @SuppressWarnings("unchecked")
  public static Object at(Object collection, Object at) {
    if (collection == null) throw collectionIsNull();
    if (collection instanceof java.util.List<?> list) {
      int i = num(at).intValueExact();
      if (i < 1 || i > list.size()) {
        throw new IndexOutOfBoundsException("SUBSCRIPT_BEYOND_COUNT (ORA-06533): element " + i + " of " + list.size());
      }
      Object element = list.get(i - 1);
      if (element == GAP) throw new IllegalStateException("NO_DATA_FOUND (ORA-01403): element " + i + " was deleted");
      return element;
    }
    java.util.Map<Object, ?> map = (java.util.Map<Object, ?>) collection;
    Object k = key(collection, at);
    if (!map.containsKey(k)) throw new IllegalStateException("NO_DATA_FOUND (ORA-01403): no element at " + k);
    return map.get(k);
  }

  @SuppressWarnings("unchecked")
  public static void set(Object collection, Object at, Object value) {
    if (collection == null) throw collectionIsNull();
    if (collection instanceof java.util.List<?> raw) {
      java.util.List<Object> list = (java.util.List<Object>) raw;
      int i = num(at).intValueExact();
      if (i < 1) throw new IndexOutOfBoundsException("SUBSCRIPT_OUTSIDE_LIMIT (ORA-06532): element " + i);
      // an INDEX BY PLS_INTEGER table takes any key: the List grows to it, the skipped slots being gaps. (A nested
      // table would raise SUBSCRIPT_BEYOND_COUNT here; both are Lists, so the lenient rule serves both)
      while (list.size() < i) list.add(GAP);
      list.set(i - 1, value);
      return;
    }
    ((java.util.Map<Object, Object>) collection).put(key(collection, at), value);
  }

  @SuppressWarnings("unchecked")
  public static void extend(Object collection) {
    extend(collection, 1);
  }

  @SuppressWarnings("unchecked")
  public static void extend(Object collection, Object n) {
    if (collection == null) throw collectionIsNull();
    if (!(collection instanceof java.util.List<?>)) throw new IllegalStateException("EXTEND on an associative array");
    java.util.List<Object> list = (java.util.List<Object>) collection;
    for (int i = num(n).intValueExact(); i > 0; i--) list.add(null);
  }

  public static void delete(Object collection) {
    if (collection == null) throw collectionIsNull();
    if (collection instanceof java.util.List<?> list) list.clear(); else ((java.util.Map<?, ?>) collection).clear();
  }

  @SuppressWarnings("unchecked")
  public static void delete(Object collection, Object at) {
    if (collection == null) throw collectionIsNull();
    if (collection instanceof java.util.List<?> list) {
      int i = num(at).intValueExact();
      if (i < 1 || i > list.size()) return;   // Oracle: deleting a missing element does nothing
      ((java.util.List<Object>) list).set(i - 1, GAP);
      return;
    }
    ((java.util.Map<?, ?>) collection).remove(key(collection, at));
  }

  /** `v.TRIM` / `v.TRIM(n)` on a nested table (`trim` itself is the string function). */
  public static void trimTable(Object collection) {
    trimTable(collection, 1);
  }

  public static void trimTable(Object collection, Object n) {
    if (collection == null) throw collectionIsNull();
    java.util.List<?> list = (java.util.List<?>) collection;
    for (int i = num(n).intValueExact(); i > 0 && !list.isEmpty(); i--) list.remove(list.size() - 1);
  }

  // DBMS_OUTPUT: the session's output buffer. Per thread here; nothing is written to a table, so a comparison
  // of table state never sees it. `output()` hands the lines back and clears the buffer.
  private static final ThreadLocal<java.util.List<String>> OUTPUT = ThreadLocal.withInitial(java.util.ArrayList::new);
  private static final ThreadLocal<StringBuilder> OUTPUT_LINE = ThreadLocal.withInitial(StringBuilder::new);

  public static void putLine(Object value) {
    OUTPUT_LINE.get().append(isNull(value) ? "" : text(value));
    newLine();
  }

  public static void put(Object value) {
    OUTPUT_LINE.get().append(isNull(value) ? "" : text(value));
  }

  public static void newLine() {
    OUTPUT.get().add(OUTPUT_LINE.get().toString());
    OUTPUT_LINE.get().setLength(0);
  }

  public static java.util.List<String> output() {
    java.util.List<String> lines = java.util.List.copyOf(OUTPUT.get());
    OUTPUT.get().clear();
    OUTPUT_LINE.get().setLength(0);
    return lines;
  }

  // Oracle-supplied packages the generator maps (plsql/builtins.py, #55)

  /** `DBMS_SESSION.SLEEP` / `DBMS_LOCK.SLEEP`: seconds, fractions allowed. */
  public static void sleep(Object seconds) {
    if (isNull(seconds)) throw new ValueError("DBMS_SESSION.SLEEP: seconds is null");
    long millis = num(seconds).movePointRight(3).setScale(0, java.math.RoundingMode.HALF_UP).longValueExact();
    try {
      Thread.sleep(Math.max(0, millis));
    } catch (InterruptedException e) {
      Thread.currentThread().interrupt();
      throw new IllegalStateException("interrupted while sleeping", e);
    }
  }

  /** `DBMS_RANDOM.VALUE`: at least 0 and less than 1. */
  public static BigDecimal randomValue() {
    return BigDecimal.valueOf(java.util.concurrent.ThreadLocalRandom.current().nextDouble());
  }

  /** `DBMS_RANDOM.VALUE(low, high)`: at least low and less than high. */
  public static BigDecimal randomValue(Object low, Object high) {
    if (isNull(low) || isNull(high)) return null;
    BigDecimal from = num(low);
    return from.add(num(high).subtract(from).multiply(randomValue()));
  }

  /** `DBMS_RANDOM.STRING(opt, len)`: U upper, L lower, A mixed, X upper and digits, P any printable. */
  public static String randomString(Object opt, Object len) {
    if (isNull(len)) return null;
    String option = isNull(opt) ? "U" : text(opt).toUpperCase(java.util.Locale.ROOT);
    String alphabet = switch (option.isEmpty() ? 'U' : option.charAt(0)) {
      case 'L' -> "abcdefghijklmnopqrstuvwxyz";
      case 'A' -> "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz";
      case 'X' -> "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
      case 'P' -> " !\"#$%&'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~";
      default -> "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
    };
    int length = Math.max(0, Math.min(4000, num(len).intValue()));
    StringBuilder out = new StringBuilder(length);
    java.util.concurrent.ThreadLocalRandom random = java.util.concurrent.ThreadLocalRandom.current();
    for (int i = 0; i < length; i++) out.append(alphabet.charAt(random.nextInt(alphabet.length())));
    return emptyIsNull(out.toString());
  }

  /** `DBMS_UTILITY.GET_TIME`: a clock in hundredths of a second, for differences only (its origin is arbitrary). */
  public static BigDecimal getTime() {
    return BigDecimal.valueOf(System.nanoTime() / 10_000_000L);
  }

  public static String rtrim(Object value) {
    return isNull(value) ? null : emptyIsNull(text(value).stripTrailing());
  }

  public static String ltrim(Object value) {
    return isNull(value) ? null : emptyIsNull(text(value).stripLeading());
  }

  /**
   * TIMESTAMP WITH TIME ZONE の値として受け取る（2026-09-19）。
   *
   * <p>ScalarDB の TIMESTAMPTZ 列は、読むと {@code Instant} で返る。生成コードは PL/SQL の型から
   * {@code OffsetDateTime} を宣言するので、そのまま cast すると {@code ClassCastException} で落ちた
   * （`last_paid_at`）。書くときに offset を落として瞬間にしている（`bind`）ので、読むときは同じ瞬間を
   * UTC で表す——元の offset はもう無い。
   */
  public static java.time.OffsetDateTime zoned(Object value) {
    if (isNull(value)) return null;
    if (value instanceof java.time.OffsetDateTime moment) return moment;
    if (value instanceof java.time.Instant instant) return instant.atOffset(java.time.ZoneOffset.UTC);
    if (value instanceof java.time.ZonedDateTime zoned) return zoned.toOffsetDateTime();
    if (value instanceof java.sql.Timestamp stamp) return stamp.toInstant().atOffset(java.time.ZoneOffset.UTC);
    if (value instanceof LocalDateTime local) return local.atOffset(java.time.ZoneOffset.UTC);
    throw new IllegalArgumentException("TIMESTAMP WITH TIME ZONE として読めない値: " + value.getClass());
  }

  /** `UPPER`。Oracle の識別子は大文字小文字を区別しないので、表名の照合に使う。 */
  public static String upper(Object value) {
    return isNull(value) ? null : text(value).toUpperCase(java.util.Locale.ROOT);
  }

  private static String emptyIsNull(String value) {
    return value.isEmpty() ? null : value;
  }

  public static BigDecimal mod(Object a, Object b) {
    if (isNull(a) || isNull(b)) return null;
    BigDecimal divisor = num(b);
    if (divisor.signum() == 0) return num(a);  // Oracle's MOD by zero returns the value
    return num(a).remainder(divisor);
  }

  public static BigDecimal abs(Object value) {
    return isNull(value) ? null : num(value).abs();
  }

  /** Oracle's {@code IN}: false when the left side is null, since the comparison is unknown. */
  public static boolean in(Object value, Object... candidates) {
    if (isNull(value)) return false;
    for (Object candidate : candidates) {
      if (eq(value, candidate)) return true;
    }
    return false;
  }

  public static boolean between(Object value, Object low, Object high) {
    return ge(value, low) && le(value, high);
  }

  /**
   * Oracle's {@code NOT IN}: true only when the value differs from every candidate. A null on either side makes
   * a comparison unknown, so {@code x NOT IN (1, NULL)} is never true -- which {@code !in(...)} cannot say.
   */
  public static boolean notIn(Object value, Object... candidates) {
    if (isNull(value)) return false;
    for (Object candidate : candidates) {
      if (!ne(value, candidate)) return false;
    }
    return true;
  }

  /** {@code NOT BETWEEN} is {@code value < low OR value > high}; with a null operand neither side is true. */
  public static boolean notBetween(Object value, Object low, Object high) {
    return lt(value, low) || gt(value, high);
  }

  /** {@code NOT LIKE}: unknown, so not true, when either side is null. */
  public static boolean notLike(Object value, String pattern) {
    if (isNull(value) || pattern == null) return false;
    return !like(value, pattern);
  }

  /** A PL/SQL BOOLEAN as a Java condition: NULL is not TRUE. */
  public static boolean isTrue(Boolean value) {
    return Boolean.TRUE.equals(value);
  }

  /** {@code NOT x} on a PL/SQL BOOLEAN is true only when x is FALSE; a NULL x leaves it unknown. */
  public static boolean isFalse(Boolean value) {
    return Boolean.FALSE.equals(value);
  }

  /**
   * A three-valued BOOLEAN from its two halves. The comparisons here answer "is it TRUE", so a condition that
   * is stored rather than branched on is rendered twice -- once as "is TRUE", once as "is FALSE" -- and the
   * value is NULL when neither holds.
   */
  public static Boolean bool3(boolean isTrue, boolean isFalse) {
    return isTrue ? Boolean.TRUE : isFalse ? Boolean.FALSE : null;
  }

  /** Oracle's {@code LIKE}: {@code %} is any run of characters, {@code _} is exactly one. */
  public static boolean like(Object value, String pattern) {
    if (isNull(value) || pattern == null) return false;
    StringBuilder regex = new StringBuilder();
    for (char c : pattern.toCharArray()) {
      if (c == '%') regex.append(".*");
      else if (c == '_') regex.append('.');
      else regex.append(java.util.regex.Pattern.quote(String.valueOf(c)));
    }
    // DOTALL: `%` and `_` match a line break in Oracle; Java's `.` does not unless told to
    return java.util.regex.Pattern.compile(regex.toString(), java.util.regex.Pattern.DOTALL)
        .matcher(text(value)).matches();
  }
  /**
   * `FETCH c BULK COLLECT INTO v LIMIT n` が回していた分割読みの、移行先での形（#14）。
   *
   * <p><b>意味が 1 つ変わっている。</b> Oracle の `n` は「1 回に<b>読み込む</b>件数」で、それが
   * メモリを守っていた。ScalarDB には跨トランザクションの cursor が無く、生成コードは行を先に
   * まとめて読むので、`n` は「1 回に<b>配る</b>件数」でしかない。メモリを守るのは走査行数の上限
   * （`--limits`）のほうである。
   *
   * <p>空の塊は返さない。Oracle のループは「取れなかったら抜ける」形で、その `EXIT` は残して
   * あるが、ここが空を配らないので発火しない——どちらでも結果は同じである。
   *
   * <p>`n` が正でないときは、ここへ来る前に生成コードが Oracle と同じ誤りを投げる（`LIMIT 0` / 負の値は
   * ORA-06502、`LIMIT NULL` は ORA-06500。2026-09-19 に 23ai で実測）。以前ここには「0 件で抜けるのと
   * 同じ結果になる」と書いていたが、**実測せずに書いた誤り**だった。ここで受け取ったら、呼び出しの誤り
   * として投げる。
   */
  public static <T> java.util.List<java.util.List<T>> chunks(java.util.List<T> rows, Object size) {
    int n = size instanceof Number number ? number.intValue() : 0;
    if (n <= 0) {
      throw new IllegalArgumentException("chunks: 塊の件数が正でない（" + size + "）。生成コードが先に止めるはずである");
    }
    java.util.List<java.util.List<T>> out = new java.util.ArrayList<>();
    if (rows == null) return out;
    for (int at = 0; at < rows.size(); at += n) {
      out.add(java.util.List.copyOf(rows.subList(at, Math.min(at + n, rows.size()))));
    }
    return out;
  }
}
