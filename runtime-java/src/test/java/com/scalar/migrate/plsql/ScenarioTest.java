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
  void anOverloadIsCalledByItsNumberedMethod() throws Exception {
    // Issue #29-23: `routine: set_email~2` names the second overload; Oracle is still called as pkg_contact.set_email
    Scenario second = Scenario.read(SCENARIOS.resolve("contact_set_email_and_name.yaml"));
    assertEquals("set_email", second.callRoutine());
    assertEquals("set_email2", second.callMethod());
    Scenario plain = Scenario.read(SCENARIOS.resolve("contact_clear_email.yaml"));
    assertEquals("clear_email", plain.callMethod());
  }

  @Test
  void aBlockScenarioFallsBackToItsUnit() throws Exception {
    Scenario scenario = Scenario.read(SCENARIOS.resolve("view_load_rowtype.yaml"));
    assertEquals(scenario.unit(), scenario.callUnit());
    assertEquals(scenario.routine(), scenario.callRoutine());
  }
  @Test
  void aBlockThatOnlyAsksWhetherTheResultIsNullIsRunnable() throws Exception {
    // `:o_is_null := CASE WHEN v IS NULL THEN 1 ELSE 0 END;` -- 時計の値そのものは固定できないので、
    // シナリオが観るのは NULL かどうかだけである。以前は「ブロックが射影以上のことをする」として比べていなかった
    Scenario scenario = Scenario.read(SCENARIOS.resolve("payment_last_paid_at.yaml"));
    assertEquals(false, scenario.blockIsMoreThanAProjection());
    assertEquals(Scenario.IS_NULL_OF_RESULT, scenario.projection().get("o_is_null"));
  }

  @Test
  void dbmsOutputIsPartOfTheResultOnlyWhenTheScenarioSaysSo(@org.junit.jupiter.api.io.TempDir Path dir) throws Exception {
    // samples/oracle-plsql-docs: what an example block prints is its result when it touches no table
    java.nio.file.Files.writeString(dir.resolve("s.yaml"),
        "name: s\nunit: u\nroutine: u\ncall:\n  kind: procedure\n  name: u\noutput: true\n");
    assertEquals(true, Scenario.read(dir.resolve("s.yaml")).output());
    assertEquals(false, Scenario.read(SCENARIOS.resolve("payment_record.yaml")).output());
  }
}
