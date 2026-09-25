"""比較できなかったシナリオを比較できるようにする（2026-09-19）。

* 準備行の時計は、**比べない列に入るときだけ**固定した時刻にする
* ブロックが素の DML だけのシナリオ（trigger のシナリオ）は、同じ DML を ScalarDB へ直接流す——
  移行先の trigger は掛からない経路なので（#12 §0）、差が出るのは決めたとおりである
"""

from __future__ import annotations

import pathlib

import pytest
import yaml

from difftest.plsql_setup import direct, pin_masked_clocks
from scalardb_migrate.schema import SchemaRegistry

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCENARIOS = ROOT / "fixtures" / "plsql" / "scenarios"
REGISTRY = SchemaRegistry.from_schema_loader_json(str(ROOT / "fixtures" / "plsql" / "scalardb-schema.json"))
INSERT = "INSERT INTO payments (payment_id, order_id, amount, method, paid_at) VALUES (5001, 1001, 1, 'CARD', SYSTIMESTAMP)"


def test_a_clock_in_a_masked_column_is_pinned():
    """マスクされた列の値は比較に出ないので、どの時刻を入れても答えは変わらない。"""
    pinned = pin_masked_clocks(INSERT, {"payments": ["paid_at"]}, "2026-01-15 09:30:00", REGISTRY)
    assert "SYSTIMESTAMP" not in pinned and "2026-01-15 09:30:00" in pinned


def test_a_clock_in_a_compared_column_is_left_alone():
    """比較に出る列の時計を置き換えると、差を作るのは置き換えの方になる。"""
    assert pin_masked_clocks(INSERT, {}, "2026-01-15 09:30:00", REGISTRY) == INSERT


def test_a_timestamptz_column_gets_an_offset():
    """ScalarDB の TIMESTAMPTZ は offset の無い文字列を読めない（"could not be parsed" で拒否した）。"""
    pinned = pin_masked_clocks(INSERT, {"payments": ["paid_at"]}, "2026-01-15 09:30:00", REGISTRY)
    assert "09:30:00Z" in pinned


def _spec(name):
    return yaml.safe_load((SCENARIOS / f"{name}.yaml").read_text(encoding="utf-8"))


def test_a_bare_dml_block_runs_directly_with_typed_binds():
    """bind には入る列の型と桁を付ける。金額列は scaled の規約で整数になっているからである。"""
    converted = direct(_spec("trigger_products_audit_price"), REGISTRY, {"products": {"unit_price": 2}})
    assert converted["statements"][0]["sql"] == \
        "UPDATE products SET unit_price = :p_price WHERE product_id = :p_product_id"
    assert {"name": "p_price", "type": "BIGINT", "scale": 2} in converted["statements"][0]["binds"]


def test_a_dml_that_does_not_convert_is_a_result_not_a_skip():
    """キーを持たない INSERT は変換できない。それは「このクライアントの文は、移行後はそのままでは
    通らない」という**答え**なので、比較できないことにはしない。"""
    converted = direct(_spec("trigger_orders_seq_assigns_id"), REGISTRY, None)
    assert "order_id" in converted["refused"]


def test_a_routine_call_is_not_a_direct_dml():
    assert direct(_spec("payment_record"), REGISTRY, None) is None


def test_the_report_says_the_direct_path_differs_by_design():
    """差が出るのは #12 §0 で決めたとおり。見分けて書かないと、読む人が不具合として追う。"""
    from difftest.plsql_compare import _is_direct_dml

    assert _is_direct_dml("trigger_products_audit_price")
    assert not _is_direct_dml("payment_record")
    assert not _is_direct_dml("view_load_rowtype"), "record を射影するブロックは routine の呼び出しである"


def test_omitted_arguments_are_filled_from_literal_defaults():
    """`annual_comp(p_salary, p_comm DEFAULT NULL)` called with p_salary only (#41)."""
    from difftest.plsql_setup import with_defaults
    from plsql.ir import model as M

    def param(name, direction="IN", default=None):
        return M.Parameter(id=name, kind="Parameter", source_range=None, name=name, direction=direction, default=default)
    routines = {("annual_comp", "annual_comp"): [param("p_salary"), param("p_comm", default="NULL")],
                ("raise_salary", "raise_salary"): [param("p_emp_id"), param("p_pct", default="5"),
                                                   param("p_new_sal", "OUT")],
                ("stamp", "stamp"): [param("p_id"), param("p_when", default="SYSDATE")]}
    call = lambda unit, args: {"unit": unit, "routine": unit, "call": {"kind": "procedure", "name": unit, "args": args}}
    assert with_defaults(call("annual_comp", {"p_salary": 6000}), routines) == {"p_salary": 6000, "p_comm": None}
    # declaration order wins over the order the scenario wrote, and OUT parameters are not arguments
    assert list(with_defaults(call("raise_salary", {"p_pct": 10, "p_emp_id": 104}), routines)) == ["p_emp_id", "p_pct"]
    assert with_defaults(call("raise_salary", {"p_emp_id": 104}), routines)["p_pct"] == 5
    assert with_defaults(call("stamp", {"p_id": 1}), routines) is None        # SYSDATE is not a literal
    assert with_defaults(call("unknown", {"p_id": 1}), routines) is None
    assert with_defaults({"call": {"kind": "block", "body": "BEGIN NULL; END;"}}, routines) is None
