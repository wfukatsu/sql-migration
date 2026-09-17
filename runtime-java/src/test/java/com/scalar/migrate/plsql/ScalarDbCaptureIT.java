package com.scalar.migrate.plsql;

import static org.junit.jupiter.api.Assertions.assertFalse;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import java.lang.reflect.Constructor;
import java.lang.reflect.Method;
import java.math.BigDecimal;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.junit.jupiter.api.condition.EnabledIfSystemProperty;

/**
 * P3-1: the ScalarDB half of the differential test.
 *
 * <p>P0-5 ran every scenario on Oracle and captured what happened. This runs the same scenarios against a real
 * ScalarDB Cluster, through the generated Java (P2-6/P2-7), and writes captures in the same canonical form. It
 * does not compare them -- that is P3-2. Producing the second capture and comparing it in the same pass would
 * let the comparison quietly define the capture, and then a scenario that cannot be run would look like one
 * that agreed.
 *
 * <p>So the only thing asserted here is that nothing was skipped in silence. Every scenario ends in exactly one
 * of two states: a capture file, or a named reason in {@code unrunnable.json}. A scenario that produced neither
 * fails the test.
 *
 * <p>Run it:
 * <pre>
 *   python -m plsql.generate fixtures/plsql/src --out-dir generated
 *   python difftest/plsql_run.py deploy                       # the Oracle side of the same fixtures
 *   # load fixtures/plsql/scalardb-schema.json with the Schema Loader, then
 *   SCALARDB_IT=1 gradle test -Dplsql.generated=1 --tests '*ScalarDbCaptureIT*'
 * </pre>
 */
@EnabledIfEnvironmentVariable(named = "SCALARDB_IT", matches = "1")
@EnabledIfSystemProperty(named = "plsql.generated", matches = "1")
class ScalarDbCaptureIT {
  private static final Path SCENARIOS = Path.of("..", "fixtures", "plsql", "scenarios");
  private static final String PACKAGE = "com.example.migrated";

  private static final String VARIANT = Variant.NAME;
  private static final String NAMESPACE = Variant.NAMESPACE;
  private static final Path SCHEMA = Variant.SCHEMA;
  private static final Path SETUP = Variant.SETUP;
  private static final Path OUT = Variant.CAPTURES;
  private static final Gson GSON = new GsonBuilder().serializeNulls().setPrettyPrinting().create();

  private static ScalarDbRunner runner;
  private static ConvertedSetup setup;

  @BeforeAll
  static void open() throws Exception {
    Variant.assertGeneratedForThisVariant();
    runner = new ScalarDbRunner(Variant.PROPERTIES, NAMESPACE, SCHEMA);
    setup = ConvertedSetup.read(SETUP);
    Files.createDirectories(OUT);
  }

  @AfterAll
  static void close() throws Exception {
    if (runner != null) runner.close();
  }

  @Test
  void everyScenarioIsEitherCapturedOrExplained() throws Exception {
    Map<String, String> unrunnable = new TreeMap<>();
    List<String> captured = new ArrayList<>();

    for (Scenario scenario : Scenario.readAll(SCENARIOS)) {
      try {
        Invoker invoker = Invoker.forScenario(scenario, runner.connection());
        runner.reset();
        for (String statement : setup.forScenario(scenario)) {
          try {
            runner.execute(statement);
          } catch (Exception e) {
            // ScalarDB refusing the starting rows is a finding about the schema or the converter, and it is
            // recorded as one. Seeding something else instead would make the capture describe a different case.
            throw new Unrunnable("ScalarDB rejected the setup `" + statement + "`: " + rootCause(e));
          }
        }
        runner.commit();

        // Oracle's harness moves the database clock to `pinned.sysdate` before running the scenario; the Java
        // side has to be moved with it, or every routine that reads SYSDATE is compared against a different day
        pinClock(scenario);
        Map<String, Object> capture = runner.capture(scenario, invoker, "scalardb:" + VARIANT);
        Files.writeString(OUT.resolve(scenario.name() + ".json"), GSON.toJson(capture) + "\n");
        captured.add(scenario.name());
      } catch (Unrunnable e) {
        runner.rollback();
        unrunnable.put(scenario.name(), e.getMessage());
      } finally {
        Plsql.setClock(LocalDateTime::now);
      }
    }

    Files.writeString(OUT.resolve("unrunnable.json"), GSON.toJson(unrunnable) + "\n");
    System.out.printf("variant %s (namespace %s): captured %d scenario(s); %d could not run "
        + "(see %s/unrunnable.json)%n", VARIANT, NAMESPACE, captured.size(), unrunnable.size(), OUT);
    unrunnable.forEach((name, reason) -> System.out.printf("  %-34s %s%n", name, reason));

    assertFalse(captured.isEmpty(), "no scenario produced a ScalarDB capture; the harness is not wired up");
  }

  /**
   * The scenario setups, already converted to ScalarDB SQL by {@code difftest/plsql_setup.py}.
   *
   * <p>Read rather than rewritten here: the converter under migration is the one that has to produce these rows,
   * and a setup it cannot convert is a finding, not something for the harness to route around.
   */
  record ConvertedSetup(Map<String, List<String>> converted, Map<String, String> unconvertible) {
    @SuppressWarnings("unchecked")
    static ConvertedSetup read(Path file) throws Exception {
      if (!Files.exists(file)) {
        throw new IllegalStateException(file.toAbsolutePath().normalize()
            + " is missing; run `python difftest/plsql_setup.py` first");
      }
      Map<String, Object> root = GSON.fromJson(Files.readString(file), Map.class);
      Map<String, List<String>> converted = new TreeMap<>();
      ((Map<String, Map<String, Object>>) root.get("scenarios")).forEach(
          (name, body) -> converted.put(name, (List<String>) body.get("setup")));
      return new ConvertedSetup(converted, new TreeMap<>((Map<String, String>) root.get("unconvertible")));
    }

    List<String> forScenario(Scenario scenario) throws Unrunnable {
      List<String> statements = converted.get(scenario.name());
      if (statements == null) {
        throw new Unrunnable("setup does not convert to ScalarDB SQL: "
            + unconvertible.getOrDefault(scenario.name(), "scenario missing from " + SETUP));
      }
      return statements;
    }
  }

  /**
   * Move the generated code's clock to where the Oracle harness moved the database's.
   *
   * <p>A scenario that pins nothing is left on the real clock, and any column it writes from that clock is
   * masked, so nothing unpinned reaches the comparison either way.
   */
  private static void pinClock(Scenario scenario) {
    Object sysdate = scenario.pinned().get("sysdate");
    if (sysdate == null) {
      Plsql.setClock(LocalDateTime::now);
      return;
    }
    LocalDateTime pinned = LocalDateTime.parse(String.valueOf(sysdate).replace(' ', 'T'));
    Plsql.setClock(() -> pinned);
  }

  /** ScalarDB wraps its own message a few layers down; the outer JDBC exception says nothing useful. */
  private static String rootCause(Throwable e) {
    Throwable cause = e;
    while (cause.getCause() != null && cause.getCause() != cause) cause = cause.getCause();
    return cause.getMessage();
  }

  /** A scenario this side cannot run at all -- an ungenerated routine, an argument the generator refused. */
  static final class Unrunnable extends Exception {
    Unrunnable(String message) {
      super(message);
    }
  }

  /**
   * Calls the generated service that corresponds to a scenario.
   *
   * <p>Reflection, rather than a hand-written call per scenario, because 59 hand-written calls are 59 more
   * chances to call something other than what the scenario says. The names follow the generator's own
   * convention (P2-6), so a lookup that fails means the routine was not generated -- which is the answer, and
   * is recorded as one.
   */
  static final class Invoker implements ScalarDbRunner.Invocation {
    private final Object service;
    private final Method method;
    private final Object[] arguments;

    private Invoker(Object service, Method method, Object[] arguments) {
      this.service = service;
      this.method = method;
      this.arguments = arguments;
    }

    static Invoker forScenario(Scenario scenario, java.sql.Connection connection) throws Unrunnable {
      String base = pascalCase(scenario.unit());
      Object service;
      try {
        Class<?> repositoryClass = Class.forName(PACKAGE + ".infrastructure." + base + "Repository");
        Class<?> serviceClass = Class.forName(PACKAGE + ".application." + base + "Service");
        Constructor<?> repository = repositoryClass.getConstructor(java.sql.Connection.class);
        service = serviceClass.getConstructor(repositoryClass).newInstance(repository.newInstance(connection));
      } catch (ReflectiveOperationException e) {
        throw new Unrunnable("no generated service for unit " + scenario.unit() + " (" + e + ")");
      }

      if (scenario.blockIsMoreThanAProjection()) {
        throw new Unrunnable("the scenario's block does more than call the routine and project its result; "
            + "running the routine alone would capture a different thing");
      }
      String name = camelCase(scenario.routine());
      List<Object> raw = scenario.arguments();
      for (Method candidate : service.getClass().getMethods()) {
        if (!candidate.getName().equals(name) || candidate.getParameterCount() != raw.size()) continue;
        return new Invoker(service, candidate, coerce(raw, candidate.getParameterTypes(), scenario));
      }
      throw new Unrunnable("generated service has no method " + name + "/" + raw.size()
          + "; the routine was refused or is not public");
    }

    private static Object[] coerce(List<Object> raw, Class<?>[] types, Scenario scenario) throws Unrunnable {
      Object[] out = new Object[raw.size()];
      for (int i = 0; i < raw.size(); i++) {
        Object value = raw.get(i);
        Class<?> type = types[i];
        if (value == null) {
          out[i] = null;
        } else if (type.isInstance(value)) {
          out[i] = value;
        } else if (type == BigDecimal.class && value instanceof Number n) {
          out[i] = new BigDecimal(n.toString());
        } else if (type == Integer.class && value instanceof Number n) {
          out[i] = n.intValue();
        } else if (type == java.time.LocalDateTime.class && value instanceof java.util.Date d) {
          // SnakeYAML reads an unquoted `2026-01-15` as a Date; the generated signature for an Oracle DATE is
          // LocalDateTime, and the scenario means midnight on that day, which is what Oracle bound too.
          out[i] = java.time.LocalDateTime.ofInstant(d.toInstant(), java.time.ZoneOffset.UTC);
        } else if (type == String.class) {
          out[i] = String.valueOf(value);
        } else {
          // a collection argument means a PL/SQL table type, which the generator refuses (P2-5)
          throw new Unrunnable("argument " + (i + 1) + " of " + scenario.routine() + " is a "
              + value.getClass().getSimpleName() + ", which does not fit " + type.getSimpleName());
        }
      }
      return out;
    }

    @Override
    public Object run() throws Exception {
      try {
        return method.invoke(service, arguments);
      } catch (java.lang.reflect.InvocationTargetException e) {
        // the routine's own failure is the result; the reflection wrapper is not part of it
        throw e.getCause() instanceof Exception cause ? cause : new Exception(e.getCause());
      }
    }
  }

  static String camelCase(String snake) {
    String[] parts = snake.split("_");
    StringBuilder out = new StringBuilder(parts[0]);
    for (int i = 1; i < parts.length; i++) {
      out.append(Character.toUpperCase(parts[i].charAt(0))).append(parts[i].substring(1));
    }
    return out.toString();
  }

  static String pascalCase(String snake) {
    String camel = camelCase(snake);
    return Character.toUpperCase(camel.charAt(0)) + camel.substring(1);
  }
}
