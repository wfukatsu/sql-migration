# plsql-run/ — 実 DB で比べる用の部分集合

`make_runnable.py` が `plsql/src` からコンパイルできるユニットだけを写し、シナリオを起こしたもの。手で直さない。

- 写したユニット: 26（除いたもの: 12。理由は `make_runnable.py` の EXCLUDED）
- シナリオ: 24
- `work/`（golden の写し、生成物、capture、比較）は git に入れない
