package com.scalar.migrate.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.scalar.db.api.DistributedTransaction;
import com.scalar.db.api.DistributedTransactionAdmin;
import com.scalar.db.api.DistributedTransactionManager;
import com.scalar.db.api.Get;
import com.scalar.db.api.Insert;
import com.scalar.db.api.Result;
import com.scalar.db.api.TableMetadata;
import com.scalar.db.io.DataType;
import com.scalar.db.io.Key;
import com.scalar.db.service.TransactionFactory;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;

/**
 * P2-9 against a real ScalarDB: what a generated repository can and cannot do inside the caller's transaction.
 *
 * <p>Opt in with {@code SCALARDB_IT=1} and a running backend:
 *
 * <pre>
 *   (cd difftest && docker compose up -d backend-postgres)
 *   SCALARDB_IT=1 gradle test --tests '*ExternalTransactionIT*'
 * </pre>
 *
 * <p>Properties default to {@code difftest/conf/scalardb.properties}; override with {@code SCALARDB_PROPERTIES}.
 */
@EnabledIfEnvironmentVariable(named = "SCALARDB_IT", matches = "1")
class ExternalTransactionIT {
  private static final String NS = "p29";
  private static final String TABLE = "orders";

  private static DistributedTransactionManager manager;
  private static DistributedTransactionAdmin admin;

  @BeforeAll
  static void setUp() throws Exception {
    String props = System.getenv().getOrDefault("SCALARDB_PROPERTIES", "../difftest/conf/scalardb.properties");
    TransactionFactory factory = TransactionFactory.create(props);
    manager = factory.getTransactionManager();
    admin = factory.getTransactionAdmin();
    admin.createCoordinatorTables(true);
    admin.createNamespace(NS, true);
    admin.createTable(NS, TABLE, TableMetadata.newBuilder()
        .addColumn("id", DataType.INT)
        .addColumn("status", DataType.TEXT)
        .addPartitionKey("id")
        .build(), true);
    admin.truncateTable(NS, TABLE);
    DistributedTransaction seed = manager.start();
    seed.insert(Insert.newBuilder().namespace(NS).table(TABLE)
        .partitionKey(Key.ofInt("id", 1)).textValue("status", "NEW").build());
    seed.commit();
  }

  @AfterAll
  static void tearDown() throws Exception {
    if (admin != null) {
      admin.dropTable(NS, TABLE, true);
      admin.dropNamespace(NS, true);
      admin.close();
    }
    if (manager != null) manager.close();
  }

  private static Plan scanPlan() {
    Plan p = new Plan();
    Plan.Fetch f = new Plan.Fetch();
    f.namespace = NS;
    f.table = TABLE;
    f.columns = List.of("id", "status");
    f.column_types = Map.of("id", "INT", "status", "TEXT");
    f.max_rows = 100;
    p.fetch = List.of(f);
    Plan.Residual r = new Plan.Residual();
    r.engine = "h2";
    r.mode = "Oracle";
    r.sql = "SELECT id, status FROM " + TABLE + " ORDER BY id";
    p.residual = Map.of("java", r);
    p.source_sql = r.sql;
    return p;
  }

  @Test
  void planRunsInsideTheCallersTransaction() throws Exception {
    DistributedTransaction tx = manager.start();
    try {
      PlanRunner.Result result = PlanRunner.join(tx, admin, scanPlan(), Map.of());
      assertEquals(1, result.fetchedRows);
      assertEquals(List.of("NEW"), List.of(result.rows().get(0).get(1).toString()));
      tx.commit();  // the caller still owns the transaction after the plan ran
    } catch (Exception e) {
      tx.abort();
      throw e;
    }
  }

  @Test
  void pointReadSeesTheTransactionsOwnWrite() throws Exception {
    DistributedTransaction tx = manager.start();
    try {
      Get get = Get.newBuilder().namespace(NS).table(TABLE).partitionKey(Key.ofInt("id", 1)).build();
      tx.get(get);  // read before write: Consensus Commit needs the record read for a conditional update
      tx.put(com.scalar.db.api.Put.newBuilder().namespace(NS).table(TABLE)
          .partitionKey(Key.ofInt("id", 1)).textValue("status", "PAID").build());
      Optional<Result> after = tx.get(get);
      assertTrue(after.isPresent());
      assertEquals("PAID", after.get().getText("status"), "read-your-own-writes holds for key access");
    } finally {
      tx.abort();  // leave the seed row untouched for the other tests
    }
  }

  @Test
  void scanOverTheTransactionsOwnWriteIsRefusedByScalarDb() throws Exception {
    DistributedTransaction tx = manager.start();
    try {
      tx.get(Get.newBuilder().namespace(NS).table(TABLE).partitionKey(Key.ofInt("id", 1)).build());
      tx.put(com.scalar.db.api.Put.newBuilder().namespace(NS).table(TABLE)
          .partitionKey(Key.ofInt("id", 1)).textValue("status", "PAID").build());
      // the routine now runs a PLANNED read over the table it just wrote
      ScanAfterWriteException e =
          assertThrows(ScanAfterWriteException.class, () -> PlanRunner.join(tx, admin, scanPlan(), Map.of()));
      assertEquals(TABLE, e.table());
    } finally {
      tx.abort();
    }
  }
}
