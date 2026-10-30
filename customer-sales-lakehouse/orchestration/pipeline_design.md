# Pipeline Design

End-to-end execution design for the customer-sales lakehouse.

---

## 1. Execution order

```
01_bronze_ingestion
        │
        ├──────────────┬──────────────┐
        ▼              ▼              │
02_silver_customers  03_silver_products
        │              │              │
        ├──────────────┘              │
        ▼                             │
04_silver_orders  ◄───────────────────┘
        │
        ▼
05_gold_dimensions
        │
        ▼
06_gold_fact_sales
        │
        ▼
07_data_quality_checks
```

### Stage-by-stage

| # | Stage | Reads | Writes | Depends on |
| --- | --- | --- | --- | --- |
| 01 | `01_bronze_ingestion.py` | `data/raw/*.csv` | `data/bronze/{customers,products,orders}` | — |
| 02 | `02_silver_customers.py` | `bronze/customers` | `silver/customers` | 01 |
| 03 | `03_silver_products.py` | `bronze/products` | `silver/products` | 01 |
| 04 | `04_silver_orders.py` | `bronze/orders`, `silver/customers` | `silver/orders` | 01, 02 |
| 05 | `05_gold_dimensions.py` | `silver/{customers,products,orders}` | `gold/{dim_customer,dim_product,dim_date}` | 02, 03, 04 |
| 06 | `06_gold_fact_sales.py` | `silver/orders`, all gold dims | `gold/fact_sales` | 04, 05 |
| 07 | `07_data_quality_checks.py` | `silver/{orders,customers}` | `quality_results/` | 02, 04 |

### Parallelism

Stages **02 and 03 are independent** and can run concurrently. Everything else is
strictly sequential because of the data dependencies above.

```
                    ┌─ 02_silver_customers ─┐
01_bronze_ingestion ┤                       ├─ 04 ─ 05 ─ 06 ─ 07
                    └─ 03_silver_products ──┘
```

In a scheduler such as Airflow this maps to:

```python
bronze >> [silver_customers, silver_products]
silver_customers >> silver_orders
silver_products  >> gold_dimensions
silver_orders    >> gold_dimensions >> fact_sales >> quality_checks
```

### Why 04 depends on 02

`04_silver_orders.py` left-joins to `silver.customers` to derive
`data_quality_status`. Running it against a stale or missing customer table would
mislabel valid orders as `Invalid Customer Reference`. The join must therefore read
the **current** silver customers, not bronze.

---

## 2. Idempotency

Every stage writes with `mode("overwrite")` and **fully rebuilds its own output** from
its inputs. There is no append-based state, no sequence counters and no mutable
accumulation.

Consequences:

| Property | Result |
| --- | --- |
| Re-running a stage | Produces identical output. Safe. |
| Re-running the whole pipeline | Produces identical output. Safe. |
| Partial failure mid-write | Spark's `overwrite` commits the output folder atomically enough for local use; the previous content is replaced only on success of the write task set. Re-run the stage to recover. |
| Out-of-order reruns | Unsafe. Always re-run **downstream** stages after an upstream rebuild (see §4). |

Determinism is also protected inside the transformations:

- `deduplicate_by_key` uses `row_number()` over an explicit `ORDER BY`, so the surviving
  row is always the same one. `dropDuplicates` would pick an arbitrary row.
- Surrogate keys are derived from business keys rather than from
  `monotonically_increasing_id()`, which is **not** stable across runs.
- `dim_date` is generated with `sequence()` over the min/max order date, so the same
  input range always yields the same calendar.

The one intentionally non-deterministic element is the audit metadata
(`ingestion_timestamp`, `processing_date`, `run_timestamp`) — these must change per run,
since their purpose is to record *when* a run happened.

---

## 3. Incremental loads

The current build is a **full refresh**, which is the right choice at this data volume.
Below is how each layer would become incremental as the dataset grows.

### Bronze — append-only with a watermark

Read only files that arrived since the last successful run, and append rather than
overwrite:

```python
new_files = [f for f in source_files if f.modified_at > last_watermark]
df.write.mode("append").parquet(BRONZE_ORDERS)
```

Partition bronze by `processing_date` so a bad run can be surgically deleted:

```python
df.write.mode("overwrite") \
  .partitionBy("processing_date") \
  .option("partitionOverwriteMode", "dynamic") \
  .parquet(BRONZE_ORDERS)
```

Dynamic partition overwrite replaces **only** the partitions present in the new data,
which keeps the stage idempotent while remaining incremental.

### Silver — upsert with `MERGE`

Switch the storage format to Delta Lake and replace the full rebuild with a merge on the
business key, applying only the latest version of each record:

```sql
MERGE INTO silver_orders AS target
USING staged_orders      AS source
   ON target.order_id = source.order_id
WHEN MATCHED AND source.updated_at > target.updated_at
    THEN UPDATE SET *
WHEN NOT MATCHED
    THEN INSERT *
```

The existing `deduplicate_by_key` step still runs **before** the merge, because a single
batch can itself contain several versions of one `order_id`, and `MERGE` errors if the
source matches a target row more than once.

### Gold — restated windows

- **Dimensions:** `MERGE` on the business key. Promote `dim_customer` to Slowly Changing
  Dimension Type 2 (add `valid_from`, `valid_to`, `is_current`) so historical facts keep
  pointing at the attribute values that were current at the time of sale.
- **Fact:** orders can be corrected after the fact, so recompute a **trailing window**
  (for example the last 90 days) rather than only brand-new rows, and overwrite just the
  affected date partitions.
- **`dim_date`:** extend the calendar forward on a schedule; never rebuild it from the
  observed order range once it is live, or existing `date_key` values could disappear.

### Watermark state

Incremental loads need persistent state — the high-water mark per source. Store it
outside the data layers (a small control table or a Delta checkpoint), never in the
layer being loaded.

---

## 4. Failure handling

### Principles

1. **Fail fast, fail loudly.** Each stage runs in its own process and raises on error, so
   a failure stops the chain rather than silently producing partial output.
2. **Never half-promote.** A stage either replaces its output completely or leaves the
   previous version in place.
3. **Quality failures are recorded, not fatal — by default.** Stage 07 logs failures and
   writes them to `quality_results/`. Set `FAIL_ON_ERROR = True` in
   `07_data_quality_checks.py` to turn the checks into a hard gate.

### Failure modes and responses

| Failure | Detection | Response |
| --- | --- | --- |
| Source file missing | Spark raises `AnalysisException: Path does not exist` | Stop. Do not write an empty bronze table — that would silently wipe downstream data. Investigate the upstream delivery. |
| Schema drift (new or renamed column) | Explicit schemas mean the new column is ignored or read as NULL | Update the schema in `01_bronze_ingestion.py` and the data dictionary, then re-run from 01. |
| Bad values in a numeric field | Cast yields NULL in silver | Flagged by the quality checks, not fatal. Fix at source or add a parsing rule. |
| Stage crashes mid-run | Non-zero exit code | Re-run that stage, then all downstream stages. |
| Orphaned facts after a dimension rebuild | Row-count warning logged by stage 06 | Re-run 05 then 06 so dimensions and facts are rebuilt from the same silver snapshot. |
| Quality check regresses to FAIL | `quality_results/` row changes `PASS` → `FAIL` | Triage using `sql/02_data_quality_checks.sql`, which lists the offending rows. |

### Retry policy

All stages are idempotent, so **automatic retry is safe** — unlike an append-based
pipeline, a retry cannot duplicate data. Recommended scheduler settings:

| Setting | Value |
| --- | --- |
| Retries | 2 |
| Retry delay | 5 minutes, exponential backoff |
| Execution timeout | 30 minutes per stage |
| On upstream failure | Skip downstream stages (`all_success` trigger rule) |

### Backfill

To reprocess history:

1. Keep `data/raw/` intact — it is the only true source and is never modified.
2. Delete the generated layers (see the runbook's reset command).
3. Re-run the pipeline from stage 01.

Because every stage is a deterministic function of its inputs, a backfill reproduces the
exact same gold tables, with only the audit timestamps differing.

---

## 5. Observability

| Signal | Where it comes from |
| --- | --- |
| Row counts in and out of each stage | Logged by every script via `src/logging_utils.py`. |
| Rows removed by de-duplication | Logged by stage 04. |
| `data_quality_status` breakdown | Logged by stage 04. |
| Rows excluded from the fact table | Logged by stage 06, with a warning if a dimension join dropped eligible rows. |
| Quality check trend | `data/quality_results/`, one row per check per run with a `run_timestamp`. |

Useful alerts to add next: a run that produces **zero** rows in any layer, a sudden drop
in `fact_sales` row count versus the previous run, and any check moving from `PASS` to
`FAIL`.
