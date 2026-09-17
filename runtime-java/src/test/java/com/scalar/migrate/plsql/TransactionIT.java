package com.scalar.migrate.plsql;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.example.migrated.application.PkgCustomerCrudService;
import com.example.migrated.application.PkgCustomerViewService;
import com.example.migrated.application.PrcAddProductService;
import com.example.migrated.domain.MigratedException;
import com.example.migrated.infrastructure.PkgCustomerCrudRepository;
import com.example.migrated.infrastructure.PkgCustomerViewRepository;
import com.example.migrated.infrastructure.PrcAddProductRepository;
import java.math.BigDecimal;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.junit.jupiter.api.condition.EnabledIfSystemProperty;

/**
 * P3-4: what survives a commit, what does not survive a failure, and what two of them do at once.
 *
 * <p>P3-2 compared one run of a routine against one run on Oracle. That says nothing about the transaction: a
 * capture is taken after the transaction ended, so a routine that committed halfway and one that committed at
 * the end look identical in it. The questions here are the ones a capture cannot answer.
 *
 * <p>The generated code never begins or commits a transaction -- the boundary is the caller's (plan §9) -- so
 * these tests own it, which is also what makes them able to interleave two.
 *
 * <p>Run against a real cluster, because this is about Consensus Commit and not about Java:
 * <pre>
 *   SCALARDB_IT=1 gradle test -Dplsql.generated=1 --tests '*TransactionIT*'
 * </pre>
 */
@EnabledIfEnvironmentVariable(named = "SCALARDB_IT", matches = "1")
@EnabledIfSystemProperty(named = "plsql.generated", matches = "1")
class TransactionIT {
  private ScalarDbRunner runner;

  @BeforeEach
  void setUp() throws Exception {
    Variant.assertGeneratedForThisVariant();
    runner = open();
    runner.reset();
  }

  @AfterEach
  void tearDown() throws Exception {
    if (runner != null) runner.close();
  }

  private static ScalarDbRunner open() throws Exception {
    return new ScalarDbRunner(Variant.PROPERTIES, Variant.NAMESPACE, Variant.SCHEMA);
  }

  private static PkgCustomerCrudService crud(ScalarDbRunner on) {
    return new PkgCustomerCrudService(new PkgCustomerCrudRepository(on.connection()));
  }

  private static PkgCustomerViewService view(ScalarDbRunner on) {
    return new PkgCustomerViewService(new PkgCustomerViewRepository(on.connection()));
  }

  /** `credit_limit` is money, so the literal has to be written the way this variant's column holds it. */
  private void seedCustomer(ScalarDbRunner on, int id, String email, String limit) throws Exception {
    on.execute("INSERT INTO customers (customer_id, name, email, tier, credit_limit, registered_on) "
        + "VALUES (" + id + ", 'C" + id + "', '" + email + "', 'GOLD', " + Variant.money(limit)
        + ", '2025-04-01 00:00:00')");
  }

  private void seedProduct(ScalarDbRunner on, int id, long stock) throws Exception {
    on.execute("INSERT INTO products (product_id, name, unit_price, stock_qty, discontinued) "
        + "VALUES (" + id + ", 'P" + id + "', " + Variant.money("10.00") + ", " + stock + ", 'N')");
  }

  private long stockOf(ScalarDbRunner on, int id) throws Exception {
    List<Map<String, Object>> rows = on.select("SELECT stock_qty FROM products WHERE product_id = " + id);
    return ((Number) rows.get(0).get("stock_qty")).longValue();
  }

  private String emailOf(int id) throws Exception {
    List<Map<String, Object>> rows = runner.select(
        "SELECT email FROM customers WHERE customer_id = " + id);
    runner.commit();
    return rows.isEmpty() ? null : (String) rows.get(0).get("email");
  }

  // --- what a commit makes durable ------------------------------------------------------------------

  @Test
  void committedWorkIsThereForSomeoneElseAfterwards() throws Exception {
    seedCustomer(runner, 1, "before@example.com", "100.00");
    runner.commit();

    crud(runner).updateEmail(new BigDecimal(1), "after@example.com");
    runner.commit();

    // a second connection, so this is durability and not the writer reading its own transaction back
    try (ScalarDbRunner other = open()) {
      List<Map<String, Object>> rows = other.select("SELECT email FROM customers WHERE customer_id = 1");
      other.commit();
      assertEquals("after@example.com", rows.get(0).get("email"));
    }
  }

  // --- what a failure leaves behind -----------------------------------------------------------------

  @Test
  void aRoutineThatRaisesLeavesNothingBehind() throws Exception {
    seedCustomer(runner, 1, "before@example.com", "100.00");
    runner.commit();

    // update_email raises when it matched no row; Oracle's routine does the same (P0-5 crud_update_email_missing)
    MigratedException raised = assertThrows(MigratedException.class,
        () -> crud(runner).updateEmail(new BigDecimal(99), "nobody@example.com"));
    assertEquals(-20010, raised.code());
    runner.rollback();

    assertEquals("before@example.com", emailOf(1));
  }

  @Test
  void aFailureAfterAWriteUndoesTheWriteToo() throws Exception {
    seedCustomer(runner, 1, "before@example.com", "100.00");
    runner.commit();

    // the routine's own write, then a failure in the same transaction: PL/SQL would keep neither, and neither
    // may the migration -- this is the partial-failure case the plan asks about
    assertThrows(MigratedException.class, () -> {
      crud(runner).updateEmail(new BigDecimal(1), "half-way@example.com");
      crud(runner).updateEmail(new BigDecimal(99), "nobody@example.com");
    });
    runner.rollback();

    assertEquals("before@example.com", emailOf(1),
        "the first update was committed although the transaction failed");
  }

  @Test
  void anInsertThatIsRolledBackNeverExisted() throws Exception {
    new PrcAddProductService(new PrcAddProductRepository(runner.connection()))
        .prcAddProduct(new BigDecimal(9001), "Rolled back", new BigDecimal("12.34"), new BigDecimal(5));
    runner.rollback();

    List<Map<String, Object>> rows = runner.select("SELECT product_id FROM products WHERE product_id = 9001");
    runner.commit();
    assertTrue(rows.isEmpty(), "a rolled-back insert is still there");
  }

  // --- two at once ----------------------------------------------------------------------------------

  /**
   * Two transactions read the same row and then write what they read, which is the shape
   * `pkg_stock_reserve.reserve` relied on `SELECT ... FOR UPDATE` to serialise.
   *
   * <p>The converter drops the locking clause and says so (`WARN ROW_LOCK`), and the routine is REDESIGN.
   * What this measures is what the redesign has to deal with. Consensus Commit does not block the second
   * reader, so one of two things happens: a commit fails, or an update is lost. The first is a transaction the
   * application must retry; the second would be a correctness hole. The assertion is that it is the first.
   *
   * <p>`stock_qty` rather than a money column on purpose -- it is the column the routine actually contends on,
   * and it holds the same value under either money convention.
   */
  @Test
  void twoTransactionsDecrementingTheSameRowDoNotBothSucceed() throws Exception {
    seedProduct(runner, 10, 100);
    runner.commit();

    CountDownLatch bothHaveRead = new CountDownLatch(2);
    AtomicReference<Throwable> first = new AtomicReference<>();
    AtomicReference<Throwable> second = new AtomicReference<>();

    Thread one = decrementer(10, bothHaveRead, first);
    Thread two = decrementer(10, bothHaveRead, second);
    one.start();
    two.start();
    one.join(TimeUnit.SECONDS.toMillis(60));
    two.join(TimeUnit.SECONDS.toMillis(60));

    long remaining = stockOf(runner, 10);
    runner.commit();

    boolean bothCommitted = first.get() == null && second.get() == null;
    if (bothCommitted) {
      // then the only acceptable outcome is that neither was lost, which a read-modify-write cannot promise
      assertEquals(98L, remaining,
          "both transactions committed a read-modify-write on the same row and one decrement was lost; "
              + "FOR UPDATE was dropped and nothing replaced it");
    } else {
      assertEquals(99L, remaining, "one transaction was rejected, so exactly one decrement should have landed");
      Throwable rejected = first.get() != null ? first.get() : second.get();
      assertNotNull(rejected);
      // this is the fact the redesign has to be built around: the application sees a failed transaction, not a
      // wait, so it has to retry -- which is not something the PL/SQL had to do
      System.out.println("  one transaction was rejected, as the redesign must expect: " + rejected);
    }
  }

  private Thread decrementer(int product, CountDownLatch bothHaveRead, AtomicReference<Throwable> failure) {
    return new Thread(() -> {
      try (ScalarDbRunner own = open()) {
        long read = stockOf(own, product);           // both read before either writes
        bothHaveRead.countDown();
        bothHaveRead.await(60, TimeUnit.SECONDS);
        own.execute("UPDATE products SET stock_qty = " + (read - 1) + " WHERE product_id = " + product);
        own.commit();
      } catch (Throwable t) {
        failure.set(t);
      }
    });
  }

  // --- the REDESIGN verdicts, shown rather than asserted ---------------------------------------------

  /**
   * A routine the rules called REDESIGN refuses, rather than running with different transaction semantics.
   *
   * <p>This is the claim the verdict makes: `prc_nightly_close` commits every hundred rows and rolls back to a
   * savepoint on error, and `prc_audit_autonomous` writes in a transaction of its own that survives the
   * caller's rollback. Neither has a meaning under one enclosing transaction. Generating something that runs
   * anyway would be the worst outcome available -- it would look migrated.
   */
  @Test
  void redesignRoutinesRefuseInsteadOfRunningWithDifferentSemantics() throws Exception {
    assertRefuses("com.example.migrated.application.PrcNightlyCloseService",
        "com.example.migrated.infrastructure.PrcNightlyCloseRepository",
        "prcNightlyClose", new Class<?>[] {java.time.LocalDateTime.class},
        new Object[] {java.time.LocalDateTime.now()});

    assertRefuses("com.example.migrated.application.PrcAuditAutonomousService",
        "com.example.migrated.infrastructure.PrcAuditAutonomousRepository",
        "prcAuditAutonomous", new Class<?>[] {String.class, String.class, String.class, String.class},
        new Object[] {"ORDERS", "1001", "NOTE", "x"});
  }

  /**
   * `pkg_stock_reserve.reserve` loses its row lock in conversion, and refuses before it can act on the value
   * it read without one. The refusal is what keeps the dropped `FOR UPDATE` from turning into a quiet
   * read-modify-write -- the very race the test above measures.
   */
  @Test
  void aRoutineThatLostItsRowLockRefusesBeforeItActsOnTheUnlockedRead() throws Exception {
    seedProduct(runner, 10, 100);
    runner.commit();

    Object service = Class.forName("com.example.migrated.application.PkgStockReserveService")
        .getConstructors()[0].newInstance(
            Class.forName("com.example.migrated.infrastructure.PkgStockReserveRepository")
                .getConstructor(java.sql.Connection.class).newInstance(runner.connection()));
    Throwable raised = assertThrows(java.lang.reflect.InvocationTargetException.class,
        () -> service.getClass().getMethod("reserve", BigDecimal.class, BigDecimal.class)
            .invoke(service, new BigDecimal(10), new BigDecimal(1))).getCause();
    assertTrue(raised instanceof UnsupportedOperationException, "expected a refusal, got " + raised);
    runner.rollback();

    long unchanged = stockOf(runner, 10);
    runner.commit();
    assertEquals(100L, unchanged, "the refused routine changed the stock anyway");
  }

  private void assertRefuses(String serviceClass, String repositoryClass, String method,
      Class<?>[] types, Object[] args) throws Exception {
    Class<?> repository = Class.forName(repositoryClass);
    Object service = Class.forName(serviceClass).getConstructor(repository)
        .newInstance(repository.getConstructor(java.sql.Connection.class).newInstance(runner.connection()));
    Throwable raised = assertThrows(java.lang.reflect.InvocationTargetException.class,
        () -> service.getClass().getMethod(method, types).invoke(service, args)).getCause();
    assertTrue(raised instanceof UnsupportedOperationException,
        serviceClass + "." + method + " ran instead of refusing; it raised " + raised);
    runner.rollback();
  }
}
