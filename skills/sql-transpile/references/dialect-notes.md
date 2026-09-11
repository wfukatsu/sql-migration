# 方言ごとの注意点と書き換え方

レポートに出る指摘コードごとに、なぜ危険か・どう書き換えるかをまとめる。
コード名はレポートの「指摘」列と「指摘の集計」表に出るものと同じ。

---

## 素の sqlglot.transpile が素通りさせるもの

SQLGlot は、Source 方言の構文を Target 方言が持っていなくても、例外を出さずにそのまま出力することがある。
Oracle → PostgreSQL の実測:

| 入力 | sqlglot.transpile の出力 | 何が起きるか |
|---|---|---|
| `WHERE ROWNUM <= 5` | `WHERE ROWNUM <= 5` | 実行時に列が無いエラー |
| `WHERE e.deptno = d.deptno(+)` | `WHERE e.deptno = d.deptno` | 外部結合が内部結合になり、**結果が静かに変わる** |
| `CONNECT BY PRIOR empno = mgr` | そのまま | 構文エラー |
| `VALUES (emp_seq.NEXTVAL)` | そのまま | 実行時エラー |
| `SELECT ROWID` | そのまま | 実行時エラー |

`(+)` だけは `unsupported_level=ErrorLevel.RAISE` で検出できる。残りは SQLGlot が未対応と認識していないため、どのエラーレベルでも検出されない。このスキルは、前処理で直せるものを直し、直せないものを下の各コードで報告する。

---

## 前処理で自動的に直すもの

### ORACLE_JOIN_MARK（INFO / ERROR）

Oracle の外部結合記法 `(+)` を `LEFT JOIN` に書き換える（`sqlglot.transforms.eliminate_join_marks`）。

```sql
-- 変換前
SELECT e.ename, d.dname FROM emp e, dept d WHERE e.deptno = d.deptno(+);
-- 変換後
SELECT e.ename, d.dname FROM emp AS e LEFT JOIN dept AS d ON e.deptno = d.deptno;
```

**ERROR になる場合**: `(+)` が FROM 句の先頭テーブル側に付いていると、SQLGlot の書き換えが壊れた SQL を作る。結合の向きを入れ替え、`(+)` を相手側に付け直してから再変換する。

### ROWNUM（INFO / WARN / ERROR）

`WHERE ROWNUM <= n` を `LIMIT n` にする（`ROWNUM < n` は `LIMIT n-1`）。

**WARN になる場合**: ORDER BY を伴う文。Oracle は ORDER BY より**前に** ROWNUM を適用するため、「並べ替えた上位 n 件」ではなく「任意の n 件を並べ替えたもの」になる。元の SQL の意図が上位 n 件なら、変換後の `ORDER BY ... LIMIT n` のほうが正しい。意図を確認する。

**ERROR になる場合**: `ROWNUM` が WHERE の単純な上限以外に出てくる（射影、OR の中、`ROWNUM > n` など）。`ROW_NUMBER() OVER (ORDER BY ...)` を使う形に書き直す。

---

## 手作業が要るもの

### CONNECT_BY（ERROR）

階層問合せ（`CONNECT BY` / `START WITH` / `PRIOR` / `LEVEL` / `SYS_CONNECT_BY_PATH`）。再帰 CTE に書き換える。

```sql
-- Oracle
SELECT ename, LEVEL FROM emp START WITH mgr IS NULL CONNECT BY PRIOR empno = mgr;

-- PostgreSQL
WITH RECURSIVE tree AS (
  SELECT empno, ename, 1 AS level FROM emp WHERE mgr IS NULL
  UNION ALL
  SELECT e.empno, e.ename, t.level + 1 FROM emp e JOIN tree t ON e.mgr = t.empno
)
SELECT ename, level FROM tree;
```

### SEQUENCE（ERROR）

`CREATE SEQUENCE`、`seq.NEXTVAL`、`seq.CURRVAL`、PostgreSQL の `nextval()`。

| Target | 代替 |
|---|---|
| PostgreSQL | `CREATE SEQUENCE` と `nextval('seq')`、または `GENERATED ALWAYS AS IDENTITY` |
| MySQL | `AUTO_INCREMENT` |
| SQL Server | `IDENTITY` または `CREATE SEQUENCE` + `NEXT VALUE FOR` |
| ScalarDB | 採番機能が無い。アプリケーション側で採番するか、採番用のテーブルを持つ |

### ROWID（ERROR）

Oracle の疑似列。Target には存在しない。主キーで行を特定する形に書き換える。

### KEEP（ERROR）

`MAX(x) KEEP (DENSE_RANK FIRST ORDER BY y)` は Oracle 固有。ウィンドウ関数で書き換える。

```sql
-- Oracle
SELECT deptno, MAX(ename) KEEP (DENSE_RANK FIRST ORDER BY hiredate) FROM emp GROUP BY deptno;
-- 標準 SQL
SELECT DISTINCT deptno, FIRST_VALUE(ename) OVER (PARTITION BY deptno ORDER BY hiredate) FROM emp;
```

### PIVOT / UNPIVOT（ERROR）

行列変換。`PIVOT` は `CASE` 式と集約、`UNPIVOT` は `UNION ALL` に展開する。

```sql
-- PIVOT の代替
SELECT deptno,
       SUM(CASE WHEN job = 'CLERK'   THEN sal END) AS clerk,
       SUM(CASE WHEN job = 'MANAGER' THEN sal END) AS manager
  FROM emp GROUP BY deptno;
```

### PLSQL（ERROR）

`DBMS_OUTPUT`・`DBMS_LOB` などの PL/SQL パッケージ呼び出し。アプリケーション側の実装に置き換える。

### TRIGGER / PROCEDURE / FUNCTION（ERROR）

トリガー・ストアドプロシージャ・ユーザー定義関数の DDL。方言差が大きく機械変換の対象外。Target の手続き言語で書き直す。

### DISTINCT_ON（ERROR、Source が PostgreSQL）

`SELECT DISTINCT ON (a) ...` は PostgreSQL 固有。`ROW_NUMBER() OVER (PARTITION BY a ORDER BY ...) = 1` で先頭行を取る形に書き換える。

### AUTO_INC / UPSERT（ERROR、Source が MySQL）

`AUTO_INCREMENT` と `ON DUPLICATE KEY UPDATE`。Target の採番機能と upsert 構文（PostgreSQL なら `ON CONFLICT ... DO UPDATE`、SQL Server なら `MERGE`）に書き換える。

---

## 要確認のもの

### FUNC_PORTABILITY（WARN）

Source 固有の関数が、元の名前のまま出力に残った。

SQLGlot は `MONTHS_BETWEEN` や `INITCAP` のような関数を方言非依存の型付きノードとして扱う。そのため Target 方言で読み直しても「未知の関数」にはならず、Target にその関数が実在しなくても素通りする。このスキルは次のように判定する。

- `NVL` → `COALESCE`、`DECODE` → `CASE` のように **書き換えられた関数は安全**とみなす
- `COUNT`・`UPPER`・`COALESCE` など **移植性の高い関数**は対象外
- それ以外で **名前がそのまま残ったもの**を WARN にする

**誤検出がある**。Target が同名・同じ意味の関数を持っていても WARN になる（例: Oracle → PostgreSQL の `INITCAP`。PostgreSQL にも `INITCAP` がある）。WARN は「動かない」ではなく「Target のドキュメントで確かめる」という意味。

よく出る例:

| 関数（Oracle） | PostgreSQL | MySQL | SQL Server |
|---|---|---|---|
| `MONTHS_BETWEEN(a, b)` | `EXTRACT` と `AGE` で計算 | `TIMESTAMPDIFF(MONTH, b, a)` | `DATEDIFF(month, b, a)` |
| `ADD_MONTHS(d, n)` | `d + n * INTERVAL '1 month'` | `DATE_ADD(d, INTERVAL n MONTH)` | `DATEADD(month, n, d)` |
| `INITCAP(s)` | `INITCAP(s)`（あり） | なし（自前で組む） | なし |
| `LAST_DAY(d)` | `date_trunc('month', d) + interval '1 month - 1 day'` | `LAST_DAY(d)`（あり） | `EOMONTH(d)` |

### UNKNOWN_FUNC（WARN）

生成した SQL を Target 方言で読み直したとき、SQLGlot が関数として認識できなかった。Target に同等の関数があるか確認する。

### HINT（WARN）

オプティマイザヒント（Oracle の `/*+ ... */`、MySQL の `STRAIGHT_JOIN`）。Target では効かないので、削除してよいことが多い。

---

## 生成・検証の段階で出るもの

### UNSUPPORTED（ERROR）

SQLGlot 自身が「Target では表現できない」と判断した。メッセージに対象の構文名が出る。

### ROUNDTRIP（ERROR）

生成した SQL を Target 方言としてパースできなかった。SQLGlot の生成器の不具合か、Target 方言の対応不足の可能性がある。

### PARSE（ERROR）

元の SQL を Source 方言として読めなかった。`--source` が実際の方言と合っているか確認する。

---

## 開発者向けの注意: 方言の登録衝突

同梱コピー `scripts/_scalardb/` と本体 `scalardb_migrate/` は、どちらも SQLGlot のグローバルな方言表に `scalardb` という名前で方言を登録する。**同じ Python プロセスで両方を import すると後勝ちで上書きされ**、先に読んだ側の `UPSERT` 生成が `Unsupported expression type Upsert` で失敗する。

スキルの実行（`transpile.py`）は同梱コピーだけを読むので影響しない。問題になるのはテストで両方を比べるときで、`tests/test_sql_transpile.py` はこのためにスキルを別プロセスで動かしている。
