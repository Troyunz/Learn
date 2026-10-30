# Transformation Rules

This document is the contract between layers. Every rule below is implemented in
`src/transformations.py` and applied by the `notebooks/0*.py` scripts.

---

## 1. Raw → Bronze

**Script:** `notebooks/01_bronze_ingestion.py`

| Rule | Detail |
| --- | --- |
| Read with explicit schema | No `inferSchema`. Schemas are declared in the script so a source change fails loudly instead of silently changing types. |
| Preserve source fidelity | No casting, trimming, filtering or de-duplication. Bronze must be reproducible from raw. |
| Add `ingestion_timestamp` | `F.current_timestamp()` — when the job ran. |
| Add `source_file_name` | `F.input_file_name()` — which file each row came from. |
| Add `processing_date` | `F.current_date()` — the logical run date, used for lineage. |
| Write mode | `overwrite` to `data/bronze/<entity>/` as Parquet. |

---

## 2. Bronze → Silver

### 2.1 Common rules (all entities)

| Rule | Implementation |
| --- | --- |
| **Trim whitespace** | `trim()` on every string column. |
| **Collapse internal whitespace** | `regexp_replace(col, '\s+', ' ')` so `"Aisha   Khan"` → `"Aisha Khan"`. |
| **Empty string → NULL** | A zero-length string after trimming is treated as missing, not as a value. |
| **Explicit casting** | IDs → `INT`, money → `DECIMAL(10,2)`, dates → `DATE`, timestamps → `TIMESTAMP`. Unparseable values become `NULL` rather than throwing. |
| **Drop bronze audit columns** | `source_file_name` is dropped in silver; `ingestion_timestamp` / `processing_date` are retained for lineage. |

### 2.2 `silver.customers`

**Script:** `notebooks/02_silver_customers.py`

1. Cast `customer_id` → `INT`, `customer_created_date` → `DATE`.
2. Clean `customer_name` (trim + collapse whitespace).
3. Standardise `region` to **Title Case** (`initcap`) so `"north"`, `"NORTH"` and `"North"` conform.
4. De-duplicate on `customer_id`, keeping the row with the latest `ingestion_timestamp`.
5. Drop rows where `customer_id IS NULL` — a customer without a key cannot be referenced.

### 2.3 `silver.products`

**Script:** `notebooks/03_silver_products.py`

1. Cast `product_id` → `INT`, `unit_price` → `DECIMAL(10,2)`.
2. Clean `product_name` (trim + collapse whitespace).
3. Standardise `category` to **Title Case**.
4. De-duplicate on `product_id`, keeping the latest `ingestion_timestamp`.
5. Drop rows where `product_id IS NULL`.

### 2.4 `silver.orders`

**Script:** `notebooks/04_silver_orders.py`

1. Cast `order_id`, `customer_id`, `product_id`, `quantity` → `INT`;
   `amount` → `DECIMAL(10,2)`; `order_date` → `DATE`; `updated_at` → `TIMESTAMP`.
2. Standardise `status` to **Title Case** → `Completed` / `Cancelled` / `Pending`.
3. **De-duplicate on `order_id`.**
   - Window: `PARTITION BY order_id ORDER BY updated_at DESC, ingestion_timestamp DESC`.
   - Keep `row_number() = 1`, i.e. the most recently updated version of the order.
   - This resolves the duplicated `order_id = 107` and would also correctly apply a
     late-arriving correction, not just an exact duplicate.
4. **Referential integrity:** left-join to `silver.customers` on `customer_id` and record
   whether a match was found.
5. **Add `data_quality_status`** using the first matching rule:

   | Priority | Condition | Value |
   | --- | --- | --- |
   | 1 | No matching customer in `silver.customers` | `Invalid Customer Reference` |
   | 2 | `amount IS NULL` | `Missing Amount` |
   | 3 | otherwise | `Valid` |

6. **Rows are flagged, never deleted.** Silver stays reconcilable with the source system.

---

## 3. Silver → Gold

### 3.1 Dimensions

**Script:** `notebooks/05_gold_dimensions.py`

| Table | Rule |
| --- | --- |
| `dim_customer` | One row per `customer_id`. `customer_key = customer_id` (surrogate key kept simple for this project; a real build would use a monotonic sequence or hash). |
| `dim_product` | One row per `product_id`. `product_key = product_id`. |
| `dim_date` | Generated with `sequence()` across the full min/max `order_date` range in `silver.orders`, so there are no gaps. `date_key = yyyyMMdd` as an `INT`. |

### 3.2 Fact

**Script:** `notebooks/06_gold_fact_sales.py`

`fact_sales` includes an order **only if both** conditions hold:

```
data_quality_status = 'Valid'   AND   status = 'Completed'
```

This deliberately excludes:

- `order_id = 105` — `Pending` **and** `Missing Amount`.
- `order_id = 106` — `Invalid Customer Reference` (customer 99 does not exist).
- `order_id = 103` — `Cancelled`, so it is not recognised revenue.

Joins to dimensions use **inner joins**, which act as a final referential guard: if a
dimension row is missing, the fact row is dropped rather than orphaned.

---

## 4. Quality-flag reference

| Flag | Meaning | Downstream effect |
| --- | --- | --- |
| `Valid` | Passes all silver-level checks. | Eligible for `fact_sales`. |
| `Invalid Customer Reference` | `customer_id` has no match in `silver.customers`. | Excluded from `fact_sales`; raised by the `invalid_customer_reference` check. |
| `Missing Amount` | `amount IS NULL`. | Excluded from `fact_sales`; raised by the `missing_order_amount` check. |

---

## 5. Idempotency

Every write uses `mode("overwrite")`. Re-running any script, or the whole pipeline,
produces the same output — there is no append-based state to corrupt.
