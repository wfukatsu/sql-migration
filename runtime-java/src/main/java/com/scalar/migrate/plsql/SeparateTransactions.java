package com.scalar.migrate.plsql;

import java.sql.Connection;

/**
 * 別のトランザクションを開いて、その中で 1 つの処理を回す口（#49）。
 *
 * <p>移行元の {@code PRAGMA AUTONOMOUS_TRANSACTION} は「親が rollback しても残る」という意味を持つ。生成コードは
 * その routine を {@code limits.yaml transactions.separate} の決定に従って**この口の中で**呼ぶ: 呼び先の Service を
 * この connection の上に組み立てて呼び、処理が返れば commit、例外なら rollback する。呼ぶ側のトランザクション
 * とは独立で、呼ぶ側が後で rollback しても残る。
 *
 * <p>実装は移行先の配線の側にある（本番ならトランザクションマネージャから新しい transaction / connection を
 * 取る。検証ハーネスは同じ properties でもう 1 本 connection を開く）。
 */
public interface SeparateTransactions {

  /** 別のトランザクションの中で回す処理。{@code connection} はそのトランザクションのもの。 */
  interface Body<T> {
    T run(Connection connection) throws Exception;
  }

  <T> T run(Body<T> body) throws Exception;
}
