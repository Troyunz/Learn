# Data Quality Rules

The quality framework runs **after** the gold layer is built, against `silver.orders` and
`silver.customers`. It is implemented in `src/quality_checks.py` and executed by
`notebooks/07_data_quality_checks.py`.

Each check returns a tuple of `(check_name, status, failed_count)` where `status` is
`PASS` when `failed_count == 0` and `FAIL` otherwise. Results are written to
`data/quality_results/` as Parquet.

---

## Result schema

| Column | Type | Description |
| --- | --- | --- |
| `check_name` | `STRING` | Stable identifier for the check. |
| `status` | `STRING` | `PASS` or `FAIL`. |
| `failed_count` | `LONG` | Number of offending rows. |
| `run_timestamp` | `TIMESTAMP` | When the check batch executed. |

---

## Checks

### 1. `duplicate_order_id`

**Rule:** `order_id` must be unique in the orders dataset.

```sql
SELECT order_id, COUNT(*) AS row_count
FROM orders
GROUP BY order_id
HAVING COUNT(*) > 1;
```

- **Failed count:** number of `order_id` values appearing more than once.
- **Severity:** Critical — duplicates double-count revenue.
- **Expected on raw data:** `1` failure (`order_id = 107`).
- **Expected on silver data:** `0` — de-duplication has already run.

---

### 2. `missing_order_amount`

**Rule:** `amount` must not be NULL for any order.

```sql
SELECT COUNT(*) FROM orders WHERE amount IS NULL;
```

- **Failed count:** number of rows with a NULL `amount`.
- **Severity:** High — a NULL measure silently understates revenue in `SUM()`.
- **Expected:** `1` failure (`order_id = 105`).
- **Handling:** flagged as `Missing Amount` in silver and excluded from `fact_sales`.

---

### 3. `invalid_customer_reference`

**Rule:** every `orders.customer_id` must exist in `customers.customer_id`.

```sql
SELECT COUNT(*)
FROM orders o
LEFT JOIN customers c ON o.customer_id = c.customer_id
WHERE c.customer_id IS NULL;
```

- **Failed count:** number of orphaned order rows.
- **Severity:** Critical — orphan facts break dimension joins and lose attribution.
- **Expected:** `1` failure (`order_id = 106`, `customer_id = 99`).
- **Handling:** flagged as `Invalid Customer Reference` in silver and excluded from `fact_sales`.

---

### 4. `invalid_status`

**Rule:** `status` must be one of the allowed domain values.

Allowed domain: `Completed`, `Cancelled`, `Pending`.

```sql
SELECT COUNT(*)
FROM orders
WHERE status IS NULL
   OR status NOT IN ('Completed', 'Cancelled', 'Pending');
```

- **Failed count:** number of rows with an out-of-domain or NULL status.
- **Severity:** Medium — unknown statuses make revenue recognition ambiguous.
- **Expected:** `0` failures after silver standardisation.
- **Note:** this check is run on **standardised** status values. Running it against raw
  data would also flag casing variants such as `"completed"`.

---

## Expected results summary

Running `notebooks/07_data_quality_checks.py` against **silver** should produce:

| `check_name` | `status` | `failed_count` |
| --- | --- | --- |
| `duplicate_order_id` | `PASS` | 0 |
| `missing_order_amount` | `FAIL` | 1 |
| `invalid_customer_reference` | `FAIL` | 1 |
| `invalid_status` | `PASS` | 0 |

The two `FAIL` results are **expected and intentional** — they prove the framework detects
the defects planted in `data/raw/orders.csv`. The pipeline does not abort on these
failures; it records them so they can be tracked over time.

---

## Extending the framework

Good next checks to add as an exercise:

| Check | Rule |
| --- | --- |
| `invalid_product_reference` | Every `orders.product_id` must exist in `products`. |
| `negative_quantity` | `quantity > 0`. |
| `negative_amount` | `amount >= 0`. |
| `amount_vs_unit_price` | `amount` should reconcile to `quantity * unit_price` within tolerance. |
| `future_order_date` | `order_date <= current_date`. |
| `duplicate_customer_id` | `customer_id` must be unique in `customers`. |
| `freshness` | `MAX(updated_at)` must be within the expected SLA window. |

Add the function to `src/quality_checks.py`, register it in `ALL_CHECKS`, and it will be
picked up automatically by `notebooks/07_data_quality_checks.py`.
