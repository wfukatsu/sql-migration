#!/bin/bash
# Benchmark an older commit of the converter + runtime against the working tree, on the same ScalarDB Cluster + Oracle
# (docs/app-side-benchmark-comparison.md).
#
#   difftest/bench_compare_versions.sh [old-commit] [out-dir]
#
# Needs Oracle, the PostgreSQL backend and ScalarDB Cluster:
#   cd difftest && ./make-cluster-conf.sh && docker compose --profile cluster --profile oracle up -d \
#     source-oracle backend-postgres scalardb-cluster
# For each case the data is loaded once by the new harness, then the old and the new code are timed in that order.
set -u
R=$(cd "$(dirname "$0")/.." && pwd)
OLD_REV=${1:-2825858}
O=$(mkdir -p "${2:-$R/out/bench-compare}" && cd "${2:-$R/out/bench-compare}" && pwd)
OLD=$R/out/old-$OLD_REV
PY=$R/.venv/bin/python
LOG=$O/run.log
IT="--iterations 15 --warmup 3"

if [ ! -d "$OLD" ]; then
  git -C "$R" worktree add -f "$OLD" "$OLD_REV" && (cd "$OLD/runtime-java" && gradle installDist -q)
fi
(cd "$R/runtime-java" && gradle installDist -q)
: > "$LOG"

run() {
  name=$1; shift
  echo "=== $(date +%H:%M:%S) $name" >> "$LOG"
  "$@" > "$O/$name.log" 2>&1
  echo "    exit=$? $(date +%H:%M:%S)" >> "$LOG"
}

# 1. conversion with both versions
mkdir -p "$O/convert-new/plans" "$O/convert-old/plans"
for c in bench bench-appside bench-area-sales; do
  (cd "$R" && $PY -m scalardb_migrate.cli "$R/difftest/cases/$c.sql" --source oracle \
     --out-dir "$O/convert-new" --plan-dir "$O/convert-new/plans" > "$O/convert-new/$c.stdout.txt" 2>&1)
  (cd "$OLD" && $PY -m scalardb_migrate.cli "$R/difftest/cases/$c.sql" --dialect oracle \
     --out-dir "$O/convert-old" --plan-dir "$O/convert-old/plans" > "$O/convert-old/$c.stdout.txt" 2>&1)
done

# 2. emp data set (20,000 rows)
run setup-bench bash -c "cd $R && $PY difftest/bench.py --case difftest/cases/bench.sql --rows 20000 --iterations 1 --warmup 0 --out $O/setup-bench"
for f in jdbc core; do
  run old-bench-$f   bash -c "cd $OLD && $PY difftest/bench.py --case $R/difftest/cases/bench.sql --rows 20000 $IT --skip-setup --fetcher $f --out $O/old-bench-$f"
  run new-bench-$f   bash -c "cd $R && $PY difftest/bench.py --case difftest/cases/bench.sql --rows 20000 $IT --skip-setup --fetcher $f --out $O/new-bench-$f"
  run old-appside-$f bash -c "cd $OLD && $PY difftest/bench.py --case $R/difftest/cases/bench-appside.sql --rows 20000 $IT --skip-setup --fetcher $f --out $O/old-appside-$f"
  run new-appside-$f bash -c "cd $R && $PY difftest/bench.py --case difftest/cases/bench-appside.sql --dataset bench --rows 20000 $IT --skip-setup --fetcher $f --out $O/new-appside-$f"
done

# 3. area / shop sales data set (40,000 sales rows)
run setup-area    bash -c "cd $R && $PY difftest/bench.py --case difftest/cases/bench-area-sales.sql --rows 40000 --iterations 1 --warmup 0 --out $O/setup-area"
run old-area-jdbc bash -c "cd $OLD && $PY difftest/bench.py --case $R/difftest/cases/bench-area-sales.sql --rows 40000 $IT --skip-setup --fetcher jdbc --out $O/old-area-jdbc"
run new-area-jdbc bash -c "cd $R && $PY difftest/bench.py --case difftest/cases/bench-area-sales.sql --rows 40000 $IT --skip-setup --fetcher jdbc --out $O/new-area-jdbc"

echo "=== ALL DONE $(date +%H:%M:%S)" >> "$LOG"
cat "$LOG"
