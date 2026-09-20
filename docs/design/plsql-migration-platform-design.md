# ANTLR・SQLGlotを用いたPL/SQL変換基盤 実装設計

作成日: 2026-09-16  
想定ターゲット: Java 21 / Spring Boot / ScalarDB SQL（必要に応じてJDBC・PostgreSQLにも拡張）

## 1. 結論

PL/SQLを直接Javaへ逐語変換してはいけない。ANTLRでPL/SQL全体を解析し、型・制御フロー・SQL・トランザクション・例外・副作用を保持する中間表現（Migration IR）へ変換する。その中のSQL断片だけをSQLGlotへ渡し、正規化、依存分析、方言変換を行う。最後に、ターゲット固有のポリシーを適用してJava/Spring、ScalarDB SQL、DTO、テスト、移行レポートを生成する。

変換結果は次の3段階に必ず分類する。

| 判定 | 意味 | 処理 |
|---|---|---|
| AUTO | 規則で意味を保存できる | コード生成と自動テスト |
| REVIEW | 変換候補は生成できるが人の判断が必要 | コード、根拠、警告、代替案を生成 |
| REDESIGN | アーキテクチャ変更が必要 | コードを確定生成せず設計課題として出力 |

主要KPIは行数ベースの変換率ではなく、`意味的同等性テスト合格率`、`AUTO判定の精度`、`未解決リスク件数`とする。

## 2. スコープ

### 2.1 MVPの対象

- Package specification/body、Procedure、Function
- 変数、定数、型宣言、引数、戻り値
- IF、CASE、LOOP、WHILE、FOR、EXIT、CONTINUE
- SELECT INTO、INSERT、UPDATE、DELETE、MERGE
- Static Cursor、Cursor FOR LOOP
- ローカル例外、標準例外、`RAISE`、`RAISE_APPLICATION_ERROR`
- `%TYPE`、`%ROWTYPE`
- COMMIT、ROLLBACK、SAVEPOINTの検出と再設計判定
- Oracle組込み関数の変換表
- Java/Spring Service、Repository、DTO、JUnitテスト雛形の生成
- ScalarDB SQL対応SQLと、非対応SQLの分類

### 2.2 MVPでは解析・警告までとする対象

- `EXECUTE IMMEDIATE`、`DBMS_SQL`
- Autonomous Transaction
- Trigger
- DB Link、Synonymを介した外部依存
- Scheduler、Advanced Queue、UTL_*、ファイル・メール・HTTPアクセス
- Object Type、Nested Table、VARRAY、Pipelined Function
- Wrapped PL/SQL
- 動的に構築されるオブジェクト名
- Oracle固有ロック、分離レベル、ヒントへの依存

## 3. 論理アーキテクチャ

| 層 | コンポーネント | 主な責務 | 推奨実装 |
|---|---|---|---|
| 収集 | Source Collector | DDL、PL/SQL、メタデータ、権限、依存、実行統計の取得 | Java/Kotlin、Oracle JDBC、DBMS_METADATA |
| 字句・構文解析 | PL/SQL Frontend | SQL*Plus前処理、ANTLR Parse Tree生成、ソース位置保持 | Java 21、ANTLR4 grammars-v4を固定版でvendor化 |
| 意味解析 | Semantic Analyzer | Symbol Table、型解決、名前解決、overload解決、依存グラフ | Java/Kotlin |
| IR | Migration IR | PL/SQLと出力言語から独立した意味表現 | JSON Schema + Java sealed interface |
| SQL解析 | SQL Service | SQL抽出、Oracle AST、正規化、lineage、変換、警告 | Python、SQLGlot、FastAPIまたはworker |
| 解析 | Program Analysis | CFG、call graph、read/write set、transaction、side effect | Java/Kotlin |
| 判定 | Rule & Policy Engine | AUTO/REVIEW/REDESIGN、変換規則、確信度 | 宣言的YAML + Java rule runtime |
| 生成 | Target Generator | Java/Spring、ScalarDB SQL、DTO、設定、テスト生成 | JavaPoet、テンプレートは最小限 |
| AI補助 | LLM Remediator | 説明、候補、テスト補完。確定変換はしない | 構造化入出力、閉域対応 |
| 検証 | Differential Test Harness | Oracle版と変換版の結果・副作用比較 | JUnit/Testcontainers/Oracle検証環境 |
| 管理 | Migration Workbench | 課題、差分、承認、再生成、監査ログ | Web UI + API + PostgreSQL |

### 3.1 変換パイプライン

1. Oracleからソース、DDL、型、依存関係をスナップショット取得する。
2. SQL*Plus命令、コメント、`/`区切りを前処理し、元ファイル位置とのSource Mapを作る。
3. ANTLRでPL/SQL全体をParse Tree化する。
4. Symbol Tableを構築し、識別子、型、overload、参照先を解決する。
5. Parse Treeを型付きMigration IRへloweringする。
6. IR中のSQL断片をバインド変数付きSQLとしてSQLGlotへ渡す。
7. SQLGlotでOracle SQL AST、table/column lineage、型注釈、canonical SQLを生成する。
8. PL/SQL IRとSQL ASTを統合し、CFG、call graph、read/write set、transaction graphを作る。
9. Rule EngineがAUTO/REVIEW/REDESIGNと根拠を判定する。
10. Target GeneratorがJava/Spring、ScalarDB SQL、DTO、テストを生成する。
11. 静的検証、コンパイル、SQL構文検証、差分テストを実施する。
12. 人がREVIEW項目を承認し、決定をルールとして蓄積して再生成する。

## 4. ANTLRとSQLGlotの責務境界

| 項目 | ANTLR側 | SQLGlot側 |
|---|---|---|
| Package、Procedure、Function | 構文解析する | 対象外 |
| 制御構造・例外 | 構文解析しIR化する | 対象外 |
| 変数スコープ・Package状態 | Symbol Tableで解決 | 対象外 |
| SQLの切り出し | ソース位置とPL/SQL変数を保持して抽出 | SQL ASTへ解析 |
| テーブル・列参照 | PL/SQL側の参照文脈を付与 | ASTとschemaから抽出 |
| SQL方言変換 | 行わない | Oracleからターゲット方言へ変換 |
| ScalarDB SQL対応判定 | Rule Engineへ渡す | AST特徴量を供給する |
| 動的SQL | 文字列構築式をIR化 | 定数化できたSQLだけ解析する |
| コード生成 | IR生成まで | SQL文字列またはAST生成まで |

SQLGlotはvalidatorではなく、パース成功が実行成功を意味しない。このため`unsupported_level=RAISE`を標準にし、必ずターゲット側の構文・統合テストを追加する。また、型依存変換のためOracleメタデータからSQLGlotのschemaを生成し、`qualify`と`annotate_types`を適用する。

## 5. Migration IR設計

### 5.1 設計原則

- Parse Treeを出力言語のASTへ直接変換しない。
- すべてのIRノードに元ソース位置、安定ID、型、確信度、診断情報を持たせる。
- Oracle固有意味を消さず、明示的な属性として保持する。
- SQLは文字列だけでなく、SQLGlot ASTの安定した独自表現へ変換して保持する。SQLGlot内部クラスを永続フォーマットにしない。
- IR Schemaにversionを持たせ、変換ルールと生成物の再現性を保証する。

### 5.2 主要IRノード

```text
Program
  modules: Package | Procedure | Function | Trigger
  symbols: SymbolTable
  dependencies: DependencyGraph

Routine
  id, name, parameters, returnType
  declarations, body, exceptionHandlers
  authId, determinism, purity
  transactionEffects, externalEffects

Statement
  Assignment | If | Case | Loop | SqlOperation | Call
  Raise | Return | Commit | Rollback | Savepoint | DynamicSql

SqlOperation
  kind, originalSql, bindVariables
  normalizedAst, readSet, writeSet
  cardinalityExpectation, lockingMode
  targetCompatibility, diagnostics
```

### 5.3 型表現

`OracleType`と`TargetType`を分離する。`NUMBER`は精度・scaleが不明なままJava型に確定しない。

| Oracle型 | Java候補 | 判定上の注意 |
|---|---|---|
| NUMBER(p,0), p <= 9 | Integer | overflow境界テスト |
| NUMBER(p,0), p <= 18 | Long | overflow境界テスト |
| NUMBER / NUMBER(p,s) | BigDecimal | scale、roundingを保持 |
| VARCHAR2/NVARCHAR2 | String | byte/char semantics、空文字=`NULL` |
| DATE | LocalDateTime候補 | Oracle DATEは時刻を含む |
| TIMESTAMP WITH TIME ZONE | OffsetDateTime | セッションTZ依存を検出 |
| RAW | byte[] | 文字列変換を禁止 |
| CLOB/BLOB | String/byte[]/stream | サイズとストリーム要件 |
| BOOLEAN（PL/SQL） | boolean/Boolean | NULLを取り得るならBoolean |
| `%TYPE` | 参照列から解決 | DDL snapshot IDを記録 |
| `%ROWTYPE` | 生成Record/DTO | 列順ではなく列名で対応 |

## 6. PL/SQL機能別の移行方針

### 6.1 プログラム構造

| PL/SQL機能 | 推奨移行 | 判定 | 実装規則 |
|---|---|---|---|
| Package specification | Java interfaceまたは公開Service API | AUTO/REVIEW | 公開routineとtypeを抽出 |
| Package body | Spring `@Service` | AUTO/REVIEW | statelessなら自動生成 |
| private procedure/function | private methodまたは専用component | AUTO | call graphで可視性決定 |
| overloaded routine | Java overload | REVIEW | NULLや暗黙変換で解決差があればrename |
| nested routine | private methodまたはlambda不可なら内部component | REVIEW | lexical captureを引数へ明示化 |
| Package initialization | Application startupに移さない | REDESIGN | 初期化の副作用を明示Serviceへ移す |
| `AUTHID DEFINER/CURRENT_USER` | Service identity/呼出者権限 | REDESIGN | 認証・認可モデルを別設計 |

### 6.2 状態・変数・型

| PL/SQL機能 | 推奨移行 | 判定 | 注意点 |
|---|---|---|---|
| local variable/constant | Java local/final | AUTO | 初期値評価順を保持 |
| Package variable | 引数、永続化、明示SessionContext | REDESIGN | Singleton beanのfieldへ置かない |
| record | Java record | AUTO | NULL性を保持 |
| associative array | Map/List | REVIEW | index型、順序、疎配列 |
| nested table/VARRAY | Listまたは別テーブル | REVIEW/REDESIGN | SQL型との境界を確認 |
| subtype | value object/validation | REVIEW | 範囲制約を生成 |
| `NOCOPY` | 通常の戻り値/Result型 | REVIEW | 例外時の部分更新意味が異なる |
| OUT/IN OUT | Result record | AUTO/REVIEW | 引数書換えを避ける |

### 6.3 制御構造

| PL/SQL機能 | Java移行 | 判定 | 注意点 |
|---|---|---|---|
| IF/CASE | if/switch | AUTO | SQLの三値論理が混じる式はREVIEW |
| LOOP/WHILE/FOR | loop/while/for | AUTO | loop変数scopeを保持 |
| Cursor FOR LOOP | query結果iteration | REVIEW | N+1、メモリ、fetch size |
| EXIT/CONTINUE | break/continue | AUTO | labelを保持 |
| GOTO | 構造化制御へ再構成 | REDESIGN | CFGでregion解析 |
| recursion | Java recursionまたはiteration | REVIEW | stack、DB round-trip |

### 6.4 SQL・データアクセス

| PL/SQL機能 | 推奨移行 | 判定 | 注意点 |
|---|---|---|---|
| SELECT INTO | Repositoryのsingle-result API | AUTO/REVIEW | 0行=`NO_DATA_FOUND`、複数行=`TOO_MANY_ROWS`を再現 |
| INSERT/UPDATE/DELETE | ScalarDB SQL/JDBC | AUTO/REVIEW | affected rowsを保持 |
| MERGE | 対応SQL、存在確認＋更新、upsert | REVIEW | atomicityと競合条件を確認 |
| RETURNING INTO | update結果または再読込 | REVIEW | 同一Txと整合性 |
| Sequence | DB sequence、採番Service、UUID | REDESIGN | 順序・欠番許容を要件化 |
| DUAL | 式評価へ除去 | AUTO | DB依存関数は別判定 |
| Oracle関数 | function mapping registry | AUTO/REVIEW | NULL、日付、文字列意味を比較 |
| Hint | 原則除去し性能要件へ変換 | REVIEW | 意味ではなく運用意図として保存 |
| CONNECT BY | recursive CTE/アプリ探索 | REVIEW | ScalarDB SQL対応を確認 |
| PIVOT/MODEL | SQL書換え/アプリ集計 | REVIEW/REDESIGN | 性能テスト必須 |
| row locking | ターゲットのlock/OCC | REDESIGN | `FOR UPDATE NOWAIT/SKIP LOCKED`を個別設計 |

### 6.5 Cursor・バルク処理

| 機能 | 推奨移行 | 判定 | 注意点 |
|---|---|---|---|
| implicit cursor | Repository呼出し | AUTO | `SQL%ROWCOUNT`等をResultに保持 |
| explicit cursor | stream/page/batch | REVIEW | cursor寿命とTx境界 |
| parameterized cursor | query method | AUTO/REVIEW | parameter binding |
| BULK COLLECT | batch read | REVIEW | LIMIT、メモリ上限 |
| FORALL | batch write | REVIEW | 一括失敗/部分失敗の意味 |
| SAVE EXCEPTIONS | item別結果と再試行 | REDESIGN | 原子性を業務要件で決定 |

### 6.6 例外

| PL/SQL機能 | 推奨移行 | 判定 | 実装規則 |
|---|---|---|---|
| predefined exception | typed Java exception | AUTO/REVIEW | Oracleと同じ発火条件をテスト |
| user exception | domain exception | AUTO | エラーコードregistryを生成 |
| `RAISE_APPLICATION_ERROR` | domain exception + code | AUTO | -20000帯を保持またはmapping |
| `WHEN OTHERS` | catch最終段 | REVIEW | `SQLCODE/SQLERRM`依存を検出 |
| exception後のOUT値 | Result/例外設計 | REVIEW | PL/SQLのobservable behaviorを確認 |

### 6.7 トランザクション

| PL/SQL機能 | 推奨移行 | 判定 | 実装規則 |
|---|---|---|---|
| routine内COMMIT/ROLLBACKなし | Service入口でTx | AUTO/REVIEW | call graph全体で確認 |
| COMMIT/ROLLBACKあり | use case単位へ再境界化 | REDESIGN | 逐語変換禁止 |
| SAVEPOINT/ROLLBACK TO | 処理分割または補償 | REDESIGN | target能力と業務要件で決定 |
| Autonomous Transaction | audit/event/outbox等へ分離 | REDESIGN | 親失敗時にも残る意味を保持 |
| distributed DB Link Tx | ScalarDB分散Tx/API連携 | REDESIGN | 参加DB、timeout、再試行設計 |

生成Javaでは、原則として公開Application Serviceに`@Transactional`相当の境界を置き、private methodやRepositoryに勝手に境界を増やさない。ScalarDBのトランザクションはtry-with-resources相当の定型に集約し、commit/abortを1箇所で制御する。

### 6.8 動的SQL・外部副作用

| 機能 | 推奨移行 | 判定 | 実装規則 |
|---|---|---|---|
| 定数`EXECUTE IMMEDIATE` | 定数畳込み後SQLGlotへ | REVIEW | bindを復元 |
| 条件分岐による有限SQL | variantごとに静的query化 | REVIEW | 組合せ上限を設定 |
| 動的where/order | allowlist型query builder | REDESIGN | 文字列連結を生成しない |
| 動的table/column | 明示allowlist/専用Repository | REDESIGN | injectionと権限を評価 |
| DBMS_SQL | 実行ログも使いquery family抽出 | REDESIGN | 静的解析だけで完結しない |
| UTL_HTTP/SMTP/FILE | adapter経由の外部Service | REDESIGN | timeout、再試行、冪等性 |
| DBMS_SCHEDULER/JOB | scheduler/batch基盤 | REDESIGN | 実行保証と重複防止 |
| AQ | message broker/outbox | REDESIGN | delivery semanticsを明示 |

### 6.9 Trigger・View・セキュリティ

| 機能 | 推奨移行 | 判定 | 注意点 |
|---|---|---|---|
| validation trigger | Service validation + DB constraint | REDESIGN | 全書込経路を統制 |
| audit trigger | audit interceptor/outbox/PCE | REDESIGN | before/after値、失敗時挙動 |
| derived-column trigger | app計算またはgenerated column | REVIEW | 一元性 |
| cascading trigger | 明示use case | REDESIGN | 隠れた副作用を除去 |
| complex view | query/read model | REVIEW | 更新可能viewは個別設計 |
| VPD/RLS | ScalarDB ABAC/Service認可 | REDESIGN | 呼出者contextと漏えいテスト |
| grant/role | API・Service・DB権限へ再配置 | REDESIGN | `AUTHID`と合わせて評価 |

## 7. SQLGlot統合設計

### 7.1 SQL抽出

ANTLR visitorは各SQL文から次を作る。

```json
{
  "sqlId": "pkg_order.create_order#stmt-12",
  "sourceDialect": "oracle",
  "text": "SELECT status INTO :v_status FROM orders WHERE id = :p_id",
  "binds": [
    {"name": "v_status", "direction": "OUT", "oracleType": "VARCHAR2"},
    {"name": "p_id", "direction": "IN", "oracleType": "NUMBER(19)"}
  ],
  "cardinalityExpectation": "EXACTLY_ONE",
  "sourceRange": {"file": "pkg_order.pkb", "startLine": 42, "endLine": 44}
}
```

PL/SQL固有の`INTO`はSQLGlotに渡す前に抽出し、SELECT本体と代入先を分離する。PL/SQL変数は衝突しない名前のbind placeholderへ置換し、Source Mapを保持する。

### 7.2 SQL処理順序

1. `parse_one(sql, dialect="oracle")`
2. parse errorを構造化診断へ変換
3. schemaを用いてidentifier qualification
4. type annotation
5. table/column/function/subquery特徴量を抽出
6. Oracle固有関数を独自transformで正規化
7. ScalarDB SQL capability matrixで適合性判定
8. 対応SQLはcustom target generatorで出力
9. 非対応SQLは分解案またはRepository実装案を生成
10. `unsupported_level=RAISE`相当で黙った意味欠落を禁止

### 7.3 ScalarDB SQL dialect adapter

SQLGlotのOracle parserは利用するが、ScalarDB SQL出力は、初期段階ではSQLGlotへの完全dialect実装より`Capability Checker + Controlled Generator`を推奨する。

- Phase 1: 許可するASTノードのallowlistを作る。
- Phase 2: ScalarDB SQLへ安全に出せる部分集合のみ生成する。
- Phase 3: 関数、型、DDL、DMLのmappingを拡張する。
- Phase 4: 十分なfixtureが揃った後、SQLGlot custom dialect/plugin化する。

これにより、未対応構文を近似SQLとして黙って出力する事故を避けられる。

## 8. ルールエンジン

ルールはコードに埋め込まず、条件、判定、理由、対策、テスト要求を宣言できるようにする。

```yaml
id: TX-001
match:
  node: CommitStatement
  inside: Routine
decision: REDESIGN
severity: critical
message: Routine内のCOMMITはServiceトランザクション境界へ逐語変換できません
remediation:
  - Split use case at durable boundary
  - Define retry and idempotency policy
requiredTests:
  - rollback_boundary
  - partial_failure
```

確信度はLLMの自己申告値ではなく、規則と検証結果から計算する。

```text
confidence = ruleCoverage × symbolResolution × typeResolution
           × targetCapability × testEvidence
```

どれかが0ならAUTOにしない。

## 9. 生成コードの構造

```text
generated/
  src/main/java/
    application/       Application Service、transaction boundary
    domain/            Value Object、Domain Exception
    infrastructure/    ScalarDB/JDBC Repository
    migration/         compatibility helper（期限付き）
  src/test/java/
    characterization/  Oracle由来fixture
    differential/      Oracle対新実装
    property/          境界値、NULL、日付、数値
  migration-report/
    inventory.json
    diagnostics.sarif
    decisions.json
    unresolved.md
    traceability.csv
```

生成ファイルと手編集ファイルを分離する。生成コードへの直接修正は禁止し、承認した変更は変換ルール、mapping、またはextension pointとして戻す。

## 10. 検証設計

### 10.1 検証レベル

| レベル | 検証内容 | 合格条件 |
|---|---|---|
| Parser | PL/SQLを構文解析できる | syntax coverage、source range保持 |
| Semantic | 名前・型・依存を解決 | unresolved symbolが閾値以下 |
| IR | Parse→IRの意味保持 | golden IR一致 |
| SQL | Oracle SQL→target SQL | unsupportedの黙殺ゼロ |
| Compile | 生成Javaをcompile | warning policyを満たす |
| Unit | 分岐・例外・mapping | branch/feature coverage |
| Differential | Oracleと新実装を比較 | 結果、例外、DB差分が一致 |
| Concurrency | lock/競合/再試行 | 定義したisolation invariantを満たす |
| Performance | representative workload | SLO内、round-trip増大を検知 |

### 10.2 差分テストの比較対象

- 戻り値、OUT/IN OUT値
- 例外型、業務エラーコード、発生条件
- 更新前後の行集合
- 影響行数
- commit/rollback後の永続状態
- 監査・メッセージなどの副作用
- NULL、空文字、末尾空白
- 数値精度、丸め、overflow
- 日付、timezone、NLS設定
- 同時実行時の不変条件

Oracle側実行と新実装側実行は、同一fixtureから独立環境を作り、結果をcanonical JSONへ変換して比較する。順序が仕様でない結果は順序を正規化するが、勝手に重複を除去しない。

## 11. LLMの利用範囲とガードレール

LLMは必須コンポーネントにしない。次だけに使用する。

- REDESIGN項目の説明と代替案
- 未対応Oracle関数のmapping候補
- Javaコード候補とテスト候補
- 動的SQLの有限variant推定
- 業務用語を使った移行レポート

入力は対象routineのIR、依存先interface、schema、診断に限定する。出力はJSON Schemaで拘束し、生成コードはREVIEW扱いにする。資格情報、実データ、秘密値を送らない。プロンプト、モデル、入力hash、出力、採否を監査記録する。

## 12. API設計

| API | 用途 |
|---|---|
| `POST /projects` | 移行プロジェクト作成 |
| `POST /projects/{id}/snapshots` | ソースsnapshot登録 |
| `POST /snapshots/{id}/analyze` | parse・semantic analysis実行 |
| `GET /snapshots/{id}/inventory` | 資産・依存・複雑度取得 |
| `POST /snapshots/{id}/convert` | target policyを指定して生成 |
| `GET /conversions/{id}/diagnostics` | 判定、根拠、source location取得 |
| `POST /diagnostics/{id}/decisions` | 人の判断とmappingを登録 |
| `POST /conversions/{id}/verify` | compile・差分テスト実行 |
| `GET /conversions/{id}/artifacts` | code、report、test取得 |

全ジョブは入力snapshot ID、grammar version、SQLGlot version、rule-set version、generator versionを固定し、同じ入力から同じ結果を再生成できるようにする。

## 13. リポジトリ構成

```text
plsql-modernizer/
  frontend-antlr/          lexer/parser、SQL*Plus preprocessor、visitor
  semantic-core/          symbol/type/name resolution
  migration-ir/           IR model、JSON Schema、serializer
  sql-service/            Python/SQLGlot service
  analysis-engine/        CFG、call graph、effects、transactions
  rule-engine/            policies、capability matrix
  generator-java/         Java/Spring generator
  generator-scalardb/     ScalarDB SQL/Repository generator
  verification-harness/   differential and property tests
  migration-api/          orchestration API
  migration-ui/           review workbench
  fixtures/               versioned PL/SQL corpus and golden outputs
```

JavaとPython間は、SQLGlotの内部ASTを直接RPC公開せず、バージョン管理した`SqlAnalysisResult` JSONで接続する。

## 14. 非機能要件

- 再現性: 全依存version、rule、schema snapshotを固定する。
- トレーサビリティ: 生成コードのmethod/statementから元PL/SQL行へ辿れる。
- セキュリティ: ソースを機密情報として扱い、暗号化、最小権限、監査を実施する。
- 障害分離: 1 routineの失敗で全schemaの解析を停止しない。
- 増分性: source hashが変わったroutineと依存先だけ再解析する。
- 拡張性: target generatorとcapability matrixをplugin化する。
- 性能: 解析処理はroutine単位で並列化するが、名前解決用schema snapshotは不変にする。
- 品質: parse/convert warningを成功扱いで隠さない。

## 15. 実装ロードマップ

### Phase 0: 2週間 — Corpusと評価基準

- 実案件から匿名化した20〜30本を収集する。
- 機能タグ、期待判定、期待結果を付ける。
- KPIとAUTO禁止条件を確定する。
- ANTLR grammarとSQLGlot versionを固定する。

完了条件: PL/SQL機能マトリクス、fixture、golden result、評価方法が揃う。

### Phase 1: 4週間 — Analyzer MVP

- SQL*Plus preprocessor
- ANTLR parser/visitor
- Source Map、Symbol Table、依存グラフ
- Migration IR v1
- SQL抽出とSQLGlot連携
- inventory/diagnostic report

完了条件: corpusの90%以上をparseし、依存と未解決理由を表示できる。これは変換率ではなくparser coverage目標である。

### Phase 2: 4〜6週間 — Safe Generator

- 基本制御構造、型、例外のJava生成
- SELECT/基本DMLのRepository生成
- ScalarDB capability checker
- AUTO/REVIEW/REDESIGN rule engine
- Java compile test、golden test

完了条件: AUTO対象が全件compileし、重大な意味欠落がゼロ。

### Phase 3: 4〜6週間 — Verification

- Oracle characterization runner
- differential DB-state comparison
- NULL/数値/日付property test
- transaction/concurrency tests
- SARIFとreview workflow

完了条件: AUTO対象の意味的同等性テストが100%、REVIEW対象は差分理由を説明可能。

### Phase 4: 継続 — 高度機能

- dynamic SQL partial evaluation
- trigger・scheduler・外部副作用の設計テンプレート
- LLM remediator
- 承認決定からrule提案
- 他ターゲットDB/C#へのgenerator追加

## 16. PoCで選ぶべきPL/SQL

同じようなCRUDを大量に選ばず、次を1〜3本ずつ含める。

- 単純CRUD procedure
- SELECT INTOと例外
- Package内private call
- `%TYPE`・`%ROWTYPE`
- Cursor loop
- BULK COLLECT/FORALL
- routine内COMMIT
- dynamic SQL
- TriggerまたはDB Link
- 日付・NUMBER・NULL依存
- 同時更新・lock依存

PoCの合否は以下で決める。

- 全入力のparse率
- symbol/type解決率
- AUTO/REVIEW/REDESIGN判定の適合率
- AUTO生成コードのcompile率
- 意味的同等性テスト合格率
- 人手修正時間
- 1,000行当たりの未解決重大リスク数

## 17. 重要な設計判断

1. ANTLR grammarは外部更新を自動追従せず、fixtureで検証したcommitをvendor化する。
2. Parse TreeからJavaを直接生成しない。
3. SQLGlotはSQL断片に限定し、PL/SQL procedural semanticsを担当させない。
4. SQLGlotのbest-effort変換を本番生成に使わず、unsupportedは失敗させる。
5. ScalarDB SQLは最初から完全dialect実装せず、allowlist方式で安全な部分集合から始める。
6. トランザクション、Package状態、Trigger、動的SQLは原則REDESIGNから開始する。
7. LLM出力をAUTOに昇格させない。規則化と差分テスト通過後にのみ昇格する。
8. 生成物より、source-to-target traceabilityと検証証跡を製品価値の中心に置く。

## 18. 推奨する最初の実装バックログ

| 優先度 | Epic | 成果物 |
|---|---|---|
| P0 | Source snapshot | DDL/PLSQL/metadata collector、manifest |
| P0 | PL/SQL frontend | parser、preprocessor、Source Map |
| P0 | Semantic model | Symbol Table、type resolver、dependency graph |
| P0 | Migration IR v1 | model、JSON Schema、golden tests |
| P0 | SQLGlot bridge | extract/parse/qualify/annotate/lineage API |
| P0 | Safety rules | transaction、state、dynamic SQL、trigger検出 |
| P1 | Java generator | Service、DTO、exception、basic control flow |
| P1 | ScalarDB checker | SQL capability matrix、controlled generator |
| P1 | Verification | compile、golden、differential test harness |
| P1 | Reporting | inventory、SARIF、traceability、unresolved report |
| P2 | Review UI | source/IR/target差分、判定承認、再生成 |
| P2 | LLM assistance | structured remediation proposal |

## 参考資料

- [ANTLR grammars-v4 PL/SQL grammar](https://github.com/antlr/grammars-v4/tree/master/sql/plsql)
- [SQLGlot API documentation](https://sqlglot.com/sqlglot.html)
- [Oracle Database PL/SQL Language Reference](https://docs.oracle.com/en/database/oracle/oracle-database/23/lnpls/)
- [ScalarDB documentation](https://scalardb.scalar-labs.com/docs/latest/)

