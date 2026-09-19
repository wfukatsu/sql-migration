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
    return Objects.equals(a, b);
  }

  public static boolean ne(Object a, Object b) {
    if (isNull(a) || isNull(b)) return false;  // NULL <> x is unknown, not true
    return !eq(a, b);
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
      return OracleNumbers.toBigDecimal(a).compareTo(OracleNumbers.toBigDecimal(b));
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
    if (value instanceof BigDecimal d) return d.stripTrailingZeros().toPlainString();
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
    return isNull(value) ? BigDecimal.valueOf(fallback) : OracleNumbers.toBigDecimal(value);
  }

  /** Oracle's ROUND is half-up; BigDecimal's default is half-even. */
  public static BigDecimal round(Object value, int scale) {
    return value == null ? null : OracleNumbers.round(OracleNumbers.toBigDecimal(value), scale);
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
        : value instanceof Number n ? OracleNumbers.toBigDecimal(n) : null;
    if (decimal == null) {
      throw new IllegalArgumentException("単項マイナスを数値でない値に適用した: " + value.getClass());
    }
    return decimal.negate();
  }

  public static Object sub(Object a, Object b) {
    if (isTemporal(a) && isTemporal(b)) {
      // Oracle subtracts two DATEs into a number of days, fraction included
      return BigDecimal.valueOf(java.time.Duration.between(castDate(b), castDate(a)).toSeconds())
          .divide(BigDecimal.valueOf(86400), OracleNumbers.NUMBER);
    }
    // `SYSTIMESTAMP - 30` は 30 日前の DATE である。日数として数値に直すと落ちる
    if (isTemporal(a) && b instanceof Number days) return shiftDays(a, days, -1);
    return arith(a, b, BigDecimal::subtract);
  }

  public static BigDecimal mul(Object a, Object b) {
    return arith(a, b, OracleNumbers::multiply);
  }

  public static BigDecimal div(Object a, Object b) {
    return arith(a, b, OracleNumbers::divide);
  }

  private static BigDecimal arith(Object a, Object b,
      java.util.function.BinaryOperator<BigDecimal> operator) {
    if (a == null || b == null) return null;
    return operator.apply(OracleNumbers.toBigDecimal(a), OracleNumbers.toBigDecimal(b));
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
    if (value instanceof LocalDateTime d) return d.format(java.time.format.DateTimeFormatter.ofPattern(pattern));
    return text(value);
  }

  /** Coerce to Oracle's NUMBER. Generated code uses it wherever a literal or a ternary lands in a NUMBER. */
  public static BigDecimal dec(Object value) {
    return isNull(value) ? null : OracleNumbers.toBigDecimal(value);
  }

  public static BigDecimal number(long value) {
    return BigDecimal.valueOf(value);
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
        : value instanceof Number n ? OracleNumbers.toBigDecimal(n) : null;
    if (decimal == null) return value;  // TEXT, DATE, TIMESTAMPTZ and the like pass through untouched
    switch (type) {
      case "BIGINT":
        return scaled(decimal, scale).longValueExact();
      case "INT":
        return scaled(decimal, scale).intValueExact();
      case "DOUBLE":
        return decimal.doubleValue();
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
    if (value instanceof Number n) return OracleNumbers.toBigDecimal(n);
    return OracleNumbers.toBigDecimal(value);
  }

  /**
   * The database clock, which is not the JVM clock.
   *
   * <p>Oracle's SYSDATE comes from the server and its time zone. Generated code calls this so the difference is
   * visible and a test can pin it; leaving {@code LocalDateTime.now()} inline would make it neither.
   */
  public static LocalDateTime sysdate() {
    return CLOCK.get();
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
    return sysdate().atOffset(java.time.ZoneOffset.UTC);
  }

  /** Pin the clock, for a test or for a run that has to be reproducible. */
  public static void setClock(java.util.function.Supplier<LocalDateTime> clock) {
    CLOCK = clock;
  }

  private static java.util.function.Supplier<LocalDateTime> CLOCK = LocalDateTime::now;

  /**
   * Oracle's RTRIM / LTRIM, including on the empty string.
   *
   * <p>Two places `''` bites, both found by replaying the recorded Oracle answers (P3-3). `RTRIM('')` is NULL
   * because `''` already is one, which testing `value == null` misses. And `RTRIM(' ')` is NULL as well: the
   * result is the empty string, and an empty string in Oracle is NULL however it was arrived at. {@link
   * #concat} has always had the second rule; these did not.
   */
  public static String rtrim(Object value) {
    return isNull(value) ? null : emptyIsNull(text(value).stripTrailing());
  }

  public static String ltrim(Object value) {
    return isNull(value) ? null : emptyIsNull(text(value).stripLeading());
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
    BigDecimal divisor = OracleNumbers.toBigDecimal(b);
    if (divisor.signum() == 0) return OracleNumbers.toBigDecimal(a);  // Oracle's MOD by zero returns the value
    return OracleNumbers.toBigDecimal(a).remainder(divisor);
  }

  public static BigDecimal abs(Object value) {
    return isNull(value) ? null : OracleNumbers.toBigDecimal(value).abs();
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

  /** Oracle's {@code LIKE}: {@code %} is any run of characters, {@code _} is exactly one. */
  public static boolean like(Object value, String pattern) {
    if (isNull(value) || pattern == null) return false;
    StringBuilder regex = new StringBuilder();
    for (char c : pattern.toCharArray()) {
      if (c == '%') regex.append(".*");
      else if (c == '_') regex.append('.');
      else regex.append(java.util.regex.Pattern.quote(String.valueOf(c)));
    }
    return text(value).matches(regex.toString());
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
   * <p>`n` が 0 以下なら塊を 1 つも返さない。Oracle で `LIMIT 0` を指定した FETCH が 0 件を返し、
   * ループがすぐ抜けるのと同じ結果になる。
   */
  public static <T> java.util.List<java.util.List<T>> chunks(java.util.List<T> rows, Object size) {
    int n = size instanceof Number number ? number.intValue() : 0;
    java.util.List<java.util.List<T>> out = new java.util.ArrayList<>();
    if (rows == null || n <= 0) return out;
    for (int at = 0; at < rows.size(); at += n) {
      out.add(java.util.List.copyOf(rows.subList(at, Math.min(at + n, rows.size()))));
    }
    return out;
  }
}
