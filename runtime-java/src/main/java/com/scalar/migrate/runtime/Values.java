package com.scalar.migrate.runtime;

import com.scalar.db.api.Result;
import com.scalar.db.io.BigIntColumn;
import com.scalar.db.io.BlobColumn;
import com.scalar.db.io.BooleanColumn;
import com.scalar.db.io.Column;
import com.scalar.db.io.DataType;
import com.scalar.db.io.DateColumn;
import com.scalar.db.io.DoubleColumn;
import com.scalar.db.io.FloatColumn;
import com.scalar.db.io.IntColumn;
import com.scalar.db.io.TextColumn;
import com.scalar.db.io.TimeColumn;
import com.scalar.db.io.TimestampColumn;
import com.scalar.db.io.TimestampTZColumn;
import java.time.Instant;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.LocalTime;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.Base64;
import java.util.List;
import java.util.Map;

/** Conversions between JSON-ish Java objects, ScalarDB columns and H2 values. */
final class Values {
  private Values() {}

  static String h2Type(String scalardbType) {
    switch (scalardbType) {
      case "BOOLEAN": return "BOOLEAN";
      case "INT": return "INT";
      case "BIGINT": return "BIGINT";
      case "FLOAT": return "REAL";
      case "DOUBLE": return "DOUBLE PRECISION";
      case "TEXT": return "VARCHAR";
      case "BLOB": return "BINARY VARYING";
      case "DATE": return "DATE";
      case "TIME": return "TIME";
      case "TIMESTAMP": return "TIMESTAMP";
      case "TIMESTAMPTZ": return "TIMESTAMP WITH TIME ZONE";
      default: throw new IllegalArgumentException("unknown ScalarDB type " + scalardbType);
    }
  }

  /**
   * The ScalarDB type of a column nobody declared, from the values it holds -- all of them, not the first. Typed
   * from the first row, a column whose first value was NULL became VARCHAR: {@code MAX(amt)} answered "9" over
   * 10, {@code ORDER BY amt} put 10 before 9, and the same query was right or wrong depending on which row the
   * scan returned first. Returns null when every value is NULL (there is nothing to go by, and nothing to get
   * wrong: NULLs compare and sort the same under any type).
   */
  static String typeOfValues(List<Object[]> rows, int column) {
    String found = null;
    for (Object[] row : rows) {
      String type = typeOfValue(row[column]);
      if (type == null) continue;
      if (found == null || found.equals(type)) {
        found = type;
      } else if (NUMERIC_RANK.containsKey(found) && NUMERIC_RANK.containsKey(type)) {
        found = NUMERIC_RANK.get(found) >= NUMERIC_RANK.get(type) ? found : type;   // INT and BIGINT: BIGINT
      } else {
        throw new IllegalArgumentException("column " + column + " holds both " + found + " and " + type
            + " values and the plan declares no type for it");
      }
    }
    return found;
  }

  private static final Map<String, Integer> NUMERIC_RANK = Map.of("INT", 0, "BIGINT", 1, "FLOAT", 2, "DOUBLE", 3);

  static String typeOfValue(Object v) {
    if (v == null) return null;
    if (v instanceof Boolean) return "BOOLEAN";
    if (v instanceof Integer || v instanceof Short || v instanceof Byte) return "INT";
    if (v instanceof Long || v instanceof java.math.BigInteger) return "BIGINT";
    if (v instanceof Float) return "FLOAT";
    if (v instanceof Double || v instanceof java.math.BigDecimal) return "DOUBLE";
    if (v instanceof byte[]) return "BLOB";
    if (v instanceof LocalDate || v instanceof java.sql.Date) return "DATE";
    if (v instanceof LocalTime || v instanceof java.sql.Time) return "TIME";
    if (v instanceof LocalDateTime || v instanceof java.sql.Timestamp) return "TIMESTAMP";
    if (v instanceof Instant || v instanceof OffsetDateTime) return "TIMESTAMPTZ";
    return "TEXT";
  }

  /** The ScalarDB type behind a JDBC column type, or null for one this runtime does not know. */
  static String typeOfJdbc(int sqlType) {
    switch (sqlType) {
      case java.sql.Types.BOOLEAN: case java.sql.Types.BIT: return "BOOLEAN";
      case java.sql.Types.TINYINT: case java.sql.Types.SMALLINT: case java.sql.Types.INTEGER: return "INT";
      case java.sql.Types.BIGINT: return "BIGINT";
      case java.sql.Types.REAL: return "FLOAT";
      case java.sql.Types.FLOAT: case java.sql.Types.DOUBLE: return "DOUBLE";
      case java.sql.Types.CHAR: case java.sql.Types.VARCHAR: case java.sql.Types.LONGVARCHAR:
      case java.sql.Types.NCHAR: case java.sql.Types.NVARCHAR: return "TEXT";
      case java.sql.Types.BINARY: case java.sql.Types.VARBINARY: case java.sql.Types.LONGVARBINARY:
      case java.sql.Types.BLOB: return "BLOB";
      case java.sql.Types.DATE: return "DATE";
      case java.sql.Types.TIME: return "TIME";
      case java.sql.Types.TIMESTAMP: return "TIMESTAMP";
      case java.sql.Types.TIMESTAMP_WITH_TIMEZONE: return "TIMESTAMPTZ";
      default: return null;
    }
  }

  /** Build a typed ScalarDB column from a plan/JSON value (numbers arrive as Double from Gson, dates as ISO strings). */
  static Column<?> column(String name, DataType type, Object v) {
    if (v == null) {
      switch (type) {
        case BOOLEAN: return BooleanColumn.ofNull(name);
        case INT: return IntColumn.ofNull(name);
        case BIGINT: return BigIntColumn.ofNull(name);
        case FLOAT: return FloatColumn.ofNull(name);
        case DOUBLE: return DoubleColumn.ofNull(name);
        case TEXT: return TextColumn.ofNull(name);
        case BLOB: return BlobColumn.ofNull(name);
        case DATE: return DateColumn.ofNull(name);
        case TIME: return TimeColumn.ofNull(name);
        case TIMESTAMP: return TimestampColumn.ofNull(name);
        case TIMESTAMPTZ: return TimestampTZColumn.ofNull(name);
        default: throw new IllegalArgumentException("unsupported type " + type);
      }
    }
    switch (type) {
      case BOOLEAN: return BooleanColumn.of(name, v instanceof Boolean ? (Boolean) v : Boolean.parseBoolean(v.toString()));
      case INT: return IntColumn.of(name, Math.toIntExact(whole(name, v)));
      case BIGINT: return BigIntColumn.of(name, whole(name, v));
      case FLOAT: return FloatColumn.of(name, ((Number) v).floatValue());
      case DOUBLE: return DoubleColumn.of(name, ((Number) v).doubleValue());
      case TEXT: return TextColumn.of(name, v.toString());
      case BLOB: return BlobColumn.of(name, v instanceof byte[] ? (byte[]) v : Base64.getDecoder().decode(v.toString()));
      case DATE: return DateColumn.of(name, LocalDate.parse(v.toString()));
      case TIME: return TimeColumn.of(name, LocalTime.parse(v.toString()));
      case TIMESTAMP: return TimestampColumn.of(name, LocalDateTime.parse(v.toString().replace(' ', 'T')));
      case TIMESTAMPTZ: return TimestampTZColumn.of(name, Instant.parse(v.toString()));
      default: throw new IllegalArgumentException("unsupported type " + type);
    }
  }

  /** Whether {@code v} can be written as a value of {@code type} without changing it (whole, and in range). */
  static boolean representable(DataType type, Object v) {
    if (type != DataType.INT && type != DataType.BIGINT) return true;
    try {
      long n = whole("", v);
      return type == DataType.BIGINT || (n >= Integer.MIN_VALUE && n <= Integer.MAX_VALUE);
    } catch (IllegalArgumentException e) {
      return false;
    }
  }

  /**
   * A value for an INT / BIGINT column, exactly. {@code intValue()} wraps a value that does not fit and drops a
   * fraction, and either way the row that comes back is not the one that was asked for.
   */
  static long whole(String name, Object v) {
    try {
      return (v instanceof Long || v instanceof Integer || v instanceof Short || v instanceof Byte)
          ? ((Number) v).longValue()
          : new java.math.BigDecimal(v.toString()).longValueExact();
    } catch (ArithmeticException | NumberFormatException e) {
      throw new IllegalArgumentException("value " + v + " is not a whole number that fits column " + name, e);
    }
  }

  /** Read a column of a ScalarDB result as a plain Java object (null-safe). */
  static Object read(Result r, String name, DataType type) {
    if (r.isNull(name)) return null;
    switch (type) {
      case BOOLEAN: return r.getBoolean(name);
      case INT: return r.getInt(name);
      case BIGINT: return r.getBigInt(name);
      case FLOAT: return r.getFloat(name);
      case DOUBLE: return r.getDouble(name);
      case TEXT: return r.getText(name);
      case BLOB: return r.getBlobAsBytes(name);
      case DATE: return r.getDate(name);
      case TIME: return r.getTime(name);
      case TIMESTAMP: return r.getTimestamp(name);
      case TIMESTAMPTZ: return r.getTimestampTZ(name);
      default: throw new IllegalArgumentException("unsupported type " + type);
    }
  }

  /** Value as accepted by H2's setObject. */
  static Object toH2(Object v) {
    if (v instanceof Instant) return OffsetDateTime.ofInstant((Instant) v, ZoneOffset.UTC);
    // the java.sql types are instants read in the JVM's zone; the session is UTC, so they would land shifted
    if (v instanceof java.sql.Timestamp) return ((java.sql.Timestamp) v).toLocalDateTime();
    if (v instanceof java.sql.Date) return ((java.sql.Date) v).toLocalDate();
    if (v instanceof java.sql.Time) return ((java.sql.Time) v).toLocalTime();
    return v;
  }

  /** Value for JSON output. */
  static Object toJson(Object v) {
    if (v == null) return null;
    if (v instanceof Number || v instanceof Boolean || v instanceof String) return v;
    if (v instanceof byte[]) return Base64.getEncoder().encodeToString((byte[]) v);
    if (v instanceof java.sql.Timestamp) return ((java.sql.Timestamp) v).toLocalDateTime().toString();
    if (v instanceof java.sql.Date) return ((java.sql.Date) v).toLocalDate().toString();
    if (v instanceof java.sql.Time) return ((java.sql.Time) v).toLocalTime().toString();
    return v.toString();
  }
}
