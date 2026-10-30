# Architecture

## Overview

This project implements the **medallion architecture**: data flows through progressively
refined layers, each with a clear contract. Every layer is stored as Parquet on the local
filesystem under `data/`, separated by folder rather than by physical partition columns
(the dataset is tiny, so partitioning would only create small-file overhead).

## Layer contracts

| Layer | Location | Contract |
| --- | --- | --- |
| **Raw** | `data/raw/` | Immutable source CSVs exactly as received. Never written to by the pipeline. |
| **Bronze** | `data/bronze/` | 1:1 copy of raw with audit columns added. No business logic, no filtering, no de-duplication. |
| **Silver** | `data/silver/` | Cleaned and conformed: correct data types, standardised text, de-duplicated business keys, referential-integrity flags. Bad rows are **flagged, not dropped**. |
| **Gold** | `data/gold/` | Business-ready star schema: conformed dimensions plus an additive fact table containing only trusted, recognised revenue. |
| **Quality results** | `data/quality_results/` | Output of the data-quality framework: one row per check per run. |

## Text diagram

```
                ┌──────────────────────────────────────────────┐
                │                 data/raw/                    │
                │   customers.csv  products.csv  orders.csv    │
                └───────────────────────┬──────────────────────┘
                                        │  01_bronze_ingestion.py
                                        │  + ingestion_timestamp
                                        │  + source_file_name
                                        │  + processing_date
                                        ▼
                ┌──────────────────────────────────────────────┐
                │                data/bronze/                  │
                │   customers/     products/      orders/      │
                └───────┬───────────────┬──────────────┬───────┘
                        │               │              │
        02_silver_customers    03_silver_products   04_silver_orders
        cast + standardise     cast + standardise   cast, standardise status,
                        │               │           dedupe order_id,
                        │               │           flag data_quality_status
                        ▼               ▼              ▼
                ┌──────────────────────────────────────────────┐
                │                data/silver/                  │
                │   customers/     products/      orders/      │
                └───────┬───────────────┬──────────────┬───────┘
                        │               │              │
                        └───────┬───────┘              │
                                │ 05_gold_dimensions   │ 06_gold_fact_sales
                                ▼                      ▼
                ┌──────────────────────────────────────────────┐
                │                 data/gold/                   │
                │  dim_customer/  dim_product/  dim_date/      │
                │                 fact_sales/                  │
                └───────────────────────┬──────────────────────┘
                                        │ 07_data_quality_checks.py
                                        ▼
                ┌──────────────────────────────────────────────┐
                │            data/quality_results/             │
                │  check_name | status | failed_count | run_ts │
                └──────────────────────────────────────────────┘
```

## Design decisions

### Why flag instead of drop?

Dropping bad rows in silver destroys evidence. Instead, `04_silver_orders.py` adds a
`data_quality_status` column (`Valid`, `Invalid Customer Reference`, `Missing Amount`).
Analysts can still reconcile source-system totals against silver, while gold stays clean.

### Why overwrite mode?

Each script fully rebuilds its output. For a dataset of this size, full refresh is cheaper
and simpler than merge logic, and it makes every step **idempotent** — re-running produces
identical results. See [orchestration/pipeline_design.md](../orchestration/pipeline_design.md)
for how this would evolve into incremental loads using a Delta `MERGE`.

### Star schema

```
          dim_customer                 dim_product
        (customer_key PK)            (product_key PK)
                │                           │
                └──────────┐   ┌────────────┘
                           ▼   ▼
                     ┌──────────────┐
                     │  fact_sales  │
                     │   order_id   │  degenerate dimension
                     │ customer_key │  FK -> dim_customer
                     │ product_key  │  FK -> dim_product
                     │   date_key   │  FK -> dim_date
                     │   quantity   │  measure (additive)
                     │    amount    │  measure (additive)
                     └───────▲──────┘
                             │
                         dim_date
                      (date_key PK)
```

`fact_sales` is at **order grain**: one row per completed, valid order.
