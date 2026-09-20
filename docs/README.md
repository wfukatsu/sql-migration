# ドキュメントの入口

このリポジトリの文書を、読む順と目的で並べたものです。リポジトリの概要とクイックスタートは [README](../README.md) にあります。

```text
docs/
  guide/             使い方（まずここ）
  design/            仕組みと設計、決定の記録
  plsql-migration/   PL/SQL の移行で人が決めること（パターン別）
  examples/          個別の SQL をどう移したかの例
  reports/           実 DB で測った結果（日付つきの記録）
  diagrams/ slides/  draw.io の図、説明資料（Google スライド）の生成元
```

## はじめて使う人の読む順

1. [README](../README.md) — 何をするツールか、全体像、前提と制約
2. [はじめに](guide/getting-started.md) — 準備と、DB なしで試せる最初の変換
3. 目的に合うガイド
   - SQL 文を移す → [SQL の変換と実行計画](guide/sql-conversion.md)
   - PL/SQL を移す → [PL/SQL → Java 変換](guide/plsql-conversion.md)
   - Claude Code から手順どおりに進める → [スキル](guide/skills.md)
   - 実 DB で結果を突き合わせる・性能を測る → [検証環境](guide/verification.md)
4. [アーキテクチャと仕組み](design/architecture.md) — 変換・実行計画・実行基盤・検証基盤・スキルがどう動くか（Mermaid の図つき）
5. 必要になったら、下の一覧から

## 用語

| 用語 | 意味 |
|---|---|
| OK / WARN / PLANNED / ERROR | **SQL 文ごと**の判定。変換できた / 注意つきで変換できた / 実行計画で動かせる / 自動では移せない |
| 実行計画（`.plan.json`） | ScalarDB SQL にできない読み取り文を、「ScalarDB から行を取得」と「メモリ上の H2 で元の SQL を実行」に分けたもの。`residual-runner` が動かす |
| アクセスパス分析 | 文がキー（パーティションキー・クラスタリングキー・索引）で行に届くか、パーティションをまたぐ走査になるかの判定 |
| アプリ側に移す処理 | 実行計画でも動かせず、アプリケーション（Java）で書き直す処理。`CONNECT BY`、`ROLLUP` など |
| AUTO / REVIEW / REDESIGN | **PL/SQL の routine ごと**の判定。無人で生成してよい / 人が確認する / 設計を決め直す。AUTO は実 DB で一致した証拠があるときだけ付く |
| IR | PL/SQL を解析して得る中間表現（JSON Schema 固定）。判定・Java 生成・仕様書の「事実の欄」の元 |
| corpus | 判定と生成を測るための合成の PL/SQL 一式（`fixtures/plsql/`）。KPI はこの上の値 |
| `limits.yaml` | 走査行数の上限など、プロジェクトが決めたことを生成器に渡すファイル。決めた人と日付が要る |
| 証拠（`--evidence`） | 実 Oracle と実 ScalarDB で同じシナリオを走らせて比べた結果（`plsql-diff.json`）。ソースか生成器が変わると古くなる |
| 事実の欄 / 文章 | スキルが書く文書の 2 つの部分。前者は IR・生成物から機械的に出し、後者はモデルが書いて `check` が事実と突き合わせる |
| OPS / CALL / BIZ 項目 | 生成コードの外で決めること。運用・呼び出し側・業務ロジックとの整合 |

## 使い方（`guide/`）

| 文書 | 内容 |
|---|---|
| [はじめに](guide/getting-started.md) | 準備、最初の SQL 変換、実行計画の確認、最初の PL/SQL 解析、テスト |
| [SQL の変換と実行計画](guide/sql-conversion.md) | `scalardb_migrate.cli` のオプション・出力・判定、`residual-runner` のサブコマンド |
| [PL/SQL → Java 変換](guide/plsql-conversion.md) | 判定の考え方、コマンド、出力、corpus 上の現在地 |
| [スキル](guide/skills.md) | migrate-flow / plsql-spec / plsql-migrate / sql-transpile の役割とコマンド |
| [検証環境](guide/verification.md) | Docker Compose の DB 群、接続プロファイル、ハーネスの一覧と実行例 |

スキルの手順そのもの（Claude Code が読むもの）は `skills/<名前>/SKILL.md` と `references/` にあります。変換ルールと指摘コードの一覧は
[scalardb-grammar.md](../skills/sql-transpile/references/scalardb-grammar.md)、方言ごとの注意は [dialect-notes.md](../skills/sql-transpile/references/dialect-notes.md)、
アプリ側に移す処理の書き方は [app-side-notes.md](../skills/sql-transpile/references/app-side-notes.md) です。

## 仕組みと設計（`design/`）

| 文書 | 内容 | 位置づけ |
|---|---|---|
| [アーキテクチャと仕組み](design/architecture.md) | 設計方針、変換パイプライン、アクセスパス分析、実行計画、実行基盤、書き込み文、検証基盤、トランザクション、性能の特性、スキル | **いまの実装の説明** |
| [PL/SQL 移行の KPI・AUTO 禁止条件・確信度](design/plsql-kpi.md) | 7 指標の定義、AUTO を付けてはいけない条件、確信度の 5 因子、計測コマンド | いまの定義（ルールとテストが参照する） |
| [PL/SQL 変換の実装計画](design/plsql-conversion-implementation-plan.md) | フェーズとタスク、**決定事項と未決事項（§9）** | 計画と決定の記録。コードのコメントが §番号で参照する |
| [PL/SQL 変換基盤の設計](design/plsql-migration-platform-design.md) | ANTLR + SQLGlot + IR の構成を選んだ当初の設計 | 出発点の設計。実装との差は実装計画 §1 |
| [アプリ側処理の実装計画](design/app-side-processing-plan.md) | 「取得 + 残りの処理」の 2 段実行、パターン分類（P1〜）、ガードレール | 実行計画の方式を決めた計画。コードが §番号とパターン名で参照する |

## PL/SQL の移行で人が決めること（`plsql-migration/`）

REVIEW / REDESIGN を塞いでいるのは、変換できない構文ではなく人が決めることです。形ごとに、選択肢と代償をまとめてあります。

| 文書 | 内容 |
|---|---|
| [cursor の移行パターン](plsql-migration/plsql-cursor-patterns.md) | 6 つの形と、それぞれ人が決めること（`CUR-001` / `CUR-002`） |
| [行ロックとトランザクション境界](plsql-migration/plsql-transaction-patterns.md) | 7 つの形。実 ScalarDB Cluster での同時更新の実測から始まる（`LOCK-*` / `TX-*`） |
| [trigger と外部副作用](plsql-migration/plsql-trigger-patterns.md) | 5 つの形。書込経路の網羅性が先（`TRG-*`） |
| [生成コードの外で決めること](plsql-migration/plsql-decisions-outside-generator.md) | 運用（OPS）・呼び出し側（CALL）・業務ロジックとの整合（BIZ）の項目。選択肢・推奨・代償 |
| [業務ロジックとの整合の問い](plsql-migration/plsql-biz-alignment-questions.md) | corpus の BIZ 項目を、routine ごとの具体的な問いにしたもの |

書き上がった文書の例: [現行の仕様（plsql-spec）](../skills/plsql-spec/examples/create_order/README.md)、[変換後の文書（plsql-migrate）](../skills/plsql-migrate/examples/create_order/README.md)。
corpus の説明は [fixtures/plsql/](../fixtures/plsql/README.md)（[キーの設計](../fixtures/plsql/KEY-DESIGN.md)、[シナリオ](../fixtures/plsql/scenarios/README.md)、[golden](../fixtures/plsql/golden/README.md)、[rule-cases](../fixtures/plsql/rule-cases/README.md)）、
corpus の外の routine を流す手順は [fixtures/plsql-external/](../fixtures/plsql-external/README.md)、ANTLR 文法の版は [plsql/grammar/VERSIONS.md](../plsql/grammar/VERSIONS.md) にあります。

## 個別の SQL の移行例（`examples/`）

| 文書 | 内容 |
|---|---|
| [顧客別売上ランキング](examples/sales-ranking-scalardb-conversion.md) | CTE + ウィンドウ関数。明細を取得し、集計と順位付けを Java で行う（短い例） |
| [エリア別・店舗別の月次売上分析](examples/area-sales-analysis-scalardb-conversion.md) | `CONNECT BY` + CTE + ウィンドウ関数を、取得 + Java に分解する |
| [同・性能を最大化する方法](examples/area-sales-analysis-performance.md) | 読む行数を減らす設計と、fetch size・分離レベル・並列取得の調整 |

## 検証レポート

`reports/` の文書は、**その日付に実 DB で測った記録**です。数値は Apple M3 Pro 上の Docker（1 ノードの ScalarDB Cluster、単一クライアント）のもので、
取り直した日は各文書の先頭にあります。「計画」は、対になる報告の測り方（ケースの設計、適性の基準、構成）を定めた文書で、ハーネスのコードが §番号で参照します。

| 分類 | 文書 | 内容 |
|---|---|---|
| 互換性 | [テスト報告](reports/test-report.md) | 差分テスト（PostgreSQL 15 文 / Oracle 17 文）。ScalarDB SQL 経路ですべて一致 |
| 互換性 | [Oracle 固有 SQL の検証](reports/oracle-sql-report.md) | Oracle 固有の構文・関数 79 文を機能カテゴリ別に確かめた結果 |
| 互換性 | [sql-transpile の課題と解決方法](reports/transpile-fix-research.md) | 9 通りの方言ペアを実 DB で動かして見つけた課題と、直したあとの実測 |
| 性能 | [Oracle 直接実行との比較](reports/bench-report.md) | 同じ SQL の互換性と応答時間。性能測定の方法と基準値 |
| 性能 | [DML テスト SQL のベンチマーク](reports/dml-benchmark-report.md) | 3 方言 × 51 文の変換と、移行元 DB 直接 vs ScalarDB Cluster。H2 の索引の効果 |
| 性能 | [変換できない文の対応案・並列取得・H2 の索引](reports/dml-followup-research.md) | 書き込み計画、並列取得、`scan_fetch_size` の効果 |
| 性能 | [変換ツールの新旧比較](reports/app-side-benchmark-comparison.md) | アプリ側分析の導入前後。日付範囲の押し下げの効果、アプリ側 Java と Oracle の一致 |
| バックエンド | [バックエンドの比較（まとめ）](reports/scalardb-backend-comparison.md) | ScalarDB のバックエンドを PostgreSQL / Oracle / Cassandra にしたときの互換性と性能。**まずこれ** |
| バックエンド | [Cassandra: 計画](reports/cassandra-verification-plan.md) / [報告](reports/cassandra-verification-report.md) | どの形のクエリが NoSQL バックエンドに向くかの適性表と、設定の落とし穴 |
| バックエンド | [Oracle バックエンド: 計画](reports/oracle-backend-verification-plan.md) | 移行元の Oracle をそのまま ScalarDB のバックエンドにする構成。結果はまとめに入っている |

## 図と説明資料

- [diagrams/architecture.drawio](diagrams/architecture.drawio)（[architecture.png](diagrams/architecture.png)、[flow.png](diagrams/flow.png)）— SQL 変換の系統の構成図と、1 文の処理の流れ
- [diagrams/plsql-conversion.drawio](diagrams/plsql-conversion.drawio)（[plsql-conversion.png](diagrams/plsql-conversion.png)、[plsql-migrate-flow.png](diagrams/plsql-migrate-flow.png)）— PL/SQL 変換の構成（解析 → 判定 → 生成と報告 → 実 DB での検証）と、承認つきの移行の流れ
- 細部まで追う図は [アーキテクチャ](design/architecture.md) の Mermaid。draw.io の図を直したら `drawio -x -f png -s 2 -b 10 -p <ページ番号（1 始まり）> -o <名前>.png <名前>.drawio` で PNG を出し直す
- `slides/*.py` — 説明資料（Google スライド）の生成元。全体の概要、DML ベンチマーク、バックエンドの比較、変換ツールの新旧比較

## 文書を足す・直すとき

- 使い方が変わったら `guide/` を直す。README には概要とクイックスタートだけを置く
- 測り直したら `reports/` の該当文書を取り直し、先頭の日付に追記する（新しい文書を増やさない）
- 途中経過の報告（フェーズの完了報告など）は置かない。いまの数値は [PL/SQL → Java 変換](guide/plsql-conversion.md) の「現在地」、決定は実装計画 §9、経緯は git の履歴にある
- コードやテストが参照している文書（`design/plsql-kpi.md`、`plsql-migration/*.md`、実装計画など）を動かすときは、`git grep <ファイル名>` で参照も直す
