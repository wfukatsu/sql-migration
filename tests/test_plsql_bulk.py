"""#14: `SELECT ... BULK COLLECT INTO` と、それを回す `FORALL` を 1 つの走査ループにする。

Oracle のこの形は「表から N 行を配列へ読み、その配列で N 回 DML する」である。書き換えないと
`BULK COLLECT INTO` を持つ SELECT は 1 行の `SELECT INTO` として扱われ、**0 件と複数件が元に無い
例外になる**（実測で `-1422 TOO_MANY_ROWS` が出ていた）。

ここで固定するのは、書き換えが**何を保つか**と、**何を書き換えないか**である。配列の使い方が
「i 番目の値を読む」以外なら、行を回す形は同じことをしない。
"""

from __future__ import annotations

import pathlib
import textwrap

import pytest

from plsql.lower import _walk, lower_source
from plsql.symbols import OracleSchema

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "plsql"
SRC = FIXTURES / "src"


@pytest.fixture(scope="module")
def schema():
    return OracleSchema.from_ddl(SRC / "schema.sql")


@pytest.fixture(scope="module")
def archive_lines(schema):
    modules, _ = lower_source(SRC / "pkg_bulk_load.pkb", schema)
    return next(r for r in modules[0].routines if r.id.endswith("archive_lines"))


def lower(tmp_path, schema, source: str):
    path = tmp_path / "p.prc"
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    modules, _ = lower_source(path, schema)
    return modules[0].routines[0]


# --- 書き換えるもの --------------------------------------------------------------------------------

def test_the_pair_becomes_one_loop_over_the_rows(archive_lines):
    loop = archive_lines.body[0]
    assert loop.kind == "Loop" and loop.loop_kind == "cursor-for" and loop.variable == "r"
    assert loop.query.original_sql == \
        "SELECT product_id, qty FROM order_lines WHERE order_id = p_order_id"
    assert "BULK COLLECT" not in loop.query.original_sql
    assert [s.kind for s in loop.body] == ["SqlOperation"]


def test_the_subscripted_collections_become_the_rows_columns(archive_lines):
    """`v_products(i)` は 1 行目の `product_id`、`v_qtys(i)` は 2 行目の `qty`——位置で対応する。"""
    insert = archive_lines.body[0].body[0].original_sql
    assert "r.product_id" in insert and "-r.qty" in insert
    assert "v_products" not in insert and "v_qtys" not in insert


def test_the_rewrite_says_what_it_changed(archive_lines):
    """FORALL は 1 往復、ループは行ごとに 1 回である。答えは変わらないが、性能は変わる。"""
    codes = [d.code for d in archive_lines.body[0].diagnostics]
    assert "BULK_CHUNKED" in codes


def test_the_statement_that_raised_too_many_rows_is_gone(archive_lines):
    """1 行の SELECT INTO として扱われていたので、2 行目があると -1422 になっていた。"""
    assert not [s for s in _walk(archive_lines.body)
                if s.kind == "SqlOperation" and "BULK COLLECT" in (s.original_sql or "")]


# --- 書き換えないもの ------------------------------------------------------------------------------

NOT_REWRITTEN = {
    "配列を回す FORALL が続かない": """\
        CREATE OR REPLACE PROCEDURE p(p_order_id IN NUMBER, p_first OUT NUMBER) IS
          TYPE t_qty IS TABLE OF NUMBER INDEX BY PLS_INTEGER;
          v_qtys t_qty;
        BEGIN
          SELECT qty BULK COLLECT INTO v_qtys FROM order_lines WHERE order_id = p_order_id;
          IF v_qtys.COUNT > 0 THEN p_first := v_qtys(1); END IF;
        END p;
        /
    """,
    "本体が配列そのものを読む": """\
        CREATE OR REPLACE PROCEDURE p(p_order_id IN NUMBER) IS
          TYPE t_qty IS TABLE OF NUMBER INDEX BY PLS_INTEGER;
          v_qtys t_qty;
        BEGIN
          SELECT qty BULK COLLECT INTO v_qtys FROM order_lines WHERE order_id = p_order_id;
          FORALL i IN 1 .. v_qtys.COUNT
            INSERT INTO inventory_tx (entry_id, product_id, delta_qty, reason, created_at)
            VALUES (1, 1, v_qtys.COUNT, 'X', SYSDATE);
        END p;
        /
    """,
    "添字が式である": """\
        CREATE OR REPLACE PROCEDURE p(p_order_id IN NUMBER) IS
          TYPE t_qty IS TABLE OF NUMBER INDEX BY PLS_INTEGER;
          v_qtys t_qty;
        BEGIN
          SELECT qty BULK COLLECT INTO v_qtys FROM order_lines WHERE order_id = p_order_id;
          FORALL i IN 1 .. v_qtys.COUNT
            INSERT INTO inventory_tx (entry_id, product_id, delta_qty, reason, created_at)
            VALUES (1, 1, v_qtys(i + 1), 'X', SYSDATE);
        END p;
        /
    """,
    "射影と配列の数が合わない": """\
        CREATE OR REPLACE PROCEDURE p(p_order_id IN NUMBER) IS
          TYPE t_qty IS TABLE OF NUMBER INDEX BY PLS_INTEGER;
          v_qtys t_qty;
        BEGIN
          SELECT product_id, qty BULK COLLECT INTO v_qtys FROM order_lines WHERE order_id = p_order_id;
          FORALL i IN 1 .. v_qtys.COUNT
            INSERT INTO inventory_tx (entry_id, product_id, delta_qty, reason, created_at)
            VALUES (1, 1, v_qtys(i), 'X', SYSDATE);
        END p;
        /
    """,
}


@pytest.mark.parametrize("why", sorted(NOT_REWRITTEN))
def test_a_shape_the_loop_would_not_reproduce_is_left_alone(tmp_path, schema, why):
    """残せば `BULK-001` が捕まえ、生成器が拒否する——黙って違うことをするより良い。"""
    routine = lower(tmp_path, schema, NOT_REWRITTEN[why])
    assert [s for s in _walk(routine.body)
            if s.kind == "SqlOperation" and "BULK COLLECT" in (s.original_sql or "")], \
        "書き換えてはいけない形を書き換えている"


def test_save_exceptions_is_not_this_rewrites_business(schema):
    """`FORALL ... SAVE EXCEPTIONS` は部分失敗を許す原子性の話で、業務要件である（BULK-002）。"""
    modules, _ = lower_source(SRC / "pkg_bulk_load.pkb", schema)
    restock = next(r for r in modules[0].routines if r.id.endswith("restock"))
    assert [s for s in _walk(restock.body) if s.kind == "Loop" and s.loop_kind == "forall"]


# --- コレクション引数を回す FORALL（#14 / MERGE） ------------------------------------------------

def test_a_collection_parameter_keeps_its_element_type(schema):
    """`TYPE t_id_list IS TABLE OF NUMBER(19)` の要素型が無いと、引数は Java で `Object` にしか
    ならない——`List<BigDecimal>` と書けない。名前だけでは移行先の型を決められない。"""
    from plsql.report import analyse as build_analysis

    corpus = build_analysis(SRC, SRC / "schema.sql", scalardb_schema=FIXTURES / "scalardb-schema.json")
    routine = next(r for _, r in corpus.routines() if r.id == "pkg_customer_import.import")
    types = {p.name: p.type.resolved for p in routine.parameters}
    assert types == {"p_ids": "TABLE OF NUMBER(19)", "p_names": "TABLE OF VARCHAR2(100)"}


def test_the_forall_becomes_a_loop_over_the_collection(schema):
    """`FORALL i IN 1 .. p_ids.COUNT` は、その要素を回す Java のループである。FORALL は 1 往復、
    ループは要素ごとに 1 回——答えは同じで、性能が変わる（#14 の走査ループと同じ代償）。"""
    from plsql.gen_java.service import generate_module
    from plsql.report import analyse as build_analysis

    corpus = build_analysis(SRC, SRC / "schema.sql", scalardb_schema=FIXTURES / "scalardb-schema.json")
    module = next(m for m in corpus.program.modules if m.name == "pkg_customer_import")
    java = generate_module(module, "g.app", "g.infra", "g.domain").file.render()
    assert "public void import_(List<BigDecimal> pIds, List<String> pNames)" in java
    assert "for (int i = 0; i < pIds.size(); i++)" in java
    assert "repository.import_Stmt2(pIds.get(i), pNames.get(i))" in java
    assert "pIds(i)" not in java, "要素参照が method 呼び出しとして描画されている"


def test_the_merge_becomes_an_upsert(schema):
    """`MERGE ... WHEN MATCHED UPDATE ... WHEN NOT MATCHED INSERT` は、キーで届く UPSERT である。
    枝の中の `SYSDATE` も持ち上げる——外側だけ見ていると、同じ形が UPDATE なら通るのに MERGE では
    拒否される。"""
    from plsql.report import analyse as build_analysis

    corpus = build_analysis(SRC, SRC / "schema.sql", scalardb_schema=FIXTURES / "scalardb-schema.json")
    routine = next(r for _, r in corpus.routines() if r.id == "pkg_customer_import.import")
    merge = next(s for s in _walk(routine.body) if s.kind == "SqlOperation")
    assert merge.target_sql == ["UPSERT INTO customers (customer_id, name, tier, registered_on) "
                                "VALUES (:p_ids_i, :p_names_i, 'BRONZE', :expr3)"]
    assert [b.expression for b in merge.binds if b.expression] == ["SYSDATE"]
    # UPSERT は列挙した列をすべて書く。既存行に当たると `WHEN MATCHED` が触っていない列まで変わる
    # ——比較ハーネスが実測した（tier: GOLD -> BRONZE、registered_on がずれる）。警告は**どの列か**
    # を名指しする
    warning = next(d for d in merge.diagnostics if d.code == "MERGE")
    assert "tier" in warning.message and "registered_on" in warning.message
    assert warning.severity == "WARN", "severity を上げるかは #26 の判断（I10 と計測報告に及ぶ）"


# --- #14: `FETCH ... BULK COLLECT INTO v LIMIT n`（分割読み、2026-09-18）------------------------

CHUNKED = """\
CREATE OR REPLACE PROCEDURE prc_count_open(p_limit IN PLS_INTEGER, p_count OUT NUMBER) IS
  CURSOR c IS SELECT order_id FROM orders WHERE status = 'NEW';
  v_ids t_id_list;
BEGIN
  p_count := 0;
  OPEN c;
  LOOP
    FETCH c BULK COLLECT INTO v_ids LIMIT p_limit;
    EXIT WHEN v_ids.COUNT = 0;
    p_count := p_count + v_ids.COUNT * 2;
  END LOOP;
  CLOSE c;
END prc_count_open;
/
"""
# 件数を足すだけの本体は COUNT(*) に書き換わる（#19 / 2026-09-19）。分割読みそのものを見るテストは、
# 数える以外のことをする本体（上の `* 2`）で見る
COUNT_ONLY = CHUNKED.replace("v_ids.COUNT * 2", "v_ids.COUNT")


def _lowered(tmp_path, source: str):
    """1 本だけ入った小さな入力を、書き換えまで通した IR で返す。"""
    import pathlib

    from plsql.report import analyse as build_analysis

    fixtures = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "plsql"
    root = tmp_path / "src"
    root.mkdir(exist_ok=True)
    (root / "schema.sql").write_text((fixtures / "src" / "schema.sql").read_text(encoding="utf-8"),
                                     encoding="utf-8")
    (root / "prc_count_open.prc").write_text(source, encoding="utf-8")
    analysis = build_analysis(root, root / "schema.sql",
                              scalardb_schema=fixtures / "scalardb-schema.json")
    return next(r for _, r in analysis.routines() if r.id == "prc_count_open")


def test_the_limit_is_not_an_into_target(tmp_path):
    """`LIMIT p_limit` の `p_limit` も文法上は `Variable_name` である。INTO の対象として数えると
    **代入先が 1 つ増えたように見える**——数えていた（2026-09-18 に直した）。"""
    # 書き換えを見送る形（ループの後で配列を読む）を使う。書き換わったあとでは FETCH が残らない
    source = tmp_path / "prc_count_open.prc"
    source.write_text(CHUNKED.replace("  CLOSE c;", "  CLOSE c;\n  p_count := p_count + v_ids.COUNT;"),
                      encoding="utf-8")
    modules, _ = lower_source(source)
    routine = modules[0].routines[0]
    fetch = next(s for s in _walk(routine.body) if s.kind == "Fetch")
    assert fetch.into_targets == ["v_ids"]
    assert fetch.bulk_limit == "p_limit"


def test_the_chunked_read_becomes_a_loop_that_hands_out_chunks(tmp_path):
    """行は先にまとめて読む（跨トランザクションの cursor が無い）。残るのは n 件ずつ配るループで、
    **n の意味は変わる**——読み込む量ではなく、配る量になる。"""
    routine = _lowered(tmp_path, CHUNKED)
    loop = next(s for s in _walk(routine.body) if s.kind == "Loop")
    assert loop.loop_kind == "cursor-for" and loop.chunk == "p_limit"
    assert loop.variable == "v_ids"
    assert loop.query is not None and loop.query.cardinality == "MANY"
    assert not [s for s in _walk(routine.body) if s.kind in ("OpenCursor", "Fetch", "CloseCursor")]


def test_the_change_of_meaning_is_recorded(tmp_path):
    """黙って「ただの走査」に潰さない。LIMIT がメモリを守らなくなったことは残す。"""
    routine = _lowered(tmp_path, CHUNKED)
    loop = next(s for s in _walk(routine.body) if s.kind == "Loop")
    message = next(d.message for d in loop.diagnostics if d.code == "BULK_CHUNKED")
    assert "配る" in message and "走査行数の上限" in message


def test_the_body_is_kept_as_it_was_written(tmp_path):
    """`EXIT WHEN v_ids.COUNT = 0` は残す。配る側は空の塊を渡さないので発火しないが、元に書いて
    あるものを落とす理由が無い。"""
    routine = _lowered(tmp_path, CHUNKED)
    loop = next(s for s in _walk(routine.body) if s.kind == "Loop")
    assert [s.kind for s in loop.body] == ["Exit", "Assignment"]


def test_a_collection_read_after_the_loop_is_not_rewritten(tmp_path):
    """Oracle は最後に取った（空の）塊を残す。そこまで同じにはできないので、ループの後で読んで
    いたら書き換えない。"""
    source = CHUNKED.replace("  CLOSE c;", "  CLOSE c;\n  p_count := p_count + v_ids.COUNT;")
    routine = _lowered(tmp_path, source)
    assert [s.kind for s in _walk(routine.body) if s.kind == "Fetch"], "書き換えてしまっている"


def test_the_generated_loop_hands_out_chunks(tmp_path):
    """生成コードまで届いていること。`v_ids.COUNT` は塊の件数である。"""
    from plsql.generate import main as generate

    root = tmp_path / "src"
    root.mkdir()
    import pathlib
    fixtures = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "plsql"
    (root / "schema.sql").write_text((fixtures / "src" / "schema.sql").read_text(encoding="utf-8"),
                                     encoding="utf-8")
    (root / "prc_count_open.prc").write_text(CHUNKED, encoding="utf-8")
    generate([str(root), "--scalardb-schema", str(fixtures / "scalardb-schema.json"),
              "--out-dir", str(tmp_path / "out"), "--quiet"])
    java = (tmp_path / "out" / "src/main/java/com/example/migrated/application"
            / "PrcCountOpenService.java").read_text(encoding="utf-8")
    assert "Plsql.chunks(repository.prcCountOpenLoop" in java
    assert "for (List<PrcCountOpenLoop3Row> vIds :" in java
    assert "vIds.size()" in java
    assert java.count("vIds") and "List<BigDecimal> vIds" not in java, \
        "塊のループ変数を、ローカルとしても宣言している（同じ名前が 2 つ）"



# --- #19: 件数を数えるだけの分割読みは COUNT(*) にする（2026-09-19） ------------------------------

def test_a_chunked_read_that_only_counts_becomes_a_count(tmp_path):
    """行を 1 行も持たずに同じ答えが出る。行数の上限を決める必要そのものが無くなる。"""
    routine = _lowered(tmp_path, COUNT_ONLY)
    body = _walk(routine.body)
    assert not [s for s in body if s.kind == "Loop"]
    count = next(s for s in body if s.kind == "SqlOperation")
    assert count.original_sql == "SELECT COUNT(*) FROM orders WHERE status = 'NEW'"
    assert any(s.kind == "Assignment" and s.target == "p_count" and "v_count_1" in s.expression for s in body)


def test_a_limit_that_is_not_positive_raises_what_oracle_raises(tmp_path):
    """2026-09-19 に Oracle 23ai で実測: LIMIT 0 / 負は ORA-06502、LIMIT NULL は ORA-06500。
    以前は「0 件で抜けるのと同じ」と書いていた——実測せずに書いた誤りだった。"""
    routine = _lowered(tmp_path, COUNT_ONLY)
    raised = {s.error_code for s in _walk(routine.body) if s.kind == "Raise"}
    assert raised == {-6500, -6502}
