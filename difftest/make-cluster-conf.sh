#!/bin/sh
# Build work/scalardb-cluster-node.properties = committed base config + license lines.
# license.properties (git-ignored) must contain the two lines
#   scalar.db.cluster.node.licensing.license_key=...
#   scalar.db.cluster.node.licensing.license_check_cert_pem=...
# taken from https://scalardb.scalar-labs.com/docs/latest/scalar-licensing/trial (evaluation only, do not redistribute)
set -e
cd "$(dirname "$0")"
mkdir -p work
cat conf/scalardb-cluster-node.properties license.properties > work/scalardb-cluster-node.properties
echo "wrote work/scalardb-cluster-node.properties"
