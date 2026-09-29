package com.scalar.migrate.plsql;

import java.util.List;
import java.util.Map;

/**
 * The NLS settings of the source database's sessions that change what a conversion writes or reads (#157): the
 * formats TO_CHAR / TO_DATE use when none is given, the language of month and day names, the decimal and group
 * characters, the currency symbols, and the territory's first day of the week and its DS / DL / TS formats.
 *
 * <p>A project decides them in limits.yaml ({@code nls:}); the generated code then hands them to
 * {@link Plsql#useNls(Nls)} once, before any routine runs. Without a decision the runtime writes what Oracle writes
 * with its defaults -- {@link #AMERICAN}, the settings of NLS_LANGUAGE=AMERICAN, NLS_TERRITORY=AMERICA -- and the rules
 * keep the routine at REVIEW (SEM-008 / SEM-012), because nobody said the source sessions ran with those.
 *
 * <p>Only the languages and territories whose names and formats were measured on Oracle are known here: AMERICAN /
 * ENGLISH and JAPANESE, AMERICA and JAPAN (Oracle 26ai, 23.26.3, fixtures/plsql/formats.json). Any other is refused
 * when the settings are made -- the names Java's locales give are not Oracle's (JAPANESE MON is '9月' padded to three
 * characters, not Java's '9月').
 */
public record Nls(
    String dateLanguage,
    String territory,
    String dateFormat,
    String timestampFormat,
    String timestampTzFormat,
    char decimal,
    char group,
    String currency,
    String isoCurrency,
    String dualCurrency) {

  /** The territory's own settings, as ALTER SESSION SET NLS_TERRITORY sets them (measured, 26ai). */
  private record Territory(String dateFormat, String timestampFormat, String timestampTzFormat, String numeric,
      String currency, String isoCurrency, String dualCurrency, int firstDay, String ds, String dl, String ts) {}

  private static final Map<String, Territory> TERRITORIES = Map.of(
      // DS / DL / TS as Oracle writes them: 9/23/2026, Wednesday, September 23, 2026 (DD keeps its zero), 1:04:05 PM
      "AMERICA", new Territory("DD-MON-RR", "DD-MON-RR HH.MI.SSXFF AM", "DD-MON-RR HH.MI.SSXFF AM TZR", ".,", "$",
          "USD", "$", java.util.Calendar.SUNDAY, "fmMM/DD/fmYYYY", "fmDay, Month fmDD, YYYY", "fmHH:fmMI:SS AM"),
      // 2026/05/03, 2026年5月3日 日曜日, 0:00:00
      "JAPAN", new Territory("RR-MM-DD", "RR-MM-DD HH24:MI:SSXFF", "RR-MM-DD HH24:MI:SSXFF TZR", ".,", "¥", "JPY",
          "\\", java.util.Calendar.SUNDAY, "RRRR/MM/DD", "fmYYYY\"年\"MM\"月\"DD\"日\" Day", "fmHH24:fmMI:SS"));

  /** NLS_ISO_CURRENCY takes a territory; C writes that territory's ISO 4217 code. */
  private static final Map<String, String> ISO_CURRENCIES = Map.of(
      "AMERICA", "USD", "JAPAN", "JPY", "GERMANY", "EUR", "FRANCE", "EUR", "ITALY", "EUR", "SPAIN", "EUR",
      "UNITED KINGDOM", "GBP", "CHINA", "CNY", "KOREA", "KRW", "CANADA", "CAD");

  /** The defaults of an Oracle session nobody configured: NLS_LANGUAGE=AMERICAN, NLS_TERRITORY=AMERICA. */
  public static final Nls AMERICAN = of("AMERICAN", "AMERICA");

  public Nls {
    dateLanguage = known(dateLanguage, Names.LANGUAGES.keySet(), "NLS_DATE_LANGUAGE");
    territory = known(territory, TERRITORIES.keySet(), "NLS_TERRITORY");
    if (decimal == group || Character.isDigit(decimal) || Character.isDigit(group)
        || "+-<>".indexOf(decimal) >= 0 || "+-<>".indexOf(group) >= 0) {
      // ORA-12705 in Oracle: the two must differ and be neither a digit nor a sign
      throw new IllegalArgumentException("NLS_NUMERIC_CHARACTERS '" + decimal + group + "' is not valid");
    }
    if (currency.isEmpty() || currency.length() > 10 || dualCurrency.isEmpty() || dualCurrency.length() > 10) {
      throw new IllegalArgumentException("NLS_CURRENCY / NLS_DUAL_CURRENCY takes 1 to 10 characters");
    }
  }

  /** A language and a territory with everything else the territory's, as ALTER SESSION sets them. */
  public static Nls of(String dateLanguage, String territory) {
    String name = known(territory, TERRITORIES.keySet(), "NLS_TERRITORY");
    Territory t = TERRITORIES.get(name);
    return new Nls(dateLanguage, name, t.dateFormat, t.timestampFormat, t.timestampTzFormat, t.numeric.charAt(0),
        t.numeric.charAt(1), t.currency, t.isoCurrency, t.dualCurrency);
  }

  public Nls withDateFormat(String format) {
    return new Nls(dateLanguage, territory, format, timestampFormat, timestampTzFormat, decimal, group, currency,
        isoCurrency, dualCurrency);
  }

  public Nls withTimestampFormat(String format) {
    return new Nls(dateLanguage, territory, dateFormat, format, timestampTzFormat, decimal, group, currency,
        isoCurrency, dualCurrency);
  }

  public Nls withTimestampTzFormat(String format) {
    return new Nls(dateLanguage, territory, dateFormat, timestampFormat, format, decimal, group, currency,
        isoCurrency, dualCurrency);
  }

  /** NLS_NUMERIC_CHARACTERS: the decimal character, then the group separator ('.,' or ',.'). */
  public Nls withNumericCharacters(String characters) {
    if (characters == null || characters.length() != 2) {
      throw new IllegalArgumentException("NLS_NUMERIC_CHARACTERS takes two characters: '" + characters + "'");
    }
    return new Nls(dateLanguage, territory, dateFormat, timestampFormat, timestampTzFormat, characters.charAt(0),
        characters.charAt(1), currency, isoCurrency, dualCurrency);
  }

  public Nls withCurrency(String symbol) {
    return new Nls(dateLanguage, territory, dateFormat, timestampFormat, timestampTzFormat, decimal, group, symbol,
        isoCurrency, dualCurrency);
  }

  /** NLS_ISO_CURRENCY names a territory, as Oracle takes it: JAPAN writes JPY for C. */
  public Nls withIsoCurrency(String territoryName) {
    String name = known(territoryName, ISO_CURRENCIES.keySet(), "NLS_ISO_CURRENCY");
    return new Nls(dateLanguage, territory, dateFormat, timestampFormat, timestampTzFormat, decimal, group, currency,
        ISO_CURRENCIES.get(name), dualCurrency);
  }

  public Nls withDualCurrency(String symbol) {
    return new Nls(dateLanguage, territory, dateFormat, timestampFormat, timestampTzFormat, decimal, group, currency,
        isoCurrency, symbol);
  }

  /** D: the number of the day of the week, 1 on the territory's first day. */
  int dayOfWeek(java.time.DayOfWeek day) {
    int first = TERRITORIES.get(territory).firstDay;   // java.util.Calendar: SUNDAY = 1
    int iso = day.getValue() % 7 + 1;                   // SUNDAY = 1 .. SATURDAY = 7
    return Math.floorMod(iso - first, 7) + 1;
  }

  /** The territory's DS, DL or TS as a format model. */
  String shortFormat(String element) {
    Territory t = TERRITORIES.get(territory);
    return switch (element) {
      case "DS" -> t.ds;
      case "DL" -> t.dl;
      default -> t.ts;
    };
  }

  Names names() {
    return Names.LANGUAGES.get(dateLanguage);
  }

  private static String known(String value, java.util.Set<String> names, String parameter) {
    String name = value == null ? "" : value.trim().toUpperCase(java.util.Locale.ROOT);
    if (!names.contains(name)) {
      throw new IllegalArgumentException(parameter + " '" + value + "' is not one the runtime knows; it knows "
          + new java.util.TreeSet<>(names));
    }
    return name;
  }

  /** The names a date language gives, as Oracle writes them in upper case (Oracle 26ai). */
  record Names(List<String> months, List<String> shortMonths, List<String> days, List<String> shortDays,
      String am, String pm, String ad, String bc, String amDots, String pmDots, String adDots, String bcDots,
      boolean cased) {

    static final Map<String, Names> LANGUAGES;

    static {
      Names english = new Names(
          List.of("JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST", "SEPTEMBER", "OCTOBER",
              "NOVEMBER", "DECEMBER"),
          List.of("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"),
          // Monday first, as java.time.DayOfWeek counts
          List.of("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"),
          List.of("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"),
          "AM", "PM", "AD", "BC", "A.M.", "P.M.", "A.D.", "B.C.", true);
      // MON and MONTH are the same in Japanese ('9月'); both pad to the longest, '12月'
      List<String> months = List.of("1月", "2月", "3月", "4月", "5月", "6月", "7月", "8月", "9月", "10月", "11月", "12月");
      Names japanese = new Names(months, months,
          List.of("月曜日", "火曜日", "水曜日", "木曜日", "金曜日", "土曜日", "日曜日"),
          List.of("月", "火", "水", "木", "金", "土", "日"),
          "午前", "午後", "西暦", "紀元前", "午前", "午後", "西暦", "紀元前", false);
      LANGUAGES = Map.of("AMERICAN", english, "ENGLISH", english, "JAPANESE", japanese);
    }

    static int longest(List<String> names) {
      return names.stream().mapToInt(OracleFormat::bytes).max().orElse(0);   // bytes, as Oracle pads
    }
  }
}
