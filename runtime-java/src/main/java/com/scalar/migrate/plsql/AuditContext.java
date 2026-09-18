package com.scalar.migrate.plsql;

import java.time.OffsetDateTime;

/**
 * 呼び出し側が渡す「誰が」「いつ」。移行元の {@code USER} と {@code SYSTIMESTAMP} の置き換え先である
 * （計画 §9、#1・#8）。
 *
 * <p><b>引数にしてあるのは、実行コンテキストから黙って取ると検証できなくなるからである。</b>
 * trigger を Service へ移す移行（#12 §0）は「この表へのすべての書き込みが Service を通る」ことに依存して
 * いて、その担保は<b>呼び出し側が何を渡したかを見る</b>ことで取る。周囲の ThreadLocal などから取ると、
 * テストからも監査からも「誰が書いたか」の出所が見えなくなる。
 *
 * <p>時刻も同じ理由でここにある。ハーネスは Oracle の {@code SYSDATE} を
 * {@code ALTER SYSTEM SET FIXED_DATE} で固定できるが、<b>{@code SYSTIMESTAMP} には届かない</b>
 * （実機で測定済み、{@code semantics.json} の {@code fixedDatePinsSystimestamp: false}）。だから
 * {@code SYSTIMESTAMP} 由来の列は全シナリオでマスクされ、主要な副作用が一度も比較されていなかった。
 * 呼び出し側から渡せば固定でき、比較できる。
 *
 * <p>{@code SYSDATE} はここに入れていない。固定できるので既に比較できており、移すと多くの routine に
 * 引数が増えるだけである。Oracle も両者を別々に読むので、両方使う routine に単一の瞬間は元から無い。
 */
public interface AuditContext {

  /**
   * 書いた主体。移行元が {@code USER}（DB セッションのユーザ）で記録していたものの置き換え先。
   *
   * <p><b>何を記録するのが正しいかは業務側の決定である</b>（#1）。DB セッションのユーザを記録して
   * いたのは Oracle の都合であって、監査要件が「操作した人」を求めているならこれは改善だが、
   * 「どの接続か」を求めているなら意味が変わる。
   */
  String user();

  /** 書いた時刻。移行元の {@code SYSTIMESTAMP} の置き換え先。 */
  OffsetDateTime now();

  /** 固定値の {@code AuditContext}。テストと、比較ハーネスが Oracle の書いた時刻を渡すときに使う。 */
  static AuditContext of(String user, OffsetDateTime now) {
    return new AuditContext() {
      @Override
      public String user() {
        return user;
      }

      @Override
      public OffsetDateTime now() {
        return now;
      }
    };
  }
}
