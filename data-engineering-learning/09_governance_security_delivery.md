# 09 — Governance, Security & Professional Delivery

> **Where this sits in the series**
>
> Guides `00`–`08` taught you how to *build* things: SQL (`01_sql_deep_dive.md`), data modelling, Linux/Git, ETL/ELT and quality (`04_etl_elt_and_quality.md`), PySpark (`05_pyspark_deep_dive.md`), Azure, Databricks/Delta, and dbt/orchestration (`08_dbt_orchestration_monitoring.md`).
>
> This guide teaches you how to **build things that an organisation can trust, audit, secure, and hand over**. It is the difference between "I wrote a pipeline" and "I own a data product."
>
> Companion project: [`../customer-sales-lakehouse`](../customer-sales-lakehouse)

---

## Table of contents

1. [Why governance exists](#1-why-governance-exists)
2. [The anatomy of data governance](#2-the-anatomy-of-data-governance)
3. [Ownership: owners, stewards, custodians](#3-ownership-owners-stewards-custodians)
4. [Lineage, catalogues and glossaries](#4-lineage-catalogues-and-glossaries)
5. [A worked business glossary for this project](#5-a-worked-business-glossary-for-this-project)
6. [Data classification tiers and handling rules](#6-data-classification-tiers-and-handling-rules)
7. [Retention policies](#7-retention-policies)
8. [Security foundations: authN vs authZ](#8-security-foundations-authn-vs-authz)
9. [Access control models and RBAC](#9-access-control-models-and-rbac)
10. [Least privilege and just-in-time access](#10-least-privilege-and-just-in-time-access)
11. [Secrets management](#11-secrets-management)
12. [What to do if a secret is committed](#12-what-to-do-if-a-secret-is-committed)
13. [Dev / test / prod separation](#13-dev--test--prod-separation)
14. [Logging and auditability](#14-logging-and-auditability)
15. [PII handling in these datasets](#15-pii-handling-in-these-datasets)
16. [Writing professional documentation](#16-writing-professional-documentation)
17. [Stakeholder communication checklist](#17-stakeholder-communication-checklist)
18. [Incident and escalation template](#18-incident-and-escalation-template)
19. [Self-assessment and practice exercises](#19-self-assessment-and-practice-exercises)

---

## 1. Why governance exists

### 1.1 The "three conflicting revenue numbers" problem

Picture a Monday morning meeting. Three people present a revenue figure for February 2026.

| Who | Figure presented | How they got it |
|---|---|---|
| Finance analyst | **275.00** | Queried `fact_sales`, filtered `status = 'Completed'`, deduplicated |
| Sales ops | **475.00** | Applied the `Completed` and validity filters correctly, but ran against a build where the **de-duplication step was missing**, so order 107 was counted twice (75.00 + 200.00 + 200.00) |
| A manager's spreadsheet | **550.00** | Exported the raw file and summed every February row: kept the duplicated order 107, kept order 106 (unknown customer 99), and filled order 105's blank `amount` with unit price × quantity = 25.00 (75.00 + 25.00 + 50.00 + 200.00 + 200.00) |

Everyone is "right" according to their own method. Nobody agrees. The meeting stops being about the business and becomes a 45-minute argument about arithmetic.

This is the single most common failure in data work, and it is **not a technical failure**. The SQL was valid in all three cases. What was missing was:

- a **definition** everyone agreed on ("what counts as revenue?"),
- a **single place** that definition is implemented (`fact_sales`),
- an **owner** who is allowed to change that definition,
- **lineage** so you can see where a number came from,
- and **documentation** so a new joiner finds the definition instead of inventing their own.

Governance is the set of practices that stops this from happening.

### 1.2 In our project, the agreed number is 525.00

For the `customer-sales-lakehouse` sample data, we publish exactly one canonical answer:

> **Recognised revenue = 525.00**, from order IDs `101`, `102`, `104`, `107`.

Derived from the canonical business rule:

> `fact_sales` contains **only valid, de-duplicated, `Completed` orders.**

| Order | Amount | Included? | Reason |
|---|---|---|---|
| 101 | 200.00 | ✅ | Valid, Completed |
| 102 | 50.00 | ✅ | Valid, Completed |
| 103 | 50.00 | ❌ | Status = `Cancelled` |
| 104 | 75.00 | ✅ | Valid, Completed |
| 105 | *(null)* | ❌ | Status = `Pending` **and** missing mandatory `amount` |
| 106 | 50.00 | ❌ | `customer_id = 99` does not exist in `customers.csv` |
| 107 | 200.00 | ✅ | Valid, Completed — counted **once** |
| 107 (dup) | 200.00 | ❌ | Duplicate row removed by de-duplication |
| **Total** | | | **525.00** |

Every report, dashboard, notebook and conversation in this project uses 525.00. If someone produces a different number, that is an **incident**, not a difference of opinion. Section 18 gives you the template for handling exactly that.

### 1.3 What happens without governance

| Symptom | Root cause | Cost |
|---|---|---|
| Conflicting numbers in meetings | No agreed definitions | Lost trust; decisions delayed |
| "Who owns this table?" silence | No ownership register | Nobody fixes broken pipelines |
| A table nobody dares delete | No lineage, no catalogue | Storage cost, migration risk |
| Secret leaked in a notebook | No secrets policy | Breach, legal exposure |
| Customer names in a log file | No PII standard | Regulatory penalty |
| Prod broken by a "small test" | No environment separation | Outage, data corruption |
| New joiner takes 6 weeks to be useful | No documentation | Salary burn, key-person risk |
| Can't answer "why did this number change?" | No audit trail | Audit failure |

Governance is often sold as bureaucracy. Reframe it: governance is **the set of answers to questions people will definitely ask you later**, written down before they ask.

### 1.4 Governance is proportional

A four-person startup does not need a 60-page policy. It needs:

- a README that states definitions,
- one owner per dataset,
- secrets out of code,
- separate dev and prod,
- a test that asserts revenue = 525.00.

That's it. Scale the ceremony to the risk. The *principles* below apply at every size; the *formality* should not.

---

## 2. The anatomy of data governance

Think of governance as six interlocking pillars.

```text
┌──────────────────────────────────────────────────────────────┐
│                      DATA GOVERNANCE                         │
├────────────┬────────────┬────────────┬───────────┬───────────┤
│ Ownership  │ Definition │  Lineage   │  Quality  │ Security  │
│  Who is    │  What the  │ Where did  │ Is it fit │ Who may   │
│ accountable│ term means │ it come    │ for use?  │ see it?   │
│     ?      │            │   from?    │           │           │
├────────────┴────────────┴────────────┴───────────┴───────────┤
│                        Lifecycle                             │
│    Creation → Use → Change control → Archive → Deletion      │
└──────────────────────────────────────────────────────────────┘
```

| Pillar | The question it answers | Where it lives in our project |
|---|---|---|
| Ownership | Who is accountable for this dataset? | `docs/data_dictionary.md` header, README |
| Definition | What does "Recognised Revenue" mean? | Business glossary (Section 5), `docs/transformation_rules.md` |
| Lineage | Which raw file produced this column? | `docs/architecture.md`, notebook numbering `01`→`07` |
| Quality | Can I rely on this number? | `docs/data_quality_rules.md`, `data/quality_results/` |
| Security | Who may read `customer_name`? | Classification tiers (Section 6) |
| Lifecycle | When is this deleted? | Retention policy (Section 7) |

### 2.1 Governance artefacts you should be able to name in an interview

- **Data catalogue** — searchable inventory of datasets.
- **Business glossary** — agreed definitions of business terms.
- **Data dictionary** — column-level technical description of a dataset.
- **Lineage graph** — the directed graph from source to report.
- **Data contract** — an agreement between producer and consumer about schema, SLA and semantics.
- **Retention schedule** — how long each class of data is kept.
- **Access matrix** — which role can do what to which dataset.
- **RACI** — who is Responsible, Accountable, Consulted, Informed.

A data dictionary and a business glossary are **not the same thing**. The dictionary says `amount DECIMAL(10,2) — order line value in GBP`. The glossary says `Recognised Revenue — the sum of amount for valid, de-duplicated, Completed orders`. One is technical, one is semantic. You need both.

---

## 3. Ownership: owners, stewards, custodians

### 3.1 The three roles

| Role | Mindset | Typical job title | Decides |
|---|---|---|---|
| **Data owner** | "This data serves my business area." | Head of Sales, Finance Director | What the data *means*; who may access it; retention; sign-off on definition changes |
| **Data steward** | "I keep it correct and documented." | Senior analyst, analytics engineer | Quality rules, glossary entries, issue triage, data dictionary upkeep |
| **Data custodian** | "I keep it running and safe." | Data engineer, platform engineer | Pipelines, storage, backups, access *implementation*, monitoring |

The crucial distinction:

- The **owner** decides *that* cancelled orders are excluded from revenue.
- The **steward** writes that down, checks it stays true, and chases exceptions.
- The **custodian** implements it in `src/transformations.py` and makes sure the job runs.

A custodian who silently changes a business rule has overstepped. A data engineer who notices that excluding cancelled orders is wrong should **raise it with the owner**, not quietly change the filter.

### 3.2 RACI for the `customer-sales-lakehouse` project

**R** = Responsible (does the work) · **A** = Accountable (one person, final say) · **C** = Consulted · **I** = Informed

| Activity | Data owner | Data steward | Data engineer (custodian) | Platform/security | Consumer (analyst) |
|---|---|---|---|---|---|
| Define "Recognised Revenue" | **A** | R | C | I | C |
| Approve a change to that definition | **A** | R | C | I | I |
| Maintain `docs/data_dictionary.md` | I | **A/R** | C | — | I |
| Write quality rules in `docs/data_quality_rules.md` | C | **A** | R | — | C |
| Implement `07_data_quality_checks.py` | I | C | **A/R** | I | I |
| Build bronze → silver → gold notebooks | I | C | **A/R** | I | I |
| Grant access to gold tables | **A** | C | R | C | I |
| Grant access to raw PII | **A** | C | R | **C** | — |
| Rotate secrets | I | I | R | **A** | — |
| Set retention periods | **A** | C | R | C | I |
| Run the pipeline on schedule | I | I | **A/R** | C | I |
| Respond to a failed quality check | I | **A** | R | I | C |
| Declare a data incident | **A** | R | R | C | I |
| Write the runbook `docs/runbook.md` | I | C | **A/R** | C | I |
| Sign off production release | **A** | C | R | C | I |

> **Rule of thumb:** exactly one **A** per row. If you can't name the single accountable person, the row is not governed.

### 3.3 Writing an ownership header

Put this at the top of every dataset doc. Example for `gold.fact_sales`:

```markdown
## fact_sales

| Field | Value |
|---|---|
| Layer | Gold |
| Data owner | <role: Head of Sales Operations> |
| Data steward | <role: Sales Analytics Lead> |
| Technical custodian | <role: Data Engineering team> |
| Classification | Internal |
| Source(s) | silver.orders, silver.customers, silver.products |
| Refresh | Daily, 02:00 local |
| SLA | Available by 06:00; max staleness 24h |
| Quality gate | All checks in docs/data_quality_rules.md PASS |
| Retention | 7 years |
```

Use **roles, not personal names**. People leave; roles persist.

---

## 4. Lineage, catalogues and glossaries

### 4.1 Lineage: the "where did this number come from?" chain

Lineage answers two questions:

- **Upstream / impact-in:** if `orders.csv` changes, what breaks?
- **Downstream / impact-out:** if I change `fact_sales`, who is affected?

Our project's lineage, end to end:

```text
data/raw/customers.csv ──► 01_bronze_ingestion.py ──► data/bronze/customers/
data/raw/products.csv  ──► 01_bronze_ingestion.py ──► data/bronze/products/
data/raw/orders.csv    ──► 01_bronze_ingestion.py ──► data/bronze/orders/
                                                           │
       ┌───────────────────────────────────────────────────┤
       ▼                      ▼                            ▼
02_silver_customers.py  03_silver_products.py      04_silver_orders.py
       │                      │                            │
       ▼                      ▼                            ▼
 silver/customers        silver/products              silver/orders
       │                      │                            │
       └──────────┬───────────┘                            │
                  ▼                                        │
        05_gold_dimensions.py                              │
   dim_customer, dim_product, dim_date                     │
                  │                                        │
                  └──────────────┬─────────────────────────┘
                                 ▼
                      06_gold_fact_sales.py
                           gold/fact_sales
                                 │
                                 ▼
                sql/03_reporting_queries.sql  →  525.00
```

### 4.2 Column-level lineage

Table-level lineage is a start; column-level lineage is what auditors want.

| Gold column | Silver source | Bronze source | Raw source column | Transformation |
|---|---|---|---|---|
| `fact_sales.order_id` | `silver.orders.order_id` | `bronze.orders.order_id` | `orders.csv:order_id` | Cast to INT; duplicates removed |
| `fact_sales.sale_amount` | `silver.orders.amount` | `bronze.orders.amount` | `orders.csv:amount` | Cast to DECIMAL(10,2); NOT NULL enforced |
| `fact_sales.customer_key` | `silver.orders.customer_id` | `bronze.orders.customer_id` | `orders.csv:customer_id` | Looked up in `dim_customer`; unmatched rows rejected |
| `fact_sales.product_key` | `silver.orders.product_id` | `bronze.orders.product_id` | `orders.csv:product_id` | Looked up in `dim_product` |
| `fact_sales.date_key` | `silver.orders.order_date` | `bronze.orders.order_date` | `orders.csv:order_date` | `yyyyMMdd` integer derived from date |
| `dim_customer.customer_region` | `silver.customers.region` | `bronze.customers.region` | `customers.csv:region` | Trimmed, title-cased |

Keep this table in `docs/data_dictionary.md`. When someone asks "why is North 525.00?", you can trace every hop in under a minute.

### 4.3 Manual lineage when you have no tooling

You do not need a commercial catalogue to have lineage. In this project lineage is encoded three ways:

1. **Notebook numbering** — `01` → `07` is literally the execution order and the dependency order.
2. **Folder structure** — `bronze/`, `silver/`, `gold/` enforce direction of flow.
3. **The diagram above**, checked into `docs/architecture.md`.

That's free, version-controlled, and reviewable in a pull request. Tooling helps at scale; discipline helps at every scale.

### 4.4 Data catalogue entry template

```markdown
### Dataset: gold.fact_sales

**Description:** One row per valid, de-duplicated, Completed sales order line.
**Grain:** One row per `order_id`.
**Row count (sample):** 4
**Owner:** <role: Head of Sales Operations>
**Classification:** Internal
**Refresh:** Daily 02:00
**Known limitations:** Sample data only; no returns or refunds modelled.
**Example question answered:** "What was February 2026 recognised revenue?" → 275.00
**Upstream:** silver.orders, dim_customer, dim_product, dim_date
**Downstream:** sql/03_reporting_queries.sql, revenue dashboard
```

### 4.5 Why glossaries beat tribal knowledge

Tribal knowledge is the state where the correct definition lives only in one person's head. Symptoms:

- "Ask Priya, she knows how revenue works."
- A Slack thread from 14 months ago is the only source of truth.
- Two dashboards disagree and nobody can say which is right.

A glossary converts tribal knowledge into an asset. It has to be:

- **Discoverable** — in the repo, linked from the README.
- **Versioned** — changes reviewed in a PR.
- **Enforced** — the definition is implemented in exactly one place (`fact_sales`), and a test asserts it.

---

## 5. A worked business glossary for this project

This is the authoritative glossary for `customer-sales-lakehouse`. Store it at `docs/data_dictionary.md` (or a dedicated `docs/glossary.md`) and link it from the README.

---

### Recognised Revenue

| Field | Value |
|---|---|
| **Term** | Recognised Revenue |
| **Definition** | The sum of `amount` across all **Valid Orders** whose `status` is `Completed`, after de-duplication. |
| **Formula** | `SUM(fact_sales.sale_amount)` |
| **Grain** | Can be aggregated by date, month, customer, region, or product |
| **Units** | Currency, 2 decimal places |
| **Value for the sample dataset** | **525.00** |
| **Explicitly excludes** | Cancelled orders; Pending orders; orders failing referential integrity; duplicate rows |
| **Owner** | <role: Head of Sales Operations> |
| **Implemented in** | `notebooks/06_gold_fact_sales.py`, surfaced by `sql/03_reporting_queries.sql` |
| **Common mistake** | Summing `amount` directly from `data/raw/orders.csv`, which yields an inflated figure because it includes cancelled, pending, invalid and duplicated rows |

---

### Valid Order

| Field | Value |
|---|---|
| **Term** | Valid Order |
| **Definition** | An order row that satisfies **all** of: (a) non-null `order_id`, `customer_id`, `product_id`, `order_date`, `quantity`, `amount`; (b) `customer_id` exists in `dim_customer`; (c) `product_id` exists in `dim_product`; (d) `status` ∈ {`Completed`, `Cancelled`, `Pending`}; (e) the row is not a duplicate of an already-accepted `order_id`. |
| **Owner** | <role: Head of Sales Operations> |
| **Steward** | <role: Sales Analytics Lead> |
| **Sample outcome** | Of 8 raw rows: order 105 fails (a) (missing `amount`); order 106 fails (b) (`customer_id = 99` unknown); the second `107` row fails (e). 5 rows remain valid: 101, 102, 103, 104, 107. |
| **Note** | Validity is **not** the same as recognised. Order 103 is a Valid Order but is `Cancelled`, so it contributes 0.00 to Recognised Revenue. |

---

### Customer Region

| Field | Value |
|---|---|
| **Term** | Customer Region |
| **Definition** | The sales territory assigned to a customer at the time of record creation, sourced from `customers.csv:region`. |
| **Permitted values (sample)** | `North`, `South`, `West` |
| **Assignment rule** | Region is an attribute of the **customer**, not of the order. Revenue is attributed to the region of the ordering customer. |
| **Change behaviour** | Treated as a Type 1 attribute in `dim_customer` for this learning project — the latest region overwrites the previous one. A production system would likely need Type 2 history. |
| **Sample outcome** | North = 525.00; South = 0.00; West = 0.00 |
| **Why South and West are zero** | Customer 2 (Ben Smith, South) has only order 103, which is Cancelled. Customer 4 (David Lee, West) has no orders at all. |
| **Classification** | Internal — quasi-identifying when combined with other attributes (see Section 15) |

---

### Order Grain

| Field | Value |
|---|---|
| **Term** | Order Grain |
| **Definition** | The level of detail of one row in `fact_sales`: **one row per unique `order_id`**. |
| **Implication** | `order_id` is unique in `fact_sales`. Counting rows = counting recognised orders. |
| **Why it matters** | Stating grain prevents double counting. The duplicate `107` row in the raw file is exactly the kind of error that breaks grain. |
| **Sample outcome** | 4 rows in `fact_sales` (101, 102, 104, 107) |
| **Future change** | If orders ever contain multiple line items, the grain would become one row per `order_id` + `product_id`, and this definition **must** be updated and re-approved by the owner before implementation. |

---

### Supporting terms

| Term | Definition | Sample value |
|---|---|---|
| **Bronze layer** | Raw data landed as-is in a columnar format, with ingestion metadata added; no business logic. | 3 datasets, 8 order rows |
| **Silver layer** | Cleansed, typed, de-duplicated, validated data. Business-ready but not aggregated. | 7 order rows after de-duplication |
| **Gold layer** | Modelled data shaped for consumption — the star schema. | `fact_sales` = 4 rows |
| **Data Quality Check** | A named assertion over a dataset producing `PASS` or `FAIL` plus a failed-record count, written to `data/quality_results/`. | 5 checks defined |
| **Failed Record Count** | Number of rows violating a given check. | duplicate check = 1 |
| **Processing Date** | The date the pipeline run executed. Distinct from `order_date`, which is a business date. | run date |
| **Business Date vs Load Date** | Business date = when the event happened (`order_date`). Load date = when we processed it (`processing_date`). Never mix them in a report. | — |
| **Orphan Record** | A fact row whose dimension key does not exist. | order 106 |
| **Referential Integrity** | Every foreign key in a fact resolves to a dimension row. | Enforced in `06_gold_fact_sales.py` |

### 5.1 Glossary change control

Changing a glossary definition changes every number derived from it. Treat it like a schema change:

1. Proposer raises an issue describing the current definition, the proposed definition, and the numeric impact.
2. Steward quantifies the impact (e.g. "revenue moves from 525.00 to 575.00").
3. Owner approves or rejects.
4. Engineer implements and updates `docs/transformation_rules.md` in the same pull request.
5. Announcement goes to all known consumers **before** the change ships.
6. The old definition is archived with its effective date range, never deleted.

---

## 6. Data classification tiers and handling rules

### 6.1 The four tiers

| Tier | Definition | Impact if disclosed | Examples in our datasets |
|---|---|---|---|
| **Public** | Intended for, or safe for, external publication. | None | Product names, product categories, published list prices |
| **Internal** | Business data for employees; not secret but not for publication. | Minor embarrassment or competitive loss | Aggregated revenue, `region`, order statuses, `fact_sales` |
| **Confidential** | Sensitive business or personal data; needs a business reason to access. | Regulatory exposure, customer harm, material commercial damage | `customer_name`, customer-level revenue, raw `customers.csv` |
| **Restricted** | Highest sensitivity; strictly need-to-know, usually regulated. | Severe legal/financial/reputational damage | Payment card data, national IDs, health data, credentials *(not present in our sample, and must never be added)* |

### 6.2 Handling rules by tier

| Control | Public | Internal | Confidential | Restricted |
|---|---|---|---|---|
| Who may read | Anyone | All employees | Named roles only, approved by data owner | Named individuals, time-bound approval, documented business case |
| Access request | None | Self-service / team lead | Owner approval, recorded | Owner + security approval, recorded, reviewed quarterly |
| Encryption at rest | Recommended | **Required** | **Required** | **Required**, with managed keys |
| Encryption in transit | **Required** | **Required** | **Required** | **Required** |
| May appear in logs | Yes | Identifiers only | **No** — mask or omit | **Never** |
| May be copied to a laptop | Yes | Discouraged | **No** | **Never** |
| May be used in dev/test | Yes | Yes | **Only masked or synthetic** | **Synthetic only** |
| Shareable by email/chat | Yes | Internal only | **No** — share a link with access control | **Never** |
| Retention | Business need | Business need | Minimum necessary, documented | Minimum necessary, enforced deletion |
| Deletion method | Standard | Standard | Verified deletion | Verified + certificate of destruction |
| Breach response | Low | Standard | Formal incident | Formal incident + regulator notification assessment |

### 6.3 Applying classification to our project

| Dataset / column | Tier | Reasoning |
|---|---|---|
| `products.csv` (all columns) | Public | Catalogue data, no personal element |
| `customers.csv:customer_id` | Internal | Pseudonymous identifier |
| `customers.csv:customer_name` | **Confidential** | Directly identifies a natural person — PII |
| `customers.csv:region` | Internal | Quasi-identifier: harmless alone, identifying in combination |
| `customers.csv:customer_created_date` | Internal | Quasi-identifier |
| `orders.csv` (all columns) | Internal | Transactional business data, no direct PII |
| `bronze/*` | Inherits source tier | Bronze is an unmodified copy — same sensitivity as the source |
| `silver/customers` | **Confidential** | Still contains `customer_name` |
| `gold/dim_customer` | **Confidential** | Contains `customer_name` |
| `gold/fact_sales` | Internal | Keys only, no names |
| `data/quality_results/` | Internal | Counts only — **never** store offending row values here |
| Aggregated revenue by region | Internal | No individual is identifiable |

> **Key insight:** classification is **per column**, and the dataset inherits the highest tier of any column it contains. `dim_customer` is Confidential solely because of `customer_name`. Split `customer_name` out, and the remainder becomes Internal — which is precisely why minimisation (Section 15) is so powerful.

### 6.4 Label datasets in code

Make the classification visible where engineers actually look:

```python
# src/config.py (illustrative addition)

DATASET_CLASSIFICATION: dict[str, str] = {
    "raw.customers": "Confidential",
    "raw.products": "Public",
    "raw.orders": "Internal",
    "bronze.customers": "Confidential",
    "bronze.products": "Public",
    "bronze.orders": "Internal",
    "silver.customers": "Confidential",
    "silver.products": "Public",
    "silver.orders": "Internal",
    "gold.dim_customer": "Confidential",
    "gold.dim_product": "Public",
    "gold.dim_date": "Public",
    "gold.fact_sales": "Internal",
}

CONFIDENTIAL_COLUMNS: dict[str, list[str]] = {
    "raw.customers": ["customer_name"],
    "silver.customers": ["customer_name"],
    "gold.dim_customer": ["customer_name"],
}
```

Now a logging helper can refuse to print a confidential column, and a reviewer can see the tier without leaving the file.

---

## 7. Retention policies

### 7.1 Why retention is a governance control, not a storage optimisation

Three independent forces push in different directions:

- **Legal/regulatory:** some records *must* be kept (financial records are commonly retained for several years).
- **Privacy:** personal data *must not* be kept longer than necessary for its stated purpose.
- **Cost and risk:** every byte you keep is a byte that can be breached, migrated, or mis-joined.

A retention policy resolves the tension by stating, per dataset, how long and why.

### 7.2 A retention schedule for this project

| Dataset | Retention | Rationale | Deletion method |
|---|---|---|---|
| `data/raw/*.csv` | 90 days | Enables full reprocessing after a bug; beyond that bronze suffices | Hard delete |
| `data/bronze/*` | 7 years | Immutable system of record for replay and audit | Lifecycle rule to cold storage after 1 year, delete at 7 years |
| `data/silver/*` | 2 years | Rebuildable from bronze; limited cost justification | Rebuild from bronze if older data needed |
| `data/gold/fact_sales` | 7 years | Financial reporting | Archive partitions by year |
| `data/gold/dim_customer` | Until customer deletion request + statutory window | Personal data minimisation | Row-level erasure, see 7.4 |
| `data/quality_results/` | 13 months | Trend analysis across a full year plus comparison | Hard delete |
| Pipeline logs (application) | 90 days | Debugging window | Automatic expiry |
| Audit logs (access/changes) | 2 years minimum | Security investigation | Immutable store, no manual deletion |

### 7.3 Encoding retention in config

```yaml
# config/prod.yml (illustrative addition)
retention:
  raw_days: 90
  bronze_days: 2555          # ~7 years
  silver_days: 730
  gold_days: 2555
  quality_results_days: 395
  application_logs_days: 90
  audit_logs_days: 730
```

```yaml
# config/dev.yml (illustrative addition)
retention:
  raw_days: 7
  bronze_days: 30
  silver_days: 30
  gold_days: 30
  quality_results_days: 30
  application_logs_days: 7
  audit_logs_days: 30
```

Dev keeps far less — it costs money, holds no business value, and short retention forces you to keep dev data refreshable.

### 7.4 The right-to-erasure problem

If a customer asks to be deleted, you must find them **everywhere**: raw, bronze, silver, gold, backups, logs, exports.

Two design patterns make this survivable:

1. **Centralise PII.** Keep `customer_name` in exactly one table (`dim_customer`). Everything else references `customer_id`. Erasure then touches one table instead of twelve.
2. **Tokenise early.** Replace the name with a surrogate key at bronze, and store the name-to-key mapping in one controlled vault table. Deleting the vault row makes the name irrecoverable everywhere at once — a technique called **crypto-shredding** when the mapping is a key.

```python
# Illustrative erasure of a single customer from the dimension only.
from pyspark.sql import functions as F

def redact_customer(dim_customer_df, customer_id: int):
    """Replace PII for one customer while preserving referential integrity."""
    return dim_customer_df.withColumn(
        "customer_name",
        F.when(F.col("customer_id") == customer_id, F.lit("[REDACTED]"))
         .otherwise(F.col("customer_name")),
    )
```

Note what this preserves: `fact_sales` still joins, so **525.00 does not change**. Historical financial totals stay correct while the personal identifier is gone. That is the design goal.

---

## 8. Security foundations: authN vs authZ

### 8.1 Two words people constantly confuse

| | Authentication (authN) | Authorization (authZ) |
|---|---|---|
| Question | **Who are you?** | **What are you allowed to do?** |
| Happens | First | Second, after authN succeeds |
| Fails with | "Invalid credentials" / 401 | "Access denied" / 403 |
| Mechanisms | Password + MFA, certificates, managed identity, service principal, SSO/OIDC | Roles, ACLs, policies, row/column-level security |
| Example | Proving you are the data-engineering service identity | That identity may read `bronze/` but not write `gold/` |

> Mnemonic: **authN = identity**, **authZ = permission**. A valid passport (authN) does not grant entry to every building (authZ).

### 8.2 Identity types you will meet

| Identity type | Use for | Secret handling |
|---|---|---|
| **Human user** | Interactive work, notebooks, ad-hoc SQL | MFA mandatory; never share accounts |
| **Service principal / app registration** | Automated pipelines | Secret or certificate stored in a managed secret store |
| **Managed identity** | Platform-to-platform access in the cloud | **No secret at all** — the platform handles it. Prefer this whenever available |
| **Shared key / account key** | Legacy access to storage | Highest risk. Avoid; rotate aggressively if unavoidable |
| **SAS token** | Time-limited delegated access | Short expiry, narrow scope, never commit |

**Preference order:** managed identity > service principal with certificate > service principal with secret > SAS token > account key.

The reason is simple: each step down the list increases the number of long-lived secrets a human could leak.

### 8.3 Authentication hygiene checklist

- [ ] MFA enforced on every human account with data access
- [ ] No shared logins — one identity per person
- [ ] Automation uses a dedicated non-human identity, never a person's account
- [ ] Service identities are named for their purpose (e.g. `sp-sales-lakehouse-prod`)
- [ ] Credentials rotate on a defined schedule and on any departure
- [ ] Accounts are disabled the same day someone leaves
- [ ] Access reviews run at least quarterly for Confidential and Restricted data

---

## 9. Access control models and RBAC

### 9.1 The main models

| Model | How it decides | Strength | Weakness |
|---|---|---|---|
| **DAC** — Discretionary | The resource owner grants access | Flexible, fast | Sprawls; nobody knows who has what |
| **MAC** — Mandatory | Central policy based on labels/clearance | Very strong, auditable | Rigid, heavy |
| **RBAC** — Role-based | Permissions attach to roles; users get roles | Scales, easy to audit, maps to org | Role explosion if over-modelled |
| **ABAC** — Attribute-based | Rules over attributes (dept, region, time, classification) | Very expressive, dynamic | Hard to reason about and debug |
| **PBAC/policy-as-code** | Declarative policies in version control | Reviewable, testable, diffable | Requires tooling maturity |

In practice: **RBAC as the backbone, ABAC for the fine edges** (e.g. "sales managers can see only their own region").

### 9.2 Why RBAC wins for data platforms

Compare onboarding a new analyst:

```text
Without RBAC:  grant read on 14 tables, 3 storage paths, 2 dashboards, 1 cluster… ×
               repeated for every new joiner, inconsistently, forever.

With RBAC:     add user to group "sales-analysts". Done.
               Remove from group when they leave. Done.
```

The second is auditable in one query. The first is unanswerable.

### 9.3 A role model for `customer-sales-lakehouse`

| Role | Raw | Bronze | Silver | Gold facts | Gold `dim_customer` (PII) | Quality results | Config | Secrets |
|---|---|---|---|---|---|---|---|---|
| `data-engineer-dev` | RW (dev only) | RW (dev) | RW (dev) | RW (dev) | RW (masked dev data) | RW | RW (dev) | Read dev secrets only |
| `data-engineer-prod` | R | RW | RW | RW | R | RW | R | Read prod secrets via JIT |
| `pipeline-service-identity` | R | RW | RW | RW | RW | W | R | Own scope only |
| `analyst-standard` | — | — | — | R | — | R | — | — |
| `analyst-pii-approved` | — | — | R | R | R | R | — | — |
| `data-steward` | R | R | R | R | R | RW | R | — |
| `platform-admin` | — | — | — | — | — | R | RW | Manage, not read |
| `auditor` | Metadata only | Metadata only | Metadata only | Metadata only | Metadata only | R | R | Access logs only |

Observations worth internalising:

- The **default analyst role cannot see `customer_name`.** Seeing PII requires a separate, approved role. That is least privilege in action.
- `platform-admin` manages the secret store but is not granted the ability to read application secrets — separation of duties.
- `auditor` can see *that* things happened without seeing the data itself.
- The service identity is scoped to its own pipeline, not granted blanket platform rights.

### 9.4 Column-level and row-level controls

Rather than duplicating a table to hide one column, use a view:

```sql
-- Column-level: a PII-free projection of the customer dimension.
CREATE VIEW gold.vw_dim_customer_safe AS
SELECT
    customer_key,
    customer_id,
    customer_region,
    customer_created_date
FROM gold.dim_customer;
-- customer_name is deliberately absent.

-- Row-level: restrict analysts to their own region.
CREATE VIEW gold.vw_fact_sales_by_region AS
SELECT f.*
FROM gold.fact_sales f
JOIN gold.dim_customer c ON f.customer_key = c.customer_key
WHERE c.customer_region = CURRENT_USER_REGION();   -- platform-specific function
```

Grant `analyst-standard` access to the **views**, never the base tables. The base table becomes a controlled asset; the views become the product.

### 9.5 Access request and review process

```text
1. REQUEST   Requester states: dataset, access level, business reason, duration.
2. TRIAGE    Steward confirms the dataset is the right one (often it isn't).
3. APPROVE   Data owner approves/rejects. Confidential also needs security review.
4. GRANT     Engineer adds the user to a GROUP (never a direct grant).
5. RECORD    Ticket retained as the audit record.
6. EXPIRE    Time-bound access auto-expires.
7. REVIEW    Quarterly recertification: owner confirms each member still needs it.
8. REVOKE    Same-day removal on role change or departure.
```

> **Anti-pattern:** granting access directly to an individual "just this once". Those grants are invisible in group-based audits and survive for years.

---

## 10. Least privilege and just-in-time access

### 10.1 Least privilege defined

> Every identity should have the **minimum permissions**, on the **minimum scope**, for the **minimum time**, needed to do its job.

Three dimensions, and most people only think about the first:

| Dimension | Bad | Good |
|---|---|---|
| **Permissions** | Owner/admin on the whole platform | Read on `gold/`, write on `data/quality_results/` |
| **Scope** | Entire storage account | A single container path `.../gold/fact_sales/` |
| **Time** | Permanent | 4 hours, expiring automatically |

### 10.2 Applying it to our pipeline identity

What does `pipeline-service-identity` actually need?

| Action | Needed? | Scope |
|---|---|---|
| Read `data/raw/` | Yes | Read-only on raw path |
| Write `data/bronze/` | Yes | Write on bronze path only |
| Read `data/bronze/` | Yes | Read on bronze path |
| Write `data/silver/`, `data/gold/` | Yes | Write on those paths |
| Write `data/quality_results/` | Yes | Write-only is fine |
| **Delete raw files** | **No** | Never granted |
| **Change access policies** | **No** | Platform admin only |
| **Read secrets other than its own** | **No** | Scoped to its own secret names |
| **Create/delete storage accounts** | **No** | Never |

Writing this table *before* you request permissions is the single highest-leverage security habit you can build. Most over-permissioned identities exist because somebody asked for "whatever makes it work."

### 10.3 Just-in-time (JIT) access

Standing privileged access is the biggest blast radius in most organisations. JIT replaces it:

```text
Standing access:   [████████████ permanently elevated ████████████]
                    ↑ compromised at any moment = full damage

JIT access:        [──────────][██ 2h approved ██][──────────────]
                    ↑ compromised here = minimal damage
```

A JIT flow:

1. Engineer needs to fix a production job.
2. They request elevation, stating the reason and ticket.
3. An approver grants it for a bounded window (e.g. 2 hours).
4. All actions during the window are logged and attributed.
5. Access revokes automatically.
6. A post-hoc review samples elevations for appropriateness.

**Break-glass accounts** are the emergency exception: a highly privileged account, credentials split and sealed, usage alerting the whole security team, and a mandatory post-incident review. Use is rare and always reviewed.

### 10.4 Separation of duties

No single person should be able to complete a sensitive action end to end unobserved.

| Action | Needs at least two parties |
|---|---|
| Change a business rule that moves revenue | Proposer + data owner approval |
| Deploy to production | Author + reviewer (PR approval) |
| Grant access to Confidential data | Requester + owner |
| Delete a production dataset | Requester + owner, with a recorded ticket |
| Rotate and retrieve a production secret | Platform admin rotates; app reads — the admin does not need the value |

In a solo learning project you obviously approve your own PRs. Still *write* the review comments: it builds the habit and demonstrates the practice in an interview.

---

## 11. Secrets management

### 11.1 The absolute rule

> **Secrets never appear in code, notebooks, config files, commit history, logs, screenshots, tickets, chat messages, or documentation.**

"Secrets" means: passwords, API keys, access tokens, connection strings containing keys, SAS tokens, certificates, private keys, service-principal client secrets, database credentials, webhook URLs with embedded tokens.

There is no "it's only dev" exception. Dev secrets are routinely reused, and the habit you practise in dev is the habit you'll repeat in prod.

### 11.2 Why "just put it in a private repo" fails

| Assumption | Reality |
|---|---|
| "The repo is private." | Repos get forked, open-sourced, archived to third-party scanners, cloned to laptops, and attached to tickets. |
| "I'll remove it later." | Git history is permanent until actively rewritten. `git rm` does nothing to history. |
| "Only my team can see it." | Teams grow; contractors join; CI logs are broadly readable. |
| "It's a low-value key." | Attackers chain low-value access into high-value access. |
| "I'll remember to rotate it." | Nobody does. Average leaked-credential lifetime is measured in months. |

### 11.3 The hierarchy of secret storage

```text
WORST  ──────────────────────────────────────────────────────► BEST

Hard-coded   →  Local .env   →  CI/CD secret  →  Managed secret store
in source       (gitignored)    variable         (e.g. Key Vault) with
                                                 managed identity access
```

| Approach | Good for | Limits |
|---|---|---|
| Hard-coded | **Nothing. Ever.** | — |
| Local `.env`, gitignored | Local development only | Lives on a laptop; easy to leak via screen-share |
| Environment variables injected by the runtime | Containers, CI | Visible in process listings; can leak into logs |
| CI/CD secret store | Build/deploy credentials | Platform-scoped; masked in logs |
| **Managed secret store** (Key Vault or equivalent) | Production | Requires an identity to read it — which is the point |

### 11.4 Local development pattern

```bash
# .env  — MUST be listed in .gitignore, never committed
# Values below are placeholders, not real credentials.
LAKEHOUSE_ENV=dev
LAKEHOUSE_DATA_ROOT=./data
STORAGE_ACCOUNT_NAME=<storage-account-name>
KEY_VAULT_NAME=<key-vault-name>
```

```bash
# .gitignore  — the project already ships with one; confirm these lines exist
.env
.env.*
!.env.example
*.pem
*.key
*.pfx
secrets/
.credentials
```

Commit a **`.env.example`** with the *names* and dummy values so a new joiner knows what to provide:

```bash
# .env.example — safe to commit, contains NO real values
LAKEHOUSE_ENV=dev
LAKEHOUSE_DATA_ROOT=./data
STORAGE_ACCOUNT_NAME=<storage-account-name>
KEY_VAULT_NAME=<key-vault-name>
```

### 11.5 Reading configuration safely in code

```python
# src/config.py — illustrative, consistent with the project's config module.
import os
from dataclasses import dataclass


class MissingConfigError(RuntimeError):
    """Raised when a required setting is absent from the environment."""


def require_env(name: str) -> str:
    """Fetch a required environment variable, failing loudly but safely.

    The error message deliberately names the variable but NEVER echoes a value.
    """
    value = os.environ.get(name)
    if not value:
        raise MissingConfigError(
            f"Required environment variable '{name}' is not set. "
            f"See .env.example for the expected variable names."
        )
    return value


@dataclass(frozen=True)
class Settings:
    environment: str
    data_root: str
    storage_account_name: str
    key_vault_name: str

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls(
            environment=os.environ.get("LAKEHOUSE_ENV", "dev"),
            data_root=os.environ.get("LAKEHOUSE_DATA_ROOT", "./data"),
            storage_account_name=require_env("STORAGE_ACCOUNT_NAME"),
            key_vault_name=require_env("KEY_VAULT_NAME"),
        )

    def __repr__(self) -> str:
        # Never let a careless print() dump configuration that may be sensitive.
        return (
            f"Settings(environment={self.environment!r}, "
            f"data_root={self.data_root!r}, "
            f"storage_account_name='<redacted>', "
            f"key_vault_name='<redacted>')"
        )
```

Three deliberate choices:

1. The code reads **names**, never values, from the repository.
2. Failure is loud and early, with a message that helps without leaking.
3. `__repr__` is overridden so an accidental `print(settings)` cannot dump sensitive values into a notebook output cell — which is then committed.

### 11.6 Conceptual managed-secret-store retrieval

```python
# Conceptual only — no live Azure environment required to read this guide.
# The pattern: authenticate with an identity, then fetch by NAME.

def get_secret(vault_name: str, secret_name: str) -> str:
    """Retrieve a secret from a managed secret store.

    Notes for the reader:
      * The application supplies only the vault NAME and secret NAME.
      * The credential comes from the hosting platform (managed identity),
        so there is no secret in the code that unlocks the other secrets.
      * The returned value must never be logged, printed, or written to disk.
    """
    # from azure.identity import DefaultAzureCredential
    # from azure.keyvault.secrets import SecretClient
    #
    # credential = DefaultAzureCredential()
    # client = SecretClient(
    #     vault_url=f"https://{vault_name}.vault.azure.net",
    #     credential=credential,
    # )
    # return client.get_secret(secret_name).value
    raise NotImplementedError("Conceptual example — wire up in a real environment.")
```

The important idea is the **bootstrap problem** and its solution. Secrets protect resources — so what protects the secrets? A managed identity: the platform vouches for the workload's identity, so there is no "first secret" to leak.

### 11.7 Notebook-specific hazards

Notebooks are uniquely dangerous because **output is saved with the file**.

- [ ] Never `print()` a secret, even once, even to check it loaded
- [ ] Never `display()` a dataframe containing credentials
- [ ] Clear all outputs before committing (`jupyter nbconvert --clear-output`)
- [ ] Avoid `%env` magic that echoes values
- [ ] Beware `df.show()` on Confidential columns during a screen-share
- [ ] Watch for secrets in tracebacks — exceptions sometimes include the offending string
- [ ] Our project uses `.py` notebooks (`notebooks/01_bronze_ingestion.py` …), which sidesteps most of this. That is a feature, not an accident.

### 11.8 Preventive controls

| Control | What it does |
|---|---|
| **Pre-commit secret scanner** | Blocks the commit before the secret ever enters history |
| **Server-side push protection** | Rejects the push if a pattern matches a known credential format |
| **Repo-wide history scanning** | Finds what already leaked |
| **`.gitignore` discipline** | Stops whole classes of files being staged |
| **Code review checklist item** | "Does this PR introduce any literal credential?" |
| **Short-lived credentials** | Shrinks the damage window if one does leak |

```yaml
# .pre-commit-config.yaml — illustrative, add a scanner of your choice
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.6.0
    hooks:
      - id: detect-private-key
      - id: check-added-large-files
      - id: end-of-file-fixer
      - id: trailing-whitespace
```

---

## 12. What to do if a secret is committed

> **Assume compromise the moment a secret touches a remote repository.** Treat it as leaked even if the repo is private and you deleted it 30 seconds later.

### 12.1 The order of operations (do them in this order)

| # | Step | Why this order |
|---|---|---|
| 1 | **Rotate / revoke the secret immediately** | Invalidating the credential is the only action that actually stops the attack. Do this *before* cleaning history. |
| 2 | **Verify the old value no longer works** | Rotation sometimes silently fails or leaves a second active key |
| 3 | **Notify your security contact** | They may have obligations and visibility you don't |
| 4 | **Check logs for misuse** | Look for access from unexpected IPs, times, or volumes since the commit timestamp |
| 5 | **Remove the secret from the working tree and fix the code** | Replace with an environment-variable or secret-store lookup |
| 6 | **Purge it from git history** | Reduces exposure for anyone who clones later |
| 7 | **Force-push and tell every collaborator to re-clone** | Old clones still contain the secret |
| 8 | **Invalidate caches, forks, CI artefacts, and build logs** | Secrets persist in surprising places |
| 9 | **Add a preventive control** | Scanner, hook, or push protection so it cannot recur |
| 10 | **Write a blameless post-incident note** | Capture the systemic cause, not the person |

> **The most common mistake is doing step 6 first.** Rewriting history feels like "fixing it", but the credential is already in someone's clone, a CI log, or a scanner's index. **Rotate first. Always.**

### 12.2 Practical commands

```bash
# 0) FIRST, in the provider's console: rotate or revoke the credential.
#    Only continue once the old value is confirmed dead.

# 1) Find when and where it entered history.
git --no-pager log --oneline -S '<unique-fragment-of-the-secret>' --all

# 2) Remove the file from the working tree and stop tracking it.
git rm --cached config/local_secrets.yml
echo "config/local_secrets.yml" >> .gitignore
git commit -m "Remove committed secret file and ignore it going forward"

# 3) Purge from history. Prefer a purpose-built tool over filter-branch.
#    Example with git-filter-repo (install separately):
# git filter-repo --path config/local_secrets.yml --invert-paths
#
#    Or redact a literal string across all history:
# echo '<the-leaked-literal>==><REMOVED>' > ..\replacements.txt
# git filter-repo --replace-text ..\replacements.txt

# 4) Force-push the rewritten history (coordinate with collaborators first).
git push --force-with-lease --all
git push --force-with-lease --tags

# 5) Every collaborator MUST re-clone. Old clones still contain the secret.
```

Delete the replacement file afterwards — it contains the leaked literal.

### 12.3 Blameless post-incident note template

```markdown
## Secret exposure — <short identifier>

**Date detected:** <YYYY-MM-DD HH:MM timezone>
**Detected by:** <scanner / person role / alert>
**Secret type:** <e.g. service principal client secret>
**Scope of access it granted:** <e.g. read on dev storage container>
**Exposure window:** <commit timestamp> → <revocation timestamp>  (duration: <X hours>)
**Repository visibility during window:** <private / internal / public>

### Actions taken
- [x] Credential rotated at <time>; old value verified non-functional
- [x] Access logs reviewed for the exposure window — <findings>
- [x] Security contact notified at <time>
- [x] Source updated to read from the managed secret store
- [x] History purged; collaborators instructed to re-clone
- [x] Secret scanning enabled on the repository

### Root cause (systemic, not personal)
<e.g. no pre-commit scanner; .env.example pattern not documented;
 onboarding did not cover secret handling>

### Prevention
| Action | Owner (role) | Due |
|---|---|---|
| Enable push protection org-wide | Platform | <date> |
| Add pre-commit secret scanner to template repo | Data Eng | <date> |
| Add secrets module to onboarding | Steward | <date> |

### Lessons
<What the system should have done to make this mistake impossible.>
```

Blameless matters. If reporting a leak gets someone punished, the next leak goes unreported — and unreported leaks are the expensive ones.

---

## 13. Dev / test / prod separation

### 13.1 Why separate environments exist

| Risk if you only have production | Example |
|---|---|
| Experiments corrupt real data | A test run overwrites `gold/fact_sales` with 2 rows |
| Bugs reach users with no filter | Revenue shows 0.00 on the executive dashboard |
| No safe place to test destructive changes | Schema migration fails halfway |
| PII exposed to every developer | All engineers can read `customer_name` |
| Cost spikes from runaway jobs | A dev loop runs on prod-sized compute |
| No rollback | The only copy was the one you broke |

### 13.2 How environments differ

| Aspect | Dev | Test / UAT | Prod |
|---|---|---|---|
| Purpose | Build and experiment | Validate before release | Serve the business |
| Data | Small sample or synthetic; PII masked | Prod-like volume, PII masked or synthetic | Real data |
| Our sample | The 4/4/8-row CSVs — ideal dev data | Larger generated set | Real orders |
| Who may write | Any engineer | CI/CD only | CI/CD only |
| Deployment | Direct push | Automated from `main` | Automated, approval-gated |
| Schedule | On demand | Nightly | Full production schedule |
| Compute | Smallest viable | Prod-like for performance testing | Sized and monitored |
| Monitoring/alerting | Minimal | Full, alerts to the team | Full, alerts on-call |
| Retention | Days | Weeks | Years |
| Secrets | Dev-scoped, separate vault | Test-scoped | Prod-scoped, JIT access |
| Blast radius of a mistake | Zero | Low | High |

> **Critical rule: never use real PII in dev.** Mask, synthesise, or subset. Our fictional dataset is a perfect illustration of a safe dev dataset.

### 13.3 How `config/dev.yml` and `config/prod.yml` model this

The project ships two configuration files so that **the same code** behaves appropriately per environment. Nothing environment-specific is hard-coded in `src/` or `notebooks/`.

```yaml
# config/dev.yml  — illustrative shape consistent with the project
environment: dev

paths:
  raw: ./data/raw
  bronze: ./data/bronze
  silver: ./data/silver
  gold: ./data/gold
  quality_results: ./data/quality_results

spark:
  app_name: customer-sales-lakehouse-dev
  shuffle_partitions: 4          # tiny data, tiny parallelism
  master: "local[*]"

data_quality:
  fail_pipeline_on_error: false  # dev: observe failures, keep going
  write_results: true

logging:
  level: DEBUG
  log_row_samples: false         # NEVER true where real data could appear

retention:
  bronze_days: 30
  quality_results_days: 30
```

```yaml
# config/prod.yml  — illustrative shape consistent with the project
environment: prod

paths:
  raw: abfss://raw@<storage-account-name>.dfs.core.windows.net/sales
  bronze: abfss://bronze@<storage-account-name>.dfs.core.windows.net/sales
  silver: abfss://silver@<storage-account-name>.dfs.core.windows.net/sales
  gold: abfss://gold@<storage-account-name>.dfs.core.windows.net/sales
  quality_results: abfss://ops@<storage-account-name>.dfs.core.windows.net/quality

spark:
  app_name: customer-sales-lakehouse-prod
  shuffle_partitions: 200

secrets:
  provider: key_vault
  vault_name: <key-vault-name>
  # Only NAMES appear here. Values are fetched at runtime by a managed identity.
  secret_names:
    storage_credential: sales-lakehouse-storage
    notification_webhook: sales-lakehouse-alerts

data_quality:
  fail_pipeline_on_error: true   # prod: a failed check stops the pipeline
  write_results: true

logging:
  level: INFO
  log_row_samples: false

retention:
  bronze_days: 2555
  quality_results_days: 395
```

Study the differences — each one is a governance decision:

| Difference | Governance reason |
|---|---|
| Local paths vs `abfss://` | Dev runs on a laptop; prod runs on cloud storage. Same code, different config. |
| `shuffle_partitions` 4 vs 200 | Right-sized compute; dev shouldn't burn prod-scale resources |
| `fail_pipeline_on_error` false vs true | In dev you want to see all failures; in prod bad data must not reach consumers |
| `log_row_samples` false in **both** | Row samples could contain PII. There is no environment where this is safe by default. |
| Separate vault names | A dev compromise must not expose prod |
| Different retention | Cost and privacy minimisation |
| Only secret **names** in config | The absolute rule from Section 11 |

### 13.4 Loading the right config

```python
# src/config.py — illustrative loader
import os
from pathlib import Path
import yaml


VALID_ENVIRONMENTS = {"dev", "test", "prod"}


def load_config(environment: str | None = None) -> dict:
    """Load the YAML config for the requested environment.

    Defaults to 'dev' so that an unconfigured laptop can never accidentally
    point at production.
    """
    env = (environment or os.environ.get("LAKEHOUSE_ENV", "dev")).lower()
    if env not in VALID_ENVIRONMENTS:
        raise ValueError(
            f"Unknown environment {env!r}. Expected one of {sorted(VALID_ENVIRONMENTS)}."
        )

    config_path = Path(__file__).resolve().parents[1] / "config" / f"{env}.yml"
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)

    if config.get("environment") != env:
        raise ValueError(
            f"Config file {config_path.name} declares environment "
            f"{config.get('environment')!r} but {env!r} was requested."
        )
    return config
```

Two safety properties: **defaulting to dev** (fail safe, not fail dangerous) and **asserting the file agrees with the request** (catches a copy-paste that points dev at prod paths).

### 13.5 Promotion flow

```text
┌──────────┐   PR + review   ┌──────────┐   automated    ┌──────────┐   approval  ┌──────────┐
│  Local   │ ──────────────► │   Dev    │ ─────────────► │   Test   │ ──────────► │   Prod   │
│ feature  │   unit tests    │  branch  │  integration   │   UAT    │   gated     │  main    │
│  branch  │                 │          │  + DQ checks   │          │  release    │          │
└──────────┘                 └──────────┘                └──────────┘             └──────────┘
     │                            │                           │                        │
  pytest                   full pipeline run           prod-like volume         scheduled run
  lint                     on sample data              performance check        monitored
  secret scan              revenue == 525.00           reconciliation           alerting
```

Gates that must pass at each hop:

| Gate | Local → Dev | Dev → Test | Test → Prod |
|---|---|---|---|
| Unit tests pass (`tests/`) | ✅ | ✅ | ✅ |
| Linting/formatting | ✅ | ✅ | ✅ |
| Secret scan clean | ✅ | ✅ | ✅ |
| Full pipeline runs end to end | — | ✅ | ✅ |
| All data-quality checks PASS | — | ✅ | ✅ |
| Reconciliation: revenue = 525.00 | — | ✅ | ✅ (against expected prod figure) |
| Performance within SLA | — | — | ✅ |
| Documentation updated | — | ✅ | ✅ |
| Owner approval recorded | — | — | ✅ |
| Rollback plan written | — | — | ✅ |

### 13.6 Config-driven, not code-driven

The signature of a well-separated project: **you can diff `config/dev.yml` against `config/prod.yml` and see every environmental difference in one screen.** If you have to read `src/` to find out how prod differs, the separation has leaked into the code.

```python
# ANTI-PATTERN — never do this
if os.environ.get("ENV") == "prod":
    path = "abfss://gold@<storage-account-name>.dfs.core.windows.net/sales"
else:
    path = "./data/gold"

# CORRECT — the difference lives in config, the code is environment-agnostic
config = load_config()
path = config["paths"]["gold"]
```

---

## 14. Logging and auditability

### 14.1 Three kinds of logs, three different purposes

| Log type | Answers | Audience | Retention |
|---|---|---|---|
| **Application log** | "What did the job do and did it fail?" | Engineers | 90 days |
| **Audit log** | "Who accessed or changed what, when?" | Security, auditors | 2+ years, immutable |
| **Data quality / operational log** | "Was the output trustworthy?" | Stewards, analysts | 13 months |

Our project has a dedicated `src/logging_utils.py` for the first, and `data/quality_results/` for the third.

### 14.2 What to log

| Category | Log this | Example |
|---|---|---|
| Run identity | Run ID, pipeline name, environment, code version | `run_id=2026-02-20T02:00:03Z-a1b2`, `env=prod`, `git_sha=abc1234` |
| Timing | Start, end, duration per stage | `stage=silver_orders duration_s=12.4` |
| Volume | Rows in, rows out, rows rejected | `rows_in=8 rows_out=7 rows_rejected=1` |
| Decisions | Which rules fired and how many rows they affected | `rule=dedupe_order_id removed=1` |
| Config | Environment name, paths (non-secret), key parameters | `shuffle_partitions=200` |
| Outcomes | Status, error type, retry count | `status=SUCCESS retries=0` |
| Quality | Check name, status, failed count | `check=orders_amount_not_null status=FAIL failed=1` |
| Lineage | Input paths and output paths | `input=bronze/orders output=silver/orders` |

### 14.3 What to NEVER log

| Never log | Why | Instead log |
|---|---|---|
| `customer_name` or any PII | Logs are widely readable and long-lived | `customer_id` (pseudonymous) |
| Any secret, token, key, or connection string | Permanent credential exposure | The secret **name** only |
| Full row contents of Confidential data | Recreates the dataset in the log store | Counts and keys |
| Raw query results | May include PII | Row counts, aggregate stats |
| Full exception payloads that embed data | Tracebacks often echo values | Exception type + sanitised message |
| User-supplied free text verbatim | Log injection and PII risk | Sanitised, length-capped |
| Entire config dictionaries | Config may contain sensitive values | An allowlist of safe keys |

> **The "screenshot test":** if you would not be comfortable with this log line appearing in a screenshot in a public ticket, do not log it.

### 14.4 Structured logging in practice

```python
# src/logging_utils.py — illustrative, safe-by-default logging helpers.
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any


SENSITIVE_KEY_FRAGMENTS = (
    "password", "secret", "token", "key", "credential",
    "connection_string", "sas", "customer_name",
)


def _redact(payload: dict[str, Any]) -> dict[str, Any]:
    """Redact values whose key suggests sensitivity.

    This is a safety net, not a licence to pass sensitive data in.
    """
    safe: dict[str, Any] = {}
    for key, value in payload.items():
        lowered = key.lower()
        if any(fragment in lowered for fragment in SENSITIVE_KEY_FRAGMENTS):
            safe[key] = "<redacted>"
        elif isinstance(value, dict):
            safe[key] = _redact(value)
        else:
            safe[key] = value
    return safe


def get_logger(name: str, level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.setLevel(level.upper())
    logger.propagate = False
    return logger


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    """Emit one structured JSON log line with redaction applied."""
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **_redact(fields),
    }
    logger.info(json.dumps(record, default=str))


def log_stage_metrics(
    logger: logging.Logger,
    *,
    run_id: str,
    stage: str,
    rows_in: int,
    rows_out: int,
    duration_seconds: float,
) -> None:
    """Log the volume and timing of a pipeline stage.

    Counts are safe to log; row contents are not.
    """
    log_event(
        logger,
        "stage_completed",
        run_id=run_id,
        stage=stage,
        rows_in=rows_in,
        rows_out=rows_out,
        rows_rejected=rows_in - rows_out,
        duration_seconds=round(duration_seconds, 3),
    )
```

Sample output for our silver orders stage:

```text
{"timestamp": "2026-02-20T02:00:15.412Z", "event": "stage_completed", "run_id": "20260220-0200-a1b2", "stage": "silver_orders", "rows_in": 8, "rows_out": 7, "rows_rejected": 1, "duration_seconds": 12.41}
```

Note what is and isn't there. You can see that one row was dropped. You cannot see who the customer was. That is the balance you're aiming for.

### 14.5 The quality results table as an audit artefact

The project writes quality outcomes to `data/quality_results/` with this schema:

| Column | Type | Meaning |
|---|---|---|
| `check_name` | STRING | Stable identifier for the rule |
| `check_status` | STRING | `PASS` or `FAIL` |
| `failed_record_count` | INT | Rows violating the rule |
| `processing_date` | DATE | When the pipeline ran |

Expected results for the sample data:

| check_name | check_status | failed_record_count | processing_date |
|---|---|---|---|
| `orders_unique_order_id` | FAIL | 1 | *(run date)* |
| `orders_amount_not_null` | FAIL | 1 | *(run date)* |
| `orders_customer_fk_valid` | FAIL | 1 | *(run date)* |
| `orders_product_fk_valid` | PASS | 0 | *(run date)* |
| `orders_status_in_allowed_values` | PASS | 0 | *(run date)* |

Three critical design decisions in that schema:

1. **No row-level data.** Only counts. The table is therefore Internal, not Confidential, and can be shared with stewards and analysts freely.
2. **`processing_date` is included**, so you can trend quality over time: "duplicates appeared on the 14th — what changed?"
3. **Stable `check_name` values**, so a dashboard can track the same rule across months. Renaming a check breaks the history; treat check names as a public API.

```sql
-- Has quality degraded over the last 30 days?
SELECT
    check_name,
    COUNT(*)                                        AS runs,
    SUM(CASE WHEN check_status = 'FAIL' THEN 1 ELSE 0 END) AS failures,
    MAX(failed_record_count)                        AS worst_failed_count
FROM quality_results
WHERE processing_date >= DATEADD(day, -30, CURRENT_DATE)
GROUP BY check_name
ORDER BY failures DESC, check_name;
```

### 14.6 An audit table for sensitive access

Where the platform does not provide it, model it:

```sql
CREATE TABLE audit.data_access_log (
    access_id        BIGINT      NOT NULL,
    accessed_at_utc  TIMESTAMP   NOT NULL,
    principal_id     VARCHAR(128) NOT NULL,   -- identity, not a person's name
    principal_type   VARCHAR(32)  NOT NULL,   -- user | service | system
    dataset_name     VARCHAR(256) NOT NULL,
    classification   VARCHAR(32)  NOT NULL,   -- Public | Internal | Confidential | Restricted
    action           VARCHAR(32)  NOT NULL,   -- read | write | delete | grant | revoke
    row_count        BIGINT       NULL,       -- volume, NOT content
    justification_ref VARCHAR(64) NULL,       -- ticket reference for JIT access
    outcome          VARCHAR(16)  NOT NULL,   -- allowed | denied
    PRIMARY KEY (access_id)
);
```

Properties that make this a real audit log:

- **Append-only.** No updates, no deletes. If you can edit the audit log, it isn't one.
- **No data content.** Volume and metadata only.
- **Separately permissioned.** Engineers write to it; only auditors read it; nobody can modify it.
- **Longer retention than the data it describes** in many cases.

Useful audit queries:

```sql
-- Who read Confidential data in the last 7 days?
SELECT principal_id, dataset_name, COUNT(*) AS access_events, SUM(row_count) AS rows_read
FROM audit.data_access_log
WHERE classification = 'Confidential'
  AND accessed_at_utc >= DATEADD(day, -7, CURRENT_TIMESTAMP)
  AND outcome = 'allowed'
GROUP BY principal_id, dataset_name
ORDER BY rows_read DESC;

-- Denied attempts can indicate misconfiguration or probing.
SELECT principal_id, dataset_name, action, COUNT(*) AS denied_attempts
FROM audit.data_access_log
WHERE outcome = 'denied'
  AND accessed_at_utc >= DATEADD(day, -1, CURRENT_TIMESTAMP)
GROUP BY principal_id, dataset_name, action
HAVING COUNT(*) > 5
ORDER BY denied_attempts DESC;
```

### 14.7 Log levels, used correctly

| Level | Use for | Example |
|---|---|---|
| `DEBUG` | Developer detail; off in prod | Partition counts, plan details |
| `INFO` | Normal lifecycle milestones | "silver_orders completed: 7 rows" |
| `WARNING` | Something unexpected but survivable | "1 duplicate order_id removed" |
| `ERROR` | The operation failed; needs attention | "Referential integrity check failed" |
| `CRITICAL` | Pipeline-stopping; wake someone | "Cannot reach storage; run aborted" |

A common mistake is logging everything at `INFO`. The result is a log nobody reads, which is the same as no log. Reserve `WARNING` and above for things a human should actually look at.

### 14.8 Logging and auditability checklist

- [ ] Every run has a unique, searchable run ID
- [ ] Row counts logged at each layer boundary
- [ ] Logs are structured (JSON), not free-form prose
- [ ] A redaction helper exists and is used by default
- [ ] `log_row_samples` is `false` in every environment
- [ ] Quality results persisted with `processing_date` for trending
- [ ] Audit log is append-only and separately permissioned
- [ ] Retention set per log type and automatically enforced
- [ ] Alerts fire on `ERROR`/`CRITICAL`, not on `INFO`
- [ ] You can answer "what happened in last night's run?" in under 5 minutes

---

## 15. PII handling in these datasets

### 15.1 Identifying PII

**PII (personally identifiable information)** is any data that identifies a natural person, alone or in combination with other data. The "in combination" clause is the one people miss.

| Category | Definition | In our datasets |
|---|---|---|
| **Direct identifier** | Identifies a person on its own | `customer_name` — **this is PII** |
| **Quasi-identifier** | Identifies in combination with others | `region`, `customer_created_date` — **quasi-identifying** |
| **Pseudonymous identifier** | Refers to a person via a surrogate | `customer_id` — personal data under most regimes, but far lower risk |
| **Non-personal** | No link to an individual | `product_id`, `category`, `unit_price`, `order_id`, `quantity`, `amount`, `status` |

### 15.2 Column-by-column assessment

| Dataset | Column | PII? | Classification | Handling |
|---|---|---|---|---|
| `customers.csv` | `customer_id` | Pseudonymous | Internal | Safe to use as a join key everywhere |
| `customers.csv` | `customer_name` | **Direct PII** | **Confidential** | Restrict; mask in dev; never log; exclude from default views |
| `customers.csv` | `region` | **Quasi-identifier** | Internal | Safe in aggregate; risky at low counts |
| `customers.csv` | `customer_created_date` | Quasi-identifier | Internal | Safe in aggregate; consider truncating to month |
| `products.csv` | all | No | Public | Unrestricted |
| `orders.csv` | `customer_id` | Pseudonymous | Internal | Join key |
| `orders.csv` | all others | No | Internal | Business data |
| `fact_sales` | all | No direct PII | Internal | Keys only — this is why the fact is broadly shareable |
| `dim_customer` | includes `customer_name` | **Yes** | **Confidential** | Expose via the PII-free view by default |

### 15.3 Why `region` is dangerous despite looking harmless

Our sample has four customers, and **only one customer in the West region** (David Lee). A "harmless" aggregate report by region therefore leaks at the individual level:

```sql
-- Looks aggregate. Is not.
SELECT c.customer_region, SUM(f.sale_amount) AS revenue
FROM gold.fact_sales f
JOIN gold.dim_customer c ON f.customer_key = c.customer_key
GROUP BY c.customer_region;
```

In our sample this returns:

| customer_region | revenue |
|---|---|
| North | 525.00 |
| South | 0.00 |
| West | 0.00 |

Anyone who knows there is exactly one West customer now knows that customer's exact revenue. This is **re-identification through aggregation**, and it is the classic failure mode of "anonymised" reporting.

Mitigations:

| Mitigation | How | Trade-off |
|---|---|---|
| **k-anonymity threshold** | Suppress any group with fewer than *k* customers (commonly k = 5 or 10) | Some legitimate groups disappear |
| **Generalise** | Roll `West` and `South` into `Other` | Loses granularity |
| **Noise** | Add small random perturbation | Numbers stop reconciling — usually unacceptable for finance |
| **Access control** | Only approved roles see low-count breakdowns | Operationally simplest, and usually the right answer |

```sql
-- k-anonymity applied: suppress regions with fewer than 5 distinct customers.
SELECT
    CASE WHEN COUNT(DISTINCT c.customer_key) >= 5
         THEN c.customer_region
         ELSE 'Suppressed (low count)'
    END AS reporting_region,
    SUM(f.sale_amount) AS revenue
FROM gold.fact_sales f
JOIN gold.dim_customer c ON f.customer_key = c.customer_key
GROUP BY c.customer_region;
```

With four customers, every region is suppressed — which correctly tells you this dataset is too small for safe region-level public reporting. In a real dataset with thousands of customers, North/South/West would all pass comfortably.

### 15.4 The four PII techniques

| Technique | What it does | Reversible? | Keeps analytics? | Best for |
|---|---|---|---|---|
| **Masking** | Hides part/all of the value | No | Poor | Display, screenshots, support tooling |
| **Pseudonymisation** | Swaps the identifier for a surrogate, mapping held separately | Yes, with the mapping | Yes | Analytics on personal data |
| **Hashing** | One-way transform (+ salt) | No (practically) | Joins only | Matching without storing identity |
| **Minimisation** | Don't collect/propagate it at all | N/A | Yes, if it wasn't needed | **Always try this first** |

#### Masking

```python
from pyspark.sql import functions as F


def mask_customer_name(df, column: str = "customer_name"):
    """Show the first character and the initial of the surname only.

    'Aisha Khan'  -> 'A*** K.'
    'Ben Smith'   -> 'B*** S.'
    'Carla Jones' -> 'C*** J.'
    'David Lee'   -> 'D*** L.'
    """
    first_initial = F.substring(F.col(column), 1, 1)
    surname_initial = F.substring(F.element_at(F.split(F.col(column), " "), -1), 1, 1)
    return df.withColumn(
        column,
        F.concat(first_initial, F.lit("*** "), surname_initial, F.lit(".")),
    )
```

| Original | Masked |
|---|---|
| Aisha Khan | `A*** K.` |
| Ben Smith | `B*** S.` |
| Carla Jones | `C*** J.` |
| David Lee | `D*** L.` |

Use this to build a **dev copy** of `customers.csv`. Developers get realistic shapes and lengths without real identities. Note that `customer_id`, `region` and all revenue figures are untouched — **525.00 still reconciles**.

#### Pseudonymisation

```python
def pseudonymise_customers(customers_df):
    """Split the customer dataset into an analytics-safe part and a PII vault.

    Returns (safe_df, vault_df).
      * safe_df   : Internal   — no names, joins to facts normally
      * vault_df  : Confidential — the ONLY place the name lives
    """
    safe_df = customers_df.select(
        "customer_id", "region", "customer_created_date"
    )
    vault_df = customers_df.select("customer_id", "customer_name")
    return safe_df, vault_df
```

Now 95% of analytics runs against `safe_df` with no Confidential access at all, and the vault is a small, tightly controlled table. Deleting one vault row satisfies an erasure request without touching any fact.

#### Hashing

```python
def hash_customer_name(df, salt: str, column: str = "customer_name"):
    """Irreversibly hash a name so equal names match without revealing identity.

    The salt MUST come from a secret store, never a literal in source.
    A salt is essential: a small, guessable value space (like names) is
    trivially reversed by brute force if you hash it unsalted.
    """
    return df.withColumn(
        f"{column}_hash",
        F.sha2(F.concat(F.lit(salt), F.col(column)), 256),
    ).drop(column)
```

Caveats to say out loud in an interview:

- Hashing is **not** anonymisation when the input space is small. Unsalted hashes of names are reversible by brute force in seconds.
- The salt is a secret — manage it per Section 11.
- Rotating the salt breaks all historical joins. Plan it.

#### Minimisation

The most underrated control. Ask of every column: **do we actually need this?**

| Column | Needed for our business questions? | Decision |
|---|---|---|
| `customer_id` | Yes — join key and ranking | Keep |
| `customer_name` | Only for *displaying* a ranking | Keep in `dim_customer`, exclude from `fact_sales` and all default views |
| `region` | Yes — regional revenue | Keep |
| `customer_created_date` | Not used by any current report | **Candidate for removal** |
| `updated_at` on orders | Used for de-duplication tie-breaks | Keep |

Every column you don't collect is a column that cannot leak, cannot be mis-joined, cannot be subject to an erasure request, and costs nothing to store.

### 15.5 PII handling checklist

- [ ] Every column classified and recorded in the data dictionary
- [ ] Direct PII confined to the smallest possible number of tables
- [ ] Default views and default roles exclude PII
- [ ] Dev and test use masked or synthetic data only
- [ ] No PII in logs, metrics, alerts, error messages, or filenames
- [ ] Low-count aggregates assessed for re-identification risk
- [ ] Retention and erasure paths defined and tested
- [ ] Access to PII is approved, time-bound, and audited
- [ ] Hashing uses a salt retrieved from a secret store
- [ ] Minimisation reviewed whenever the schema changes

---

## 16. Writing professional documentation

Documentation is a deliverable, not an afterthought. The test of good documentation is simple: **can a competent stranger operate, extend, and trust your pipeline without talking to you?**

The project already contains the right set of documents. Here is what "good" looks like for each.

### 16.1 `docs/architecture.md`

**Purpose:** explain the shape of the system so a newcomer can orient in ten minutes.

Should contain:

- A one-paragraph plain-English summary ("raw CSV files land, are promoted through bronze/silver/gold, and are queried as a star schema").
- The ASCII/diagram flow from raw → bronze → silver → gold → reporting.
- Each layer's purpose, format, and guarantees.
- Technology choices **and the reasons** ("Parquet because columnar compression suits analytical scans and preserves types, unlike CSV").
- The local-vs-Azure mapping table, clearly labelled as *design* for the Azure side.
- Explicit non-goals ("no streaming; no CDC; no real-time serving").
- Known limitations.

Should **not** contain: credentials, tenant identifiers, internal hostnames, or anything environment-specific that belongs in config.

```markdown
## Layer guarantees

| Layer  | Format  | Guarantee |
|--------|---------|-----------|
| Raw    | CSV     | Byte-for-byte as received. Never modified. |
| Bronze | Parquet | Faithful copy + ingestion metadata. No business logic. Replayable. |
| Silver | Parquet | Typed, de-duplicated, validated. Business-ready, not aggregated. |
| Gold   | Parquet | Modelled star schema. Referentially complete. Report-ready. |
```

### 16.2 `docs/data_dictionary.md`

**Purpose:** the column-level contract.

Every dataset should have: owner, steward, classification, grain, refresh cadence, and a table of columns with name, type, nullability, description, example value, and source.

```markdown
### gold.fact_sales

**Grain:** one row per unique `order_id` (valid, de-duplicated, Completed only)
**Owner:** <role: Head of Sales Operations> · **Classification:** Internal
**Row count (sample data):** 4 · **Sample total `sale_amount`:** 525.00

| Column | Type | Null? | Description | Example | Source |
|---|---|---|---|---|---|
| `order_id` | INT | No | Natural key of the order. Unique. | 101 | `orders.csv:order_id` |
| `customer_key` | INT | No | FK to `dim_customer`. Always resolves. | 1 | derived |
| `product_key` | INT | No | FK to `dim_product`. Always resolves. | 1003 | derived |
| `date_key` | INT | No | FK to `dim_date`, `yyyyMMdd`. | 20260105 | derived from `order_date` |
| `quantity` | INT | No | Units ordered. | 1 | `orders.csv:quantity` |
| `sale_amount` | DECIMAL(10,2) | No | Recognised value of the order. | 200.00 | `orders.csv:amount` |
| `order_status` | STRING | No | Always `Completed` in this table. | Completed | `orders.csv:status` |
| `processing_date` | DATE | No | Pipeline run date. | 2026-02-20 | generated |
```

Signs of a *bad* data dictionary: a description that restates the column name (`customer_id — the customer id`), missing nullability, no example values, and no statement of grain.

### 16.3 `docs/transformation_rules.md`

**Purpose:** the authoritative, reviewable statement of every business rule. This is where a disputed number gets settled.

Each rule needs: an ID, the layer, a plain-English statement, the rationale, the implementation location, and the effect on the sample data.

```markdown
| ID | Layer | Rule | Rationale | Implemented in | Sample effect |
|---|---|---|---|---|---|
| TR-01 | Bronze | Land all raw columns as strings; add `ingestion_timestamp` and `source_file`. | Preserve fidelity; defer type decisions. | `01_bronze_ingestion.py` | 8 order rows landed |
| TR-02 | Silver | Cast `order_date` to DATE, `amount` to DECIMAL(10,2), `quantity` to INT. | Correct arithmetic and comparisons. | `04_silver_orders.py` | — |
| TR-03 | Silver | De-duplicate on `order_id`, keeping the row with the latest `updated_at`. | `order_id` must be unique at order grain. | `04_silver_orders.py` | Removes 1 row (duplicate 107) → 7 rows |
| TR-04 | Silver | Trim whitespace and title-case `region`. | Prevent `North`/`north ` fragmenting groups. | `02_silver_customers.py` | — |
| TR-05 | Silver | Reject rows where any mandatory field is null. | Incomplete orders cannot be valued. | `04_silver_orders.py` | Flags order 105 (null `amount`) |
| TR-06 | Gold | Reject fact rows whose `customer_id` is absent from `dim_customer`. | Referential integrity. | `06_gold_fact_sales.py` | Rejects order 106 (`customer_id` 99) |
| TR-07 | Gold | `fact_sales` includes only `status = 'Completed'`. | Recognised Revenue definition. | `06_gold_fact_sales.py` | Excludes 103 (Cancelled) and 105 (Pending) |
| TR-08 | Gold | `date_key` = `yyyyMMdd` integer from `order_date`. | Conventional, compact date surrogate key. | `05_gold_dimensions.py` | 2026-01-05 → 20260105 |
| TR-09 | Gold | Net effect: `fact_sales` = 4 rows, `SUM(sale_amount)` = **525.00**. | Canonical figure. | `06_gold_fact_sales.py` | Orders 101, 102, 104, 107 |
```

Note how TR-09 makes the canonical number an explicit, testable rule. That single line prevents the three-conflicting-numbers meeting.

### 16.4 `docs/data_quality_rules.md`

**Purpose:** what we check, why, what happens on failure, and who to tell.

```markdown
| check_name | Dataset | Rule | Severity | On failure | Expected (sample) |
|---|---|---|---|---|---|
| `orders_unique_order_id` | silver.orders | `order_id` must be unique | High | Deduplicate, log, continue | FAIL, 1 |
| `orders_amount_not_null` | silver.orders | `amount` must be non-null | High | Quarantine row, alert steward | FAIL, 1 |
| `orders_customer_fk_valid` | gold.fact_sales | Every `customer_id` exists in `dim_customer` | High | Reject row, alert steward | FAIL, 1 |
| `orders_product_fk_valid` | gold.fact_sales | Every `product_id` exists in `dim_product` | High | Reject row, alert steward | PASS, 0 |
| `orders_status_in_allowed_values` | silver.orders | `status` ∈ {Completed, Cancelled, Pending} | Medium | Quarantine row | PASS, 0 |
| `fact_sales_revenue_reconciles` | gold.fact_sales | `SUM(sale_amount)` = 525.00 for the sample | Critical | **Stop the pipeline** | PASS, 0 |
```

Key documentation qualities here:

- **Severity drives behaviour.** Not every failure should stop a pipeline; critical ones must.
- **Expected results are documented.** The sample is *supposed* to fail three checks. If they suddenly pass, something is wrong with the checks, not the data.
- **Named checks** match the `check_name` column in `data/quality_results/`, so docs and data agree.

### 16.5 `docs/runbook.md`

**Purpose:** let someone who did not build the system operate it at 3am.

A runbook is written for a stressed reader. Short sentences. Numbered steps. Exact commands.

```markdown
# Runbook — customer-sales-lakehouse

## 1. Quick facts
| Item | Value |
|---|---|
| Schedule | Daily 02:00 local |
| Expected duration | < 5 minutes on sample data |
| Owner (role) | Data Engineering |
| Escalation | Data steward → data owner |
| Expected recognised revenue (sample) | 525.00 |

## 2. Normal run
```bash
python notebooks/01_bronze_ingestion.py
python notebooks/02_silver_customers.py
python notebooks/03_silver_products.py
python notebooks/04_silver_orders.py
python notebooks/05_gold_dimensions.py
python notebooks/06_gold_fact_sales.py
python notebooks/07_data_quality_checks.py
```

## 3. How to tell it worked
- [ ] `data/gold/fact_sales/` has been written with 4 rows
- [ ] `SUM(sale_amount)` = 525.00
- [ ] `data/quality_results/` has 6 rows for today's `processing_date`
- [ ] Exactly 3 checks FAIL with `failed_record_count` = 1 each (expected for the sample)

## 4. Common failures
| Symptom | Likely cause | Action |
|---|---|---|
| `FileNotFoundError` on raw path | Source file not delivered | Check `data/raw/`; do not re-run until present |
| Revenue ≠ 525.00 | A transformation rule changed | Compare against `docs/transformation_rules.md`; do not publish |
| `orders_product_fk_valid` FAILs | New product not in `products.csv` | Escalate to steward; a new product is a business event |
| Duplicate count > 1 | Source system re-delivered a batch | Confirm with source owner before re-running |
| Out-of-memory | Data volume grew | Increase `shuffle_partitions`; review partitioning |

## 5. Re-run safety
All stages are idempotent: outputs are overwritten per run, so a re-run
produces the same result. It is always safe to re-run the full pipeline
from `01_bronze_ingestion.py`.

## 6. Rollback
Bronze is immutable and retained. To recover, re-run silver and gold
from the existing bronze outputs; no source re-delivery is required.
```

### 16.6 Documenting assumptions

Assumptions are the invisible load-bearing walls of a data product. Write them down.

```markdown
## Assumptions

| # | Assumption | Impact if false | Verified by |
|---|---|---|---|
| A1 | `order_id` uniquely identifies an order across all time | Grain breaks; revenue double-counts | `orders_unique_order_id` |
| A2 | One order contains exactly one product | Grain must change to order-line | Manual review of source |
| A3 | `amount` is already net of discounts and tax | Revenue overstated/understated | Confirmed with data owner |
| A4 | All amounts are in a single currency | Cross-currency totals meaningless | Source system constraint |
| A5 | `status` only ever takes the three documented values | Unknown statuses silently excluded | `orders_status_in_allowed_values` |
| A6 | A customer's `region` does not change, or history is not required | Historical regional revenue misstated | Owner decision (Type 1) |
| A7 | Cancelled orders are never later re-completed | Revenue permanently understated | Owner confirmation |
| A8 | Source files are complete when they appear | Partial loads produce wrong totals | Row-count reconciliation |
```

Pattern: **assumption → impact if false → how we'd know.** The third column is what distinguishes an engineer from a note-taker.

### 16.7 Documentation quality checklist

- [ ] A newcomer can run the pipeline from the README alone
- [ ] Every number quoted in docs is reproducible from the code
- [ ] Every dataset has an owner, steward, classification and grain
- [ ] Every business rule has an ID and an implementation location
- [ ] Assumptions are explicit, with impact and verification
- [ ] The runbook gives exact commands and verification steps
- [ ] No secrets, no real names, no environment-specific hostnames
- [ ] Docs live in the repo and change in the same PR as the code
- [ ] Diagrams are text-based so they diff in version control
- [ ] A "last reviewed" date appears on each document

---

## 17. Stakeholder communication checklist

Technical excellence that nobody understands has limited value. These checklists cover the four moments that matter.

### 17.1 Intake — starting a new request

- [ ] Captured the **business question**, not the requested table ("I need revenue by region", not "give me a join of orders and customers")
- [ ] Identified **who will act** on the answer and what decision it drives
- [ ] Agreed the **definition** of every term used (what is "revenue"? what counts as a customer?)
- [ ] Confirmed the **grain** required (per order? per day? per month?)
- [ ] Established **freshness** requirements (real-time? daily? monthly?)
- [ ] Established **accuracy** requirements (exact? approximate is fine?)
- [ ] Identified the **data sources** and confirmed access is obtainable
- [ ] Assessed **data classification** and whether PII is involved
- [ ] Identified the **data owner** who must approve the definition
- [ ] Agreed **success criteria** ("the number matches finance's close figure")
- [ ] Agreed a **deadline** and flagged dependencies and risks
- [ ] Wrote down **assumptions** and shared them back for confirmation
- [ ] Confirmed whether an existing dataset already answers it (very often one does)
- [ ] Sent a short written summary and got explicit agreement before building

> **The single most valuable intake question:** *"What decision will you make differently depending on the answer?"* If there isn't one, the work may not be needed.

### 17.2 Status updates — keeping people informed

- [ ] Agreed a **cadence** up front (e.g. Friday written update)
- [ ] Each update states: **done / in progress / blocked / next**
- [ ] Blockers name the **specific help needed** and from whom
- [ ] Risks are raised **early**, not when they become issues
- [ ] Written in **business language**; technical detail in an appendix
- [ ] Includes a **confidence level** on the delivery date
- [ ] Scope changes are called out explicitly with their schedule impact
- [ ] Good news and bad news are delivered with the same promptness
- [ ] No surprises — anything in a status update should already have been flagged

```markdown
## Status — customer-sales-lakehouse — week ending <date>

**Overall:** 🟢 On track · **Confidence in <date>:** High

**Done this week**
- Gold star schema built; `fact_sales` reconciles to 525.00 on the sample set
- Six data-quality checks implemented and writing to `quality_results`

**In progress**
- Reporting queries for monthly revenue and regional breakdown (80%)

**Blocked**
- None

**Risks**
- 🟡 The sample dataset has only four customers, so region-level reporting is
  re-identifying. Recommend restricting region breakdowns until volume grows.
  *Decision needed from: data owner, by <date>.*

**Next week**
- Complete reporting queries; draft runbook; dry-run handover
```

### 17.3 Incident communications

- [ ] **Notify early**, even before you know the cause — silence is worse than uncertainty
- [ ] State **impact first** in business terms, cause later
- [ ] Say clearly whether the data **can still be used** in the meantime
- [ ] Give a **next-update time** and honour it
- [ ] Use one channel as the **source of truth**; don't fragment across DMs
- [ ] Keep a **timeline** as you go — reconstructing it later is painful
- [ ] Separate **mitigation** (stop the bleeding) from **fix** (correct the cause)
- [ ] Confirm **resolution explicitly**; people need to know it's over
- [ ] Follow up with a **blameless review** and concrete prevention actions
- [ ] Never speculate publicly about cause or blame before the facts are in

### 17.4 Handover

- [ ] All code is in version control with a clear README
- [ ] `docs/architecture.md`, `data_dictionary.md`, `transformation_rules.md`, `data_quality_rules.md` and `runbook.md` are current
- [ ] Assumptions and known limitations are documented
- [ ] Access has been transferred, not shared — the receiver has their **own** credentials
- [ ] A live walkthrough has been done, with the receiver driving
- [ ] The receiver has **successfully run the pipeline unaided**
- [ ] Escalation contacts and ownership (RACI) are updated
- [ ] Open issues and planned work are written down and prioritised
- [ ] Monitoring and alerts route to the new owner
- [ ] An agreed support window exists where the outgoing owner is still reachable
- [ ] Your personal accounts and tokens are removed from all automation

> **The handover test:** the receiver runs the pipeline end to end, explains why revenue is 525.00, and finds the answer to one novel question using only the documentation. If they can, the handover is real.

### 17.5 Communication principles

| Principle | In practice |
|---|---|
| **Lead with the answer** | "February revenue was 275.00." Then the method. |
| **Quantify uncertainty** | "±0 — this is an exact figure from a complete source." |
| **Translate, don't dumb down** | "We removed a duplicated order" beats "we applied `dropDuplicates`" and beats "it's complicated." |
| **Show the exclusions** | Always say what you *left out* and why. That's where disputes live. |
| **Write it down** | A decision not written down will be re-litigated. |
| **Escalate early** | Early escalation is information. Late escalation is an apology. |
| **Never present a number you can't derive** | If you can't trace it, don't say it. |

---

## 18. Incident and escalation template

### 18.1 Severity definitions

| Severity | Definition | Example in this project | Response | Comms |
|---|---|---|---|---|
| **SEV1 — Critical** | Wrong data published and in use for decisions; or total outage of a business-critical dataset | Revenue reported as 975.00 because de-duplication was removed | Immediate; all hands | Notify consumers within 30 min; updates hourly |
| **SEV2 — High** | Pipeline failed; data stale beyond SLA; quality gate failed and blocked publication | Pipeline aborted; `fact_sales` not refreshed | Same business day | Notify consumers within 2h; updates every 4h |
| **SEV3 — Medium** | Degraded but usable; non-blocking quality failures | Quality checks FAIL as expected but counts increased | Next business day | Daily update to steward |
| **SEV4 — Low** | Cosmetic, documentation, or future risk | Data dictionary out of date | Backlog | Standard reporting |

### 18.2 Escalation path

```text
          ┌─────────────────────────────────────────────┐
  DETECT  │ Alert, failed check, or consumer report     │
          └────────────────────┬────────────────────────┘
                               ▼
          ┌─────────────────────────────────────────────┐
  TRIAGE  │ Data engineer on duty — assign severity     │
          │ within 15 min (SEV1/2) or 1 business day    │
          └────────────────────┬────────────────────────┘
                               ▼
        SEV3/SEV4 ◄────────────┼────────────► SEV1/SEV2
            │                                     │
            ▼                                     ▼
   ┌──────────────────┐            ┌──────────────────────────┐
   │ Data steward     │            │ Data steward + data owner│
   │ Normal backlog   │            │ notified immediately     │
   └──────────────────┘            └────────────┬─────────────┘
                                                ▼
                                   ┌──────────────────────────┐
                                   │ SEV1 only: notify all    │
                                   │ known consumers + stop   │
                                   │ downstream publication   │
                                   └────────────┬─────────────┘
                                                ▼
                                   ┌──────────────────────────┐
                                   │ Security contact if data │
                                   │ exposure or PII involved │
                                   └──────────────────────────┘
```

### 18.3 Incident record template

```markdown
# Incident <ID> — <short, factual title>

## Summary
| Field | Value |
|---|---|
| Severity | SEV<1-4> |
| Status | Investigating / Mitigated / Resolved / Closed |
| Detected at | <YYYY-MM-DD HH:MM timezone> |
| Detected by | <alert name / role / consumer report> |
| Affected datasets | <e.g. gold.fact_sales, reporting queries> |
| Affected consumers | <e.g. revenue dashboard, finance month-end> |
| Data classification involved | Public / Internal / Confidential / Restricted |
| PII involved | Yes / No |
| Incident lead (role) | <role> |
| Data owner notified | Yes / No — at <time> |

## Impact — business language first
<e.g. "Reported February revenue was 475.00 instead of the correct 275.00,
an overstatement of 200.00, because a duplicated order was counted twice.
Two dashboards and one month-end pack used the incorrect figure.">

**Can the data be used right now?** <Yes / No / Only for X>

## Timeline (UTC)
| Time | Event |
|---|---|
| <HH:MM> | Pipeline run started |
| <HH:MM> | `orders_unique_order_id` check reported FAIL with count 1 |
| <HH:MM> | Check result not gated; pipeline continued and published |
| <HH:MM> | Consumer reported a discrepancy |
| <HH:MM> | Incident declared SEV1; downstream publication paused |
| <HH:MM> | Root cause identified |
| <HH:MM> | Corrected data republished; revenue reconciles to 525.00 |
| <HH:MM> | Consumers notified of correction |
| <HH:MM> | Incident resolved |

## Root cause
<Technical cause AND the systemic reason it was possible.
 e.g. "A change removed the dropDuplicates step (technical). It shipped
 because the de-duplication rule had no automated test asserting the
 expected row count (systemic).">

## Mitigation taken
<What stopped the bleeding, e.g. "Reverted the change and re-ran the
 pipeline from bronze; no source re-delivery required.">

## Permanent fix
<e.g. "Added a unit test asserting silver.orders has 7 rows and
 fact_sales sums to 525.00 for the sample dataset. Added a critical-severity
 quality gate that aborts the pipeline when revenue does not reconcile.">

## Data correction
- [ ] Incorrect data identified and scoped
- [ ] Corrected data produced and verified (revenue = 525.00)
- [ ] Downstream consumers re-run or notified
- [ ] Correction communicated to everyone who saw the wrong figure
- [ ] Any external/regulatory reporting assessed for restatement

## Prevention actions
| # | Action | Owner (role) | Due | Status |
|---|---|---|---|---|
| 1 | Add reconciliation test to `tests/` | Data Engineer | <date> | Open |
| 2 | Make critical quality checks pipeline-blocking | Data Engineer | <date> | Open |
| 3 | Add revenue-anomaly alert (>20% day-on-day change) | Data Engineer | <date> | Open |
| 4 | Document the gate in `docs/runbook.md` | Steward | <date> | Open |

## Lessons learned (blameless)
**What went well:** <e.g. the quality check correctly detected the duplicate>
**What went poorly:** <e.g. the check was informational and did not block publication>
**Where we got lucky:** <e.g. a consumer noticed within a day>
**Systemic change:** <what makes this class of failure impossible, not just this instance>
```

### 18.4 Consumer notification template

```markdown
Subject: [Data Incident SEV1] Sales revenue figures overstated — do not use

**What happened**
Revenue figures published for February 2026 were overstated. The reported
figure included a duplicated order.

**Impact**
- Affected report: monthly recognised revenue
- Incorrect figure shown: <X>
- Correct figure: 275.00 for February 2026 (total sample revenue 525.00)
- Affected period of publication: <date> to <date>

**What you should do now**
Do not use figures from the affected reports for the period above.
Previously circulated figures should be considered withdrawn.

**What we are doing**
The pipeline has been corrected and data republished. A reconciliation
control has been added so this cannot recur undetected.

**Next update:** <time> · **Contact:** <team channel / role>
```

Notice what the template does **not** contain: names of individuals, blame, speculation, or technical jargon in the lead. It leads with impact and with the action the reader must take.

### 18.5 A note on scope

Everything in this guide is **generic and principle-based**. Real organisations have their own incident management systems, named severity scales, regulatory obligations, approved tooling and legally reviewed notification procedures. When you join one, **follow their documented process**, not this template. Use this to understand *why* their process exists and to recognise good practice when you see it.

Never reproduce a specific employer's confidential procedures, escalation contacts, internal tooling details, or client names in a public portfolio.

---

## 19. Self-assessment and practice exercises

### 19.1 Can you explain these without notes?

- [ ] Why three people can produce three different revenue numbers from the same file
- [ ] The difference between a data owner, a data steward, and a data custodian
- [ ] The difference between a data dictionary and a business glossary
- [ ] Why `region` is quasi-identifying even though it isn't a name
- [ ] Why `fact_sales` is Internal but `dim_customer` is Confidential
- [ ] Authentication vs authorization, with an example of each failing
- [ ] Why managed identity is preferable to a service principal secret
- [ ] The three dimensions of least privilege
- [ ] Why you rotate a leaked secret **before** rewriting git history
- [ ] Why `log_row_samples` is `false` in dev as well as prod
- [ ] What makes a log an audit log rather than an application log
- [ ] Why the sample data is *supposed* to fail three quality checks
- [ ] Why recognised revenue is 525.00 and not any other number

### 19.2 Practice exercises

**Exercise 1 — Classify and justify.**
For each column in the three CSVs, assign a classification tier and write one sentence of justification. Compare against Section 15.2.

**Exercise 2 — Write the glossary entry.**
A stakeholder asks for "average order value". Write a full glossary entry: definition, formula, grain, exclusions, owner, and the value for the sample data. (Hint: decide whether the denominator is recognised orders or all orders — and notice how much that choice matters. With 4 recognised orders totalling 525.00, one defensible answer is 131.25.)

**Exercise 3 — Design the access matrix.**
A new role, `finance-controller`, needs customer-level revenue for month-end but must not see customer names. Which objects do you grant, and which do you deliberately withhold? Write the `CREATE VIEW` statement that makes this possible.

**Exercise 4 — Secret leak drill.**
Write out, in order, the ten steps you would take if you discovered a storage account key committed to `config/dev.yml` three weeks ago. Identify which single step most reduces risk, and explain why it comes first.

**Exercise 5 — Environment diff.**
Without looking back, list five settings that should differ between `config/dev.yml` and `config/prod.yml`, and one that must be identical in both. Explain the governance reason for each.

**Exercise 6 — Incident simulation.**
Someone removes the de-duplication step. February revenue is published as 475.00. Complete the incident record template in Section 18.3 end to end, including the consumer notification.

**Exercise 7 — Redaction review.**
Review `src/logging_utils.py` in the project. List every place a Confidential value could leak into a log, and propose a change for each.

**Exercise 8 — Re-identification audit.**
Write a query that lists every region-level aggregate where fewer than five distinct customers contribute. Explain to a non-technical stakeholder why those rows should be suppressed, using the West region as the example.

**Exercise 9 — Documentation gap hunt.**
Hand `../customer-sales-lakehouse` to a friend. Ask them to run it and tell you February's revenue. Every question they have to ask you is a documentation defect. Fix each one.

**Exercise 10 — Assumption register.**
Add three assumptions to Section 16.6 that are not already listed, each with impact-if-false and how you would verify it.

### 19.3 Interview questions you should be ready for

1. "Two reports disagree on revenue. Walk me through how you'd resolve it."
2. "How do you decide who gets access to a dataset?"
3. "Where do you store credentials for a production pipeline, and why there?"
4. "You find an API key in the repo. What do you do, in order?"
5. "How do you stop PII leaking into logs?"
6. "How do dev and prod differ in your project, and how is that enforced?"
7. "What's the difference between a data owner and a data steward?"
8. "Your pipeline's quality check fails. Does the pipeline stop? How do you decide?"
9. "How would you handle a customer's deletion request across a medallion lakehouse?"
10. "Your sample has one customer in a region. Why is a region-level report risky?"
11. "What goes in a runbook that doesn't go in an architecture document?"
12. "How do you communicate a data incident to non-technical stakeholders?"

### 19.4 Where to go next

- `10_azure_data_engineer_study_plan.md` — a 12-week plan that builds the Azure skills to deploy these principles.
- `11_portfolio_project_blueprint.md` — how to package `../customer-sales-lakehouse` as a portfolio piece, including how to describe governance work honestly.
- Revisit `04_etl_elt_and_quality.md` for the mechanics of the quality checks referenced throughout.
- Revisit `08_dbt_orchestration_monitoring.md` for how lineage and testing are automated by tooling.

---

## Key takeaways

| # | Takeaway |
|---|---|
| 1 | Governance exists to prevent the three-conflicting-numbers meeting. One definition, one implementation, one owner. |
| 2 | Recognised revenue for this sample is **525.00**. Any other number is an incident. |
| 3 | Exactly one person is accountable (**A**) for each governed activity. |
| 4 | Classification is per column; a dataset inherits its highest tier. |
| 5 | Secrets never appear in code, notebooks, config, logs, or history — no exceptions, including dev. |
| 6 | If a secret leaks: **rotate first**, clean history second. |
| 7 | Least privilege has three dimensions: permission, scope, and **time**. |
| 8 | Environment differences live in config, never in `if env == 'prod'` branches. |
| 9 | Log counts, keys and decisions. Never log PII, secrets, or row contents. |
| 10 | Minimisation is the strongest privacy control: data you never hold cannot leak. |
| 11 | Documentation is a deliverable; the test is whether a stranger can operate your pipeline. |
| 12 | Lead communications with business impact, and always state what you excluded. |
| 13 | Incident reviews are blameless and target systemic causes, or people stop reporting. |
| 14 | Keep everything generic and principle-based — never publish an employer's confidential procedures. |

---

*Last reviewed: 2026-10-08 · Part of the data-engineering-learning series · Companion project: [`../customer-sales-lakehouse`](../customer-sales-lakehouse)*
