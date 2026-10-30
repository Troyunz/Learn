# Runbook

Operational guide for running, troubleshooting and recovering the pipeline.

---

## 1. Prerequisites

| Requirement | Notes |
| --- | --- |
| Python **3.9 – 3.11** | `python --version`. **PySpark 3.5.1 does not support Python 3.12 or 3.13** — see the troubleshooting table below. |
| Java 11 or 17 | Required by Spark. `java -version`. Spark 3.5 does **not** officially support Java 21+. |
| `JAVA_HOME` | Must point at the JDK root. |
| Disk | A few MB for the data; ~400 MB for the virtual environment (PySpark ships the Spark distribution). |

Windows users also need `winutils.exe` + `hadoop.dll` on `HADOOP_HOME` for Spark to write
files to the local filesystem. See [Troubleshooting](#4-troubleshooting).

---

## 2. First-time setup

```bash
cd customer-sales-lakehouse

# Must be a Python 3.11 (or older) interpreter
python3.11 -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

Verify the environment before running anything:

```bash
python -c "import sys, pyspark; print(sys.version, pyspark.__version__)"
```

---

## 3. Running the pipeline

Always run from the **project root** — the scripts resolve paths relative to the
repository root via `src/config.py`.

### Full run, in order

```bash
python notebooks/01_bronze_ingestion.py       # raw  -> bronze
python notebooks/02_silver_customers.py       # bronze -> silver.customers
python notebooks/03_silver_products.py        # bronze -> silver.products
python notebooks/04_silver_orders.py          # bronze -> silver.orders  (needs 02)
python notebooks/05_gold_dimensions.py        # silver -> gold dimensions
python notebooks/06_gold_fact_sales.py        # silver + gold dims -> fact_sales
python notebooks/07_data_quality_checks.py    # silver -> quality_results
```

### Dependency order

```
01 ──┬── 02 ──┐
     ├── 03 ──┼── 05 ──┐
     └────────┴── 04 ──┴── 06 ── 07
```

- `02` and `03` are independent of each other and may run in parallel.
- `04` **requires** `02` (it joins to `silver.customers` for the referential check).
- `05` requires `02`, `03` and `04` (`dim_date` is derived from order dates).
- `06` requires `04` and `05`.
- `07` requires `02` and `04`.

### Running the tests

```bash
pytest -q                       # whole suite
pytest tests/test_quality_checks.py -q
pytest -q -k dedup              # single behaviour
```

The test suite builds its own in-memory DataFrames and does **not** read `data/`, so it
can run before the pipeline has ever executed.

---

## 4. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `Python worker exited unexpectedly (crashed)` or `Python worker failed to connect back`, with a `java.io.EOFException` and **no Python traceback** | **Python 3.12 or 3.13.** PySpark 3.5.1 only supports Python 3.9–3.11. The JVM starts fine and `spark.range(...).collect()` even works, but any operation that needs the Python worker (such as `createDataFrame(...).collect()`) crashes silently. | Rebuild the virtual environment on Python 3.11 and reinstall requirements. Also set `PYSPARK_PYTHON` to the venv interpreter so Spark launches the right Python. |
| `java.io.FileNotFoundException: HADOOP_HOME and hadoop.home.dir are unset` when **writing** | Missing Hadoop native shims on Windows. Reads succeed, writes fail. | Download `winutils.exe` + `hadoop.dll` for Hadoop 3.3.x into `C:\hadoop\bin`, set `HADOOP_HOME=C:\hadoop` and add `%HADOOP_HOME%\bin` to `PATH`. |
| `JAVA_HOME is not set` | No JDK, or variable unset. | Install JDK 17 and set `JAVA_HOME`. |
| `UnsupportedClassVersionError` / odd Spark crashes on startup | Java 21+ with Spark 3.5. | Switch to JDK 11 or 17. |
| `Path does not exist: data/bronze/orders` | A prior stage was skipped. | Re-run from `01_bronze_ingestion.py`. |
| `ModuleNotFoundError: No module named 'src'` | Script launched from the wrong directory. | Run from the project root. The scripts insert the project root onto `sys.path`, which only resolves correctly from there. |
| `py4j.protocol.Py4JJavaError ... Permission denied` on write | Output folder locked by another process (often an open Parquet file in an editor). | Close the file / stop other Spark sessions and re-run. |
| Quality run reports `FAIL` for `missing_order_amount` and `invalid_customer_reference` | **Expected.** These defects are planted on purpose. | No action — see `data_quality_rules.md`. |
| Quality run reports `FAIL` for `duplicate_order_id` on silver | De-duplication did not apply. | Confirm `04_silver_orders.py` completed and re-run it. |
| Spark logs flood the console | Default `INFO` log level. | `spark.sparkContext.setLogLevel("WARN")` is already applied in each script; raise to `ERROR` if still noisy. |

### Inspecting output without re-running the pipeline

The Parquet output is easiest to read back through Spark, since that needs no extra
dependencies:

```python
from src.ingestion import get_spark_session, read_parquet

spark = get_spark_session()
read_parquet(spark, "data/silver/orders").show(truncate=False)
read_parquet(spark, "data/quality_results").show(truncate=False)
```

`pandas.read_parquet()` also works, but only after installing a Parquet engine
(`pip install pyarrow`) — it is deliberately **not** in `requirements.txt`.

---

## 5. Safe rerun / recovery

The pipeline is **idempotent**: every write uses `mode("overwrite")`, so a stage fully
replaces its own output. There is no append state and no partial-commit risk beyond a
single stage.

### Rerun rules

| Scenario | Action |
| --- | --- |
| A stage failed mid-run | Re-run **that stage**, then every downstream stage. |
| Raw data changed | Re-run the full pipeline from `01`. |
| Transformation logic changed | Re-run the changed stage and everything downstream. |
| Only quality rules changed | Re-run `07` alone. |

### Full reset

Deleting the generated layers is always safe — `data/raw/` is never modified.

```bash
# Windows PowerShell
Remove-Item -Recurse -Force data\bronze\*, data\silver\*, data\gold\*, data\quality_results\* -Exclude .gitkeep

# macOS / Linux
find data/bronze data/silver data/gold data/quality_results -mindepth 1 ! -name .gitkeep -delete
```

Then re-run the full pipeline.

---

## 6. Validating a successful run

| Check | Expected |
| --- | --- |
| `data/bronze/` | 3 folders: `customers` (4 rows), `products` (4), `orders` (8). |
| `data/silver/customers` | 4 rows; regions `North`, `South`, `West`. |
| `data/silver/products` | 4 rows; categories `Accessories`, `Hardware`. |
| `data/silver/orders` | 7 rows (8 raw rows minus 1 duplicate), flagged 5 `Valid` / 1 `Missing Amount` / 1 `Invalid Customer Reference`. |
| `data/gold/dim_customer` | 4 rows. |
| `data/gold/dim_product` | 4 rows. |
| `data/gold/dim_date` | 42 rows (2026-01-05 → 2026-02-15, contiguous). |
| `data/gold/fact_sales` | 4 rows (orders 101, 102, 104, 107); recognised revenue `525.00`. |
| `data/quality_results` | 4 rows: `duplicate_order_id` PASS, `invalid_status` PASS, `missing_order_amount` FAIL (1), `invalid_customer_reference` FAIL (1). |
| `pytest -q` | 39 passed. |

---

## 7. Configuration

Environment settings live in `config/dev.yml` and `config/prod.yml` and are loaded by
`src/config.py` at import time.

| Key | `dev.yml` | `prod.yml` | Effect |
| --- | --- | --- | --- |
| `environment` | `dev` | `prod` | Label echoed in the logs. |
| `data_root` | `./data` | `/mnt/data` | Root of every lakehouse layer. A relative value is resolved against the **project root**, not the shell's working directory. |
| `log_level` | `INFO` | `WARNING` | Verbosity of the project loggers. |

Select the environment with `LAKEHOUSE_ENV` (defaults to `dev`):

```bash
# Windows PowerShell
$env:LAKEHOUSE_ENV = "prod"; python notebooks\01_bronze_ingestion.py

# macOS / Linux
LAKEHOUSE_ENV=prod python notebooks/01_bronze_ingestion.py
```

### Precedence

Settings resolve highest-first:

1. Environment variables — `LAKEHOUSE_DATA_ROOT`, `LAKEHOUSE_LOG_LEVEL`.
2. `config/<LAKEHOUSE_ENV>.yml`.
3. Built-in defaults — `<project root>/data` and `INFO`.

This means a single run can be redirected without editing any file:

```bash
LAKEHOUSE_DATA_ROOT=/tmp/scratch LAKEHOUSE_LOG_LEVEL=DEBUG python notebooks/01_bronze_ingestion.py
```

A missing YAML file (or a missing `pyyaml`) is tolerated — the pipeline falls back to the
built-in defaults rather than failing, so configuration is an enhancement, not a hard
dependency.

**No credentials are stored in this repository.** If you extend the project to a cloud
object store, supply secrets via environment variables or a secret manager — never commit
them. `.env` is already listed in `.gitignore`.
