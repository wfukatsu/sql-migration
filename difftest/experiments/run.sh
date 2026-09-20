#!/bin/bash
# Experiments behind docs/reports/dml-followup-research.md: parallel fetch (backend scan_fetch_size 10 and 1000), H2 index cost,
# read-compute-write cost and key-feed fetch.
#
# Needs the ScalarDB Cluster of difftest/docker-compose.yml (profile cluster) with the read data of
# difftest/bench_dml.py loaded, and the runtime built:  (cd runtime-java && gradle installDist)
#
#   difftest/experiments/run.sh [parallel|h2index|writes|all]      (default: all; output in difftest/work/experiments)
#
# "parallel" appends scalar.db.scan_fetch_size=1000 to difftest/work/scalardb-cluster-node.properties, restarts the node,
# and restores the original file and restarts again afterwards.
set -u
REPO=$(cd "$(dirname "$0")/../.." && pwd)
EXP=$REPO/difftest/experiments
OUT=$REPO/difftest/work/experiments
CP="$OUT/classes:$REPO/runtime-java/build/install/residual-runner/lib/*"
PROPS=$REPO/difftest/conf/scalardb-sql-jdbc-bench.properties
NODE=$REPO/difftest/work/scalardb-cluster-node.properties
COMPOSE="docker compose -f $REPO/difftest/docker-compose.yml --profile cluster"
WHAT=${1:-all}

mkdir -p "$OUT/classes"
javac -cp "$REPO/runtime-java/build/install/residual-runner/lib/*" -d "$OUT/classes" "$EXP"/*.java || exit 1

restart() {
  $COMPOSE restart scalardb-cluster >/dev/null 2>&1
  for _ in $(seq 1 90); do
    nc -z localhost 60053 2>/dev/null && { sleep 8; return 0; }  # the port opens before metadata is loaded
    sleep 2
  done
  echo "ScalarDB Cluster did not come back on localhost:60053" >&2
  return 1
}

if [ "$WHAT" = parallel ] || [ "$WHAT" = all ]; then
  echo "=== parallel fetch, scan_fetch_size default (10)" | tee "$OUT/parallel.log"
  java -cp "$CP" ParFetch "$PROPS" 2>/dev/null | tee -a "$OUT/parallel.log"
  cp "$NODE" "$OUT/node.properties.bak"
  trap 'cp "$OUT/node.properties.bak" "$NODE"; restart' EXIT  # always restore the node config
  echo "scalar.db.scan_fetch_size=1000" >> "$NODE"
  restart || exit 1
  echo "=== parallel fetch, scan_fetch_size 1000" | tee -a "$OUT/parallel.log"
  java -cp "$CP" ParFetch "$PROPS" 2>/dev/null | tee -a "$OUT/parallel.log"
  cp "$OUT/node.properties.bak" "$NODE"
  trap - EXIT
  restart
fi

if [ "$WHAT" = h2index ] || [ "$WHAT" = all ]; then
  echo "=== H2 index cost" | tee "$OUT/h2index.log"
  java -Xmx6g -cp "$CP" H2Index 5000,20000,100000,500000 2>/dev/null | tee -a "$OUT/h2index.log"
fi

if [ "$WHAT" = writes ] || [ "$WHAT" = all ]; then
  echo "=== read-compute-write and key-feed fetch" | tee "$OUT/writes.log"
  java -cp "$CP" WriteAndKeyFeed "$PROPS" 2>/dev/null | tee -a "$OUT/writes.log"
fi
