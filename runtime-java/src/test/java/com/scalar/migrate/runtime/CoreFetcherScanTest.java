package com.scalar.migrate.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.scalar.db.api.Scan;
import com.scalar.db.api.ScanAll;
import com.scalar.db.api.ScanWithIndex;
import com.scalar.db.api.TableMetadata;
import com.scalar.db.io.DataType;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

/** Which access the Core fetcher asks ScalarDB for. No cluster: a Scan is a value that can be looked at. */
class CoreFetcherScanTest {
  private static final TableMetadata ORDERS = TableMetadata.newBuilder()
      .addColumn("customer_id", DataType.INT).addColumn("order_no", DataType.BIGINT)
      .addColumn("status", DataType.TEXT).addColumn("amount", DataType.DOUBLE)
      .addPartitionKey("customer_id").addClusteringKey("order_no", Scan.Ordering.Order.ASC)
      .addSecondaryIndex("status").build();

  private static Scan scan(TableMetadata md, Object... predicates) {
    Plan.Fetch spec = new Plan.Fetch();
    spec.table = "orders";
    spec.max_rows = 100;
    spec.predicates = new ArrayList<>();
    for (int i = 0; i < predicates.length; i += 3) {
      spec.predicates.add(Map.of("column", predicates[i], "op", predicates[i + 1], "value", predicates[i + 2]));
    }
    return CoreFetcher.buildScan("ns", spec, md, Map.of());
  }

  @Test
  void anEqualityOnAnIndexedColumnIsAnIndexScanNotAFilteredFullScan() {
    // the converter calls this access INDEX, and on Cassandra splits key lists into one fetch per value. Each
    // of them used to run as Scan.all().where(...): a filtered scan of the whole table
    Scan scan = scan(ORDERS, "status", "=", "OPEN", "amount", ">", 10L);
    assertInstanceOf(ScanWithIndex.class, scan);
    assertEquals("OPEN", scan.getPartitionKey().getColumns().get(0).getTextValue());
    assertEquals(1, scan.getConjunctions().size(), "the other predicate is still pushed down");
    assertEquals(101, scan.getLimit());
  }

  @Test
  void aPartitionScanKeepsTheOtherPredicatesAndBoundsTheClusteringKey() {
    // it used to be a bare partition scan: a wide partition was read whole and tripped the row limit
    Scan scan = scan(ORDERS, "customer_id", "=", 7L, "order_no", ">=", 100L, "order_no", "<", 200L,
        "status", "=", "OPEN");
    assertFalse(scan instanceof ScanAll || scan instanceof ScanWithIndex);
    assertEquals(7, scan.getPartitionKey().getColumns().get(0).getIntValue());
    assertEquals(100L, scan.getStartClusteringKey().orElseThrow().getColumns().get(0).getBigIntValue());
    assertTrue(scan.getStartInclusive());
    assertEquals(200L, scan.getEndClusteringKey().orElseThrow().getColumns().get(0).getBigIntValue());
    assertFalse(scan.getEndInclusive());
    assertEquals(1, scan.getConjunctions().size(), "status = 'OPEN' only: key columns are not repeated as filters");
  }

  @Test
  void aDescendingClusteringColumnIsNotBounded() {
    TableMetadata descending = TableMetadata.newBuilder()
        .addColumn("customer_id", DataType.INT).addColumn("order_no", DataType.BIGINT)
        .addPartitionKey("customer_id").addClusteringKey("order_no", Scan.Ordering.Order.DESC).build();
    Scan scan = scan(descending, "customer_id", "=", 7L, "order_no", ">=", 100L);
    assertTrue(scan.getStartClusteringKey().isEmpty() && scan.getEndClusteringKey().isEmpty());
  }

  @Test
  void withNoKeyAndNoIndexItIsAFilteredScanOfTheTable() {
    Scan scan = scan(ORDERS, "amount", ">", 10L);
    assertInstanceOf(ScanAll.class, scan);
    assertEquals(1, scan.getConjunctions().size());
  }

  @Test
  void aValueTheKeyColumnCannotHoldDoesNotBecomeAKey() {
    // customer_id = 1.5 matches no row; it must not be truncated into "partition 1"
    Scan scan = scan(ORDERS, "customer_id", "=", 1.5);
    assertInstanceOf(ScanAll.class, scan);
    assertTrue(scan.getConjunctions().isEmpty(), "and it is not pushed down either; the residual SQL decides");
  }
}
