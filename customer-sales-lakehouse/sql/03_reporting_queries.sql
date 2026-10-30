-- =============================================================================
-- 03_reporting_queries.sql
-- -----------------------------------------------------------------------------
-- Purpose : Business reporting against the gold star schema.
-- Dialect : ANSI SQL - runs unchanged on PostgreSQL and Spark SQL.
-- Tables  : fact_sales, dim_customer, dim_product, dim_date
--
-- fact_sales contains only orders where
--     data_quality_status = 'Valid' AND status = 'Completed'
-- so every figure below is already *recognised revenue*. No status filtering is
-- needed in these queries - that rule lives in the model, not in the report.
--
-- Joins to dimensions are INNER joins because the fact table is guaranteed
-- referentially complete by notebooks/06_gold_fact_sales.py.
-- =============================================================================


-- =============================================================================
-- 1. MONTHLY RECOGNISED REVENUE
-- =============================================================================

-- 1a. Revenue by calendar month.
SELECT
    d.year,
    d.month,
    d.month_name,
    COUNT(*)            AS order_count,
    SUM(f.quantity)     AS units_sold,
    SUM(f.amount)       AS recognised_revenue
FROM fact_sales f
JOIN dim_date d ON f.date_key = d.date_key
GROUP BY d.year, d.month, d.month_name
ORDER BY d.year, d.month;

-- 1b. Monthly revenue with a month-over-month delta and growth percentage.
--     LAG() reads the previous row in the ordered window.
SELECT
    year,
    month,
    month_name,
    recognised_revenue,
    LAG(recognised_revenue) OVER (ORDER BY year, month) AS previous_month_revenue,
    recognised_revenue
        - LAG(recognised_revenue) OVER (ORDER BY year, month) AS revenue_change,
    CASE
        WHEN LAG(recognised_revenue) OVER (ORDER BY year, month) IS NULL
             OR LAG(recognised_revenue) OVER (ORDER BY year, month) = 0
        THEN NULL
        ELSE ROUND(
            100.0 * (recognised_revenue
                     - LAG(recognised_revenue) OVER (ORDER BY year, month))
            / LAG(recognised_revenue) OVER (ORDER BY year, month),
            2
        )
    END AS revenue_growth_pct
FROM (
    SELECT
        d.year,
        d.month,
        d.month_name,
        SUM(f.amount) AS recognised_revenue
    FROM fact_sales f
    JOIN dim_date d ON f.date_key = d.date_key
    GROUP BY d.year, d.month, d.month_name
) AS monthly
ORDER BY year, month;

-- 1c. Running (cumulative) revenue by month.
SELECT
    year,
    month,
    month_name,
    recognised_revenue,
    SUM(recognised_revenue) OVER (
        ORDER BY year, month
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS cumulative_revenue
FROM (
    SELECT
        d.year,
        d.month,
        d.month_name,
        SUM(f.amount) AS recognised_revenue
    FROM fact_sales f
    JOIN dim_date d ON f.date_key = d.date_key
    GROUP BY d.year, d.month, d.month_name
) AS monthly
ORDER BY year, month;


-- =============================================================================
-- 2. REVENUE BY REGION
-- =============================================================================

-- 2a. Total revenue and customer reach per region.
SELECT
    c.region,
    COUNT(*)                        AS order_count,
    COUNT(DISTINCT c.customer_key)  AS active_customers,
    SUM(f.quantity)                 AS units_sold,
    SUM(f.amount)                   AS recognised_revenue,
    ROUND(AVG(f.amount), 2)         AS average_order_value
FROM fact_sales f
JOIN dim_customer c ON f.customer_key = c.customer_key
GROUP BY c.region
ORDER BY recognised_revenue DESC;

-- 2b. Each region's share of total revenue, using a window total as the
--     denominator so no self-join or subquery is required.
SELECT
    region,
    recognised_revenue,
    SUM(recognised_revenue) OVER () AS total_revenue,
    ROUND(
        100.0 * recognised_revenue / SUM(recognised_revenue) OVER (),
        2
    ) AS revenue_share_pct
FROM (
    SELECT
        c.region,
        SUM(f.amount) AS recognised_revenue
    FROM fact_sales f
    JOIN dim_customer c ON f.customer_key = c.customer_key
    GROUP BY c.region
) AS by_region
ORDER BY recognised_revenue DESC;

-- 2c. Region by month - the classic two-dimensional revenue grid.
SELECT
    c.region,
    d.year,
    d.month,
    d.month_name,
    SUM(f.amount) AS recognised_revenue
FROM fact_sales f
JOIN dim_customer c ON f.customer_key = c.customer_key
JOIN dim_date     d ON f.date_key     = d.date_key
GROUP BY c.region, d.year, d.month, d.month_name
ORDER BY c.region, d.year, d.month;


-- =============================================================================
-- 3. TOP PRODUCTS BY REVENUE
-- =============================================================================

-- 3a. Product league table.
SELECT
    p.product_id,
    p.product_name,
    p.category,
    p.unit_price,
    COUNT(*)                AS order_count,
    SUM(f.quantity)         AS units_sold,
    SUM(f.amount)           AS recognised_revenue
FROM fact_sales f
JOIN dim_product p ON f.product_key = p.product_key
GROUP BY p.product_id, p.product_name, p.category, p.unit_price
ORDER BY recognised_revenue DESC;

-- 3b. Top 3 products overall. FETCH FIRST ... ROWS ONLY is the ANSI form and is
--     accepted by both PostgreSQL and Spark SQL (both also accept LIMIT).
SELECT
    p.product_name,
    p.category,
    SUM(f.amount) AS recognised_revenue
FROM fact_sales f
JOIN dim_product p ON f.product_key = p.product_key
GROUP BY p.product_name, p.category
ORDER BY recognised_revenue DESC
FETCH FIRST 3 ROWS ONLY;

-- 3c. Top product *within each category*, using a ranked subquery.
SELECT
    category,
    product_name,
    recognised_revenue,
    category_rank
FROM (
    SELECT
        p.category,
        p.product_name,
        SUM(f.amount) AS recognised_revenue,
        RANK() OVER (
            PARTITION BY p.category
            ORDER BY SUM(f.amount) DESC
        ) AS category_rank
    FROM fact_sales f
    JOIN dim_product p ON f.product_key = p.product_key
    GROUP BY p.category, p.product_name
) AS ranked
WHERE category_rank = 1
ORDER BY recognised_revenue DESC;

-- 3d. Revenue by category.
SELECT
    p.category,
    COUNT(*)        AS order_count,
    SUM(f.quantity) AS units_sold,
    SUM(f.amount)   AS recognised_revenue
FROM fact_sales f
JOIN dim_product p ON f.product_key = p.product_key
GROUP BY p.category
ORDER BY recognised_revenue DESC;


-- =============================================================================
-- 4. CUSTOMER VIEWS
-- =============================================================================

-- 4a. Customer league table with first/last purchase dates.
SELECT
    c.customer_id,
    c.customer_name,
    c.region,
    COUNT(*)            AS order_count,
    SUM(f.amount)       AS lifetime_revenue,
    MIN(d.full_date)    AS first_purchase_date,
    MAX(d.full_date)    AS last_purchase_date
FROM fact_sales f
JOIN dim_customer c ON f.customer_key = c.customer_key
JOIN dim_date     d ON f.date_key     = d.date_key
GROUP BY c.customer_id, c.customer_name, c.region
ORDER BY lifetime_revenue DESC;

-- 4b. Customers with NO recognised revenue. A LEFT JOIN from the dimension is
--     required here - an INNER JOIN would hide exactly the rows of interest.
SELECT
    c.customer_id,
    c.customer_name,
    c.region
FROM dim_customer c
LEFT JOIN fact_sales f ON c.customer_key = f.customer_key
WHERE f.customer_key IS NULL
ORDER BY c.customer_id;


-- =============================================================================
-- 5. HEADLINE SUMMARY - the single row a dashboard tile would display.
-- =============================================================================
SELECT
    COUNT(*)                            AS total_orders,
    COUNT(DISTINCT f.customer_key)      AS active_customers,
    COUNT(DISTINCT f.product_key)       AS products_sold,
    SUM(f.quantity)                     AS total_units,
    SUM(f.amount)                       AS recognised_revenue,
    ROUND(AVG(f.amount), 2)             AS average_order_value
FROM fact_sales f;
