package com.scalar.migrate.runtime;

import java.util.List;
import java.util.Map;

/** Execution plan produced by scalardb_migrate (docs/app-side-processing-plan.md §3.1). */
public class Plan {
  public String pattern;
  public String source_dialect;
  public String source_sql;
  public List<Fetch> fetch;
  public Map<String, Residual> residual;
  public Map<String, Object> guardrails;
  public List<String> unresolved;

  public static class Fetch {
    public String table;
    public String namespace;
    public String alias;
    public List<String> columns;
    public Map<String, String> column_types; // ScalarDB types
    public List<Object> predicates;          // Predicate or List<Predicate> (OR group) as parsed JSON
    public String scalardb_sql;
    public String access_path;
    public int max_rows;
  }

  public static class Residual {
    public String engine;
    public String mode;
    public String sql;
  }

  public static class Predicate {
    public String column;
    public String op;
    public Object value;
  }
}
