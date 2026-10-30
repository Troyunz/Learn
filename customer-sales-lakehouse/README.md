# Customer Sales Lakehouse

A hands-on learning and portfolio project that implements a **medallion lakehouse architecture**
(raw → bronze → silver → gold) over a small customer/product/order dataset.

## Purpose

This project exists to practise core data-engineering skills end to end:

- Ingesting raw CSV files into a **bronze** layer with full audit metadata.
- Cleaning, standardising and de-duplicating data into a **silver** layer.
- Modelling a **gold** star schema (dimensions + fact) ready for analytics.
- Running **data-quality checks** and persisting the results for monitoring.
- Writing reusable, tested Python/PySpark modules instead of one-off notebooks.

The dataset is deliberately small so the whole pipeline runs on a laptop in seconds,
while still exercising realistic patterns.

## Tech stack

| Area | Technology |
| --- | --- |
| Language | Python 3.11+ |
| Processing | PySpark 3.5.x (Spark SQL + DataFrame API) |
| Storage format | Parquet (Delta-style medallion concepts: layered, overwrite-idempotent writes) |
| Analytics | ANSI SQL (PostgreSQL / Spark SQL compatible) |
| Testing | pytest |
| Config | YAML |

> Note: the project uses **Parquet** so it runs with no extra dependencies, but the folder
> layout, layering and write semantics follow Delta Lake / lakehouse conventions.

## Folder structure

```
customer-sales-lakehouse/
├── README.md
├── requirements.txt
├── .gitignore
├── config/                 # Environment configuration (dev / prod)
├── data/
│   ├── raw/                # Source CSV files (checked into Git)
│   ├── bronze/             # Ingested data + audit columns (generated)
│   ├── silver/             # Cleaned, conformed data (generated)
│   ├── gold/               # Star schema: dimensions + facts (generated)
│   └── quality_results/    # Data-quality check output (generated)
├── docs/                   # Architecture, data dictionary, rules, runbook
├── notebooks/              # Notebook-style PySpark scripts (01 … 07)
├── orchestration/          # Pipeline design and execution order
├── sql/                    # Exploration, quality, reporting, DDL
├── src/                    # Reusable Python modules
└── tests/                  # pytest suite
```

## How to run locally

### Prerequisites

| Requirement | Version | Why |
| --- | --- | --- |
| Python | **3.9 – 3.11** (3.11 recommended) | PySpark 3.5.1 does **not** support Python 3.12+. On 3.12/3.13 the Spark Python worker crashes with `Python worker exited unexpectedly`. |
| Java | **11 or 17** (17 recommended) | Required by Spark. Spark 3.5 does not officially support Java 21+. |
| `winutils.exe` + `hadoop.dll` | Hadoop 3.3.x | **Windows only.** Without them Spark can read but not *write* local files. See [docs/runbook.md](./docs/runbook.md). |

> The source code itself uses Python 3.11+ syntax (PEP 604 unions, built-in generics).

### Steps

```bash
# 1. Create and activate a virtual environment (must be Python 3.11 or older)
python3.11 -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run the pipeline in order (from the project root)
python notebooks/01_bronze_ingestion.py
python notebooks/02_silver_customers.py
python notebooks/03_silver_products.py
python notebooks/04_silver_orders.py
python notebooks/05_gold_dimensions.py
python notebooks/06_gold_fact_sales.py
python notebooks/07_data_quality_checks.py

# 4. Run the tests
pytest -q
```

Every script writes with `mode("overwrite")`, so the pipeline is **idempotent** — you can
re-run any step safely without duplicating data.

### Expected output

| Layer | Result |
| --- | --- |
| `data/bronze/` | 4 customers, 4 products, 8 orders (raw fidelity, duplicate retained). |
| `data/silver/orders` | 7 rows — the duplicate `order_id = 107` is removed. |
| | Flags: 5 `Valid`, 1 `Missing Amount`, 1 `Invalid Customer Reference`. |
| `data/gold/fact_sales` | 4 rows (orders 101, 102, 104, 107), recognised revenue **525.00**. |
| `data/quality_results/` | 4 checks: 2 `PASS`, 2 intentional `FAIL`. |

## Known data-quality issues (intentional)

The raw data contains planted defects so the quality framework has something to find:

| Issue | Where | Detail |
| --- | --- | --- |
| Duplicate primary key | `orders.csv` | `order_id = 107` appears twice (exact duplicate row). |
| Missing measure | `orders.csv` | `order_id = 105` has a NULL `amount`. |
| Invalid foreign key | `orders.csv` | `order_id = 106` references `customer_id = 99`, which does not exist in `customers.csv`. |
| Mixed status casing | `orders.csv` | Status values need standardising before analytics. |
| Non-completed orders | `orders.csv` | `Cancelled` and `Pending` orders must be excluded from recognised revenue. |

These rows are **kept** in silver and flagged via `data_quality_status`, then **excluded**
from `fact_sales`. See [docs/data_quality_rules.md](./docs/data_quality_rules.md).
