# 06 — Azure Data Engineering

> **Scope:** this guide covers the Azure services a data engineer actually touches, explained
> conceptually and mapped onto the companion project **`../customer-sales-lakehouse`**.
>
> **Nothing here requires an Azure subscription to read.** There are no credentials, no tenant IDs,
> no connection strings, and no client data anywhere in this guide. Every identifier is a
> placeholder such as `<storage-account-name>`, `<container>`, `<workspace-url>` or
> `<key-vault-name>`.
>
> ⚠️ **Azure changes.** Service names, portal layouts, pricing tiers and default behaviours are
> revised frequently (Synapse and Microsoft Fabric are a live example). Treat this guide as a
> **mental model**, and always confirm specifics against the current official Microsoft
> documentation before you build or sit an exam.

---

## Table of contents

1. [Azure fundamentals for data engineers](#1-azure-fundamentals-for-data-engineers)
2. [Naming conventions, tags and cost basics](#2-naming-conventions-tags-and-cost-basics)
3. [Storage accounts and ADLS Gen2](#3-storage-accounts-and-adls-gen2)
4. [Azure Data Factory in depth](#4-azure-data-factory-in-depth)
5. [Copy Activity vs Mapping Data Flows](#5-copy-activity-vs-mapping-data-flows)
6. [Azure Databricks positioning](#6-azure-databricks-positioning)
7. [Synapse Analytics concepts](#7-synapse-analytics-concepts)
8. [Orchestration and monitoring](#8-orchestration-and-monitoring)
9. [Security concepts](#9-security-concepts)
10. [Governance with Microsoft Purview](#10-governance-with-microsoft-purview)
11. [A logical Azure architecture for the customer sales project](#11-a-logical-azure-architecture-for-the-customer-sales-project)
12. [Local pipeline step to Azure service mapping](#12-local-pipeline-step-to-azure-service-mapping)
13. [Cost awareness in practice](#13-cost-awareness-in-practice)
14. [Azure learning checklist](#14-azure-learning-checklist)
15. [Exercises](#15-exercises)

---

## 1. Azure fundamentals for data engineers

You cannot reason about permissions, billing or deployments until the hierarchy is clear in your
head. Learn this first; everything else hangs off it.

### The resource hierarchy

```text
Microsoft Entra ID tenant            <- identity boundary (your organisation)
        |
        +-- Management group(s)      <- optional, for policy at scale
                |
                +-- Subscription     <- BILLING and quota boundary
                        |
                        +-- Resource group   <- LIFECYCLE and permission boundary
                                |
                                +-- Resource (storage account, data factory, ...)
```

| Level | What it really is | Why a data engineer cares |
|---|---|---|
| **Tenant** | A Microsoft Entra ID directory — the identity boundary for an organisation | Every user, group, service principal and managed identity lives here |
| **Management group** | A container for subscriptions, used to apply policy consistently | Governance teams enforce "no public storage accounts" here |
| **Subscription** | The billing container; also where quotas live | Your costs roll up here; `dev` and `prod` are usually separate subscriptions |
| **Resource group** | A logical folder for resources that share a lifecycle | You delete a whole environment by deleting its resource group |
| **Resource** | An actual service instance | The storage account, data factory, Databricks workspace |

### Practical rules

1. **One resource group per environment per workload.** Resources that are created and destroyed
   together belong together. If `rg-salespipeline-dev` is deleted, everything dev goes with it and
   nothing production is touched.
2. **Separate subscriptions (or at minimum separate resource groups) for dev / test / prod.** A
   shared subscription means a dev experiment can exhaust a production quota.
3. **Resource groups are not a security-free zone.** RBAC applied at the resource group level is
   inherited by every resource in it — convenient and dangerous in equal measure.
4. **Moving resources between groups is possible but not universally supported.** Decide the layout
   before you build.

### Regions

A region is a physical datacentre cluster (e.g. `uksouth`, `westeurope`, `eastus`).

| Consideration | Why it matters |
|---|---|
| **Latency** | Keep compute in the same region as its storage. Cross-region reads are slow |
| **Egress cost** | Data leaving a region is billed. Same-region traffic generally is not |
| **Data residency** | Regulation may require data to stay in a country or economic area |
| **Service availability** | Not every service or SKU exists in every region |
| **Paired regions** | Azure pairs regions for geo-redundancy and staged platform updates |

> **Rule of thumb:** put the storage account, Data Factory, Databricks workspace and Synapse
> workspace for one pipeline in **the same region**. Cross-region analytics pipelines are a
> reliable way to produce a surprising invoice.

### Resource providers and the control plane

Two different "planes" exist, and confusing them wastes hours:

| Plane | Handles | Permission model |
|---|---|---|
| **Control plane** (Azure Resource Manager) | Creating, configuring, deleting resources | Azure RBAC roles like `Contributor` |
| **Data plane** | Reading and writing the actual data inside a resource | Data-specific roles like `Storage Blob Data Reader` |

**The classic trap:** you are `Owner` of a storage account and still get `403` reading a file.
`Owner` is a control-plane role; reading blobs needs a **data-plane** role such as
`Storage Blob Data Contributor`. Being able to delete the account does not imply being able to
read it. This catches almost everyone once.

---

## 2. Naming conventions, tags and cost basics

### Why naming matters

In a real estate you will see 400 resources in one subscription. A name is the only context you
get in a bill, an alert, or a log line. Inconsistent naming makes cost attribution impossible.

### A workable convention

```text
<resource-type-abbrev>-<workload>-<environment>-<region-abbrev>-<instance>
```

| Resource | Example (fictional) | Notes |
|---|---|---|
| Resource group | `rg-salespipeline-dev-uks` | Hyphens allowed |
| Data Factory | `adf-salespipeline-dev-uks` | Globally unique |
| Databricks workspace | `dbw-salespipeline-dev-uks` | |
| Key Vault | `kv-salespipeline-dev-uks` | Globally unique, 3–24 chars |
| Synapse workspace | `syn-salespipeline-dev-uks` | Globally unique |
| Log Analytics workspace | `log-salespipeline-dev-uks` | |
| **Storage account** | `stsalespipelinedevuks` | **No hyphens, lowercase, 3–24 chars, globally unique** |

Storage accounts are the awkward one: they must be lowercase alphanumeric only and globally unique
across all of Azure, which is why they look so unreadable. Pick a short workload abbreviation.

Rules worth adopting:

- **Lowercase everywhere** — some resource types are case-sensitive, most are not; consistency wins.
- **Environment in the name, always** — `dev`, `tst`, `prd`. Ambiguity here causes production
  incidents.
- **Never put secrets, client names or personal data in a resource name.** Names appear in logs,
  bills, support tickets and URLs.
- **Agree the convention before the first resource is created.** Renaming is usually a recreate.

### Tags

Tags are key/value metadata attached to resources and resource groups. They are the backbone of
cost reporting and ownership.

| Tag key | Example value | Purpose |
|---|---|---|
| `environment` | `dev` | Filter cost and policy by environment |
| `owner` | `data-platform-team` | Who to contact — a team, never a personal email |
| `costCentre` | `CC-1234` | Charge-back to a budget |
| `project` | `customer-sales-lakehouse` | Group spend by initiative |
| `dataClassification` | `internal` | Signals handling requirements |
| `managedBy` | `terraform` | Warns humans not to edit by hand |

Important behaviours:

- Tags are **not** inherited by child resources by default. Azure Policy can enforce or append
  them.
- Tags appear in **Cost Management**, which is how you answer "what does this pipeline cost?"
- Tags are **not a security control**. Anyone who can read the resource can read its tags, so never
  put sensitive values in them.

### Cost basics

| Service | What you are billed for | Biggest lever |
|---|---|---|
| **ADLS Gen2 / Blob** | GB stored per month, per-10,000 transactions, egress | Access tier + lifecycle rules |
| **Data Factory** | Pipeline activity runs, Data Flow **compute hours**, integration runtime hours | Avoid idle Data Flow clusters; prefer Copy where possible |
| **Databricks** | DBUs (compute units) × VM cost × time | **Auto-termination**, right-sized clusters, job clusters |
| **Synapse dedicated SQL pool** | Provisioned DWU per hour, **whether or not you query it** | **Pause it** when idle |
| **Synapse serverless SQL** | **TB of data scanned** per query | Parquet + partition pruning |
| **Log Analytics** | GB ingested and retention period | Sample noisy logs; set retention deliberately |

The three mistakes that cause 90% of surprise bills:

1. **An all-purpose Databricks cluster left running overnight** (or over a weekend).
2. **A dedicated SQL pool provisioned and never paused.**
3. **Serverless SQL queries doing `SELECT *` over an unpartitioned CSV lake** — every query scans
   everything, and you pay per terabyte scanned.

Set a **budget with alerts** in Cost Management on day one, even for a learning subscription.

---

## 3. Storage accounts and ADLS Gen2

### What a storage account is

A storage account is the top-level container for Azure Storage. It holds one or more **services**:

| Service | Purpose |
|---|---|
| Blob (containers) | Unstructured object storage |
| **Data Lake Gen2** | Blob **plus a hierarchical namespace** — the analytics option |
| File shares | SMB/NFS network shares |
| Queues | Simple message queues |
| Tables | Key-value NoSQL |

For a lakehouse, you want **ADLS Gen2**, which is not a separate product: it is a storage account
with the **hierarchical namespace (HNS)** feature enabled at creation time.

### The hierarchical namespace — the whole point

Plain Blob storage is a **flat key-value store**. A "folder" is an illusion created by `/`
characters in the blob name.

```text
Plain Blob (flat):
  key = "bronze/orders/part-00000.parquet"   <- one long string, no real folders

ADLS Gen2 (hierarchical):
  bronze/            <- a real directory object
    orders/          <- a real directory object
      part-00000.parquet
```

Why this matters enormously for analytics:

| Operation | Flat Blob | ADLS Gen2 (HNS) |
|---|---|---|
| Rename a "folder" of 100,000 files | Copy + delete every object — minutes to hours, not atomic | **Single atomic metadata operation** |
| Delete a directory tree | Enumerate and delete each object | One operation |
| Set permissions on a folder | Per-object, or container-wide only | **POSIX-style ACLs on directories**, inherited |
| List a directory | Scan and filter by prefix | True directory listing |
| Spark job commit | Rename-based commit is slow and risky | Fast and atomic |

That last row is the killer argument. Spark writes output to a temporary location and then
**renames** it into place to make the write atomic. On flat Blob storage a rename is a full copy;
on ADLS Gen2 it is a metadata flip. This is the difference between a 20-minute and a 20-second
write for a large table.

### ADLS Gen2 URI format

```text
abfss://<container>@<storage-account-name>.dfs.core.windows.net/<path>
```

- `abfss` — Azure Blob File System, **secure** (TLS). Always use `abfss`, never `abfs`.
- `<container>` — the filesystem (sometimes called the container).
- `.dfs.core.windows.net` — the Data Lake endpoint. (The `.blob.` endpoint also exists for the
  same account; for analytics you want `.dfs.`.)

Examples for this project (all placeholders):

```text
abfss://raw@<storage-account-name>.dfs.core.windows.net/customer_sales/orders/
abfss://bronze@<storage-account-name>.dfs.core.windows.net/customer_sales/orders/
abfss://silver@<storage-account-name>.dfs.core.windows.net/customer_sales/orders/
abfss://gold@<storage-account-name>.dfs.core.windows.net/customer_sales/fact_sales/
```

### Container layout: one container per layer, or one container with folders?

| Approach | Pros | Cons |
|---|---|---|
| **One container per medallion layer** (`raw`, `bronze`, `silver`, `gold`) | Clean RBAC boundaries — grant analysts `gold` only; easy lifecycle rules per layer | More containers to manage |
| **One container, folders per layer** | Simpler; one mount point | Permissions must use ACLs; coarse RBAC leaks everything |

For a learning project and most production estates, **one container per layer** is the better
default because it makes "analysts can read gold but not raw" a one-line RBAC assignment.

Recommended path convention inside a container:

```text
<container>/<domain>/<entity>/<partition columns>/
gold/customer_sales/fact_sales/order_year=2026/order_month=01/
```

Including the **domain** (`customer_sales`) leaves room for a second subject area later without
reorganising everything.

### Access tiers

| Tier | Storage cost | Access cost | Minimum retention | Retrieval |
|---|---|---|---|---|
| **Hot** | Highest | Lowest | None | Instant |
| **Cool** | Lower | Higher | 30 days | Instant |
| **Cold** | Lower still | Higher still | 90 days | Instant |
| **Archive** | Lowest | Highest | 180 days | **Rehydrate: hours** |

Guidance for a medallion lakehouse:

- `raw` — Hot for the current period; move to Cool after ~30 days; Archive after a year if you must
  retain it for compliance but will realistically never read it.
- `bronze` — Hot while it is being reprocessed; Cool thereafter.
- `silver` / `gold` — **Hot**. These are queried constantly; cool-tier read charges would dwarf the
  storage saving.

**Warning:** archived blobs **cannot be read** until rehydrated, which takes hours. Never archive
anything a pipeline might need to reread. Automate tiering with **lifecycle management policies**
rather than by hand.

### Redundancy options

| Option | Copies | Where | Survives |
|---|---|---|---|
| **LRS** (locally redundant) | 3 | One datacentre | Disk/rack failure |
| **ZRS** (zone redundant) | 3 | Three availability zones in one region | A zone outage |
| **GRS** (geo redundant) | 6 | Primary + paired region | A regional disaster |
| **GZRS** | 6 | ZRS primary + paired region | Zone **and** regional failure |
| **RA-GRS / RA-GZRS** | 6 | As above, with **read access** to the secondary | As above, and you can read during an outage |

Practical defaults: **LRS for dev** (cheapest, and the data is reproducible); **ZRS or GZRS for
production gold data**. Remember raw data that is reproducible from source does not need the same
protection as a gold table nobody can rebuild.

> Redundancy is **not backup**. Replication faithfully copies your mistakes. If a bad pipeline run
> overwrites gold with garbage, GRS gives you six copies of garbage. You need **soft delete**,
> **versioning**, and — the best answer — **Delta Lake time travel** (guide `07`).

### Protective features worth enabling

| Feature | Protects against |
|---|---|
| **Soft delete** (blob and container) | Accidental deletion — recoverable for N days |
| **Blob versioning** | Accidental overwrite — previous versions retained |
| **Point-in-time restore** | Bulk accidental change |
| **Immutable storage / legal hold** | Regulatory retention requirements |
| **Change feed** | Audit log of every data-plane change |

**How this maps to the project:** locally, `src/config.py` defines `RAW_DIR`, `BRONZE_DIR`,
`SILVER_DIR`, `GOLD_DIR`, `QUALITY_RESULTS_DIR` and `LOGS_DIR` with `pathlib`. In Azure those same
six constants become six `abfss://` base paths. **Because every path in the project is read from
config, moving from laptop to cloud is a config change, not a rewrite.** That is the entire reason
`config.py` exists, and it is worth saying out loud in an interview.

---

## 4. Azure Data Factory in depth

Azure Data Factory (ADF) is Azure's managed **orchestration and data-movement** service. The
mental model that unlocks it:

> **ADF is a scheduler and a data mover. It is not, primarily, a transformation engine.**

It is excellent at "get data from A to B on a schedule, reliably, with retries and alerting". For
heavy transformation you call Databricks or Synapse Spark from ADF.

### The object model

```text
Linked Service   = a CONNECTION  ("how do I reach this system?")
      |
Dataset          = a SHAPE       ("which file/table/folder, in what format?")
      |
Activity         = a VERB        ("copy it", "run this notebook", "look this up")
      |
Pipeline         = a SEQUENCE    (activities with dependencies = a DAG)
      |
Trigger          = a WHEN        ("every night at 02:00", "when a file lands")
```

Learn these five words properly and ADF stops being confusing.

### Linked services

A linked service is a **connection definition**: endpoint plus authentication method.

Typical linked services for this project:

| Linked service | Connects to | Preferred auth |
|---|---|---|
| `ls_adls_datalake` | ADLS Gen2 storage account | **Managed identity** |
| `ls_databricks` | Azure Databricks workspace | Managed identity / Key Vault-backed token |
| `ls_keyvault` | Azure Key Vault | Managed identity |
| `ls_sql_source` | A source SQL database | Managed identity, or Key Vault secret |
| `ls_sftp_source` | An external SFTP drop | Key Vault secret |

```json
{
  "name": "ls_adls_datalake",
  "properties": {
    "type": "AzureBlobFS",
    "typeProperties": {
      "url": "https://<storage-account-name>.dfs.core.windows.net"
    },
    "connectVia": { "referenceName": "AutoResolveIntegrationRuntime", "type": "IntegrationRuntimeReference" }
  }
}
```

Notice there is **no key, no secret, no connection string** in that definition. With a managed
identity, ADF authenticates as itself and Azure checks RBAC. This is the target state — see
section 9.

### Datasets

A dataset points at a **specific shape of data** through a linked service: a folder, a file
pattern, a table, and the format.

```json
{
  "name": "ds_raw_orders_csv",
  "properties": {
    "linkedServiceName": { "referenceName": "ls_adls_datalake", "type": "LinkedServiceReference" },
    "type": "DelimitedText",
    "parameters": {
      "fileName": { "type": "string", "defaultValue": "orders.csv" }
    },
    "typeProperties": {
      "location": {
        "type": "AzureBlobFSLocation",
        "fileSystem": "raw",
        "folderPath": "customer_sales",
        "fileName": { "value": "@dataset().fileName", "type": "Expression" }
      },
      "columnDelimiter": ",",
      "firstRowAsHeader": true
    }
  }
}
```

**Parameterise datasets.** One parameterised `ds_raw_csv` dataset can serve `customers.csv`,
`products.csv` and `orders.csv`. Three near-identical datasets is a smell; by the time you have
fifty tables it is unmaintainable.

### Activities

| Activity | What it does | Used in this project for |
|---|---|---|
| **Copy** | Move data between two stores, optionally changing format | Landing source files into `raw`, converting CSV → Parquet |
| **Databricks Notebook** | Run a notebook on a Databricks cluster | Bronze/silver/gold transformations and quality checks |
| **Lookup** | Read a small result (config row, watermark, file list) into the pipeline | Reading `config/dev.yml`-equivalent settings; fetching the last watermark |
| **Get Metadata** | Inspect a file/folder (existence, size, last modified, child items) | Confirm the raw file arrived and is non-empty |
| **ForEach** | Iterate over an array, optionally in parallel | Loop over `[customers, products, orders]` |
| **If Condition** | Branch on an expression | Stop the pipeline if quality checks failed |
| **Switch** | Multi-way branch | Route by source system type |
| **Until** | Loop until a condition is true | Poll an external job for completion |
| **Web / Webhook** | Call a REST endpoint | Post a message to a notification channel |
| **Stored Procedure** | Execute a proc in a SQL target | Refresh a reporting table, log a run |
| **Execute Pipeline** | Call a child pipeline | Compose `pl_bronze`, `pl_silver`, `pl_gold` from a master |
| **Fail** | Deliberately fail the pipeline with a message | Make a quality failure visible in monitoring |
| **Delete** | Remove files | Clean a staging area after success |
| **Validation** | Wait for a file to exist/meet criteria | Gate on late-arriving source files |

### Activity dependencies — the part people get wrong

Each dependency has a **condition**:

| Condition | Runs when the predecessor… |
|---|---|
| `Succeeded` | finished successfully (the default) |
| `Failed` | failed |
| `Completed` | finished either way |
| `Skipped` | was skipped |

```text
[Get Metadata: raw file exists]
            | Succeeded
            v
[Copy: raw CSV -> bronze Parquet]
            | Succeeded
            v
[Notebook: silver transformations]
            | Succeeded
            v
[Notebook: quality checks]
       |              |
   Succeeded        Failed
       v              v
[Notebook: gold]   [Web: alert the on-call channel]
```

**Gotcha:** if an activity has multiple incoming dependencies, ADF treats them as **AND**. Multiple
*separate* dependency arrows into the same activity from different predecessors also AND. To
express OR you need separate branches or a `Completed` condition plus an `If`.

### Pipelines

A pipeline is a DAG of activities plus parameters, variables and settings (concurrency, timeout,
retry).

Sensible decomposition for this project:

| Pipeline | Responsibility |
|---|---|
| `pl_ingest_raw_to_bronze` | Copy the three CSVs into bronze as Parquet, with metadata columns |
| `pl_bronze_to_silver` | Databricks notebook: standardise, type, deduplicate, flag quality |
| `pl_silver_to_gold` | Databricks notebook: build `dim_customer`, `dim_product`, `dim_date`, `fact_sales` |
| `pl_data_quality` | Databricks notebook: run checks, write summary, fail loudly on FAIL |
| `pl_master_customer_sales` | `Execute Pipeline` × 4, in order, with failure handling |

Small, single-purpose pipelines are easier to retry, test and reason about than one 40-activity
monster.

### Parameters vs variables — a genuinely confusing distinction

| | **Parameter** | **Variable** |
|---|---|---|
| Set when | At the start of a run (by trigger, parent pipeline, or manually) | During the run, with `Set Variable` / `Append Variable` |
| Mutable during the run | **No** — read-only | **Yes** |
| Scope | Pipeline, dataset, linked service, data flow | Pipeline only |
| Expression | `@pipeline().parameters.environment` | `@variables('fileList')` |
| Use for | Inputs: environment, date, source name, container | Working state: accumulators, loop results, flags |

```text
@pipeline().parameters.environment            -> "dev"
@pipeline().parameters.processingDate         -> "2026-03-01"
@variables('rowsLoaded')                      -> 8
@concat('customer_sales/', pipeline().parameters.entityName, '.csv')
@utcNow()
@formatDateTime(utcNow(), 'yyyy-MM-dd')
@activity('Get Metadata orders').output.size
@activity('Lookup quality summary').output.firstRow.failed_record_count
```

> **Parallel `ForEach` warning:** `Set Variable` inside a **parallel** `ForEach` is not safe —
> iterations share pipeline-scoped variables and race each other. Either set `isSequential: true`
> or avoid variables inside the loop entirely.

### Triggers

| Trigger type | Fires | Backfill support | Typical use |
|---|---|---|---|
| **Schedule** | At wall-clock times (cron-like) | No | "Every night at 02:00" |
| **Tumbling window** | For fixed, contiguous, non-overlapping time slices | **Yes** — rerun any past window | Hourly/daily incremental loads that must not skip a slice |
| **Event-based** | When a blob is created or deleted (via Event Grid) | No | "Run as soon as `orders.csv` lands in `raw`" |
| **Custom event** | On a custom Event Grid topic message | No | Cross-system choreography |
| **Manual** | On demand / via REST | n/a | Testing, ad-hoc reruns |

**Schedule vs tumbling window** is a favourite interview question:

- A **schedule** trigger just fires at a time. If a run fails, the window is simply lost unless you
  rerun it by hand. Runs can overlap.
- A **tumbling window** trigger owns a *time slice*, exposes `windowStartTime` and `windowEndTime`
  as parameters, supports **dependencies between windows**, enforces **no overlap**, and lets you
  **rerun a historical window** to backfill. For incremental loads where each run owns a date
  range, this is the right tool.

```text
@trigger().outputs.windowStartTime      -> 2026-03-01T00:00:00Z
@trigger().outputs.windowEndTime        -> 2026-03-02T00:00:00Z
```

For this project, an **event-based trigger** on the `raw` container is the natural fit: the moment
a new `orders.csv` lands, the pipeline starts — no polling, no fixed schedule guessing when the
upstream team will deliver.

### Integration runtimes

The **integration runtime (IR)** is the compute that actually performs ADF's work. Three kinds:

| IR type | Runs where | Use for |
|---|---|---|
| **Azure IR** (auto-resolve or region-pinned) | Microsoft-managed, serverless | Cloud-to-cloud copies, Data Flow execution. The default |
| **Self-hosted IR** | **A VM or server you manage**, often on-premises or in a private network | Reading an on-prem SQL Server, a private-network file share, or anything behind a corporate firewall |
| **Azure-SSIS IR** | Managed cluster running the SSIS engine | Lift-and-shift of existing SSIS packages |

Key facts about the **self-hosted IR**:

- It makes **outbound** connections only — no inbound firewall holes required.
- It is the standard answer to *"how does ADF read my on-prem database?"*
- You install, patch, scale and monitor it. It can be clustered (multiple nodes) for HA.
- It can also be used to reach private endpoints inside a VNet.

Pin the Azure IR to a **specific region** when data residency matters; "auto-resolve" may select a
region you did not intend.

---

## 5. Copy Activity vs Mapping Data Flows

Both move data. They are very different tools.

| | **Copy Activity** | **Mapping Data Flow** |
|---|---|---|
| What it is | A highly optimised **data mover** | A **visual transformation designer** that compiles to Spark |
| Compute | Integration runtime (lightweight) | A **Spark cluster that ADF spins up** |
| Transformations | Column mapping, type conversion, format change (CSV→Parquet) | Joins, aggregates, derived columns, pivots, window functions, surrogate keys, **alter row (upsert)** |
| Startup latency | Seconds | **Minutes** (cluster warm-up), unless a TTL is configured |
| Cost model | Data Integration Units × duration | **Spark cluster cores × hours** — far more expensive |
| Code visibility | Config | Visual graph; no notebook to version meaningfully |
| Best for | Landing data, format conversion, bulk ingest | Transformations when your team does not write Spark |

### How to choose

```text
Do you only need to MOVE or re-FORMAT the data?
        |-- Yes -> Copy Activity
        |
        No: you need real transformation logic
                |
                Does your team write PySpark?
                        |-- Yes -> Databricks Notebook activity   <- this project
                        |
                        |-- No  -> Mapping Data Flow
```

**For `../customer-sales-lakehouse` the answer is clear:** the transformation logic already exists
as tested Python in `src/transformations.py` and `src/quality_checks.py`. Reimplementing it as a
visual Data Flow would mean losing unit tests, losing meaningful code review, and losing Git
diffs. Use **Copy Activity** to land raw → bronze, and **Databricks Notebook activities** for
everything downstream.

### A worth-knowing Copy Activity feature set

- **Binary / passthrough copy** — fastest; no parsing.
- **Format conversion** — CSV → Parquet without any Spark cluster.
- **Fault tolerance** — skip incompatible rows and log them to a storage path.
- **Staged copy** — route through Blob staging when the source and sink cannot talk directly.
- **Parallel copy and degree of copy parallelism** — scale throughput for large files.
- **Incremental copy with a watermark** — combine `Lookup` (last watermark) → `Copy` (filtered) →
  `Stored Procedure` (write new watermark).

---

## 6. Azure Databricks positioning

Azure Databricks is a **first-party Azure service** that provides a managed Apache Spark platform:
notebooks, clusters, jobs, Delta Lake, and Unity Catalog governance. Guide `07` covers it in
depth; here we cover *when to reach for it*.

### What it is good at

| Strength | Why it matters |
|---|---|
| Real code (PySpark, SQL, Scala, R) | Unit-testable, reviewable, versioned in Git |
| **Delta Lake** | ACID transactions, `MERGE`, time travel, schema enforcement on a data lake |
| Scalable, autoscaling Spark | Handles data far beyond one machine |
| Collaborative notebooks | Fast iteration with a visible execution history |
| ML and streaming on the same engine | One platform for batch, stream and ML |
| Unity Catalog | Central governance, lineage and access control across workspaces |

### When to use Databricks **over** ADF Mapping Data Flows

| Choose Databricks when | Choose Mapping Data Flows when |
|---|---|
| The team writes Python/SQL and wants unit tests | The team is GUI-oriented and does not write Spark |
| Logic is complex (dedup windows, SCD2, MERGE, quality frameworks) | Logic is straightforward mapping and aggregation |
| You want the same code to run locally and in the cloud | You are happy to live inside ADF |
| You need Delta features: time travel, `MERGE`, schema evolution | Delta is not required |
| You need ML, streaming, or advanced analytics | Batch only |
| You want proper CI/CD on transformation logic | A simpler deployment story is acceptable |

A common and healthy pattern is **both**: ADF orchestrates (triggers, dependencies, retries,
monitoring, alerting) and Databricks transforms (notebooks doing the real work). ADF is the
conductor; Databricks is the orchestra.

### Compute cost discipline

| Habit | Effect |
|---|---|
| Use **job clusters**, not all-purpose clusters, for scheduled work | The cluster exists only for the run; cheaper DBU rate |
| Set **auto-termination** (e.g. 20 minutes) on all-purpose clusters | Prevents the classic "cluster ran all weekend" bill |
| Right-size, do not over-provision | Most pipelines are I/O bound, not CPU bound |
| Enable autoscaling with a sensible max | Elastic without a blank cheque |

---

## 7. Synapse Analytics concepts

Azure Synapse Analytics is an integrated analytics workspace bundling SQL engines, Spark pools,
pipelines (essentially ADF), and Data Explorer pools.

> **Note on the moving target:** Microsoft Fabric now covers much of this territory, and
> Synapse's positioning has shifted over time. The *concepts* below — especially dedicated vs
> serverless and external tables — remain the important, transferable ideas. Check current
> Microsoft documentation for what is recommended today.

### Dedicated SQL pool (formerly SQL Data Warehouse)

A **provisioned**, massively parallel processing (MPP) data warehouse.

| Aspect | Detail |
|---|---|
| Compute | Reserved, measured in **DWUs** (Data Warehouse Units) |
| Storage | Data is **loaded into** the pool's own storage |
| Billing | **Per hour provisioned**, regardless of query activity |
| Pause | **Yes — pause it and compute billing stops** (storage still bills) |
| Distribution | Tables distributed as `HASH`, `ROUND_ROBIN`, or `REPLICATE` |
| Indexing | Clustered columnstore by default |
| Best for | High-concurrency BI serving, predictable heavy workloads, complex joins at scale |

Distribution strategy is the skill that matters here:

| Distribution | Use when |
|---|---|
| `HASH(column)` | Large fact tables — hash on the most-joined, high-cardinality key |
| `ROUND_ROBIN` | Staging tables with no obvious key; even spread, but joins shuffle |
| `REPLICATE` | **Small dimensions** (< ~2 GB) — a full copy on every node eliminates join movement |

For this project's star schema the natural choice would be: `fact_sales` hashed on
`customer_key`, `dim_customer` / `dim_product` / `dim_date` replicated.

### Serverless SQL pool

A **query engine with no provisioned compute**, querying files in ADLS directly.

| Aspect | Detail |
|---|---|
| Compute | On demand, nothing to provision or pause |
| Storage | **None — data stays in ADLS as Parquet/CSV/Delta** |
| Billing | **Per terabyte of data scanned** |
| Best for | Ad-hoc exploration, logical data warehouse over the lake, lightweight serving |
| Not for | High-concurrency sub-second BI; repeated full scans of unpartitioned CSV |

```sql
-- Serverless: query Parquet in the lake with no ingestion step
SELECT
    COUNT(*)      AS fact_rows,
    SUM(amount)   AS recognised_revenue
FROM OPENROWSET(
    BULK 'https://<storage-account-name>.dfs.core.windows.net/gold/customer_sales/fact_sales/',
    FORMAT = 'PARQUET'
) AS fact_sales;
```

```text
fact_rows | recognised_revenue
----------+-------------------
        4 |             525.00
```

### External tables over ADLS

An **external table** gives a file-based dataset a SQL name and schema, so BI tools can query it
as if it were a table.

```sql
-- 1. A credential-free data source using managed identity
CREATE EXTERNAL DATA SOURCE gold_lake
WITH (
    LOCATION = 'https://<storage-account-name>.dfs.core.windows.net/gold',
    CREDENTIAL = <managed-identity-credential-name>
);

-- 2. Declare the file format
CREATE EXTERNAL FILE FORMAT parquet_snappy
WITH (
    FORMAT_TYPE = PARQUET,
    DATA_COMPRESSION = 'org.apache.hadoop.io.compress.SnappyCodec'
);

-- 3. Expose the folder as a table
CREATE EXTERNAL TABLE dbo.fact_sales (
    order_id      INT,
    customer_key  INT,
    product_key   INT,
    date_key      INT,
    order_date    DATE,
    quantity      INT,
    amount        DECIMAL(10,2)
)
WITH (
    LOCATION    = '/customer_sales/fact_sales/',
    DATA_SOURCE = gold_lake,
    FILE_FORMAT = parquet_snappy
);
```

```sql
SELECT c.region, SUM(f.amount) AS revenue
FROM dbo.fact_sales f
JOIN dbo.dim_customer c ON c.customer_key = f.customer_key
GROUP BY c.region;
```

```text
region | revenue
-------+--------
North  |  525.00
```

Note the credential is a **named managed-identity credential**, not a key in the DDL. Never paste a
SAS token or account key into SQL that lands in source control.

### Which SQL pool when?

| Need | Choose |
|---|---|
| Explore files in the lake ad hoc | **Serverless** |
| Expose gold Parquet to Power BI with modest concurrency | **Serverless** external tables |
| Hundreds of concurrent BI users, sub-second responses | **Dedicated** |
| Pay nothing when idle | **Serverless** (or a paused dedicated pool) |
| Complex joins over billions of rows, repeatedly | **Dedicated** |
| Minimal operational overhead | **Serverless** |

For `../customer-sales-lakehouse` — four fact rows — **serverless over gold Parquet is the only
sane choice.** A dedicated pool for 525.00 of revenue would be a memorable line item on a bill.

---

## 8. Orchestration and monitoring

A pipeline that runs is a prototype. A pipeline you can **see, diagnose and be alerted about** is a
product.

### ADF Monitor

Built into the Data Factory studio:

| View | Shows |
|---|---|
| **Pipeline runs** | Status, duration, trigger, parameters, run ID for every execution |
| **Activity runs** | Per-activity status, input/output JSON, error detail |
| **Trigger runs** | Which trigger fired, when, and what it started |
| **Alerts & metrics** | Built-in metrics (succeeded/failed runs) and alert rules |
| **Debug runs** | Results of interactive runs while developing |

Essential habits:

- **Rerun from the failed activity**, not from the start — ADF supports this, and it saves hours.
- Read the **activity output JSON**: `rowsRead`, `rowsCopied`, `throughput`, `errors`. The row
  counts are a free reconciliation check.
- Keep a **run ID** in your own audit log so a business question maps to an exact execution.

ADF retains monitoring data for a limited period (45 days at time of writing). **Ship logs to Log
Analytics** if you need longer history — and you will.

### Azure Monitor and Log Analytics

```text
ADF / Databricks / Storage / Key Vault
            |
            |  diagnostic settings
            v
      Azure Monitor
            |
   +--------+---------+
   |                  |
Metrics            Logs -> Log Analytics workspace
(numeric,            (rich, queryable with KQL)
 near-real-time)
            |
            v
   Alert rules -> Action groups -> email / Teams / webhook / ITSM / Logic App
```

Turn on **diagnostic settings** for every data resource and route them to one Log Analytics
workspace. Without this, your observability stops at whatever the portal retains.

A KQL query (structure only — syntax evolves, check current docs):

```text
ADFActivityRun
| where TimeGenerated > ago(24h)
| where Status == "Failed"
| project TimeGenerated, PipelineName, ActivityName, ErrorMessage, PipelineRunId
| order by TimeGenerated desc
```

### What to alert on

| Alert | Condition | Severity |
|---|---|---|
| Pipeline failed | Any failed run of `pl_master_customer_sales` | High |
| Data quality FAIL | `failed_record_count > 0` for a critical check | High |
| Pipeline ran long | Duration > 2× the rolling average | Medium |
| **Pipeline did not run** | No successful run in the last 26 hours | **High** |
| Source file missing | `Get Metadata` found no file by the SLA time | Medium |
| Row count anomaly | Loaded rows outside expected bounds | Medium |
| Cost spike | Daily spend above budget threshold | Medium |

The fourth row is the one beginners forget. **A pipeline that silently stops triggering produces no
failure alert** — and stale data is often worse than missing data because nobody notices. Alert on
*absence of success*, not only on presence of failure.

### Action groups

An action group bundles notification targets and actions, so alert rules stay simple:

| Action type | Example |
|---|---|
| Email / SMS / push | On-call engineer, team distribution list |
| Webhook | Post to a chat channel |
| Logic App | Automated remediation or ticket creation |
| Azure Function | Custom response |
| ITSM connector | Raise an incident in the service desk |

Route by severity: high severity pages a human; medium severity posts to a channel; low severity
goes to a daily digest. Alert fatigue destroys a monitoring system faster than having no alerts.

### Operational metadata you should write yourself

Platform monitoring tells you *the job ran*. Your own audit table tells you *what the job did*:

| Column | Example |
|---|---|
| `run_id` | ADF pipeline run ID |
| `pipeline_name` | `pl_bronze_to_silver` |
| `layer` | `silver` |
| `entity` | `orders` |
| `rows_read` | 8 |
| `rows_written` | 7 |
| `rows_rejected` | 1 |
| `start_time` / `end_time` | timestamps |
| `status` | `SUCCESS` / `FAILED` |
| `processing_date` | `2026-03-01` |

**How this maps to the project:** `src/logging_utils.py` writes to console and `LOGS_DIR` locally.
In Azure, those logs go to Log Analytics via the Databricks diagnostic settings, and the quality
summary DataFrame (`check_name, check_status, failed_record_count, processing_date`) from
`src/quality_checks.py` becomes the persisted audit table that alerts query.

---

## 9. Security concepts

> **Conceptual only.** No credentials, tokens, keys, tenant IDs, object IDs or connection strings
> appear in this guide or anywhere in this repository. If you ever find one in a repository you
> work on, treat it as an incident: rotate the secret first, then remove it from history.

### Microsoft Entra ID (formerly Azure Active Directory)

The identity provider for the whole tenant. Everything that can be authenticated is an Entra
identity:

| Identity type | What it is | Use for |
|---|---|---|
| **User** | A human | Interactive access, development |
| **Group** | A collection of users/service principals | **Assign RBAC to groups, never to individuals** |
| **Service principal** | An application identity with its own credential | Automation where managed identity is unavailable |
| **Managed identity** | A service principal **whose credential Azure manages for you** | **The preferred option for Azure-to-Azure auth** |

### Managed identities — why they are the right answer

A managed identity is an Entra identity automatically attached to an Azure resource. The platform
creates, stores and **rotates the credential**; you never see it, so you cannot leak it.

| Type | Lifecycle | Use when |
|---|---|---|
| **System-assigned** | Created with the resource, deleted with it; 1:1 | A single resource needs its own identity |
| **User-assigned** | Standalone resource; assignable to many | Several resources should share one identity and one set of permissions |

The flow, with no secret anywhere:

```text
1. Enable a managed identity on the Data Factory.
2. In the storage account's IAM, assign that identity the role
   "Storage Blob Data Contributor" scoped to the bronze/silver/gold containers.
3. The ADF linked service selects "Managed Identity" as its auth method.
4. At runtime ADF requests a token from Entra and calls storage.
   -> No key. No secret. Nothing to rotate. Nothing to leak.
```

Compare with the alternatives:

| Method | Secret to manage | Rotation | Verdict |
|---|---|---|---|
| **Managed identity** | **None** | Automatic | ✅ Preferred |
| Service principal + certificate | Certificate | Manual, scheduled | Acceptable where MI is impossible |
| Service principal + client secret | Secret in Key Vault | Manual, expires | Acceptable, more work |
| SAS token | Token string | Manual, expires | Narrow, time-boxed external sharing only |
| **Storage account key** | **Full-access key** | Painful | ❌ Avoid — it is root access to the account |

### Azure RBAC

Role-based access control: **who** (principal) can do **what** (role) **where** (scope).

```text
Scope inheritance (permissions flow DOWN):
Management group -> Subscription -> Resource group -> Resource -> (storage) container
```

Roles a data engineer meets constantly:

| Role | Grants | Plane |
|---|---|---|
| `Reader` | View resource configuration | Control |
| `Contributor` | Create/modify/delete resources — **but not data access, and not role assignment** | Control |
| `Owner` | Contributor + **assign roles** | Control |
| `User Access Administrator` | Manage role assignments only | Control |
| **`Storage Blob Data Reader`** | **Read blob data** | **Data** |
| **`Storage Blob Data Contributor`** | **Read/write/delete blob data** | **Data** |
| `Key Vault Secrets User` | Read secret values | Data |
| `Data Factory Contributor` | Manage ADF resources | Control |

Least privilege in practice for this project:

| Principal | Role | Scope |
|---|---|---|
| ADF managed identity | `Storage Blob Data Contributor` | `raw` and `bronze` containers |
| Databricks managed identity | `Storage Blob Data Contributor` | `bronze`, `silver`, `gold` containers |
| Analyst group | `Storage Blob Data Reader` | **`gold` container only** |
| Data engineer group | `Contributor` | The dev resource group only |
| Everyone | **No** standing access to production data | — |

Note the analyst row: analysts can read gold and **cannot** read raw. That is the single clearest
practical reason to use one container per medallion layer.

### ADLS Gen2 POSIX ACLs

Beneath RBAC, ADLS Gen2 supports POSIX-style ACLs on directories and files (read / write /
execute), including **default ACLs** inherited by new children.

Guidance: **use RBAC for the broad strokes** (container-level access for a group) and ACLs only
for fine-grained exceptions. ACL sprawl across thousands of directories becomes unauditable
quickly, and "why can this person read that folder?" turns into an archaeology project.

### Azure Key Vault

A managed store for **secrets**, **keys** and **certificates**, with access logging.

| Object | Example |
|---|---|
| Secret | A source system's password, an API token, a third-party credential |
| Key | An encryption key (can be HSM-backed) |
| Certificate | TLS certificates with managed renewal |

How it is used, without any secret ever touching code:

```text
ADF linked service  --(managed identity)-->  Key Vault  -->  returns secret at runtime
                                                   ^
                                   secret stored once by an administrator
```

In a Databricks notebook, secrets are read through a **secret scope** backed by Key Vault:

```python
# Conceptual - the value is never printed, logged, or committed
sftp_password = dbutils.secrets.get(scope="<key-vault-scope-name>", key="<secret-name>")
```

Databricks redacts secret values in notebook output automatically, but **never** write
`print(secret)` anyway — redaction is a safety net, not a design.

Key Vault practices:

- Enable **soft delete** and **purge protection** (deleting a vault by accident is catastrophic).
- Use **separate vaults per environment** — a dev vault must never hold production secrets.
- Set **expiry dates** on secrets so rotation is forced rather than forgotten.
- Turn on **diagnostic logging**: who read which secret, and when.
- Grant `Key Vault Secrets User`, not `Key Vault Administrator`, to pipelines.

### The non-negotiable rule

> **Secrets never go in code, notebooks, config files, environment files committed to Git,
> pipeline JSON, resource names, tags, log messages, or screenshots.**

Practical consequences for this repository and the project:

- `config/dev.yml` and `config/prod.yml` in `../customer-sales-lakehouse` contain **paths,
  thresholds, table names and environment flags** — and never a credential.
- Secrets are referenced by **name**: `keyVaultSecretName: "<secret-name>"`. The value is fetched
  at runtime.
- `.gitignore` excludes `.env`, `*.pem`, `*.key`, `local.settings.json`, and anything similar.
- Use a pre-commit secret scanner and enable GitHub secret scanning / push protection.
- If a secret is ever committed: **rotate it immediately**, then clean history. Rotation first —
  history rewriting does not un-leak anything.

### Private endpoints and network isolation

| Control | Effect |
|---|---|
| **Private endpoint** | Gives the service a **private IP inside your VNet**; traffic never traverses the public internet |
| **Service endpoint** | Keeps traffic on the Azure backbone; the resource still has a public endpoint |
| **Storage firewall** | Allow only listed VNets/IP ranges |
| **"Disable public network access"** | Force all access through private endpoints |
| **Private DNS zone** | Resolves the service's public hostname to the private IP — **the step people forget** |

A hardened production pattern:

```text
+-------------------------- Virtual Network ---------------------------+
|                                                                      |
|  [Databricks - VNet injected]      [Self-hosted IR VM]               |
|            |                                |                        |
|            +--------- private endpoints ----+                        |
|                          |          |                                |
+--------------------------|----------|--------------------------------+
                           v          v
                   [ADLS Gen2]   [Key Vault]
                 public access    public access
                    disabled        disabled
```

Diagnosing private-endpoint problems is almost always DNS. If a name still resolves to a public IP,
the private DNS zone link is missing.

---

## 10. Governance with Microsoft Purview

Governance answers the questions that scale *worse* than data volume: *what do we have, where did
it come from, who owns it, and is any of it sensitive?*

### What Purview provides

| Capability | What it means | Why it matters |
|---|---|---|
| **Data map / catalogue** | Automated scanning and registration of data assets | You cannot govern what you cannot find |
| **Business glossary** | Agreed definitions of business terms | "Revenue" means one thing, written down |
| **Lineage** | Column- and asset-level flow from source to report | Impact analysis and root-cause analysis |
| **Classification** | Detects sensitive data types and applies labels | Finds personal data you did not know you had |
| **Sensitivity labels** | Integrates with Microsoft Purview Information Protection | Consistent handling rules |
| **Ownership / stewardship** | Named owner and expert per asset | Someone to ask, and someone accountable |
| **Insights** | Reports on coverage, classification, glossary adoption | Measures whether governance is actually happening |

### Lineage for this project

```text
customers.csv ---+
                 |
products.csv ----+---> bronze (Parquet) ---> silver (cleansed, deduplicated, quality-flagged)
                 |                                      |
orders.csv ------+                                      +---> dim_customer
                                                        +---> dim_product
                                                        +---> dim_date
                                                        +---> fact_sales ---> reporting SQL
                                                                                  |
                                                                                  v
                                                                         Revenue by region
                                                                         Revenue by category
                                                                         Revenue by month
```

Lineage answers the two questions that actually get asked in incidents:

1. **Downstream (impact):** "`orders.csv` is adding a column next week — what breaks?"
2. **Upstream (root cause):** "The revenue figure looks wrong — which file and which transformation
   produced it?"

Guide `05` showed that recognised revenue is 525.00 because of three explicit exclusions.
**Lineage plus documented business rules is what lets you defend that number** when a stakeholder
insists it should be 825.00.

### Classification in this project

| Column | Classification | Handling |
|---|---|---|
| `customer_name` | Personal data | Restrict in raw/bronze; consider masking in gold |
| `customer_id` | Pseudonymous identifier | Internal |
| `region` | Non-sensitive | Internal |
| `amount` | Commercially sensitive | Restrict to finance consumers |
| `product_name`, `category` | Non-sensitive | Internal |
| `order_date`, `status` | Non-sensitive | Internal |

Even with entirely fictional data, practising classification builds the habit. In a real estate the
`customer_name` column is exactly the one that triggers a subject access request or a retention
obligation.

### Governance habits that cost nothing

1. **A data dictionary per layer** — column, type, source, business meaning, nullability.
2. **A documented owner per dataset** — a team, not a person who may leave.
3. **Documented business rules** — e.g. "`fact_sales` contains only valid, de-duplicated,
   `Completed` orders."
4. **A quality summary persisted per run** — `check_name, check_status, failed_record_count,
   processing_date`.
5. **Versioned schemas in Git** — schema changes get reviewed like code.
6. **A retention policy per layer** — what is kept, for how long, and why.

**How this maps to the project:** `../customer-sales-lakehouse/docs/` is the lightweight,
zero-cost version of a data catalogue. The discipline of writing it is what transfers to Purview —
the tool is easy once the thinking is done.

---

## 11. A logical Azure architecture for the customer sales project

### The full picture

```text
                        ===== SOURCE =====
        +-------------------------------------------------+
        |  customers.csv   products.csv   orders.csv      |
        |  (SFTP drop / source system export / API)        |
        +-------------------------+-----------------------+
                                  |
                   ADF Copy Activity (Azure IR, or
                   Self-hosted IR if the source is on-prem)
                                  |
                                  v
   =============== ADLS GEN2: st<workload><env><region> ===============
   |                                                                  |
   |  CONTAINER: raw                              <- landing zone     |
   |    abfss://raw@<storage-account-name>.dfs.core.windows.net/      |
   |            customer_sales/ingest_date=2026-03-01/customers.csv   |
   |            customer_sales/ingest_date=2026-03-01/products.csv    |
   |            customer_sales/ingest_date=2026-03-01/orders.csv      |
   |    Format: CSV, immutable. Tier: Hot -> Cool after 30 days       |
   |    Written by: ADF Copy Activity                                 |
   |                                                                  |
   |  CONTAINER: bronze                           <- faithful copy    |
   |    abfss://bronze@<storage-account-name>.dfs.core.windows.net/   |
   |            customer_sales/customers/                             |
   |            customer_sales/products/                              |
   |            customer_sales/orders/                                |
   |    Format: Parquet (or Delta). + ingestion_timestamp,            |
   |            source_system, source_file. 8 order rows - the        |
   |            duplicate 107 is PRESERVED here on purpose.           |
   |    Written by: ADF Copy Activity, or Databricks notebook 01      |
   |                                                                  |
   |  CONTAINER: silver                           <- cleansed/typed   |
   |    abfss://silver@<storage-account-name>.dfs.core.windows.net/   |
   |            customer_sales/customers/                             |
   |            customer_sales/products/                              |
   |            customer_sales/orders/                                |
   |    Standardised text, typed columns, deduplicated to 7 rows,     |
   |            data_quality_status added.                            |
   |    Written by: Databricks notebooks 02-04                        |
   |                                                                  |
   |  CONTAINER: gold                             <- star schema      |
   |    abfss://gold@<storage-account-name>.dfs.core.windows.net/     |
   |            customer_sales/dim_customer/                          |
   |            customer_sales/dim_product/                           |
   |            customer_sales/dim_date/                              |
   |            customer_sales/fact_sales/      <- 4 rows = 525.00    |
   |    Written by: Databricks notebooks 05-06                        |
   |                                                                  |
   |  CONTAINER: quality                          <- audit results    |
   |    abfss://quality@<storage-account-name>.dfs.core.windows.net/  |
   |            customer_sales/check_results/processing_date=.../     |
   |    Schema: check_name, check_status,                             |
   |            failed_record_count, processing_date                  |
   |    Written by: Databricks notebook 07                            |
   |                                                                  |
   ====================================================================
                                  |
                                  v
        +-------------------------------------------------+
        |  Synapse SERVERLESS SQL  - external tables over  |
        |  the gold container (no data movement)           |
        +-------------------------+-----------------------+
                                  |
                                  v
        +-------------------------------------------------+
        |  Power BI  - revenue by region / category /      |
        |              month.  Total: 525.00               |
        +-------------------------------------------------+

   ===== CROSS-CUTTING =====
   Orchestration : Azure Data Factory  (triggers, dependencies, retries)
   Transformation: Azure Databricks    (PySpark notebooks = src/ modules)
   Secrets       : Azure Key Vault     (referenced by name, never by value)
   Identity      : Microsoft Entra ID  (managed identities + RBAC groups)
   Monitoring    : Azure Monitor + Log Analytics + alert action groups
   Governance    : Microsoft Purview   (catalogue, lineage, classification)
```

### Local folder to Azure path mapping

| Local path (`src/config.py`) | Azure container and path | Format | Written by |
|---|---|---|---|
| `RAW_DIR` → `data/raw/` | `abfss://raw@<storage-account-name>.dfs.core.windows.net/customer_sales/` | CSV | **ADF Copy Activity** |
| `BRONZE_DIR` → `data/bronze/` | `abfss://bronze@<storage-account-name>.dfs.core.windows.net/customer_sales/` | Parquet / Delta | **ADF Copy Activity** or Databricks notebook `01` |
| `SILVER_DIR` → `data/silver/` | `abfss://silver@<storage-account-name>.dfs.core.windows.net/customer_sales/` | Parquet / Delta | **Databricks** notebooks `02`–`04` |
| `GOLD_DIR` → `data/gold/` | `abfss://gold@<storage-account-name>.dfs.core.windows.net/customer_sales/` | Parquet / Delta | **Databricks** notebooks `05`–`06` |
| `QUALITY_RESULTS_DIR` → `data/quality_results/` | `abfss://quality@<storage-account-name>.dfs.core.windows.net/customer_sales/check_results/` | Parquet | **Databricks** notebook `07` |
| `LOGS_DIR` → `logs/` | Log Analytics workspace (via diagnostic settings) | Log records | Databricks + ADF |

**How this maps to the project:** notice that only the *values* of the six constants in
`src/config.py` change. `src/transformations.py` and `src/quality_checks.py` — the modules that
contain all the actual business logic — do not change at all. That is not luck; it is the payoff
for never hard-coding a path. Make this point explicitly in `docs/`, because interviewers notice
it.

### Environment configuration

```yaml
# config/dev.yml  - paths and settings only, NEVER secrets
environment: dev
storage:
  account_name_placeholder: "<storage-account-name>"
  containers:
    raw: raw
    bronze: bronze
    silver: silver
    gold: gold
    quality: quality
  base_path: customer_sales
spark:
  shuffle_partitions: 4
quality:
  fail_pipeline_on: ["invalid_customer_refs", "duplicate_order_ids"]
  warn_only_on: ["missing_mandatory_fields"]
secrets:
  key_vault_name_placeholder: "<key-vault-name>"
  source_password_secret_name: "<secret-name>"   # NAME ONLY - the value lives in Key Vault
```

```yaml
# config/prod.yml
environment: prod
storage:
  account_name_placeholder: "<storage-account-name>"
  containers:
    raw: raw
    bronze: bronze
    silver: silver
    gold: gold
    quality: quality
  base_path: customer_sales
spark:
  shuffle_partitions: 200
quality:
  fail_pipeline_on:
    ["invalid_customer_refs", "duplicate_order_ids", "missing_mandatory_fields"]
  warn_only_on: []
secrets:
  key_vault_name_placeholder: "<key-vault-name>"
  source_password_secret_name: "<secret-name>"
```

The only things that differ are the environment name, Spark sizing, and how strict the quality
gate is. Production refuses to publish data with missing mandatory fields; dev warns so you can
keep working. Both files contain secret **names**, never secret **values**.

---

## 12. Local pipeline step to Azure service mapping

| # | Local step (`../customer-sales-lakehouse`) | Azure service | Notes |
|---|---|---|---|
| 1 | Source CSVs sitting in `data/raw/` | **ADLS Gen2** `raw` container | Landed by ADF Copy Activity, partitioned by `ingest_date` |
| 2 | `src/config.py` path constants | **ADF pipeline parameters** + `config/*.yml` in the Databricks repo | One config per environment |
| 3 | `src/logging_utils.py` → console + `LOGS_DIR` | **Azure Monitor / Log Analytics** | Diagnostic settings ship Databricks and ADF logs |
| 4 | `src/ingestion.py` — read CSV, add metadata, write Parquet | **ADF Copy Activity** and/or **Databricks Notebook activity** | Copy for pure format conversion; notebook if logic is needed |
| 5 | `notebooks/01_bronze_ingestion.py` | **Databricks Notebook activity** in `pl_ingest_raw_to_bronze` | Job cluster, not all-purpose |
| 6 | `src/transformations.py` — standardise, cast, dedup, quality flag | **Databricks Notebook activity** in `pl_bronze_to_silver` | The same PySpark code, unchanged |
| 7 | `notebooks/02`–`05` — silver + dimensions | **Databricks notebooks** | Chained with `Succeeded` dependencies |
| 8 | `notebooks/06_*` — `fact_sales` | **Databricks notebook** in `pl_silver_to_gold` | Produces the 4 rows totalling 525.00 |
| 9 | `src/quality_checks.py` + `notebooks/07_data_quality_checks.py` | **Databricks notebook** in `pl_data_quality`, then **If Condition** | `If` + **Fail** activity stops promotion on a FAIL |
| 10 | `sql/` reporting queries | **Synapse serverless SQL** external tables over `gold` | No data movement, pay per TB scanned |
| 11 | `tests/` (pytest) | **Azure DevOps Pipelines / GitHub Actions** | Run on every PR before deployment |
| 12 | `orchestration/pipeline_design.md` | **ADF pipelines + triggers** | The design document becomes the implementation |
| 13 | Running the pipeline by hand | **ADF trigger** (event-based on `raw`, or scheduled) | Event-based removes schedule guesswork |
| 14 | `config/dev.yml` / `config/prod.yml` | **ADF global parameters** + Databricks job parameters + **Key Vault** for secret *names* | Promote by swapping config, never code |
| 15 | Reading quality results in a text editor | **Log Analytics queries + alert rules** | Alert when `failed_record_count > 0` |
| 16 | `docs/` | **Microsoft Purview** glossary, classification and lineage | Start in Markdown; graduate to the tool |

---

## 13. Cost awareness in practice

A data engineer who ignores cost gets managed by someone who does not. Build the habit early.

### Estimating before you build

Ask four questions for every component:

1. **Is this billed for existence or for use?** (Dedicated SQL pool: existence. Serverless:
   use.)
2. **What is the unit?** (GB stored, GB scanned, DBU-hours, activity runs, DIU-hours.)
3. **What happens when it is idle?** (Can it pause? Does it auto-terminate?)
4. **What happens if a loop runs 10,000 times instead of 10?** (ADF bills per activity run.)

### Cheap habits that save real money

| Habit | Saves |
|---|---|
| Parquet/Delta instead of CSV in the lake | Storage **and** serverless scan cost (often 5–10×) |
| Partition gold tables on commonly filtered columns | Serverless scans a fraction of the data |
| Auto-terminate all-purpose Databricks clusters | The single biggest avoidable Databricks cost |
| Use job clusters for scheduled work | Lower DBU rate than all-purpose |
| Pause dedicated SQL pools | Compute billing stops entirely |
| Lifecycle policies to move cold raw data to Cool | Lower storage rate on data you never read |
| Keep compute and storage in the same region | Avoids egress charges |
| Set budgets and alerts on day one | Converts a surprise into a warning |
| Delete abandoned dev resources | Zombie resources bill forever |

### A deliberately small example

For this project — three tiny CSVs, 8 order rows, one daily run — the realistic architecture is:

- ADLS Gen2, LRS, Hot: effectively pennies.
- ADF: a handful of activity runs per day.
- Databricks: a **job cluster** running for a couple of minutes per day (a single small node is
  more than enough for 8 rows).
- Synapse **serverless**: a few megabytes scanned per query.

The expensive version of the same architecture — an all-purpose cluster left running and a
dedicated SQL pool never paused — could cost hundreds of pounds a month to compute 525.00. Being
able to explain *why* you chose the cheap version is exactly the judgement an employer is paying
for.

---

## 14. Azure learning checklist

Work through these in order. Tick them off as you genuinely understand them — not as you have read
about them. Use the free tier and delete resources when you are done.

### Fundamentals

- [ ] Explain tenant → subscription → resource group → resource without looking it up
- [ ] Explain the difference between the control plane and the data plane
- [ ] Explain why an `Owner` can still get a 403 reading a blob
- [ ] Choose a region for a workload and justify it (latency, residency, cost, availability)
- [ ] Write a naming convention for 6 resource types, including a storage account
- [ ] Apply tags (`environment`, `owner`, `costCentre`, `project`) and find them in Cost Management
- [ ] Create a budget with an alert threshold

### Storage and ADLS Gen2

- [ ] Create a storage account **with hierarchical namespace enabled**
- [ ] Explain why HNS makes Spark writes dramatically faster (atomic rename)
- [ ] Create containers `raw`, `bronze`, `silver`, `gold`, `quality`
- [ ] Write out the `abfss://<container>@<storage-account-name>.dfs.core.windows.net/<path>` format from memory
- [ ] Compare Hot / Cool / Cold / Archive and state which layer belongs in which
- [ ] Explain why Archive is dangerous for data a pipeline may reread
- [ ] Compare LRS / ZRS / GRS / GZRS and pick one per environment
- [ ] Explain why redundancy is **not** backup
- [ ] Enable soft delete and blob versioning
- [ ] Write a lifecycle management rule that moves `raw` to Cool after 30 days

### Data Factory

- [ ] Create a linked service to ADLS Gen2 using a **managed identity** (no keys)
- [ ] Create a **parameterised** dataset that serves all three CSVs
- [ ] Build a pipeline: `Get Metadata` → `Copy` → `Databricks Notebook`
- [ ] Use `ForEach` to loop over `[customers, products, orders]`
- [ ] Use `Lookup` to read a configuration value and use it downstream
- [ ] Use `If Condition` to branch on a quality result and a `Fail` activity to stop the run
- [ ] Explain parameters vs variables, and why variables are unsafe in a parallel `ForEach`
- [ ] Create a **schedule** trigger, then an **event-based** trigger on the `raw` container
- [ ] Explain when a **tumbling window** trigger beats a schedule trigger (backfill, no overlap)
- [ ] Explain the three integration runtime types and when a self-hosted IR is mandatory
- [ ] Explain Copy Activity vs Mapping Data Flow and justify your choice for this project
- [ ] Rerun a failed pipeline **from the failed activity**

### Databricks

- [ ] Create a workspace and attach a notebook to a cluster
- [ ] Explain all-purpose vs job clusters and the DBU cost difference
- [ ] Set auto-termination and explain what it prevents
- [ ] Run a PySpark notebook that reads from `bronze` and writes to `silver`
- [ ] Read a secret from a Key Vault-backed secret scope without printing it
- [ ] Connect the workspace to Git and commit a notebook
- [ ] State three reasons to choose Databricks over Mapping Data Flows

### Synapse

- [ ] Explain dedicated vs serverless SQL pools in two sentences each
- [ ] Query a Parquet folder with `OPENROWSET` in a serverless pool
- [ ] Create an external data source, file format, and external table over `gold`
- [ ] Explain `HASH` / `ROUND_ROBIN` / `REPLICATE` distribution and pick one per table
- [ ] Explain why a dedicated pool is the wrong choice for this project

### Security

- [ ] Explain the difference between a service principal and a managed identity
- [ ] Explain system-assigned vs user-assigned managed identities
- [ ] Assign `Storage Blob Data Reader` to a group on the `gold` container only
- [ ] Explain why RBAC should be assigned to groups, never individuals
- [ ] Explain why a storage account key is the worst available auth option
- [ ] Store a secret in Key Vault and reference it by name from a pipeline
- [ ] Enable Key Vault soft delete and purge protection
- [ ] Explain what a private endpoint does and why DNS is usually the thing that breaks
- [ ] State the rule about secrets in code — and audit your own repository against it

### Monitoring and governance

- [ ] Find a failed activity in ADF Monitor and read its error output JSON
- [ ] Enable diagnostic settings sending ADF and Databricks logs to Log Analytics
- [ ] Write an alert rule for pipeline failure
- [ ] Write an alert rule for **no successful run in 26 hours** and explain why it matters
- [ ] Create an action group that notifies a team channel
- [ ] Design an audit table schema capturing rows read/written/rejected per run
- [ ] Sketch lineage for `orders.csv` → `fact_sales` → revenue report
- [ ] Classify every column in the three source datasets by sensitivity

### Consolidation

- [ ] Draw the full architecture from memory on a blank page
- [ ] Explain, in under two minutes, how `525.00` is derived and why it is not `825.00`
- [ ] Explain what changes and what does **not** change when the project moves from laptop to Azure
- [ ] Estimate the monthly cost of your design and identify its biggest lever

---

## 15. Exercises

No answers are provided. Where an exercise would cost money, do it on paper or in a free tier and
delete the resources afterwards.

1. **Hierarchy on paper.** Draw tenant → subscription → resource group → resource for a company
   with `dev`, `test` and `prod`. Place the storage account, data factory, Databricks workspace,
   Key Vault and Log Analytics workspace for this project. Justify every boundary.

2. **Naming convention.** Write a naming standard covering eight resource types, including the
   storage-account special case. Apply it to this project for all three environments and produce
   the full list of 24 names.

3. **Container design.** Argue, in writing, for one container per medallion layer versus one
   container with folders. State which you would choose for a 50-person organisation and why,
   referencing the "analysts read gold only" requirement.

4. **Tiering policy.** Write a lifecycle policy in prose for each of `raw`, `bronze`, `silver`,
   `gold` and `quality`: starting tier, transition rules, and deletion rule. Justify why you would
   never archive `silver`.

5. **Redundancy choice.** Pick a redundancy option for each container in dev and in prod. Explain
   why `raw` and `gold` might reasonably differ.

6. **ADF object model.** For `pl_ingest_raw_to_bronze`, list every linked service, dataset,
   activity, parameter, variable and trigger you would create. Mark which datasets are
   parameterised and what the parameters are.

7. **Dependency design.** Draw the activity dependency graph for the master pipeline, including a
   failure branch that alerts and a quality gate that blocks the gold load. Mark each arrow as
   `Succeeded`, `Failed`, `Completed` or `Skipped`.

8. **Parameters vs variables.** Write five ADF expressions that use parameters and five that use
   variables. Then describe a concrete bug caused by using `Set Variable` inside a parallel
   `ForEach`.

9. **Trigger selection.** For each of these, choose schedule, tumbling window, or event trigger and
   justify it: (a) a vendor drops a file at an unpredictable time; (b) an hourly incremental load
   that must never skip an hour and must be backfillable; (c) a nightly full refresh at 02:00.

10. **Integration runtime.** The source system moves on-premises behind a corporate firewall.
    Describe exactly what changes in your design, which IR you need, where it runs, and what
    network holes are (and are not) required.

11. **Copy vs Data Flow.** Write a one-page recommendation for your team on when to use Copy
    Activity, Mapping Data Flows, and Databricks notebooks. Use this project as the worked example
    and be explicit about testability.

12. **Synapse serverless.** Write the `CREATE EXTERNAL DATA SOURCE`, `CREATE EXTERNAL FILE FORMAT`
    and `CREATE EXTERNAL TABLE` statements for `fact_sales`, `dim_customer` and `dim_product` using
    placeholders only. Then write the revenue-by-region query and state the expected result.

13. **Distribution strategy.** If this project were 500 million fact rows in a dedicated SQL pool,
    choose a distribution for `fact_sales` and each dimension. Justify each choice in one sentence.

14. **Least privilege.** Produce an RBAC table: principal, role, scope, justification — for the ADF
    identity, the Databricks identity, an analyst group, a data engineer group, and a support
    engineer who may read logs but not data.

15. **Secret flow.** Draw the end-to-end flow for a source system password from administrator entry
    to pipeline use, showing every component it passes through. Mark clearly every place where it
    must **never** appear.

16. **Security review.** Audit `../customer-sales-lakehouse` against the secrets rule: check
    `config/dev.yml`, `config/prod.yml`, every notebook, every module and `.gitignore`. Write up
    what you found and what you changed.

17. **Monitoring plan.** Define eight alerts with condition, severity, action group and owner.
    Include the "did not run" alert and explain in a paragraph why its absence is a common and
    serious gap.

18. **Audit table.** Design the schema for a pipeline run audit table and write the PySpark that
    would populate it from a silver notebook. Include rows read, written and rejected.

19. **Lineage and classification.** Produce the full lineage diagram from three CSVs to three
    reports, and a classification table for all 16 source columns across the three datasets.

20. **Cost estimate.** Estimate the monthly cost of your Azure design at three scales: this
    project's 8 rows, 10 million rows/day, and 1 billion rows/day. Identify the dominant cost at
    each scale and the single biggest lever available.

21. **Migration plan.** Write a step-by-step plan to move `../customer-sales-lakehouse` from laptop
    to Azure. Explicitly list what changes (paths, compute, orchestration, secrets) and what does
    **not** change (`transformations.py`, `quality_checks.py`, the business rules, and the 525.00
    result). Keep it to one page — this is an excellent interview answer.

22. **Verify against current docs.** Pick three claims from this guide (for example, ADF monitoring
    retention, the serverless SQL billing unit, and the minimum retention period for the Cool
    tier), check each against current official Microsoft documentation, and note any differences.
    Build the habit of verifying rather than trusting any single source, including this one.

---

> **Next:** guide `07` goes deep on Azure Databricks and Delta Lake — the transaction log, ACID
> guarantees, idempotent `MERGE`, time travel, `OPTIMIZE`, Z-ordering and `VACUUM` — and shows
> exactly how the local Parquet implementation in `../customer-sales-lakehouse` maps onto Delta
> while the transformation logic, the quality checks, and the **525.00** result stay identical.
