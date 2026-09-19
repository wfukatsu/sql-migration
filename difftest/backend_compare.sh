#!/bin/sh
# One backend's share of the Oracle -> ScalarDB + Cassandra verification (docs/cassandra-verification-plan.md §6).
#
#   difftest/backend_compare.sh postgres|cassandra|oracle [out-dir]
#
# Runs, against the ScalarDB Cluster node of that backend (only one node may be up at a time on an 8 GiB Docker VM):
#   1. compatibility: difftest/cases/oracle.sql and oracle-features.sql (Oracle result = expected)
#   2. benchmark:     difftest/cases/bench.sql         at 5,000 / 20,000 / 40,000 emp rows
#   3. benchmark:     difftest/cases/nosql-patterns.sql at 5,000 / 20,000 / 40,000 orders
# Before a cassandra run: docker compose stop scalardb-cluster && docker compose --profile cassandra up -d
# Before a postgres run:  docker compose stop scalardb-cluster-cassandra && docker compose --profile cluster up -d
# Before an oracle run:   ./oracle-backend-init.sh && docker compose stop scalardb-cluster && \
#                         docker compose --profile oracle --profile oracle-backend up -d scalardb-cluster-oracle
set -e
backend=${1:?usage: backend_compare.sh postgres|cassandra|oracle [out-dir]}
out=${2:-out/cassandra-verify}
cd "$(dirname "$0")/.."
py=.venv/bin/python
mkdir -p "$out"
ITER=${ITER:-15}
WARMUP=${WARMUP:-3}
SIZES=${SIZES:-"5000 20000 40000"}

node=$($py -c "import sys; sys.path.insert(0, 'difftest'); from backends import NODES; print(NODES['$backend'])")
container=difftest-$node-1
restart_node() {
  # a fresh node per compatibility case: the cases create emp with different columns, and a running node keeps using
  # the old table definition ("column job does not exist", "cached plan must not change result type")
  # `|| true`: on a node that has not logged the line yet (just created) grep -c prints 0 and exits 1, and
  # `set -e` then ended the whole script without a word
  n=$(docker logs "$container" 2>&1 | grep -c 'main services started' || true)
  # every profile, so that compose accepts the node's dependencies (the Oracle-backed node depends on source-oracle)
  if ! docker compose -f difftest/docker-compose.yml --profile cluster --profile cassandra --profile oracle \
         --profile oracle-backend restart "$node" > /dev/null; then
    echo "backend_compare: could not restart $node" >&2; exit 1
  fi
  until [ "$(docker logs "$container" 2>&1 | grep -c 'main services started' || true)" -gt "$n" ]; do
    docker logs --tail 5 "$container" 2>&1 | grep -q 'Shutting down' && { echo "backend_compare: $node stopped" >&2; exit 1; }
    sleep 3
  done
}
for case in oracle oracle-features; do
  restart_node
  $py difftest/run.py difftest/cases/$case.sql --dialect oracle --fetcher jdbc --backend "$backend" \
      --json-out "$out/$backend.$case.json" > "$out/$backend.$case.log" 2>&1 || true   # FAIL rows are results, not errors
  tail -1 "$out/$backend.$case.log"
done
for n in $SIZES; do
  $py difftest/bench.py --rows "$n" --iterations "$ITER" --warmup "$WARMUP" --backend "$backend" \
      --out "$out/bench-$backend-$n" > "$out/bench-$backend-$n.log" 2>&1 || true
  echo "bench.sql $n: $(grep -c PASS "$out/bench-$backend-$n/bench.md" 2>/dev/null) PASS"
  $py difftest/bench.py --case difftest/cases/nosql-patterns.sql --rows "$n" --iterations "$ITER" --warmup "$WARMUP" \
      --verify-rows 10000 --backend "$backend" --out "$out/nosql-$backend-$n" > "$out/nosql-$backend-$n.log" 2>&1 || true
  echo "nosql-patterns.sql $n: $(grep -c PASS "$out/nosql-$backend-$n/bench.md" 2>/dev/null) PASS"
done
