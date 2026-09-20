package com.scalar.migrate.examples;

import com.scalar.migrate.appside.AppSideQuery;
import com.scalar.migrate.appside.Hierarchy;
import com.scalar.migrate.appside.OracleDates;
import com.scalar.migrate.appside.OracleNumbers;
import com.scalar.migrate.appside.OracleOrdering;
import com.scalar.migrate.appside.Windows;
import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Area / shop monthly sales analysis (difftest/golden/area-sales/query.sql) reproduced with the appside helpers:
 * START WITH / CONNECT BY + SYS_CONNECT_BY_PATH, a monthly GROUP BY, LAG, a 3-row moving AVG and DENSE_RANK.
 * Fetching the two tables from ScalarDB is out of scope; see docs/examples/area-sales-analysis-scalardb-conversion.md.
 */
public final class AreaSalesReport implements AppSideQuery {
  public record OrgNode(Long nodeId, Long parentId, String nodeName) {}
  public record Sale(Long shopId, LocalDateTime salesDate, BigDecimal amount) {}
  public record ResultRow(String areaPath, String shopName, String salesMonth, BigDecimal totalAmount,
      BigDecimal momRatioPct, BigDecimal movingAvg3m, long rankInArea) {
    public Map<String, Object> toMap() {
      Map<String, Object> m = new LinkedHashMap<>();
      m.put("area_path", areaPath);
      m.put("shop_name", shopName);
      m.put("sales_month", salesMonth);
      m.put("total_amount", totalAmount);
      m.put("mom_ratio_pct", momRatioPct);
      m.put("moving_avg_3m", movingAvg3m);
      m.put("rank_in_area", rankInArea);
      return m;
    }
  }

  static final LocalDateTime FROM = LocalDate.of(2026, 1, 1).atStartOfDay();
  static final LocalDateTime TO = LocalDate.of(2027, 1, 1).atStartOfDay();
  static final int SHOP_LEVEL = 3;
  static final BigDecimal HUNDRED = BigDecimal.valueOf(100);

  private record Joined(Hierarchy.Entry<OrgNode> area, Long shopId, String month, BigDecimal total) {}
  private record Draft(Joined j, BigDecimal mom, BigDecimal avg) {}

  @Override
  public List<Map<String, Object>> run(Map<String, List<Map<String, Object>>> tables) {
    List<OrgNode> nodes = new ArrayList<>();
    for (Map<String, Object> r : tables.get("organization_master")) {
      nodes.add(new OrgNode(toLong(r.get("node_id")), toLong(r.get("parent_id")), (String) r.get("node_name")));
    }
    List<Sale> sales = new ArrayList<>();
    for (Map<String, Object> r : tables.get("sales_transactions")) {
      Object d = r.get("sales_date");
      sales.add(new Sale(toLong(r.get("shop_id")), d instanceof LocalDate ? ((LocalDate) d).atStartOfDay() : (LocalDateTime) d,
          OracleNumbers.toBigDecimal(r.get("amount"))));
    }
    List<Map<String, Object>> out = new ArrayList<>();
    for (ResultRow row : report(nodes, sales)) out.add(row.toMap());
    return out;
  }

  public static List<ResultRow> report(List<OrgNode> nodes, List<Sale> sales) {
    // area_hierarchy: START WITH parent_id IS NULL CONNECT BY PRIOR node_id = parent_id
    List<Hierarchy.Entry<OrgNode>> tree = Hierarchy.connectBy(nodes, OrgNode::nodeId, OrgNode::parentId,
        n -> n.parentId() == null, OrgNode::nodeName, " > ");

    // monthly_sales: WHERE sales_date in [FROM, TO) GROUP BY shop_id, TO_CHAR(sales_date, 'YYYY-MM')
    List<Sale> inRange = new ArrayList<>();
    for (Sale s : sales) {
      if (s.salesDate() != null && !s.salesDate().isBefore(FROM) && s.salesDate().isBefore(TO)) inRange.add(s);
    }
    Map<List<Object>, List<Sale>> groups =
        Windows.partitionBy(inRange, s -> Arrays.asList(s.shopId(), OracleDates.yearMonth(s.salesDate())));

    // area_hierarchy h JOIN monthly_sales s ON h.shop_or_area_id = s.shop_id WHERE h.hierarchy_level = 3
    Map<Long, List<Hierarchy.Entry<OrgNode>>> shops =
        Windows.partitionBy(tree.stream().filter(e -> e.level() == SHOP_LEVEL).toList(), e -> e.row().nodeId());
    List<Joined> joined = new ArrayList<>();
    for (Map.Entry<List<Object>, List<Sale>> g : groups.entrySet()) {
      Long shopId = (Long) g.getKey().get(0);
      BigDecimal total = OracleNumbers.sum(g.getValue().stream().map(Sale::amount).toList());
      if (shopId == null) continue;
      for (Hierarchy.Entry<OrgNode> area : shops.getOrDefault(shopId, List.of())) {
        joined.add(new Joined(area, shopId, (String) g.getKey().get(1), total));
      }
    }

    // LAG / AVG ... OVER (PARTITION BY s.shop_id ORDER BY s.sales_month)
    List<Draft> drafts = new ArrayList<>();
    for (List<Joined> p : Windows.partitionBy(joined, Joined::shopId).values()) {
      p.sort(Comparator.comparing(Joined::month, OracleOrdering.asc(OracleOrdering.BINARY)));
      List<BigDecimal> totals = p.stream().map(Joined::total).toList();
      List<BigDecimal> prev = Windows.lag(p, Joined::total, 1, null);
      List<BigDecimal> avg3 = Windows.movingAverage(totals, 2);
      for (int i = 0; i < p.size(); i++) {
        BigDecimal mom = OracleNumbers.round(
            OracleNumbers.multiply(OracleNumbers.divide(totals.get(i), prev.get(i)), HUNDRED), 2);
        drafts.add(new Draft(p.get(i), mom, OracleNumbers.round(avg3.get(i), 0)));
      }
    }

    // DENSE_RANK() OVER (PARTITION BY h.parent_id, s.sales_month ORDER BY s.total_amount DESC)
    Comparator<Draft> byTotalDesc =
        Comparator.comparing(d -> d.j().total(), OracleOrdering.desc(Comparator.<BigDecimal>naturalOrder()));
    List<ResultRow> result = new ArrayList<>();
    for (List<Draft> p : Windows.partitionBy(drafts, d -> Arrays.asList(d.j().area().row().parentId(), d.j().month())).values()) {
      p.sort(byTotalDesc);
      List<Long> ranks = Windows.denseRank(p, byTotalDesc);
      for (int i = 0; i < p.size(); i++) {
        Joined j = p.get(i).j();
        result.add(new ResultRow(j.area().path(), j.area().row().nodeName(), j.month(), j.total(),
            p.get(i).mom(), p.get(i).avg(), ranks.get(i)));
      }
    }

    // ORDER BY h.area_path, s.sales_month
    result.sort(Comparator.comparing(ResultRow::areaPath, OracleOrdering.asc(OracleOrdering.BINARY))
        .thenComparing(ResultRow::salesMonth, OracleOrdering.asc(OracleOrdering.BINARY)));
    return result;
  }

  private static Long toLong(Object v) {
    BigDecimal d = OracleNumbers.toBigDecimal(v);
    return d == null ? null : d.longValueExact();
  }
}
