# クイックスタートの題材（図書の貸出）

[README](../../README.md) のクイックスタートと [はじめに](../guide/getting-started.md) が入力に使う、小さな題材です。
公開の GitHub の版にも入っているので、clone した直後からそのまま動きます。

| ファイル | 中身 |
|---|---|
| `library.sql` | Oracle の SQL 16 文（表 2 つ・索引・採番の DDL、読み取り 6 文、書き込み 5 文、COMMIT）。OK / WARN / PLANNED / ERROR がどれも出るように選んである |
| `plsql/src/schema.sql` | PL/SQL が参照する表の DDL（`%TYPE` の解決に使う） |
| `plsql/src/pkg_library.pks`、`pkg_library.pkb` | package 1 つ（貸出中の冊数を数える関数、本を貸す procedure、返却を記録する procedure） |
| `plsql/scalardb-schema.json` | 移行先の ScalarDB の表定義（Schema Loader の形。namespace は `library`） |

## 出所

このディレクトリのファイルは、すべてこのリポジトリの作者が 2026-09-29 にクイックスタートのために書いた合成のものです。
実在のシステム、顧客や他社のコード・SQL・データは使っていません。リポジトリと同じ [MIT License](../../LICENSE) です。

## 使い方

コマンドと出力の読み方は [はじめに](../guide/getting-started.md) にあります。自分の SQL や PL/SQL で試すときは、パスを差し替えます。
