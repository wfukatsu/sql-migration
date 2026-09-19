"""Source-database profiles of the difftest harnesses (difftest/sources.py): values come from environment variables,
the environment is checked before anything connects, and credentials never show up in messages."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "difftest"))
from sources import ProfileError, parse_profile_args, source_config  # noqa: E402

ENV = {"host_env": "T_HOST", "port_env": "T_PORT", "user_env": "T_USER", "password_env": "T_PASSWORD",
       "database_env": "T_DB"}


def profile(tmp_path, **fields) -> dict:
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"product": "oracle", "hosts": ["db.internal"], **ENV, **fields}))
    return {"oracle": path}


@pytest.fixture
def env(monkeypatch):
    for k, v in {"T_HOST": "db.internal", "T_PORT": "1522", "T_USER": "reader", "T_PASSWORD": "s3cret",
                 "T_DB": "ORCL"}.items():
        monkeypatch.setenv(k, v)
    for k in ("DIFFTEST_PROFILE_ORACLE", "SRC_ORACLE_HOST", "SRC_ORACLE_PASSWORD"):
        monkeypatch.delenv(k, raising=False)


def test_values_come_from_the_named_environment_variables(tmp_path, env):
    cfg = source_config("oracle", profile(tmp_path, environment="test"))
    assert cfg.oracle_kwargs() == {"user": "reader", "password": "s3cret", "dsn": "db.internal:1522/ORCL"}
    assert cfg.jdbc_url() == "jdbc:oracle:thin:@//db.internal:1522/ORCL"
    assert "s3cret" not in repr(cfg) and "s3cret" not in cfg.label() and "reader" not in cfg.label()
    assert "db.internal:1522" in cfg.label(), "where the harness writes is not a secret; the operator must see it"


def test_bundled_local_profiles_work_without_any_variable(monkeypatch):
    for d in ("postgres", "oracle", "mysql"):
        monkeypatch.delenv(f"DIFFTEST_PROFILE_{d.upper()}", raising=False)
    monkeypatch.delenv("SRC_PG_PORT", raising=False)
    assert source_config("postgres").psycopg_kwargs()["port"] == 15432
    assert source_config("mysql").jdbc_url().startswith("jdbc:mysql://127.0.0.1:13306/verify")
    monkeypatch.setenv("SRC_PG_PORT", "25432")  # a variable still wins over the local default
    assert source_config("postgres").port == 25432


@pytest.mark.parametrize("fields,message", [
    ({}, "environment is required"),
    ({"environment": "production"}, "refusing to create tables and load data in environment 'production'"),
    ({"environment": "staging"}, "refusing to create tables"),
    ({"environment": "test", "product": "postgresql"}, "product must be 'oracle'"),
    ({"environment": "production", "local_defaults": {"host": "x"}}, "local_defaults is only allowed"),
])
def test_writing_harnesses_refuse_what_is_not_a_disposable_database(tmp_path, env, fields, message):
    with pytest.raises(ProfileError, match=message):
        source_config("oracle", profile(tmp_path, **fields))


def test_reading_production_needs_an_explicit_permission(tmp_path, env):
    prod = profile(tmp_path, environment="production")
    with pytest.raises(ProfileError, match="needs an explicit --allow-production"):
        source_config("oracle", prod, writes=False)
    assert source_config("oracle", prod, writes=False, allow_production=True).environment == "production"


def test_a_missing_variable_is_named_without_its_value(tmp_path, env, monkeypatch):
    monkeypatch.delenv("T_PASSWORD")
    with pytest.raises(ProfileError) as e:
        source_config("oracle", profile(tmp_path, environment="test"))
    assert "T_PASSWORD" in str(e.value) and "reader" not in str(e.value)


def test_profile_arguments_and_the_environment_override(tmp_path, env, monkeypatch):
    assert parse_profile_args(["oracle=a.json"]) == {"oracle": Path("a.json")}
    with pytest.raises(ProfileError):
        parse_profile_args(["db2=a.json"])
    path = profile(tmp_path, environment="ci")["oracle"]
    monkeypatch.setenv("DIFFTEST_PROFILE_ORACLE", str(path))
    assert source_config("oracle").environment == "ci"


# --- #27-37: the environment label has to agree with the host it resolves to ---------------------------
def test_the_committed_local_profile_refuses_a_host_that_is_not_this_machine(env, monkeypatch):
    """Regression: SRC_ORACLE_HOST overrides local_defaults, and the label stayed "local" -- so a harness would
    have gone on to DROP TABLE ... PURGE on whatever that variable pointed at."""
    assert source_config("oracle").host == "localhost"
    monkeypatch.setenv("SRC_ORACLE_HOST", "prod-db.corp.example")
    with pytest.raises(ProfileError, match="means this machine.*prod-db.corp.example"):
        source_config("oracle")
    with pytest.raises(ProfileError, match="means this machine"):
        source_config("oracle", writes=False)


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "::1", "LOCALHOST"])
def test_local_accepts_loopback(tmp_path, env, monkeypatch, host):
    monkeypatch.setenv("T_HOST", host)
    assert source_config("oracle", profile(tmp_path, environment="local", hosts=[])).environment == "local"


def test_a_disposable_label_is_not_enough_to_write(tmp_path, env):
    unlisted = profile(tmp_path, environment="dev", hosts=[])
    with pytest.raises(ProfileError, match="refusing to create tables and load data on 'db.internal'"):
        source_config("oracle", unlisted)
    assert source_config("oracle", unlisted, writes=False).host == "db.internal", "reading needs no list"


def test_hosts_are_patterns_and_the_label_shows_where_it_writes(tmp_path, env, monkeypatch):
    monkeypatch.setenv("T_HOST", "ora7.ci.example.internal")
    cfg = source_config("oracle", profile(tmp_path, environment="ci", hosts=["*.ci.example.internal"]))
    assert "host=ora7.ci.example.internal:1522" in cfg.label()
    assert "s3cret" not in cfg.label() and "reader" not in cfg.label()
    monkeypatch.setenv("T_HOST", "ora7.prod.example.internal")
    with pytest.raises(ProfileError, match="not in the profile's hosts list"):
        source_config("oracle", profile(tmp_path, environment="ci", hosts=["*.ci.example.internal"]))


# ---- review #27, 40 ------------------------------------------------------------------------------------------

def test_the_privileged_account_is_only_handed_out_for_a_disposable_database(tmp_path, env, monkeypatch):
    """ALTER SYSTEM SET FIXED_DATE moves the clock of the whole instance. It used to run under writes=False."""
    from sources import sys_config

    monkeypatch.setenv("SRC_ORACLE_SYS_PASSWORD", "sys-s3cret")
    production = source_config("oracle", profile(tmp_path, environment="production"), writes=False,
                               allow_production=True)
    with pytest.raises(ProfileError, match="whole instance"):
        sys_config(production, "system")
    test = sys_config(source_config("oracle", profile(tmp_path, environment="test")), "system")
    assert test.oracle_kwargs()["user"] == "system" and test.oracle_kwargs()["password"] == "sys-s3cret"
    assert "sys-s3cret" not in repr(test)


def test_the_privileged_password_comes_from_the_environment_not_from_argv(tmp_path, env, monkeypatch):
    from sources import sys_config

    monkeypatch.delenv("SRC_ORACLE_SYS_PASSWORD", raising=False)
    with pytest.raises(ProfileError, match="SRC_ORACLE_SYS_PASSWORD"):
        sys_config(source_config("oracle", profile(tmp_path, environment="test")), "system")
    for script in ("plsql_run.py", "plsql_semantics.py"):
        assert '"--sys-password"' not in (ROOT / "difftest" / script).read_text(encoding="utf-8")


def test_the_semantics_capture_does_not_touch_fixed_date_unless_asked():
    sys.path.insert(0, str(ROOT))
    from difftest.plsql_semantics import _probe_fixed_date

    assert _probe_fixed_date(cur=None, sys_cfg=None) is True, "not measured: the conservative answer, no connection"


@pytest.mark.parametrize("query,accepted", [
    ("SELECT id FROM t WHERE id = 1", True),
    ("SELECT id FROM t UNION ALL SELECT id FROM u", True),
    ("SELECT id FROM t FOR UPDATE", False),
    ("DELETE FROM t", False),
    ("TRUNCATE TABLE t", False),
    ("BEGIN DELETE FROM t; COMMIT; END;", False),
    ("SELECT 1 FROM dual; DROP TABLE t", False),
])
def test_a_read_only_golden_capture_accepts_one_select_only(query, accepted):
    """`SET TRANSACTION READ ONLY` is a mode: DDL commits out of it and a PL/SQL block can too."""
    from golden import _require_plain_select

    if accepted:
        _require_plain_select(query)
    else:
        with pytest.raises(SystemExit):
            _require_plain_select(query)


def test_the_compose_file_publishes_its_ports_on_localhost_only():
    import re

    text = (ROOT / "difftest" / "docker-compose.yml").read_text(encoding="utf-8")
    published = re.findall(r'ports:\s*\[([^\]]*)\]', text)
    assert published and all(p.strip().startswith('"127.0.0.1:') for p in published), published
