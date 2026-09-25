"""#12 §0「検証で追う」の照合を、Java として生成する（2026-09-19 の決定）。

出すのは 1 クラス（`TriggerChecks`）で、trigger ごと・型ごとに method が並ぶ。**回すのは呼び出し側**
である——間隔（A / C は日次、B / D は短い間隔）は `INTERVALS` に書いてあるが、スケジューラは持たない。
生成コードがトランザクションを開かない（計画 §9）のと同じ理由で、いつ・どの境界で回すかはアプリが決める。

照合は**表を全件読む**（パーティションをまたぐ走査）。移行先を JDBC バックエンドに限定した（#20）ので
そのまま使える。件数が多い表では、照合そのものの間隔とコストが釣り合うかを確かめること。
"""

from __future__ import annotations

from ..trigger_checks import Check
from .emit import JavaFile
from .expr import translate
from .types import java_class_name, java_name


def generate(checks: list[Check], package: str, domain_package: str,
             types: dict[tuple[str, str], tuple[str, int]]) -> JavaFile | None:
    """`types`: (表, 列) -> (ScalarDB の型, 桁)。金額列は scaled の規約で整数になっているので、
    読むときに戻す必要がある。"""
    usable = [c for c in checks if c.refused is None]
    if not checks:
        return None
    _REJECTING.clear()
    _REJECTING.update(c.trigger for c in usable if c.kind == "B")
    file = JavaFile(package=package, name="TriggerChecks", source="(triggers)")
    file.add_import("java.sql.Connection", "java.sql.PreparedStatement", "java.sql.ResultSet",
                    "java.sql.SQLException", "java.util.ArrayList", "java.util.LinkedHashMap",
                    "java.util.List", "java.util.Map", "java.util.Objects",
                    "com.scalar.migrate.plsql.Plsql", "com.scalar.migrate.plsql.Sequences",
                    "com.scalar.migrate.plsql.AuditContext", "java.math.BigDecimal")
    guards = sorted({c.trigger for c in usable if c.kind == "D"})
    file.comment("#12 §0「検証で追う」: 移行先の trigger を通らなかった書き込みを、データだけで見つける。\n"
                 "回すのは呼び出し側である（間隔は INTERVALS）。照合は表を全件読むので、JDBC バックエンド\n"
                 "前提である（#20）。見つけたものは Drift として返す——A は補える（backfill）、B / D は\n"
                 "人が判断する（入ってしまった値を戻してよいかは業務の判断）。")
    with file.block("public class TriggerChecks") as f:
        f.comment("照合ごとの間隔（#12 §0 の決定 2b）。A / C は日次、B / D は短い間隔")
        entries = ", ".join(f'"{_method(c)}", "{c.interval}"' for c in usable)
        f.line(f"public static final Map<String, String> INTERVALS = Map.of({entries});")
        f.line()
        f.comment("見つかったずれ。detail は人が読むための説明である")
        f.line("public record Drift(String trigger, String kind, String key, String detail) {}")
        f.line()
        f.line("private final Connection connection;")
        f.line("private final Sequences sequences;")
        for guard in guards:
            f.add_import(f"{package}.{java_class_name(guard)}Service")
            f.line(f"private final {java_class_name(guard)}Service {java_name(guard)};")
        f.line()
        parameters = ["Connection connection", "Sequences sequences"] + \
            [f"{java_class_name(g)}Service {java_name(g)}" for g in guards]
        with f.block(f"public TriggerChecks({', '.join(parameters)})") as g:
            g.line("this.connection = connection;")
            g.line("this.sequences = sequences;")
            for guard in guards:
                g.line(f"this.{java_name(guard)} = {java_name(guard)};")
        audits = {}
        for check in checks:
            f.line()
            if check.refused:
                f.comment(f"{check.trigger}（{check.kind} 型）: 照合を組めない——{check.refused}")
                continue
            if check.audit is not None and check.trigger not in audits:
                audits[check.trigger] = check.audit
                _audit_reads(f, check, types)
                f.line()
            {"A": _unaudited, "B": _rejected, "C": _max_key, "D": _violations}[check.kind](
                f, check, types, domain_package)
    return file


# 照合の控え（A-3）。監査行が削除されても、最後に監査した値をここに残す
BASELINE = "trigger_check_baseline"

# B 型の照合を持つ trigger。A の補完は、B が拒否する行を飛ばす必要がある
_REJECTING: set[str] = set()


def _method(check: Check) -> str:
    suffix = {"A": "Unaudited", "B": "Rejected", "C": "MaxKey", "D": "Violations"}[check.kind]
    return f"{java_name(check.trigger)}{suffix}"


def _read(value: str, table: str, column: str, types) -> str:
    kind, scale = types.get((table, column), ("", 0))
    if kind.upper() in ("BIGINT", "INT", "DOUBLE", "FLOAT"):
        return f'Plsql.read({value}, "{kind.upper()}", {scale})'
    return value


def _audit_reads(file: JavaFile, check: Check, types) -> None:
    """今の値と、最後に監査した値を読む。A と B が使う。"""
    audit = check.audit
    prefix = java_name(check.trigger)
    column_of = {role: column for column, role in audit.roles.items()}
    filters = " AND ".join(f"{c} = '{v}'" for c, v in sorted(audit.literals.items()))
    order = [column_of[r] for r in ("time", "id") if r in column_of]
    file.comment(f"{check.trigger}: {check.table}.{audit.watched} の今の値（主キー -> 値）")
    with file.block(f"private Map<String, Object> {prefix}Current() throws SQLException") as f:
        f.line("Map<String, Object> out = new LinkedHashMap<>();")
        sql = f"SELECT {audit.key}, {audit.watched} FROM {check.table}"
        with f.block(f'try (PreparedStatement statement = connection.prepareStatement("{sql}"); '
                     f"ResultSet rows = statement.executeQuery())") as g:
            with g.block("while (rows.next())") as h:
                key = _read("rows.getObject(1)", check.table, audit.key, types)
                value = _read("rows.getObject(2)", check.table, audit.watched, types)
                h.line(f"out.put(Plsql.text({key}), {value});")
        f.line("return out;")
    file.line()
    file.comment(f"{check.trigger}: 最後に監査した値（主キー -> [値, 時刻]）。並びは "
                 f"{' / '.join(order) or '（無し）'}——hi/lo の採番は時刻順とは限らないので、時刻を先に見る")
    with file.block(f"private Map<String, Object[]> {prefix}LastAudited() throws SQLException") as f:
        f.line("Map<String, Object[]> out = new LinkedHashMap<>();")
        # A-3（2026-09-19）: 控えの表から始める。監査行が削除（prc_purge_audit）で消えても、最後に
        # 監査した値はここに残る——残さないと、しばらく変わっていない行が照合から外れる
        baseline = (f"SELECT key_value, last_value, last_at FROM {BASELINE} "
                    f"WHERE trigger_name = '{check.trigger}'")
        with f.block(f'try (PreparedStatement statement = connection.prepareStatement("{baseline}"); '
                     f"ResultSet rows = statement.executeQuery())") as g:
            with g.block("while (rows.next())") as h:
                h.line(f"out.put(String.valueOf(rows.getObject(1)), new Object[] {{rows.getObject(2), "
                       f"{', '.join(['rows.getObject(3)'] + ['null'] * (len(order) - 1)) or 'null'}}});")
        selected = [column_of["key"], column_of["new"]] + order
        sql = f"SELECT {', '.join(selected)} FROM {audit.table}" + (f" WHERE {filters}" if filters else "")
        with f.block(f'try (PreparedStatement statement = connection.prepareStatement("{sql}"); '
                     f"ResultSet rows = statement.executeQuery())") as g:
            with g.block("while (rows.next())") as h:
                h.line("String key = String.valueOf(rows.getObject(1));")
                h.line(f"Object[] seen = new Object[] {{rows.getObject(2), "
                       f"{', '.join(f'rows.getObject({i + 3})' for i in range(len(order))) or 'null'}}};")
                h.line("Object[] kept = out.get(key);")
                # 辞書順: 時刻が新しいもの。時刻が同じなら id が大きいもの。OR で並べると、時刻が
                # 古くても id が大きいだけで勝ってしまう（hi/lo の採番では実際に起こる）
                compare = "false"
                for i in reversed(range(len(order))):
                    compare = (f"Plsql.gt(seen[{i + 1}], kept[{i + 1}])" if compare == "false" else
                               f"Plsql.gt(seen[{i + 1}], kept[{i + 1}]) || "
                               f"(Objects.equals(seen[{i + 1}], kept[{i + 1}]) && ({compare}))")
                h.line(f"if (kept == null || {compare}) out.put(key, seen);")
        f.line("return out;")


def _remember(file: JavaFile, check: Check) -> None:
    """控えの表を、最後に監査した値で更新する（A-3）。**今の値ではない**——今の値を控えると、
    ずれを「監査済み」として洗い流してしまう。B が拒否する行を補わないのと同じ理由である。"""
    prefix = java_name(check.trigger)
    upsert = f"UPSERT INTO {BASELINE} (trigger_name, key_value, last_value, last_at) VALUES (?, ?, ?, ?)"
    file.comment(f"控えの表（{BASELINE}）を、最後に監査した値で更新する（A-3）。**今の値ではない**——\n"
                 f"今の値を控えると、ずれを監査済みとして洗い流してしまう。監査を全件読むので、補完と同じ\n"
                 f"トランザクションでは回せない（同じトランザクションで書いた表の走査になる: DB-CORE-10106）")
    with file.block(f"public int {prefix}Remember() throws SQLException") as f:
        f.line("int written = 0;")
        with f.block(f"for (Map.Entry<String, Object[]> entry : {prefix}LastAudited().entrySet())") as g:
            with g.block(f'try (PreparedStatement statement = connection.prepareStatement("{upsert}"))') as h:
                h.line(f'statement.setObject(1, "{check.trigger}");')
                h.line("statement.setObject(2, entry.getKey());")
                h.line("statement.setObject(3, entry.getValue()[0] == null ? null : String.valueOf(entry.getValue()[0]));")
                h.line("statement.setObject(4, entry.getValue()[1]);")
                h.line("written += statement.executeUpdate();")
        f.line("return written;")


def _unaudited(file: JavaFile, check: Check, types, domain_package: str) -> None:
    audit = check.audit
    prefix = java_name(check.trigger)
    now = "Plsql.text(entry.getValue())" if audit.text else "entry.getValue() == null ? null : String.valueOf(entry.getValue())"
    file.comment(f"A 型（{check.interval}）: {check.table}.{audit.watched} が、最後に監査した値と違う行。\n"
                 f"{check.trigger} を通らずに変わった。監査行が 1 行も無い行は、比べる相手が無いので見ない")
    with file.block(f"public List<Drift> {_method(check)}() throws SQLException") as f:
        f.line("List<Drift> out = new ArrayList<>();")
        f.line(f"Map<String, Object[]> last = {prefix}LastAudited();")
        with f.block(f"for (Map.Entry<String, Object> entry : {prefix}Current().entrySet())") as g:
            g.line("Object[] audited = last.get(entry.getKey());")
            g.line("if (audited == null) continue;")
            g.line(f"String now = {now};")
            with g.block("if (!Objects.equals(now, audited[0]))") as h:
                h.line(f'out.add(new Drift("{check.trigger}", "A", entry.getKey(), '
                       f'"監査は " + audited[0] + " で止まっているが、いまは " + now));')
        f.line("return out;")
    file.line()
    _backfill(file, check, types)
    file.line()
    _remember(file, check)


def _backfill(file: JavaFile, check: Check, types) -> None:
    """A のずれを補う（#12 §0 の決定 4）。**印を付ける**——誰が・いつは分からないので、書いた主体は
    `BACKFILL`、時刻は補った時刻である。"""
    audit = check.audit
    values, binds = [], []
    for column in audit.columns:
        role = audit.roles[column]
        if role == "literal":
            values.append(f"'{audit.literals[column]}'")
        elif role == "who":
            values.append("'BACKFILL'")
        else:
            values.append("?")
            binds.append({"id": f'sequences.next("{audit.sequence}")', "key": "drift.key()",
                          "old": "previous", "new": "current",
                          "time": 'Plsql.bind(audit.now(), "TIMESTAMP", 0)'}[role])
    sql = f"INSERT INTO {audit.table} ({', '.join(audit.columns)}) VALUES ({', '.join(values)})"
    file.comment(f"A 型のずれを補う（決定 4）。**補った行には印を付ける**: 書いた主体は 'BACKFILL'、時刻は\n"
                 f"補った時刻である——元の「誰が」「いつ」は分からない。前の値は最後に監査した値、後の値は今の値")
    guarded = check.trigger in _REJECTING
    if guarded:
        file.comment("**B 型で拒否されるはずの行は補わない。** 補うと最後に監査した値が今の値になり、拒否の照合\n"
                     "（前 = 最後に監査した値）から消える——入ってはいけなかった値を、監査済みに見せてしまう")
    with file.block(f"public int {java_name(check.trigger)}Backfill(List<Drift> drifts, AuditContext audit) "
                    "throws SQLException") as f:
        f.line("int written = 0;")
        if guarded:
            f.line("java.util.Set<String> rejected = new java.util.HashSet<>();")
            f.line(f"for (Drift found : {java_name(check.trigger)}Rejected()) rejected.add(found.key());")
        f.line(f"Map<String, Object[]> last = {java_name(check.trigger)}LastAudited();")
        f.line(f"Map<String, Object> now = {java_name(check.trigger)}Current();")
        with f.block("for (Drift drift : drifts)") as g:
            g.line(f'if (!"{check.trigger}".equals(drift.trigger()) || !"A".equals(drift.kind())) continue;')
            if guarded:
                g.line("if (rejected.contains(drift.key())) continue;   // 人が判断するまで補わない")
            g.line("Object previous = last.get(drift.key())[0];")
            current = "Plsql.text(now.get(drift.key()))" if audit.text else "String.valueOf(now.get(drift.key()))"
            g.line(f"String current = {current};")
            with g.block(f'try (PreparedStatement statement = connection.prepareStatement("{sql}"))') as h:
                for index, bind in enumerate(binds, start=1):
                    h.line(f"statement.setObject({index}, {bind});")
                h.line("written += statement.executeUpdate();")
        f.line("return written;")


def _rejected(file: JavaFile, check: Check, types, domain_package: str) -> None:
    """B 型: trigger 自身の拒否条件を、「最後に監査した値 → 今の値」に当てる。"""
    audit = check.audit
    prefix = java_name(check.trigger)
    scope = {}
    for qualifier, name in (("NEW", "current"), ("OLD", "previous")):
        scope[f"{qualifier}.{audit.watched}"] = name
        scope[f":{qualifier}.{audit.watched}"] = name
    rendered = translate(check.condition, scope)
    file.comment(f"B 型（{check.interval}）: {check.trigger} なら拒否していたはずの値が、もう入っている行。\n"
                 f"条件は trigger のもの（{check.condition}）を、前 = 最後に監査した値、後 = 今の値として当てる。\n"
                 f"**補えない**——入ってしまった値を戻してよいかは業務の判断である")
    with file.block(f"public List<Drift> {_method(check)}() throws SQLException") as f:
        f.line("List<Drift> out = new ArrayList<>();")
        if rendered.unknown:
            f.comment(f"条件を読めない: {', '.join(rendered.unknown)}")
            f.line(f'throw new UnsupportedOperationException("{check.trigger}: 拒否の条件を読めない");')
            return
        f.line(f"Map<String, Object[]> last = {prefix}LastAudited();")
        with f.block(f"for (Map.Entry<String, Object> entry : {prefix}Current().entrySet())") as g:
            g.line("Object[] audited = last.get(entry.getKey());")
            g.line("if (audited == null) continue;")
            g.line("Object current = entry.getValue();")
            previous = "Plsql.dec(audited[0])" if audit.text else "audited[0]"
            g.line(f"Object previous = {previous};")
            with g.block(f"if ({rendered.java})") as h:
                h.line(f'out.add(new Drift("{check.trigger}", "B", entry.getKey(), '
                       f'"{check.condition} に当たる: " + previous + " -> " + current));')
        f.line("return out;")


def _max_key(file: JavaFile, check: Check, types, domain_package: str) -> None:
    column = check.key[0]
    file.comment(f"C 型（{check.interval}）: {check.table}.{column} の最大値。採番（{check.sequence}）の次の値が\n"
                 f"これ以下なら、採番を通らずに作られた行があり、次の INSERT が主キー重複で落ちる。\n"
                 f"採番の次の値を最大値より先へ進めるのは、自動でしてよい（決定 4 の範囲外だが害が無い）")
    with file.block(f"public BigDecimal {_method(check)}() throws SQLException") as f:
        sql = f"SELECT MAX({column}) FROM {check.table}"
        with f.block(f'try (PreparedStatement statement = connection.prepareStatement("{sql}"); '
                     f"ResultSet rows = statement.executeQuery())") as g:
            read = _read("rows.getObject(1)", check.table, column, types)
            g.line(f"return rows.next() ? Plsql.dec({read}) : null;")


def _violations(file: JavaFile, check: Check, types, domain_package: str) -> None:
    """D 型: 書かない本体を、今ある行ごとに呼ぶ。拒否されたら違反の候補である。"""
    file.add_import(f"{domain_package}.MigratedException")
    # reads has one entry per correlation the body takes (new.x and old.x both read column x): select each once
    selected = check.key + [c for c in dict.fromkeys(check.reads) if c not in check.key]
    file.comment(f"D 型（{check.interval}）: 今ある行を {check.trigger} に通すと拒否される行。\n"
                 f"**当時は正しかった行も含む**（支払いのあとで取消された注文など）——判断は人に回す")
    with file.block(f"public List<Drift> {_method(check)}() throws Exception") as f:
        f.line("List<Drift> out = new ArrayList<>();")
        sql = f"SELECT {', '.join(selected)} FROM {check.table}"
        with f.block(f'try (PreparedStatement statement = connection.prepareStatement("{sql}"); '
                     f"ResultSet rows = statement.executeQuery())") as g:
            with g.block("while (rows.next())") as h:
                key = _read("rows.getObject(1)", check.table, check.key[0], types)
                h.line(f"String key = Plsql.text({key});")
                arguments = []
                for name in check.arguments or [f"NEW.{c}" for c in check.reads]:
                    if not name.upper().startswith(("NEW.", "OLD.")):
                        # INSERTING / UPDATING('列') など: 今ある行は書かれている最中ではないので false
                        arguments.append("false")
                        continue
                    column = name.partition(".")[2].lower()
                    index = selected.index(column) + 1
                    kind, _ = types.get((check.table, column), ("", 0))
                    raw = _read(f"rows.getObject({index})", check.table, column, types)
                    arguments.append(f"Plsql.dec({raw})" if kind.upper() in ("BIGINT", "INT", "DOUBLE", "FLOAT")
                                     else f"(String) {raw}")
                with h.block("try") as i:
                    i.line(f"{java_name(check.trigger)}.body({', '.join(arguments)});")
                with h.block("catch (MigratedException rejected)") as i:
                    i.line(f'out.add(new Drift("{check.trigger}", "D", key, rejected.getMessage()));')
        f.line("return out;")



def generate_job(checks: list[Check], package: str) -> JavaFile | None:
    """照合を回すジョブの雛形（A-2 / 2026-09-19）。

    **1 回の照合は 1 つの読み取り専用トランザクションで読む。** `orders` と `audit_log` を別々のトランザ
    クションで読むと、その間の書き込みで誤検知が出る。移行後の trigger は書き込みと同じトランザクション
    で呼ばれるので、同じ時点を読めば誤検知は出ない。`Connection.setReadOnly(true)` が ScalarDB の
    読み取り専用トランザクションになることは実クラスタで確かめた（書き込みは DB-CORE-10211 で拒否される）。

    補完と控えの更新は、別々の書き込みトランザクションで行う（控えの更新は監査を全件読むので、補完と
    同じトランザクションでは走査が拒否される）。スケジューラは持たない——いつ回すかは運用が決める。
    """
    usable = [c for c in checks if c.refused is None]
    if not usable:
        return None
    file = JavaFile(package=package, name="TriggerCheckJob", source="(triggers)")
    file.add_import("java.sql.Connection", "java.sql.SQLException", "java.util.ArrayList", "java.util.List",
                    "java.util.LinkedHashMap", "java.util.Map", "java.math.BigDecimal",
                    "com.scalar.migrate.plsql.AuditContext")
    daily = [c for c in usable if c.interval == DAILY_]
    hourly = [c for c in usable if c.interval != DAILY_]
    file.comment("#12 §0 の照合を回すジョブの雛形（A-2）。1 回の照合は 1 つの読み取り専用トランザクションで読む。\n"
                 "日次（A / C）: 照合 -> 補完 -> 控えの更新。**監査の削除（prc_purge_audit）より先に回す**——\n"
                 "削除のあとに控えを更新すると、消えた監査行の値が控えに残らない。\n"
                 "短い間隔（B / D）: 照合だけ。見つけたものは人が判断する。")
    with file.block("public final class TriggerCheckJob") as f:
        f.line("public record Report(List<TriggerChecks.Drift> drifts, Map<String, BigDecimal> maxKeys, "
               "int backfilled, int remembered) {}")
        f.line()
        f.comment("読み取り専用トランザクションが衝突で弾かれたときに、読み直す回数（SERIALIZABLE では起こりうる）")
        f.line("private static final int ATTEMPTS = 3;")
        f.line()
        f.line("private final Connection connection;")
        f.line("private final TriggerChecks checks;")
        f.line()
        with f.block("public TriggerCheckJob(Connection connection, TriggerChecks checks)") as g:
            g.line("this.connection = connection;")
            g.line("this.checks = checks;")
        f.line()
        f.comment("日次（A / C）: 照合 -> 補完 -> 控えの更新")
        with f.block("public Report daily(AuditContext audit) throws Exception") as g:
            g.line("Map<String, BigDecimal> maxKeys = new LinkedHashMap<>();")
            with g.block("List<TriggerChecks.Drift> drifts = readOnly(() ->") as h:
                h.line("List<TriggerChecks.Drift> out = new ArrayList<>();")
                for check in daily:
                    if check.kind == "A":
                        h.line(f"out.addAll(checks.{java_name(check.trigger)}Unaudited());")
                    elif check.kind == "C":
                        h.line(f'maxKeys.put("{check.trigger}", checks.{java_name(check.trigger)}MaxKey());')
                h.line("return out;")
            g.line(");")
            audits = sorted({c.trigger for c in daily if c.kind == "A"})
            backfill = " + ".join(f"checks.{java_name(t)}Backfill(drifts, audit)" for t in audits) or "0"
            remember = " + ".join(f"checks.{java_name(t)}Remember()" for t in audits) or "0"
            g.line(f"int backfilled = write(() -> {backfill});")
            g.comment("補完とは別のトランザクション: 控えの更新は監査を全件読む")
            g.line(f"int remembered = write(() -> {remember});")
            g.line("return new Report(drifts, maxKeys, backfilled, remembered);")
        f.line()
        f.comment("短い間隔（B / D）: 照合だけ。見つけたものは人が判断する")
        with f.block("public Report hourly() throws Exception") as g:
            with g.block("List<TriggerChecks.Drift> drifts = readOnly(() ->") as h:
                h.line("List<TriggerChecks.Drift> out = new ArrayList<>();")
                for check in hourly:
                    suffix = {"B": "Rejected", "D": "Violations"}[check.kind]
                    h.line(f"out.addAll(checks.{java_name(check.trigger)}{suffix}());")
                h.line("return out;")
            g.line(");")
            g.line("return new Report(drifts, Map.of(), 0, 0);")
        f.line()
        f.line("@FunctionalInterface")
        f.line("private interface Work<T> { T run() throws Exception; }")
        f.line()
        with f.block("private <T> T readOnly(Work<T> work) throws Exception") as g:
            with g.block("for (int attempt = 1; ; attempt++)") as h:
                h.line("connection.setReadOnly(true);")
                with h.block("try") as i:
                    i.line("T result = work.run();")
                    i.line("connection.commit();")
                    i.line("return result;")
                with h.block("catch (SQLException e)") as i:
                    i.line("rollbackQuietly();")
                    i.comment("衝突で弾かれた読み取りだけを読み直す。それ以外の誤りは隠さない")
                    i.line('if (attempt >= ATTEMPTS || e.getMessage() == null || '
                           '!e.getMessage().contains("conflict")) throw e;')
                with h.block("finally") as i:
                    i.line("connection.setReadOnly(false);")
        f.line()
        with f.block("private <T> T write(Work<T> work) throws Exception") as g:
            with g.block("try") as h:
                h.line("T result = work.run();")
                h.line("connection.commit();")
                h.line("return result;")
            with g.block("catch (Exception e)") as h:
                h.line("rollbackQuietly();")
                h.line("throw e;")
        f.line()
        with f.block("private void rollbackQuietly()") as g:
            with g.block("try") as h:
                h.line("connection.rollback();")
            with g.block("catch (SQLException ignored)") as h:
                h.comment("トランザクションが始まっていない（DB-SQL-10015）。戻すものが無い")
    return file


DAILY_ = "daily"
