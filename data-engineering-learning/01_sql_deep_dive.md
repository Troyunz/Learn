# 01 — SQL Deep Dive

> **Dataset:** every example in this guide runs against the three fictional tables of the companion project **`../customer-sales-lakehouse`** — `customers`, `products`, `orders`.
>
> **Promise:** each concept gets (a) an explanation of *why it exists*, (b) a runnable query, and (c) the exact expected result table. Verify them yourself — predicting the answer before running is the single highest-value SQL habit.

---

## Table of contents

1. [Setup: the sample data](#1-setup-the-sample-data)
2. [The relational model: tables, rows, columns](#2-the-relational-model-tables-rows-columns)
3. [Keys: primary, foreign, natural, surrogate](#3-keys-primary-foreign-natural-surrogate)
4. [Grain — the most important word in this guide](#4-grain--the-most-important-word-in-this-guide)
5. [SELECT and projection](#5-select-and-projection)
6. [WHERE — filtering rows](#6-where--filtering-rows)
7. [ORDER BY and LIMIT](#7-order-by-and-limit)
8. [GROUP BY and aggregate functions](#8-group-by-and-aggregate-functions)
9. [HAVING](#9-having)
10. [NULL handling](#10-null-handling)
11. [COALESCE, NULLIF and friends](#11-coalesce-nullif-and-friends)
12. [CASE WHEN](#12-case-when)
13. [JOINs — every type](#13-joins--every-type)
14. [Set operations](#14-set-operations)
15. [Subqueries](#15-subqueries)
16. [Common Table Expressions (CTEs)](#16-common-table-expressions-ctes)
17. [Window functions](#17-window-functions)
18. [Putting it together: deriving 525.00](#18-putting-it-together-deriving-52500)
19. [Data quality in SQL](#19-data-quality-in-sql)
20. [Source-to-target reconciliation](#20-source-to-target-reconciliation)
21. [Query-writing method and performance notes](#21-query-writing-method-and-performance-notes)
22. [Practice exercises](#22-practice-exercises)

---

## 1. Setup: the sample data

### DDL

```sql
CREATE TABLE customers (
    customer_id            INTEGER      NOT NULL,
    customer_name          VARCHAR(100) NOT NULL,
    region                 VARCHAR(20)  NOT NULL,
    customer_created_date  DATE         NOT NULL,
    CONSTRAINT pk_customers PRIMARY KEY (customer_id)
);

CREATE TABLE products (
    product_id    INTEGER       NOT NULL,
    product_name  VARCHAR(100)  NOT NULL,
    category      VARCHAR(50)   NOT NULL,
    unit_price    DECIMAL(10,2) NOT NULL,
    CONSTRAINT pk_products PRIMARY KEY (product_id)
);

-- NOTE: `orders` is the RAW landing table. It deliberately has NO primary key
-- and NO foreign keys, because raw data is dirty and we must be able to land it
-- before we validate it. Constraints are enforced later, in the silver layer.
CREATE TABLE orders (
    order_id     INTEGER       NOT NULL,
    customer_id  INTEGER,
    product_id   INTEGER,
    order_date   DATE,
    quantity     INTEGER,
    amount       DECIMAL(10,2),
    status       VARCHAR(20),
    updated_at   TIMESTAMP
);
```

### Data

```sql
INSERT INTO customers (customer_id, customer_name, region, customer_created_date) VALUES
    (1, 'Aisha Khan',  'North', DATE '2025-10-01'),
    (2, 'Ben Smith',   'South', DATE '2025-11-12'),
    (3, 'Carla Jones', 'North', DATE '2025-12-07'),
    (4, 'David Lee',   'West',  DATE '2026-01-15');

INSERT INTO products (product_id, product_name, category, unit_price) VALUES
    (1001, 'Laptop Bag',     'Accessories',  50.00),
    (1002, 'Wireless Mouse', 'Accessories',  25.00),
    (1003, 'Monitor',        'Hardware',    200.00),
    (1004, 'Keyboard',       'Accessories',  75.00);

INSERT INTO orders (order_id, customer_id, product_id, order_date, quantity, amount, status, updated_at) VALUES
    (101,  1, 1003, DATE '2026-01-05', 1, 200.00, 'Completed', TIMESTAMP '2026-01-05 10:00:00'),
    (102,  1, 1002, DATE '2026-01-10', 2,  50.00, 'Completed', TIMESTAMP '2026-01-10 11:00:00'),
    (103,  2, 1001, DATE '2026-01-12', 1,  50.00, 'Cancelled', TIMESTAMP '2026-01-12 09:30:00'),
    (104,  3, 1004, DATE '2026-02-01', 1,  75.00, 'Completed', TIMESTAMP '2026-02-01 14:00:00'),
    (105,  3, 1002, DATE '2026-02-05', 1,   NULL, 'Pending',   TIMESTAMP '2026-02-05 15:00:00'),
    (106, 99, 1001, DATE '2026-02-10', 1,  50.00, 'Completed', TIMESTAMP '2026-02-10 12:00:00'),
    (107,  1, 1003, DATE '2026-02-15', 1, 200.00, 'Completed', TIMESTAMP '2026-02-15 08:00:00'),
    (107,  1, 1003, DATE '2026-02-15', 1, 200.00, 'Completed', TIMESTAMP '2026-02-15 08:00:00');
```

### The planted defects (know them by heart)

| # | Defect | Evidence | Count |
|---|---|---|---|
| 1 | Duplicate `order_id` | `107` twice, **identical** `updated_at` | exactly 1 |
| 2 | Missing mandatory `amount` | order `105` | exactly 1 |
| 3 | Invalid customer reference | order `106` → `customer_id = 99` | exactly 1 |
| 4 | Invalid product reference | — | **0** |
| 5 | Invalid `status` | valid set = `Completed`, `Cancelled`, `Pending` | **0** |

### The canonical business rule

> `fact_sales` contains **only valid, de-duplicated, `Completed`** orders.
> Recognised revenue = **525.00** = 200.00 (101) + 50.00 (102) + 75.00 (104) + 200.00 (107).

**How this maps to the project:** these three tables are the contents of `../customer-sales-lakehouse/data/raw/`. The queries in this guide become the files in `../customer-sales-lakehouse/sql/` and the assertions in `src/quality_checks.py`.

---

## 2. The relational model: tables, rows, columns

A **relation** (table) is a set of rows (tuples) with a fixed set of named, typed columns (attributes). Three ideas matter more than the vocabulary:

1. **Rows are unordered.** There is no "first row" unless you `ORDER BY`. Any query that relies on insertion order is a bug waiting to happen.
2. **Columns are typed.** `amount DECIMAL(10,2)` is not the string `'200.00'`. Type errors become silent wrong answers (`'10' > '9'` is *false* as text).
3. **A relation is logically a set.** Duplicates like our two `107` rows are therefore *not* a relational concept — they are a data-quality defect that SQL tables happen to permit.

### Why relational still wins for analytics

| Property | Benefit |
|---|---|
| Declarative | You say *what* you want; the engine decides *how* |
| Composable | Every query output is itself a table, so queries nest and chain |
| Set-based | Operates on millions of rows per statement, not row-by-row loops |
| Constraint-aware | Keys and types let the database (or your pipeline) reject nonsense |

```sql
SELECT * FROM customers;
```

| customer_id | customer_name | region | customer_created_date |
|---|---|---|---|
| 1 | Aisha Khan | North | 2025-10-01 |
| 2 | Ben Smith | South | 2025-11-12 |
| 3 | Carla Jones | North | 2025-12-07 |
| 4 | David Lee | West | 2026-01-15 |

```sql
SELECT * FROM products;
```

| product_id | product_name | category | unit_price |
|---|---|---|---:|
| 1001 | Laptop Bag | Accessories | 50.00 |
| 1002 | Wireless Mouse | Accessories | 25.00 |
| 1003 | Monitor | Hardware | 200.00 |
| 1004 | Keyboard | Accessories | 75.00 |

```sql
SELECT * FROM orders ORDER BY order_id, updated_at;
```

| order_id | customer_id | product_id | order_date | quantity | amount | status | updated_at |
|---|---|---|---|---:|---:|---|---|
| 101 | 1 | 1003 | 2026-01-05 | 1 | 200.00 | Completed | 2026-01-05 10:00:00 |
| 102 | 1 | 1002 | 2026-01-10 | 2 | 50.00 | Completed | 2026-01-10 11:00:00 |
| 103 | 2 | 1001 | 2026-01-12 | 1 | 50.00 | Cancelled | 2026-01-12 09:30:00 |
| 104 | 3 | 1004 | 2026-02-01 | 1 | 75.00 | Completed | 2026-02-01 14:00:00 |
| 105 | 3 | 1002 | 2026-02-05 | 1 | *(null)* | Pending | 2026-02-05 15:00:00 |
| 106 | 99 | 1001 | 2026-02-10 | 1 | 50.00 | Completed | 2026-02-10 12:00:00 |
| 107 | 1 | 1003 | 2026-02-15 | 1 | 200.00 | Completed | 2026-02-15 08:00:00 |
| 107 | 1 | 1003 | 2026-02-15 | 1 | 200.00 | Completed | 2026-02-15 08:00:00 |

**8 rows.** Remember that number — most reconciliation starts there.

---

## 3. Keys: primary, foreign, natural, surrogate

### Primary key (PK)

A column (or set of columns) that is **unique and never NULL**, identifying exactly one row.

- `customers.customer_id` → PK
- `products.product_id` → PK
- `orders.order_id` → *intended* PK, but **violated** in raw data by the duplicate `107`.

```sql
-- Does order_id actually behave like a primary key in raw?
SELECT
    COUNT(*)                 AS total_rows,
    COUNT(DISTINCT order_id) AS distinct_order_ids,
    COUNT(*) - COUNT(DISTINCT order_id) AS surplus_rows
FROM orders;
```

| total_rows | distinct_order_ids | surplus_rows |
|---:|---:|---:|
| 8 | 7 | 1 |

A non-zero `surplus_rows` is the simplest possible uniqueness test, and it belongs in `src/quality_checks.py`.

### Foreign key (FK)

A column whose values must exist as a primary key in another table.

- `orders.customer_id` → `customers.customer_id` — **violated** by order 106 (`99`).
- `orders.product_id` → `products.product_id` — satisfied by all rows.

### Natural key vs surrogate key

| | Natural key | Surrogate key |
|---|---|---|
| Definition | A real business identifier (`customer_id = 1`, an email, an ISBN) | A meaningless system-generated id (`customer_sk = 1, 2, 3…`) |
| Comes from | The source system | Your warehouse |
| Stability | Can be reused, corrected, or change format | Never changes |
| Needed for | Matching to source, de-duplication | SCD Type 2, decoupling from source changes |

In the gold layer we keep **both**: `dim_customer.customer_sk` (surrogate, used by `fact_sales`) and `dim_customer.customer_id` (natural, used to match incoming data). Guide `02` explains why SCD Type 2 makes this mandatory.

### Composite keys

Some tables need more than one column to be unique — e.g. a hypothetical `order_lines` table keyed on `(order_id, line_number)`. Our `orders` table is already one row per order, so a single-column key suffices.

---

## 4. Grain — the most important word in this guide

> **Grain = what exactly one row of this table represents, stated in one sentence.**

| Table | Grain |
|---|---|
| `customers` | one row per customer |
| `products` | one row per product |
| `orders` (intended) | one row per order |
| `orders` (actual, raw) | one row per order **version received**, hence the duplicate 107 |
| `fact_sales` | one row per valid, de-duplicated, completed order |

### Why grain is everything

Almost every "the numbers don't match" incident is a grain mistake:

- Joining a one-row-per-order table to a one-row-per-order-line table **multiplies** your amounts.
- Aggregating a table whose grain you assumed wrongly **double-counts**.
- Our duplicate `107` inflates raw revenue by exactly 200.00 — a grain violation, not a maths error.

```sql
-- The cost of ignoring grain:
SELECT SUM(amount) AS raw_sum FROM orders;
```

| raw_sum |
|---:|
| 825.00 |

`825.00` is the sum of a table whose grain is broken. After de-duplication it is `625.00`; after validity rules and the `Completed` filter it is `525.00`. Same data, three numbers, one correct answer for the question *"what revenue did we recognise?"*.

**Rule:** write the grain in a comment at the top of every table definition and every transformation. If you cannot state it, you are not ready to write the query.

---

## 5. SELECT and projection

`SELECT` chooses **columns** (projection). `WHERE` chooses **rows** (selection).

```sql
SELECT customer_id, customer_name, region
FROM customers;
```

| customer_id | customer_name | region |
|---|---|---|
| 1 | Aisha Khan | North |
| 2 | Ben Smith | South |
| 3 | Carla Jones | North |
| 4 | David Lee | West |

### Expressions, aliases and derived columns

```sql
SELECT
    order_id,
    quantity,
    amount,
    quantity * 1.0            AS units,
    amount / NULLIF(quantity, 0) AS implied_unit_price
FROM orders
WHERE order_id IN (101, 102, 105)
ORDER BY order_id;
```

| order_id | quantity | amount | units | implied_unit_price |
|---|---:|---:|---:|---:|
| 101 | 1 | 200.00 | 1.0 | 200.00 |
| 102 | 2 | 50.00 | 2.0 | 25.00 |
| 105 | 1 | *(null)* | 1.0 | *(null)* |

Two lessons here:
- Order 102 has `quantity = 2` and `amount = 50.00`, so `amount` is an **order-level total**, not a unit price (2 × 25.00 = 50.00 ✔ matches `products.unit_price` for 1002).
- Any arithmetic involving `NULL` yields `NULL` — see section 10.

### `SELECT DISTINCT`

```sql
SELECT DISTINCT status FROM orders ORDER BY status;
```

| status |
|---|
| Cancelled |
| Completed |
| Pending |

Exactly the three valid values — confirming defect #5 count is **0**.

### Never ship `SELECT *`

| Problem | Consequence in a pipeline |
|---|---|
| Schema drift | A new source column silently appears downstream |
| Column reorder | Positional consumers break |
| Width | Reads and shuffles far more data than needed |
| Readability | A reviewer cannot tell what the query depends on |

Explicit column lists are a contract. In `src/transformations.py`, always `select()` the columns you mean.

### Logical order of evaluation

SQL is *written* in one order and *evaluated* in another. Memorise the evaluation order — it explains most beginner errors:

```
FROM / JOIN  →  WHERE  →  GROUP BY  →  HAVING  →  SELECT  →  DISTINCT  →  ORDER BY  →  LIMIT
```

Consequences:
- You cannot use a `SELECT` alias in `WHERE` (the alias does not exist yet) — but you usually *can* in `ORDER BY`.
- `WHERE` filters **before** aggregation; `HAVING` filters **after**.

---

## 6. WHERE — filtering rows

```sql
SELECT order_id, order_date, amount, status
FROM orders
WHERE status = 'Completed'
ORDER BY order_id, updated_at;
```

| order_id | order_date | amount | status |
|---|---|---:|---|
| 101 | 2026-01-05 | 200.00 | Completed |
| 102 | 2026-01-10 | 50.00 | Completed |
| 104 | 2026-02-01 | 75.00 | Completed |
| 106 | 2026-02-10 | 50.00 | Completed |
| 107 | 2026-02-15 | 200.00 | Completed |
| 107 | 2026-02-15 | 200.00 | Completed |

**6 rows** — note that the duplicate and the orphan (106) are still present. Filtering on `status` alone is *not* the business rule.

### Comparison and logical operators

| Operator | Example | Meaning |
|---|---|---|
| `=`, `<>` | `status <> 'Cancelled'` | equality / inequality |
| `<`, `<=`, `>`, `>=` | `amount >= 75` | ordering |
| `BETWEEN` | `order_date BETWEEN DATE '2026-01-01' AND DATE '2026-01-31'` | inclusive range |
| `IN` | `status IN ('Completed','Pending')` | membership |
| `LIKE` | `customer_name LIKE 'A%'` | pattern (`%` any, `_` one) |
| `IS NULL` / `IS NOT NULL` | `amount IS NULL` | null test (never use `= NULL`) |
| `AND`, `OR`, `NOT` | `status = 'Completed' AND amount > 100` | combination |

### Date range filtering

```sql
SELECT order_id, order_date, amount, status
FROM orders
WHERE order_date >= DATE '2026-02-01'
  AND order_date <  DATE '2026-03-01'
ORDER BY order_id, updated_at;
```

| order_id | order_date | amount | status |
|---|---|---:|---|
| 104 | 2026-02-01 | 75.00 | Completed |
| 105 | 2026-02-05 | *(null)* | Pending |
| 106 | 2026-02-10 | 50.00 | Completed |
| 107 | 2026-02-15 | 200.00 | Completed |
| 107 | 2026-02-15 | 200.00 | Completed |

**5 rows.** Prefer the half-open interval `>= start AND < next_start` over `BETWEEN` for dates: it is immune to time components (a `2026-02-28 13:00` timestamp is *not* `<= DATE '2026-02-28'`).

### `IN` with a list

```sql
SELECT order_id, status, amount
FROM orders
WHERE status IN ('Cancelled', 'Pending')
ORDER BY order_id;
```

| order_id | status | amount |
|---|---|---:|
| 103 | Cancelled | 50.00 |
| 105 | Pending | *(null)* |

### Pattern matching

```sql
SELECT customer_id, customer_name, region
FROM customers
WHERE customer_name LIKE '%a%'
ORDER BY customer_id;
```

| customer_id | customer_name | region |
|---|---|---|
| 1 | Aisha Khan | North |
| 3 | Carla Jones | North |
| 4 | David Lee | West |

(`Ben Smith` contains no lower-case `a`. `LIKE` is case-sensitive in most engines; `Aisha` matches on the `a` in `Aisha`… careful: `Aisha` → `A`,`i`,`s`,`h`,`a` — yes, the final `a` matches.)

### `IS NULL`

```sql
SELECT order_id, customer_id, product_id, amount, status
FROM orders
WHERE amount IS NULL;
```

| order_id | customer_id | product_id | amount | status |
|---|---|---|---:|---|
| 105 | 3 | 1002 | *(null)* | Pending |

Exactly one row — defect #2 confirmed.

---

## 7. ORDER BY and LIMIT

```sql
SELECT order_id, amount, status
FROM orders
ORDER BY amount DESC, order_id ASC;
```

| order_id | amount | status |
|---|---:|---|
| 107 | 200.00 | Completed |
| 107 | 200.00 | Completed |
| 101 | 200.00 | Completed |
| 104 | 75.00 | Completed |
| 102 | 50.00 | Completed |
| 103 | 50.00 | Cancelled |
| 106 | 50.00 | Completed |
| 105 | *(null)* | Pending |

> ⚠️ **NULL ordering is engine-specific.** PostgreSQL treats `NULL` as *largest* (so `DESC` puts it first); SQL Server, MySQL and SQLite treat it as *smallest* (so `DESC` puts it last, as shown). Spark SQL also puts nulls last for `DESC`. Be explicit when it matters:

```sql
SELECT order_id, amount
FROM orders
ORDER BY amount DESC NULLS LAST, order_id;
```

### Deterministic ordering

`ORDER BY amount DESC` alone leaves the three 50.00 rows in an arbitrary order. **Always add a tiebreaker** (here `order_id`) when the output feeds a report, a test, or a `ROW_NUMBER()`. Non-deterministic output makes tests flaky.

### LIMIT / TOP / FETCH

```sql
-- PostgreSQL, MySQL, SQLite, Spark SQL
SELECT order_id, amount FROM orders ORDER BY amount DESC NULLS LAST, order_id LIMIT 3;

-- SQL Server
-- SELECT TOP (3) order_id, amount FROM orders ORDER BY amount DESC, order_id;

-- ANSI standard
-- SELECT order_id, amount FROM orders ORDER BY amount DESC, order_id FETCH FIRST 3 ROWS ONLY;
```

| order_id | amount |
|---|---:|
| 101 | 200.00 |
| 107 | 200.00 |
| 107 | 200.00 |

(With the tiebreaker `order_id` ascending, 101 comes before the two 107s.)

---

## 8. GROUP BY and aggregate functions

`GROUP BY` collapses many rows into one row per group. **It changes the grain of the result.**

### The core aggregates

| Function | Ignores NULL? | Notes |
|---|---|---|
| `COUNT(*)` | no | counts rows |
| `COUNT(col)` | **yes** | counts non-null values |
| `COUNT(DISTINCT col)` | yes | counts distinct non-null values |
| `SUM(col)` | yes | `NULL` if *all* inputs are null |
| `AVG(col)` | yes | denominator = count of non-nulls |
| `MIN` / `MAX` | yes | works on dates and text too |

### Group by status

```sql
SELECT
    status,
    COUNT(*)      AS order_rows,
    COUNT(amount) AS rows_with_amount,
    SUM(amount)   AS total_amount,
    MIN(amount)   AS min_amount,
    MAX(amount)   AS max_amount
FROM orders
GROUP BY status
ORDER BY status;
```

| status | order_rows | rows_with_amount | total_amount | min_amount | max_amount |
|---|---:|---:|---:|---:|---:|
| Cancelled | 1 | 1 | 50.00 | 50.00 | 50.00 |
| Completed | 6 | 6 | 775.00 | 50.00 | 200.00 |
| Pending | 1 | 0 | *(null)* | *(null)* | *(null)* |

Read this carefully — it contains three teaching points:

1. `Completed` has **6** rows (including the duplicate 107 and the orphan 106) summing to **775.00** = 200 + 50 + 75 + 50 + 200 + 200.
2. `Pending` has one row but `rows_with_amount = 0`, so `SUM` returns `NULL`, **not** `0`.
3. The grand total 50.00 + 775.00 + NULL is *not* 825.00 unless you handle the null — see section 11.

### Group by customer (raw grain)

```sql
SELECT
    customer_id,
    COUNT(*)                 AS order_rows,
    COUNT(DISTINCT order_id) AS distinct_orders,
    SUM(amount)              AS total_amount
FROM orders
GROUP BY customer_id
ORDER BY customer_id;
```

| customer_id | order_rows | distinct_orders | total_amount |
|---:|---:|---:|---:|
| 1 | 4 | 3 | 650.00 |
| 2 | 1 | 1 | 50.00 |
| 3 | 2 | 2 | 75.00 |
| 99 | 1 | 1 | 50.00 |

Checks: customer 1 → 200 + 50 + 200 + 200 = **650.00** across 4 rows but only 3 distinct orders (the duplicate). Customer 3 → 75.00 + NULL = **75.00** across 2 rows. Grand total 650 + 50 + 75 + 50 = **825.00** ✔. And `customer_id = 99` appearing here is defect #3 staring at you.

### Multi-column grouping

```sql
SELECT
    customer_id,
    status,
    COUNT(*)    AS order_rows,
    SUM(amount) AS total_amount
FROM orders
GROUP BY customer_id, status
ORDER BY customer_id, status;
```

| customer_id | status | order_rows | total_amount |
|---:|---|---:|---:|
| 1 | Completed | 4 | 650.00 |
| 2 | Cancelled | 1 | 50.00 |
| 3 | Completed | 1 | 75.00 |
| 3 | Pending | 1 | *(null)* |
| 99 | Completed | 1 | 50.00 |

### Grouping by a derived expression

```sql
-- Monthly totals from the raw table (still contains duplicates/orphans)
SELECT
    CAST(DATE_TRUNC('month', order_date) AS DATE) AS order_month,
    COUNT(*)    AS order_rows,
    SUM(amount) AS total_amount
FROM orders
GROUP BY CAST(DATE_TRUNC('month', order_date) AS DATE)
ORDER BY order_month;
```

| order_month | order_rows | total_amount |
|---|---:|---:|
| 2026-01-01 | 3 | 300.00 |
| 2026-02-01 | 5 | 525.00 |

January: 200 + 50 + 50 = **300.00** (orders 101, 102, 103).
February: 75 + NULL + 50 + 200 + 200 = **525.00** (orders 104, 105, 106, 107, 107).

> ⚠️ That February `525.00` is a **coincidence** — it is *not* recognised revenue. It happens to equal the business total because the duplicate 200.00 and the orphan 50.00 cancel against January's excluded rows. This is exactly how wrong numbers sneak into dashboards: the figure looks familiar, so nobody checks. Always derive 525.00 from the *rule*, never from a lucky aggregate.

`DATE_TRUNC` is PostgreSQL/Spark syntax. Portable alternatives: `FORMAT(order_date,'yyyy-MM')`, `EXTRACT(YEAR ...)`/`EXTRACT(MONTH ...)`, or — best practice — join to a `dim_date` table (guide `02`).

---

## 9. HAVING

`WHERE` filters rows **before** grouping. `HAVING` filters groups **after** aggregation.

```sql
-- Which customer_ids appear on more than one order row?
SELECT
    customer_id,
    COUNT(*) AS order_rows
FROM orders
GROUP BY customer_id
HAVING COUNT(*) > 1
ORDER BY customer_id;
```

| customer_id | order_rows |
|---:|---:|
| 1 | 4 |
| 3 | 2 |

### Combining WHERE and HAVING

```sql
SELECT
    customer_id,
    COUNT(*)    AS completed_rows,
    SUM(amount) AS completed_amount
FROM orders
WHERE status = 'Completed'          -- row filter, applied first
GROUP BY customer_id
HAVING SUM(amount) > 100            -- group filter, applied after
ORDER BY customer_id;
```

| customer_id | completed_rows | completed_amount |
|---:|---:|---:|
| 1 | 4 | 650.00 |

Customer 3 (75.00) and customer 99 (50.00) fail the `HAVING`; customer 2 was removed by `WHERE` before grouping ever happened.

### The classic duplicate-detection idiom

```sql
SELECT
    order_id,
    COUNT(*) AS row_count
FROM orders
GROUP BY order_id
HAVING COUNT(*) > 1
ORDER BY order_id;
```

| order_id | row_count |
|---:|---:|
| 107 | 2 |

One row, exactly as specified by defect #1. Memorise this pattern — it is the single most-used data-quality query in the industry.

### `WHERE` vs `HAVING` cheat-sheet

| Question | Use |
|---|---|
| "Only February orders" | `WHERE` |
| "Only customers whose total exceeds 100" | `HAVING` |
| "Only Completed orders, for customers with >2 orders" | `WHERE status='Completed'` **and** `HAVING COUNT(*)>2` |
| "Faster" | `WHERE` — it reduces rows before the aggregation work |

---

## 10. NULL handling

`NULL` means **unknown**, not zero and not empty string. It follows *three-valued logic*: TRUE, FALSE, UNKNOWN.

### The rules

| Expression | Result | Why |
|---|---|---|
| `NULL = NULL` | UNKNOWN | two unknowns are not known to be equal |
| `NULL <> 5` | UNKNOWN | unknown compared to anything is unknown |
| `NULL + 100` | `NULL` | arithmetic propagates unknowns |
| `'abc' \|\| NULL` | `NULL` | so does concatenation (in most engines) |
| `NULL IS NULL` | TRUE | the only reliable null test |
| `WHERE <UNKNOWN>` | row excluded | `WHERE` keeps only TRUE |
| `SUM(col)` | ignores nulls | `NULL` only if every input is null |
| `COUNT(col)` | ignores nulls | `COUNT(*)` does not |
| `GROUP BY col` | nulls form **one** group | grouping treats nulls as equal |
| `x IN (1, NULL)` when `x = 2` | UNKNOWN, not FALSE | the classic `NOT IN` trap |

### Demonstration on order 105

```sql
SELECT
    order_id,
    amount,
    amount + 100        AS amount_plus_100,
    amount * quantity   AS amount_times_qty,
    CASE WHEN amount = NULL     THEN 'equals-null'  ELSE 'not-equals-null' END AS wrong_test,
    CASE WHEN amount IS NULL    THEN 'is-null'      ELSE 'is-not-null'     END AS right_test
FROM orders
WHERE order_id IN (104, 105)
ORDER BY order_id;
```

| order_id | amount | amount_plus_100 | amount_times_qty | wrong_test | right_test |
|---|---:|---:|---:|---|---|
| 104 | 75.00 | 175.00 | 75.00 | not-equals-null | is-not-null |
| 105 | *(null)* | *(null)* | *(null)* | not-equals-null | is-null |

Note that `wrong_test` returns `'not-equals-null'` even for the null row, because `amount = NULL` is UNKNOWN, which is not TRUE, so the `ELSE` branch wins. **This is how null bugs hide.**

### The `NOT IN` trap

```sql
-- DANGEROUS: if the subquery returns any NULL, this returns ZERO rows.
SELECT order_id
FROM orders
WHERE customer_id NOT IN (SELECT customer_id FROM customers);
```

`customers.customer_id` is `NOT NULL`, so here it happens to work and returns:

| order_id |
|---:|
| 106 |

But if a single `NULL` existed in that subquery, the result would be **empty** — and you would conclude there are no orphans. **Always prefer `NOT EXISTS` or a `LEFT JOIN ... IS NULL` anti-join** (section 13).

### Aggregates and nulls side by side

```sql
SELECT
    COUNT(*)                       AS rows_total,
    COUNT(amount)                  AS rows_with_amount,
    COUNT(*) - COUNT(amount)       AS rows_missing_amount,
    SUM(amount)                    AS sum_amount,
    AVG(amount)                    AS avg_amount_null_ignored,
    SUM(amount) / COUNT(*)         AS avg_amount_null_as_divisor
FROM orders;
```

| rows_total | rows_with_amount | rows_missing_amount | sum_amount | avg_amount_null_ignored | avg_amount_null_as_divisor |
|---:|---:|---:|---:|---:|---:|
| 8 | 7 | 1 | 825.00 | 117.857142… | 103.125 |

`AVG` divides 825.00 by **7** (non-null count) = 117.857142…, whereas dividing by 8 gives 103.125. Neither is "wrong" — but you must know which one your stakeholder means. Spell it out in the column name.

---

## 11. COALESCE, NULLIF and friends

### `COALESCE(a, b, c, …)`

Returns the first non-null argument.

```sql
SELECT
    o.order_id,
    o.quantity,
    o.amount                                        AS amount_raw,
    COALESCE(o.amount, 0)                           AS amount_zero_filled,
    COALESCE(o.amount, o.quantity * p.unit_price)   AS amount_derived,
    CASE WHEN o.amount IS NULL THEN 'derived' ELSE 'source' END AS amount_source
FROM orders o
JOIN products p ON p.product_id = o.product_id
WHERE o.order_id IN (104, 105)
ORDER BY o.order_id;
```

| order_id | quantity | amount_raw | amount_zero_filled | amount_derived | amount_source |
|---|---:|---:|---:|---:|---|
| 104 | 1 | 75.00 | 75.00 | 75.00 | source |
| 105 | 1 | *(null)* | 0.00 | 25.00 | derived |

Order 105 is product 1002 (`Wireless Mouse`, `unit_price = 25.00`) with `quantity = 1`, so the derived amount is **25.00**.

> ⚠️ **Does this change recognised revenue?** No. Order 105 has `status = 'Pending'`, so it is excluded from `fact_sales` regardless of how you fill its amount. Recognised revenue stays **525.00**. Deriving a value is a *repair* decision; whether the row qualifies as revenue is a *business-rule* decision. Keep them separate.

### `COALESCE` for safe totals

```sql
SELECT
    status,
    SUM(amount)              AS sum_raw,
    COALESCE(SUM(amount), 0) AS sum_safe      -- null→0 AFTER aggregation
FROM orders
GROUP BY status
ORDER BY status;
```

| status | sum_raw | sum_safe |
|---|---:|---:|
| Cancelled | 50.00 | 50.00 |
| Completed | 775.00 | 775.00 |
| Pending | *(null)* | 0.00 |

Use `COALESCE(SUM(x), 0)` when a null total would break a downstream calculation or chart. Use raw `NULL` when "we don't know" is genuinely more honest than "zero".

### `NULLIF(a, b)`

Returns `NULL` if `a = b`, else `a`. The canonical use is guarding division:

```sql
SELECT
    order_id,
    amount,
    quantity,
    amount / NULLIF(quantity, 0) AS unit_price_implied
FROM orders
WHERE order_id IN (102, 103)
ORDER BY order_id;
```

| order_id | amount | quantity | unit_price_implied |
|---|---:|---:|---:|
| 102 | 50.00 | 2 | 25.00 |
| 103 | 50.00 | 1 | 50.00 |

If `quantity` were ever `0`, `NULLIF` converts it to `NULL`, yielding `NULL` instead of a divide-by-zero error.

### Related functions

| Function | Behaviour | Portability |
|---|---|---|
| `COALESCE(a,b,…)` | first non-null | ANSI, everywhere |
| `NULLIF(a,b)` | null when equal | ANSI, everywhere |
| `ISNULL(a,b)` | two-argument coalesce | SQL Server only |
| `IFNULL(a,b)` | two-argument coalesce | MySQL, SQLite, Spark |
| `NVL(a,b)` | two-argument coalesce | Oracle, Spark |

Prefer `COALESCE` — it is standard and takes any number of arguments.

---

## 12. CASE WHEN

`CASE` is SQL's if/else. It evaluates branches top-to-bottom and returns the first match.

### Searched CASE: order size bands

```sql
SELECT
    order_id,
    amount,
    CASE
        WHEN amount IS NULL  THEN 'Unknown'
        WHEN amount >= 200   THEN 'Large'
        WHEN amount >= 75    THEN 'Medium'
        ELSE                      'Small'
    END AS size_band
FROM orders
ORDER BY order_id, updated_at;
```

| order_id | amount | size_band |
|---|---:|---|
| 101 | 200.00 | Large |
| 102 | 50.00 | Small |
| 103 | 50.00 | Small |
| 104 | 75.00 | Medium |
| 105 | *(null)* | Unknown |
| 106 | 50.00 | Small |
| 107 | 200.00 | Large |
| 107 | 200.00 | Large |

> ⚠️ **Branch order matters.** The `IS NULL` test must come first. If it came last, `amount >= 200` would evaluate to UNKNOWN for order 105, fall through every branch, and land in `ELSE 'Small'` — silently mislabelling an unknown amount as a small order.

Aggregated:

```sql
SELECT
    CASE
        WHEN amount IS NULL THEN 'Unknown'
        WHEN amount >= 200  THEN 'Large'
        WHEN amount >= 75   THEN 'Medium'
        ELSE                     'Small'
    END AS size_band,
    COUNT(*) AS order_rows
FROM orders
GROUP BY
    CASE
        WHEN amount IS NULL THEN 'Unknown'
        WHEN amount >= 200  THEN 'Large'
        WHEN amount >= 75   THEN 'Medium'
        ELSE                     'Small'
    END
ORDER BY order_rows DESC, size_band;
```

| size_band | order_rows |
|---|---:|
| Large | 3 |
| Small | 3 |
| Medium | 1 |
| Unknown | 1 |

3 + 3 + 1 + 1 = **8** ✔

### Conditional aggregation (pivot)

This is one of the highest-value SQL patterns: turn rows into columns with `CASE` inside an aggregate.

```sql
SELECT
    customer_id,
    COUNT(*)                                                      AS order_rows,
    SUM(CASE WHEN status = 'Completed' THEN 1 ELSE 0 END)         AS completed_rows,
    SUM(CASE WHEN status = 'Cancelled' THEN 1 ELSE 0 END)         AS cancelled_rows,
    SUM(CASE WHEN status = 'Pending'   THEN 1 ELSE 0 END)         AS pending_rows,
    COALESCE(SUM(CASE WHEN status = 'Completed' THEN amount END), 0) AS completed_amount
FROM orders
GROUP BY customer_id
ORDER BY customer_id;
```

| customer_id | order_rows | completed_rows | cancelled_rows | pending_rows | completed_amount |
|---:|---:|---:|---:|---:|---:|
| 1 | 4 | 4 | 0 | 0 | 650.00 |
| 2 | 1 | 0 | 1 | 0 | 0.00 |
| 3 | 2 | 1 | 0 | 1 | 75.00 |
| 99 | 1 | 1 | 0 | 0 | 50.00 |

(Still raw grain: customer 1's 650.00 includes the duplicate 107.)

### CASE as a data-quality classifier

```sql
SELECT
    o.order_id,
    CASE
        WHEN c.customer_id IS NULL                   THEN 'INVALID_CUSTOMER'
        WHEN p.product_id  IS NULL                   THEN 'INVALID_PRODUCT'
        WHEN o.amount IS NULL                        THEN 'MISSING_AMOUNT'
        WHEN o.status NOT IN ('Completed','Cancelled','Pending') THEN 'INVALID_STATUS'
        ELSE 'VALID'
    END AS quality_flag
FROM orders o
LEFT JOIN customers c ON c.customer_id = o.customer_id
LEFT JOIN products  p ON p.product_id  = o.product_id
ORDER BY o.order_id, o.updated_at;
```

| order_id | quality_flag |
|---|---|
| 101 | VALID |
| 102 | VALID |
| 103 | VALID |
| 104 | VALID |
| 105 | MISSING_AMOUNT |
| 106 | INVALID_CUSTOMER |
| 107 | VALID |
| 107 | VALID |

Note that 103 is `VALID` — being `Cancelled` is a perfectly valid business state. Validity and revenue-eligibility are different questions.

**How this maps to the project:** this classifier is the heart of `src/quality_checks.py`; rows flagged non-`VALID` go to a quarantine table rather than being deleted.

<!-- CHUNK-MARKER -->
