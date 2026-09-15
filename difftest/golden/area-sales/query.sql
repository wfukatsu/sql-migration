-- 1. エリアの階層構造（ツリー）を定義
WITH area_hierarchy AS (
    SELECT
        node_id AS shop_or_area_id,
        parent_id,
        node_name,
        -- SYS_CONNECT_BY_PATH で組織のルートからの経路を生成
        SYS_CONNECT_BY_PATH(node_name, ' > ') AS area_path,
        LEVEL AS hierarchy_level
    FROM
        organization_master
    START WITH parent_id IS NULL  -- ルート（本部）から開始
    CONNECT BY PRIOR node_id = parent_id
),

-- 2. 店舗別・月別の売上を集計（インラインビューでの事前集約による高速化）
monthly_sales AS (
    SELECT
        shop_id,
        TO_CHAR(sales_date, 'YYYY-MM') AS sales_month,
        SUM(amount) AS total_amount,
        COUNT(order_id) AS total_orders
    FROM
        sales_transactions
    WHERE
        sales_date >= DATE '2026-01-01'
        AND sales_date < DATE '2027-01-01'
    GROUP BY
        shop_id,
        TO_CHAR(sales_date, 'YYYY-MM')
)

-- 3. メイン処理：階層情報と売上集計を結合し、各種分析関数を適用
SELECT
    h.area_path,
    h.node_name AS shop_name,
    s.sales_month,
    s.total_amount,

    -- ① 前月比の算出 (LAG関数で1行前の売上を取得)
    ROUND(
        (s.total_amount / LAG(s.total_amount, 1) OVER (
            PARTITION BY s.shop_id
            ORDER BY s.sales_month
        )) * 100, 2
    ) AS mom_ratio_pct,

    -- ② 過去3ヶ月の移動平均 (ROWS BETWEEN で現在の行と前の2行を指定)
    ROUND(
        AVG(s.total_amount) OVER (
            PARTITION BY s.shop_id
            ORDER BY s.sales_month
            ROWS BETWEEN 2 PRECEDING AND CURRENT ROW
        ), 0
    ) AS moving_avg_3m,

    -- ③ 同一月内、エリア(親組織)別での売上ランキング (DENSE_RANK関数)
    DENSE_RANK() OVER (
        PARTITION BY h.parent_id, s.sales_month
        ORDER BY s.total_amount DESC
    ) AS rank_in_area

FROM
    area_hierarchy h
-- 組織ツリーのうち「店舗（最下層レベル3と仮定）」のみに売上データを結合
INNER JOIN
    monthly_sales s ON h.shop_or_area_id = s.shop_id
WHERE
    h.hierarchy_level = 3
ORDER BY
    h.area_path,
    s.sales_month;
