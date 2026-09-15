# 変換の後にやること・保守・仕組み

SKILL.md の Workflow から外した、ときどきしか使わない手順と、変換の仕組み。必要になったときだけ読む。

---

## アプリ側の実装を Oracle の結果と突き合わせる（ScalarDB でアプリ側に移したとき）

アプリ側の Java 実装を書いたら、Oracle の結果と比べる方法を利用者に案内する。Oracle で 1 回だけ正解（入力の表と結果）を取り、以降は DB なしで比べる。

```bash
# 1 回だけ（Oracle が要る）
.venv/bin/python difftest/golden.py capture --setup <setup.sql> --query <query.sql> \
  --tables <表1>,<表2> --out difftest/golden/<名前>
# 以降（DB 不要。runtime-java を gradle installDist しておく）
.venv/bin/python difftest/golden.py check --golden difftest/golden/<名前> --impl <実装クラス名>
```

実装クラスは `com.scalar.migrate.appside.AppSideQuery` を実装する。例は `runtime-java/src/main/java/com/scalar/migrate/examples/AreaSalesReport.java`。`capture` を代わりに実行するのは、利用者が Oracle の接続先を用意しているときだけ。

---

## 同梱コピーの鮮度を確かめる（ScalarDB を Target にしたとき）

ScalarDB 変換は、リポジトリ本体 `scalardb_migrate/` のコピーを `scripts/_scalardb/` に同梱して使っている。本体を改善してもコピーには自動で反映されない。

```bash
.venv/bin/python skills/sql-transpile/scripts/vendor_sync.py --check
```

終了コード 1（`VENDOR_DRIFT=` が 0 でない）なら、本体と食い違っている。利用者に伝え、取り込むなら次を実行する。

```bash
.venv/bin/python skills/sql-transpile/scripts/vendor_sync.py --update
```

---

## 関数一覧を作り直す（Target のバージョンが違うとき）

関数一覧は PostgreSQL 16、Oracle Database 23ai Free、MySQL 8.4、DuckDB 1.5.5 から取った。利用者の Target のバージョンが大きく違い、`FUNC_PORTABILITY` の結果が疑わしいときだけ作り直す。作り直すと、指定した方言の `scripts/catalogs/<方言>.json` が上書きされる。

```bash
.venv/bin/python skills/sql-transpile/scripts/build_catalogs.py --duckdb
.venv/bin/python skills/sql-transpile/scripts/build_catalogs.py --postgres postgresql://user:pass@host:5432/db
```

接続先は利用者に確認する。Oracle は `--oracle user/pass@host:1521/service`、MySQL は `--mysql user:pass@host:3306`。

---

## 仕組み

Target によって 2 つのバックエンドを使い分ける。どちらも同じ形の結果を返すので、レポートは共通。

```
                       ┌─ target = scalardb ──→ 同梱した ScalarDB 変換ルール一式
入力 SQL ─→ 文分割 ─┤                          （DNF/CNF 正規化・キー設計・型対応）
                       └─ target = その他 ────→ 汎用パス（下の 6 段）
```

汎用パスの 6 段:

1. **解析** — Source 方言で AST にする。スクリプト内の `CREATE TABLE` から表定義を集め、型の判定に使う
2. **変換元の検査** — Target に持ち込めない構文（`CONNECT BY`、`ROWID`、`NEXTVAL`、`KEEP` など）を AST で拾う。コメントや文字列リテラルの中の語には反応しない
3. **前処理** — SQLGlot が直さない構文を AST 上で直す。外部結合 `(+)`、`ROWNUM`、再帰 CTE、`FROM dual`、日付リテラル、整数除算、`INTERVAL`、`DISTINCT ON`、`TRUNC` の書式、引用符付きの識別子など
4. **生成** — `ErrorLevel.RAISE` で生成し、SQLGlot が未対応と知っている構文を例外にする
5. **変換先の検査** — Target が持っていない構文（`MERGE`、`ON CONFLICT`、`RETURNING` など）と、Target の組み込み関数一覧に無い関数を拾う
6. **往復検証** — 生成した SQL が Target 方言としてパースできることを確かめる

図つきの説明はリポジトリの `docs/architecture.md` の 10 章。
