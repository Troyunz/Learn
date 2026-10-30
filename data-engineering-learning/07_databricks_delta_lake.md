# 07 — Databricks and Delta Lake

> **Scope:** how Azure Databricks is organised, what Delta Lake actually does under the hood, and
> exactly how the local Parquet implementation in **`../customer-sales-lakehouse`** maps onto
> Delta.
>
> **No live environment needed to read this.** Every path is a placeholder such as
> `abfss://<container>@<storage-account-name>.dfs.core.windows.net/...` or
> `/mnt/<mount-name>/...`. There are no credentials, tokens, workspace URLs or account names
> anywhere in this guide.
>
> **The claim we will prove:** moving from Parquet to Delta changes *how* data is written and
> *what guarantees* you get. It does **not** change the transformation logic, the quality checks,
> or the answer — recognised revenue is still exactly **525.00**.

---

## Table of contents

1. [Databricks workspace anatomy](#1-databricks-workspace-anatomy)
2. [Compute: clusters and cost discipline](#2-compute-clusters-and-cost-discipline)
3. [Jobs and Workflows](#3-jobs-and-workflows)
4. [DBFS, mounts and storage access](#4-dbfs-mounts-and-storage-access)
5. [Unity Catalog at a concept level](#5-unity-catalog-at-a-concept-level)
6. [Repos and Git integration](#6-repos-and-git-integration)
7. [Monitoring and run history](#7-monitoring-and-run-history)
8. [What Delta Lake actually is](#8-what-delta-lake-actually-is)
9. [The transaction log in detail](#9-the-transaction-log-in-detail)
10. [ACID guarantees and optimistic concurrency](#10-acid-guarantees-and-optimistic-concurrency)
11. [Schema enforcement and schema evolution](#11-schema-enforcement-and-schema-evolution)
12. [Parquet vs Delta comparison](#12-parquet-vs-delta-comparison)
13. [Writing and reading Delta with PySpark](#13-writing-and-reading-delta-with-pyspark)
14. [MERGE INTO in depth](#14-merge-into-in-depth)
15. [The bronze to silver to gold design in Delta terms](#15-the-bronze-to-silver-to-gold-design-in-delta-terms)
16. [Time travel: audit and rollback](#16-time-travel-audit-and-rollback)
17. [OPTIMIZE, Z-ordering and VACUUM](#17-optimize-z-ordering-and-vacuum)
18. [How the local Parquet project maps onto Delta](#18-how-the-local-parquet-project-maps-onto-delta)
19. [Hands-on exercises](#19-hands-on-exercises)

---

## 1. Databricks workspace anatomy

A **workspace** is the top-level Databricks environment: a web application plus the compute and
metadata that belong to it. In Azure it is an Azure resource (`dbw-<workload>-<env>-<region>`)
reached at a URL of the form `https://<workspace-url>`.

### The objects you work with

| Object | What it is | Where the real work happens |
|---|---|---|
| **Notebook** | A document of cells (Python / SQL / Scala / R / Markdown) attached to compute | Development and, in many shops, production transformation code |
| **Repo** | A Git-backed folder of files in the workspace | Version-controlled code — the professional default |
| **Cluster (compute)** | A Spark driver + workers | Executes everything |
| **Job** | A scheduled or triggered run of a notebook / script / JAR / Python wheel | Production execution |
| **Workflow** | A DAG of tasks, each with its own compute and dependencies | Multi-step production pipelines |
| **DBFS** | A filesystem abstraction over object storage | Legacy mount points and small scratch files |
| **Unity Catalog** | Central governance: catalogs, schemas, tables, permissions, lineage | Metadata and access control |
| **SQL Warehouse** | Compute optimised for SQL/BI queries | Dashboards and ad-hoc SQL |
| **Secret scope** | Named store for secrets, often Key Vault backed | Credentials at runtime, never in code |

### Notebooks

A notebook cell runs in a language determined by the notebook's default or a magic command:

```python
# Default language Python
df = spark.read.format("delta").load("/mnt/<mount-name>/silver/orders")
display(df)
```

```text
%sql    -- run SQL in this cell
%python -- run Python in this cell
%scala  -- run Scala
%sh     -- run a shell command on the DRIVER node only
%fs     -- filesystem shortcuts (ls, cp, rm)
%md     -- markdown documentation
%run    -- execute another notebook inline, sharing the same session
%pip    -- install a library for this session
```

Useful built-ins:

| Call | Does |
|---|---|
| `display(df)` | Rich, sortable, chartable table output |
| `dbutils.fs.ls(path)` | List files |
| `dbutils.widgets.text("processing_date", "")` | Declare a parameter the job can pass in |
| `dbutils.widgets.get("processing_date")` | Read that parameter |
| `dbutils.notebook.run("./child", 600, {"k": "v"})` | Run another notebook as a separate job |
| `dbutils.notebook.exit("SUCCESS")` | Return a value to the caller |
| `dbutils.secrets.get(scope=..., key=...)` | Fetch a secret — **never print the result** |

Parameterising a notebook so a job can drive it:

```python
dbutils.widgets.text("processing_date", "2026-03-01")
dbutils.widgets.text("environment", "dev")

processing_date = dbutils.widgets.get("processing_date")
environment = dbutils.widgets.get("environment")
```

This is the Databricks equivalent of a command-line argument, and it is how one notebook serves
both dev and prod.

> **Notebook discipline.** Notebooks are a wonderful development surface and a poor place to keep
> business logic. The professional pattern — and the one `../customer-sales-lakehouse` already
> uses — is **thin notebooks calling tested modules**: the logic lives in `src/transformations.py`
> and `src/quality_checks.py`, which are importable, unit-testable and reviewable, while
> `notebooks/01_bronze_ingestion.py` … `notebooks/07_data_quality_checks.py` are short
> orchestration shells.

```python
# notebooks/02_silver_transformations.py  - a thin shell, in spirit
import sys
sys.path.append("/Workspace/Repos/<repo-folder>/customer-sales-lakehouse")

from src.config import BRONZE_DIR, SILVER_DIR
from src.logging_utils import get_logger
from src.transformations import standardise_text_columns, remove_duplicate_orders, add_data_quality_status

logger = get_logger(__name__)

bronze_orders = spark.read.format("delta").load(f"{BRONZE_DIR}/orders")
silver_orders = add_data_quality_status(remove_duplicate_orders(bronze_orders), customers, products)
logger.info("Silver orders rows: %s", silver_orders.count())
```

---

## 2. Compute: clusters and cost discipline

### All-purpose vs job clusters

| | **All-purpose cluster** | **Job cluster** |
|---|---|---|
| Created by | A person, in the UI | **The job, automatically, at run time** |
| Lifetime | Until terminated (manually or by auto-termination) | **Only for that run, then destroyed** |
| Shared | Yes — several users can attach notebooks | No — dedicated to one job run |
| DBU rate | **Higher** | **Lower** |
| Interactive use | Yes | No |
| Use for | Development, exploration, debugging | **All scheduled production work** |

> **The single most important cost rule in Databricks: use job clusters for scheduled work, and
> set auto-termination on every all-purpose cluster.** An all-purpose cluster left running over a
> long weekend is the most common large, entirely avoidable bill in the platform.

### Cluster configuration that matters

| Setting | Guidance |
|---|---|
| **Databricks Runtime (DBR) version** | Pick an **LTS** release for production. Note the bundled Spark and Delta versions |
| **Node type** | Memory-optimised for joins/shuffles; general purpose for most ETL; I/O-optimised for huge scans |
| **Workers (fixed vs autoscale)** | Autoscale with a sensible `min`/`max`; fixed for predictable, steady workloads |
| **Auto-termination** | **Always set it.** 20–30 minutes is a sane default for development |
| **Spot / low-priority instances** | Cheaper workers, can be reclaimed — fine for retryable batch, not for long single-shot jobs |
| **Photon** | Vectorised C++ engine; can substantially speed up SQL and Delta workloads |
| **Single node** | Driver only, no workers — **perfect for an 8-row teaching dataset** |
| **Cluster policy** | Admin-defined guard rails capping size, runtime and features |
| **Access mode** | Determines Unity Catalog compatibility and isolation between users |

### Autoscaling, honestly

Autoscaling adds workers when tasks queue and removes them when they idle. It is genuinely useful
for variable workloads, but:

- Scaling **up** takes minutes (VMs must be acquired and joined to the cluster).
- Scaling **down** requires the cluster to be safely idle; cached data can hold nodes.
- For a short, predictable job, a **fixed small cluster often costs less** than an autoscaling one
  that spends half the run warming up.

For `../customer-sales-lakehouse`, a **single-node job cluster** is correct and honest. Eight rows
do not need a cluster, and saying so demonstrates judgement.

### Libraries

| Scope | How | When |
|---|---|---|
| Notebook session | `%pip install <package>` | Experimentation |
| Cluster | Libraries tab (PyPI/Maven/wheel) | Shared dependency for all notebooks on the cluster |
| Job | Task-level library configuration | **Production — pinned and reproducible** |
| Repo | `requirements.txt` installed by the job | Keeps dependencies in version control |

Pin versions in production. `delta-spark` and `pyspark` versions must match your runtime, and
"it worked yesterday" is not a dependency strategy.

---

## 3. Jobs and Workflows

A **job** runs code on a schedule or trigger without a human attached. A **workflow** is a job with
multiple **tasks** arranged as a DAG.

### Task types

| Task type | Runs |
|---|---|
| Notebook | A workspace or Repo notebook |
| Python script | A `.py` file from a Repo or cloud storage |
| Python wheel | A packaged, versioned library entry point — the most production-grade option |
| SQL | A query, dashboard refresh, or alert |
| JAR | Compiled Scala/Java |
| dbt | A dbt project (see guide `08`) |
| Delta Live Tables | A declarative pipeline |
| Run Job | Another job, for composition |

### A workflow for this project

```text
          [1 bronze_ingestion]
                   |
                   v
          [2 silver_customers]  [3 silver_products]      <- these two run in PARALLEL
                   \                   /
                    \                 /
                     v               v
                  [4 silver_orders_merge]                 <- idempotent MERGE, section 14
                           |
                           v
                  [5 data_quality_checks]
                           |
              +------------+------------+
              | if all critical = PASS  | if any critical = FAIL
              v                         v
      [6 gold_dimensions]        [8 alert_and_fail]
              |
              v
      [7 gold_fact_sales]  -> 4 rows, 525.00
```

Task settings that matter in production:

| Setting | Why |
|---|---|
| **Retries** (with exponential backoff) | Transient cloud failures are normal; 2–3 retries absorbs them |
| **Timeout** | A hung task must not run for 12 hours and bill for it |
| **Max concurrent runs** | Usually **1** for batch pipelines, to prevent overlapping writers |
| **Depends on** | Builds the DAG |
| **Run if** | `all_success`, `at_least_one_failed`, `all_done` — enables failure branches |
| **Parameters** | `processing_date`, `environment` passed to widgets |
| **Notifications** | On start / success / failure / duration-exceeded |
| **Job cluster vs shared cluster** | Job clusters for isolation; a shared job cluster across tasks saves warm-up time |

> **Set `max concurrent runs = 1`** for this pipeline. Two simultaneous runs writing the same
> silver table is exactly the scenario that makes idempotency and ACID matter — and it is better
> to prevent it than to survive it.

---

## 4. DBFS, mounts and storage access

### DBFS

The Databricks File System is a filesystem abstraction layered over object storage. Paths look
like `/dbfs/...` from shell commands and `dbfs:/...` from Spark.

```python
dbutils.fs.ls("dbfs:/FileStore/")
dbutils.fs.mkdirs("dbfs:/tmp/scratch/")
```

The **DBFS root** is workspace-scoped storage. It is convenient and is **not** where production
data belongs: it has weaker access controls, it is tied to the workspace lifecycle, and it is easy
to leave data there by accident.

### Mounts (the older pattern)

A mount makes an ADLS path appear under `/mnt/<mount-name>/`:

```python
# Conceptual only - credentials come from a secret scope, never from code
configs = {
    "fs.azure.account.auth.type": "OAuth",
    "fs.azure.account.oauth.provider.type":
        "org.apache.hadoop.fs.azurebfs.oauth2.ClientCredsTokenProvider",
    "fs.azure.account.oauth2.client.id":
        dbutils.secrets.get(scope="<key-vault-scope-name>", key="<client-id-secret-name>"),
    "fs.azure.account.oauth2.client.secret":
        dbutils.secrets.get(scope="<key-vault-scope-name>", key="<client-secret-secret-name>"),
    "fs.azure.account.oauth2.client.endpoint":
        dbutils.secrets.get(scope="<key-vault-scope-name>", key="<tenant-endpoint-secret-name>"),
}

dbutils.fs.mount(
    source="abfss://<container>@<storage-account-name>.dfs.core.windows.net/",
    mount_point="/mnt/<mount-name>",
    extra_configs=configs,
)
```

After mounting, paths are pleasantly short:

```python
df = spark.read.format("delta").load("/mnt/<mount-name>/silver/orders")
```

**But mounts have a serious drawback:** the mount's credential applies to *everyone* who can use
the cluster. A user who should only read `gold` can read `raw` through the mount, because the mount
authenticates as the mount's identity, not as the user. That is why mounts are discouraged in
favour of direct `abfss://` access with credential passthrough, or Unity Catalog **external
locations**.

### Direct `abfss://` access — the preferred pattern

```python
BRONZE_ORDERS = "abfss://bronze@<storage-account-name>.dfs.core.windows.net/customer_sales/orders"
SILVER_ORDERS = "abfss://silver@<storage-account-name>.dfs.core.windows.net/customer_sales/orders"
GOLD_FACT     = "abfss://gold@<storage-account-name>.dfs.core.windows.net/customer_sales/fact_sales"

bronze_orders = spark.read.format("delta").load(BRONZE_ORDERS)
```

No mount, no shared credential, and access is governed by the identity actually running the code.

| Approach | Access control | Verdict |
|---|---|---|
| DBFS root | Workspace-wide, weak | Scratch only |
| Mount | Mount's credential for everyone | Legacy; avoid for new work |
| Direct `abfss://` + managed identity / passthrough | Per-identity RBAC | ✅ Good |
| **Unity Catalog external location + table** | **Centrally governed, auditable, with lineage** | ✅ Best |

**How this maps to the project:** `src/config.py` already centralises `RAW_DIR`, `BRONZE_DIR`,
`SILVER_DIR`, `GOLD_DIR`, `QUALITY_RESULTS_DIR` and `LOGS_DIR`. In Databricks these become
`abfss://` strings (or Unity Catalog table names) **and nothing else in the codebase changes.**

---

## 5. Unity Catalog at a concept level

Unity Catalog (UC) is Databricks' centralised governance layer: one place to define data objects,
permissions, and lineage across all workspaces in an account.

### The three-level namespace

```text
<catalog>.<schema>.<table>

prod_sales.silver.orders
prod_sales.gold.fact_sales
dev_sales.bronze.customers
```

| Level | Analogy | Typical use |
|---|---|---|
| **Metastore** | The account-level root | One per region, shared by workspaces |
| **Catalog** | A database server | Environment (`dev_sales`, `prod_sales`) or domain |
| **Schema** | A database/namespace | Medallion layer (`bronze`, `silver`, `gold`) |
| **Table / View / Volume / Function** | The object | `orders`, `fact_sales` |

### Managed vs external tables

| | **Managed table** | **External table** |
|---|---|---|
| Storage location | Chosen and owned by UC | **A path you specify** |
| `DROP TABLE` | **Deletes the data** | Drops the metadata only; files remain |
| Best for | New tables with no external consumers | Existing lake data, or data read by other tools |

### Why UC matters

| Capability | Benefit |
|---|---|
| One permission model across workspaces | Grant once, applies everywhere |
| `GRANT`/`REVOKE` in standard SQL | Familiar and auditable |
| **Automatic column-level lineage** | Trace `fact_sales.amount` back to `orders.csv` without manual mapping |
| **Row filters and column masks** | Show analysts `region` but mask `customer_name` |
| Audit logs | Who queried what, when |
| Data discovery / search | Find the table before rebuilding it badly |
| **Delta Sharing** | Share live data externally without copying it |

```sql
-- Conceptual grants for this project
GRANT USAGE ON CATALOG prod_sales TO `data-analysts`;
GRANT USAGE ON SCHEMA prod_sales.gold TO `data-analysts`;
GRANT SELECT ON TABLE prod_sales.gold.fact_sales   TO `data-analysts`;
GRANT SELECT ON TABLE prod_sales.gold.dim_customer TO `data-analysts`;

-- Analysts explicitly do NOT get bronze or raw
GRANT ALL PRIVILEGES ON SCHEMA prod_sales.silver TO `data-engineers`;
```

This is the Databricks expression of the same least-privilege idea from guide `06`: analysts read
gold; engineers own silver; nobody has standing access to everything.

### Storage credentials and external locations

```text
Storage credential  = a managed identity UC can use to reach storage
        |
External location   = (storage credential) + (an abfss:// path)
        |
External table      = a table defined over a path inside that external location
```

Permissions are granted on the **external location**, so "who can create tables over the silver
container" becomes a single, auditable grant rather than a sprawl of mount configurations.

---

## 6. Repos and Git integration

Databricks Repos provide a Git working copy inside the workspace.

### Why this is non-negotiable for production

| Without Git | With Repos |
|---|---|
| Notebooks edited live in production | Changes go through a branch and a pull request |
| No meaningful history | Full commit history and blame |
| No code review | Review before merge |
| No tests | CI runs `pytest` on every PR |
| "Who changed this and why?" is unanswerable | `git log` answers it |
| Promotion is copy-paste | Promotion is a merge plus a deployment |

### A workable branching model

```text
main          ------o----------------o----------------o------>   (deployed to prod)
                     \              /                /
feature/silver-merge  o----o----o--/                /
                                                   /
hotfix/quality-threshold  ------------------o-----/
```

- `main` is always deployable; production jobs point at it.
- Feature branches per change; PR required to merge.
- CI on PR: lint, unit tests, and ideally a smoke run against sample data.
- Tag releases so you can state exactly which code produced a given output.

### Notebook source format

Databricks notebooks can be stored as **source files** (`.py` with `# COMMAND ----------`
separators) rather than opaque JSON. **Always choose source format** — it makes diffs readable and
code review possible.

```python
# Databricks notebook source
# MAGIC %md
# MAGIC # 04 - Silver orders (idempotent MERGE)

# COMMAND ----------

from delta.tables import DeltaTable
from pyspark.sql.functions import col, row_number
from pyspark.sql import Window

# COMMAND ----------

dbutils.widgets.text("processing_date", "2026-03-01")
processing_date = dbutils.widgets.get("processing_date")
```

**How this maps to the project:** `../customer-sales-lakehouse/notebooks/01_bronze_ingestion.py`
through `07_data_quality_checks.py` are already **`.py` files**, not `.ipynb`. That is deliberate
and it is exactly the format Databricks Repos wants. The repository is already shaped for
professional deployment.

---

## 7. Monitoring and run history

### Where to look

| Surface | Shows |
|---|---|
| **Job runs list** | Every run: status, duration, trigger, parameters, cluster |
| **Run detail** | Per-task status, start/end, output, error, and the notebook *as executed* |
| **Spark UI** (per cluster) | Jobs, stages, tasks, shuffles, skew — the same UI as guide `05` |
| **Driver logs** | `stdout`, `stderr`, `log4j` — where your Python logging lands |
| **Event log** | Cluster lifecycle: created, resized, terminated, and **why** |
| **Metrics / Ganglia** | CPU, memory, network over the run |
| **Query history** (SQL warehouses) | SQL text, duration, bytes scanned |
| **Audit logs / lineage** (Unity Catalog) | Who accessed what; column-level lineage |

The most valuable artefact is the **notebook snapshot attached to each run**: it shows the actual
cell outputs from that execution. When someone asks "what did the pipeline see on 1 March?", the
snapshot answers it.

### Logging that survives

```python
import logging

logger = logging.getLogger("customer_sales")
logger.setLevel(logging.INFO)

logger.info("Bronze orders rows read: %s", bronze_orders.count())
logger.info("Silver orders rows after merge: %s", silver_orders.count())
logger.warning("Quality check FAILED: %s failed_record_count=%s", check_name, failed_count)
```

Logger output goes to the driver logs, which diagnostic settings ship to Log Analytics (guide
`06`). `print()` statements end up in notebook output only and are far harder to query or alert on.

### Failing loudly

```python
summary = run_quality_checks(spark, silver_orders, customers, products)

critical = ["duplicate_order_ids", "invalid_customer_refs"]
failures = summary.filter(
    (col("check_status") == "FAIL") & (col("check_name").isin(critical))
).collect()

if failures:
    details = ", ".join(f"{r.check_name}={r.failed_record_count}" for r in failures)
    raise ValueError(f"Critical data quality failures: {details}")
```

A pipeline that writes bad data and reports success is worse than one that fails. **Make failure
visible, specific and actionable** — the message should name the check and the count so the
on-call engineer knows what to do before opening a single notebook.

**How this maps to the project:** `src/logging_utils.py` provides the logger;
`notebooks/07_data_quality_checks.py` is where this gate belongs; `config/dev.yml` and
`config/prod.yml` decide which checks are critical in each environment.

---

## 8. What Delta Lake actually is

### The one-sentence definition

> **Delta Lake is Parquet files plus a transaction log**, and that log is what turns a folder of
> files into a table with ACID guarantees.

That is genuinely the whole idea. The data is ordinary Parquet — readable by anything. The magic
lives in a `_delta_log` directory that records, as an ordered sequence of commits, which files are
part of the table right now.

### The problems it solves

Plain Parquet on a data lake has four serious gaps. Every one of them bites in production.

| Problem with plain Parquet | What goes wrong | How Delta fixes it |
|---|---|---|
| **No atomicity** | A job writing 500 files crashes after 300. Readers now see a half-written table and report wrong numbers | A commit is atomic: readers see *all* of it or *none* of it |
| **No isolation** | A reader queries while a writer overwrites. Rows vanish mid-query or the job fails with "file not found" | Snapshot isolation: readers get a consistent version |
| **No updates or deletes** | "Correct order 105's amount" or "delete this customer (GDPR)" means rewriting the whole table by hand | `UPDATE`, `DELETE`, `MERGE INTO` |
| **No schema enforcement** | A job writes `amount` as a string; the table is now a mix of types and queries fail weeks later | Writes that violate the schema are **rejected** |
| **No history** | A bad load overwrote good data. There is no "undo" | **Time travel** and `RESTORE` |

### Physical layout

```text
abfss://<container>@<storage-account-name>.dfs.core.windows.net/silver/orders/
├── _delta_log/
│   ├── 00000000000000000000.json      <- commit 0: initial write
│   ├── 00000000000000000001.json      <- commit 1: a MERGE
│   ├── 00000000000000000002.json      <- commit 2: another MERGE
│   ├── ...
│   ├── 00000000000000000010.checkpoint.parquet   <- every 10 commits by default
│   └── _last_checkpoint
├── part-00000-a1b2c3d4-....snappy.parquet
├── part-00000-e5f6a7b8-....snappy.parquet
└── part-00000-c9d0e1f2-....snappy.parquet
```

Two things are worth pausing on:

1. The data files are **plain Parquet**. If Delta vanished tomorrow, your data is not trapped.
2. **Files are never edited in place.** An "update" writes new files and records, in the log, that
   the old ones are no longer part of the table. This is what makes atomic commits and time travel
   possible — and it is why `VACUUM` exists (section 17).

### Delta is open

Delta Lake is an open-source project (under the Linux Foundation), not a proprietary Databricks
format. You can use it in open-source Spark with the `delta-spark` package, which means
**`../customer-sales-lakehouse` can use Delta locally on your laptop** — no cloud account
required:

```bash
pip install delta-spark
```

```python
from pyspark.sql import SparkSession
from delta import configure_spark_with_delta_pip

builder = (
    SparkSession.builder
    .appName("customer-sales-lakehouse")
    .master("local[*]")
    .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
    .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog")
    .config("spark.sql.shuffle.partitions", "4")
)

spark = configure_spark_with_delta_pip(builder).getOrCreate()
```

On Databricks none of this configuration is needed — Delta is the default table format.

---

## 9. The transaction log in detail

The `_delta_log` directory **is** the table definition. Understanding it removes all the mystery.

### Commits are JSON files

Each commit is a numbered JSON file containing one JSON object per line — a set of **actions**.

```json
{"commitInfo":{"timestamp":1772323200000,"operation":"WRITE","operationParameters":{"mode":"Overwrite"},"operationMetrics":{"numFiles":"1","numOutputRows":"7"}}}
{"protocol":{"minReaderVersion":1,"minWriterVersion":2}}
{"metaData":{"id":"<table-uuid>","format":{"provider":"parquet"},"schemaString":"{...}","partitionColumns":[],"configuration":{}}}
{"add":{"path":"part-00000-a1b2c3d4-....snappy.parquet","size":2048,"modificationTime":1772323200000,"dataChange":true,"stats":"{\"numRecords\":7,\"minValues\":{\"order_id\":101},\"maxValues\":{\"order_id\":107}}"}}
```

### Action types

| Action | Meaning |
|---|---|
| `metaData` | Schema, partition columns, table properties |
| `protocol` | Minimum reader/writer versions required |
| `add` | **A file is now part of the table** (with size and column statistics) |
| `remove` | **A file is no longer part of the table** (tombstone; the file still exists on disk) |
| `commitInfo` | Audit information: operation, timestamp, user, metrics |
| `txn` | Idempotency marker for streaming writes |

### Reading a Delta table — the algorithm

```text
1. Read _last_checkpoint to find the most recent checkpoint, if any.
2. Load that checkpoint (a Parquet file summarising state up to commit N).
3. Replay commits N+1, N+2, ... in order.
4. Apply every `add` and `remove`.
5. The result is the exact set of Parquet files that constitute the current version.
6. Read only those files. Any other file in the directory is invisible.
```

That final point deserves emphasis: **a Parquet file sitting in the folder but absent from the log
is not part of the table.** A half-finished write leaves orphan files that readers simply never
see. That is atomicity, achieved without locks or coordination servers.

### Column statistics and data skipping

Each `add` action records per-file min/max values and null counts for the leading columns. A query
filtering `order_id = 104` can discard any file whose recorded range excludes 104 **without
opening it**. This is **data skipping**, and it is why Z-ordering (section 17) is effective.

### Checkpoints

Replaying 50,000 JSON commits to open a table would be painfully slow. Every 10 commits by default,
Delta writes a **checkpoint**: a Parquet file holding the complete state at that version. Readers
start from the latest checkpoint and replay only the commits after it.

### Inspecting the log

```python
from delta.tables import DeltaTable

delta_table = DeltaTable.forPath(spark, SILVER_ORDERS)
delta_table.history().select(
    "version", "timestamp", "operation", "operationMetrics"
).show(truncate=False)
```

```text
+-------+-----------------------+---------+--------------------------------------------------------------+
|version|timestamp              |operation|operationMetrics                                              |
+-------+-----------------------+---------+--------------------------------------------------------------+
|2      |2026-03-03 02:00:11.000|MERGE    |{numTargetRowsInserted -> 0, numTargetRowsUpdated -> 1, ...}  |
|1      |2026-03-02 02:00:09.000|MERGE    |{numTargetRowsInserted -> 1, numTargetRowsUpdated -> 0, ...}  |
|0      |2026-03-01 02:00:07.000|WRITE    |{numFiles -> 1, numOutputRows -> 7}                           |
+-------+-----------------------+---------+--------------------------------------------------------------+
```

```sql
DESCRIBE HISTORY delta.`abfss://<container>@<storage-account-name>.dfs.core.windows.net/silver/orders`;
DESCRIBE DETAIL  delta.`abfss://<container>@<storage-account-name>.dfs.core.windows.net/silver/orders`;
```

`DESCRIBE HISTORY` is a free, built-in audit log of every change ever made to the table. Plain
Parquet gives you nothing comparable.

---

## 10. ACID guarantees and optimistic concurrency

### What ACID means for a lake table

| Property | Delta's guarantee | Why you care |
|---|---|---|
| **Atomicity** | A write commits entirely or not at all | A crashed job never leaves a half-written table |
| **Consistency** | Schema is enforced; constraints can be declared | A bad write is rejected, not absorbed |
| **Isolation** | Readers see a consistent snapshot; concurrent writers are serialised | A dashboard running during a load gets correct numbers |
| **Durability** | Once committed, it is in the log in durable object storage | Committed means committed |

### Snapshot isolation in practice

```text
t0  Reader A opens the table  -> pinned to version 5
t1  Writer commits version 6 (MERGE)
t2  Reader A is still reading  -> STILL SEES VERSION 5, consistently
t3  Reader B opens the table  -> sees version 6
```

Reader A never sees a mixture of versions and never fails with "file not found" because a file was
deleted underneath it. With plain Parquet and an `overwrite`, both of those failures are routine.

### Optimistic concurrency control

Delta does not lock the table. Instead, each writer:

```text
1. Reads the current version (say, version 5) and the files it contains.
2. Does its work and writes new Parquet files.
3. Attempts to commit as version 6.
      |
      +-- If 00000000000000000006.json does not exist -> commit succeeds (atomic file creation).
      |
      +-- If it already exists, another writer won the race:
             a. Read what that writer changed.
             b. If the changes do not conflict (different files/partitions),
                retry the commit as version 7.
             c. If they do conflict, throw ConcurrentModificationException.
```

This is "optimistic" because it assumes conflicts are rare — which is true for most pipelines.

| Conflict scenario | Outcome |
|---|---|
| Two writers append to different partitions | **Both succeed** (auto-retry resolves it) |
| Two writers `MERGE` on disjoint key ranges | Usually both succeed |
| Two writers `MERGE` the **same** keys | One fails with a concurrency exception |
| One writer overwrites while another merges | Likely conflict |

Practical guidance:

- Set **max concurrent runs = 1** on batch jobs. Prevention beats recovery.
- Partition tables so independent writers touch independent partitions.
- Catch and retry concurrency exceptions in long-running services.
- **Never** have two different pipelines own writes to the same table. One table, one writer.

---

## 11. Schema enforcement and schema evolution

### Enforcement: rejecting bad writes

Delta compares the incoming DataFrame's schema with the table's schema and **fails the write** if
they are incompatible.

```python
# Table column: amount DECIMAL(10,2)
# Incoming DataFrame: amount STRING

bad_df.write.format("delta").mode("append").save(SILVER_ORDERS)
```

```text
AnalysisException: Failed to merge fields 'amount' and 'amount'.
Failed to merge incompatible data types DecimalType(10,2) and StringType
```

This is the behaviour you want. With plain Parquet, that write **succeeds**, and three weeks later
a report fails with a type error that nobody can trace back to its cause. Delta turns a silent
future problem into a loud present one.

Rules Delta enforces on write:

- Column **names** must match (case-insensitive by default, but it preserves case).
- Column **types** must match or be safely upcastable.
- **Extra columns** in the source are rejected unless you opt in to evolution.
- **Missing** columns are allowed on append (they become null) but not on strict overwrite modes.

### Evolution: adding columns deliberately

Schemas legitimately change — a new `discount_amount` column appears upstream. Delta lets you
accept that explicitly:

```python
(new_orders
   .write
   .format("delta")
   .mode("append")
   .option("mergeSchema", "true")       # <- explicit opt-in
   .save(SILVER_ORDERS))
```

Existing rows get `NULL` for the new column. The schema change is recorded in the transaction log
as a new `metaData` action, so it is auditable: you can see exactly which version added the column.

```python
# For MERGE operations
spark.conf.set("spark.databricks.delta.schema.autoMerge.enabled", "true")
```

```sql
ALTER TABLE <catalog>.<schema>.orders ADD COLUMNS (discount_amount DECIMAL(10,2));
ALTER TABLE <catalog>.<schema>.orders SET TBLPROPERTIES ('delta.minReaderVersion' = '2');
```

| Change | Supported by `mergeSchema`? |
|---|---|
| Add a new column | ✅ Yes |
| Widen a type (e.g. `int` → `long`) | ✅ Usually |
| Reorder columns | ✅ Yes (matched by name) |
| **Drop** a column | ❌ Needs `overwriteSchema` or column mapping |
| **Rename** a column | ❌ Needs column mapping enabled |
| **Narrow** a type (`long` → `int`) | ❌ Would lose data |

The important discipline: **`mergeSchema` is opt-in per write.** Never set it globally. Making
schema change a conscious decision is most of the value.

### Overwriting the schema entirely

```python
(rebuilt_df
   .write
   .format("delta")
   .mode("overwrite")
   .option("overwriteSchema", "true")
   .save(GOLD_FACT))
```

Use this for a deliberate, reviewed redesign of a gold table — never as a reflex to make an error
go away.

### Constraints

```sql
ALTER TABLE <catalog>.silver.orders
  ADD CONSTRAINT valid_status CHECK (status IN ('Completed', 'Cancelled', 'Pending'));

ALTER TABLE <catalog>.silver.orders
  ADD CONSTRAINT positive_quantity CHECK (quantity > 0);

ALTER TABLE <catalog>.silver.orders
  ADD CONSTRAINT order_id_not_null CHECK (order_id IS NOT NULL);
```

A write violating a `CHECK` constraint fails. This moves part of your quality framework from
"detect after the fact" to "prevent at write time" — the strongest form of data quality there is.

Note the deliberate design choice for this project: `status IN ('Completed','Cancelled','Pending')`
can be a hard constraint because an invalid status means the source system is broken. But *missing
`amount`* must **not** be a constraint, because order 105's null amount is a legitimate state we
want to **load and report**, not reject.

---

## 12. Parquet vs Delta comparison

| Capability | **Parquet** | **Delta Lake** |
|---|---|---|
| Underlying file format | Parquet | **Parquet** (identical) |
| Columnar + compressed | ✅ | ✅ |
| Schema stored with data | ✅ | ✅ |
| Transaction log | ❌ | ✅ `_delta_log` |
| **Atomic writes** | ❌ Partial writes are visible | ✅ All-or-nothing |
| **Reader isolation** | ❌ Readers can see torn state | ✅ Snapshot isolation |
| **UPDATE / DELETE** | ❌ Rewrite the whole dataset manually | ✅ Native SQL and Python APIs |
| **MERGE / upsert** | ❌ Hand-rolled, error-prone | ✅ `MERGE INTO` |
| **Schema enforcement** | ❌ Mismatches silently accepted | ✅ Bad writes rejected |
| **Schema evolution** | Manual and risky | ✅ `mergeSchema`, auditable |
| **Time travel** | ❌ | ✅ `VERSION AS OF`, `TIMESTAMP AS OF` |
| **Rollback** | ❌ Restore from backup, if you have one | ✅ `RESTORE TABLE` |
| **Audit history** | ❌ | ✅ `DESCRIBE HISTORY` |
| **Data skipping statistics** | Row-group level only | ✅ File-level stats in the log |
| **Z-ordering** | ❌ | ✅ `OPTIMIZE ... ZORDER BY` |
| **Small-file compaction** | Manual rewrite | ✅ `OPTIMIZE` |
| **Streaming source/sink** | Awkward | ✅ First-class |
| **CHECK constraints** | ❌ | ✅ |
| **Change Data Feed** | ❌ | ✅ (opt-in) |
| Readable by non-Spark tools | ✅ Universally | ✅ Via Delta readers; underlying Parquet also readable |
| Storage overhead | None | Small (`_delta_log` + retained old files until `VACUUM`) |
| Write latency | Slightly lower | Slightly higher (log commit) |

**When plain Parquet is still fine:** a true write-once archive, or an interchange format handed to
a system that cannot read Delta. **Everywhere else in a lakehouse, Delta wins** — and because the
data files are still Parquet, you give up almost nothing to get there.

---

## 13. Writing and reading Delta with PySpark

### Paths used throughout this guide

```python
# All placeholders - substitute your own values, never commit real account names
BRONZE_CUSTOMERS = "abfss://<container>@<storage-account-name>.dfs.core.windows.net/bronze/customers"
BRONZE_PRODUCTS  = "abfss://<container>@<storage-account-name>.dfs.core.windows.net/bronze/products"
BRONZE_ORDERS    = "abfss://<container>@<storage-account-name>.dfs.core.windows.net/bronze/orders"

SILVER_CUSTOMERS = "abfss://<container>@<storage-account-name>.dfs.core.windows.net/silver/customers"
SILVER_PRODUCTS  = "abfss://<container>@<storage-account-name>.dfs.core.windows.net/silver/products"
SILVER_ORDERS    = "abfss://<container>@<storage-account-name>.dfs.core.windows.net/silver/orders"

GOLD_DIM_CUSTOMER = "abfss://<container>@<storage-account-name>.dfs.core.windows.net/gold/dim_customer"
GOLD_DIM_PRODUCT  = "abfss://<container>@<storage-account-name>.dfs.core.windows.net/gold/dim_product"
GOLD_DIM_DATE     = "abfss://<container>@<storage-account-name>.dfs.core.windows.net/gold/dim_date"
GOLD_FACT_SALES   = "abfss://<container>@<storage-account-name>.dfs.core.windows.net/gold/fact_sales"

QUALITY_RESULTS   = "abfss://<container>@<storage-account-name>.dfs.core.windows.net/quality/check_results"

# Mount-style equivalents (legacy pattern, shown for completeness)
# SILVER_ORDERS = "/mnt/<mount-name>/silver/orders"
```

### Basic write and read

```python
# Write
(df.write
   .format("delta")
   .mode("overwrite")
   .save(BRONZE_ORDERS))

# Read
bronze_orders = spark.read.format("delta").load(BRONZE_ORDERS)
```

The **only** difference from the project's current Parquet code is `.format("delta")` instead of
`.parquet(...)`. That is the entire migration for a simple write.

### Registering a table name

```python
(df.write
   .format("delta")
   .mode("overwrite")
   .option("path", SILVER_ORDERS)
   .saveAsTable("<catalog>.silver.orders"))
```

```sql
CREATE TABLE IF NOT EXISTS <catalog>.silver.orders
USING DELTA
LOCATION 'abfss://<container>@<storage-account-name>.dfs.core.windows.net/silver/orders';
```

Once registered, you can use plain SQL everywhere — and the `sql/` folder in
`../customer-sales-lakehouse` becomes directly runnable against the lakehouse.

### Path-based SQL without registration

```sql
SELECT COUNT(*) AS rows, SUM(amount) AS revenue
FROM delta.`abfss://<container>@<storage-account-name>.dfs.core.windows.net/gold/fact_sales`;
```

```text
rows | revenue
-----+--------
   4 |  525.00
```

### UPDATE and DELETE

```python
from delta.tables import DeltaTable
from pyspark.sql.functions import col, lit

silver = DeltaTable.forPath(spark, SILVER_ORDERS)

# Correct a single order's amount
silver.update(
    condition=col("order_id") == 105,
    set={"amount": lit("25.00").cast("decimal(10,2)"),
         "data_quality_status": lit("VALID")}
)

# Remove a customer's rows (e.g. an erasure request)
silver.delete(col("customer_id") == 99)
```

```sql
UPDATE <catalog>.silver.orders SET amount = 25.00 WHERE order_id = 105;
DELETE FROM <catalog>.silver.orders WHERE customer_id = 99;
```

Both are atomic, logged, and reversible with time travel. Neither is possible with plain Parquet
without rewriting the dataset by hand and hoping nothing reads it mid-rewrite.

### Partitioning, as before

```python
(fact_sales
   .write
   .format("delta")
   .mode("overwrite")
   .partitionBy("order_year", "order_month")
   .save(GOLD_FACT_SALES))
```

The same judgement from guide `05` applies: **do not partition this project's four-row fact
table.** Delta's file statistics and Z-ordering handle small-to-medium tables better than
partitioning does, and over-partitioning is still the most common self-inflicted wound.

---

## 14. MERGE INTO in depth

`MERGE INTO` is the single most important Delta feature for a data engineer. It is the operation
that makes a pipeline **idempotent**.

### The problem it solves

Your pipeline runs nightly. The source sends **all orders updated since yesterday**. Some are new;
some are revisions of orders you already have. What do you do?

| Naive approach | What breaks |
|---|---|
| `mode("append")` | Revised orders appear **twice**. `order_id` is no longer unique. Revenue inflates on every rerun |
| `mode("overwrite")` with only today's data | You **delete all history** — yesterday's orders disappear |
| `mode("overwrite")` with a full re-read of everything | Works, but you must re-read the entire source every night. At scale this is impossible |
| **`MERGE INTO`** | Updates what exists, inserts what does not, leaves everything else alone. **Rerunnable** |

### Anatomy

```sql
MERGE INTO target AS t
USING source AS s
  ON  t.<key> = s.<key>
WHEN MATCHED AND <extra condition> THEN UPDATE SET ...
WHEN MATCHED THEN DELETE
WHEN NOT MATCHED THEN INSERT ...
WHEN NOT MATCHED BY SOURCE THEN DELETE
```

| Clause | Fires when |
|---|---|
| `WHEN MATCHED` | The key exists in **both** target and source |
| `WHEN NOT MATCHED` | The key exists in **source only** → insert |
| `WHEN NOT MATCHED BY SOURCE` | The key exists in **target only** → typically a soft delete |

### The fully worked idempotent MERGE into silver orders

This is the centrepiece of the guide. Read every line.

#### Step 1 — deduplicate the source **before** merging

```python
from pyspark.sql import Window
from pyspark.sql.functions import col, row_number

def deduplicate_orders(orders_df):
    """Keep exactly one row per order_id: the one with the latest updated_at.

    This MUST run before the MERGE. Delta raises an error if more than one
    source row matches the same target row, because the result would be
    ambiguous - and ambiguity is precisely what we are eliminating.
    """
    dedup_window = Window.partitionBy("order_id").orderBy(col("updated_at").desc())
    return (orders_df
            .withColumn("row_num", row_number().over(dedup_window))
            .filter(col("row_num") == 1)
            .drop("row_num"))


bronze_orders = spark.read.format("delta").load(BRONZE_ORDERS)
source_orders = deduplicate_orders(bronze_orders)

print(f"bronze rows: {bronze_orders.count()}   deduplicated source rows: {source_orders.count()}")
# bronze rows: 8   deduplicated source rows: 7
```

This is the **exact same `row_number()` window** used in
`../customer-sales-lakehouse/src/transformations.py`. Delta does not replace that logic — it
consumes its output.

> **Why the pre-dedup is mandatory.** If two source rows share `order_id = 107`, the `MERGE` has
> two candidate values for one target row. Delta refuses:
> `UnsupportedOperationException: Cannot perform Merge as multiple source rows matched ... the same
> target row`. That error is Delta protecting you from a non-deterministic result.

#### Step 2 — the MERGE itself

```python
from delta.tables import DeltaTable

def merge_silver_orders(spark, source_orders, silver_path: str):
    """Idempotent upsert of orders into the silver Delta table, keyed on order_id.

    - Existing order_id with a NEWER updated_at  -> UPDATE
    - Existing order_id with an OLDER/EQUAL one  -> left untouched (late-arriving data is safe)
    - New order_id                                -> INSERT
    """
    if not DeltaTable.isDeltaTable(spark, silver_path):
        # First ever run: create the table
        source_orders.write.format("delta").mode("overwrite").save(silver_path)
        return

    target = DeltaTable.forPath(spark, silver_path)

    (target.alias("t")
        .merge(
            source=source_orders.alias("s"),
            condition="t.order_id = s.order_id",
        )
        .whenMatchedUpdate(
            condition="s.updated_at > t.updated_at",      # <- the idempotency guard
            set={
                "customer_id":         "s.customer_id",
                "product_id":          "s.product_id",
                "order_date":          "s.order_date",
                "quantity":            "s.quantity",
                "amount":              "s.amount",
                "status":              "s.status",
                "updated_at":          "s.updated_at",
                "data_quality_status": "s.data_quality_status",
            },
        )
        .whenNotMatchedInsertAll()
        .execute())
```

The equivalent SQL:

```sql
MERGE INTO delta.`abfss://<container>@<storage-account-name>.dfs.core.windows.net/silver/orders` AS t
USING source_orders AS s
  ON t.order_id = s.order_id

WHEN MATCHED AND s.updated_at > t.updated_at THEN
  UPDATE SET
    t.customer_id         = s.customer_id,
    t.product_id          = s.product_id,
    t.order_date          = s.order_date,
    t.quantity            = s.quantity,
    t.amount              = s.amount,
    t.status              = s.status,
    t.updated_at          = s.updated_at,
    t.data_quality_status = s.data_quality_status

WHEN NOT MATCHED THEN
  INSERT *;
```

### Why this makes reruns safe — precisely

**Idempotent** means: *running the operation N times produces exactly the same result as running it
once.* Here is why this `MERGE` qualifies, stated exactly:

1. **The key is deterministic.** `t.order_id = s.order_id` identifies at most one target row,
   because the pre-dedup guarantees one source row per `order_id` and the merge keeps one target
   row per `order_id`.
2. **The update is guarded by `s.updated_at > t.updated_at`.** On a rerun with identical input,
   `s.updated_at` equals `t.updated_at`, the condition is **false**, and **no update is applied**.
   The table is byte-for-byte unchanged in content. No duplication is possible.
3. **The insert cannot duplicate.** `WHEN NOT MATCHED` only fires for keys absent from the target.
   After the first run, those keys are present, so the clause never fires again for the same data.
4. **Late-arriving stale data is rejected automatically.** If a delayed file redelivers an *older*
   version of order 104, `s.updated_at > t.updated_at` is false and the good current row survives.
   With `append` or a naive overwrite, that stale row would corrupt the table.
5. **The whole operation is atomic.** It is a single Delta commit. A crash halfway through leaves
   the table at its previous version — never half-merged.

Contrast with the project's current local behaviour:

| Operation | Run it twice | Result |
|---|---|---|
| `mode("append")` | 7 rows → 14 rows | ❌ **Revenue doubles to 1050.00** |
| `mode("overwrite")` (full source) | 7 rows → 7 rows | ✅ Correct, but requires re-reading everything, every time |
| `MERGE` with the `updated_at` guard | 7 rows → **7 rows, unchanged** | ✅ Correct **and** incremental |

> This is the difference between a pipeline you can safely rerun at 03:00 after a failure, and one
> where a rerun means a manual cleanup and an apologetic email to finance.

### How the duplicate order 107 is handled

Trace it precisely through the whole flow.

**Input — `orders.csv` contains two rows for 107, with identical values and identical
`updated_at`:**

```text
107,1,1003,2026-02-15,1,200.00,Completed,2026-02-15 08:00:00
107,1,1003,2026-02-15,1,200.00,Completed,2026-02-15 08:00:00
```

**Stage 1 — bronze (8 rows).** Bronze is a faithful record of what the source sent. **Both rows are
preserved deliberately.** If you deduplicate in bronze you destroy the evidence that the source
system has a duplicate-emission bug, and the `duplicate_order_ids` quality check has nothing to
count.

```python
spark.read.format("delta").load(BRONZE_ORDERS).filter(col("order_id") == 107).show()
```

```text
+--------+-----------+----------+----------+--------+------+---------+-------------------+
|order_id|customer_id|product_id|order_date|quantity|amount|   status|         updated_at|
+--------+-----------+----------+----------+--------+------+---------+-------------------+
|     107|          1|      1003|2026-02-15|       1|200.00|Completed|2026-02-15 08:00:00|
|     107|          1|      1003|2026-02-15|       1|200.00|Completed|2026-02-15 08:00:00|
+--------+-----------+----------+----------+--------+------+---------+-------------------+
```

**Stage 2 — `deduplicate_orders()` (8 → 7 rows).** The window
`partitionBy("order_id").orderBy(col("updated_at").desc())` numbers the two 107 rows 1 and 2;
`filter(row_num == 1)` keeps one.

Because both rows have the **same** `updated_at` **and identical values**, the choice between them
is arbitrary and completely harmless — either one produces the same table. (In production, add a
tie-breaker such as `ingestion_timestamp` or `source_file` so the choice is *reproducible* as well
as harmless.)

**Stage 3 — the MERGE.** The source now contains exactly one row for 107, so:

- **First run:** the silver table is empty → `WHEN NOT MATCHED` → **INSERT** one row for 107.
- **Second run (identical file):** 107 matches → `s.updated_at > t.updated_at` is
  `2026-02-15 08:00:00 > 2026-02-15 08:00:00` = **false** → **no update, no insert, nothing
  changes.**
- **If a genuine revision later arrives** with `updated_at = 2026-02-20 09:00:00` and
  `status = Cancelled`, the condition is **true** → **UPDATE**, and 107 correctly leaves
  `fact_sales`, reducing revenue to 325.00. The pipeline tracks reality without any manual
  intervention.

```python
spark.read.format("delta").load(SILVER_ORDERS).filter(col("order_id") == 107).show()
```

```text
+--------+-----------+----------+----------+--------+------+---------+-------------------+-------------------+
|order_id|customer_id|product_id|order_date|quantity|amount|   status|         updated_at|data_quality_status|
+--------+-----------+----------+----------+--------+------+---------+-------------------+-------------------+
|     107|          1|      1003|2026-02-15|       1|200.00|Completed|2026-02-15 08:00:00|              VALID|
+--------+-----------+----------+----------+--------+------+---------+-------------------+-------------------+
```

**One row. Every time. However many times you run it.** That single row carries 200.00 of the
525.00 total.

**Stage 4 — the quality check still reports the problem.** Critically, deduplicating in silver does
**not** hide the defect, because the check compares bronze with silver:

```python
duplicate_count = bronze_orders.count() - source_orders.count()   # 8 - 7 = 1
```

```text
+------------------------+------------+-------------------+---------------+
|check_name              |check_status|failed_record_count|processing_date|
+------------------------+------------+-------------------+---------------+
|duplicate_order_ids     |FAIL        |1                  |2026-03-01     |
|missing_mandatory_fields|FAIL        |1                  |2026-03-01     |
|invalid_customer_refs   |FAIL        |1                  |2026-03-01     |
|invalid_product_refs    |PASS        |0                  |2026-03-01     |
|invalid_order_status    |PASS        |0                  |2026-03-01     |
+------------------------+------------+-------------------+---------------+
```

**Fix the data, report the defect.** Silently cleaning data without reporting it is how source
system bugs survive for years.

### Other MERGE patterns worth knowing

**Deduplicating inserts only (no updates) — classic streaming idempotency:**

```python
(target.alias("t")
   .merge(source.alias("s"), "t.order_id = s.order_id")
   .whenNotMatchedInsertAll()
   .execute())
```

**Soft delete for records that disappeared from the source:**

```python
(target.alias("t")
   .merge(source.alias("s"), "t.order_id = s.order_id")
   .whenMatchedUpdateAll()
   .whenNotMatchedInsertAll()
   .whenNotMatchedBySourceUpdate(set={"is_deleted": "true"})
   .execute())
```

**SCD Type 2 on `dim_customer`** (conceptually — a customer moves from `North` to `West`):

```sql
MERGE INTO dim_customer AS t
USING customer_updates AS s
  ON t.customer_id = s.customer_id AND t.is_current = true

WHEN MATCHED AND (t.region <> s.region OR t.customer_name <> s.customer_name) THEN
  UPDATE SET t.is_current = false, t.valid_to = s.effective_date

WHEN NOT MATCHED THEN
  INSERT (customer_id, customer_name, region, valid_from, valid_to, is_current)
  VALUES (s.customer_id, s.customer_name, s.region, s.effective_date, NULL, true);
```

(An SCD2 implementation needs two passes — expire then insert the new version — but the shape above
is the part that matters here.)

### MERGE performance notes

| Technique | Effect |
|---|---|
| Add partition predicates to the `ON` clause | Prunes target files; often the single biggest win |
| Pre-deduplicate and pre-filter the source | Less data to match, fewer files rewritten |
| Z-order the target on the merge key | Co-locates matching keys into fewer files |
| Keep the source small | `MERGE` cost scales with files touched, not table size |
| Run `OPTIMIZE` regularly | Fewer, larger files to rewrite |

A `MERGE` rewrites every **file** containing an affected row — not just the row. On a poorly
organised table, updating ten rows can rewrite gigabytes. That is why section 17 matters.

---

## 15. The bronze to silver to gold design in Delta terms

### Layer responsibilities

| Layer | Delta table | Write mode | Content | Rows |
|---|---|---|---|---|
| **Bronze** | `bronze.customers` / `.products` / `.orders` | `append` (or `MERGE` on a batch key) | Raw, faithful, plus ingestion metadata. **Defects preserved** | orders: **8** |
| **Silver** | `silver.customers` / `.products` / `.orders` | **`MERGE`** on the business key | Standardised, typed, deduplicated, quality-flagged | orders: **7** |
| **Gold** | `gold.dim_customer` / `dim_product` / `dim_date` / `fact_sales` | `overwrite` (small) or `MERGE` (large) | Star schema, business rules applied | fact: **4** = **525.00** |
| **Quality** | `quality.check_results` | `append` | One row per check per run | 5 per run |

### Bronze

```python
from pyspark.sql.functions import current_timestamp, lit, input_file_name

bronze_orders = (
    raw_orders
    .withColumn("ingestion_timestamp", current_timestamp())
    .withColumn("source_file", input_file_name())
    .withColumn("source_system", lit("sales_crm"))
    .withColumn("processing_date", lit(processing_date).cast("date"))
)

(bronze_orders.write
   .format("delta")
   .mode("append")
   .option("mergeSchema", "false")        # explicit: reject unexpected schema drift
   .save(BRONZE_ORDERS))
```

**Bronze rules:**

- Append only. Never update, never clean.
- Keep **both** rows of order 107, the null `amount` on 105, and the orphan customer 99 on 106.
- Add metadata; change nothing else.
- Schema enforcement **on**: if the source adds or retypes a column, you want to know immediately.

Bronze is your replay buffer. If silver logic turns out to be wrong, you rebuild silver from bronze
rather than going back to the source system — which may no longer have the data.

### Silver

```python
from pyspark.sql.functions import col, trim, initcap

# 1. Standardise (unchanged from src/transformations.py)
orders_std = bronze_orders.withColumn("status", initcap(trim(col("status"))))

customers_std = (spark.read.format("delta").load(BRONZE_CUSTOMERS)
                 .withColumn("customer_name", initcap(trim(col("customer_name"))))
                 .withColumn("region", initcap(trim(col("region")))))

products_std = (spark.read.format("delta").load(BRONZE_PRODUCTS)
                .withColumn("product_name", initcap(trim(col("product_name"))))
                .withColumn("category", initcap(trim(col("category")))))

# 2. Deduplicate (unchanged)
orders_dedup = deduplicate_orders(orders_std)

# 3. Quality flag (unchanged)
orders_flagged = add_data_quality_status(orders_dedup, customers_std, products_std)

# 4. Idempotent MERGE  <- THE ONLY NEW STEP
merge_silver_orders(spark, orders_flagged, SILVER_ORDERS)
```

```python
spark.read.format("delta").load(SILVER_ORDERS) \
     .select("order_id", "status", "amount", "data_quality_status") \
     .orderBy("order_id").show()
```

```text
+--------+---------+------+-------------------+
|order_id|   status|amount|data_quality_status|
+--------+---------+------+-------------------+
|     101|Completed|200.00|              VALID|
|     102|Completed| 50.00|              VALID|
|     103|Cancelled| 50.00|              VALID|
|     104|Completed| 75.00|              VALID|
|     105|  Pending|  NULL|     MISSING_AMOUNT|
|     106|Completed| 50.00|   INVALID_CUSTOMER|
|     107|Completed|200.00|              VALID|
+--------+---------+------+-------------------+
```

Silver keeps **all seven** orders, including the flagged ones. Silver is "clean and complete", not
"clean and filtered". The business rules that *exclude* rows belong in gold.

Customers and products merge on their natural keys:

```python
customers_target = DeltaTable.forPath(spark, SILVER_CUSTOMERS)
(customers_target.alias("t")
   .merge(customers_std.alias("s"), "t.customer_id = s.customer_id")
   .whenMatchedUpdateAll()
   .whenNotMatchedInsertAll()
   .execute())
```

### Gold

```python
from pyspark.sql.functions import col, date_format

silver_orders = spark.read.format("delta").load(SILVER_ORDERS)
silver_customers = spark.read.format("delta").load(SILVER_CUSTOMERS)
silver_products = spark.read.format("delta").load(SILVER_PRODUCTS)

dim_customer = (silver_customers
                .withColumn("customer_key", col("customer_id"))
                .select("customer_key", "customer_id", "customer_name",
                        "region", "customer_created_date"))

dim_product = (silver_products
               .withColumn("product_key", col("product_id"))
               .select("product_key", "product_id", "product_name", "category", "unit_price"))

fact_sales = (
    silver_orders
    .filter(col("data_quality_status") == "VALID")    # excludes 105 and 106
    .filter(col("status") == "Completed")             # excludes 103
    .join(dim_customer, silver_orders.customer_id == dim_customer.customer_key, "inner")
    .join(dim_product,  silver_orders.product_id  == dim_product.product_key,  "inner")
    .withColumn("date_key", date_format(col("order_date"), "yyyyMMdd").cast("int"))
    .select("order_id", "customer_key", "product_key", "date_key",
            "order_date", "quantity", "amount")
)

for df, path in [(dim_customer, GOLD_DIM_CUSTOMER),
                 (dim_product, GOLD_DIM_PRODUCT),
                 (dim_date, GOLD_DIM_DATE),
                 (fact_sales, GOLD_FACT_SALES)]:
    df.write.format("delta").mode("overwrite").save(path)
```

```sql
SELECT COUNT(*) AS fact_rows, SUM(amount) AS recognised_revenue
FROM delta.`abfss://<container>@<storage-account-name>.dfs.core.windows.net/gold/fact_sales`;
```

```text
fact_rows | recognised_revenue
----------+-------------------
        4 |             525.00
```

The business rule, restated because it is the point of the whole project:

> **`fact_sales` contains only valid, de-duplicated, `Completed` orders.** Recognised revenue is
> **525.00** = 101 (200.00) + 102 (50.00) + 104 (75.00) + 107 (200.00). Order 103 is excluded
> (Cancelled), 105 is excluded (Pending with a missing amount), 106 is excluded (invalid customer
> reference), and the duplicate 107 row is removed.

### Why gold can use `overwrite` while silver uses `MERGE`

| | Silver | Gold |
|---|---|---|
| Source of truth | Accumulated across many loads | **Fully derivable from silver** |
| Cost of rebuilding | High — you would need every historical file | Low — recompute from silver |
| Needs incremental upsert | **Yes** | Not at this scale |
| Write mode | **`MERGE`** | `overwrite` (switch to `MERGE` when rebuild cost grows) |

Because gold is a deterministic function of silver, overwriting it is itself idempotent: the same
silver input always produces the same four rows and the same 525.00.

### Quality results

```python
(quality_summary
   .write
   .format("delta")
   .mode("append")
   .save(QUALITY_RESULTS))
```

Appending (never overwriting) gives you a **trend over time**: is `duplicate_order_ids` always 1,
or did it jump to 4,000 last Tuesday? A single run's result tells you about today; the history
tells you whether the source system is degrading.

---

## 16. Time travel: audit and rollback

Because Delta never edits files in place and records every commit, you can query **any previous
version** of a table.

### Querying an earlier version

```python
# By version number
v3 = spark.read.format("delta").option("versionAsOf", 3).load(GOLD_FACT_SALES)

# By timestamp
yesterday = (spark.read.format("delta")
             .option("timestampAsOf", "2026-03-01 02:00:00")
             .load(GOLD_FACT_SALES))
```

```sql
SELECT * FROM delta.`abfss://<container>@<storage-account-name>.dfs.core.windows.net/gold/fact_sales`
VERSION AS OF 3;

SELECT * FROM delta.`abfss://<container>@<storage-account-name>.dfs.core.windows.net/gold/fact_sales`
TIMESTAMP AS OF '2026-03-01 02:00:00';
```

### A worked example: a bad load, audited and rolled back

**Scenario.** On 2 March, a careless edit removes **both** gold filters — the
`data_quality_status == "VALID"` filter and the `status == "Completed"` filter — so the nightly run
publishes every silver order into `fact_sales`. Finance opens the dashboard, sees revenue of
**625.00** instead of 525.00, and asks what happened.

#### 1. Look at the history

```python
from delta.tables import DeltaTable

fact = DeltaTable.forPath(spark, GOLD_FACT_SALES)
fact.history().select("version", "timestamp", "operation", "operationMetrics").show(truncate=False)
```

```text
+-------+-----------------------+---------+------------------------------------------+
|version|timestamp              |operation|operationMetrics                          |
+-------+-----------------------+---------+------------------------------------------+
|2      |2026-03-02 02:00:14.000|WRITE    |{numFiles -> 1, numOutputRows -> 7}       |   <- the bad load
|1      |2026-03-01 02:00:11.000|WRITE    |{numFiles -> 1, numOutputRows -> 4}       |   <- known good
|0      |2026-02-28 02:00:09.000|WRITE    |{numFiles -> 1, numOutputRows -> 4}       |
+-------+-----------------------+---------+------------------------------------------+
```

The row count jumped from 4 to 7. That alone localises the problem to version 2 in seconds.

#### 2. Compare the two versions side by side

```python
from pyspark.sql.functions import sum as spark_sum, count, lit

current = spark.read.format("delta").load(GOLD_FACT_SALES)
previous = spark.read.format("delta").option("versionAsOf", 1).load(GOLD_FACT_SALES)

comparison = (
    previous.agg(lit("v1 (good)").alias("version"),
                 count("*").alias("rows"),
                 spark_sum("amount").alias("revenue"))
    .union(
        current.agg(lit("v2 (bad)").alias("version"),
                    count("*").alias("rows"),
                    spark_sum("amount").alias("revenue"))
    )
)
comparison.show()
```

```text
+---------+----+-------+
|  version|rows|revenue|
+---------+----+-------+
|v1 (good)|   4| 525.00|
| v2 (bad)|   7| 625.00|
+---------+----+-------+
```

```sql
-- Exactly which rows were wrongly added?
SELECT order_id, amount
FROM delta.`abfss://<container>@<storage-account-name>.dfs.core.windows.net/gold/fact_sales` VERSION AS OF 2
EXCEPT
SELECT order_id, amount
FROM delta.`abfss://<container>@<storage-account-name>.dfs.core.windows.net/gold/fact_sales` VERSION AS OF 1;
```

```text
order_id | amount
---------+-------
     103 |  50.00     <- Cancelled, should be excluded
     105 |   NULL     <- Pending with a missing amount
     106 |  50.00     <- invalid customer reference 99
```

Three rows, each explainable in one sentence, and together they account for the whole discrepancy:
`525.00 + 50.00 + 0.00 + 50.00 = 625.00`. That is a root-cause analysis completed in minutes, with
evidence, before a single line of code is read.

#### 3. Roll back

```sql
RESTORE TABLE delta.`abfss://<container>@<storage-account-name>.dfs.core.windows.net/gold/fact_sales`
TO VERSION AS OF 1;
```

```python
fact = DeltaTable.forPath(spark, GOLD_FACT_SALES)
fact.restoreToVersion(1)
# or: fact.restoreToTimestamp("2026-03-01 02:00:00")
```

```python
spark.read.format("delta").load(GOLD_FACT_SALES) \
     .agg(spark_sum("amount").alias("recognised_revenue")).show()
```

```text
+------------------+
|recognised_revenue|
+------------------+
|            525.00|
+------------------+
```

Revenue is back to **525.00** — in seconds, with no backup restore, no support ticket, and no data
loss.

#### 4. Note what `RESTORE` actually does

`RESTORE` does **not** erase history. It creates a **new version (3)** whose content equals
version 1:

```text
+-------+-----------------------+---------+
|version|timestamp              |operation|
+-------+-----------------------+---------+
|3      |2026-03-02 09:15:22.000|RESTORE  |   <- content identical to v1
|2      |2026-03-02 02:00:14.000|WRITE    |   <- the bad load, still auditable
|1      |2026-03-01 02:00:11.000|WRITE    |
|0      |2026-02-28 02:00:09.000|WRITE    |
+-------+-----------------------+---------+
```

The bad load remains inspectable at version 2 for the post-incident review. This is exactly what
an auditor wants: **you fixed it, and you can still prove what went wrong.**

#### 5. Then fix the actual cause

Time travel fixed the *symptom*. The *cause* was a missing filter and an absent test. Add both:

```python
assert fact_sales.count() == 4, f"Expected 4 fact rows, got {fact_sales.count()}"
total = fact_sales.agg(spark_sum("amount")).collect()[0][0]
assert total == Decimal("525.00"), f"Expected 525.00 recognised revenue, got {total}"
```

A regression test in `tests/` that asserts 525.00 would have failed the build before deployment.

### Other uses of time travel

| Use | How |
|---|---|
| Reproduce a report exactly as it looked last month | `TIMESTAMP AS OF` |
| Debug "the number changed and nobody knows why" | Diff two versions |
| Rerun a model on the exact training snapshot | `VERSION AS OF` |
| Audit who changed what and when | `DESCRIBE HISTORY` |
| Recover from an accidental `DELETE` | `RESTORE` |

### The limit you must know

**Time travel only works while the old files still exist.** `VACUUM` deletes files no longer
referenced by the current version once they are older than the retention threshold. After that,
those versions are unreadable. See section 17 — this is the single most important caveat in this
guide.

---

## 17. OPTIMIZE, Z-ordering and VACUUM

### The small-file problem

Every write and every `MERGE` creates new files. Frequent small writes produce thousands of tiny
files, and then:

- Every query pays per-file open and metadata overhead.
- The driver spends longer planning than executors spend reading.
- `MERGE` gets slower because more files must be examined and rewritten.

### `OPTIMIZE` — compaction

```sql
OPTIMIZE delta.`abfss://<container>@<storage-account-name>.dfs.core.windows.net/silver/orders`;

-- Restrict to recent partitions on a large table
OPTIMIZE delta.`abfss://<container>@<storage-account-name>.dfs.core.windows.net/gold/fact_sales`
WHERE order_year = 2026;
```

```python
from delta.tables import DeltaTable
DeltaTable.forPath(spark, SILVER_ORDERS).optimize().executeCompaction()
```

`OPTIMIZE` rewrites many small files into fewer large ones (target ~1 GB on Databricks). It is a
normal Delta commit: atomic, logged, and safe to run while readers are querying.

```text
Before: 847 files averaging 1.2 MB
After :   6 files averaging 170 MB
```

### Z-ordering — co-locating related data

```sql
OPTIMIZE delta.`abfss://<container>@<storage-account-name>.dfs.core.windows.net/silver/orders`
ZORDER BY (order_id);

OPTIMIZE delta.`abfss://<container>@<storage-account-name>.dfs.core.windows.net/gold/fact_sales`
ZORDER BY (customer_key, order_date);
```

Z-ordering sorts data by a space-filling curve so that rows with similar values in the chosen
columns land in the **same files**. Combined with the min/max statistics in the transaction log,
this means a query filtering on those columns can skip most files entirely.

```text
Without Z-order:  order_id values scattered across all 100 files
                  -> filter order_id = 104 must open ~100 files

With Z-order:     each file holds a narrow order_id range
                  -> filter order_id = 104 opens ~1 file
```

| Choose Z-order columns that | Avoid |
|---|---|
| Appear frequently in `WHERE` clauses | Columns never filtered on |
| Have **high cardinality** (`order_id`, `customer_key`) | Very low cardinality (`status`) — use partitioning instead |
| Are used as join or `MERGE` keys | More than ~3–4 columns; effectiveness dilutes fast |

**Partitioning vs Z-ordering:**

| | Partitioning | Z-ordering |
|---|---|---|
| Granularity | Directory | File within the data |
| Cardinality | **Low** (year, month, region) | **High** (IDs, keys) |
| Risk | Too many small partitions | Cost of rewriting during `OPTIMIZE` |
| Changeable later | Hard (rewrite the table) | **Easy — just run `OPTIMIZE` again** |

A good default for a large `fact_sales`: partition by `order_year`/`order_month`, Z-order by
`customer_key`. For this project's four rows, **do neither** — and be able to say why.

### `VACUUM` — and its retention warning

Old files hang around after `OPTIMIZE`, `MERGE`, `UPDATE`, `DELETE` and `RESTORE`. They cost
storage. `VACUUM` deletes files that are no longer referenced by the current version **and** are
older than the retention threshold.

```sql
VACUUM delta.`abfss://<container>@<storage-account-name>.dfs.core.windows.net/silver/orders`;
-- default retention: 168 hours (7 days)

VACUUM delta.`abfss://<container>@<storage-account-name>.dfs.core.windows.net/silver/orders`
RETAIN 720 HOURS;   -- 30 days

-- Preview without deleting - ALWAYS do this first
VACUUM delta.`abfss://<container>@<storage-account-name>.dfs.core.windows.net/silver/orders`
RETAIN 168 HOURS DRY RUN;
```

> ### ⚠️ The retention warning — read this twice
>
> **`VACUUM` permanently and irreversibly destroys your ability to time travel to versions older
> than the retention window.** The transaction log still lists those versions, but the Parquet
> files they reference are gone, so querying them fails.
>
> Two specific dangers:
>
> 1. **`VACUUM ... RETAIN 0 HOURS` destroys all rollback capability instantly.** Databricks blocks
>    retention below 168 hours by default via
>    `spark.databricks.delta.retentionDurationCheck.enabled`. **Do not disable that check** unless
>    you can state exactly why, in writing. Disabling it to "free up space" is how teams discover
>    that their 03:00 rollback plan no longer exists.
> 2. **`VACUUM` can break in-flight long-running readers or concurrent writers** whose files it
>    removes. The retention period exists partly to protect them.
>
> **Rule:** set retention to at least as long as your worst realistic "we noticed the problem N
> days later" window. Seven days is the default; 30 days is a common, defensible choice for gold
> tables. Always run `DRY RUN` first.

### A sensible maintenance schedule

| Operation | Frequency | Scope |
|---|---|---|
| `OPTIMIZE` | Daily or weekly after loads | Hot/recent partitions |
| `OPTIMIZE ... ZORDER BY` | Weekly | Tables with selective point lookups |
| `VACUUM` (≥ 7 days retention) | Weekly | All tables |
| `ANALYZE TABLE ... COMPUTE STATISTICS` | After large loads | Tables with complex joins |
| Review `DESCRIBE HISTORY` | During incidents, and in monthly reviews | All critical tables |

Databricks can handle much of this automatically (auto-compaction, optimized writes, predictive
optimization). Understand the manual commands anyway — when something is slow, you need to know
what the platform is or is not doing on your behalf.

---

## 18. How the local Parquet project maps onto Delta

This is the section to reread before an interview.

### What **changes**

| Aspect | Local Parquet (`../customer-sales-lakehouse` today) | Delta Lake version |
|---|---|---|
| **Write format** | `.write.parquet(path)` | `.write.format("delta").save(path)` |
| **Read format** | `spark.read.parquet(path)` | `spark.read.format("delta").load(path)` |
| **Silver load strategy** | `mode("overwrite")` — full rewrite each run | **`MERGE INTO` on `order_id`** — incremental and idempotent |
| **Rerun safety** | Safe only because the source is re-read in full every time | **Safe by construction**, guarded by `s.updated_at > t.updated_at` |
| **Schema enforcement** | **None** — a type change is silently accepted | **Enforced** — an incompatible write is rejected |
| **Schema evolution** | Implicit and invisible | Explicit via `mergeSchema`, recorded in the log |
| **Atomicity** | A crashed write leaves partial files visible | **Atomic commits** — all or nothing |
| **Reader isolation** | A reader during a write can see torn state | **Snapshot isolation** |
| **Update a single row** | Rewrite the whole dataset by hand | `UPDATE` / `DELETE` / `MERGE` |
| **History / audit** | None (check file timestamps and hope) | **`DESCRIBE HISTORY`** — every operation, metrics, timestamp |
| **Rollback** | Re-run the pipeline and hope the source is unchanged | **`RESTORE TABLE ... TO VERSION AS OF n`** |
| **Reproduce last month's report** | Impossible | `TIMESTAMP AS OF` |
| **File maintenance** | Manual rewrite | `OPTIMIZE`, `ZORDER`, `VACUUM` |
| **Constraints** | Only what your checks catch after the fact | `CHECK` constraints reject bad writes |
| **Paths in `src/config.py`** | `pathlib` paths like `data/silver/orders` | `abfss://<container>@<storage-account-name>.dfs.core.windows.net/silver/orders`, or a Unity Catalog name |
| **Compute** | Local `SparkSession` with `local[*]` | Databricks **job cluster** |
| **Orchestration** | Running notebooks by hand, in order | ADF pipeline or a Databricks **Workflow** |
| **Logging destination** | `LOGS_DIR` on local disk | Driver logs → Log Analytics |

### What stays **exactly the same**

| Aspect | Detail |
|---|---|
| **The medallion architecture** | raw → bronze → silver → gold → reporting SQL. Unchanged |
| **`src/transformations.py`** | Text standardisation, type casting, dedup window, `data_quality_status` — **every line unchanged** |
| **The dedup logic** | `row_number()` over `Window.partitionBy("order_id").orderBy(col("updated_at").desc())`. **Identical.** Delta consumes its output; it does not replace it |
| **`src/quality_checks.py`** | Duplicate order IDs, missing mandatory fields, invalid customer refs, invalid product refs, invalid statuses — **unchanged** |
| **The quality summary schema** | `check_name, check_status, failed_record_count, processing_date` — **unchanged** |
| **The quality results** | 1 duplicate, 1 missing amount, 1 invalid customer ref, **0** invalid product refs, **0** invalid statuses |
| **The star schema** | `dim_customer`, `dim_product`, `dim_date`, `fact_sales` — same grain, same keys |
| **The business rule** | `fact_sales` contains only valid, de-duplicated, `Completed` orders |
| **The row counts** | bronze 8 → silver 7 → fact **4** |
| **🎯 The answer** | **Recognised revenue = 525.00** = 101 (200.00) + 102 (50.00) + 104 (75.00) + 107 (200.00) |
| **The exclusions** | 103 Cancelled, 105 Pending + missing amount, 106 invalid customer, duplicate 107 row removed |
| **The reporting SQL in `sql/`** | Same queries; the `FROM` clause points at Delta tables |
| **The tests in `tests/`** | Same assertions, including `total == Decimal("525.00")` |
| **`config/dev.yml` / `config/prod.yml`** | Same structure; path **values** change, and still no secrets |
| **The PySpark API** | `select`, `filter`, `withColumn`, `join`, `groupBy`, window functions — all identical |

### The one-sentence summary

> **Delta changes the storage guarantees, not the logic.** You gain atomicity, isolation, schema
> enforcement, upserts, audit history and rollback. You keep every transformation, every quality
> check, and the same answer: **525.00**.

### A concrete migration plan

```text
1. Add delta-spark to requirements.txt; configure the local SparkSession for Delta.
2. Change every .parquet(path) write to .format("delta").save(path).
3. Change every spark.read.parquet(path) to spark.read.format("delta").load(path).
4. Run the full pipeline. Assert fact_sales = 4 rows and 525.00. <- unchanged behaviour
5. Replace the silver orders overwrite with merge_silver_orders().
6. Run the pipeline TWICE. Assert silver = 7 rows and fact = 525.00 both times.   <- idempotency
7. Add DESCRIBE HISTORY output to the run log.
8. Add a time-travel regression test: break the gold filters, observe 625.00,
   RESTORE to the previous version, assert 525.00.
9. Add CHECK constraints on status and quantity.
10. Add OPTIMIZE / VACUUM to a weekly maintenance notebook (retention >= 168 hours).
11. Repoint src/config.py at abfss:// paths (or Unity Catalog names) for the cloud run.
12. Deploy as a Databricks Workflow with max concurrent runs = 1.
```

Steps 1–4 are a mechanical, low-risk change you can do in an afternoon. Step 5 is the one that
genuinely upgrades the pipeline, and step 6 is the proof.

### Documenting it

Add a short section to `../customer-sales-lakehouse/docs/` that states:

- The format choice (Parquet locally today, Delta for the cloud target) and the reason.
- The write strategy per layer (append / `MERGE` / overwrite) and why each is correct.
- The idempotency guarantee and the exact condition that provides it.
- The retention policy and the `VACUUM` schedule.
- The reconciliation: 8 raw rows → 7 silver rows → 4 fact rows → **525.00**.

Being able to explain that last line to a non-technical stakeholder is the single most employable
skill in this entire repository.

---

## 19. Hands-on exercises

No answers are provided. Everything below can be done **locally** with `delta-spark` — no cloud
account and no credentials required.

1. **Enable Delta locally.** Add `delta-spark` to `requirements.txt`, configure the SparkSession
   with the Delta extensions, and confirm `spark.version` and the Delta version are compatible.

2. **Convert the pipeline.** Change every write and read in `../customer-sales-lakehouse` from
   Parquet to Delta. Run the full pipeline and assert `fact_sales` has 4 rows totalling exactly
   `Decimal("525.00")`. Confirm no transformation logic changed.

3. **Inspect the log.** Open `data/silver/orders/_delta_log/` and read
   `00000000000000000000.json`. Identify the `metaData`, `add` and `commitInfo` actions and write
   down what each tells you. Find the min/max statistics for `order_id`.

4. **History.** Run `DESCRIBE HISTORY` on the silver orders table and record version, timestamp,
   operation and `operationMetrics` for every commit so far.

5. **Schema enforcement.** Deliberately cast `amount` to a string and append to the silver Delta
   table. Capture the exact error. Then explain in writing what would have happened with plain
   Parquet, and when the problem would have surfaced.

6. **Schema evolution.** Add a `discount_amount DECIMAL(10,2)` column to a new batch and append it
   with `mergeSchema = true`. Confirm existing rows show `NULL`, then find the commit in
   `DESCRIBE HISTORY` that recorded the schema change.

7. **Implement the MERGE.** Write `merge_silver_orders()` exactly as in section 14, including the
   `s.updated_at > t.updated_at` guard and the first-run table creation branch.

8. **Prove idempotency.** Run the silver merge **three times** with the identical source file.
   After each run record the row count, the sum of `amount`, and the table version. Explain why the
   row count never changes and why a new version may or may not be created.

9. **Prove append is not idempotent.** Swap the `MERGE` for `mode("append")` and run twice. Record
   the resulting row count and revenue figure. Write two sentences on why this is dangerous in
   production.

10. **The multiple-match error.** Remove the pre-dedup step and run the `MERGE` with both order 107
    rows in the source. Capture the exact exception. Explain why Delta refuses rather than picking
    one, and why that refusal is a feature.

11. **Trace order 107.** Document its journey in a table: rows in bronze, rows after dedup, rows in
    silver after run 1, after run 2, and its contribution to the 525.00 total.

12. **A genuine revision.** Add a third 107 row with `updated_at = 2026-02-20 09:00:00` and
    `status = Cancelled`. Rerun the pipeline. Confirm silver still has 7 rows, 107 is now
    `Cancelled`, `fact_sales` has 3 rows, and revenue is 325.00. Explain every number.

13. **Stale data rejection.** Send an *older* version of order 104 (`updated_at` earlier than the
    stored value, `amount = 999.00`). Confirm the `MERGE` leaves the table unchanged and revenue is
    still 525.00. Explain exactly which clause prevented the corruption.

14. **Time travel audit.** Deliberately break the gold notebook by removing **both** the
    `data_quality_status = 'VALID'` filter and the `status = 'Completed'` filter. Run it. Confirm
    `fact_sales` now has 7 rows and revenue becomes 625.00. Then use `VERSION AS OF` and `EXCEPT`
    to list precisely which rows were wrongly added, and show they account for the full difference.

15. **Rollback.** `RESTORE` `fact_sales` to the last good version. Confirm revenue is 525.00 again.
    Then show, via `DESCRIBE HISTORY`, that the bad version is still inspectable and explain why
    that matters to an auditor.

16. **Reproduce an old report.** Use `TIMESTAMP AS OF` to run the revenue-by-region query as it
    would have returned yesterday. Confirm the result and explain one business situation where this
    capability is essential.

17. **UPDATE and DELETE.** Update order 105's `amount` to `25.00` and set its
    `data_quality_status` to `VALID`. Recompute `fact_sales` and state the new revenue figure.
    Explain carefully **why it does not change** even though the row is now valid, and what you
    would also have to change for order 105 to be recognised as revenue. Then `RESTORE` the silver
    table and confirm 525.00.

18. **Constraints.** Add `CHECK (status IN ('Completed','Cancelled','Pending'))` and
    `CHECK (quantity > 0)`. Attempt to insert a row with `status = 'Shipped'` and capture the
    error. Explain why a `NOT NULL` constraint on `amount` would be the **wrong** choice for this
    dataset.

19. **OPTIMIZE.** Run the silver merge 30 times to create many small files. Count the Parquet files
    before and after `OPTIMIZE`. Record both numbers.

20. **Z-ordering.** `OPTIMIZE ... ZORDER BY (order_id)` on silver orders. Explain which queries
    benefit, why `status` would be a poor Z-order column, and how Z-ordering differs from
    partitioning.

21. **VACUUM safely.** Run `VACUUM ... RETAIN 168 HOURS DRY RUN` and record what it *would* delete.
    Then write a short policy document: your retention period, your justification, and what you
    would lose if you set `RETAIN 0 HOURS`.

22. **Concurrency.** Set up two notebooks merging into the same silver table at the same time.
    Observe the result (success via retry, or a concurrency exception). Explain optimistic
    concurrency control and why `max concurrent runs = 1` is the right production setting here.

23. **Parquet vs Delta table.** Build the comparison table from section 12 from memory, then check
    it against the guide. For each row where you were wrong, write one sentence explaining the
    concept.

24. **Workflow design.** Design a Databricks Workflow for this project with 8 tasks, dependencies,
    retries, timeouts, a quality gate with a failure branch, and notifications. Specify cluster type
    and size for each task and justify the choices on cost grounds.

25. **Write the migration doc.** Produce the "what changes / what stays the same" document for
    `../customer-sales-lakehouse/docs/`, ending with the reconciliation 8 → 7 → 4 → **525.00**.
    Then explain it out loud in under two minutes, as if to a finance stakeholder who believes the
    answer should be 825.00.

---

> **Where this fits:** guide `05` gave you the PySpark engine, guide `06` gave you the Azure
> platform, and this guide gave you the table format that makes a data lake behave like a database.
> Guide `08` moves on to dbt, orchestration and monitoring — turning this pipeline into something
> that runs itself, tells you when it breaks, and can be deployed by someone who is not you.


