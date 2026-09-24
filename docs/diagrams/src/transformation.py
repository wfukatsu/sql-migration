#!/usr/bin/env python3
"""docs/diagrams/architecture-transformation.drawio を書き出す（どのように構造とアプリケーションのアーキテクチャが変わるか）。

スライドに 9.2in 幅で載せても文字が 9pt 以上になるよう、920 x 430 の面に 13px 以上で描く。座標を手で持つので、
draw.io で開いて直すより、ここを直して書き出し直すほうが崩れない。

    python docs/diagrams/src/transformation.py
    cd docs/diagrams && for p in 1 2 3 4; do drawio -x -f png -s 3 -b 6 -p $p -o transformation-$p.png architecture-transformation.drawio; done
"""
from __future__ import annotations

from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "architecture-transformation.drawio"

BLUE, BLUE_L = "#2673BB", "#EAF2FB"
ORANGE, ORANGE_L = "#D79B00", "#FFF4E0"
GREEN, GREEN_L = "#4C9A2A", "#EEF7E9"
RED, RED_L = "#C0392B", "#FDECEA"
GREY, GREY_L = "#6B7280", "#F3F4F6"
INK = "#0F172A"


class Page:
    def __init__(self, ident: str, name: str, w: int = 920, h: int = 430):
        self.ident, self.name, self.w, self.h = ident, name, w, h
        self.cells: list[str] = []
        self.n = 0

    def _id(self) -> str:
        self.n += 1
        return f"{self.ident}{self.n}"

    def box(self, label, x, y, w, h, *, fill="#FFFFFF", stroke=BLUE, fs=13, bold=False, color=INK, align="center",
            valign="middle", shape="rounded=1;arcSize=8;", dashed=False, sw=1.5) -> str:
        i = self._id()
        style = (f"{shape}whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};strokeWidth={sw};fontSize={fs};"
                 f"fontColor={color};fontStyle={1 if bold else 0};align={align};verticalAlign={valign};spacing=4;"
                 f"{'dashed=1;' if dashed else ''}")
        self.cells.append(f'<mxCell id="{i}" value="{escape(label, quote=True)}" style="{style}" vertex="1" parent="1">'
                          f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry" /></mxCell>')
        return i

    def group(self, label, x, y, w, h, *, fill, stroke, fs=14) -> str:
        return self.box(label, x, y, w, h, fill=fill, stroke=stroke, fs=fs, bold=True, color=stroke, align="left",
                        valign="top", shape="rounded=1;arcSize=4;spacingLeft=6;", sw=2)

    def text(self, label, x, y, w, h, *, fs=12, color=GREY, align="center", bold=False) -> str:
        return self.box(label, x, y, w, h, fill="none", stroke="none", fs=fs, color=color, align=align, bold=bold,
                        shape="text;")

    def cyl(self, label, x, y, w, h, *, fill="#FFFFFF", stroke=GREY, fs=13) -> str:
        return self.box(label, x, y, w, h, fill=fill, stroke=stroke, fs=fs,
                        shape="shape=cylinder3;boundedLbl=1;size=8;")

    def edge(self, s, t, label="", *, color=INK, dashed=False, sw=2, exit=None, entry=None, pts=(), fs=11,
             arrow="block") -> None:
        i = self._id()
        style = (f"edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;strokeColor={color};strokeWidth={sw};fontSize={fs};"
                 f"fontColor={color};labelBackgroundColor=#FFFFFF;endArrow={arrow};endFill=1;"
                 f"{'dashed=1;' if dashed else ''}")
        if exit:
            style += f"exitX={exit[0]};exitY={exit[1]};exitDx=0;exitDy=0;"
        if entry:
            style += f"entryX={entry[0]};entryY={entry[1]};entryDx=0;entryDy=0;"
        points = ('<Array as="points">' + "".join(f'<mxPoint x="{x}" y="{y}" />' for x, y in pts) + "</Array>") if pts else ""
        self.cells.append(f'<mxCell id="{i}" value="{escape(label, quote=True)}" style="{style}" edge="1" parent="1" '
                          f'source="{s}" target="{t}"><mxGeometry relative="1" as="geometry">{points}</mxGeometry></mxCell>')

    def xml(self) -> str:
        body = "\n".join(self.cells)
        return (f'  <diagram id="{self.ident}" name="{escape(self.name, quote=True)}">\n'
                f'    <mxGraphModel dx="1200" dy="800" grid="0" page="1" pageWidth="{self.w}" pageHeight="{self.h}">\n'
                f'      <root>\n<mxCell id="0" />\n<mxCell id="1" parent="0" />\n{body}\n      </root>\n'
                f'    </mxGraphModel>\n  </diagram>\n')


# ------------------------------------------------------------------------------------------------------
# 1. 実行時の構成: 業務ロジックが DB の中からアプリの中へ移る
# ------------------------------------------------------------------------------------------------------
p1 = Page("a", "1. 実行時の構成 (現行 → 移行後)")
p1.group("現行: 業務ロジックは Oracle の中", 0, 0, 380, 430, fill=ORANGE_L, stroke=ORANGE)
app = p1.box("<b>アプリケーション</b><br>CALL pkg_points.use_points(…)<br>COMMIT / ROLLBACK", 40, 40, 300, 70, stroke=ORANGE)
p1.group("Oracle Database", 20, 150, 340, 260, fill="#FFFFFF", stroke=ORANGE, fs=13)
pkg = p1.box("<b>PL/SQL package pkg_points</b><br>検査・計算・例外・SQL", 40, 185, 300, 60, fill=ORANGE_L, stroke=ORANGE)
p1.box("行ロック<br>FOR UPDATE", 40, 262, 92, 50, fill=RED_L, stroke=RED, fs=12)
p1.box("sequence<br>trigger", 144, 262, 92, 50, fill=RED_L, stroke=RED, fs=12)
p1.box("SYSDATE<br>USER", 248, 262, 92, 50, fill=RED_L, stroke=RED, fs=12)
tbl = p1.cyl("members / point_history", 70, 332, 240, 64, stroke=ORANGE)
p1.edge(app, pkg, "JDBC", color=ORANGE)
p1.edge(pkg, tbl, color=ORANGE, exit=(0.08, 1), entry=(0.08, 0), pts=[(31, 300)]) if False else None
p1.text("DB が黙って保証していたもの（赤）", 40, 312, 300, 18, fs=11, color=RED)

p1.box("変換", 392, 180, 56, 56, fill=BLUE, stroke=BLUE, fs=15, bold=True, color="#FFFFFF",
       shape="shape=singleArrow;arrowWidth=0.55;arrowSize=0.45;")

p1.group("移行後: 業務ロジックはアプリの中（生成した Java）", 460, 0, 460, 430, fill=BLUE_L, stroke=BLUE)
p1.group("アプリケーション（JVM）", 480, 32, 420, 232, fill="#FFFFFF", stroke=BLUE, fs=13)
caller = p1.box("<b>呼び出し側</b>（人が書く）<br>begin → 呼ぶ → commit / rollback、衝突だけ再試行", 500, 62, 380, 50, stroke=GREEN, fill=GREEN_L, fs=12)
svc = p1.box("<b>PkgPointsService</b>（生成）<br>PL/SQL の本体を文の順のまま", 500, 128, 236, 54, fs=12)
hlp = p1.box("<b>Plsql</b> ヘルパ<br>Oracle の意味論・時計", 748, 128, 132, 54, fill=GREY_L, stroke=GREY, fs=11)
repo = p1.box("<b>PkgPointsRepository</b>（生成）　SQL 1 文 = 1 method", 500, 198, 380, 40, fs=12)
sdb = p1.box("<b>ScalarDB Cluster</b>　ScalarDB SQL（JDBC）<br>Consensus Commit: 衝突は commit で弾く", 480, 288, 420, 50, fill="#FFE6CC", stroke=ORANGE, fs=12)
be = p1.cyl("points.members / points.point_history<br>PostgreSQL・Oracle・Cassandra など", 540, 356, 300, 66, fs=12)
p1.edge(caller, svc, color=BLUE, exit=(0.31, 1), entry=(0.5, 0))
p1.edge(svc, repo, color=BLUE, exit=(0.5, 1), entry=(0.31, 0))
p1.edge(svc, hlp, color=GREY, dashed=True, sw=1.5, arrow="open")
p1.edge(repo, sdb, "ScalarDB SQL", color=BLUE)
p1.edge(sdb, be, color=ORANGE)

# ------------------------------------------------------------------------------------------------------
# 2. 1 回の呼び出し: トランザクションの境界と、衝突の分かり方が変わる
# ------------------------------------------------------------------------------------------------------
p2 = Page("b", "2. 1 回の呼び出し (use_points)")
def lane(page, x, title, names, fill, stroke):
    page.group(title, x, 0, 450, 430, fill=fill, stroke=stroke)
    ids = []
    width = 450 // len(names)
    for k, name in enumerate(names):
        cx = x + k * width + width // 2
        ids.append(cx)
        page.box(name, cx - 50, 30, 100, 32, fill="#FFFFFF", stroke=stroke, fs=12, bold=True)
        page.box("", cx - 1, 62, 2, 318, fill=stroke, stroke="none", shape="")
    return ids
def msg(page, x1, x2, y, label, color=INK, dashed=False, fs=11, bg=None):
    a = page.box("", x1 - 2, y - 2, 4, 4, fill=color, stroke="none", shape="ellipse;")
    b = page.box("", x2 - 2, y - 2, 4, 4, fill=color, stroke="none", shape="ellipse;")
    page.edge(a, b, color=color, dashed=dashed, sw=1.6)
    width = min(abs(x2 - x1) - 12, 9 + int(len(label) * fs * 0.78))
    page.box(label, (x1 + x2) / 2 - width / 2, y - 19, width, 16, fill=bg or "none", stroke="none", fs=fs, color=color, shape="text;")
o = lane(p2, 0, "現行（Oracle）", ["アプリ", "pkg_points", "表"], ORANGE_L, ORANGE)
msg(p2, o[0], o[1], 96, "CALL use_points(…)")
msg(p2, o[1], o[2], 132, "SELECT … FOR UPDATE", RED)
p2.box("<b>行をロック。</b>ほかの利用は、ここで待たされる", o[1] + 8, 142, 200, 38, fill=RED_L, stroke=RED, fs=11)
msg(p2, o[1], o[2], 214, "INSERT point_history")
msg(p2, o[1], o[2], 250, "UPDATE members")
msg(p2, o[1], o[0], 290, "戻る / ORA-20103", dashed=True)
msg(p2, o[0], o[2], 340, "COMMIT → ロックを離す", ORANGE, bg=ORANGE_L)
p2.text("境界: アプリ。安全にしているのは行ロック", 10, 394, 430, 26, fs=12, color=ORANGE, bold=True)

n = lane(p2, 470, "移行後（ScalarDB）", ["呼び出し側", "Service", "Repository", "ScalarDB"], BLUE_L, BLUE)
msg(p2, n[0], n[3], 92, "begin（setAutoCommit(false)）", GREEN, bg=BLUE_L)
msg(p2, n[0], n[1], 124, "usePoints(…)")
msg(p2, n[1], n[2], 152, "usePointsStmt3")
msg(p2, n[2], n[3], 180, "SELECT", BLUE)
msg(p2, n[1], n[2], 216, "Stmt7 / Stmt8")
msg(p2, n[2], n[3], 244, "INSERT, UPDATE", BLUE)
msg(p2, n[1], n[0], 276, "戻る / 例外", dashed=True)
msg(p2, n[0], n[3], 308, "commit", GREEN, bg=BLUE_L)
p2.box("<b>同じ行を読んで書いた別の commit があれば、ここで弾かれる</b>（DB-CORE-20013）→ rollback して最初から再試行",
       478, 318, 434, 42, fill=RED_L, stroke=RED, fs=11)
p2.text("境界: 呼び出し側。安全にしているのは、同じトランザクションで読んで書くこと", 474, 394, 442, 26, fs=11, color=BLUE, bold=True)
p2.text("ロックなし", n[2] + 4, 184, n[3] - n[2] - 8, 14, fs=10, color=BLUE)

# ------------------------------------------------------------------------------------------------------
# 3. 要素の対応: PL/SQL の何が、Java / ScalarDB の何になるか
# ------------------------------------------------------------------------------------------------------
p3 = Page("c", "3. 要素の対応 (PL/SQL → Java / ScalarDB)")
p3.text("PL/SQL（Oracle）", 0, 0, 330, 24, fs=15, color=ORANGE, bold=True)
p3.text("Java + ScalarDB", 560, 0, 360, 24, fs=15, color=BLUE, bold=True)
p3.text("変わること", 340, 0, 210, 24, fs=13, color=GREY, bold=True)
rows = [
    ("package pkg_points", "class PkgPointsService", "1 module = 1 Service", None),
    ("PROCEDURE / FUNCTION（仕様部にある）", "public method（OUT 引数は record）", "package 内だけの関数は private", None),
    ("SQL 1 文", "Repository の method 1 つ", "原文の位置がコメントと CSV に残る", None),
    ("members.balance%TYPE、NUMBER", "Long / Integer / BigDecimal + Plsql.fit…", "桁あふれも Oracle と同じに", None),
    ("RAISE_APPLICATION_ERROR(-20103)", "MigratedException(-20103, …)", "コードは e.code() で見る", None),
    ("NO_DATA_FOUND / TOO_MANY_ROWS", "NoDataFoundException / TooManyRows…", "handler は catch になる", None),
    ("SYSDATE", "Plsql.sysdate()（アプリの時計）", "時刻の出所が変わる", "w"),
    ("SELECT … FOR UPDATE", "ロックなしの SELECT + commit で衝突", "人が決める（limits.yaml）", "r"),
    ("sequence.NEXTVAL", "Sequences（counters 表 + 再試行）", "人が決める（採番方式）", "r"),
    ("trigger", "Service に織り込み + TriggerChecks", "Service を通る書き込みだけ", "r"),
    ("routine の中の COMMIT", "1 反復 = 1 トランザクションの部品", "回すのは呼び出し側", "r"),
]
y = 30
for left, right, note, kind in rows:
    fill, stroke = {None: ("#FFFFFF", GREY), "w": (ORANGE_L, ORANGE), "r": (RED_L, RED)}[kind]
    a = p3.box(left, 0, y, 330, 29, fill=ORANGE_L if kind is None else fill, stroke=ORANGE if kind is None else stroke, fs=12, align="left")
    b = p3.box(right, 560, y, 360, 29, fill=BLUE_L if kind is None else fill, stroke=BLUE if kind is None else stroke, fs=12, align="left")
    p3.edge(a, b, color=stroke if kind else GREY, sw=1.5)
    p3.text(note, 336, y - 3, 218, 17, fs=11, color=stroke if kind else GREY)
    y += 34
p3.text("白 = そのまま写る　橙 = 出所が変わる　赤 = 意味が変わる。REDESIGN になり、人の決定が要る", 0, 408, 920, 20, fs=12, color=INK)

# ------------------------------------------------------------------------------------------------------
# 4. データ構造: 主キーが、パーティションキーとクラスタリングキーになる
# ------------------------------------------------------------------------------------------------------
p4 = Page("d", "4. データ構造 (Oracle の表 → ScalarDB の表)")
p4.group("Oracle", 0, 0, 300, 300, fill=ORANGE_L, stroke=ORANGE)
m = p4.box("<b>members</b><br>member_id NUMBER(10) <b>PK</b><br>name VARCHAR2(100)<br>rank VARCHAR2(10) DEFAULT 'REGULAR'<br>balance NUMBER(10)<br>updated_at DATE", 14, 30, 272, 118, stroke=ORANGE, fs=12, align="left")
h = p4.box("<b>point_history</b><br>PK (member_id, seq_no)<br>points NUMBER(10)、created_at DATE", 14, 160, 272, 64, stroke=ORANGE, fs=12, align="left")
x = p4.box("INDEX idx_members_rank (rank)<br>SEQUENCE member_seq", 14, 236, 272, 50, stroke=ORANGE, fs=12, align="left")

p4.group("ScalarDB（namespace points）", 380, 0, 540, 300, fill=BLUE_L, stroke=BLUE)
m2 = p4.box("<b>points.members</b><br><b>partition key</b>: member_id BIGINT<br>name TEXT、rank TEXT、balance BIGINT<br>updated_at TIMESTAMP<br><b>secondary index</b>: rank", 394, 30, 300, 118, fs=12, align="left")
h2 = p4.box("<b>points.point_history</b><br><b>partition key</b>: member_id　<b>clustering key</b>: seq_no ASC<br>points BIGINT、created_at TIMESTAMP", 394, 160, 512, 64, fs=12, align="left")
x2 = p4.box("索引名は落ちる（表 + 列で識別）。<b>sequence は無い</b> → アプリで採番", 394, 236, 512, 50, fill=RED_L, stroke=RED, fs=12, align="left")
p4.box("<b>変わること</b><br>DEFAULT は落ちる（アプリが入れる）<br>DATE は時刻を持つ → TIMESTAMP<br>NUMBER(p,s) → DOUBLE か、スケール済み BIGINT", 706, 30, 200, 118, fill=ORANGE_L, stroke=ORANGE, fs=11, align="left")
p4.edge(m, m2, color=BLUE); p4.edge(h, h2, color=BLUE); p4.edge(x, x2, color=RED)

p4.text("キーの設計が、SQL の動き方を決める（アクセスパス分析）", 0, 308, 920, 22, fs=13, color=INK, bold=True)
paths = [("WHERE member_id = 1", "GET（1 行）", GREEN, GREEN_L), ("member_id = 1 AND seq_no >= 10<br>ORDER BY seq_no DESC", "パーティション内の SCAN", GREEN, GREEN_L),
         ("WHERE rank = 'GOLD'", "索引で取得", GREEN, GREEN_L), ("GROUP BY rank（キーで絞れない）", "パーティションをまたぐ走査<br>RDBMS のバックエンド限定", RED, RED_L)]
for k, (sql, how, stroke, fill) in enumerate(paths):
    px = k * 232
    p4.box(f"<font face='Courier New'>{sql}</font>", px, 334, 224, 44, stroke=GREY, fs=11)
    p4.box(how, px, 382, 224, 46, fill=fill, stroke=stroke, fs=12, bold=True)


# ------------------------------------------------------------------------------------------------------
# 表紙と章扉の画像枠に入れる絵（マスターの画像枠: 表紙 3.05 x 2.03in、章扉 3.15 x 2.78in）
# ------------------------------------------------------------------------------------------------------
def vignette(ident, name, w, h):
    page = Page(ident, name, w, h)
    page.box("", 0, 0, w, h, fill="#FFFFFF", stroke="none", shape="rounded=1;arcSize=5;")
    return page

c0 = vignette("v", "cover", 305, 203)
a = c0.cyl("Oracle<br>SQL / PL/SQL", 14, 60, 92, 84, fill=ORANGE_L, stroke=ORANGE, fs=12)
b = c0.box("変換<br>判定<br>証拠", 124, 66, 58, 72, fill=BLUE, stroke=BLUE, fs=12, bold=True, color="#FFFFFF")
c = c0.cyl("ScalarDB<br>SQL + Java", 200, 60, 92, 84, fill=BLUE_L, stroke=BLUE, fs=12)
c0.edge(a, b, color=INK); c0.edge(b, c, color=INK)
c0.text("AUTO / REVIEW / REDESIGN", 0, 160, 305, 22, fs=13, color=BLUE, bold=True)
c0.text("SQL → OK / WARN / PLANNED / ERROR", 0, 22, 305, 22, fs=12, color=GREY, bold=True)

v1 = vignette("w", "section-1", 315, 278)
v1.text("移行先に無いもの", 0, 12, 315, 24, fs=15, color=RED, bold=True)
for k, label in enumerate(["SELECT … FOR UPDATE", "sequence.NEXTVAL", "trigger", "routine の中の COMMIT", "SET col = col + 1"]):
    v1.box(label, 28, 46 + k * 44, 200, 34, fill=ORANGE_L, stroke=ORANGE, fs=13, align="left")
    v1.box("?", 244, 46 + k * 44, 42, 34, fill=RED_L, stroke=RED, fs=16, bold=True, color=RED)

v2 = vignette("x", "section-2", 315, 278)
v2.text("3 つの柱", 0, 12, 315, 24, fs=15, color=BLUE, bold=True)
for k, (t, s) in enumerate([("SQL の変換", "OK / WARN / PLANNED / ERROR"), ("PL/SQL → Java", "AUTO は証拠でしか付かない"), ("移行の流れ", "3 つの承認 → テスト")]):
    v2.box(f"<b>{t}</b><br><font style='font-size:11px'>{s}</font>", 24, 48 + k * 74, 267, 62, fill=BLUE_L, stroke=BLUE, fs=14)

v3 = vignette("y", "section-3", 315, 278)
v3.text("層と向き", 0, 12, 315, 24, fs=15, color=BLUE, bold=True)
ids = []
for k, (t, fill, stroke) in enumerate([("呼び出し側（境界・再試行）", GREEN_L, GREEN), ("Service（生成）", BLUE_L, BLUE), ("Repository（生成）", BLUE_L, BLUE),
                                        ("ScalarDB Cluster", "#FFE6CC", ORANGE), ("バックエンド DB", GREY_L, GREY)]):
    ids.append(v3.box(t, 40, 44 + k * 46, 235, 32, fill=fill, stroke=stroke, fs=13, bold=True))
for s, t in zip(ids, ids[1:]):
    v3.edge(s, t, color=INK, sw=1.5)

v4 = vignette("z", "section-4", 315, 278)
v4.box("", 18, 20, 279, 150, fill="#1F2933", stroke="#1F2933", shape="rounded=1;arcSize=6;")
v4.text("<font face='Courier New' color='#9CDCFE'>&gt; /plugin install</font><br><font face='Courier New' color='#E8ECF1'>&nbsp;&nbsp;sql-migration</font><br><br>"
        "<font face='Courier New' color='#7DBA7D'>「この PL/SQL を</font><br><font face='Courier New' color='#7DBA7D'>&nbsp;ScalarDB に移行して」</font>", 28, 28, 262, 136, fs=13, align="left")
for k, t in enumerate(["migrate-flow", "plsql-spec", "plsql-migrate", "sql-transpile"]):
    v4.box(t, 18 + (k % 2) * 142, 184 + (k // 2) * 44, 137, 34, fill=BLUE_L, stroke=BLUE, fs=13, bold=True)

v5 = vignette("u", "section-5", 315, 278)
v5.text("ポイントカード", 0, 12, 315, 24, fs=15, color=BLUE, bold=True)
t1 = v5.cyl("members", 28, 48, 116, 70, fill=ORANGE_L, stroke=ORANGE, fs=13)
t2 = v5.cyl("point_history", 171, 48, 116, 70, fill=ORANGE_L, stroke=ORANGE, fs=13)
v5.box("<b>pkg_points</b><br>get_balance / add_points / use_points", 28, 132, 259, 50, fill=BLUE_L, stroke=BLUE, fs=12)
v5.box("<b>SQL<br>9 / 10 一致</b>", 28, 196, 124, 60, fill=GREEN_L, stroke=GREEN, fs=13)
v5.box("<b>PL/SQL<br>13 / 13 一致</b>", 163, 196, 124, 60, fill=GREEN_L, stroke=GREEN, fs=13)

pages = [p1, p2, p3, p4, c0, v1, v2, v3, v4, v5]
OUT.write_text('<mxfile host="app.diagrams.net">\n' + "".join(p.xml() for p in pages) + "</mxfile>\n", encoding="utf-8")
print(f"{len(pages)} pages -> {OUT}")
