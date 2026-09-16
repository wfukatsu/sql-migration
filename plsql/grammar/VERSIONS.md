# Vendored PL/SQL grammar

設計書 §17-1「ANTLR grammar は外部更新を自動追従せず、fixture で検証した commit を vendor 化する」に従い、
上流を固定して取り込んでいる。更新は `make -C plsql/grammar fetch` → `make -C plsql/grammar` →
テスト（`pytest tests/test_plsql_grammar.py`）の順で行い、この表と Makefile の `GRAMMARS_V4_SHA` を同時に更新する。

| 項目 | 値 |
|---|---|
| 上流 | [antlr/grammars-v4](https://github.com/antlr/grammars-v4) `sql/plsql` |
| 固定 commit | `dc6d678008e1a6090c75adab1237d504ba7c276a`（2026-09-12、`[PlSql] Support multiple rows in values_clause` #5003） |
| 取り込み日 | 2026-09-16 |
| ANTLR ツール | 4.13.2（`build/antlr/` に取得。commit しない） |
| Python ランタイム | `antlr4-python3-runtime==4.13.2`（`requirements.txt`） |
| ライセンス | 上流 grammars-v4 は BSD-3-Clause |

## ファイル構成

```text
plsql/grammar/
  PlSqlLexer.g4        上流のまま（未改変）
  PlSqlParser.g4       上流のまま（未改変）
  python3/             上流 sql/plsql/Python3/ のまま（未改変）
    PlSqlLexerBase.py    lexer の superClass
    PlSqlParserBase.py   parser の superClass
    transformGrammar.py  .g4 のアクションを Java 記法から Python 記法へ書き換える
  generated/           ANTLR の出力（commit 済み。手編集禁止）
  patches/             上流へ当てたパッチ（現時点では空）
```

`generated/` を commit しているのは、利用側に Java と ANTLR ツールを要求しないため。
`pip install -r requirements.txt` だけで parse できる状態を維持する。

## 生成手順が 3 段になっている理由

1. `transformGrammar.py` — `.g4` の埋め込みアクションは Java 記法（`this.`、`&&`、`||`）で書かれており、
   そのままでは Python ターゲットで動かない。上流のこのスクリプトが Python 記法へ書き換える。
2. `antlr4 -Dlanguage=Python3 -visitor` — lexer / parser / listener / visitor を生成する。
3. base class の内部 import を相対 import へ書き換え — ANTLR 4.13 は `superClass` の import を
   `if "." in __name__` で package 対応にして出力するが、`PlSqlParserBase.py` がメソッド内で行う
   `from PlSqlLexer import ...` は上流のまま package 非対応なので、ここだけ書き換える。

## 確認済みの挙動

- キーワードは大文字小文字を問わない（`begin` も `BEGIN` も通る）。呼び出し側で入力を大文字化する必要はなく、
  文字列リテラルの元の大小文字はそのまま保持される。
- 上流 README が明記しているとおり、この文法は曖昧性を含む。パース時間は大きめで、
  package body 15 行程度で約 2.4 秒（初回。ATN のシリアライズ解凍を含む）。
  Phase 1 で routine 単位の並列化（設計書 §14 性能）を前提にする。
