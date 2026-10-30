# 10 — Azure Data Engineer: 12-Week Study Plan

> **Where this sits in the series**
>
> Guides `00`–`09` gave you the foundations: the roadmap, SQL, data modelling, Linux/Git, ETL/ELT & quality, PySpark, Azure basics, Databricks/Delta, dbt/orchestration, and governance.
>
> This guide turns that knowledge into a **schedule**. Twelve weeks, 8–12 hours a week, every week producing a tangible deliverable you can show someone.
>
> Companion project: [`../customer-sales-lakehouse`](../customer-sales-lakehouse)

---

> ## ⚠️ Important note on certifications — read before you plan
>
> **Microsoft's certification catalogue changes over time.** Exams are renamed, retired, consolidated, replaced, and have their skills-measured documents revised — sometimes several times a year.
>
> **This guide therefore does not name a target exam and does not claim that any particular exam code is currently available.**
>
> Before you commit time or money to a specific certification:
>
> - [ ] Check the **current official Microsoft certification catalogue** for the data-engineering role path.
> - [ ] Read the **current official "skills measured" document** for whatever exam you select — this is the only authoritative syllabus.
> - [ ] Check the **retirement/renewal notices** for that exam.
> - [ ] Check the current **exam format, cost, language availability, and renewal policy** in your region.
> - [ ] Re-check at the start of your study and again before booking.
>
> **The plan below is deliberately built around durable, transferable skills** — storage, pipelines, Spark, Delta, modelling, quality, security, orchestration, CI/CD — rather than around any one exam's objective list. Those skills are what interviews test and what the job actually requires. A certification, if you choose to pursue one, should sit on top of this, not replace it.
>
> Treat any exam-specific content as something you add in **Weeks 11–12**, once you have confirmed the current catalogue yourself.

---

## Table of contents

- [How to use this plan](#how-to-use-this-plan)
- [Weekly time budget](#weekly-time-budget)
- [The 12-week map](#the-12-week-map)
- [Week 1 — Cloud and Azure fundamentals](#week-1--cloud-and-azure-fundamentals)
- [Week 2 — ADLS Gen2 and the storage layer](#week-2--adls-gen2-and-the-storage-layer)
- [Week 3 — Azure Data Factory fundamentals](#week-3--azure-data-factory-fundamentals)
- [Week 4 — PySpark for data engineering](#week-4--pyspark-for-data-engineering)
- [Week 5 — Databricks workspace and workflows](#week-5--databricks-workspace-and-workflows)
- [Week 6 — Delta Lake](#week-6--delta-lake)
- [Week 7 — Incremental loads and change handling](#week-7--incremental-loads-and-change-handling)
- [Week 8 — Dimensional modelling and the gold layer](#week-8--dimensional-modelling-and-the-gold-layer)
- [Week 9 — Synapse concepts and the serving layer](#week-9--synapse-concepts-and-the-serving-layer)
- [Week 10 — Data quality engineering](#week-10--data-quality-engineering)
- [Week 11 — Security, governance and monitoring](#week-11--security-governance-and-monitoring)
- [Week 12 — Git, CI/CD and capstone delivery](#week-12--git-cicd-and-capstone-delivery)
- [Progress tracking checklist](#progress-tracking-checklist)
- [After week 12](#after-week-12)

---

## How to use this plan

### The rules that make it work

1. **Every week produces a deliverable.** Not notes — an artefact. A file, a diagram, a working pipeline, a document in the repo. Reading without building produces the illusion of competence.
2. **Everything ties back to one project.** `../customer-sales-lakehouse` is the thread. By week 12 you have one deep project rather than twelve shallow tutorials. Interviewers strongly prefer the former.
3. **Self-check questions have no answers here.** That's deliberate. If you can answer them out loud, in your own words, without looking, you know it. If you can't, you have found this week's gap. Record your answers in a file and revisit at week 12.
4. **Protect the cloud budget.** Use free tiers, the smallest SKUs, and **delete resources the moment you finish**. Most of this plan runs locally with PySpark; cloud time is for the bits that genuinely require it.
5. **Cross-reference the guides.** Each week points at the relevant guide in this repo. Re-read, don't re-learn.
6. **Missing a week is fine; abandoning the plan is not.** Slide the schedule. Consistency beats intensity.

### Cost discipline checklist

- [ ] Set a **budget alert** on your subscription before creating anything
- [ ] Prefer free tiers and the smallest available SKUs
- [ ] Use **serverless/on-demand** options where available rather than always-on
- [ ] Set **auto-termination** on any compute cluster (10–20 minutes idle)
- [ ] Create resources in a **single resource group** so you can delete everything at once
- [ ] Tag everything with `purpose=learning` and a creation date
- [ ] **Delete the resource group** at the end of every session that doesn't need persistence
- [ ] Review spend weekly — put it in your calendar
- [ ] Never leave a cluster running overnight

### Prerequisites

| You should already be comfortable with | Guide |
|---|---|
| SQL: joins, aggregation, window functions, CTEs | `01_sql_deep_dive.md` |
| Dimensional modelling: facts, dimensions, grain, SCDs | data modelling guide (`02`) |
| Shell basics and Git: branch, commit, PR, rebase | Linux/Git guide (`03`) |
| ETL vs ELT, medallion architecture, data quality dimensions | `04_etl_elt_and_quality.md` |
| Python 3.11: functions, type hints, dataclasses, pytest | `00_data_engineering_roadmap.md` |
| PySpark DataFrame API basics | `05_pyspark_deep_dive.md` |

If two or more of these are shaky, spend a pre-week shoring them up. The plan assumes them.

---

## Weekly time budget

Two variants. Pick the one that matches your life, and **schedule the blocks in your calendar as appointments**. Unscheduled study does not happen.

### Lighter variant — 8 hours/week

For people with demanding jobs or caring responsibilities.

| Day | Block | Hours | Typical use |
|---|---|---|---|
| Monday | Evening | 1.0 | Read / watch — learning topics |
| Tuesday | Evening | 1.0 | Read / watch — learning topics |
| Wednesday | Evening | 1.0 | Hands-on, part 1 |
| Thursday | Evening | 1.0 | Hands-on, part 2 |
| Friday | — | 0.0 | **Rest** — protected |
| Saturday | Morning | 3.0 | Deep work: the week's deliverable |
| Sunday | Morning | 1.0 | Self-check questions + write-up + commit |
| **Total** | | **8.0** | |

### Heavier variant — 12 hours/week

For people between roles, studying full-time alongside, or with more capacity.

| Day | Block | Hours | Typical use |
|---|---|---|---|
| Monday | Evening | 1.5 | Read / watch — learning topics |
| Tuesday | Evening | 1.5 | Read / watch + note-taking |
| Wednesday | Evening | 1.5 | Hands-on, part 1 |
| Thursday | Evening | 1.5 | Hands-on, part 2 |
| Friday | Evening | 1.0 | Review, debug, tidy, push to Git |
| Saturday | Morning + afternoon | 4.0 | Deep work: the week's deliverable |
| Sunday | Morning | 1.0 | Self-check questions + write-up + retro |
| **Total** | | **12.0** | |

### How to split any week's hours

| Activity | Share | 8h week | 12h week |
|---|---|---|---|
| Learning (reading, video, docs) | 30% | 2.4h | 3.6h |
| Hands-on building | 45% | 3.6h | 5.4h |
| Deliverable write-up and documentation | 15% | 1.2h | 1.8h |
| Self-check and review | 10% | 0.8h | 1.2h |

> **If you cut anything, do not cut the hands-on or the write-up.** Reading is the cheapest activity and the least effective. Building and explaining are where learning actually happens.

### Session structure that works

```text
0:00–0:05   Review last session's notes. What did you leave unfinished?
0:05–0:15   State today's goal in one sentence. Write it down.
0:15–0:50   Focused work. No notifications.
0:50–1:00   Break.
1:00–1:40   Focused work.
1:40–1:50   Write down: what worked, what broke, what's next.
1:50–2:00   Commit to Git with a meaningful message.
```

Finishing every session with a commit means you always have a visible record of progress — and twelve weeks of green commit history is itself a portfolio signal.

---

## The 12-week map

```text
FOUNDATIONS          BUILD                       MODEL & TRUST          DELIVER
├─ W1  Azure         ├─ W4  PySpark             ├─ W8  Modelling       ├─ W11 Security,
│      fundamentals  │                          │                      │      governance,
├─ W2  ADLS Gen2     ├─ W5  Databricks          ├─ W9  Synapse /       │      monitoring
├─ W3  ADF           ├─ W6  Delta Lake          │      serving         ├─ W12 Git, CI/CD,
                     ├─ W7  Incremental loads   ├─ W10 Data quality    │      capstone
```

| Week | Theme | Primary guide | Cloud needed? |
|---|---|---|---|
| 1 | Cloud & Azure fundamentals | `06` Azure | Portal only (free) |
| 2 | ADLS Gen2 & storage | `06` Azure | Yes — minimal |
| 3 | Azure Data Factory | `06`, `08` | Yes — minimal |
| 4 | PySpark | `05_pyspark_deep_dive.md` | No — local |
| 5 | Databricks | `07` Databricks/Delta | Free/community tier |
| 6 | Delta Lake | `07` Databricks/Delta | Free/community tier |
| 7 | Incremental loads | `04`, `07` | Local + optional cloud |
| 8 | Dimensional modelling | `02` modelling, `01` SQL | No — local |
| 9 | Synapse & serving | `06`, `01` SQL | Optional, serverless only |
| 10 | Data quality | `04_etl_elt_and_quality.md` | No — local |
| 11 | Security, governance, monitoring | `09_governance_security_delivery.md` | Portal reading |
| 12 | Git, CI/CD, capstone | `08_dbt_orchestration_monitoring.md`, `11_portfolio_project_blueprint.md` | No |

---

## Week 1 — Cloud and Azure fundamentals

**Goal:** understand what the cloud actually gives you, and be able to navigate Azure confidently without fear of accidental spend.

**Cross-reference:** `06` (Azure guide), `00_data_engineering_roadmap.md`

### Learning topics

- Cloud service models: IaaS vs PaaS vs SaaS, and where data services sit on that spectrum.
- The **shared responsibility model** — what the cloud provider secures and what remains yours.
- Azure's resource hierarchy: tenant → management group → subscription → resource group → resource. Why resource groups are your unit of lifecycle and deletion.
- Regions, availability zones, paired regions, and why **data residency** is a governance concern, not just a latency one.
- Azure Resource Manager (ARM) and declarative deployment: the idea that infrastructure is described, not clicked.
- Identity: Entra ID (the directory), users, groups, service principals, **managed identities**. Map these back to Section 8 of `09_governance_security_delivery.md`.
- RBAC in Azure: role definitions, role assignments, scope inheritance. Built-in roles (Reader, Contributor, Owner) and why Owner is rarely the right answer.
- Pricing models: pay-as-you-go, reserved, spot, and **consumption vs provisioned** — the single biggest driver of a surprise bill.
- Cost management: budgets, alerts, cost analysis, tagging strategy.
- The Azure data services landscape at a glance: storage, orchestration, compute, warehousing, governance, reporting. You are not learning them all this week — you are learning the map.
- Naming conventions: why `rg-salesdata-dev-uksouth` beats `myresourcegroup2`.

### Hands-on activity

1. Create a free Azure account (or use an existing subscription) and **immediately** set a budget alert at a low threshold.
2. Create a resource group named with a clear convention, e.g. `rg-<project>-learn-dev`. Tag it `purpose=learning`, `owner=<your-role>`, `created=<date>`.
3. Explore the portal: find Cost Management, find Entra ID, find the Activity Log. Locate where RBAC assignments are shown for your resource group.
4. Create and delete a trivial resource (a storage account) purely to watch the Activity Log record both events. This is your first encounter with an audit trail.
5. Write a naming convention for the `customer-sales-lakehouse` Azure design: resource group, storage account, data factory, Databricks workspace, key vault. Use placeholders like `<storage-account-name>`.
6. Map each local folder in `../customer-sales-lakehouse/data/` to the Azure service that would host it in production, and write down why.
7. Delete the resource group at the end of the session. Confirm it's gone in Cost Management.

### Deliverable

A new file in your own notes (not in the project repo unless you want it there): **`azure_foundations.md`**, containing:

- A diagram of the Azure resource hierarchy as you understand it.
- A naming convention table for all resources the `customer-sales-lakehouse` Azure design would need.
- A local-to-Azure mapping table:

| Local artefact | Azure service (design) | Why |
|---|---|---|
| `data/raw/*.csv` | ADLS Gen2 — `raw` container | Cheap, hierarchical, supports fine-grained ACLs |
| `data/bronze/` | ADLS Gen2 — `bronze` container | Immutable landing zone, lifecycle rules |
| `notebooks/*.py` | Databricks notebooks/jobs | Managed Spark |
| Pipeline sequencing | Azure Data Factory | Orchestration, scheduling, monitoring |
| `config/*.yml` secrets | Key Vault `<key-vault-name>` | Secrets never in config files |
| `sql/03_reporting_queries.sql` | Serverless SQL / warehouse | Ad-hoc analytical querying |

- A cost-control checklist you will actually follow.

### Self-check questions

1. What is the difference between IaaS, PaaS and SaaS, and which model does a managed Spark service fall into?
2. Under the shared responsibility model, who is responsible for classifying your data correctly, and who is responsible for patching the storage service?
3. Why is the resource group the natural unit for a learning project, and what happens when you delete one?
4. What is the practical difference between a service principal and a managed identity, and why is the latter preferred?
5. If you assign the Contributor role at subscription scope, what can that identity do to a resource group created later? Why is that a problem?
6. Name three ways a learning subscription can generate unexpected cost, and the control that prevents each.
7. Why does region choice matter for a dataset containing `customer_name`?
8. What information does the Activity Log capture that your application logs do not?

---

## Week 2 — ADLS Gen2 and the storage layer

**Goal:** understand cloud data lake storage deeply enough to design the physical layout of a medallion lakehouse.

**Cross-reference:** `06` (Azure guide), `04_etl_elt_and_quality.md`

### Learning topics

- Blob storage vs **ADLS Gen2**: what the hierarchical namespace adds (real directories, atomic directory rename, POSIX-style ACLs) and why that matters enormously for Spark.
- Storage account anatomy: account → container → directory → blob. The `abfss://container@<storage-account-name>.dfs.core.windows.net/path` URI format.
- Access tiers: hot, cool, cold, archive. Rehydration latency and why archive is not a "cheap hot".
- **Lifecycle management policies** — expressing the retention schedule from `09_governance_security_delivery.md` Section 7 as storage rules.
- Redundancy: LRS, ZRS, GRS, RA-GRS. What each protects against and what each costs.
- Security: RBAC at the data plane (Storage Blob Data Reader/Contributor) vs **ACLs** on directories; shared keys and SAS tokens and why both are last resorts; private endpoints and firewall rules conceptually.
- Soft delete, versioning, and immutable (WORM) policies — the controls that make bronze genuinely a system of record.
- File formats revisited: CSV vs Parquet vs Delta. Columnar storage, predicate pushdown, column pruning, compression codecs (snappy vs zstd vs gzip).
- **The small-file problem**: why thousands of tiny files destroy Spark performance, and the target file size rule of thumb (roughly 128 MB–1 GB).
- Partitioning strategy: partition by a column you filter on, with reasonable cardinality. Why partitioning our 8-row sample by `order_date` would be actively harmful.
- Folder conventions for medallion: `/<layer>/<domain>/<entity>/<partition>/`.

### Hands-on activity

1. Create a storage account with the hierarchical namespace **enabled**. Create containers: `raw`, `bronze`, `silver`, `gold`, `ops`.
2. Upload the three CSVs from `../customer-sales-lakehouse/data/raw/` into `raw/sales/`.
3. Set a lifecycle policy: move `bronze` blobs to cool after 30 days, delete `raw` blobs after 90 days. Match the retention schedule in guide `09`.
4. Enable soft delete and blob versioning on the account. Delete a file and restore it. Note how long restoration took.
5. Assign yourself **Storage Blob Data Reader** on the `gold` container only. Try to write. Observe the 403 and connect it to authN vs authZ.
6. Locally, write the same orders data as CSV and as Parquet with `../customer-sales-lakehouse` and compare file sizes:

```python
from pyspark.sql import SparkSession

spark = (
    SparkSession.builder
    .appName("week2-format-comparison")
    .master("local[*]")
    .config("spark.sql.shuffle.partitions", "4")
    .getOrCreate()
)

orders = spark.read.option("header", True).csv("data/raw/orders.csv")
orders.write.mode("overwrite").csv("data/scratch/orders_csv")
orders.write.mode("overwrite").parquet("data/scratch/orders_parquet")

# Then compare directory sizes on disk and reason about why they differ.
print(f"orders rows: {orders.count()}")   # 8 rows in the raw file
```

7. Write the **physical layout design** for the Azure deployment of `customer-sales-lakehouse`, including container names, directory structure, partitioning decisions, and access tier per layer.
8. Delete or empty the storage account when finished.

### Deliverable

**`storage_design.md`** containing:

- A container and directory layout diagram for all four medallion layers.
- A table: layer → format → partitioning → access tier → retention → classification → who can write.

| Layer | Format | Partitioned by | Tier | Retention | Classification | Writer |
|---|---|---|---|---|---|---|
| raw | CSV | none (small) | Hot → delete | 90 days | varies by file | ingestion only |
| bronze | Parquet | `ingestion_date` | Hot → Cool at 30d | 7 years | inherits source | pipeline identity |
| silver | Parquet | `order_date` (when volume justifies) | Hot | 2 years | Internal/Confidential | pipeline identity |
| gold | Parquet/Delta | `date_key` year | Hot | 7 years | Internal | pipeline identity |
| ops (quality_results) | Parquet | `processing_date` | Hot | 13 months | Internal | pipeline identity |

- A short written justification of why you would **not** partition the 8-row sample dataset.
- Measured CSV vs Parquet size comparison with your own numbers.

### Self-check questions

1. What exactly does the hierarchical namespace change, and why does it matter for a Spark job writing thousands of files?
2. When would you use an ACL on a directory instead of an RBAC role assignment on a container?
3. Explain the small-file problem to a non-engineer. What causes it in a Spark pipeline, and how do you fix it?
4. Our orders sample has 8 rows. What would happen if you partitioned it by `order_date`, and why is that bad?
5. What is the difference between soft delete, versioning, and an immutability policy, and which one makes bronze a trustworthy system of record?
6. Why does Parquet compress better than CSV for this dataset, even though the dataset is tiny?
7. A dataset must be kept for 7 years but is rarely read after 90 days. Describe the lifecycle policy you would write.
8. Why is a SAS token more dangerous than a managed identity, and when is it nonetheless the right tool?

---

## Week 3 — Azure Data Factory fundamentals

**Goal:** be able to design and reason about orchestration — the layer that decides *when* and *in what order* things run.

**Cross-reference:** `06` (Azure guide), `08_dbt_orchestration_monitoring.md`

### Learning topics

- What orchestration is and is not. ADF **moves and coordinates**; it is not where heavy transformation logic belongs.
- Core objects: **linked service** (connection), **dataset** (shape and location), **activity** (unit of work), **pipeline** (ordered activities), **trigger** (when it runs), **integration runtime** (where it executes).
- Activity types you'll actually use: Copy, Lookup, Get Metadata, ForEach, If Condition, Switch, Until, Set/Append Variable, Execute Pipeline, Databricks Notebook, Stored Procedure, Web, Fail, Wait.
- Control flow: sequential vs parallel ForEach, batch count, dependency conditions (Success, Failure, Completion, Skipped). Designing a graph, not a script.
- Parameters vs variables vs expressions. Pipeline parameters, dataset parameters, global parameters, and the expression language (`@pipeline().parameters.x`, `@utcnow()`, `@activity('X').output`).
- Triggers: schedule, tumbling window (with backfill and dependency), event-based (blob created), manual. Why tumbling window is the right choice for daily incremental loads.
- **Idempotency and reruns** — why every pipeline must be safely re-runnable, and how that connects to the runbook in guide `09`.
- Error handling: retry policies, timeouts, failure paths, and the critical difference between a pipeline that *fails loudly* and one that *silently publishes bad data*.
- Monitoring: run history, activity-level inputs/outputs, rerun from failed activity, alerts.
- Mapping Data Flows vs calling Databricks: when visual transformation is appropriate and when code wins (versioning, testing, reviewability).
- Integration runtimes: Azure IR, self-hosted IR (for on-prem sources), Azure-SSIS IR. Conceptual understanding is enough.
- Secrets: linked services should reference Key Vault, never embed credentials.

### Hands-on activity

1. Create a Data Factory (lowest tier). Explore the authoring canvas, the monitor tab, and the manage tab.
2. Build a **Copy** pipeline that moves the three CSVs from `raw/sales/` to `bronze/sales/`. Parameterise the filename so one pipeline handles all three.
3. Add a **Get Metadata** activity that lists files in `raw/sales/` and a **ForEach** that copies each one. Set `batchCount` and observe parallelism in the monitor view.
4. Add an **If Condition** that fails the pipeline when the expected file count is not 3. This is your first orchestration-level quality gate.
5. Configure a schedule trigger for 02:00 daily. Then disable it — you don't want it running up cost.
6. Deliberately break it (point at a non-existent path), observe the failure in Monitor, then use **rerun from failed activity**.
7. On paper (or in Markdown), design the **full ADF pipeline** for `customer-sales-lakehouse`, mapping each of the seven notebooks to an activity with dependencies. Compare your design against `../customer-sales-lakehouse/orchestration/pipeline_design.md`.
8. Delete the Data Factory when finished.

### Deliverable

**`adf_pipeline_design.md`** containing an orchestration design for the project:

```text
                        ┌──────────────────────┐
                        │ Trigger: daily 02:00 │
                        └──────────┬───────────┘
                                   ▼
                        ┌──────────────────────┐
                        │ Validate source files│  Get Metadata + If Condition
                        │ (expect 3 files)     │  FAIL FAST if missing
                        └──────────┬───────────┘
                                   ▼
                        ┌──────────────────────┐
                        │ 01 Bronze ingestion  │  Databricks Notebook activity
                        └──────────┬───────────┘
                                   ▼
             ┌─────────────────────┼─────────────────────┐
             ▼                     ▼                     ▼
      ┌─────────────┐       ┌─────────────┐       ┌─────────────┐
      │ 02 Silver   │       │ 03 Silver   │       │ 04 Silver   │   parallel
      │  customers  │       │  products   │       │   orders    │
      └──────┬──────┘       └──────┬──────┘       └──────┬──────┘
             └─────────────────────┼─────────────────────┘
                                   ▼
                        ┌──────────────────────┐
                        │ 05 Gold dimensions   │
                        └──────────┬───────────┘
                                   ▼
                        ┌──────────────────────┐
                        │ 06 Gold fact_sales   │
                        └──────────┬───────────┘
                                   ▼
                        ┌──────────────────────┐
                        │ 07 Quality checks    │
                        │ critical check FAILs │
                        │   → fail pipeline    │
                        └──────────┬───────────┘
                                   ▼
                        ┌──────────────────────┐
                        │ Notify on success /  │
                        │ alert on failure     │
                        └──────────────────────┘
```

Plus a table of every activity with: name, type, depends-on, dependency condition, retry count, timeout, and what happens on failure.

### Self-check questions

1. What is the difference between a linked service, a dataset, and an activity? Give an example of each from your pipeline.
2. Why can the three silver notebooks run in parallel, but gold dimensions cannot start until all three finish?
3. What is a tumbling window trigger, and why is it better than a schedule trigger for a daily incremental load that might need backfilling?
4. Explain idempotency. What would go wrong in this pipeline if `06_gold_fact_sales.py` appended instead of overwriting, and you reran it?
5. Your quality check finds one duplicate order. Should the pipeline fail? What determines the answer, and where is that decision documented?
6. What is the difference between the `Success`, `Completion` and `Skipped` dependency conditions, and when would you use `Completion`?
7. Where should a storage credential live for an ADF linked service, and why never in the linked service definition itself?
8. When would you choose a Mapping Data Flow over a Databricks notebook, and what do you lose by doing so?

---

## Week 4 — PySpark for data engineering

**Goal:** write production-quality PySpark — tested, typed, and understood at the execution-plan level, not just the API level.

**Cross-reference:** `05_pyspark_deep_dive.md`

### Learning topics

- Spark architecture: driver, executors, cluster manager, tasks, stages, jobs. Why understanding this explains almost every performance problem you'll hit.
- **Lazy evaluation**: transformations build a plan; actions execute it. Why `df.count()` is expensive and `df.printSchema()` is not.
- Narrow vs wide transformations. The **shuffle** — what it is, why it's slow, and which operations cause it (`groupBy`, `join`, `distinct`, `repartition`).
- DataFrame API in depth: `select`, `filter`, `withColumn`, `when/otherwise`, `groupBy/agg`, `join` (inner, left, left_anti, left_semi), `union`, `orderBy`.
- **Schema management**: inferred vs explicit schemas. Why `inferSchema=True` is unacceptable in production (it reads the data twice and guesses wrong).
- Null semantics: `None` vs `NaN`, three-valued logic, why `col != 'x'` silently drops nulls, `isNull`, `coalesce`, `fillna`.
- Window functions: `row_number`, `rank`, `dense_rank`, `lag`, `lead` — the engine behind de-duplication.
- `dropDuplicates` vs window-based de-duplication: why the latter is deterministic and the former is not.
- Caching and persistence: when it helps, when it hurts, `unpersist`.
- Partitioning: `repartition` vs `coalesce`, `spark.sql.shuffle.partitions`, adaptive query execution.
- Broadcast joins: why joining an 8-row fact to a 4-row dimension should never shuffle.
- Reading execution plans: `df.explain(True)`, spotting `Exchange` (shuffle) and `BroadcastHashJoin`.
- UDFs and why to avoid them (they break Catalyst optimisation and serialise row by row).
- **Testing PySpark**: a session-scoped fixture, small deterministic inputs, asserting on collected results.

### Hands-on activity

Work entirely locally against `../customer-sales-lakehouse`. No cloud needed.

1. Read `data/raw/orders.csv` with an **explicit schema**. Verify you get 8 rows.

```python
from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import (
    StructType, StructField, IntegerType, StringType,
    DateType, DecimalType, TimestampType,
)

spark = (
    SparkSession.builder
    .appName("week4-pyspark")
    .master("local[*]")
    .config("spark.sql.shuffle.partitions", "4")
    .getOrCreate()
)

orders_schema = StructType([
    StructField("order_id", IntegerType(), nullable=False),
    StructField("customer_id", IntegerType(), nullable=False),
    StructField("product_id", IntegerType(), nullable=False),
    StructField("order_date", DateType(), nullable=False),
    StructField("quantity", IntegerType(), nullable=False),
    StructField("amount", DecimalType(10, 2), nullable=True),
    StructField("status", StringType(), nullable=False),
    StructField("updated_at", TimestampType(), nullable=False),
])

orders = (
    spark.read
    .option("header", True)
    .schema(orders_schema)
    .csv("data/raw/orders.csv")
)
assert orders.count() == 8, "Raw orders should have 8 rows including the duplicate"
```

2. Implement **deterministic de-duplication** with a window function and confirm you get 7 rows.

```python
from pyspark.sql.window import Window

dedupe_window = (
    Window.partitionBy("order_id")
    .orderBy(F.col("updated_at").desc(), F.col("amount").desc_nulls_last())
)

orders_deduped = (
    orders
    .withColumn("_row_num", F.row_number().over(dedupe_window))
    .filter(F.col("_row_num") == 1)
    .drop("_row_num")
)
assert orders_deduped.count() == 7, "After de-duplication there should be 7 orders"
```

3. Count orders by status before and after de-duplication. Confirm: raw = Completed 6, Cancelled 1, Pending 1; deduped = Completed 5, Cancelled 1, Pending 1.

```python
orders.groupBy("status").count().orderBy("status").show()
orders_deduped.groupBy("status").count().orderBy("status").show()
```

4. Find the three deliberate quality issues using PySpark only — the duplicate (107), the null amount (105), and the orphan customer reference (106).

```python
customers = spark.read.option("header", True).csv("data/raw/customers.csv")

duplicates = (
    orders.groupBy("order_id").count().filter(F.col("count") > 1)
)
missing_amount = orders.filter(F.col("amount").isNull())
orphans = orders_deduped.join(
    customers.select(F.col("customer_id").cast("int").alias("customer_id")),
    on="customer_id",
    how="left_anti",
)

print(f"duplicate order_ids      : {duplicates.count()}")   # expect 1
print(f"rows with null amount    : {missing_amount.count()}")  # expect 1
print(f"orphan customer refs     : {orphans.count()}")      # expect 1
```

5. Compute recognised revenue and **assert it equals 525.00**.

```python
recognised = orders_deduped.filter(
    (F.col("status") == "Completed") & F.col("amount").isNotNull()
).join(
    customers.select(F.col("customer_id").cast("int").alias("customer_id")),
    on="customer_id",
    how="inner",
)

total = recognised.agg(F.sum("amount").alias("recognised_revenue")).collect()[0][0]
print(f"Recognised revenue: {total}")
assert float(total) == 525.00, f"Expected 525.00, got {total}"
```

6. Run `recognised.explain(True)` and identify every `Exchange` in the plan. Add a broadcast hint to the dimension side and see the plan change.
7. Write at least four tests in `tests/test_transformations.py` style, each asserting one of the known facts above.
8. Refactor one notebook's logic into a pure function in `src/transformations.py` that takes a DataFrame and returns a DataFrame — making it testable.

### Deliverable

- A new or extended test file with **at least six passing tests** covering: raw row count (8), deduped row count (7), duplicate detection (1), null amount detection (1), orphan detection (1), and recognised revenue (525.00).
- A short written note: `pyspark_plan_notes.md`, recording the execution plan before and after the broadcast hint, with your explanation of the difference.
- At least one notebook's transformation logic extracted into a testable pure function.

```bash
# Run your tests
python -m pytest tests/ -v
```

### Self-check questions

1. Explain lazy evaluation to someone who knows pandas. Why does `df.filter(...)` return instantly but `df.count()` take seconds?
2. What is a shuffle, which operations cause one, and why is it the dominant cost in most Spark jobs?
3. Why is `dropDuplicates("order_id")` insufficient for de-duplicating our orders, and what does the window-function approach guarantee that it doesn't?
4. Our sample has a null `amount` on order 105. What does `WHERE amount != 0` return for that row, and why does that surprise people?
5. Why is `inferSchema=True` unacceptable in a production pipeline? Name two distinct problems it causes.
6. When does a broadcast join beat a sort-merge join, and what is the risk of broadcasting too large a table?
7. What is the difference between `repartition(10)` and `coalesce(10)`, and when would each be correct?
8. Why should you avoid Python UDFs, and what should you reach for instead?
9. How would you write a test that proves the de-duplication logic keeps the *right* row rather than just *a* row?

---

## Week 5 — Databricks workspace and workflows

**Goal:** be productive in a managed Spark environment and understand how production Spark jobs are actually run and scheduled.

**Cross-reference:** `07` (Databricks/Delta guide), `05_pyspark_deep_dive.md`

### Learning topics

- What Databricks adds over raw Spark: managed clusters, notebooks, job scheduling, a catalog, access control, and optimised runtimes.
- Workspace anatomy: workspace, repos, notebooks, clusters, jobs, catalogs, SQL warehouses.
- **Cluster types**: all-purpose (interactive, shared) vs job clusters (ephemeral, created per run). Why job clusters are cheaper and safer for production.
- Cluster configuration: runtime version, node type, autoscaling min/max, **auto-termination** (set it, always), spot/preemptible instances, Photon.
- The driver/executor mapping onto cluster nodes — connects directly to Week 4's architecture topic.
- Notebooks in production: magic commands (`%sql`, `%python`, `%sh`, `%run`), widgets for parameterisation, `dbutils` (fs, widgets, notebook, secrets), and `dbutils.notebook.exit()` for returning values.
- **Databricks Repos / Git integration** — why notebooks must live in Git, and why source-controlled `.py` files (as in our project) are more reviewable than `.ipynb`.
- Workflows/Jobs: multi-task jobs, task dependencies, retries, timeouts, notifications, and parameter passing between tasks.
- **Secret scopes** — the Databricks mechanism for reading from a key vault without exposing values. `dbutils.secrets.get()` and why its output is automatically redacted in notebook output.
- Unity Catalog concepts: metastore → catalog → schema → table, three-level namespace, grants, lineage capture. Compare with the governance model in guide `09`.
- Cost control in Databricks: DBU pricing, auto-termination, right-sizing, job clusters over all-purpose, and watching for abandoned clusters.
- Mounting vs direct `abfss://` access, and why direct access with a managed identity is the modern preference.

### Hands-on activity

> Use a free/community tier or the smallest possible cluster. **Set auto-termination to 10 minutes before you do anything else.**

1. Create a workspace and a single-node cluster with auto-termination enabled. Note the runtime version.
2. Connect the workspace to a Git repository containing your `customer-sales-lakehouse` work.
3. Upload the three sample CSVs to DBFS or a storage location the cluster can read.
4. Run `01_bronze_ingestion.py` logic in a notebook. Confirm 8 order rows land in bronze.
5. Parameterise the notebook with widgets so the environment and paths are inputs, not literals:

```python
dbutils.widgets.text("environment", "dev", "Environment")
dbutils.widgets.text("data_root", "/mnt/learning/sales", "Data root path")

environment = dbutils.widgets.get("environment")
data_root = dbutils.widgets.get("data_root")

print(f"Running in environment={environment} against {data_root}")
dbutils.notebook.exit(f"bronze_ingestion_complete:{environment}")
```

6. Create a **multi-task Workflow** replicating the seven-notebook DAG from Week 3: bronze → three parallel silver tasks → gold dimensions → gold fact → quality checks.
7. Configure the job to use a **job cluster**, not your interactive cluster. Set retries to 1 and a timeout.
8. Create a secret scope and store a dummy non-sensitive placeholder value. Retrieve it with `dbutils.secrets.get()` and confirm it prints as `[REDACTED]`.
9. Run the full workflow. Examine the run graph, per-task duration, and the Spark UI for the heaviest task.
10. **Terminate the cluster and delete the workspace resources** if you're on a paid tier.

### Deliverable

- A screenshot or exported JSON of a **working multi-task Databricks Workflow** with the correct dependency graph.
- **`databricks_notes.md`** containing:
  - Your cluster configuration and the reasoning for each setting.
  - A table comparing all-purpose vs job clusters on cost, isolation, startup time and use case.
  - A note on how secrets were accessed and why the value never appeared in output.
  - The run duration of each task, and which one dominated.

### Self-check questions

1. What is the practical difference between an all-purpose cluster and a job cluster, and which would you use for a nightly production pipeline? Why?
2. Why is auto-termination the single most important cost control in Databricks for a learner?
3. How do widgets make a notebook production-ready, and what anti-pattern do they replace?
4. What does `dbutils.secrets.get()` do that a plain environment variable read does not, and why does that matter for notebooks specifically?
5. In Unity Catalog's three-level namespace, what does each level represent, and how would you map our bronze/silver/gold layers onto it?
6. Why do our project's notebooks exist as `.py` files rather than `.ipynb`? Give two concrete benefits.
7. Your workflow's silver tasks run in parallel but the job takes as long as the slowest one plus the others. Is that expected? What does it tell you about the DAG?
8. What happens to data written to the cluster's local disk when the cluster terminates, and what's the implication for where output must go?

---

## Week 6 — Delta Lake

**Goal:** understand why the lakehouse pattern works — transactions on a data lake — and be able to convert the project's Parquet layers to Delta.

**Cross-reference:** `07` (Databricks/Delta guide), `04_etl_elt_and_quality.md`

### Learning topics

- The problem Delta solves: Parquet on a lake has **no transactions**. A failed write leaves partial files; a concurrent read sees a half-written table; there is no update or delete.
- The **transaction log** (`_delta_log/`): JSON commits, checkpoint files, and how atomicity comes from the log rather than the data files.
- **ACID** on object storage: atomic commits, snapshot isolation, optimistic concurrency control, and what happens on a conflict.
- `MERGE INTO` — the upsert primitive. `WHEN MATCHED THEN UPDATE`, `WHEN NOT MATCHED THEN INSERT`, `WHEN NOT MATCHED BY SOURCE THEN DELETE`.
- `UPDATE` and `DELETE` on a lake table — and the right-to-erasure capability this unlocks (guide `09`, Section 7.4).
- **Time travel**: `VERSION AS OF`, `TIMESTAMP AS OF`, `DESCRIBE HISTORY`. Auditing "what did this table look like last Tuesday?"
- `RESTORE` — rolling back a bad load, which is the technical backing for the rollback step in your runbook.
- **Schema enforcement** (rejecting mismatched writes) vs **schema evolution** (`mergeSchema`). Why enforcement is the default and should stay that way.
- `OPTIMIZE` and bin-packing — the fix for the small-file problem from Week 2. Z-ordering and when it helps.
- `VACUUM` — removing unreferenced files, the default retention window, and why vacuuming too aggressively destroys time travel.
- **Delta constraints**: `NOT NULL` and `CHECK` constraints enforced at write time — quality checks that cannot be bypassed.
- Generated columns and identity columns for surrogate keys.
- Delta vs Iceberg vs Hudi at a conceptual level: they solve the same problem with different trade-offs.
- Streaming with Delta: a Delta table as both source and sink, and `readStream`/`writeStream` conceptually.

### Hands-on activity

1. Convert `data/bronze/orders` from Parquet to Delta, locally with `delta-spark` or in Databricks.

```python
# Requires the delta-spark package locally; trivial in Databricks.
orders_deduped.write.format("delta").mode("overwrite").save("data/delta/silver_orders")

delta_orders = spark.read.format("delta").load("data/delta/silver_orders")
print(f"Silver orders row count: {delta_orders.count()}")   # expect 7
```

2. Inspect `_delta_log/`. Open the first JSON commit file and identify the `add` entries and the schema.
3. Run `DESCRIBE HISTORY` and record the versions.
4. Perform an `UPDATE`: change order 105's status to `Cancelled`. Check history again — a new version appears.
5. **Time travel** back to version 0 and confirm the original status returns. This is your audit capability made real.

```sql
-- In Databricks SQL or Spark SQL
DESCRIBE HISTORY delta.`/path/to/silver_orders`;

SELECT order_id, status
FROM delta.`/path/to/silver_orders` VERSION AS OF 0
WHERE order_id = 105;
```

6. Implement an **upsert** with `MERGE`. Simulate a late-arriving correction: order 105 arrives again with `amount = 25.00` and `status = 'Completed'`.

```sql
MERGE INTO silver_orders AS target
USING corrections AS source
  ON target.order_id = source.order_id
WHEN MATCHED AND source.updated_at > target.updated_at THEN
  UPDATE SET *
WHEN NOT MATCHED THEN
  INSERT *;
```

Then recompute recognised revenue and observe that it is **no longer 525.00** — because you changed the input. Restore to the previous version and confirm it returns to 525.00. This is the single most instructive exercise of the week.

7. Add a `CHECK` constraint that `amount >= 0` and attempt to insert a negative amount. Observe the rejection.
8. Deliberately write 20 tiny files, then run `OPTIMIZE` and compare the file count before and after.
9. Run `DESCRIBE DETAIL` and note `numFiles` and `sizeInBytes`.

### Deliverable

- **`delta_conversion.md`** documenting:
  - The before/after of converting silver and gold to Delta.
  - A `DESCRIBE HISTORY` output table showing at least four versions with the operation that caused each.
  - Your MERGE statement and a written explanation of what changed in recognised revenue and why.
  - A `RESTORE` demonstrating rollback, with revenue confirmed back at 525.00.
  - A before/after file count from `OPTIMIZE`.
- A section in the project's `docs/architecture.md` (or your notes) explaining **why Delta rather than plain Parquet**, in terms a stakeholder would accept.

### Self-check questions

1. A Spark job writing Parquet fails halfway through. What state is the table in? How does Delta change that outcome, and by what mechanism?
2. What exactly is in the `_delta_log/` directory, and why does the *log*, not the data files, define the table?
3. Explain optimistic concurrency control. What happens when two writers commit to the same Delta table simultaneously?
4. Write the `MERGE` statement for a Type 1 slowly changing dimension on `dim_customer`. Which clause handles a region change?
5. You ran `VACUUM` with a very short retention and now time travel fails. What happened, and what did you lose?
6. What is the difference between schema enforcement and schema evolution? When is enabling `mergeSchema` dangerous?
7. How does a Delta `CHECK` constraint differ from a quality check in `07_data_quality_checks.py`? Which failures does each catch, and when?
8. How would you satisfy a customer deletion request on a Delta `dim_customer` while keeping `fact_sales` reconciling to 525.00?
9. What does `OPTIMIZE` actually do to the files on storage, and why does it matter more as a table ages?

---

## Week 7 — Incremental loads and change handling

**Goal:** stop reprocessing everything every night. Learn to load only what changed, correctly and idempotently.

**Cross-reference:** `04_etl_elt_and_quality.md`, `07` (Databricks/Delta guide)

### Learning topics

- Full load vs incremental load: cost, runtime, and risk trade-offs. Why "just reload everything" works at 8 rows and fails at 8 billion.
- **Watermarking**: tracking a high-water mark (usually a timestamp like `updated_at`) and loading only rows beyond it. Where to store the watermark and why it must be updated *after* a successful load.
- Late-arriving data: records that appear after the watermark has moved past their business date. Lookback windows as the standard mitigation.
- Out-of-order data: why `updated_at` ordering matters for our de-duplication, and what happens if the source clock is unreliable.
- **Idempotency patterns**: overwrite-by-partition, delete-and-insert, and `MERGE`. Why `append` is almost never idempotent.
- Change Data Capture (CDC) conceptually: log-based vs query-based vs trigger-based. What each costs the source system.
- **Slowly changing dimensions revisited**: Type 0 (never change), Type 1 (overwrite), Type 2 (history rows with effective dates), Type 3 (previous value column). Why `dim_customer.region` is Type 1 in our project and what breaks if the business later needs Type 2.
- Soft deletes and tombstones: how do you know a source row was deleted if it simply stops appearing?
- Backfill strategy: reprocessing a historical range without corrupting current data. Tumbling-window triggers and partition overwrite.
- Reconciliation after incremental loads: source count vs target count, and the control totals that prove nothing was lost or duplicated.
- Deduplication at scale: why the window-function approach from Week 4 is the one that survives incremental loading.

### Hands-on activity

1. Add a watermark column to the bronze orders load and store the last successful watermark in a small control table.

```python
from pyspark.sql import functions as F

def read_incremental_orders(spark, path: str, last_watermark: str):
    """Read only orders updated after the previous successful run.

    A lookback buffer catches late-arriving rows whose updated_at was
    slightly behind when the previous run took its snapshot.
    """
    return (
        spark.read.format("delta").load(path)
        .filter(F.col("updated_at") > F.lit(last_watermark))
    )


def next_watermark(df) -> str:
    """The new high-water mark is the max updated_at actually processed."""
    row = df.agg(F.max("updated_at").alias("max_updated")).collect()[0]
    return str(row["max_updated"])
```

2. Simulate three sequential daily loads by splitting `orders.csv` by `order_date`:
   - **Load 1** — January rows only (orders 101, 102, 103). Recognised revenue so far: **250.00**.
   - **Load 2** — February rows (104, 105, 106, 107, and the duplicate 107). Cumulative recognised revenue: **525.00**.
   - **Load 3** — rerun load 2 unchanged. Revenue must **still** be 525.00. If it isn't, your pipeline is not idempotent.
3. Implement the load with `MERGE` keyed on `order_id` so the duplicate 107 is naturally handled on arrival.
4. Introduce a **late-arriving** record: an order dated 2026-01-20 that arrives during the February run. Confirm your lookback window picks it up and that monthly figures update correctly.
5. Implement a **Type 2** version of `dim_customer` alongside the Type 1 version. Change Ben Smith's region from South to East and show both the Type 1 result (history lost) and the Type 2 result (two rows with effective dates).

```sql
-- Type 2 dim_customer shape
CREATE TABLE gold.dim_customer_scd2 (
    customer_key      BIGINT      NOT NULL,   -- surrogate, unique per version
    customer_id       INT         NOT NULL,   -- natural key, repeats across versions
    customer_name     STRING      NOT NULL,
    customer_region   STRING      NOT NULL,
    effective_from    DATE        NOT NULL,
    effective_to      DATE        NULL,       -- NULL = current
    is_current        BOOLEAN     NOT NULL
);
```

6. Write a **reconciliation query** that compares source row counts to target row counts per load, and prove no rows were lost or duplicated.
7. Deliberately break idempotency (switch `MERGE` to `append`), rerun, and document the resulting wrong revenue figure. Then fix it. Keep both outputs — this is excellent interview material.

### Deliverable

- **`incremental_load_design.md`** containing:
  - A watermark design: which column, where stored, when updated, what happens on failure.
  - A three-load simulation table:

| Load | Rows arriving | Cumulative silver rows | Cumulative recognised revenue | Notes |
|---|---|---|---|---|
| 1 (January) | 3 | 3 | 250.00 | Orders 101, 102, 103 |
| 2 (February) | 5 (incl. duplicate) | 7 | 525.00 | Duplicate 107 merged, 105 & 106 excluded from revenue |
| 3 (rerun of 2) | 5 | 7 | 525.00 | **Unchanged — proves idempotency** |

  - Your `MERGE` implementation with an explanation of each clause.
  - A side-by-side Type 1 vs Type 2 `dim_customer` comparison with the Ben Smith region change.
  - A short written account of the broken-idempotency experiment and what the wrong number was.

### Self-check questions

1. What is a watermark, and why must it be updated only after a load has fully succeeded?
2. Your run on 2026-02-20 sets the watermark to the max `updated_at` processed. A record with `updated_at` one second earlier arrives the next day. What happens, and how does a lookback window fix it?
3. Why is `append` not idempotent, and what exact symptom would you see in recognised revenue after a duplicate run?
4. Explain why cumulative recognised revenue must be 525.00 after load 2 *and* after load 3. What does it mean if it is 1,050.00?
5. `dim_customer.region` is Type 1 in our project. If Ben Smith moves from South to East, what happens to historical regional revenue reporting, and is that acceptable? Who decides?
6. Design the `MERGE` clauses for a Type 2 dimension. Which clause closes the old row and which opens the new one?
7. A source row is deleted upstream. How would your incremental load ever find out, and what pattern handles it?
8. What control totals would you compute to prove an incremental load neither lost nor duplicated rows?
9. Why does query-based CDC put more load on the source system than log-based CDC?

---

## Week 8 — Dimensional modelling and the gold layer

**Goal:** design a star schema that non-technical users can query correctly without help — the real test of a gold layer.

**Cross-reference:** `02` (data modelling guide), `01_sql_deep_dive.md`

### Learning topics

- Why dimensional modelling exists: normalised OLTP schemas are correct but hostile to analysts. Star schemas trade storage for comprehensibility and query speed.
- **Grain** — the most important decision in the entire model. Declare it first, in words, before writing any DDL.
- Facts vs dimensions. Facts = measurements of events (numeric, additive, many rows). Dimensions = descriptive context (textual, few rows, wide).
- Fact types: transaction, periodic snapshot, accumulating snapshot, factless. Our `fact_sales` is a **transaction fact**.
- Measure additivity: fully additive (`sale_amount`), semi-additive (account balance — additive across customers, not across time), non-additive (ratios, percentages — never sum them).
- **Surrogate keys** vs natural keys. Why `date_key = 20260105` is a conventional and useful exception.
- Star vs snowflake. Why we deliberately denormalise `category` into `dim_product` rather than creating a `dim_category`.
- **Conformed dimensions** — the same `dim_customer` serving multiple facts, which is what makes a warehouse a warehouse rather than a collection of marts.
- The **date dimension**: why it always exists, what it contains (year, quarter, month, month name, day of week, is_weekend, fiscal period), and why it beats date functions at query time.
- Degenerate dimensions: `order_id` living on the fact with no dimension table of its own.
- Junk dimensions for low-cardinality flags.
- Role-playing dimensions: one `dim_date` referenced as order date, ship date, and payment date.
- Handling unknown members: the `-1` / "Unknown" dimension row, and the explicit decision we made *not* to use it for order 106.
- Modelling quality gates: every fact FK must resolve; every dimension key must be unique.

### Hands-on activity

1. Write the grain statement for `fact_sales` in one sentence, then validate it by asking "could two rows in this table have the same business meaning?"
2. Build the full star schema DDL and compare it to `../customer-sales-lakehouse/sql/04_star_schema_ddl.sql`.

```sql
CREATE TABLE gold.dim_customer (
    customer_key          INT           NOT NULL,
    customer_id           INT           NOT NULL,
    customer_name         VARCHAR(200)  NOT NULL,   -- Confidential: see guide 09
    customer_region       VARCHAR(50)   NOT NULL,
    customer_created_date DATE          NOT NULL,
    PRIMARY KEY (customer_key)
);

CREATE TABLE gold.dim_product (
    product_key   INT           NOT NULL,
    product_id    INT           NOT NULL,
    product_name  VARCHAR(200)  NOT NULL,
    category      VARCHAR(100)  NOT NULL,
    unit_price    DECIMAL(10,2) NOT NULL,
    PRIMARY KEY (product_key)
);

CREATE TABLE gold.dim_date (
    date_key      INT          NOT NULL,   -- yyyyMMdd
    full_date     DATE         NOT NULL,
    year          SMALLINT     NOT NULL,
    quarter       TINYINT      NOT NULL,
    month         TINYINT      NOT NULL,
    month_name    VARCHAR(20)  NOT NULL,
    day_of_month  TINYINT      NOT NULL,
    day_of_week   TINYINT      NOT NULL,
    day_name      VARCHAR(20)  NOT NULL,
    is_weekend    BOOLEAN      NOT NULL,
    PRIMARY KEY (date_key)
);

CREATE TABLE gold.fact_sales (
    order_id        INT           NOT NULL,   -- degenerate dimension
    customer_key    INT           NOT NULL,
    product_key     INT           NOT NULL,
    date_key        INT           NOT NULL,
    quantity        INT           NOT NULL,
    sale_amount     DECIMAL(10,2) NOT NULL,
    order_status    VARCHAR(20)   NOT NULL,   -- always 'Completed' here
    processing_date DATE          NOT NULL,
    PRIMARY KEY (order_id)
);
```

3. Generate `dim_date` covering 2026-01-01 to 2026-12-31 programmatically. Confirm 2026-01-05 maps to `date_key = 20260105` and `day_name = 'Monday'`.
4. Build the star from silver and verify **every one of these**:

| Assertion | Expected |
|---|---|
| `dim_customer` row count | 4 |
| `dim_product` row count | 4 |
| `fact_sales` row count | 4 |
| `SUM(fact_sales.sale_amount)` | **525.00** |
| Orphan `customer_key` count | 0 |
| Orphan `product_key` count | 0 |
| Duplicate `order_id` in fact | 0 |

5. Write every reporting query and verify the answers against the canonical figures:

```sql
-- Monthly recognised revenue
SELECT d.year, d.month, SUM(f.sale_amount) AS recognised_revenue
FROM gold.fact_sales f
JOIN gold.dim_date d ON f.date_key = d.date_key
GROUP BY d.year, d.month
ORDER BY d.year, d.month;
-- Expected: 2026-01 = 250.00, 2026-02 = 275.00, total 525.00

-- Revenue by region
SELECT c.customer_region, SUM(f.sale_amount) AS recognised_revenue
FROM gold.fact_sales f
JOIN gold.dim_customer c ON f.customer_key = c.customer_key
GROUP BY c.customer_region
ORDER BY recognised_revenue DESC;
-- Expected: North = 525.00, South = 0.00, West = 0.00
-- (South and West appear as 0.00 only via an outer join against all regions)

-- Customer revenue ranking
SELECT c.customer_name, SUM(f.sale_amount) AS recognised_revenue
FROM gold.fact_sales f
JOIN gold.dim_customer c ON f.customer_key = c.customer_key
GROUP BY c.customer_name
ORDER BY recognised_revenue DESC;
-- Expected: Aisha Khan = 450.00, Carla Jones = 75.00

-- Top products by recognised revenue
SELECT p.product_name, SUM(f.sale_amount) AS recognised_revenue
FROM gold.fact_sales f
JOIN gold.dim_product p ON f.product_key = p.product_key
GROUP BY p.product_name
ORDER BY recognised_revenue DESC;
-- Expected: Monitor = 400.00, Keyboard = 75.00, Wireless Mouse = 50.00
```

6. Prove the sanity checks: region totals, customer totals, product totals and monthly totals must **each** sum to 525.00.
7. Design (don't necessarily build) an accumulating snapshot fact for an order lifecycle: ordered → dispatched → delivered. What milestones and lag measures would it hold?

### Deliverable

- A completed `sql/04_star_schema_ddl.sql` equivalent with all four tables, keys, and comments documenting the grain.
- **`star_schema_design.md`** containing:
  - The grain statement for `fact_sales`, written in plain English.
  - A bus matrix: business processes as rows, dimensions as columns.
  - A table of every measure with its additivity classification.
  - All four reporting queries with their verified outputs.
  - A reconciliation section proving all four breakdowns sum to 525.00.
  - A written justification for excluding order 106 rather than routing it to an "Unknown" customer member.

### Self-check questions

1. State the grain of `fact_sales` in one sentence. How would you prove the data actually conforms to it?
2. Why does `fact_sales` contain 4 rows when `silver.orders` contains 7? Name each excluded row and its reason.
3. Why is `sale_amount` fully additive but an "average order value" column would not be? What should you store instead?
4. Why do we denormalise `category` into `dim_product` instead of creating `dim_category`? What do we gain and lose?
5. Why does a date dimension exist at all when SQL has date functions? Give three things `dim_date` lets you do that `MONTH(order_date)` does not.
6. Order 106 references customer 99, which does not exist. What are the three possible designs for handling that, and what does each one do to the 525.00 total?
7. Revenue by region shows North = 525.00 and both other regions at 0.00. Explain to a stakeholder exactly why, naming the specific orders involved.
8. Aisha Khan's revenue is 450.00 and Carla Jones's is 75.00. Which orders make up each figure, and why do they sum to the same 525.00 as the regional and product breakdowns?
9. What is a conformed dimension, and what breaks if two teams each build their own `dim_customer`?
10. What is a degenerate dimension, and why is `order_id` one?

---

## Week 9 — Synapse concepts and the serving layer

**Goal:** understand how modelled data is served to analysts and BI tools at scale, and when a warehouse beats a lakehouse.

**Cross-reference:** `06` (Azure guide), `01_sql_deep_dive.md`

### Learning topics

- The serving-layer question: gold data exists — now how do hundreds of analysts query it concurrently without touching your pipeline?
- Synapse Analytics components conceptually: **serverless SQL pool** (query files in place, pay per TB scanned), **dedicated SQL pool** (provisioned MPP warehouse), Spark pools, and pipelines (essentially ADF).
- **Serverless SQL** — `OPENROWSET` over Parquet/Delta in the lake, external tables, and views. The key insight: you can give analysts SQL over your gold layer without copying data anywhere.
- **MPP architecture**: control node, compute nodes, distributions. Why a 60-distribution model means data placement determines performance.
- **Distribution strategies**: hash (large facts, on a high-cardinality join key), round-robin (staging), replicated (small dimensions). Mapping our tables: `fact_sales` → hash on `customer_key`; `dim_customer`/`dim_product`/`dim_date` → replicated (they're tiny).
- **Data movement (shuffle) in MPP**: why a join on a non-distribution key forces redistribution, and how replicated dimensions eliminate it.
- Partitioning and columnstore indexes: rowgroup sizing, and why a 4-row table in a columnstore is pathologically inefficient (another reminder that our sample is for learning, not benchmarking).
- Statistics — why MPP engines need them and what happens when they're stale.
- Workload management: resource classes, workload groups, importance. Preventing one analyst's query from starving the nightly load.
- **PolyBase / COPY INTO** for bulk loading from the lake.
- Lakehouse vs warehouse decision framework: cost model, concurrency, latency, data types, governance, and skills available.
- The semantic layer: measures defined once (as in `08_dbt_orchestration_monitoring.md`) so every dashboard agrees that revenue is 525.00.
- BI connectivity concepts: import vs direct query, aggregation tables, and why the gold layer's shape determines dashboard performance.

### Hands-on activity

> Use **serverless only** if you touch Azure at all. A dedicated SQL pool costs money whether or not you query it. Pause or delete it immediately.

1. If using Azure: create a Synapse workspace and query the gold Parquet files with serverless SQL.

```sql
-- Conceptual serverless query over gold Parquet in the lake.
SELECT
    customer_region,
    SUM(sale_amount) AS recognised_revenue
FROM OPENROWSET(
    BULK 'https://<storage-account-name>.dfs.core.windows.net/gold/sales/fact_sales/**',
    FORMAT = 'PARQUET'
) AS fact
JOIN OPENROWSET(
    BULK 'https://<storage-account-name>.dfs.core.windows.net/gold/sales/dim_customer/**',
    FORMAT = 'PARQUET'
) AS dim
  ON fact.customer_key = dim.customer_key
GROUP BY customer_region;
-- Expected: North = 525.00
```

2. If not using Azure: achieve the same thing locally with Spark SQL over the gold Parquet, registering temp views and running the identical queries. The SQL is nearly identical — that is the point.
3. Create **external table definitions** (or local views) for all four gold tables, so analysts never write a file path.
4. Build the **PII-safe view** from guide `09`:

```sql
CREATE VIEW gold.vw_dim_customer_safe AS
SELECT customer_key, customer_id, customer_region, customer_created_date
FROM gold.dim_customer;

CREATE VIEW gold.vw_revenue_by_region AS
SELECT c.customer_region, SUM(f.sale_amount) AS recognised_revenue
FROM gold.fact_sales f
JOIN gold.vw_dim_customer_safe c ON f.customer_key = c.customer_key
GROUP BY c.customer_region;
```

5. Write a **distribution design** for a hypothetical scaled-up version of this warehouse (100 million orders, 2 million customers, 50,000 products). Justify each choice.
6. Write the four reporting queries against the serving layer and confirm they return the canonical answers.
7. Produce a **lakehouse vs warehouse decision table** for this specific project and state your recommendation with reasoning.
8. Pause or delete every Synapse resource.

### Deliverable

- **`serving_layer_design.md`** containing:
  - The serving-layer architecture: who queries what, through which objects.
  - External table / view definitions for all four gold tables plus the PII-safe view.
  - A distribution strategy table for the scaled scenario:

| Table | Scaled row count | Distribution | Reasoning |
|---|---|---|---|
| `fact_sales` | 100,000,000 | HASH(`customer_key`) | High cardinality, even spread, most joins are to customer |
| `dim_customer` | 2,000,000 | HASH(`customer_key`) | Too large to replicate economically |
| `dim_product` | 50,000 | REPLICATE | Small enough to sit on every node; eliminates shuffle |
| `dim_date` | ~4,000 | REPLICATE | Tiny and joined by nearly every query |

  - A lakehouse vs warehouse comparison with a clear recommendation.
  - All four reporting queries with verified outputs (525.00 total; North 525.00; Aisha Khan 450.00; Monitor 400.00).

### Self-check questions

1. What problem does a serving layer solve that the gold layer alone does not?
2. Explain serverless SQL's pricing model. What query behaviour would make it expensive, and how would you avoid that?
3. In an MPP warehouse, what is a distribution, and why does choosing the wrong distribution key destroy join performance?
4. Why would you replicate `dim_date` but hash-distribute `fact_sales`? What happens to a query joining them under each choice?
5. Our `fact_sales` has 4 rows. Why is a columnstore index a poor fit at that size, and at roughly what scale does it start to pay off?
6. What is data movement in MPP, and name two design decisions that reduce it.
7. When would you choose a dedicated warehouse over querying the lakehouse directly? Give three concrete triggers.
8. How do views in the serving layer enforce the PII rules from guide `09` without duplicating data?
9. Two dashboards disagree on revenue. How does a semantic layer prevent that, and where would the 525.00 definition live?

---

## Week 10 — Data quality engineering

**Goal:** make quality a first-class, automated, monitored part of the pipeline rather than something you check manually.

**Cross-reference:** `04_etl_elt_and_quality.md`, `09_governance_security_delivery.md`

### Learning topics

- The six quality dimensions: **completeness, uniqueness, validity, consistency, accuracy, timeliness**. Learn to classify any check into one.
- Where to check: at ingestion (fail fast, cheapest), at transformation boundaries (catch logic errors), at publication (last line of defence before consumers).
- Check severity and the behaviour it drives: informational (log), warning (alert, continue), critical (**stop the pipeline**). Connect to the severity table in guide `09`.
- Quarantine patterns: rejecting rows to a side table rather than dropping them silently. Why silent drops are the most dangerous failure mode in data engineering.
- **Reconciliation controls**: source-to-target row counts, control totals, and hash totals. The 525.00 assertion is a control total.
- Anomaly detection without ML: day-on-day percentage change thresholds, rolling-average bands, min/max expectations.
- Freshness and volume checks: "did data arrive?" and "did the right *amount* arrive?" are separate questions and both matter.
- Schema drift detection: new columns, removed columns, changed types. Why Delta's schema enforcement helps but doesn't cover semantic drift.
- Profiling: distinct counts, null rates, min/max, value distributions — the first thing you do with an unfamiliar dataset.
- Declarative quality frameworks conceptually (expectation suites, dbt tests) vs hand-rolled checks. Trade-offs in both directions.
- Quality results as data: the `check_name, check_status, failed_record_count, processing_date` schema, and the dashboards and trends it unlocks.
- Alert fatigue: why a check that fails every day is worse than no check at all.

### Hands-on activity

1. Classify each of the project's checks into one of the six dimensions:

| check_name | Dimension | Severity | Expected on sample |
|---|---|---|---|
| `orders_unique_order_id` | Uniqueness | High | FAIL, 1 |
| `orders_amount_not_null` | Completeness | High | FAIL, 1 |
| `orders_customer_fk_valid` | Consistency | High | FAIL, 1 |
| `orders_product_fk_valid` | Consistency | High | PASS, 0 |
| `orders_status_in_allowed_values` | Validity | Medium | PASS, 0 |
| `fact_sales_revenue_reconciles` | Accuracy | **Critical** | PASS, 0 |
| `orders_freshness` | Timeliness | High | PASS, 0 |

2. Implement a reusable check function and a results writer matching the project's schema.

```python
from dataclasses import dataclass
from datetime import date
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import (
    StructType, StructField, StringType, LongType, DateType,
)


QUALITY_RESULT_SCHEMA = StructType([
    StructField("check_name", StringType(), nullable=False),
    StructField("check_status", StringType(), nullable=False),
    StructField("failed_record_count", LongType(), nullable=False),
    StructField("processing_date", DateType(), nullable=False),
])


@dataclass(frozen=True)
class CheckResult:
    check_name: str
    check_status: str
    failed_record_count: int
    processing_date: date


def run_check(
    name: str,
    failing_rows: DataFrame,
    processing_date: date,
) -> CheckResult:
    """Evaluate one quality rule.

    `failing_rows` must be a DataFrame of ONLY the violating rows.
    We persist the COUNT, never the row contents — see guide 09, section 14.3.
    """
    failed = failing_rows.count()
    return CheckResult(
        check_name=name,
        check_status="PASS" if failed == 0 else "FAIL",
        failed_record_count=failed,
        processing_date=processing_date,
    )


def write_results(
    spark: SparkSession,
    results: list[CheckResult],
    output_path: str,
) -> None:
    rows = [
        (r.check_name, r.check_status, r.failed_record_count, r.processing_date)
        for r in results
    ]
    (
        spark.createDataFrame(rows, schema=QUALITY_RESULT_SCHEMA)
        .write.mode("append")
        .partitionBy("processing_date")
        .parquet(output_path)
    )
```

3. Implement the critical reconciliation check:

```python
def check_revenue_reconciles(fact_sales: DataFrame, expected: float = 525.00):
    """Critical control total: recognised revenue must equal the agreed figure.

    This is the technical implementation of transformation rule TR-09 and the
    glossary definition of Recognised Revenue.
    """
    actual = fact_sales.agg(F.sum("sale_amount").alias("total")).collect()[0]["total"]
    actual_value = float(actual) if actual is not None else 0.0
    if abs(actual_value - expected) > 0.001:
        raise ValueError(
            f"Revenue reconciliation FAILED: expected {expected:.2f}, "
            f"got {actual_value:.2f}. Pipeline halted; do not publish."
        )
    return actual_value
```

4. Run the full check suite and confirm the expected results table: three FAILs with count 1 each, and the rest PASS.
5. Build a **quarantine** table: route order 105 (null amount) and order 106 (orphan customer) to `data/quarantine/orders/` with a `rejection_reason` column, instead of dropping them.
6. Profile all three datasets: null rates, distinct counts, min/max per column. Record the baseline.
7. Implement an anomaly check: fail if today's recognised revenue differs from the 7-day rolling average by more than 50%.
8. Write a trend query over `quality_results` showing failure counts by check over the last 30 days.
9. Make the critical check **pipeline-blocking** in `prod` and non-blocking in `dev`, driven by `fail_pipeline_on_error` in the config files.

### Deliverable

- A working, extended `07_data_quality_checks.py` equivalent producing a `quality_results` output with the exact four-column schema.
- A populated results table for the sample run:

| check_name | check_status | failed_record_count |
|---|---|---|
| `orders_unique_order_id` | FAIL | 1 |
| `orders_amount_not_null` | FAIL | 1 |
| `orders_customer_fk_valid` | FAIL | 1 |
| `orders_product_fk_valid` | PASS | 0 |
| `orders_status_in_allowed_values` | PASS | 0 |
| `fact_sales_revenue_reconciles` | PASS | 0 |

- A quarantine output containing exactly 2 rows (orders 105 and 106) with rejection reasons.
- **`data_quality_report.md`**: a one-page report a stakeholder could read, stating what was checked, what failed, what was excluded, and confirming recognised revenue of 525.00.

### Self-check questions

1. Name the six data quality dimensions and give an example check from this project for each.
2. Why does the sample dataset *deliberately* fail three checks? What would it mean if all checks suddenly passed?
3. What is the difference between dropping a bad row and quarantining it? Why is silent dropping the most dangerous option?
4. Which single check should stop the pipeline entirely, and why that one rather than the duplicate check?
5. What is a control total, and why is asserting 525.00 more powerful than asserting "4 rows exist"?
6. Why should `fail_pipeline_on_error` differ between `config/dev.yml` and `config/prod.yml`?
7. Order 105 has a null amount and status `Pending`. Which quality dimension does that violate, and would excluding it still be correct if its status were `Completed`?
8. Describe alert fatigue. Our sample fails three checks every single run — how would you design around that so real regressions are still visible?
9. Why does the quality results table store only counts and never the offending row values?
10. How would you detect schema drift, and what would you do when a new column appears in `orders.csv`?

---

## Week 11 — Security, governance and monitoring

**Goal:** apply the principles from guide `09` to an Azure design, and make the pipeline observable.

**Cross-reference:** `09_governance_security_delivery.md`, `08_dbt_orchestration_monitoring.md`

### Learning topics

**Security**

- Entra ID in depth: users, groups, service principals, **managed identities** (system-assigned vs user-assigned), and app registrations.
- Azure RBAC: scope inheritance (management group → subscription → resource group → resource), built-in vs custom roles, and data-plane roles like Storage Blob Data Contributor.
- **Key Vault** conceptually: secrets, keys, certificates; access policies vs RBAC; soft delete and purge protection; referencing secrets from ADF, Databricks and app configuration **by name only**.
- Managed identity end to end: how a Data Factory or Databricks workload authenticates to storage with **no secret in existence**.
- Network controls conceptually: private endpoints, service endpoints, storage firewalls, and why "public endpoint with a strong key" is weaker than "private endpoint with a managed identity".
- Encryption: at rest (platform-managed vs customer-managed keys), in transit (TLS), and column-level encryption for Confidential fields.
- Applying the classification tiers and the access matrix from guide `09` to actual Azure role assignments.

**Governance**

- Data catalogue and lineage tooling conceptually: scanning, classification, glossary terms, and lineage capture from ADF/Databricks.
- Policy as code: enforcing tagging, allowed regions, and required encryption through subscription-level policy.
- Mapping the RACI from guide `09` onto Azure groups and role assignments.

**Monitoring**

- The three pillars: **metrics** (numbers over time), **logs** (events), **traces** (request paths).
- Azure Monitor, Log Analytics workspaces, and diagnostic settings — routing ADF, Databricks and storage logs to one queryable place.
- KQL basics for querying logs.
- Alerting design: what deserves an alert, what deserves a dashboard, and what deserves neither. Actionability as the test.
- **SLAs, SLOs and SLIs** for a data pipeline: freshness, completeness, success rate, duration.
- Pipeline observability: run duration trends, row-count trends, failure rates, and the quality trends from Week 10.
- On-call practicalities: runbook links in alerts, escalation paths, and the incident template from guide `09` Section 18.

### Hands-on activity

1. Write the complete **Azure RBAC design** for the project, translating the role table from guide `09` Section 9.3 into Azure groups and role assignments. Use placeholders throughout.

```yaml
# rbac_design.yml — design artefact, placeholders only, no real identities
resource_group: rg-<project>-prod
assignments:
  - group: grp-sales-data-engineers
    role: Storage Blob Data Contributor
    scope: /storage/<storage-account-name>/containers/bronze,silver,gold
    justification: Build and maintain pipeline outputs
  - group: grp-sales-analysts
    role: Storage Blob Data Reader
    scope: /storage/<storage-account-name>/containers/gold
    justification: Read gold layer only; no PII view access
  - group: grp-sales-analysts-pii
    role: Storage Blob Data Reader
    scope: /storage/<storage-account-name>/containers/gold/dim_customer
    justification: Approved PII access; quarterly recertification required
  - identity: mi-<project>-pipeline      # user-assigned managed identity
    role: Storage Blob Data Contributor
    scope: /storage/<storage-account-name>/containers/bronze,silver,gold,ops
    justification: Pipeline writes; no delete on raw
  - identity: mi-<project>-pipeline
    role: Key Vault Secrets User
    scope: /keyvault/<key-vault-name>/secrets/sales-lakehouse-*
    justification: Scoped to its own secrets only
```

2. Create a Key Vault, store a **placeholder non-sensitive value**, and reference it from a Databricks secret scope or ADF linked service. Confirm the value never appears in any output.
3. Enable diagnostic settings on your storage account and Data Factory, routing to a Log Analytics workspace. Run a pipeline and find the resulting log entries.
4. Write three KQL queries:

```text
// 1. Pipeline failures in the last 7 days
ADFPipelineRun
| where TimeGenerated > ago(7d)
| where Status == "Failed"
| project TimeGenerated, PipelineName, FailureType, Error = tostring(Parameters)
| order by TimeGenerated desc

// 2. Pipeline duration trend — is it degrading?
ADFPipelineRun
| where TimeGenerated > ago(30d)
| where Status == "Succeeded"
| summarize avg_minutes = avg(DurationMs / 60000.0) by bin(TimeGenerated, 1d), PipelineName
| order by TimeGenerated asc

// 3. Unusual access to the gold container
StorageBlobLogs
| where TimeGenerated > ago(1d)
| where ObjectKey contains "gold"
| summarize requests = count() by CallerIpAddress, AuthenticationType, StatusText
| order by requests desc
```

5. Define SLIs and SLOs for the pipeline and write them into the runbook.

| SLI | Definition | SLO target |
|---|---|---|
| Freshness | Hours since `fact_sales` last successfully refreshed | < 26 hours, 99% of days |
| Success rate | Successful runs ÷ scheduled runs over 30 days | ≥ 98% |
| Duration | p95 end-to-end pipeline runtime | < 30 minutes |
| Quality | Runs where all critical checks PASS | 100% |
| Completeness | Source rows ÷ expected rows | ≥ 99.9% |

6. Design the alert set: for each, state the condition, severity, recipient, and the runbook section it links to. Deliberately keep it short — three to five alerts.
7. Complete a **PII handling review** of the project: list every location `customer_name` exists, the control protecting each, and any gaps.
8. Write a one-page **governance summary** for the project covering owners, classifications, retention, and access.
9. Delete all Azure resources created this week.

### Deliverable

- **`security_governance_design.md`** containing:
  - The full RBAC design (groups, roles, scopes, justifications) using placeholders only.
  - A secrets inventory: what secrets would exist, where they'd live, who can read them, and the rotation cadence. **No values.**
  - A classification table for every dataset and the controls applied.
  - A PII map for `customer_name` with controls and gaps.
  - The retention schedule mapped to storage lifecycle policies.
- **`monitoring_design.md`** containing:
  - The SLI/SLO table above, with your own targets and justification.
  - Three working KQL queries with example output.
  - An alert design table (condition, severity, recipient, runbook link) with no more than five alerts.
  - A dashboard sketch: which five panels would you put on a single screen for this pipeline?

### Self-check questions

1. Walk through, step by step, how a Data Factory pipeline authenticates to ADLS Gen2 with a managed identity. At which point does a secret exist? (The answer should be "never".)
2. Why is Key Vault safer than an environment variable, given that both ultimately hand a value to the application?
3. Our analysts must see revenue by customer but not customer names. Describe every control you'd implement, and name which one is the actual enforcement point.
4. What is the difference between control-plane RBAC (Contributor on a storage account) and data-plane RBAC (Storage Blob Data Reader)? Which one lets you read a blob?
5. Design the minimum permission set for `mi-<project>-pipeline`. Which permissions did you deliberately withhold, and what would each enable an attacker to do?
6. Define SLI, SLO and SLA for a daily data pipeline and give a concrete example of each from this project.
7. Which conditions in this pipeline deserve a page at 3am, and which deserve a ticket in the morning? Justify the line you drew.
8. Alert fatigue: what is the most likely source of it in this pipeline, and how would you prevent it?
9. A customer invokes their right to erasure. Trace every location their name exists across raw, bronze, silver, gold, logs and backups, and describe how you satisfy the request while keeping revenue at 525.00.
10. What would a diagnostic log tell you that your application log does not, and vice versa?

---

## Week 12 — Git, CI/CD and capstone delivery

**Goal:** ship it. Version-controlled, tested automatically, documented, and presentable to an interviewer.

**Cross-reference:** `08_dbt_orchestration_monitoring.md`, `11_portfolio_project_blueprint.md`, Linux/Git guide (`03`)

### Learning topics

- Branching strategies: trunk-based vs GitFlow vs GitHub Flow. Why trunk-based with short-lived feature branches suits data teams.
- Commit hygiene: atomic commits, conventional commit messages, and why `git log` is documentation.
- Pull requests as a governance control: review checklists, required approvals, and the separation-of-duties point from guide `09` Section 10.4.
- **CI for data projects**: linting, formatting, type checking, unit tests, and **secret scanning** on every push.
- Testing pyramid for data: unit tests on pure transformation functions (fast, many), integration tests on the pipeline (slower, fewer), data-quality tests on outputs (the 525.00 assertion).
- **CD for data**: deploying notebooks, pipeline definitions, and config. Why config is deployed separately from code.
- Infrastructure as code conceptually: declaring the resource group, storage, factory and vault rather than clicking them.
- Environment promotion automation: the dev → test → prod gate table from guide `09` Section 13.5, implemented as pipeline stages.
- Release practices: versioning, tagging, changelogs, rollback plans.
- Documentation as a release artefact: README, architecture, data dictionary, runbook all updated in the same PR as the code.
- **Presenting the work**: repository README as the shop window, the honest separation of "built locally" from "designed for Azure".

### Hands-on activity

1. Audit the repository history for secrets. Run a scanner. If anything is found, follow guide `09` Section 12 **in order** — rotate first.
2. Verify `.gitignore` excludes `data/bronze/`, `data/silver/`, `data/gold/`, `data/quality_results/`, `.env`, and any credential file patterns.
3. Write a CI workflow that runs on every push and pull request.

```yaml
# .github/workflows/ci.yml — illustrative
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  quality:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt
          pip install pytest ruff

      - name: Lint
        run: ruff check src tests notebooks

      - name: Unit tests
        run: python -m pytest tests/ -v --tb=short

      - name: Pipeline smoke test and reconciliation
        run: |
          python notebooks/01_bronze_ingestion.py
          python notebooks/02_silver_customers.py
          python notebooks/03_silver_products.py
          python notebooks/04_silver_orders.py
          python notebooks/05_gold_dimensions.py
          python notebooks/06_gold_fact_sales.py
          python notebooks/07_data_quality_checks.py
          python -m pytest tests/ -k reconciliation -v
```

4. Add a **reconciliation test** that CI cannot pass without the canonical numbers being correct.

```python
# tests/test_reconciliation.py
import pytest
from pyspark.sql import SparkSession, functions as F


@pytest.fixture(scope="session")
def spark():
    session = (
        SparkSession.builder
        .appName("reconciliation-tests")
        .master("local[2]")
        .config("spark.sql.shuffle.partitions", "2")
        .getOrCreate()
    )
    yield session
    session.stop()


@pytest.fixture(scope="session")
def fact_sales(spark):
    return spark.read.parquet("data/gold/fact_sales")


def test_fact_sales_row_count(fact_sales):
    assert fact_sales.count() == 4


def test_recognised_revenue_total(fact_sales):
    total = fact_sales.agg(F.sum("sale_amount")).collect()[0][0]
    assert float(total) == pytest.approx(525.00, abs=0.001)


def test_order_ids_are_unique(fact_sales):
    assert fact_sales.select("order_id").distinct().count() == fact_sales.count()


def test_expected_order_ids_present(fact_sales):
    actual = {row["order_id"] for row in fact_sales.select("order_id").collect()}
    assert actual == {101, 102, 104, 107}


def test_monthly_revenue_split(spark, fact_sales):
    monthly = {
        row["month_key"]: float(row["revenue"])
        for row in (
            fact_sales
            .withColumn("month_key", (F.col("date_key") / 100).cast("int"))
            .groupBy("month_key")
            .agg(F.sum("sale_amount").alias("revenue"))
            .collect()
        )
    }
    assert monthly[202601] == pytest.approx(250.00, abs=0.001)
    assert monthly[202602] == pytest.approx(275.00, abs=0.001)
    assert sum(monthly.values()) == pytest.approx(525.00, abs=0.001)
```

5. Add a pre-commit configuration with a secret-detection hook, formatting and trailing-whitespace fixes.
6. Protect `main`: require the CI check to pass and at least one review before merge.
7. Rewrite the project README as a portfolio shop window. It must contain, unmistakably separated:
   - **What is built and runnable locally** (PySpark + Parquet on a laptop).
   - **What is a design for Azure and not deployed** (ADLS Gen2, ADF, Databricks, Delta, serving layer).
8. Review and update every document in `docs/` so it matches the code as it now stands.
9. Tag a release `v1.0.0` and write a changelog.
10. Do a **mock interview**: record yourself giving the 60-second project summary from `11_portfolio_project_blueprint.md`. Watch it back. Redo it until you can explain 525.00 without hesitating.

### Deliverable

**The finished capstone.** Specifically:

- [ ] A green CI run on `main` that executes the full pipeline and asserts 525.00
- [ ] Branch protection enabled with a required review
- [ ] Zero secrets anywhere in history (verified by scan)
- [ ] A README that clearly separates the local build from the Azure design
- [ ] All five `docs/` files current and consistent
- [ ] A tagged `v1.0.0` release with a changelog
- [ ] Your 60-second verbal summary recorded and rehearsed
- [ ] A written answer to all twelve interview questions in guide `09` Section 19.3
- [ ] Your week-by-week self-check answers compiled into one document, with gaps flagged for follow-up study

### Self-check questions

1. Why should a data project's CI run the pipeline and not just the unit tests? What class of bug does that catch?
2. What exactly does the reconciliation test protect? Describe a code change it would block.
3. Why is config deployed separately from code, and what breaks if you bundle them?
4. Why does branch protection with a required review implement separation of duties, and what risk does it mitigate specifically?
5. Which files must **never** be committed in this project, and how is that enforced automatically rather than by discipline?
6. Explain the testing pyramid for a data pipeline. Why are there more unit tests than integration tests?
7. Someone changes the de-duplication logic. Trace exactly which CI step fails and what the error message would say.
8. How would you describe the Azure portion of this project in an interview without overclaiming? Write the exact sentence you'd use.
9. What goes in a changelog entry for this release, and who is the audience?
10. What is the single weakest part of your project right now, and what would you do about it with another two weeks?

---

## Progress tracking checklist

Tick each box only when the deliverable actually exists as an artefact you could show someone.

| Week | Theme | Learning topics read | Hands-on done | Deliverable produced | Self-check answered | Committed to Git | Notes / date completed |
|---|---|---|---|---|---|---|---|
| 1 | Azure fundamentals | ☐ | ☐ | ☐ `azure_foundations.md` | ☐ | ☐ | |
| 2 | ADLS Gen2 & storage | ☐ | ☐ | ☐ `storage_design.md` | ☐ | ☐ | |
| 3 | Azure Data Factory | ☐ | ☐ | ☐ `adf_pipeline_design.md` | ☐ | ☐ | |
| 4 | PySpark | ☐ | ☐ | ☐ 6+ passing tests | ☐ | ☐ | |
| 5 | Databricks | ☐ | ☐ | ☐ Multi-task workflow | ☐ | ☐ | |
| 6 | Delta Lake | ☐ | ☐ | ☐ `delta_conversion.md` | ☐ | ☐ | |
| 7 | Incremental loads | ☐ | ☐ | ☐ `incremental_load_design.md` | ☐ | ☐ | |
| 8 | Dimensional modelling | ☐ | ☐ | ☐ `star_schema_design.md` + DDL | ☐ | ☐ | |
| 9 | Synapse & serving | ☐ | ☐ | ☐ `serving_layer_design.md` | ☐ | ☐ | |
| 10 | Data quality | ☐ | ☐ | ☐ `data_quality_report.md` | ☐ | ☐ | |
| 11 | Security, governance, monitoring | ☐ | ☐ | ☐ `security_governance_design.md` + `monitoring_design.md` | ☐ | ☐ | |
| 12 | Git, CI/CD, capstone | ☐ | ☐ | ☐ Green CI + release v1.0.0 | ☐ | ☐ | |

### Milestone gates

Three points where you should stop and honestly assess before continuing.

| Gate | After week | You should be able to… | If you can't |
|---|---|---|---|
| **Foundations** | 3 | Explain the Azure resource hierarchy, design a lakehouse storage layout, and read an ADF pipeline graph | Repeat week 1–3 hands-on; don't proceed on shaky ground |
| **Build** | 7 | Write tested PySpark, run a Databricks workflow, explain Delta's transaction log, and implement an idempotent incremental load | Revisit `05_pyspark_deep_dive.md` and week 6–7 exercises |
| **Deliver** | 12 | Hand the repo to a stranger who runs it and reproduces 525.00 unaided | Fix documentation first, then re-test with a different person |

### Canonical facts you must know cold by week 12

Keep this card. If any answer takes more than a second, review.

| Question | Answer |
|---|---|
| Raw order rows | 8 |
| Rows after de-duplication | 7 |
| Rows in `fact_sales` | 4 |
| Recognised revenue | **525.00** |
| Order IDs in `fact_sales` | 101, 102, 104, 107 |
| January 2026 revenue | 250.00 |
| February 2026 revenue | 275.00 |
| Revenue by region | North 525.00, South 0.00, West 0.00 |
| Aisha Khan revenue | 450.00 |
| Carla Jones revenue | 75.00 |
| Top product | Monitor (1003), 400.00 |
| Raw status counts | Completed 6, Cancelled 1, Pending 1 |
| Deduped status counts | Completed 5, Cancelled 1, Pending 1 |
| Quality issues | 1 duplicate, 1 missing amount, 1 invalid customer ref, 0 invalid product refs, 0 invalid statuses |
| Why 103 is excluded | Cancelled |
| Why 105 is excluded | Pending **and** missing `amount` |
| Why 106 is excluded | `customer_id` 99 does not exist |

---

## After week 12

### Immediate next steps

- [ ] Re-check the **current official Microsoft certification catalogue** and decide whether a certification serves your goals right now.
- [ ] If yes, read the current official skills-measured document end to end and gap-analyse it against your twelve weeks of notes.
- [ ] Publish the repository and write a short post explaining one thing you learned — teaching is the strongest retention tool there is.
- [ ] Complete the CV bullets and 60-second summary from `11_portfolio_project_blueprint.md`.
- [ ] Do three mock interviews with the questions from guide `09` Section 19.3 and Week 12.

### Deepening options (pick one, not all)

| Direction | What to build next | Why |
|---|---|---|
| **Streaming** | Convert the orders ingestion to a streaming source with Delta as sink | Streaming is the most common gap in batch-only CVs |
| **Scale** | Generate 50 million synthetic orders and tune the pipeline | Makes partitioning, shuffles and file sizing real rather than theoretical |
| **Analytics engineering** | Rebuild the gold layer in dbt with tests and docs | Revisits `08_dbt_orchestration_monitoring.md` with real stakes |
| **Infrastructure as code** | Declare the full Azure design in Bicep or Terraform | Turns the Week 11 design into something deployable |
| **Data contracts** | Formalise producer/consumer contracts with schema validation at the boundary | Increasingly expected at senior level |

### A closing word on honesty

Most of this plan runs on a laptop. That is a strength, not a weakness — it means the project is reproducible by anyone who clones it, costs nothing, and demonstrates genuine engineering rather than portal clicking.

But be precise about it. "I built a medallion lakehouse locally in PySpark and designed its Azure deployment" is impressive and true. "I built an Azure data platform" is neither. Interviewers check, and the first sentence earns more credibility than the second ever will.

See `11_portfolio_project_blueprint.md` for exactly how to word it.

---

*Last reviewed: 2026-10-08 · Part of the data-engineering-learning series · Companion project: [`../customer-sales-lakehouse`](../customer-sales-lakehouse)*
*Reminder: certification details change — always verify against the current official Microsoft certification catalogue.*
