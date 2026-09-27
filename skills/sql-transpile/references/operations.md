# 保守の手順と変換の仕組み

SKILL.md の Workflow から外した、ときどきしか使わない手順と、変換の仕組み。同梱コピーの確認、関数一覧の作り直し、変換の流れを聞かれたときに読む。最後の節は sql-migration のリポジトリで作業するときだけ当てはまる。

---

## 同梱コピーの鮮度を確かめる（ScalarDB を Target にしたとき）

ScalarDB への変換は、リポジトリ本体 `scalardb_migrate/` の 7 モジュールのコピーを `scripts/_scalardb/` に同梱して使う。スキルが本体を import しないので、スキルのディレクトリを別の場所へコピーしても変換できる。本体を直してもコピーには自動で反映されない。

```bash
.venv/bin/python skills/sql-transpile/scripts/vendor_sync.py --check
```

| 終了コード | 最終行 | 意味 |
|---|---|---|
| 0 | `VENDOR_DRIFT=0` | 同梱コピーは本体と一致 |
| 0 | `VENDOR_DRIFT=n/a` | 比べる本体が無い（スキルがリポジトリの外にある）。同梱コピーで変換できるので、そのまま進める |
| 1 | `VENDOR_DRIFT=<差分のあるモジュール数>` | 本体と食い違っている。利用者に伝える。取り込みはリポジトリの中で行う（最後の節） |

---

## 関数一覧を作り直す（Target のバージョンが違うとき）

`FUNC_PORTABILITY` の判定に使う組み込み関数の一覧（`scripts/catalogs/<方言>.json`）は、次のデータベースから取った。

| 方言 | 取ったデータベース | 取り方 |
|---|---|---|
| PostgreSQL | PostgreSQL 16 | `pg_proc` の `pg_catalog` スキーマの分 |
| Oracle | Oracle Database 23ai Free | `V$SQLFN_METADATA` と `SYS.STANDARD` のプロシージャ |
| MySQL | MySQL 8.4 | `mysql.help_topic` の関数と演算子の分類 |
| DuckDB | DuckDB 1.5.5 | `duckdb_functions()` |

利用者の Target のバージョンが大きく違い、`FUNC_PORTABILITY` の結果が疑わしいときだけ作り直す。指定した方言の JSON だけが上書きされる。

```bash
.venv/bin/python skills/sql-transpile/scripts/build_catalogs.py --duckdb
.venv/bin/python skills/sql-transpile/scripts/build_catalogs.py --postgres postgresql://user:pass@host:5432/db
.venv/bin/python skills/sql-transpile/scripts/build_catalogs.py --oracle user/pass@host:1521/service
.venv/bin/python skills/sql-transpile/scripts/build_catalogs.py --mysql user:pass@host:3306
```

接続先は利用者に確かめる。使う方言の分だけドライバ（`duckdb`・`psycopg`・`oracledb`・`pymysql`）が要る。終了コードは 0 が成功、1 がいずれかの方言で失敗、2 が引数の誤り。

---

## 仕組み

Target によって 2 つのバックエンドを使い分ける。どちらも同じ形の結果（文ごとの状態と指摘）を返すので、レポートは共通。

```
                       ┌─ target = scalardb ──→ 同梱した ScalarDB 変換ルール一式
入力 SQL ─→ 文分割 ─┤                          （DNF/CNF 正規化・キー設計・型対応・実行計画）
                       └─ target = その他 ────→ 汎用パス（下の 6 段）
```

文分割では、Oracle の入力の `/` だけの行を SQL*Plus の区切りとして文の切れ目にし、PL/SQL のブロックと `WITH FUNCTION` の問合せは `/` までを 1 文として扱う。入力に `CREATE TABLE` があれば、その表定義を後の文の型の判定とアクセスパスの判定に使う。

汎用パスの 6 段:

1. **解析** — Source 方言で AST にする
2. **変換元の検査** — Target に持ち込めない構文（`CONNECT BY`、`ROWID`、`NEXTVAL`、`KEEP` など）を AST で拾う。コメントや文字列リテラルの中の語には反応しない
3. **前処理** — SQLGlot が直さない構文を AST 上で直す。外部結合 `(+)`、`ROWNUM`、再帰 CTE、`FROM dual`、日付リテラル、整数除算、`INTERVAL`、`DISTINCT ON`、`TRUNC` の書式、引用符付きの識別子など（`dialect-notes.md`）
4. **生成** — `ErrorLevel.RAISE` で生成し、SQLGlot が未対応と知っている構文を例外にする
5. **変換先の検査** — Target が持っていない構文（`MERGE`、`ON CONFLICT`、`RETURNING` など）と、Target の組み込み関数一覧に無い関数を拾う
6. **往復検証** — 生成した SQL が Target 方言としてパースできることを確かめる

---

## sql-migration のリポジトリで作業するとき

ここから下は、sql-migration の開発用のチェックアウトを作業ディレクトリにしているときだけ使える。`difftest/` とリポジトリ本体の `scalardb_migrate/` を使うので、プラグインとして入れたスキルからは使えない。

### 同梱コピーを本体から取り込む

`vendor_sync.py --check` が終了コード 1 を返したら、本体から同梱コピーを作り直す。本体の無い場所で実行すると「本体が見つかりません」で終了コード 2 になる。

```bash
.venv/bin/python skills/sql-transpile/scripts/vendor_sync.py --update
```

同梱コピーと本体は、どちらも SQLGlot のグローバルな方言表に `scalardb` という名前で方言を登録する。**同じ Python プロセスで両方を import すると後勝ちで上書きされ**、先に読んだ側の `UPSERT` の生成が `Unsupported expression type Upsert` で失敗する。スキルの実行（`transpile.py`）は同梱コピーだけを読むので影響しない。両方を比べるテストは、スキルを別プロセスで動かす。

### アプリ側の実装を Oracle の結果と突き合わせる（golden）

アプリ側の Java 実装を書いたら、Oracle の結果と比べる。Oracle で 1 回だけ正解（入力の表と結果）を取り、以降は DB なしで比べる。

```bash
# 1 回だけ（Oracle が要る）
.venv/bin/python difftest/golden.py capture --setup <setup.sql> --query <query.sql> \
  --tables <表1>,<表2> --out difftest/golden/<名前>
# 以降（DB 不要）
(cd runtime-java && gradle installDist)
.venv/bin/python difftest/golden.py check --golden difftest/golden/<名前> --impl <実装クラス名> [--cp <追加のクラスパス>]
```

- 実装クラスは `com.scalar.migrate.appside.AppSideQuery` を実装する。`run` は小文字の表名 → 行（小文字の列名 → 値）を受け取り、結果の行を返す。数値は `BigDecimal`、Oracle の `DATE` は `LocalDateTime` で届く
- `golden.json` の `ordered` は、最上位の問合せに `ORDER BY` があるかで決まる。`ORDER BY` の値が同じ行どうしの順序は Oracle でも決まっていないので、差分が出たらまずそこを疑う
- 数値は値で比べる（`2.50` と `2.5` は同じ）。`LocalDate` はその日の 0 時の `LocalDateTime` と同じとみなす
- `capture` を代わりに実行するのは、利用者が Oracle の接続先を用意しているときだけ

接続先はプロファイル（`difftest/conf/sources/oracle-local.json`、値は環境変数）で渡す。`capture` は準備の SQL を実行するので、使い捨ての DB（プロファイルの `environment` が `local` / `dev` / `test` / `ci`）にしか接続しない。本番の移行元にある表から一度だけ取るときは、利用者に環境変数を設定してもらい、準備の SQL を実行せずに読み取りだけで取る。

```bash
.venv/bin/python difftest/golden.py capture --profile oracle=<プロファイル.json> --no-setup --allow-production \
  --query <query.sql> --tables <表1>,<表2> --out <リポジトリ外の保存先>
```

本番のデータを含む `golden.json` はコミットしないよう伝える。

変換の仕組みを図で追うなら、リポジトリの `docs/design/architecture.md` の 10 章。
