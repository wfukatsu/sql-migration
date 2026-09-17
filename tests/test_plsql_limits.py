"""走査行数の上限（2026-09-17 の決定）。

生成コードは cursor の行を先に全部読むので、動く行数はメモリで決まる。上限は業務ごとに違うため、既定を
config に置き、routine 単位で上書きする。ここで固定するのは、上限が**生成物まで届いていること**と、
超えたときに**理由を言って止まる**ことである。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plsql.generate import main as generate
from plsql.limits import DEFAULT_SCAN_ROWS, Limits

SRC = "fixtures/plsql/src"
CONFIG = "fixtures/plsql/limits.yaml"


def test_without_a_config_the_builtin_default_applies():
    limits = Limits.load(None)
    assert limits.for_routine("anything") == DEFAULT_SCAN_ROWS
    assert "決められていない" in limits.explain("anything")


def test_a_routine_can_be_given_its_own_limit():
    limits = Limits.load(CONFIG)
    assert limits.for_routine("pkg_order_pricing.order_total") == 1000
    assert limits.for_routine("prc_nightly_close") == limits.scan_rows


def test_the_explanation_says_where_the_limit_came_from():
    """A generated file saying "10000" without saying why is a number nobody can question."""
    limits = Limits.load(CONFIG)
    assert CONFIG in limits.explain("pkg_order_pricing.order_total")
    assert "決められていない" in limits.explain("prc_nightly_close")


def test_a_missing_config_is_an_error_not_a_silent_default():
    with pytest.raises(FileNotFoundError):
        Limits.load("does-not-exist.yaml")


@pytest.mark.parametrize("value", [0, -1])
def test_a_limit_that_cannot_stop_anything_is_refused(tmp_path, value):
    path = tmp_path / "limits.yaml"
    path.write_text(f"scanRows:\n  default: {value}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="正の整数"):
        Limits.load(path)


def test_the_limit_reaches_the_generated_code(tmp_path):
    generate([SRC, "--out-dir", str(tmp_path), "--limits", CONFIG, "--quiet"])
    source = (Path(tmp_path) / "src/main/java/com/example/migrated/infrastructure"
              / "PkgOrderPricingRepository.java").read_text(encoding="utf-8")
    assert "1000" in source and "走査行数が上限" in source


def test_the_generated_check_stops_before_the_rows_are_held(tmp_path):
    """Counting after reading them all would have used the memory before it could stop."""
    generate([SRC, "--out-dir", str(tmp_path), "--limits", CONFIG, "--quiet"])
    source = (Path(tmp_path) / "src/main/java/com/example/migrated/infrastructure"
              / "PkgOrderPricingRepository.java").read_text(encoding="utf-8")
    check = source.index("rows.size() >=")
    add = source.index("rows.add(new OrderTotalLoop1Row")
    assert check < add, "the limit is checked after the row was added"


def test_the_generated_code_says_where_its_limit_came_from(tmp_path):
    generate([SRC, "--out-dir", str(tmp_path), "--limits", CONFIG, "--quiet"])
    source = (Path(tmp_path) / "src/main/java/com/example/migrated/infrastructure"
              / "PkgOrderPricingRepository.java").read_text(encoding="utf-8")
    assert "limits.yaml で pkg_order_pricing.order_total に指定された値" in source


def test_a_routine_with_no_specific_limit_says_so_in_the_code(tmp_path):
    """"既定値" is not a decision; the generated code should not read as if it were."""
    generate([SRC, "--out-dir", str(tmp_path), "--limits", CONFIG, "--quiet"])
    source = (Path(tmp_path) / "src/main/java/com/example/migrated/infrastructure"
              / "PrcNightlyCloseRepository.java").read_text(encoding="utf-8")
    assert "この routine 固有の上限は決められていない" in source
