#!/bin/sh
# Build work/scalardb-cluster-node{,-cassandra,-oracle}.properties = committed base config + license lines.
# license.properties (git-ignored) must contain the two lines
#   scalar.db.cluster.node.licensing.license_key=...
#   scalar.db.cluster.node.licensing.license_check_cert_pem=...
# taken from https://scalardb.scalar-labs.com/docs/latest/scalar-licensing/trial (evaluation only, do not redistribute)
set -e
cd "$(dirname "$0")"
mkdir -p work
cat conf/scalardb-cluster-node.properties license.properties > work/scalardb-cluster-node.properties
cat conf/scalardb-cluster-node-cassandra.properties license.properties > work/scalardb-cluster-node-cassandra.properties
cat conf/scalardb-cluster-node-oracle.properties license.properties > work/scalardb-cluster-node-oracle.properties
echo "wrote work/scalardb-cluster-node{,-cassandra,-oracle}.properties"
