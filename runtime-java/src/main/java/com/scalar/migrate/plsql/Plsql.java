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
    if (numeric(a, b)) return compare(a, b) == 0;   // text against a number converts the text (#113)
    if (a instanceof java.util.List<?> x && b instanceof java.util.List<?> y) return Boolean.TRUE.equals(sameMultiset(x, y));
    // two RAWs are equal byte for byte: HEXTORAW('ab') = HEXTORAW('AB') (#140)
    if (a instanceof byte[] x && b instanceof byte[] y) return java.util.Arrays.equals(x, y);
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

  /**
   * Text against a number compares as numbers, the text converted as TO_NUMBER would (#113). Measured on Oracle 26ai:
   * `v VARCHAR2 := '10'` is `= 10` and `> 9`, ' 10 ', '1e1', '10.0', '+5' all equal their number, and 'abc' or
   * '1,000' is ORA-06502 (character to number conversion error). Here `eq("10", 10)` was false and `gt("10", 9)` a
   * ClassCastException. Two texts still compare as text: `'9' < '10'` is false.
   */
  @SuppressWarnings({"unchecked", "rawtypes"})
  private static int compare(Object a, Object b) {
    if (numeric(a, b)) {
      return num(a).compareTo(num(b));
    }
    if (a instanceof byte[] x && b instanceof byte[] y) return java.util.Arrays.compareUnsigned(x, y);
    return ((Comparable) a).compareTo(b);
  }

  private static boolean numeric(Object a, Object b) {
    return (a instanceof Number || a instanceof CharSequence) && (b instanceof Number || b instanceof CharSequence)
        && (a instanceof Number || b instanceof Number);
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
    // a Float is only ever a BINARY_FLOAT / SIMPLE_FLOAT: Oracle writes those as 4.0E+000, not as a NUMBER (#91)
    if (value instanceof Float f) return binaryText(f.doubleValue(), 9);
    // a REAL / FLOAT / DOUBLE PRECISION local, or a NUMBER column a driver handed back as a Double: NUMBER's rule,
    // not Java's "0.5". A BINARY_DOUBLE is also a Double, so the generator marks it and calls binaryDouble instead
    if (value instanceof Double d) {
      if (!Double.isNaN(d) && !Double.isInfinite(d)) return text(BigDecimal.valueOf(d));
      return binaryText(d, 17);
    }
    // what Oracle writes with the default NLS settings (AMERICAN): Java's toString gave 2026-09-26T01:00 for a DATE
    // Oracle writes as 26-SEP-26 (#94). A DATE and a TIMESTAMP are both a LocalDateTime; the generator marks a
    // TIMESTAMP local and calls timestampText, so a bare LocalDateTime is a DATE -- as `t + 1` is in Oracle
    if (value instanceof Boolean b) return b ? "TRUE" : "FALSE";
    if (value instanceof LocalDateTime d) return dateText(d);
    if (value instanceof java.time.LocalDate d) return dateText(d.atStartOfDay());
    if (value instanceof java.time.OffsetDateTime d) return timestampText(d, 6);
    // a RAW is written as its hex digits, upper case: `v VARCHAR2(32) := SYS_GUID()`, `'x' || r` (#140)
    if (value instanceof byte[] raw) return hex(raw);
    return String.valueOf(value);
  }

  private static final String[] MONTHS =
      {"JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"};

  /** A DATE as NLS_DATE_FORMAT DD-MON-RR writes it: 05-JAN-99. The time of day is not written. */
  static String dateText(LocalDateTime d) {
    return String.format("%02d-%s-%02d", d.getDayOfMonth(), MONTHS[d.getMonthValue() - 1], Math.floorMod(d.getYear(), 100));
  }

  /**
   * TO_CHAR of a TIMESTAMP(precision) or TIMESTAMP WITH TIME ZONE, or `'...' || t` with one, as NLS_TIMESTAMP_FORMAT
   * DD-MON-RR HH.MI.SSXFF AM (and _TZ_FORMAT's TZR) write it: 26-SEP-26 09.30.00.500000 AM +09:00. FF is as many
   * digits as the declared precision, and TIMESTAMP(0) has no fraction at all (#94).
   */
  public static String timestampText(Object value, int precision) {
    if (isNull(value)) return "";
    LocalDateTime t;
    String zone = "";
    if (value instanceof java.time.OffsetDateTime o) {
      t = o.toLocalDateTime();
      int seconds = o.getOffset().getTotalSeconds();
      zone = String.format(" %s%02d:%02d", seconds < 0 ? "-" : "+", Math.abs(seconds) / 3600, Math.abs(seconds) / 60 % 60);
    } else if (value instanceof LocalDateTime l) {
      t = l;
    } else {
      return text(value);
    }
    int hour = t.getHour() % 12 == 0 ? 12 : t.getHour() % 12;
    String fraction = precision > 0 ? "." + String.format("%09d", t.getNano()).substring(0, Math.min(precision, 9)) : "";
    return dateText(t) + String.format(" %02d.%02d.%02d", hour, t.getMinute(), t.getSecond()) + fraction
        + (t.getHour() < 12 ? " AM" : " PM") + zone;
  }

  /** TO_CHAR of a BINARY_DOUBLE / SIMPLE_DOUBLE, or `'...' || d` with one: 17 significant digits (#91). */
  public static String binaryDouble(Object value) {
    if (value == null) return "";
    if (value instanceof Number n && !(value instanceof BigDecimal)) return binaryText(n.doubleValue(), 17);
    return text(value);
  }

  /**
   * Oracle's text for a binary floating-point value, as Oracle 26ai writes it: the exact binary value rounded to 9
   * (BINARY_FLOAT) or 17 (BINARY_DOUBLE) significant digits, trailing zeros dropped but one kept after the point,
   * and a signed three-digit exponent -- 4.0E+000, 1.00000001E-001, 3.3333333333333335E+000. Zero is "0", and
   * NaN and the infinities are "Nan", "Inf", "-Inf".
   */
  static String binaryText(double value, int digits) {
    if (Double.isNaN(value)) return "Nan";
    if (Double.isInfinite(value)) return value > 0 ? "Inf" : "-Inf";
    if (value == 0) return "0";
    BigDecimal rounded = new BigDecimal(value).round(new java.math.MathContext(digits, java.math.RoundingMode.HALF_EVEN))
        .stripTrailingZeros();
    String unscaled = rounded.unscaledValue().abs().toString();
    int exponent = rounded.precision() - rounded.scale() - 1;
    String fraction = unscaled.length() > 1 ? unscaled.substring(1) : "0";
    return (rounded.signum() < 0 ? "-" : "") + unscaled.charAt(0) + "." + fraction
        + "E" + (exponent < 0 ? "-" : "+") + String.format("%03d", Math.abs(exponent));
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

  /** TRUNC on a number, or on a value whose type is only known at run time (a date or a number): #75, 4-23. */
  public static Object trunc(Object value) {
    if (isNull(value)) return null;
    if (isTemporal(value)) return trunc(castDate(value));
    return num(value).setScale(0, java.math.RoundingMode.DOWN);
  }

  /**
   * `TRUNC(d, 'MM')` on a DATE or TIMESTAMP: a DATE at the start of that unit. The overloads take the static type,
   * so `TRUNC(SYSDATE, 'MM')` no longer reaches the numeric TRUNC below and fails as VALUE_ERROR (#112).
   */
  public static LocalDateTime trunc(LocalDateTime value, Object format) {
    return isNull(value) || isNull(format) ? null : dateUnit(castDate(value), text(format), false);
  }

  public static LocalDateTime trunc(java.time.OffsetDateTime value, Object format) {
    return isNull(value) || isNull(format) ? null : dateUnit(castDate(value), text(format), false);
  }

  /** `ROUND(d)` on a DATE: the nearest midnight, noon going up (#112). */
  public static LocalDateTime round(LocalDateTime value) {
    return value == null ? null : dateUnit(castDate(value), "DD", true);
  }

  /** `ROUND(d, 'MM')` on a DATE or TIMESTAMP (#112). */
  public static LocalDateTime round(LocalDateTime value, Object format) {
    return isNull(value) || isNull(format) ? null : dateUnit(castDate(value), text(format), true);
  }

  public static LocalDateTime round(java.time.OffsetDateTime value, Object format) {
    return isNull(value) || isNull(format) ? null : dateUnit(castDate(value), text(format), true);
  }

  /**
   * The start of the unit a date format names (TRUNC), or the nearer of that start and the next one (ROUND). The
   * units and the half-way points, measured on Oracle 26ai with 2026-09-27 13:45:10 (a Sunday):
   *
   * <pre>
   *   CC SCC                    2001-01-01; ROUND goes up from year 51 of the century
   *   YYYY YEAR YYY YY Y ...    2026-01-01; up from July
   *   IYYY IY I                 2025-12-29, the Monday of ISO week 1
   *   Q                         2026-07-01; up from the 16th of the quarter's second month
   *   MM MON MONTH RM           2026-09-01; up from the 16th
   *   WW                        2026-09-24, the weekday of 1 January; IW 2026-09-21 (Monday); W 2026-09-22, the
   *                             weekday of the 1st; D DY DAY 2026-09-27 (Sunday). ROUND: up from 3.5 days in
   *   DD DDD J                  2026-09-27; up from noon
   *   HH HH12 HH24              13:00; up from :30.   MI 13:45; up from :30 seconds
   * </pre>
   *
   * Any other format is ORA-01821 (date format not recognized) in Oracle, and so it is here.
   */
  static LocalDateTime dateUnit(LocalDateTime d, String format, boolean round) {
    String unit = format.toUpperCase(java.util.Locale.ROOT);
    java.time.LocalDate day = d.toLocalDate();
    LocalDateTime start;
    LocalDateTime next;
    switch (unit) {
      case "CC", "SCC" -> {
        int first = (d.getYear() - 1) / 100 * 100 + 1;
        start = LocalDateTime.of(first, 1, 1, 0, 0);
        next = start.plusYears(100);
        if (round) return d.getYear() - first >= 50 ? next : start;
        return start;
      }
      case "SYYYY", "YYYY", "YEAR", "SYEAR", "YYY", "YY", "Y" -> {
        start = day.withDayOfYear(1).atStartOfDay();
        next = start.plusYears(1);
        if (round) return d.getMonthValue() >= 7 ? next : start;
        return start;
      }
      case "IYYY", "IY", "I" -> {
        start = isoYearStart(day.get(java.time.temporal.IsoFields.WEEK_BASED_YEAR));
        next = isoYearStart(day.get(java.time.temporal.IsoFields.WEEK_BASED_YEAR) + 1);
      }
      case "Q" -> {
        int firstMonth = (d.getMonthValue() - 1) / 3 * 3 + 1;
        start = LocalDateTime.of(d.getYear(), firstMonth, 1, 0, 0);
        next = start.plusMonths(3);
        if (round) return !d.isBefore(start.plusMonths(1).withDayOfMonth(16)) ? next : start;
        return start;
      }
      case "MONTH", "MON", "MM", "RM" -> {
        start = day.withDayOfMonth(1).atStartOfDay();
        next = start.plusMonths(1);
        if (round) return d.getDayOfMonth() >= 16 ? next : start;
        return start;
      }
      case "WW" -> {
        start = weekFrom(day, day.withDayOfYear(1).getDayOfWeek());
        next = start.plusDays(7);
      }
      case "IW" -> {
        start = weekFrom(day, java.time.DayOfWeek.MONDAY);
        next = start.plusDays(7);
      }
      case "W" -> {
        start = weekFrom(day, day.withDayOfMonth(1).getDayOfWeek());
        next = start.plusDays(7);
      }
      case "DAY", "DY", "D" -> {
        // the first day of the week is the territory's: AMERICA's Sunday, which the comparison sessions use
        start = weekFrom(day, java.time.DayOfWeek.SUNDAY);
        next = start.plusDays(7);
      }
      case "DDD", "DD", "J" -> {
        start = day.atStartOfDay();
        next = start.plusDays(1);
      }
      case "HH", "HH12", "HH24" -> {
        start = d.truncatedTo(java.time.temporal.ChronoUnit.HOURS);
        next = start.plusHours(1);
      }
      case "MI" -> {
        start = d.truncatedTo(java.time.temporal.ChronoUnit.MINUTES);
        next = start.plusMinutes(1);
      }
      default -> throw new IllegalArgumentException("ORA-01821: date format not recognized: " + format);
    }
    if (!round) return start;
    // half-way or later goes to the next unit: noon for a day, 3.5 days for a week, 30 seconds for a minute
    long toNext = java.time.Duration.between(d, next).getSeconds();
    long fromStart = java.time.Duration.between(start, d).getSeconds();
    return fromStart >= toNext ? next : start;
  }

  private static LocalDateTime weekFrom(java.time.LocalDate day, java.time.DayOfWeek first) {
    return day.with(java.time.temporal.TemporalAdjusters.previousOrSame(first)).atStartOfDay();
  }

  private static LocalDateTime isoYearStart(int isoYear) {
    return java.time.LocalDate.of(isoYear, 1, 4).with(java.time.temporal.TemporalAdjusters.previousOrSame(
        java.time.DayOfWeek.MONDAY)).atStartOfDay();
  }

  /** TRUNC(n, places): toward zero at that many decimals (a negative count truncates left of the point). */
  public static BigDecimal trunc(Object value, Object places) {
    if (isNull(value) || isNull(places)) return null;
    BigDecimal cut = num(value).setScale(num(places).intValue(), java.math.RoundingMode.DOWN);
    return cut.scale() < 0 ? cut.setScale(0) : cut;
  }

  /** A value going into a BINARY_DOUBLE / REAL / FLOAT local (a Double): #75, 2-13 `radius REAL := 1`. */
  public static Double toDouble(Object value) {
    return isNull(value) ? null : (value instanceof Double d ? d : num(value).doubleValue());
  }

  /** A value going into a BINARY_FLOAT local (a Float): #75, 8-14. */
  public static Float toFloat(Object value) {
    return isNull(value) ? null : (value instanceof Float f ? f : num(value).floatValue());
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
  /**
   * A value read back from a column into a DATE / TIMESTAMP local. A direct read hands over a LocalDateTime; a plan
   * runs the rest in H2 and hands text back ("2003-06-17 00:00:00"), which a cast turned into a ClassCastException
   * (oracle-plsql-docs 6-6, a cursor read through a plan).
   */
  public static LocalDateTime moment(Object value) {
    if (isNull(value)) return null;
    if (value instanceof LocalDateTime moment) return moment;
    if (value instanceof java.sql.Timestamp moment) return moment.toLocalDateTime();
    if (value instanceof java.time.OffsetDateTime moment) return moment.toLocalDateTime();
    if (value instanceof java.time.Instant moment) return LocalDateTime.ofInstant(moment, java.time.ZoneOffset.UTC);
    if (value instanceof java.time.LocalDate day) return day.atStartOfDay();
    if (value instanceof java.sql.Date day) return day.toLocalDate().atStartOfDay();
    String text = value.toString().trim().replace(' ', 'T');
    return text.length() == 10 ? java.time.LocalDate.parse(text).atStartOfDay() : LocalDateTime.parse(text);
  }

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

  /**
   * Every result is rounded as a NUMBER holds it: 40 significant digits, or 39 when the leading base-100 pair holds
   * one digit (OracleNumbers.round40). Only the division was; `a := 1/3; 'x' || (a * a)` printed 80 digits where
   * Oracle 26ai prints .1111111111111111111111111111111111111111, and `1e30 + 1e-30` kept the 1e-30 Oracle drops
   * (#113). Rounding once per operation is what Oracle does: `a := 1/3; a * 3` is .9999999999999999999999999999999999999999.
   */
  private static BigDecimal arith(Object a, Object b,
      java.util.function.BinaryOperator<BigDecimal> operator) {
    if (a == null || b == null) return null;
    return OracleNumbers.round40(operator.apply(num(a), num(b)));
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
      // Oracle 26ai's text: `n NUMBER := 'abc'` is "ORA-06502: PL/SQL: value or conversion error: character to
      // number conversion error". The older "numeric or value error" is what SQLERRM showed here (#114)
      super("ORA-06502: PL/SQL: value or conversion error: " + detail);
    }

    /** The bare ORA-06502 an argument out of range raises, with no detail after it (`POWER(0, -1)`, #113). */
    public ValueError() {
      super("ORA-06502: PL/SQL: value or conversion error");
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

  /**
   * ORA-12899: a value longer than its VARCHAR2 / CHAR column. Oracle names the column as "SCHEMA"."TABLE"."COLUMN";
   * the schema is not known here, so the name is "TABLE"."COLUMN". It has no predefined name, so a WHEN OTHERS sees
   * it as the base class with this number (service._HELPER_ERRORS, #115).
   */
  public static final class ValueTooLarge extends OracleError {
    public ValueTooLarge(String column, long actual, int maximum) {
      super(-12899, "ORA-12899: value too large for column " + column + " (actual: " + actual + ", maximum: " + maximum + ")");
    }
  }

  /**
   * ORA-01438: a value with more digits left of the point than its NUMBER(p,s) column allows. The text is the one
   * Oracle 26ai gives for `CAST(123 AS NUMBER(2))`: "value 123 greater than specified precision (2, 0) for column",
   * with the column's name after it (#115).
   */
  public static final class PrecisionTooLarge extends OracleError {
    public PrecisionTooLarge(String value, int precision, int scale, String column) {
      super(-1438, "ORA-01438: value " + value + " greater than specified precision (" + precision + ", " + scale
          + ") for column " + column);
    }
  }

  /**
   * A value written into a VARCHAR2(size) / CHAR(size) column (#115): ORA-12899 when it is longer, counted in
   * characters for a CHAR-semantics column ({@code chars}) and in UTF-8 bytes (AL32UTF8) otherwise. The value comes
   * back as it was given, for {@link #bind} to convert. ScalarDB's TEXT has no length, so this is the only place
   * the declaration is kept.
   */
  public static Object columnText(Object value, int size, boolean chars, String column) {
    if (isNull(value)) return value;
    String text = text(value);
    long length = chars ? text.codePointCount(0, text.length())
        : text.getBytes(java.nio.charset.StandardCharsets.UTF_8).length;
    if (length > size) throw new ValueTooLarge(column, length, size);
    return value;
  }

  /**
   * A value written into a NUMBER(precision, scale) column (#115): rounded half-up to the scale, as the column
   * stores it, and ORA-01438 when that needs more than precision - scale digits left of the point. Measured with
   * CAST on Oracle 26ai: 12.345 into (4,2) is 12.35, 99.995 and -99.995 are ORA-01438, 123.5 into (3) is 124,
   * 0.0123 into (2,3) is 0.012 and 0.123 is ORA-01438. The value comes back as it was given: {@link #bind} rounds
   * it for the column's storage. Text that is not a number is left to the bind, as before.
   */
  public static Object columnNumber(Object value, int precision, int scale, String column) {
    if (isNull(value) || !(value instanceof Number || value instanceof CharSequence)) return value;
    BigDecimal number;
    try {
      number = OracleNumbers.toBigDecimal(value);
    } catch (NumberFormatException notANumber) {
      return value;
    }
    BigDecimal rounded = number.setScale(scale, java.math.RoundingMode.HALF_UP);
    if (rounded.signum() != 0 && rounded.precision() - rounded.scale() > precision - scale) {
      throw new PrecisionTooLarge(text(number), precision, scale, column);
    }
    return value;
  }

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

  /**
   * `TRIM(x)`: both ends, the blank (U+0020) only; an empty result is NULL as in Oracle. Java's strip() took every
   * Unicode space as well: `TRIM(CHR(10)||'a'||CHR(9))` is `\na\t` on Oracle 26ai and `TRIM('　a　')` keeps its
   * full-width spaces, where this gave `a` for both (#112).
   */
  public static String trim(Object value) {
    return isNull(value) ? null : emptyIsNull(trimmed(text(value), " ", true, true));
  }

  /** The characters of {@code set} removed from the start and/or the end of {@code text}, by code point. */
  private static String trimmed(String text, String set, boolean leading, boolean trailing) {
    int[] points = text.codePoints().toArray();
    java.util.Set<Integer> remove = new java.util.HashSet<>();
    set.codePoints().forEach(remove::add);
    int start = 0;
    int end = points.length;
    while (leading && start < end && remove.contains(points[start])) start++;
    while (trailing && end > start && remove.contains(points[end - 1])) end--;
    return new String(points, start, end - start);
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

  /** A bound of `FOR i IN low .. high`: a PLS_INTEGER, and NULL is ORA-06502 (measured on 26ai, #99). */
  public static int loopBound(Object value) {
    if (isNull(value)) throw new ValueError("FOR loop bound is NULL");
    return toInt(value);
  }

  /** A NUMBER(10..18) value: rounded half away from zero as Oracle assigns it, not refused (#100). */
  public static Long toLong(Object value) {
    if (isNull(value)) return null;
    BigDecimal rounded = num(value).setScale(0, java.math.RoundingMode.HALF_UP);
    try {
      return rounded.longValueExact();
    } catch (ArithmeticException tooLarge) {
      throw new ValueError("number precision too large");
    }
  }

  /** `SQLERRM` inside a handler: Oracle's text is `ORA-nnnnn: message` (the code is negative in PL/SQL). */
  /** `SQLERRM(n)`: the message Oracle gives for an error number, outside any handler (#76, 11-13). Texts measured on 26ai. */
  public static String sqlerrmOf(Object code) {
    if (isNull(code)) return null;
    int n = num(code).intValue();
    if (n == 0) return "ORA-0000: normal, successful completion";
    if (n == 100 || n == -1403) return "ORA-01403: no data found";
    if (n > 0) return "User-Defined Exception";
    if (n <= -20000 && n >= -20999) return String.format("ORA-%05d: ", -n);
    String text = switch (n) {
      case -1 -> "unique constraint (.) violated on table . columns ()";
      case -1001 -> "cursor number is invalid or does not exist";
      case -1422 -> "exact fetch returned more than the requested number of rows ";
      case -1476 -> "divisor is equal to zero";
      case -1722 -> "unable to convert string value containing  to a number: ";
      case -1428 -> "Argument  is out of range.";
      case -1438 -> "value  greater than specified precision  for column ";
      case -6502 -> "PL/SQL: value or conversion error";
      case -6510 -> "PL/SQL: unhandled user-defined exception";
      case -6511 -> "PL/SQL: cursor already open";
      case -6530 -> "Reference to uninitialized composite";
      case -6531 -> "Reference to uninitialized collection";
      case -6532 -> "subscript outside of limit";
      case -6533 -> "Subscript beyond count";
      case -6592 -> "CASE not found while executing CASE statement";
      case -12899 -> "value too large for column  (actual: , maximum: )";
      default -> "Message " + (-n) + " not found";
    };
    return String.format("ORA-%05d: %s", -n, text);
  }

  // the numbers of Oracle's predefined exceptions (plsql/gen_java/exception.py PREDEFINED)
  private static final java.util.Set<Integer> PREDEFINED_CODES =
      java.util.Set.of(-1, -1001, -1422, -1476, -1722, -6502, -6511, -6531, -6532, -6533, -6592);

  /**
   * The numbers the generator gives a PL/SQL-declared exception that has no EXCEPTION_INIT (plsql/gen_java/exception.py
   * `_user_code`): -900000 .. -900999. They keep the registry's classes apart and never reach the PL/SQL: Oracle's
   * SQLCODE for such an exception is 1 (#114).
   */
  static final int USER_DEFINED_HIGH = -900000;
  static final int USER_DEFINED_LOW = -900999;

  static boolean userDefined(int code) {
    return code <= USER_DEFINED_HIGH && code >= USER_DEFINED_LOW;
  }

  /**
   * `SQLCODE` inside a handler: the handled exception's number, and +1 for a PL/SQL-declared one without
   * EXCEPTION_INIT, as Oracle 26ai returns (`RAISE e_x` ... `WHEN OTHERS THEN SQLCODE` is 1). The class keeps its
   * own number, which is what tells two declared exceptions apart in the registry (#114).
   */
  public static int sqlcode(int code) {
    return userDefined(code) ? 1 : code;
  }

  /** `SQLERRM(n)` inside a handler: the handled error's own message when n is its number (11-13). */
  public static String sqlerrmOf(Object code, int current, String message) {
    // SQLERRM(SQLCODE) of a declared exception asks with +1, the number the handler sees (#114)
    if (!isNull(code) && num(code).intValue() == sqlcode(current)) return sqlerrm(current, message);
    return sqlerrmOf(code);
  }

  public static String sqlerrm(int code, String message) {
    if (code == 0) return "ORA-0000: normal, successful completion";
    // a PL/SQL-declared exception with no EXCEPTION_INIT: Oracle's SQLERRM is this text, whatever its class's own
    // number is (`e_x EXCEPTION; RAISE e_x;` shows "1 User-Defined Exception" on 26ai; it showed "ORA-900875: e_x")
    if (userDefined(code) || code > 0 && code != 100) return "User-Defined Exception";
    if (code == 100) return "ORA-01403: no data found";
    String prefix = String.format("ORA-%05d: ", Math.abs(code));
    // the runtime's own errors (Plsql.ZeroDivide, Plsql.NoDataFound, ...) already carry Oracle's whole text
    if (message != null && message.startsWith(prefix)) return message;
    // an error Oracle names has Oracle's text whatever the Java message says: `RAISE VALUE_ERROR` is "ORA-06502:
    // PL/SQL: value or conversion error" on 26ai, not "ORA-06502: VALUE_ERROR", and a SELECT INTO's second row
    // "ORA-01422: exact fetch returned more than the requested number of rows " (#114)
    if (PREDEFINED_CODES.contains(code)) return sqlerrmOf(code);
    return prefix + (message == null ? "" : message);
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
    // `nt_of_nt(n1, n2)` holds copies of n1 and n2, as every PL/SQL assignment does (#111)
    java.util.List<E> out = new java.util.ArrayList<>(elements.length);
    for (E element : elements) out.add(copy(element));
    return out;
  }

  /**
   * A PL/SQL value copy. {@code n2 := n1} copies a collection in PL/SQL, and so does storing it into a record or
   * another collection, and passing it IN OUT without NOCOPY; a Java List or Map is shared, so a change through
   * one name showed through the other (#111). Collections are copied deeply -- a collection of collections, a
   * record holding one. Everything else the generated code holds (BigDecimal, String, the java.time types, a
   * record of those) cannot change, and is returned as it is.
   */
  @SuppressWarnings("unchecked")
  public static <T> T copy(T value) {
    if (value instanceof java.util.List<?> list) {
      java.util.List<Object> out = new java.util.ArrayList<>(list.size());
      for (Object element : list) out.add(element == GAP ? GAP : copy(element));
      return (T) out;
    }
    if (value instanceof java.util.SortedMap<?, ?> map) {
      java.util.TreeMap<Object, Object> out = new java.util.TreeMap<>((java.util.Comparator<Object>) map.comparator());
      for (java.util.Map.Entry<?, ?> entry : map.entrySet()) out.put(entry.getKey(), copy(entry.getValue()));
      return (T) out;
    }
    if (value instanceof java.util.Map<?, ?> map) {
      java.util.Map<Object, Object> out = new java.util.LinkedHashMap<>();
      for (java.util.Map.Entry<?, ?> entry : map.entrySet()) out.put(entry.getKey(), copy(entry.getValue()));
      return (T) out;
    }
    if (value instanceof Record record) {
      return (T) copyRecord(record);
    }
    return value;
  }

  /** A record with its collection components copied; the record itself when it holds none (it is immutable). */
  private static Record copyRecord(Record record) {
    java.lang.reflect.RecordComponent[] components = record.getClass().getRecordComponents();
    Object[] values = new Object[components.length];
    Class<?>[] types = new Class<?>[components.length];
    boolean changed = false;
    try {
      for (int i = 0; i < components.length; i++) {
        java.lang.reflect.Method accessor = components[i].getAccessor();
        accessor.setAccessible(true);
        Object value = accessor.invoke(record);
        values[i] = copy(value);
        types[i] = components[i].getType();
        changed |= values[i] != value;
      }
      if (!changed) return record;
      java.lang.reflect.Constructor<?> constructor = record.getClass().getDeclaredConstructor(types);
      constructor.setAccessible(true);
      return (Record) constructor.newInstance(values);
    } catch (ReflectiveOperationException e) {
      throw new IllegalStateException("cannot copy " + record.getClass().getName(), e);
    }
  }

  public static <K, E> java.util.Map<K, E> indexBy() {
    return new java.util.TreeMap<>();
  }

  private static RuntimeException collectionIsNull() {
    return new CollectionIsNull();
  }

  /**
   * A predefined Oracle error the runtime raises itself, with the SQLCODE a handler reads. Each is its own type so
   * that the generated code can turn it into the migrated exception a PL/SQL handler names (service._guarded).
   * They used to be IllegalStateException and IndexOutOfBoundsException, which neither `WHEN NO_DATA_FOUND` nor
   * `WHEN OTHERS` caught: `BEGIN x := cache(k); EXCEPTION WHEN NO_DATA_FOUND THEN ...` escaped the routine (#99).
   */
  public abstract static class OracleError extends RuntimeException {
    private final int code;

    protected OracleError(int code, String message) {
      super(message);
      this.code = code;
    }

    public int code() {
      return code;
    }
  }

  // The messages are Oracle's own (26ai), because SQLERRM reads them.

  /** NO_DATA_FOUND: SQLCODE +100, ORA-01403 to a client. */
  public static final class NoDataFound extends OracleError {
    public NoDataFound() {
      super(100, "ORA-01403: no data found");
    }
  }

  public static final class SubscriptBeyondCount extends OracleError {
    public SubscriptBeyondCount() {
      super(-6533, "ORA-06533: Subscript beyond count");
    }
  }

  public static final class SubscriptOutsideLimit extends OracleError {
    public SubscriptOutsideLimit() {
      super(-6532, "ORA-06532: subscript outside of limit");
    }
  }

  public static final class CollectionIsNull extends OracleError {
    public CollectionIsNull() {
      super(-6531, "ORA-06531: Reference to uninitialized collection");
    }
  }

  public static final class InvalidCursor extends OracleError {
    public InvalidCursor() {
      super(-1001, "ORA-01001: cursor number is invalid or does not exist");
    }
  }

  public static final class CursorAlreadyOpen extends OracleError {
    public CursorAlreadyOpen() {
      super(-6511, "ORA-06511: PL/SQL: cursor already open");
    }
  }

  /** A CASE statement with no ELSE whose WHEN all failed. The generated final `else` throws it. */
  public static final class CaseNotFound extends OracleError {
    public CaseNotFound() {
      super(-6592, "ORA-06592: CASE not found while executing CASE statement");
    }
  }

  /** ORA-06503: a function reached its end without RETURN. It has no predefined name, only WHEN OTHERS sees it. */
  public static final class NoReturn extends OracleError {
    public NoReturn() {
      super(-6503, "ORA-06503: PL/SQL: Function returned without value");
    }
  }

  /** ORA-22160: FORALL reached an index its collection has no element at. It has no predefined name (#136). */
  public static final class ElementNotExist extends OracleError {
    public ElementNotExist(Object index) {
      super(-22160, "ORA-22160: element at index [" + (index == null ? "null" : index) + "] does not exist");
    }
  }

  // ---- FORALL (#136). Measured on 26ai: the bounds are evaluated once; a NULL bound, lo > hi, or a NULL bound of
  // INDICES OF ... BETWEEN runs nothing and leaves SQL%ROWCOUNT / SQL%BULK_ROWCOUNT as they were, while INDICES OF /
  // VALUES OF over an empty collection runs nothing and sets SQL%ROWCOUNT to 0. Each element's statement is checked
  // for its element first: a missing one is ORA-22160, and the statements before it stay done.

  /** {@code FORALL i IN lo .. hi}: the indexes, or null when the statement does not run at all. */
  public static java.util.List<Integer> forallRange(Object lo, Object hi) {
    if (isNull(lo) || isNull(hi)) return null;
    int from = toInt(lo), to = toInt(hi);
    if (from > to) return null;
    java.util.List<Integer> out = new java.util.ArrayList<>(to - from + 1);
    for (int i = from; i <= to; i++) out.add(i);
    return out;
  }

  /** {@code FORALL i IN INDICES OF v}: the indexes v has an element at, in order; gaps are skipped. */
  public static java.util.List<Integer> indicesOf(Object collection) {
    if (collection == null) throw collectionIsNull();
    java.util.List<Integer> out = new java.util.ArrayList<>();
    if (collection instanceof java.util.List<?> list) {
      for (int i = 1; i <= list.size(); i++) if (list.get(i - 1) != GAP) out.add(i);
      return out;
    }
    for (Object k : ((java.util.Map<?, ?>) collection).keySet()) out.add(num(k).intValueExact());
    return out;
  }

  /** {@code FORALL i IN INDICES OF v BETWEEN lo AND hi}: those of them from lo to hi; null (nothing runs) for a NULL bound. */
  public static java.util.List<Integer> indicesOf(Object collection, Object lo, Object hi) {
    java.util.List<Integer> all = indicesOf(collection);
    if (isNull(lo) || isNull(hi)) return null;
    int from = toInt(lo), to = toInt(hi);
    java.util.List<Integer> out = new java.util.ArrayList<>();
    for (Integer i : all) if (i >= from && i <= to) out.add(i);
    return out;
  }

  /**
   * {@code FORALL i IN VALUES OF p}: p's element values, in p's order -- a value twice runs the statement twice, and
   * SQL%BULK_ROWCOUNT keeps one count per index. A NULL value is ORA-22160 when its statement is reached.
   */
  public static java.util.List<Integer> valuesOf(Object indexes) {
    if (indexes == null) throw collectionIsNull();
    java.util.Collection<?> values = indexes instanceof java.util.List<?> list ? list
        : ((java.util.Map<?, ?>) indexes).values();
    java.util.List<Integer> out = new java.util.ArrayList<>();
    for (Object value : values) {
      if (value == GAP) continue;
      out.add(isNull(value) ? null : toInt(value));
    }
    return out;
  }

  /** Before one element's statement: ORA-22160 when the collection it reads has no element at {@code at}. */
  public static void forallElement(Object collection, Object at) {
    if (collection == null) throw collectionIsNull();
    if (at == null || !exists(collection, at)) throw new ElementNotExist(at);
  }

  /**
   * {@code SQL%BULK_ROWCOUNT(i)}: ORA-06532 for an index the last FORALL did not run (measured). A NULL index is
   * ORA-06530 in Oracle; it is VALUE_ERROR here, as a NULL key of any collection is.
   */
  public static Object bulkRowCount(java.util.Map<Integer, BigDecimal> counts, Object at) {
    if (isNull(at)) throw new ValueError("NULL index for SQL%BULK_ROWCOUNT");
    Integer k = toInt(at);
    if (!counts.containsKey(k)) throw new SubscriptOutsideLimit();
    return counts.get(k);
  }

  // ---- A query over TABLE(v) of a PL/SQL collection, run here rather than by SQL (#135). Measured on 26ai: the rows
  // are the elements in index order with a nested table's gaps skipped, TABLE(NULL) has none, and ORDER BY puts NULLs
  // last ascending and first descending, strings by their binary value.

  /** The rows of {@code TABLE(v)}: its elements, in index order. */
  public static java.util.List<Object> tableRows(Object collection) {
    java.util.List<Object> out = new java.util.ArrayList<>();
    if (collection == null) return out;
    java.util.Collection<?> elements = collection instanceof java.util.List<?> list ? list
        : ((java.util.Map<?, ?>) collection).values();
    for (Object element : elements) if (element != GAP) out.add(element);
    return out;
  }

  /**
   * ORDER BY over rows whose columns from {@code width} on are the sort keys the query added: sorted by
   * {@code keys} (column positions), each descending or not and with its NULLs first or not, then cut back to the
   * {@code width} columns the query selects.
   */
  public static void orderRows(java.util.List<Object[]> rows, int width, int[] keys, boolean[] descending,
                               boolean[] nullsFirst) {
    rows.sort((a, b) -> {
      for (int k = 0; k < keys.length; k++) {
        Object x = a[keys[k]], y = b[keys[k]];
        boolean xNull = isNull(x), yNull = isNull(y);
        int order;
        if (xNull || yNull) {
          if (xNull && yNull) continue;
          order = xNull == nullsFirst[k] ? -1 : 1;   // NULL placement does not flip with DESC
        } else {
          order = compare(x, y);
          if (descending[k]) order = -order;
        }
        if (order != 0) return order;
      }
      return 0;
    });
    for (int i = 0; i < rows.size(); i++) {
      if (rows.get(i).length > width) rows.set(i, java.util.Arrays.copyOf(rows.get(i), width));
    }
  }

  /** {@code FETCH FIRST n ROWS ONLY}: the first n. */
  public static java.util.List<Object[]> firstRows(java.util.List<Object[]> rows, int n) {
    return rows.size() > n ? new java.util.ArrayList<>(rows.subList(0, Math.max(n, 0))) : rows;
  }

  /** {@code SELECT COUNT(*)}: one row, the number of rows. */
  public static java.util.List<Object[]> countRows(java.util.List<Object[]> rows) {
    java.util.List<Object[]> out = new java.util.ArrayList<>();
    out.add(new Object[] {BigDecimal.valueOf(rows.size())});
    return out;
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
    if (isNull(at)) throw new ValueError("NULL index table key value");
    if (collection instanceof java.util.List<?> list) {
      int i = num(at).intValueExact();
      if (i < 1) throw new SubscriptOutsideLimit();
      if (i > list.size()) throw new SubscriptBeyondCount();
      Object element = list.get(i - 1);
      if (element == GAP) throw new NoDataFound();
      return element;
    }
    java.util.Map<Object, ?> map = (java.util.Map<Object, ?>) collection;
    Object k = key(collection, at);
    if (!map.containsKey(k)) throw new NoDataFound();
    return map.get(k);
  }

  @SuppressWarnings("unchecked")
  public static void set(Object collection, Object at, Object value) {
    if (collection == null) throw collectionIsNull();
    if (isNull(at)) throw new ValueError("NULL index table key value");
    if (collection instanceof java.util.List<?> raw) {
      java.util.List<Object> list = (java.util.List<Object>) raw;
      int i = num(at).intValueExact();
      if (i < 1) throw new SubscriptOutsideLimit();
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

  /** `v.EXTEND(n, i)`: n copies of element i at the end (#78, samples/oracle-plsql-docs 5-20). */
  @SuppressWarnings("unchecked")
  public static void extend(Object collection, Object n, Object i) {
    if (collection == null) throw collectionIsNull();
    if (!(collection instanceof java.util.List<?>)) throw new IllegalStateException("EXTEND on an associative array");
    java.util.List<Object> list = (java.util.List<Object>) collection;
    int from = num(i).intValueExact();
    if (from < 1 || from > list.size() || list.get(from - 1) == GAP) {
      throw new SubscriptBeyondCount();
    }
    Object element = list.get(from - 1);
    // each new element is a copy of its own: a collection of collections must not share one inner List (#111)
    for (int k = num(n).intValueExact(); k > 0; k--) list.add(copy(element));
  }

  /**
   * `v.DELETE(m, n)`: every element whose index is from m to n. Nothing when m is after n, and a missing one is
   * skipped, as for DELETE(i). A nested table keeps the gaps; an associative array loses the keys (#78, 5-17, 5-18).
   */
  @SuppressWarnings("unchecked")
  public static void delete(Object collection, Object from, Object to) {
    if (collection == null) throw collectionIsNull();
    if (isNull(from) || isNull(to)) return;
    if (collection instanceof java.util.List<?> list) {
      int low = Math.max(num(from).intValueExact(), 1);
      int high = Math.min(num(to).intValueExact(), list.size());
      for (int k = low; k <= high; k++) ((java.util.List<Object>) list).set(k - 1, GAP);
      return;
    }
    java.util.NavigableMap<Object, Object> map = (java.util.NavigableMap<Object, Object>) collection;
    Object low = key(collection, from);
    Object high = key(collection, to);
    if (compare(low, high) > 0) return;
    map.subMap(low, true, high, true).clear();
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

  /**
   * An explicit cursor opened in a shape the generator does not rewrite to a loop or a single read (#81). OPEN
   * reads every row -- Oracle's cursor is read-consistent as of its OPEN too, and ScalarDB holds no cursor across
   * statements -- and each FETCH takes the next. The attributes are Oracle's: %FOUND / %NOTFOUND are NULL before
   * the first FETCH, %ROWCOUNT counts the rows fetched so far, and every one but %ISOPEN raises INVALID_CURSOR on a
   * cursor that is not open.
   */
  public static final class Cursor {
    private final boolean variable;
    private java.util.List<Object[]> rows;
    private int position;
    private Boolean found;

    /** {@code variable}: a cursor variable ({@code OPEN cv FOR ...}), which OPEN may reopen without a CLOSE. */
    public Cursor(boolean variable) {
      this.variable = variable;
    }

    /** The rows of the query: {@code Object[]} from a direct read, a {@code List} per row from a plan. */
    public void open(java.util.List<?> rows) {
      if (this.rows != null && !variable) {
        throw new CursorAlreadyOpen();
      }
      java.util.List<Object[]> out = new java.util.ArrayList<>(rows.size());
      for (Object row : rows) out.add(row instanceof Object[] a ? a : ((java.util.List<?>) row).toArray());
      this.rows = out;
      this.position = 0;
      this.found = null;
    }

    /** The next row, or null -- the FETCH then leaves its targets as they were, as Oracle's does. */
    public Object[] fetch() {
      open();
      if (position < rows.size()) {
        found = true;
        return rows.get(position++);
      }
      found = false;
      return null;
    }

    /**
     * {@code FETCH c BULK COLLECT INTO ... [LIMIT n]} (#67): the next n rows, or every row left when there is no
     * limit. %NOTFOUND is TRUE when fewer than n came back -- the last chunk, even a non-empty one -- as Oracle has it.
     */
    public java.util.List<Object[]> fetchMany(Object limit) {
      open();
      int n = isNull(limit) ? Integer.MAX_VALUE : num(limit).intValueExact();
      if (n < 1) throw new ValueError("numeric or value error: LIMIT must be a positive integer");
      java.util.List<Object[]> out = new java.util.ArrayList<>();
      while (out.size() < n && position < rows.size()) out.add(rows.get(position++));
      found = isNull(limit) ? !out.isEmpty() : out.size() == n;
      return out;
    }

    public void close() {
      open();
      rows = null;
    }

    public Boolean found() {
      open();
      return found;
    }

    public Boolean notFound() {
      open();
      return found == null ? null : !found;
    }

    public BigDecimal rowCount() {
      open();
      return BigDecimal.valueOf(position);
    }

    public boolean isOpen() {
      return rows != null;
    }

    private void open() {
      if (rows == null) throw new InvalidCursor();
    }
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

  /** `RTRIM(x)`: the trailing blanks (U+0020) only, as {@link #trim} (#112: `RTRIM(' a '||CHR(9))` keeps the tab). */
  public static String rtrim(Object value) {
    return rtrim(value, " ");
  }

  /** `RTRIM(x, set)`: every trailing character that is in the set -- `RTRIM('xxaxx', 'x')` is `xxa`. */
  public static String rtrim(Object value, Object set) {
    return isNull(value) || isNull(set) ? null : emptyIsNull(trimmed(text(value), text(set), false, true));
  }

  public static String ltrim(Object value) {
    return ltrim(value, " ");
  }

  /** `LTRIM(x, set)`: `LTRIM('xyxaxy', 'xy')` is `axy` (26ai). */
  public static String ltrim(Object value, Object set) {
    return isNull(value) || isNull(set) ? null : emptyIsNull(trimmed(text(value), text(set), true, false));
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
    if (value instanceof CharSequence) {
      // a plan runs the rest in H2 and hands text back: "2026-09-27 10:00:00+09", "...:00.5+09:00", or no offset
      // at all (oracle-plsql-docs 12-5)
      String text = value.toString().trim().replace(' ', 'T');
      java.util.regex.Matcher short_ = java.util.regex.Pattern.compile("([+-]\\d{2})$").matcher(text);
      if (short_.find()) text = text + ":00";
      if (text.matches(".*([+-]\\d{2}:\\d{2}|Z)$")) return java.time.OffsetDateTime.parse(text);
      return LocalDateTime.parse(text).atOffset(java.time.ZoneOffset.UTC);
    }
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

  // --- SQL functions PL/SQL calls directly (#84). Each keeps Oracle's rules: a NULL argument gives NULL, text
  // that comes out empty is NULL, positions count from 1 and characters, not bytes.

  public static BigDecimal length(Object value) {
    if (isNull(value)) return null;
    String text = text(value);
    return text.isEmpty() ? null : BigDecimal.valueOf(text.codePointCount(0, text.length()));
  }

  public static String lower(Object value) {
    return isNull(value) ? null : emptyIsNull(text(value).toLowerCase(java.util.Locale.ROOT));
  }

  /** SUBSTR(s, position[, length]): position 0 is 1, a negative one counts from the end; a length below 1 is NULL. */
  public static String substr(Object value, Object position) {
    return substr(value, position, null, false);
  }

  public static String substr(Object value, Object position, Object length) {
    return substr(value, position, length, true);
  }

  private static String substr(Object value, Object position, Object length, boolean bounded) {
    if (isNull(value) || isNull(position) || (bounded && isNull(length))) return null;
    int[] points = text(value).codePoints().toArray();
    int start = num(position).setScale(0, java.math.RoundingMode.DOWN).intValue();
    if (start == 0) start = 1;
    if (start < 0) start = points.length + start + 1;
    if (start < 1 || start > points.length) return null;
    int end = points.length;
    if (bounded) {
      BigDecimal n = num(length).setScale(0, java.math.RoundingMode.DOWN);
      if (n.signum() <= 0) return null;
      end = (int) Math.min((long) start - 1 + n.longValue(), points.length);
    }
    return emptyIsNull(new String(points, start - 1, end - start + 1));
  }

  /** INSTR(s, search[, position[, occurrence]]): 0 when not found; a negative position searches backward from there. */
  public static BigDecimal instr(Object value, Object search) {
    return instr(value, search, 1, 1);
  }

  public static BigDecimal instr(Object value, Object search, Object position) {
    return instr(value, search, position, 1);
  }

  public static BigDecimal instr(Object value, Object search, Object position, Object occurrence) {
    if (isNull(value) || isNull(search) || isNull(position) || isNull(occurrence)) return null;
    String text = text(value);
    String needle = text(search);
    int start = num(position).intValue();
    int nth = num(occurrence).intValue();
    // an occurrence below 1 is ORA-06502 in PL/SQL (ORA-01428 in SQL), measured on 26ai; it was 0 here (#113). A
    // start of 0 is not an error: INSTR('abc','b',0,1) is 0
    if (nth < 1) throw new ValueError();
    if (start == 0) return BigDecimal.ZERO;
    int found = -1;
    if (start > 0) {
      int from = start - 1;
      for (int k = 0; k < nth; k++) {
        found = text.indexOf(needle, from);
        if (found < 0) return BigDecimal.ZERO;
        from = found + 1;
      }
    } else {
      int from = text.length() + start;
      for (int k = 0; k < nth; k++) {
        found = text.lastIndexOf(needle, from);
        if (found < 0) return BigDecimal.ZERO;
        from = found - 1;
      }
    }
    return BigDecimal.valueOf(text.codePointCount(0, found) + 1L);
  }

  // --- DBMS_LOB's read-only functions (#132). The generated code holds a CLOB as a String and a BLOB as a byte[]
  // (the catalog's data types), so these do to the value what DBMS_LOB does to the locator. Their rules are not the
  // SQL functions' (measured on Oracle 26ai): a NULL or out-of-range argument gives NULL rather than an error, an
  // offset does not count from the end, and numbers are truncated. As everywhere in this class an empty String is
  // NULL, so an EMPTY_CLOB() -- length 0 in Oracle -- cannot be told from a NULL CLOB here.

  private static final int LOB_MAX_AMOUNT = 32767;

  /** DBMS_LOB.GETLENGTH(lob): characters of a CLOB, bytes of a BLOB. */
  public static BigDecimal lobGetLength(Object lob) {
    if (lob instanceof byte[] bytes) return BigDecimal.valueOf(bytes.length);
    if (isNull(lob)) return null;
    String text = text(lob);
    return BigDecimal.valueOf(text.codePointCount(0, text.length()));
  }

  /** DBMS_LOB.SUBSTR(lob[, amount[, offset]]): `amount` characters from `offset`; 32767 and 1 when left out. */
  public static String lobSubstr(String lob) {
    return lobSubstr(lob, LOB_MAX_AMOUNT, 1);
  }

  public static String lobSubstr(String lob, Object amount) {
    return lobSubstr(lob, amount, 1);
  }

  public static String lobSubstr(String lob, Object amount, Object offset) {
    int[] range = lobRange(isNull(lob) ? null : lob.codePointCount(0, lob.length()), amount, offset);
    if (range == null) return null;
    int[] points = lob.codePoints().toArray();
    return emptyIsNull(new String(points, range[0], range[1] - range[0]));
  }

  public static byte[] lobSubstr(byte[] lob) {
    return lobSubstr(lob, LOB_MAX_AMOUNT, 1);
  }

  public static byte[] lobSubstr(byte[] lob, Object amount) {
    return lobSubstr(lob, amount, 1);
  }

  public static byte[] lobSubstr(byte[] lob, Object amount, Object offset) {
    int[] range = lobRange(lob == null ? null : lob.length, amount, offset);
    return range == null ? null : java.util.Arrays.copyOfRange(lob, range[0], range[1]);
  }

  /** [from, to) of a SUBSTR, or null where DBMS_LOB.SUBSTR returns NULL. */
  private static int[] lobRange(Integer length, Object amount, Object offset) {
    if (length == null || isNull(amount) || isNull(offset)) return null;
    BigDecimal n = num(amount).setScale(0, java.math.RoundingMode.DOWN);
    BigDecimal from = num(offset).setScale(0, java.math.RoundingMode.DOWN);
    if (n.signum() <= 0 || n.compareTo(BigDecimal.valueOf(LOB_MAX_AMOUNT)) > 0) return null;
    if (from.signum() <= 0 || from.compareTo(BigDecimal.valueOf(length)) > 0) return null;
    int start = from.intValue() - 1;
    return new int[] {start, (int) Math.min((long) start + n.intValue(), length)};
  }

  /** DBMS_LOB.INSTR(lob, pattern[, offset[, nth]]): where the nth match at or after `offset` starts; 0 if none. */
  public static BigDecimal lobInstr(Object lob, Object pattern) {
    return lobInstr(lob, pattern, 1, 1);
  }

  public static BigDecimal lobInstr(Object lob, Object pattern, Object offset) {
    return lobInstr(lob, pattern, offset, 1);
  }

  public static BigDecimal lobInstr(Object lob, Object pattern, Object offset, Object nth) {
    if (lob == null || isNull(lob) || isNull(pattern) || isNull(offset) || isNull(nth)) return null;
    BigDecimal from = num(offset).setScale(0, java.math.RoundingMode.DOWN);
    BigDecimal occurrence = num(nth).setScale(0, java.math.RoundingMode.DOWN);
    if (from.signum() <= 0 || occurrence.signum() <= 0) return null;
    if (lob instanceof byte[] bytes) {
      if (!(pattern instanceof byte[] needle) || needle.length == 0) return null;
      if (from.compareTo(BigDecimal.valueOf(bytes.length)) > 0) return BigDecimal.ZERO;
      int found = from.intValue() - 2;
      for (long k = occurrence.longValue(); k > 0; k--) {
        found = indexOf(bytes, needle, found + 1);
        if (found < 0) return BigDecimal.ZERO;
      }
      return BigDecimal.valueOf(found + 1L);
    }
    int[] points = text(lob).codePoints().toArray();
    int[] needle = text(pattern).codePoints().toArray();
    if (from.compareTo(BigDecimal.valueOf(points.length)) > 0) return BigDecimal.ZERO;
    int found = from.intValue() - 2;
    for (long k = occurrence.longValue(); k > 0; k--) {
      found = indexOf(points, needle, found + 1);
      if (found < 0) return BigDecimal.ZERO;
    }
    return BigDecimal.valueOf(found + 1L);
  }

  private static int indexOf(byte[] haystack, byte[] needle, int from) {
    for (int i = Math.max(from, 0); i + needle.length <= haystack.length; i++) {
      if (java.util.Arrays.equals(haystack, i, i + needle.length, needle, 0, needle.length)) return i;
    }
    return -1;
  }

  private static int indexOf(int[] haystack, int[] needle, int from) {
    for (int i = Math.max(from, 0); i + needle.length <= haystack.length; i++) {
      if (java.util.Arrays.equals(haystack, i, i + needle.length, needle, 0, needle.length)) return i;
    }
    return -1;
  }

  /** REPLACE(s, from[, to]): a NULL `from` leaves s as it is; a NULL or missing `to` removes the matches. */
  public static String replace(Object value, Object from) {
    return replace(value, from, null);
  }

  public static String replace(Object value, Object from, Object to) {
    if (isNull(value)) return null;
    if (isNull(from)) return emptyIsNull(text(value));
    return emptyIsNull(text(value).replace(text(from), isNull(to) ? "" : text(to)));
  }

  public static String lpad(Object value, Object length) {
    return padTo(value, length, " ", true);
  }

  public static String lpad(Object value, Object length, Object fill) {
    return isNull(fill) ? null : padTo(value, length, text(fill), true);
  }

  public static String rpad(Object value, Object length) {
    return padTo(value, length, " ", false);
  }

  public static String rpad(Object value, Object length, Object fill) {
    return isNull(fill) ? null : padTo(value, length, text(fill), false);
  }

  /**
   * LPAD / RPAD: cut to the length when longer, padded with the fill repeated when shorter. The length is Oracle's
   * display width, not a count of characters: an East Asian wide or full-width character is 2 (#112). Where a wide
   * character would straddle the length it is left out and a blank takes its column -- on the left for LPAD, on the
   * right for RPAD. Measured on Oracle 26ai (AL32UTF8):
   *
   * <pre>
   *   LPAD('あ',4,'*')   '**あ'      RPAD('あい',3)     'あ '       LPAD('あい',3)   ' あ'
   *   LPAD('abc',6,'あ') ' あabc'    RPAD('abc',6,'あ') 'abcあ '    LPAD('あ',1,'*') ' '
   *   LPAD('ab',5,'Ａ')  ' Ａab'     LPAD('ab',5,'ｶ')   'ｶｶｶab'     LPAD('ab',5,'★') '★★★ab'
   * </pre>
   */
  private static String padTo(Object value, Object length, String fill, boolean left) {
    if (isNull(value) || isNull(length) || fill.isEmpty()) return null;
    int size = num(length).setScale(0, java.math.RoundingMode.DOWN).intValue();
    if (size < 1) return null;
    int[] points = text(value).codePoints().toArray();
    StringBuilder body = new StringBuilder();
    int width = 0;
    for (int point : points) {
      if (width + displayWidth(point) > size) break;
      body.appendCodePoint(point);
      width += displayWidth(point);
    }
    StringBuilder filler = new StringBuilder();
    if (body.length() == text(value).length()) {
      int[] fillPoints = fill.codePoints().toArray();
      for (int k = 0; width + displayWidth(fillPoints[k % fillPoints.length]) <= size; k++) {
        filler.appendCodePoint(fillPoints[k % fillPoints.length]);
        width += displayWidth(fillPoints[k % fillPoints.length]);
      }
    }
    String blanks = " ".repeat(size - width);
    return left ? blanks + filler + body : body + filler.toString() + blanks;
  }

  /**
   * How many columns Oracle gives a character in LPAD / RPAD: 2 for Unicode's East Asian Width W and F, 1 for the
   * rest -- the ambiguous ones (○ ※ ① ★ §), the half-width katakana, even a combining mark or a zero-width space.
   * Each range below was checked on Oracle 26ai with a character of it (`LPAD('ab',5,UNISTR(...))`): all of W and
   * F measured 2 and nothing else did. The table is Unicode 16.0's.
   */
  static int displayWidth(int point) {
    int low = 0;
    int high = WIDE.length / 2 - 1;
    while (low <= high) {
      int middle = (low + high) >>> 1;
      if (point < WIDE[middle * 2]) high = middle - 1;
      else if (point > WIDE[middle * 2 + 1]) low = middle + 1;
      else return 2;
    }
    return 1;
  }

  // East Asian Width W and F, as [first, last] pairs (Unicode 16.0, generated with Python's unicodedata)
  private static final int[] WIDE = {
      0x1100, 0x115F, 0x231A, 0x231B, 0x2329, 0x232A, 0x23E9, 0x23EC, 0x23F0, 0x23F0, 0x23F3, 0x23F3, 0x25FD, 0x25FE,
      0x2614, 0x2615, 0x2630, 0x2637, 0x2648, 0x2653, 0x267F, 0x267F, 0x268A, 0x268F, 0x2693, 0x2693, 0x26A1, 0x26A1,
      0x26AA, 0x26AB, 0x26BD, 0x26BE, 0x26C4, 0x26C5, 0x26CE, 0x26CE, 0x26D4, 0x26D4, 0x26EA, 0x26EA, 0x26F2, 0x26F3,
      0x26F5, 0x26F5, 0x26FA, 0x26FA, 0x26FD, 0x26FD, 0x2705, 0x2705, 0x270A, 0x270B, 0x2728, 0x2728, 0x274C, 0x274C,
      0x274E, 0x274E, 0x2753, 0x2755, 0x2757, 0x2757, 0x2795, 0x2797, 0x27B0, 0x27B0, 0x27BF, 0x27BF, 0x2B1B, 0x2B1C,
      0x2B50, 0x2B50, 0x2B55, 0x2B55, 0x2E80, 0x2E99, 0x2E9B, 0x2EF3, 0x2F00, 0x2FD5, 0x2FF0, 0x303E, 0x3041, 0x3096,
      0x3099, 0x30FF, 0x3105, 0x312F, 0x3131, 0x318E, 0x3190, 0x31E5, 0x31EF, 0x321E, 0x3220, 0x3247, 0x3250, 0xA48C,
      0xA490, 0xA4C6, 0xA960, 0xA97C, 0xAC00, 0xD7A3, 0xF900, 0xFAFF, 0xFE10, 0xFE19, 0xFE30, 0xFE52, 0xFE54, 0xFE66,
      0xFE68, 0xFE6B, 0xFF01, 0xFF60, 0xFFE0, 0xFFE6, 0x16FE0, 0x16FE4, 0x16FF0, 0x16FF1, 0x17000, 0x187F7, 0x18800,
      0x18CD5, 0x18CFF, 0x18D08, 0x1AFF0, 0x1AFF3, 0x1AFF5, 0x1AFFB, 0x1AFFD, 0x1AFFE, 0x1B000, 0x1B122, 0x1B132,
      0x1B132, 0x1B150, 0x1B152, 0x1B155, 0x1B155, 0x1B164, 0x1B167, 0x1B170, 0x1B2FB, 0x1D300, 0x1D356, 0x1D360,
      0x1D376, 0x1F004, 0x1F004, 0x1F0CF, 0x1F0CF, 0x1F18E, 0x1F18E, 0x1F191, 0x1F19A, 0x1F200, 0x1F202, 0x1F210,
      0x1F23B, 0x1F240, 0x1F248, 0x1F250, 0x1F251, 0x1F260, 0x1F265, 0x1F300, 0x1F320, 0x1F32D, 0x1F335, 0x1F337,
      0x1F37C, 0x1F37E, 0x1F393, 0x1F3A0, 0x1F3CA, 0x1F3CF, 0x1F3D3, 0x1F3E0, 0x1F3F0, 0x1F3F4, 0x1F3F4, 0x1F3F8,
      0x1F43E, 0x1F440, 0x1F440, 0x1F442, 0x1F4FC, 0x1F4FF, 0x1F53D, 0x1F54B, 0x1F54E, 0x1F550, 0x1F567, 0x1F57A,
      0x1F57A, 0x1F595, 0x1F596, 0x1F5A4, 0x1F5A4, 0x1F5FB, 0x1F64F, 0x1F680, 0x1F6C5, 0x1F6CC, 0x1F6CC, 0x1F6D0,
      0x1F6D2, 0x1F6D5, 0x1F6D7, 0x1F6DC, 0x1F6DF, 0x1F6EB, 0x1F6EC, 0x1F6F4, 0x1F6FC, 0x1F7E0, 0x1F7EB, 0x1F7F0,
      0x1F7F0, 0x1F90C, 0x1F93A, 0x1F93C, 0x1F945, 0x1F947, 0x1F9FF, 0x1FA70, 0x1FA7C, 0x1FA80, 0x1FA89, 0x1FA8F,
      0x1FAC6, 0x1FACE, 0x1FADC, 0x1FADF, 0x1FAE9, 0x1FAF0, 0x1FAF8, 0x20000, 0x2FFFD, 0x30000, 0x3FFFD,
  };

  /** COALESCE: the first argument that is not NULL. */
  public static Object coalesce(Object... values) {
    for (Object value : values) {
      if (!isNull(value)) return value;
    }
    return null;
  }

  /** NVL2(x, if not null, if null). */
  public static Object nvl2(Object value, Object ifNotNull, Object ifNull) {
    return isNull(value) ? ifNull : ifNotNull;
  }

  /** GREATEST / LEAST: NULL when any argument is NULL. */
  public static Object greatest(Object... values) {
    return extreme(values, 1);
  }

  public static Object least(Object... values) {
    return extreme(values, -1);
  }

  private static Object extreme(Object[] values, int sign) {
    Object best = null;
    for (Object value : values) {
      if (isNull(value)) return null;
      if (best == null || Integer.signum(compare(value, best)) == sign) best = value;
    }
    return best;
  }

  public static BigDecimal power(Object base, Object exponent) {
    if (isNull(base) || isNull(exponent)) return null;
    BigDecimal b = num(base);
    BigDecimal e = num(exponent);
    boolean whole = e.stripTrailingZeros().scale() <= 0;
    // POWER(0, -1) and POWER(-2, 0.5) are ORA-06502 in PL/SQL (26ai). They were Java's ArithmeticException and a
    // NumberFormatException from a NaN, which no migrated handler catches (#113)
    if ((b.signum() == 0 && e.signum() < 0) || (b.signum() < 0 && !whole)) throw new ValueError();
    if (whole && e.abs().compareTo(BigDecimal.valueOf(999)) <= 0) {
      int n = e.intValue();
      BigDecimal raised = b.pow(Math.abs(n));
      // rounded as a NUMBER holds it (#113): POWER(2, 200) is 160693804425899027554196209234116260252 followed by
      // 22 zeros on 26ai, and POWER(1.1, 50) 117.390852879695316506666495990358319939
      return n >= 0 ? OracleNumbers.round40(raised) : OracleNumbers.divide(BigDecimal.ONE, raised);
    }
    // x ** 0.5 as √x: Oracle's own result differs from it in the last digit or two (it uses exp and ln)
    if (e.compareTo(new BigDecimal("0.5")) == 0) return sqrt(b);
    // other fractional exponents in double precision: about 16 digits, where Oracle keeps 39-40
    return OracleNumbers.round40(new BigDecimal(Math.pow(b.doubleValue(), e.doubleValue())));
  }

  /** SQRT: rounded as a NUMBER is stored (40 or 39 digits, OracleNumbers.divide). */
  public static BigDecimal sqrt(Object value) {
    if (isNull(value)) return null;
    BigDecimal n = num(value);
    if (n.signum() < 0) throw new ValueError("argument of SQRT is negative");
    return OracleNumbers.round40(n.sqrt(new java.math.MathContext(50)));
  }

  public static BigDecimal ceil(Object value) {
    return isNull(value) ? null : num(value).setScale(0, java.math.RoundingMode.CEILING);
  }

  public static BigDecimal floor(Object value) {
    return isNull(value) ? null : num(value).setScale(0, java.math.RoundingMode.FLOOR);
  }

  public static BigDecimal sign(Object value) {
    return isNull(value) ? null : BigDecimal.valueOf(num(value).signum());
  }

  public static String chr(Object code) {
    return isNull(code) ? null : new String(Character.toChars(num(code).intValue()));
  }

  public static BigDecimal ascii(Object value) {
    if (isNull(value) || text(value).isEmpty()) return null;
    return BigDecimal.valueOf(text(value).codePointAt(0));
  }

  /** TO_DATE(text[, format]). Without a format the session's NLS_DATE_FORMAT, DD-MON-RR in English. */
  public static LocalDateTime toDate(Object value) {
    return toDate(value, "DD-MON-RR");
  }

  /**
   * TO_DATE(text, format), read element by element as Oracle reads it (#112). Measured on Oracle 26ai, 2026-09-27:
   *
   * <ul>
   *   <li>a numeric element takes up to its width in digits and fewer are fine: `TO_DATE('1-1-2026','DD-MM-YYYY')`
   *       is 2026-01-01 and '2026-3-5 7:8:9' reads too. java.time asked for exactly two, and refused them;
   *   <li>`YY` is a year of the current century -- `TO_DATE('01-JAN-99','DD-MON-YY')` is 2099-01-01. `RR` (and `RRRR`
   *       given two digits) is the one that picks the century around the current year: 99 is 1999, 49 is 2049;
   *   <li>what the format leaves out is the current year and month, day 1, midnight: `TO_DATE('2026','YYYY')` is
   *       2026-09-01 in September, `TO_DATE('10:30','HH24:MI')` the 1st of this month at 10:30. Year and month come
   *       from {@link #sysdate()}, the database clock, as Oracle's come from SYSDATE;
   *   <li>punctuation in the format matches any punctuation: '2026/01/05' reads with 'YYYY-MM-DD'.
   * </ul>
   */
  public static LocalDateTime toDate(Object value, Object format) {
    if (isNull(value) || isNull(format)) return null;
    if (value instanceof LocalDateTime d) return d;
    return parseMoment(text(value).trim(), text(format), false);
  }

  /** TO_TIMESTAMP(text): the session's NLS_TIMESTAMP_FORMAT, DD-MON-RR HH.MI.SSXFF AM (#140). */
  public static LocalDateTime toTimestamp(Object value) {
    return toTimestamp(value, "DD-MON-RR HH.MI.SSXFF AM");
  }

  /**
   * TO_TIMESTAMP(text, format): {@link #toDate(Object, Object)}'s reading plus the fraction of a second (#140). Measured
   * on Oracle 26ai: `FF` takes up to nine digits and `FFn` up to n -- more is ORA-01830, fewer are fine
   * ('01:02:03.5' with FF3 is .500); `X` is the radix character `.`; a fraction left out of the input is zero. A
   * DATE or TIMESTAMP argument comes back as it is.
   */
  public static LocalDateTime toTimestamp(Object value, Object format) {
    if (isNull(value) || isNull(format)) return null;
    if (value instanceof LocalDateTime d) return d;
    if (value instanceof java.time.OffsetDateTime d) return d.toLocalDateTime();
    return parseMoment(text(value).trim(), text(format), true);
  }

  /**
   * An error a SQL function raises with its own ORA number, one PL/SQL has no predefined name for: TO_DATE's
   * ORA-01843 (not a valid month), REGEXP_SUBSTR's ORA-01428 (argument out of range), ... Only WHEN OTHERS or a
   * name bound to the number with EXCEPTION_INIT catches it in Oracle, and not VALUE_ERROR -- which TO_DATE's
   * failures used to be here (#140). The generated code turns it into MigratedException with {@link #code()}.
   */
  public static final class FunctionError extends OracleError {
    public FunctionError(int code, String message) {
      super(code, message);
    }
  }

  private static FunctionError dateError(int code, String text) {
    return new FunctionError(code, String.format("ORA-%05d: %s", -code, text));
  }

  // what Oracle 26ai says for each way a date does not read (#140)
  private static FunctionError notLongEnough() {
    return dateError(-1840, "input value not long enough for date format");
  }

  private static FunctionError nonNumeric() {
    return dateError(-1858, "A non-numeric character was found instead of a numeric character.");
  }

  /**
   * TO_DATE / TO_TIMESTAMP, read element by element as Oracle reads them (#112, #140). Measured on Oracle 26ai:
   *
   * <ul>
   *   <li>a numeric element takes up to its width in digits and fewer are fine: `TO_DATE('1-1-2026','DD-MM-YYYY')`
   *       is 2026-01-01 and '2026-3-5 7:8:9' reads too;
   *   <li>`YY` is a year of the current century -- `TO_DATE('01-JAN-99','DD-MON-YY')` is 2099-01-01. `RR` (and `RRRR`
   *       given two digits) is the one that picks the century around the current year: 99 is 1999, 49 is 2049;
   *   <li>what the format leaves out is the current year and month, day 1, midnight: `TO_DATE('2026','YYYY')` is
   *       2026-09-01 in September. Year and month come from {@link #sysdate()}, the database clock;
   *   <li>punctuation in the format matches any punctuation: '2026/01/05' reads with 'YYYY-MM-DD';
   *   <li>the input may stop before a time element ('2026-01-01 10' with 'YYYY-MM-DD HH24:MI:SS' is 10:00:00), not
   *       before a date element (ORA-01840);
   *   <li>each failure has its own number, and none is VALUE_ERROR: ORA-01830 input left over, ORA-01841 year,
   *       ORA-01843 month, ORA-01847 day, ORA-01839 day for the month, ORA-01850 / 01849 hour, ORA-01851 minutes,
   *       ORA-01852 seconds, ORA-01855 AM/PM, ORA-01858 not a number where one was expected.
   * </ul>
   */
  private static LocalDateTime parseMoment(String input, String model, boolean timestamp) {
    String f = model.toUpperCase(java.util.Locale.ROOT);
    LocalDateTime now = sysdate();
    int year = now.getYear();
    int month = now.getMonthValue();
    int day = 1;
    int hour = 0;
    int minute = 0;
    int second = 0;
    int nano = 0;
    boolean twelve = false;
    Boolean afternoon = null;
    int[] at = {0};
    int i = 0;
    while (i < f.length()) {
      String rest = f.substring(i);
      boolean dateElement = rest.startsWith("Y") || rest.startsWith("R") || rest.startsWith("MM")
          || rest.startsWith("MON") || rest.startsWith("DD");
      if (at[0] >= input.length() && Character.isLetter(f.charAt(i))) {
        if (dateElement) throw notLongEnough();
        break;   // the time of day may be left out
      }
      if (rest.startsWith("YYYY") || rest.startsWith("RRRR")) {
        int start = at[0];
        if (!Character.isDigit(input.charAt(start))) {
          throw dateError(-1841, "(full) year must be between -4713 and +9999, and not be 0");
        }
        int y = digits(input, at, 4);
        year = rest.startsWith("RRRR") && at[0] - start <= 2 ? rr(y, now.getYear()) : y;
        if (year == 0) throw dateError(-1841, "(full) year must be between -4713 and +9999, and not be 0");
        i += 4;
      } else if (rest.startsWith("RR")) {
        int start = at[0];
        int y = digits(input, at, 4);
        year = at[0] - start <= 2 ? rr(y, now.getYear()) : y;   // RR given four digits takes them as they are
        i += 2;
      } else if (rest.startsWith("YY")) {
        year = now.getYear() / 100 * 100 + digits(input, at, 2);
        i += 2;
      } else if (rest.startsWith("MONTH") || rest.startsWith("MON")) {
        month = monthName(input, at);
        i += rest.startsWith("MONTH") ? 5 : 3;
      } else if (rest.startsWith("MM")) {
        month = digits(input, at, 2);
        if (month < 1 || month > 12) throw dateError(-1843, "An invalid month was specified.");
        i += 2;
      } else if (rest.startsWith("DD")) {
        day = digits(input, at, 2);
        if (day < 1 || day > 31) throw dateError(-1847, "day of month must be between 1 and last day of month");
        i += 2;
      } else if (rest.startsWith("HH24")) {
        hour = digits(input, at, 2);
        if (hour > 23) throw dateError(-1850, "hour must be between 0 and 23");
        i += 4;
      } else if (rest.startsWith("HH12") || rest.startsWith("HH")) {
        hour = digits(input, at, 2);
        if (hour < 1 || hour > 12) throw dateError(-1849, "hour must be between 1 and 12");
        twelve = true;
        i += rest.startsWith("HH12") ? 4 : 2;
      } else if (rest.startsWith("MI")) {
        minute = digits(input, at, 2);
        if (minute > 59) throw dateError(-1851, "minutes must be between 0 and 59");
        i += 2;
      } else if (rest.startsWith("SS")) {
        second = digits(input, at, 2);
        if (second > 59) throw dateError(-1852, "seconds must be between 0 and 59");
        i += 2;
      } else if (timestamp && rest.startsWith("FF")) {
        boolean sized = rest.length() > 2 && rest.charAt(2) >= '1' && rest.charAt(2) <= '9';
        int width = sized ? rest.charAt(2) - '0' : 9;
        int start = at[0];
        digits(input, at, width);
        String fraction = input.substring(start, at[0]);
        nano = Integer.parseInt((fraction + "000000000").substring(0, 9));
        i += sized ? 3 : 2;
      } else if (timestamp && rest.startsWith("X")) {
        // the radix character. Anything else stops the reading there: '03,5' with SSXFF is ORA-01830 (26ai)
        if (input.charAt(at[0]) != '.') break;
        at[0]++;
        i += 1;
      } else if (rest.startsWith("AM") || rest.startsWith("PM")) {
        String marker = input.substring(at[0], Math.min(at[0] + 2, input.length())).toUpperCase(java.util.Locale.ROOT);
        if (!marker.equals("AM") && !marker.equals("PM")) throw dateError(-1855, "AM/A.M. or PM/P.M. required");
        afternoon = marker.equals("PM");
        at[0] += 2;
        i += 2;
      } else if (Character.isLetterOrDigit(f.charAt(i))) {
        throw new UnsupportedOperationException("TO_DATE format element not mapped: " + model.substring(i));
      } else {
        // a separator: any one punctuation or blank of the input stands for it, and a missing one is fine
        if (at[0] < input.length() && !Character.isLetterOrDigit(input.charAt(at[0]))) at[0]++;
        i++;
      }
    }
    if (at[0] < input.length()) {
      throw dateError(-1830, "Date format picture ends before converting entire input string.");
    }
    if (twelve) hour = hour % 12 + (Boolean.TRUE.equals(afternoon) ? 12 : 0);
    if (day > java.time.YearMonth.of(year, month).lengthOfMonth()) {
      throw dateError(-1839, "date not valid for month specified");
    }
    return LocalDateTime.of(year, month, day, hour, minute, second, nano);
  }

  /** Up to {@code width} digits of the input from {@code at[0]}, at least one (ORA-01858 when there is none). */
  private static int digits(String input, int[] at, int width) {
    int start = at[0];
    while (at[0] < input.length() && at[0] - start < width && Character.isDigit(input.charAt(at[0]))) at[0]++;
    if (at[0] == start) throw nonNumeric();
    return Integer.parseInt(input.substring(start, at[0]));
  }

  /** RR: a two-digit year into the century that puts it nearest the current year (Oracle's rule for RR). */
  private static int rr(int twoDigits, int currentYear) {
    int century = currentYear / 100 * 100;
    boolean currentLow = currentYear % 100 < 50;
    if (currentLow) return twoDigits < 50 ? century + twoDigits : century - 100 + twoDigits;
    return twoDigits < 50 ? century + 100 + twoDigits : century + twoDigits;
  }

  /** A month's English name, full or its first three letters, in any case: 'January' and 'jan' are both 1. */
  private static int monthName(String input, int[] at) {
    String rest = input.substring(at[0]).toUpperCase(java.util.Locale.ROOT);
    for (java.time.Month month : java.time.Month.values()) {
      if (rest.startsWith(month.name())) {
        at[0] += month.name().length();
        return month.getValue();
      }
    }
    for (int m = 0; m < MONTHS.length; m++) {
      if (rest.startsWith(MONTHS[m])) {
        at[0] += 3;
        return m + 1;
      }
    }
    throw dateError(-1843, "An invalid month was specified.");
  }

  /** ADD_MONTHS: the last day of a month stays the last day (31-JAN + 1 month is 28/29-FEB, 28-FEB + 1 is 31-MAR). */
  public static LocalDateTime addMonths(Object value, Object months) {
    if (isNull(value) || isNull(months)) return null;
    LocalDateTime date = castDate(value);
    LocalDateTime moved = date.plusMonths(num(months).intValue());
    if (date.toLocalDate().equals(date.toLocalDate().withDayOfMonth(date.toLocalDate().lengthOfMonth()))) {
      moved = moved.withDayOfMonth(moved.toLocalDate().lengthOfMonth());
    }
    return moved;
  }

  public static LocalDateTime lastDay(Object value) {
    if (isNull(value)) return null;
    LocalDateTime date = castDate(value);
    return date.withDayOfMonth(date.toLocalDate().lengthOfMonth());
  }

  // --- more SQL functions PL/SQL calls (#140). Each was measured on Oracle 26ai (AL32UTF8, AMERICAN) for NULL, the
  // boundaries and the error it raises; the numbers are in the tests (PlsqlTest.builtins*).

  /** NULLIF(a, b): NULL when a = b (Oracle's `=`, so '1' = 1), else a. A NULL b never equals, so a comes back. */
  public static <T> T nullif(T a, Object b) {
    if (isNull(a)) return null;
    return eq(a, b) ? null : a;
  }

  /**
   * MONTHS_BETWEEN(d1, d2): whole months when both are the same day of the month or both the last day (the time of
   * day ignored), else a fraction over a 31-day month, rounded as a NUMBER is. The rule is written once, in
   * {@link com.scalar.migrate.runtime.OracleFunctions#monthsBetween}, which the residual SQL engine registers in H2; a
   * TIMESTAMP is read as a DATE (the fraction of a second dropped) and text as TO_DATE reads it.
   */
  public static BigDecimal monthsBetween(Object d1, Object d2) {
    if (isNull(d1) || isNull(d2)) return null;
    return com.scalar.migrate.runtime.OracleFunctions.monthsBetween(dateArgument(d1), dateArgument(d2));
  }

  private static LocalDateTime dateArgument(Object value) {
    return value instanceof CharSequence ? toDate(value) : castDate(value);
  }

  /**
   * EXTRACT(field FROM value) (#140). YEAR, MONTH, DAY, HOUR, MINUTE and SECOND (with its fraction: 5.456) of a DATE
   * or TIMESTAMP; of a TIMESTAMP WITH TIME ZONE they are the UTC ones, as Oracle's are (EXTRACT(HOUR FROM TIMESTAMP
   * '2026-01-02 10:11:12 +09:00') is 1), and TIMEZONE_HOUR / TIMEZONE_MINUTE are its offset. Oracle refuses HOUR of a
   * DATE when it compiles (PLS-00656), so this does not tell the two apart. An INTERVAL is not modelled: the
   * generated code has a number of days where Oracle has one, and reading fields out of that would be wrong.
   */
  public static BigDecimal extract(String field, Object value) {
    if (isNull(value)) return null;
    if (value instanceof Number) {
      throw new UnsupportedOperationException("EXTRACT(" + field + " FROM an INTERVAL) is not modelled: "
          + "the difference of two datetimes is a number of days here");
    }
    java.time.ZoneOffset offset = null;
    LocalDateTime t;
    if (value instanceof java.time.OffsetDateTime o) {
      offset = o.getOffset();
      t = o.withOffsetSameInstant(java.time.ZoneOffset.UTC).toLocalDateTime();
    } else if (value instanceof java.time.LocalDate d) {
      t = d.atStartOfDay();
    } else {
      t = moment(value);
    }
    switch (field.toUpperCase(java.util.Locale.ROOT)) {
      case "YEAR":
        return BigDecimal.valueOf(t.getYear());
      case "MONTH":
        return BigDecimal.valueOf(t.getMonthValue());
      case "DAY":
        return BigDecimal.valueOf(t.getDayOfMonth());
      case "HOUR":
        return BigDecimal.valueOf(t.getHour());
      case "MINUTE":
        return BigDecimal.valueOf(t.getMinute());
      case "SECOND":
        BigDecimal seconds = BigDecimal.valueOf(t.getSecond()).add(BigDecimal.valueOf(t.getNano(), 9)).stripTrailingZeros();
        return seconds.scale() < 0 ? seconds.setScale(0) : seconds;
      case "TIMEZONE_HOUR":
        if (offset == null) break;
        return BigDecimal.valueOf(offset.getTotalSeconds() / 3600);
      case "TIMEZONE_MINUTE":
        if (offset == null) break;
        return BigDecimal.valueOf(offset.getTotalSeconds() / 60 % 60);
      default:
        break;
    }
    throw new UnsupportedOperationException("EXTRACT(" + field + " FROM " + value.getClass().getSimpleName() + ")");
  }

  /** LENGTHB: the bytes of the text in the database character set, which is taken to be AL32UTF8 (UTF-8). */
  public static BigDecimal lengthb(Object value) {
    if (isNull(value)) return null;
    String text = text(value);
    return text.isEmpty() ? null : BigDecimal.valueOf(text.getBytes(java.nio.charset.StandardCharsets.UTF_8).length);
  }

  /**
   * TRANSLATE(s, from, to): each character of {@code from} becomes the one at the same place in {@code to}, and one
   * with no counterpart there is removed ('abcabc','ab','x' is 'xcxc'). The first occurrence of a repeated character
   * decides ('aab','aa','xy' is 'xxb'). Any NULL argument -- '' included -- makes it NULL, as does a result with
   * nothing left.
   */
  public static String translate(Object value, Object from, Object to) {
    if (isNull(value) || isNull(from) || isNull(to)) return null;
    int[] source = text(from).codePoints().toArray();
    int[] target = text(to).codePoints().toArray();
    java.util.Map<Integer, Integer> map = new java.util.HashMap<>();
    for (int k = 0; k < source.length; k++) map.putIfAbsent(source[k], k < target.length ? target[k] : -1);
    StringBuilder out = new StringBuilder();
    text(value).codePoints().forEach(point -> {
      Integer mapped = map.get(point);
      if (mapped == null) out.appendCodePoint(point);
      else if (mapped >= 0) out.appendCodePoint(mapped);
    });
    return emptyIsNull(out.toString());
  }

  private static final java.math.BigInteger BITAND_LIMIT = java.math.BigInteger.ONE.shiftLeft(127);

  /**
   * BITAND(a, b): the bits both have, of the integer parts (5.7 is 5, -2.5 is -2), in two's complement: BITAND(-6, -3)
   * is -8. An operand outside -2**127 .. 2**127-1 is ORA-06502.
   */
  public static BigDecimal bitand(Object a, Object b) {
    if (isNull(a) || isNull(b)) return null;
    java.math.BigInteger x = num(a).setScale(0, java.math.RoundingMode.DOWN).toBigIntegerExact();
    java.math.BigInteger y = num(b).setScale(0, java.math.RoundingMode.DOWN).toBigIntegerExact();
    for (java.math.BigInteger v : new java.math.BigInteger[] {x, y}) {
      if (v.compareTo(BITAND_LIMIT) >= 0 || v.compareTo(BITAND_LIMIT.negate()) < 0) throw new ValueError();
    }
    return new BigDecimal(x.and(y));
  }

  /**
   * RAWTOHEX(r): upper-case hex. In PL/SQL a text argument is converted to RAW first, as hex -- RAWTOHEX('ab') is 'AB'
   * and RAWTOHEX('XY') is ORA-06502 -- where SQL's RAWTOHEX('AB') is the characters' bytes, '4142'. This is the
   * PL/SQL one (#140).
   */
  public static String rawToHex(Object value) {
    byte[] raw = hexToRaw(value);
    return raw == null ? null : hex(raw);
  }

  /**
   * HEXTORAW(text): the bytes the hex digits spell, either case; an odd count takes a leading 0 ('ABC' is 0ABC). A
   * character that is not a hex digit -- a blank included -- is ORA-06502 in PL/SQL. A number is read as its text.
   */
  public static byte[] hexToRaw(Object value) {
    if (isNull(value)) return null;
    if (value instanceof byte[] raw) return raw;
    String text = text(value);
    if (text.length() % 2 == 1) text = "0" + text;
    byte[] out = new byte[text.length() / 2];
    for (int k = 0; k < out.length; k++) {
      int high = Character.digit(text.charAt(2 * k), 16);
      int low = Character.digit(text.charAt(2 * k + 1), 16);
      if (high < 0 || low < 0 || text.charAt(2 * k) > 0x7f || text.charAt(2 * k + 1) > 0x7f) {
        throw new ValueError("hex to raw conversion error");
      }
      out[k] = (byte) (high << 4 | low);
    }
    return out;
  }

  static String hex(byte[] raw) {
    StringBuilder out = new StringBuilder(raw.length * 2);
    for (byte b : raw) out.append(Character.toUpperCase(Character.forDigit(b >> 4 & 0xf, 16)))
        .append(Character.toUpperCase(Character.forDigit(b & 0xf, 16)));
    return out.toString();
  }

  private static final java.security.SecureRandom GUIDS = new java.security.SecureRandom();

  /**
   * SYS_GUID(): 16 bytes, a RAW(16) -- 32 hex digits as text. Oracle's are made from the host and process and a
   * counter; these are random. Which to use is a decision the migration still has to take (SEM-014), which is why
   * a routine calling it stays REVIEW.
   */
  public static byte[] sysGuid() {
    byte[] out = new byte[16];
    GUIDS.nextBytes(out);
    return out;
  }

  // --- regular expressions (#140). The pattern is Oracle's dialect, translated by OracleRegex. The numeric arguments
  // are rounded (REGEXP_INSTR('abab','b',2.5) starts at 3), a NULL one makes the result NULL, and one out of range is
  // ORA-01428 -- a position or occurrence below 1, a return option below 0, a subexpression outside 0 .. 9.

  /** REGEXP_LIKE(s, pattern[, match]): UNKNOWN (null) when s or the pattern is NULL. */
  public static Boolean regexpLike(Object value, Object pattern) {
    return regexpLike(value, pattern, null);
  }

  public static Boolean regexpLike(Object value, Object pattern, Object match) {
    if (isNull(value) || isNull(pattern)) return null;
    return regex(pattern, match).matcher(text(value)).find();
  }

  /** REGEXP_SUBSTR(s, pattern[, position[, occurrence[, match[, subexpression]]]]): NULL when nothing is found. */
  public static String regexpSubstr(Object value, Object pattern) {
    return regexpSubstr(value, pattern, 1, 1, null, 0);
  }

  public static String regexpSubstr(Object value, Object pattern, Object position) {
    return regexpSubstr(value, pattern, position, 1, null, 0);
  }

  public static String regexpSubstr(Object value, Object pattern, Object position, Object occurrence) {
    return regexpSubstr(value, pattern, position, occurrence, null, 0);
  }

  public static String regexpSubstr(Object value, Object pattern, Object position, Object occurrence, Object match) {
    return regexpSubstr(value, pattern, position, occurrence, match, 0);
  }

  public static String regexpSubstr(Object value, Object pattern, Object position, Object occurrence, Object match,
      Object subexpression) {
    Object[] a = arguments(position, occurrence, match, subexpression);
    if (isNull(value) || isNull(pattern) || a[0] == null || a[1] == null || a[3] == null) return null;
    int from = positive(a[0]);
    int nth = positive(a[1]);
    int group = subexpression(a[3]);
    java.util.regex.Matcher m = regex(pattern, a[2]).matcher(text(value));
    if (!nth(m, text(value), from, nth) || group > m.groupCount()) return null;
    String found = m.group(group);
    return found == null ? null : emptyIsNull(found);
  }

  /**
   * REGEXP_INSTR(s, pattern[, position[, occurrence[, return_option[, match[, subexpression]]]]]): where the match
   * (or its subexpression) starts, or with a non-zero return option where it ends plus one; 0 when not found.
   */
  public static BigDecimal regexpInstr(Object value, Object pattern) {
    return regexpInstr(value, pattern, 1, 1, 0, null, 0);
  }

  public static BigDecimal regexpInstr(Object value, Object pattern, Object position) {
    return regexpInstr(value, pattern, position, 1, 0, null, 0);
  }

  public static BigDecimal regexpInstr(Object value, Object pattern, Object position, Object occurrence) {
    return regexpInstr(value, pattern, position, occurrence, 0, null, 0);
  }

  public static BigDecimal regexpInstr(Object value, Object pattern, Object position, Object occurrence, Object option) {
    return regexpInstr(value, pattern, position, occurrence, option, null, 0);
  }

  public static BigDecimal regexpInstr(Object value, Object pattern, Object position, Object occurrence, Object option,
      Object match) {
    return regexpInstr(value, pattern, position, occurrence, option, match, 0);
  }

  public static BigDecimal regexpInstr(Object value, Object pattern, Object position, Object occurrence, Object option,
      Object match, Object subexpression) {
    Object[] a = arguments(position, occurrence, option, match, subexpression);
    if (isNull(value) || isNull(pattern) || a[0] == null || a[1] == null || a[2] == null || a[4] == null) return null;
    int from = positive(a[0]);
    int nth = positive(a[1]);
    BigDecimal returned = num(a[2]);
    if (returned.signum() < 0) throw outOfRange(rounded(returned));
    int group = subexpression(a[4]);
    String text = text(value);
    java.util.regex.Matcher m = regex(pattern, a[3]).matcher(text);
    if (!nth(m, text, from, nth) || group > m.groupCount() || m.start(group) < 0) return BigDecimal.ZERO;
    int at = returned.signum() == 0 ? m.start(group) : m.end(group);
    return BigDecimal.valueOf(text.codePointCount(0, at) + 1L);
  }

  /** REGEXP_COUNT(s, pattern[, position[, match]]): how many times it matches, the matches not overlapping. */
  public static BigDecimal regexpCount(Object value, Object pattern) {
    return regexpCount(value, pattern, 1, null);
  }

  public static BigDecimal regexpCount(Object value, Object pattern, Object position) {
    return regexpCount(value, pattern, position, null);
  }

  public static BigDecimal regexpCount(Object value, Object pattern, Object position, Object match) {
    Object[] a = arguments(position, match);
    if (isNull(value) || isNull(pattern) || a[0] == null) return null;
    String text = text(value);
    java.util.regex.Matcher m = regex(pattern, a[1]).matcher(text);
    int start = offset(text, positive(a[0]));
    if (start < 0) return BigDecimal.ZERO;
    int n = 0;
    m.region(start, text.length());
    m.useTransparentBounds(true).useAnchoringBounds(false);
    while (m.find()) n++;
    return BigDecimal.valueOf(n);
  }

  /**
   * REGEXP_REPLACE(s, pattern[, replacement[, position[, occurrence[, match]]]]): every match (occurrence 0) or the
   * n-th replaced. The replacement refers to a subexpression as \1 .. \9 -- one the pattern does not have is empty
   * -- and `\\` is a backslash; `$1` and any other backslash are themselves. A NULL pattern leaves s as it is, a NULL
   * replacement removes the matches, and a result with nothing left is NULL.
   */
  public static String regexpReplace(Object value, Object pattern) {
    return regexpReplace(value, pattern, null, 1, 0, null);
  }

  public static String regexpReplace(Object value, Object pattern, Object replacement) {
    return regexpReplace(value, pattern, replacement, 1, 0, null);
  }

  public static String regexpReplace(Object value, Object pattern, Object replacement, Object position) {
    return regexpReplace(value, pattern, replacement, position, 0, null);
  }

  public static String regexpReplace(Object value, Object pattern, Object replacement, Object position,
      Object occurrence) {
    return regexpReplace(value, pattern, replacement, position, occurrence, null);
  }

  public static String regexpReplace(Object value, Object pattern, Object replacement, Object position,
      Object occurrence, Object match) {
    Object[] a = arguments(replacement, position, occurrence, match);
    if (isNull(value)) return null;
    String text = text(value);
    if (isNull(pattern)) return emptyIsNull(text);
    if (a[1] == null || a[2] == null) return null;
    int start = offset(text, positive(a[1]));
    int nth = rounded(num(a[2]));
    if (nth < 0) throw outOfRange(nth);
    String with = isNull(a[0]) ? "" : text(a[0]);
    if (start < 0) return emptyIsNull(text);
    java.util.regex.Matcher m = regex(pattern, a[3]).matcher(text);
    m.region(start, text.length());
    m.useTransparentBounds(true).useAnchoringBounds(false);
    StringBuilder out = new StringBuilder(text.substring(0, start));
    int copied = start;
    int k = 0;
    while (m.find()) {
      k++;
      if (nth != 0 && k != nth) continue;
      out.append(text, copied, m.start()).append(substitute(with, m));
      copied = m.end();
      if (nth != 0) break;
    }
    out.append(text.substring(copied));
    return emptyIsNull(out.toString());
  }

  private static String substitute(String replacement, java.util.regex.Matcher m) {
    StringBuilder out = new StringBuilder();
    for (int k = 0; k < replacement.length(); k++) {
      char c = replacement.charAt(k);
      if (c == '\\' && k + 1 < replacement.length()) {
        char next = replacement.charAt(k + 1);
        if (next >= '1' && next <= '9') {
          int group = next - '0';
          String found = group <= m.groupCount() ? m.group(group) : null;
          if (found != null) out.append(found);
          k++;
          continue;
        }
        if (next == '\\') {
          out.append('\\');
          k++;
          continue;
        }
      }
      out.append(c);
    }
    return out.toString();
  }

  private static java.util.regex.Pattern regex(Object pattern, Object match) {
    return OracleRegex.compile(text(pattern), isNull(match) ? null : text(match));
  }

  /** The optional arguments, '' read as the NULL it is. A NULL match parameter is its default; any other NULL makes
   * the result NULL (a left-out argument arrives as its default from the shorter overload). */
  private static Object[] arguments(Object... given) {
    Object[] out = given.clone();
    for (int k = 0; k < out.length; k++) out[k] = isNull(out[k]) ? null : out[k];
    return out;
  }

  private static int rounded(BigDecimal value) {
    BigDecimal whole = value.setScale(0, java.math.RoundingMode.HALF_UP);
    if (whole.compareTo(BigDecimal.valueOf(Integer.MAX_VALUE)) > 0) return Integer.MAX_VALUE;
    if (whole.compareTo(BigDecimal.valueOf(Integer.MIN_VALUE)) < 0) return Integer.MIN_VALUE;
    return whole.intValue();
  }

  private static int positive(Object value) {
    int n = rounded(num(value));
    if (n < 1) throw outOfRange(n);
    return n;
  }

  private static int subexpression(Object value) {
    int n = rounded(num(value));
    if (n < 0 || n > 9) throw outOfRange(n);
    return n;
  }

  /** ORA-01428, as 26ai words it for REGEXP_SUBSTR('abc', 'b', 0). */
  private static FunctionError outOfRange(int argument) {
    return new FunctionError(-1428, "ORA-01428: Argument " + argument + " is out of range.");
  }

  /** The UTF-16 index of character {@code position} (from 1), or -1 past the end. */
  private static int offset(String text, int position) {
    int count = text.codePointCount(0, text.length());
    if (position > count) return -1;
    return text.offsetByCodePoints(0, position - 1);
  }

  /** Moves {@code m} to the {@code occurrence}-th match at or after character {@code position}. */
  private static boolean nth(java.util.regex.Matcher m, String text, int position, int occurrence) {
    int start = offset(text, position);
    if (start < 0) return false;
    m.region(start, text.length());
    m.useTransparentBounds(true).useAnchoringBounds(false);
    for (int k = 0; k < occurrence; k++) {
      if (!m.find()) return false;
    }
    return true;
  }

  /**
   * `SELECT a, b BULK COLLECT INTO va, vb` (#66): column `index` of every row, as a nested table of `type` -- a
   * NUMBER column read as BigDecimal, an INTEGER one as Integer. No row is an empty collection, not NULL.
   */
  /** A plan's rows ({@code List<List<Object>>}) as the arrays a direct read hands back (#83). */
  public static java.util.List<Object[]> arrays(java.util.List<?> rows) {
    java.util.List<Object[]> out = new java.util.ArrayList<>(rows.size());
    for (Object row : rows) out.add(row instanceof Object[] a ? a : ((java.util.List<?>) row).toArray());
    return out;
  }

  /** BULK COLLECT into an INDEX BY PLS_INTEGER table: the values keyed 1 .. n, as Oracle fills it (#67, #93). */
  public static <T> java.util.Map<Integer, T> indexed(java.util.List<T> values) {
    java.util.TreeMap<Integer, T> out = new java.util.TreeMap<>();
    for (int i = 0; i < values.size(); i++) out.put(i + 1, values.get(i));
    return out;
  }

  public static <T> java.util.List<T> column(java.util.List<Object[]> rows, int index, Class<T> type) {
    java.util.List<T> out = new java.util.ArrayList<>(rows.size());
    for (Object[] row : rows) {
      Object value = row[index];
      if (value == null || type.isInstance(value)) {
        out.add(type.cast(value));
      } else if (type == BigDecimal.class) {
        out.add(type.cast(num(value)));
      } else if (type == Integer.class) {
        out.add(type.cast(toInt(value)));
      } else if (type == Long.class) {
        out.add(type.cast(num(value).longValueExact()));
      } else if (type == String.class) {
        out.add(type.cast(text(value)));
      } else if (type == LocalDateTime.class) {
        out.add(type.cast(castDate(value)));
      } else {
        out.add(type.cast(value));
      }
    }
    return out;
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

  /**
   * {@code x LIKE pattern ESCAPE c} (#140): the escape character makes the {@code %}, {@code _} or itself after it
   * literal. Measured on Oracle 26ai in PL/SQL: a NULL escape makes it UNKNOWN; an escape of other than one
   * character ('' or '##') is ORA-06502; the escape before any other character, or last in the pattern, matches
   * nothing -- LIKE is FALSE and NOT LIKE TRUE (SQL raises ORA-01424 there instead).
   */
  public static boolean like(Object value, Object pattern, Object escape) {
    return Boolean.TRUE.equals(likeEscaped(value, pattern, escape));
  }

  public static boolean notLike(Object value, Object pattern, Object escape) {
    return Boolean.FALSE.equals(likeEscaped(value, pattern, escape));
  }

  private static Boolean likeEscaped(Object value, Object pattern, Object escape) {
    // '' is NULL everywhere else, but not here: `ESCAPE ''` is ORA-06502 and `ESCAPE NULL` UNKNOWN (26ai)
    if (escape != null && text(escape).codePointCount(0, text(escape).length()) != 1) throw new ValueError();
    if (isNull(value) || isNull(pattern) || escape == null) return null;
    int esc = text(escape).codePointAt(0);
    StringBuilder regex = new StringBuilder();
    int[] points = text(pattern).codePoints().toArray();
    for (int k = 0; k < points.length; k++) {
      int c = points[k];
      if (c == esc) {
        if (k + 1 >= points.length || (points[k + 1] != '%' && points[k + 1] != '_' && points[k + 1] != esc)) {
          return Boolean.FALSE;
        }
        regex.append(java.util.regex.Pattern.quote(new String(Character.toChars(points[++k]))));
      } else if (c == '%') {
        regex.append(".*");
      } else if (c == '_') {
        regex.append('.');
      } else {
        regex.append(java.util.regex.Pattern.quote(new String(Character.toChars(c))));
      }
    }
    return java.util.regex.Pattern.compile(regex.toString(), java.util.regex.Pattern.DOTALL).matcher(text(value)).matches();
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
