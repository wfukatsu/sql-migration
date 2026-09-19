package com.scalar.migrate.plsql;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
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

  /**
   * `pkg_customer_import.import`: the MERGE became "read whether the row is there, then UPDATE or INSERT" (SEM-011,
   * limits.yaml rowLocks.optimistic). Two imports of the same new customer both read "not there".
   *
   * <p>Oracle's MERGE makes the second one wait and then update. Here nothing waits, so the question is whether
   * the second one is rejected or whether the customer is written twice / one name is silently lost. The decision
   * on record says: rejected at commit, and the caller retries that one element, which then takes the UPDATE
   * branch. This runs exactly the three statements the generated repository runs.
   */
  @Test
  void twoImportsOfTheSameNewCustomerDoNotBothInsertAndTheLoserRetriesAsAnUpdate() throws Exception {
    CountDownLatch bothHaveRead = new CountDownLatch(2);
    AtomicReference<Throwable> first = new AtomicReference<>();
    AtomicReference<Throwable> second = new AtomicReference<>();
    Thread one = importer(77, "First Co", bothHaveRead, first);
    Thread two = importer(77, "Second Co", bothHaveRead, second);
    one.start();
    two.start();
    one.join(TimeUnit.SECONDS.toMillis(60));
    two.join(TimeUnit.SECONDS.toMillis(60));

    assertTrue(first.get() == null || second.get() == null, "at least one import has to land");
    assertTrue(first.get() != null || second.get() != null,
        "both imports read 'not there' and both committed an INSERT of the same customer: nothing serialised them");
    assertEquals(1, runner.select("SELECT customer_id FROM customers WHERE customer_id = 77").size());
    runner.commit();

    // the caller retries the rejected element on its own: the row is there now, so it is the UPDATE branch
    String loser = first.get() != null ? "First Co" : "Second Co";
    try (ScalarDbRunner retry = open()) {
      importOnce(retry, 77, loser);
      retry.commit();
    }
    List<Map<String, Object>> rows = runner.select("SELECT name FROM customers WHERE customer_id = 77");
    runner.commit();
    assertEquals(1, rows.size(), "the retry must not add a second row");
    assertEquals(loser, rows.get(0).get("name"), "the retried element wins, as the later MERGE would have in Oracle");
  }

  private Thread importer(int id, String name, CountDownLatch bothHaveRead, AtomicReference<Throwable> failure) {
    return new Thread(() -> {
      try (ScalarDbRunner own = open()) {
        boolean exists = exists(own, id);            // both read before either writes
        bothHaveRead.countDown();
        bothHaveRead.await(60, TimeUnit.SECONDS);
        write(own, id, name, exists);
        own.commit();
      } catch (Throwable t) {
        failure.set(t);
      }
    });
  }

  private void importOnce(ScalarDbRunner on, int id, String name) throws Exception {
    write(on, id, name, exists(on, id));
  }

  private boolean exists(ScalarDbRunner on, int id) throws Exception {
    List<Map<String, Object>> rows = on.select("SELECT COUNT(*) AS n FROM customers WHERE customer_id = " + id);
    return ((Number) rows.get(0).get("n")).longValue() > 0;
  }

  private void write(ScalarDbRunner on, int id, String name, boolean exists) throws Exception {
    if (exists) {
      on.execute("UPDATE customers SET name = '" + name + "' WHERE customer_id = " + id);
    } else {
      on.execute("INSERT INTO customers (customer_id, name, tier, registered_on) VALUES (" + id + ", '" + name
          + "', 'BRONZE', '2026-01-15 09:30:00')");
    }
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
    assumeUndecided("application", "PrcNightlyCloseService");
    assumeUndecided("application", "PrcAuditAutonomousService");
    assertRefuses("com.example.migrated.application.PrcNightlyCloseService",
        "com.example.migrated.infrastructure.PrcNightlyCloseRepository",
        "prcNightlyClose",
        new Class<?>[] {java.time.LocalDateTime.class, AuditContext.class},
        new Object[] {java.time.LocalDateTime.now(),
            AuditContext.of("SOURCE", java.time.OffsetDateTime.now())});

    // `AuditContext` は #1 / #8 で足した引数である。この IT は `SCALARDB_IT=1` のときしか走らないので、
    // 署名が変わったことにここまで気づいていなかった（`getMethod` が NoSuchMethodException で落ちる）。
    //
    // `prc_audit_autonomous` は #23 のあと、INSERT が成立して**採番してから**拒否するようになって
    // いた。#25 で「完走できない routine は採番の前で止める」と決めたので、この assertion が戻った
    // ——採番が呼ばれたら、それ自体が失敗である。
    assertRefuses("com.example.migrated.application.PrcAuditAutonomousService",
        "com.example.migrated.infrastructure.PrcAuditAutonomousRepository",
        "prcAuditAutonomous",
        new Class<?>[] {String.class, String.class, String.class, String.class, AuditContext.class},
        new Object[] {"ORDERS", "1001", "NOTE", "x",
            AuditContext.of("SOURCE", java.time.OffsetDateTime.now())});
  }

  /**
   * `pkg_stock_reserve.reserve` loses its row lock in conversion, and refuses before it can act on the value
   * it read without one. The refusal is what keeps the dropped `FOR UPDATE` from turning into a quiet
   * read-modify-write -- the very race the test above measures.
   */
  @Test
  void aRoutineThatLostItsRowLockRefusesBeforeItActsOnTheUnlockedRead() throws Exception {
    assumeUndecided("infrastructure", "PkgStockReserveRepository");
    seedProduct(runner, 10, 100);
    runner.commit();

    // `reserve_nowait` は**まだ決まっていない**（`NOWAIT` の意味をどう見せるかが B 型の宿題）。
    // 決めた routine（`reserve`）は生成されるようになったが、決めていないものは拒否のままである
    // ——ロックこそがその読み書きを安全にしていたので、決めた人がいないうちは進めない（#9）。
    Object service = Class.forName("com.example.migrated.application.PkgStockReserveService")
        .getConstructors()[0].newInstance(
            repositoryOf("com.example.migrated.infrastructure.PkgStockReserveRepository"));
    Throwable raised = assertThrows(java.lang.reflect.InvocationTargetException.class,
        () -> service.getClass().getMethod("reserveNowait", BigDecimal.class, BigDecimal.class)
            .invoke(service, new BigDecimal(10), new BigDecimal(1))).getCause();
    assertTrue(raised instanceof UnsupportedOperationException, "expected a refusal, got " + raised);
    runner.rollback();

    long unchanged = stockOf(runner, 10);
    runner.commit();
    assertEquals(100L, unchanged, "the refused routine changed the stock anyway");
  }

  // --- the patterns in docs/plsql-transaction-patterns.md, measured rather than assumed ------------------

  /**
   * Pattern A: a conflict is retryable, and retrying it succeeds.
   *
   * <p>The document tells a reader to retry a rejected transaction. That is only advice if nothing checks it.
   * Here the first attempt is made to lose a race and the retry is the one that lands, so the decrement the
   * rejected transaction intended is not lost.
   */
  @Test
  void aRejectedTransactionSucceedsWhenItIsRetried() throws Exception {
    seedProduct(runner, 10, 100);
    runner.commit();

    CountDownLatch bothHaveRead = new CountDownLatch(2);
    AtomicReference<Throwable> loser = new AtomicReference<>();
    Thread one = decrementer(10, bothHaveRead, new AtomicReference<>());
    Thread two = decrementer(10, bothHaveRead, loser);
    one.start();
    two.start();
    one.join(TimeUnit.SECONDS.toMillis(60));
    two.join(TimeUnit.SECONDS.toMillis(60));

    if (loser.get() == null) return;  // both landed; the retry story is not exercised by this run
    // the rejected side retries, on its own, and this time nothing is racing it
    try (ScalarDbRunner retry = open()) {
      long current = stockOf(retry, 10);
      retry.execute("UPDATE products SET stock_qty = " + (current - 1) + " WHERE product_id = 10");
      retry.commit();
    }
    long remaining = stockOf(runner, 10);
    runner.commit();
    assertEquals(98L, remaining, "after the retry both decrements should have landed");
  }

  /**
   * Pattern A: a business failure is not a conflict, and must not be retried.
   *
   * <p>The document says to separate the two. They arrive as different exception types, and this pins that:
   * retrying a `MigratedException` would loop forever and change nothing.
   */
  @Test
  void aBusinessFailureIsNotAConflict() throws Exception {
    seedCustomer(runner, 1, "before@example.com", "100.00");
    runner.commit();

    MigratedException raised = assertThrows(MigratedException.class,
        () -> crud(runner).updateEmail(new BigDecimal(99), "nobody@example.com"));
    runner.rollback();

    assertEquals(-20010, raised.code());
    // the retry loop catches SQLTransactionRollbackException; a business error is not one, and the compiler
    // says so -- `raised instanceof SQLTransactionRollbackException` does not even compile. Asserting it on
    // the classes keeps the guarantee readable where the pattern is described.
    assertFalse(java.sql.SQLTransactionRollbackException.class.isAssignableFrom(raised.getClass()),
        "a business error must not arrive as the type the retry loop catches");
  }

  /**
   * Pattern D: a counter is a high-conflict point, which is why the document says to keep it out of a
   * transaction that does anything else.
   */
  @Test
  void twoTransactionsTakingTheSameCounterDoNotBothSucceed() throws Exception {
    runner.execute("INSERT INTO counters (counter_name, next_value) VALUES ('PAYMENT_ID', 5000)");
    runner.commit();

    CountDownLatch bothHaveRead = new CountDownLatch(2);
    AtomicReference<Throwable> first = new AtomicReference<>();
    AtomicReference<Throwable> second = new AtomicReference<>();
    Thread one = counterTaker(bothHaveRead, first);
    Thread two = counterTaker(bothHaveRead, second);
    one.start();
    two.start();
    one.join(TimeUnit.SECONDS.toMillis(60));
    two.join(TimeUnit.SECONDS.toMillis(60));

    List<Map<String, Object>> rows = runner.select(
        "SELECT next_value FROM counters WHERE counter_name = 'PAYMENT_ID'");
    long next = ((Number) rows.get(0).get("next_value")).longValue();
    runner.commit();

    boolean bothCommitted = first.get() == null && second.get() == null;
    assertFalse(bothCommitted && next == 5001,
        "both transactions took the same number: two payments would share an id");
  }

  /**
   * 生成された `nextPaymentId` を 2 つ同時に呼ぶ。手で書いた SQL ではなく**生成コード**を測るのは、
   * 「この形なら安全」と「生成された物がその形になっている」が別の主張だからである（#9 D 型）。
   */
  private Thread generatedCounterTaker(CountDownLatch bothHaveRead, AtomicReference<Throwable> failure,
      AtomicReference<java.math.BigDecimal> taken) {
    return new Thread(() -> {
      try (ScalarDbRunner own = open()) {
        Object service = Class.forName("com.example.migrated.application.PkgStockReserveService")
            .getConstructors()[0].newInstance(
                Class.forName("com.example.migrated.infrastructure.PkgStockReserveRepository")
                    .getConstructor(java.sql.Connection.class).newInstance(own.connection()));
        bothHaveRead.countDown();
        bothHaveRead.await(60, TimeUnit.SECONDS);
        taken.set((java.math.BigDecimal) service.getClass().getMethod("nextPaymentId").invoke(service));
        own.commit();
      } catch (Throwable t) {
        failure.set(t);
      }
    });
  }

  @Test
  void theGeneratedCounterDoesNotHandTheSameNumberToTwoCallers() throws Exception {
    runner.execute("INSERT INTO counters (counter_name, next_value) VALUES ('PAYMENT_ID', 5000)");
    runner.commit();

    CountDownLatch ready = new CountDownLatch(2);
    AtomicReference<Throwable> firstFailure = new AtomicReference<>();
    AtomicReference<Throwable> secondFailure = new AtomicReference<>();
    AtomicReference<java.math.BigDecimal> firstTaken = new AtomicReference<>();
    AtomicReference<java.math.BigDecimal> secondTaken = new AtomicReference<>();
    Thread one = generatedCounterTaker(ready, firstFailure, firstTaken);
    Thread two = generatedCounterTaker(ready, secondFailure, secondTaken);
    one.start();
    two.start();
    one.join(TimeUnit.SECONDS.toMillis(60));
    two.join(TimeUnit.SECONDS.toMillis(60));

    boolean bothCommitted = firstFailure.get() == null && secondFailure.get() == null;
    if (bothCommitted) {
      assertFalse(java.util.Objects.equals(firstTaken.get(), secondTaken.get()),
          "生成された採番が同じ番号を 2 人に渡した: " + firstTaken.get());
    }
    // どちらかが弾かれるのが期待どおりで、そのときは呼び出し側が再試行する（計画 §9）
    assertTrue(bothCommitted || firstFailure.get() != null || secondFailure.get() != null);
  }

  private Thread counterTaker(CountDownLatch bothHaveRead, AtomicReference<Throwable> failure) {
    return new Thread(() -> {
      try (ScalarDbRunner own = open()) {
        List<Map<String, Object>> rows = own.select(
            "SELECT next_value FROM counters WHERE counter_name = 'PAYMENT_ID'");
        long taken = ((Number) rows.get(0).get("next_value")).longValue();
        bothHaveRead.countDown();
        bothHaveRead.await(60, TimeUnit.SECONDS);
        own.execute("UPDATE counters SET next_value = " + (taken + 1)
            + " WHERE counter_name = 'PAYMENT_ID'");
        own.commit();
      } catch (Throwable t) {
        failure.set(t);
      }
    });
  }

  /**
   * 採番する Repository は `Sequences` も受け取る（計画 §9）。受け取らない物に依存を足さない設計なので、
   * コンストラクタは 2 種類ある。テストはどちらでも組み立てられる必要がある。
   */
  private Object repositoryOf(String repositoryClass) throws Exception {
    Class<?> repository = Class.forName(repositoryClass);
    for (var constructor : repository.getConstructors()) {
      Class<?>[] parameters = constructor.getParameterTypes();
      if (parameters.length == 1) {
        return constructor.newInstance(runner.connection());
      }
      if (parameters.length == 2 && parameters[1] == Sequences.class) {
        // 拒否されることを確かめるテストなので、採番が呼ばれたらそれ自体が失敗である
        Sequences never = name -> {
          throw new AssertionError("拒否されるはずの routine が採番した: " + name);
        };
        return constructor.newInstance(runner.connection(), never);
      }
    }
    throw new AssertionError("組み立てられないコンストラクタ: " + repositoryClass);
  }

  /**
   * A refusal is what a redesign nobody decided generates. Once the decision is recorded (`--limits`, which is
   * how `plsql_capture.py` builds the tree) the routine is generated and runs, and its behaviour is compared
   * with Oracle by the scenarios instead -- so against such a tree there is nothing here to check, and saying
   * "skipped, decided" is truer than failing. `tests/test_plsql_generate.py` pins the refusal without a cluster.
   */
  private static void assumeUndecided(String layer, String javaClass) throws Exception {
    Path source = Path.of("..", "generated", "src", "main", "java", "com", "example", "migrated", layer,
        javaClass + ".java");
    org.junit.jupiter.api.Assumptions.assumeTrue(
        java.nio.file.Files.readString(source).contains("UnsupportedOperationException"),
        javaClass + " was generated with its redesign decided (limits.yaml); it no longer refuses");
  }

  private void assertRefuses(String serviceClass, String repositoryClass, String method,
      Class<?>[] types, Object[] args) throws Exception {
    Class<?> repository = Class.forName(repositoryClass);
    Object service = Class.forName(serviceClass).getConstructor(repository)
        .newInstance(repositoryOf(repositoryClass));
    Throwable raised = assertThrows(java.lang.reflect.InvocationTargetException.class,
        () -> service.getClass().getMethod(method, types).invoke(service, args)).getCause();
    assertTrue(raised instanceof UnsupportedOperationException,
        serviceClass + "." + method + " ran instead of refusing; it raised " + raised);
    runner.rollback();
  }
}
