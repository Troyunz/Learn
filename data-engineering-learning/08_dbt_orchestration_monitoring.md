# 08 — dbt, Orchestration & Monitoring (Deep Dive)

> **Companion project:** everything in this guide is illustrated with the hands-on lakehouse
> that lives beside this repo at `../customer-sales-lakehouse`.
> All data is fictional. No credentials, tokens or connection strings appear anywhere —
> only placeholders such as `<storage-account-name>`.

---

## 0. Where this guide fits

By now you can take raw CSVs, land them as bronze Parquet, cleanse them into silver, and
model them into a gold star schema with PySpark (`../customer-sales-lakehouse/notebooks/`).

That is the *transformation* half of data engineering. This guide covers the other half:

| Question | Answered by |
|---|---|
| "How do I express transformations as versioned, tested, documented SQL?" | **dbt** |
| "How do I make the pipeline run reliably, in the right order, on a schedule?" | **Orchestration** |
| "How do I know it ran, and that the output is trustworthy?" | **Monitoring & audit** |
| "How do I ship a change safely from my laptop to production?" | **Git + CI/CD** |

A pipeline that only works when *you* run it by hand is a script. A pipeline that runs on a
schedule, proves its own correctness, records what it did, and can be deployed by someone
else is a **data product**. That difference is most of what a job interview is probing for.

---

## 1. dbt — the analytics engineering layer

### 1.1 What dbt actually is (and is not)

**dbt (data build tool) is a transformation framework for the `T` in ELT.** You write
`SELECT` statements; dbt turns them into tables/views in your warehouse, in dependency order,
with tests and documentation attached.

dbt **is**:
- a SQL compiler + templating engine (Jinja) that resolves references between models
- a DAG builder that works out what must run before what
- a test runner for data assertions
- a documentation & lineage generator

dbt **is not**:
- an orchestrator (it does not schedule itself — ADF/Airflow/Databricks Workflows call it)
- an ingestion tool (it does not move data *into* the warehouse)
- a compute engine (your warehouse — Synapse, Databricks SQL, Snowflake, Postgres — does the work)

> **Why it matters for our project:** in `../customer-sales-lakehouse`, the silver and gold
> layers are PySpark notebooks. The exact same logic can be expressed as dbt models running on
> Databricks SQL or Synapse. Understanding both lets you talk credibly about either stack.

### 1.2 Project anatomy

```
customer_sales_dbt/
├── dbt_project.yml          # project config: name, paths, default materializations
├── profiles.yml             # connection config (lives OUTSIDE the repo in practice)
├── models/
│   ├── staging/
│   │   ├── _sources.yml     # declares raw/bronze tables as sources
│   │   ├── stg_customers.sql
│   │   ├── stg_products.sql
│   │   └── stg_orders.sql
│   ├── intermediate/
│   │   └── int_orders_validated.sql
│   ├── marts/
│   │   ├── dim_customer.sql
│   │   ├── dim_product.sql
│   │   ├── dim_date.sql
│   │   ├── fact_sales.sql
│   │   └── schema.yml       # tests + docs for the mart models
├── seeds/
│   └── valid_order_statuses.csv
├── snapshots/
│   └── customers_snapshot.sql
├── macros/
│   └── cents_to_currency.sql
└── tests/
    └── assert_fact_sales_revenue_is_positive.sql
```

Mapping to the medallion layers you already built:

| Medallion layer | dbt folder | dbt materialization |
|---|---|---|
| bronze (raw Parquet, as-landed) | declared via `source()` | not materialized by dbt |
| silver (cleansed, typed) | `models/staging/`, `models/intermediate/` | `view` or `table` |
| gold (star schema) | `models/marts/` | `table` or `incremental` |

### 1.3 `dbt_project.yml`

```yaml
name: customer_sales_dbt
version: "1.0.0"
config-version: 2

profile: customer_sales          # points at an entry in profiles.yml

model-paths: ["models"]
seed-paths: ["seeds"]
snapshot-paths: ["snapshots"]
macro-paths: ["macros"]
test-paths: ["tests"]

target-path: "target"
clean-targets: ["target", "dbt_packages"]

models:
  customer_sales_dbt:
    staging:
      +materialized: view        # cheap, always fresh
      +schema: silver
    intermediate:
      +materialized: ephemeral   # inlined as a CTE, never persisted
      +schema: silver
    marts:
      +materialized: table       # queried often by BI -> persist it
      +schema: gold
```

### 1.4 `source()` — declaring where raw data comes from

Never hardcode a raw table name inside a model. Declare it once as a *source*, then reference
it. This gives you lineage from the true origin and enables freshness checks.

```yaml
# models/staging/_sources.yml
version: 2

sources:
  - name: bronze
    description: "Raw files landed as-is from the source system, no business logic applied."
    database: lakehouse            # catalog / database name
    schema: bronze
    loaded_at_field: ingestion_timestamp
    freshness:
      warn_after:  {count: 12, period: hour}
      error_after: {count: 24, period: hour}
    tables:
      - name: customers
        description: "Customer master. Grain: one row per customer_id."
        columns:
          - name: customer_id
            description: "Natural key from the source CRM."
            tests: [not_null, unique]
          - name: customer_name
          - name: region
            description: "North / South / West."
          - name: customer_created_date

      - name: products
        description: "Product catalogue. Grain: one row per product_id."
        columns:
          - name: product_id
            tests: [not_null, unique]
          - name: product_name
          - name: category
          - name: unit_price

      - name: orders
        description: >
          Order transactions. Grain is *intended* to be one row per order_id but the
          raw feed is known to contain duplicates — see int_orders_validated.
        columns:
          - name: order_id
            tests: [not_null]       # deliberately NOT unique: raw contains a duplicate
          - name: customer_id
          - name: product_id
          - name: order_date
          - name: quantity
          - name: amount
          - name: status
          - name: updated_at
```

> **Why `not_null` but not `unique` on raw `order_id`?** Because we *know* order 107 appears
> twice in the sample feed. Tests should encode the contract you are actually promising at
> that layer. Raw promises "we landed what we were sent". Gold promises "one row per order".

### 1.5 `ref()` — the function that builds the DAG

`ref('stg_orders')` does two things:
1. compiles to the fully-qualified table/view name for the current environment
2. tells dbt "this model depends on `stg_orders`" — which is how the DAG is inferred

You never write `JOIN silver.stg_orders`. You write `JOIN {{ ref('stg_orders') }}`. That single
discipline is what makes the project portable between dev and prod and makes lineage automatic.

### 1.6 Staging models

```sql
-- models/staging/stg_customers.sql
with source as (
    select * from {{ source('bronze', 'customers') }}
),

renamed as (
    select
        cast(customer_id as int)                  as customer_id,
        trim(customer_name)                       as customer_name,
        initcap(trim(region))                     as region,
        cast(customer_created_date as date)       as customer_created_date
    from source
    where customer_id is not null
)

select * from renamed
```

```sql
-- models/staging/stg_products.sql
with source as (
    select * from {{ source('bronze', 'products') }}
),

renamed as (
    select
        cast(product_id as int)        as product_id,
        trim(product_name)             as product_name,
        trim(category)                 as category,
        cast(unit_price as decimal(10,2)) as unit_price
    from source
    where product_id is not null
)

select * from renamed
```

```sql
-- models/staging/stg_orders.sql
-- Light typing + de-duplication only. No business filtering here;
-- "is this order recognisable revenue?" is a mart-layer decision.
with source as (
    select * from {{ source('bronze', 'orders') }}
),

typed as (
    select
        cast(order_id    as int)            as order_id,
        cast(customer_id as int)            as customer_id,
        cast(product_id  as int)            as product_id,
        cast(order_date  as date)           as order_date,
        cast(quantity    as int)            as quantity,
        cast(amount      as decimal(10,2))  as amount,
        initcap(trim(status))               as status,
        cast(updated_at  as timestamp)      as updated_at
    from source
),

deduplicated as (
    select *
    from (
        select
            typed.*,
            row_number() over (
                partition by order_id
                order by updated_at desc
            ) as rn
        from typed
    )
    where rn = 1
)

select
    order_id, customer_id, product_id, order_date,
    quantity, amount, status, updated_at
from deduplicated
```

> **De-dup pattern to memorise:** `row_number() over (partition by <business key> order by
> <recency column> desc) = 1`. It keeps the newest version of each key. For order 107 both rows
> are byte-identical, so either survives — but the pattern also handles the far more common
> case of a genuinely updated record.

### 1.7 Intermediate model — encoding the validity rules

```sql
-- models/intermediate/int_orders_validated.sql
-- Ephemeral: compiled inline as a CTE, never persisted.
-- Adds validity flags so BOTH fact_sales and the DQ reporting can reuse one definition.
with orders as (
    select * from {{ ref('stg_orders') }}
),

customers as (
    select customer_id from {{ ref('stg_customers') }}
),

products as (
    select product_id from {{ ref('stg_products') }}
),

flagged as (
    select
        o.*,
        (o.amount is not null)                             as has_amount,
        (c.customer_id is not null)                        as has_valid_customer,
        (p.product_id  is not null)                        as has_valid_product,
        (o.status in ('Completed', 'Cancelled', 'Pending')) as has_valid_status
    from orders o
    left join customers c on o.customer_id = c.customer_id
    left join products  p on o.product_id  = p.product_id
)

select
    *,
    (has_amount and has_valid_customer and has_valid_product and has_valid_status) as is_valid
from flagged
```

Applied to our sample feed (after de-duplication there are **7** distinct orders):

| order_id | has_amount | has_valid_customer | has_valid_product | has_valid_status | is_valid | status |
|---|---|---|---|---|---|---|
| 101 | ✅ | ✅ | ✅ | ✅ | ✅ | Completed |
| 102 | ✅ | ✅ | ✅ | ✅ | ✅ | Completed |
| 103 | ✅ | ✅ | ✅ | ✅ | ✅ | Cancelled |
| 104 | ✅ | ✅ | ✅ | ✅ | ✅ | Completed |
| 105 | ❌ | ✅ | ✅ | ✅ | ❌ | Pending |
| 106 | ✅ | ❌ (customer 99) | ✅ | ✅ | ❌ | Completed |
| 107 | ✅ | ✅ | ✅ | ✅ | ✅ | Completed |

### 1.8 Mart models — the star schema

```sql
-- models/marts/dim_customer.sql
{{ config(materialized='table') }}

select
    {{ dbt_utils.generate_surrogate_key(['customer_id']) }} as customer_key,
    customer_id,
    customer_name,
    region,
    customer_created_date
from {{ ref('stg_customers') }}
```

```sql
-- models/marts/dim_product.sql
{{ config(materialized='table') }}

select
    {{ dbt_utils.generate_surrogate_key(['product_id']) }} as product_key,
    product_id,
    product_name,
    category,
    unit_price
from {{ ref('stg_products') }}
```

```sql
-- models/marts/dim_date.sql
{{ config(materialized='table') }}

-- A conformed date dimension generated from the observed order date range.
with bounds as (
    select
        min(order_date) as min_date,
        max(order_date) as max_date
    from {{ ref('stg_orders') }}
),

date_spine as (
    select explode(sequence(
        (select min_date from bounds),
        (select max_date from bounds),
        interval 1 day
    )) as date_day
)

select
    cast(date_format(date_day, 'yyyyMMdd') as int) as date_key,
    date_day                                       as full_date,
    year(date_day)                                 as calendar_year,
    month(date_day)                                as calendar_month,
    date_format(date_day, 'yyyy-MM')               as year_month,
    day(date_day)                                  as day_of_month,
    date_format(date_day, 'EEEE')                  as day_name,
    case when dayofweek(date_day) in (1, 7) then true else false end as is_weekend
from date_spine
```

```sql
-- models/marts/fact_sales.sql
{{ config(
    materialized='incremental',
    unique_key='order_id',
    incremental_strategy='merge'
) }}

-- CANONICAL BUSINESS RULE:
-- fact_sales contains ONLY valid, de-duplicated, Completed orders.
with validated as (
    select * from {{ ref('int_orders_validated') }}
    where is_valid = true
      and status   = 'Completed'

    {% if is_incremental() %}
      and updated_at > (select coalesce(max(updated_at), '1900-01-01') from {{ this }})
    {% endif %}
)

select
    v.order_id,
    dc.customer_key,
    dp.product_key,
    cast(date_format(v.order_date, 'yyyyMMdd') as int) as date_key,
    v.order_date,
    v.quantity,
    v.amount                       as sales_amount,
    v.status,
    v.updated_at,
    current_timestamp()            as dbt_loaded_at
from validated v
join {{ ref('dim_customer') }} dc on v.customer_id = dc.customer_id
join {{ ref('dim_product')  }} dp on v.product_id  = dp.product_id
```

**Expected content of `fact_sales` for the sample data — memorise this:**

| order_id | customer_id | product_id | order_date | quantity | sales_amount |
|---|---|---|---|---|---|
| 101 | 1 | 1003 | 2026-01-05 | 1 | 200.00 |
| 102 | 1 | 1002 | 2026-01-10 | 2 | 50.00 |
| 104 | 3 | 1004 | 2026-02-01 | 1 | 75.00 |
| 107 | 1 | 1003 | 2026-02-15 | 1 | 200.00 |

**Recognised revenue = 200.00 + 50.00 + 75.00 + 200.00 = `525.00`.**

Excluded and why:
- **103** — status `Cancelled`, never recognised revenue.
- **105** — status `Pending` *and* `amount` is null.
- **106** — `customer_id = 99` does not exist in the customer master (orphan reference).
- the second **107** row — duplicate removed in `stg_orders`.

### 1.9 Materializations — choose deliberately

| Materialization | What dbt does | Use when | Cost profile |
|---|---|---|---|
| `view` | `CREATE OR REPLACE VIEW` | thin renames/casts, always-fresh, small data | cheap to build, cost paid on every read |
| `table` | `CREATE OR REPLACE TABLE AS SELECT` | heavy joins read many times, BI-facing marts | full rebuild each run, fast reads |
| `incremental` | build once, then `MERGE`/`INSERT` only new rows | large fact tables, append/late-arriving data | cheapest at scale, most complex |
| `ephemeral` | inlined as a CTE in downstream models | reusable logic you never want to query directly | zero storage, can bloat SQL if overused |

Rules of thumb:
- Start everything as `view`. Promote to `table` when queries get slow.
- Promote to `incremental` only when a full rebuild becomes genuinely expensive — incremental
  models are harder to reason about and need a `unique_key` plus a reliable watermark column
  (`updated_at` in our case).
- Use `ephemeral` for intermediate logic that exists purely to avoid copy-pasting a CTE.

**Incremental gotcha:** on the very first run, and whenever you pass `--full-refresh`, the
`{% if is_incremental() %}` block is skipped and the model rebuilds from scratch. Always test
both paths.

### 1.10 Seeds — small static reference data in version control

```csv
-- seeds/valid_order_statuses.csv
status_code,status_description,is_revenue_recognised
Completed,Order fulfilled and invoiced,true
Cancelled,Order cancelled before fulfilment,false
Pending,Order placed but not yet fulfilled,false
```

```bash
dbt seed
```

Seeds are for **small, static, human-curated** CSVs: status code lists, region groupings,
currency mappings. They are **not** for loading real data volumes — that's ingestion's job.
The win is that changing a business rule becomes a reviewable pull request.

### 1.11 Snapshots — capturing history (SCD Type 2)

Our `customers.csv` is a point-in-time extract. If Aisha Khan moves from `North` to `West`,
a plain overwrite destroys the history, and last quarter's revenue-by-region report silently
changes. Snapshots fix this.

```sql
-- snapshots/customers_snapshot.sql
{% snapshot customers_snapshot %}

{{
    config(
      target_schema='snapshots',
      unique_key='customer_id',
      strategy='check',
      check_cols=['customer_name', 'region']
    )
}}

select
    customer_id,
    customer_name,
    region,
    customer_created_date
from {{ source('bronze', 'customers') }}

{% endsnapshot %}
```

dbt adds `dbt_valid_from` / `dbt_valid_to` columns. A row with `dbt_valid_to is null` is the
current version. Two strategies:

| Strategy | How change is detected | Needs |
|---|---|---|
| `timestamp` | an `updated_at` column moved forward | reliable source timestamp (preferred) |
| `check` | listed columns changed value | no timestamp available (our customers feed) |

### 1.12 Tests — schema tests and data tests

**Schema tests** are declarative, defined in YAML, and reusable:

```yaml
# models/marts/schema.yml
version: 2

models:
  - name: dim_customer
    description: "Customer dimension. Grain: one row per customer_id."
    columns:
      - name: customer_key
        description: "Surrogate key (hash of customer_id)."
        tests: [not_null, unique]
      - name: customer_id
        tests: [not_null, unique]
      - name: region
        tests:
          - accepted_values:
              values: ['North', 'South', 'West']

  - name: dim_product
    description: "Product dimension. Grain: one row per product_id."
    columns:
      - name: product_key
        tests: [not_null, unique]
      - name: product_id
        tests: [not_null, unique]
      - name: unit_price
        tests:
          - dbt_utils.accepted_range:
              min_value: 0
              inclusive: false

  - name: dim_date
    description: "Conformed calendar dimension, one row per day."
    columns:
      - name: date_key
        tests: [not_null, unique]

  - name: fact_sales
    description: >
      Sales fact. Grain: one row per Completed, valid, de-duplicated order.
      Expected recognised revenue for the sample dataset is 525.00.
    columns:
      - name: order_id
        tests: [not_null, unique]
      - name: customer_key
        tests:
          - not_null
          - relationships:
              to: ref('dim_customer')
              field: customer_key
      - name: product_key
        tests:
          - not_null
          - relationships:
              to: ref('dim_product')
              field: product_key
      - name: date_key
        tests:
          - not_null
          - relationships:
              to: ref('dim_date')
              field: date_key
      - name: sales_amount
        tests:
          - not_null
          - dbt_utils.accepted_range:
              min_value: 0
              inclusive: false
      - name: status
        tests:
          - accepted_values:
              values: ['Completed']
```

The four built-in schema tests, and what each one is really protecting you from:

| Test | Protects against | Example failure in our data |
|---|---|---|
| `unique` | duplicate grain → double-counted revenue | raw `order_id` 107 twice |
| `not_null` | missing mandatory fact → silent under-count | `amount` null on order 105 |
| `accepted_values` | unexpected categories breaking downstream logic | a new status like `Refunded` |
| `relationships` | orphan foreign keys → joins silently drop rows | order 106 → customer 99 |

**Data tests (singular tests)** are a SQL query that must return **zero rows** to pass:

```sql
-- tests/assert_fact_sales_revenue_is_positive.sql
-- Fails if any Completed order carries a non-positive amount.
select
    order_id,
    sales_amount
from {{ ref('fact_sales') }}
where sales_amount <= 0
```

```sql
-- tests/assert_no_cancelled_or_pending_in_fact.sql
-- Guards the canonical business rule: fact_sales is Completed-only.
select
    order_id,
    status
from {{ ref('fact_sales') }}
where status <> 'Completed'
```

```sql
-- tests/assert_sample_revenue_total.sql
-- Regression test pinned to the known sample dataset.
-- Expected recognised revenue is exactly 525.00.
select
    sum(sales_amount) as total_revenue
from {{ ref('fact_sales') }}
having sum(sales_amount) <> 525.00
```

> **Why pin a magic number?** On a *fixed sample dataset* this is a powerful regression test:
> if someone "improves" the filter logic and revenue moves, CI fails immediately. In production
> with changing data you would replace it with a relative check (e.g. "revenue did not move more
> than 30% day-over-day").

**Severity.** Not every failure should stop the pipeline:

```yaml
      - name: region
        tests:
          - accepted_values:
              values: ['North', 'South', 'West']
              config:
                severity: warn        # log it, keep going
```

Use `error` for anything that would corrupt a number a human reports on. Use `warn` for
things you want visibility of but that shouldn't wake anyone at 03:00.

### 1.13 Documentation and lineage

```yaml
  - name: fact_sales
    description: >
      One row per Completed order. Excludes Cancelled and Pending orders, orders with a
      missing amount, orders referencing an unknown customer or product, and duplicate
      order_id rows (latest updated_at wins).
```

```bash
dbt docs generate
dbt docs serve     # browsable site: model descriptions, columns, tests, and the DAG
```

Two reasons this matters far more than it sounds:
1. **Lineage/DAG** — "if I change `stg_orders`, what breaks?" is answered visually. dbt derives
   the graph from `ref()`/`source()`, so it cannot drift out of date like a hand-drawn diagram.
2. **Shared vocabulary** — when Finance asks "what counts as revenue?", you point at the model
   description instead of re-explaining it. Keep these descriptions aligned with
   `../customer-sales-lakehouse/docs/transformation_rules.md`.

The DAG for our project:

```
source: bronze.customers ──> stg_customers ──┬──> dim_customer ──┐
source: bronze.products  ──> stg_products  ──┼──> dim_product  ──┤
source: bronze.orders    ──> stg_orders    ──┼──> dim_date      ──┼──> fact_sales
                                             └──> int_orders_validated ─┘
```

### 1.14 Environments & profiles

`profiles.yml` holds connection details and lives **outside** the repo (by default in
`~/.dbt/`). Secrets come from environment variables — never literals.

```yaml
# ~/.dbt/profiles.yml  (NOT committed to git)
customer_sales:
  target: dev
  outputs:
    dev:
      type: databricks
      host: "{{ env_var('DBT_HOST') }}"
      http_path: "{{ env_var('DBT_HTTP_PATH') }}"
      token: "{{ env_var('DBT_TOKEN') }}"
      catalog: lakehouse_dev
      schema: dbt_<your-username>
      threads: 4

    prod:
      type: databricks
      host: "{{ env_var('DBT_HOST') }}"
      http_path: "{{ env_var('DBT_HTTP_PATH_PROD') }}"
      token: "{{ env_var('DBT_TOKEN') }}"
      catalog: lakehouse_prod
      schema: gold
      threads: 8
```

Key conventions:
- Every developer gets their **own dev schema** (`dbt_aisha`, `dbt_ben`) so nobody clobbers
  anyone else's tables.
- `dbt run --target prod` is only ever executed by CI/CD, never from a laptop.
- `threads` controls how many independent DAG nodes run concurrently.

### 1.15 The dbt commands you will actually type

```bash
dbt deps                     # install packages from packages.yml (e.g. dbt_utils)
dbt debug                    # verify the connection and profile resolve
dbt seed                     # load seed CSVs
dbt run                      # build all models
dbt run --select fact_sales  # build one model
dbt run --select +fact_sales # build it AND everything it depends on
dbt run --select fact_sales+ # build it AND everything that depends on it
dbt run --select state:modified+ --defer --state ./prod_artifacts  # CI: changed models only
dbt test                     # run all tests
dbt test --select fact_sales # test one model
dbt build                    # run + test + seed + snapshot, node by node, in DAG order
dbt snapshot                 # capture SCD2 history
dbt source freshness         # check how stale the sources are
dbt docs generate && dbt docs serve
dbt run --full-refresh       # force incremental models to rebuild from scratch
```

> **Prefer `dbt build` over `dbt run && dbt test`.** `build` tests each model *immediately
> after* it is created, so a broken model stops its children from being built on bad data.
> `run` then `test` would happily propagate garbage through the whole DAG first.

### 1.16 Porting our PySpark logic to dbt — side by side

```python
# ../customer-sales-lakehouse/notebooks/06_gold_fact_sales.py  (PySpark)
from pyspark.sql import functions as F
from pyspark.sql.window import Window

dedup_window = Window.partitionBy("order_id").orderBy(F.col("updated_at").desc())

fact_sales = (
    silver_orders
    .withColumn("rn", F.row_number().over(dedup_window))
    .filter(F.col("rn") == 1)
    .drop("rn")
    .filter(F.col("status") == "Completed")
    .filter(F.col("amount").isNotNull())
    .join(dim_customer, "customer_id", "inner")   # drops order 106 (customer 99)
    .join(dim_product, "product_id", "inner")
    .withColumn("date_key", F.date_format("order_date", "yyyyMMdd").cast("int"))
    .select(
        "order_id", "customer_key", "product_key", "date_key",
        "order_date", "quantity",
        F.col("amount").alias("sales_amount"), "status",
    )
)

assert fact_sales.count() == 4
assert float(fact_sales.agg(F.sum("sales_amount")).collect()[0][0]) == 525.00
```

Same rules, same answer (`4` rows, `525.00`), different engine. The lesson: **business logic
is the asset; the framework is an implementation detail.**

---

## 2. Orchestration

### 2.1 Why you need an orchestrator

Running `01_bronze_ingestion.py` … `07_data_quality_checks.py` by hand works for a demo.
In production you need answers to:

- What runs first, and what may run in parallel? (**dependencies**)
- When does it start? (**scheduling**)
- What if step 4 fails transiently? (**retries & backoff**)
- What if step 4 fails permanently? (**alerting + failure isolation**)
- What if we need to recompute January? (**backfills**)
- Who ran what, when, with what outcome? (**run logs & audit**)
- How do we stop two runs colliding? (**concurrency control**)

An orchestrator is the component that owns all of those answers.

### 2.2 DAGs and task dependencies

A **DAG** (Directed Acyclic Graph) is the orchestration unit: tasks (nodes) with directed
dependencies (edges) and no cycles. Our project's DAG:

```
                           ┌──> silver_customers ──> gold_dim_customer ──┐
ingest_bronze ──> validate ├──> silver_products  ──> gold_dim_product  ──┼──> gold_fact_sales ──> dq_checks ──> publish
                           └──> silver_orders ────> gold_dim_date      ──┘
```

Observations worth internalising:
- The three silver tasks are **independent** → run them in parallel. Wall-clock time drops to
  the slowest branch, not the sum.
- `gold_fact_sales` is a **join point** — it waits for all three dimensions.
- `dq_checks` runs **after** gold but **before** `publish`. That ordering is the whole point:
  you validate before anyone consumes the data.
- `publish` is a **gate**: it only flips the "data is ready" marker if DQ passed.

**Task granularity:** one task per logical, independently-retryable unit of work. Too coarse
("run_everything.py") and a failure forces you to redo everything. Too fine (one task per
column) and the orchestrator overhead dominates. One task per medallion table is a good default.

**Idempotency** is the non-negotiable property: re-running a task for the same logical date
must produce the same result, not duplicate rows. Achieve it with overwrite-by-partition or
`MERGE` on a business key — never bare `INSERT`.

### 2.3 Tool comparison — ADF vs Databricks Workflows vs Airflow

| Dimension | Azure Data Factory (ADF) | Databricks Workflows | Apache Airflow |
|---|---|---|---|
| **Primary strength** | Managed data movement: 90+ connectors, copy at scale, low-code UI | Native to the lakehouse; zero-friction notebook/dbt/Delta Live Tables orchestration | Maximum flexibility; Python-native; huge provider ecosystem |
| **Authoring model** | Visual drag-and-drop pipelines (JSON underneath) | UI job definitions or YAML/Terraform/REST | Python code defining DAG objects |
| **Best at** | Ingestion — pulling from on-prem SQL, SFTP, SaaS APIs into ADLS | Transformation — orchestrating Spark/SQL inside Databricks | Complex cross-system dependencies, custom branching, non-data tasks |
| **Scheduling** | Trigger types: schedule, tumbling window, event (blob created) | Cron schedules, file arrival triggers, continuous mode | Cron / timetables, rich catch-up & backfill semantics |
| **Retries** | Per-activity retry count + interval | Per-task retries, optional "retry on timeout" | Per-task `retries` + `retry_delay` + `retry_exponential_backoff` |
| **Monitoring** | ADF Monitor blade, Azure Monitor, Log Analytics | Job run UI, task-level Spark UI, system tables | Web UI with Grid/Graph/Gantt views, extensive logs |
| **Weaknesses** | Weak at complex logic; JSON diffs are painful to review; limited native testing | Databricks-centric — awkward for orchestrating non-Databricks systems | You operate it (scheduler, workers, metadata DB) unless you buy a managed service; steeper learning curve |
| **Cost model** | Per activity run + data movement (DIU-hours) | Jobs-compute DBUs (cheaper than all-purpose compute) | Infrastructure cost + engineering time (or managed-service fee) |
| **Pick it when…** | You must *move* data from many heterogeneous sources into Azure | Your transformations already live in Databricks and you want the least moving parts | You need sophisticated dependency logic across many tools/clouds, or want everything as Python in Git |

**The pragmatic enterprise Azure pattern** — and the one to describe in an interview:

```
ADF  → orchestrates ingestion (source systems -> ADLS Gen2 bronze)
     → then triggers a Databricks job
Databricks Workflows → orchestrates bronze -> silver -> gold (notebooks and/or dbt)
Azure Monitor / Log Analytics → collects logs, metrics and alerts from both
```
Each tool does what it is genuinely best at, and the handoff point is one clean trigger.

**Serverless alternatives** worth knowing by name: *Azure Synapse Pipelines* (the same engine as
ADF, embedded in Synapse) and *Azure Logic Apps* (event/workflow glue, not a data orchestrator).

### 2.4 Scheduling with cron

```
┌───────────── minute (0-59)
│ ┌─────────── hour (0-23)
│ │ ┌───────── day of month (1-31)
│ │ │ ┌─────── month (1-12)
│ │ │ │ ┌───── day of week (0-6, Sunday = 0)
│ │ │ │ │
* * * * *
```

| Expression | Meaning | Typical use |
|---|---|---|
| `0 2 * * *` | 02:00 every day | nightly batch — our default |
| `0 */4 * * *` | every 4 hours | intraday refresh |
| `0 6 * * 1` | 06:00 every Monday | weekly reporting snapshot |
| `30 1 1 * *` | 01:30 on the 1st | monthly close |
| `0 2 * * 1-5` | 02:00 weekdays only | business-day-only feeds |

Scheduling rules learned the hard way:
- **Always be explicit about the timezone.** Many schedulers default to UTC. A `0 2 * * *` job
  in UTC fires at 03:00 or 04:00 UK local depending on daylight saving — and your "daily"
  partition boundary moves with it.
- **Schedule after the source is ready**, not "early to be safe". Prefer an *event* trigger
  (file landed) or a sensor over a hopeful fixed time.
- **Stagger start times.** Ten pipelines all at `0 0 * * *` will fight for the same compute.
- **Set a timeout.** A hung task that never fails is worse than one that fails fast.

### 2.5 Retries and backoff

Classify every failure before deciding retry policy:

| Failure class | Example | Retry? |
|---|---|---|
| **Transient** | network blip, throttling (HTTP 429), cluster still starting | ✅ yes, with backoff |
| **Resource** | out-of-memory, disk full | ⚠️ only after changing the resource — a blind retry just burns money |
| **Data** | schema changed, mandatory column null | ❌ no — retrying produces the identical failure; alert a human |
| **Logic** | bug in your transformation | ❌ no — fix the code |

**Exponential backoff with jitter** is the standard pattern: wait `base × 2^attempt` plus a
small random amount, so that retries from many tasks don't synchronise into a thundering herd.

```python
# Conceptual retry helper — plain Python 3.11, no cloud dependency.
import random
import time
from typing import Callable, TypeVar

T = TypeVar("T")


class TransientError(Exception):
    """Raised for failures that are safe to retry."""


def retry_with_backoff(
    operation: Callable[[], T],
    max_attempts: int = 4,
    base_delay_seconds: float = 2.0,
    max_delay_seconds: float = 60.0,
) -> T:
    """Retry `operation` on TransientError using exponential backoff with jitter."""
    last_error: Exception | None = None

    for attempt in range(max_attempts):
        try:
            return operation()
        except TransientError as exc:
            last_error = exc
            if attempt == max_attempts - 1:
                break
            delay = min(base_delay_seconds * (2 ** attempt), max_delay_seconds)
            delay += random.uniform(0, delay * 0.1)   # jitter
            print(f"Attempt {attempt + 1} failed: {exc}. Retrying in {delay:.1f}s")
            time.sleep(delay)

    raise RuntimeError(f"Operation failed after {max_attempts} attempts") from last_error
```

Retry delays for `base_delay_seconds=2`: attempt 1 → ~2s, attempt 2 → ~4s, attempt 3 → ~8s.

> **Retries are only safe if the task is idempotent.** If `ingest_bronze` appends, three
> retries give you triple the rows. Make it overwrite the target partition instead.

### 2.6 Backfills

A **backfill** is re-running a pipeline for historical logical dates — because you fixed a bug,
added a column, or received corrected source data.

Design requirements for painless backfills:

1. **Parameterise the processing date.** No `date.today()` buried in a notebook.
   ```python
   import argparse
   from datetime import date

   parser = argparse.ArgumentParser()
   parser.add_argument("--processing-date", type=date.fromisoformat, default=date.today())
   args = parser.parse_args()
   ```
2. **Partition by that date** so a rerun replaces exactly one partition.
   ```python
   (df.write
      .mode("overwrite")
      .partitionBy("processing_date")
      .option("partitionOverwriteMode", "dynamic")
      .parquet("data/bronze/orders"))
   ```
3. **Bound the concurrency.** Backfilling 400 days at once will saturate your cluster and may
   hit source-system rate limits. Run 3–5 dates in parallel.
4. **Backfill downstream in DAG order.** Rebuilding silver without rebuilding gold leaves the
   layers inconsistent — the worst possible state.
5. **Announce it.** Dashboards will change while a backfill is in flight. Tell consumers first.

```bash
# Conceptual backfill loop for our local project.
for d in 2026-01-05 2026-01-10 2026-02-01 2026-02-15; do
  python notebooks/01_bronze_ingestion.py --processing-date "$d"
  python notebooks/04_silver_orders.py    --processing-date "$d"
  python notebooks/06_gold_fact_sales.py  --processing-date "$d"
done
```

### 2.7 Alerting

Alert on **symptoms users care about**, not on every event.

| Signal | Severity | Route to | Why |
|---|---|---|---|
| Pipeline failed after all retries | 🔴 Critical | on-call channel + email | data will be missing/stale |
| DQ check `error` severity failed | 🔴 Critical | on-call + data owner | numbers would be wrong |
| Pipeline still running past SLA | 🟠 Warning | team channel | report may miss its deadline |
| DQ check `warn` severity failed | 🟡 Info | daily digest | trend to watch |
| Row count deviates >30% vs 7-day average | 🟠 Warning | team channel | silent upstream change |
| Source freshness exceeded | 🟠 Warning | team channel + source owner | upstream is late |
| Pipeline succeeded | ⚪ None | run log only | success is not an event |

A good alert message contains: **what** failed, **which** pipeline and run id, **when**,
**which** logical date is affected, the **impact** in business terms, and a **link** to the
runbook (`../customer-sales-lakehouse/docs/runbook.md`).

```
🔴 FAILED — customer_sales_gold_load
Run id      : 2026-02-15T02:00:00Z / run-8841
Task        : gold_fact_sales
Logical date: 2026-02-15
Error       : relationships test failed — 1 order references an unknown customer_id
Impact      : Daily Sales dashboard will not refresh for 2026-02-15
Runbook     : docs/runbook.md#invalid-customer-reference
```

Avoid **alert fatigue**: if an alert fires every day and everyone ignores it, it is not an
alert, it is noise. Either fix the underlying cause or downgrade it to a digest.

### 2.8 Run logs and observability

Three distinct things, often confused:

| | What it is | Example | Where it lives |
|---|---|---|---|
| **Logs** | timestamped event text | "Wrote 7 rows to silver/orders" | Log Analytics / driver logs / stdout |
| **Metrics** | numeric time series | `rows_written = 7`, `duration_seconds = 42` | audit table / Azure Monitor |
| **Traces/lineage** | the path a record took | bronze.orders → silver → fact_sales | dbt docs / Purview / audit table |

Good logging practice, as implemented in `../customer-sales-lakehouse/src/logging_utils.py`:

```python
import logging
import sys


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """Return a configured logger. Structured, timestamped, no secrets."""
    logger = logging.getLogger(name)
    if logger.handlers:                     # avoid duplicate handlers on re-import
        return logger

    logger.setLevel(level)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))
    logger.addHandler(handler)
    return logger


log = get_logger("gold.fact_sales")
log.info("stage=start layer=gold table=fact_sales processing_date=2026-02-15")
log.info("stage=dedup rows_in=8 rows_out=7 duplicates_removed=1")
log.info("stage=filter rule=completed_only rows_in=7 rows_out=5")
log.info("stage=filter rule=valid_references rows_in=5 rows_out=4")
log.info("stage=end   rows_written=4 total_revenue=525.00 status=SUCCESS")
```

Rules:
- **Never log secrets or PII.** Log `customer_id=1`, not `customer_name=Aisha Khan`.
- **Log counts at every stage boundary.** `rows_in` / `rows_out` per stage is how you diagnose
  "where did my rows go?" without re-running anything.
- **Use `key=value` pairs.** They are trivially parseable into metrics later.
- **Log the processing date and run id** on every line so logs can be correlated.
