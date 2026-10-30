-- =============================================================================
-- 04_star_schema_ddl.sql
-- -----------------------------------------------------------------------------
-- Purpose : DDL for the gold-layer star schema.
-- Dialect : ANSI SQL - portable across PostgreSQL and Spark SQL.
--
-- Portability note
--   PostgreSQL enforces PRIMARY KEY / FOREIGN KEY constraints. Spark SQL parses
--   them but does NOT enforce them, so the relationships below are additionally
--   documented in comments and are enforced in code by
--   notebooks/06_gold_fact_sales.py, which uses INNER joins to the dimensions.
--
--   For Spark SQL, replace each `CREATE TABLE ...` with
--       CREATE TABLE ... USING PARQUET LOCATION 'data/gold/<table>'
--   and drop the constraint clauses.
--
-- Model   : one fact surrounded by three conformed dimensions.
--
--         dim_customer                 dim_product
--       (customer_key PK)            (product_key PK)
--               |                           |
--               +-----------+   +-----------+
--                           v   v
--                      +-------------+
--                      | fact_sales  |
--                      +------^------+
--                             |
--                         dim_date
--                       (date_key PK)
-- =============================================================================


DROP TABLE IF EXISTS fact_sales;
DROP TABLE IF EXISTS dim_customer;
DROP TABLE IF EXISTS dim_product;
DROP TABLE IF EXISTS dim_date;


-- =============================================================================
-- DIMENSION : dim_customer
-- Grain     : one row per customer.
-- PK        : customer_key (surrogate)
-- NK        : customer_id  (natural / business key from the source system)
-- Source    : data/silver/customers/
-- SCD       : Type 1 (overwrite). A Type 2 build would add valid_from,
--             valid_to and is_current columns, and the PK would become
--             (customer_key) with a unique constraint on (customer_id, valid_from).
-- =============================================================================
CREATE TABLE dim_customer (
    customer_key            INTEGER        NOT NULL,   -- PRIMARY KEY (surrogate)
    customer_id             INTEGER        NOT NULL,   -- natural key, unique
    customer_name           VARCHAR(200)   NOT NULL,
    region                  VARCHAR(50)    NOT NULL,   -- North / South / East / West
    customer_created_date   DATE           NOT NULL,

    CONSTRAINT pk_dim_customer       PRIMARY KEY (customer_key),
    CONSTRAINT uq_dim_customer_natural_key UNIQUE (customer_id)
);


-- =============================================================================
-- DIMENSION : dim_product
-- Grain     : one row per product.
-- PK        : product_key (surrogate)
-- NK        : product_id  (natural / business key)
-- Source    : data/silver/products/
-- Note      : unit_price is DECIMAL, never FLOAT - money must not be subject to
--             binary floating-point rounding error.
-- =============================================================================
CREATE TABLE dim_product (
    product_key             INTEGER        NOT NULL,   -- PRIMARY KEY (surrogate)
    product_id              INTEGER        NOT NULL,   -- natural key, unique
    product_name            VARCHAR(200)   NOT NULL,
    category                VARCHAR(100)   NOT NULL,   -- Accessories / Hardware
    unit_price              DECIMAL(10, 2) NOT NULL,

    CONSTRAINT pk_dim_product        PRIMARY KEY (product_key),
    CONSTRAINT uq_dim_product_natural_key UNIQUE (product_id),
    CONSTRAINT ck_dim_product_price  CHECK (unit_price >= 0)
);


-- =============================================================================
-- DIMENSION : dim_date
-- Grain     : one row per calendar day.
-- PK        : date_key (smart key, yyyyMMdd as an INTEGER)
-- Source    : generated in notebooks/05_gold_dimensions.py
-- Note      : the calendar is contiguous (no gaps), so a month with zero sales
--             still appears in a time series.
-- =============================================================================
CREATE TABLE dim_date (
    date_key                INTEGER        NOT NULL,   -- PRIMARY KEY, e.g. 20260105
    full_date               DATE           NOT NULL,
    year                    INTEGER        NOT NULL,
    quarter                 INTEGER        NOT NULL,   -- 1-4
    month                   INTEGER        NOT NULL,   -- 1-12
    month_name              VARCHAR(20)    NOT NULL,
    day                     INTEGER        NOT NULL,   -- 1-31
    day_of_week             INTEGER        NOT NULL,   -- 1 = Sunday ... 7 = Saturday
    day_name                VARCHAR(20)    NOT NULL,
    is_weekend              BOOLEAN        NOT NULL,

    CONSTRAINT pk_dim_date           PRIMARY KEY (date_key),
    CONSTRAINT uq_dim_date_full_date UNIQUE (full_date),
    CONSTRAINT ck_dim_date_quarter   CHECK (quarter BETWEEN 1 AND 4),
    CONSTRAINT ck_dim_date_month     CHECK (month   BETWEEN 1 AND 12)
);


-- =============================================================================
-- FACT : fact_sales
-- Grain  : one row per completed, quality-valid order.
-- PK     : order_id (the natural key is unique after silver de-duplication)
-- FKs    : customer_key -> dim_customer.customer_key
--          product_key  -> dim_product.product_key
--          date_key     -> dim_date.date_key
-- Source : data/silver/orders/ filtered to
--              data_quality_status = 'Valid' AND status = 'Completed'
--
-- Measures (both fully additive across every dimension):
--     quantity - units sold
--     amount   - recognised revenue
--
-- order_id is a DEGENERATE DIMENSION: a transaction identifier that carries no
-- attributes of its own, so it lives on the fact rather than in a dimension.
--
-- order_status is retained for traceability. It is always 'Completed' by
-- construction, which the CHECK constraint below enforces.
-- =============================================================================
CREATE TABLE fact_sales (
    order_id                INTEGER        NOT NULL,   -- PRIMARY KEY (degenerate dimension)
    customer_key            INTEGER        NOT NULL,   -- FOREIGN KEY -> dim_customer
    product_key             INTEGER        NOT NULL,   -- FOREIGN KEY -> dim_product
    date_key                INTEGER        NOT NULL,   -- FOREIGN KEY -> dim_date
    quantity                INTEGER        NOT NULL,   -- additive measure
    amount                  DECIMAL(10, 2) NOT NULL,   -- additive measure
    order_status            VARCHAR(20)    NOT NULL,   -- always 'Completed'

    CONSTRAINT pk_fact_sales           PRIMARY KEY (order_id),

    CONSTRAINT fk_fact_sales_customer  FOREIGN KEY (customer_key)
        REFERENCES dim_customer (customer_key),

    CONSTRAINT fk_fact_sales_product   FOREIGN KEY (product_key)
        REFERENCES dim_product (product_key),

    CONSTRAINT fk_fact_sales_date      FOREIGN KEY (date_key)
        REFERENCES dim_date (date_key),

    CONSTRAINT ck_fact_sales_quantity  CHECK (quantity > 0),
    CONSTRAINT ck_fact_sales_amount    CHECK (amount >= 0),
    CONSTRAINT ck_fact_sales_status    CHECK (order_status = 'Completed')
);


-- =============================================================================
-- INDEXES
-- Foreign-key columns on a fact table are the hot path for every star join, so
-- each one gets its own index. PostgreSQL does not create these automatically.
-- (Spark SQL ignores CREATE INDEX; partitioning or Z-ordering is used instead.)
-- =============================================================================
CREATE INDEX ix_fact_sales_customer_key ON fact_sales (customer_key);
CREATE INDEX ix_fact_sales_product_key  ON fact_sales (product_key);
CREATE INDEX ix_fact_sales_date_key     ON fact_sales (date_key);
