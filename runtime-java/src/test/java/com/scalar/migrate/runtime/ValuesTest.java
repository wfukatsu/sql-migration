package com.scalar.migrate.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.scalar.db.io.DataType;
import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.List;
import org.junit.jupiter.api.Test;

/** Review #27 (45): the value conversions between a plan, ScalarDB and H2 had no test of their own. */
class ValuesTest {

  @Test
  void aWholeNumberBecomesTheColumnsOwnTypeExactly() {
    assertEquals(9007199254740993L, Values.column("id", DataType.BIGINT, new BigDecimal("9007199254740993"))
        .getBigIntValue());
    assertEquals(7, Values.column("n", DataType.INT, 7.0d).getIntValue());
    assertEquals(123, Values.column("n", DataType.INT, "123").getIntValue());
  }

  @Test
  void aFractionOrAnOverflowIsRefusedRatherThanWrapped() {
    assertThrows(IllegalArgumentException.class, () -> Values.column("n", DataType.INT, 1.5d));
    assertThrows(ArithmeticException.class, () -> Values.column("n", DataType.INT, 3_000_000_000L));
    assertFalse(Values.representable(DataType.INT, 1.5d));
    assertFalse(Values.representable(DataType.INT, 3_000_000_000L));
    assertTrue(Values.representable(DataType.BIGINT, 3_000_000_000L));
    assertTrue(Values.representable(DataType.TEXT, 1.5d));
  }

  @Test
  void aNullKeepsTheColumnsType() {
    for (DataType type : DataType.values()) {
      assertTrue(Values.column("c", type, null).hasNullValue(), type.name());
      assertEquals(type, Values.column("c", type, null).getDataType());
    }
  }

  @Test
  void aTimestampIsReadWithASpaceOrAT() {
    LocalDateTime expected = LocalDateTime.of(2024, 1, 15, 10, 11, 12);
    assertEquals(expected, Values.column("t", DataType.TIMESTAMP, "2024-01-15 10:11:12").getTimestampValue());
    assertEquals(expected, Values.column("t", DataType.TIMESTAMP, "2024-01-15T10:11:12").getTimestampValue());
  }

  @Test
  void anUntypedColumnIsTypedFromAllItsValuesNotTheFirst() {
    List<Object[]> rows = List.of(new Object[] {null}, new Object[] {3L}, new Object[] {4L});
    assertEquals(Values.typeOfValue(3L), Values.typeOfValues(rows, 0), "a leading NULL used to make it VARCHAR");
  }
}
