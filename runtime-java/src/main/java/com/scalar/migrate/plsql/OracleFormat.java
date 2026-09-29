package com.scalar.migrate.plsql;

import java.math.BigDecimal;
import java.math.BigInteger;
import java.math.RoundingMode;
import java.time.LocalDateTime;
import java.time.OffsetDateTime;
import java.time.temporal.IsoFields;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Oracle's format models for TO_CHAR and TO_NUMBER, and the NLS-dependent text of the implicit conversions (#157).
 *
 * <p>Every behaviour here was measured on Oracle 26ai (23.26.3) in PL/SQL and is replayed by {@code
 * OracleFormatTest} from {@code fixtures/plsql/formats.json} ({@code difftest/plsql_formats.py} records it). A model
 * Oracle refuses raises the error Oracle raises (ORA-01481 for a number model, ORA-01821 / ORA-01801 / ORA-01822 for
 * a date model). What this class does not implement is refused by name with {@link UnsupportedOperationException} --
 * never an answer that merely looks right:
 *
 * <ul>
 *   <li>a date before 1582-10-15, where Oracle counts days in the Julian calendar and java.time does not;
 *   <li>FF or X on a date-time the generated code could not type as a DATE or a TIMESTAMP (a DATE refuses them with
 *       ORA-01821, a TIMESTAMP writes them) when it carries no fraction that would tell;
 *   <li>B together with FM, and a number model with no digit and no decimal point -- shapes nobody measured.
 * </ul>
 *
 * <p>Widths are counted in bytes of the database character set, AL32UTF8 (Oracle's default since 12.2), as Oracle
 * counts them: L takes 10 bytes, so '¥' (two bytes) is padded one character less than '$'. A source database in
 * another character set pads multi-byte symbols and names differently.
 */
public final class OracleFormat {
  private OracleFormat() {}

  // --- the errors Oracle raises for a model it does not accept ---------------------------------------------------

  static Plsql.FunctionError invalidNumberModel() {
    return new Plsql.FunctionError(-1481, "ORA-01481: invalid number format model");
  }

  static Plsql.FunctionError dateFormatNotRecognized() {
    return new Plsql.FunctionError(-1821, "ORA-01821: date format not recognized");
  }

  static Plsql.FunctionError dateFormatTooLong() {
    return new Plsql.FunctionError(-1801, "ORA-01801: date format is too long for internal buffer");
  }

  static Plsql.FunctionError eraNotValid() {
    return new Plsql.FunctionError(-1822, "ORA-01822: era format code is not valid with this calendar");
  }

  private static UnsupportedOperationException unsupported(String what) {
    return new UnsupportedOperationException(what + " (#157: not implemented by the runtime; decide it or rewrite the format)");
  }

  // =================================================================================================================
  // numbers
  // =================================================================================================================

  private enum NumberKind { DIGITS, HEX, ROMAN, TM, TME }

  /** A number format model, parsed once. */
  private static final class NumberModel {
    NumberKind kind = NumberKind.DIGITS;
    boolean fm;
    boolean lower;                  // xx, rn
    boolean zeroPadHex;
    int positions;                  // HEX: the number of places
    // DIGITS
    final List<Character> integer = new ArrayList<>();   // '9', '0', ',' (literal group), 'G'
    int fraction;                   // digits after the decimal character
    int lastFractionZero;           // FM keeps fraction digits up to the last '0' element
    char decimalElement;            // 0, '.', 'D', 'V', or 'L' / 'C' / 'U' written between digits
    boolean dollar;
    char currency;                  // 0 or 'L' / 'C' / 'U'
    boolean currencyLeading;
    boolean blank;                  // B
    boolean eeee;
    char sign;                      // 0, 'S' (leading), 's' (trailing), 'M' (MI), 'P' (PR)

    int integerDigits() {
      return (int) integer.stream().filter(c -> c == '9' || c == '0').count();
    }
  }

  private static final ConcurrentHashMap<String, NumberModel> NUMBER_MODELS = new ConcurrentHashMap<>();

  private static NumberModel numberModel(String format) {
    NumberModel cached = NUMBER_MODELS.get(format);
    if (cached != null) return cached;
    NumberModel model = parseNumberModel(format);
    if (NUMBER_MODELS.size() < 10_000) NUMBER_MODELS.put(format, model);
    return model;
  }

  private static NumberModel parseNumberModel(String format) {
    if (format.length() > 63) throw invalidNumberModel();   // 63 nines read, 64 are ORA-01481 (26ai)
    NumberModel m = new NumberModel();
    String f = format;
    if (f.regionMatches(true, 0, "FM", 0, 2)) {
      m.fm = true;
      f = f.substring(2);
    }
    String upper = f.toUpperCase(Locale.ROOT);
    if (upper.startsWith("TM")) {
      switch (upper) {
        case "TM", "TM9" -> m.kind = NumberKind.TM;
        case "TME" -> m.kind = NumberKind.TME;
        default -> throw invalidNumberModel();
      }
      return m;
    }
    if (upper.equals("RN")) {
      m.kind = NumberKind.ROMAN;
      m.lower = f.charAt(0) == 'r';
      return m;
    }
    if (upper.indexOf('X') >= 0) {
      if (!upper.matches("[09]*X+")) throw invalidNumberModel();
      m.kind = NumberKind.HEX;
      m.lower = f.indexOf('x') >= 0;
      m.zeroPadHex = upper.indexOf('0') >= 0;
      m.positions = upper.length();
      return m;
    }
    boolean afterDecimal = false;
    boolean digits = false;
    int i = 0;
    while (i < upper.length()) {
      char c = upper.charAt(i);
      boolean last = i == upper.length() - 1;
      if (m.sign == 's' || m.sign == 'M' || m.sign == 'P') throw invalidNumberModel();   // a trailing sign ends it
      switch (c) {
        case '9', '0' -> {
          if (m.eeee || (m.currency != 0 && !m.currencyLeading && m.decimalElement != m.currency)) {
            throw invalidNumberModel();
          }
          if (afterDecimal) {
            m.fraction++;
            if (c == '0') m.lastFractionZero = m.fraction;
          } else {
            m.integer.add(c);
          }
          digits = true;
        }
        case ',', 'G' -> {
          if (!digits || afterDecimal || m.eeee) throw invalidNumberModel();
          char other = c == ',' ? 'G' : ',';
          if (m.integer.contains(other)) throw invalidNumberModel();
          m.integer.add(c);
        }
        case '.', 'D', 'V' -> {
          if (m.decimalElement != 0 || m.eeee) throw invalidNumberModel();
          m.decimalElement = c;
          afterDecimal = true;
        }
        case '$' -> {
          if (m.dollar || m.currency != 0) throw invalidNumberModel();
          m.dollar = true;
        }
        case 'L', 'C', 'U' -> {
          if (m.currency != 0 || m.dollar) throw invalidNumberModel();
          m.currency = c;
          if (!digits && !afterDecimal) {
            m.currencyLeading = true;
          } else if (!afterDecimal && !lastOrSign(upper, i + 1)) {
            m.decimalElement = c;   // between digits it stands where the decimal character would: 99L99 is 12$00
            afterDecimal = true;
          } else if (!lastOrSign(upper, i + 1)) {
            throw invalidNumberModel();
          }
        }
        case 'B' -> {
          if (m.blank) throw invalidNumberModel();
          if (!last && "LCUS".indexOf(upper.charAt(i + 1)) >= 0) throw invalidNumberModel();
          m.blank = true;
        }
        case 'S' -> {
          if (m.sign != 0) throw invalidNumberModel();
          if (i == 0) {
            m.sign = 'S';
          } else if (last) {
            m.sign = 's';
          } else {
            throw invalidNumberModel();
          }
        }
        case 'M', 'P' -> {
          String two = upper.substring(i, Math.min(i + 2, upper.length()));
          if (!(two.equals("MI") || two.equals("PR")) || i + 2 != upper.length() || m.sign != 0) {
            throw invalidNumberModel();
          }
          m.sign = c;
          i++;
        }
        case 'E' -> {
          if (!upper.startsWith("EEEE", i) || m.eeee || m.integerDigits() == 0 || m.decimalElement == 'V'
              || m.integer.contains(',') || m.integer.contains('G')) {
            throw invalidNumberModel();
          }
          m.eeee = true;
          i += 3;
        }
        default -> throw invalidNumberModel();
      }
      i++;
    }
    if (m.eeee && m.currency != 0 && !m.currencyLeading) throw invalidNumberModel();
    if (!digits && m.decimalElement == 0) {
      throw unsupported("TO_CHAR number format '" + format + "' has no digit");
    }
    if (m.blank && m.fm) throw unsupported("TO_CHAR number format '" + format + "': B with FM");
    return m;
  }

  /** Whether only a trailing sign (S, MI, PR) follows position {@code from}. */
  private static boolean lastOrSign(String upper, int from) {
    String rest = upper.substring(from);
    return rest.isEmpty() || rest.equals("S") || rest.equals("MI") || rest.equals("PR");
  }

  /** TO_CHAR(number, format). NULL for a NULL number or format. */
  public static String number(BigDecimal value, String format, Nls nls) {
    if (value == null || format == null || format.isEmpty()) return null;
    NumberModel m = numberModel(format);
    return switch (m.kind) {
      case TM -> {
        String text = plain(value, nls);
        yield text.length() > 64 ? scientific(value, nls) : text;
      }
      case TME -> scientific(value, nls);
      case ROMAN -> roman(value, m);
      case HEX -> hex(value, m);
      default -> digits(value, m, nls);
    };
  }

  /**
   * The text of a NUMBER without a format, as PL/SQL writes it (`v := n`, `'x' || n`, `TO_CHAR(n)` in PL/SQL): no zero
   * before the point, the session's decimal character, and fixed notation while that takes at most 100 characters.
   * Longer, it is scientific notation zero-padded to exactly 100 characters (#161). Measured on Oracle 26ai
   * (23.26.3), 2026-09-30: 10^99 is 100 digits, 10^100 is {@code 1.000…000E+100} (93 zeros), -10^98 is fixed and
   * -10^99 is {@code -1.00…0E+99}, 10^-99 is {@code .000…01} (100 characters) and 10^-100 is {@code 1.0…0E-100}; with
   * NLS_NUMERIC_CHARACTERS ',.' the mantissa is {@code 1,000…}. SQL's TO_CHAR(n) is another rule (40 characters,
   * rounded: {@code 1.000000000000000000000000000000000E+100}); the runtime writes PL/SQL's.
   */
  public static String minimal(BigDecimal value, Nls nls) {
    String text = plain(value, nls);
    return text.length() <= PLAIN_WIDTH ? text : padded(value, nls);
  }

  private static final int PLAIN_WIDTH = 100;

  /** Fixed notation with no zero before the point: 0.5 is '.5', -0.5 is '-.5'. */
  private static String plain(BigDecimal value, Nls nls) {
    String plain = value.stripTrailingZeros().toPlainString();
    if (plain.startsWith("0.")) plain = plain.substring(1);
    else if (plain.startsWith("-0.")) plain = "-" + plain.substring(2);
    return nls.decimal() == '.' ? plain : plain.replace('.', nls.decimal());
  }

  /** Scientific notation whose mantissa is zero-padded to make the whole text {@link #PLAIN_WIDTH} characters. */
  private static String padded(BigDecimal value, Nls nls) {
    BigDecimal stripped = value.stripTrailingZeros();
    String unscaled = stripped.unscaledValue().abs().toString();
    int exponent = unscaled.length() - 1 - stripped.scale();
    String tail = exponent(exponent);
    String sign = value.signum() < 0 ? "-" : "";
    int fraction = PLAIN_WIDTH - sign.length() - 2 - tail.length();   // "d" + point + fraction digits + E±nn
    String digits = unscaled.substring(1);
    if (digits.length() < fraction) digits = digits + "0".repeat(fraction - digits.length());
    return sign + unscaled.charAt(0) + nls.decimal() + digits + tail;
  }

  /** TME: the fewest digits the value needs, 1.2345E+03, 0E+00. */
  private static String scientific(BigDecimal value, Nls nls) {
    if (value.signum() == 0) return "0E+00";
    BigDecimal stripped = value.stripTrailingZeros();
    String unscaled = stripped.unscaledValue().abs().toString();
    int exponent = unscaled.length() - 1 - stripped.scale();
    String mantissa = unscaled.length() > 1 ? unscaled.charAt(0) + String.valueOf(nls.decimal()) + unscaled.substring(1)
        : unscaled;
    return (value.signum() < 0 ? "-" : "") + mantissa + exponent(exponent);
  }

  private static String exponent(int exponent) {
    return "E" + (exponent < 0 ? "-" : "+") + (Math.abs(exponent) < 10 ? "0" : "") + Math.abs(exponent);
  }

  private static String roman(BigDecimal value, NumberModel m) {
    BigDecimal rounded = value.setScale(0, RoundingMode.HALF_UP);
    String text;
    if (rounded.signum() <= 0 || rounded.compareTo(BigDecimal.valueOf(3999)) > 0) {
      text = "#".repeat(15);
    } else {
      text = romanNumeral(rounded.intValue());
      if (m.lower) text = text.toLowerCase(Locale.ROOT);
    }
    return m.fm || text.startsWith("#") ? text : pad(text, 15);
  }

  static String romanNumeral(int n) {
    int[] values = {1000, 900, 500, 400, 100, 90, 50, 40, 10, 9, 5, 4, 1};
    String[] symbols = {"M", "CM", "D", "CD", "C", "XC", "L", "XL", "X", "IX", "V", "IV", "I"};
    StringBuilder out = new StringBuilder();
    for (int i = 0; i < values.length; i++) {
      while (n >= values[i]) {
        out.append(symbols[i]);
        n -= values[i];
      }
    }
    return out.toString();
  }

  private static String hex(BigDecimal value, NumberModel m) {
    BigInteger rounded = value.setScale(0, RoundingMode.HALF_UP).toBigInteger();
    int width = m.positions + 1;
    if (rounded.signum() < 0 || rounded.bitLength() > 4 * m.positions) return "#".repeat(width);
    String text = rounded.toString(16);
    text = m.lower ? text : text.toUpperCase(Locale.ROOT);
    if (m.zeroPadHex) text = "0".repeat(m.positions - text.length()) + text;
    return m.fm ? text : pad(text, width);
  }

  private static String digits(BigDecimal value, NumberModel m, Nls nls) {
    boolean negative = value.signum() < 0;
    int integerDigits = m.integerDigits();
    int width = width(m, nls);
    String body;
    boolean zero;
    if (m.eeee) {
      // the mantissa has one digit before the point whatever the model says; the exponent takes up to three digits
      int fraction = m.fraction;
      BigDecimal abs = value.abs();
      int exp = 0;
      BigDecimal mantissa = BigDecimal.ZERO;
      if (abs.signum() != 0) {
        exp = abs.precision() - abs.scale() - 1;
        mantissa = abs.movePointLeft(exp).setScale(fraction, RoundingMode.HALF_UP);
        if (mantissa.compareTo(BigDecimal.TEN) >= 0) {
          exp++;
          mantissa = abs.movePointLeft(exp).setScale(fraction, RoundingMode.HALF_UP);
        }
      }
      zero = mantissa.signum() == 0;
      String digitsText = mantissa.unscaledValue().toString();
      digitsText = "0".repeat(Math.max(0, fraction + 1 - digitsText.length())) + digitsText;
      String intText = digitsText.substring(0, digitsText.length() - fraction);
      String fracText = digitsText.substring(digitsText.length() - fraction);
      if (m.fm) fracText = trimFraction(fracText, m.lastFractionZero);
      boolean forced = m.integer.contains('0');
      if (intText.equals("0") && !forced && !(fracText.isEmpty())) intText = "";
      body = intText + (m.decimalElement != 0 ? decimalText(m, nls) : "") + fracText + exponent(exp);
    } else {
      int fraction = m.decimalElement == 'V' ? 0 : m.fraction;
      BigDecimal scaled = m.decimalElement == 'V' ? value.abs().movePointRight(m.fraction) : value.abs();
      BigDecimal rounded = scaled.setScale(fraction, RoundingMode.HALF_UP);
      zero = rounded.signum() == 0;
      String all = rounded.unscaledValue().toString();
      int tail = m.decimalElement == 'V' ? m.fraction : fraction;
      all = "0".repeat(Math.max(0, tail + 1 - all.length())) + all;
      String intText = all.substring(0, all.length() - tail);
      String fracText = all.substring(all.length() - tail);
      if (intText.equals("0")) intText = "";
      if (intText.length() > integerDigits) return "#".repeat(width);
      if (m.fm && m.decimalElement != 'V') fracText = trimFraction(fracText, m.lastFractionZero);
      int forced = forcedIntegerDigits(m);
      if (intText.length() < forced) intText = "0".repeat(forced - intText.length()) + intText;
      if (intText.isEmpty() && integerDigits > 0 && fracText.isEmpty()) intText = "0";
      body = group(intText, m, nls) + (m.decimalElement != 0 ? decimalText(m, nls) : "") + fracText;
    }
    if (m.blank && zero) return " ".repeat(width);
    String currency = m.currency == 0 ? "" : currencyText(m.currency, nls);
    StringBuilder out = new StringBuilder();
    switch (m.sign) {
      case 'S' -> out.append(negative ? '-' : '+');
      case 'P' -> out.append(negative ? "<" : (m.fm ? "" : " "));
      case 0 -> {
        if (negative) out.append('-');
      }
      default -> { }
    }
    if (m.dollar) out.append('$');
    if (m.currencyLeading) out.append(currency);
    out.append(body);
    if (m.currency != 0 && !m.currencyLeading && m.decimalElement != m.currency) out.append(currency);
    switch (m.sign) {
      case 's' -> out.append(negative ? '-' : '+');
      case 'M' -> out.append(negative ? "-" : (m.fm ? "" : " "));
      case 'P' -> out.append(negative ? ">" : (m.fm ? "" : " "));
      default -> { }
    }
    String text = out.toString();
    return m.fm ? text : pad(text, width);
  }

  /** The width Oracle gives the result: one place per element, the sign's place, 10 for L and U, 7 for C. */
  private static int width(NumberModel m, Nls nls) {
    int width = switch (m.sign) {
      case 'P' -> 2;
      default -> 1;   // the sign's place, or S / MI itself
    };
    if (m.eeee) {
      width += 1 + (m.decimalElement != 0 ? 1 : 0) + m.fraction + 5;
    } else {
      width += m.integer.size() + m.fraction + (m.decimalElement == '.' || m.decimalElement == 'D' ? 1 : 0);
    }
    if (m.dollar) width += 1;
    if (m.currency != 0) width += m.currency == 'C' ? 7 : 10;
    return width;
  }

  private static String decimalText(NumberModel m, Nls nls) {
    return switch (m.decimalElement) {
      case '.' -> ".";
      case 'D' -> String.valueOf(nls.decimal());
      case 'V' -> "";
      default -> currencyText(m.decimalElement, nls);
    };
  }

  private static String currencyText(char element, Nls nls) {
    return switch (element) {
      case 'L' -> nls.currency();
      case 'C' -> nls.isoCurrency();
      default -> nls.dualCurrency();
    };
  }

  /** FM drops the fraction's trailing zeros, but not past the last 0 element: FM9.090 writes 1.500. */
  private static String trimFraction(String fraction, int keep) {
    int end = fraction.length();
    while (end > keep && fraction.charAt(end - 1) == '0') end--;
    return fraction.substring(0, end);
  }

  /** The integer places a 0 element forces: from the leftmost 0 to the point (909 writes 5 as 05). */
  private static int forcedIntegerDigits(NumberModel m) {
    int seen = 0;
    int total = m.integerDigits();
    for (char c : m.integer) {
      if (c == '0') return total - seen;
      if (c == '9') seen++;
    }
    return 0;
  }

  /** The integer digits with the group separators the model puts between them, only between written digits. */
  private static String group(String intText, NumberModel m, Nls nls) {
    int total = m.integerDigits();
    int skip = total - intText.length();
    StringBuilder out = new StringBuilder();
    int position = 0;
    int written = 0;
    for (char c : m.integer) {
      if (c == '9' || c == '0') {
        if (position >= skip) out.append(intText.charAt(written++));
        position++;
      } else if (position > skip) {
        out.append(c == ',' ? ',' : nls.group());
      }
    }
    return out.toString();
  }

  private static String pad(String text, int width) {
    int length = bytes(text);
    return length >= width ? text : " ".repeat(width - length) + text;
  }

  /** The length Oracle pads to: bytes in AL32UTF8. */
  static int bytes(String text) {
    return text.getBytes(java.nio.charset.StandardCharsets.UTF_8).length;
  }

  // --- TO_NUMBER(text, format) ---------------------------------------------------------------------------------

  /**
   * TO_NUMBER(text, format) in PL/SQL. Text the model does not describe is ORA-06502 (VALUE_ERROR), as in a PL/SQL
   * expression; a model Oracle refuses is ORA-01481. Measured on 26ai: a 0 element needs its digit (TO_NUMBER('12',
   * '0000') fails), a group separator must stand where the model puts it ('1234' does not read with 9G999), fewer
   * fraction digits are fine and more are not, leading blanks are skipped and trailing ones are not, and RN, TM and V
   * do not read at all.
   */
  public static BigDecimal parseNumber(String text, String format, Nls nls) {
    if (text == null || text.isEmpty() || format == null || format.isEmpty()) return null;
    NumberModel m = numberModel(format);
    String s = text;
    int start = 0;
    while (start < s.length() && s.charAt(start) == ' ') start++;
    s = s.substring(start);
    if (s.isEmpty()) throw new Plsql.ValueError();
    switch (m.kind) {
      case HEX -> {
        if (s.length() > m.positions || !s.matches("[0-9A-Fa-f]+")) throw new Plsql.ValueError();
        return new BigDecimal(new BigInteger(s, 16));
      }
      case DIGITS -> { }
      default -> throw new Plsql.ValueError();
    }
    if (m.decimalElement == 'V') throw new Plsql.ValueError();
    if (m.currency != 0 && m.decimalElement == m.currency) {
      throw unsupported("TO_NUMBER format '" + format + "': a currency element between digits");
    }
    boolean negative = false;
    // the sign
    switch (m.sign) {
      case 'S' -> {
        if (s.startsWith("-") || s.startsWith("+")) {
          negative = s.charAt(0) == '-';
          s = s.substring(1);
        } else {
          throw new Plsql.ValueError();
        }
      }
      case 's' -> {
        if (s.endsWith("-") || s.endsWith("+")) {
          negative = s.endsWith("-");
          s = s.substring(0, s.length() - 1);
        } else {
          throw new Plsql.ValueError();
        }
      }
      case 'M' -> {
        // MI's place holds '-', or the blank TO_CHAR writes for a positive number ('12 ' reads as 12, measured)
        if (s.endsWith("-") || s.endsWith("+") || s.endsWith(" ")) {
          negative = s.endsWith("-");
          s = s.substring(0, s.length() - 1);
        } else {
          throw new Plsql.ValueError();
        }
      }
      case 'P' -> {
        if (s.startsWith("<")) {
          // the closing '>' may be missing: '<12' reads as -12 (measured)
          negative = true;
          s = s.substring(1, s.endsWith(">") ? s.length() - 1 : s.length());
        }
      }
      default -> {
        if (s.startsWith("-")) {
          negative = true;
          s = s.substring(1);
        }
      }
    }
    // the currency
    if (m.dollar) s = expect(s, "$", true);
    if (m.currency != 0) {
      s = expect(s, currencyText(m.currency, nls), m.currencyLeading);
    }
    BigDecimal result;
    if (m.eeee) {
      result = readScientific(s, m, nls);
    } else {
      char decimal = m.decimalElement == '.' ? '.' : nls.decimal();
      int point = m.decimalElement == 0 ? -1 : s.indexOf(decimal);
      String intPart = point < 0 ? s : s.substring(0, point);
      String fracPart = point < 0 ? "" : s.substring(point + 1);
      // a model without a decimal element refuses the session's decimal character, not '.': with ',.' the '.' of
      // '1.234' is 9G999's group separator and reads as 1234 (#167, measured on 26ai)
      if (point < 0 && m.decimalElement == 0 && s.indexOf(nls.decimal()) >= 0) throw new Plsql.ValueError();
      if (fracPart.length() > m.fraction || !fracPart.chars().allMatch(Character::isDigit)) {
        throw new Plsql.ValueError();
      }
      String digits = readInteger(intPart, m, nls);
      String number = (digits.isEmpty() ? "0" : digits) + (fracPart.isEmpty() ? "" : "." + fracPart);
      result = new BigDecimal(number);
    }
    return negative ? result.negate() : result;
  }

  private static String expect(String s, String symbol, boolean leading) {
    if (leading ? s.startsWith(symbol) : s.endsWith(symbol)) {
      return leading ? s.substring(symbol.length()) : s.substring(0, s.length() - symbol.length());
    }
    throw new Plsql.ValueError();
  }

  /** The integer part, matched from its right against the model's places: skipped places may not be 0 elements. */
  private static String readInteger(String input, NumberModel m, Nls nls) {
    int inputDigits = (int) input.chars().filter(Character::isDigit).count();
    int places = m.integerDigits();
    if (inputDigits > places) throw new Plsql.ValueError();
    int skip = places - inputDigits;
    int at = 0;
    int position = 0;
    StringBuilder digits = new StringBuilder();
    for (char c : m.integer) {
      if (c == '9' || c == '0') {
        if (position++ < skip) {
          if (c == '0') throw new Plsql.ValueError();
          continue;
        }
        if (at >= input.length() || !Character.isDigit(input.charAt(at))) throw new Plsql.ValueError();
        digits.append(input.charAt(at++));
      } else if (position > skip) {
        char separator = c == ',' ? ',' : nls.group();
        if (at >= input.length() || input.charAt(at) != separator) throw new Plsql.ValueError();
        at++;
      }
    }
    if (at != input.length()) throw new Plsql.ValueError();
    return digits.toString();
  }

  private static BigDecimal readScientific(String s, NumberModel m, Nls nls) {
    int e = s.toUpperCase(Locale.ROOT).indexOf('E');
    if (e < 0) throw new Plsql.ValueError();
    String mantissa = s.substring(0, e);
    String exponent = s.substring(e + 1);
    if (!exponent.matches("[+-]?\\d{1,3}")) throw new Plsql.ValueError();
    char decimal = m.decimalElement == '.' ? '.' : nls.decimal();
    int point = mantissa.indexOf(decimal);
    String intPart = point < 0 ? mantissa : mantissa.substring(0, point);
    String fracPart = point < 0 ? "" : mantissa.substring(point + 1);
    if (!intPart.matches("\\d?") || !fracPart.matches("\\d*") || fracPart.length() > m.fraction) {
      throw new Plsql.ValueError();
    }
    BigDecimal base = new BigDecimal((intPart.isEmpty() ? "0" : intPart) + (fracPart.isEmpty() ? "" : "." + fracPart));
    return base.scaleByPowerOfTen(Integer.parseInt(exponent.startsWith("+") ? exponent.substring(1) : exponent))
        .stripTrailingZeros();
  }

  // =================================================================================================================
  // dates
  // =================================================================================================================

  /** What the runtime knows of a date-time's Oracle type: a DATE and a TIMESTAMP are both a LocalDateTime. */
  public enum DateKind { DATE, TIMESTAMP, TIMESTAMP_TZ, UNKNOWN }

  private enum Element {
    SYYYY(true), YYYY(true), YYY(true), YY(true), Y(true), Y_YYY(true), IYYY(true), IYY(true), IY(true), I(true),
    RRRR(true), RR(true), SCC(true), CC(true), Q(true), MM(true), WW(true), W(true), IW(true), DDD(true), DD(true),
    D(true), J(true), HH24(true), HH12(true), HH(true), MI(true), SSSSS(true), SS(true),
    SYEAR(false), YEAR(false), MONTH(false), MON(false), RM(false), DAY(false), DY(false),
    AM(false), AM_DOTS(false), AD(false), AD_DOTS(false), FF(false), X(false),
    TZH(false), TZM(false), TZR(false), TZD(false), FM(false), FX(false), DS(false), DL(false), TS(false),
    LITERAL(false), DROPPED(false);

    final boolean numeric;

    Element(boolean numeric) {
      this.numeric = numeric;
    }
  }

  /** One element of a date model: its kind, how it was written (for the case of names), and TH / SP. */
  private record Token(Element element, String written, String text, int ffDigits, boolean th, boolean sp) {}

  // longest first: the tokenizer takes the first that matches at each place
  private static final String[][] DATE_ELEMENTS = {
      {"SYYYY", "SYYYY"}, {"SYEAR", "SYEAR"}, {"Y,YYY", "Y_YYY"}, {"YYYY", "YYYY"}, {"YEAR", "YEAR"},
      {"YYY", "YYY"}, {"YY", "YY"}, {"Y", "Y"}, {"IYYY", "IYYY"}, {"IYY", "IYY"}, {"IY", "IY"}, {"IW", "IW"},
      {"I", "I"}, {"RRRR", "RRRR"}, {"RR", "RR"}, {"SCC", "SCC"}, {"CC", "CC"}, {"Q", "Q"}, {"MONTH", "MONTH"},
      {"MON", "MON"}, {"MM", "MM"}, {"MI", "MI"}, {"RM", "RM"}, {"WW", "WW"}, {"W", "W"}, {"DDD", "DDD"},
      {"DD", "DD"}, {"DAY", "DAY"}, {"DY", "DY"}, {"DS", "DS"}, {"DL", "DL"}, {"D", "D"}, {"J", "J"},
      {"HH24", "HH24"}, {"HH12", "HH12"}, {"HH", "HH"}, {"SSSSS", "SSSSS"}, {"SS", "SS"},
      {"A.M.", "AM_DOTS"}, {"P.M.", "AM_DOTS"}, {"AM", "AM"}, {"PM", "AM"}, {"A.D.", "AD_DOTS"},
      {"B.C.", "AD_DOTS"}, {"AD", "AD"}, {"BC", "AD"}, {"FF", "FF"}, {"TZH", "TZH"}, {"TZM", "TZM"},
      {"TZR", "TZR"}, {"TZD", "TZD"}, {"TS", "TS"}, {"FM", "FM"}, {"FX", "FX"}, {"X", "X"}};

  private static final ConcurrentHashMap<String, List<Token>> DATE_MODELS = new ConcurrentHashMap<>();

  private static List<Token> dateModel(String format) {
    List<Token> cached = DATE_MODELS.get(format);
    if (cached != null) return cached;
    List<Token> tokens = parseDateModel(format);
    if (DATE_MODELS.size() < 10_000) DATE_MODELS.put(format, tokens);
    return tokens;
  }

  private static List<Token> parseDateModel(String format) {
    List<Token> tokens = new ArrayList<>();
    String upper = format.toUpperCase(Locale.ROOT);
    int cost = 0;
    int i = 0;
    StringBuilder literal = null;
    while (i < format.length()) {
      char c = format.charAt(i);
      if (c == '"') {
        int end = format.indexOf('"', i + 1);
        String text = end < 0 ? format.substring(i + 1) : format.substring(i + 1, end);
        if (literal == null) literal = new StringBuilder();
        literal.append(text);
        i = end < 0 ? format.length() : end + 1;
        continue;
      }
      if (c < 128 && !Character.isLetterOrDigit(c)) {
        // punctuation is written as it is, except '|', which Oracle 26ai leaves out
        if (literal == null) literal = new StringBuilder();
        if (c != '|') literal.append(c);
        i++;
        continue;
      }
      if (literal != null) {
        cost += literal.length() + 2;
        tokens.add(new Token(Element.LITERAL, "", literal.toString(), 0, false, false));
        literal = null;
      }
      if (upper.startsWith("EE", i) || (upper.charAt(i) == 'E')) throw eraNotValid();
      String[] match = null;
      for (String[] candidate : DATE_ELEMENTS) {
        if (upper.startsWith(candidate[0], i)) {
          match = candidate;
          break;
        }
      }
      if (match == null) throw dateFormatNotRecognized();
      Element element = Element.valueOf(match[1]);
      String written = format.substring(i, i + match[0].length());
      i += match[0].length();
      int ffDigits = -1;
      if (element == Element.FF && i < format.length() && format.charAt(i) >= '1' && format.charAt(i) <= '9') {
        ffDigits = format.charAt(i) - '0';
        i++;
      }
      boolean th = false;
      boolean sp = false;
      if (element.numeric) {
        for (String suffix : new String[] {"SPTH", "THSP", "SP", "TH"}) {
          if (upper.startsWith(suffix, i)) {
            th = suffix.contains("TH");
            sp = suffix.contains("SP");
            i += suffix.length();
            break;
          }
        }
      }
      cost += 2;
      tokens.add(new Token(element, written, "", ffDigits, th, sp));
    }
    if (literal != null) {
      cost += literal.length() + 2;
      tokens.add(new Token(Element.LITERAL, "", literal.toString(), 0, false, false));
    }
    // Oracle compiles the model into a buffer: 2 bytes an element, a run of text its length plus 2, and 73 fit (26ai)
    if (cost > 73) throw dateFormatTooLong();
    return tokens;
  }

  /**
   * TO_CHAR(date-time, format). {@code ffDigits} is what a bare FF writes: nine digits in an explicit TO_CHAR in
   * PL/SQL (whatever the variable's precision, measured), the declared precision in the implicit conversion that
   * uses NLS_TIMESTAMP_FORMAT (and nothing at all, radix character included, for TIMESTAMP(0)).
   */
  public static String datetime(Object value, DateKind kind, String format, Nls nls, int ffDigits) {
    if (value == null || format == null || format.isEmpty()) return null;
    LocalDateTime t;
    java.time.ZoneOffset offset = null;
    if (value instanceof OffsetDateTime o) {
      t = o.toLocalDateTime();
      offset = o.getOffset();
      kind = DateKind.TIMESTAMP_TZ;
    } else if (value instanceof LocalDateTime l) {
      t = l;
    } else if (value instanceof java.time.LocalDate d) {
      t = d.atStartOfDay();
    } else if (value instanceof java.time.Instant instant) {
      t = LocalDateTime.ofInstant(instant, java.time.ZoneOffset.UTC);
      offset = java.time.ZoneOffset.UTC;
      kind = DateKind.TIMESTAMP_TZ;
    } else {
      throw new IllegalArgumentException("not a date-time: " + value.getClass().getSimpleName());
    }
    List<Token> tokens = dateModel(format);
    for (Token token : tokens) {
      switch (token.element()) {
        case FF, X -> {
          if (kind == DateKind.DATE) throw dateFormatNotRecognized();
          if (kind == DateKind.UNKNOWN) {
            if (t.getNano() == 0) {
              throw unsupported("TO_CHAR format element " + token.written() + " on a value the generator could not "
                  + "type as DATE (ORA-01821) or TIMESTAMP");
            }
            kind = DateKind.TIMESTAMP;
          }
        }
        case TZH, TZM -> {
          if (kind != DateKind.TIMESTAMP_TZ) throw dateFormatNotRecognized();
        }
        case TZR, TZD -> {
          // a TIMESTAMP without a zone writes +00:00 for TZR and nothing for TZD, where TZH / TZM refuse it -- and
          // +00:00 whatever the session's TIME_ZONE: measured in PL/SQL on 26ai with UTC, +09:00, Asia/Tokyo,
          // America/New_York and -05:30 (#167). It is not the session's zone, so nothing here depends on one. A
          // TIMESTAMP WITH TIME ZONE writes its own offset (+09:00, TZD empty); a region (ASIA/TOKYO, TZD JST) never
          // reaches the runtime, which holds an OffsetDateTime
          if (kind == DateKind.DATE) throw dateFormatNotRecognized();
          if (kind == DateKind.UNKNOWN) {
            if (t.getNano() == 0) {
              throw unsupported("TO_CHAR format element " + token.written() + " on a value the generator could not "
                  + "type as DATE (ORA-01821) or TIMESTAMP");
            }
            kind = DateKind.TIMESTAMP;
          }
          if (offset == null) offset = java.time.ZoneOffset.UTC;
        }
        default -> { }
      }
    }
    if (t.isBefore(LocalDateTime.of(1582, 10, 15, 0, 0))) {
      throw unsupported("TO_CHAR of a date before 1582-10-15, which Oracle counts in the Julian calendar");
    }
    StringBuilder out = new StringBuilder();
    render(tokens, t, offset, nls, ffDigits, out);
    return out.length() == 0 ? null : out.toString();   // '' is NULL in Oracle
  }

  private static void render(List<Token> tokens, LocalDateTime t, java.time.ZoneOffset offset, Nls nls, int ffDigits,
      StringBuilder out) {
    boolean fm = false;
    Nls.Names names = nls.names();
    for (int k = 0; k < tokens.size(); k++) {
      Token token = tokens.get(k);
      int year = t.getYear();
      switch (token.element()) {
        case LITERAL -> out.append(token.text());
        case FM -> fm = !fm;
        case FX, DROPPED -> { }
        case DS, DL, TS -> render(dateModel(nls.shortFormat(token.element().name())), t, offset, nls, ffDigits, out);
        case SYYYY -> out.append(year < 0 ? '-' : ' ').append(numeric(token, Math.abs(year), 4, fm));
        case YYYY, RRRR -> out.append(numeric(token, year, 4, fm));
        case YYY -> out.append(numeric(token, year % 1000, 3, fm));
        case YY, RR -> out.append(numeric(token, year % 100, 2, fm));
        case Y -> out.append(numeric(token, year % 10, 1, fm));
        case Y_YYY -> {
          if (token.th() || token.sp()) throw dateFormatNotRecognized();
          String digits = String.format("%04d", year);
          out.append(fm && year < 1000 ? String.valueOf(year) : digits.charAt(0) + "," + digits.substring(1));
        }
        case IYYY -> out.append(numeric(token, isoYear(t), 4, fm));
        case IYY -> out.append(numeric(token, isoYear(t) % 1000, 3, fm));
        case IY -> out.append(numeric(token, isoYear(t) % 100, 2, fm));
        case I -> out.append(numeric(token, isoYear(t) % 10, 1, fm));
        case SCC -> out.append(year < 0 ? '-' : ' ').append(numeric(token, (Math.abs(year) + 99) / 100, 2, fm));
        case CC -> out.append(numeric(token, (year + 99) / 100, 2, fm));
        case Q -> out.append(numeric(token, (t.getMonthValue() + 2) / 3, 1, fm));
        case MM -> out.append(numeric(token, t.getMonthValue(), 2, fm));
        case WW -> out.append(numeric(token, (t.getDayOfYear() - 1) / 7 + 1, 2, fm));
        case W -> out.append(numeric(token, (t.getDayOfMonth() - 1) / 7 + 1, 1, fm));
        case IW -> out.append(numeric(token, t.get(IsoFields.WEEK_OF_WEEK_BASED_YEAR), 2, fm));
        case DDD -> out.append(numeric(token, t.getDayOfYear(), 3, fm));
        case DD -> out.append(numeric(token, t.getDayOfMonth(), 2, fm));
        case D -> out.append(numeric(token, nls.dayOfWeek(t.getDayOfWeek()), 1, fm));
        case J -> out.append(numeric(token, julianDay(t), 7, fm));
        case HH24 -> out.append(numeric(token, t.getHour(), 2, fm));
        case HH12, HH -> out.append(numeric(token, t.getHour() % 12 == 0 ? 12 : t.getHour() % 12, 2, fm));
        case MI -> out.append(numeric(token, t.getMinute(), 2, fm));
        case SS -> out.append(numeric(token, t.getSecond(), 2, fm));
        case SSSSS -> out.append(numeric(token, t.toLocalTime().toSecondOfDay(), 5, fm));
        case SYEAR -> out.append(year < 0 ? '-' : ' ').append(cased(token.written().substring(1), spellYear(year)));
        case YEAR -> out.append(cased(token.written(), spellYear(year)));
        case MONTH -> out.append(name(token, names.months().get(t.getMonthValue() - 1),
            Nls.Names.longest(names.months()), fm, names.cased()));
        case MON -> out.append(name(token, names.shortMonths().get(t.getMonthValue() - 1),
            Nls.Names.longest(names.shortMonths()), fm, names.cased()));
        case DAY -> out.append(name(token, names.days().get(t.getDayOfWeek().getValue() - 1),
            Nls.Names.longest(names.days()), fm, names.cased()));
        case DY -> out.append(name(token, names.shortDays().get(t.getDayOfWeek().getValue() - 1),
            Nls.Names.longest(names.shortDays()), fm, names.cased()));
        case RM -> out.append(name(token, romanNumeral(t.getMonthValue()), 4, fm, true));
        case AM -> out.append(cased(token.written(), t.getHour() < 12 ? names.am() : names.pm(), names.cased()));
        case AM_DOTS -> out.append(cased(token.written(), t.getHour() < 12 ? names.amDots() : names.pmDots(),
            names.cased()));
        case AD -> out.append(cased(token.written(), year > 0 ? names.ad() : names.bc(), names.cased()));
        case AD_DOTS -> out.append(cased(token.written(), year > 0 ? names.adDots() : names.bcDots(), names.cased()));
        case FF -> {
          int digits = token.ffDigits() > 0 ? token.ffDigits() : ffDigits;
          out.append(String.format("%09d", t.getNano()), 0, digits);
        }
        case X -> {
          // TIMESTAMP(0) written with NLS_TIMESTAMP_FORMAT has no radix character, even before FF3 (measured)
          if (ffDigits != 0) out.append(nls.decimal());
        }
        case TZH -> {
          int seconds = offset.getTotalSeconds();
          int hours = Math.abs(seconds) / 3600;
          out.append(seconds < 0 ? '-' : '+').append(fm ? String.valueOf(hours) : String.format("%02d", hours));
        }
        case TZM -> {
          int minutes = Math.abs(offset.getTotalSeconds()) / 60 % 60;
          out.append(fm ? String.valueOf(minutes) : String.format("%02d", minutes));
        }
        case TZR -> {
          int seconds = offset.getTotalSeconds();
          out.append(String.format("%s%02d:%02d", seconds < 0 ? "-" : "+", Math.abs(seconds) / 3600,
              Math.abs(seconds) / 60 % 60));
        }
        case TZD -> { }   // a zone given as an offset has no daylight-saving abbreviation (26ai writes nothing)
        default -> throw new IllegalStateException(token.element().name());
      }
    }
  }

  /** A numeric element: zero-padded to its width unless FM, spelled out with SP, an ordinal suffix with TH. */
  private static String numeric(Token token, long value, int width, boolean fm) {
    if (token.sp()) {
      String spelled = token.th() ? ordinal(spell(value)) : spell(value);
      return cased(token.written(), spelled);
    }
    String digits = fm ? String.valueOf(value) : String.format("%0" + width + "d", value);
    if (!token.th()) return digits;
    String suffix = ordinalSuffix(value);
    return digits + (caseOf(token.written()) == Case.UPPER ? suffix.toUpperCase(Locale.ROOT) : suffix);
  }

  private static String ordinalSuffix(long value) {
    long lastTwo = value % 100;
    if (lastTwo >= 11 && lastTwo <= 13) return "th";
    return switch ((int) (value % 10)) {
      case 1 -> "st";
      case 2 -> "nd";
      case 3 -> "rd";
      default -> "th";
    };
  }

  private static String name(Token token, String name, int width, boolean fm, boolean cased) {
    String text = cased ? cased(token.written(), name) : name;
    int length = bytes(text);
    return fm || length >= width ? text : text + " ".repeat(width - length);
  }

  private enum Case { UPPER, LOWER, INITCAP }

  /** Oracle's rule: a lower-case first letter writes lower case; upper then upper, upper; upper then lower, Initcap. */
  private static Case caseOf(String written) {
    int first = -1;
    int second = -1;
    for (int i = 0; i < written.length(); i++) {
      if (Character.isLetter(written.charAt(i))) {
        if (first < 0) {
          first = i;
        } else {
          second = i;
          break;
        }
      }
    }
    if (first < 0 || Character.isLowerCase(written.charAt(first))) return first < 0 ? Case.UPPER : Case.LOWER;
    if (second < 0 || Character.isUpperCase(written.charAt(second))) return Case.UPPER;
    return Case.INITCAP;
  }

  private static String cased(String written, String upperText) {
    return cased(written, upperText, true);
  }

  private static String cased(String written, String upperText, boolean cased) {
    if (!cased) return upperText;
    return switch (caseOf(written)) {
      case UPPER -> upperText;
      case LOWER -> upperText.toLowerCase(Locale.ROOT);
      default -> {
        StringBuilder out = new StringBuilder(upperText.length());
        boolean start = true;
        for (char c : upperText.toCharArray()) {
          out.append(start ? Character.toUpperCase(c) : Character.toLowerCase(c));
          start = !Character.isLetter(c) && c != '.';
        }
        yield out.toString();
      }
    };
  }

  private static int isoYear(LocalDateTime t) {
    return t.get(IsoFields.WEEK_BASED_YEAR);
  }

  private static long julianDay(LocalDateTime t) {
    return t.toLocalDate().toEpochDay() + 2440588;
  }

  private static final String[] ONES = {"ZERO", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE",
      "TEN", "ELEVEN", "TWELVE", "THIRTEEN", "FOURTEEN", "FIFTEEN", "SIXTEEN", "SEVENTEEN", "EIGHTEEN", "NINETEEN"};
  private static final String[] TENS = {"", "", "TWENTY", "THIRTY", "FORTY", "FIFTY", "SIXTY", "SEVENTY", "EIGHTY",
      "NINETY"};

  /** A number in English words as Oracle's SP writes it: TWO THOUSAND TWENTY-SIX. */
  static String spell(long n) {
    if (n < 20) return ONES[(int) n];
    if (n < 100) return TENS[(int) (n / 10)] + (n % 10 == 0 ? "" : "-" + ONES[(int) (n % 10)]);
    if (n < 1000) return ONES[(int) (n / 100)] + " HUNDRED" + (n % 100 == 0 ? "" : " " + spell(n % 100));
    if (n < 1_000_000) return spell(n / 1000) + " THOUSAND" + (n % 1000 == 0 ? "" : " " + spell(n % 1000));
    return spell(n / 1_000_000) + " MILLION" + (n % 1_000_000 == 0 ? "" : " " + spell(n % 1_000_000));
  }

  /** YEAR: two pairs, TWENTY TWENTY-SIX, except when the last two digits are under ten: TWO THOUSAND SIX. */
  static String spellYear(int year) {
    int y = Math.abs(year);
    if (y % 100 < 10 || y < 1000) return spell(y);
    return spell(y / 100) + " " + spell(y % 100);
  }

  /** The ordinal of a spelled number: TWENTY-EIGHT -> TWENTY-EIGHTH, ONE -> FIRST. */
  static String ordinal(String spelled) {
    int cut = Math.max(spelled.lastIndexOf(' '), spelled.lastIndexOf('-')) + 1;
    String head = spelled.substring(0, cut);
    String word = spelled.substring(cut);
    String ordinal = switch (word) {
      case "ONE" -> "FIRST";
      case "TWO" -> "SECOND";
      case "THREE" -> "THIRD";
      case "FIVE" -> "FIFTH";
      case "EIGHT" -> "EIGHTH";
      case "NINE" -> "NINTH";
      case "TWELVE" -> "TWELFTH";
      default -> word.endsWith("Y") ? word.substring(0, word.length() - 1) + "IETH" : word + "TH";
    };
    return head + ordinal;
  }
}
