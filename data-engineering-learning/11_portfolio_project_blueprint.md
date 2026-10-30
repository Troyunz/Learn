# 11 — Portfolio Project Blueprint: `customer-sales-lakehouse`

> **Where this sits in the series**
>
> Guides `00`–`09` taught the skills. Guide `10_azure_data_engineer_study_plan.md` gave you the twelve-week schedule.
>
> This guide is the **specification and presentation manual** for the project itself: what to build, in what order, what "done" looks like, and — critically — **how to talk about it honestly** on a CV and in an interview.
>
> Project location: [`../customer-sales-lakehouse`](../customer-sales-lakehouse)

---

> ## 🔴 Read this before anything else: the honesty boundary
>
> This project has two distinct halves, and conflating them will damage your credibility in an interview.
>
> | | **Local learning implementation** | **Azure deployment design** |
> |---|---|---|
> | Status | ✅ **BUILT. Runs on a laptop.** | 📐 **DESIGNED ONLY. Not deployed.** |
> | Technology | PySpark (local mode), Parquet, Python 3.11, pytest | ADLS Gen2, Azure Data Factory, Databricks, Delta Lake, a serving layer |
> | Evidence | Working code, passing tests, reproducible output | Architecture documents and diagrams |
> | How to describe it | "I built…" | "I designed…" / "I mapped it onto…" |
>
> Every section below is labelled. When in doubt, under-claim. See [Presenting this project](#13-presenting-this-project-cv-and-interview) for exact wording.

---

## Table of contents

1. [Business context and goals](#1-business-context-and-goals)
2. [Business questions answered](#2-business-questions-answered)
3. [Architecture](#3-architecture)
4. [Repository structure](#4-repository-structure)
5. [Milestone plan and effort](#5-milestone-plan-and-effort)
6. [The datasets](#6-the-datasets)
7. [Data quality rules and expected findings](#7-data-quality-rules-and-expected-findings)
8. [Layer definitions and expected outputs](#8-layer-definitions-and-expected-outputs)
9. [Star schema design](#9-star-schema-design)
10. [Reporting questions and expected answers](#10-reporting-questions-and-expected-answers)
11. [Local learning implementation — BUILT](#11-local-learning-implementation--built)
12. [Azure deployment design — DESIGN ONLY](#12-azure-deployment-design--design-only)
13. [Presenting this project: CV and interview](#13-presenting-this-project-cv-and-interview)
14. [Testing plan](#14-testing-plan)
15. [Completion checklist](#15-completion-checklist)
16. [Next steps](#16-next-steps)

---

## 1. Business context and goals

### 1.1 The scenario

A mid-sized retailer sells accessories and hardware through a simple order system. Three flat files arrive daily from operational systems:

- a **customer master** list,
- a **product catalogue**,
- a **transactional order feed**.

The business cannot currently answer basic revenue questions with confidence. Each team extracts the raw order file into a spreadsheet and applies its own interpretation. The result is the classic failure described in `09_governance_security_delivery.md` Section 1: **three people, three revenue figures, one very unproductive meeting.**

The raw order feed is also demonstrably dirty. It contains a duplicated order, an order with a missing monetary amount, and an order referencing a customer who does not exist in the customer master. Anyone summing the raw file gets a wrong answer, and nobody can tell which answer is wrong.

### 1.2 The goal

> Build a single, documented, tested, reproducible pipeline that turns three raw CSVs into a trustworthy star schema, and publishes **one** agreed revenue figure that every team uses.

That figure, for this dataset, is **525.00**.

### 1.3 Objectives

| # | Objective | Success measure |
|---|---|---|
| O1 | Establish a single source of truth for sales revenue | Every report derives from `gold.fact_sales`; total is 525.00 |
| O2 | Make data quality visible rather than invisible | Every run writes named check results with failure counts |
| O3 | Make the pipeline reproducible by anyone | A stranger clones, runs, and reproduces 525.00 unaided |
| O4 | Make business rules explicit and reviewable | Every rule has an ID in `docs/transformation_rules.md` |
| O5 | Make the design cloud-ready | A documented mapping from local artefacts to Azure services |
| O6 | Demonstrate professional delivery practice | Tests, docs, runbook, config separation, no secrets |

### 1.4 Non-goals

Being explicit about what you did **not** do is a senior-engineer habit. It prevents an interviewer assuming a gap is an oversight.

- ❌ Real-time or streaming ingestion — this is a daily batch design.
- ❌ Change data capture from source systems — the feed is a flat file.
- ❌ Multi-currency handling — all amounts are assumed to be a single currency.
- ❌ Returns, refunds, discounts or tax — out of scope for the sample.
- ❌ Multi-line orders — the sample's grain is one product per order (see assumption A2).
- ❌ Type 2 history on dimensions — `dim_customer` is Type 1 by explicit decision.
- ❌ A production Azure deployment — **design only**, see Section 12.
- ❌ A BI dashboard — the reporting layer is SQL.

### 1.5 Why this project is a good portfolio piece

| Property | Why it matters to an interviewer |
|---|---|
| **Small enough to verify by hand** | You can prove every number. Big datasets hide errors; small ones expose them. |
| **Dirty data by design** | Three deliberate defects force real engineering, not a happy-path tutorial. |
| **End to end** | Ingestion, transformation, modelling, quality, reporting, docs, tests. |
| **Zero cost, fully reproducible** | Anyone can clone and run it. No "trust me, it worked in my subscription". |
| **Has a defensible canonical answer** | 525.00 is testable, explainable, and memorable. |
| **Shows governance maturity** | Classification, lineage, runbook, assumptions — rare in junior portfolios. |
| **Honest about scope** | The local/Azure separation signals judgement, which is what seniority looks like. |

---

## 2. Business questions answered

Every pipeline should start from questions, not from tables. These five drove the design.

| # | Business question | Who asks it | Decision it drives | Answered by |
|---|---|---|---|---|
| Q1 | What is our recognised revenue, and for which period? | Finance | Month-end close, forecasting | `fact_sales` + `dim_date` |
| Q2 | Which regions generate revenue? | Sales leadership | Territory investment | `fact_sales` + `dim_customer` |
| Q3 | Which customers are most valuable? | Account management | Retention focus | `fact_sales` + `dim_customer` |
| Q4 | Which products drive revenue? | Merchandising | Stock and range decisions | `fact_sales` + `dim_product` |
| Q5 | How much of our order data is unusable, and why? | Data steward, ops | Source system remediation | `quality_results` |

Note Q5. Most portfolio projects answer only Q1–Q4. Including a question about **data quality itself** is what distinguishes a data engineer from a report writer.

### 2.1 Questions deliberately out of scope

| Question | Why not | What it would need |
|---|---|---|
| "What is our profit margin?" | No cost data in the source | A cost or COGS feed |
| "What is customer lifetime value?" | Needs history far beyond this sample | Years of orders, churn definition |
| "Which products are frequently bought together?" | Sample has one product per order | Multi-line orders, basket grain |
| "How is revenue trending year on year?" | Sample spans two months | ≥ 24 months of data |
| "What is our forecast for next quarter?" | Forecasting is out of scope | A modelling layer and far more history |

---

## 3. Architecture

### 3.1 The medallion flow

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│                     customer-sales-lakehouse — data flow                     │
└──────────────────────────────────────────────────────────────────────────────┘

   ┌─────────────┐
   │     RAW     │   data/raw/  ·  CSV  ·  as received, never modified
   │             │
   │ customers   │  4 rows
   │ products    │  4 rows
   │ orders      │  8 rows  ◄── contains 1 duplicate, 1 null amount, 1 orphan FK
   └──────┬──────┘
          │  01_bronze_ingestion.py
          │  • read with explicit schema
          │  • add ingestion_timestamp, source_file
          │  • NO business logic
          ▼
   ┌─────────────┐
   │   BRONZE    │   data/bronze/  ·  Parquet  ·  faithful copy + metadata
   │             │
   │ customers   │  4 rows
   │ products    │  4 rows
   │ orders      │  8 rows  ◄── defects PRESERVED on purpose
   └──────┬──────┘
          │  02_silver_customers.py · 03_silver_products.py · 04_silver_orders.py
          │  • cast types          • trim / standardise text
          │  • de-duplicate        • validate mandatory fields
          ▼
   ┌─────────────┐
   │   SILVER    │   data/silver/  ·  Parquet  ·  cleansed, typed, de-duplicated
   │             │
   │ customers   │  4 rows
   │ products    │  4 rows
   │ orders      │  7 rows  ◄── duplicate 107 removed
   └──────┬──────┘
          │  05_gold_dimensions.py  →  dim_customer, dim_product, dim_date
          │  06_gold_fact_sales.py  →  fact_sales
          │  • surrogate keys       • referential integrity enforced
          │  • Completed only       • orphan rows rejected
          ▼
   ┌─────────────┐
   │    GOLD     │   data/gold/  ·  Parquet  ·  star schema, report-ready
   │             │
   │ dim_customer│  4 rows
   │ dim_product │  4 rows
   │ dim_date    │  365 rows (2026 calendar)
   │ fact_sales  │  4 rows  ◄── SUM(sale_amount) = 525.00
   └──────┬──────┘
          │  sql/03_reporting_queries.sql
          ▼
   ┌─────────────┐             ┌──────────────────────────────┐
   │  REPORTING  │             │  07_data_quality_checks.py   │
   │             │             │            ↓                 │
   │ • monthly   │             │  data/quality_results/       │
   │ • regional  │             │  check_name, check_status,   │
   │ • customer  │             │  failed_record_count,        │
   │ • product   │             │  processing_date             │
   └─────────────┘             └──────────────────────────────┘
```

### 3.2 Why medallion?

| Layer | Purpose | Guarantee | What you could NOT do without it |
|---|---|---|---|
| **Raw** | Immutable receipt | Byte-for-byte as delivered | Prove what the source actually sent during a dispute |
| **Bronze** | Replayable landing zone | Faithful copy, efficient format, metadata added | Rebuild silver and gold after a logic bug without re-requesting source files |
| **Silver** | Clean, trustworthy detail | Typed, de-duplicated, validated | Serve any question not anticipated by the gold model |
| **Gold** | Business-ready model | Modelled, referentially complete | Let an analyst self-serve without a six-way join |

The key insight: **each layer exists so a different kind of mistake is recoverable.** Lose gold → rebuild from silver. Lose silver → rebuild from bronze. Lose bronze → you need the source again. That gradient is the whole point.

### 3.3 Technology choices and reasoning

| Choice | Why | What was rejected and why |
|---|---|---|
| **PySpark** | Industry-standard distributed engine; the same API scales from 8 rows to billions | pandas — doesn't transfer to production data engineering |
| **Parquet** | Columnar, compressed, schema-carrying, splittable | CSV — no types, no compression, no predicate pushdown |
| **Local mode** | Zero cost, fully reproducible, no cloud dependency | A cloud-only build — nobody can verify it |
| **`.py` notebooks** | Diffable in Git, reviewable in a PR, importable, testable | `.ipynb` — output noise, merge conflicts, risk of committed secrets |
| **Separate `src/` modules** | Pure functions that pytest can exercise without a pipeline run | All logic inline in notebooks — untestable |
| **YAML config per environment** | Environment differences visible in one diff | `if env == 'prod'` branches scattered in code |
| **Star schema** | Analysts can query it correctly without help | Querying silver directly — correct but hostile |
| **File-based quality results** | Quality becomes data you can trend | Printing to the console — invisible tomorrow |

---

## 4. Repository structure

```text
customer-sales-lakehouse/
├── README.md                       Shop window: what's built vs designed
├── requirements.txt                Pinned Python dependencies
├── .gitignore                      Excludes data outputs, .env, credentials
│
├── docs/
│   ├── architecture.md             Layers, flow, technology reasoning, Azure mapping
│   ├── data_dictionary.md          Column-level contract for every dataset
│   ├── transformation_rules.md     Every business rule, with an ID (TR-01 …)
│   ├── data_quality_rules.md       Every check, severity, and expected result
│   └── runbook.md                  How to run it, verify it, and fix it at 3am
│
├── data/
│   ├── raw/                        customers.csv, products.csv, orders.csv  ← committed
│   ├── bronze/                     Parquet output                           ← gitignored
│   ├── silver/                     Parquet output                           ← gitignored
│   ├── gold/                       Parquet output                           ← gitignored
│   └── quality_results/            Parquet output                           ← gitignored
│
├── notebooks/                      Executable pipeline stages, in order
│   ├── 01_bronze_ingestion.py
│   ├── 02_silver_customers.py
│   ├── 03_silver_products.py
│   ├── 04_silver_orders.py
│   ├── 05_gold_dimensions.py
│   ├── 06_gold_fact_sales.py
│   └── 07_data_quality_checks.py
│
├── sql/
│   ├── 01_exploration_queries.sql  Profiling and first-look queries
│   ├── 02_data_quality_checks.sql  SQL expression of the same rules
│   ├── 03_reporting_queries.sql    The five business questions
│   └── 04_star_schema_ddl.sql      Table definitions with grain comments
│
├── src/                            Testable library code
│   ├── __init__.py
│   ├── config.py                   Environment-aware config loading
│   ├── ingestion.py                Schema definitions and readers
│   ├── transformations.py          Pure DataFrame → DataFrame functions
│   ├── quality_checks.py           Reusable check primitives
│   └── logging_utils.py            Structured, redacting logger
│
├── tests/
│   ├── __init__.py
│   ├── test_transformations.py     Unit tests on pure functions
│   └── test_quality_checks.py      Unit tests on check logic
│
├── config/
│   ├── dev.yml                     Local paths, permissive quality gates
│   └── prod.yml                    Cloud paths, blocking quality gates, secret NAMES only
│
└── orchestration/
    └── pipeline_design.md          DAG design, dependencies, scheduling, failure handling
```

### 4.1 Why the structure matters

| Convention | Signal it sends |
|---|---|
| Numbered notebooks | Execution order and dependencies are self-documenting |
| `src/` separate from `notebooks/` | Logic is testable; notebooks are thin orchestration |
| `docs/` with five specific files | You know what professional documentation consists of |
| `config/` per environment | You understand environment separation (guide `09`, Section 13) |
| `tests/` present and populated | You test data code, which most juniors do not |
| Outputs gitignored, inputs committed | You know what belongs in version control |
| `orchestration/` as design | You distinguish building from designing |

---

## 5. Milestone plan and effort

Estimates assume a motivated learner at roughly 8–12 hours per week (see `10_azure_data_engineer_study_plan.md`). Adjust to your pace; the **order** is what matters.

| # | Milestone | Effort | Depends on | Definition of done |
|---|---|---|---|---|
| **M0** | Environment setup | 2–3 h | — | `pyspark` runs; `python -m pytest` executes; repo initialised with `.gitignore` |
| **M1** | Datasets and exploration | 3–4 h | M0 | Three CSVs in `data/raw/`; `sql/01_exploration_queries.sql` written; all three defects found by hand |
| **M2** | Bronze ingestion | 4–6 h | M1 | `01_bronze_ingestion.py` lands 4/4/8 rows as Parquet with ingestion metadata |
| **M3** | Silver customers & products | 4–5 h | M2 | `02`/`03` produce 4 typed, trimmed rows each |
| **M4** | Silver orders | 6–8 h | M3 | `04` produces 7 rows; duplicate 107 removed deterministically; null amount flagged |
| **M5** | Gold dimensions | 5–7 h | M4 | `05` produces `dim_customer` (4), `dim_product` (4), `dim_date` (365) with surrogate keys |
| **M6** | Gold fact | 6–8 h | M5 | `06` produces 4 rows; `SUM(sale_amount)` = **525.00**; zero orphan keys |
| **M7** | Data quality checks | 6–8 h | M6 | `07` writes 6 results; exactly 3 FAIL with count 1 each |
| **M8** | Reporting queries | 4–5 h | M6 | All five business questions answered and matching the canonical figures |
| **M9** | Unit tests | 5–7 h | M7 | `tests/` covers transformations and checks; all pass |
| **M10** | Documentation | 6–8 h | M8, M9 | All five `docs/` files complete, accurate, cross-referenced |
| **M11** | Config and environments | 3–4 h | M10 | `dev.yml`/`prod.yml` drive behaviour; no hard-coded paths in code |
| **M12** | Orchestration design | 3–4 h | M11 | `orchestration/pipeline_design.md` with the full DAG and failure handling |
| **M13** | Azure deployment design | 5–6 h | M12 | Service mapping, storage layout, security model — clearly labelled as design |
| **M14** | Portfolio polish | 4–6 h | M13 | README separates built/designed; CI green; 60-second summary rehearsed |
| | **Total** | **≈ 62–89 h** | | ≈ 7–9 weeks at 8–12 h/week |

### 5.1 Critical path and parallelism

```text
M0 ─► M1 ─► M2 ─► M3 ─► M4 ─► M5 ─► M6 ─┬─► M7 ─┬─► M9 ─► M10 ─► M11 ─► M12 ─► M13 ─► M14
                                          └─► M8 ─┘
```

M7 (quality) and M8 (reporting) can run in parallel once gold exists. Everything else is strictly sequential — **you cannot build a fact table before the dimensions it references.**

### 5.2 Milestone gates

Do not advance past these without the stated proof.

| Gate | After | Proof required |
|---|---|---|
| **Data understood** | M1 | You can name all three defects and the affected order IDs from memory |
| **Pipeline works** | M6 | `SUM(sale_amount)` = 525.00, verified twice from a clean run |
| **Pipeline is trustworthy** | M9 | Tests fail if you deliberately break de-duplication |
| **Pipeline is handover-ready** | M10 | Someone else runs it from the docs and gets 525.00 |
| **Project is presentable** | M14 | You give the 60-second summary without notes |

### 5.3 Where learners typically lose time

| Trap | Symptom | Fix |
|---|---|---|
| Schema inference | Amounts become strings; sums fail or concatenate | Define explicit schemas from the start (M2) |
| Non-deterministic de-duplication | Row counts change between runs | Use a window function with an explicit `ORDER BY` (M4) |
| Building gold before dimensions | Orphan key errors everywhere | Respect the milestone order |
| Decimal vs float | 525.0000000001 | Use `DecimalType(10, 2)` throughout |
| Skipping tests until the end | A late bug invalidates earlier results | Write the 525.00 assertion at M6, not M9 |
| Documenting last | You've forgotten why you made decisions | Write `transformation_rules.md` entries as you implement each rule |
| Gold-plating the sample data | Adding 10,000 rows "for realism" | Keep 8 rows. Verifiability is the feature. |

---

## 6. The datasets

All data is **fictional**. Three files, deliberately small enough to verify every result by hand.

### 6.1 `data/raw/customers.csv`

```text
customer_id,customer_name,region,customer_created_date
1,Aisha Khan,North,2025-10-01
2,Ben Smith,South,2025-11-12
3,Carla Jones,North,2025-12-07
4,David Lee,West,2026-01-15
```

| Column | Type | Nullable | Description | Classification |
|---|---|---|---|---|
| `customer_id` | INT | No | Natural key. Unique. | Internal (pseudonymous) |
| `customer_name` | STRING | No | Full name of the customer. | **Confidential — direct PII** |
| `region` | STRING | No | Sales territory: North, South, West. | Internal (quasi-identifier) |
| `customer_created_date` | DATE | No | Date the customer record was created. | Internal (quasi-identifier) |

**Notes:** 4 rows, no defects. Two customers in North, one in South, one in West. Only one customer per region for South and West — see the re-identification warning in `09_governance_security_delivery.md` Section 15.3.

### 6.2 `data/raw/products.csv`

```text
product_id,product_name,category,unit_price
1001,Laptop Bag,Accessories,50.00
1002,Wireless Mouse,Accessories,25.00
1003,Monitor,Hardware,200.00
1004,Keyboard,Accessories,75.00
```

| Column | Type | Nullable | Description | Classification |
|---|---|---|---|---|
| `product_id` | INT | No | Natural key. Unique. | Public |
| `product_name` | STRING | No | Display name. | Public |
| `category` | STRING | No | `Accessories` or `Hardware`. | Public |
| `unit_price` | DECIMAL(10,2) | No | List price per unit. | Public |

**Notes:** 4 rows, no defects. Three Accessories, one Hardware.

> ⚠️ **Watch this trap.** `unit_price` is the *list* price. `orders.amount` is the *transacted* amount, and they do not always agree. Order 102 has `quantity = 2` of product 1002 (`unit_price = 25.00`) with `amount = 50.00` — consistent. But **never derive revenue from `quantity × unit_price`.** Revenue comes from `amount`, because that is what was actually transacted. This is a genuine business-rule distinction that interviewers like to probe.

### 6.3 `data/raw/orders.csv`

```text
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

| Column | Type | Nullable | Description | Classification |
|---|---|---|---|---|
| `order_id` | INT | No | Natural key. **Should** be unique — is not, in raw. | Internal |
| `customer_id` | INT | No | FK to customers. **Not guaranteed valid in raw.** | Internal |
| `product_id` | INT | No | FK to products. Valid throughout. | Internal |
| `order_date` | DATE | No | Business date of the order. | Internal |
| `quantity` | INT | No | Units ordered. | Internal |
| `amount` | DECIMAL(10,2) | **Yes in raw** | Transacted value. Mandatory for a valid order. | Internal |
| `status` | STRING | No | `Completed`, `Cancelled`, or `Pending`. | Internal |
| `updated_at` | TIMESTAMP | No | Source system last-modified time. Used as the de-duplication tie-breaker. | Internal |

**8 rows in the raw file**, containing three deliberate defects.

### 6.4 The three deliberate defects

| # | Defect | Affected row | Detected by | Handling |
|---|---|---|---|---|
| 1 | **Duplicate order ID** | `107` appears twice | `orders_unique_order_id` | De-duplicated in silver; one row survives |
| 2 | **Missing mandatory amount** | Order `105` has empty `amount` | `orders_amount_not_null` | Quarantined; excluded from `fact_sales` |
| 3 | **Invalid customer reference** | Order `106` → `customer_id = 99`, which does not exist | `orders_customer_fk_valid` | Rejected at gold; excluded from `fact_sales` |

And, equally importantly, what is **not** wrong:

| Non-defect | Confirmation |
|---|---|
| Invalid product references | **ZERO.** All of 1001–1004 exist in `products.csv`. |
| Invalid statuses | **ZERO.** Only `Completed`, `Cancelled`, `Pending` appear. |

> This matters. A quality suite that reports failures everywhere is as useless as one that reports none. Two checks **passing** proves the checks discriminate.

### 6.5 Row counts at a glance

| Dataset | Raw | Bronze | Silver | Gold |
|---|---|---|---|---|
| customers | 4 | 4 | 4 | 4 (`dim_customer`) |
| products | 4 | 4 | 4 | 4 (`dim_product`) |
| orders | **8** | **8** | **7** | **4** (`fact_sales`) |
| dates | — | — | — | 365 (`dim_date`, 2026) |

The 8 → 7 → 4 progression is the story of the entire project. Be able to explain both steps instantly:

- **8 → 7:** the duplicate order 107 is removed.
- **7 → 4:** order 103 (Cancelled), order 105 (Pending + missing amount), and order 106 (invalid customer) are excluded.

### 6.6 Status counts

| Status | Raw file (8 rows) | After de-duplication (7 rows) |
|---|---|---|
| Completed | **6** | **5** |
| Cancelled | **1** | **1** |
| Pending | **1** | **1** |
| **Total** | **8** | **7** |

The duplicate was a `Completed` order, so only that count changes.

Of the 5 deduplicated `Completed` orders, **4 reach `fact_sales`** — order 106 is excluded for its invalid customer reference.

---

## 7. Data quality rules and expected findings

### 7.1 The rules

| check_name | Dataset | Rule | Dimension | Severity | On failure |
|---|---|---|---|---|---|
| `orders_unique_order_id` | silver.orders | `order_id` must be unique | Uniqueness | High | De-duplicate, log, continue |
| `orders_amount_not_null` | silver.orders | `amount` must be non-null | Completeness | High | Quarantine row, alert steward |
| `orders_customer_fk_valid` | gold.fact_sales | Every `customer_id` exists in `dim_customer` | Consistency | High | Reject row, alert steward |
| `orders_product_fk_valid` | gold.fact_sales | Every `product_id` exists in `dim_product` | Consistency | High | Reject row, alert steward |
| `orders_status_in_allowed_values` | silver.orders | `status` ∈ {Completed, Cancelled, Pending} | Validity | Medium | Quarantine row |
| `fact_sales_revenue_reconciles` | gold.fact_sales | `SUM(sale_amount)` = 525.00 | Accuracy | **Critical** | **Stop the pipeline** |

### 7.2 Expected findings on the sample data

This is the exact content of `data/quality_results/` after a clean run.

| check_name | check_status | failed_record_count | processing_date |
|---|---|---|---|
| `orders_unique_order_id` | **FAIL** | **1** | *(run date)* |
| `orders_amount_not_null` | **FAIL** | **1** | *(run date)* |
| `orders_customer_fk_valid` | **FAIL** | **1** | *(run date)* |
| `orders_product_fk_valid` | **PASS** | **0** | *(run date)* |
| `orders_status_in_allowed_values` | **PASS** | **0** | *(run date)* |
| `fact_sales_revenue_reconciles` | **PASS** | **0** | *(run date)* |

Schema of the results table (exactly four columns):

```text
check_name           STRING
check_status         STRING    -- 'PASS' or 'FAIL'
failed_record_count  BIGINT
processing_date      DATE
```

> **Say this in an interview:** *"The three failures are expected — they're the defects I deliberately seeded. If they ever pass, my checks have broken, not my data. That's why the expected results are documented in `docs/data_quality_rules.md`."*
>
> That single observation demonstrates more maturity than a perfect-looking green dashboard.

### 7.3 Why counts, never contents

The results table stores **counts only**, never the offending row values. Reasons:

1. Row contents could include Confidential data; the counts table stays Internal and can be shared freely.
2. Counts are trendable over time; row dumps are not.
3. A quality table that accumulates row contents eventually becomes a shadow copy of the dataset.

Offending rows go to a separate, access-controlled **quarantine** location with a `rejection_reason` column.

### 7.4 Quarantine contents for the sample

| order_id | rejection_reason | rejected_at_layer |
|---|---|---|
| 105 | `MISSING_MANDATORY_AMOUNT` | silver |
| 106 | `CUSTOMER_REFERENCE_NOT_FOUND` | gold |

Exactly **2 rows**. The duplicate 107 is not quarantined — de-duplication is a normal transformation, not a rejection, and the order is still legitimately recognised once.

### 7.5 Quality rules expressed in SQL

```sql
-- orders_unique_order_id → expect 1
SELECT COUNT(*) AS failed_record_count
FROM (
    SELECT order_id
    FROM bronze_orders
    GROUP BY order_id
    HAVING COUNT(*) > 1
) duplicated;

-- orders_amount_not_null → expect 1
SELECT COUNT(*) AS failed_record_count
FROM silver_orders
WHERE amount IS NULL;

-- orders_customer_fk_valid → expect 1
SELECT COUNT(*) AS failed_record_count
FROM silver_orders o
LEFT JOIN dim_customer c ON o.customer_id = c.customer_id
WHERE c.customer_id IS NULL;

-- orders_product_fk_valid → expect 0
SELECT COUNT(*) AS failed_record_count
FROM silver_orders o
LEFT JOIN dim_product p ON o.product_id = p.product_id
WHERE p.product_id IS NULL;

-- orders_status_in_allowed_values → expect 0
SELECT COUNT(*) AS failed_record_count
FROM silver_orders
WHERE status NOT IN ('Completed', 'Cancelled', 'Pending');

-- fact_sales_revenue_reconciles → expect SUM = 525.00
SELECT
    SUM(sale_amount)                                      AS actual_revenue,
    CAST(525.00 AS DECIMAL(10,2))                         AS expected_revenue,
    CASE WHEN SUM(sale_amount) = 525.00 THEN 'PASS' ELSE 'FAIL' END AS check_status
FROM fact_sales;
```

---

## 8. Layer definitions and expected outputs

### 8.1 Bronze — faithful landing

**Definition:** an unmodified copy of the raw data in an efficient columnar format, with ingestion metadata added. **No business logic whatsoever.**

**Rules:**
- Read with an explicit schema (never infer).
- Add `ingestion_timestamp` and `source_file`.
- Preserve every row, including defective ones.
- Write Parquet, overwrite mode (idempotent).

**Expected output:**

| Dataset | Rows | Added columns |
|---|---|---|
| `bronze/customers` | 4 | `ingestion_timestamp`, `source_file` |
| `bronze/products` | 4 | `ingestion_timestamp`, `source_file` |
| `bronze/orders` | **8** | `ingestion_timestamp`, `source_file` |

> **Bronze must still contain all 8 order rows, including the duplicate.** A common beginner error is cleaning at bronze. That destroys your ability to prove what the source sent, and makes source-system disputes unwinnable.

### 8.2 Silver — cleansed and conformed

**Definition:** typed, trimmed, de-duplicated, validated data at full detail. Business-ready but not aggregated or modelled.

**Rules applied:**

| Rule ID | Rule | Effect on sample |
|---|---|---|
| TR-02 | Cast `order_date` → DATE, `amount` → DECIMAL(10,2), `quantity` → INT | Enables correct arithmetic |
| TR-03 | De-duplicate on `order_id`, keeping latest `updated_at` | **8 → 7 rows** |
| TR-04 | Trim and title-case `region` | No visible change in the sample (already clean) |
| TR-05 | Flag rows with null mandatory fields | Order 105 flagged |

**Expected output:**

| Dataset | Rows | Notes |
|---|---|---|
| `silver/customers` | 4 | Typed, trimmed |
| `silver/products` | 4 | Typed, trimmed |
| `silver/orders` | **7** | Duplicate 107 removed; 105 still present but flagged |

**Status breakdown of silver orders:** Completed 5, Cancelled 1, Pending 1.

**Deterministic de-duplication:**

```python
from pyspark.sql import functions as F
from pyspark.sql.window import Window


def deduplicate_orders(orders_df):
    """Keep exactly one row per order_id, deterministically.

    Ordering by updated_at DESC then amount DESC guarantees the same
    survivor on every run. dropDuplicates() does NOT make that guarantee,
    which is why reruns can silently change results.
    """
    window = (
        Window.partitionBy("order_id")
        .orderBy(F.col("updated_at").desc(), F.col("amount").desc_nulls_last())
    )
    return (
        orders_df
        .withColumn("_rn", F.row_number().over(window))
        .filter(F.col("_rn") == 1)
        .drop("_rn")
    )
```

### 8.3 Gold — the modelled star

**Definition:** a dimensional model shaped for consumption. Referentially complete, business-rule applied, ready to query without expertise.

**Rules applied:**

| Rule ID | Rule | Effect on sample |
|---|---|---|
| TR-06 | Reject fact rows whose `customer_id` is absent from `dim_customer` | Rejects order 106 |
| TR-07 | `fact_sales` includes only `status = 'Completed'` | Excludes 103 (Cancelled), 105 (Pending) |
| TR-08 | `date_key` = `yyyyMMdd` from `order_date` | 2026-01-05 → 20260105 |
| TR-09 | Net result: 4 fact rows summing to **525.00** | The canonical figure |

**Expected output:**

| Dataset | Rows | Key facts |
|---|---|---|
| `gold/dim_customer` | 4 | Keys 1–4; contains Confidential `customer_name` |
| `gold/dim_product` | 4 | Keys 1001–1004 |
| `gold/dim_date` | 365 | Full 2026 calendar |
| `gold/fact_sales` | **4** | Orders 101, 102, 104, 107; `SUM(sale_amount)` = **525.00** |

**The `fact_sales` contents in full:**

| order_id | customer_key | product_key | date_key | quantity | sale_amount | order_status |
|---|---|---|---|---|---|---|
| 101 | 1 | 1003 | 20260105 | 1 | 200.00 | Completed |
| 102 | 1 | 1002 | 20260110 | 2 | 50.00 | Completed |
| 104 | 3 | 1004 | 20260201 | 1 | 75.00 | Completed |
| 107 | 1 | 1003 | 20260215 | 1 | 200.00 | Completed |
| | | | | | **525.00** | |

Memorise this table. Every reporting answer in Section 10 is derivable from it in your head, which is exactly what you want in an interview.

### 8.4 The exclusion ledger

| Order | Amount | In silver? | In fact_sales? | Reason excluded |
|---|---|---|---|---|
| 101 | 200.00 | ✅ | ✅ | — |
| 102 | 50.00 | ✅ | ✅ | — |
| 103 | 50.00 | ✅ | ❌ | Status = `Cancelled` |
| 104 | 75.00 | ✅ | ✅ | — |
| 105 | *(null)* | ✅ (flagged) | ❌ | Status = `Pending` **and** missing mandatory `amount` |
| 106 | 50.00 | ✅ | ❌ | `customer_id` 99 not found in `dim_customer` |
| 107 | 200.00 | ✅ | ✅ | — |
| 107 (dup) | 200.00 | ❌ | ❌ | Duplicate removed during de-duplication |

> **Design decision worth defending:** order 106 is **rejected**, not routed to an "Unknown customer" member. For a revenue figure, attributing 50.00 to a customer who does not exist is worse than excluding it and reporting the exclusion. The alternative design — an `Unknown` dimension member — is valid when completeness matters more than attribution, and would make the total 575.00. We chose attribution accuracy, documented the choice, and surfaced the exclusion through `orders_customer_fk_valid`. Being able to articulate *both* options and justify the choice is what interviewers are listening for.

---

## 9. Star schema design

### 9.1 The model

```text
                      ┌──────────────────────┐
                      │     dim_date         │
                      │──────────────────────│
                      │ PK date_key (INT)    │
                      │    full_date         │
                      │    year, quarter     │
                      │    month, month_name │
                      │    day_of_week       │
                      │    is_weekend        │
                      │  365 rows (2026)     │
                      └──────────┬───────────┘
                                 │
┌──────────────────────┐         │         ┌──────────────────────┐
│    dim_customer      │         │         │     dim_product      │
│──────────────────────│         │         │──────────────────────│
│ PK customer_key      │         │         │ PK product_key       │
│    customer_id       │         │         │    product_id        │
│    customer_name 🔒  │         │         │    product_name      │
│    customer_region   │         │         │    category          │
│    customer_created… │         │         │    unit_price        │
│  4 rows              │         │         │  4 rows              │
└──────────┬───────────┘         │         └──────────┬───────────┘
           │                     │                    │
           │         ┌───────────▼────────────┐       │
           └────────►│      fact_sales        │◄──────┘
                     │────────────────────────│
                     │ PK  order_id (degen.)  │
                     │ FK  customer_key       │
                     │ FK  product_key        │
                     │ FK  date_key           │
                     │     quantity           │
                     │     sale_amount        │
                     │     order_status       │
                     │     processing_date    │
                     │  4 rows · 525.00       │
                     └────────────────────────┘

                 🔒 = Confidential (direct PII)
```

### 9.2 Grain statement

> **One row in `fact_sales` represents exactly one valid, de-duplicated, `Completed` sales order, identified uniquely by `order_id`.**

Consequences of that statement:

- `order_id` is unique in `fact_sales` — counting rows counts recognised orders.
- A multi-line order would break this grain and require a redesign (see assumption A2).
- `COUNT(*)` on `fact_sales` = 4 = the number of recognised orders.
- `SUM(sale_amount)` is fully additive across any dimension.

### 9.3 Table definitions

```sql
-- =====================================================================
-- gold.dim_customer
-- Grain: one row per customer (Type 1 — latest attributes overwrite).
-- Classification: Confidential (contains customer_name).
-- =====================================================================
CREATE TABLE gold.dim_customer (
    customer_key          INT           NOT NULL,   -- surrogate key
    customer_id           INT           NOT NULL,   -- natural key from source
    customer_name         VARCHAR(200)  NOT NULL,   -- CONFIDENTIAL: direct PII
    customer_region       VARCHAR(50)   NOT NULL,   -- North | South | West
    customer_created_date DATE          NOT NULL,
    CONSTRAINT pk_dim_customer PRIMARY KEY (customer_key),
    CONSTRAINT uq_dim_customer_natural UNIQUE (customer_id)
);

-- =====================================================================
-- gold.dim_product
-- Grain: one row per product. Category denormalised (star, not snowflake).
-- Classification: Public.
-- =====================================================================
CREATE TABLE gold.dim_product (
    product_key   INT           NOT NULL,
    product_id    INT           NOT NULL,
    product_name  VARCHAR(200)  NOT NULL,
    category      VARCHAR(100)  NOT NULL,   -- Accessories | Hardware
    unit_price    DECIMAL(10,2) NOT NULL,   -- LIST price, not transacted amount
    CONSTRAINT pk_dim_product PRIMARY KEY (product_key),
    CONSTRAINT uq_dim_product_natural UNIQUE (product_id)
);

-- =====================================================================
-- gold.dim_date
-- Grain: one row per calendar date. Pre-generated, not derived at query time.
-- Classification: Public.
-- =====================================================================
CREATE TABLE gold.dim_date (
    date_key     INT          NOT NULL,   -- yyyyMMdd, e.g. 20260105
    full_date    DATE         NOT NULL,
    year         SMALLINT     NOT NULL,
    quarter      TINYINT      NOT NULL,
    month        TINYINT      NOT NULL,
    month_name   VARCHAR(20)  NOT NULL,
    day_of_month TINYINT      NOT NULL,
    day_of_week  TINYINT      NOT NULL,
    day_name     VARCHAR(20)  NOT NULL,
    is_weekend   BOOLEAN      NOT NULL,
    CONSTRAINT pk_dim_date PRIMARY KEY (date_key)
);

-- =====================================================================
-- gold.fact_sales
-- Grain: one row per valid, de-duplicated, Completed order (by order_id).
-- Classification: Internal (no direct PII — keys only).
-- Sample: 4 rows, SUM(sale_amount) = 525.00
-- =====================================================================
CREATE TABLE gold.fact_sales (
    order_id        INT           NOT NULL,   -- degenerate dimension + grain key
    customer_key    INT           NOT NULL,
    product_key     INT           NOT NULL,
    date_key        INT           NOT NULL,
    quantity        INT           NOT NULL,
    sale_amount     DECIMAL(10,2) NOT NULL,   -- transacted amount, fully additive
    order_status    VARCHAR(20)   NOT NULL,   -- always 'Completed' in this table
    processing_date DATE          NOT NULL,   -- pipeline run date (NOT business date)
    CONSTRAINT pk_fact_sales PRIMARY KEY (order_id),
    CONSTRAINT fk_fact_customer FOREIGN KEY (customer_key) REFERENCES gold.dim_customer(customer_key),
    CONSTRAINT fk_fact_product  FOREIGN KEY (product_key)  REFERENCES gold.dim_product(product_key),
    CONSTRAINT fk_fact_date     FOREIGN KEY (date_key)     REFERENCES gold.dim_date(date_key)
);
```

### 9.4 Key design decisions

| Decision | Choice | Reasoning |
|---|---|---|
| Surrogate keys | Simple integers; `customer_key` seeded from `customer_id` for this small sample | Insulates the warehouse from source key changes |
| `date_key` format | `yyyyMMdd` integer | Human-readable, sortable, compact, industry convention |
| `order_id` on the fact | Degenerate dimension, also the PK | Preserves traceability to the source without a pointless dimension table |
| Snowflaking `category` | **No** — denormalised into `dim_product` | Fewer joins, simpler for analysts; storage cost is trivial |
| SCD type on `dim_customer` | **Type 1** | Documented decision; Type 2 would be needed if historical regional reporting mattered |
| Unknown members | **Not used** | Order 106 is excluded rather than attributed to a fake customer |
| `unit_price` on `dim_product` | Kept, but **never** used to compute revenue | Revenue comes from `fact_sales.sale_amount` only |
| `order_status` on the fact | Kept despite always being `Completed` | Makes the filter self-documenting and supports a future design change |

### 9.5 Measure additivity

| Measure | Additivity | Safe to sum across… | Notes |
|---|---|---|---|
| `sale_amount` | **Fully additive** | date, customer, product, region — all dimensions | The basis of every reported figure |
| `quantity` | **Fully additive** | all dimensions | Units sold |
| Average order value | **Non-additive** | nothing | Must be recomputed as `SUM(sale_amount) / COUNT(DISTINCT order_id)`, never averaged |
| `unit_price` | **Non-additive** | nothing | A dimension attribute, not a measure |

> Storing a pre-computed average in a fact table is one of the most common modelling errors. Averages of averages are wrong. Store the numerator and the denominator; compute the ratio at query time.

### 9.6 Bus matrix

| Business process | dim_date | dim_customer | dim_product | Status (future) |
|---|---|---|---|---|
| **Sales orders** (built) | ✅ | ✅ | ✅ | degenerate |
| Returns (future) | ✅ | ✅ | ✅ | ✅ |
| Shipments (future) | ✅ | ✅ | ✅ | — |
| Inventory snapshot (future) | ✅ | — | ✅ | — |

The matrix shows that `dim_date`, `dim_customer` and `dim_product` would be **conformed dimensions** — shared unchanged across future processes. That's what turns a single star into a warehouse.

---

## 10. Reporting questions and expected answers

These are the canonical answers. **Verify every implementation against this section.**

### 10.1 Q1 — Monthly recognised revenue

```sql
SELECT
    d.year,
    d.month,
    SUM(f.sale_amount) AS recognised_revenue
FROM gold.fact_sales f
JOIN gold.dim_date d ON f.date_key = d.date_key
GROUP BY d.year, d.month
ORDER BY d.year, d.month;
```

| year | month | recognised_revenue | Contributing orders |
|---|---|---|---|
| 2026 | 1 | **250.00** | 101 (200.00) + 102 (50.00) |
| 2026 | 2 | **275.00** | 104 (75.00) + 107 (200.00) |
| | **Total** | **525.00** | |

### 10.2 Q2 — Revenue by region

```sql
SELECT
    c.customer_region,
    SUM(f.sale_amount) AS recognised_revenue
FROM gold.fact_sales f
JOIN gold.dim_customer c ON f.customer_key = c.customer_key
GROUP BY c.customer_region
ORDER BY recognised_revenue DESC;
```

| customer_region | recognised_revenue | Explanation |
|---|---|---|
| **North** | **525.00** | Aisha Khan (450.00) + Carla Jones (75.00) — both North customers |
| **South** | **0.00** | Ben Smith's only order (103) is Cancelled |
| **West** | **0.00** | David Lee has no orders at all |
| **Total** | **525.00** | |

> ⚠️ **Note on zero rows.** An inner join from `fact_sales` returns only North. To show South and West as 0.00 you must start from `dim_customer` and LEFT JOIN the fact:

```sql
SELECT
    c.customer_region,
    COALESCE(SUM(f.sale_amount), 0.00) AS recognised_revenue
FROM gold.dim_customer c
LEFT JOIN gold.fact_sales f ON f.customer_key = c.customer_key
GROUP BY c.customer_region
ORDER BY recognised_revenue DESC;
```

This is a genuinely instructive detail: *"revenue by region" and "revenue by region including regions with no revenue" are different queries.* Knowing which one the stakeholder meant is the job.

> 🔒 **Privacy caveat.** West has exactly one customer, so publishing a West figure reveals that individual's revenue exactly. See `09_governance_security_delivery.md` Section 15.3 on re-identification through aggregation.

### 10.3 Q3 — Customer revenue ranking

```sql
SELECT
    c.customer_name,
    c.customer_region,
    COUNT(DISTINCT f.order_id) AS recognised_orders,
    SUM(f.sale_amount)         AS recognised_revenue
FROM gold.fact_sales f
JOIN gold.dim_customer c ON f.customer_key = c.customer_key
GROUP BY c.customer_name, c.customer_region
ORDER BY recognised_revenue DESC;
```

| customer_name | region | recognised_orders | recognised_revenue | Orders |
|---|---|---|---|---|
| **Aisha Khan** | North | 3 | **450.00** | 101 (200.00) + 102 (50.00) + 107 (200.00) |
| **Carla Jones** | North | 1 | **75.00** | 104 (75.00) |
| | | **4** | **525.00** | |

Ben Smith and David Lee do not appear: Ben's only order was Cancelled, and David has no orders.

### 10.4 Q4 — Top products by recognised revenue

```sql
SELECT
    p.product_id,
    p.product_name,
    p.category,
    SUM(f.quantity)    AS units_sold,
    SUM(f.sale_amount) AS recognised_revenue
FROM gold.fact_sales f
JOIN gold.dim_product p ON f.product_key = p.product_key
GROUP BY p.product_id, p.product_name, p.category
ORDER BY recognised_revenue DESC;
```

| product_id | product_name | category | units_sold | recognised_revenue | Orders |
|---|---|---|---|---|---|
| 1003 | **Monitor** | Hardware | 2 | **400.00** | 101 + 107 |
| 1004 | **Keyboard** | Accessories | 1 | **75.00** | 104 |
| 1002 | **Wireless Mouse** | Accessories | 2 | **50.00** | 102 |
| | | | **5** | **525.00** | |

Laptop Bag (1001) earns 0.00: its only appearances are order 103 (Cancelled) and order 106 (invalid customer).

### 10.5 Q5 — Orders by status

```sql
-- Raw file, before any processing (8 rows)
SELECT status, COUNT(*) AS order_count
FROM bronze_orders
GROUP BY status
ORDER BY status;

-- Silver, after de-duplication (7 rows)
SELECT status, COUNT(*) AS order_count
FROM silver_orders
GROUP BY status
ORDER BY status;
```

| status | Raw (8 rows) | After de-duplication (7 rows) |
|---|---|---|
| Cancelled | **1** | **1** |
| Completed | **6** | **5** |
| Pending | **1** | **1** |
| **Total** | **8** | **7** |

### 10.6 The reconciliation sanity check

Every breakdown must sum to the same total. Make this an explicit test.

| Breakdown | Components | Total |
|---|---|---|
| By month | 250.00 + 275.00 | **525.00** ✅ |
| By region | 525.00 + 0.00 + 0.00 | **525.00** ✅ |
| By customer | 450.00 + 75.00 | **525.00** ✅ |
| By product | 400.00 + 75.00 + 50.00 | **525.00** ✅ |
| By order | 200.00 + 50.00 + 75.00 + 200.00 | **525.00** ✅ |

```sql
-- Prove all breakdowns reconcile in one query.
WITH by_month AS (
    SELECT SUM(sale_amount) AS total FROM gold.fact_sales
),
by_region AS (
    SELECT SUM(f.sale_amount) AS total
    FROM gold.fact_sales f
    JOIN gold.dim_customer c ON f.customer_key = c.customer_key
),
by_product AS (
    SELECT SUM(f.sale_amount) AS total
    FROM gold.fact_sales f
    JOIN gold.dim_product p ON f.product_key = p.product_key
)
SELECT
    (SELECT total FROM by_month)   AS month_total,
    (SELECT total FROM by_region)  AS region_total,
    (SELECT total FROM by_product) AS product_total,
    CASE
        WHEN (SELECT total FROM by_month)
           = (SELECT total FROM by_region)
         AND (SELECT total FROM by_region)
           = (SELECT total FROM by_product)
         AND (SELECT total FROM by_month) = 525.00
        THEN 'PASS' ELSE 'FAIL'
    END AS reconciliation_status;
```

If a join ever drops or duplicates rows, this query catches it immediately. That is exactly why it exists.

---

## 11. Local learning implementation — BUILT

> ## ✅ EVERYTHING IN THIS SECTION IS BUILT AND RUNNABLE
>
> Runs on a laptop with Python 3.11 and PySpark in local mode. No cloud account, no subscription, no cost. Anyone can clone the repository and reproduce every number in this guide.

### 11.1 What exists

| Component | Technology | Status |
|---|---|---|
| Ingestion | PySpark reading CSV with explicit schemas | ✅ Built |
| Bronze layer | Parquet on local disk | ✅ Built |
| Silver layer | PySpark transformations → Parquet | ✅ Built |
| Gold layer | Star schema → Parquet | ✅ Built |
| Data quality checks | PySpark assertions → Parquet results | ✅ Built |
| Reporting | SQL over Spark temp views | ✅ Built |
| Unit tests | pytest | ✅ Built |
| Configuration | YAML per environment | ✅ Built |
| Logging | Structured, redacting | ✅ Built |
| Documentation | Five documents in `docs/` | ✅ Built |

### 11.2 Prerequisites

```text
Python 3.11
Java 11 or 17 (required by Spark)
8 GB RAM (comfortably sufficient for this dataset)
~500 MB disk
```

### 11.3 Setup

```bash
# From the parent directory containing both repos
cd customer-sales-lakehouse

# Create and activate a virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # Windows PowerShell
# source .venv/bin/activate            # macOS / Linux

pip install --upgrade pip
pip install -r requirements.txt

# Verify Spark starts
python -c "from pyspark.sql import SparkSession; s = SparkSession.builder.master('local[*]').getOrCreate(); print(s.version); s.stop()"
```

### 11.4 Running the pipeline

```bash
# Run every stage in order
python notebooks/01_bronze_ingestion.py
python notebooks/02_silver_customers.py
python notebooks/03_silver_products.py
python notebooks/04_silver_orders.py
python notebooks/05_gold_dimensions.py
python notebooks/06_gold_fact_sales.py
python notebooks/07_data_quality_checks.py

# Run the tests
python -m pytest tests/ -v
```

### 11.5 Verifying it worked

```python
from pyspark.sql import SparkSession, functions as F

spark = SparkSession.builder.master("local[*]").appName("verify").getOrCreate()

fact = spark.read.parquet("data/gold/fact_sales")

print(f"fact_sales rows      : {fact.count()}")                 # 4
total = fact.agg(F.sum("sale_amount")).collect()[0][0]
print(f"recognised revenue   : {total}")                        # 525.00
ids = sorted(r["order_id"] for r in fact.select("order_id").collect())
print(f"order ids            : {ids}")                          # [101, 102, 104, 107]

assert fact.count() == 4
assert float(total) == 525.00
assert ids == [101, 102, 104, 107]
print("✅ Verification passed")

spark.stop()
```

### 11.6 Acceptance criteria

| # | Criterion | Expected |
|---|---|---|
| 1 | Bronze orders row count | 8 |
| 2 | Silver orders row count | 7 |
| 3 | `fact_sales` row count | 4 |
| 4 | `SUM(fact_sales.sale_amount)` | **525.00** |
| 5 | `fact_sales` order IDs | 101, 102, 104, 107 |
| 6 | `dim_customer` / `dim_product` rows | 4 / 4 |
| 7 | `dim_date` rows | 365 |
| 8 | Quality results rows | 6 |
| 9 | Quality checks FAILing | exactly 3, each with count 1 |
| 10 | Orphan foreign keys in `fact_sales` | 0 |
| 11 | Duplicate `order_id` in `fact_sales` | 0 |
| 12 | All pytest tests | pass |
| 13 | Rerunning the full pipeline | produces identical output (idempotent) |

### 11.7 Known limitations — state these before anyone asks

| Limitation | Impact | Why it's acceptable here |
|---|---|---|
| 8 source rows | No performance characteristics observable | Verifiability is the design goal |
| Local Spark only | No distributed shuffle behaviour exercised | API is identical; scale is a config change |
| Parquet not Delta | No ACID, no time travel, no upserts | Delta conversion is a documented next step |
| No orchestrator | Stages run manually in sequence | The DAG is designed in `orchestration/pipeline_design.md` |
| Type 1 dimensions | No historical attribute tracking | Explicit, documented business decision |
| Single currency assumed | Cross-currency totals would be wrong | Stated as assumption A4 |
| One product per order | Grain would change for multi-line orders | Stated as assumption A2 |
| Synthetic data | Not representative of real distributions | All data is fictional by design |

> **In an interview, volunteer these.** An engineer who knows the limitations of their own system is far more credible than one who presents it as flawless.

---

## 12. Azure deployment design — DESIGN ONLY

> ## 📐 NOTHING IN THIS SECTION IS DEPLOYED
>
> This is an **architecture design** showing how the local implementation would map onto Azure services. **No Azure resources have been provisioned. No cloud deployment exists.**
>
> **Describe this as "designed", never as "built", "deployed", or "implemented".** See Section 13.4 for exact wording.

### 12.1 Service mapping

| Local artefact (built) | Azure service (designed) | Role in the design |
|---|---|---|
| `data/raw/*.csv` | ADLS Gen2 — `raw` container | Landing zone for source files |
| `data/bronze/` | ADLS Gen2 — `bronze` container | Immutable system of record |
| `data/silver/` | ADLS Gen2 — `silver` container | Cleansed detail |
| `data/gold/` | ADLS Gen2 — `gold` container | Star schema |
| `data/quality_results/` | ADLS Gen2 — `ops` container | Quality telemetry |
| Parquet format | **Delta Lake** | ACID, time travel, MERGE, schema enforcement |
| `notebooks/*.py` | Databricks notebooks / jobs | Managed Spark execution |
| Manual stage sequencing | **Azure Data Factory** | Orchestration, scheduling, retries, monitoring |
| `config/prod.yml` secret names | **Key Vault** `<key-vault-name>` | Secret storage; values never in config |
| `sql/03_reporting_queries.sql` | Serverless SQL / warehouse | Analyst-facing serving layer |
| `src/logging_utils.py` output | Azure Monitor + Log Analytics | Centralised, queryable logs |
| `.github/workflows/ci.yml` | Same, plus deployment stages | CI/CD into dev → test → prod |

### 12.2 Target architecture

```text
┌─────────────────────────────────────────────────────────────────────────┐
│                  AZURE DEPLOYMENT DESIGN (not deployed)                 │
└─────────────────────────────────────────────────────────────────────────┘

   SOURCE                ORCHESTRATION              COMPUTE
┌────────────┐        ┌──────────────────┐     ┌──────────────────┐
│ Source     │        │ Azure Data       │     │   Databricks     │
│ systems    │───────►│ Factory          │────►│   job clusters   │
│ (CSV drop) │        │                  │     │                  │
└────────────┘        │ • daily trigger  │     │ • bronze → gold  │
                      │ • file validation│     │ • quality checks │
                      │ • notebook tasks │     │ • Delta writes   │
                      │ • retries/alerts │     └────────┬─────────┘
                      └────────┬─────────┘              │
                               │                        ▼
                               │     ┌──────────────────────────────────┐
                               │     │   ADLS Gen2  <storage-account>   │
                               │     │  ┌────────┐ ┌────────┐           │
                               │     │  │  raw   │ │ bronze │           │
                               │     │  ├────────┤ ├────────┤   Delta   │
                               │     │  │ silver │ │  gold  │           │
                               │     │  ├────────┴─┴────────┤           │
                               │     │  │        ops        │           │
                               │     │  └───────────────────┘           │
                               │     └──────────────┬───────────────────┘
                               │                    │
                               │                    ▼
                               │         ┌──────────────────────┐
                               │         │   Serving layer      │
                               │         │   (serverless SQL)   │
                               │         │  • external tables   │
                               │         │  • PII-safe views    │
                               │         └──────────┬───────────┘
                               │                    │
                               │                    ▼
                               │         ┌──────────────────────┐
                               │         │  Reporting / BI      │
                               │         └──────────────────────┘
          ┌────────────────────┴───────────────────┐
          ▼                                        ▼
┌──────────────────────┐              ┌──────────────────────────┐
│ Key Vault            │              │ Azure Monitor /          │
│ <key-vault-name>     │              │ Log Analytics            │
│ • secret NAMES only  │              │ • pipeline run logs      │
│   referenced in code │              │ • storage access logs    │
│ • managed identity   │              │ • alerts on failure      │
│   access             │              │ • quality trend queries  │
└──────────────────────┘              └──────────────────────────┘
```

### 12.3 Designed storage layout

| Container | Path pattern | Format | Tier | Retention | Classification |
|---|---|---|---|---|---|
| `raw` | `/sales/<yyyy>/<MM>/<dd>/*.csv` | CSV | Hot | 90 days | varies |
| `bronze` | `/sales/<entity>/ingestion_date=<yyyy-MM-dd>/` | Delta | Hot → Cool at 30d | 7 years | inherits source |
| `silver` | `/sales/<entity>/` | Delta | Hot | 2 years | Internal / Confidential |
| `gold` | `/sales/<table>/` | Delta | Hot | 7 years | Internal |
| `ops` | `/quality/processing_date=<yyyy-MM-dd>/` | Delta | Hot | 13 months | Internal |

### 12.4 Designed orchestration DAG

```text
Trigger: daily 02:00
    │
    ▼
[Validate source files]  Get Metadata + If Condition — expect 3 files, FAIL FAST
    │
    ▼
[01 Bronze ingestion]    Databricks notebook task
    │
    ├──────────────┬──────────────┐        (parallel — no interdependency)
    ▼              ▼              ▼
[02 Silver     [03 Silver     [04 Silver
 customers]     products]      orders]
    │              │              │
    └──────────────┴──────────────┘
                   ▼
         [05 Gold dimensions]
                   ▼
         [06 Gold fact_sales]
                   ▼
         [07 Quality checks]  ── critical check FAIL ──► ABORT, alert, do not publish
                   ▼
         [Notify success]
```

| Setting | Value | Reason |
|---|---|---|
| Trigger | Tumbling window, daily 02:00 | Supports backfill and dependency chaining |
| Retries | 1, with a 5-minute interval | Covers transient failures without masking real ones |
| Timeout | 60 minutes per task | Prevents a hung task blocking the schedule |
| Failure behaviour | Abort downstream; alert on-call | Bad data must never reach consumers |
| Compute | Job clusters, auto-terminating | Cost control and isolation |
| Idempotency | Overwrite or MERGE per stage | Safe to re-run from any point |

### 12.5 Designed security model

Derived directly from `09_governance_security_delivery.md` Sections 9–11.

| Principal | Access | Scope | Notes |
|---|---|---|---|
| `mi-<project>-pipeline` (managed identity) | Storage Blob Data Contributor | `bronze`, `silver`, `gold`, `ops` | Read-only on `raw`; **no delete** anywhere |
| `mi-<project>-pipeline` | Key Vault Secrets User | `sales-lakehouse-*` secrets only | Scoped to its own secrets |
| `grp-data-engineers` | Contributor (dev), Reader (prod) | Resource group | Prod writes go through CI/CD only |
| `grp-analysts` | Storage Blob Data Reader | `gold` container, PII-safe views | **Cannot see `customer_name`** |
| `grp-analysts-pii` | Storage Blob Data Reader | `gold/dim_customer` | Approved access, quarterly recertification |
| `grp-stewards` | Reader | all containers | Oversight without write capability |
| `grp-auditors` | Reader | `ops` + diagnostic logs | Metadata only |

Principles applied:

- **No secrets anywhere in code, config or history** — only secret *names* appear, resolved at runtime by managed identity.
- **Least privilege across all three dimensions** — permission, scope, and time.
- **PII isolated** to `dim_customer`, surfaced by default only through `vw_dim_customer_safe`.
- **Dev/test/prod separated** by subscription or resource group, with separate vaults.
- **JIT elevation** for production troubleshooting, time-bound and logged.

### 12.6 Designed monitoring

| SLI | Definition | SLO |
|---|---|---|
| Freshness | Hours since `fact_sales` last refreshed successfully | < 26 h, 99% of days |
| Success rate | Successful runs ÷ scheduled runs (30 days) | ≥ 98% |
| Duration | p95 end-to-end runtime | < 30 minutes |
| Quality | Runs where all critical checks PASS | 100% |

| Alert | Condition | Severity | Action |
|---|---|---|---|
| Pipeline failed | Any task fails after retries | High | Page on-call; link to `docs/runbook.md` §4 |
| Revenue reconciliation failed | Critical check FAIL | **Critical** | Abort publication; declare SEV1 |
| Source files missing | < 3 files at trigger time | High | Contact source owner; do not re-run blindly |
| Freshness breach | `fact_sales` older than 26 h | High | Investigate; notify consumers |
| Quality regression | Any check's failed count exceeds its documented baseline | Medium | Steward triage next business day |

### 12.7 Estimated running cost (design estimate only)

| Component | Sizing assumption | Order of magnitude |
|---|---|---|
| ADLS Gen2 | < 1 GB at sample scale | Negligible |
| Data Factory | ~10 activity runs/day | Very low |
| Databricks | 1 small job cluster, ~15 min/day | Low |
| Serverless SQL | Occasional ad-hoc queries | Low, pay-per-scan |
| Key Vault | A handful of operations/day | Negligible |
| Log Analytics | Modest ingestion | Low |

> These are **order-of-magnitude design estimates**, not quotes. Pricing varies by region, SKU and time. Always check current pricing before committing. **Nothing here has been provisioned, so nothing has been billed.**

### 12.8 What would need to be proven before calling this "deployed"

Be explicit about the gap. It is a strength, not a weakness.

- [ ] Resources provisioned (ideally via infrastructure as code)
- [ ] Managed identities created and role assignments applied
- [ ] Key Vault populated and referenced successfully at runtime
- [ ] Notebooks executing on Databricks job clusters
- [ ] ADF pipeline running end to end on a schedule
- [ ] Delta conversion completed and verified
- [ ] Diagnostic settings routing to Log Analytics
- [ ] Alerts firing correctly in a controlled test
- [ ] Recognised revenue of 525.00 reproduced in the cloud environment
- [ ] CI/CD promoting through dev → test → prod

Until every box is ticked, the correct word is **designed**.

---

## 13. Presenting this project: CV and interview

### 13.1 Sample CV bullets

Pick three to five. Adapt the wording to the role, never the facts.

**Core set:**

- Built an end-to-end medallion lakehouse (raw → bronze → silver → gold) in **PySpark** and **Parquet**, transforming three raw CSV feeds into a dimensional star schema with a documented, reconciled revenue figure.
- Designed and implemented a **star schema** (`dim_customer`, `dim_product`, `dim_date`, `fact_sales`) at one-row-per-order grain, enforcing referential integrity and removing duplicate, incomplete, and orphaned records.
- Implemented an automated **data quality framework** producing named check results (`check_name`, `check_status`, `failed_record_count`, `processing_date`), detecting duplicate keys, missing mandatory values, and broken foreign keys on every run.
- Established a **reconciliation control** asserting that recognised revenue matches the agreed figure across monthly, regional, customer and product breakdowns, wired into CI so a regression in business logic fails the build.
- Authored full delivery documentation — architecture, data dictionary, transformation rules, data quality rules, and an operational runbook — so the pipeline can be run and supported by someone other than the author.
- **Designed** an Azure deployment architecture mapping the local lakehouse onto **ADLS Gen2, Azure Data Factory, Databricks and Delta Lake**, including storage layout, orchestration DAG, RBAC model, secrets handling and monitoring SLOs.

**Additional bullets if the role emphasises governance, testing, or CI/CD:**

- Applied data governance practice: per-column classification (Public/Internal/Confidential), PII isolation and masking, retention scheduling, and an ownership RACI.
- Separated dev and production behaviour entirely through configuration (`config/dev.yml` / `config/prod.yml`), with secrets referenced by name only and never stored in code or version control.
- Built a **pytest** suite covering transformation logic and quality checks, integrated into a CI workflow that runs the full pipeline and verifies output totals on every pull request.

### 13.2 The 60-second verbal summary

Rehearse this until it's natural. Time yourself.

> *"I built a project called customer-sales-lakehouse — an end-to-end medallion lakehouse that turns three raw CSV feeds into a star schema with one trustworthy revenue number.*
>
> *The data is deliberately dirty. Eight order rows containing a duplicate order ID, an order with a missing amount, and an order referencing a customer who doesn't exist. That's the point — if you just sum the raw file you get the wrong answer, and different people get different wrong answers.*
>
> *So I built it in layers. Bronze keeps the data exactly as it arrived, defects and all, so it's replayable. Silver types and de-duplicates it, which takes eight rows down to seven. Gold applies the business rule — only valid, de-duplicated, completed orders count as revenue — which gives four fact rows totalling 525.*
>
> *Every run writes data quality results, and three checks fail on purpose, because those are the defects I seeded. I've got a reconciliation test in CI that asserts the 525 total, so if someone breaks the de-duplication logic the build fails rather than the number quietly changing.*
>
> *It's built and runnable locally in PySpark — anyone can clone it and reproduce that number. I've also designed how it would map onto Azure — ADLS Gen2, Data Factory, Databricks, Delta — but I want to be clear that part is a design, not a deployment."*

**Why this works:**

| Element | Effect |
|---|---|
| Opens with the problem, not the tech | Shows you think about outcomes |
| Concrete numbers (8 → 7 → 4, 525.00) | Proves you actually built it |
| "Deliberately dirty" | Signals intent, not accident |
| "Three checks fail on purpose" | Memorable, and demonstrates real understanding |
| CI reconciliation test | Shows engineering rigour, not just scripting |
| Explicit Azure honesty at the end | Builds trust and pre-empts the probe |

### 13.3 Likely interview questions

**About the data and the number**

1. "Why is revenue 525 and not some other figure? Walk me through it." → 101 (200) + 102 (50) + 104 (75) + 107 (200).
2. "Why did you exclude order 106?" → `customer_id` 99 doesn't exist in the customer master; attributing revenue to a non-existent customer is worse than excluding it and reporting the exclusion.
3. "Order 105 is both Pending and missing an amount. Which rule excludes it?" → Either would; both do. Good opportunity to discuss rule precedence and why documenting both matters.
4. "Why keep the duplicate in bronze instead of cleaning it on arrival?" → Bronze is the replayable system of record; cleaning there destroys the ability to prove what the source sent.
5. "Aisha Khan is 450 and Carla Jones is 75. Where do those come from?" → Aisha: orders 101, 102, 107. Carla: order 104. Both are North customers, which is why North is 525.
6. "Why are South and West both zero?" → Ben Smith's only order is Cancelled; David Lee has no orders at all.

**About design decisions**

7. "Why a star schema rather than querying silver directly?" → Analyst self-service, query performance, and a single place the business rule lives.
8. "Why Type 1 on `dim_customer`? When would you need Type 2?" → Historical regional reporting; discuss the cost and the owner decision.
9. "Why not use `quantity × unit_price` for revenue?" → `unit_price` is list price; `amount` is transacted value. They can legitimately differ.
10. "Why Parquet and not CSV?" → Columnar, typed, compressed, splittable, predicate pushdown.
11. "What would change if orders had multiple line items?" → The grain changes to order + product; the PK changes; every reporting query must be re-validated.

**About quality and reliability**

12. "Which check should stop the pipeline, and why that one?" → The revenue reconciliation. Publishing a wrong number is worse than publishing nothing.
13. "Three checks fail every run. Isn't that alert fatigue?" → Yes if unmanaged. The expected baseline is documented; alerts fire on *deviation* from baseline, not on failure itself.
14. "How do you know a re-run won't double-count?" → Idempotent writes (overwrite/MERGE) plus the reconciliation test that asserts the total is unchanged.
15. "How would you detect data that's wrong but passes all your checks?" → Anomaly detection against historical baselines, reconciliation against an independent source, and stakeholder feedback loops.

**About governance and security**

16. "Which columns are PII, and how are they protected?" → `customer_name` is direct PII (Confidential); `region` and `customer_created_date` are quasi-identifying. Isolated to `dim_customer`, excluded from default views and all logs.
17. "Your West region has one customer. What's the risk in a regional report?" → Re-identification through aggregation; discuss k-anonymity and access control.
18. "Where would secrets live?" → Key Vault, referenced by name; never in code, config, notebooks or history. Managed identity removes the bootstrap secret entirely.

**About scope and honesty**

19. "Did you deploy this to Azure?" → **"No — the Azure part is a design. The implementation runs locally in PySpark."** Then describe the design confidently.
20. "What's the weakest part of this project?" → Scale. Eight rows prove correctness but exercise none of Spark's distributed behaviour. Next step is generating millions of synthetic rows and tuning.
21. "What would you do differently?" → Start with Delta instead of Parquet; write the reconciliation test before the gold layer rather than after.

### 13.4 The honesty note — say it exactly like this

**✅ Accurate phrasings:**

- "I **built** a local medallion lakehouse in PySpark and **designed** its Azure deployment."
- "The implementation runs locally. The Azure architecture is a design, not a deployment."
- "I've **mapped** the local components onto ADLS Gen2, Data Factory, Databricks and Delta, and documented the storage layout, orchestration DAG and security model."
- "I haven't run this in a live Azure subscription — I'd want to be clear about that."

**❌ Phrasings that will damage you:**

- ~~"I built an Azure data platform."~~
- ~~"I deployed a lakehouse on ADLS Gen2."~~
- ~~"I have production experience with Data Factory and Databricks."~~
- ~~"I implemented Delta Lake in Azure."~~

**Why this matters more than it might seem:**

1. **Interviewers verify.** A single follow-up question — "what SKU was your cluster?" or "how did ADF authenticate to storage?" — exposes an overclaim instantly.
2. **The overclaim is unnecessary.** A well-built local project with an honest, detailed cloud design is *more* impressive than a vague claim of cloud experience, because it is verifiable.
3. **Honesty is the signal.** Someone who says "this part is designed, not deployed" without being asked has just demonstrated the judgement that makes them trustworthy with production systems.
4. **It costs you nothing.** Nobody expects a self-study portfolio project to have a funded Azure subscription behind it. Everyone expects you to know the difference.

> **The rule:** if you cannot show the resource, the run history, or the logs, you **designed** it. You did not deploy it.

### 13.5 Portfolio README structure

The repository README is the first thing anyone reads. Structure it so the boundary is impossible to miss.

```markdown
# customer-sales-lakehouse

End-to-end medallion lakehouse turning three raw CSV feeds into a trusted
star schema with one agreed revenue figure.

## ✅ Built and runnable locally
PySpark · Parquet · Python 3.11 · pytest
Clone, install, run — reproduces recognised revenue of **525.00** in minutes.

## 📐 Designed, not deployed
Azure architecture (ADLS Gen2, Data Factory, Databricks, Delta Lake,
serving layer) is documented in `docs/architecture.md`.
**No Azure resources have been provisioned.**

## Quick start
...

## The data (deliberately dirty)
8 raw order rows containing 1 duplicate ID, 1 missing amount,
1 invalid customer reference. 8 → 7 after de-duplication → 4 in fact_sales.

## The canonical answer
Recognised revenue = **525.00** (orders 101, 102, 104, 107)
...
```

---

## 14. Testing plan

Four layers of testing, each catching a different class of failure.

```text
                    ┌──────────────────────────┐
                    │   Reconciliation tests   │   Fewest, highest value
                    │  "Is the TOTAL right?"   │   525.00 across every breakdown
                    ├──────────────────────────┤
                    │   Data quality tests     │
                    │  "Is the DATA right?"    │   6 named checks, run every pipeline run
                    ├──────────────────────────┤
                    │   Integration tests      │
                    │ "Do the STAGES connect?" │   Full pipeline, end to end
                    ├──────────────────────────┤
                    │      Unit tests          │   Most numerous, fastest
                    │ "Is the LOGIC right?"    │   Pure functions, no I/O
                    └──────────────────────────┘
```

### 14.1 Unit tests — `tests/test_transformations.py`

Target pure functions in `src/transformations.py`. Fast, deterministic, no file I/O.

| Test | Asserts |
|---|---|
| `test_deduplicate_removes_exact_duplicate` | 8 input rows → 7 output rows |
| `test_deduplicate_keeps_latest_updated_at` | The surviving row is the one with the latest `updated_at` |
| `test_deduplicate_is_deterministic` | Running twice yields identical results |
| `test_cast_types_produces_decimal_amount` | `amount` is `DecimalType(10,2)`, not string or double |
| `test_standardise_region_trims_and_titlecases` | `' north '` → `'North'` |
| `test_filter_completed_excludes_cancelled_and_pending` | Orders 103 and 105 removed |
| `test_date_key_format` | `2026-01-05` → `20260105` |
| `test_reject_orphan_customers` | Order 106 removed when joined to a 4-row dimension |

```python
import pytest
from decimal import Decimal
from pyspark.sql import SparkSession
from src.transformations import deduplicate_orders, build_date_key


@pytest.fixture(scope="session")
def spark():
    session = (
        SparkSession.builder
        .appName("unit-tests")
        .master("local[2]")
        .config("spark.sql.shuffle.partitions", "2")
        .getOrCreate()
    )
    yield session
    session.stop()


def test_deduplicate_removes_exact_duplicate(spark):
    rows = [
        (101, 1, 1003, "2026-01-05", 1, Decimal("200.00"), "Completed", "2026-01-05 10:00:00"),
        (107, 1, 1003, "2026-02-15", 1, Decimal("200.00"), "Completed", "2026-02-15 08:00:00"),
        (107, 1, 1003, "2026-02-15", 1, Decimal("200.00"), "Completed", "2026-02-15 08:00:00"),
    ]
    columns = [
        "order_id", "customer_id", "product_id", "order_date",
        "quantity", "amount", "status", "updated_at",
    ]
    df = spark.createDataFrame(rows, columns)
    result = deduplicate_orders(df)

    assert result.count() == 2
    assert result.filter("order_id = 107").count() == 1


def test_date_key_format(spark):
    assert build_date_key("2026-01-05") == 20260105
    assert build_date_key("2026-02-15") == 20260215
```

### 14.2 Integration tests

Run the real stages against the real sample files and assert the layer contracts.

| Test | Asserts |
|---|---|
| `test_bronze_preserves_all_raw_rows` | `bronze/orders` = 8 rows, defects intact |
| `test_silver_orders_deduplicated` | `silver/orders` = 7 rows |
| `test_silver_status_counts` | Completed 5, Cancelled 1, Pending 1 |
| `test_gold_dimensions_row_counts` | `dim_customer` 4, `dim_product` 4, `dim_date` 365 |
| `test_fact_sales_has_no_orphans` | Left-anti join to each dimension returns 0 |
| `test_pipeline_is_idempotent` | Full run twice → identical row counts and totals |

### 14.3 Data quality tests

These are the production checks from Section 7, executed as part of every run and asserted in CI.

| Test | Asserts |
|---|---|
| `test_quality_results_has_six_checks` | 6 rows written |
| `test_expected_failures` | Exactly 3 FAIL, each with `failed_record_count` = 1 |
| `test_expected_passes` | `orders_product_fk_valid` and `orders_status_in_allowed_values` PASS with 0 |
| `test_quality_schema` | Columns are exactly `check_name`, `check_status`, `failed_record_count`, `processing_date` |
| `test_quarantine_contains_two_rows` | Orders 105 and 106 with correct rejection reasons |

### 14.4 Reconciliation tests — the most important ones

```python
import pytest
from pyspark.sql import functions as F


def test_recognised_revenue_total(fact_sales):
    total = fact_sales.agg(F.sum("sale_amount")).collect()[0][0]
    assert float(total) == pytest.approx(525.00, abs=0.001)


def test_expected_order_ids(fact_sales):
    actual = {r["order_id"] for r in fact_sales.select("order_id").collect()}
    assert actual == {101, 102, 104, 107}


def test_monthly_breakdown_reconciles(fact_sales):
    monthly = {
        r["m"]: float(r["rev"])
        for r in (
            fact_sales
            .withColumn("m", (F.col("date_key") / 100).cast("int"))
            .groupBy("m").agg(F.sum("sale_amount").alias("rev"))
            .collect()
        )
    }
    assert monthly[202601] == pytest.approx(250.00, abs=0.001)
    assert monthly[202602] == pytest.approx(275.00, abs=0.001)
    assert sum(monthly.values()) == pytest.approx(525.00, abs=0.001)


def test_region_breakdown_reconciles(fact_sales, dim_customer):
    by_region = {
        r["customer_region"]: float(r["rev"])
        for r in (
            fact_sales.join(dim_customer, "customer_key")
            .groupBy("customer_region").agg(F.sum("sale_amount").alias("rev"))
            .collect()
        )
    }
    assert by_region["North"] == pytest.approx(525.00, abs=0.001)
    assert sum(by_region.values()) == pytest.approx(525.00, abs=0.001)


def test_customer_breakdown_reconciles(fact_sales, dim_customer):
    by_customer = {
        r["customer_name"]: float(r["rev"])
        for r in (
            fact_sales.join(dim_customer, "customer_key")
            .groupBy("customer_name").agg(F.sum("sale_amount").alias("rev"))
            .collect()
        )
    }
    assert by_customer["Aisha Khan"] == pytest.approx(450.00, abs=0.001)
    assert by_customer["Carla Jones"] == pytest.approx(75.00, abs=0.001)
    assert sum(by_customer.values()) == pytest.approx(525.00, abs=0.001)


def test_product_breakdown_reconciles(fact_sales, dim_product):
    by_product = {
        r["product_name"]: float(r["rev"])
        for r in (
            fact_sales.join(dim_product, "product_key")
            .groupBy("product_name").agg(F.sum("sale_amount").alias("rev"))
            .collect()
        )
    }
    assert by_product["Monitor"] == pytest.approx(400.00, abs=0.001)
    assert by_product["Keyboard"] == pytest.approx(75.00, abs=0.001)
    assert by_product["Wireless Mouse"] == pytest.approx(50.00, abs=0.001)
    assert sum(by_product.values()) == pytest.approx(525.00, abs=0.001)
```

### 14.5 Negative testing — prove the tests actually work

A test suite that has never failed is a test suite you cannot trust. Deliberately break things and confirm the right test catches each one.

| Deliberate break | Test that must fail | What it proves |
|---|---|---|
| Remove the de-duplication step | `test_recognised_revenue_total` (725.00) and `test_silver_orders_deduplicated` | Duplicate handling is genuinely tested |
| Remove the `Completed` filter | `test_recognised_revenue_total` and `test_expected_order_ids` | The business rule is enforced |
| Remove the orphan rejection | `test_fact_sales_has_no_orphans` and the revenue total (575.00) | Referential integrity is enforced |
| Change `amount` to `DoubleType` | `test_cast_types_produces_decimal_amount` | Type discipline is enforced |
| Swap `amount` for `quantity × unit_price` | `test_recognised_revenue_total` | The list-price trap is guarded |

> **Run this exercise once and keep the output.** "I deliberately broke my de-duplication and confirmed the build failed with the revenue at 725 instead of 525" is a genuinely strong interview answer.

### 14.6 Test execution

```bash
# Everything
python -m pytest tests/ -v

# Just the reconciliation tests (fastest meaningful signal)
python -m pytest tests/ -k reconcil -v

# With coverage
python -m pytest tests/ --cov=src --cov-report=term-missing
```

---

## 15. Completion checklist

### Data and pipeline

- [ ] All three CSVs present in `data/raw/` with the exact contents in Section 6
- [ ] `01_bronze_ingestion.py` lands 4 / 4 / **8** rows with explicit schemas
- [ ] Bronze preserves all defects (duplicate, null amount, orphan FK)
- [ ] `02`/`03` produce 4 clean, typed rows each
- [ ] `04_silver_orders.py` produces **7** rows via deterministic de-duplication
- [ ] Silver status counts are Completed 5, Cancelled 1, Pending 1
- [ ] `05_gold_dimensions.py` produces `dim_customer` 4, `dim_product` 4, `dim_date` 365
- [ ] `06_gold_fact_sales.py` produces **4** rows
- [ ] `SUM(fact_sales.sale_amount)` = **525.00**
- [ ] `fact_sales` order IDs are exactly 101, 102, 104, 107
- [ ] Zero orphan foreign keys; zero duplicate `order_id`
- [ ] Re-running the full pipeline is idempotent

### Quality

- [ ] `07_data_quality_checks.py` writes 6 results
- [ ] Schema is exactly `check_name, check_status, failed_record_count, processing_date`
- [ ] `orders_unique_order_id` FAIL, 1
- [ ] `orders_amount_not_null` FAIL, 1
- [ ] `orders_customer_fk_valid` FAIL, 1
- [ ] `orders_product_fk_valid` PASS, 0
- [ ] `orders_status_in_allowed_values` PASS, 0
- [ ] `fact_sales_revenue_reconciles` PASS, 0
- [ ] Quarantine holds exactly 2 rows (105, 106) with rejection reasons

### Reporting

- [ ] Monthly: 2026-01 = 250.00, 2026-02 = 275.00
- [ ] Region: North = 525.00, South = 0.00, West = 0.00
- [ ] Customer: Aisha Khan = 450.00, Carla Jones = 75.00
- [ ] Product: Monitor = 400.00, Keyboard = 75.00, Wireless Mouse = 50.00
- [ ] Status (raw): Completed 6, Cancelled 1, Pending 1
- [ ] Status (deduped): Completed 5, Cancelled 1, Pending 1
- [ ] All four breakdowns reconcile to 525.00

### Testing

- [ ] Unit tests cover every transformation function
- [ ] Integration tests cover every layer boundary
- [ ] Quality tests assert the expected pass/fail pattern
- [ ] Reconciliation tests assert all four breakdowns
- [ ] Negative testing done: each deliberate break caught by the right test
- [ ] `python -m pytest tests/ -v` passes cleanly

### Documentation

- [ ] `README.md` separates **built locally** from **designed for Azure**, unmistakably
- [ ] `docs/architecture.md` — layers, flow, technology reasoning, Azure mapping
- [ ] `docs/data_dictionary.md` — every column: type, nullability, description, example, classification
- [ ] `docs/transformation_rules.md` — every rule with an ID and sample effect
- [ ] `docs/data_quality_rules.md` — every check with severity and expected result
- [ ] `docs/runbook.md` — exact commands, verification steps, failure table, rollback
- [ ] `orchestration/pipeline_design.md` — DAG, dependencies, scheduling, failure handling
- [ ] Assumptions register complete with impact-if-false and verification method

### Engineering practice

- [ ] `config/dev.yml` and `config/prod.yml` drive all environmental differences
- [ ] No hard-coded paths or `if env == 'prod'` branches in `src/` or `notebooks/`
- [ ] **No secrets** in code, config, notebooks, logs, or git history (verified by scan)
- [ ] `.gitignore` excludes `data/bronze|silver|gold|quality_results/`, `.env`, credential patterns
- [ ] Structured logging with redaction; no PII or row contents logged
- [ ] CI workflow runs lint, tests, full pipeline, and the 525.00 assertion
- [ ] Branch protection requires a passing CI check and a review
- [ ] Repository tagged `v1.0.0` with a changelog

### Presentation

- [ ] CV bullets drafted, using "built" and "designed" correctly
- [ ] 60-second verbal summary rehearsed and timed
- [ ] All 21 interview questions in Section 13.3 answerable without notes
- [ ] You can state why revenue is 525.00 in under 15 seconds
- [ ] You can name all three defects and the affected order IDs instantly
- [ ] You can explain 8 → 7 → 4 without hesitating
- [ ] Known limitations list ready to volunteer unprompted
- [ ] Someone else has cloned the repo, run it, and reproduced 525.00

---

## 16. Next steps

Five directions, in the order that adds most value per hour invested.

### 16.1 Run it locally

**Effort: 2–3 hours · Value: essential**

Nothing else matters until this works. Clone, install, run all seven stages, and verify every acceptance criterion in Section 11.6.

```bash
cd customer-sales-lakehouse
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python notebooks/01_bronze_ingestion.py
# ... through 07
python -m pytest tests/ -v
```

- [ ] All seven stages complete without error
- [ ] Recognised revenue is 525.00
- [ ] All quality results match the expected table
- [ ] All tests pass
- [ ] A second full run produces identical output

### 16.2 Add more records

**Effort: 4–8 hours · Value: high**

Eight rows prove correctness but exercise nothing. Generate more — but do it **deliberately**, not randomly.

Keep the original eight rows as a **golden dataset** with its known answer of 525.00, and add generated rows in a separate dataset with its own independently computed expected total.

| Scale | Rows | What it reveals |
|---|---|---|
| Small | 1,000 | Partitioning starts to matter; multi-file writes appear |
| Medium | 1,000,000 | Shuffle costs become visible in the Spark UI |
| Large | 50,000,000 | Real tuning: file sizes, broadcast thresholds, skew |

Seed new defect types to extend the quality suite:

- A negative `amount`
- An `order_date` in the future
- A `status` value not in the allowed list
- A customer appearing twice in the customer master
- An order referencing a non-existent product

- [ ] Generator script written and parameterised by row count
- [ ] Golden 8-row dataset preserved and still asserting 525.00
- [ ] New expected totals computed independently (not by the pipeline under test)
- [ ] Runtime measured at each scale
- [ ] At least three new quality checks added for the new defect types

### 16.3 Convert Parquet to Delta in Databricks

**Effort: 6–10 hours · Value: high**

This is the single biggest capability upgrade. Follow Week 6 of `10_azure_data_engineer_study_plan.md`.

```python
# Convert silver and gold to Delta
orders_silver.write.format("delta").mode("overwrite").save("data/delta/silver_orders")
fact_sales.write.format("delta").mode("overwrite").save("data/delta/gold_fact_sales")
```

What you gain:

| Capability | Why it matters here |
|---|---|
| ACID transactions | A failed write no longer leaves a half-written table |
| `MERGE INTO` | Proper upserts for incremental loads |
| Time travel | "What did revenue look like before last Tuesday's change?" |
| `RESTORE` | The rollback step in `docs/runbook.md` becomes a one-liner |
| Schema enforcement | Bad writes rejected at the boundary |
| `CHECK` constraints | `sale_amount >= 0` enforced by the table itself |
| `OPTIMIZE` | Fixes small files as the dataset grows |

- [ ] Silver and gold converted to Delta
- [ ] `DESCRIBE HISTORY` shows at least four versions
- [ ] A `MERGE`-based incremental load implemented
- [ ] Time travel demonstrated, with revenue verified at an earlier version
- [ ] A `RESTORE` performed and 525.00 confirmed after rollback
- [ ] `docs/architecture.md` updated to explain why Delta over Parquet

### 16.4 Orchestrate using an approved workflow tool

**Effort: 6–12 hours · Value: high**

Manual sequencing is the biggest remaining gap. Use whatever orchestrator your organisation or learning environment permits — Databricks Workflows, Azure Data Factory, Airflow, or another approved tool.

> ⚠️ Use only tools approved in your environment. Do not install unapproved software on a work machine, and do not connect a learning project to any production or company system.

Implement the DAG from Section 12.4:

- [ ] Source file validation runs first and fails fast
- [ ] Bronze runs before silver
- [ ] The three silver stages run in **parallel**
- [ ] Gold dimensions wait for all three silver stages
- [ ] Gold fact waits for gold dimensions
- [ ] Quality checks run last
- [ ] A critical check failure **aborts** and prevents publication
- [ ] Retries and timeouts configured per task
- [ ] Failure alerting configured and tested
- [ ] A schedule defined (then disabled, if cost is a concern)
- [ ] Re-run-from-failure verified

### 16.5 Add CI checks

**Effort: 3–6 hours · Value: high**

Make correctness automatic. See Week 12 of `10_azure_data_engineer_study_plan.md` for a full workflow example.

- [ ] CI runs on every push and pull request
- [ ] Linting and formatting enforced
- [ ] Unit tests run
- [ ] **The full pipeline runs** and the 525.00 reconciliation is asserted
- [ ] Secret scanning runs on every commit
- [ ] Pre-commit hooks installed locally
- [ ] Branch protection requires a green CI run and a review
- [ ] A deliberate break confirmed to fail the build
- [ ] A CI status badge added to the README

### 16.6 Beyond these five

| Direction | Builds on | Why consider it |
|---|---|---|
| **Type 2 dimensions** | 16.3 (needs MERGE) | Historical attribute tracking is a common interview topic |
| **Streaming ingestion** | 16.3 | Most frequently missing skill in batch-only portfolios |
| **dbt for the gold layer** | 16.5 | Revisit `08_dbt_orchestration_monitoring.md` with real stakes |
| **Infrastructure as code** | 16.4 | Turns the Section 12 design into something deployable |
| **Data contracts** | 16.5 | Schema validation at the producer boundary; increasingly expected |
| **A BI dashboard** | 16.1 | Closes the loop from raw CSV to a visual answer |

### 16.7 Prioritisation

If you have limited time, do them in this order:

1. **Run it locally** (16.1) — without this, nothing else counts.
2. **Add CI checks** (16.5) — cheapest route to demonstrating engineering rigour.
3. **Convert to Delta** (16.3) — biggest single capability jump.
4. **Orchestrate** (16.4) — closes the most visible gap in the current build.
5. **Add more records** (16.2) — valuable, but only once correctness is locked in.

---

## Key facts card

Memorise. These must be instant.

| Fact | Value |
|---|---|
| Raw order rows | **8** |
| Rows after de-duplication | **7** |
| Rows in `fact_sales` | **4** |
| **Recognised revenue** | **525.00** |
| Order IDs in `fact_sales` | **101, 102, 104, 107** |
| 2026-01 revenue | **250.00** (101 + 102) |
| 2026-02 revenue | **275.00** (104 + 107) |
| Region revenue | **North 525.00**, South 0.00, West 0.00 |
| Aisha Khan | **450.00** (orders 101, 102, 107) |
| Carla Jones | **75.00** (order 104) |
| Monitor (1003) | **400.00** |
| Keyboard (1004) | **75.00** |
| Wireless Mouse (1002) | **50.00** |
| Laptop Bag (1001) | **0.00** |
| Raw status counts | Completed **6**, Cancelled **1**, Pending **1** |
| Deduped status counts | Completed **5**, Cancelled **1**, Pending **1** |
| Duplicate order IDs | **1** (order 107) |
| Missing amounts | **1** (order 105) |
| Invalid customer refs | **1** (order 106 → customer 99) |
| Invalid product refs | **0** |
| Invalid statuses | **0** |
| Why 103 excluded | **Cancelled** |
| Why 105 excluded | **Pending + missing amount** |
| Why 106 excluded | **customer_id 99 does not exist** |
| Local build | ✅ **BUILT** — PySpark + Parquet |
| Azure | 📐 **DESIGNED ONLY** — not deployed |

---

*Last reviewed: 2026-10-08 · Part of the data-engineering-learning series*
*Project: [`../customer-sales-lakehouse`](../customer-sales-lakehouse) · See also `09_governance_security_delivery.md` and `10_azure_data_engineer_study_plan.md`*
