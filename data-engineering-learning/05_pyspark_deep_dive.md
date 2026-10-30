# 05 — PySpark Deep Dive

> **Dataset:** every example runs against the three fictional CSV files of the companion project **`../customer-sales-lakehouse`** — `customers.csv`, `products.csv`, `orders.csv`.
>
> **Promise:** you will finish this guide able to explain *what Spark is actually doing* when you call `.groupBy()`, not just able to copy the syntax. The difference between those two states is the difference between a junior and a mid-level data engineer.
>
> **Target result:** by section 15 you will have built `fact_sales` from raw CSV and proved that recognised revenue is exactly **525.00**.

---

## Table of contents

1. [Why Spark exists](#1-why-spark-exists)
2. [When you genuinely do NOT need Spark](#2-when-you-genuinely-do-not-need-spark)
3. [Spark architecture in depth](#3-spark-architecture-in-depth)
4. [Lazy evaluation, the DAG, and actions](#4-lazy-evaluation-the-dag-and-actions)
5. [Narrow vs wide transformations and the shuffle](#5-narrow-vs-wide-transformations-and-the-shuffle)
6. [Creating a SparkSession in local mode](#6-creating-a-sparksession-in-local-mode)
7. [Explicit schemas with StructType](#7-explicit-schemas-with-structtype)
8. [Reading CSV and Parquet](#8-reading-csv-and-parquet)
9. [Column operations: select, filter, withColumn, cast](#9-column-operations-select-filter-withcolumn-cast)
10. [Aggregations](#10-aggregations)
11. [Joins — every type](#11-joins--every-type)
12. [Null handling](#12-null-handling)
13. [Deduplication: row_number vs dropDuplicates](#13-deduplication-row_number-vs-dropduplicates)
14. [Window functions in depth](#14-window-functions-in-depth)
15. [Writing data out](#15-writing-data-out)
16. [End to end: building fact_sales and proving 525.00](#16-end-to-end-building-fact_sales-and-proving-52500)
17. [Performance habits without over-optimising](#17-performance-habits-without-over-optimising)
18. [Common beginner mistakes](#18-common-beginner-mistakes)
19. [Debugging PySpark](#19-debugging-pyspark)
20. [Hands-on exercises](#20-hands-on-exercises)

---

## 1. Why Spark exists

### The problem: one machine is not enough

A single computer has three hard limits:

| Limit | What happens when you hit it |
|---|---|
| RAM | `MemoryError` / the OS kills your process / the machine swaps to disk and crawls |
| CPU cores | Your loop runs for six hours and you cannot make it faster by waiting |
| Local disk | The file does not fit, or reading it sequentially takes longer than the business can wait |

Pandas loads the whole dataset into the memory of one process. That is wonderful up to a few
gigabytes and useless beyond it. If your orders table is 400 GB of Parquet sitting in object
storage, there is no single machine you can reasonably rent that makes `pd.read_parquet()` work.

### The idea: split the data, split the work

Spark's core idea is almost embarrassingly simple:

```text
One 400 GB dataset
        |
        v
  split into ~3,200 chunks of ~128 MB   <- these are PARTITIONS
        |
        v
  send chunks to 50 machines            <- these are EXECUTORS
        |
        v
  each machine processes its own chunks in parallel
        |
        v
  combine the partial results           <- this is where SHUFFLES happen
```

Everything else in Spark — the API, the optimiser, the cluster manager — exists to make that
picture work reliably when machines die, networks stall, and data is skewed.

### What Spark gives you beyond raw parallelism

1. **A single API over many data sources.** CSV, Parquet, JSON, JDBC, Delta, Kafka — same
   `DataFrame` code.
2. **Fault tolerance.** If an executor dies mid-job, Spark recomputes only the lost partitions
   from the lineage it recorded. You do not restart the pipeline by hand.
3. **A query optimiser (Catalyst).** You write *what* you want; Spark decides *how*. It reorders
   filters, prunes columns, and picks join strategies.
4. **A unified engine.** Batch, SQL, streaming, and ML all share the same execution core, so the
   skills transfer.

### Why a data engineer specifically must learn it

Spark is the lingua franca of the modern lakehouse. Databricks is Spark. Azure Synapse Spark
pools are Spark. AWS Glue is Spark. Fabric notebooks are Spark. Learning PySpark once unlocks
most cloud data platforms, which is why guides `06` and `07` in this repository build directly
on this one.

---

## 2. When you genuinely do NOT need Spark

This section matters as much as the rest of the guide. Reaching for Spark on small data is one of
the most common and most expensive mistakes beginners make, because a distributed engine has a
fixed overhead cost that small data can never repay.

**Do not use Spark when:**

| Situation | Use instead | Why |
|---|---|---|
| Data fits comfortably in RAM (say under ~5 GB) | pandas, Polars, DuckDB | No JVM startup, no shuffle, no cluster bill; often 10x faster end to end |
| The work is pure SQL over a warehouse table | The warehouse itself (Postgres, Synapse, Snowflake) | Pushing compute to where the data already lives beats moving it |
| You need sub-second responses for an app | A database with indexes | Spark is a throughput engine, not a low-latency one |
| You have 200 tiny files and a nightly 3-minute job | A Python script | Operational complexity is a real cost |
| You are prototyping and just need to *see* the data | pandas / `DuckDB` over the CSV | Faster feedback loop |

**Do use Spark when:**

- The dataset genuinely exceeds a single machine's memory, or will within a year.
- You need to process many files in parallel from object storage.
- You are already on a platform where Spark is the native compute (Databricks, Synapse, Fabric).
- You need one engine for batch and streaming with shared transformation code.

> **The honest truth about this project.** `../customer-sales-lakehouse` has **4 customers,
> 4 products and 8 order rows**. That is eight rows. Pandas would process it in microseconds.
> We use PySpark here deliberately, as a *training harness*: the code you write against eight
> rows is byte-for-byte the code you would write against eight billion. Learning the API and the
> execution model on data you can verify by eye is the fastest safe way to learn. Be clear-eyed
> that this is a teaching choice, not an engineering one — and be able to say exactly that in an
> interview, because it shows judgement.

**How this maps to the project:** `../customer-sales-lakehouse/docs/` should state this trade-off
explicitly. Documenting *why you chose a tool* (and when you would not) is a senior habit.

---

## 3. Spark architecture in depth

### The cast of characters

```text
                    +-------------------------------------+
                    |            DRIVER PROGRAM            |
                    |  - your Python script / notebook     |
                    |  - holds the SparkSession            |
                    |  - builds the logical plan (DAG)     |
                    |  - schedules stages and tasks        |
                    |  - collects results back             |
                    +-------------------+-----------------+
                                        |
                                 asks for resources
                                        |
                                        v
                    +-------------------------------------+
                    |          CLUSTER MANAGER             |
                    |  local[*] / Standalone / YARN /      |
                    |  Kubernetes / Databricks             |
                    +-------------------+-----------------+
                                        |
                          launches and monitors executors
                     +------------------+------------------+
                     |                  |                  |
                     v                  v                  v
             +---------------+  +---------------+  +---------------+
             |  EXECUTOR 1   |  |  EXECUTOR 2   |  |  EXECUTOR 3   |
             |  JVM process  |  |  JVM process  |  |  JVM process  |
             |  N task slots |  |  N task slots |  |  N task slots |
             |  block cache  |  |  block cache  |  |  block cache  |
             +---------------+  +---------------+  +---------------+
                     |                  |                  |
                     +---- shuffle data exchanged over network ----+
```

### Driver

The driver is the process running *your* code. It is the brain and the single point of
coordination:

- It owns the `SparkSession` and therefore the `SparkContext`.
- It converts your DataFrame calls into a **logical plan**, then (via Catalyst) a **physical
  plan**.
- It breaks that plan into **jobs → stages → tasks** and hands tasks to executors.
- It tracks which partitions live where, and reschedules tasks when executors fail.
- It receives data returned by actions such as `collect()` and `show()`.

**Consequence you must internalise:** the driver has finite memory. `df.collect()` pulls *every
row* from every executor into the driver's heap. On a big DataFrame this kills the driver — and
killing the driver kills the whole application, not just one task.

### Executors

Executors are JVM processes on worker machines. Each executor:

- Runs **tasks** (one task per core slot, concurrently).
- Holds cached/persisted partitions in its block manager.
- Writes shuffle output to local disk for other executors to fetch.

Executors do the actual data work. They never talk to your Python code directly — in PySpark,
Python UDFs require a side-car Python worker process per executor, which is why they are slow
(see section 17).

### Cluster manager

The component that decides which physical machines your executors run on.

| Cluster manager | Where you meet it |
|---|---|
| `local[*]` | Your laptop — "executors" are just threads in one JVM |
| Standalone | A small self-managed Spark cluster |
| YARN | Hadoop estates, on-prem |
| Kubernetes | Container-native platforms |
| Databricks-managed | Databricks workspaces (guide `07`) |

In **local mode** the driver and executors live in a single JVM, and `local[*]` means "use as many
threads as the machine has cores". Everything you learn about stages, shuffles and partitions
still applies — just at a tiny scale, which is exactly what makes a laptop a good classroom.

### Jobs, stages, tasks, partitions

These four words have precise meanings. Interviewers ask about them constantly.

| Term | Definition | Created by |
|---|---|---|
| **Partition** | A chunk of the data, the unit of parallelism | The reader (file splits) or a shuffle |
| **Task** | The work of applying a stage's code to exactly **one** partition | The scheduler |
| **Stage** | A group of tasks that can run without moving data between executors | A **shuffle boundary** |
| **Job** | Everything triggered by a single **action** | Calling `.count()`, `.show()`, `.write...` |

The relationships:

```text
1 ACTION          ->  1 JOB
1 JOB             ->  1..N STAGES   (a new stage starts at every shuffle)
1 STAGE           ->  1..N TASKS    (exactly one task per partition)
1 TASK            ->  processes 1 PARTITION on 1 core
```

Worked mental example on our orders data:

```python
orders_df.filter(col("status") == "Completed") \
         .groupBy("customer_id") \
         .agg(spark_sum("amount").alias("revenue")) \
         .count()
```

- `count()` is an action → **1 job**.
- `filter` is narrow; `groupBy().agg()` requires a shuffle → **2 stages**.
  - Stage 0: read CSV + filter (narrow, fused together).
  - Stage 1: aggregate the shuffled data and count.
- Number of tasks in stage 0 = number of input partitions (for one small CSV: 1).
- Number of tasks in stage 1 = `spark.sql.shuffle.partitions` (default **200**).

That last line explains a mystery every beginner hits: *"Why did my 8-row job create 200 tasks?"*
Because the default shuffle partition count is 200, and Spark does not know your data is tiny.
On a laptop, set it down (section 6).

---

## 4. Lazy evaluation, the DAG, and actions

### Transformations are lazy

```python
df2 = orders_df.filter(col("status") == "Completed")   # nothing runs
df3 = df2.withColumn("amount", col("amount").cast("decimal(10,2)"))  # nothing runs
df4 = df3.select("order_id", "amount")                 # STILL nothing runs
df4.show()                                             # <- NOW Spark runs everything
```

Each transformation returns a **new DataFrame object that only records the recipe**. Spark builds
a **DAG** (directed acyclic graph) of these steps. Nothing touches data until you call an
**action**.

### Why laziness is a feature, not a quirk

Because Spark sees the *whole* recipe before executing, Catalyst can rewrite it:

- **Predicate pushdown** — move your `filter` down into the file reader, so Parquet row groups
  that cannot match are never read from disk.
- **Column pruning** — you selected 2 of 8 columns, so only 2 columns are read.
- **Constant folding, filter reordering, join reordering** — algebraic rewrites.
- **Operator fusion (whole-stage codegen)** — several narrow operations are compiled into one
  tight Java loop instead of one pass per operation.

An eager engine like pandas cannot do any of this; it must materialise each intermediate.

### Actions (the things that actually trigger work)

| Action | Returns | Danger level |
|---|---|---|
| `show(n)` | Prints `n` rows | Safe — only pulls `n` rows |
| `count()` | A number | Safe, but scans everything |
| `collect()` | **All rows** as a Python list | **Dangerous** on big data |
| `take(n)` / `head(n)` / `first()` | Up to `n` rows to the driver | Safe |
| `write.parquet(...)` | Nothing (side effect) | Safe; this is how pipelines end |
| `foreach` / `foreachPartition` | Nothing | Safe; runs on executors |
| `toPandas()` | A pandas DataFrame in the driver | **Dangerous** — same as `collect()` |

### The lineage / recompute consequence

Because a DataFrame is a recipe, calling two actions runs the recipe **twice**:

```python
clean = orders_df.filter(col("amount").isNotNull())
print(clean.count())      # job 1 - reads the CSV, filters
print(clean.count())      # job 2 - reads the CSV AGAIN, filters AGAIN
```

That is the single legitimate reason to use `.cache()` — see section 17.

---

## 5. Narrow vs wide transformations and the shuffle

### Narrow transformations

A transformation is **narrow** when each output partition depends on exactly one input partition.
No data crosses the network.

`select`, `filter` / `where`, `withColumn`, `withColumnRenamed`, `drop`, `cast`, `map`-style
operations, and `union` are narrow.

```text
input  partition A ---> output partition A'
input  partition B ---> output partition B'
input  partition C ---> output partition C'
(no arrows cross)
```

### Wide transformations

A transformation is **wide** when an output partition may need rows from *many* input partitions.
That requires a **shuffle**: data is repartitioned across the cluster by a key.

`groupBy().agg()`, `join` (unless broadcast), `distinct`, `dropDuplicates`, `orderBy` /`sort`,
`repartition`, and window functions with `partitionBy` are wide.

```text
input  partition A --\     /--> output partition X  (all rows with key hash 0)
input  partition B ----X-----> output partition Y  (all rows with key hash 1)
input  partition C --/     \--> output partition Z  (all rows with key hash 2)
(arrows cross - data moves over the network)
```

### Why shuffles are expensive

A shuffle is not one cost, it is five:

1. **Serialisation** — every row is converted to bytes.
2. **Disk write** — each map task writes shuffle files to local disk.
3. **Network transfer** — reduce tasks fetch blocks from every map task (an all-to-all pattern,
   `M × R` connections).
4. **Deserialisation + sort/merge** on the reading side.
5. **A stage barrier** — stage *n+1* cannot start until *every* task of stage *n* has finished, so
   one slow or skewed task stalls the entire job.

Rule of thumb: a shuffle can be **10x to 100x** slower than the equivalent narrow work. You do not
need to eliminate shuffles (aggregations and joins genuinely need them) — you need to **avoid
unnecessary ones**:

- Filter *before* joining or grouping, so there is less to move.
- Select only the columns you need before a wide operation.
- Broadcast small dimension tables instead of shuffling both sides (section 17).
- Do not call `repartition()` or `orderBy()` "just to be tidy" — each one is a full shuffle.

---

## 6. Creating a SparkSession in local mode

### The entry point

```python
from pyspark.sql import SparkSession


def get_spark_session(app_name: str = "customer-sales-lakehouse") -> SparkSession:
    """Create (or reuse) a local SparkSession tuned for a small, single-machine dataset."""
    return (
        SparkSession.builder
        .appName(app_name)
        .master("local[*]")                                   # all available cores
        .config("spark.sql.shuffle.partitions", "4")          # default 200 is absurd for 8 rows
        .config("spark.sql.session.timeZone", "UTC")          # never rely on machine local time
        .config("spark.driver.memory", "2g")
        .config("spark.sql.parquet.compression.codec", "snappy")
        .getOrCreate()
    )


spark = get_spark_session()
spark.sparkContext.setLogLevel("WARN")   # mute the INFO firehose
print(spark.version)
```

`getOrCreate()` returns the existing session if one is already active — which is why re-running a
notebook cell does not spawn a second cluster.

### Configuration choices explained

| Config | Why it is set here |
|---|---|
| `master("local[*]")` | Driver and executors in one JVM, one thread per core |
| `spark.sql.shuffle.partitions = 4` | 200 tiny shuffle partitions on a laptop means 200 near-empty tasks and pure overhead |
| `spark.sql.session.timeZone = UTC` | Timestamps must not change meaning because a colleague's laptop is in another timezone |
| `spark.driver.memory = 2g` | In local mode the driver *is* the executor, so it needs headroom |

### The Java requirement (read this before you debug for an hour)

Spark runs on the JVM. PySpark is a Python API that talks to a JVM via Py4J. **You must have a
working Java runtime installed**, and it must be a version Spark supports — in practice
**Java 11 or Java 17** for Spark 3.4+. Java 8 is end-of-life for modern Spark; Java 21 may not be
supported by your Spark version.

```bash
java -version
# Expect something like: openjdk version "17.0.11"
```

Typical environment variables:

```bash
# Linux / macOS (example only - your path will differ)
export JAVA_HOME=/usr/lib/jvm/java-17-openjdk
export PATH="$JAVA_HOME/bin:$PATH"
```

```powershell
# Windows (example only - your path will differ)
$env:JAVA_HOME = "C:\Program Files\Eclipse Adoptium\jdk-17"
$env:PATH = "$env:JAVA_HOME\bin;$env:PATH"
```

**Local Spark setup varies by machine and is genuinely fiddly, especially on Windows.** Common
Windows-specific friction includes needing `winutils.exe` and a `HADOOP_HOME` for the Hadoop
filesystem shims, long-path issues, and antivirus software locking temp directories. Expect to
spend some time on this once; it is a normal part of the job, not a sign you are doing it wrong.
If local setup blocks you for more than an afternoon, run the notebooks in Databricks Community
Edition or a container image that already has Java and Spark configured, and come back to local
setup later.

Typical first-run errors and what they really mean:

| Error | Real cause |
|---|---|
| `JAVA_HOME is not set` | No JDK, or the variable points at a JRE/wrong folder |
| `UnsupportedClassVersionError` | JDK version mismatch with your Spark build |
| `Py4JJavaError` on startup | JVM failed to launch — scroll up for the real Java stack trace |
| `HADOOP_HOME and hadoop.home.dir are unset` (Windows) | Missing `winutils.exe` shim |
| Port 4040 "already in use" | A previous session is still alive; the UI moves to 4041 |

**How this maps to the project:** this factory function is what
`../customer-sales-lakehouse/src/ingestion.py` calls to start a session before reading raw CSV.

---

## 7. Explicit schemas with StructType

### Why `inferSchema=True` is risky in production

When you pass `inferSchema=True`, Spark runs an **extra pass over the data** and guesses types from
the values it happens to see. That is convenient in a notebook and dangerous in a pipeline:

1. **It is a performance cost** — an additional full read of the file, every run.
2. **It is non-deterministic across loads.** `customer_id` is inferred as `integer` today. Next
   month a source system emits `C-0007` and the same column is inferred as `string`. Your
   downstream join silently starts failing, or worse, silently returns zero rows.
3. **It silently corrupts money.** `amount` inferred as `double` introduces binary floating point
   error. `0.1 + 0.2 != 0.3`. Financial columns should be `DecimalType`.
4. **It hides broken data.** A schema is a contract. If the file no longer matches the contract,
   you *want* to find out loudly at 02:00, not discover six weeks of wrong numbers later.
5. **It cannot express nullability or intent** — an explicit schema documents which fields are
   mandatory.

> **Rule:** notebooks may infer. Pipelines must declare.

### The three schemas for this project

```python
from pyspark.sql.types import (
    StructType, StructField, StringType, IntegerType,
    DecimalType, DateType, TimestampType,
)

CUSTOMER_SCHEMA = StructType([
    StructField("customer_id",           IntegerType(), nullable=False),
    StructField("customer_name",         StringType(),  nullable=False),
    StructField("region",                StringType(),  nullable=False),
    StructField("customer_created_date", DateType(),    nullable=False),
])

PRODUCT_SCHEMA = StructType([
    StructField("product_id",   IntegerType(),       nullable=False),
    StructField("product_name", StringType(),        nullable=False),
    StructField("category",     StringType(),        nullable=False),
    StructField("unit_price",   DecimalType(10, 2),  nullable=False),
])

ORDER_SCHEMA = StructType([
    StructField("order_id",    IntegerType(),      nullable=False),
    StructField("customer_id", IntegerType(),      nullable=False),
    StructField("product_id",  IntegerType(),      nullable=False),
    StructField("order_date",  DateType(),         nullable=False),
    StructField("quantity",    IntegerType(),      nullable=False),
    StructField("amount",      DecimalType(10, 2), nullable=True),   # order 105 is NULL
    StructField("status",      StringType(),       nullable=False),
    StructField("updated_at",  TimestampType(),    nullable=False),
])
```

Note `amount` is the only order column declared nullable — that is not an accident, it encodes the
known reality that order 105 has a missing amount.

### A caution about `nullable=False`

Spark treats `nullable` as a **hint for the optimiser**, not a hard constraint enforced on read.
Declaring `nullable=False` does not make Spark reject a null row in a CSV. It documents intent and
enables optimisations. **Actual enforcement is the job of your quality checks** — which is exactly
why `../customer-sales-lakehouse/src/quality_checks.py` exists as a separate module.

### Why `DecimalType(10, 2)` for money

```python
# Floating point reality
0.1 + 0.2        # 0.30000000000000004
```

`DecimalType(precision, scale)` stores exact base-10 values. `DecimalType(10, 2)` means up to 10
total digits with 2 after the decimal point — fine for amounts up to 99,999,999.99. Sums of
decimals are exact, which is how we can confidently claim revenue is exactly `525.00` and not
`524.9999999998`.

---

## 8. Reading CSV and Parquet

### Reading CSV with an explicit schema

```python
from pathlib import Path

RAW_DIR = Path("data/raw")

customers_df = (
    spark.read
    .option("header", True)
    .option("mode", "PERMISSIVE")          # keep bad rows, null the unparseable fields
    .option("dateFormat", "yyyy-MM-dd")
    .schema(CUSTOMER_SCHEMA)
    .csv(str(RAW_DIR / "customers.csv"))
)

products_df = (
    spark.read
    .option("header", True)
    .schema(PRODUCT_SCHEMA)
    .csv(str(RAW_DIR / "products.csv"))
)

orders_df = (
    spark.read
    .option("header", True)
    .option("timestampFormat", "yyyy-MM-dd HH:mm:ss")
    .schema(ORDER_SCHEMA)
    .csv(str(RAW_DIR / "orders.csv"))
)

print(orders_df.count())     # 8  (seven order_ids, 107 appears twice)
orders_df.printSchema()
```

```text
root
 |-- order_id: integer (nullable = true)
 |-- customer_id: integer (nullable = true)
 |-- product_id: integer (nullable = true)
 |-- order_date: date (nullable = true)
 |-- quantity: integer (nullable = true)
 |-- amount: decimal(10,2) (nullable = true)
 |-- status: string (nullable = true)
 |-- updated_at: timestamp (nullable = true)
```

(Spark widens everything to `nullable = true` on CSV read — see the caution above.)

### The three CSV parse modes

| Mode | Behaviour on a malformed row |
|---|---|
| `PERMISSIVE` (default) | Sets unparseable fields to `null`; can capture the raw line in `_corrupt_record` |
| `DROPMALFORMED` | Silently discards the row — **dangerous**, you lose data with no trace |
| `FAILFAST` | Throws immediately — good for contracts you must not violate |

For bronze ingestion, `PERMISSIVE` plus a downstream quality check is usually right: you keep the
evidence and you report the problem, instead of silently losing rows.

### Other CSV options you will actually use

```python
(spark.read
   .option("header", True)
   .option("sep", ",")
   .option("quote", '"')
   .option("escape", '"')
   .option("multiLine", False)          # True if a field contains embedded newlines
   .option("nullValue", "")             # treat empty string as NULL -> matters for order 105
   .option("emptyValue", "")
   .option("ignoreLeadingWhiteSpace", True)
   .option("ignoreTrailingWhiteSpace", True)
   .schema(ORDER_SCHEMA)
   .csv("data/raw/orders.csv"))
```

That `nullValue` option is exactly what turns order 105's empty `amount` field into a true `NULL`
rather than an empty string.

### Reading and writing Parquet

```python
bronze_orders = spark.read.parquet("data/bronze/orders")
```

Parquet carries its own schema, so no `.schema()` and no `header` option is needed. Parquet is:

- **Columnar** — reading 2 of 8 columns reads roughly 2/8 of the bytes.
- **Compressed** — typically 3-10x smaller than CSV.
- **Typed** — no re-parsing strings into dates on every read.
- **Statistics-bearing** — min/max per row group enables predicate pushdown (skip entire blocks).

| Format | Human readable | Typed | Columnar | Compressed | Splittable | Good for |
|---|---|---|---|---|---|---|
| CSV | Yes | No | No | No | Yes | Interchange, raw landing |
| JSON | Yes | Partly | No | No | Line-delimited only | APIs, nested data |
| Parquet | No | Yes | Yes | Yes | Yes | Every analytics layer |
| Delta | No | Yes | Yes | Yes | Yes | Parquet + ACID (guide `07`) |

**How this maps to the project:** `src/ingestion.py` reads CSV with `header=True`, adds ingestion
metadata, and writes Parquet into `BRONZE_DIR` — defined in `src/config.py`.

### Adding ingestion metadata (the bronze pattern)

```python
from pyspark.sql.functions import current_timestamp, lit, input_file_name

bronze_orders = (
    orders_df
    .withColumn("ingestion_timestamp", current_timestamp())
    .withColumn("source_file", input_file_name())
    .withColumn("source_system", lit("sales_crm"))
)
```

Why bother? Because when a number looks wrong six months from now, the first question is *"which
file did this row come from and when did we load it?"*. Metadata columns answer that in one query
instead of a day of archaeology.

---

## 9. Column operations: select, filter, withColumn, cast

### Referring to columns

```python
from pyspark.sql.functions import col

orders_df.select("order_id")          # string name
orders_df.select(col("order_id"))     # Column object - preferred, composable
orders_df.select(orders_df.order_id)  # attribute access - breaks on names with spaces
orders_df.select(orders_df["order_id"])
```

Prefer `col()`. It works with any column name, supports operators, and makes functions reusable.

### `select` — projection

```python
orders_df.select("order_id", "customer_id", "amount", "status").show()
```

```text
+--------+-----------+------+---------+
|order_id|customer_id|amount|   status|
+--------+-----------+------+---------+
|     101|          1|200.00|Completed|
|     102|          1| 50.00|Completed|
|     103|          2| 50.00|Cancelled|
|     104|          3| 75.00|Completed|
|     105|          3|  NULL|  Pending|
|     106|         99| 50.00|Completed|
|     107|          1|200.00|Completed|
|     107|          1|200.00|Completed|
+--------+-----------+------+---------+
```

### `filter` / `where` — they are identical

```python
from pyspark.sql.functions import col

orders_df.filter(col("status") == "Completed")
orders_df.where(col("status") == "Completed")          # same thing, SQL-flavoured alias
orders_df.where("status = 'Completed'")                # SQL expression string also works
```

Combining conditions — **parentheses are mandatory** because `&` binds tighter than `==`:

```python
completed_with_amount = orders_df.filter(
    (col("status") == "Completed") & (col("amount").isNotNull())
)
# & = AND,  | = OR,  ~ = NOT
```

Writing `col("status") == "Completed" & col("amount").isNotNull()` raises a confusing error. This
costs every beginner at least one afternoon.

```python
orders_df.filter(col("status").isin("Completed", "Pending"))
orders_df.filter(col("order_date").between("2026-01-01", "2026-01-31"))
orders_df.filter(col("status").startswith("C"))
orders_df.filter(~col("status").isin("Cancelled"))
```

### `withColumn` — add or replace one column

```python
from pyspark.sql.functions import col, upper, initcap, trim, when, lit, year, month

enriched = (
    orders_df
    .withColumn("status", initcap(trim(col("status"))))                 # replaces in place
    .withColumn("order_year", year(col("order_date")))
    .withColumn("order_month", month(col("order_date")))
    .withColumn("is_revenue", when(col("status") == "Completed", lit(True)).otherwise(lit(False)))
)
```

Chaining 20 `withColumn` calls builds a deep plan and is slower to analyse. For many columns, use
a single `select` with expressions:

```python
enriched = orders_df.select(
    col("order_id"),
    col("customer_id"),
    initcap(trim(col("status"))).alias("status"),
    year(col("order_date")).alias("order_year"),
)
```

### `withColumnRenamed` and `drop`

```python
orders_df.withColumnRenamed("amount", "order_amount")
orders_df.drop("quantity", "updated_at")
```

`drop` on a non-existent column is a silent no-op — convenient, but it means a typo fails
silently. Verify with `printSchema()`.

### `cast` — changing types

```python
from pyspark.sql.functions import col, to_date, to_timestamp

typed = (
    raw_orders
    .withColumn("order_id",    col("order_id").cast("int"))
    .withColumn("customer_id", col("customer_id").cast("int"))
    .withColumn("product_id",  col("product_id").cast("int"))
    .withColumn("quantity",    col("quantity").cast("int"))
    .withColumn("amount",      col("amount").cast("decimal(10,2)"))
    .withColumn("order_date",  to_date(col("order_date"), "yyyy-MM-dd"))
    .withColumn("updated_at",  to_timestamp(col("updated_at"), "yyyy-MM-dd HH:mm:ss"))
)
```

**Critical behaviour:** a failed `cast` produces `NULL`, it does not raise. `"abc".cast("int")` is
`NULL`. So always count nulls after casting:

```python
from pyspark.sql.functions import count, when, col

typed.select([
    count(when(col(c).isNull(), c)).alias(f"{c}_nulls")
    for c in typed.columns
]).show()
```

If `amount_nulls` is 1 you are fine (order 105). If it is 8, your cast or format string is wrong.

### Standardising text — the real project rule

```python
from pyspark.sql.functions import trim, initcap, col

def standardise_text_columns(df, columns: list[str]):
    """Trim whitespace and apply consistent capitalisation to the given text columns."""
    for column_name in columns:
        df = df.withColumn(column_name, initcap(trim(col(column_name))))
    return df


customers_clean = standardise_text_columns(customers_df, ["customer_name", "region"])
orders_clean    = standardise_text_columns(orders_df, ["status"])
```

Why it matters: `"north"`, `"North "` and `"NORTH"` are three different group-by keys. One
untrimmed space splits a region's revenue across two rows in a report, and the business loses
trust in the whole platform.

**How this maps to the project:** this is `standardise_text_columns` in
`../customer-sales-lakehouse/src/transformations.py`.

---

## 10. Aggregations

### The basic shape

```python
from pyspark.sql.functions import (
    sum as spark_sum, avg, count, countDistinct, min as spark_min, max as spark_max, round as spark_round
)
```

> Alias the built-ins. `sum`, `min`, `max`, `round` and `count` are Python builtins too, and
> shadowing them creates bugs that are miserable to find.

### Simple aggregation over the whole DataFrame

```python
orders_df.agg(
    count("*").alias("row_count"),
    countDistinct("order_id").alias("distinct_order_ids"),
    countDistinct("customer_id").alias("distinct_customers"),
    spark_sum("amount").alias("sum_all_amounts"),
    spark_min("order_date").alias("first_order_date"),
    spark_max("order_date").alias("last_order_date"),
).show()
```

```text
+---------+------------------+------------------+---------------+----------------+---------------+
|row_count|distinct_order_ids|distinct_customers|sum_all_amounts|first_order_date|last_order_date|
+---------+------------------+------------------+---------------+----------------+---------------+
|        8|                 7|                 4|         825.00|      2026-01-05|     2026-02-15|
+---------+------------------+------------------+---------------+----------------+---------------+
```

Read that carefully, because it is the whole point of this guide:

- `row_count = 8` but `distinct_order_ids = 7` → **there is a duplicate** (order 107).
- `distinct_customers = 4` → customers 1, 2, 3 and 99. Note **99 does not exist** in `customers`,
  and customer 4 (David Lee) has **no orders at all**.
- `sum_all_amounts = 825.00` → the *raw* sum of everything: 200 + 50 + 50 + 75 + NULL + 50 + 200 +
  200. That is **not** recognised revenue. Recognised revenue is 525.00 after business rules.

The gap between 825.00 and 525.00 is the entire value of a silver and gold layer.

### `count` and NULLs

```python
orders_df.select(
    count("*").alias("count_star"),         # 8 - counts rows
    count("amount").alias("count_amount"),  # 7 - ignores the NULL in order 105
).show()
```

`count("*")` counts rows. `count(column)` counts **non-null values** in that column. The
difference between them is a free null-detector.

### `groupBy().agg()`

```python
revenue_by_status = (
    orders_df
    .groupBy("status")
    .agg(
        count("*").alias("order_rows"),
        spark_sum("amount").alias("total_amount"),
        avg("amount").alias("avg_amount"),
    )
    .orderBy("status")
)
revenue_by_status.show()
```

```text
+---------+----------+------------+------------+
|   status|order_rows|total_amount|  avg_amount|
+---------+----------+------------+------------+
|Cancelled|         1|       50.00|   50.000000|
|Completed|         6|      775.00|  129.166667|
|  Pending|         1|        NULL|        NULL|
+---------+----------+------------+------------+
```

Observe: `Completed` shows **6 rows and 775.00** because this is still *raw* data. Those six rows
are 101 (200.00), 102 (50.00), 104 (75.00), 106 (50.00), and order 107 **twice** (200.00 + 200.00).
The `Pending` average is `NULL` because its only row has a `NULL` amount — aggregates ignore nulls,
and ignoring every value leaves nothing to average.

Cleaning must remove the duplicate 107 row (−200.00) and the invalid-customer order 106 (−50.00):
`775.00 − 200.00 − 50.00 = 525.00`.

Let us lay that out row by row, because precision here is the skill:

| order_id | amount | status | Keep in fact_sales? | Reason |
|---|---|---|---|---|
| 101 | 200.00 | Completed | Yes | valid |
| 102 | 50.00 | Completed | Yes | valid |
| 103 | 50.00 | Cancelled | No | not Completed |
| 104 | 75.00 | Completed | Yes | valid |
| 105 | NULL | Pending | No | not Completed, and missing amount |
| 106 | 50.00 | Completed | No | customer_id 99 does not exist |
| 107 (row 1) | 200.00 | Completed | Yes | valid, kept by dedup |
| 107 (row 2) | 200.00 | Completed | No | exact duplicate, removed |

Sum of the kept rows: `200.00 + 50.00 + 75.00 + 200.00 = `**`525.00`**.

Cross-check from the other direction: all raw `Completed` rows sum to `775.00`; subtract the
duplicate 200.00 and the invalid-customer 50.00 → `775.00 − 200.00 − 50.00 = 525.00`. ✔

Two independent routes to the same number is how you build confidence in a figure. Section 16
proves it end to end in code.

### Multi-column grouping

```python
by_customer_status = (
    orders_df
    .groupBy("customer_id", "status")
    .agg(spark_sum("amount").alias("total_amount"))
    .orderBy("customer_id", "status")
)
```

### Aggregations that will appear in the gold layer

```python
# Revenue by region (after cleaning - see section 16)
fact_sales.join(dim_customer, "customer_key") \
          .groupBy("region") \
          .agg(spark_sum("amount").alias("revenue")) \
          .show()
```

```text
+------+-------+
|region|revenue|
+------+-------+
| North| 525.00|
+------+-------+
```

All recognised revenue in this sample belongs to the **North** region (Aisha Khan 450.00 + Carla
Jones 75.00). Ben Smith's only order was cancelled and David Lee never ordered. That is a useful
reminder that a correct report can still look surprising.

```python
# Revenue by product category
fact_sales.join(dim_product, "product_key") \
          .groupBy("category") \
          .agg(spark_sum("amount").alias("revenue")) \
          .orderBy("category") \
          .show()
```

```text
+-----------+-------+
|   category|revenue|
+-----------+-------+
|Accessories| 125.00|
|   Hardware| 400.00|
+-----------+-------+
```

`125.00 + 400.00 = 525.00`. ✔ Every slice must reconcile to the same total — that cross-check is
how you catch a broken join.

### `pivot`

```python
(orders_df
   .groupBy("customer_id")
   .pivot("status", ["Completed", "Cancelled", "Pending"])   # always supply the value list
   .agg(spark_sum("amount"))
   .orderBy("customer_id")
   .show())
```

Supplying the list of pivot values avoids an extra job to discover them and keeps the output schema
stable across runs — a stable schema is what downstream consumers depend on.

---

## 11. Joins — every type

Join syntax:

```python
left_df.join(right_df, on=<condition or column names>, how=<join type>)
```

The `on` argument accepts three forms:

```python
orders_df.join(customers_df, on="customer_id", how="inner")                    # shared name - no dup column
orders_df.join(customers_df, on=["customer_id"], how="inner")                  # list form
orders_df.join(customers_df,
               on=orders_df.customer_id == customers_df.customer_id,
               how="inner")                                                    # keeps BOTH columns
```

Prefer the string/list form when the key name matches on both sides: it produces a single
`customer_id` column instead of two ambiguous ones.

### The full catalogue

| `how` | Keeps | Typical use |
|---|---|---|
| `inner` | Only rows matching on both sides | Default fact-to-dimension enrichment where orphans are not allowed |
| `left` / `left_outer` | All left rows; right columns `NULL` when unmatched | Enrich but keep every fact row so you can *see* orphans |
| `right` / `right_outer` | All right rows | Rare; usually rewrite as a `left` with sides swapped |
| `full` / `full_outer` | All rows from both sides | Reconciliation: find what exists on one side only |
| `left_semi` | Left rows **that have** a match; **no right columns** | Filtering ("which orders have a valid customer?") |
| `left_anti` | Left rows **that have no** match; no right columns | **Finding orphans / referential integrity breaks** |
| `cross` | Cartesian product | Date spines, deliberate explosion — never accidental |

### Inner join

```python
inner = orders_df.join(customers_df, on="customer_id", how="inner")
inner.select("order_id", "customer_id", "customer_name", "amount", "status") \
     .orderBy("order_id").show()
```

```text
+--------+-----------+-------------+------+---------+
|order_id|customer_id|customer_name|amount|   status|
+--------+-----------+-------------+------+---------+
|     101|          1|   Aisha Khan|200.00|Completed|
|     102|          1|   Aisha Khan| 50.00|Completed|
|     103|          2|    Ben Smith| 50.00|Cancelled|
|     104|          3|  Carla Jones| 75.00|Completed|
|     105|          3|  Carla Jones|  NULL|  Pending|
|     107|          1|   Aisha Khan|200.00|Completed|
|     107|          1|   Aisha Khan|200.00|Completed|
+--------+-----------+-------------+------+---------+
```

**7 rows out of 8.** Order 106 vanished. This is the quiet danger of an inner join: it silently
drops the bad row and your row count shrinks with no warning. If you had not counted first, you
would never know.

### Left join

```python
left = orders_df.join(customers_df, on="customer_id", how="left")
left.select("order_id", "customer_id", "customer_name", "status").orderBy("order_id").show()
```

```text
+--------+-----------+-------------+---------+
|order_id|customer_id|customer_name|   status|
+--------+-----------+-------------+---------+
|     101|          1|   Aisha Khan|Completed|
|     102|          1|   Aisha Khan|Completed|
|     103|          2|    Ben Smith|Cancelled|
|     104|          3|  Carla Jones|Completed|
|     105|          3|  Carla Jones|  Pending|
|     106|         99|         NULL|Completed|
|     107|          1|   Aisha Khan|Completed|
|     107|          1|   Aisha Khan|Completed|
+--------+-----------+-------------+---------+
```

**8 rows — nothing lost**, and the broken reference is now *visible* as a `NULL customer_name`.
This is why quality checks prefer `left` joins: you cannot report a problem you have deleted.

### Right join

```python
right = orders_df.join(customers_df, on="customer_id", how="right")
right.select("customer_id", "customer_name", "order_id").orderBy("customer_id", "order_id").show()
```

```text
+-----------+-------------+--------+
|customer_id|customer_name|order_id|
+-----------+-------------+--------+
|          1|   Aisha Khan|     101|
|          1|   Aisha Khan|     102|
|          1|   Aisha Khan|     107|
|          1|   Aisha Khan|     107|
|          2|    Ben Smith|     103|
|          3|  Carla Jones|     104|
|          3|  Carla Jones|     105|
|          4|     David Lee|   NULL|
+-----------+-------------+--------+
```

**8 rows:** the 7 matched order rows plus **David Lee**, who has never ordered. That `NULL
order_id` is a genuine business insight (a dormant customer), not an error.

### Full outer join

```python
full = orders_df.join(customers_df, on="customer_id", how="full")
print(full.count())    # 9
```

9 rows = 7 matched + order 106 (orphan order) + David Lee (customer with no orders). A full outer
join answers *"show me everything that fails to line up, in both directions, at once"* — the
classic reconciliation tool.

```python
from pyspark.sql.functions import col

full.filter(col("order_id").isNull() | col("customer_name").isNull()) \
    .select("customer_id", "customer_name", "order_id").show()
```

```text
+-----------+-------------+--------+
|customer_id|customer_name|order_id|
+-----------+-------------+--------+
|          4|    David Lee|    NULL|
|         99|         NULL|     106|
+-----------+-------------+--------+
```

### Left semi join — "does a match exist?"

```python
valid_orders = orders_df.join(customers_df, on="customer_id", how="left_semi")
print(valid_orders.count())    # 7
print(valid_orders.columns)    # original order columns only - NO customer columns
```

A left semi join is a `WHERE EXISTS` filter. It never duplicates left rows even if the right side
has many matches, and it brings back no right-hand columns. Use it when you want to *filter*, not
*enrich*.

### Left anti join — finding order 106

This is the single most useful join for a data engineer, because referential-integrity breaks are
the most common real-world data defect.

```python
from pyspark.sql.functions import col, lit, current_date

orphan_orders = (
    orders_df
    .join(customers_df, on="customer_id", how="left_anti")
    .select("order_id", "customer_id", "order_date", "amount", "status")
)
orphan_orders.show()
```

```text
+--------+-----------+----------+------+---------+
|order_id|customer_id|order_date|amount|   status|
+--------+-----------+----------+------+---------+
|     106|         99|2026-02-10| 50.00|Completed|
+--------+-----------+----------+------+---------+
```

**Exactly one row: order 106, whose `customer_id` 99 does not exist in `customers.csv`.** This is
the deliberate invalid-customer defect in the sample data, and this three-line join is how you
detect it.

Run the same pattern against products:

```python
invalid_product_refs = orders_df.join(products_df, on="product_id", how="left_anti")
print(invalid_product_refs.count())    # 0  <- zero invalid product references by design
```

Zero — every `product_id` in `orders.csv` (1001–1004) exists in `products.csv`. A check that
returns zero is not a wasted check; it is a passing assertion, and it must keep passing.

Equivalent SQL for anyone coming from guide `01`:

```sql
SELECT o.*
FROM orders o
LEFT JOIN customers c ON c.customer_id = o.customer_id
WHERE c.customer_id IS NULL;
```

The `left_anti` form is clearer and lets Catalyst choose a better physical plan.

**How this maps to the project:** `src/quality_checks.py` uses exactly this pattern for
`invalid_customer_refs` and `invalid_product_refs`, returning DataFrames rather than booleans so
the failing rows can be written to `QUALITY_RESULTS_DIR` for investigation.

### Join pitfalls

| Pitfall | Symptom | Fix |
|---|---|---|
| Duplicate keys on the right side | Row count **increases** after a join | Deduplicate the dimension first; assert uniqueness |
| Ambiguous column name | `AnalysisException: Reference 'customer_id' is ambiguous` | Use `on="customer_id"` or alias the DataFrames |
| Type mismatch (`"1"` vs `1`) | Join returns 0 rows | Cast both sides explicitly |
| Trailing whitespace in string keys | Silent non-matches | `trim()` during standardisation |
| Null keys | Nulls never match, including null-to-null | Filter or default them before joining |

Always assert row counts around a join:

```python
before = orders_df.count()
joined = orders_df.join(customers_df, on="customer_id", how="left")
after = joined.count()
assert after == before, f"Join changed row count: {before} -> {after}"
```

If a `left` join changes the row count, your right-hand side is not unique on the key. That assert
has saved more pipelines than any dashboard.

---

## 12. Null handling

Nulls are not a nuisance to be squashed; they are *information*. Order 105 has a `NULL` amount
because the source system genuinely does not know it yet. Replacing it with `0` would quietly
assert a fact that is false.

### Detecting nulls

```python
from pyspark.sql.functions import col, count, when, sum as spark_sum

orders_df.filter(col("amount").isNull()).select("order_id", "status").show()
```

```text
+--------+-------+
|order_id| status|
+--------+-------+
|     105|Pending|
+--------+-------+
```

Null profile across every column in one pass:

```python
null_profile = orders_df.select([
    spark_sum(when(col(c).isNull(), 1).otherwise(0)).alias(c)
    for c in orders_df.columns
])
null_profile.show()
```

```text
+--------+-----------+----------+----------+--------+------+------+----------+
|order_id|customer_id|product_id|order_date|quantity|amount|status|updated_at|
+--------+-----------+----------+----------+--------+------+------+----------+
|       0|          0|         0|         0|       0|     1|     0|         0|
+--------+-----------+----------+----------+--------+------+------+----------+
```

**Exactly one null, in `amount`.** That matches the documented defect list precisely. Run this
profile on every new source before you write a line of transformation logic.

### `na.fill` — replace nulls with a default

```python
orders_df.na.fill(0, subset=["quantity"])                 # typed by column
orders_df.na.fill({"status": "Unknown", "quantity": 0})   # dict form, multiple columns
```

**Do not do this to `amount`.** Filling a missing monetary value with `0` turns "we do not know"
into "it was free", and that error propagates into every downstream report. Nulls in money columns
should be *excluded* from revenue and *reported*, not filled.

### `na.drop` — remove rows with nulls

```python
orders_df.na.drop()                                            # any null in any column
orders_df.na.drop(subset=["amount"])                           # null in amount only -> drops 105
orders_df.na.drop(how="all")                                   # only fully-null rows
orders_df.na.drop(thresh=6)                                    # keep rows with >= 6 non-nulls
```

`how="any"` (the default) is aggressive. On a wide table, one null in a column you do not even use
will delete the row. Always pass `subset`.

### `coalesce` — first non-null wins

```python
from pyspark.sql.functions import coalesce, col, lit

with_fallback = orders_df.withColumn(
    "amount_for_display",
    coalesce(col("amount"), col("quantity") * lit(0), lit(0)).cast("decimal(10,2)")
)
```

Note the deliberate column name: `amount_for_display`, not `amount`. Keep the true value and the
presentation value in separate columns so nobody accidentally sums the fallback.

> **Naming trap:** `coalesce()` the *function* (first non-null value) and `df.coalesce(n)` the
> *method* (reduce partition count without a full shuffle) are completely unrelated things that
> share a name. Context tells you which is which.

### `nullif`, `nvl` and conditional nulls

```python
from pyspark.sql.functions import when, col, lit

# Turn empty strings into true nulls
cleaned = orders_df.withColumn(
    "status",
    when(col("status") == "", lit(None).cast("string")).otherwise(col("status"))
)
```

### The project's quality flag

```python
from pyspark.sql.functions import col, when, lit

def add_data_quality_status(orders, customers, products):
    """Tag every order row with a quality verdict instead of deleting bad rows."""
    valid_statuses = ["Completed", "Cancelled", "Pending"]

    customer_ids = customers.select(col("customer_id").alias("c_id")).distinct()
    product_ids  = products.select(col("product_id").alias("p_id")).distinct()

    enriched = (
        orders
        .join(customer_ids, orders.customer_id == col("c_id"), "left")
        .join(product_ids,  orders.product_id  == col("p_id"), "left")
    )

    return enriched.withColumn(
        "data_quality_status",
        when(col("c_id").isNull(), lit("INVALID_CUSTOMER"))
        .when(col("p_id").isNull(), lit("INVALID_PRODUCT"))
        .when(col("amount").isNull(), lit("MISSING_AMOUNT"))
        .when(~col("status").isin(valid_statuses), lit("INVALID_STATUS"))
        .otherwise(lit("VALID"))
    ).drop("c_id", "p_id")
```

```text
+--------+-------------------+
|order_id|data_quality_status|
+--------+-------------------+
|     101|              VALID|
|     102|              VALID|
|     103|              VALID|
|     104|              VALID|
|     105|     MISSING_AMOUNT|
|     106|   INVALID_CUSTOMER|
|     107|              VALID|
|     107|              VALID|
+--------+-------------------+
```

Order 103 is `VALID` even though it is `Cancelled`: being cancelled is a legitimate business
state, not a data defect. Validity and revenue-eligibility are different questions, and conflating
them is a classic modelling error.

**How this maps to the project:** this is the `data_quality_status` column added to silver orders
in `src/transformations.py`, and the counts behind the summary produced by `src/quality_checks.py`.

---

## 13. Deduplication: row_number vs dropDuplicates

### The problem

`orders.csv` has 8 rows but only 7 distinct `order_id` values. Order 107 appears twice with
**identical** values, including an identical `updated_at` of `2026-02-15 08:00:00`. Left alone, it
inflates revenue by 200.00 and makes the fact table's grain a lie.

### Approach 1 — `dropDuplicates` (blunt)

```python
orders_df.dropDuplicates()                    # all columns must match
orders_df.dropDuplicates(["order_id"])        # keeps ONE arbitrary row per order_id
```

```python
print(orders_df.count())                       # 8
print(orders_df.dropDuplicates().count())      # 7
```

It works here *only because* the two 107 rows are byte-identical. `dropDuplicates(["order_id"])`
has a serious flaw for real pipelines: **which row survives is non-deterministic.** Spark keeps
whichever row a task happened to see first. If the two rows differed — say one said `Completed`
and a later one said `Cancelled` — you could get a different answer on every run. Non-deterministic
pipelines are unauditable pipelines.

### Approach 2 — `row_number()` over a window (the project's real logic)

```python
from pyspark.sql import Window
from pyspark.sql.functions import row_number, col

dedup_window = Window.partitionBy("order_id").orderBy(col("updated_at").desc())

deduplicated_orders = (
    orders_df
    .withColumn("row_num", row_number().over(dedup_window))
    .filter(col("row_num") == 1)
    .drop("row_num")
)
```

Read it as English: *"within each `order_id`, sort rows newest-first by `updated_at`, number them
1, 2, 3…, and keep only number 1."*

```python
print(deduplicated_orders.count())    # 7
deduplicated_orders.orderBy("order_id").select("order_id", "amount", "status", "updated_at").show(truncate=False)
```

```text
+--------+------+---------+-------------------+
|order_id|amount|status   |updated_at         |
+--------+------+---------+-------------------+
|101     |200.00|Completed|2026-01-05 10:00:00|
|102     |50.00 |Completed|2026-01-10 11:00:00|
|103     |50.00 |Cancelled|2026-01-12 09:30:00|
|104     |75.00 |Completed|2026-02-01 14:00:00|
|105     |NULL  |Pending  |2026-02-05 15:00:00|
|106     |50.00 |Completed|2026-02-10 12:00:00|
|107     |200.00|Completed|2026-02-15 08:00:00|
+--------+------+---------+-------------------+
```

**8 rows in, 7 rows out, one row per `order_id`.** The grain is now trustworthy.

### Why this approach is better

| | `dropDuplicates(["order_id"])` | `row_number()` window |
|---|---|---|
| Which row survives | Arbitrary / non-deterministic | **The one you specified** (latest `updated_at`) |
| Handles changed rows (CDC updates) | No — may keep the stale version | **Yes — keeps the newest** |
| Expresses business intent in code | No | **Yes, explicitly** |
| Auditable / reproducible | Not reliably | **Yes** |
| Cost | One shuffle | One shuffle (same order of magnitude) |

The decisive point: real source systems resend rows because a record was *updated*. You almost
never want "any one of them" — you want "the latest one". `row_number()` says that out loud.

### The tie-break caveat

Our two 107 rows have the **same** `updated_at`, so `orderBy(col("updated_at").desc())` cannot
distinguish them. That is fine here because the rows are otherwise identical — either one gives the
same result. In production, add a deterministic tie-breaker so reruns are reproducible:

```python
dedup_window = Window.partitionBy("order_id").orderBy(
    col("updated_at").desc(),
    col("ingestion_timestamp").desc(),
    col("source_file").asc(),
)
```

### `rank` vs `dense_rank` vs `row_number`

| Function | On a tie | Use for dedup? |
|---|---|---|
| `row_number()` | Assigns 1, 2, 3 — **always unique** | **Yes** |
| `rank()` | Ties share a number, then it skips (1, 1, 3) | No — a tie keeps both rows |
| `dense_rank()` | Ties share a number, no skip (1, 1, 2) | No — same problem |

If you used `rank()` here, both 107 rows get rank 1 and `filter(rank == 1)` keeps **both**. The
duplicate survives and revenue is 725.00 instead of 525.00. Choosing the wrong window function is
a silent, expensive bug.

**How this maps to the project:** this exact window is the `remove_duplicate_orders` function in
`../customer-sales-lakehouse/src/transformations.py`.

---

## 14. Window functions in depth

A window function computes a value **per row**, using a *frame* of related rows — unlike
`groupBy`, which collapses rows. You keep all your detail *and* get the aggregate.

### Anatomy

```python
Window.partitionBy(<split into groups>).orderBy(<sort within group>).rowsBetween(<frame>)
```

- `partitionBy` — like `GROUP BY`, but rows are not collapsed.
- `orderBy` — defines sequence (required for `row_number`, `lag`, running totals).
- frame clause — which rows around the current row are in scope.

### Ranking: top order per customer

```python
from pyspark.sql import Window
from pyspark.sql.functions import row_number, col

rank_window = Window.partitionBy("customer_id").orderBy(col("amount").desc())

ranked = (
    deduplicated_orders
    .filter(col("status") == "Completed")
    .withColumn("amount_rank", row_number().over(rank_window))
)
ranked.select("customer_id", "order_id", "amount", "amount_rank").orderBy("customer_id", "amount_rank").show()
```

```text
+-----------+--------+------+-----------+
|customer_id|order_id|amount|amount_rank|
+-----------+--------+------+-----------+
|          1|     101|200.00|          1|
|          1|     107|200.00|          2|
|          1|     102| 50.00|          3|
|          3|     104| 75.00|          1|
|         99|     106| 50.00|          1|
+-----------+--------+------+-----------+
```

(Customer 99 still appears because we have not yet applied referential filtering — section 16
removes it.)

### Running total

```python
from pyspark.sql import Window
from pyspark.sql.functions import sum as spark_sum, col

running_window = (
    Window.orderBy("order_date")
          .rowsBetween(Window.unboundedPreceding, Window.currentRow)
)

running = fact_sales.withColumn("running_revenue", spark_sum("amount").over(running_window))
running.select("order_id", "order_date", "amount", "running_revenue").orderBy("order_date").show()
```

```text
+--------+----------+------+---------------+
|order_id|order_date|amount|running_revenue|
+--------+----------+------+---------------+
|     101|2026-01-05|200.00|         200.00|
|     102|2026-01-10| 50.00|         250.00|
|     104|2026-02-01| 75.00|         325.00|
|     107|2026-02-15|200.00|         525.00|
+--------+----------+------+---------------+
```

The final running total is **525.00** — the same number, derived a third independent way.

> **Performance warning:** a `Window.orderBy()` with **no** `partitionBy` forces *all* data into a
> **single partition** on one executor. Fine for 4 rows; catastrophic for 400 million. Spark even
> logs a warning. Partition by something (customer, region, month) whenever the business question
> allows.

### Frame types: `rowsBetween` vs `rangeBetween`

| Clause | Means |
|---|---|
| `rowsBetween(Window.unboundedPreceding, Window.currentRow)` | Running total by physical row position |
| `rowsBetween(-2, 0)` | Current row plus the two before it (3-row moving window) |
| `rangeBetween(-7, 0)` on a numeric/date ordering | All rows whose *value* lies within 7 units |
| `rowsBetween(Window.unboundedPreceding, Window.unboundedFollowing)` | The whole partition (e.g. share-of-total) |

Share of total without a second pass:

```python
from pyspark.sql.functions import sum as spark_sum, round as spark_round, col
from pyspark.sql import Window

whole = Window.rowsBetween(Window.unboundedPreceding, Window.unboundedFollowing)

share = fact_sales.withColumn(
    "pct_of_total",
    spark_round(col("amount") / spark_sum("amount").over(whole) * 100, 2)
)
```

```text
+--------+------+------------+
|order_id|amount|pct_of_total|
+--------+------+------------+
|     101|200.00|       38.10|
|     102| 50.00|        9.52|
|     104| 75.00|       14.29|
|     107|200.00|       38.10|
+--------+------+------------+
```

`38.10 + 9.52 + 14.29 + 38.10 = 100.01` — the rounding residual is expected and is exactly why you
round for presentation only, never for storage.

### `lag` and `lead`

```python
from pyspark.sql.functions import lag, lead, col, datediff
from pyspark.sql import Window

seq = Window.orderBy("order_date")

trend = (
    fact_sales
    .withColumn("prev_amount", lag("amount", 1).over(seq))
    .withColumn("next_amount", lead("amount", 1).over(seq))
    .withColumn("delta_vs_prev", col("amount") - lag("amount", 1).over(seq))
    .withColumn("days_since_prev", datediff(col("order_date"), lag("order_date", 1).over(seq)))
)
trend.select("order_id", "order_date", "amount", "prev_amount", "next_amount",
             "delta_vs_prev", "days_since_prev").orderBy("order_date").show()
```

```text
+--------+----------+------+-----------+-----------+-------------+---------------+
|order_id|order_date|amount|prev_amount|next_amount|delta_vs_prev|days_since_prev|
+--------+----------+------+-----------+-----------+-------------+---------------+
|     101|2026-01-05|200.00|       NULL|      50.00|         NULL|           NULL|
|     102|2026-01-10| 50.00|     200.00|      75.00|      -150.00|              5|
|     104|2026-02-01| 75.00|      50.00|     200.00|        25.00|             22|
|     107|2026-02-15|200.00|      75.00|       NULL|       125.00|             14|
+--------+----------+------+-----------+-----------+-------------+---------------+
```

The leading and trailing `NULL`s are correct — the first row has no predecessor. Supply a default
if you prefer: `lag("amount", 1, 0)`.

`lag`/`lead` are the backbone of change detection: comparing a row to its previous version is how
you build SCD Type 2 dimensions and spot unexpected jumps in a metric.

### Useful window functions at a glance

| Function | Gives you |
|---|---|
| `row_number()` | Unique sequence within partition — **deduplication** |
| `rank()` / `dense_rank()` | Competition ranking with/without gaps |
| `lag(col, n, default)` / `lead(...)` | Value from n rows back/forward — change detection |
| `first_value` / `last_value` | Boundary values of the frame |
| `ntile(n)` | Bucket rows into n groups — quartiles, deciles |
| `sum/avg/min/max/count` over a window | Running and moving aggregates |
| `cume_dist()` / `percent_rank()` | Distribution position |

---

## 15. Writing data out

### Write modes

| Mode | Behaviour | When to use |
|---|---|---|
| `overwrite` | Replace the target path's data | Full refresh of a layer — the project's default |
| `append` | Add files alongside existing ones | Incremental loads, event data |
| `ignore` | Do nothing if the path exists | Rare |
| `error` / `errorifexists` (default) | Throw if the path exists | Protects against accidental clobbering |

```python
(bronze_orders
   .write
   .mode("overwrite")
   .parquet("data/bronze/orders"))
```

Note Spark writes a **directory**, not a file: `part-00000-....snappy.parquet`, `_SUCCESS`, and so
on. One part file per output partition. The `_SUCCESS` marker is how downstream jobs know the write
completed rather than crashed halfway.

### `partitionBy` — physical layout for pruning

```python
(fact_sales
   .write
   .mode("overwrite")
   .partitionBy("order_year", "order_month")
   .parquet("data/gold/fact_sales"))
```

```text
data/gold/fact_sales/
├── order_year=2026/
│   ├── order_month=1/
│   │   └── part-00000-....snappy.parquet
│   └── order_month=2/
│       └── part-00000-....snappy.parquet
└── _SUCCESS
```

A query filtering `order_year = 2026 AND order_month = 1` now reads one directory instead of all
of them. That is **partition pruning**, and on large tables it is the difference between 4 seconds
and 40 minutes.

**Choosing a partition column:**

| Good partition column | Bad partition column |
|---|---|
| Low-to-medium cardinality (year, month, region) | High cardinality (`order_id`, `customer_id`) |
| Used in most `WHERE` clauses | Rarely filtered on |
| Produces partitions of roughly 128 MB – 1 GB | Produces thousands of tiny files |

Partitioning by `order_id` would create one directory per order. With eight rows that is
grotesque; with eight million it destroys the cluster. This is the **small-files problem**, and it
is the most common self-inflicted performance disaster in lakehouses.

> **For this project's tiny dataset, do not partition at all.** Partitioning 4 rows creates
> overhead with zero benefit. Learn the syntax, apply the judgement.

### Controlling file count

```python
fact_sales.coalesce(1).write.mode("overwrite").parquet("data/gold/fact_sales")   # 1 file, no shuffle
fact_sales.repartition(8).write.mode("overwrite").parquet("data/gold/fact_sales") # 8 files, shuffles
```

- `coalesce(n)` only **reduces** partitions and avoids a full shuffle — cheap.
- `repartition(n)` can increase or decrease and always shuffles — expensive but balances sizes.

### Parquet vs CSV on write

```python
# Parquet - for every internal layer
df.write.mode("overwrite").parquet("data/silver/orders")

# CSV - only when a human or an external system demands it
(df.coalesce(1)
   .write.mode("overwrite")
   .option("header", True)
   .csv("data/quality_results/summary_csv"))
```

Use CSV at the **edges** of your system (hand-off, inspection) and Parquet everywhere inside it.

**How this maps to the project:** `src/config.py` defines `RAW_DIR`, `BRONZE_DIR`, `SILVER_DIR`,
`GOLD_DIR`, `QUALITY_RESULTS_DIR` and `LOGS_DIR` with `pathlib`, plus a function that creates the
output directories. Every write target comes from those constants — never a hard-coded string —
so the whole pipeline relocates by editing one file.

---

## 16. End to end: building fact_sales and proving 525.00

This section stitches everything together. Every step is a transformation you have already met.

### Step 0 — session and raw read

```python
from decimal import Decimal
from pyspark.sql import SparkSession, Window
from pyspark.sql.functions import (
    col, trim, initcap, row_number, when, lit, current_timestamp,
    sum as spark_sum, min as spark_min, max as spark_max,
    count, countDistinct, to_date, date_format,
    year, month, dayofmonth, quarter, dayofweek, explode, sequence,
)

spark = get_spark_session()

customers_raw = (spark.read.option("header", True).schema(CUSTOMER_SCHEMA)
                 .csv("data/raw/customers.csv"))
products_raw  = (spark.read.option("header", True).schema(PRODUCT_SCHEMA)
                 .csv("data/raw/products.csv"))
orders_raw    = (spark.read.option("header", True)
                 .option("timestampFormat", "yyyy-MM-dd HH:mm:ss")
                 .schema(ORDER_SCHEMA).csv("data/raw/orders.csv"))

print(f"customers={customers_raw.count()}  products={products_raw.count()}  orders={orders_raw.count()}")
# customers=4  products=4  orders=8
```

### Step 1 — bronze: land it unchanged, plus metadata

```python
def to_bronze(df, source_name: str):
    return (df
            .withColumn("ingestion_timestamp", current_timestamp())
            .withColumn("source_system", lit(source_name)))

to_bronze(customers_raw, "sales_crm").write.mode("overwrite").parquet("data/bronze/customers")
to_bronze(products_raw,  "product_master").write.mode("overwrite").parquet("data/bronze/products")
to_bronze(orders_raw,    "sales_crm").write.mode("overwrite").parquet("data/bronze/orders")
```

Bronze rule: **do not fix anything here.** Bronze is the faithful, replayable record of what the
source actually sent — including the duplicate and the orphan. If you clean in bronze you destroy
your own evidence.

```python
bronze_orders = spark.read.parquet("data/bronze/orders")
print(bronze_orders.count())   # 8  <- the duplicate is still present, as intended
```

### Step 2 — silver: standardise, type, deduplicate, flag

```python
# 2a. Standardise text
customers_std = (customers_raw
                 .withColumn("customer_name", initcap(trim(col("customer_name"))))
                 .withColumn("region", initcap(trim(col("region")))))

products_std = (products_raw
                .withColumn("product_name", initcap(trim(col("product_name"))))
                .withColumn("category", initcap(trim(col("category")))))

orders_std = orders_raw.withColumn("status", initcap(trim(col("status"))))

# 2b. Deduplicate orders - keep the latest updated_at per order_id
dedup_window = Window.partitionBy("order_id").orderBy(col("updated_at").desc())

orders_dedup = (orders_std
                .withColumn("row_num", row_number().over(dedup_window))
                .filter(col("row_num") == 1)
                .drop("row_num"))

print(f"before dedup: {orders_std.count()}   after dedup: {orders_dedup.count()}")
# before dedup: 8   after dedup: 7
```

```python
# 2c. Add the quality verdict
valid_statuses = ["Completed", "Cancelled", "Pending"]

customer_keys = customers_std.select(col("customer_id").alias("ref_customer_id")).distinct()
product_keys  = products_std.select(col("product_id").alias("ref_product_id")).distinct()

silver_orders = (
    orders_dedup
    .join(customer_keys, orders_dedup.customer_id == col("ref_customer_id"), "left")
    .join(product_keys,  orders_dedup.product_id  == col("ref_product_id"),  "left")
    .withColumn(
        "data_quality_status",
        when(col("ref_customer_id").isNull(), lit("INVALID_CUSTOMER"))
        .when(col("ref_product_id").isNull(), lit("INVALID_PRODUCT"))
        .when(col("amount").isNull(), lit("MISSING_AMOUNT"))
        .when(~col("status").isin(valid_statuses), lit("INVALID_STATUS"))
        .otherwise(lit("VALID"))
    )
    .drop("ref_customer_id", "ref_product_id")
)

silver_orders.select("order_id", "status", "amount", "data_quality_status").orderBy("order_id").show()
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

```python
silver_orders.write.mode("overwrite").parquet("data/silver/orders")
customers_std.write.mode("overwrite").parquet("data/silver/customers")
products_std.write.mode("overwrite").parquet("data/silver/products")
```

### Step 3 — gold dimensions

```python
dim_customer = (
    customers_std
    .withColumn("customer_key", col("customer_id"))      # natural key as surrogate for this scale
    .select("customer_key", "customer_id", "customer_name", "region", "customer_created_date")
)

dim_product = (
    products_std
    .withColumn("product_key", col("product_id"))
    .select("product_key", "product_id", "product_name", "category", "unit_price")
)
```

```text
dim_customer                                  dim_product
+------------+-------------+------+           +-----------+--------------+-----------+
|customer_key|customer_name|region|           |product_key|  product_name|   category|
+------------+-------------+------+           +-----------+--------------+-----------+
|           1|   Aisha Khan| North|           |       1001|    Laptop Bag|Accessories|
|           2|    Ben Smith| South|           |       1002|Wireless Mouse|Accessories|
|           3|  Carla Jones| North|           |       1003|       Monitor|   Hardware|
|           4|    David Lee|  West|           |       1004|      Keyboard|Accessories|
+------------+-------------+------+           +-----------+--------------+-----------+
```

`dim_customer` keeps **all four** customers, including David Lee who never ordered. Dimensions
describe the world; facts record events. A customer with no orders is still a customer.

```python
from pyspark.sql.functions import explode, sequence, to_date, date_format, year, month, dayofmonth, quarter, dayofweek

date_bounds = silver_orders.select(
    spark_min("order_date").alias("min_date"),
    spark_max("order_date").alias("max_date"),
).collect()[0]       # safe: exactly one tiny row

dim_date = (
    spark.sql(f"SELECT sequence(to_date('{date_bounds.min_date}'), "
              f"to_date('{date_bounds.max_date}'), interval 1 day) AS date_seq")
    .select(explode(col("date_seq")).alias("date"))
    .withColumn("date_key", date_format(col("date"), "yyyyMMdd").cast("int"))
    .withColumn("year", year(col("date")))
    .withColumn("quarter", quarter(col("date")))
    .withColumn("month", month(col("date")))
    .withColumn("day", dayofmonth(col("date")))
    .withColumn("day_of_week", dayofweek(col("date")))
    .select("date_key", "date", "year", "quarter", "month", "day", "day_of_week")
)

print(dim_date.count())   # 42 - every day from 2026-01-05 to 2026-02-15 inclusive
```

A date dimension must be **dense** (every day, not just days with orders), otherwise a report
cannot show a zero-sales Tuesday.

### Step 4 — gold fact: the business rule in code

```python
fact_sales = (
    silver_orders
    .filter(col("data_quality_status") == "VALID")   # excludes 105 and 106
    .filter(col("status") == "Completed")            # excludes 103
    .join(dim_customer, silver_orders.customer_id == dim_customer.customer_key, "inner")
    .join(dim_product,  silver_orders.product_id  == dim_product.product_key,  "inner")
    .withColumn("date_key", date_format(col("order_date"), "yyyyMMdd").cast("int"))
    .select(
        col("order_id"),
        col("customer_key"),
        col("product_key"),
        col("date_key"),
        col("order_date"),
        col("quantity"),
        col("amount"),
    )
)

fact_sales.orderBy("order_id").show()
```

```text
+--------+------------+-----------+--------+----------+--------+------+
|order_id|customer_key|product_key|date_key|order_date|quantity|amount|
+--------+------------+-----------+--------+----------+--------+------+
|     101|           1|       1003|20260105|2026-01-05|       1|200.00|
|     102|           1|       1002|20260110|2026-01-10|       2| 50.00|
|     104|           3|       1004|20260201|2026-02-01|       1| 75.00|
|     107|           1|       1003|20260215|2026-02-15|       1|200.00|
+--------+------------+-----------+--------+----------+--------+------+
```

Four rows. Exactly the four the business rule predicts.

### Step 5 — prove the number

```python
total = fact_sales.agg(spark_sum("amount").alias("recognised_revenue")).collect()[0][0]
print(total)        # Decimal('525.00')

assert total == Decimal("525.00"), f"Revenue regression! expected 525.00, got {total}"
```

```text
+------------------+
|recognised_revenue|
+------------------+
|            525.00|
+------------------+
```

### Step 6 — reconcile from raw to gold

This table belongs in `docs/` and in every handover conversation you ever have:

| Stage | Rows | Amount | What changed |
|---|---|---|---|
| Raw orders | 8 | 825.00 | Everything as received |
| After dedup | 7 | 625.00 | Removed the second order 107 row (−200.00) |
| After `VALID` filter | 5 | 575.00 | Removed 105 (NULL amount) and 106 (invalid customer, −50.00) |
| After `Completed` filter | 4 | **525.00** | Removed 103 (Cancelled, −50.00) |

Every subtraction is explainable to a finance stakeholder in one sentence. That — not elegant
code — is what makes a data engineer trusted.

### Step 7 — the quality summary

```python
from pyspark.sql.functions import current_date

def build_quality_summary(spark, orders_raw, orders_dedup, customers, products):
    """Return one row per check with schema: check_name, check_status, failed_record_count, processing_date."""
    duplicate_count = orders_raw.count() - orders_dedup.count()
    missing_amount  = orders_dedup.filter(col("amount").isNull()).count()
    invalid_cust    = orders_dedup.join(customers, on="customer_id", how="left_anti").count()
    invalid_prod    = orders_dedup.join(products,  on="product_id",  how="left_anti").count()
    invalid_status  = orders_dedup.filter(~col("status").isin("Completed", "Cancelled", "Pending")).count()

    rows = [
        ("duplicate_order_ids",        duplicate_count),
        ("missing_mandatory_fields",   missing_amount),
        ("invalid_customer_refs",      invalid_cust),
        ("invalid_product_refs",       invalid_prod),
        ("invalid_order_status",       invalid_status),
    ]

    return (spark.createDataFrame(rows, "check_name string, failed_record_count int")
            .withColumn("check_status", when(col("failed_record_count") == 0, lit("PASS")).otherwise(lit("FAIL")))
            .withColumn("processing_date", current_date())
            .select("check_name", "check_status", "failed_record_count", "processing_date"))
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

Exactly **one** duplicate, **one** missing amount, **one** invalid customer reference, **zero**
invalid product references, **zero** invalid statuses. That matches the documented defect list
precisely, which is how you know your checks are measuring what you think they are measuring.

**How this maps to the project:** this is the summary DataFrame returned by
`src/quality_checks.py` with schema `check_name, check_status, failed_record_count,
processing_date`, written to `QUALITY_RESULTS_DIR`, and executed by
`notebooks/07_data_quality_checks.py`.

### Where this runs in the project

| Notebook | Does |
|---|---|
| `notebooks/01_bronze_ingestion.py` | Steps 0–1 |
| `notebooks/02`–`05` | Step 2 (silver standardisation, typing, dedup, flags) and gold dimensions |
| `notebooks/06_*` | Step 4 (`fact_sales`) |
| `notebooks/07_data_quality_checks.py` | Step 7 |
| `sql/` | Reporting queries over gold — revenue by region, category, month |
| `orchestration/pipeline_design.md` | How these steps are sequenced and scheduled |

---

## 17. Performance habits without over-optimising

> **First rule:** do not optimise until something is slow, and never without measuring. Premature
> optimisation in Spark usually means adding `.cache()` and `.repartition()` everywhere, which
> makes jobs *slower* and plans unreadable.

### 1. Partition sizing

Aim for roughly **128 MB per partition** after decompression.

```python
print(df.rdd.getNumPartitions())
```

| Symptom | Cause | Fix |
|---|---|---|
| Thousands of tasks finishing in <100 ms each | Partitions too small | `coalesce(n)`; lower `spark.sql.shuffle.partitions` |
| A handful of tasks running for an hour | Partitions too large, or data skew | `repartition(n)`; salt the skewed key |
| One task in a stage takes 50x the others | **Skew** — one key dominates | Salting, or enable AQE skew join handling |

Adaptive Query Execution (on by default in Spark 3.2+) coalesces shuffle partitions at runtime and
handles some skew automatically:

```python
spark.conf.set("spark.sql.adaptive.enabled", "true")
spark.conf.set("spark.sql.adaptive.coalescePartitions.enabled", "true")
spark.conf.set("spark.sql.adaptive.skewJoin.enabled", "true")
```

### 2. Never `collect()` large data

```python
# BAD - pulls every row into driver memory
all_rows = big_df.collect()

# GOOD - keep the work distributed
big_df.write.mode("overwrite").parquet("data/gold/result")

# GOOD - bring back only a bounded, tiny result
total = big_df.agg(spark_sum("amount")).collect()[0][0]
sample = big_df.limit(20).toPandas()
```

Collecting a single aggregate row (as in step 5) is perfectly safe. Collecting a billion rows is
how you learn what an `OutOfMemoryError` in the driver looks like.

### 3. Broadcast joins for small dimensions

If one side of a join fits comfortably in executor memory, Spark can **broadcast** it: send a full
copy to every executor and join locally. **No shuffle at all.**

```python
from pyspark.sql.functions import broadcast

enriched = fact_sales.join(broadcast(dim_customer), on="customer_key", how="inner")
```

Spark auto-broadcasts tables under `spark.sql.autoBroadcastJoinThreshold` (default 10 MB):

```python
spark.conf.set("spark.sql.autoBroadcastJoinThreshold", 50 * 1024 * 1024)   # 50 MB
```

This is the single highest-value Spark optimisation, because star-schema joins (giant fact,
small dimensions) are overwhelmingly the common case. Do **not** broadcast something large — you
will OOM every executor simultaneously.

### 4. Cache only what you reuse

```python
silver_orders.cache()
silver_orders.count()            # materialises the cache

quality_summary = build_quality_summary(...)   # reuse 1
fact_sales      = build_fact_sales(...)        # reuse 2
report          = build_report(...)            # reuse 3

silver_orders.unpersist()        # free it when done
```

Rules: cache only a DataFrame used **2+ times**; it must fit in memory; always `unpersist()`.
Caching a DataFrame used once makes the job strictly slower — you pay the write cost for a read
that never happens.

| Storage level | Meaning |
|---|---|
| `MEMORY_ONLY` | Fast; recompute if evicted |
| `MEMORY_AND_DISK` (default for `cache()` on DataFrames) | Spill to disk instead of recomputing |
| `DISK_ONLY` | For expensive-to-recompute, memory-hungry data |

### 5. Filter early, project early (predicate and projection pushdown)

```python
# BAD - join 8 columns of everything, then throw most of it away
result = orders.join(customers, "customer_id").filter(col("status") == "Completed") \
               .select("order_id", "customer_name", "amount")

# GOOD - shrink both sides before the shuffle
orders_slim = orders.filter(col("status") == "Completed") \
                    .select("order_id", "customer_id", "amount")
cust_slim   = customers.select("customer_id", "customer_name")
result      = orders_slim.join(cust_slim, "customer_id")
```

Catalyst often does this for you — but not always (user-defined functions and some sources block
pushdown), and explicit code is self-documenting. With Parquet, filters on partition columns skip
whole directories and filters on other columns skip row groups via min/max statistics.

Verify pushdown actually happened:

```python
orders.filter(col("status") == "Completed").explain(True)
# Look for "PushedFilters: [IsNotNull(status), EqualTo(status,Completed)]"
```

### 6. Prefer built-ins over Python UDFs

```python
# SLOW - Python UDF: serialise each row to a Python worker and back
from pyspark.sql.types import StringType
from pyspark.sql.functions import udf

@udf(returnType=StringType())
def classify_slow(amount):
    return "HIGH" if amount is not None and amount >= 100 else "LOW"

# FAST - native expression: stays in the JVM, benefits from codegen
from pyspark.sql.functions import when, col, lit
classify_fast = when(col("amount") >= 100, lit("HIGH")).otherwise(lit("LOW"))

df.withColumn("band", classify_fast)
```

A Python UDF is a black box to Catalyst: no pushdown, no codegen, plus per-row serialisation
across a process boundary. Expect **2x–100x** slowdowns. If you truly need Python logic, use a
**pandas UDF** (vectorised, Arrow-based), which is far cheaper:

```python
import pandas as pd
from pyspark.sql.functions import pandas_udf

@pandas_udf("string")
def classify_vectorised(amount: pd.Series) -> pd.Series:
    return (amount >= 100).map({True: "HIGH", False: "LOW"})
```

Order of preference: **built-in function → SQL expression → pandas UDF → Python UDF**.

### 7. Write good files

- Target 128 MB – 1 GB per output file.
- Use `coalesce` before writing small results.
- Compact small files periodically (in Delta: `OPTIMIZE` — see guide `07`).
- Snappy compression is the sane default: fast, splittable, good enough.

---

## 18. Common beginner mistakes

| # | Mistake | What goes wrong | Do this instead |
|---|---|---|---|
| 1 | `inferSchema=True` in a pipeline | Types drift between runs; money becomes `double` | Declare `StructType` with `DecimalType` for money |
| 2 | `from pyspark.sql.functions import *` | Shadows `sum`, `min`, `max`, `round`, `count` | Import explicitly and alias: `sum as spark_sum` |
| 3 | `col("a") == 1 & col("b") == 2` | Operator precedence error, cryptic exception | `(col("a") == 1) & (col("b") == 2)` |
| 4 | `df.collect()` on a large DataFrame | Driver OOM, whole app dies | Write to storage, or aggregate first |
| 5 | `dropDuplicates(["order_id"])` for CDC data | Keeps an arbitrary (possibly stale) row | `row_number()` over `orderBy(updated_at.desc())` |
| 6 | `rank()` instead of `row_number()` for dedup | Ties both survive; duplicates remain | Use `row_number()` |
| 7 | `na.fill(0)` on a money column | "Unknown" silently becomes "free"; reports lie | Exclude and report the null |
| 8 | Inner join without checking counts | Rows silently disappear (order 106) | `left` join + `left_anti` check + count assert |
| 9 | `partitionBy("order_id")` | Millions of tiny files; cluster crawls | Partition on low-cardinality, frequently filtered columns |
| 10 | `.cache()` on everything | Memory pressure, eviction thrash, slower jobs | Cache only what is reused 2+ times, then `unpersist()` |
| 11 | `.repartition()` "to be safe" | A full shuffle you did not need | Only repartition with a measured reason |
| 12 | Python UDF for simple logic | 10x+ slowdown, no optimisation | `when/otherwise` and built-ins |
| 13 | `Window.orderBy()` without `partitionBy` on big data | All rows funnel into one partition | Partition by a business key |
| 14 | Assuming `df` is mutable | `df.withColumn(...)` result discarded | Reassign: `df = df.withColumn(...)` |
| 15 | Testing only on the happy path | The duplicate/orphan bugs reach production | Keep deliberately dirty rows in test fixtures |
| 16 | Hard-coded paths everywhere | Nothing is portable between machines | Centralise in `src/config.py` |
| 17 | Timing a job that never ran | Lazy evaluation means nothing happened yet | Trigger an action before timing |
| 18 | `float` for currency | `0.1 + 0.2 != 0.3`; pennies drift | `DecimalType(10, 2)` |

---

## 19. Debugging PySpark

### `printSchema()` — always your first move

```python
orders_df.printSchema()
```

Before blaming your logic, confirm the types. Perhaps 80% of "my join returns nothing" bugs are a
`string` key joined to an `integer` key.

### `show()` with sensible arguments

```python
df.show()                              # 20 rows, truncated at 20 chars
df.show(5)                             # 5 rows
df.show(truncate=False)                # full column values - essential for timestamps and paths
df.show(n=10, truncate=False, vertical=True)   # one field per line - great for wide tables
```

```text
-RECORD 0------------------------------
 order_id            | 101
 customer_id         | 1
 product_id          | 1003
 order_date          | 2026-01-05
 quantity            | 1
 amount              | 200.00
 status              | Completed
 updated_at          | 2026-01-05 10:00:00
 data_quality_status | VALID
```

### `count()` at every stage

```python
print(f"raw         : {orders_raw.count()}")        # 8
print(f"deduplicated: {orders_dedup.count()}")      # 7
print(f"valid       : {silver_orders.filter(col('data_quality_status') == 'VALID').count()}")  # 5
print(f"fact_sales  : {fact_sales.count()}")        # 4
```

Row counts between stages are the cheapest bug detector in existence. When a number surprises you,
you have found your bug's neighbourhood.

### `explain()` — read the physical plan

```python
fact_sales.explain()           # physical plan only
fact_sales.explain(True)       # parsed -> analysed -> optimised -> physical
fact_sales.explain("formatted")  # the most readable form
```

What to look for:

| In the plan | Means |
|---|---|
| `BroadcastHashJoin` | Small side broadcast — fast, no shuffle |
| `SortMergeJoin` | Both sides shuffled and sorted — the expensive path |
| `Exchange hashpartitioning(...)` | **A shuffle.** Count these; each one is a stage boundary |
| `PushedFilters: [...]` | Predicate pushdown worked |
| `ReadSchema: struct<order_id:int,...>` | Column pruning — check only needed columns appear |
| `PartitionFilters: [...]` | Partition pruning worked |
| `WholeStageCodegen` | Operators fused into one compiled loop — good |

### The Spark UI at `localhost:4040`

While a local session is alive, open **http://localhost:4040** in a browser. (If 4040 is taken by
another session, Spark uses 4041, 4042, …; the startup log tells you which.)

| Tab | Use it to answer |
|---|---|
| **Jobs** | Which action triggered what, and how long each job took |
| **Stages** | Where the time went; shuffle read/write bytes; **task duration distribution** |
| **Storage** | What is actually cached and how much memory it occupies |
| **SQL / DataFrame** | A visual DAG of your query with row counts per operator |
| **Executors** | Memory and GC pressure; whether an executor died |
| **Environment** | Every config value actually in effect — settles arguments fast |

The highest-value habit: in **Stages**, look at the min / median / max task duration. If max is
wildly larger than median, you have **skew**, and no amount of adding executors will help.

Keep the session alive to inspect the UI after a script ends:

```python
input("Press Enter to stop Spark and close the UI...")
spark.stop()
```

### Logging instead of `print`

```python
import logging

logger = logging.getLogger(__name__)
logger.info("Silver orders written: %s rows", silver_orders.count())
logger.warning("Quality check FAILED: %s had %s bad rows", check_name, failed_count)
```

`print` vanishes in a scheduled job. Structured logs with timestamps and levels survive, get
shipped to a log aggregator, and can trigger alerts.

**How this maps to the project:** `../customer-sales-lakehouse/src/logging_utils.py` configures a
standard-library logger that writes to both the console and `LOGS_DIR`, so every notebook run
leaves an auditable trail on disk.

### A quick debugging checklist

1. `printSchema()` — are the types what you believe?
2. `count()` before and after the suspicious step — did rows appear or vanish?
3. `show(truncate=False)` on 5 rows — does the data *look* right?
4. Null profile — is a key column unexpectedly null?
5. `left_anti` join — are there orphan keys breaking your join?
6. `explain("formatted")` — is the plan doing what you intended?
7. Spark UI Stages tab — is one task dominating the runtime?

---

## 20. Hands-on exercises

Work these against `../customer-sales-lakehouse`. No answers are provided — **predict the result
before you run the code**, then check. The prediction step is where the learning happens.

1. **Session and environment.** Write a `get_spark_session()` function with `appName`,
   `local[*]`, `spark.sql.shuffle.partitions = 4` and UTC timezone. Print `spark.version` and
   `java -version`, and record both in a short note in `docs/`.

2. **Schemas.** Define `CUSTOMER_SCHEMA`, `PRODUCT_SCHEMA` and `ORDER_SCHEMA` as `StructType`
   objects. Read all three CSVs twice — once with `inferSchema=True`, once with your explicit
   schemas — and produce a table comparing the resulting types column by column. Explain in
   writing which differences would cause a production incident.

3. **Null profile.** Write a reusable `null_profile(df)` function returning one row with a null
   count per column. Run it on all three raw DataFrames and confirm that `orders.amount` is the
   only non-zero cell.

4. **Referential integrity.** Use `left_anti` joins to find orders whose `customer_id` has no match
   and orders whose `product_id` has no match. Confirm you get exactly 1 row and exactly 0 rows
   respectively, and print the offending `order_id`.

5. **All six join types.** Join `orders` to `customers` with `inner`, `left`, `right`, `full`,
   `left_semi` and `left_anti`. Record the row count for each in a markdown table and write one
   sentence per join explaining *why* that count is what it is.

6. **Deduplication, two ways.** Deduplicate orders using (a) `dropDuplicates(["order_id"])` and
   (b) `row_number()` over `Window.partitionBy("order_id").orderBy(col("updated_at").desc())`.
   Confirm both give 7 rows here. Then modify your copy of the data so the second 107 row has
   `updated_at = 2026-02-16 09:00:00` and `status = Cancelled`, rerun both, and explain which
   approach you would defend in a code review.

7. **The wrong window function.** Replace `row_number()` with `rank()` in the dedup logic. Report
   the resulting row count and the resulting revenue figure, and explain in two sentences exactly
   why it differs from 525.00.

8. **Build `fact_sales`.** Implement the full raw → bronze → silver → gold flow and assert that
   `fact_sales` has 4 rows and sums to `Decimal("525.00")`. Make the assertion fail deliberately
   (e.g. remove the `Completed` filter) and confirm your error message is genuinely useful.

9. **Reconciliation table.** Produce the four-stage reconciliation (raw → dedup → valid →
   completed) as a DataFrame with columns `stage, row_count, total_amount` and write it to
   `data/quality_results/reconciliation`. Every row-count drop must be explainable in one sentence.

10. **Aggregate three ways.** Compute recognised revenue grouped by (a) region, (b) product
    category, and (c) order month. Assert that each grouping sums to 525.00. Write a short note on
    why cross-checking a total across independent groupings catches join bugs.

11. **Window functions.** Add a running revenue total ordered by `order_date`, a `lag` of the
    previous order amount, and each order's percentage share of the total. Confirm the final
    running total is 525.00 and the percentages sum to approximately 100.

12. **Quality summary.** Implement `build_quality_summary()` returning the schema
    `check_name, check_status, failed_record_count, processing_date`. Confirm you get
    1 / 1 / 1 / 0 / 0 for duplicates, missing fields, invalid customer refs, invalid product refs
    and invalid statuses. Write it to `QUALITY_RESULTS_DIR` as Parquet.

13. **Partitioning experiment.** Write `fact_sales` partitioned by `order_year` and `order_month`,
    then inspect the directory tree. Now write it partitioned by `order_id` and count the
    directories created. Write a paragraph on why the second approach destroys large-scale
    performance.

14. **Read the plan.** Run `.explain("formatted")` on your `fact_sales` build. Count the `Exchange`
    operators, identify each join strategy, and confirm whether `PushedFilters` appears. Then wrap
    `dim_customer` in `broadcast()` and diff the two plans.

15. **Spark UI investigation.** Run the full pipeline with the session kept alive. In the UI,
    record: number of jobs, number of stages, the largest shuffle read in bytes, and the
    min/median/max task duration of your widest stage. Summarise in `docs/`.

16. **UDF versus built-in.** Classify each order as `HIGH` (>= 100) or `LOW` using (a) a Python
    UDF, (b) a `when/otherwise` expression, and (c) a pandas UDF. Time all three on data unioned
    with itself until you have ~1 million rows, and report the ratio.

17. **Make it fail safely.** Add a fifth product row to `products.csv` and a new order referencing
    a `product_id` that does not exist. Rerun your quality checks and confirm
    `invalid_product_refs` flips from `PASS` (0) to `FAIL` (1), and that `fact_sales` revenue is
    unchanged at 525.00. Explain why an invalid product reference should **not** change recognised
    revenue in this design.

18. **Parquet vs CSV.** Write `silver_orders` as both CSV and Parquet. Compare on-disk size,
    read time, and whether a filter on `status` shows `PushedFilters` in the plan. Produce a short
    recommendation.

---

> **Next:** guide `06` moves this pipeline into Azure conceptually (ADLS Gen2, Data Factory,
> Synapse), and guide `07` replaces Parquet with Delta Lake so that overwrites become idempotent
> `MERGE`s — with the transformation logic, the quality checks, and the 525.00 result all unchanged.


