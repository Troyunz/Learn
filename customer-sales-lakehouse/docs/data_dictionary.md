# Data Dictionary

All source files live in `data/raw/` and are comma-delimited with a header row.

---

## `customers.csv`

Master list of customers. One row per customer.

| Column | Type (raw) | Type (silver) | Nullable | Description |
| --- | --- | --- | --- | --- |
| `customer_id` | string | `INT` | No | **Business key.** Unique identifier for the customer. |
| `customer_name` | string | `STRING` | No | Full name. Trimmed and collapsed to single spaces in silver. |
| `region` | string | `STRING` | No | Sales region. Standardised to Title Case. Expected domain: `North`, `South`, `East`, `West`. |
| `customer_created_date` | string | `DATE` | No | Date the customer record was created (`yyyy-MM-dd`). |

**Grain:** one row per `customer_id`.

---

## `products.csv`

Master list of sellable products. One row per product.

| Column | Type (raw) | Type (silver) | Nullable | Description |
| --- | --- | --- | --- | --- |
| `product_id` | string | `INT` | No | **Business key.** Unique identifier for the product. |
| `product_name` | string | `STRING` | No | Display name of the product. Trimmed in silver. |
| `category` | string | `STRING` | No | Product category. Standardised to Title Case. Expected domain: `Accessories`, `Hardware`. |
| `unit_price` | string | `DECIMAL(10,2)` | No | List price per unit, in the reporting currency. |

**Grain:** one row per `product_id`.

---

## `orders.csv`

Transactional order records emitted by the source system. May contain duplicates and
late-arriving corrections.

| Column | Type (raw) | Type (silver) | Nullable | Description |
| --- | --- | --- | --- | --- |
| `order_id` | string | `INT` | No | **Business key.** Should be unique — the raw file violates this (see below). |
| `customer_id` | string | `INT` | No | FK to `customers.customer_id`. Not enforced by the source system. |
| `product_id` | string | `INT` | No | FK to `products.product_id`. |
| `order_date` | string | `DATE` | No | Date the order was placed (`yyyy-MM-dd`). |
| `quantity` | string | `INT` | No | Number of units ordered. |
| `amount` | string | `DECIMAL(10,2)` | **Yes** | Gross order value. NULL where the source failed to emit a value. |
| `status` | string | `STRING` | No | Order lifecycle state. Standardised to Title Case. Domain: `Completed`, `Cancelled`, `Pending`. |
| `updated_at` | string | `TIMESTAMP` | No | Last modification time in the source system (`yyyy-MM-dd HH:mm:ss`). Used as the tie-breaker when de-duplicating `order_id`. |

**Grain (intended):** one row per `order_id`.
**Grain (actual, raw):** `order_id` is duplicated for `107`.

---

## Audit columns (bronze layer)

Added to every bronze table by `01_bronze_ingestion.py`.

| Column | Type | Description |
| --- | --- | --- |
| `ingestion_timestamp` | `TIMESTAMP` | Wall-clock time the ingestion job ran. |
| `source_file_name` | `STRING` | Full path of the source file the row came from (`input_file_name()`). |
| `processing_date` | `DATE` | Logical date of the pipeline run; used for lineage and reprocessing. |

---

## Derived columns (silver layer)

| Table | Column | Type | Description |
| --- | --- | --- | --- |
| `silver.orders` | `data_quality_status` | `STRING` | `Valid`, `Invalid Customer Reference`, or `Missing Amount`. Only `Valid` rows flow into `fact_sales`. |

---

## Gold layer

### `dim_customer`

| Column | Type | Description |
| --- | --- | --- |
| `customer_key` | `INT` | **PK.** Surrogate key (derived from `customer_id` in this project). |
| `customer_id` | `INT` | Business key from the source system. |
| `customer_name` | `STRING` | Cleaned customer name. |
| `region` | `STRING` | Standardised region. |
| `customer_created_date` | `DATE` | Customer creation date. |

### `dim_product`

| Column | Type | Description |
| --- | --- | --- |
| `product_key` | `INT` | **PK.** Surrogate key (derived from `product_id`). |
| `product_id` | `INT` | Business key from the source system. |
| `product_name` | `STRING` | Cleaned product name. |
| `category` | `STRING` | Standardised category. |
| `unit_price` | `DECIMAL(10,2)` | List price per unit. |

### `dim_date`

| Column | Type | Description |
| --- | --- | --- |
| `date_key` | `INT` | **PK.** Integer date in `yyyyMMdd` form. |
| `full_date` | `DATE` | The calendar date. |
| `year` | `INT` | Calendar year. |
| `quarter` | `INT` | Calendar quarter (1–4). |
| `month` | `INT` | Month number (1–12). |
| `month_name` | `STRING` | Full month name. |
| `day` | `INT` | Day of month. |
| `day_of_week` | `INT` | Day of week (1 = Sunday). |
| `day_name` | `STRING` | Full day name. |
| `is_weekend` | `BOOLEAN` | True for Saturday and Sunday. |

### `fact_sales`

| Column | Type | Description |
| --- | --- | --- |
| `order_id` | `INT` | Degenerate dimension; natural key of the transaction. |
| `customer_key` | `INT` | **FK** → `dim_customer.customer_key`. |
| `product_key` | `INT` | **FK** → `dim_product.product_key`. |
| `date_key` | `INT` | **FK** → `dim_date.date_key`. |
| `quantity` | `INT` | Units sold (additive). |
| `amount` | `DECIMAL(10,2)` | Recognised revenue (additive). |
| `order_status` | `STRING` | Always `Completed` by construction; retained for traceability. |
