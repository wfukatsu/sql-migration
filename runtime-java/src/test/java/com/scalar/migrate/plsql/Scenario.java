package com.scalar.migrate.plsql;

import java.io.Reader;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.yaml.snakeyaml.Yaml;

/**
 * One case from {@code fixtures/plsql/scenarios/}, read here exactly as {@code difftest/plsql_run.py} reads it.
 *
 * <p>Both sides read the same file on purpose. A scenario transcribed into Java would be a second description of
 * the case, free to drift from the one Oracle was measured against -- and then a difference between the captures
 * would mean the transcriptions disagreed, not the databases.
 *
 * @param setup  the rows the case starts from, as SQL. They are written through ScalarDB, so a statement ScalarDB
 *               cannot run is a finding about the fixture, not something for the harness to paper over.
 * @param args   the call's arguments in declaration order -- SnakeYAML keeps the mapping's order, and the
 *               scenarios are written in that order because Oracle binds them by name.
 */
public record Scenario(String name, String unit, String routine, Map<String, Object> pinned, List<String> setup,
                       String call, String kind, String body, Map<String, Object> args,
                       List<String> captureTables, Map<String, List<String>> mask, String note,
                       List<String> outs) {

  /** `:o_status := v.status;` -- the field of a record a block scenario projects into an OUT bind. */
  private static final java.util.regex.Pattern PROJECTION =
      java.util.regex.Pattern.compile(":(\\w+)\\s*:=\\s*(\\w+)\\.(\\w+)\\s*;");

  /**
   * `:o_is_null := CASE WHEN v IS NULL THEN 1 ELSE 0 END;` -- 返った値が NULL かどうかだけを観る射影。
   * 値そのものを固定できない（時計の値）ときに、シナリオが観るのはこれだけである（2026-09-19）。
   */
  private static final java.util.regex.Pattern IS_NULL = java.util.regex.Pattern.compile(
      ":(\\w+)\\s*:=\\s*CASE\\s+WHEN\\s+\\w+\\s+IS\\s+NULL\\s+THEN\\s+1\\s+ELSE\\s+0\\s+END\\s*;",
      java.util.regex.Pattern.CASE_INSENSITIVE);

  /** 射影の印: 返った値そのものが NULL なら 1、そうでなければ 0。 */
  public static final String IS_NULL_OF_RESULT = "#is_null";

  public static Scenario read(Path file) throws Exception {
    try (Reader reader = Files.newBufferedReader(file)) {
      return of(new Yaml().load(reader));
    }
  }

  public static List<Scenario> readAll(Path directory) throws Exception {
    List<Scenario> out = new ArrayList<>();
    try (var files = Files.list(directory)) {
      for (Path file : files.filter(f -> f.toString().endsWith(".yaml")).sorted().toList()) {
        out.add(read(file));
      }
    }
    return out;
  }

  @SuppressWarnings("unchecked")
  private static Scenario of(Map<String, Object> spec) {
    Map<String, Object> call = (Map<String, Object>) spec.get("call");
    Map<String, Object> pinned = (Map<String, Object>) spec.getOrDefault("pinned", Map.of());
    return new Scenario(
        (String) spec.get("name"),
        (String) spec.get("unit"),
        (String) spec.get("routine"),
        normalisePinned(pinned),
        or((List<String>) spec.get("setup")),
        (String) call.get("name"),
        (String) call.get("kind"),
        (String) call.get("body"),
        call.get("args") == null ? new LinkedHashMap<>() : (Map<String, Object>) call.get("args"),
        or((List<String>) spec.get("capture_tables")),
        spec.get("mask") == null ? Map.of() : (Map<String, List<String>>) spec.get("mask"),
        (String) spec.get("note"),
        call.get("out") == null ? List.of() : new ArrayList<>(((Map<String, Object>) call.get("out")).keySet()));
  }

  /** The Oracle capture always writes both keys, with nulls where the scenario said nothing. */
  private static Map<String, Object> normalisePinned(Map<String, Object> pinned) {
    Map<String, Object> out = new LinkedHashMap<>();
    out.put("sysdate", pinned.get("sysdate"));
    out.put("sequences", pinned.getOrDefault("sequences", new LinkedHashMap<>()));
    return out;
  }

  private static <T> List<T> or(List<T> value) {
    return value == null ? List.of() : value;
  }

  /**
   * What a block scenario takes out of the record the routine returned, as {OUT bind: record field}.
   *
   * <p>A routine returning a `%ROWTYPE` cannot be bound straight out of Oracle, so those scenarios wrap the call
   * in an anonymous block that projects the row down to a few fields. The ScalarDB side has the whole record in
   * hand and has to make the same projection, or it would be compared against something the Oracle capture never
   * contained.
   *
   * <p>Only the exact shape the corpus uses is read. A block doing anything else returns an empty map, and the
   * caller reports the scenario as one it cannot run rather than guessing what the block meant.
   */
  public Map<String, String> projection() {
    Map<String, String> out = new LinkedHashMap<>();
    if (!"block".equals(kind) || body == null) return out;
    java.util.regex.Matcher matcher = PROJECTION.matcher(body);
    while (matcher.find()) {
      out.put(matcher.group(1), matcher.group(3));
    }
    java.util.regex.Matcher isNull = IS_NULL.matcher(body);
    while (isNull.find()) {
      out.put(isNull.group(1), IS_NULL_OF_RESULT);
    }
    return out;
  }

  /** True when the block does something beyond calling the routine and projecting fields out of the result. */
  public boolean blockIsMoreThanAProjection() {
    if (!"block".equals(kind) || body == null) return false;
    String stripped = IS_NULL.matcher(PROJECTION.matcher(body).replaceAll("")).replaceAll("");
    // what is left should be the DECLARE of the record, the call assigning into it, and BEGIN / END
    return !stripped.replaceAll("(?s)DECLARE.*?BEGIN", "").replaceAll("\\s+", "")
        .matches("(?i)\\w+:=[\\w.]+\\([^)]*\\);END;");
  }

  /**
   * 実際に呼ぶ module。**`call` が名指すもの**であって、`unit` ではない。
   *
   * <p>ふつうは同じだが、trigger のシナリオは違う: `trigger_orders_audit_on_status` は
   * `unit: trg_orders_audit` を**確かめる**ために `pkg_stock_reserve.claim_batch` を**呼ぶ**。移行先に
   * trigger は無く、掛かるのは書き込む側が呼ぶときだけなので、書き込み経路を通すこの形が正しい。
   * `unit` から引いていたので、存在しない `trgOrdersAudit` を探して「比較できない」になっていた。
   */
  public String callUnit() {
    if (!"procedure".equals(kind) && !"function".equals(kind) || call == null || call.isBlank()) return unit;
    int dot = call.lastIndexOf('.');
    return dot < 0 ? call : call.substring(0, dot);
  }

  /** 実際に呼ぶ routine。`callUnit` と同じ理由で `call` から採る。 */
  public String callRoutine() {
    if (!"procedure".equals(kind) && !"function".equals(kind) || call == null || call.isBlank()) return routine;
    int dot = call.lastIndexOf('.');
    return dot < 0 ? call : call.substring(dot + 1);
  }

  /**
   * The generated method's stem: `callRoutine`, and for an overloaded routine the overload's number after it.
   *
   * <p>Overloads are numbered in declaration order (`pkg.put~2`, the Java method `put2`). Oracle picks the overload
   * from the named binds by itself, so `call.name` stays `pkg.put`; the scenario says which one it means in
   * `routine: put~2`, which is also what ties the capture to that overload's verdict.
   */
  public String callMethod() {
    int mark = routine == null ? -1 : routine.lastIndexOf('~');
    boolean numbered = mark >= 0 && routine.substring(0, mark).equalsIgnoreCase(callRoutine())
        && routine.substring(mark + 1).chars().allMatch(Character::isDigit) && mark + 1 < routine.length();
    return numbered ? callRoutine() + routine.substring(mark + 1) : callRoutine();
  }

  /** The same scenario with its arguments replaced (the harness fills omitted DEFAULTs from the setup file, #41). */
  public Scenario withArgs(Map<String, Object> replaced) {
    return new Scenario(name, unit, routine, pinned, setup, call, kind, body, new java.util.LinkedHashMap<>(replaced),
        captureTables, mask, note, outs);
  }

  /** The PL/SQL routine's arguments, in declaration order. */
  public List<Object> arguments() {
    return new ArrayList<>(args.values());
  }
}
