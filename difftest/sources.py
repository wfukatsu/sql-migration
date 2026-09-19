#!/usr/bin/env python3
"""Connection settings of the migration-source databases, from profiles that name environment variables.

A profile is a JSON file that says which environment variables hold the connection values and what kind of
environment the database is. It never needs to hold a real host or password, so it can be committed.

  {"product": "oracle", "environment": "production",
   "host_env": "SRC_HOST", "port_env": "SRC_PORT", "user_env": "SRC_USER",
   "password_env": "SRC_PASSWORD", "database_env": "SRC_DB"}

  * product      postgresql | oracle | mysql (database is the Oracle service name)
  * environment  required. local / dev / test / ci are disposable; anything else (production, staging, ...) is not
  * local_defaults  values used when an environment variable is unset -- only allowed with environment "local", for
                 the containers of difftest/docker-compose.yml (difftest/conf/sources/*-local.json)
  * hosts        the hosts this profile may write to (fnmatch patterns, e.g. "*.ci.example.internal"). "local"
                 means this machine and needs none; dev / test / ci must list theirs before a harness writes

`environment` is a label somebody typed, and the host comes from an environment variable somebody else may have
exported. A label alone therefore proves nothing: with SRC_ORACLE_HOST pointing at production, the committed
"local" profile used to pass, and the harness went on to DROP TABLE ... PURGE there. So the label has to agree
with the resolved host -- loopback for "local", the profile's own `hosts` list for the other disposable ones.

The harnesses create tables and load data, so they refuse a database that is not disposable. Reading only (golden.py
capture --no-setup) is allowed on other environments with an explicit --allow-production.

Profile per dialect: --profile DIALECT=PATH on the harnesses, else $DIFFTEST_PROFILE_<DIALECT>, else
difftest/conf/sources/<dialect>-local.json. Messages name the profile, the environment, the host and port the
harness is about to use, and environment variable names -- never a user or password.

  .venv/bin/python difftest/sources.py oracle [--profile oracle=path.json]     # show what a harness would use
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import sys
from dataclasses import dataclass, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILE_DIR = ROOT / "difftest/conf/sources"
PRODUCTS = {"postgres": "postgresql", "oracle": "oracle", "mysql": "mysql"}
DISPOSABLE = ("local", "dev", "test", "ci")
FIELDS = ("host", "port", "user", "password", "database")
LOOPBACK = ("localhost", "127.0.0.1", "::1", "[::1]")
PROFILES: dict[str, Path] = {}  # --profile values of the running harness (shared by harnesses that import each other)


class ProfileError(Exception):
    """A profile that is missing, malformed, incomplete, or points at an environment the caller may not use."""


@dataclass(frozen=True)
class SourceConfig:
    profile: str
    product: str
    environment: str
    host: str
    port: int
    user: str
    password: str
    database: str

    def __repr__(self) -> str:  # never show the credentials in logs or tracebacks
        return f"SourceConfig({self.label()})"

    def label(self) -> str:
        # the host is where the tables get dropped; the operator has to be able to see it
        return f"profile {self.profile} ({self.product}, environment={self.environment}, host={self.host}:{self.port})"

    def psycopg_kwargs(self) -> dict:
        return {"host": self.host, "port": self.port, "user": self.user, "password": self.password, "dbname": self.database}

    def oracle_kwargs(self) -> dict:
        return {"user": self.user, "password": self.password, "dsn": f"{self.host}:{self.port}/{self.database}"}

    def pymysql_kwargs(self) -> dict:
        return {"host": self.host, "port": self.port, "user": self.user, "password": self.password, "database": self.database}

    def jdbc_url(self, schema: str | None = None) -> str:
        if self.product == "oracle":
            return f"jdbc:oracle:thin:@//{self.host}:{self.port}/{self.database}"
        if self.product == "postgresql":
            return f"jdbc:postgresql://{self.host}:{self.port}/{self.database}" + (f"?currentSchema={schema}" if schema else "")
        return f"jdbc:mysql://{self.host}:{self.port}/{self.database}?useSSL=false&allowPublicKeyRetrieval=true"


SYS_PASSWORD_ENV = "SRC_ORACLE_SYS_PASSWORD"


def sys_config(cfg: SourceConfig, user: str, password_env: str = SYS_PASSWORD_ENV) -> SourceConfig:
    """The privileged account that runs `ALTER SYSTEM SET FIXED_DATE`, on the same database as `cfg`.

    `ALTER SYSTEM` changes the clock of the whole instance, for every session: it is a write whatever the
    statements around it do, so only a disposable environment gets one. The password comes from the environment
    (a command-line argument is readable in `ps`); docker-compose's own default is used for `local` only.
    """
    if cfg.environment not in DISPOSABLE:
        raise ProfileError(f"{cfg.label()}: ALTER SYSTEM SET FIXED_DATE changes the clock of the whole instance; "
                           f"it is only run against a disposable environment ({' / '.join(DISPOSABLE)})")
    password = os.environ.get(password_env)
    if not password:
        if cfg.environment != "local":
            raise ProfileError(f"set {password_env} to the password of {user} (not read from the command line)")
        password = "oracle"   # difftest/docker-compose.yml
    return replace(cfg, user=user, password=password)


def jdbc_spec(cfg: SourceConfig, schema: str | None = None, password_env: str = "DIFFTEST_SOURCE_PASSWORD") -> dict:
    """The "source" entry of a residual-runner bench spec. The spec file is written to disk, so it names the
    environment variable that carries the password; the variable is set here for the runner subprocess."""
    os.environ[password_env] = cfg.password
    return {"url": cfg.jdbc_url(schema), "user": cfg.user, "password_env": password_env}


def parse_profile_args(values: list[str] | None) -> dict[str, Path]:
    """--profile DIALECT=PATH (repeatable) -> {dialect: path}."""
    out = {}
    for v in values or []:
        dialect, sep, path = v.partition("=")
        if not sep or dialect not in PRODUCTS or not path:
            raise ProfileError(f"--profile expects DIALECT=PATH with DIALECT one of {', '.join(PRODUCTS)}: {v!r}")
        out[dialect] = Path(path)
    return out


def profile_path(dialect: str, profiles: dict[str, Path] | None = None) -> Path:
    if profiles and dialect in profiles:
        return profiles[dialect]
    env = os.environ.get(f"DIFFTEST_PROFILE_{dialect.upper()}")
    return Path(env) if env else PROFILE_DIR / f"{dialect}-local.json"


def source_config(dialect: str, profiles: dict[str, Path] | None = None, *, writes: bool = True,
                  allow_production: bool = False) -> SourceConfig:
    """Resolve the connection of `dialect`'s source database.

    writes=True (the default) is for harnesses that create tables or load data: only disposable environments pass.
    writes=False reads only; a non-disposable environment then also needs allow_production=True."""
    if dialect not in PRODUCTS:
        raise ProfileError(f"unknown dialect {dialect!r}")
    path = profile_path(dialect, profiles)
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ProfileError(f"profile not found: {path}") from None
    except json.JSONDecodeError as e:
        raise ProfileError(f"profile {path.name} is not valid JSON: {e}") from None
    name = path.name

    if profile.get("product") != PRODUCTS[dialect]:
        raise ProfileError(f"profile {name}: product must be {PRODUCTS[dialect]!r} for dialect {dialect}, "
                           f"got {profile.get('product')!r}")
    environment = profile.get("environment")
    if not environment:
        raise ProfileError(f"profile {name}: environment is required ({' / '.join(DISPOSABLE)}, or e.g. production)")
    defaults = profile.get("local_defaults") or {}
    if defaults and environment != "local":
        raise ProfileError(f"profile {name}: local_defaults is only allowed with environment \"local\"")
    if environment not in DISPOSABLE:
        if writes:
            raise ProfileError(f"profile {name}: refusing to create tables and load data in environment "
                               f"{environment!r}; use a disposable database ({' / '.join(DISPOSABLE)})")
        if not allow_production:
            raise ProfileError(f"profile {name}: reading from environment {environment!r} needs an explicit "
                               f"--allow-production")

    values = {}
    for field in FIELDS:
        var = profile.get(f"{field}_env")
        if not var:
            raise ProfileError(f"profile {name}: {field}_env is required (the name of an environment variable)")
        value = os.environ.get(var)
        if value is None:
            value = defaults.get(field)
        if value is None:
            raise ProfileError(f"profile {name}: environment variable {var} ({field}) is not set")
        values[field] = value
    host = str(values["host"]).strip().lower()
    hosts = profile.get("hosts") or []
    if not isinstance(hosts, list) or not all(isinstance(h, str) for h in hosts):
        raise ProfileError(f"profile {name}: hosts must be a list of host names or patterns")
    allowed = [h.lower() for h in hosts] + (list(LOOPBACK) if environment == "local" else [])
    listed = any(fnmatch.fnmatchcase(host, pattern) for pattern in allowed)
    if environment == "local" and not listed:
        raise ProfileError(f"profile {name}: environment \"local\" means this machine, but {profile['host_env']} "
                           f"resolves to {host!r}; unset it, or use a profile that names that environment")
    if environment in DISPOSABLE and writes and not listed:
        raise ProfileError(f"profile {name}: refusing to create tables and load data on {host!r}: it is not in "
                           f"the profile's hosts list. environment={environment!r} is only a label -- list the "
                           f"hosts that really are disposable (\"hosts\": [...]) in the profile")
    try:
        port = int(values["port"])
    except ValueError:
        raise ProfileError(f"profile {name}: {profile['port_env']} (port) is not a number") from None
    return SourceConfig(name, profile["product"], environment, str(values["host"]), port, str(values["user"]),
                        str(values["password"]), str(values["database"]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Show which source database a harness would connect to (no connection).")
    ap.add_argument("dialect", choices=sorted(PRODUCTS))
    ap.add_argument("--profile", action="append", metavar="DIALECT=PATH")
    ap.add_argument("--read-only", action="store_true", help="check as a read-only use (golden.py capture --no-setup)")
    ap.add_argument("--allow-production", action="store_true")
    args = ap.parse_args(argv)
    try:
        cfg = source_config(args.dialect, parse_profile_args(args.profile), writes=not args.read_only,
                            allow_production=args.allow_production)
    except ProfileError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    print(f"ok: {cfg.label()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
