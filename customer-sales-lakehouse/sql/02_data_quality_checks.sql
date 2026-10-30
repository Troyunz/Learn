-- =============================================================================
-- 02_data_quality_checks.sql
-- -----------------------------------------------------------------------------
-- Purpose : SQL equivalents of the four checks implemented in
--           src/quality_checks.py. Useful for ad-hoc investigation and for
--           verifying that the PySpark implementation agrees with plain SQL.
-- Dialect : ANSI SQL - runs unchanged on PostgreSQL and Spark SQL.
-- Rules   : documented in docs/data_quality_rules.md
--
-- Each check comes in two forms:
--   (a) a DETAIL query listing the offending rows, and
--   (b) a COUNT query returning the failed_count used by the framework.
-- =============================================================================


-- =============================================================================
-- CHECK 1 : duplicate_order_id
-- Rule    : order_id must be unique.
-- Expected: 1 failure on raw data (order_id 107); 0 on silver.
-- =============================================================================

-- 1a. DETAIL - which keys are duplicated, and how many times?
SELECT
    order_id,
    COUNT(*) AS row_count
FROM orders
GROUP BY order_id
HAVING COUNT(*) > 1
ORDER BY row_count DESC, order_id;

-- 1b. COUNT - number of duplicated keys (not excess rows).
SELECT
    'duplicate_order_id' AS check_name,
    CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END AS status,
    COUNT(*) AS failed_count
FROM (
    SELECT order_id
    FROM orders
    GROUP BY order_id
    HAVING COUNT(*) > 1
) AS duplicated_keys;

-- 1c. DETAIL - the full duplicate rows, newest first. ROW_NUMBER() mirrors the
--     de-duplication logic in src/transformations.deduplicate_by_key().
SELECT *
FROM (
    SELECT
        o.*,
        ROW_NUMBER() OVER (
            PARTITION BY o.order_id
            ORDER BY o.updated_at DESC
        ) AS version_rank
    FROM orders o
) AS ranked
WHERE order_id IN (
    SELECT order_id FROM orders GROUP BY order_id HAVING COUNT(*) > 1
)
ORDER BY order_id, version_rank;


-- =============================================================================
-- CHECK 2 : missing_order_amount
-- Rule    : amount must not be NULL.
-- Expected: 1 failure (order_id 105).
-- =============================================================================

-- 2a. DETAIL - orders with no monetary amount.
SELECT
    order_id,
    customer_id,
    product_id,
    order_date,
    quantity,
    status
FROM orders
WHERE amount IS NULL
ORDER BY order_id;

-- 2b. COUNT.
SELECT
    'missing_order_amount' AS check_name,
    CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END AS status,
    COUNT(*) AS failed_count
FROM orders
WHERE amount IS NULL;


-- =============================================================================
-- CHECK 3 : invalid_customer_reference
-- Rule    : every orders.customer_id must exist in customers.customer_id.
-- Expected: 1 failure (order_id 106 -> customer_id 99).
-- =============================================================================

-- 3a. DETAIL - orphaned orders via an anti-join pattern.
SELECT
    o.order_id,
    o.customer_id AS orphaned_customer_id,
    o.order_date,
    o.amount,
    o.status
FROM orders o
LEFT JOIN customers c
       ON o.customer_id = c.customer_id
WHERE c.customer_id IS NULL
ORDER BY o.order_id;

-- 3b. COUNT.
SELECT
    'invalid_customer_reference' AS check_name,
    CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END AS status,
    COUNT(*) AS failed_count
FROM orders o
LEFT JOIN customers c
       ON o.customer_id = c.customer_id
WHERE c.customer_id IS NULL;

-- 3c. Same check expressed with NOT EXISTS. Prefer this over NOT IN, which
--     returns no rows at all if the subquery contains a single NULL.
SELECT
    'invalid_customer_reference' AS check_name,
    CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END AS status,
    COUNT(*) AS failed_count
FROM orders o
WHERE NOT EXISTS (
    SELECT 1
    FROM customers c
    WHERE c.customer_id = o.customer_id
);


-- =============================================================================
-- CHECK 4 : invalid_status
-- Rule    : status must be one of 'Completed', 'Cancelled', 'Pending'.
-- Expected: 0 failures after silver standardisation.
-- Note    : NULL is treated as a failure - an unknown lifecycle state cannot be
--           classified as revenue or not.
-- =============================================================================

-- 4a. DETAIL - rows with an out-of-domain or NULL status.
SELECT
    order_id,
    status,
    order_date
FROM orders
WHERE status IS NULL
   OR status NOT IN ('Completed', 'Cancelled', 'Pending')
ORDER BY order_id;

-- 4b. COUNT.
SELECT
    'invalid_status' AS check_name,
    CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END AS status,
    COUNT(*) AS failed_count
FROM orders
WHERE status IS NULL
   OR status NOT IN ('Completed', 'Cancelled', 'Pending');

-- 4c. Case-sensitivity probe - run this against RAW data to catch casing
--     variants such as 'completed' that 4b would already flag, but which are
--     fixable by standardisation rather than by rejecting the row.
SELECT
    status                  AS raw_status,
    UPPER(status)           AS normalised_status,
    COUNT(*)                AS row_count
FROM orders
GROUP BY status, UPPER(status)
ORDER BY normalised_status, raw_status;


-- =============================================================================
-- FULL SUITE : all four checks as a single result set.
-- This mirrors the output written to data/quality_results/ by
-- notebooks/07_data_quality_checks.py.
-- =============================================================================
SELECT 'duplicate_order_id' AS check_name,
       CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END AS status,
       COUNT(*) AS failed_count
FROM (
    SELECT order_id FROM orders GROUP BY order_id HAVING COUNT(*) > 1
) AS d

UNION ALL

SELECT 'missing_order_amount',
       CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END,
       COUNT(*)
FROM orders
WHERE amount IS NULL

UNION ALL

SELECT 'invalid_customer_reference',
       CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END,
       COUNT(*)
FROM orders o
WHERE NOT EXISTS (
    SELECT 1 FROM customers c WHERE c.customer_id = o.customer_id
)

UNION ALL

SELECT 'invalid_status',
       CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END,
       COUNT(*)
FROM orders
WHERE status IS NULL
   OR status NOT IN ('Completed', 'Cancelled', 'Pending');
