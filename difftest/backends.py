"""ScalarDB backends the harness can target (docs/cassandra-verification-plan.md §3.1).

Each backend is one ScalarDB Cluster node + its storage. The harness only picks the matching client configuration and
Schema Loader service; it never connects to the storage itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONF = ROOT / "difftest/conf"


@dataclass(frozen=True)
class Backend:
    name: str
    core: str            # ScalarDB Core client properties (loader, --fetcher core), resolved on the host
    sql: str             # ScalarDB SQL JDBC client properties, namespace difftest
    sql_bench: str       # ScalarDB SQL JDBC client properties, namespace bench
    loader_service: str  # docker compose service running Schema Loader against this storage
    loader_config: str   # Schema Loader config path inside the container
    storage: str          # what the converter targets (scalardb_migrate convert_script(storage=...))
    create_options: tuple[str, ...] = ()  # extra Schema Loader options when creating tables


BACKENDS = {
    "postgres": Backend("postgres", str(CONF / "scalardb.properties"), str(CONF / "scalardb-sql-jdbc.properties"),
                        str(CONF / "scalardb-sql-jdbc-bench.properties"), "schema-loader",
                        "/conf/scalardb-in-docker.properties", "jdbc"),
    "cassandra": Backend("cassandra", str(CONF / "scalardb-cassandra.properties"),
                         str(CONF / "scalardb-sql-jdbc-cassandra.properties"),
                         str(CONF / "scalardb-sql-jdbc-bench-cassandra.properties"), "schema-loader-cassandra",
                         "/conf/scalardb-in-docker-cassandra.properties", "cassandra", ("--replication-factor", "1")),
    "oracle": Backend("oracle", str(CONF / "scalardb-oracle.properties"), str(CONF / "scalardb-sql-jdbc-oracle.properties"),
                      str(CONF / "scalardb-sql-jdbc-bench-oracle.properties"), "schema-loader-oracle",
                      "/conf/scalardb-in-docker-oracle.properties", "jdbc"),
}

# docker compose service of each backend's ScalarDB Cluster node
NODES = {"postgres": "scalardb-cluster", "cassandra": "scalardb-cluster-cassandra", "oracle": "scalardb-cluster-oracle"}


def schema_loader(backend: Backend, schema_file: str) -> tuple[list[str], list[str]]:
    """(delete command, create command) for Schema Loader; schema_file is the path inside the container (/work/...)."""
    # every backend profile, so that compose accepts all loader services' dependencies; `run` starts only this one's own
    base = ["docker", "compose", "-f", str(ROOT / "difftest/docker-compose.yml"), "--profile", "tools", "--profile",
            "cassandra", "--profile", "oracle", "run", "--rm",
            backend.loader_service, "--config", backend.loader_config, "--schema-file", schema_file]
    return base + ["--delete-all"], base + ["--coordinator", *backend.create_options]
