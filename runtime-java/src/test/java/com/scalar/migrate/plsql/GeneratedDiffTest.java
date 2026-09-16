package com.scalar.migrate.plsql;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.example.migrated.application.PkgCustomerCrudService;
import com.example.migrated.application.PkgOrderStatusService;
import com.example.migrated.domain.MigratedException;
import com.example.migrated.infrastructure.PkgCustomerCrudRepository;
import com.example.migrated.infrastructure.PkgOrderStatusRepository;
import com.google.gson.Gson;
import com.google.gson.JsonObject;
import java.math.BigDecimal;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.Statement;
import java.util.List;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfSystemProperty;

/**
 * P2-11: the first semantic signal, before the rest of the generator is built.
 *
 * <p>Phase 2's exit condition is that AUTO code compiles, and compiling says nothing about meaning. This runs a
 * few generated routines for real and compares the result with what Oracle actually did (the P0-5 captures), so
 * that a mistake in the generation rules -- an exception not reproduced, a number rounded the other way -- is
 * found now rather than after every routine has been generated the same wrong way.
 *
 * <p>The driver is H2, not ScalarDB. What is under test is the generated Java: its control flow, its exceptions
 * and its arithmetic. Whether ScalarDB executes the same SQL the same way is a different question, and P3-1 asks
 * it against a real cluster. Using H2 here keeps the signal available without a licensed cluster, which is the
 * difference between a check that runs on every change and one that does not.
 *
 * <p>Enabled when the generated sources are present:
 * {@code python -m plsql.generate fixtures/plsql/src --out-dir generated}, then
 * {@code gradle test -Dplsql.generated=1}.
 */
@EnabledIfSystemProperty(named = "plsql.generated", matches = "1")
class GeneratedDiffTest {
  private static final Path GOLDEN = Path.of("..", "fixtures", "plsql", "golden");
  private static final Gson GSON = new Gson();

  private Connection connection;

  @BeforeEach
  void setUp() throws Exception {
    connection = DriverManager.getConnection("jdbc:h2:mem:plsql;MODE=Oracle;DB_CLOSE_DELAY=-1");
    connection.setAutoCommit(false);
    try (Statement statement = connection.createStatement()) {
      statement.execute("DROP ALL OBJECTS");
      statement.execute("CREATE TABLE customers (customer_id BIGINT PRIMARY KEY, name VARCHAR(100), "
          + "email VARCHAR(200), tier VARCHAR(10), credit_limit BIGINT, registered_on TIMESTAMP)");
      statement.execute("CREATE TABLE orders (order_id BIGINT PRIMARY KEY, customer_id BIGINT, "
          + "status VARCHAR(20), ordered_at TIMESTAMP, shipped_at TIMESTAMP, total_amount BIGINT, "
          + "note VARCHAR(400))");
    }
  }

  @AfterEach
  void tearDown() throws Exception {
    if (connection != null) connection.close();
  }

  private JsonObject golden(String scenario) throws Exception {
    return GSON.fromJson(Files.readString(GOLDEN.resolve(scenario + ".json")), JsonObject.class);
  }

  private void seed(String... rows) throws Exception {
    try (Statement statement = connection.createStatement()) {
      for (String row : rows) statement.execute(row);
    }
  }

  private PkgOrderStatusService orderStatus() {
    return new PkgOrderStatusService(new PkgOrderStatusRepository(connection));
  }

  // --- the captures ------------------------------------------------------------------------------------

  @Test
  void statusOfReturnsWhatOracleReturned() throws Exception {
    seed("INSERT INTO orders (order_id, customer_id, status, ordered_at) "
        + "VALUES (1001, 1, 'NEW', TIMESTAMP '2026-01-10 11:00:00')");

    String expected = golden("order_status_found").getAsJsonObject("result").get("returned").getAsString();
    assertEquals(expected, orderStatus().statusOf(BigDecimal.valueOf(1001)));
  }

  @Test
  void statusOfRaisesTheSameBusinessCodeOracleRaised() throws Exception {
    // no row: Oracle's SELECT INTO raises NO_DATA_FOUND, the handler turns it into -20020
    JsonObject expected = golden("order_status_missing").getAsJsonObject("exception");

    MigratedException thrown = assertThrows(MigratedException.class,
        () -> orderStatus().statusOf(BigDecimal.valueOf(9999)));
    assertEquals(expected.get("code").getAsInt(), thrown.code());
    assertTrue(expected.get("message").getAsString().contains(String.valueOf(9999)));
  }

  @Test
  void updateEmailRaisesWhenNoRowWasAffected() throws Exception {
    // SQL%ROWCOUNT = 0 is the whole behaviour of this routine
    JsonObject expected = golden("crud_update_email_missing").getAsJsonObject("exception");

    var service = new PkgCustomerCrudService(new PkgCustomerCrudRepository(connection));
    MigratedException thrown = assertThrows(MigratedException.class,
        () -> service.updateEmail(BigDecimal.valueOf(424242), "x@example.com"));
    assertEquals(expected.get("code").getAsInt(), thrown.code());
  }

  @Test
  void updateEmailUpdatesTheRowItFinds() throws Exception {
    seed("INSERT INTO customers (customer_id, name, tier, registered_on) "
        + "VALUES (1, 'Acme', 'GOLD', TIMESTAMP '2025-04-01 00:00:00')");

    var service = new PkgCustomerCrudService(new PkgCustomerCrudRepository(connection));
    service.updateEmail(BigDecimal.valueOf(1), "changed@example.com");

    try (var statement = connection.prepareStatement("SELECT email FROM customers WHERE customer_id = 1");
         var rows = statement.executeQuery()) {
      assertTrue(rows.next());
      assertEquals("changed@example.com", rows.getString(1));
    }
  }

  @Test
  void selectIntoRaisesWhenMoreThanOneRowMatches() throws Exception {
    // the capture for this is `status_for_customer_multiple`: Oracle answers 'MULTIPLE' through TOO_MANY_ROWS
    seed("INSERT INTO orders (order_id, customer_id, status, ordered_at) "
            + "VALUES (1001, 1, 'NEW', TIMESTAMP '2026-01-10 11:00:00')",
        "INSERT INTO orders (order_id, customer_id, status, ordered_at) "
            + "VALUES (1002, 1, 'SHIPPED', TIMESTAMP '2026-01-11 11:00:00')");

    JsonObject expected = golden("status_for_customer_multiple").getAsJsonObject("result")
        .getAsJsonObject("out");
    var result = orderStatus().statusForCustomer(BigDecimal.valueOf(1));
    assertEquals(expected.get("p_status").getAsString(), result.pStatus());
  }

  @Test
  void aScenarioWithNoRowsAnswersTheSameWay() throws Exception {
    JsonObject expected = golden("status_for_customer_none").getAsJsonObject("result").getAsJsonObject("out");
    var result = orderStatus().statusForCustomer(BigDecimal.valueOf(55));
    assertEquals(expected.get("p_status").getAsString(), result.pStatus());
  }

  @Test
  void everyScenarioUsedHereExists() {
    for (String scenario : List.of("order_status_found", "order_status_missing",
        "crud_update_email_missing", "status_for_customer_multiple", "status_for_customer_none")) {
      assertTrue(Files.exists(GOLDEN.resolve(scenario + ".json")), scenario);
    }
  }
}
