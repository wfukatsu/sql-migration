package com.scalar.migrate.plsql;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.sql.PreparedStatement;
import java.time.LocalDateTime;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.junit.jupiter.api.condition.EnabledIfSystemProperty;

/**
 * #23: `AuditContext.now()` が TIMESTAMP 列へ届くことを、**実際のドライバで**確かめる。
 *
 * <p>この欠陥の性質上、単体テストでは足りない。`AuditContext.now()` は `OffsetDateTime` を返し、
 * 生成コードはコンパイルでき、`Plsql.bind` の単体テストも通り、それでも ScalarDB SQL のドライバが
 * 実行時に型ごと拒否していた（DB-SQL-10016）。**「ドライバが受け取るか」は、ドライバに聞く以外に
 * 確かめようがない。**
 *
 * <p>比較ハーネス（P3-2）でも捕まらなかった。`audit.now()` を書く routine は corpus では
 * `prc_audit_autonomous` と `pkg_bulk_load` だけで、どちらも別の未対応（自律トランザクションの
 * COMMIT、BULK COLLECT）で先に落ちるため、audit 列まで到達しない。
 *
 * <pre>
 *   SCALARDB_IT=1 gradle test -Dplsql.generated=1 --tests '*AuditBindIT*'
 * </pre>
 */
@EnabledIfEnvironmentVariable(named = "SCALARDB_IT", matches = "1")
@EnabledIfSystemProperty(named = "plsql.generated", matches = "1")
class AuditBindIT {
  private ScalarDbRunner runner;

  @BeforeEach
  void setUp() throws Exception {
    Variant.assertGeneratedForThisVariant();
    runner = new ScalarDbRunner(Variant.PROPERTIES, Variant.NAMESPACE, Variant.SCHEMA);
    runner.reset();
  }

  @AfterEach
  void tearDown() throws Exception {
    if (runner != null) runner.close();
  }

  @Test
  void theMomentTheCallerSuppliedReachesATimestampColumn() throws Exception {
    // 生成 repository がやっているのと同じこと: AuditContext の値を bind 境界に通して TIMESTAMP 列へ
    AuditContext audit = AuditContext.of(
        "SOURCE", OffsetDateTime.of(2026, 1, 15, 9, 30, 0, 0, ZoneOffset.ofHours(-5)));

    String sql = "INSERT INTO audit_log "
        + "(audit_id, table_name, key_value, action, old_value, new_value, changed_at, changed_by) "
        + "VALUES (?, 'ORDERS', '1001', 'NOTE', NULL, 'hello', ?, ?)";
    try (PreparedStatement statement = runner.connection().prepareStatement(sql)) {
      statement.setObject(1, 1L);
      statement.setObject(2, Plsql.bind(audit.now(), "TIMESTAMP", 0));
      statement.setObject(3, Plsql.bind(audit.user(), "TEXT", 0));
      statement.executeUpdate();
    }
    runner.commit();

    List<Map<String, Object>> rows = runner.select(
        "SELECT changed_at, changed_by FROM audit_log WHERE audit_id = 1");
    runner.commit();

    // Oracle が TIMESTAMP WITH TIME ZONE を TIMESTAMP 列へ入れるときと同じ: offset は落ち、
    // 日時のフィールドは換算されない（Oracle 23ai で実測。#23）
    assertEquals(LocalDateTime.of(2026, 1, 15, 9, 30, 0), rows.get(0).get("changed_at"));
    assertEquals("SOURCE", rows.get(0).get("changed_by"));
  }
}
