-- =============================================================================
-- 01_exploration_queries.sql
-- -----------------------------------------------------------------------------
-- Purpose : First-look queries for profiling the raw / silver data before any
--           modelling decisions are made.
-- Dialect : ANSI SQL - runs unchanged on PostgreSQL and Spark SQL.
-- Tables  : customers, products, orders
--
-- How to run
--   PostgreSQL : load the CSVs from data/raw/ into tables of the same name.
--   Spark SQL  : register the silver Parquet folders as temporary views, e.g.
--                spark.read.parquet("data/silver/orders").createOrReplaceTempView("orders");
-- =============================================================================


-- -----------------------------------------------------------------------------
-- 1. Row counts - always start by knowing how much data you have.
-- -----------------------------------------------------------------------------
SELECT 'customers' AS table_name, COUNT(*) AS row_count FROM customers
UNION ALL
SELECT 'products'  AS table_name, COUNT(*) AS row_count FROM products
UNION ALL
SELECT 'orders'    AS table_name, COUNT(*) AS row_count FROM orders;


-- -----------------------------------------------------------------------------
-- 2. Full sample of each table (small dataset, so a full scan is cheap).
-- -----------------------------------------------------------------------------
SELECT *
FROM customers
ORDER BY customer_id;

SELECT *
FROM products
ORDER BY product_id;

SELECT *
FROM orders
ORDER BY order_id, updated_at;


-- -----------------------------------------------------------------------------
-- 3. Column profiling - distinct values and NULL counts for orders.
--    A quick way to spot missing data and unexpected cardinality.
-- -----------------------------------------------------------------------------
SELECT
    COUNT(*)                                             AS total_rows,
    COUNT(DISTINCT order_id)                             AS distinct_order_ids,
    COUNT(DISTINCT customer_id)                          AS distinct_customers,
    COUNT(DISTINCT product_id)                           AS distinct_products,
    SUM(CASE WHEN amount   IS NULL THEN 1 ELSE 0 END)    AS null_amounts,
    SUM(CASE WHEN quantity IS NULL THEN 1 ELSE 0 END)    AS null_quantities,
    SUM(CASE WHEN status   IS NULL THEN 1 ELSE 0 END)    AS null_statuses
FROM orders;


-- -----------------------------------------------------------------------------
-- 4. Categorical domains - what values actually occur?
--    Reveals casing variants and unexpected categories before standardisation.
-- -----------------------------------------------------------------------------
SELECT region, COUNT(*) AS customer_count
FROM customers
GROUP BY region
ORDER BY customer_count DESC, region;

SELECT category, COUNT(*) AS product_count
FROM products
GROUP BY category
ORDER BY product_count DESC, category;

SELECT status, COUNT(*) AS order_count
FROM orders
GROUP BY status
ORDER BY order_count DESC, status;


-- -----------------------------------------------------------------------------
-- 5. WHERE clause examples - filtering orders.
-- -----------------------------------------------------------------------------

-- 5a. Completed orders only.
SELECT order_id, customer_id, product_id, order_date, amount
FROM orders
WHERE status = 'Completed'
ORDER BY order_date;

-- 5b. Orders placed in February 2026, using a half-open range so the query can
--     use an index and is not affected by any time component.
SELECT order_id, order_date, amount, status
FROM orders
WHERE order_date >= DATE '2026-02-01'
  AND order_date <  DATE '2026-03-01'
ORDER BY order_date;

-- 5c. Orders missing a monetary amount.
SELECT order_id, customer_id, order_date, status
FROM orders
WHERE amount IS NULL;

-- 5d. High-value completed orders.
SELECT order_id, customer_id, amount
FROM orders
WHERE status = 'Completed'
  AND amount >= 100.00
ORDER BY amount DESC;


-- -----------------------------------------------------------------------------
-- 6. GROUP BY examples - early aggregate views.
-- -----------------------------------------------------------------------------

-- 6a. Order count and value per customer (completed only).
SELECT
    customer_id,
    COUNT(*)            AS order_count,
    SUM(amount)         AS total_amount,
    MIN(order_date)     AS first_order_date,
    MAX(order_date)     AS last_order_date
FROM orders
WHERE status = 'Completed'
GROUP BY customer_id
ORDER BY total_amount DESC;

-- 6b. Value per product.
SELECT
    product_id,
    COUNT(*)            AS order_count,
    SUM(quantity)       AS units_sold,
    SUM(amount)         AS total_amount
FROM orders
WHERE status = 'Completed'
GROUP BY product_id
ORDER BY total_amount DESC;

-- 6c. Orders per month. DATE_TRUNC is supported by both PostgreSQL and Spark SQL.
SELECT
    DATE_TRUNC('month', order_date) AS order_month,
    COUNT(*)                        AS order_count,
    SUM(amount)                     AS total_amount
FROM orders
GROUP BY DATE_TRUNC('month', order_date)
ORDER BY order_month;

-- 6d. GROUP BY with HAVING - customers with more than one completed order.
SELECT
    customer_id,
    COUNT(*) AS completed_orders
FROM orders
WHERE status = 'Completed'
GROUP BY customer_id
HAVING COUNT(*) > 1
ORDER BY completed_orders DESC;


-- -----------------------------------------------------------------------------
-- 7. Joining the three raw tables - the denormalised view analysts ask for first.
--    LEFT JOIN on customers deliberately exposes orphaned orders (customer 99)
--    instead of silently hiding them as an INNER JOIN would.
-- -----------------------------------------------------------------------------
SELECT
    o.order_id,
    o.order_date,
    o.status,
    o.quantity,
    o.amount,
    c.customer_name,
    c.region,
    p.product_name,
    p.category,
    p.unit_price
FROM orders o
LEFT JOIN customers c ON o.customer_id = c.customer_id
LEFT JOIN products  p ON o.product_id  = p.product_id
ORDER BY o.order_id;


-- -----------------------------------------------------------------------------
-- 8. Reconciliation - does amount agree with quantity * unit_price?
--    A classic measure-integrity probe.
-- -----------------------------------------------------------------------------
SELECT
    o.order_id,
    o.quantity,
    p.unit_price,
    o.quantity * p.unit_price AS expected_amount,
    o.amount                  AS actual_amount,
    o.amount - (o.quantity * p.unit_price) AS variance
FROM orders o
JOIN products p ON o.product_id = p.product_id
WHERE o.amount IS NULL
   OR o.amount <> o.quantity * p.unit_price
ORDER BY o.order_id;
