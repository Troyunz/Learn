# Data Engineering Learning

A structured, self-paced learning repository for becoming a job-ready data engineer.

This repository is **not** a collection of random notes. It is a deliberately sequenced
curriculum: twelve deep-dive guides that build on each other, paired with one realistic
hands-on project that you implement, break, fix, test, and document.

Everything here is **fictional sample data** and **local-first**. Nothing in this repository
requires a paid cloud subscription, and nothing here contains credentials, tokens, client
data, or company-confidential information.

---

## Who this is for

- You can write a little SQL or Python, but you have never built a data pipeline end to end.
- You want to move into a data engineering role and need both the theory and a portfolio piece.
- You learn best by reading a concept, then immediately applying it to a project you own.

If you have never written a `SELECT` statement, start at guide `01` and go slowly. If you
already write SQL at work, skim `01`, then start properly at `02`.

---

## What's in here

### The twelve deep-dive guides

| # | Guide | What you learn | Phase |
|---|-------|----------------|-------|
| 00 | [00_data_engineering_roadmap.md](./00_data_engineering_roadmap.md) | What data engineering actually is, and a 9-month roadmap | Orientation |
| 01 | [01_sql_deep_dive.md](./01_sql_deep_dive.md) | Relational basics through window functions and reconciliation | Foundations |
| 02 | [02_data_modelling_deep_dive.md](./02_data_modelling_deep_dive.md) | Normalization, star schemas, grain, surrogate keys, SCD 1 & 2 | Foundations |
| 03 | [03_linux_git_deep_dive.md](./03_linux_git_deep_dive.md) | Terminal fluency, permissions, and a professional Git workflow | Foundations |
| 04 | [04_etl_elt_and_quality.md](./04_etl_elt_and_quality.md) | ETL vs ELT, medallion layers, incremental loads, quality dimensions | Core engineering |
| 05 | [05_pyspark_deep_dive.md](./05_pyspark_deep_dive.md) | Spark architecture and the PySpark API you use every day | Core engineering |
| 06 | [06_azure_data_engineering.md](./06_azure_data_engineering.md) | ADLS Gen2, Data Factory, Synapse concepts, security, governance | Cloud |
| 07 | [07_databricks_delta_lake.md](./07_databricks_delta_lake.md) | Databricks workflows, Delta Lake, ACID, MERGE, time travel | Cloud |
| 08 | [08_dbt_orchestration_monitoring.md](./08_dbt_orchestration_monitoring.md) | dbt, DAGs, scheduling, retries, alerting, audit tables, CI/CD | Delivery |
| 09 | [09_governance_security_delivery.md](./09_governance_security_delivery.md) | Governance, least privilege, secrets, documentation, stakeholders | Delivery |
| 10 | [10_azure_data_engineer_study_plan.md](./10_azure_data_engineer_study_plan.md) | A 12-week Azure-focused plan with weekly deliverables | Consolidation |
| 11 | [11_portfolio_project_blueprint.md](./11_portfolio_project_blueprint.md) | The full blueprint for the portfolio project | Consolidation |

### The hands-on project

The companion project is **`customer-sales-lakehouse`**, located as a sibling folder:

```
Learn/
├── data-engineering-learning/      <- you are here (the guides)
└── customer-sales-lakehouse/       <- the hands-on project
```

So from inside this repository, the project is at **`../customer-sales-lakehouse`**.

It is a local medallion lakehouse built with PySpark:

```
raw CSV  ->  bronze  ->  silver  ->  gold  ->  reporting queries
```

| Folder | Purpose |
|--------|---------|
| `data/raw/` | Three fictional CSVs: customers, products, orders |
| `data/bronze/` | Raw data landed as Parquet with ingestion metadata |
| `data/silver/` | Typed, standardised, de-duplicated, quality-flagged data |
| `data/gold/` | Star schema: `dim_customer`, `dim_product`, `dim_date`, `fact_sales` |
| `data/quality_results/` | Output of the data-quality checks |
| `src/` | Reusable modules: config, ingestion, transformations, quality checks, logging |
| `notebooks/` | Seven runnable scripts, one per pipeline stage |
| `sql/` | Exploration, quality, reporting queries, and the star-schema DDL |
| `docs/` | Architecture, data dictionary, transformation rules, quality rules, runbook |
| `tests/` | Pytest suites for transformations and quality checks |
| `config/` | `dev.yml` and `prod.yml` placeholder environment config |
| `orchestration/` | Pipeline design and a conceptual ADF / Databricks Workflows mapping |

---

## How the guides and the project fit together

The guides and the project are two halves of the same thing. Each guide teaches a concept
using **the exact same fictional dataset** that the project runs on, so there is never a
translation gap between "the tutorial example" and "my project".

| Guide | What you then build or change in `../customer-sales-lakehouse` |
|-------|-----------------------------------------------------------------|
| 01 SQL | Run and extend `sql/01_exploration_queries.sql` and `sql/02_data_quality_checks.sql` |
| 02 Modelling | Understand and critique `sql/04_star_schema_ddl.sql` and the gold layer |
| 03 Linux & Git | Put the project under version control; branch, commit, open a PR |
| 04 ETL & quality | Understand why bronze/silver/gold exist; extend `src/quality_checks.py` |
| 05 PySpark | Read every line of `src/transformations.py` and the `notebooks/` scripts |
| 06 Azure | Redesign the local folders as ADLS Gen2 containers and ADF pipelines |
| 07 Databricks & Delta | Convert the Parquet writes to Delta; add an idempotent `MERGE` |
| 08 dbt & orchestration | Turn `orchestration/pipeline_design.md` into a real DAG; add audit tables |
| 09 Governance | Review `docs/` and write the missing assumptions and runbook detail |
| 10 Study plan | Weekly deliverables, most of which land in this project |
| 11 Blueprint | The complete specification and completion checklist for the project |

### The one number to remember

The sample `orders.csv` contains **deliberate data-quality defects**. After de-duplication
and after excluding invalid, pending, and cancelled orders, the **recognised revenue is
exactly `525.00`**.

| Order | Amount | Included? | Why |
|-------|--------|-----------|-----|
| 101 | 200.00 | Yes | Valid, Completed |
| 102 | 50.00 | Yes | Valid, Completed |
| 103 | 50.00 | No | Cancelled |
| 104 | 75.00 | Yes | Valid, Completed |
| 105 | *(missing)* | No | Pending, and `amount` is null |
| 106 | 50.00 | No | `customer_id` 99 does not exist |
| 107 | 200.00 | Yes | Valid, Completed — counted **once** after de-duplication |
| **Total** | **525.00** | | |

The expected quality findings on the sample data are:

- 1 duplicate order ID
- 1 missing mandatory amount
- 1 invalid customer reference
- 0 invalid product references
- 0 invalid statuses

If your pipeline produces anything other than `525.00` and those five findings, something
in your implementation is wrong. That is the point — it is a self-checking exercise.

---

## Recommended study sequence

Work through this in order. Resist the urge to jump straight to Spark and the cloud; the
people who get stuck are almost always the ones who skipped modelling and SQL.

**Phase 1 — Foundations (months 1–2)**
1. Read `00_data_engineering_roadmap.md` end to end. Decide your weekly hours.
2. Work `01_sql_deep_dive.md`. Do all 15+ exercises by hand before checking anything.
3. Work `02_data_modelling_deep_dive.md`. Draw the star schema on paper.
4. Work `03_linux_git_deep_dive.md`. Put the project in Git and practise branching.

**Phase 2 — Core engineering (months 3–4)**
5. Work `04_etl_elt_and_quality.md`. Map each concept onto a folder in the project.
6. Work `05_pyspark_deep_dive.md`. Run the project pipeline locally, stage by stage.
7. Break the pipeline on purpose (add a bad row), watch the quality checks catch it, fix it.

**Phase 3 — Cloud (months 5–6)**
8. Work `06_azure_data_engineering.md`. Sketch the Azure architecture for the project.
9. Work `07_databricks_delta_lake.md`. Convert the gold layer to Delta.

**Phase 4 — Delivery (months 7–8)**
10. Work `08_dbt_orchestration_monitoring.md`. Add a DAG, retries, and an audit table.
11. Work `09_governance_security_delivery.md`. Complete the project's `docs/`.

**Phase 5 — Consolidation (month 9)**
12. Follow `10_azure_data_engineer_study_plan.md` as a 12-week sprint.
13. Finish every item in `11_portfolio_project_blueprint.md`'s completion checklist.

---

## How to use this repository

### 1. Set up a virtual environment

```bash
cd data-engineering-learning
python -m venv .venv
```

```bash
# Windows PowerShell
.\.venv\Scripts\Activate.ps1

# macOS / Linux / Git Bash
source .venv/bin/activate
```

```bash
pip install -r requirements.txt
```

> **PySpark note:** PySpark needs a working Java runtime (Java 11 or 17 are the safest
> choices). Local Spark setup varies a lot by machine, especially on Windows. If `pyspark`
> fails to start, fix Java first — it is almost never a problem with your code.

### 2. Read actively, not passively

- Keep the relevant guide open on one side and your terminal on the other.
- Type every example out. Do not copy and paste — you learn the API by typing it.
- When a guide shows an expected result table, predict the answer *before* you run it.

### 3. Do the exercises without answers

Several guides end with exercises that deliberately have **no answer key**. Verify your
own work by cross-checking against the sample data and the `525.00` rule. Being able to
prove your own result is correct is the actual skill being trained.

### 4. Run the project after every major guide

```bash
cd ../customer-sales-lakehouse
python notebooks/01_bronze_ingestion.py
```

Each guide should change how you read that code. If re-reading
`src/transformations.py` after guide `05` feels exactly the same as before, slow down.

### 5. Write things down

Treat `../customer-sales-lakehouse/docs/` as your lab notebook. Every assumption you make,
every rule you invent, every bug you hit — document it. Documentation is a graded part of
the job, not an afterthought.

---

## Repository layout

```
data-engineering-learning/
├── README.md
├── requirements.txt
├── .gitignore
├── 00_data_engineering_roadmap.md
├── 01_sql_deep_dive.md
├── 02_data_modelling_deep_dive.md
├── 03_linux_git_deep_dive.md
├── 04_etl_elt_and_quality.md
├── 05_pyspark_deep_dive.md
├── 06_azure_data_engineering.md
├── 07_databricks_delta_lake.md
├── 08_dbt_orchestration_monitoring.md
├── 09_governance_security_delivery.md
├── 10_azure_data_engineer_study_plan.md
└── 11_portfolio_project_blueprint.md
```

The hands-on project lives alongside it at `../customer-sales-lakehouse`.

---

## Ground rules

- **No real data.** Every record in this repository is invented.
- **No secrets.** Never commit credentials, tokens, passwords, or connection strings.
  Use `config/dev.yml` placeholders and environment variables.
- **No client or company-confidential material.** Keep examples generic.
- **Cloud services change.** Service names, portal layouts, and certification catalogues
  move. Always confirm against current official documentation before relying on a detail.

---

## Next Steps

Once you have worked through the guides and the pipeline runs clean:

- **Run locally.** Execute the full pipeline in order and confirm recognised revenue is
  `525.00` and that the five expected quality findings appear.
- **Add more records.** Extend the raw CSVs with new customers, products, and orders —
  including new edge cases — and confirm the quality checks and totals still behave.
- **Convert Parquet to Delta in Databricks.** Swap the writes to Delta, add schema
  enforcement, and implement an idempotent `MERGE` into silver orders keyed on `order_id`.
- **Orchestrate using an approved workflow tool.** Turn the notebook sequence into a real
  DAG in Azure Data Factory, Databricks Workflows, or Airflow, with dependencies, retries,
  and alerting.
- **Add CI checks.** Wire up a pipeline that installs dependencies, runs `pytest`, and
  fails the build when a data-quality check regresses.
