# 04 — ETL, ELT, and Data Quality

> **Deep-dive guide.** Companion hands-on project: `../customer-sales-lakehouse`
>
> Everything in this guide is demonstrated against the three fictional CSV files that live in
> `../customer-sales-lakehouse/data/raw/`. All data is invented. No credentials appear anywhere.

---

## Table of contents

1. [Why this topic matters](#1-why-this-topic-matters)
2. [ETL vs ELT](#2-etl-vs-elt)
3. [Batch vs streaming](#3-batch-vs-streaming)
4. [The medallion architecture: bronze, silver, gold](#4-the-medallion-architecture-bronze-silver-gold)
5. [Full vs incremental loads](#5-full-vs-incremental-loads)
6. [Idempotency, watermarks, upserts, and late data](#6-idempotency-watermarks-upserts-and-late-data)
7. [Error handling and quarantine tables](#7-error-handling-and-quarantine-tables)
8. [The six data-quality dimensions](#8-the-six-data-quality-dimensions)
9. [Putting it together: the quality summary table](#9-putting-it-together-the-quality-summary-table)
10. [Glossary](#10-glossary)
11. [Exercises](#11-exercises)

---

## 1. Why this topic matters

A data engineer is paid for one thing above all others: **a number on a dashboard is correct, and
it is correct again tomorrow morning without anyone babysitting it.**

Every concept below exists to serve that sentence:

| Concept | The failure it prevents |
|---|---|
| ELT / ETL choice | Paying too much, or being unable to re-derive history |
| Medallion layers | "Which table is the real one?" |
| Incremental loads | A 6-hour job that should take 90 seconds |
| Idempotency | Re-running a failed job doubles yesterday's revenue |
| Watermarks | Silently missing rows that arrived late |
| MERGE / upsert | Duplicate keys poisoning a join |
| Quality checks | A wrong number reaching the CFO undetected |

### The running example

Our sample is deliberately tiny so you can hold the correct answer in your head.

`data/raw/customers.csv`

```
customer_id,customer_name,region,customer_created_date
1,Aisha Khan,North,2025-10-01
2,Ben Smith,South,2025-11-12
3,Carla Jones,North,2025-12-07
4,David Lee,West,2026-01-15
```

`data/raw/products.csv`

```
product_id,product_name,category,unit_price
1001,Laptop Bag,Accessories,50.00
1002,Wireless Mouse,Accessories,25.00
1003,Monitor,Hardware,200.00
1004,Keyboard,Accessories,75.00
```

`data/raw/orders.csv`

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

**The four planted defects** (memorise these — every example in every guide refers to them):

| # | Defect | Which row | Count in sample |
|---|---|---|---|
| 1 | Duplicate `order_id` | `107` appears twice with identical `updated_at` | exactly 1 duplicated key |
| 2 | Missing mandatory `amount` | order `105` | exactly 1 |
| 3 | Invalid customer reference | order `106` → `customer_id = 99`, which is not in `customers.csv` | exactly 1 |
| 4 | *(control)* invalid product reference | — | **0** |
| 5 | *(control)* invalid status | — | **0** (valid set is `Completed`, `Cancelled`, `Pending`) |

### The canonical business rule

> `fact_sales` contains **only valid, de-duplicated, `Completed`** orders.

Therefore recognised revenue for this sample is **exactly `525.00`**:

| order_id | amount | included? | reason |
|---|---|---|---|
| 101 | 200.00 | ✅ | valid, Completed |
| 102 | 50.00 | ✅ | valid, Completed |
| 103 | 50.00 | ❌ | status = `Cancelled` |
| 104 | 75.00 | ✅ | valid, Completed |
| 105 | *(null)* | ❌ | status = `Pending` **and** missing amount |
| 106 | 50.00 | ❌ | `customer_id = 99` does not exist |
| 107 | 200.00 | ✅ | valid, Completed — duplicate row removed, counted once |
| **Total** | **525.00** | | `200 + 50 + 75 + 200` |

If any pipeline you write produces a number other than `525.00`, something is wrong. That is the
whole point of a tiny, hand-checkable dataset.

---

## 2. ETL vs ELT

### 2.1 Definitions

**ETL — Extract, Transform, Load**

```
 ┌─────────┐    ┌───────────────────┐    ┌──────────────┐
 │ Source  │ -> │ Transform engine  │ -> │ Target store │
 │ systems │    │ (separate server) │    │ (warehouse)  │
 └─────────┘    └───────────────────┘    └──────────────┘
                 cleansing happens HERE
```

Data is pulled out of source systems, reshaped on a dedicated transformation tier, and only the
finished, conformed result is written into the warehouse. The warehouse never sees the mess.

**ELT — Extract, Load, Transform**

```
 ┌─────────┐    ┌──────────────────────────────────────┐
 │ Source  │ -> │ Target store (lake / lakehouse / DW) │
 │ systems │    │   land raw  ->  transform in-place   │
 └─────────┘    └──────────────────────────────────────┘
                 cleansing happens HERE, inside the target
```

Data is landed essentially as-is, then transformed using the compute of the target platform
(Spark, Synapse, Snowflake, BigQuery, Databricks SQL).

### 2.2 A short history (why the industry swung)

| Era | Dominant pattern | Why |
|---|---|---|
| 1990s–2000s | **ETL** | Warehouse storage was expensive (SAN disks, per-TB licensing) and warehouse compute was a fixed, precious resource. You could not afford to store raw junk, so you cleaned it on a cheap intermediate server (Informatica, SSIS, DataStage). |
| ~2010–2015 | Hybrid | Hadoop made *storage* cheap first. People started landing raw files into HDFS "data lakes", but the compute story was clumsy (MapReduce). Many lakes became swamps. |
| ~2015–today | **ELT** | Cloud object storage (ADLS Gen2, S3) made storage near-free, and elastic cloud compute (Spark, serverless SQL) made "transform later, at scale" cheap and fast. Separating storage from compute removed the original reason for ETL. |

The decisive change was **decoupled storage and compute**. When storage costs pennies per GB-month
and compute is rented by the minute, keeping the raw bytes forever is an obviously good trade.

### 2.3 When each pattern wins

**ELT wins when:**

- Your target platform has powerful, elastic compute (Databricks, Synapse, Snowflake).
- You want **replayability** — the ability to re-derive every downstream table from the original
  bytes after you discover a bug in your logic six months later.
- Requirements are still moving. Raw data preserves optionality; a transformed-on-the-way-in
  pipeline throws away the columns you did not think you needed.
- You want schema-on-read flexibility for semi-structured data (JSON, logs, nested events).
- Your team is comfortable in SQL/PySpark — ELT transformations live in version-controlled code
  next to the data.

**ETL wins when:**

- **Compliance / PII**: you must mask, tokenise, or drop sensitive fields *before* they ever land
  in the analytics estate. You cannot "land first and clean later" if landing raw is itself the
  violation.
- The target is a legacy or expensive system that charges heavily for compute or storage.
- The source is a fragile operational database and you must do heavy filtering at the edge to
  avoid shipping terabytes over a slow network link.
- The volume reduction is extreme — e.g. you only ever need a daily aggregate of a firehose.
- Real-time enrichment en route (lookups, API calls) is intrinsic to the pipeline.

### 2.4 Trade-off table

| Dimension | ETL | ELT |
|---|---|---|
| Where compute happens | Dedicated transform tier | Inside the target platform |
| Raw data retained? | Usually no | Yes (that's the point) |
| Replay after a logic bug | Hard — you must re-extract from source | Easy — re-run from bronze |
| Storage cost | Lower | Higher (but storage is cheap) |
| Target compute cost | Lower | Higher |
| Schema handling | Schema-on-write, rigid | Schema-on-read then schema-on-write at silver |
| Handles semi-structured data | Awkward | Natural |
| PII controlled before landing | ✅ Yes | ❌ Needs extra controls |
| Time to first value | Slower (must model upfront) | Faster (land now, model later) |
| Typical tooling | SSIS, Informatica, ADF Mapping Data Flows | Spark/Databricks, dbt, Synapse, ADF Copy + notebooks |
| Debuggability | Opaque middle tier | Every intermediate layer is a queryable table |

### 2.5 "ELT with a T in the middle" — the honest real-world answer

Most production estates are **EtLT**: a *light* transform during extraction (type coercion, PII
masking, file format conversion to Parquet), then the heavy modelling happens inside the platform.

**How this maps to the project:** `../customer-sales-lakehouse` is an **ELT** pipeline with a
lowercase `t`.

- `src/ingestion.py` does the light `t`: `read_csv_to_df()` reads with `header=True`,
  `add_audit_columns()` stamps ingestion metadata, `write_parquet()` converts CSV → Parquet.
  **No business logic, no filtering, no cleansing.**
- `src/transformations.py` does the heavy `T`, *after* the data has landed: `clean_string_columns()`,
  `cast_columns()`, `deduplicate_by_key()`, `add_data_quality_status()`, and the gold-layer
  dimension and fact builders.

Because bronze keeps the raw bytes faithfully, you can delete `data/silver/` and `data/gold/`
entirely and rebuild them — including the `525.00` total — without touching the source CSVs again.

---

## 3. Batch vs streaming

### 3.1 The conceptual split

**Batch** processes a *bounded* set of data: "everything that arrived yesterday", "the whole table".
The job starts, reads a finite input, writes an output, and exits.

**Streaming** processes an *unbounded* set: records arrive continuously and the job never ends. It
maintains state, emits results incrementally, and must reason about time explicitly.

A useful mental model: **batch is a special case of streaming with an infinitely long window**, and
in Spark the two share the same DataFrame API (Structured Streaming).

### 3.2 Comparison

| Dimension | Batch | Streaming |
|---|---|---|
| Latency | Minutes to hours (often "T+1 day") | Milliseconds to seconds |
| Input | Bounded | Unbounded |
| Cost profile | Pay for a burst; cluster idle otherwise | Pay for always-on compute |
| Operational complexity | Low — rerun on failure | High — checkpoints, state stores, backpressure |
| Reprocessing / backfill | Trivial: point at old partitions, re-run | Hard: replay from the log, rebuild state |
| Handling late data | Naturally, because the window is wide | Needs explicit watermarks and may drop data |
| Testing | Easy — deterministic inputs | Hard — time-dependent, non-deterministic ordering |
| Debugging a bad number | Query the intermediate table | Reconstruct from logs and state |
| Typical skill ramp | Days | Weeks to months |

### 3.3 Micro-batch: the pragmatic middle

Spark Structured Streaming is mostly **micro-batch**: it runs tiny batch jobs every N seconds.
This gives you streaming-like latency with batch-like semantics and the same code. A trigger of
`availableNow` even lets you run a "streaming" job once, process everything new, and shut down —
which is an excellent, cheap incremental-batch pattern.

### 3.4 When to choose what

**Choose batch when:**

- The business consumes the data on a daily or hourly dashboard.
- Source systems themselves only export daily (most ERP/CRM extracts).
- You need easy backfills and audit-friendly reruns.
- You are learning. **Start here.**

**Choose streaming when:**

- A decision must be made in seconds: fraud scoring, dynamic pricing, operational alerting,
  real-time inventory.
- The data arrives as an unbounded log anyway (Event Hubs, Kafka, IoT telemetry, CDC feeds).
- The cost of staleness exceeds the cost of always-on compute.

### 3.5 The question to ask the business

> "If this number is 4 hours old, what decision becomes wrong, and what does that cost?"

If the answer is "nothing really", build batch. Most "we need real-time" requests dissolve under
that question. Streaming that nobody acts on in real time is pure cost.

**How this maps to the project:** `customer-sales-lakehouse` is **pure batch**. Sales reporting is
a daily-cadence business question. The notebooks `01_bronze_ingestion.py` through
`07_data_quality_checks.py` run end-to-end in seconds. Streaming would add checkpoint directories,
state management and always-on compute for zero business benefit — a textbook case of
over-engineering.

---

## 4. The medallion architecture: bronze, silver, gold

The medallion (or multi-hop) architecture splits a pipeline into layers where each layer has a
**single, stated purpose** and **progressively increasing quality and business meaning**.

```
   raw CSV          bronze            silver              gold            reporting
 ┌──────────┐   ┌────────────┐   ┌──────────────┐   ┌──────────────┐   ┌──────────┐
 │ source   │-> │ faithful   │-> │ cleansed,    │-> │ star schema, │-> │ SQL /    │
 │ files    │   │ copy +     │   │ typed,       │   │ conformed,   │   │ BI /     │
 │ (as-is)  │   │ audit cols │   │ deduplicated │   │ aggregated   │   │ ad-hoc   │
 └──────────┘   └────────────┘   └──────────────┘   └──────────────┘   └──────────┘
   trust: none     trust: low        trust: medium      trust: high
```

### 4.1 Bronze — "what the source actually sent us"

| Aspect | Detail |
|---|---|
| **Purpose** | An immutable, faithful, replayable landing zone |
| **Transformations allowed** | Format conversion (CSV → Parquet/Delta), adding audit columns. **Nothing else.** |
| **Typing** | Often everything as `string` — you do not want an ingestion job to fail because a source sent `"N/A"` in a numeric column |
| **Bad rows** | **Kept.** All 8 order rows land, including the duplicate 107 and the null amount |
| **Who consumes it** | Data engineers only. Never expose bronze to analysts |
| **Retention** | Long — this is your ability to rebuild history |

Bronze's golden rule: **if you ever have to explain a number, bronze is the evidence.**

Typical audit columns:

```python
from pyspark.sql import functions as F

def add_audit_columns(df):
    return (
        df.withColumn("ingestion_timestamp", F.current_timestamp())
          .withColumn("source_file", F.input_file_name())
          .withColumn("ingestion_date", F.current_date())
    )
```

### 4.2 Silver — "clean, conformed, one row per real-world thing"

| Aspect | Detail |
|---|---|
| **Purpose** | Enforce types, standardise text, resolve duplicates, flag quality issues |
| **Transformations** | Cast, trim, title-case, parse dates, deduplicate, validate referential integrity, tag rows |
| **Typing** | Strict and explicit |
| **Bad rows** | **Flagged, not silently dropped.** This is a key design decision |
| **Who consumes it** | Data engineers, data scientists, power analysts who need row-level detail |
| **Grain** | Usually one row per business entity/event |

In this project, silver orders gets a `data_quality_status` column (via `add_data_quality_status()`)
rather than deleting bad rows. Why? Because **a row that is deleted cannot be reported on.** The
business needs to know that order 106 exists and is broken; if you delete it, the problem is
invisible and nobody fixes the source system.

After silver:

| order_id | status | data_quality_status | note |
|---|---|---|---|
| 101 | Completed | `valid` | |
| 102 | Completed | `valid` | |
| 103 | Cancelled | `valid` | valid data, just not a sale |
| 104 | Completed | `valid` | |
| 105 | Pending | `invalid` | missing mandatory `amount` |
| 106 | Completed | `invalid` | customer 99 does not exist |
| 107 | Completed | `valid` | **one** row — duplicate removed here |

Silver orders therefore has **7 rows**, not 8.

### 4.3 Gold — "the business answer"

| Aspect | Detail |
|---|---|
| **Purpose** | Model data for consumption: star schemas, aggregates, metrics |
| **Transformations** | Join, conform, apply business rules, generate surrogate keys, aggregate |
| **Bad rows** | **Excluded** — gold is where the business rule bites |
| **Who consumes it** | BI tools, analysts, executives, downstream apps |
| **Grain** | One row per reporting grain (e.g. one row per completed order line) |

Gold tables in this project:

| Table | Grain | Built from |
|---|---|---|
| `dim_customer` | one row per customer | silver customers |
| `dim_product` | one row per product | silver products |
| `dim_date` | one row per calendar date | generated from the order date range |
| `fact_sales` | one row per valid completed order | silver orders filtered + joined to dims |

```
fact_sales for the sample
┌──────────┬─────────────┬────────────┬────────┐
│ order_id │ customer_id │ product_id │ amount │
├──────────┼─────────────┼────────────┼────────┤
│ 101      │ 1           │ 1003       │ 200.00 │
│ 102      │ 1           │ 1002       │  50.00 │
│ 104      │ 3           │ 1004       │  75.00 │
│ 107      │ 1           │ 1003       │ 200.00 │
└──────────┴─────────────┴────────────┴────────┘
                        TOTAL REVENUE = 525.00
```

### 4.4 Why layers at all? Five concrete reasons

1. **Debuggability.** A wrong number can be bisected: is it wrong in gold, silver, or bronze?
2. **Replayability.** Logic bug found in month 7? Rebuild silver and gold from bronze.
3. **Separation of concerns.** Ingestion code never contains business rules; business rules never
   contain parsing code.
4. **Cost control.** Expensive joins and aggregations run once in gold, not per dashboard query.
5. **Contract clarity.** Analysts are told "gold is supported, silver is at your own risk, bronze is
   off-limits". That single sentence prevents enormous amounts of chaos.

### 4.5 Anti-patterns

| Anti-pattern | Why it hurts |
|---|---|
| Business logic in bronze | Destroys replayability; you can no longer prove what the source sent |
| Deleting bad rows in silver | Problems become invisible; the source is never fixed |
| Analysts querying bronze | Untyped, duplicated data → wrong numbers with your name on them |
| A "silver" layer that is just a copy of bronze | Pure cost with no quality gain — make each layer earn its place |
| Twelve layers | Each hop costs compute and cognitive load; three is usually right |

---

## 5. Full vs incremental loads

### 5.1 Full load (truncate-and-reload / overwrite)

Read **everything** from the source, replace the target entirely.

```python
(spark.read.parquet(str(BRONZE_ORDERS))
      .transform(clean_and_cast)
      .write.mode("overwrite")
      .parquet(str(SILVER_ORDERS)))
```

```sql
-- SQL equivalent
TRUNCATE TABLE silver_orders;

INSERT INTO silver_orders
SELECT * FROM bronze_orders;
```

**Pros**

- Dead simple. Very hard to get wrong.
- **Naturally idempotent** — run it 1 time or 50 times, the result is identical.
- Automatically picks up deletes and retro-active edits in the source.

**Cons**

- Cost and runtime grow linearly with total history, forever.
- A long write window means the table may be briefly empty or locked.
- Hammers the source system.

**Use when:** the table is small (< ~10 million rows), or the source has no reliable change marker,
or correctness matters far more than runtime. **This includes almost every learning project.**

### 5.2 Incremental load (delta load)

Read **only what changed** since the last successful run, then merge it in.

```python
from pyspark.sql import functions as F

last_watermark = read_watermark("silver_orders")      # e.g. 2026-02-10 12:00:00

new_rows = (
    spark.read.parquet(str(BRONZE_ORDERS))
         .filter(F.col("updated_at") > F.lit(last_watermark))
)
```

```sql
SELECT *
FROM   bronze_orders
WHERE  updated_at > '2026-02-10 12:00:00';
-- returns only orders 107 (both copies) from our sample
```

**Pros**

- Runtime proportional to *change volume*, not *history volume*.
- Cheap, fast, kind to source systems.

**Cons**

- Needs a reliable change marker (`updated_at`, a sequence, or CDC).
- Hard deletes in the source are invisible unless the source emits tombstones.
- Not idempotent by default — you must add a MERGE or dedup step.
- Clock skew and timezone bugs cause silently missed rows.

### 5.3 Decision table

| Situation | Choose |
|---|---|
| Small dimension table (customers, products) | Full |
| Large append-mostly fact table | Incremental |
| Source has no `updated_at` / CDC | Full (you have no choice) |
| Source hard-deletes rows and you must reflect it | Full, or incremental + periodic full reconciliation |
| History is immutable (event logs) | Incremental, append-only |
| You are learning / prototyping | Full |

### 5.4 Hybrid: incremental with periodic full refresh

The pattern used by most mature teams:

- **Daily**: incremental load for speed.
- **Weekly (or monthly)**: full reload to repair any drift from missed late rows, missed deletes, or
  historical corrections.

This gives you incremental economics with full-load correctness guarantees.

**How this maps to the project:** `customer-sales-lakehouse` uses **full overwrite loads** at every
hop (`write_parquet(..., mode="overwrite")`). With 4 customers, 4 products and 8 order rows this is
correct and trivially idempotent. Section 6 shows what you would change to make it incremental —
and `07_databricks_delta_lake.md` shows the production-grade Delta `MERGE INTO` version.

---

## 6. Idempotency, watermarks, upserts, and late data

### 6.1 Idempotency

> **Idempotent**: running an operation N times produces exactly the same result as running it once.

This is the single most important operational property of a data pipeline, because **pipelines fail
and get re-run.** Network blips, cluster evictions, a human clicking "rerun". If your pipeline is
not idempotent, each rerun corrupts the data a little more.

**Not idempotent** ❌

```python
# Every rerun appends the same rows again -> revenue doubles, triples...
df.write.mode("append").parquet(str(SILVER_ORDERS))
```

Run this twice on our sample and `fact_sales` reports `1050.00` instead of `525.00`. The dashboard
shows a glorious 100% month-on-month growth, and you find out in a board meeting.

**Idempotent — option A: overwrite**

```python
df.write.mode("overwrite").parquet(str(SILVER_ORDERS))
```

**Idempotent — option B: partition overwrite** (replace only today's slice)

```python
spark.conf.set("spark.sql.sources.partitionOverwriteMode", "dynamic")

(df.write
   .mode("overwrite")
   .partitionBy("order_date")
   .parquet(str(SILVER_ORDERS)))
```

**Idempotent — option C: MERGE on a business key** (the production answer — see 6.3)

**Idempotency checklist**

- [ ] Can I re-run this job twice in a row and get the same row count?
- [ ] Does it use `append` anywhere? If so, what stops duplicates?
- [ ] Is there a natural/business key I can key a MERGE on?
- [ ] Does it call `current_timestamp()` in a way that changes the *output data*, not just audit
      columns? (That breaks determinism.)
- [ ] If the job dies halfway, is the target left in a valid state?

### 6.2 Watermarks

A **watermark** is the high-water mark of data already processed. It answers "where do I resume?"

```python
from pyspark.sql import functions as F

# After a successful run, store the maximum timestamp you actually processed
new_watermark = (
    silver_orders.select(F.max("updated_at").alias("wm")).collect()[0]["wm"]
)
# For the full sample this is 2026-02-15 08:00:00 (order 107)
save_watermark("silver_orders", new_watermark)
```

```sql
-- A simple watermark control table
CREATE TABLE etl_watermark (
    table_name        STRING,
    last_processed_at TIMESTAMP,
    updated_at        TIMESTAMP
);

-- Read it
SELECT last_processed_at FROM etl_watermark WHERE table_name = 'silver_orders';

-- Use it
SELECT * FROM bronze_orders
WHERE  updated_at > (SELECT last_processed_at FROM etl_watermark
                     WHERE table_name = 'silver_orders');
```

**Three watermark traps**

1. **`>` vs `>=`.** Use `>` (strictly greater) or you reprocess the boundary row every time. But if
   two rows share the exact boundary timestamp and you only processed one, `>` loses the other.
   Our sample has exactly this hazard: **both copies of order 107 share `2026-02-15 08:00:00`.**
   The robust fix is a MERGE, which makes reprocessing harmless.
2. **Timezones.** Source in local time, pipeline in UTC → you skip or duplicate an hour's data
   twice a year. Store watermarks in UTC. Always.
3. **Update the watermark only on success.** If the job writes the watermark before the write
   commits and then crashes, you have permanently skipped data. Commit data first, watermark second.

### 6.3 Upserts and MERGE

An **upsert** = UPDATE if the key exists, INSERT if it does not. In SQL this is `MERGE`.

```sql
MERGE INTO silver_orders AS target
USING (
    -- only the latest row per order_id from the incoming batch
    SELECT order_id, customer_id, product_id, order_date,
           quantity, amount, status, updated_at
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY order_id
                   ORDER BY updated_at DESC
               ) AS rn
        FROM bronze_orders_incremental
    ) ranked
    WHERE rn = 1
) AS source
ON target.order_id = source.order_id

WHEN MATCHED AND source.updated_at >= target.updated_at THEN
    UPDATE SET
        target.customer_id = source.customer_id,
        target.product_id  = source.product_id,
        target.order_date  = source.order_date,
        target.quantity    = source.quantity,
        target.amount      = source.amount,
        target.status      = source.status,
        target.updated_at  = source.updated_at

WHEN NOT MATCHED THEN
    INSERT (order_id, customer_id, product_id, order_date,
            quantity, amount, status, updated_at)
    VALUES (source.order_id, source.customer_id, source.product_id,
            source.order_date, source.quantity, source.amount,
            source.status, source.updated_at);
```

Two details that make this production-grade:

1. **Deduplicate the source first.** `MERGE` raises an error if one target row matches multiple
   source rows. Our duplicate 107 would break the merge without the `ROW_NUMBER()` subquery.
2. **`WHEN MATCHED AND source.updated_at >= target.updated_at`.** This guards against an older,
   late-arriving row overwriting a newer one.

> Plain Parquet does not support `MERGE`. Delta Lake, Iceberg and Hudi do. See
> `07_databricks_delta_lake.md` for the PySpark `DeltaTable.merge()` form of exactly this statement.

### 6.4 Deduplication

Deduplication is required whenever a key can legitimately appear more than once in the input.

**Case A — fully identical rows** (our order 107): `dropDuplicates()` is enough.

```python
deduped = orders_df.dropDuplicates(["order_id"])   # 8 rows -> 7 rows
```

**Case B — same key, different versions**: keep the latest. This is the general, correct form, and
it also handles Case A.

```python
from pyspark.sql import functions as F
from pyspark.sql.window import Window

def deduplicate_by_key(df, key_columns, order_column):
    """Keep one row per key: the one with the highest order_column."""
    window = (
        Window.partitionBy(*key_columns)
              .orderBy(F.col(order_column).desc())
    )
    return (
        df.withColumn("_row_number", F.row_number().over(window))
          .filter(F.col("_row_number") == 1)
          .drop("_row_number")
    )

silver_orders = deduplicate_by_key(
    bronze_orders,
    key_columns=["order_id"],
    order_column="updated_at",
)
```

```sql
-- SQL equivalent
SELECT order_id, customer_id, product_id, order_date,
       quantity, amount, status, updated_at
FROM (
    SELECT *,
           ROW_NUMBER() OVER (
               PARTITION BY order_id
               ORDER BY updated_at DESC
           ) AS rn
    FROM bronze_orders
) t
WHERE rn = 1;
```

**Expected result on the sample:** 8 input rows → **7 output rows**. Order 107 appears once.

> **Tie-breaking.** Both 107 rows share `updated_at = 2026-02-15 08:00:00`, so the ordering is
> ambiguous. Because the two rows are *completely identical*, either choice is correct here. In
> production, where ties are not identical, add a deterministic tie-breaker to the `orderBy`, e.g.
> `.orderBy(F.col("updated_at").desc(), F.col("source_file").desc())`. Non-deterministic dedup is a
> classic source of "the number changed and nobody touched the code".

**`dropDuplicates` vs `row_number()`**

| | `dropDuplicates(keys)` | `row_number()` window |
|---|---|---|
| Which row survives | Arbitrary | You choose, explicitly |
| Deterministic | No | Yes (with a tie-breaker) |
| Use for | Truly identical rows | Versioned rows / CDC |

### 6.5 Late-arriving data

**Late-arriving facts.** A row for `2026-01-05` arrives on `2026-02-20`. Strategies:

| Strategy | How | Trade-off |
|---|---|---|
| Lookback window | Reprocess the last N days every run | Simple, costs a little extra compute |
| MERGE on key | Upsert regardless of date | Needs a merge-capable format |
| Partition overwrite | Rewrite only the affected date partitions | Needs the affected partitions identified |
| Watermark with grace | `updated_at > watermark - INTERVAL 2 DAYS` | Reprocesses a tail; needs idempotency |

```python
from pyspark.sql import functions as F

LOOKBACK_DAYS = 3

incremental = (
    spark.read.parquet(str(BRONZE_ORDERS))
         .filter(
             F.col("updated_at") > F.lit(last_watermark) - F.expr(f"INTERVAL {LOOKBACK_DAYS} DAYS")
         )
)
# Safe ONLY because the downstream write is a MERGE, not an append.
```

**Late-arriving dimensions ("early-arriving facts").** Order 106 references customer 99, which does
not exist. Two readings:

1. **Genuine corruption** — customer 99 never existed. Quarantine the row. *(This is the assumption
   in `customer-sales-lakehouse`.)*
2. **Timing** — customer 99 was created in the CRM but that extract has not landed yet. The
   classic fix is an **inferred member**: insert a placeholder `dim_customer` row with
   `customer_name = 'Unknown'` and a late-arriving flag, so the fact can still join, then update it
   when the real record lands.

Which is right is a **business decision, not a technical one**. The engineering job is to surface
the choice, not to silently pick one.

---

## 7. Error handling and quarantine tables

### 7.1 The three ways to handle a bad row

| Approach | What happens | When to use |
|---|---|---|
| **Fail fast** | Abort the whole job | A schema change, or a breach so severe that partial data is worse than no data |
| **Drop silently** | Discard the row | ❌ Almost never. This is how trust dies |
| **Quarantine + flag** | Route the row to an exception table, continue with the good rows | ✅ The default for row-level quality problems |

### 7.2 The quarantine pattern

```python
from pyspark.sql import functions as F

VALID_STATUSES = ["Completed", "Cancelled", "Pending"]

def split_valid_invalid(orders_df, customers_df, products_df):
    """Return (clean_df, quarantine_df) with a human-readable rejection reason."""

    valid_customers = customers_df.select(
        F.col("customer_id").alias("_cust_id")
    ).distinct()
    valid_products = products_df.select(
        F.col("product_id").alias("_prod_id")
    ).distinct()

    enriched = (
        orders_df
        .join(valid_customers, orders_df.customer_id == F.col("_cust_id"), "left")
        .join(valid_products, orders_df.product_id == F.col("_prod_id"), "left")
        .withColumn(
            "rejection_reason",
            F.when(F.col("order_id").isNull(), F.lit("missing_order_id"))
             .when(F.col("amount").isNull(), F.lit("missing_amount"))
             .when(F.col("_cust_id").isNull(), F.lit("invalid_customer_reference"))
             .when(F.col("_prod_id").isNull(), F.lit("invalid_product_reference"))
             .when(~F.col("status").isin(VALID_STATUSES), F.lit("invalid_status"))
             .otherwise(F.lit(None)),
        )
        .drop("_cust_id", "_prod_id")
    )

    clean = enriched.filter(F.col("rejection_reason").isNull()).drop("rejection_reason")
    quarantine = (
        enriched.filter(F.col("rejection_reason").isNotNull())
                .withColumn("quarantined_at", F.current_timestamp())
    )
    return clean, quarantine
```

Applied to the de-duplicated sample (7 rows):

| order_id | rejection_reason | destination |
|---|---|---|
| 101 | *(null)* | clean |
| 102 | *(null)* | clean |
| 103 | *(null)* | clean — *valid data*, excluded later only by the `Completed` rule |
| 104 | *(null)* | clean |
| 105 | `missing_amount` | **quarantine** |
| 106 | `invalid_customer_reference` | **quarantine** |
| 107 | *(null)* | clean |

**5 clean rows, 2 quarantined rows.** Note that `Cancelled` order 103 is *not* a quality failure —
it is perfectly good data that the gold business rule excludes. Keeping those two ideas separate is
a hallmark of a well-designed pipeline:

> **Quality failure** = the data is wrong.
> **Business exclusion** = the data is right but not relevant to this metric.

### 7.3 A quarantine table you can actually operate

```sql
CREATE TABLE quarantine_orders (
    order_id          STRING,
    customer_id       STRING,
    product_id        STRING,
    order_date        STRING,
    quantity          STRING,
    amount            STRING,
    status            STRING,
    updated_at        STRING,
    rejection_reason  STRING,
    source_file       STRING,
    quarantined_at    TIMESTAMP,
    processing_date   DATE
);
```

Note that quarantine columns are **strings**. The row failed precisely because it did not conform —
if you type the quarantine table strictly, the insert into quarantine fails too.

A quarantine table is useless without a process around it:

1. **Alert** when `COUNT(*)` for today exceeds a threshold.
2. **Own it** — a named person or team triages it.
3. **Feed back** — fix the *source system*, not just the pipeline.
4. **Reprocess** — once the source is fixed, re-run; the row should clear.
5. **Trend it** — a rising quarantine rate is an early warning of upstream change.

### 7.4 Other failure modes worth handling explicitly

| Failure | Handling |
|---|---|
| Source file missing | Fail fast with a clear message; do not write an empty table over good data |
| Schema drift (new column) | Log and continue if additive; fail if a required column vanished |
| Schema drift (type change) | Fail fast — silent coercion produces wrong numbers |
| Corrupt CSV lines | `PERMISSIVE` mode + `_corrupt_record` column, routed to quarantine |
| Zero rows read | Fail or alert — "successfully processed 0 records" is the most dangerous log line in data engineering |
| Row count collapse (90% drop) | Alert; this is usually an upstream outage, not real |

```python
# Capture malformed CSV rows instead of losing them
from pyspark.sql.types import StructType, StructField, StringType

schema = StructType([
    StructField("order_id", StringType(), True),
    StructField("customer_id", StringType(), True),
    StructField("product_id", StringType(), True),
    StructField("order_date", StringType(), True),
    StructField("quantity", StringType(), True),
    StructField("amount", StringType(), True),
    StructField("status", StringType(), True),
    StructField("updated_at", StringType(), True),
    StructField("_corrupt_record", StringType(), True),
])

raw = (
    spark.read
         .option("header", True)
         .option("mode", "PERMISSIVE")
         .option("columnNameOfCorruptRecord", "_corrupt_record")
         .schema(schema)
         .csv(str(RAW_ORDERS))
)
```

---

## 8. The six data-quality dimensions

These six are the industry-standard vocabulary (DAMA-DMBOK). Learn the names — they come up in
interviews and in conversations with data stewards.

| # | Dimension | The question it answers |
|---|---|---|
| 1 | Completeness | Is anything missing? |
| 2 | Validity | Does it conform to the rules? |
| 3 | Uniqueness | Is anything duplicated? |
| 4 | Consistency | Do related facts agree with each other? |
| 5 | Timeliness | Is it fresh enough to be useful? |
| 6 | Accuracy | Does it match reality? |

Each section below gives the definition, a SQL check, a PySpark check, and the **expected result on
our sample**.

---

### 8.1 Completeness

> **Definition:** required attributes are populated; expected rows are present.

Two sub-flavours: *attribute* completeness (no nulls in mandatory columns) and *record*
completeness (you received all the rows you should have).

**Check C1 — mandatory `amount` is populated**

```sql
SELECT order_id, customer_id, product_id, order_date, status, amount
FROM   bronze_orders
WHERE  amount IS NULL
    OR TRIM(amount) = '';
```

```python
from pyspark.sql import functions as F

def check_missing_order_amount(orders_df):
    """Orders where a mandatory field is null/blank."""
    return orders_df.filter(
        F.col("amount").isNull()
        | (F.trim(F.col("amount").cast("string")) == "")
    )

failed = check_missing_order_amount(orders_df)
print(f"missing_order_amount failed_record_count = {failed.count()}")
```

| | |
|---|---|
| **Expected result** | **1 row — order `105`** |
| Status | FAIL |
| Action | Quarantine order 105; it never reaches `fact_sales` |

**Check C2 — all mandatory order fields at once**

```sql
SELECT order_id,
       CASE WHEN order_id    IS NULL THEN 1 ELSE 0 END AS miss_order_id,
       CASE WHEN customer_id IS NULL THEN 1 ELSE 0 END AS miss_customer_id,
       CASE WHEN product_id  IS NULL THEN 1 ELSE 0 END AS miss_product_id,
       CASE WHEN order_date  IS NULL THEN 1 ELSE 0 END AS miss_order_date,
       CASE WHEN amount      IS NULL THEN 1 ELSE 0 END AS miss_amount,
       CASE WHEN status      IS NULL THEN 1 ELSE 0 END AS miss_status
FROM   bronze_orders
WHERE  order_id IS NULL OR customer_id IS NULL OR product_id IS NULL
    OR order_date IS NULL OR amount IS NULL OR status IS NULL;
```

```python
MANDATORY = ["order_id", "customer_id", "product_id", "order_date", "amount", "status"]

missing_condition = None
for column in MANDATORY:
    this_col_is_missing = F.col(column).isNull()
    missing_condition = (
        this_col_is_missing if missing_condition is None
        else missing_condition | this_col_is_missing
    )

missing_mandatory = orders_df.filter(missing_condition)
```

**Expected result: 1 row (order 105, missing `amount`).** Every other mandatory field is populated
in every row.

**Check C3 — record completeness (null-rate profile)**

```python
null_profile = orders_df.select([
    F.sum(F.col(c).isNull().cast("int")).alias(f"{c}_nulls")
    for c in orders_df.columns
])
null_profile.show(truncate=False)
```

Expected: every `*_nulls` column is `0` except `amount_nulls = 1`.

---

### 8.2 Validity

> **Definition:** values conform to the defined domain, format, type, or range.

Validity is about *conformance to a rule*, regardless of whether the value is "true".
`status = 'Shipped'` would be **invalid** here because it is outside our agreed domain — even
though it is a perfectly sensible word.

**Check V1 — status is in the allowed domain**

```sql
SELECT order_id, status
FROM   bronze_orders
WHERE  status NOT IN ('Completed', 'Cancelled', 'Pending')
    OR status IS NULL;
```

```python
VALID_ORDER_STATUSES = ["Completed", "Cancelled", "Pending"]

def check_invalid_status(orders_df, valid_statuses=VALID_ORDER_STATUSES):
    return orders_df.filter(
        ~F.col("status").isin(valid_statuses) | F.col("status").isNull()
    )

print(check_invalid_status(orders_df).count())
```

| | |
|---|---|
| **Expected result** | **0 rows** |
| Status | PASS |

This is a **control check**. A test suite that only contains failing checks cannot tell you the
difference between "the data is clean" and "the check is broken". Always include checks you expect
to pass.

> ⚠️ **Case sensitivity.** `isin(["Completed"])` will reject `"completed"` and `"COMPLETED"`. In this
> project `clean_string_columns()` / `standardise_title_case()` run *before* validation, which is why
> the check is safe. Order of operations matters: **standardise, then validate.**

**Check V2 — numeric ranges and types**

```sql
SELECT order_id, quantity, amount
FROM   bronze_orders
WHERE  CAST(quantity AS INT) <= 0
    OR CAST(amount   AS DECIMAL(10,2)) < 0;
```

```python
invalid_numbers = orders_df.filter(
    (F.col("quantity").cast("int") <= 0)
    | (F.col("amount").cast("decimal(10,2)") < 0)
)
```

**Expected result: 0 rows.** All quantities are 1 or 2, all present amounts are positive.

**Check V3 — date format parses cleanly**

A string that fails to parse becomes `NULL` after a cast, which is how you detect bad formats:

```python
bad_dates = orders_df.filter(
    F.col("order_date").isNotNull()
    & F.to_date(F.col("order_date"), "yyyy-MM-dd").isNull()
)
```

**Expected result: 0 rows.** All eight order dates are well-formed `yyyy-MM-dd`.

**Check V4 — dates are not in the future**

```sql
SELECT order_id, order_date
FROM   bronze_orders
WHERE  CAST(order_date AS DATE) > CURRENT_DATE();
```

Expected (as at any date after 2026-02-15): **0 rows**.

---

### 8.3 Uniqueness

> **Definition:** no business entity is represented more than once.

**Check U1 — duplicate order IDs**

```sql
SELECT order_id, COUNT(*) AS occurrence_count
FROM   bronze_orders
GROUP  BY order_id
HAVING COUNT(*) > 1
ORDER  BY order_id;
```

```python
def check_duplicate_order_id(orders_df, key_column="order_id"):
    """Return the duplicated keys and how many times each appears."""
    return (
        orders_df.groupBy(key_column)
                 .agg(F.count("*").alias("occurrence_count"))
                 .filter(F.col("occurrence_count") > 1)
    )

duplicates = check_duplicate_order_id(orders_df)
duplicates.show()
```

| | |
|---|---|
| **Expected result** | **1 row: `order_id = 107, occurrence_count = 2`** |
| Status | FAIL |
| Action | `deduplicate_by_key(orders_df, ["order_id"], "updated_at")` → 8 rows become 7 |

> **Counting convention.** Note the difference between "1 duplicated **key**" and "1 **excess row**".
> Here both happen to be 1. For a key appearing 3 times you would have 1 duplicated key but 2 excess
> rows. Be explicit about which you are reporting — this project reports the **number of duplicated
> keys**, so `failed_record_count = 1`.

**Check U2 — dimension keys are unique**

```sql
SELECT customer_id, COUNT(*) FROM bronze_customers GROUP BY customer_id HAVING COUNT(*) > 1;
SELECT product_id,  COUNT(*) FROM bronze_products  GROUP BY product_id  HAVING COUNT(*) > 1;
```

**Expected: 0 rows each.** A duplicate dimension key is far more dangerous than a duplicate fact,
because it causes **row multiplication in joins** — one duplicated customer silently doubles every
sale for that customer.

**Check U3 — post-dedup assertion**

```python
assert silver_orders.count() == silver_orders.select("order_id").distinct().count(), \
    "order_id is not unique in silver_orders"
assert silver_orders.count() == 7, "expected 7 unique orders in the sample"
```

---

### 8.4 Consistency

> **Definition:** related data agrees across tables, columns, and time. Mostly this means
> **referential integrity** and **cross-field arithmetic**.

**Check K1 — every order references a real customer**

```sql
SELECT o.order_id, o.customer_id
FROM   bronze_orders o
LEFT   JOIN bronze_customers c
       ON o.customer_id = c.customer_id
WHERE  c.customer_id IS NULL;
```

```python
def check_invalid_customer_reference(orders_df, customers_df):
    """Orders whose customer_id has no match in the customer dimension."""
    valid_ids = customers_df.select(
        F.col("customer_id").alias("_valid_customer_id")
    ).distinct()

    return (
        orders_df
        .join(valid_ids, orders_df.customer_id == F.col("_valid_customer_id"), "left_anti")
    )

orphans = check_invalid_customer_reference(orders_df, customers_df)
orphans.select("order_id", "customer_id").show()
```

| | |
|---|---|
| **Expected result** | **1 row — order `106`, `customer_id = 99`** |
| Status | FAIL |
| Action | Quarantine; excluded from `fact_sales` |

> **`left_anti` vs `left` + `IS NULL`.** A `left_anti` join returns rows from the left side with *no*
> match on the right. It is the idiomatic Spark way to express "orphans", it never multiplies rows,
> and it keeps the left schema unchanged. Prefer it.

**Check K2 — every order references a real product**

```sql
SELECT o.order_id, o.product_id
FROM   bronze_orders o
LEFT   JOIN bronze_products p
       ON o.product_id = p.product_id
WHERE  p.product_id IS NULL;
```

```python
def check_invalid_product_reference(orders_df, products_df):
    valid_ids = products_df.select(
        F.col("product_id").alias("_valid_product_id")
    ).distinct()
    return orders_df.join(
        valid_ids, orders_df.product_id == F.col("_valid_product_id"), "left_anti"
    )
```

| | |
|---|---|
| **Expected result** | **0 rows** |
| Status | PASS |

Every product id used (1001, 1002, 1003, 1004) exists in `products.csv`. This is the second
**control check** — it proves the orphan-detection logic is discriminating, not just returning
everything.

**Check K3 — cross-field arithmetic**

Does `amount` equal `quantity × unit_price`? Let us check honestly:

```sql
SELECT o.order_id, o.quantity, p.unit_price, o.amount,
       o.quantity * p.unit_price AS expected_amount
FROM   bronze_orders o
JOIN   bronze_products p ON o.product_id = p.product_id
WHERE  o.amount IS NOT NULL
  AND  ABS(CAST(o.amount AS DECIMAL(10,2)) - o.quantity * p.unit_price) > 0.01;
```

Working it through by hand:

| order_id | qty | product | unit_price | qty × price | amount | matches? |
|---|---|---|---|---|---|---|
| 101 | 1 | 1003 Monitor | 200.00 | 200.00 | 200.00 | ✅ |
| 102 | **2** | 1002 Wireless Mouse | 25.00 | **50.00** | 50.00 | ✅ |
| 103 | 1 | 1001 Laptop Bag | 50.00 | 50.00 | 50.00 | ✅ |
| 104 | 1 | 1004 Keyboard | 75.00 | 75.00 | 75.00 | ✅ |
| 105 | 1 | 1002 Wireless Mouse | 25.00 | 25.00 | *(null)* | excluded (null) |
| 106 | 1 | 1001 Laptop Bag | 50.00 | 50.00 | 50.00 | ✅ |
| 107 | 1 | 1003 Monitor | 200.00 | 200.00 | 200.00 | ✅ |

**Expected result: 0 rows.** The amounts are internally consistent. (Order 102 is the interesting
one — 2 × 25.00 = 50.00, which is why it looks like a "50" row at a glance.)

This check is worth running in real projects because an amount that disagrees with
`quantity × price` usually means a discount, a currency mix-up, or a broken upstream calculation —
all things the business wants to know about.

---

### 8.5 Timeliness

> **Definition:** data arrives and is processed within the window the business needs, and the
> latest data present is as recent as expected.

Timeliness is about **freshness and SLA**, not about correctness of values.

**Check T1 — data freshness**

```sql
SELECT MAX(CAST(updated_at AS TIMESTAMP)) AS latest_update,
       DATEDIFF(CURRENT_DATE(), MAX(CAST(order_date AS DATE))) AS days_since_last_order
FROM   bronze_orders;
```

```python
freshness = orders_df.select(
    F.max(F.to_timestamp("updated_at", "yyyy-MM-dd HH:mm:ss")).alias("latest_update"),
    F.max(F.to_date("order_date", "yyyy-MM-dd")).alias("latest_order_date"),
)
freshness.show(truncate=False)
```

| | |
|---|---|
| **Expected result** | `latest_update = 2026-02-15 08:00:00`, `latest_order_date = 2026-02-15` |
| Interpretation | If today is 2026-02-16, the feed is 1 day old → **PASS** for a daily SLA. If today is 2026-04-01, the feed has been stale for ~6 weeks → **FAIL**, investigate the source |

**Check T2 — the pipeline itself ran**

```python
pipeline_latency = (
    bronze_orders.select(
        F.max("ingestion_timestamp").alias("last_ingested_at")
    )
)
```

A row count of zero *with a successful job status* is the classic silent failure. Always assert:

```python
row_count = orders_df.count()
assert row_count > 0, "bronze_orders is empty — refusing to overwrite silver"
```

**Check T3 — expected row-count band**

```python
EXPECTED_MIN_ROWS = 1
if row_count < EXPECTED_MIN_ROWS:
    logger.error("Row count %s below expected minimum %s", row_count, EXPECTED_MIN_ROWS)
```

For our fixed sample `row_count` is always **8** in bronze and **7** in silver. In production you
would compare against a rolling average of recent runs rather than a hard-coded number.

---

### 8.6 Accuracy

> **Definition:** the data correctly describes the real-world entity or event it represents.

Accuracy is the **hardest** dimension because it requires an external source of truth. A value can
be complete, valid, unique, consistent and timely — and still be **wrong**. If Aisha Khan's region
is recorded as `North` but she actually lives in the South, no amount of SQL will detect it.

Practical proxies for accuracy:

**Check A1 — reconciliation against a trusted total**

The strongest accuracy test available to a data engineer. If finance says the month's recognised
revenue is `525.00`, your pipeline must produce `525.00`.

```sql
SELECT ROUND(SUM(amount), 2) AS total_revenue,
       COUNT(*)              AS order_count
FROM   gold_fact_sales;
-- Expected: total_revenue = 525.00, order_count = 4
```

```python
reconciliation = fact_sales.agg(
    F.round(F.sum("amount"), 2).alias("total_revenue"),
    F.count("*").alias("order_count"),
).collect()[0]

assert reconciliation["total_revenue"] == 525.00, \
    f"Revenue reconciliation failed: got {reconciliation['total_revenue']}, expected 525.00"
assert reconciliation["order_count"] == 4
```

**Check A2 — row-count reconciliation across the pipeline**

```sql
SELECT 'bronze_orders'   AS layer, COUNT(*) AS row_count FROM bronze_orders
UNION ALL
SELECT 'silver_orders',  COUNT(*) FROM silver_orders
UNION ALL
SELECT 'gold_fact_sales', COUNT(*) FROM gold_fact_sales;
```

| layer | row_count | why |
|---|---|---|
| `bronze_orders` | **8** | faithful copy of the CSV, duplicate included |
| `silver_orders` | **7** | duplicate 107 removed; 105 and 106 retained but flagged `invalid` |
| `gold_fact_sales` | **4** | only valid `Completed` orders: 101, 102, 104, 107 |

Every reduction must be **explainable in one sentence**. `8 → 7` = "removed one duplicate order id".
`7 → 4` = "excluded one Cancelled order, one Pending order with a missing amount, and one order with
an invalid customer reference". If you cannot narrate the drop, you have a bug.

**Check A3 — plausibility / outlier detection**

```sql
SELECT order_id, amount
FROM   gold_fact_sales
WHERE  amount > (SELECT AVG(amount) + 3 * STDDEV(amount) FROM gold_fact_sales);
```

**Expected: 0 rows.** Amounts (200, 50, 75, 200) are tightly clustered; nothing is three standard
deviations out.

**Check A4 — distribution drift**

Compare today's category mix or regional mix against the historical average. A sudden swing usually
means a source-system change, not a change in customer behaviour.

```sql
SELECT c.region, COUNT(*) AS orders, ROUND(SUM(f.amount), 2) AS revenue
FROM   gold_fact_sales f
JOIN   gold_dim_customer c ON f.customer_key = c.customer_key
GROUP  BY c.region
ORDER  BY revenue DESC;
```

Expected for the sample:

| region | orders | revenue |
|---|---|---|
| North | 4 | 525.00 |

All four surviving orders belong to customers 1 (Aisha Khan, North) and 3 (Carla Jones, North) — a
good reminder that filtering changes the shape of your data, not just its size. Ben Smith (South)
appears only on the cancelled order, and David Lee (West) has no orders at all.

---

### 8.7 The six dimensions at a glance for this sample

| Dimension | Check | Expected failures | Verdict |
|---|---|---|---|
| Completeness | mandatory order fields populated | **1** (order 105, `amount`) | FAIL |
| Validity | `status` in (Completed, Cancelled, Pending) | **0** | PASS |
| Validity | numeric ranges / date formats | **0** | PASS |
| Uniqueness | duplicate `order_id` | **1** (order 107, ×2) | FAIL |
| Consistency | customer referential integrity | **1** (order 106 → customer 99) | FAIL |
| Consistency | product referential integrity | **0** | PASS |
| Consistency | `amount` = `quantity` × `unit_price` | **0** | PASS |
| Timeliness | latest `updated_at` | `2026-02-15 08:00:00` | context-dependent |
| Accuracy | revenue reconciliation | `525.00` expected | PASS when the rule is applied |

**Three failing checks, and they are the three defects we planted.** Nothing more, nothing less.

---

## 9. Putting it together: the quality summary table

The individual checks return DataFrames of offending rows — useful for a human triaging a problem.
For dashboards, alerting and history you also need a single, narrow **summary** table.

### 9.1 The schema

| Column | Type | Meaning |
|---|---|---|
| `check_name` | string | Stable identifier, e.g. `duplicate_order_id` |
| `check_status` | string | `PASS` or `FAIL` |
| `failed_record_count` | long | How many records (or keys) failed |
| `processing_date` | date | Which run this result belongs to |

```sql
CREATE TABLE quality_check_summary (
    check_name           STRING,
    check_status         STRING,
    failed_record_count  BIGINT,
    processing_date      DATE
);
```

### 9.2 Building it in PySpark

```python
from dataclasses import dataclass
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import (
    StructType, StructField, StringType, LongType, DateType,
)


@dataclass(frozen=True)
class CheckResult:
    check_name: str
    check_status: str
    failed_record_count: int


SUMMARY_SCHEMA = StructType([
    StructField("check_name", StringType(), False),
    StructField("check_status", StringType(), False),
    StructField("failed_record_count", LongType(), False),
    StructField("processing_date", DateType(), False),
])


def _result(check_name: str, failed_count: int) -> CheckResult:
    status = "PASS" if failed_count == 0 else "FAIL"
    return CheckResult(check_name, status, failed_count)


def run_all_checks(
    orders_df: DataFrame,
    customers_df: DataFrame,
    products_df: DataFrame,
) -> list[CheckResult]:
    return [
        _result("duplicate_order_id",
                check_duplicate_order_id(orders_df).count()),
        _result("missing_order_amount",
                check_missing_order_amount(orders_df).count()),
        _result("invalid_customer_reference",
                check_invalid_customer_reference(orders_df, customers_df).count()),
        _result("invalid_product_reference",
                check_invalid_product_reference(orders_df, products_df).count()),
        _result("invalid_status",
                check_invalid_status(orders_df).count()),
    ]


def summarise(spark: SparkSession, results: list[CheckResult]) -> DataFrame:
    rows = [(r.check_name, r.check_status, int(r.failed_record_count)) for r in results]
    return (
        spark.createDataFrame(rows, "check_name string, check_status string, failed_record_count bigint")
             .withColumn("processing_date", F.current_date())
    )
```

### 9.3 Expected output for the sample

```
+----------------------------+--------------+---------------------+----------------+
|check_name                  |check_status  |failed_record_count  |processing_date |
+----------------------------+--------------+---------------------+----------------+
|duplicate_order_id          |FAIL          |1                    |2026-02-16      |
|missing_order_amount        |FAIL          |1                    |2026-02-16      |
|invalid_customer_reference  |FAIL          |1                    |2026-02-16      |
|invalid_product_reference   |PASS          |0                    |2026-02-16      |
|invalid_status              |PASS          |0                    |2026-02-16      |
+----------------------------+--------------+---------------------+----------------+
```

Three `FAIL` with count 1, two `PASS` with count 0. Commit this expectation to a test — it is the
cheapest regression safety net you will ever write.

### 9.4 Querying the summary over time

```sql
-- Today's health
SELECT check_name, check_status, failed_record_count
FROM   quality_check_summary
WHERE  processing_date = CURRENT_DATE()
ORDER  BY failed_record_count DESC;

-- Is quality degrading? (30-day trend per check)
SELECT check_name,
       processing_date,
       failed_record_count,
       failed_record_count
         - LAG(failed_record_count) OVER (
               PARTITION BY check_name ORDER BY processing_date
           ) AS change_vs_previous_run
FROM   quality_check_summary
WHERE  processing_date >= DATE_SUB(CURRENT_DATE(), 30)
ORDER  BY check_name, processing_date;

-- Which checks have never passed?
SELECT check_name, COUNT(*) AS runs, SUM(CASE WHEN check_status = 'PASS' THEN 1 ELSE 0 END) AS passes
FROM   quality_check_summary
GROUP  BY check_name
HAVING SUM(CASE WHEN check_status = 'PASS' THEN 1 ELSE 0 END) = 0;
```

### 9.5 Blocking vs warning checks

Not every failure should stop a pipeline. Classify each check:

| Severity | Behaviour | Example from our sample |
|---|---|---|
| **Blocking** | Abort before writing gold | Duplicate keys in a *dimension* (would multiply rows) |
| **Warning** | Log, alert, continue | `duplicate_order_id`, `missing_order_amount`, `invalid_customer_reference` — all handled by dedup/quarantine |
| **Informational** | Record the number only | Row count, freshness |

```python
BLOCKING_CHECKS = {"duplicate_customer_id", "duplicate_product_id"}

failures = [r for r in results if r.check_status == "FAIL"]
blocking_failures = [r for r in failures if r.check_name in BLOCKING_CHECKS]

for r in failures:
    logger.warning("Quality check %s failed with %s records", r.check_name, r.failed_record_count)

if blocking_failures:
    names = ", ".join(r.check_name for r in blocking_failures)
    raise RuntimeError(f"Blocking data quality failures: {names}")
```

**How this maps to the project:** `../customer-sales-lakehouse/src/quality_checks.py` implements
exactly this design — one function per check returning a DataFrame of offending rows, plus
`run_all_checks()` and `summarise()` producing the
`check_name, check_status, failed_record_count, processing_date` summary. Notebook
`07_data_quality_checks.py` runs them and writes the results under
`QUALITY_RESULTS_DIR` (`data/quality_results/`). `sql/02_data_quality_checks.sql` contains the SQL
equivalents of the same checks so you can compare both idioms side by side.

---

## 10. Glossary

| Term | Meaning |
|---|---|
| **Backfill** | Reprocessing historical periods, usually after a logic fix |
| **CDC** | Change Data Capture — streaming inserts/updates/deletes out of a source database |
| **Conformed dimension** | A dimension shared identically across multiple facts |
| **Data contract** | An agreed, versioned schema + SLA between a producer and a consumer |
| **Grain** | What exactly one row of a table represents |
| **Idempotent** | Re-running produces the same result |
| **Inferred member** | A placeholder dimension row created so an early-arriving fact can join |
| **Lineage** | The traceable path from a reported number back to its source |
| **Medallion** | The bronze/silver/gold layering pattern |
| **Quarantine table** | Where rejected rows are stored with a reason, instead of being dropped |
| **Replayability** | The ability to rebuild all downstream data from retained raw data |
| **SCD** | Slowly Changing Dimension — how a dimension records historical change |
| **Schema-on-read** | Structure is applied when querying, not when writing |
| **Surrogate key** | A system-generated key (e.g. `customer_key`) independent of the business key |
| **Tombstone** | A marker record indicating a deletion |
| **Watermark** | The high-water mark of data already processed |

---

## 11. Exercises

Work these against `../customer-sales-lakehouse`. Each has a **verifiable** answer.

### Level 1 — read and verify

1. Run the pipeline end to end (notebooks `01` → `07`). Confirm the row counts are **8 / 7 / 4** for
   bronze orders, silver orders and `fact_sales`.
2. Confirm `SUM(amount)` in `fact_sales` is exactly **525.00**.
3. Open `data/quality_results/` and confirm three `FAIL` rows and two `PASS` rows, each with
   `failed_record_count` of 1 or 0 respectively.

### Level 2 — modify the data, predict the result first

4. Add a 9th row to `orders.csv`:
   `108,4,1002,2026-02-20,2,50.00,Completed,2026-02-20 09:00:00`.
   **Predict**, then verify: new revenue total, new `fact_sales` row count, and which region now
   appears in the regional breakdown. *(Answer: 575.00, 5 rows, West joins North.)*
5. Change order 103's status from `Cancelled` to `Completed`. Predict the new total.
   *(Answer: 575.00 — 525.00 + 50.00.)*
6. Add `109,2,1099,2026-02-21,1,99.00,Completed,2026-02-21 10:00:00`. Which check now fails that
   previously passed, and what is its `failed_record_count`?
   *(Answer: `invalid_product_reference`, count 1.)*
7. Add `110,2,1001,2026-02-22,1,50.00,Shipped,2026-02-22 10:00:00`. Which check fails?
   *(Answer: `invalid_status`, count 1 — `Shipped` is outside the allowed domain.)*

### Level 3 — build

8. Implement `check_invalid_product_reference()` using a `left_anti` join if it is not already
   present, and add it to `run_all_checks()`.
9. Implement the quarantine pattern: write rejected rows to `data/quarantine/orders/` with a
   `rejection_reason` column. Verify exactly **2** rows land there (105 and 106).
10. Add a consistency check for `amount = quantity * unit_price` and confirm it returns 0 rows.
    Then deliberately corrupt order 104's amount to `80.00` and confirm it returns 1 row.
11. Write a pytest test asserting `fact_sales` revenue equals `525.00`. Break a transformation on
    purpose and confirm the test fails.

### Level 4 — design (write your answer as prose, no code)

12. Make silver orders **incremental**. Which column is your watermark? What breaks because both
    copies of order 107 share `2026-02-15 08:00:00`? How does a MERGE fix it?
13. The business says customer 99 *does* exist but their CRM extract is delayed by a day. Redesign
    the handling of order 106. What does `fact_sales` look like on day 1 and day 2? Does the
    `525.00` figure change, and should it?
14. Customer 1 moves from `North` to `South`. Explain what `dim_customer` should do under SCD Type 1
    versus SCD Type 2, and what each choice does to the regional revenue report for January.
15. Which of the five checks would you classify as blocking versus warning for this project, and
    why? Justify each.

---

## Key takeaways

- **ELT beat ETL** because cloud storage became cheap and compute became elastic — but ETL still
  wins where PII must never land raw.
- **Batch until proven otherwise.** Ask what decision changes if the data is 4 hours old.
- **Bronze is evidence, silver is truth, gold is the answer.** Each layer has exactly one job.
- **Flag bad rows, never delete them.** Deleted problems are invisible problems.
- **Idempotency is not optional** — pipelines get re-run, always.
- **Deduplicate with an explicit, deterministic rule**, not with luck.
- **Separate quality failures from business exclusions.** Order 103 is good data that the rule
  excludes; order 106 is bad data.
- **Always include checks you expect to pass**, so you can tell "clean" from "broken check".
- **Reconcile to a known number.** For this project that number is, and always will be, **525.00**.

---

**Next:** `05_pyspark_deep_dive.md` — the engine that executes everything described here.
