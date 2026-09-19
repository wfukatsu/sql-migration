package com.scalar.migrate.appside;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.IdentityHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.function.Function;
import java.util.function.Predicate;

/**
 * Oracle hierarchical query {@code START WITH <startWith> CONNECT BY PRIOR <id> = <parentId>} over in-memory rows.
 *
 * Entries come out in Oracle's depth-first pre-order. Oracle leaves sibling order unspecified (unless ORDER SIBLINGS BY);
 * here siblings keep input order. A row reachable from several roots appears once per root, as in Oracle. A NULL
 * parent id never matches (NULL = x is not true). Traversal is recursive: depth is bounded by the call stack.
 */
public final class Hierarchy {
  private Hierarchy() {}

  /**
   * One output row of the hierarchical query.
   *
   * @param row    the row
   * @param level  LEVEL (root = 1)
   * @param path   SYS_CONNECT_BY_PATH(pathValue, separator), or null when no path function was given
   * @param root   CONNECT_BY_ROOT row
   * @param isLeaf CONNECT_BY_ISLEAF = 1
   */
  public record Entry<T>(T row, int level, String path, T root, boolean isLeaf) {}

  /** START WITH / CONNECT BY without SYS_CONNECT_BY_PATH. */
  public static <T, K> List<Entry<T>> connectBy(List<T> rows, Function<? super T, K> id,
      Function<? super T, K> parentId, Predicate<? super T> startWith) {
    return connectBy(rows, id, parentId, startWith, null, null);
  }

  /**
   * START WITH / CONNECT BY with SYS_CONNECT_BY_PATH: the separator is prefixed before every value (also the root's)
   * and a NULL value contributes an empty string.
   *
   * @throws IllegalStateException ORA-01436 when a row is reached again below itself (cycle), ORA-30004 when a path
   *                               value contains the separator
   */
  public static <T, K> List<Entry<T>> connectBy(List<T> rows, Function<? super T, K> id,
      Function<? super T, K> parentId, Predicate<? super T> startWith,
      Function<? super T, String> pathValue, String separator) {
    Objects.requireNonNull(rows, "rows");
    if (pathValue != null && (separator == null || separator.isEmpty())) {
      throw new IllegalArgumentException("SYS_CONNECT_BY_PATH needs a non-empty separator");
    }
    Map<Object, List<T>> children = new HashMap<>();
    for (T r : rows) {
      K p = parentId.apply(r);
      if (p != null) children.computeIfAbsent(key(p), k -> new ArrayList<>()).add(r);
    }
    Walker<T, K> w = new Walker<>(id, children, pathValue, separator);
    for (T r : rows) {
      if (startWith.test(r)) w.walk(r);
    }
    return w.out;
  }

  /**
   * A key as `PRIOR id = parent_id` compares it: numbers by value. Rows come back with "any Number" -- an id as
   * Integer and its parent_id as Long, or 1 against a BigDecimal 1.0 -- and {@code equals()} calls those different,
   * so the children were never found: a three-level tree came back as its root alone, with no error.
   */
  static Object key(Object value) {
    if (value instanceof Number n) {
      if (value instanceof Double || value instanceof Float) {
        double d = n.doubleValue();
        if (Double.isNaN(d) || Double.isInfinite(d)) return value;
      }
      java.math.BigDecimal exact = new java.math.BigDecimal(n.toString()).stripTrailingZeros();
      return exact.scale() < 0 ? exact.setScale(0) : exact;   // 100 and 1E+2 are one key
    }
    return value;
  }

  private static final class Walker<T, K> {
    final Function<? super T, K> id;
    final Map<Object, List<T>> children;
    final Function<? super T, String> pathValue;
    final String separator;
    final Set<T> ancestors = Collections.newSetFromMap(new IdentityHashMap<>());
    final List<Entry<T>> out = new ArrayList<>();

    Walker(Function<? super T, K> id, Map<Object, List<T>> children, Function<? super T, String> pathValue,
        String separator) {
      this.id = id;
      this.children = children;
      this.pathValue = pathValue;
      this.separator = separator;
    }

    private final class Frame {
      final T row;
      final String path;
      final int level;
      final java.util.Iterator<T> kids;

      Frame(T row, String path, int level, List<T> kids) {
        this.row = row;
        this.path = path;
        this.level = level;
        this.kids = kids.iterator();
      }
    }

    /**
     * Depth-first, in the order Oracle returns a hierarchy, on an explicit stack: recursion ran out of call stack
     * on a chain some 20,000 deep, and a StackOverflowError is an Error -- the callers' `catch (Exception)` did not
     * see it.
     */
    void walk(T root) {
      java.util.ArrayDeque<Frame> stack = new java.util.ArrayDeque<>();
      stack.push(enter(root, root, 1, ""));
      while (!stack.isEmpty()) {
        Frame top = stack.peek();
        if (top.kids.hasNext()) {
          stack.push(enter(top.kids.next(), root, top.level + 1, top.path));
        } else {
          ancestors.remove(top.row);
          stack.pop();
        }
      }
    }

    private Frame enter(T row, T root, int level, String parentPath) {
      if (!ancestors.add(row)) {
        throw new IllegalStateException("ORA-01436: CONNECT BY loop in user data (row " + row + ")");
      }
      String path = null;
      if (pathValue != null) {
        String v = pathValue.apply(row);
        if (v != null && v.contains(separator)) {
          throw new IllegalStateException("ORA-30004: when using SYS_CONNECT_BY_PATH function, cannot have separator as"
              + " part of column value (value \"" + v + "\")");
        }
        path = parentPath + separator + (v == null ? "" : v);
      }
      K key = id.apply(row);
      List<T> kids = key == null ? List.of() : children.getOrDefault(key(key), List.of());
      out.add(new Entry<>(row, level, path, root, kids.isEmpty()));
      return new Frame(row, path, level, kids);
    }
  }
}
