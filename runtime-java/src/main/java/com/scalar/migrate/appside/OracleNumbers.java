package com.scalar.migrate.appside;

import java.math.BigDecimal;
import java.math.BigInteger;
import java.math.MathContext;
import java.math.RoundingMode;

/** Oracle NUMBER arithmetic on BigDecimal. Every function propagates NULL (null in, null out). */
public final class OracleNumbers {
  private OracleNumbers() {}

  /** 38 significant digits: the precision a NUMBER column declares at most. Arithmetic keeps more (see divide). */
  public static final MathContext NUMBER = new MathContext(38, RoundingMode.HALF_UP);

  /** ROUND(n, scale): half away from zero (-2.5 -> -3). A negative scale rounds left of the decimal point. */
  public static BigDecimal round(BigDecimal n, int scale) {
    return n == null ? null : n.setScale(scale, RoundingMode.HALF_UP);
  }

  /**
   * a / b rounded the way Oracle stores a NUMBER: a mantissa of 20 base-100 digits, the pairs aligned on the
   * decimal point. That is 40 significant digits when the leading pair is full (1/3 = .3333…3, forty 3s) and 39
   * when it holds one digit (10/3 = 3.333…3, 1/30 = .0333…3). Measured with DUMP on Oracle 26ai; 38 digits made
   * the printed 1/3 two digits short (samples/oracle-plsql-docs 11-21, #64).
   */
  public static BigDecimal divide(BigDecimal a, BigDecimal b) {
    if (a == null || b == null) return null;
    if (b.signum() == 0) throw new ArithmeticException("ORA-01476: divisor is equal to zero");
    if (a.signum() == 0) return BigDecimal.ZERO;
    int leading = leadingPower(a.divide(b, new MathContext(12, RoundingMode.DOWN)));
    BigDecimal q = a.divide(b, scaleFor(leading), RoundingMode.HALF_UP);
    if (leadingPower(q) != leading) {
      // rounding carried into the next power (9.99…5 -> 10): the pairs line up differently there
      q = a.divide(b, scaleFor(leadingPower(q)), RoundingMode.HALF_UP);
    }
    q = q.stripTrailingZeros();
    return q.scale() < 0 ? q.setScale(0) : q;
  }

  private static int leadingPower(BigDecimal n) {
    return n.precision() - n.scale() - 1;
  }

  /** The scale that keeps 40 digits from an odd leading power (a full pair), 39 from an even one. */
  private static int scaleFor(int leadingPower) {
    int digits = Math.floorMod(leadingPower, 2) == 1 ? 40 : 39;
    return digits - 1 - leadingPower;
  }

  /** a * b (exact). */
  public static BigDecimal multiply(BigDecimal a, BigDecimal b) {
    return a == null || b == null ? null : a.multiply(b);
  }

  /** SUM: NULLs are ignored; all NULL (or no rows) gives NULL. */
  public static BigDecimal sum(Iterable<? extends BigDecimal> values) {
    BigDecimal s = null;
    for (BigDecimal v : values) {
      if (v != null) s = s == null ? v : s.add(v);
    }
    return s;
  }

  /** AVG: NULLs are ignored (neither summed nor counted); all NULL (or no rows) gives NULL. 38 significant digits. */
  public static BigDecimal avg(Iterable<? extends BigDecimal> values) {
    BigDecimal s = null;
    long n = 0;
    for (BigDecimal v : values) {
      if (v != null) {
        s = s == null ? v : s.add(v);
        n++;
      }
    }
    return n == 0 ? null : divide(s, BigDecimal.valueOf(n));
  }

  /** Any Number (or numeric string) as BigDecimal; Double/Float via their shortest decimal representation. */
  public static BigDecimal toBigDecimal(Object v) {
    if (v == null) return null;
    if (v instanceof BigDecimal) return (BigDecimal) v;
    if (v instanceof BigInteger) return new BigDecimal((BigInteger) v);
    if (v instanceof Long || v instanceof Integer || v instanceof Short || v instanceof Byte) {
      return BigDecimal.valueOf(((Number) v).longValue());
    }
    if (v instanceof Double || v instanceof Float) return new BigDecimal(v.toString());
    return new BigDecimal(v.toString().trim());
  }
}
