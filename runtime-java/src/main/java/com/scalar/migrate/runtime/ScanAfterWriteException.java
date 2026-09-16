package com.scalar.migrate.runtime;

/**
 * A plan tried to fetch rows that the surrounding transaction has already written or deleted.
 *
 * <p>ScalarDB's Consensus Commit refuses this ("Scanning data already-written or already-deleted by the same
 * transaction is not allowed"), so a routine that writes a table and then runs a plan whose fetch scans that same
 * table cannot execute as one transaction. The conversion has to split the routine at a durable boundary, or the
 * read has to be served by key access instead of a scan -- which is a REDESIGN decision, not something the runtime
 * can paper over. The exception names the table so the diagnostic points at the statement to change.
 */
public class ScanAfterWriteException extends RuntimeException {
  private final String table;

  public ScanAfterWriteException(String table, Throwable cause) {
    super("plan fetch on '" + table + "' scans rows this transaction has already written or deleted; "
        + "ScalarDB does not allow that. Split the routine at a durable boundary, or serve the read by key access.",
        cause);
    this.table = table;
  }

  public String table() {
    return table;
  }
}
