# 00 — Data Engineering Roadmap (Deep Dive)

> **Who this is for:** a motivated beginner who wants to become *job-ready* as a data engineer, not just "aware of" the tools.
>
> **Companion project:** every guide in this repository is anchored to a hands-on project called **`customer-sales-lakehouse`**, which lives as a *sibling folder* of this learning repo. From inside this repo you refer to it as `../customer-sales-lakehouse`.

---

## Table of contents

1. [What data engineering actually is](#1-what-data-engineering-actually-is)
2. [How it differs from neighbouring roles](#2-how-it-differs-from-neighbouring-roles)
3. [The anatomy of a data platform](#3-the-anatomy-of-a-data-platform)
4. [The companion project: `customer-sales-lakehouse`](#4-the-companion-project-customer-sales-lakehouse)
5. [Why the learning order is SQL → modelling → Linux/Git → ETL → Spark → cloud → Databricks → orchestration → governance](#5-why-the-learning-order-is-this-order)
6. [The 9-month roadmap](#6-the-9-month-roadmap)
7. [Expected outcomes per phase](#7-expected-outcomes-per-phase)
8. [Weekly study habits that actually work](#8-weekly-study-habits-that-actually-work)
9. [How to know you are job-ready](#9-how-to-know-you-are-job-ready)
10. [Common traps](#10-common-traps-and-how-to-avoid-them)

---

## 1. What data engineering actually is

A data engineer **builds and operates the systems that move data from where it is produced to where it is consumed, in a shape that can be trusted.**

Break that sentence apart, because each clause is a whole job area:

| Clause | What it means in practice | Example from our project |
|---|---|---|
| *"systems that move data"* | Pipelines: scheduled or event-driven code that reads a source and writes a target | `notebooks/01_bronze_ingestion.py` reads `data/raw/orders.csv` and writes Parquet |
| *"from where it is produced"* | Source systems: operational databases, SaaS APIs, files, event streams | The three CSV extracts: `customers.csv`, `products.csv`, `orders.csv` |
| *"to where it is consumed"* | Warehouses, lakehouses, BI tools, ML feature stores | The `gold` star schema queried by `sql/` reporting queries |
| *"in a shape that can be trusted"* | Modelling, typing, de-duplication, validation, reconciliation | The rule that recognised revenue is **exactly 525.00** |

### The mental model: data as a supply chain

Think of a factory.

```
  SUPPLIERS            WAREHOUSE            ASSEMBLY LINE        SHOWROOM
 (source systems)      (raw/bronze)        (silver → gold)     (BI / ML / API)
      │                     │                     │                   │
   messy, late,        stored as-is,        cleaned, typed,     trustworthy,
   inconsistent        never mutated        conformed, joined    fast to query
```

A data engineer owns the warehouse and the assembly line. Nobody thanks you when the showroom looks good — but everyone notices when a number is wrong. **Your product is trust, delivered on a schedule.**

### What a data engineer does on a normal Tuesday

- Investigates why last night's pipeline run produced 0 rows for one region.
- Adds a column to a silver table without breaking three downstream gold tables.
- Writes a data-quality check that fails the run when duplicate primary keys appear.
- Reviews a colleague's pull request that changes a join condition.
- Explains to an analyst why "revenue" in dashboard A differs from dashboard B (usually: different grain or different filter on `status`).
- Tunes a Spark job that went from 4 minutes to 40 minutes after a data volume spike.

Notice how much of that is **SQL, modelling, debugging, and communication** — not "learning a new tool".

---

## 2. How it differs from neighbouring roles

| | **Data Engineer** | **Data Analyst** | **Data Scientist** | **Software Engineer** |
|---|---|---|---|---|
| Primary question | *"Is the data available, correct, timely and cheap to query?"* | *"What happened and why?"* | *"What will happen, and what should we do?"* | *"Does the application behave correctly for users?"* |
| Main artefact | Pipelines, tables, schemas, tests | Dashboards, reports, ad-hoc analysis | Models, experiments, features | Applications, services, APIs |
| Core languages | SQL, Python, Bash | SQL, Excel, DAX/BI tooling | Python/R, SQL | Any (Java, C#, TS, Python…) |
| Optimises for | Correctness, reliability, cost, latency | Insight and clarity | Predictive accuracy and business lift | Correctness, latency, maintainability |
| Typical failure | "The nightly load silently dropped 10% of orders" | "The chart answered the wrong question" | "The model worked offline but not in production" | "The release broke checkout" |
| Data volume mindset | Batch of millions/billions of rows | Aggregates, thousands of rows | Samples and feature sets | One request at a time |
| State ownership | Owns the *history* of data | Consumes current state | Consumes curated features | Owns *current* application state |

### The overlaps are the interesting part

- **DE ∩ SWE**: version control, testing, code review, CI/CD, modularity, logging. A data engineer *is* a software engineer whose domain is data. This is exactly why `03_linux_git_deep_dive.md` exists in this repo.
- **DE ∩ Analyst**: SQL and data modelling. The difference is that an analyst writes SQL to *answer* a question; an engineer writes SQL (and DDL) to *define* the asset many people will query.
- **DE ∩ DS**: feature pipelines and reproducibility. A scientist's notebook is an experiment; an engineer's job is to make it run every night, reliably, on fresh data.

### A one-line distinction worth memorising

> Analysts and scientists **consume** data models. Data engineers **are accountable for** them.

---

## 3. The anatomy of a data platform

Everything you will learn fits into one of these layers. Keep this picture in your head — it is the map for the whole 9 months.

```
┌──────────────────────────────────────────────────────────────────────┐
│ 9. GOVERNANCE        lineage · catalog · access control · PII · cost  │
├──────────────────────────────────────────────────────────────────────┤
│ 8. ORCHESTRATION     schedules · dependencies · retries · alerting    │
├──────────────────────────────────────────────────────────────────────┤
│ 7. COMPUTE           PySpark · Databricks · Delta Lake · SQL engines  │
├──────────────────────────────────────────────────────────────────────┤
│ 6. TRANSFORMATION    bronze → silver → gold · business rules · tests  │
├──────────────────────────────────────────────────────────────────────┤
│ 5. STORAGE + MODEL   Parquet/Delta · star schema · partitions · SCDs  │
├──────────────────────────────────────────────────────────────────────┤
│ 4. INGESTION         batch · incremental · CDC · schema handling      │
├──────────────────────────────────────────────────────────────────────┤
│ 3. SOURCES           OLTP DBs · APIs · files · events                 │
└──────────────────────────────────────────────────────────────────────┘
        ↑ supported at every layer by: SQL · Python · Linux · Git
```

The bottom-to-top arrow matters: **SQL, Python, Linux and Git are not "a phase you finish"** — they are the substrate of every layer above. That is why they come first in the roadmap and get the deepest guides.

---

## 4. The companion project: `customer-sales-lakehouse`

All examples across these guides use the same fictional dataset and the same architecture, so that concepts reinforce each other instead of competing for your memory.

### Architecture (local medallion lakehouse)

```
 data/raw/*.csv      bronze/           silver/             gold/            sql/
 ───────────────►  ──────────────►  ──────────────────►  ─────────────►  ──────────
   CSV extracts     Parquet,          cleansed, typed,     star schema     reporting
   landed as-is     1:1 with raw      de-duplicated,       dim_* + fact_*  queries
                    + audit cols      quarantined          surrogate keys
```

- **Bronze** — faithful copy of the source, converted to Parquet, with audit columns (`_ingested_at`, `_source_file`). Never edited. If downstream logic is wrong, you can always rebuild from bronze.
- **Silver** — one row per business entity at a declared grain, correct data types, de-duplicated, referential integrity enforced, bad rows quarantined rather than silently dropped.
- **Gold** — dimensional star schema built for consumption: `dim_customer`, `dim_product`, `dim_date`, `fact_sales`.

### Project layout

```
../customer-sales-lakehouse/
├── config/
│   ├── dev.yml                 # paths, thresholds, run options for local dev
│   └── prod.yml                # same keys, production values
├── data/
│   └── raw/
│       ├── customers.csv
│       ├── products.csv
│       └── orders.csv
├── docs/                       # architecture notes, data dictionary
├── notebooks/
│   ├── 01_bronze_ingestion.py
│   ├── 02_silver_customers.py
│   ├── 03_silver_products.py
│   ├── 04_silver_orders.py
│   ├── 05_gold_dimensions.py
│   ├── 06_gold_fact_sales.py
│   └── 07_data_quality_checks.py
├── orchestration/
│   └── pipeline_design.md      # DAG, dependencies, SLAs, failure handling
├── sql/                        # reporting + reconciliation queries
├── src/
│   ├── config.py               # load dev.yml / prod.yml
│   ├── ingestion.py            # read raw → write bronze
│   ├── transformations.py      # bronze → silver → gold logic
│   ├── quality_checks.py       # reusable assertions
│   └── logging_utils.py        # structured run logging
└── tests/                      # pytest unit tests over transformations
```

### The fictional source data (memorise this — it recurs in every guide)

**`data/raw/customers.csv`**

```
customer_id,customer_name,region,customer_created_date
1,Aisha Khan,North,2025-10-01
2,Ben Smith,South,2025-11-12
3,Carla Jones,North,2025-12-07
4,David Lee,West,2026-01-15
```

**`data/raw/products.csv`**

```
product_id,product_name,category,unit_price
1001,Laptop Bag,Accessories,50.00
1002,Wireless Mouse,Accessories,25.00
1003,Monitor,Hardware,200.00
1004,Keyboard,Accessories,75.00
```

**`data/raw/orders.csv`**

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

### Deliberate data-quality issues

These are planted on purpose so you can practise detection and remediation:

| # | Issue | Where | Count |
|---|---|---|---|
| 1 | Duplicate `order_id` | `107` appears twice with identical `updated_at` | exactly 1 |
| 2 | Missing mandatory `amount` | order `105` | exactly 1 |
| 3 | Invalid customer reference | order `106` → `customer_id = 99` does not exist | exactly 1 |
| 4 | Invalid product reference | — | **0** |
| 5 | Invalid status value | valid set is `Completed`, `Cancelled`, `Pending` | **0** |

### The canonical business rule

> **`fact_sales` contains ONLY valid, de-duplicated, `Completed` orders.**

Therefore recognised revenue for this sample is **exactly 525.00**:

| order_id | amount | included? | reason |
|---|---:|---|---|
| 101 | 200.00 | ✅ | valid, Completed |
| 102 | 50.00 | ✅ | valid, Completed |
| 103 | 50.00 | ❌ | status = `Cancelled` |
| 104 | 75.00 | ✅ | valid, Completed |
| 105 | *(null)* | ❌ | status = `Pending` **and** missing mandatory `amount` |
| 106 | 50.00 | ❌ | `customer_id = 99` fails referential integrity |
| 107 | 200.00 | ✅ | valid, Completed (**one** row kept) |
| 107 (dup) | 200.00 | ❌ | duplicate removed by de-duplication |
| **Total** | **525.00** | | 200.00 + 50.00 + 75.00 + 200.00 |

**Never quote a different total.** Any guide, query, test or dashboard in this learning repo that yields something other than `525.00` is wrong, and finding out *why* is itself the exercise.

---

## 5. Why the learning order is this order

A roadmap is only useful if you understand *why* each step unlocks the next. Here is the dependency chain.

### SQL → everything

SQL is the lingua franca. Spark DataFrame code, dbt models, BI semantic layers and warehouse queries are all re-expressions of the same relational algebra: filter, project, join, aggregate, window. If `GROUP BY ... HAVING` and `LEFT JOIN` semantics are not automatic for you, every later tool will feel like magic instead of engineering.

**→ `01_sql_deep_dive.md`**

### SQL → data modelling

Once you can query, the next question is *what should the tables look like in the first place?* Grain, keys, facts, dimensions and slowly changing dimensions are what separate a reusable platform from a pile of one-off queries. Modelling is the highest-leverage skill in the whole roadmap and the one most often skipped.

**→ `02_data_modelling_deep_dive.md`**

### Modelling → Linux & Git

Now you are producing *assets* that other people depend on. That immediately requires version control, branching, code review and the ability to work in a terminal — because pipelines run on Linux, logs live in files, and your code lives in a repo.

**→ `03_linux_git_deep_dive.md`**

### Linux/Git → ETL/ELT and data quality

With code under version control, you can build real pipelines: idempotent loads, incremental processing, schema evolution, quarantining bad rows, and reconciliation between source and target. This is where the medallion architecture becomes concrete.

### ETL → PySpark

Pandas is fine until the data does not fit in memory. PySpark teaches you distributed execution: partitions, shuffles, lazy evaluation, broadcast joins, skew. Crucially, the *API* mirrors SQL, so your earlier investment pays off directly.

### PySpark → cloud (Azure)

Local Spark proves the logic. The cloud adds storage (ADLS Gen2), managed compute, identity, networking and cost. You learn to parameterise environments (`config/dev.yml` vs `config/prod.yml`) instead of hard-coding paths — and to use placeholders like `<storage-account-name>` instead of real values.

### Cloud → Databricks & Delta Lake

Delta adds ACID transactions, schema enforcement, time travel and `MERGE` to the lake. `MERGE` is what makes upserts and SCD Type 2 practical at scale. Databricks adds the workspace, clusters, jobs and Unity Catalog around it.

### Databricks → orchestration

A pipeline is not a pipeline until it runs on a schedule, in the right order, with retries, alerting and backfills. Dependencies between bronze/silver/gold become a DAG.

### Orchestration → governance

Finally: who may read what, where did this number come from, how much does this cost, how long do we retain it, and is any of it personal data. Governance is the layer that turns a working platform into a *trusted* one.

---

## 6. The 9-month roadmap

Assumes **8–12 focused hours per week**. If you can do more, do not skip phases — deepen them with extra project work.

### Phase overview

| Phase | Months | Theme | Guide |
|---|---|---|---|
| 1 | 1 | SQL fundamentals → advanced | `01_sql_deep_dive.md` |
| 2 | 2 | Data modelling & warehouse design | `02_data_modelling_deep_dive.md` |
| 3 | 3 | Linux, Git, Python engineering hygiene | `03_linux_git_deep_dive.md` |
| 4 | 4 | ETL/ELT patterns + data quality | guides 01–03 applied to the project |
| 5 | 5 | PySpark and distributed processing | project `src/` + `notebooks/` |
| 6 | 6 | Cloud fundamentals (Azure) | project `config/dev.yml` / `prod.yml` |
| 7 | 7 | Databricks & Delta Lake | project `notebooks/` |
| 8 | 8 | Orchestration & productionisation | `orchestration/pipeline_design.md` |
| 9 | 9 | Governance, performance, interview readiness | project `docs/` |

---

### Phase 1 — Month 1: SQL (guide `01`)

| Week | Topics | Project work in `../customer-sales-lakehouse` |
|---|---|---|
| 1 | Relational model, tables, PK/FK, **grain**, `SELECT`, `WHERE`, `ORDER BY`, `LIMIT` | Load the three CSVs into any local SQL engine; describe each table's grain in `docs/` |
| 2 | `GROUP BY`, `HAVING`, aggregates, `NULL` semantics, `COALESCE`, `CASE WHEN` | Revenue by status; prove raw sum is 825.00 and understand why that is *not* recognised revenue |
| 3 | All join types, anti-joins, self joins, set operations, CTEs, subqueries | Reproduce the 525.00 rule entirely in SQL |
| 4 | Window functions: `ROW_NUMBER`, `RANK`, `DENSE_RANK`, `LAG`/`LEAD`, running totals, frames | De-duplicate order 107 with `ROW_NUMBER`; build a running revenue total |

**Deliverable:** a `sql/` folder with reconciliation queries that detect all three planted issues and return exactly 525.00 of recognised revenue.

---

### Phase 2 — Month 2: Data modelling (guide `02`)

| Week | Topics | Project work |
|---|---|---|
| 1 | OLTP vs OLAP, workload characteristics, why one schema cannot serve both | Write `docs/oltp_vs_olap.md` contrasting the source system with the gold layer |
| 2 | Normalization 1NF/2NF/3NF, denormalization trade-offs | Normalise the flattened orders extract on paper, then justify denormalising for gold |
| 3 | Star vs snowflake, facts vs dimensions, grain, additive/semi-/non-additive measures, surrogate vs natural keys | Draw the star schema; write DDL for `dim_customer`, `dim_product`, `dim_date`, `fact_sales` |
| 4 | SCD Type 1 and Type 2, conformed dimensions, factless facts, bridge tables | Implement a worked SCD2 change for a customer's region |

**Deliverable:** a documented star schema with declared grain, keys, and the 525.00 business rule written down as a testable assertion.

---

### Phase 3 — Month 3: Linux, Git, Python hygiene (guide `03`)

| Week | Topics | Project work |
|---|---|---|
| 1 | Shell navigation (`pwd`, `ls`, `cd`, `mkdir`, `cp`, `mv`, `rm`), paths, globbing, WSL/Git Bash on Windows | Inspect `data/raw/` from the terminal; count rows with `wc -l` |
| 2 | `cat`, `head`, `tail`, `grep`, `find`, `wc`, pipes, redirection, permissions (`chmod`, `chown`, rwx, octal) | Find the duplicate `107` line with `grep`/`sort`/`uniq` |
| 3 | Git: repo, working tree, staging area, commits, branches, remotes, `.gitignore` | Initialise the project repo; ignore `data/bronze/`, `data/silver/`, `data/gold/` |
| 4 | Branching workflow, pull requests, merge conflicts and resolution, safe `reset`/`revert`, code review | Do a full `branch → commit → push → PR → merge` cycle, including a deliberate conflict |

**Deliverable:** the project under version control with a clean history, a meaningful `.gitignore`, and at least one merged PR.

---

### Phase 4 — Month 4: ETL/ELT patterns and data quality

| Week | Topics | Project work |
|---|---|---|
| 1 | ETL vs ELT, idempotency, full vs incremental load, watermarks, late-arriving data | Make `01_bronze_ingestion.py` re-runnable without duplicating data |
| 2 | Medallion architecture in depth, audit columns, quarantine tables, replay from bronze | Add `_ingested_at`, `_source_file`, `_run_id` to bronze |
| 3 | Data-quality dimensions: completeness, uniqueness, validity, consistency, timeliness, accuracy | Build `src/quality_checks.py` covering the 5 planted-issue categories |
| 4 | Reconciliation, run logging, failure policy (fail vs warn vs quarantine) | `07_data_quality_checks.py` asserts fact revenue == 525.00 and fails the run otherwise |

**Deliverable:** a pipeline that is safe to re-run, quarantines orders 105 and 106, de-duplicates 107, and proves 525.00.

---

### Phase 5 — Month 5: PySpark

| Week | Topics | Project work |
|---|---|---|
| 1 | Spark architecture: driver, executors, jobs/stages/tasks, lazy evaluation, DAG | Convert ingestion to PySpark with an explicit `StructType` schema |
| 2 | DataFrame API: `select`, `filter`, `withColumn`, `groupBy`, `agg`, `join`, `union` | Rebuild silver transformations in `src/transformations.py` |
| 3 | Window functions in Spark, UDFs (and why to avoid them), handling nulls | De-duplicate with `row_number().over(Window.partitionBy("order_id"))` |
| 4 | Partitions, shuffles, broadcast joins, skew, caching, `explain()`, file sizing | Partition gold output by order year/month; read a physical plan |

```python
# Illustrative PySpark de-duplication — runs locally, no cloud needed.
from pyspark.sql import SparkSession, Window
from pyspark.sql import functions as F

spark = SparkSession.builder.appName("silver_orders").master("local[*]").getOrCreate()

bronze_orders = spark.read.parquet("data/bronze/orders")

dedup_window = Window.partitionBy("order_id").orderBy(
    F.col("updated_at").desc(), F.col("_ingested_at").desc()
)

silver_orders = (
    bronze_orders
    .withColumn("row_num", F.row_number().over(dedup_window))
    .filter(F.col("row_num") == 1)
    .drop("row_num")
)
```

**Deliverable:** the whole bronze→silver→gold flow running in local PySpark, producing 4 `fact_sales` rows totalling 525.00.

---

### Phase 6 — Month 6: Cloud fundamentals (Azure)

| Week | Topics | Project work |
|---|---|---|
| 1 | Cloud concepts: regions, resource groups, IaaS/PaaS, cost model | Map each local folder to its cloud equivalent in `docs/` |
| 2 | Object storage (ADLS Gen2), containers, hierarchical namespace, path conventions | Replace hard-coded paths with `abfss://<container>@<storage-account-name>.dfs.core.windows.net/...` templates |
| 3 | Identity and secrets: managed identity, service principals, key vault **concepts** — never commit secrets | Keep only placeholders in `config/*.yml`; document that real values come from the environment |
| 4 | Environment separation, parameterisation, deployment basics | Make `src/config.py` select `dev.yml` vs `prod.yml` from an environment variable |

```yaml
# config/dev.yml — placeholders only, never real credentials
environment: dev
storage:
  account_name: <storage-account-name>
  container: lakehouse
paths:
  raw:    data/raw
  bronze: data/bronze
  silver: data/silver
  gold:   data/gold
quality:
  fail_on_duplicate_keys: true
  expected_recognised_revenue: 525.00
```

**Deliverable:** zero hard-coded paths, zero secrets in the repo, one config switch between environments.

---

### Phase 7 — Month 7: Databricks & Delta Lake

| Week | Topics | Project work |
|---|---|---|
| 1 | Workspace, clusters, notebooks, jobs, DBFS vs external locations | Document how each `notebooks/*.py` would map to a Databricks job task |
| 2 | Delta Lake: transaction log, ACID, schema enforcement/evolution, time travel, `OPTIMIZE`/`VACUUM` | Convert bronze/silver/gold writes to Delta format locally |
| 3 | `MERGE INTO` for upserts and SCD Type 2 | Implement SCD2 on `dim_customer` using `MERGE` |
| 4 | Medallion best practice, Unity Catalog concepts, table properties | Add table comments and a data dictionary to `docs/` |

```sql
-- Delta MERGE: SCD Type 1 upsert on dim_product
MERGE INTO gold.dim_product AS tgt
USING silver.products        AS src
    ON tgt.product_id = src.product_id
WHEN MATCHED AND (
        tgt.product_name <> src.product_name
     OR tgt.category     <> src.category
     OR tgt.unit_price   <> src.unit_price
) THEN UPDATE SET
        tgt.product_name = src.product_name,
        tgt.category     = src.category,
        tgt.unit_price   = src.unit_price,
        tgt.updated_at   = current_timestamp()
WHEN NOT MATCHED THEN INSERT (product_id, product_name, category, unit_price, updated_at)
     VALUES (src.product_id, src.product_name, src.category, src.unit_price, current_timestamp());
```

**Deliverable:** Delta-backed medallion layers with a working SCD2 dimension and time-travel demo.

---

### Phase 8 — Month 8: Orchestration & productionisation

| Week | Topics | Project work |
|---|---|---|
| 1 | DAGs, task dependencies, schedules, triggers, sensors | Finish `orchestration/pipeline_design.md` with the full DAG |
| 2 | Retries, idempotency under retry, backfills, parameterised run dates | Add a `--run-date` parameter to the pipeline entry point |
| 3 | Logging, metrics, alerting, SLAs, run metadata tables | Flesh out `src/logging_utils.py` with structured run logs |
| 4 | Testing: unit tests on transformations, integration tests on the pipeline, CI | Add `tests/` covering de-duplication, FK validation, and the 525.00 assertion |

```
      ┌──────────────────────┐
      │ 01_bronze_ingestion  │
      └──────────┬───────────┘
                 │
      ┌──────────┼─────────────────────┐
      ▼          ▼                     ▼
 02_silver   03_silver            04_silver
 customers   products              orders
      │          │                     │
      └──────────┴──────────┬──────────┘
                            ▼
                   05_gold_dimensions
                            │
                            ▼
                    06_gold_fact_sales
                            │
                            ▼
                 07_data_quality_checks   ← fails the run if revenue ≠ 525.00
```

**Deliverable:** a documented, retry-safe, testable DAG with clear failure semantics.

---

### Phase 9 — Month 9: Governance, performance, interview readiness

| Week | Topics | Project work |
|---|---|---|
| 1 | Lineage, data catalogs, data dictionaries, ownership, SLAs/SLOs | Produce `docs/data_dictionary.md` and a lineage diagram |
| 2 | Access control, PII handling, masking, retention, GDPR-style concepts | Classify every column: public / internal / personal |
| 3 | Performance & cost: partitioning strategy, file sizes, Z-ordering concepts, query tuning | Benchmark gold queries before/after partitioning; write up findings |
| 4 | Portfolio and interview prep: system design, SQL drills, project walkthrough | Write a README that explains the project in 5 minutes |

**Deliverable:** a portfolio-ready repo you can walk an interviewer through end-to-end.

---

## 7. Expected outcomes per phase

Phrased as *"by the end of this phase I can…"* — if you cannot do the bullet unaided, the phase is not finished.

### Phase 1 — SQL
- State the **grain** of any table in one sentence before writing a query against it.
- Write multi-CTE queries with joins, aggregation and filtering, and predict the row count before running them.
- Explain why `COUNT(*)`, `COUNT(col)` and `COUNT(DISTINCT col)` differ, using order 105 and the duplicate 107 as examples.
- Use `ROW_NUMBER()` to de-duplicate on a business key and justify the `ORDER BY` inside the window.
- Detect duplicates, nulls, orphan foreign keys and invalid domain values with targeted queries.

### Phase 2 — Data modelling
- Choose between star and snowflake and defend the choice.
- Declare a fact table's grain and prove the loaded data matches it.
- Explain surrogate vs natural keys and why a surrogate key is required for SCD2.
- Perform an SCD Type 2 change by hand and show the before/after rows.
- Translate a business rule ("recognised revenue = valid, de-duplicated, Completed orders = 525.00") into schema plus tests.

### Phase 3 — Linux & Git
- Navigate, inspect and search files from a terminal without a GUI.
- Read and set permissions in both symbolic and octal notation.
- Work on a feature branch, resolve a merge conflict deliberately, and open a reviewable pull request.
- Explain the difference between working tree, staging area and commit — and recover from a mistake without losing work.
- Articulate why `rm -rf`, `git reset --hard` and force-push are dangerous, and the safe alternatives.

### Phase 4 — ETL/ELT & quality
- Design an idempotent load and prove it by running it twice.
- Implement incremental loading with a watermark.
- Build quality checks that fail a run, and quarantine rows rather than dropping them silently.
- Reconcile source row counts and amounts against target, and explain every difference.

### Phase 5 — PySpark
- Read a Spark physical plan and identify the shuffle.
- Apply explicit schemas instead of relying on inference.
- Use window functions, broadcast joins and partitioning deliberately.
- Diagnose a slow job: skew, too many small files, or an accidental cross join.

### Phase 6 — Cloud (Azure)
- Describe object storage layout for a medallion lakehouse.
- Parameterise an application for multiple environments with no secrets in source control.
- Explain managed identity vs service principal at a conceptual level.

### Phase 7 — Databricks & Delta
- Explain ACID guarantees via the Delta transaction log.
- Use `MERGE INTO` for upserts and SCD2.
- Use time travel to compare a table before and after a load.
- Explain schema enforcement vs schema evolution and when each is appropriate.

### Phase 8 — Orchestration
- Model dependencies as a DAG with explicit failure behaviour.
- Design safe retries and backfills.
- Write unit tests over transformation functions and integration tests over the pipeline.

### Phase 9 — Governance
- Produce lineage and a data dictionary.
- Classify and protect sensitive columns.
- Justify a partitioning and file-size strategy in terms of cost and latency.
- Walk a stranger through the project in five minutes, ending with "and this is why revenue is exactly 525.00".

---

## 8. Weekly study habits that actually work

### The weekly shape (8–12 hours)

| Block | Hours | Purpose |
|---|---|---|
| **Concept** | 2 | Read one guide section. Take notes *in your own words*, not copy-paste. |
| **Drills** | 2 | Short, repetitive reps: 10 SQL problems, 20 shell commands, 5 Git scenarios. Builds fluency. |
| **Build** | 4–6 | Apply it to `../customer-sales-lakehouse`. Code that runs beats notes that look tidy. |
| **Review** | 1 | Re-read your own code from last week. Refactor one thing. Write down one lesson. |
| **Explain** | 0.5 | Write a short paragraph explaining the week's hardest idea to an imaginary junior. |

### Rules of thumb

1. **Never read without typing.** If a guide shows a query, run it and verify the result table matches.
2. **Predict before you run.** Write down the expected row count and sum, *then* execute. Being wrong is the lesson.
3. **One project, many passes.** Rebuilding the same lakehouse with better tools each month compounds; starting five different projects does not.
4. **Commit daily, in small pieces.** A commit per logical change makes your history a learning log.
5. **Keep a `docs/decisions.md`.** Record every choice and its reason. Interviewers love "why", and future-you forgets.
6. **Timebox debugging to 45 minutes.** Then write down exactly what you tried before seeking help. Half the time you solve it while writing.
7. **Re-derive the 525.00 from scratch at least once a month**, with a fresh query. It keeps the whole pipeline honest.
8. **Protect one deep block per week** (3+ uninterrupted hours). Distributed systems concepts do not fit in 20-minute gaps.
9. **Spaced repetition for syntax only.** Concepts need practice; syntax needs flashcards. Do not confuse the two.
10. **Ship something visible each month** — a diagram, a README, a working notebook. Momentum is a resource.

### A sample week (Phase 1, Week 3 — joins and CTEs)

| Day | ~Time | Activity |
|---|---|---|
| Mon | 1.5 h | Read the joins section of `01_sql_deep_dive.md`; reproduce every result table by hand first |
| Tue | 1.5 h | Drill: 10 join questions on customers/products/orders; predict row counts before running |
| Wed | 2.0 h | Build: write the anti-join that isolates order 106; add it to `sql/` |
| Thu | 2.0 h | Build: express the full 525.00 rule as a single CTE chain |
| Fri | 1.5 h | Drill: rewrite three subqueries as CTEs and compare readability |
| Sat | 2.0 h | Review + explain: refactor the week's SQL, write `docs/decisions.md` entry on join choices |
| Sun | — | Rest. Genuinely. |

---

## 9. How to know you are job-ready

You are ready when you can do **all** of the following, unprompted:

- [ ] Explain the medallion architecture and *why* bronze is immutable.
- [ ] Declare the grain of every table in your project.
- [ ] Write a window-function query to de-duplicate on a business key and explain the tie-breaking rule when `updated_at` is identical (as with order 107).
- [ ] Describe how you detect and handle orphan foreign keys (order 106) — quarantine, not delete.
- [ ] Trace a single number (525.00) from raw CSV to the reporting query, naming every filter applied along the way.
- [ ] Implement SCD Type 2 and explain what breaks if you use the natural key as the fact's foreign key.
- [ ] Work confidently in a terminal and in Git, including resolving a merge conflict.
- [ ] Explain lazy evaluation and shuffles in Spark, with an example of a query you optimised.
- [ ] Describe your orchestration DAG, its retries, and what happens when a quality check fails.
- [ ] Discuss cost, lineage and access control without hand-waving.
- [ ] Present the project repo as a coherent story, not a folder of scripts.

---

## 10. Common traps (and how to avoid them)

| Trap | Why it happens | Fix |
|---|---|---|
| **Tool-chasing** | New tools feel like progress | Tools change; grain, joins and idempotency do not. Depth over breadth. |
| **Skipping modelling** | It has no shiny UI | It is the single most-asked topic in interviews and the main cause of untrustworthy dashboards. |
| **Tutorial loops** | Following along feels productive | Always extend the example with your own requirement. If you did not break it, you did not learn it. |
| **Silently dropping bad rows** | `WHERE amount IS NOT NULL` is easy | Quarantine and count. Rows that vanish without a record destroy trust. |
| **Non-idempotent pipelines** | Works first time, duplicates on retry | Design every load so running it twice yields the same result. |
| **Hard-coded paths and secrets** | Fast during development | Use `config/dev.yml` + placeholders like `<storage-account-name>`. Never commit real credentials. |
| **No tests** | "It's just a pipeline" | Transformations are functions. Functions get unit tests. |
| **Giant commits** | Working late, committing once | Small commits are documentation and a safety net. |
| **Ignoring cost** | Local dev is free | In the cloud, an accidental cross join is a line item on an invoice. |
| **Reporting numbers you cannot defend** | Pressure to deliver | If you cannot explain why it is 525.00 and not 825.00, do not publish it. |

---

## How this maps to the project

| Roadmap phase | Where you work in `../customer-sales-lakehouse` |
|---|---|
| 1 — SQL | `sql/`, `data/raw/` |
| 2 — Modelling | `docs/`, `sql/` DDL, gold layer design |
| 3 — Linux & Git | the repo itself, `.gitignore`, PR workflow |
| 4 — ETL & quality | `src/ingestion.py`, `src/quality_checks.py`, `notebooks/01`–`07` |
| 5 — PySpark | `src/transformations.py`, all `notebooks/` |
| 6 — Cloud | `config/dev.yml`, `config/prod.yml`, `src/config.py` |
| 7 — Databricks/Delta | `notebooks/05`, `notebooks/06`, Delta rewrites |
| 8 — Orchestration | `orchestration/pipeline_design.md`, `tests/`, `src/logging_utils.py` |
| 9 — Governance | `docs/data_dictionary.md`, `docs/decisions.md`, README |

---

## Next steps

1. Read **`01_sql_deep_dive.md`** and work every example against the sample data.
2. Then **`02_data_modelling_deep_dive.md`** to design the gold layer properly.
3. Then **`03_linux_git_deep_dive.md`** to put it all under version control like an engineer.

Start today, with Phase 1, Week 1. The first query you write should return four rows of customers.
