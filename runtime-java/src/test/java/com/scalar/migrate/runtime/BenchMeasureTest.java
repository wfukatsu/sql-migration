package com.scalar.migrate.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

/** Issue #28: the reports say the two legs are measured alternately; the loop has to do that. */
class BenchMeasureTest {

  @Test
  void theLegsAlternateWithinEveryIterationAndWarmUpIsNotCounted() {
    List<String> calls = new ArrayList<>();
    Bench.Leg source = new Bench.Leg(i -> { calls.add("o" + i); return Map.of("rows", 1); }, () -> calls.add("reset"));
    Bench.Leg scalar = new Bench.Leg(i -> { calls.add("s" + i); return Map.of("rows", 1); }, null);

    Bench.measure(2, 1, source, scalar);

    assertEquals(List.of("reset", "o0", "s0", "reset", "o1", "s1", "reset", "o2", "s2"), calls);
    assertEquals(2, ((List<?>) source.result().get("ms")).size());
    assertEquals(2, ((List<?>) scalar.result().get("ms")).size());
    assertEquals(1L, source.result().get("rows_min"));
  }

  @Test
  void aFailingLegStopsAndTheOtherGoesOn() {
    List<String> calls = new ArrayList<>();
    Bench.Leg source = new Bench.Leg(i -> { calls.add("o" + i); return Map.of("rows", (long) i); }, null);
    Bench.Leg scalar = new Bench.Leg(i -> {
      calls.add("s" + i);
      if (i == 1) throw new IllegalStateException("not supported\nsecond line");
      return Map.of("rows", 1);
    }, null);

    Bench.measure(3, 0, source, scalar);

    assertEquals(List.of("o0", "s0", "o1", "s1", "o2"), calls);
    assertEquals(3, ((List<?>) source.result().get("ms")).size());
    assertFalse(source.result().containsKey("error"));
    assertEquals(1, ((List<?>) scalar.result().get("ms")).size());
    assertEquals("IllegalStateException: not supported", scalar.result().get("error"));
    assertTrue(source.result().get("rows_max").equals(2L));
  }
}
