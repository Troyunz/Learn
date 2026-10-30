# 02 — Data Modelling Deep Dive

> **Companion project:** `../customer-sales-lakehouse`
> **Prerequisite:** `01_sql_deep_dive.md`
> **What you get out of this:** the ability to look at a pile of messy CSV files and design a warehouse that answers business questions quickly, correctly, and repeatedly.

Data modelling is the part of data engineering that separates "I can write a query" from "I can design the thing other people query." Pipelines move bytes. Models decide whether those bytes are *usable*.

Everything in this guide uses one running example — the customer sales lakehouse — so that every abstract concept has a concrete anchor.

---

## Table of contents

1. [The running example](#1-the-running-example)
2. [Operational (OLTP) vs analytical (OLAP) systems](#2-operational-oltp-vs-analytical-olap-systems)
3. [Normalization, worked step by step](#3-normalization-worked-step-by-step)
4. [Denormalization and its trade-offs](#4-denormalization-and-its-trade-offs)
5. [Facts and dimensions](#5-facts-and-dimensions)
6. [Grain — the most important decision you will make](#6-grain--the-most-important-decision-you-will-make)
7. [Surrogate keys vs natural keys](#7-surrogate-keys-vs-natural-keys)
8. [Star schema vs snowflake schema](#8-star-schema-vs-snowflake-schema)
9. [The star schema diagram for this project](#9-the-star-schema-diagram-for-this-project)
10. [Slowly Changing Dimensions (SCD1 and SCD2)](#10-slowly-changing-dimensions-scd1-and-scd2)
11. [Gold layer DDL (PostgreSQL-compatible)](#11-gold-layer-ddl-postgresql-compatible)
12. [Business rules for this model](#12-business-rules-for-this-model)
13. [Worked example: one order row's journey into fact_sales](#13-worked-example-one-order-rows-journey-into-fact_sales)
14. [Loading patterns in PySpark](#14-loading-patterns-in-pyspark)
15. [Common modelling mistakes](#15-common-modelling-mistakes)
16. [How this maps to the project](#16-how-this-maps-to-the-project)
17. [Exercises](#17-exercises)

---

## 1. The running example

Three fictional CSV files land in `../customer-sales-lakehouse/data/raw/`. They are small on purpose — small enough that you can verify every number by hand, which is exactly how you should learn modelling.

### `data/raw/customers.csv`

```
customer_id,customer_name,region,customer_created_date
1,Aisha Khan,North,2025-10-01
2,Ben Smith,South,2025-11-12
3,Carla Jones,North,2025-12-07
4,David Lee,West,2026-01-15
```

### `data/raw/products.csv`

```
product_id,product_name,category,unit_price
1001,Laptop Bag,Accessories,50.00
1002,Wireless Mouse,Accessories,25.00
1003,Monitor,Hardware,200.00
1004,Keyboard,Accessories,75.00
```

### `data/raw/orders.csv`

```
order_id,customer_id,product_id,order_date,quantity,amount,status,updated_at
101,1,1003,2026-01-05,1,200.00,Completed,2026-01-05 10:00:00
102,1,1002,2026-01-10,2,50.00,Completed,2026-01-10 11:00:00
103,2,1001,2026-01-12,1,50.00,Cancelled,2026-01-12 09:30:00
104,3,1004,2026-02-01,1,75.00,Completed,2026-02-01 14:00:00
105,3,1002,2026-02-05,1,,Pending,2026-02-05 15:00:00
106,99,1001,2026-02-10,1,50.00,Completed,2026-02-10 12:00:00
107,1,1003,2026-02-15,1,200.00,Completed,2026-02-15 08:00:00
107,1,1003,2026-02-15,1,200.00,Completed,2026-02-15 08:00:00
```

### The deliberate data-quality issues

These are planted so that your model and your quality checks have something real to catch:

| # | Issue | Where | Count |
|---|-------|-------|-------|
| 1 | Duplicate `order_id` (identical `updated_at`) | order `107` appears twice | exactly 1 |
| 2 | Missing mandatory `amount` | order `105` | exactly 1 |
| 3 | Invalid customer reference | order `106` → `customer_id = 99`, which does not exist | exactly 1 |
| 4 | Invalid product reference | — | zero |
| 5 | Invalid status | valid set is `Completed`, `Cancelled`, `Pending` | zero |

### The canonical business rule

> `fact_sales` contains **only valid, de-duplicated, `Completed` orders.**

For this sample, recognised revenue is **exactly 525.00**:

| order_id | amount | included? | reason |
|----------|--------|-----------|--------|
| 101 | 200.00 | ✅ | valid, Completed |
| 102 | 50.00 | ✅ | valid, Completed |
| 103 | 50.00 | ❌ | status = Cancelled |
| 104 | 75.00 | ✅ | valid, Completed |
| 105 | *(null)* | ❌ | status = Pending **and** missing mandatory amount |
| 106 | 50.00 | ❌ | invalid customer reference (99 not in customers) |
| 107 | 200.00 | ✅ | valid, Completed — **counted once**, duplicate removed |
| 107 (dup) | 200.00 | ❌ | exact duplicate row |

`200.00 + 50.00 + 75.00 + 200.00 = **525.00**`

Memorise that number. Every layer of the lakehouse — silver, gold, the reporting SQL in `sql/03_reporting_queries.sql`, and the tests in `tests/` — must agree on **525.00**. If a refactor changes it, the refactor is wrong until proven otherwise.

---

## 2. Operational (OLTP) vs analytical (OLAP) systems

A beginner's instinct is: "the data is already in the application database, why not just query it there?" Here is the full answer.

### 2.1 The two workloads

| Dimension | OLTP (operational) | OLAP (analytical) |
|-----------|--------------------|-------------------|
| Purpose | Run the business | Understand the business |
| Typical user | The checkout service, the mobile app | Analyst, data scientist, exec dashboard |
| Typical statement | `INSERT`, `UPDATE`, single-row `SELECT` | Large multi-table `SELECT` with aggregation |
| Rows touched per query | 1 to a few hundred | thousands to billions |
| Columns touched per query | most/all of a narrow row | 3–8 columns out of 60 |
| Latency target | 1–50 ms | 1 s to minutes |
| Concurrency | thousands of sessions | tens of sessions |
| Data freshness | right now | minutes to a day old is usually fine |
| Schema shape | highly normalized (3NF+) | denormalized (star schema) |
| Storage layout | row-oriented | column-oriented (Parquet, Delta, columnstore) |
| History | current state only; updates overwrite | full history retained |
| Failure impact | business stops | a dashboard is stale |

### 2.2 Access patterns drive physical design

An OLTP system's dominant query is "give me *this one* order and all its fields":

```sql
SELECT * FROM orders WHERE order_id = 107;
```

Row-oriented storage is perfect for that: the whole row sits together on one page, one disk seek gets it.

An OLAP system's dominant query is "give me *one or two columns* from *every* row":

```sql
SELECT region, SUM(amount) FROM fact_sales f JOIN dim_customer c USING (customer_key) GROUP BY region;
```

If you store that row-wise, you drag 60 columns off disk to use 2. Column-oriented formats (Parquet, which the bronze/silver/gold layers of this project use) store each column contiguously, so you read only `region` and `amount`. On top of that, a column of repeated values like `Completed`, `Completed`, `Cancelled` compresses brutally well — run-length and dictionary encoding can shrink it 10–50×. Less I/O, faster scans, cheaper cloud bills.

### 2.3 Normalization pressure

OLTP systems are normalized because their enemy is the **anomaly**:

- **Update anomaly** — a customer's region is stored on 10,000 order rows. The customer moves. Now you must update 10,000 rows atomically or you have contradictory data.
- **Insert anomaly** — you cannot register a new product until somebody orders it, because product attributes only live on order rows.
- **Delete anomaly** — deleting the last order for a product erases the only record that the product existed.

Normalization solves all three by storing each fact exactly once ("one fact, one place").

OLAP systems are denormalized because their enemy is the **join**. Reporting queries are read-only and never update a customer's region in place — the ETL job does that, once, under control. So the anomaly risk that justifies normalization largely disappears, while the join cost that it creates stays. Different enemy, different design.

### 2.4 Concurrency and isolation

OLTP databases are built around transactions and locking. A `SELECT SUM(amount) FROM orders` that scans ten million rows inside a production database will:

- hold read locks or build a long-lived MVCC snapshot,
- pollute the buffer pool by evicting hot pages the application needs,
- compete for the same CPU and I/O as the checkout path,
- and, in the worst case, block writers or trigger lock escalation.

That is the mechanical reason analysts get banned from production.

### 2.5 Why you don't report off production — the full list

1. **Performance isolation.** An analyst's accidental cartesian join should not be able to take down checkout.
2. **History.** Production overwrites. If Carla Jones moves from North to East, production shows East and the past is gone. Analytics needs to answer "what were North's sales in January?" with January's truth.
3. **Conformance across sources.** Revenue lives in the order system, marketing spend in another SaaS tool, headcount in HR. Only a warehouse can put them on one grain with one calendar.
4. **Query ergonomics.** Production's 3NF schema needs a six-table join to answer "sales by region by month". A star schema needs two.
5. **Governance and blast radius.** A warehouse can expose a curated, masked, column-filtered view. Production gives read access to PII you never intended to share.
6. **Schema stability.** App developers refactor tables whenever they like. A warehouse is a contract — the gold layer changes on your schedule, not theirs.
7. **Reproducibility.** A report run today against production and re-run tomorrow gives different numbers with no explanation. A warehouse snapshot is reproducible and auditable.

> **How this maps to the project:** `../customer-sales-lakehouse` simulates the production extract as `data/raw/*.csv`. Bronze preserves it byte-faithfully (so you can always reproduce), silver cleans and types it, and gold reshapes it into the star schema that reporting actually queries. You never "fix" raw in place — that would destroy reproducibility.

---

## 3. Normalization, worked step by step

Let's take a deliberately horrible flat table — the kind of thing a well-meaning person builds in a spreadsheet — and normalize it properly.

### 3.0 The messy source table

Imagine the sales team hands you `orders_flat`:

| order_id | order_date | customer_id | customer_name | region | region_manager | items | item_categories | order_total |
|---|---|---|---|---|---|---|---|---|
| 101 | 2026-01-05 | 1 | Aisha Khan | North | N. Patel | Monitor | Hardware | 200.00 |
| 102 | 2026-01-10 | 1 | Aisha Khan | North | N. Patel | Wireless Mouse | Accessories | 50.00 |
| 103 | 2026-01-12 | 2 | Ben Smith | South | S. Rao | Laptop Bag | Accessories | 50.00 |
| 104 | 2026-02-01 | 3 | Carla Jones | North | N. Patel | Keyboard | Accessories | 75.00 |
| 108 | 2026-02-20 | 1 | Aisha Khan | North | N. Patel | Monitor, Keyboard | Hardware, Accessories | 275.00 |

Problems visible to the naked eye:

- Row 108 crams two products into one cell (`items`) and two categories into another.
- `customer_name`, `region` and `region_manager` repeat on every order.
- `region_manager` depends on `region`, not on `order_id`.
- If Aisha's name is corrected, you must update many rows.
- You cannot record a region that has no orders yet.

### 3.1 First Normal Form (1NF)

> **Rule:** every column holds a single atomic value; no repeating groups; every row is uniquely identifiable.

`items` and `item_categories` are multi-valued. Split row 108 into one row per product and introduce a line number so each row is unique.

**`orders_1nf`**

| order_id | line_no | order_date | customer_id | customer_name | region | region_manager | product_name | category | unit_price | quantity | line_amount |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 101 | 1 | 2026-01-05 | 1 | Aisha Khan | North | N. Patel | Monitor | Hardware | 200.00 | 1 | 200.00 |
| 102 | 1 | 2026-01-10 | 1 | Aisha Khan | North | N. Patel | Wireless Mouse | Accessories | 25.00 | 2 | 50.00 |
| 103 | 1 | 2026-01-12 | 2 | Ben Smith | South | S. Rao | Laptop Bag | Accessories | 50.00 | 1 | 50.00 |
| 104 | 1 | 2026-02-01 | 3 | Carla Jones | North | N. Patel | Keyboard | Accessories | 75.00 | 1 | 75.00 |
| 108 | 1 | 2026-02-20 | 1 | Aisha Khan | North | N. Patel | Monitor | Hardware | 200.00 | 1 | 200.00 |
| 108 | 2 | 2026-02-20 | 1 | Aisha Khan | North | N. Patel | Keyboard | Accessories | 75.00 | 1 | 75.00 |

Primary key is now the composite `(order_id, line_no)`.

> **Why this matters practically:** comma-separated cells are unqueryable. `WHERE items = 'Monitor'` misses row 108 entirely, and `WHERE items LIKE '%Monitor%'` would also match a hypothetical "Monitor Stand". Atomicity isn't academic purity — it's the difference between a correct filter and a wrong one.

### 3.2 Second Normal Form (2NF)

> **Rule:** be in 1NF, **and** every non-key column depends on the *whole* primary key, not just part of it.

This rule only bites when the key is composite — which ours now is, `(order_id, line_no)`.

Which columns depend on only *part* of the key?

| Column | Depends on | Verdict |
|--------|-----------|---------|
| `order_date` | `order_id` only | ❌ partial dependency |
| `customer_id` | `order_id` only | ❌ partial dependency |
| `customer_name` | `order_id` only (via customer) | ❌ partial dependency |
| `region`, `region_manager` | `order_id` only (via customer) | ❌ partial dependency |
| `product_name`, `category`, `unit_price` | the product on that line | ❌ partial (depends on line's product, not the whole key) |
| `quantity`, `line_amount` | `(order_id, line_no)` | ✅ full dependency |

Split into order-header, order-line, and product tables.

**`orders_hdr`** — PK `order_id`

| order_id | order_date | customer_id | customer_name | region | region_manager | status |
|---|---|---|---|---|---|---|
| 101 | 2026-01-05 | 1 | Aisha Khan | North | N. Patel | Completed |
| 102 | 2026-01-10 | 1 | Aisha Khan | North | N. Patel | Completed |
| 103 | 2026-01-12 | 2 | Ben Smith | South | S. Rao | Cancelled |
| 104 | 2026-02-01 | 3 | Carla Jones | North | N. Patel | Completed |
| 108 | 2026-02-20 | 1 | Aisha Khan | North | N. Patel | Completed |

**`order_line`** — PK `(order_id, line_no)`

| order_id | line_no | product_id | quantity | line_amount |
|---|---|---|---|---|
| 101 | 1 | 1003 | 1 | 200.00 |
| 102 | 1 | 1002 | 2 | 50.00 |
| 103 | 1 | 1001 | 1 | 50.00 |
| 104 | 1 | 1004 | 1 | 75.00 |
| 108 | 1 | 1003 | 1 | 200.00 |
| 108 | 2 | 1004 | 1 | 75.00 |

**`product`** — PK `product_id`

| product_id | product_name | category | unit_price |
|---|---|---|---|
| 1001 | Laptop Bag | Accessories | 50.00 |
| 1002 | Wireless Mouse | Accessories | 25.00 |
| 1003 | Monitor | Hardware | 200.00 |
| 1004 | Keyboard | Accessories | 75.00 |

### 3.3 Third Normal Form (3NF)

> **Rule:** be in 2NF, **and** no non-key column depends on another non-key column (no *transitive* dependencies).

Look at `orders_hdr`. The key is `order_id`. But:

```
order_id  →  customer_id  →  customer_name, region
region    →  region_manager
```

`customer_name` doesn't really depend on the order; it depends on the customer. And `region_manager` depends on the region. Both are transitive dependencies. Remove them into their own tables.

**`orders_hdr`** (3NF) — PK `order_id`

| order_id | order_date | customer_id | status |
|---|---|---|---|
| 101 | 2026-01-05 | 1 | Completed |
| 102 | 2026-01-10 | 1 | Completed |
| 103 | 2026-01-12 | 2 | Cancelled |
| 104 | 2026-02-01 | 3 | Completed |
| 108 | 2026-02-20 | 1 | Completed |

**`customer`** — PK `customer_id`

| customer_id | customer_name | region_code | customer_created_date |
|---|---|---|---|
| 1 | Aisha Khan | N | 2025-10-01 |
| 2 | Ben Smith | S | 2025-11-12 |
| 3 | Carla Jones | N | 2025-12-07 |
| 4 | David Lee | W | 2026-01-15 |

**`region`** — PK `region_code`

| region_code | region_name | region_manager |
|---|---|---|
| N | North | N. Patel |
| S | South | S. Rao |
| W | West | W. Chen |
| E | East | E. Duarte |

### 3.4 What 3NF bought us

| Anomaly | Before | After |
|---------|--------|-------|
| Update | correcting "Aisha Khan" touched 3 rows | touches 1 row in `customer` |
| Insert | couldn't record region East (no orders) | `INSERT INTO region` works standalone |
| Delete | deleting order 103 erased Laptop Bag's existence | `product` row survives independently |

### 3.5 The cost of 3NF: look at the query

To answer "total completed revenue by region by month" against the 3NF model:

```sql
SELECT
    r.region_name,
    DATE_TRUNC('month', o.order_date) AS order_month,
    SUM(ol.line_amount)               AS revenue
FROM order_line  ol
JOIN orders_hdr  o ON o.order_id    = ol.order_id
JOIN customer    c ON c.customer_id = o.customer_id
JOIN region      r ON r.region_code = c.region_code
WHERE o.status = 'Completed'
GROUP BY r.region_name, DATE_TRUNC('month', o.order_date)
ORDER BY r.region_name, order_month;
```

Four tables. And this is a *toy*. A real 3NF ERP has 400 tables and the same question needs nine joins, three of which have surprising cardinality. That is the pressure that produces the star schema.

### 3.6 A quick word on BCNF and beyond

Boyce-Codd Normal Form (BCNF) tightens 3NF: *every* determinant must be a candidate key. 4NF removes multi-valued dependencies, 5NF removes join dependencies. In practice:

- OLTP systems target **3NF, occasionally BCNF**.
- Analytical systems deliberately stop short and then *reverse course* into denormalization.

You will almost never be asked to produce a 5NF design in a data engineering job. You *will* be asked to explain 1NF/2NF/3NF in an interview. Know them cold; know why you're abandoning them in gold.

---

## 4. Denormalization and its trade-offs

Denormalization is the deliberate reintroduction of redundancy to make reads faster and simpler.

In the gold layer, `dim_customer` collapses `customer` + `region` back together:

| customer_key | customer_id | customer_name | region | region_manager |
|---|---|---|---|---|
| 1 | 1 | Aisha Khan | North | N. Patel |
| 2 | 2 | Ben Smith | South | S. Rao |
| 3 | 3 | Carla Jones | North | N. Patel |
| 4 | 4 | David Lee | West | W. Chen |

`N. Patel` now appears twice. In OLTP that's a sin. In gold it's fine, because:

1. Nothing updates `dim_customer` except the controlled ETL job in `notebooks/05_gold_dimensions.py`.
2. If the North manager changes, the ETL rewrites every affected dimension row in one pass — there is no partial-update window exposed to users.
3. The query drops from four joins to two.

### 4.1 Trade-off table

| Aspect | Normalized (3NF) | Denormalized (star) |
|--------|------------------|---------------------|
| Write complexity | low (update one place) | higher (ETL must propagate) |
| Read complexity | high (many joins) | low (few joins) |
| Storage | minimal | more (repeated attribute values) |
| Risk of inconsistency | low, enforced by FKs | moderate, enforced by ETL + tests |
| Query performance | worse at scale | much better at scale |
| Schema comprehensibility for analysts | poor | excellent |
| Suitability for BI tools | poor | excellent (tools assume star) |
| Handling of history | hard (needs audit tables) | natural (SCD2) |

### 4.2 Rules of thumb

- **Denormalize dimensions aggressively.** Dimension tables are small; a few million rows at most. Redundancy costs almost nothing.
- **Do not denormalize facts.** Facts are huge. Adding `customer_name` to a billion-row fact is expensive and defeats the point of the dimension.
- **Never denormalize in silver.** Silver stays close to source shape: cleansed, typed, deduplicated — but still recognisably "orders", "customers", "products". Denormalization is a gold-layer activity.
- **Cost is paid at write time, once; benefit is collected at read time, forever.** That asymmetry is the whole argument.

---

## 5. Facts and dimensions

### 5.1 The core intuition

Split every business question into two parts:

> "**How much** [measure] by **what** [context]?"

- The **how much** goes in a **fact table** — numeric, additive, voluminous, growing.
- The **what** goes in a **dimension table** — descriptive, textual, relatively small, slowly changing.

"Total completed revenue (how much) by region (what) by month (what)."

### 5.2 Fact table characteristics

| Property | Typical |
|----------|---------|
| Row count | large, grows forever |
| Columns | mostly foreign keys + a handful of numeric measures |
| Width | narrow |
| Updates | rare; ideally insert-only |
| Keys | composite of dimension FKs, or a surrogate `sales_key` |
| Naming | `fact_*` (`fact_sales`) |

`fact_sales` in this project:

| sales_key | order_id | customer_key | product_key | date_key | quantity | amount |
|---|---|---|---|---|---|---|
| 1 | 101 | 1 | 3 | 20260105 | 1 | 200.00 |
| 2 | 102 | 1 | 2 | 20260110 | 2 | 50.00 |
| 3 | 104 | 3 | 4 | 20260201 | 1 | 75.00 |
| 4 | 107 | 1 | 3 | 20260215 | 1 | 200.00 |

Sum of `amount` = 200.00 + 50.00 + 75.00 + 200.00 = **525.00**. ✅

### 5.3 Dimension table characteristics

| Property | Typical |
|----------|---------|
| Row count | small to medium |
| Columns | many descriptive attributes |
| Width | wide |
| Updates | occasional, handled by SCD policy |
| Keys | surrogate PK + natural/business key |
| Naming | `dim_*` (`dim_customer`, `dim_product`, `dim_date`) |

### 5.4 Types of fact tables

#### a) Transactional fact

One row per business event, recorded at the moment it happens. Immutable once written.

- Grain: one row per order line.
- Our `fact_sales` is exactly this.
- Measures are fully additive.
- Advantages: maximum flexibility; you can aggregate to any level later.

#### b) Periodic snapshot fact

One row per entity per *regular time period*, whether or not anything happened.

Example: `fact_daily_customer_balance` — one row per customer per day with their outstanding balance.

| date_key | customer_key | open_orders | outstanding_amount |
|---|---|---|---|
| 20260131 | 1 | 0 | 0.00 |
| 20260131 | 3 | 0 | 0.00 |
| 20260228 | 1 | 0 | 0.00 |
| 20260228 | 3 | 1 | 0.00 |

- Rows appear on a predictable cadence, so size is predictable: `entities × periods`.
- Measures like `outstanding_amount` are **semi-additive** — you can add them across customers, but not across days.

#### c) Accumulating snapshot fact

One row per *process instance*, **updated in place** as the process advances through milestones.

Example: `fact_order_fulfilment` — one row per order, with a column per milestone.

| order_id | customer_key | placed_date_key | paid_date_key | shipped_date_key | delivered_date_key | days_to_deliver |
|---|---|---|---|---|---|---|
| 101 | 1 | 20260105 | 20260105 | 20260106 | 20260109 | 4 |
| 105 | 3 | 20260205 | *(null)* | *(null)* | *(null)* | *(null)* |

- Has multiple date FKs (role-playing dimensions — all pointing at `dim_date`).
- Is **mutated**, unlike the other two types.
- Perfect for lag/cycle-time analysis: "average days from placed to delivered".

#### Comparison

| | Transactional | Periodic snapshot | Accumulating snapshot |
|---|---|---|---|
| Grain | one event | one entity per period | one process instance |
| Insert-only? | yes | yes | no — updated in place |
| Row count growth | with events | with periods | with new processes |
| Date FKs | usually 1 | 1 | many (one per milestone) |
| Classic question | "revenue by month" | "inventory on hand at month-end" | "how long does fulfilment take?" |

### 5.5 Additivity of measures

This is the concept that most often trips up beginners, and it's directly testable in interviews.

| Type | Definition | Example | Can you `SUM` it? |
|------|------------|---------|-------------------|
| **Additive** | Summable across **all** dimensions | `amount`, `quantity` | Yes, always |
| **Semi-additive** | Summable across **some** dimensions but not time | account balance, inventory on hand, headcount | Yes across customers/products; **no** across days — use last-value or average |
| **Non-additive** | Not summable across **any** dimension | ratios, percentages, unit price, margin % | No — recompute from additive components |

#### Worked example of the non-additive trap

Suppose you store `avg_unit_price` on each fact row.

| order_id | quantity | amount | avg_unit_price |
|---|---|---|---|
| 101 | 1 | 200.00 | 200.00 |
| 102 | 2 | 50.00 | 25.00 |
| 104 | 1 | 75.00 | 75.00 |
| 107 | 1 | 200.00 | 200.00 |

Naive average of the column: `(200 + 25 + 75 + 200) / 4 = 125.00`.
Correct weighted average: `SUM(amount) / SUM(quantity) = 525.00 / 5 = 105.00`.

The naive answer is wrong by 19%. **The fix is structural, not procedural:** store only the additive components (`amount`, `quantity`) in the fact, and compute ratios in the query or in a semantic layer.

```sql
-- Correct: compute the ratio from additive components at query time
SELECT
    d.category,
    SUM(f.amount)                       AS revenue,
    SUM(f.quantity)                     AS units,
    SUM(f.amount) / NULLIF(SUM(f.quantity), 0) AS avg_price_per_unit
FROM fact_sales  f
JOIN dim_product d ON d.product_key = f.product_key
GROUP BY d.category;
```

#### Semi-additive worked example

For a daily balance snapshot, "total balance for February" is meaningless as a sum over days. The right aggregation is the **last** value in the period:

```sql
SELECT
    customer_key,
    MAX(date_key) AS as_of_date_key,
    -- take the balance on the latest day in the window, not the sum
    (ARRAY_AGG(outstanding_amount ORDER BY date_key DESC))[1] AS month_end_balance
FROM fact_daily_customer_balance
WHERE date_key BETWEEN 20260201 AND 20260228
GROUP BY customer_key;
```

---

## 6. Grain — the most important decision you will make

**Grain** is the precise business meaning of a single row in a fact table. Declare it in one sentence, in English, before you write a single `CREATE TABLE`.

### 6.1 The grain of `fact_sales`

> **One row per valid, de-duplicated, `Completed` order line.**

Unpack every word:

| Word | Meaning | Consequence |
|------|---------|-------------|
| **one row per** | the row identity | the composite business key is `(order_id, product_id)` in this source, since each order has a single product line |
| **valid** | passes referential integrity | order 106 is excluded — customer 99 doesn't exist |
| **de-duplicated** | exact duplicates collapsed | only one row for order 107 |
| **Completed** | status filter | orders 103 (Cancelled) and 105 (Pending) excluded |
| **order line** | not "order", not "customer-month" | measures are per line: `quantity`, `amount` |

### 6.2 Why declaring grain first is non-negotiable

1. **It determines the foreign keys.** At order-line grain you can attach `dim_product`. At order-header grain you cannot, because an order may have many products.
2. **It prevents double counting.** Mixing grains in one table — some rows per line, some per order — guarantees inflated sums and nobody will notice for six months.
3. **It makes tests writable.** "Row count equals distinct `(order_id, product_id)` of valid completed orders" is a test you can actually run. Without a grain statement, you can't even state the test.
4. **It settles arguments.** When someone says "revenue is wrong", the first question is "at what grain did you expect it?"

### 6.3 Always use the finest grain you can afford

A row at order-line grain can be aggregated up to order, customer-month, region-quarter — anything. A row pre-aggregated to region-month can never be drilled back down. You can always roll up; you can never roll down. Store atomic detail; build aggregates as *additional* tables if performance demands.

### 6.4 Grain test in SQL

```sql
-- There must be no duplicates at the declared grain.
-- Expected result: zero rows.
SELECT order_id, product_key, COUNT(*) AS n
FROM fact_sales
GROUP BY order_id, product_key
HAVING COUNT(*) > 1;

-- And the headline number must hold.
-- Expected result: 525.00
SELECT SUM(amount) AS recognised_revenue FROM fact_sales;
```

---

## 7. Surrogate keys vs natural keys

### 7.1 Definitions

- **Natural (business) key** — an identifier that comes from the source system and has business meaning: `customer_id = 3`, `product_id = 1003`, an email address, an ISBN.
- **Surrogate key** — a meaningless, warehouse-generated integer: `customer_key = 3`. Its only job is to identify a dimension row.

In `dim_customer`, `customer_id` is the natural key and `customer_key` is the surrogate.

### 7.2 Why warehouses prefer surrogates

| Reason | Explanation |
|--------|-------------|
| **SCD2 requires it** | Under SCD2, customer 3 has *two* rows (North version, East version). `customer_id = 3` is no longer unique, so it cannot be a primary key. `customer_key` can. |
| **Source-system independence** | If the CRM is replaced and re-issues IDs, or you merge two companies with overlapping IDs, the surrogate protects the fact table. |
| **Multi-source conformance** | Customer "Aisha Khan" might be `CUST-0001` in Salesforce and `1` in the billing system. One surrogate row can map both. |
| **Narrow, uniform join keys** | A 4-byte `INT` joins faster than a 40-character composite string and keeps the fact table small. |
| **Handles late-arriving / unknown members** | Reserve `customer_key = -1` for "Unknown" so facts never have NULL FKs. |
| **Insulates against type changes** | Source changes `product_id` from `INT` to `VARCHAR`? Only the dimension's natural-key column changes. |

### 7.3 The unknown-member pattern

Order 106 references customer 99, which doesn't exist. You have two legitimate choices:

**Option A (what this project does):** reject the row. It fails referential integrity, so it never enters `fact_sales`. Revenue stays at 525.00.

**Option B (common in enterprises):** route it to `customer_key = -1` ("Unknown") so revenue is never lost, and report the Unknown bucket as a data-quality KPI.

```sql
-- The unknown member, inserted once at dimension build time
INSERT INTO dim_customer
    (customer_key, customer_id, customer_name, region, customer_created_date,
     effective_from, effective_to, is_current)
VALUES
    (-1, NULL, 'Unknown', 'Unknown', NULL, DATE '1900-01-01', DATE '9999-12-31', TRUE);
```

> **Which is right?** It depends on the business rule. Here, the canonical rule says `fact_sales` contains only *valid* orders, so Option A applies and order 106 is quarantined and reported, not loaded. The important thing is that the rule is written down and tested.

### 7.4 Keep the natural key in the fact table too

Note that `fact_sales` keeps `order_id` alongside the surrogate FKs. That's deliberate:

- It makes debugging possible ("show me the fact row for order 107").
- It enables idempotent reprocessing (delete-and-reload by `order_id`).
- It lets you reconcile with the source system without walking dimensions.

This is called a **degenerate dimension**: a business key that lives in the fact table because it has no attributes of its own worth putting in a dimension.

---

## 8. Star schema vs snowflake schema

### 8.1 Star

One central fact table joined directly to fully denormalized dimension tables. Dimension hierarchies (category → product, region → customer) are flattened into the dimension itself.

```
dim_customer        fact_sales        dim_product
```

### 8.2 Snowflake

Dimensions are themselves normalized into sub-dimensions:

```
dim_customer ──> dim_region ──> dim_country
dim_product  ──> dim_category ──> dim_department
```

### 8.3 Comparison table

| Aspect | Star schema | Snowflake schema |
|--------|-------------|------------------|
| Dimension normalization | denormalized (1 table per dimension) | normalized into multiple tables |
| Number of joins per query | fewer (1 hop per dimension) | more (multi-hop chains) |
| Query performance | generally faster | generally slower |
| Storage used by dimensions | slightly more | slightly less |
| Query readability for analysts | high | lower |
| ETL complexity | moderate | higher (more tables to load in order) |
| Data integrity enforcement | relies on ETL + tests | partly enforced by FKs |
| BI tool support | excellent — tools assume star | workable but needs more modelling |
| Handling of very large dimensions | can be wide | can help if a sub-dimension is huge and volatile |
| Typical use | most analytics warehouses | when attribute hierarchies are huge/reused |
| Best for | reporting speed and simplicity | storage-constrained or heavily shared hierarchies |

### 8.4 Which should you use?

Default to **star**. Reach for snowflaking only when you have a specific, measured reason:

- A dimension attribute set is enormous (e.g. a 50-million-row geography hierarchy) and duplicating it is genuinely costly.
- A hierarchy is shared by many dimensions and must be maintained in exactly one place.
- Regulatory requirements demand normalized reference data.

> Storage is cheap; analyst confusion and join latency are not. Kimball's own guidance is: snowflake only when you must.

### 8.5 The galaxy / fact constellation

When multiple fact tables share conformed dimensions, you have a **fact constellation** (or galaxy schema). For example `fact_sales` and `fact_order_fulfilment` both joining `dim_customer`, `dim_product`, `dim_date`. This is normal and desirable — **conformed dimensions** are what make cross-process analysis possible ("do customers with slow deliveries buy less next quarter?").

---

## 9. The star schema diagram for this project

```
                    +-------------------------------+
                    |         dim_date              |
                    |-------------------------------|
                    | date_key (PK)   INT           |
                    | full_date       DATE          |
                    | day_of_month    INT           |
                    | month_num       INT           |
                    | month_name      TEXT          |
                    | quarter         INT           |
                    | year            INT           |
                    | day_name        TEXT          |
                    | is_weekend      BOOLEAN       |
                    +---------------+---------------+
                                    |
                                    | FK: fact_sales.date_key
                                    |   -> dim_date.date_key
                                    |
                                    v
+---------------------------+   +---------------------------------+   +---------------------------+
|      dim_customer         |   |           fact_sales            |   |       dim_product         |
|---------------------------|   |---------------------------------|   |---------------------------|
| customer_key (PK)   INT   |<--| sales_key (PK)       BIGINT     |-->| product_key (PK)   INT    |
| customer_id (NK)    INT   |   | order_id (DD)        INT        |   | product_id (NK)    INT    |
| customer_name       TEXT  |   | customer_key (FK)    INT        |   | product_name       TEXT   |
| region              TEXT  |   | product_key  (FK)    INT        |   | category           TEXT   |
| customer_created_date DATE|   | date_key     (FK)    INT        |   | unit_price         NUMERIC|
| effective_from      DATE  |   | quantity             INT        |   | effective_from     DATE   |
| effective_to        DATE  |   | amount               NUMERIC    |   | effective_to       DATE   |
| is_current          BOOL  |   | order_status         TEXT       |   | is_current         BOOL   |
+---------------------------+   | load_timestamp       TIMESTAMP  |   +---------------------------+
          ^                     +---------------------------------+               ^
          |                                                                       |
          |  FK: fact_sales.customer_key                FK: fact_sales.product_key |
          +-----> dim_customer.customer_key             -----> dim_product.product_key

Legend:  PK = primary (surrogate) key   NK = natural/business key   FK = foreign key
         DD = degenerate dimension (business key kept in the fact table)

Relationship cardinality:
    dim_customer 1 ────< many fact_sales
    dim_product  1 ────< many fact_sales
    dim_date     1 ────< many fact_sales

Grain of fact_sales: one row per valid, de-duplicated, Completed order line.
SUM(fact_sales.amount) for the sample data = 525.00
```

### 9.1 Reading the diagram

- Every arrow points **from the fact table to a dimension**. That direction is the foreign key direction: the fact holds the key, the dimension owns it.
- All three relationships are **one-to-many** from dimension to fact. One customer, many sales rows.
- There are **no dimension-to-dimension joins**. That absence is what makes it a star rather than a snowflake.
- `dim_date` is generated, not sourced. You build it once from a date range; it never comes from a CSV.

---

## 10. Slowly Changing Dimensions (SCD1 and SCD2)

Dimension attributes change over time. **How you handle that change is a business decision, not a technical one.** The modelling patterns for it are called Slowly Changing Dimensions.

### 10.1 The scenario

On **2026-03-01**, customer 3 — **Carla Jones** — transfers from the **North** region to the **East** region.

Her historical orders:

| order_id | order_date | amount | status |
|---|---|---|---|
| 104 | 2026-02-01 | 75.00 | Completed |
| 105 | 2026-02-05 | *(null)* | Pending |

Only order 104 reaches `fact_sales`, contributing **75.00**, and it happened in **February — while Carla was still in North.**

Now the business question: **"What was North's revenue in February 2026?"**

The answer depends entirely on which SCD type you chose.

### 10.2 Before the change — `dim_customer` (SCD-agnostic)

| customer_key | customer_id | customer_name | region | customer_created_date |
|---|---|---|---|---|
| 1 | 1 | Aisha Khan | North | 2025-10-01 |
| 2 | 2 | Ben Smith | South | 2025-11-12 |
| 3 | 3 | Carla Jones | North | 2025-12-07 |
| 4 | 4 | David Lee | West | 2026-01-15 |

And `fact_sales`:

| sales_key | order_id | customer_key | product_key | date_key | quantity | amount |
|---|---|---|---|---|---|---|
| 1 | 101 | 1 | 3 | 20260105 | 1 | 200.00 |
| 2 | 102 | 1 | 2 | 20260110 | 2 | 50.00 |
| 3 | 104 | 3 | 4 | 20260201 | 1 | 75.00 |
| 4 | 107 | 1 | 3 | 20260215 | 1 | 200.00 |

---

### 10.3 SCD Type 1 — overwrite

> **Policy:** the dimension always reflects the *current* truth. History is discarded.

#### After the change — `dim_customer` under SCD1

| customer_key | customer_id | customer_name | region | customer_created_date |
|---|---|---|---|---|
| 1 | 1 | Aisha Khan | North | 2025-10-01 |
| 2 | 2 | Ben Smith | South | 2025-11-12 |
| 3 | 3 | Carla Jones | **East** | 2025-12-07 |
| 4 | 4 | David Lee | West | 2026-01-15 |

Note what did **not** change: `customer_key` is still `3`, the row count is still 4, and `fact_sales` was not touched at all.

#### Consequence

```sql
SELECT c.region, SUM(f.amount) AS revenue
FROM fact_sales f
JOIN dim_customer c ON c.customer_key = f.customer_key
WHERE f.date_key BETWEEN 20260201 AND 20260228
GROUP BY c.region;
```

| region | revenue |
|---|---|
| East | 75.00 |
| North | 200.00 |

February's 75.00 has **retroactively moved to East**, even though the sale genuinely happened while Carla was in North. The North regional manager's February number just changed without any sale changing. That is the defining behaviour of SCD1.

#### Type 1 upsert SQL

```sql
UPDATE dim_customer AS d
SET    customer_name = s.customer_name,
       region        = s.region,
       updated_at    = CURRENT_TIMESTAMP
FROM   stg_customers AS s
WHERE  d.customer_id = s.customer_id
  AND  (d.customer_name IS DISTINCT FROM s.customer_name
     OR d.region        IS DISTINCT FROM s.region);

INSERT INTO dim_customer (customer_id, customer_name, region, customer_created_date)
SELECT s.customer_id, s.customer_name, s.region, s.customer_created_date
FROM   stg_customers s
LEFT JOIN dim_customer d ON d.customer_id = s.customer_id
WHERE  d.customer_id IS NULL;
```

#### When SCD1 is correct

- **Correcting errors.** "Aisha Kahn" was a typo for "Aisha Khan". You don't want the typo preserved as history — you want it gone.
- **Attributes with no analytical meaning over time.** A `last_updated_by` username.
- **When the business explicitly says "always show current".** Some orgs want all history restated under the current org structure so year-over-year comparisons use today's hierarchy.

#### Pros and cons

| Pros | Cons |
|---|---|
| Simplest to implement | History is irrecoverably lost |
| Dimension stays small (1 row per entity) | Past reports become non-reproducible |
| No key-lookup complexity in the fact load | Can't answer "what was true then?" |
| Natural key stays unique | Restates prior-period numbers silently |

---

### 10.4 SCD Type 2 — add a new row, version the history

> **Policy:** preserve history. When a tracked attribute changes, expire the current row and insert a new one.

Three control columns do the work:

| Column | Meaning |
|--------|---------|
| `effective_from` | the date this version became true (inclusive) |
| `effective_to` | the date this version stopped being true (here: exclusive upper bound; `9999-12-31` = still true) |
| `is_current` | convenience boolean, `TRUE` for exactly one row per natural key |

#### Before the change — `dim_customer` under SCD2

| customer_key | customer_id | customer_name | region | customer_created_date | effective_from | effective_to | is_current |
|---|---|---|---|---|---|---|---|
| 1 | 1 | Aisha Khan | North | 2025-10-01 | 2025-10-01 | 9999-12-31 | TRUE |
| 2 | 2 | Ben Smith | South | 2025-11-12 | 2025-11-12 | 9999-12-31 | TRUE |
| 3 | 3 | Carla Jones | North | 2025-12-07 | 2025-12-07 | 9999-12-31 | TRUE |
| 4 | 4 | David Lee | West | 2026-01-15 | 2026-01-15 | 9999-12-31 | TRUE |

#### After the change (2026-03-01) — `dim_customer` under SCD2

| customer_key | customer_id | customer_name | region | customer_created_date | effective_from | effective_to | is_current |
|---|---|---|---|---|---|---|---|
| 1 | 1 | Aisha Khan | North | 2025-10-01 | 2025-10-01 | 9999-12-31 | TRUE |
| 2 | 2 | Ben Smith | South | 2025-11-12 | 2025-11-12 | 9999-12-31 | TRUE |
| 3 | 3 | Carla Jones | **North** | 2025-12-07 | 2025-12-07 | **2026-03-01** | **FALSE** |
| 4 | 4 | David Lee | West | 2026-01-15 | 2026-01-15 | 9999-12-31 | TRUE |
| **5** | **3** | **Carla Jones** | **East** | **2025-12-07** | **2026-03-01** | **9999-12-31** | **TRUE** |

Observe carefully:

- The dimension grew from 4 rows to **5**.
- `customer_id = 3` now appears **twice**. This is exactly why the primary key must be the surrogate `customer_key`, not the natural key.
- `customer_key = 3` is frozen forever as "Carla Jones, North".
- `customer_key = 5` is the new current version.
- **`fact_sales` is not retro-updated.** Row `sales_key = 3` (order 104, February) still points at `customer_key = 3` — the North version — because that was the truth on 2026-02-01.

#### Consequence

The same February query:

```sql
SELECT c.region, SUM(f.amount) AS revenue
FROM fact_sales f
JOIN dim_customer c ON c.customer_key = f.customer_key
WHERE f.date_key BETWEEN 20260201 AND 20260228
GROUP BY c.region;
```

| region | revenue |
|---|---|
| North | 275.00 |

February stays as it was recorded: 75.00 (Carla, North) + 200.00 (Aisha, North) = **275.00**. The past does not move. Any *new* Carla order dated 2026-03-05 would pick up `customer_key = 5` and land in East.

And the grand total across the whole sample is unchanged:

```sql
SELECT SUM(amount) FROM fact_sales;  -- 525.00
```

SCD type affects *attribution*, never *the total*.

#### Querying "current state only" under SCD2

```sql
SELECT customer_id, customer_name, region
FROM   dim_customer
WHERE  is_current = TRUE
ORDER  BY customer_id;
```

| customer_id | customer_name | region |
|---|---|---|
| 1 | Aisha Khan | North |
| 2 | Ben Smith | South |
| 3 | Carla Jones | East |
| 4 | David Lee | West |

#### Querying "as it was on a given date"

```sql
SELECT customer_id, customer_name, region
FROM   dim_customer
WHERE  DATE '2026-02-01' >= effective_from
  AND  DATE '2026-02-01' <  effective_to;
```

Returns Carla in **North**. This is the superpower SCD1 cannot give you.

#### SCD2 implementation SQL

```sql
-- Step 1: expire rows whose tracked attributes changed
UPDATE dim_customer AS d
SET    effective_to = DATE '2026-03-01',
       is_current   = FALSE
FROM   stg_customers AS s
WHERE  d.customer_id = s.customer_id
  AND  d.is_current  = TRUE
  AND  (d.customer_name IS DISTINCT FROM s.customer_name
     OR d.region        IS DISTINCT FROM s.region);

-- Step 2: insert the new current version for those changed customers
INSERT INTO dim_customer
    (customer_id, customer_name, region, customer_created_date,
     effective_from, effective_to, is_current)
SELECT s.customer_id, s.customer_name, s.region, s.customer_created_date,
       DATE '2026-03-01', DATE '9999-12-31', TRUE
FROM   stg_customers s
JOIN   dim_customer  d ON d.customer_id = s.customer_id
WHERE  d.is_current   = FALSE
  AND  d.effective_to = DATE '2026-03-01';

-- Step 3: insert brand-new customers never seen before
INSERT INTO dim_customer
    (customer_id, customer_name, region, customer_created_date,
     effective_from, effective_to, is_current)
SELECT s.customer_id, s.customer_name, s.region, s.customer_created_date,
       DATE '2026-03-01', DATE '9999-12-31', TRUE
FROM   stg_customers s
WHERE  NOT EXISTS (SELECT 1 FROM dim_customer d WHERE d.customer_id = s.customer_id);
```

#### PySpark sketch of the same logic

```python
from pyspark.sql import SparkSession, functions as F

spark = SparkSession.builder.appName("scd2_dim_customer").getOrCreate()

RUN_DATE = "2026-03-01"
HIGH_DATE = "9999-12-31"
TRACKED = ["customer_name", "region"]

dim = spark.read.parquet("data/gold/dim_customer")
stg = spark.read.parquet("data/silver/customers")

current = dim.filter(F.col("is_current"))
history = dim.filter(~F.col("is_current"))

joined = current.alias("d").join(stg.alias("s"), on="customer_id", how="full_outer")

changed_cond = F.expr(
    " OR ".join(f"NOT (d.{c} <=> s.{c})" for c in TRACKED)
)

# rows whose tracked attributes changed -> expire the old version
expired = (
    joined.filter(F.col("d.customer_id").isNotNull() & F.col("s.customer_id").isNotNull() & changed_cond)
          .select("d.*")
          .withColumn("effective_to", F.lit(RUN_DATE).cast("date"))
          .withColumn("is_current", F.lit(False))
)

# unchanged current rows pass through untouched
unchanged = (
    joined.filter(F.col("d.customer_id").isNotNull() & F.col("s.customer_id").isNotNull() & ~changed_cond)
          .select("d.*")
)

# new versions for changed customers + brand-new customers
new_versions = (
    joined.filter(F.col("s.customer_id").isNotNull() & (F.col("d.customer_id").isNull() | changed_cond))
          .select("s.customer_id", "s.customer_name", "s.region", "s.customer_created_date")
          .withColumn("effective_from", F.lit(RUN_DATE).cast("date"))
          .withColumn("effective_to", F.lit(HIGH_DATE).cast("date"))
          .withColumn("is_current", F.lit(True))
)

max_key = dim.agg(F.coalesce(F.max("customer_key"), F.lit(0))).first()[0]

window = __import__("pyspark.sql.window", fromlist=["Window"]).Window.orderBy("customer_id")
new_versions = new_versions.withColumn(
    "customer_key", F.row_number().over(window) + F.lit(max_key)
)

result = (
    history.unionByName(expired)
           .unionByName(unchanged)
           .unionByName(new_versions, allowMissingColumns=True)
)

result.write.mode("overwrite").parquet("data/gold/dim_customer")
```

> In a Delta Lake environment you would express steps 1–3 as a single `MERGE INTO`. The three-step form above is written to work with plain Parquet so it runs locally without extra dependencies.

#### Pros and cons

| Pros | Cons |
|---|---|
| Full, auditable history | Dimension grows with every change |
| Past reports are reproducible | Fact loads must look up the *correct version*, not just the entity |
| Supports point-in-time ("as-of") analysis | Every query must remember `is_current = TRUE` or it double-counts |
| Required for most regulated reporting | More complex ETL and more tests |

### 10.5 Choosing attribute by attribute

SCD type is chosen **per column**, not per table. A realistic policy for `dim_customer`:

| Attribute | SCD type | Rationale |
|-----------|----------|-----------|
| `customer_name` | 1 | Name corrections are fixes, not history |
| `region` | 2 | Drives revenue attribution; history matters |
| `customer_created_date` | 0 (never changes) | Immutable fact about the entity |
| `email` | 1 | Current contact detail; old ones aren't analytically useful |
| `customer_segment` | 2 | Segment migration is a business event worth tracking |

> **Type 0** means "retain original, never update". **Type 3** adds a `previous_region` column to track exactly one prior value — rarely used, because it only remembers one step back. Types 4 and 6 are hybrids (mini-dimensions and "Type 1 + 2 + 3" combined). Know that they exist; reach for 1 and 2 in practice.

### 10.6 The SCD2 trap you will hit

If you forget `is_current = TRUE` when joining a dimension for a *current-state* report, every changed customer contributes twice:

```sql
-- WRONG: joins every historical version
SELECT c.region, COUNT(*) FROM dim_customer c GROUP BY c.region;
-- North: 3  (Aisha + old Carla + ...), East: 1  -> Carla counted twice overall

-- RIGHT
SELECT c.region, COUNT(*) FROM dim_customer c WHERE c.is_current GROUP BY c.region;
```

Note this trap applies to *dimension-only* queries. Fact joins are safe because `fact_sales.customer_key` points at exactly one version.

---

## 11. Gold layer DDL (PostgreSQL-compatible)

These definitions mirror `sql/04_star_schema_ddl.sql` in `../customer-sales-lakehouse`. They use standard PostgreSQL syntax so you can create them in a local Postgres or an in-memory DuckDB session and actually test them.

### 11.1 `dim_customer`

```sql
DROP TABLE IF EXISTS fact_sales;
DROP TABLE IF EXISTS dim_customer;

CREATE TABLE dim_customer (
    customer_key           INTEGER      NOT NULL,
    customer_id            INTEGER,                      -- natural key; NULL only for the Unknown member
    customer_name          VARCHAR(200) NOT NULL,
    region                 VARCHAR(50)  NOT NULL,
    customer_created_date  DATE,
    effective_from         DATE         NOT NULL DEFAULT DATE '1900-01-01',
    effective_to           DATE         NOT NULL DEFAULT DATE '9999-12-31',
    is_current             BOOLEAN      NOT NULL DEFAULT TRUE,
    load_timestamp         TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT pk_dim_customer       PRIMARY KEY (customer_key),
    CONSTRAINT ck_dim_customer_dates CHECK (effective_from < effective_to),
    CONSTRAINT ck_dim_customer_region
        CHECK (region IN ('North', 'South', 'East', 'West', 'Unknown'))
);

-- Exactly one current row per natural key (the Unknown member has NULL customer_id,
-- and NULLs are excluded from unique indexes in PostgreSQL).
CREATE UNIQUE INDEX ux_dim_customer_current
    ON dim_customer (customer_id)
    WHERE is_current;

-- Supports as-of lookups
CREATE INDEX ix_dim_customer_asof
    ON dim_customer (customer_id, effective_from, effective_to);

COMMENT ON TABLE  dim_customer IS
    'Customer dimension. SCD2 on region; SCD1 on customer_name.';
COMMENT ON COLUMN dim_customer.customer_key IS
    'Surrogate key. -1 reserved for the Unknown member.';
```

### 11.2 `dim_product`

```sql
DROP TABLE IF EXISTS dim_product;

CREATE TABLE dim_product (
    product_key      INTEGER       NOT NULL,
    product_id       INTEGER,                              -- natural key
    product_name     VARCHAR(200)  NOT NULL,
    category         VARCHAR(100)  NOT NULL,
    unit_price       NUMERIC(12,2) NOT NULL,
    effective_from   DATE          NOT NULL DEFAULT DATE '1900-01-01',
    effective_to     DATE          NOT NULL DEFAULT DATE '9999-12-31',
    is_current       BOOLEAN       NOT NULL DEFAULT TRUE,
    load_timestamp   TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT pk_dim_product              PRIMARY KEY (product_key),
    CONSTRAINT ck_dim_product_price        CHECK (unit_price >= 0),
    CONSTRAINT ck_dim_product_dates        CHECK (effective_from < effective_to)
);

CREATE UNIQUE INDEX ux_dim_product_current
    ON dim_product (product_id)
    WHERE is_current;
```

Populated from `products.csv`:

| product_key | product_id | product_name | category | unit_price |
|---|---|---|---|---|
| 1 | 1001 | Laptop Bag | Accessories | 50.00 |
| 2 | 1002 | Wireless Mouse | Accessories | 25.00 |
| 3 | 1003 | Monitor | Hardware | 200.00 |
| 4 | 1004 | Keyboard | Accessories | 75.00 |

### 11.3 `dim_date`

```sql
DROP TABLE IF EXISTS dim_date;

CREATE TABLE dim_date (
    date_key        INTEGER     NOT NULL,   -- YYYYMMDD, e.g. 20260105
    full_date       DATE        NOT NULL,
    day_of_month    SMALLINT    NOT NULL,
    day_of_week     SMALLINT    NOT NULL,   -- 1 = Monday ... 7 = Sunday
    day_name        VARCHAR(10) NOT NULL,
    week_of_year    SMALLINT    NOT NULL,
    month_num       SMALLINT    NOT NULL,
    month_name      VARCHAR(10) NOT NULL,
    quarter_num     SMALLINT    NOT NULL,
    year_num        SMALLINT    NOT NULL,
    is_weekend      BOOLEAN     NOT NULL,
    month_start_date DATE       NOT NULL,
    month_end_date   DATE       NOT NULL,
    CONSTRAINT pk_dim_date       PRIMARY KEY (date_key),
    CONSTRAINT uq_dim_date_date  UNIQUE (full_date),
    CONSTRAINT ck_dim_date_key   CHECK (date_key = CAST(TO_CHAR(full_date, 'YYYYMMDD') AS INTEGER))
);

-- Generate the calendar (no source file needed)
INSERT INTO dim_date
SELECT
    CAST(TO_CHAR(d, 'YYYYMMDD') AS INTEGER)       AS date_key,
    d::date                                        AS full_date,
    EXTRACT(DAY     FROM d)::smallint              AS day_of_month,
    EXTRACT(ISODOW  FROM d)::smallint              AS day_of_week,
    TRIM(TO_CHAR(d, 'Day'))                        AS day_name,
    EXTRACT(WEEK    FROM d)::smallint              AS week_of_year,
    EXTRACT(MONTH   FROM d)::smallint              AS month_num,
    TRIM(TO_CHAR(d, 'Month'))                      AS month_name,
    EXTRACT(QUARTER FROM d)::smallint              AS quarter_num,
    EXTRACT(YEAR    FROM d)::smallint              AS year_num,
    EXTRACT(ISODOW  FROM d) IN (6, 7)              AS is_weekend,
    DATE_TRUNC('month', d)::date                   AS month_start_date,
    (DATE_TRUNC('month', d) + INTERVAL '1 month - 1 day')::date AS month_end_date
FROM GENERATE_SERIES(DATE '2025-01-01', DATE '2027-12-31', INTERVAL '1 day') AS d;
```

Sample rows relevant to our orders:

| date_key | full_date | day_name | month_name | quarter_num | year_num | is_weekend |
|---|---|---|---|---|---|---|
| 20260105 | 2026-01-05 | Monday | January | 1 | 2026 | false |
| 20260110 | 2026-01-10 | Saturday | January | 1 | 2026 | true |
| 20260201 | 2026-02-01 | Sunday | February | 1 | 2026 | true |
| 20260215 | 2026-02-15 | Sunday | February | 1 | 2026 | true |

> **Why an integer `date_key` and not just the date?** It is compact, it sorts naturally, it partitions readably in the file system (`date_key=20260105`), and it makes an "unknown date" member trivial (`-1` or `19000101`). Keep `full_date` alongside it so date arithmetic stays easy.

### 11.4 `fact_sales`

```sql
DROP TABLE IF EXISTS fact_sales;

CREATE TABLE fact_sales (
    sales_key       BIGINT        NOT NULL,
    order_id        INTEGER       NOT NULL,   -- degenerate dimension (natural key kept for traceability)
    customer_key    INTEGER       NOT NULL,
    product_key     INTEGER       NOT NULL,
    date_key        INTEGER       NOT NULL,
    quantity        INTEGER       NOT NULL,
    amount          NUMERIC(12,2) NOT NULL,
    order_status    VARCHAR(20)   NOT NULL,
    source_updated_at TIMESTAMP,
    load_timestamp  TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT pk_fact_sales           PRIMARY KEY (sales_key),
    CONSTRAINT uq_fact_sales_grain     UNIQUE (order_id, product_key),
    CONSTRAINT fk_fact_sales_customer  FOREIGN KEY (customer_key) REFERENCES dim_customer (customer_key),
    CONSTRAINT fk_fact_sales_product   FOREIGN KEY (product_key)  REFERENCES dim_product  (product_key),
    CONSTRAINT fk_fact_sales_date      FOREIGN KEY (date_key)     REFERENCES dim_date     (date_key),
    CONSTRAINT ck_fact_sales_qty       CHECK (quantity > 0),
    CONSTRAINT ck_fact_sales_amount    CHECK (amount >= 0),
    CONSTRAINT ck_fact_sales_status    CHECK (order_status = 'Completed')
);

CREATE INDEX ix_fact_sales_date     ON fact_sales (date_key);
CREATE INDEX ix_fact_sales_customer ON fact_sales (customer_key);
CREATE INDEX ix_fact_sales_product  ON fact_sales (product_key);

COMMENT ON TABLE fact_sales IS
    'Grain: one row per valid, de-duplicated, Completed order line. '
    'Sample-data recognised revenue = 525.00.';
```

Note how much of the business rule is encoded as constraints:

- `uq_fact_sales_grain` makes the duplicate order 107 **impossible to load twice**. The database itself enforces the grain.
- `ck_fact_sales_status` makes orders 103 and 105 **impossible to load**.
- The three foreign keys make order 106 (customer 99) **impossible to load**.
- `amount NOT NULL` makes order 105's missing amount **impossible to load**.

> **Design principle:** encode every rule you can in the schema. A constraint is a test that runs on every single insert, forever, with no maintenance. It is strictly stronger than a test that runs nightly.

### 11.5 Populating the fact table

```sql
INSERT INTO fact_sales
    (sales_key, order_id, customer_key, product_key, date_key,
     quantity, amount, order_status, source_updated_at)
WITH deduped AS (
    SELECT DISTINCT
           order_id, customer_id, product_id, order_date,
           quantity, amount, status, updated_at
    FROM   silver_orders
),
valid AS (
    SELECT d.*
    FROM   deduped d
    JOIN   dim_customer c ON c.customer_id = d.customer_id
                         AND d.order_date >= c.effective_from
                         AND d.order_date <  c.effective_to
    JOIN   dim_product  p ON p.product_id  = d.product_id
                         AND p.is_current
    WHERE  d.status = 'Completed'
      AND  d.amount IS NOT NULL
)
SELECT
    ROW_NUMBER() OVER (ORDER BY v.order_id)             AS sales_key,
    v.order_id,
    c.customer_key,
    p.product_key,
    CAST(TO_CHAR(v.order_date, 'YYYYMMDD') AS INTEGER)  AS date_key,
    v.quantity,
    v.amount,
    v.status,
    v.updated_at
FROM valid v
JOIN dim_customer c ON c.customer_id = v.customer_id
                   AND v.order_date >= c.effective_from
                   AND v.order_date <  c.effective_to
JOIN dim_product  p ON p.product_id  = v.product_id
                   AND p.is_current;
```

Verify:

```sql
SELECT COUNT(*) AS row_count, SUM(amount) AS recognised_revenue FROM fact_sales;
```

| row_count | recognised_revenue |
|---|---|
| 4 | 525.00 |

---

## 12. Business rules for this model

Write these down. Put them in `docs/` in the project. Test every one of them.

| # | Rule | Where enforced | Test |
|---|------|----------------|------|
| BR-01 | `fact_sales` contains only orders with `status = 'Completed'` | silver filter + `ck_fact_sales_status` | `SELECT COUNT(*) FROM fact_sales WHERE order_status <> 'Completed'` → 0 |
| BR-02 | Exact-duplicate order rows are collapsed to one | silver `DISTINCT` + `uq_fact_sales_grain` | `GROUP BY order_id, product_key HAVING COUNT(*) > 1` → 0 rows |
| BR-03 | `amount` is mandatory for a sale | `NOT NULL` | `SELECT COUNT(*) FROM fact_sales WHERE amount IS NULL` → 0 |
| BR-04 | Every `customer_key` resolves to a real customer | FK `fk_fact_sales_customer` | orphan check → 0 rows |
| BR-05 | Every `product_key` resolves to a real product | FK `fk_fact_sales_product` | orphan check → 0 rows |
| BR-06 | Valid statuses are exactly `Completed`, `Cancelled`, `Pending` | silver check | `SELECT DISTINCT status FROM silver_orders` ⊆ the set |
| BR-07 | `quantity > 0` and `amount >= 0` | `CHECK` constraints | constraint violation count → 0 |
| BR-08 | **Recognised revenue for the sample is exactly 525.00** | end-to-end | `SELECT SUM(amount) FROM fact_sales` → `525.00` |
| BR-09 | Row count for the sample is exactly 4 | end-to-end | `SELECT COUNT(*) FROM fact_sales` → `4` |
| BR-10 | Exactly one `is_current = TRUE` row per `customer_id` | partial unique index | duplicate-current check → 0 rows |
| BR-11 | Rejected rows are quarantined and counted, never silently dropped | quality checks notebook | reject count = 4 (103, 105, 106, dup 107) |
| BR-12 | Loading twice produces identical results (idempotency) | pipeline design | run twice, `SUM(amount)` still `525.00` |

### 12.1 The rejection ledger

"Silently dropping bad rows" is the single most damaging habit in data engineering. Every excluded row should be *counted and explained*:

| order_id | rejection_reason | amount_excluded |
|---|---|---|
| 103 | status_not_completed (Cancelled) | 50.00 |
| 105 | status_not_completed (Pending) + missing_amount | *(null)* |
| 106 | invalid_customer_reference (customer_id 99) | 50.00 |
| 107 | exact_duplicate_row | 200.00 |

```sql
SELECT rejection_reason, COUNT(*) AS rows_rejected
FROM   quarantine_orders
GROUP  BY rejection_reason
ORDER  BY rows_rejected DESC;
```

If order 106 is a *genuine* sale whose customer master record just hasn't arrived yet, the ledger is how the business finds out — and 50.00 of real revenue gets recovered once the master data is fixed.

---

## 13. Worked example: one order row's journey into `fact_sales`

Let's follow **order 107** end to end. It's the most interesting one because it's a duplicate.

### Step 1 — Raw (bronze)

Two byte-identical lines land in `data/raw/orders.csv`:

```
107,1,1003,2026-02-15,1,200.00,Completed,2026-02-15 08:00:00
107,1,1003,2026-02-15,1,200.00,Completed,2026-02-15 08:00:00
```

Bronze ingests them **as-is**, preserving both, adding only lineage columns:

| order_id | customer_id | product_id | order_date | quantity | amount | status | updated_at | _ingest_file | _ingest_ts |
|---|---|---|---|---|---|---|---|---|---|
| 107 | 1 | 1003 | 2026-02-15 | 1 | 200.00 | Completed | 2026-02-15 08:00:00 | orders.csv | 2026-03-01 06:00:01 |
| 107 | 1 | 1003 | 2026-02-15 | 1 | 200.00 | Completed | 2026-02-15 08:00:00 | orders.csv | 2026-03-01 06:00:01 |

> Bronze never deletes. If you later discover your dedup logic was wrong, you re-run from bronze. That's the whole point.

### Step 2 — Silver: type, cleanse, de-duplicate

Types are applied: `order_date` → `DATE`, `amount` → `DECIMAL(12,2)`, `updated_at` → `TIMESTAMP`.

Because the two rows are byte-identical including `updated_at`, deduplication is unambiguous:

```python
from pyspark.sql import Window, functions as F

w = Window.partitionBy("order_id", "product_id").orderBy(
    F.col("updated_at").desc_nulls_last(),
    F.col("_ingest_ts").desc_nulls_last(),
)

silver_orders = (
    bronze_orders
      .withColumn("_rn", F.row_number().over(w))
      .filter(F.col("_rn") == 1)
      .drop("_rn")
)
```

One row survives:

| order_id | customer_id | product_id | order_date | quantity | amount | status | updated_at |
|---|---|---|---|---|---|---|---|
| 107 | 1 | 1003 | 2026-02-15 | 1 | 200.00 | Completed | 2026-02-15 08:00:00 |

> **Why `row_number` over `dropDuplicates`?** With identical rows either works. But real systems emit *updated* versions of the same key with a later `updated_at`. `row_number` ordered by `updated_at DESC` gives you "latest wins" semantics, which is what you almost always want. It generalises; `dropDuplicates` doesn't.

### Step 3 — Quality gate

| Check | Result |
|---|---|
| `order_id` not null | ✅ |
| `amount` not null | ✅ (200.00) |
| `customer_id = 1` exists in silver customers | ✅ |
| `product_id = 1003` exists in silver products | ✅ |
| `status` in (Completed, Cancelled, Pending) | ✅ |
| `quantity > 0` | ✅ |
| no duplicate at grain | ✅ (deduped) |

Row passes. Order 107 is a valid, Completed sale.

### Step 4 — Surrogate key lookup: customer

Look up `customer_id = 1` in `dim_customer`, choosing the version effective on `order_date = 2026-02-15`:

```sql
SELECT customer_key
FROM   dim_customer
WHERE  customer_id = 1
  AND  DATE '2026-02-15' >= effective_from
  AND  DATE '2026-02-15' <  effective_to;
```

| customer_key |
|---|
| 1 |

> Aisha Khan has only one version, so this is easy. But note the **as-of** join, not `is_current`. If this were Carla's February order, the as-of join correctly returns the *North* version (`customer_key = 3`), not the current East version (`customer_key = 5`). Using `is_current` here would silently rewrite history — a classic and very expensive bug.

### Step 5 — Surrogate key lookup: product

```sql
SELECT product_key
FROM   dim_product
WHERE  product_id = 1003
  AND  DATE '2026-02-15' >= effective_from
  AND  DATE '2026-02-15' <  effective_to;
```

| product_key |
|---|
| 3 |

(`product_id 1003` = Monitor, `unit_price` 200.00.)

### Step 6 — Date key derivation

`order_date = 2026-02-15` → `date_key = 20260215`. No lookup needed; it's a deterministic transform. But you *should* still verify the key exists in `dim_date`, otherwise the FK will fail:

```sql
SELECT date_key, full_date, day_name, month_name, year_num
FROM   dim_date WHERE date_key = 20260215;
```

| date_key | full_date | day_name | month_name | year_num |
|---|---|---|---|---|
| 20260215 | 2026-02-15 | Sunday | February | 2026 |

### Step 7 — Assemble the fact row

| Column | Value | Source |
|--------|-------|--------|
| `sales_key` | 4 | generated |
| `order_id` | 107 | degenerate dimension from source |
| `customer_key` | 1 | as-of lookup on `dim_customer` |
| `product_key` | 3 | as-of lookup on `dim_product` |
| `date_key` | 20260215 | derived from `order_date` |
| `quantity` | 1 | source measure |
| `amount` | 200.00 | source measure |
| `order_status` | Completed | source, filtered |
| `source_updated_at` | 2026-02-15 08:00:00 | source lineage |
| `load_timestamp` | 2026-03-01 06:05:12 | pipeline lineage |

### Step 8 — Final state of `fact_sales`

| sales_key | order_id | customer_key | product_key | date_key | quantity | amount | order_status |
|---|---|---|---|---|---|---|---|
| 1 | 101 | 1 | 3 | 20260105 | 1 | 200.00 | Completed |
| 2 | 102 | 1 | 2 | 20260110 | 2 | 50.00 | Completed |
| 3 | 104 | 3 | 4 | 20260201 | 1 | 75.00 | Completed |
| 4 | 107 | 1 | 3 | 20260215 | 1 | 200.00 | Completed |

```sql
SELECT COUNT(*) AS rows, SUM(amount) AS revenue FROM fact_sales;
```

| rows | revenue |
|---|---|
| 4 | **525.00** |

### Step 9 — Now actually use the model

```sql
-- Revenue by category
SELECT p.category, SUM(f.amount) AS revenue, SUM(f.quantity) AS units
FROM fact_sales  f
JOIN dim_product p ON p.product_key = f.product_key
GROUP BY p.category
ORDER BY revenue DESC;
```

| category | revenue | units |
|---|---|---|
| Hardware | 400.00 | 2 |
| Accessories | 125.00 | 3 |

```sql
-- Revenue by region by month (two joins — compare to the four-join 3NF version in §3.5)
SELECT c.region, d.year_num, d.month_name, SUM(f.amount) AS revenue
FROM fact_sales   f
JOIN dim_customer c ON c.customer_key = f.customer_key
JOIN dim_date     d ON d.date_key     = f.date_key
GROUP BY c.region, d.year_num, d.month_num, d.month_name
ORDER BY c.region, d.year_num, d.month_num;
```

| region | year_num | month_name | revenue |
|---|---|---|---|
| North | 2026 | January | 250.00 |
| North | 2026 | February | 275.00 |

`250.00 + 275.00 = 525.00`. ✅ Consistent at every cut.

```sql
-- Revenue by customer
SELECT c.customer_name, SUM(f.amount) AS revenue, COUNT(*) AS orders
FROM fact_sales   f
JOIN dim_customer c ON c.customer_key = f.customer_key
GROUP BY c.customer_name
ORDER BY revenue DESC;
```

| customer_name | revenue | orders |
|---|---|---|
| Aisha Khan | 450.00 | 3 |
| Carla Jones | 75.00 | 1 |

`450.00 + 75.00 = 525.00`. ✅

> **This is the acid test of a good model.** Slice it any way you like — by category, region, month, customer — and the total always reconciles to 525.00. If a slice doesn't add up, you have a grain problem, a fan-out join, or a duplicate.

---

## 14. Loading patterns in PySpark

A compact, dependency-free version of the gold build you can run locally.

```python
"""Build the gold star schema from silver Parquet. Python 3.11, PySpark, local mode."""

from pyspark.sql import SparkSession, Window, functions as F

spark = (
    SparkSession.builder
    .appName("gold_star_schema")
    .master("local[*]")
    .config("spark.sql.shuffle.partitions", "4")
    .getOrCreate()
)

SILVER = "data/silver"
GOLD = "data/gold"
VALID_STATUSES = ["Completed", "Cancelled", "Pending"]


def build_dim_customer() -> None:
    customers = spark.read.parquet(f"{SILVER}/customers")
    w = Window.orderBy("customer_id")
    dim = (
        customers
        .select("customer_id", "customer_name", "region", "customer_created_date")
        .withColumn("customer_key", F.row_number().over(w))
        .withColumn("effective_from", F.col("customer_created_date"))
        .withColumn("effective_to", F.lit("9999-12-31").cast("date"))
        .withColumn("is_current", F.lit(True))
        .withColumn("load_timestamp", F.current_timestamp())
    )
    unknown = spark.createDataFrame(
        [(-1, None, "Unknown", "Unknown", None)],
        "customer_key INT, customer_id INT, customer_name STRING, "
        "region STRING, customer_created_date DATE",
    ).withColumn("effective_from", F.lit("1900-01-01").cast("date")) \
     .withColumn("effective_to", F.lit("9999-12-31").cast("date")) \
     .withColumn("is_current", F.lit(True)) \
     .withColumn("load_timestamp", F.current_timestamp())

    dim.unionByName(unknown).write.mode("overwrite").parquet(f"{GOLD}/dim_customer")


def build_dim_product() -> None:
    products = spark.read.parquet(f"{SILVER}/products")
    w = Window.orderBy("product_id")
    (
        products
        .select("product_id", "product_name", "category", "unit_price")
        .withColumn("product_key", F.row_number().over(w))
        .withColumn("effective_from", F.lit("1900-01-01").cast("date"))
        .withColumn("effective_to", F.lit("9999-12-31").cast("date"))
        .withColumn("is_current", F.lit(True))
        .withColumn("load_timestamp", F.current_timestamp())
        .write.mode("overwrite").parquet(f"{GOLD}/dim_product")
    )


def build_dim_date(start: str = "2025-01-01", end: str = "2027-12-31") -> None:
    dates = (
        spark.sql(f"SELECT sequence(to_date('{start}'), to_date('{end}'), interval 1 day) AS d")
        .select(F.explode("d").alias("full_date"))
    )
    (
        dates
        .withColumn("date_key", F.date_format("full_date", "yyyyMMdd").cast("int"))
        .withColumn("day_of_month", F.dayofmonth("full_date"))
        .withColumn("day_name", F.date_format("full_date", "EEEE"))
        .withColumn("month_num", F.month("full_date"))
        .withColumn("month_name", F.date_format("full_date", "MMMM"))
        .withColumn("quarter_num", F.quarter("full_date"))
        .withColumn("year_num", F.year("full_date"))
        .withColumn("is_weekend", F.dayofweek("full_date").isin(1, 7))
        .write.mode("overwrite").parquet(f"{GOLD}/dim_date")
    )


def build_fact_sales() -> None:
    orders = spark.read.parquet(f"{SILVER}/orders")
    dim_customer = spark.read.parquet(f"{GOLD}/dim_customer")
    dim_product = spark.read.parquet(f"{GOLD}/dim_product")

    dedup_w = Window.partitionBy("order_id", "product_id").orderBy(
        F.col("updated_at").desc_nulls_last()
    )
    deduped = (
        orders.withColumn("_rn", F.row_number().over(dedup_w))
              .filter(F.col("_rn") == 1)
              .drop("_rn")
    )

    completed = deduped.filter(
        (F.col("status") == "Completed")
        & F.col("amount").isNotNull()
        & (F.col("quantity") > 0)
    )

    joined = (
        completed.alias("o")
        .join(
            dim_customer.alias("c"),
            (F.col("o.customer_id") == F.col("c.customer_id"))
            & (F.col("o.order_date") >= F.col("c.effective_from"))
            & (F.col("o.order_date") < F.col("c.effective_to")),
            "inner",  # inner join drops order 106 (customer 99 does not exist)
        )
        .join(
            dim_product.alias("p"),
            (F.col("o.product_id") == F.col("p.product_id")) & F.col("p.is_current"),
            "inner",
        )
    )

    key_w = Window.orderBy("o.order_id")
    fact = (
        joined
        .select(
            F.row_number().over(key_w).alias("sales_key"),
            F.col("o.order_id"),
            F.col("c.customer_key"),
            F.col("p.product_key"),
            F.date_format("o.order_date", "yyyyMMdd").cast("int").alias("date_key"),
            F.col("o.quantity"),
            F.col("o.amount"),
            F.col("o.status").alias("order_status"),
            F.col("o.updated_at").alias("source_updated_at"),
            F.current_timestamp().alias("load_timestamp"),
        )
    )
    fact.write.mode("overwrite").parquet(f"{GOLD}/fact_sales")


def assert_business_rules() -> None:
    fact = spark.read.parquet(f"{GOLD}/fact_sales")

    row_count = fact.count()
    revenue = fact.agg(F.sum("amount")).first()[0]
    dupes = (
        fact.groupBy("order_id", "product_key").count().filter(F.col("count") > 1).count()
    )

    assert row_count == 4, f"BR-09 failed: expected 4 rows, got {row_count}"
    assert float(revenue) == 525.00, f"BR-08 failed: expected 525.00, got {revenue}"
    assert dupes == 0, f"BR-02 failed: {dupes} duplicate grain keys"
    print(f"All business rules passed. rows={row_count} revenue={revenue}")


if __name__ == "__main__":
    build_dim_customer()
    build_dim_product()
    build_dim_date()
    build_fact_sales()
    assert_business_rules()
```

Expected console output:

```
All business rules passed. rows=4 revenue=525.00
```

### 14.1 Full reload vs incremental

| Strategy | How | When to use | Risk |
|---|---|---|---|
| **Full reload** | truncate gold, rebuild from silver | small data, simple, trivially idempotent | expensive at scale |
| **Incremental append** | append only new `order_id`s | append-only sources | duplicates if a key is re-sent |
| **Merge / upsert** | `MERGE` on the grain key | sources that update rows | needs a reliable change marker (`updated_at`) |
| **Delete-insert by partition** | drop and rewrite affected `date_key` partitions | late-arriving facts | must know the affected window |

This project uses **full reload** for gold, because the dataset is tiny and full reload is the most teachable idempotent pattern. `orchestration/pipeline_design.md` discusses what would change at scale.

### 14.2 Idempotency

> Running the pipeline twice must produce exactly the same result as running it once.

Test it:

```bash
python notebooks/05_gold_dimensions.py
python notebooks/06_gold_facts.py
python notebooks/05_gold_dimensions.py
python notebooks/06_gold_facts.py
# revenue must still be 525.00, row count must still be 4
```

Non-idempotent pipelines are the #1 cause of "the numbers doubled overnight" incidents.

---

## 15. Common modelling mistakes

| # | Mistake | Symptom | Fix |
|---|---|---|---|
| 1 | Grain never declared | Nobody can say why the total is wrong | Write the one-sentence grain in `docs/` and test it |
| 2 | Mixed grain in one fact table | Sums are inflated, inconsistently | Split into separate fact tables at separate grains |
| 3 | Storing ratios/percentages as measures | Averages of averages; wrong by double digits | Store additive numerator and denominator |
| 4 | Using natural keys as dimension PKs | SCD2 becomes impossible | Introduce surrogate keys |
| 5 | Joining on `is_current` when loading historical facts | History silently rewritten | Use as-of joins on `effective_from`/`effective_to` |
| 6 | Forgetting `is_current` in dimension-only reports | Entities counted multiple times | Always filter current versions |
| 7 | NULL foreign keys in facts | Rows disappear from inner-join reports | Unknown member (`-1`) or explicit quarantine |
| 8 | Silently dropping bad rows | Revenue quietly understated | Quarantine table + reject ledger |
| 9 | Pre-aggregating too early | Can't drill down later | Store atomic grain; add aggregates as extra tables |
| 10 | Denormalizing the fact table | Enormous storage, slow writes | Keep facts narrow; attributes live in dimensions |
| 11 | No date dimension (using raw dates) | Fiscal calendars and holidays are impossible | Build `dim_date` once |
| 12 | Non-idempotent loads | Numbers double on re-run | Full reload or merge on the grain key |
| 13 | Business rules only in code comments | Rules drift between pipeline and report | Encode as constraints + tests |
| 14 | Dimensions built after facts | FK lookups fail or create unknowns | Load dimensions first, always |
| 15 | Snowflaking by reflex | Analysts write 7-join queries | Default to star |

---

## 16. How this maps to the project

| Concept in this guide | Where it lives in `../customer-sales-lakehouse` |
|---|---|
| Raw source files | `data/raw/customers.csv`, `products.csv`, `orders.csv` |
| Bronze ingestion (preserve as-is) | `notebooks/01_bronze_ingestion.py`, `src/ingestion.py` |
| Silver cleansing, typing, dedup | `notebooks/03_silver_transformations.py`, `src/transformations.py` |
| Quality gate / reject ledger | `notebooks/07_data_quality_checks.py`, `src/quality_checks.py`, `sql/02_data_quality_checks.sql` |
| Dimension builds (SCD logic) | `notebooks/05_gold_dimensions.py` |
| Fact build + surrogate lookups | `notebooks/06_gold_facts.py` |
| Star schema DDL | `sql/04_star_schema_ddl.sql` |
| Exploration of the raw/silver data | `sql/01_exploration_queries.sql` |
| Reporting queries (must total 525.00) | `sql/03_reporting_queries.sql` |
| Environment-specific paths | `config/dev.yml`, `config/prod.yml` |
| Run-order and dependency design | `orchestration/pipeline_design.md` |
| Automated assertions on the model | `tests/` |
| Narrative documentation | `docs/` |

Suggested order of work in the project after reading this guide:

1. Read `sql/04_star_schema_ddl.sql` and check every constraint against §11 and §12.
2. Run `notebooks/05_gold_dimensions.py` then `notebooks/06_gold_facts.py`.
3. Run `sql/03_reporting_queries.sql` and confirm **525.00** appears in every total.
4. Add an SCD2 column to `dim_customer` and re-run — confirm the total is still 525.00.
5. Add a test in `tests/` for every rule in the §12 table.

---

## 17. Exercises

No answers are provided — work them out against the sample data and verify with SQL or PySpark.

1. Write the grain statement for a hypothetical `fact_order_fulfilment` table in one sentence, then list which type of fact table it is and why.
2. Take the `orders_flat` table from §3.0 and normalize it to 3NF on paper, but this time assume an order can have a shipping address *and* a billing address. Show every table and key.
3. Classify each of these as additive, semi-additive, or non-additive, and justify: `quantity`, `amount`, `unit_price`, `discount_percent`, `number_of_open_orders_at_month_end`, `customer_lifetime_value`.
4. Order 106 references `customer_id = 99`. Rewrite §11.5's fact load so that instead of rejecting the row, it routes it to the Unknown member (`customer_key = -1`). State the new recognised revenue and explain, in writing, why the canonical 525.00 rule means this variant must be a *separate* reporting measure rather than a redefinition.
5. Carla Jones (customer 3) moves from North to East on 2026-03-01, and then on 2026-06-15 she moves again to South. Draw the complete `dim_customer` SCD2 table after both changes, including all `effective_from`, `effective_to`, `is_current` and surrogate key values.
6. Using the table you built in exercise 5, write the SQL that answers "what region was Carla in on 2026-04-01?" without using `is_current`.
7. Add a `dim_region` table and convert the star schema in §9 into a snowflake. Write the "revenue by region by month" query against both versions and count the joins in each. Which would you ship, and what would change your mind?
8. Write a SQL query that proves there are no orphan foreign keys in `fact_sales` for all three dimensions, returning zero rows when the model is correct.
9. Design an aggregate table `agg_monthly_region_sales`. State its grain, write its DDL, write the SQL that populates it from `fact_sales`, and write the assertion that proves its total still reconciles to 525.00.
10. The business asks for "average order value by region". Explain why you must not store `avg_order_value` in `fact_sales`, then write the correct query. Compute the expected answer by hand for the sample data before running it.
11. Order 105 is `Pending` with a missing `amount`. Suppose it later completes on 2026-03-10 with `amount = 25.00`. Describe exactly what your pipeline must do — in bronze, silver and gold — for the new recognised revenue to become 550.00, and write the test that would have caught it if the duplicate-detection logic broke in the process.
12. Add a `product_price_change` scenario: on 2026-04-01 the Monitor (product 1003) rises from 200.00 to 220.00. Decide SCD1 vs SCD2 for `dim_product.unit_price`, justify the choice in two sentences, and show the resulting `dim_product` rows.
13. Write a `CHECK` constraint or test that enforces "exactly one `is_current = TRUE` row per `customer_id`" in a database that does **not** support partial unique indexes.
14. Explain, with a concrete example from the sample data, what would go wrong if `fact_sales` were built at order-header grain instead of order-line grain, assuming order 108 from §3.0 (Monitor + Keyboard) were part of the dataset.
15. Identify three columns you would add to `fact_sales` for lineage and auditability, and explain what question each one lets you answer during an incident.

---

**Next:** `03_linux_git_deep_dive.md` — the terminal and version-control skills you need to actually ship the project in `../customer-sales-lakehouse`.
