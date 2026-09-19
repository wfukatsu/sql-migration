package com.scalar.migrate.plsql;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.nio.file.Path;
import org.junit.jupiter.api.Test;

/**
 * シナリオが**何を呼ぶか**の読み取り（2026-09-19）。
 *
 * <p>ハーネスは `unit` / `routine` から呼ぶ method を引いていた。trigger のシナリオは、trigger を
 * 確かめるために書き込み経路を呼ぶ（移行先に trigger は無く、掛かるのは書き込む側が呼ぶときだけ）
 * ので、`unit` は確かめる対象、`call` は呼ぶ対象である。取り違えて「比較できない」になっていた。
 */
class ScenarioTest {
  private static final Path SCENARIOS = Path.of("../fixtures/plsql/scenarios");

  @Test
  void aTriggerScenarioCallsTheWritePathItNames() throws Exception {
    Scenario scenario = Scenario.read(SCENARIOS.resolve("trigger_orders_audit_on_status.yaml"));
    assertEquals("trg_orders_audit", scenario.unit(), "確かめる対象はそのまま");
    assertEquals("pkg_stock_reserve", scenario.callUnit());
    assertEquals("claim_batch", scenario.callRoutine());
  }

  @Test
  void anOrdinaryScenarioCallsItsOwnRoutine() throws Exception {
    Scenario scenario = Scenario.read(SCENARIOS.resolve("payment_record.yaml"));
    assertEquals("pkg_payment", scenario.callUnit());
    assertEquals("record_payment", scenario.callRoutine());
  }

  @Test
  void aStandaloneProcedureIsItsOwnUnit() throws Exception {
    Scenario scenario = Scenario.read(SCENARIOS.resolve("nightly_close.yaml"));
    assertEquals("prc_nightly_close", scenario.callUnit());
    assertEquals("prc_nightly_close", scenario.callRoutine());
  }

  @Test
  void aBlockScenarioFallsBackToItsUnit() throws Exception {
    Scenario scenario = Scenario.read(SCENARIOS.resolve("view_load_rowtype.yaml"));
    assertEquals(scenario.unit(), scenario.callUnit());
    assertEquals(scenario.routine(), scenario.callRoutine());
  }
}
