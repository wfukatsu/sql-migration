-- Fixed cases of the area sales analysis (difftest/golden/area-sales/query.sql), from the Verify.java investigation.
-- Deterministic: no random rows; the zero-divide case (ORA-01476) is covered by AreaSalesReportTest instead.
--   golden.py capture --setup this --query query.sql --tables organization_master,sales_transactions --out .
DROP TABLE organization_master PURGE;
DROP TABLE sales_transactions PURGE;

CREATE TABLE organization_master (
    node_id   NUMBER(18) PRIMARY KEY,
    parent_id NUMBER(18),
    node_name VARCHAR2(200)
);

CREATE TABLE sales_transactions (
    shop_id    NUMBER(18),
    sales_date DATE,
    order_id   NUMBER(18),
    amount     NUMBER(18),
    PRIMARY KEY (shop_id, sales_date, order_id)
);

-- two roots (本部, 第2本部); an empty area (閉鎖エリア); a level-4 node (5); an orphan whose parent does not exist (999)
-- shop names that sort differently by code point vs UTF-16: half-width ｱ (U+FF71) and 𠮷 (U+20BB7, surrogate pair)
INSERT INTO organization_master VALUES (1, NULL, '本部');
INSERT INTO organization_master VALUES (2, 1, '関東');
INSERT INTO organization_master VALUES (3, 1, '関西');
INSERT INTO organization_master VALUES (4, 1, '閉鎖エリア');
INSERT INTO organization_master VALUES (10, 2, '新宿店');
INSERT INTO organization_master VALUES (11, 2, '渋谷店');
INSERT INTO organization_master VALUES (12, 2, 'ｱ店');
INSERT INTO organization_master VALUES (13, 2, '𠮷野家前店');
INSERT INTO organization_master VALUES (20, 3, '梅田店');
INSERT INTO organization_master VALUES (21, 3, '難波店');
INSERT INTO organization_master VALUES (5, 10, '新宿店直営コーナー');
INSERT INTO organization_master VALUES (100, NULL, '第2本部');
INSERT INTO organization_master VALUES (101, 100, '九州');
INSERT INTO organization_master VALUES (110, 101, '博多店');
INSERT INTO organization_master VALUES (999, 998, '孤立店');

-- shop 10: gap month (no 2026-03, so LAG of 2026-04 is 2026-02); date boundaries 2025-12-31 23:59:59 (out),
--          2026-12-31 23:59:59 (in), 2027-01-01 00:00:00 (out)
INSERT INTO sales_transactions VALUES (10, TIMESTAMP '2026-01-15 10:00:00', 1, 1000);
INSERT INTO sales_transactions VALUES (10, TIMESTAMP '2026-02-10 10:00:00', 2, 1200);
INSERT INTO sales_transactions VALUES (10, TIMESTAMP '2026-04-01 10:00:00', 3, 900);
INSERT INTO sales_transactions VALUES (10, TIMESTAMP '2025-12-31 23:59:59', 4, 5000);
INSERT INTO sales_transactions VALUES (10, TIMESTAMP '2026-12-31 23:59:59', 5, 700);
INSERT INTO sales_transactions VALUES (10, TIMESTAMP '2027-01-01 00:00:00', 6, 800);
-- shop 11: all-NULL month (2026-02); ties with shop 10 in 2026-01 (1000)
INSERT INTO sales_transactions VALUES (11, TIMESTAMP '2026-01-20 10:00:00', 7, 1000);
INSERT INTO sales_transactions VALUES (11, TIMESTAMP '2026-02-10 11:00:00', 8, NULL);
INSERT INTO sales_transactions VALUES (11, TIMESTAMP '2026-03-05 10:00:00', 9, 300);
-- shop 12: first month all NULL (ties with shop 11 in 2026-02 as NULL, ranked first under DESC)
INSERT INTO sales_transactions VALUES (12, TIMESTAMP '2026-02-01 00:00:00', 10, NULL);
INSERT INTO sales_transactions VALUES (12, TIMESTAMP '2026-03-01 00:00:00', 11, 500);
INSERT INTO sales_transactions VALUES (13, TIMESTAMP '2026-01-01 00:00:00', 12, 10);
-- shops 20 / 21: rounding (3.125 -> 3.13, 1/3 -> 0, -2.5 -> -3) and negative amounts
INSERT INTO sales_transactions VALUES (20, TIMESTAMP '2026-01-10 00:00:00', 13, 32);
INSERT INTO sales_transactions VALUES (20, TIMESTAMP '2026-02-10 00:00:00', 14, 1);
INSERT INTO sales_transactions VALUES (20, TIMESTAMP '2026-03-10 00:00:00', 15, -32);
INSERT INTO sales_transactions VALUES (21, TIMESTAMP '2026-01-10 00:00:00', 16, 5);
INSERT INTO sales_transactions VALUES (21, TIMESTAMP '2026-02-10 00:00:00', 17, -10);
-- sales of a level-2 area, a level-4 node and the orphan: excluded by hierarchy_level = 3
INSERT INTO sales_transactions VALUES (2, TIMESTAMP '2026-01-01 00:00:00', 18, 99999);
INSERT INTO sales_transactions VALUES (5, TIMESTAMP '2026-01-01 00:00:00', 19, 99999);
INSERT INTO sales_transactions VALUES (999, TIMESTAMP '2026-01-01 00:00:00', 20, 99999);
-- shop 110 (second root): month boundary 06-30 23:00 / 07-01 00:00
INSERT INTO sales_transactions VALUES (110, TIMESTAMP '2026-06-30 23:00:00', 21, 100);
INSERT INTO sales_transactions VALUES (110, TIMESTAMP '2026-07-01 00:00:00', 22, 200);
