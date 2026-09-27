package com.scalar.migrate.plsql;

import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.regex.Pattern;

/**
 * An Oracle regular expression (REGEXP_LIKE / SUBSTR / REPLACE / INSTR / COUNT) as a {@link Pattern} (#140).
 *
 * <p>Oracle's dialect is POSIX ERE with some Perl additions, and it does not mean what the same text means to Java.
 * Every difference below was measured on Oracle 26ai (AL32UTF8, NLS_SORT BINARY) and is translated here:
 *
 * <ul>
 *   <li>a bracket expression takes POSIX classes ({@code [[:digit:]]}) and treats a backslash as itself: {@code [\w]}
 *       is a backslash or a {@code w}. {@code []a]} starts with a literal {@code ]}, and {@code &}, {@code [} are
 *       plain characters ({@code [a&&b]} matches {@code &});
 *   <li>the classes are Unicode's, not ASCII's: {@code [[:digit:]]} and {@code \d} match a full-width digit,
 *       {@code [[:alpha:]]} a CJK ideograph and a combining mark, {@code [[:punct:]]} a symbol such as {@code $}.
 *       {@code [[:space:]]} and {@code \s} are Java's whitespace (a tab, U+3000; not a no-break space), and
 *       {@code [[:blank:]]} is the space alone. Case-insensitive matching ({@code 'i'}) leaves {@code [[:upper:]]}
 *       and {@code [[:lower:]]} as they are, where Java would let {@code \p{Ll}} match {@code A};
 *   <li>outside a bracket, a backslash before anything but {@code d D w W s S A Z z} and a back reference makes the
 *       character literal: {@code \n} is {@code n}, {@code \t} is {@code t}, {@code \Q} is {@code Q}, {@code \0} is
 *       {@code 0}, and a trailing backslash is a backslash;
 *   <li>a quantifier with nothing before it is ignored ({@code '*a'} finds {@code a}), and a brace that is not an
 *       interval ({@code a{}, {@code a{x}}) is literal;
 *   <li>only a line feed ends a line ({@code UNIX_LINES}): {@code .} matches a carriage return, {@code $} does not
 *       stop before one;
 *   <li>{@code 'x'} drops the whitespace outside brackets and nothing else ({@code #} is not a comment).
 * </ul>
 *
 * <p>Oracle's own errors keep their numbers (ORA-12725 unmatched parenthesis, ORA-12726 bracket, ORA-12727 back
 * reference, ORA-12728 range, ORA-12729 class, ORA-12731 collation element, ORA-12732 interval, ORA-01760 match
 * parameter). What Oracle reads differently and this does not reproduce -- an equivalence class {@code [[=e=]]}, which
 * is accent- and case-blind there, a {@code (?} group, a quantifier on a quantifier -- raises
 * UnsupportedOperationException rather than matching something else.
 */
final class OracleRegex {
  private OracleRegex() {}

  private static final Map<String, Pattern> CACHE = new ConcurrentHashMap<>();

  /** The pattern with the match parameter's flags. {@code match} is null for Oracle's default. */
  static Pattern compile(String pattern, String match) {
    int flags = Pattern.UNIX_LINES;
    boolean extended = false;
    if (match != null) {
      for (char c : match.toCharArray()) {
        switch (c) {
          case 'i' -> flags |= Pattern.CASE_INSENSITIVE | Pattern.UNICODE_CASE;
          case 'c' -> flags &= ~(Pattern.CASE_INSENSITIVE | Pattern.UNICODE_CASE);
          case 'n' -> flags |= Pattern.DOTALL;
          case 'm' -> flags |= Pattern.MULTILINE;
          case 'x' -> extended = true;
          default -> throw new Plsql.FunctionError(-1760, "ORA-01760: illegal argument for function");
        }
      }
    }
    String key = flags + (extended ? "x" : "-") + pattern;
    Pattern cached = CACHE.get(key);
    if (cached != null) return cached;
    boolean caseless = (flags & Pattern.CASE_INSENSITIVE) != 0;
    Pattern compiled = Pattern.compile(translate(pattern, extended, caseless), flags);
    if (CACHE.size() > 512) CACHE.clear();
    CACHE.put(key, compiled);
    return compiled;
  }

  // what each POSIX class holds, as the inside of a Java character class
  private static final Map<String, String> CLASSES = Map.ofEntries(
      Map.entry("alpha", "\\p{L}\\p{M}\\p{Nl}"),
      Map.entry("digit", "\\p{Nd}\\p{Nl}"),
      Map.entry("alnum", "\\p{L}\\p{M}\\p{Nl}\\p{Nd}"),
      Map.entry("upper", "\\p{Lu}\\p{Lt}"),
      Map.entry("lower", "\\p{Ll}\\p{Lt}"),
      Map.entry("space", "\\p{javaWhitespace}"),
      Map.entry("blank", " "),
      Map.entry("punct", "\\p{P}\\p{S}"),
      Map.entry("cntrl", "\\p{Cc}"),
      Map.entry("print", "\\p{L}\\p{M}\\p{N}\\p{P}\\p{S}\\p{Z}"),
      Map.entry("graph", "\\p{L}\\p{M}\\p{N}\\p{P}\\p{S}"),
      Map.entry("xdigit", "0-9A-Fa-f"));

  private static final String WORD = "\\p{L}\\p{M}\\p{Nd}\\p{Nl}_";

  static String translate(String p, boolean extended, boolean caseless) {
    StringBuilder out = new StringBuilder();
    int groups = 0;
    int depth = 0;
    boolean atom = false;       // whether a quantifier here has something to repeat
    boolean quantified = false; // whether the last thing written was a quantifier
    boolean lazy = false;       // ... and it was made lazy already
    int i = 0;
    while (i < p.length()) {
      char c = p.charAt(i);
      if (extended && Character.isWhitespace(c)) {
        i++;
        continue;
      }
      if (c == '\\') {
        if (i + 1 >= p.length()) {
          out.append("\\\\");
          i++;
        } else {
          char n = p.charAt(i + 1);
          i += 2;
          if (n >= '1' && n <= '9') {
            if (n - '0' > groups) {
              throw new Plsql.FunctionError(-12727, "ORA-12727: invalid back reference in regular expression");
            }
            out.append("(?:\\").append(n).append(')');
          } else {
            switch (n) {
              case 'd' -> out.append("[\\p{Nd}\\p{Nl}]");
              case 'D' -> out.append("[^\\p{Nd}\\p{Nl}]");
              case 'w' -> out.append('[').append(WORD).append(']');
              case 'W' -> out.append("[^").append(WORD).append(']');
              case 's' -> out.append("\\p{javaWhitespace}");
              case 'S' -> out.append("\\P{javaWhitespace}");
              case 'A', 'Z', 'z' -> out.append('\\').append(n);
              default -> literal(out, n);
            }
            if (n == 'A' || n == 'Z' || n == 'z') {
              atom = false;
              quantified = false;
              continue;
            }
          }
        }
        atom = true;
        quantified = false;
        continue;
      }
      switch (c) {
        case '(' -> {
          if (i + 1 < p.length() && p.charAt(i + 1) == '?') {
            throw new UnsupportedOperationException("regular expression: Oracle does not read '(?' as Java does: " + p);
          }
          groups++;
          depth++;
          out.append('(');
          atom = false;
          quantified = false;
          i++;
        }
        case ')' -> {
          if (depth == 0) throw new Plsql.FunctionError(-12725, "ORA-12725: unmatched parentheses in regular expression");
          depth--;
          out.append(')');
          atom = true;
          quantified = false;
          i++;
        }
        case '[' -> {
          i = bracket(p, i, out, caseless);
          atom = true;
          quantified = false;
        }
        case '*', '+', '?' -> {
          i++;
          if (!atom && !quantified) continue;   // nothing to repeat: Oracle ignores it
          if (quantified) {
            if (c == '?' && !lazy) {
              out.append('?');                  // `a*?`, `a{1,2}?`: lazy, as in Perl
              lazy = true;
              continue;
            }
            throw new UnsupportedOperationException("regular expression: a quantifier on a quantifier: " + p);
          }
          out.append(c);
          atom = false;
          quantified = true;
          lazy = false;
        }
        case '{' -> {
          java.util.regex.Matcher interval = INTERVAL.matcher(p).region(i, p.length());
          if (!interval.lookingAt()) {
            out.append("\\{");
            atom = true;
            quantified = false;
            i++;
            continue;
          }
          i = interval.end();
          if (interval.group(2) != null && !interval.group(3).isEmpty()
              && Long.parseLong(interval.group(3)) < Long.parseLong(interval.group(1))) {
            throw new Plsql.FunctionError(-12732, "ORA-12732: invalid interval value in regular expression");
          }
          if (!atom && !quantified) continue;
          if (quantified) throw new UnsupportedOperationException("regular expression: a quantifier on a quantifier: " + p);
          out.append(interval.group());
          atom = false;
          quantified = true;
          lazy = false;
        }
        case '|', '^', '$' -> {
          out.append(c);
          atom = false;
          quantified = false;
          i++;
        }
        case '.' -> {
          out.append('.');
          atom = true;
          quantified = false;
          i++;
        }
        case ']', '}' -> {
          out.append('\\').append(c);
          atom = true;
          quantified = false;
          i++;
        }
        default -> {
          out.append(c);
          atom = true;
          quantified = false;
          i++;
        }
      }
    }
    if (depth != 0) throw new Plsql.FunctionError(-12725, "ORA-12725: unmatched parentheses in regular expression");
    return out.toString();
  }

  private static final Pattern INTERVAL = Pattern.compile("\\{(\\d+)(,(\\d*))?\\}");

  private static void literal(StringBuilder out, char c) {
    if (Character.isLetterOrDigit(c)) out.append(c);
    else out.append('\\').append(c);
  }

  /** A bracket expression from {@code p[start] == '['}; returns the index after its {@code ]}. */
  private static int bracket(String p, int start, StringBuilder out, boolean caseless) {
    int j = start + 1;
    boolean negate = j < p.length() && p.charAt(j) == '^';
    if (negate) j++;
    StringBuilder plain = new StringBuilder();
    StringBuilder cased = new StringBuilder();   // [:upper:] / [:lower:] under 'i': kept case-sensitive
    boolean first = true;
    while (true) {
      if (j >= p.length()) throw new Plsql.FunctionError(-12726, "ORA-12726: unmatched bracket in regular expression");
      char c = p.charAt(j);
      if (c == ']' && !first) return close(out, plain, cased, negate, j + 1);
      first = false;
      if (c == '[' && j + 1 < p.length() && ":=.".indexOf(p.charAt(j + 1)) >= 0) {
        char kind = p.charAt(j + 1);
        int end = p.indexOf(kind + "]", j + 2);
        if (end < 0) throw new Plsql.FunctionError(-12726, "ORA-12726: unmatched bracket in regular expression");
        String name = p.substring(j + 2, end);
        j = end + 2;
        if (kind == ':') {
          String members = CLASSES.get(name);
          if (members == null) {
            throw new Plsql.FunctionError(-12729, "ORA-12729: invalid character class in regular expression");
          }
          (caseless && (name.equals("upper") || name.equals("lower")) ? cased : plain).append(members);
        } else if (kind == '=') {
          throw new UnsupportedOperationException("regular expression: an equivalence class [[=" + name + "=]] "
              + "follows Oracle's linguistic sort, which is not reproduced");
        } else {
          if (name.codePointCount(0, name.length()) != 1) {
            throw new Plsql.FunctionError(-12731, "ORA-12731: invalid collation class in regular expression");
          }
          member(plain, name.codePointAt(0));
        }
        continue;
      }
      int from = p.codePointAt(j);
      j += Character.charCount(from);
      if (j + 1 < p.length() && p.charAt(j) == '-' && p.charAt(j + 1) != ']') {
        int to = p.codePointAt(j + 1);
        j += 1 + Character.charCount(to);
        if (to < from) throw new Plsql.FunctionError(-12728, "ORA-12728: invalid range in regular expression");
        member(plain, from);
        plain.append('-');
        member(plain, to);
      } else {
        member(plain, from);
      }
    }
  }

  private static void member(StringBuilder out, int point) {
    if ("\\[]&^-".indexOf(point) >= 0) out.append('\\');
    out.appendCodePoint(point);
  }

  private static int close(StringBuilder out, StringBuilder plain, StringBuilder cased, boolean negate, int next) {
    if (cased.length() == 0) {
      out.append('[').append(negate ? "^" : "").append(plain).append(']');
    } else if (!negate) {
      out.append("(?:(?-i:[").append(cased).append("])");
      if (plain.length() > 0) out.append("|[").append(plain).append(']');
      out.append(')');
    } else {
      out.append("(?:(?!(?-i:[").append(cased).append("]))");
      out.append(plain.length() > 0 ? "[^" + plain + "]" : "(?s:.)");
      out.append(')');
    }
    return next;
  }
}
