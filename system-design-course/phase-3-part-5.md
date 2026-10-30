# Lesson 56 — Database Normalisation and Denormalisation

*Phase 3, Topic 17*

## 1. Simple definition

**Normalisation** is the process of arranging a relational schema so that **every fact is stored in exactly one place**.

**Denormalisation** is the deliberate, measured decision to **break that rule in specific places** — storing a copy of a fact somewhere else — in order to make a known, measured read faster.

Lesson 40 introduced tables and keys. Lesson 42 covered joins. Lesson 43 covered constraints and referential integrity. This lesson answers the question those lessons left open: *given the data BookNook needs to store, which columns belong in which tables, and why?*

Terms defined before use:

- **Redundancy**: the same fact physically written in more than one row or more than one table. "Publisher *Harrow Press* is based in Leeds" written on 4,000 book rows is 4,000 copies of one fact.
- **Anomaly**: a situation where a normal `INSERT`, `UPDATE` or `DELETE` either corrupts the data or is impossible to express. Anomalies are the *symptom*; redundancy is the *cause*.
- **Candidate key**: a minimal set of columns whose values uniquely identify a row. A table may have several. One is chosen as the **primary key**; the others are **alternate keys**.
- **Prime attribute**: a column that is part of *any* candidate key. **Non-prime attribute**: a column that is part of none.
- **Normal form**: a named, precisely defined condition a table either satisfies or does not. They are cumulative — a table in 3NF is automatically in 2NF and 1NF.

The one sentence to carry out of this lesson: **normalisation is the default because it makes correctness automatic; denormalisation is an exception you earn with a measurement, and pay for with a synchronisation mechanism.**

## 2. Why it exists

Suppose BookNook starts with the obvious thing a beginner does — one wide table holding everything about an order line:

```
order_lines(
  order_id, order_date, customer_email, customer_name, customer_address,
  isbn, book_title, author_name, author_born, publisher_name, publisher_city,
  unit_price, quantity
)
```

It works. You can query it with no joins. And it is a trap, because the same fact is now written many times, and nothing forces the copies to agree. Three specific failures follow — the **insert**, **update** and **delete anomalies**. These are not abstractions; each one is a concrete way BookNook's data silently becomes wrong.

### Update anomaly

*Harrow Press* moves from Leeds to Manchester. The fact "Harrow Press is in Manchester" lives on every order line that ever contained a Harrow Press book — say **412,000 rows**.

```sql
UPDATE order_lines SET publisher_city = 'Manchester' WHERE publisher_name = 'Harrow Press';
```

Three ways this corrupts BookNook:

1. The statement updates 412,000 rows. It takes ~90 s, holds row locks the whole time (Lesson 53), and blocks checkout writes touching the same rows.
2. If it is interrupted at 60 % — connection drop, deploy, statement timeout — and the application was not running it inside a transaction, 247,000 rows say Manchester and 165,000 say Leeds. **The database now holds two contradictory answers to one question, and no constraint was violated.** `SELECT DISTINCT publisher_city FROM order_lines WHERE publisher_name='Harrow Press'` returns two rows.
3. Someone typed `'Harrow press'` on 300 rows years ago. The `WHERE` misses them. They stay Leeds forever, and nobody notices for two years.

The deeper point: **there is no single row you can point to and say "this is the publisher's city".** Correctness depends on 412,000 rows agreeing, which is a property no mechanism is enforcing.

### Insert anomaly

BookNook signs a contract with a new publisher, *Pelham & Vole*, in Bristol. Their first title ships in six weeks. Where does the fact "Pelham & Vole is in Bristol" go?

It cannot go anywhere. `order_lines` rows require an `order_id`, an `isbn` and a `quantity`. **You cannot record a publisher without inventing a fake order.** Teams solve this by inserting a sentinel row (`order_id = -1`, `quantity = 0`), and now every analytical query must remember to exclude it — and one of them always forgets, and the quarterly revenue report is wrong by one phantom order.

The general statement: **in an under-normalised table you cannot record a fact about entity A until an unrelated entity B exists.**

### Delete anomaly

A customer exercises a data-deletion request. BookNook deletes their order lines. Those were the only three lines in the entire table containing *Pelham & Vole*. The publisher's city, the author's birth year, and the book's title are now **gone from the database entirely** — destroyed as collateral damage of an unrelated deletion.

The general statement: **in an under-normalised table, deleting a fact about entity B can destroy the only copy of a fact about entity A.**

### Plus the costs that are not anomalies

- **Storage.** Repeating `publisher_name`, `publisher_city`, `author_name` on every line at ~80 bytes × 412,000 rows ≈ 33 MB for *one* publisher's duplication. Across the whole table it is gigabytes of nothing.
- **Cache efficiency.** Those bytes occupy buffer-pool pages (Lesson 54), so fewer useful rows fit in RAM.
- **Index bloat.** Any index on `publisher_city` has 412,000 entries where 1 would do.

Normalisation removes all five problems at once, by a single mechanism: **give every fact exactly one home row, and reference it by key.**

## 3. Real-world analogy

**A company's internal phone directory versus sticky notes on desks.**

The sticky-note version: every employee writes their colleagues' extension numbers on notes stuck to their own monitor. Fast — you glance down, no walking to a directory. This is the denormalised design.

- **Update anomaly**: Priya changes extension. Eighty monitors still show the old number. Some get updated, some don't. Two "truths" coexist.
- **Insert anomaly**: a new hire's number cannot be recorded until somebody has a reason to write it on their own monitor.
- **Delete anomaly**: Tom leaves and his desk is cleared. His monitor held the *only* note with the facilities team's out-of-hours number. It is gone.
- **The normalised version**: one directory page on the intranet. One row per person. Priya changes it once; everyone is correct immediately. But every lookup now costs a walk to the intranet — a *join*.
- **Justified denormalisation**: the fire warden's phone list is printed and pinned by every exit. Duplicated on purpose, because at the moment you need it, the intranet is exactly what you cannot reach. Note that it comes with an explicit synchronisation process: a named person reprints them quarterly.

**Where the analogy breaks down:**

| Office directory | Relational database |
|---|---|
| A human notices "that number looks stale" and double-checks | The database never notices. A stale duplicate is returned with full confidence, indistinguishable from truth |
| Reprinting 12 fire lists is trivial | Updating 412,000 duplicated rows is a long-running write that competes with live traffic and can itself cause an outage |
| One directory, one copy, obviously authoritative | After denormalisation there is no inherent marker of which copy is authoritative — you must *decide* and document it, or two teams will each believe a different column |
| Walking to the intranet always costs the same | A join's cost depends on row counts, index availability, join order and the planner's estimates (Lesson 55). It can be 0.2 ms or 20 s for the *same* SQL |
| Sticky notes never conflict with each other mid-write | Two concurrent transactions updating the same duplicated column can interleave; without the right isolation level (Lesson 52) the copies diverge *even when both writers did everything right* |
| The directory is the goal | In a database, normalisation is **not** the goal. Correct, fast answers are the goal. Normalisation is the default route to them — nothing more |

That last row matters. Chasing normal forms as a scorecard is as wrong as denormalising on a hunch.

## 4. Detailed technical explanation

### 4.1 Functional dependencies, in plain language

A **functional dependency** written `X → Y` means:

> *"If I know X, then Y is already decided. Two rows with the same X can never disagree about Y."*

That is the whole idea. Examples from BookNook:

- `isbn → title` — an ISBN identifies one edition of one book; its title is fixed.
- `isbn → publisher_id`, and `publisher_id → publisher_city`.
- `(order_id, isbn) → quantity` — quantity is decided only by *which order* and *which book* together. `order_id` alone does not decide it; `isbn` alone certainly does not.
- `customer_id → email` holds. `email → customer_id` *also* holds if emails are unique — then `email` is an alternate key.
- `order_id → order_date` holds. `order_date → order_id` does **not**; many orders share a date.

A dependency is a statement about the **business rules of the domain**, not about the rows you happen to have today. If your table currently has one Leeds publisher, you cannot conclude `publisher_city → publisher_name`. You derive dependencies by asking the business, not by inspecting data.

**Transitive dependency**: `X → Y` and `Y → Z`, where `Y` is not a key, therefore `X → Z` indirectly. `isbn → publisher_id → publisher_city` is the canonical BookNook case, and it is exactly what 3NF forbids.

**Partial dependency**: the key is composite, say `(order_id, isbn)`, and a non-prime column depends on only *part* of it. `order_date` depends on `order_id` alone. That is exactly what 2NF forbids.

Every normal form from 2NF onward is a rule of the same shape: **the only thing a non-key column is allowed to depend on is the whole key.** The old mnemonic is precise and worth memorising:

> *Every non-key attribute must depend on **the key** (1NF/2NF), **the whole key** (2NF), **and nothing but the key** (3NF) — so help me Codd.*

### 4.2 The starting point: one flat table

```sql
-- STEP 0 — unnormalised ("0NF"). Illustrative only; never ship this.
order_lines(
  order_id, order_date, customer_email, customer_name,
  customer_phones,            -- '0113-555-0191, 07700-900123'  ← multiple values in one cell
  isbn, book_title, book_categories,  -- 'Fiction; Historical'  ← repeating group
  author_name, author_born,
  publisher_name, publisher_city,
  unit_price, quantity
)
```

### 4.3 First normal form (1NF)

> **Definition.** A table is in 1NF if every column holds a **single, atomic value** from its declared domain, there are **no repeating groups** (no `phone1, phone2, phone3`), every row is **unique** (a key exists), and row order carries no meaning.

`customer_phones` holding `'0113-555-0191, 07700-900123'` violates 1NF: it is a list hiding in a string. So does `book_categories`. The practical damage is immediate — you cannot index a phone number, `WHERE customer_phones = '07700-900123'` misses the row, and `LIKE '%0113%'` matches `'0113'` appearing inside a different number.

Fix: pull each multi-valued attribute into its own table.

```sql
-- STEP 1 — 1NF
order_lines(order_id, order_date, customer_id, customer_email, customer_name,
            isbn, book_title, author_name, author_born,
            publisher_name, publisher_city, unit_price, quantity)
            -- PRIMARY KEY (order_id, isbn)

customer_phones(customer_id, phone)        -- PRIMARY KEY (customer_id, phone)
book_categories(isbn, category)            -- PRIMARY KEY (isbn, category)
```

**Teaching model vs production reality.** The textbook rule says atomic values only. Production PostgreSQL happily stores `text[]` and `jsonb`, and sometimes that is the right call — a `tags text[]` with a GIN index (Lesson 55) is indexable and queryable, so it is not the opaque blob 1NF was invented to outlaw. The rule's *intent* is "do not hide structure the database cannot see or index". Judge against the intent, not the letter.

### 4.4 Second normal form (2NF)

> **Definition.** A table is in 2NF if it is in 1NF **and** no non-prime attribute is functionally dependent on a *proper subset* of any candidate key. Informally: **no partial dependencies.** A table whose primary key is a single column is automatically in 2NF.

In the 1NF table the key is `(order_id, isbn)`. Check each non-key column:

| Column | Depends on | Verdict |
|---|---|---|
| `order_date` | `order_id` only | **Partial — violates 2NF** |
| `customer_id`, `customer_email`, `customer_name` | `order_id` only | **Partial — violates 2NF** |
| `book_title`, `author_name`, `author_born` | `isbn` only | **Partial — violates 2NF** |
| `publisher_name`, `publisher_city` | `isbn` only | **Partial — violates 2NF** |
| `unit_price` | `(order_id, isbn)` — the price *charged on that order* | OK (whole key) |
| `quantity` | `(order_id, isbn)` | OK (whole key) |

Split on the dependency boundaries:

```sql
-- STEP 2 — 2NF
orders(order_id PK, order_date, customer_id, customer_email, customer_name)
books(isbn PK, book_title, author_name, author_born, publisher_name, publisher_city)
order_items(order_id, isbn, unit_price, quantity)   -- PK (order_id, isbn)
customer_phones(customer_id, phone)
book_categories(isbn, category)
```

The insert anomaly is already half-dead: a new book can be inserted with no order. But the update anomaly survives — `publisher_city` is still repeated across every book from that publisher.

### 4.5 Third normal form (3NF)

> **Definition.** A table is in 3NF if it is in 2NF **and** no non-prime attribute is transitively dependent on a candidate key. Equivalently, for every non-trivial dependency `X → Y`, either `X` is a superkey, **or** every column in `Y` is prime.

Remaining offenders:

- In `books`: `isbn → publisher_name → publisher_city`. `publisher_name` is not a key of `books`, so `publisher_city` is transitively dependent. **Violation.**
- In `books`: `isbn → author_name → author_born`. Same shape. **Violation.**
- In `orders`: `order_id → customer_id → customer_email, customer_name`. **Violation.**

Extract each transitively-dependent group into its own table, referenced by a key:

```sql
-- STEP 3 — 3NF  (the practical target)
customers(customer_id PK, email UNIQUE, name)
customer_phones(customer_id FK, phone)                -- PK (customer_id, phone)
publishers(publisher_id PK, name, city)
authors(author_id PK, name, born)
books(isbn PK, title, author_id FK, publisher_id FK)
book_categories(isbn FK, category)                    -- PK (isbn, category)
orders(order_id PK, customer_id FK, order_date)
order_items(order_id FK, isbn FK, unit_price, quantity)   -- PK (order_id, isbn)
```

Now re-run the three anomalies:

- **Update**: Harrow Press moves. `UPDATE publishers SET city='Manchester' WHERE publisher_id=77;` — **one row, ~0.3 ms, atomic, no partial-failure state possible.** 412,000 → 1.
- **Insert**: `INSERT INTO publishers ...` with no order in sight. Works.
- **Delete**: deleting a customer's orders cannot touch `publishers`, `authors` or `books`. Foreign keys (Lesson 43) make the dependency explicit and the deletion safe.

**Why 3NF is the practical target for transactional systems.** Three concrete reasons, not a convention:

1. **It eliminates all three anomalies** arising from redundancy of non-key facts. 2NF does not; BCNF and above address residual cases that are rare in practice.
2. **It is always achievable while being both lossless and dependency-preserving.** "Lossless" means the original table can be reconstructed exactly by joining. "Dependency-preserving" means every functional dependency can still be enforced by a key or unique constraint on a *single* table, with no cross-table check. BCNF guarantees losslessness but **not** dependency preservation — so pushing to BCNF can force you to enforce a rule in application code or a trigger, which is strictly worse than a `UNIQUE` constraint.
3. **It matches OLTP access patterns.** Transactional writes touch few rows; narrow tables mean fewer bytes written, smaller WAL records, less index churn, and more rows per buffer-pool page (Lesson 54).

### 4.6 Boyce–Codd normal form (BCNF)

> **Definition.** A table is in BCNF if for **every** non-trivial functional dependency `X → Y`, `X` is a superkey. BCNF is 3NF with the "or Y is prime" escape hatch removed.

3NF permits a dependency whose right-hand side is a prime attribute. That loophole bites only when a table has **multiple overlapping candidate keys**. BookNook has exactly one such case: shop-floor pick assignments in the warehouse.

Rules: each `(warehouse, isbn)` pair is assigned to exactly one picker; and each picker works in exactly one warehouse.

```sql
-- 3NF but NOT BCNF
pick_assignments(warehouse_id, isbn, picker_id)
-- Candidate keys: (warehouse_id, isbn)  and  (picker_id, isbn)
-- Dependencies:
--   (warehouse_id, isbn) -> picker_id     ✔ left side is a superkey
--   picker_id -> warehouse_id             ✘ picker_id is NOT a superkey
-- 3NF is satisfied only because warehouse_id is prime (it is in a candidate key).
```

The redundancy is real: "picker 9 works in warehouse 3" is repeated on every row for picker 9, and the update anomaly returns in miniature — move picker 9 to warehouse 5 and you must update every one of their rows consistently.

```sql
-- STEP 4 — BCNF
pickers(picker_id PK, warehouse_id FK)        -- the fact "picker 9 is in warehouse 3", stored once
pick_assignments(picker_id FK, isbn, ...)     -- PK (picker_id, isbn)
```

**The honest cost.** The rule "each `(warehouse_id, isbn)` pair has exactly one picker" can no longer be enforced by a single-table constraint — `warehouse_id` is not in `pick_assignments` any more. This decomposition is lossless but **not dependency-preserving**, so the rule now needs a trigger, a materialised helper column, or application logic. That is the standard BCNF trade, and it is why many production schemas stop at 3NF and accept the small redundancy.

### 4.7 4NF and 5NF — brief, and rarely needed

- **4NF** removes **multi-valued dependencies**: two genuinely independent multi-valued facts crammed into one table. If BookNook stored `author_promotions(author_id, language_edition, conference_attended)`, and editions and conferences are independent, the table forces a Cartesian product — 3 editions × 4 conferences = 12 rows encoding 7 facts. The fix is obvious once named: two separate tables. **If you decomposed every many-to-many relationship into its own junction table (which 1NF already pushes you towards), you are almost certainly in 4NF already.**
- **5NF** (project–join normal form) removes **join dependencies**: a table that must be decomposed into *three or more* pieces to remove redundancy without loss. Genuine 5NF-only violations are rare enough that most engineers never knowingly meet one.

**Practical guidance:** aim for 3NF, check BCNF when a table has overlapping candidate keys, and treat 4NF/5NF as vocabulary you should recognise in an interview rather than a routine design step.

### 4.8 Denormalisation: the deliberate, measured trade

Denormalisation is **duplicating data to avoid a join or an aggregation at read time**. It is not "skipping normalisation" — you normalise first, then selectively copy, knowing exactly which copy is authoritative.

**All four criteria must hold before you denormalise. Not three.**

| # | Criterion | How you satisfy it — concretely |
|---|---|---|
| 1 | **Measured read latency** | You have a p95/p99 number for the specific query, from production, and a target it is missing. "Joins are slow" is not a measurement. Check first whether an index (Lesson 54), a better join order, or a covering index (Lesson 55) fixes it — they often do, at zero correctness cost |
| 2 | **Known, stable access pattern** | You can name the query, its frequency, and its read:write ratio. Denormalisation optimises *one* shape; if the shape changes quarterly you will be rebuilding it quarterly |
| 3 | **Acceptable staleness** | The product owner has agreed, in writing, to a specific bound: "the best-seller rank may be up to 10 minutes old." If the answer is "it must always be exact", you cannot use an asynchronously maintained copy |
| 4 | **Manageable update cost** | You have computed the **write amplification** (§9) and the **fan-out** — how many duplicate rows one source change must touch. A fan-out of 4 is routine; a fan-out of 400,000 is an outage waiting for a trigger |

### 4.9 The techniques

**(a) Redundant column (copied attribute).** Copy a parent column into the child to avoid a join.

```sql
ALTER TABLE order_items ADD COLUMN book_title text;  -- copy of books.title
```
BookNook's order-history page lists titles. This removes a join to `books`. **Important nuance:** for an order, the title at purchase time is arguably a *different fact* from the book's current title — a historical snapshot, like `unit_price`. When the copy is a point-in-time snapshot it is **not redundancy at all**; it is an independent fact that must never be synced. Distinguish the two cases explicitly in a comment, or a future engineer will "fix" it.

**(b) Precomputed aggregate (counter column).** Store the answer to a `COUNT`/`SUM`/`AVG` next to the entity.

```sql
ALTER TABLE books ADD COLUMN review_count int NOT NULL DEFAULT 0,
                  ADD COLUMN rating_sum  bigint NOT NULL DEFAULT 0;
```
BookNook's product page shows "4.3 ★ (1,284 reviews)". Without this, every page view aggregates 1,284 review rows. With it, the page reads two integers already on the row it was fetching anyway — **zero extra page reads**.

**(c) Summary table (rollup).** A separate table holding pre-grouped results.

```sql
daily_sales_by_category(day, category, units, revenue)  -- PK (day, category)
```
3 years of daily rollups across 40 categories = ~44,000 rows, versus ~88 M `order_items` rows. The dashboard reads 44,000 rows instead of scanning 88 M.

**(d) Materialised view.** A summary table the *database* maintains and knows about. In PostgreSQL it is refreshed on command (`REFRESH MATERIALIZED VIEW CONCURRENTLY`), not continuously; in Oracle and SQL Server (indexed views) it can be maintained synchronously on write. The advantage over a hand-rolled summary table is that the definition lives in the schema, so the duplication is **documented and reproducible** rather than folklore.

**(e) Arrays and JSONB columns.** Collapse a whole child collection into the parent row.

```sql
ALTER TABLE orders ADD COLUMN items_snapshot jsonb;
-- [{"isbn":"978...","title":"The Lamplighters","qty":1,"price":8.99}, ...]
```
BookNook's order-confirmation and order-history pages are satisfied by **one row read**. This is the most powerful and the most dangerous technique: it trades away the ability to query, index and constrain the inner fields the way real columns allow. Use it for **immutable snapshots** (what was ordered, at what price, on what date) and avoid it for live, mutable, independently-queried data.

### 4.10 The cost, stated honestly

> **Every duplicate must be kept in sync, by a named mechanism, with a named owner, and a known staleness bound. If you cannot name all three, you have not denormalised — you have introduced a bug with a delayed fuse.**

The four mechanisms, with their real properties:

| Mechanism | How it works | Consistency | Honest drawbacks |
|---|---|---|---|
| **Database trigger** | `AFTER INSERT/UPDATE/DELETE` on the source table updates the copy | **Synchronous** — copy is updated in the same transaction, so it can never be stale | Invisible to application developers; adds latency to every write; hot counter rows serialise concurrent writers (Lesson 53) and cause lock contention and deadlocks; logic lives outside version-controlled application code unless you are disciplined |
| **Application logic** | The service writes both the source and the copy | Synchronous *only* if both writes are in **one** transaction | If the copy lives in a different store, you have a dual-write problem with no atomicity — a crash between the two leaves them permanently divergent. Every code path must remember; the one written by the new joiner will not |
| **Change data capture (CDC)** | A process tails the database's write-ahead log and applies derived updates downstream | **Asynchronous**, typically sub-second to seconds | Extra infrastructure, ordering and replay semantics to understand, and a real staleness window. Covered fully in **Phase 4, Topic 22 — Change data capture and event sourcing** |
| **Scheduled recomputation** | A periodic job rebuilds the copy from the authoritative tables | Stale by up to one interval | Simplest and most robust — and crucially **self-healing**, because it recomputes from the source rather than applying deltas. Cost scales with total data, not with the number of changes |

A practical pattern used widely: **incremental updates for freshness, plus a nightly full recomputation for correctness.** Deltas drift; the nightly rebuild is the safety net, and its diff count is a drift metric worth alerting on.

## 5. Request / data flow

A customer opens BookNook's order-history page, `GET /orders/12345`, against the 3NF schema plus a denormalised `order_summary` table maintained by trigger. Start to finish:

1. Request reaches an app server via the load balancer (Lesson 23) and the session is authenticated.
2. The handler checks Redis for `order:12345:summary` (Lesson 30). **Miss** — this is a cold order.
3. The handler takes a connection from the pool and issues one prepared statement: `SELECT * FROM order_summary WHERE order_id = $1`.
4. The planner (Lesson 55) resolves the cached plan: an index-only probe on the `order_summary` primary key. No join nodes appear in the plan at all.
5. The executor descends the B-tree (Lesson 55): root → internal → leaf, 3 pages, then 1 heap page, then 1 TOAST page for the `items_snapshot` JSONB. **5 page accesses total.**
6. The row is returned. The application deserialises the JSONB into line items. **Zero joins executed.**
7. The handler writes the result to Redis with a 300 s TTL and renders the page.
8. Meanwhile, in a separate request, a warehouse operator marks order 12345 as shipped: `UPDATE orders SET status='SHIPPED' WHERE order_id=12345`.
9. That `UPDATE` fires `AFTER UPDATE` trigger `trg_sync_order_summary`, **inside the same transaction**.
10. The trigger updates `order_summary.status` for `order_id = 12345`. Both rows are modified atomically — a reader can never observe `orders` shipped while `order_summary` says pending.
11. The transaction commits; WAL is flushed; the change becomes visible under the session's isolation level (Lesson 52).
12. The application publishes a cache-invalidation message so the stale Redis key is deleted. **This step is asynchronous and therefore the real staleness window** — up to a few hundred milliseconds. The *database* copies are consistent; the *cache* copy is eventually consistent (Lesson 31).
13. Nightly at 03:10, a reconciliation job recomputes `order_summary` for orders modified in the last 48 h and emits a `summary_drift_rows` metric. An alert fires if it is non-zero — that metric is how a broken trigger is discovered before a customer finds it.

Step 13 is the part teams skip, and it is the part that turns denormalisation from a liability into an engineering practice.

## 6. ASCII diagram

```
PROGRESSIVE NORMALISATION OF BOOKNOOK'S FLAT TABLE
──────────────────────────────────────────────────────────────────────

0NF  one wide table, lists inside cells
┌──────────────────────────────────────────────────────────────────┐
│ order_id │ date │ cust_name │ phones          │ isbn │ title │ … │
│   1001   │ 5/3  │ A. Okafor │ "0113…, 07700…" │ 978… │ Lamp… │ … │
└──────────────────────────────────────────────────────────────────┘
        │  1NF: atomic cells, no repeating groups
        ▼
1NF  order_lines(order_id, isbn, …everything else…)   PK(order_id, isbn)
     customer_phones(customer_id, phone)
     book_categories(isbn, category)
        │  2NF: remove PARTIAL dependencies on part of (order_id, isbn)
        ▼
2NF  orders(order_id, date, customer_id, email, name)
     books(isbn, title, author_name, author_born, pub_name, pub_city)
     order_items(order_id, isbn, unit_price, quantity)
        │  3NF: remove TRANSITIVE deps  isbn→publisher_id→city
        ▼
3NF  customers ──┐
     publishers ─┤                  ◀── THE PRACTICAL TARGET
     authors ────┤
     books ──────┤
     orders ─────┤
     order_items ┘
        │  BCNF: every determinant is a superkey (overlapping keys only)
        ▼
BCNF pickers(picker_id, warehouse_id)
     pick_assignments(picker_id, isbn)


THE SAME FACT, BEFORE AND AFTER
──────────────────────────────────────────────────────────────────────
  BEFORE (0NF)                         AFTER (3NF)
  "Harrow Press is in Leeds"           "Harrow Press is in Leeds"
   stored 412,000 times                 stored 1 time
   UPDATE: 90 s, 412k locks             UPDATE: 0.3 ms, 1 lock
   partial failure ⇒ 2 truths           partial failure ⇒ impossible


NORMALISED READ vs DENORMALISED READ (order detail, 4 line items)
──────────────────────────────────────────────────────────────────────
NORMALISED — 5 tables, 4 joins, 59 page accesses
  orders ──┬─▶ order_items ──┬─▶ books ──┬─▶ authors
           │   (4 rows)      │  (×4)     └─▶ publishers
           │                 │              (×4)
      [4 pages]         [7 pages]      [16+16+16 pages]

DENORMALISED — 1 table, 0 joins, 5 page accesses
  order_summary ─▶ [idx 3][heap 1][toast 1]
                    └── items_snapshot JSONB holds all 4 lines ──┘

  READ:  11.8× fewer pages          WRITE: every duplicated column
                                           needs a sync mechanism


THE FAN-OUT TRAP
──────────────────────────────────────────────────────────────────────
  books.title changed ONCE
        │
        ├──▶ order_summary rows containing that isbn ... 120,000 rows
        ├──▶ wishlist_cards ............................. 38,000 rows
        └──▶ search_documents ............................ 1 document
   One logical edit ⇒ 158,001 physical writes ⇒ 60 s of throttled churn
   ⇒ 60 s during which different pages show different titles.
```

## 7. Detailed example

### BookNook — the order-history page

**The situation.** BookNook's `/account/orders` page lists a customer's last 20 orders with line items, titles, cover thumbnails and current delivery status. The schema is textbook 3NF. The page issues one query joining 5 tables. Measured production latency: **p50 = 38 ms, p95 = 210 ms, p99 = 1,850 ms.** The target is p99 under 300 ms.

**Step 1 — do not denormalise yet; read the plan.** `EXPLAIN (ANALYZE, BUFFERS)` (Lesson 55) shows a hash join on `order_items` with a sequential scan, because there is no index on `order_items(order_id)` — only the primary key `(order_id, isbn)`, which *is* usable as a prefix… except the table was created with the key declared as `(isbn, order_id)`. Column order in a composite index matters (Lesson 55): a key on `(isbn, order_id)` cannot serve a lookup by `order_id` alone.

**Fix: `CREATE INDEX CONCURRENTLY ON order_items (order_id);`** Result: **p99 falls from 1,850 ms to 240 ms.** Target met. **No denormalisation required.** This is the single most common real outcome, and it is why criterion 1 in §4.8 insists on measurement first — the vast majority of "we need to denormalise" conclusions are actually missing or mis-ordered indexes.

**Step 2 — one year later, the pattern genuinely changes.** Order volume grows 12×. The same page is now p99 = 520 ms, and `EXPLAIN` shows the plan is already optimal — the cost is irreducibly 59 random page accesses across 5 tables, with buffer-pool hit rate falling because the working set no longer fits in RAM. Now the four criteria are checked:

1. **Measured**: p99 = 520 ms against a 300 ms target, from production metrics. ✔
2. **Known pattern**: this exact query is 31 % of all database time; read:write ratio for an order row is approximately **400:1** — orders are written once and read hundreds of times. ✔
3. **Acceptable staleness**: product agrees delivery status may lag by up to 2 s; prices and titles in order history must be the **historical snapshot**, which is not staleness at all. ✔
4. **Update cost**: an order is written once and updated ~4 times in its life (paid, picked, shipped, delivered). Fan-out is **1 summary row per order**. ✔

**The design.** `order_summary(order_id PK, customer_id, order_date, status, total_pence, items_snapshot jsonb)`. `items_snapshot` is an immutable purchase-time snapshot — title and price as charged — so it needs no sync. Only `status` and `total_pence` are live copies, maintained by an `AFTER UPDATE` trigger on `orders` within the same transaction. Nightly reconciliation emits a drift metric.

**Result:** p99 = 42 ms. Write path cost: +1 row write and +3.2 KB of WAL per order (§9). Accepted, documented in the schema as a comment, owned by the orders team.

### A contrasting real-world example — an airline seat map

A seat-availability display is the mirror image, and shows when denormalisation is *forbidden*.

- The read "is seat 14C free on flight BA287 on 3 June?" is high-volume and latency-sensitive — exactly the profile that tempts you to cache a denormalised `seats_available` counter.
- But criterion 3 **fails outright**: staleness is not acceptable at the moment of sale. Selling one seat twice is not a cosmetic glitch; it is a refund, a compensation payment under passenger-rights regulation, and a furious customer at a gate.
- The resolution the industry actually uses is a **split by purpose**: the browsing tier reads a deliberately stale, heavily denormalised availability cache (fast, approximate, explicitly labelled "subject to availability" in the UI), while the **commit** path goes to the authoritative normalised row and takes a real lock or uses an optimistic conditional update (Lesson 53) inside a transaction (Lesson 51).

The transferable rule: **denormalise the browse path, never the commit path.** A stale read is recoverable; a double-committed write is not. BookNook applies the identical split — the catalogue page shows an approximate `stock_count`; `POST /orders` decrements the authoritative row with a conditional update that fails if stock would go negative.

## 8. Code or configuration example

> **This SQL is illustrative teaching material, not production-ready.** It omits schema qualification, `search_path` hardening, `SECURITY DEFINER` review, permissions, migration ordering, `CONCURRENTLY` index builds under load, batching of backfills, monitoring, and rollback plans. Treat it as a readable sketch of the shape, not something to paste into a migration.

```sql
---------------------------------------------------------------------
-- PART A — the 3NF core. Every fact has exactly one home row.
---------------------------------------------------------------------

CREATE TABLE publishers (
  publisher_id  bigserial PRIMARY KEY,          -- surrogate key: stable, narrow, never reused
  name          text NOT NULL,
  city          text NOT NULL,                  -- "Harrow Press is in Leeds" lives HERE, once
  CONSTRAINT publishers_name_uq UNIQUE (name)   -- alternate key; stops duplicate publisher rows
);

CREATE TABLE authors (
  author_id     bigserial PRIMARY KEY,
  name          text NOT NULL,
  born          date                            -- nullable: unknown is a legitimate state
);

CREATE TABLE books (
  isbn          char(13) PRIMARY KEY,           -- natural key: globally assigned, genuinely immutable
  title         text NOT NULL,
  author_id     bigint NOT NULL REFERENCES authors(author_id),
  publisher_id  bigint NOT NULL REFERENCES publishers(publisher_id),
  list_price_pence int NOT NULL CHECK (list_price_pence >= 0)  -- CHECK keeps nonsense out (Lesson 43)
);

-- 1NF demands atomic cells, so the category LIST becomes a table, not 'Fiction; Historical'.
CREATE TABLE book_categories (
  isbn          char(13) NOT NULL REFERENCES books(isbn) ON DELETE CASCADE,
  category      text NOT NULL,
  PRIMARY KEY (isbn, category)                  -- composite PK also prevents duplicate pairs
);

CREATE TABLE customers (
  customer_id   bigserial PRIMARY KEY,
  email         citext NOT NULL UNIQUE,         -- UNIQUE makes email an alternate key
  name          text NOT NULL
);

CREATE TABLE orders (
  order_id      bigserial PRIMARY KEY,
  customer_id   bigint NOT NULL REFERENCES customers(customer_id),
  order_date    timestamptz NOT NULL DEFAULT now(),
  status        text NOT NULL DEFAULT 'PENDING'
                CHECK (status IN ('PENDING','PAID','PICKED','SHIPPED','DELIVERED','CANCELLED'))
);

CREATE TABLE order_items (
  order_id      bigint   NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
  isbn          char(13) NOT NULL REFERENCES books(isbn),
  quantity      int NOT NULL CHECK (quantity > 0),
  -- NOT redundancy: the price CHARGED is a historical fact, independent of books.list_price_pence.
  -- It must never be "synced" to the current list price. This comment is load-bearing.
  unit_price_pence int NOT NULL CHECK (unit_price_pence >= 0),
  PRIMARY KEY (order_id, isbn)                  -- column ORDER matters: order_id first (Lesson 55)
);

-- The index that usually removes the need to denormalise at all (see §7, Step 1).
CREATE INDEX idx_order_items_isbn ON order_items (isbn);   -- supports the reverse lookup

---------------------------------------------------------------------
-- PART B — BCNF decomposition of the warehouse pick table
---------------------------------------------------------------------

-- Before (3NF, not BCNF): picker_id -> warehouse_id, and picker_id is not a superkey.
-- CREATE TABLE pick_assignments (warehouse_id int, isbn char(13), picker_id int,
--                                PRIMARY KEY (warehouse_id, isbn));

CREATE TABLE pickers (
  picker_id     int PRIMARY KEY,
  warehouse_id  int NOT NULL                    -- the offending fact, now stored exactly once
);

CREATE TABLE pick_assignments (
  picker_id     int NOT NULL REFERENCES pickers(picker_id),
  isbn          char(13) NOT NULL REFERENCES books(isbn),
  PRIMARY KEY (picker_id, isbn)
);
-- HONEST COST: "one picker per (warehouse, isbn)" is no longer expressible as a single-table
-- constraint. This decomposition is lossless but NOT dependency-preserving, so the rule now
-- needs a trigger or application check. This is the standard reason teams stop at 3NF.

---------------------------------------------------------------------
-- PART C — denormalisation technique 1: precomputed aggregate
---------------------------------------------------------------------

ALTER TABLE books
  ADD COLUMN review_count int    NOT NULL DEFAULT 0,   -- duplicate of COUNT(*) over reviews
  ADD COLUMN rating_sum   bigint NOT NULL DEFAULT 0;   -- store SUM + COUNT, never a rounded AVG,
                                                       -- so the average stays exactly recomputable

CREATE OR REPLACE FUNCTION sync_book_rating() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    UPDATE books                                      -- SYNCHRONOUS: same transaction as the review
       SET review_count = review_count + 1,
           rating_sum   = rating_sum + NEW.stars
     WHERE isbn = NEW.isbn;                           -- single-row update, uses the PK index
  ELSIF TG_OP = 'DELETE' THEN
    UPDATE books
       SET review_count = review_count - 1,
           rating_sum   = rating_sum - OLD.stars
     WHERE isbn = OLD.isbn;
  END IF;
  RETURN NULL;                                        -- AFTER triggers ignore the return value
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_sync_book_rating
  AFTER INSERT OR DELETE ON reviews
  FOR EACH ROW EXECUTE FUNCTION sync_book_rating();
-- CONTENTION WARNING: every review of a bestseller updates the SAME books row, so concurrent
-- reviewers serialise on that row lock (Lesson 53). Fine at 5 reviews/sec on one title;
-- a throughput wall at 500/sec. The standard remedy is sharded counters: N counter rows per
-- book, written at random and SUMmed at read time.

---------------------------------------------------------------------
-- PART D — denormalisation technique 2: summary table + scheduled recompute
---------------------------------------------------------------------

CREATE TABLE daily_sales_by_category (
  day       date NOT NULL,
  category  text NOT NULL,
  units     bigint NOT NULL,
  revenue_pence bigint NOT NULL,
  computed_at timestamptz NOT NULL DEFAULT now(),  -- lets a reader SEE the staleness, not guess it
  PRIMARY KEY (day, category)
);

-- Idempotent recompute for ONE day. Idempotency is the property that makes a retry safe,
-- and it is why scheduled recomputation is self-healing where delta updates are not.
INSERT INTO daily_sales_by_category (day, category, units, revenue_pence)
SELECT o.order_date::date,
       bc.category,
       SUM(oi.quantity),
       SUM(oi.quantity * oi.unit_price_pence)
  FROM orders o
  JOIN order_items    oi ON oi.order_id = o.order_id
  JOIN book_categories bc ON bc.isbn    = oi.isbn
 WHERE o.order_date >= date '2026-10-08'
   AND o.order_date <  date '2026-10-09'          -- half-open range: no double-counting at midnight
   AND o.status <> 'CANCELLED'                    -- business rule stated once, in one place
 GROUP BY 1, 2
    ON CONFLICT (day, category) DO UPDATE          -- UPSERT ⇒ rerunning the job is harmless
       SET units = EXCLUDED.units,
           revenue_pence = EXCLUDED.revenue_pence,
           computed_at = now();

---------------------------------------------------------------------
-- PART E — technique 3: materialised view (duplication the SCHEMA documents)
---------------------------------------------------------------------

CREATE MATERIALIZED VIEW mv_bestsellers_30d AS
SELECT oi.isbn, SUM(oi.quantity) AS units
  FROM order_items oi
  JOIN orders o ON o.order_id = oi.order_id
 WHERE o.order_date > now() - interval '30 days'
 GROUP BY oi.isbn;

CREATE UNIQUE INDEX ON mv_bestsellers_30d (isbn);  -- REQUIRED for REFRESH ... CONCURRENTLY
-- REFRESH MATERIALIZED VIEW CONCURRENTLY mv_bestsellers_30d;
--   CONCURRENTLY keeps readers unblocked but is SLOWER and needs the unique index above.
--   PostgreSQL refreshes ON COMMAND ONLY: this view is stale between refreshes, by design.
--   Other engines differ — SQL Server indexed views are maintained synchronously on write.

---------------------------------------------------------------------
-- PART F — technique 4: JSONB snapshot (the read-path denormalisation of §7)
---------------------------------------------------------------------

CREATE TABLE order_summary (
  order_id     bigint PRIMARY KEY REFERENCES orders(order_id) ON DELETE CASCADE,
  customer_id  bigint NOT NULL,
  order_date   timestamptz NOT NULL,
  status       text NOT NULL,                    -- LIVE copy of orders.status ⇒ must be synced
  total_pence  bigint NOT NULL,                  -- LIVE derived value      ⇒ must be synced
  items_snapshot jsonb NOT NULL                  -- IMMUTABLE snapshot      ⇒ must NOT be synced
);

CREATE INDEX idx_order_summary_cust ON order_summary (customer_id, order_date DESC);
-- Supports "last 20 orders for this customer" as an index range scan in date order.

CREATE OR REPLACE FUNCTION sync_order_summary_status() RETURNS trigger AS $$
BEGIN
  UPDATE order_summary
     SET status = NEW.status                     -- same transaction ⇒ the copies cannot diverge
   WHERE order_id = NEW.order_id;
  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_sync_order_summary_status
  AFTER UPDATE OF status ON orders               -- OF status: do not fire on unrelated updates
  FOR EACH ROW WHEN (OLD.status IS DISTINCT FROM NEW.status)  -- skip no-op writes
  EXECUTE FUNCTION sync_order_summary_status();

---------------------------------------------------------------------
-- PART G — the drift detector. Without this, denormalisation is a bug with a timer.
---------------------------------------------------------------------

SELECT count(*) AS summary_drift_rows            -- export as a metric; alert when > 0
  FROM orders o
  JOIN order_summary s USING (order_id)
 WHERE o.status <> s.status                      -- any disagreement means the trigger failed
   AND o.order_date > now() - interval '48 hours';
```

## 9. Numerical example

**Goal:** compare the 5-table normalised join against the denormalised `order_summary` read, then price the write cost honestly.

### Assumptions (all stated; all typical, not measured from any one system)

| Assumption | Value | Why this value |
|---|---|---|
| Page size | 8 KB | PostgreSQL default |
| Buffer-pool page access (RAM hit) | 1 µs | Typical for a hot page, including latch cost |
| NVMe random page read (miss) | 100 µs | Typical modern NVMe random 8 KB read |
| Buffer-pool hit rate, p50 conditions | 95 % | Typical for a well-sized OLTP pool |
| Buffer-pool hit rate, p99 conditions | 70 % | Cold orders, evicted pages |
| B-tree height for every table here | 3 levels | Typical for 10⁶–10⁸ rows |
| Executor overhead, 5-node join plan | 0.25 ms | Node setup, hashing, row assembly |
| Executor overhead, single-row fetch | 0.03 ms | One index-scan node |
| Plan lookup (prepared statement) | 0.05 ms | Parse/plan cached |
| App↔DB network round trip | 0.30 ms | Same availability zone |
| Order size | 4 line items | BookNook's measured mean is 3.8 |
| `items_snapshot` JSONB size | 3 KB | 4 items × ~750 B, TOAST-stored |

### Step 1 — page accesses for the normalised 5-table join

| Access | Index pages | Heap pages | Subtotal |
|---|---|---|---|
| `orders` by PK | 3 | 1 | 4 |
| `order_items` by `order_id` (4 matching rows) | 3 | 4 | 7 |
| `books` by PK, ×4 | 3×4 = 12 | 1×4 = 4 | 16 |
| `authors` by PK, ×4 | 12 | 4 | 16 |
| `publishers` by PK, ×4 | 12 | 4 | 16 |
| **Total** | 42 | 17 | **59 pages** |

### Step 2 — expected time per page access

- p50: `0.95 × 1 µs + 0.05 × 100 µs = 0.95 + 5.00 = 5.95 µs`
- p99: `0.70 × 1 µs + 0.30 × 100 µs = 0.70 + 30.00 = 30.70 µs`

### Step 3 — normalised query time

- p50 I/O: `59 × 5.95 µs = 351 µs = 0.351 ms`
- p50 total server: `0.351 + 0.25 (executor) + 0.05 (plan) = 0.651 ms`
- p50 end-to-end: `0.651 + 0.30 (network) = 0.951 ms`
- p99 I/O: `59 × 30.70 µs = 1,811 µs = 1.811 ms`
- p99 total server: `1.811 + 0.25 + 0.05 = 2.111 ms`
- p99 end-to-end: `2.111 + 0.30 = 2.411 ms`

### Step 4 — denormalised read

Page accesses: 3 index + 1 heap + 1 TOAST = **5 pages.**

- p50 I/O: `5 × 5.95 µs = 29.75 µs = 0.030 ms`
- p50 server: `0.030 + 0.03 + 0.05 = 0.110 ms`; end-to-end `0.410 ms`
- p99 I/O: `5 × 30.70 µs = 153.5 µs = 0.154 ms`
- p99 server: `0.154 + 0.03 + 0.05 = 0.234 ms`; end-to-end `0.534 ms`

### Step 5 — the improvement, stated two ways

| Metric | Normalised | Denormalised | Speed-up |
|---|---|---|---|
| Page accesses | 59 | 5 | **11.8×** |
| Server time, p50 | 0.651 ms | 0.110 ms | **5.9×** |
| Server time, p99 | 2.111 ms | 0.234 ms | **9.0×** |
| End-to-end, p50 | 0.951 ms | 0.410 ms | **2.3×** |
| End-to-end, p99 | 2.411 ms | 0.534 ms | **4.5×** |

**Read the last two rows carefully.** The database got 9× faster; the *user-visible* p99 improved 4.5×, because the fixed 0.30 ms network round trip does not shrink. If your network RTT were 3 ms instead of 0.3 ms, the end-to-end gain would collapse to `5.11 / 3.23 = 1.6×` — and the whole exercise would not be worth the synchronisation debt. **The value of denormalisation depends on the ratio of database time to total time; measure total time before you start.**

### Step 6 — write amplification per order

Normalised insert of one order (1 `orders` row + 4 `order_items` rows), assuming 1 heap page + 2 index leaf pages dirtied per row:

- Page writes: `5 rows × 3 = 15 pages`
- WAL: `5 rows × ~200 B = ~1.0 KB` (ignoring full-page images)

Adding the `order_summary` row (3 KB JSONB → 1 heap + 1 TOAST + 2 index pages):

- Extra page writes: `4` → total `19`. Amplification = `19 / 15 = 1.27×`
- Extra WAL: `~3.2 KB` → total `~4.2 KB`. Amplification = `4.2 / 1.0 = 4.2×`

At BookNook's 50 orders/sec: extra WAL = `50 × 3.2 KB = 160 KB/s`, extra page writes = `200/s`. Against an NVMe budget of roughly 60,000 random IOPS, that is **0.33 % of write capacity.** Verdict: comfortably affordable.

### Step 7 — the cost that actually bites: fan-out on a shared attribute

Now suppose BookNook had *also* copied `books.title` into `order_summary.items_snapshot` **as a live column** (rather than an immutable snapshot) and a publisher corrects a title.

- Orders containing that bestseller: **120,000**
- Each update rewrites the whole 3 KB JSONB row (PostgreSQL MVCC writes a new row version — Lesson 53): `120,000 × 3 KB = 360 MB` of new row versions
- WAL at roughly 2× row bytes: `~720 MB`
- Plus dead-tuple cleanup: vacuum must later reclaim 360 MB
- Throttled to 2,000 updates/sec to avoid disturbing OLTP: `120,000 / 2,000 = 60 seconds`

**Therefore: one logical edit costs 1.08 GB of I/O and produces a 60-second window in which different BookNook pages display different titles for the same book.** Compare to the normalised cost: **one row, 0.3 ms, zero inconsistency window.**

This is the entire argument in one comparison. The read win is real (9×). The write cost is real too, and it is **not proportional to the edit — it is proportional to the fan-out.** That is why §4.8 criterion 4 is non-negotiable, and why BookNook keeps `items_snapshot` immutable: a snapshot has a fan-out of zero.

### Step 8 — the aggregate case, where the gap is far larger

"Top 10 bestsellers, last 30 days", at 88 M `order_items` rows over 3 years, ~2.4 M in a 30-day window:

- **Normalised**: scan 2.4 M rows × 60 B = **144 MB**, hash-aggregate ~400,000 distinct ISBNs. At 1.2 GB/s effective scan rate: `144 MB / 1,200 MB/s = 0.12 s` of pure I/O, plus aggregation CPU ≈ **0.4 s total**, repeated for every homepage view.
- **Denormalised** (materialised view, 400,000 rows, or a top-10 summary row): **10 rows, ~0.05 ms.**
- **Speed-up: ~8,000×.**

Aggregates are where denormalisation pays overwhelmingly, because the normalised cost scales with *rows scanned* while the denormalised cost scales with *rows returned*. Joins typically buy you single-digit multiples; aggregates buy you three or four orders of magnitude. **If you are going to denormalise one thing, denormalise the aggregate.**

## 10. Trade-offs

| Dimension | Normalised (3NF) | Denormalised |
|---|---|---|
| **Correctness** | Enforced structurally — one fact, one row, so divergence is *impossible* | Enforced by a mechanism you built. Divergence is possible and will eventually occur |
| **Read latency, point lookup** | 59 page accesses in the worked example | 5. Typically 2–12× faster server-side |
| **Read latency, aggregate** | Scales with rows scanned | Scales with rows returned. 10³–10⁴× faster |
| **Write latency** | Minimal: one small row per fact | Higher: every copy written, synchronously or asynchronously |
| **Write amplification** | ~1× | 1.3× here; unbounded if fan-out is large |
| **Storage** | Minimal | Duplicated bytes, plus extra indexes on the copies |
| **Schema flexibility** | High — a new query is a new join, no migration | Low — a new query shape may need a *new* denormalised structure and a backfill |
| **Query complexity** | Joins in every query; more SQL to write and read | Simple reads, complex *writes*. The complexity moves, it does not vanish |
| **Operational burden** | Constraints do the work | Triggers/CDC/jobs to own, monitor, alert on, and reconcile |
| **Failure blast radius** | A bug corrupts one row | A bug corrupts N copies, silently, and is discovered weeks later |
| **Onboarding cost** | Any engineer who knows SQL can navigate it | Requires tribal knowledge of which column is authoritative |
| **Backfill cost** | n/a | Every new denormalised column needs a batched backfill over the whole table |

**The asymmetry worth internalising:** normalisation's costs are *paid continuously and visibly* (every query writes joins). Denormalisation's costs are *deferred and invisible* (the drift you find in six months). Humans systematically under-price deferred, invisible costs. Correct for that bias deliberately.

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| **Trigger disabled during a bulk load and never re-enabled** | Order status shows "Pending" on the history page while the tracking email says "Delivered". Support tickets, trust damage | The §8 Part G drift query returns non-zero; alert fires | Re-enable the trigger, then run the reconciliation job over the affected window. This is why reconciliation must be able to rebuild from source, not just apply deltas |
| **Partial `UPDATE` on an un-normalised table (the 412,000-row case)** | Different pages show different publisher cities; the "publishers by city" report double-counts | `SELECT DISTINCT` over the duplicated column returns >1 value per entity — a check nobody writes until after the incident | Re-run the update to completion inside a transaction, then normalise the column out so the class of failure disappears permanently |
| **Counter drift from a lost trigger execution or an aborted subtransaction** | Product page shows "1,284 reviews" but the list renders 1,281. Looks like a UI bug; wastes a day of front-end debugging | Nightly job compares `books.review_count` against `COUNT(*)` from `reviews` and emits a diff metric | Recompute the counter from source. Prefer recomputation over "adjust by the delta" — the delta assumes you know the true drift, and you do not |
| **Hot-row lock contention on an aggregate counter** | Checkout latency spikes and some writes time out, but only for bestsellers. p99 doubles with no deploy and no traffic change | `pg_stat_activity` shows many sessions waiting on the same tuple lock; deadlock count rises | Short term: retry with backoff. Proper fix: shard the counter into N rows per book, sum at read time, or move the increment off the synchronous write path |
| **Materialised view refresh overruns its window** | Dashboard shows yesterday's figures; a merchandiser prices a promotion from stale data | `computed_at` is older than the SLA; alert on `now() - computed_at` | Switch to incremental refresh for recent partitions only, or move the aggregation to a warehouse (Lesson 58) |
| **Backfill of a new denormalised column saturates I/O** | Site-wide latency spike during the migration; checkout timeouts | Disk utilisation at 100 %, replication lag climbing (Lesson 35) | Abort; redo in batches of 5,000 rows with a sleep between batches and a lag check that pauses when replicas fall behind |
| **Dual write to a second datastore crashes between the two writes** | A book appears in the catalogue but is unfindable in search, permanently | Periodic count/checksum comparison between the two stores | Reconcile from the authoritative store. Structurally: replace dual writes with CDC (**Phase 4, Topic 22 — Change data capture and event sourcing**) so there is exactly one write and the copy is derived |
| **Over-normalisation: an 11-table join on the hot path** | Page is uniformly slow; p50 and p99 both bad, with no obvious culprit | `EXPLAIN ANALYZE` shows the planner picking a bad join order because estimates compound across 11 relations (Lesson 55) | Add the missing index first; if the plan is genuinely optimal, denormalise *that one path* using the §4.8 criteria |

## 12. Alternatives

| Approach | What it is | Latency gain | Staleness | Correctness risk | Operational cost | Best when |
|---|---|---|---|---|---|---|
| **Better indexing** (Lessons 54–55) | Covering/composite index so the join is an index-only scan | 2–50× | **None** | **None** | Low — one DDL statement | **Always try this first.** It is free correctness |
| **Query rewrite** | Replace `IN (subquery)` with a join, push predicates down, remove `SELECT *` | 2–20× | None | None | Low | The plan shows an avoidable node |
| **Application-side cache** (Lessons 30–31) | Redis holds the assembled object | 10–100× | TTL-bounded | Low — the database stays canonical | Medium: invalidation, stampede control | Read-heavy, tolerant of seconds of staleness |
| **Read replica** (Lesson 35) | Route reads to a follower | **0× on latency** — adds lag | Replication lag | Low | Medium | You need read *throughput*, not lower latency |
| **Materialised view** | DB-maintained summary | 10³–10⁴× on aggregates | Until next refresh | Low — definition is in the schema | Medium: refresh scheduling | Aggregates with a tolerable refresh interval |
| **Denormalised column/table** | Hand-maintained copy | 2–10× on joins | Depends on mechanism | **Medium–high** | High: sync + reconcile + backfill | All four §4.8 criteria hold |
| **Document model** (Lesson 46) | Store the aggregate as one document | 5–20× | None within the document | Medium: duplication across documents | Medium | The access pattern is overwhelmingly "fetch one whole aggregate" |
| **Separate analytical store** (Lesson 57, Lesson 58) | Copy to a columnar warehouse via ETL/ELT | 10–100× on analytics | Minutes to hours | Low — one-way derivation | High: a pipeline to own | The slow queries are **analytical**, not transactional |

**The decision rule, in order:** index → rewrite → cache → materialised view → denormalise → separate store. Each step up costs more correctness and more operations. Stop at the first one that meets the measured target.

## 13. When to use it

**Use normalisation (3NF) when:**

1. **The system is transactional and write-bearing** — orders, payments, inventory, user accounts. Writes must be small, atomic and unambiguous.
2. **The data has shared, mutable, cross-cutting attributes** — publisher city, author name, tax rate, currency conversion. Anything one edit should change everywhere is a normalised table by definition.
3. **Access patterns are not yet known.** A normalised schema answers queries you have not thought of yet with a new join; a denormalised one answers only the question it was built for.
4. **Correctness is regulated or audited** — financial records, medical data, anything where two systems disagreeing is a reportable incident.
5. **The team is growing.** Structural constraints outlive the engineers who remember the conventions.

**Use denormalisation when all four §4.8 criteria hold, specifically:**

1. **An aggregate is recomputed on a hot path.** Review counts, follower counts, cart totals, bestseller ranks. The read:write ratio is enormous and the gain is 10³–10⁴× (§9 Step 8). This is the clearest-cut case in the lesson.
2. **An immutable historical snapshot is required.** `order_items.unit_price_pence`, `items_snapshot`. The copy *should not* track the source, so there is literally no synchronisation cost — the strongest form of denormalisation because it has no downside.
3. **A measured, stable, high-volume read path remains slow after indexing.** BookNook's order-history page at 12× growth (§7 Step 2).
4. **A fan-out-light, high-selectivity filter column.** Copying `orders.customer_id` onto `order_items` so "all items a customer ever bought" avoids a join. `customer_id` never changes for an existing order, so the copy is write-once.
5. **Analytical/reporting tables**, where the workload is scan-and-aggregate rather than transactional — but see Lesson 57 for why those usually belong in a different system entirely.

## 14. When not to use it

**Do not denormalise when:**

1. **You have not measured.** Without a production p95/p99 and a target, you are guessing — and §7 Step 1 shows that the guess is usually wrong, because the real cause is an index.
2. **The source attribute changes often and has high fan-out.** The §9 Step 7 case: 120,000 rewrites and a 60-second inconsistency window per title edit. The fan-out, not the edit, sets the cost.
3. **Staleness is unacceptable at the point of decision.** Seat availability at commit, account balance at withdrawal, stock level at checkout. Denormalise the browse path, never the commit path (§7).
4. **You cannot name the synchronisation mechanism, its owner and its staleness bound.** If all three are not written down, you are shipping a latent data-corruption bug.
5. **The access pattern is still changing.** Denormalisation freezes one query shape into physical structure. Doing it during product discovery means rebuilding it every sprint.
6. **The real problem is analytical.** A slow `GROUP BY` over three years of history is not a normalisation problem; it is a workload-separation problem (Lesson 57).

**Do not over-normalise when:**

1. **A "lookup table" has one column and no behaviour.** `countries(country_id, name)` referenced everywhere adds a join to every query to save bytes that a CHECK constraint or enum would handle. Normalise facts, not labels.
2. **BCNF costs you a dependency-preserving constraint.** If reaching BCNF means moving a rule from a `UNIQUE` constraint into application code (§4.6), staying at 3NF with a small, documented redundancy is usually the better engineering trade.

## 15. Common mistakes

### Beginner mistakes

1. **Storing lists in a single column.** `'Fiction; Historical'` or `'0113-555-0191, 07700-900123'`. Unindexable, unjoinable, and `LIKE '%…%'` matches substrings of other values. This is the 1NF violation and it is the most common one in real codebases.
2. **Treating normal forms as a score.** "We're 5NF compliant" is not an achievement; a schema that answers queries correctly and fast is. 3NF is the target, not the floor of a climb.
3. **Denormalising before indexing.** Skipping straight to duplication when `CREATE INDEX` would have delivered the same result with zero correctness risk (§7 Step 1).
4. **Storing a computed average instead of sum and count.** `avg_rating numeric` cannot be updated incrementally without the count, and rounding error accumulates. Store `rating_sum` and `review_count`; divide at read time.
5. **Using a mutable natural key as the primary key.** Email addresses change; so do usernames and SKUs. Every foreign key then has to cascade. Use a surrogate key and a `UNIQUE` constraint on the natural one. (ISBN is a fair exception: it is externally assigned and genuinely immutable.)
6. **Adding a denormalised column with no backfill.** New rows get the value, the 40 M existing rows have `NULL`, and queries silently return partial results.
7. **Believing "NoSQL means denormalised, so schema design doesn't matter."** Document stores (Lesson 46) have *exactly* the same anomalies; they simply give you no constraints to prevent them, so the design discipline matters more, not less.

### Experienced-engineer mistakes

1. **Denormalising the commit path.** Caching stock levels for display is correct; using the cached value to decide whether a sale succeeds is how you oversell. The browse/commit split (§7) is the guard.
2. **Dual writes without atomicity.** Writing the source and the copy to two systems with no transaction. There is no retry strategy that makes this correct — only CDC or an outbox pattern does (**Phase 4, Topic 22 — Change data capture and event sourcing**).
3. **Building incremental sync with no reconciliation.** Deltas drift. Without a periodic recompute-from-source and a drift metric, you learn about divergence from a customer, months later.
4. **Ignoring lock contention on aggregate counters.** A trigger that updates a single `books` row per review works beautifully at 5 writes/sec and becomes a throughput ceiling at 500/sec (§8 Part C). Load-test the *write* path of any denormalisation, not just the read.
5. **Denormalising for a problem that is actually analytical.** Bolting summary tables onto the OLTP database because the quarterly report is slow. The report belongs in a warehouse; see Lesson 57 for the first-principles reason.
6. **Forgetting MVCC row-rewrite cost.** In PostgreSQL, updating one field of a 3 KB JSONB row rewrites the **entire row** and every non-HOT index entry. Narrow, frequently-updated columns must live in narrow tables.
7. **Confusing a snapshot with redundancy.** "Deduplicating" `order_items.unit_price_pence` against `books.list_price_pence` destroys the historical record and silently rewrites past invoices. The code comment in §8 that marks the distinction is load-bearing.
8. **Letting normal-form purity dictate a hot path.** The reverse error of #5: an 11-table join on a checkout path, defended on principle, where the planner's estimates compound into a bad join order (Lesson 55).

## 16. Interview perspective

**What is actually being tested.** Not whether you can recite "a table is in 2NF if…". Interviewers are checking three things: that you can *derive* a schema from stated business rules; that you treat denormalisation as a **measured trade with a stated cost**, not a reflex; and that you know **who keeps the copies in sync**.

**Answers that land well:**

- *"What normal form should I target?"* — "3NF for transactional tables, because it removes insert, update and delete anomalies while remaining both lossless and dependency-preserving. I check BCNF specifically when a table has overlapping candidate keys, and I'd accept staying at 3NF if BCNF forced a constraint out of the database and into application code."
- *"This query is slow, would you denormalise?"* — "Not yet. First `EXPLAIN ANALYZE` to see if the plan is optimal; the usual cause is a missing or mis-ordered composite index. If the plan is already optimal and p99 still misses target, I'd denormalise the narrowest thing that fixes it, state the staleness bound, pick a sync mechanism, and add a drift metric."
- *"How do you keep a denormalised copy correct?"* — "Trigger for synchronous correctness where fan-out is small and contention is low; CDC where the copy lives in another system; scheduled recomputation as the self-healing backstop in every case, with the drift count exported as a metric."
- Volunteering the **fan-out** question — "how many copies does one source edit touch?" — immediately signals production experience, because it is the number that decides whether the design is safe.

**Common interview tasks:** normalise a flat spreadsheet to 3NF out loud; identify which anomaly a given table suffers; design a schema for a bookstore/ride-share/ticketing system; explain why a counter column needs sharding at high write rates; explain the difference between a snapshot and a duplicate.

**Red flags interviewers watch for:**

- Reciting normal-form definitions but being unable to apply them to a concrete table.
- Saying "normalise everything" or "denormalise for performance" with no measurement, no criteria and no cost.
- Proposing a denormalised column and never mentioning how it is kept in sync.
- Not knowing that `UPDATE` on a duplicated column is a multi-row, long-running, partially-failable operation.
- Confusing denormalisation with caching — they have different consistency models, different failure modes and different owners.
- Answering "it depends" without finishing the sentence. It depends on **read:write ratio, fan-out, acceptable staleness, and measured latency against a target** — say those four or say nothing.
- Treating a historical snapshot as redundancy to be eliminated.
- Designing a schema with no primary keys, no foreign keys, and no `NOT NULL` — then calling it "flexible".

## 17. Knowledge check

**Conceptual**

1. A table in 3NF can still have redundancy. Describe precisely the structural condition under which this happens, explain which normal form removes it, and state the specific cost you may have to pay to get there.
2. Explain why the insert and delete anomalies are *different* problems rather than two descriptions of the same problem, using a BookNook example for each that the other anomaly does not cover.
3. `order_items.unit_price_pence` duplicates a value that also exists in `books.list_price_pence`. Explain why this is **not** a normalisation violation, and state the general rule that distinguishes a snapshot from a redundant copy.

**Practical scenario**

4. BookNook's homepage shows "Top 10 bestsellers this week". It currently aggregates 2.4 M `order_items` rows on every page load and is the single largest consumer of database CPU. Product says the list may be up to 15 minutes stale. Design the fix end to end: the structure you would add, the synchronisation mechanism and why you chose it over the alternatives, the staleness bound you would guarantee, how you would detect drift, and what you would do if the bestseller list must later become personalised per customer.

**Calculation**

5. BookNook denormalises `publisher_name` into `books`. There are 2,000,000 book rows, 14,000 publishers, and book rows average 600 bytes. Publishers rename on average 3 times per year each. Assume a renamed publisher has the mean number of titles, each row update rewrites the full row plus 2 index entries (~200 bytes), WAL is 2× row bytes, and the maintenance job is throttled to 1,500 updates/sec. Calculate: (a) the mean number of rows touched per rename; (b) the total bytes of row rewrites and WAL generated per rename; (c) the wall-clock duration of the inconsistency window per rename; (d) the total annual write volume caused purely by this denormalisation; and (e) state the one additional measurement you would need before deciding whether this trade is acceptable.

**Progress checklist**

- Phase 1 — Foundations (Lessons 1–17): **complete** ✅
- Phase 2 — Scaling (Lessons 18–39): **complete** ✅
- Phase 3 — Data and Storage:
  - [x] Topic 1 — Relational databases and the relational model (L40)
  - [x] Topic 2 — Tables, keys and relationships (L41)
  - [x] Topic 3 — Joins (L42)
  - [x] Topic 4 — Constraints and referential integrity (L43)
  - [x] Topic 5 — SQL versus NoSQL (L44)
  - [x] Topic 6 — Key-value stores (L45)
  - [x] Topic 7 — Document stores (L46)
  - [x] Topic 8 — Wide-column stores (L47)
  - [x] Topic 9 — Graph databases (L48)
  - [x] Topic 10 — Search engines and inverted indexes (L49)
  - [x] Topic 11 — Choosing a database (L50)
  - [x] Topic 12 — ACID transactions (L51)
  - [x] Topic 13 — Isolation levels and anomalies (L52)
  - [x] Topic 14 — Concurrency control: locking and MVCC (L53)
  - [x] Topic 15 — Indexes and how they work (L54)
  - [x] Topic 16 — Index types: B-tree, hash, LSM and specialised (L55)
  - [x] **Topic 17 — Database normalisation and denormalisation (L56) ← you are here**
  - [ ] Topic 18 — OLTP versus OLAP (L57)
  - [ ] Topic 19 — Data warehouses, data lakes and lakehouses (L58)
  - [ ] Topic 20 — Partitioning and sharding strategies
  - [ ] Topic 21 — Multi-tenancy and data isolation
  - [ ] Topic 22 — Connection pooling and database scaling limits
  - [ ] Topic 23 — Schema migrations and zero-downtime changes
  - [ ] Topic 24 — Backup, restore and point-in-time recovery

**NEXT LESSON: Phase 3, Topic 18 — OLTP versus OLAP**

---

# Lesson 57 — OLTP versus OLAP

*Phase 3, Topic 18*

## 1. Simple definition

There are two fundamentally different kinds of database workload, and almost every serious data-architecture decision follows from telling them apart.

- **OLTP — Online Transaction Processing.** **Many, small, short, write-heavy, point-access transactions, serving users.** "Fetch order 12345." "Insert a new order." "Decrement stock for ISBN 978…". Thousands per second, each touching a handful of rows, each expected to finish in single-digit milliseconds.
- **OLAP — Online Analytical Processing.** **Few, large, long-running, read-heavy, scan-and-aggregate queries, serving analysts.** "Revenue by category by month for the last three years." A handful per minute, each touching hundreds of millions of rows, each acceptable at seconds or even minutes.

Terms defined before use:

- **Point access**: retrieving a specific row by key. The opposite is a **scan**: reading many rows to compute something over them.
- **Transaction**: a unit of work that is atomic, consistent, isolated and durable (Lesson 51). In OLTP, essentially every statement is in one.
- **Working set**: the subset of data actively being accessed, which you hope fits in RAM.
- **Buffer pool** (also **page cache**, **shared buffers**): the database's in-memory cache of disk pages (Lesson 54). Its hit rate is the single biggest determinant of OLTP latency.
- **Cardinality**: the number of distinct values in a column. **Selectivity**: the fraction of rows a predicate keeps.
- **Fact table**: in an analytical schema, the large table of events (one row per order line). **Dimension table**: the small descriptive tables it references (book, customer, date).

The sentence to carry out of this lesson: **OLTP and OLAP want opposite things from every layer of the stack — storage layout, indexing, schema design, concurrency, caching and hardware — so running both on one system means both are wrong.**

## 2. Why it exists

BookNook's production PostgreSQL serves checkout. It is tuned, indexed, and comfortably handles 2,000 queries/sec at p99 = 4 ms. Then the finance team asks for a revenue report, and someone — reasonably, helpfully — runs it against production at 09:30 on a Monday.

Forty seconds later the site is effectively down. Checkout times out. The on-call engineer sees no deploy, no traffic spike, no failed host. One `SELECT` did this.

This happens because the two workloads make **incompatible demands on shared, finite resources**:

| Resource | What OLTP needs | What OLAP does to it |
|---|---|---|
| **Buffer pool** | Keep the hot 12 GB working set resident so hit rate stays ~99 % | Streams 16 GB of cold historical pages through it, evicting the hot set. Hit rate collapses |
| **Disk IOPS** | ~70,000 random reads/sec available for point lookups | Consumes the entire sequential bandwidth budget; random reads queue behind it |
| **CPU** | Short bursts; many cores mostly idle, low per-query cost | One query parallelised across 8 workers pins most of the box for 40 s |
| **MVCC / vacuum** | Old row versions cleaned up promptly (Lesson 53) | A 40-minute read transaction holds back the vacuum horizon, so dead tuples accumulate table-wide and *every* query gets slower |
| **Locks** | Brief row locks, released in milliseconds | Long-lived shared locks that block DDL and can block maintenance |
| **Replication** | Replicas within a second of the primary (Lesson 35) | Long queries on a replica force either query cancellation or unbounded lag |

Note the fourth row especially: a long analytical read on PostgreSQL pins the oldest transaction snapshot, which prevents vacuum from reclaiming *any* row version newer than it — **across the whole database**. The damage outlives the query.

So the separation exists because **no single tuning of one system can satisfy both profiles simultaneously**. It is not a matter of buying a bigger box; the two workloads want structurally different machines.

## 3. Real-world analogy

**A bank branch counter versus the bank's back-office audit team.**

The **counter** (OLTP): a queue of customers, each needing one specific thing — "pay in £200 to account 4471". Each interaction must take under two minutes or the queue backs out of the door. The teller needs instant access to *one* account record. Thirty tellers work in parallel on thirty different customers. Every action must be exactly right and permanently recorded.

The **audit team** (OLAP): three analysts in a back room asking "what was the average balance of accounts opened in Leeds in 2019, by quarter?" They need to look at *millions* of records but only two or three fields of each. They do not need today's transactions from the last five minutes. Taking a whole afternoon is fine.

Now imagine the bank had no back office, so the auditors walked to the counter and asked a teller to fetch every ledger book from 2019. **The counter stops. Every customer in the queue waits.** The auditors are not doing anything wrong — they are using the wrong facility for their task.

The industry's answer is the obvious one: take a copy of the ledgers to the back room overnight, and let the auditors work there.

**Where the analogy breaks down:**

| Bank branch | Database systems |
|---|---|
| The teller visibly stops serving; everyone understands the cause | The database keeps accepting queries and just gets slower. Cause and effect are separated by minutes and look like "random" latency |
| Fetching ledgers affects only the fetching teller | Buffer-pool eviction degrades **every** query on **every** connection — the blast radius is global, not local |
| Photocopied ledgers in the back room are byte-identical to the originals | The analytical copy is usually *restructured*: columnar layout, denormalised star schema, different types, pre-aggregated. It is derived, not duplicated |
| The back room reads yesterday's copy and nobody minds | Freshness expectations in analytics have risen; "yesterday" is often no longer acceptable, which is what drives streaming ingestion and HTAP claims |
| An auditor can stop and come back later | A 40-minute query cancelled at minute 39 returns nothing and has consumed 39 minutes of resource for zero value |
| Ledger books are ledger books | Row-oriented and column-oriented storage are *physically different byte layouts*. This is the deepest difference and has no analogue in paper records |

That last row is the technical heart of the lesson, and §4.3 builds it from first principles.

## 4. Detailed technical explanation

### 4.1 The definitive comparison

| Dimension | OLTP | OLAP |
|---|---|---|
| **Query shape** | Point lookup or small range by key; `SELECT … WHERE pk = ?`; `INSERT`; `UPDATE` of 1 row | Full or partitioned scan with `GROUP BY`, window functions, multi-way joins to dimensions |
| **Rows touched per query** | 1–100 | 10⁶–10¹¹ |
| **Columns touched per query** | Most or all columns of a few rows | A few columns (typically 3–10) of a very large number of rows |
| **Data volume touched** | Kilobytes | Gigabytes to terabytes |
| **Latency requirement** | p99 **< 10 ms**; a user is waiting on a page render | Seconds to minutes; an analyst is waiting on a dashboard, and interactivity means < 5 s, not < 5 ms |
| **Throughput** | 10³–10⁶ queries/sec | 10⁻¹–10² queries/sec |
| **Concurrency** | Thousands of concurrent short transactions | Tens of concurrent long queries; each may internally use many cores |
| **Read/write mix** | Write-heavy relative to data size; typically 20–50 % writes | Overwhelmingly read; writes arrive as bulk appends, rarely as single-row updates |
| **Freshness requirement** | **Absolute.** Must read the committed truth as of now | Tolerant. Minutes-to-hours lag is normal and usually acceptable; "as of last night" is a legitimate answer |
| **Schema design** | Normalised to 3NF (Lesson 56); many narrow tables; heavy foreign keys | Denormalised **star** or **snowflake** schema; one wide fact table plus dimensions; few or no foreign keys |
| **Indexing** | Many selective B-tree indexes (Lesson 55); index maintenance cost accepted | Few or no row indexes; instead zone maps / min-max statistics, partition pruning, sort keys |
| **Storage layout** | **Row-oriented** | **Column-oriented** |
| **Typical isolation** | Read Committed or Repeatable Read, per statement (Lesson 52) | Snapshot of a consistent point; often no transactions at all |
| **Durability** | Every commit fsynced to WAL | Bulk-loaded, often re-derivable from the source, so durability is cheaper |
| **Example systems** | PostgreSQL, MySQL, SQL Server, Oracle, CockroachDB, DynamoDB | ClickHouse, Snowflake, BigQuery, Redshift, Databricks, DuckDB, Apache Druid |
| **Who is waiting** | A customer, in a browser | An analyst, in a dashboard |

### 4.2 Why analytics on production is a classic and serious mistake

The mechanism is not "big query = slow". It is **resource stealing with a global blast radius**, and it has four distinct channels:

1. **Buffer-pool eviction.** The pool is a fixed-size LRU-ish cache. A 16 GB scan pushes 16 GB of cold pages through a 32 GB pool and evicts the hot 12 GB working set. OLTP hit rate falls from 99 % to ~40 %. Each point lookup's cost per page rises from ~2 µs to ~60 µs — a **30× increase** — and this applies to *every query from every user*, not just the analyst's.
2. **I/O saturation.** Once the hit rate collapses, OLTP's demand for random reads explodes beyond what the device can supply. The queue grows without bound (§9 Step 4). This is the channel that actually produces the timeouts.
3. **Vacuum / snapshot pinning.** A long-running read transaction holds a snapshot; MVCC cannot reclaim row versions newer than it (Lesson 53). Dead tuples accumulate across the entire database, tables bloat, and index scans must skip over ever more invisible versions. The slowdown persists **after** the query finishes, until vacuum catches up.
4. **CPU and worker starvation.** Parallel query workers and connection-pool slots are finite. A query using 8 parallel workers for 40 s is 8 cores unavailable to checkout.

The second-order effect is the dangerous one: **the cause is invisible in the usual dashboards**. There is no deploy, no traffic change, no error spike at the edge — just a latency cliff. Teams routinely spend hours chasing the wrong thing.

> **Simplified teaching model vs production reality.** The clean story is "analytics evicts the cache". In production you will also see: connection-pool exhaustion, replica lag alarms, autovacuum falling behind and never recovering, a planner that chooses a different plan for OLTP queries because statistics shifted, and — most confusingly — the analyst's query *succeeding* while everything else fails, so the analyst reports no problem.

### 4.3 Row-oriented versus column-oriented storage, from first principles

The unit of I/O is a **page** (commonly 8 KB; in analytical systems often much larger blocks). You cannot read half a page. **Therefore the question that decides everything is: what fraction of the bytes you read are bytes you actually wanted?**

Consider a table with **50 columns**, averaging **16 bytes** each, so **800 bytes per row**.

**Row-oriented ("row store", "N-ary storage model").** All columns of one row are stored contiguously:

```
page 1: [r1:c1 c2 c3 … c50][r2:c1 c2 c3 … c50][r3:c1 … c50] …
```

A query needing `c7`, `c22`, `c41` must still read whole rows, because `c7` of row 2 is 800 bytes away from `c7` of row 1 — on a different part of the page, and after 10 rows, a different page entirely. **Useful fraction = 48 bytes of 800 = 6 %.**

This layout is *perfect* for OLTP: `SELECT * FROM orders WHERE order_id = 12345` wants the whole row, and the whole row is one contiguous read. Likewise, inserting a row writes one place, and updating a row rewrites one contiguous region.

**Column-oriented ("column store", "decomposition storage model").** Each column is stored contiguously, in its own file or block range, with rows aligned by position:

```
c1 block : [r1 r2 r3 r4 … r1,000,000]
c2 block : [r1 r2 r3 r4 … r1,000,000]
…
c7 block : [r1 r2 r3 r4 … r1,000,000]   ← read only this
```

The query reads only the three column blocks it needs. **Useful fraction ≈ 100 %.** That is the **projection gain**: `50 / 3 = 16.7×` fewer bytes before compression.

**Then compression multiplies the gain,** for a reason that is purely a consequence of the layout: a column block contains **one data type with one distribution** — a million ISO dates, or a million category codes, or a million prices. Adjacent values are similar, so general and specialised encodings work extremely well:

- **Dictionary encoding**: 40 distinct categories → 6 bits per value instead of a 12-byte string. ~16×.
- **Run-length encoding (RLE)**: sorted or low-cardinality columns compress to `(value, count)` pairs. Often 50×+.
- **Delta encoding**: timestamps and monotonic IDs store differences, which are tiny integers.
- **Frame of reference + bit packing**: prices in a known range need 11 bits, not 64.

A row store cannot do any of this well, because a page interleaves a string, an integer, a timestamp and a boolean every few bytes — the entropy is high and the patterns are destroyed. Typical compression ratios: **row store ~1.5×, column store ~6–20×** on the same data.

**Total analytical advantage = projection gain × compression gain.** With `16.7 × 4 = 67×` in the worked case (§9 Step 2), which is why "10 to 100 times" is the standard honest range rather than a marketing figure.

**Two further mechanisms, briefly:**

- **Vectorised execution.** Instead of the classic row-at-a-time "Volcano" iterator — one virtual function call per row per operator — a columnar engine processes a **batch (vector) of 1,024–8,192 values** per call. This amortises interpretation overhead, keeps data in CPU cache, and lets the compiler emit SIMD instructions that apply one operation to 8 or 16 values at once. Typical speed-up over row-at-a-time: **10–50× on CPU-bound aggregation**, independent of the I/O gain above.
- **Late materialisation.** Rather than reconstructing whole rows as soon as they are read, the engine operates on compressed column vectors and **position lists** for as long as possible, only assembling the tuples that survive all filters at the very end. If a filter keeps 0.1 % of rows, late materialisation avoids reconstructing 99.9 % of them — and often allows filtering *directly on dictionary codes* without decompressing at all.

**The honest cost of columnar.** It is terrible at OLTP, for symmetric reasons: fetching one whole row requires touching 50 separate column blocks; updating one field means rewriting or versioning a compressed block; inserting one row means appending to 50 places. Column stores therefore typically offer no single-row updates, no per-row locking, append-only or batched ingestion, and weaker or absent transactional guarantees. **The property that makes them fast for OLAP is exactly the property that makes them unusable for OLTP.**

### 4.4 The standard separation: OLTP → ETL/ELT → warehouse

```
users ─▶ OLTP database ─▶ extraction ─▶ transformation ─▶ warehouse ─▶ analysts
         (row store)                                      (column store)
```

- **ETL (Extract, Transform, Load)**: transform the data *before* loading. Classic, control over what lands, but transformation logic sits in a separate pipeline tool and re-running history is expensive.
- **ELT (Extract, Load, Transform)**: load raw data first, transform *inside* the warehouse with SQL. Now dominant, because warehouse compute is cheap and elastic, raw data is retained so transformations can be re-run against history, and the transformation logic is plain version-controlled SQL.

Extraction methods, with their real freshness: full table dump (hours, simple, heavy), incremental by `updated_at` watermark (minutes, but misses hard deletes), and **change data capture** from the write-ahead log (seconds, complete, more infrastructure — **Phase 4, Topic 22 — Change data capture and event sourcing**).

The warehouse itself, along with data lakes and the lakehouse architecture that merges them, is **Lesson 58 — Data warehouses, data lakes and lakehouses** (Phase 3, Topic 19).

### 4.5 Read replicas: a partial and often insufficient answer

The tempting shortcut is "point analytics at a read replica" (Lesson 35). It helps with exactly one of the four problems and nothing else.

| Problem | Does a replica solve it? | Why |
|---|---|---|
| Analytics evicts the **primary's** buffer pool | **Yes** | The scan runs on another machine's memory |
| Analytics saturates the **primary's** I/O and CPU | **Yes** | Different hardware |
| The query is still **slow** | **No** | The replica is byte-for-byte the same row-oriented layout. 16 GB still has to be scanned at 6 % useful bytes. A 40 s query is still 40 s |
| Long queries pin the **vacuum horizon** | **Partly, and it can be worse** | With `hot_standby_feedback = on`, the replica's long query **holds back vacuum on the primary** — reintroducing the exact bloat problem you moved away from. With it off, the replica cancels your query mid-flight with "canceling statement due to conflict with recovery", which for a 40-minute report is a guaranteed failure |
| Replication lag | **No — it adds it** | Analytics reads data that is seconds to minutes stale, which is fine, but the replica now also lags for *other* consumers |
| Row-store indexing is wrong for scans | **No** | You would need columnar indexes the engine does not have |
| Schema is normalised, so reports need 5-way joins | **No** | Same schema |

**The fundamental mismatch a replica cannot fix: a replica changes *where* the work happens, not *how* it is done.** Copying an unsuitable physical design to a second machine leaves it unsuitable. A replica is a genuine and correct answer to *read throughput* and *primary protection*; it is not an answer to *analytical query performance*.

When a replica **is** sufficient, state the conditions honestly: the analytical queries are small (seconds, not minutes), infrequent, touch recent partitioned data, and the team's alternative is "no analytics at all". A dedicated reporting replica with `statement_timeout`, a resource-limited role and `hot_standby_feedback = off` is a perfectly respectable stepping stone. It stops being sufficient the moment query time exceeds the lag tolerance or the scan volume exceeds the replica's memory.

### 4.6 HTAP, assessed honestly

**HTAP (Hybrid Transactional/Analytical Processing)** means one system serving both workloads, so analysts see live data with no pipeline. Real implementations include TiDB (row store plus a columnar replica, TiFlash), SingleStore (row store for recent data, column store for older), SAP HANA, Oracle's In-Memory Column Store, and MySQL HeatWave.

**How they actually work** — and this is the key point: **almost all of them maintain two physical copies internally**, one row-oriented and one columnar, and route each query to the appropriate one. They have not dissolved the trade-off; they have **internalised the pipeline**, replacing your ETL with their replication.

| Claim | Honest assessment |
|---|---|
| "No ETL pipeline" | Largely true, and genuinely valuable — the sync is the vendor's problem, with second-scale freshness |
| "One system to operate" | True, but it is a more complex system, with fewer engineers who know how to tune it, and resource-governance settings you must get right or you recreate the §4.2 problem inside one box |
| "Analytical performance matching a dedicated warehouse" | **Usually not.** Dedicated columnar engines separate storage from compute, scale compute elastically to hundreds of cores for one query, and are tuned for nothing else. HTAP engines are typically within 2–5× on good workloads, further behind on hard ones |
| "Transactional performance unaffected" | Partly. Maintaining the columnar replica costs write throughput; vendors quote 5–20 % typical overhead |
| "Always-fresh analytics" | Freshness is sub-second to seconds, not zero. Still far better than nightly ETL |
| "Lower total cost" | Sometimes genuinely yes at small-to-mid scale (under ~10 TB), where a separate warehouse plus a pipeline plus a data team is disproportionate overhead |

**Where HTAP is the right answer:** operational analytics on fresh data — real-time dashboards, fraud scoring, personalisation, inventory intelligence — at moderate scale, where freshness matters more than peak analytical throughput and the team is small. **Where it is not:** petabyte historical analysis, heavy data-science workloads, or anywhere a pipeline already exists and works. **The honest default remains the standard separation**; HTAP is a considered optimisation for a specific freshness requirement, not a reason to skip Lesson 58.

## 5. Request / data flow

Two requests, traced start to finish against the same BookNook data, to make the structural difference concrete.

**A — OLTP: `GET /orders/12345` on the production PostgreSQL primary**

1. Request hits the app server; the handler takes a pooled connection (one of 32).
2. It issues a prepared statement, `SELECT … WHERE order_id = $1`, so parsing and planning are skipped — the cached plan is an index scan.
3. The executor descends the `orders` primary-key B-tree: root, internal, leaf — **3 page accesses**, all buffer-pool hits (~1 µs each).
4. It fetches the heap page holding the row: **1 page access.**
5. MVCC visibility check: the row version is compared against the transaction snapshot (Lesson 53). Invisible versions are skipped.
6. The join to `order_items` runs as a nested loop over the `(order_id, isbn)` index: **3 index + 4 heap pages.**
7. Four `books` primary-key lookups follow: **16 pages.**
8. **59 pages total, ~3 KB of useful data returned.** Server time ≈ 0.65 ms (Lesson 56 §9).
9. Result is serialised, the connection returns to the pool, and the page renders. Total elapsed ≈ 1 ms.
10. **Resource footprint: negligible.** The pages touched were already hot and remain hot; nothing is evicted; no other query is affected.

**B — OLAP: "revenue by category by month, last 3 years", run against that same primary**

1. An analyst opens a BI tool, which connects as a normal application user and issues a 3-way join with `GROUP BY` over 36 months.
2. The planner estimates 87.6 M `order_items` rows and chooses parallel sequential scans with hash joins. **8 parallel workers are allocated**, leaving few cores for checkout.
3. A snapshot is taken. **From this instant, vacuum across the entire database cannot reclaim any newer row version.** The clock on bloat starts.
4. The scan begins streaming `order_items` pages. All 50 columns of every row are read because the layout is row-oriented, though only 3 columns are wanted.
5. After ~6 seconds, **the buffer pool is full of cold historical pages.** The hot 12 GB working set has been evicted.
6. Checkout queries now miss the cache. Per-page cost rises from ~2 µs to ~60 µs; each needs 59 pages, so each query's I/O demand rises 30×.
7. **Aggregate random-read demand exceeds device capacity** (§9 Step 4). The I/O queue grows without bound; latency rises super-linearly, not proportionally.
8. Checkout p99 crosses the 2,000 ms application timeout. Users see spinners, then errors. Retries arrive, **adding load**, and the system enters a retry storm (Lesson 17).
9. Connection-pool slots fill with queries waiting on I/O; new requests are rejected at the pool.
10. At t = 40 s the analytical query returns 1,440 rows. The analyst is satisfied and reports nothing unusual.
11. Buffer-pool refill takes another 2–4 minutes of degraded service as hot pages are faulted back in one by one.
12. Autovacuum, blocked since step 3, now has a backlog. Bloat persists for hours.
13. The on-call engineer sees a 4-minute latency cliff with no deploy, no traffic change, and — crucially — **no slow query in the log, because the offending query was fast enough not to trip the threshold on a box that was not yet degraded when it started.**

**C — the same OLAP query against a columnar warehouse**

1. The BI tool queries the warehouse, which holds a star schema loaded by ELT, lagging production by ~15 minutes.
2. Partition pruning on `order_date` eliminates partitions outside the 3-year window before any data is read.
3. Only three column segments are opened: `revenue_pence`, `order_month`, `category_id`. **47 of 50 columns are never touched.**
4. Zone maps (per-block min/max) skip blocks that cannot match remaining predicates.
5. Compressed blocks are read — ~200 MB rather than 16 GB (§9 Step 3).
6. Vectorised execution aggregates batches of 4,096 values at a time; `category_id` is grouped **on its dictionary codes without decompressing to strings.**
7. Late materialisation means only the 1,440 surviving result groups are ever materialised as rows.
8. Result returns in ~0.4 s. **Production OLTP was not involved and did not notice.**

## 6. ASCII diagram

```
BYTE LAYOUT ON DISK — the root of everything in this lesson
═══════════════════════════════════════════════════════════════════════
Table: 50 columns × 16 bytes = 800 bytes/row. Query needs c7, c22, c41.

ROW STORE (OLTP)  — one row contiguous
┌─── 8 KB page ─────────────────────────────────────────────────────┐
│ r1: c1 c2 … c7 … c22 … c41 … c50 │ r2: c1 c2 … c7 … c41 … c50 │ …│
│      ░░░░░░░ ██ ░░░ ██ ░░ ██ ░░░ │     ░░░░░ ██ ░░░ ██ ░░░░░░ │  │
└───────────────────────────────────────────────────────────────────┘
  ██ = wanted (48 B)   ░░ = read anyway (752 B)
  USEFUL FRACTION = 48/800 = 6 %        ⇒ 94 % of all I/O is waste
  PERFECT for: SELECT * WHERE pk = 12345   (whole row, one read)

COLUMN STORE (OLAP) — one column contiguous
┌─ c1  block ─┐┌─ c2  block ─┐ … ┌─ c7 block ─┐ … ┌─ c41 block ─┐
│r1 r2 r3 …   ││r1 r2 r3 …   │   │r1 r2 r3 … │   │r1 r2 r3 …   │
│░░░░░░░░░░░░ ││░░░░░░░░░░░░ │   │███████████│   │█████████████│
└─────────────┘└─────────────┘   └───────────┘   └─────────────┘
         never opened                  read only these 3
  USEFUL FRACTION ≈ 100 %
  TERRIBLE for: SELECT * WHERE pk = 12345  (50 separate block reads)


WHY COLUMNS COMPRESS AND ROWS DO NOT
═══════════════════════════════════════════════════════════════════════
 ROW page bytes:  "978013" │ 4 │ 899 │ 2026-10-09 │ t │ "Fiction" │ …
                  ↑ mixed types, mixed distributions ⇒ high entropy ⇒ ~1.5×

 COLUMN block:    Fiction Fiction Fiction Crime Crime Crime Crime …
                  ↑ one type, sorted-ish, 40 distinct values
                  dictionary → 6 bits/value   +  RLE → (Fiction,3)(Crime,4)
                  ⇒ 6–20× typical

 TOTAL GAIN = projection (50/3 = 16.7×) × compression (6/1.5 = 4×) ≈ 67×


THE INCIDENT — one analytical query on the OLTP primary
═══════════════════════════════════════════════════════════════════════
 t=0s   buffer pool 32 GB: [▓▓▓▓▓▓▓▓▓▓▓▓ hot 12 GB ▓▓▓▓][ free ]
        checkout p99 = 4 ms   hit rate 99 %      ✅
          │
          │  analyst runs GROUP BY over 3 years (16 GB scanned)
          ▼
 t=6s   buffer pool: [░░░░ cold historical pages ░░░░░░░░░░░░░░░░]
        hot set EVICTED.  hit rate 40 %
        per-page cost  2 µs ──▶ 60 µs   (30×)
          │
          ▼
 t=8s   random IOPS demanded 70,800/s  >  device capacity 60,000/s
        ρ = 1.18  ⇒  QUEUE GROWS WITHOUT BOUND
        checkout p99  4 ms ──▶ 2,000 ms ──▶ TIMEOUT      ❌
          │
          ▼
 t=40s  analytical query returns 1,440 rows. Analyst: "worked fine."
 t=40s→4m  buffer pool slowly refills; service still degraded
 t=40s→hrs autovacuum backlog from the pinned snapshot ⇒ bloat


THE STANDARD SEPARATION
═══════════════════════════════════════════════════════════════════════
  users                                              analysts
    │                                                    ▲
    ▼                                                    │
 ┌─────────────┐   CDC / batch   ┌──────────┐   ELT  ┌───┴────────┐
 │ OLTP (row)  │ ──────────────▶ │ staging  │ ─────▶ │ warehouse  │
 │ 3NF, B-tree │   sec–hours     │ raw      │  SQL   │ (columnar) │
 │ p99 < 10 ms │                 └──────────┘        │ star schema│
 └─────────────┘                                     └────────────┘
   ONE-WAY. The warehouse never writes back. Lesson 58.

  Partial answer:  OLTP ──▶ read replica (row store)
                   fixes WHERE the work runs, not HOW. Still 40 s.

  HTAP:            ┌── row store (OLTP) ──┐
                   │   internal sync ~1 s │   two copies, one product.
                   └── column store (OLAP)┘   Trade-off internalised,
                                              not eliminated.
```

## 7. Detailed example

### BookNook — two queries, counted precisely

**Scale assumptions (typical for a mid-size retailer, stated so the arithmetic is checkable):** 20,000 orders/day → 7.3 M orders/year; mean 4 line items → 29.2 M `order_items`/year; **3 years = 21.9 M orders and 87.6 M order items**; 2 M books; `order_items` row ≈ 120 B; `orders` row ≈ 200 B; `books` row ≈ 600 B; buffer pool 32 GB; hot working set 12 GB.

**Query 1 — "fetch order 12345" (OLTP).**

- Rows touched: 1 order + 4 items + 4 books + 4 authors + 4 publishers = **17 rows**.
- Useful bytes: `200 + 4×120 + 4×600 + 4×80 + 4×60 = 200 + 480 + 2,400 + 320 + 240 = 3,640 B ≈ 3.6 KB`.
- Pages read: **59** (Lesson 56 §9) = `59 × 8 KB = 472 KB` of pages touched, nearly all already resident.
- Server time: **≈ 0.65 ms**. Frequency: ~1,200/sec at peak.
- Buffer-pool impact: **zero eviction** — the pages were hot and stay hot.

**Query 2 — "revenue by category by month, last 3 years" (OLAP), run on the OLTP schema.**

- `order_items`: `87.6 M × 120 B = 10.5 GB`
- `orders` (needed for `order_date`): `21.9 M × 200 B = 4.4 GB`
- `book_categories` + `books` (needed for category): `≈ 1.2 GB`
- **Total scanned ≈ 16.1 GB; rows touched ≈ 111.5 M.**
- Useful bytes: only three values per item row — `revenue (8 B) + month (4 B) + category_id (4 B) = 16 B` of the 120 B row, plus the join overhead. `87.6 M × 16 B = 1.4 GB` of genuinely wanted data out of 16.1 GB read. **Useful fraction ≈ 8.7 %.**
- Result size: `36 months × 40 categories = 1,440 rows ≈ 60 KB`.

**The ratio that defines the lesson:** Query 1 touches 17 rows and 3.6 KB; Query 2 touches 111,500,000 rows and 16.1 GB. That is **6.6 million times more rows and 4.4 million times more bytes, to produce a 60 KB answer.** No single tuning makes both of those fast on one machine with one storage layout.

**Query 2 in the columnar warehouse.** The ELT job has already flattened `order_date` and `category_id` into the fact table, so no joins are needed:

- Columns read: `revenue_pence` (8 B), `order_month` (delta-encoded, ~2 B), `category_id` (dictionary, 1 B) = **11 B/row**.
- Uncompressed: `87.6 M × 11 B = 964 MB`. Compressed ~5×: **≈ 200 MB.**
- At 1.5 GB/s: `200 MB / 1,500 MB/s = 0.13 s` of I/O; with 8-way parallelism and vectorised aggregation, **≈ 0.4 s total.**
- **Bytes read: 16.1 GB → 0.2 GB = 80× less. Time: ~40 s → ~0.4 s = 100× faster. Production impact: none.**

**What BookNook actually did, in order.** (1) Set `statement_timeout = '30s'` for a dedicated `analytics` role on the primary — stops the worst incidents in one line of configuration. (2) Added a reporting read replica with `hot_standby_feedback = off`; this protected the primary but reports still took 40 s and were cancelled during heavy write bursts. (3) Built a nightly ELT into a columnar warehouse; reports went to sub-second and the replica returned to serving overflow reads. (4) When finance asked for same-day figures, switched extraction from nightly batch to CDC (**Phase 4, Topic 22**), giving ~2-minute freshness. Each step was justified by the previous step's measured shortfall — not by architecture fashion.

### A contrasting real-world example — a hospital's patient record versus its epidemiology unit

The same split, with the stakes inverted, which clarifies *why* the freshness columns differ.

- **OLTP**: a clinician opens one patient's record at the bedside. One patient, all fields, **absolute freshness required** — a medication administered four minutes ago **must** be visible, because the failure mode is a double dose. Latency target is sub-second because a person is standing there. This is a point lookup on a row store, and nothing else is acceptable.
- **OLAP**: the epidemiology unit asks "readmission rates within 30 days by procedure and age band, across 1.2 M admissions over 5 years". Three columns, hundreds of millions of rows, no individual patient matters, and **last night's data is entirely sufficient** — a cohort trend does not change because of this afternoon's admissions.

Two further points this example makes that BookNook does not. First, the analytical copy is often **structurally different, not just physically different**: the research extract is de-identified, bucketed into age bands, and governed by separate access controls. The pipeline is a transformation boundary, not merely a transport. Second, it shows the **cost of getting the separation wrong is domain-specific**: at BookNook an OLTP latency cliff costs abandoned carts; in a hospital it delays clinical decisions. The architecture is identical; the urgency of enforcing it is not.

The transferable rule: **ask "who is waiting, and what happens if this answer is 12 hours old?"** If the answer is "a user, and it must be now", it is OLTP. If it is "an analyst, and last night is fine", it is OLAP. That single question classifies almost every query you will ever be handed.



---

# Lesson 58 — Data Warehouses, Data Lakes, and Lakehouses

*Phase 3, Topic 19*

## 1. Simple definition

Lesson 57 drew the line between **OLTP** (many small reads and writes serving the live application) and **OLAP** (few huge scans serving analysis). This lesson is about the three storage architectures the industry built to hold OLAP data, in the order they were invented:

- A **data warehouse** is a central store of *cleaned, modelled, structured* data, where the structure is decided and enforced **before** the data is written. Querying it is fast because somebody already did the hard work of shaping it.
- A **data lake** is a central store of *raw files* in cheap object storage, where structure is applied **when you read**, not when you write. Ingestion is nearly free; making sense of it later is your problem.
- A **lakehouse** is a data lake plus a **metadata layer** that adds the guarantees a warehouse had — atomic transactions, schema enforcement, time travel — directly on top of those cheap files.

Terms defined before use:
- **Schema-on-write**: the storage system rejects data that does not match a declared schema at the moment of writing. A warehouse does this.
- **Schema-on-read**: the storage system accepts any bytes; the *query engine* imposes a schema at read time, and a mismatch is discovered then. A lake does this.
- **Object storage**: a key–value store for large immutable blobs (Amazon S3, Azure Data Lake Storage Gen2, Google Cloud Storage). You `PUT` a whole object and `GET` a whole object or a byte range. There is no in-place update, no rename-is-cheap guarantee on every vendor, and no transaction across two objects.
- **Table format** (also **open table format**): a specification for a set of metadata files that sit beside the data files and declare *which files currently constitute the table*. Delta Lake, Apache Iceberg and Apache Hudi are the three mainstream examples.
- **Compute engine**: the thing that actually executes a query — Spark, Trino, Presto, Snowflake's virtual warehouse, BigQuery's Dremel, DuckDB. Distinct from the storage.

The one sentence to remember: **a warehouse enforces structure on the way in, a lake defers it to the way out, and a lakehouse keeps the lake's cheap storage while putting the warehouse's guarantees in a metadata layer.**

## 2. Why it exists

BookNook has, by Lesson 57, an OLTP PostgreSQL cluster handling orders, a read replica, and an analytics problem. The analytics problem arrives in four waves, and each wave is why one of these architectures exists.

**Wave 1 — "run the report on the replica".** Finance wants monthly revenue by category. You run it against the read replica (Lesson 32). It works for a year. Then the query starts taking 40 minutes, holds a long snapshot open, and on PostgreSQL that long-running transaction interacts badly with replication (Lesson 34). Worse, marketing wants the same number joined against clickstream data that does not live in PostgreSQL at all.

**Wave 2 — the warehouse.** So you build a separate system. You extract from PostgreSQL nightly, reshape the rows into a model designed for *questions* rather than *transactions*, and load them into a columnar analytic store. Queries that took 40 minutes take 8 seconds. This is the data warehouse, and the reason it exists is: **OLTP schemas are normalised for write correctness (Lesson 56) and that is exactly the wrong shape for analysis.**

**Wave 3 — the lake.** Now product wants to analyse raw application logs, A/B test event streams, the JSON payloads from the shipping carrier's webhook, and scanned supplier invoices. None of it has a stable schema. Forcing each into warehouse tables before anybody knows what questions to ask means months of modelling work for data that might be thrown away. Also, warehouse storage historically cost 20–60× object storage per terabyte. So you dump everything raw into S3 and worry later. That is the data lake, and the reason it exists is: **you cannot model data whose value you have not discovered yet, and you should not pay warehouse prices to store data you may never query.**

**Wave 4 — the lakehouse.** The lake works for exploration and fails for anything that must be *correct*. Two Spark jobs write the same partition and you get duplicate rows. A job dies halfway and leaves 340 complete files and 1 truncated one, and every downstream query silently reads the mess. Someone adds a column upstream and the whole pipeline breaks. GDPR says delete one customer's rows and you have no way to do that without rewriting petabytes. Meanwhile you now have *two* systems, two copies of the same facts, and two numbers for "revenue" that do not agree. The lakehouse exists to resolve this: **keep one copy of the data in cheap object storage, and add a transaction log so that it behaves like a table instead of like a directory.**

Each architecture is a response to a concrete failure of the previous one. None of them is simply "better"; they trade different things.

## 3. Real-world analogy

Think about how an organisation stores paper.

- The **data warehouse** is the **finance department's filing cabinet**. Every document has a form number, a defined set of fields, and a clerk who refuses anything incomplete at the counter. Finding "all invoices over £500 from Q3" takes two minutes because the cabinet was designed for exactly that question. Getting a *new kind* of document accepted takes a six-week committee.
- The **data lake** is the **warehouse full of unopened boxes**. Anything can be dropped off, instantly, for almost no cost. Need something? You can search — but you will be opening boxes. If nobody keeps a register of what was delivered, you eventually have a building full of boxes nobody can identify. That is the **data swamp**.
- The **lakehouse** is **the same boxes, plus a strictly maintained manifest at the door**. The boxes stay cheap and in place. But the manifest records exactly which boxes are the current official contents of "2026 Invoices", who changed it, and when — so you can ask for yesterday's contents, swap in corrected boxes atomically, and never see a half-finished delivery.

**Where the analogy breaks down:**

| Paper storage | Data architecture |
|---|---|
| A box has one copy; moving it removes it from where it was | Object storage files are copied, not moved; "delete" is often just removing a reference in metadata, and the bytes may linger until a vacuum job runs |
| A clerk can glance in a box and recognise an invoice | A query engine reading an unregistered file has *no* idea what it is; schema-on-read means a human must have written the interpretation somewhere |
| Retrieval cost scales with human effort | Retrieval cost scales with **bytes scanned**, which is why file format and partitioning (§4) dominate the bill in a way shelving layout never does |
| One box = one unit | 10,000 tiny files cost far more to read than one file of the same total size, because per-file overhead dominates — there is no paper equivalent of the small-files problem |
| A manifest is a convenience | The lakehouse manifest is the *definition of the table*. Files not in it do not exist, even though they are sitting right there in the bucket |
| Boxes delivered yesterday are still yesterday's | Time travel is real and cheap in a lakehouse because old manifests are retained; you genuinely can query the table as of 09:00 last Tuesday |

The fifth row is the mechanism of the whole lesson. Everything a lakehouse does comes from "the metadata decides what the table is".

## 4. Detailed technical explanation

### 4.1 The data warehouse

A warehouse has four defining properties.

1. **Schema-on-write.** `INSERT` of a row that violates the column types or constraints fails. The data in the warehouse is, by construction, conformant.
2. **Curated and modelled.** Data is reshaped from source systems into a model optimised for querying, not for transaction integrity.
3. **Columnar storage with compression.** Covered in Lesson 57: values of one column stored contiguously, so a query touching 3 of 40 columns reads roughly 3/40 of the bytes, and like-typed neighbouring values compress 5–15× (typical).
4. **Historically, storage coupled to compute.** Teradata, Netezza, early Oracle Exadata and on-premise SQL Server warehouses stored data on disks physically attached to the machines that ran the queries. Buying more storage meant buying more CPU, and vice versa, whether you needed it or not. This coupling is the single biggest historical reason warehouses were expensive, and its removal (Snowflake, BigQuery, Redshift RA3) is the single biggest change of the last decade.

#### Dimensional modelling

Lesson 56 covered normalisation — the discipline of removing redundancy so that every fact is stored once, which makes *writes* safe. Dimensional modelling is the deliberate opposite discipline, designed by Ralph Kimball, which makes *reads* fast and *questions* easy to express.

Two kinds of table:

- A **fact table** holds measurements of business events. One row per event. Columns are mostly **measures** (numeric, additive values like `quantity`, `line_amount`) plus **foreign keys** to dimensions. Fact tables are long and narrow and grow forever — this is where your billion rows live.
- A **dimension table** holds the descriptive context you filter and group by: customer, product, date, store, channel. One row per *thing*. Dimension tables are short and wide: 50 columns and 200,000 rows is normal. They are deliberately **denormalised** — `publisher_name`, `publisher_country`, `category`, `subcategory` all live flat in `dim_book`, even though that repeats "Penguin / UK" 4,000 times. Repetition is cheap in columnar storage (it compresses away) and joins are expensive, so you trade one for the other.

**Grain** is the single most important modelling decision, and it must be made first. The grain is the precise definition of what one fact row means, stated as an English sentence. For BookNook, candidate grains:

| Candidate grain | One row = | Consequence |
|---|---|---|
| One row per order | an order | Cannot analyse by book or category at all; too coarse |
| One row per order line | one book on one order | Can analyse by book, category, order, customer; standard choice |
| One row per unit shipped | one physical copy | Allows per-copy tracking (serials, returns of partial lines) but 1.4× the rows for no current question |

Rules about grain:
- Declare it in a sentence before writing any DDL. "One row per order line at the moment of order confirmation."
- Choose the **finest grain you can afford**, because aggregates can always be derived from fine grain, and never the reverse.
- **Never mix grains in one fact table.** A table containing both order-line rows and order-level shipping-cost rows will double-count the moment anyone writes `SUM(amount)`. If you need order-level measures, make a second fact table at order grain.

**Star schema versus snowflake schema.**

- A **star schema** has one fact table joined directly to fully denormalised dimension tables. Visually: fact in the middle, dimensions radiating out. One join hop from fact to any attribute.
- A **snowflake schema** normalises the dimensions themselves: `dim_book` → `dim_publisher` → `dim_publisher_country`. Visually the arms branch, hence "snowflake". Two or three join hops to reach an attribute.

| | Star | Snowflake |
|---|---|---|
| Joins per query | 1 hop per dimension | 2–4 hops per dimension |
| Storage for dimensions | Larger (redundant text) | Smaller |
| Storage as % of total | Typically <2% — dimensions are tiny next to facts | Saves a fraction of that 2% |
| Query SQL | Simple, readable | More verbose, easier to get wrong |
| BI tool compatibility | Excellent; most tools assume star | Workable but more configuration |
| Update anomalies | Possible, handled by the ETL job | Prevented structurally |

The practical verdict: **use a star schema.** Snowflaking saves storage on the smallest part of the warehouse while adding joins to every query. Snowflake only when a dimension is genuinely huge (a 300-million-row customer dimension) or when a sub-dimension is independently reused by many facts.

#### Slowly changing dimensions (SCDs)

A dimension attribute changes in the real world — a book's price changes, a customer moves city, a category is renamed. The question is what happens to *history*. Three standard answers:

- **Type 1 — overwrite.** Update the attribute in place. History is lost. Old orders now appear to have always had the new value. Correct for genuine corrections ("the publisher's name was misspelt"). Wrong for real changes.
- **Type 2 — add a new row.** Keep the old row, mark it expired, insert a new row with a new surrogate key. The dimension gains `valid_from`, `valid_to`, `is_current` columns. The fact row points at whichever version was current *at the time of the event*, so a report rerun in 2030 still shows the 2026 category. This is the workhorse; when someone says "SCD" unqualified they usually mean Type 2. Cost: the dimension grows, and every lookup in ETL must resolve "which version was live at this timestamp".
- **Type 3 — add a new column.** Keep `current_category` and `previous_category` side by side. Supports exactly one level of history, and only for pre-chosen attributes. Useful in the narrow case of a one-off reorganisation where analysts want to pivot "old structure versus new structure".

A term you need: a **surrogate key** is a meaningless integer primary key generated by the warehouse (`book_key = 48213`), as opposed to the **natural key** or **business key** from the source system (`isbn = '9780140449136'`). Type 2 *requires* surrogate keys, because the same ISBN now legitimately has three dimension rows and the fact table must point at one specific version.

### 4.2 The data lake

A data lake is: object storage, plus files, plus a catalogue, plus engines that read files.

- **Storage**: S3/ADLS/GCS. Typical list price around **$0.023 per GB-month** for S3 Standard, falling to roughly $0.0125 for Infrequent Access and about $0.004 for Glacier Instant Retrieval (approximate, region-dependent, 2026). Durability quoted at eleven nines. Effectively unlimited capacity, no provisioning.
- **Files**: anything. CSV dumps, JSON event streams, Parquet, images, PDFs.
- **Catalogue**: a metadata service (AWS Glue Data Catalog, Hive Metastore, Unity Catalog) that maps a *table name* to a *path prefix* plus a declared schema, so that SQL engines can be pointed at `s3://booknook-lake/events/clickstream/` and told "these are Parquet files with these columns".
- **Engines**: Spark, Trino, Athena, DuckDB read the files directly. Storage and compute are fully decoupled — you can run zero compute for a week and still be storing the data for pennies.

**Schema-on-read in practice.** Nothing validates the write. If an upstream service starts emitting `price` as the string `"12.99"` instead of the number `12.99`, the write succeeds. The failure surfaces days later as either a query error or — far worse — silently nulled values in a report.

#### The data swamp

A **data swamp** is a data lake that has accumulated data nobody can find, trust, or interpret. It is the default end state of an ungoverned lake. The causes are specific and each has a specific countermeasure:

| Cause | What it looks like | Countermeasure |
|---|---|---|
| No catalogue discipline | 400 prefixes in the bucket, no record of what wrote them or what they mean | Mandatory registration in the catalogue; unregistered prefixes are deleted on a schedule |
| No ownership | Every dataset's Slack question ends in "I think Dave built that and Dave left" | A required `owner` tag per dataset, validated in CI |
| No schema contract | Producers change fields freely; consumers break silently | Schema registry with compatibility rules; producer-side contract tests |
| No data quality checks | Nulls, duplicates and impossible values flow through to dashboards | Assertion jobs (row counts, null rates, referential checks) that fail the pipeline loudly |
| No lifecycle policy | Seven years of raw debug logs at Standard storage prices | Lifecycle rules: Standard → IA at 30 days → Glacier at 180 days → delete at N years |
| No lineage | Nobody can answer "if I change this, what breaks?" | Lineage capture in the orchestrator; column-level lineage in the transformation tool |
| Duplicated derived data | Six teams each built their own "revenue" table, with six different numbers | A single modelled layer that is the only sanctioned source for business metrics |

A swamp is not a storage problem. It is a **governance** problem that cheap storage makes it easy to ignore for two years.

### 4.3 The lakehouse

A lakehouse is a data lake where tables are defined by an **open table format**: a published specification for metadata files that live alongside the data files and declare the table's current state.

The three mainstream formats:

- **Delta Lake** (originated at Databricks, now Linux Foundation). Metadata is a transaction log: an ordered sequence of JSON commit files in `_delta_log/`, periodically compacted into Parquet checkpoints.
- **Apache Iceberg** (originated at Netflix, Apache project). Metadata is a tree: a metadata JSON file points at a manifest list, which points at manifest files, which list data files with per-column statistics. Each commit writes a new root metadata file and atomically swaps a pointer held in a catalogue.
- **Apache Hudi** (originated at Uber, Apache project). Built around fast upserts with a timeline of commits, offering copy-on-write and merge-on-read table types; strongest where streaming upserts and incremental pulls dominate.

They differ in detail but share the mechanism, and the mechanism is the thing to understand.

#### The metadata-layer mechanism

The core problem: object storage gives you **atomic single-object writes** and nothing else. You cannot atomically replace 200 files. So how do you get ACID (Lesson 47) on top of it?

The answer is **indirection**: never define the table as "the files under this prefix". Define it as "the files listed in the current metadata". Then:

1. A writer writes new data files as **new objects** with new names. Nobody is reading them, because no metadata references them. Readers are unaffected — this gives snapshot isolation (Lesson 48) for free.
2. The writer then performs **one atomic operation** on the metadata: append commit `N+1` to the log, or swap the catalogue's pointer from metadata file `v7` to `v8`.
3. That single atomic operation is the commit. Before it, the new files are invisible; after it, they are the table. There is no intermediate state in which half the files are live. **Atomicity achieved.**
4. Concurrency control: two writers both try to create commit `N+1`. Exactly one wins (via a conditional put, a catalogue compare-and-swap, or a lock service). The loser re-reads the new state and either retries its operation on top or aborts. This is **optimistic concurrency control** — the same mechanism as optimistic locking in Lesson 48, applied at table granularity.
5. **Time travel** falls out automatically: commit `N-5` still lists a complete, consistent set of files. If those files have not been vacuumed, you can read the table exactly as it was. `SELECT ... VERSION AS OF 112` or `TIMESTAMP AS OF '2026-10-01 09:00:00'`.
6. **Schema evolution** is metadata-only: the schema is recorded in the metadata, and each data file records the schema it was written with. Adding a nullable column is a metadata write costing milliseconds, not a rewrite of petabytes. Renaming a column is safe because Iceberg and Delta track columns by **assigned integer field ID**, not by name position — this is why renames in these formats do not silently re-map data the way bare-Parquet-plus-Hive does.
7. **Partition evolution** (an Iceberg speciality) works because the partition specification is also metadata, and each data file records which spec it was written under. You can switch a table from daily to hourly partitioning going forward; old files keep their old spec and remain readable. Under the classic Hive layout, where the partition is baked into the directory path, the equivalent change requires rewriting the entire table.
8. **Deletes and updates** on immutable files work by one of two strategies. **Copy-on-write**: rewrite the affected files without the deleted rows and commit the swap — expensive write, fast read. **Merge-on-read**: write a small delete file listing removed rows (by position or by predicate) and commit that — cheap write, slightly slower read until compaction. Either way, GDPR deletion becomes a bounded operation instead of an impossible one.
9. **Statistics for skipping.** Manifests carry per-file min/max values per column, row counts and null counts. A query with `WHERE order_date = '2026-09-14'` can eliminate 99% of files **without opening them**, using metadata alone. This is called **file skipping** or **data skipping**, and it is the single biggest reason lakehouse queries got fast.

What the lakehouse still is not: a low-latency OLTP store. Commit latency is typically hundreds of milliseconds to seconds, and the concurrency model assumes few writers doing large operations, not thousands doing tiny ones. Point lookups by key remain far slower than a B-tree index (Lesson 54) in PostgreSQL. **The lakehouse replaces the warehouse, not the operational database.**

### 4.4 File formats

| Format | Layout | Typed? | Schema travels with data? | Compression (typical) | Column pruning | Predicate pushdown | Splittable | Best for |
|---|---|---|---|---|---|---|---|---|
| **CSV** | Row, text | No — everything is a string | No | ~1.5–2× (gzip 3–4× but not splittable) | No | No | Yes (plain), No (gzip) | Interchange with humans and spreadsheets |
| **JSON / JSONL** | Row, text | Weakly | Self-describing per record, repeated every row | ~2–3× | No | No | Yes (JSONL) | Raw landing of nested event data |
| **Avro** | Row, binary | Yes | Yes, embedded | ~3–5× | No | Limited | Yes | Streaming, message payloads, write-heavy, full-row reads, schema evolution |
| **Parquet** | **Columnar**, binary | Yes | Yes, embedded footer | ~6–12× | **Yes** | **Yes** (min/max, dictionary, optional Bloom filters) | Yes (row groups) | Analytical tables; the default |
| **ORC** | **Columnar**, binary | Yes | Yes | ~6–12× | **Yes** | **Yes** (plus lightweight built-in indexes) | Yes (stripes) | Analytical tables in Hive/Trino-centric stacks |

Compression ratios are approximate and depend entirely on data cardinality; wide tables of repeated low-cardinality strings compress far better than high-entropy numeric data.

**Why Parquet's layout matters.** Parquet stores a table as **row groups** (typically 128 MB of rows), and within each row group each column is a contiguous **column chunk** split into **pages**, with a footer holding per-chunk statistics.

Three consequences, each a cost reduction on a system billed by bytes scanned:

- **Column pruning** (projection pushdown): a query selecting 3 of 40 columns reads only those 3 column chunks. Roughly 92% of bytes never leave storage.
- **Predicate pushdown**: `WHERE order_date = '2026-09-14'` is evaluated against each row group's min/max before reading it. Row groups whose range excludes the value are skipped entirely. Effectiveness depends completely on **clustering** — if dates are scattered randomly across row groups, every group's min/max spans the whole range and nothing is skipped. Sorting on the predicate column before writing is what makes pushdown work.
- **Encoding before compression**: dictionary encoding replaces repeated strings with small integers, run-length encoding collapses runs, bit-packing uses the minimum bits per value. Then general compression (Snappy, ZSTD) runs on top of already-compact bytes.

Rule of thumb: **land raw data as JSON or Avro, store queryable data as Parquet.**

### 4.5 ETL versus ELT

- **ETL** — Extract, **Transform**, Load. Pull from sources, transform on a dedicated transformation server (Informatica, Talend, custom Python), load the *finished* result into the warehouse.
- **ELT** — Extract, Load, **Transform**. Pull from sources, load raw into the warehouse or lakehouse immediately, transform *inside* it using SQL.

ETL was correct in 1998 and is mostly wrong now. The reason is entirely economic:

| Factor | ETL era (coupled storage+compute) | ELT era (decoupled, elastic) |
|---|---|---|
| Warehouse compute | Fixed, expensive, bought for 3 years | Elastic, per-second billing, scale to 64 nodes for 4 minutes |
| Warehouse storage | $$$ per TB; you could not afford to keep raw | $0.023/GB-month; keeping raw forever is trivial |
| Therefore | Transform outside; load only the small finished result | Load everything; transform where the power is |
| Reprocessing a bug | Re-extract from source systems (slow, sometimes impossible) | Re-run SQL over raw data you still have |
| Who writes transforms | Specialist ETL engineers with a GUI tool | Any analyst who knows SQL, in version control |
| Schema changes | Break the pipeline at the transform step | Land anyway, fix the transform later |

The decisive advantage is the fourth row. In ELT the raw extract is retained, so a logic bug found in March can be fixed and **replayed over January's data**. In ETL, if the source system has overwritten the rows, that history is simply gone.

ELT does not eliminate transformation; it relocates it and renames the layers. The common convention (the "medallion" naming) is **bronze** = raw as landed, **silver** = cleaned, typed, deduplicated, conformed, **gold** = business-level aggregates and dimensional models consumed by BI.

### 4.6 The modern stack, named concretely

| Layer | Job | Representative tools (2026) |
|---|---|---|
| Ingestion — batch/CDC | Move source rows into the lake/warehouse; **CDC** = change data capture, reading the database's write-ahead log (Lesson 47) to stream row changes | Fivetran, Airbyte, Debezium, AWS DMS |
| Ingestion — streaming | Carry high-volume events | Kafka, Kinesis, Pub/Sub, Redpanda |
| Storage | Hold bytes durably and cheaply | S3, ADLS Gen2, GCS |
| Table format | Make files behave like tables | Delta Lake, Apache Iceberg, Apache Hudi |
| Catalogue / governance | Name, discover, permission, lineage | Glue Data Catalog, Unity Catalog, Polaris, Hive Metastore |
| Query engine | Execute SQL | Snowflake, BigQuery, Databricks SQL, Trino, Athena, Redshift, DuckDB |
| Transformation | Define silver/gold as version-controlled SQL with tests | dbt, SQLMesh, Spark SQL |
| Orchestration | Schedule, order by dependency, retry, alert | Airflow, Dagster, Prefect, Temporal |
| BI / consumption | Dashboards, ad-hoc analysis, reverse ETL | Looker, Tableau, Power BI, Metabase, Superset |
| Observability | Freshness, volume, schema and distribution monitoring | Monte Carlo, Elementary, Soda |

### 4.7 Partitioning and file sizing

**Partitioning** here means physically grouping rows into separate files by the value of a column, so queries filtering on that column read only the relevant files. (Distinct from the *sharding* of Lesson 36, which spread an OLTP dataset across nodes for write throughput; here the goal is read elimination.)

Choosing a partition column:
- Partition on what queries **filter** on — nearly always a date.
- Target roughly **0.5–2 GB of data per partition**. Smaller and you create the small-files problem; larger and you lose skipping precision.
- **Do not over-partition.** `date/country/category/publisher` on a 200 GB table yields perhaps 400,000 partitions averaging 500 KB each — catastrophic.
- Prefer **coarse partitioning plus sorting within files** over fine partitioning. Partition by day; sort by `category, book_key` inside. Parquet statistics then do the fine-grained skipping with no directory explosion.
- Iceberg's **hidden partitioning** lets you declare `days(order_ts)` as the partition transform. Queries write `WHERE order_ts >= ...` naturally and Iceberg derives the partition filter — removing the classic Hive bug where forgetting `AND dt = '...'` triggers a full-table scan.

**The small-files problem, quantified.** Every file read costs a metadata lookup and an HTTP request. Assume (typical) ~20 ms of fixed per-file overhead against S3 and ~100 MB/s of effective per-task throughput.

- 1,000 files × 256 MB = 256 GB. Overhead: 1,000 × 20 ms = **20 seconds**. Transfer at 100 MB/s across 50 parallel tasks: 256,000 MB ÷ (100 × 50) ≈ **51 seconds**. Overhead is 28% of the transfer cost — acceptable.
- 2,000,000 files × 128 KB = the same 256 GB. Overhead: 2,000,000 × 20 ms = 40,000 seconds of task time; across 50 tasks that is **800 seconds = 13.3 minutes of pure overhead**, before a single useful byte is processed. Plus 2 million Parquet footers parsed, and S3 GET charges of 2,000,000 × $0.0004/1,000 = **$0.80 per query run** in request fees alone.

Same bytes, roughly **16× slower**. Streaming ingestion creates exactly this: a micro-batch every 30 seconds across 40 partitions is 115,200 files per day. The fix is **compaction** — a scheduled job (`OPTIMIZE` in Delta, `rewrite_data_files` in Iceberg, clustering in Hudi) that rewrites many small files into few large ones and commits the swap atomically, with `VACUUM`/`expire_snapshots` later removing the orphaned originals once the time-travel retention window has passed.

## 5. Request / data flow

End-to-end path of one BookNook order line, from the checkout button to a number on the CFO's dashboard, in a lakehouse with ELT:

1. **Transaction commits.** A customer confirms an order. PostgreSQL writes rows to `orders` and `order_lines` and commits (Lesson 47). This is the system of record; nothing downstream may alter it.
2. **Change captured.** Debezium, tailing PostgreSQL's logical replication slot (the same WAL stream used for replication in Lesson 32), emits one change event per affected row: operation `c` (create), the full after-image, the commit LSN and timestamp.
3. **Event buffered.** The event is produced to a Kafka topic `booknook.public.order_lines`, partitioned by `order_id` so that all changes to one order stay ordered. Retention 7 days.
4. **Landed to bronze.** A streaming job consumes the topic and appends to the Delta/Iceberg table `bronze.order_lines_cdc` in S3, micro-batching every 60 seconds. Rows are stored raw plus ingestion metadata (`_ingested_at`, `_source_lsn`, `_op`). The table is append-only; nothing is interpreted yet.
5. **Commit made visible.** The streaming job writes its Parquet files, then appends one commit entry to the table metadata. Only at that instant do the new rows become visible to readers; a reader that started 10 ms earlier continues on the older snapshot and sees a consistent view (Lesson 48).
6. **Compaction.** Hourly, an `OPTIMIZE` job rewrites the 60 small files from the last hour into 1–2 files of ~512 MB, sorted by `order_ts`, and commits the swap atomically. Readers never see an inconsistent table.
7. **Silver built.** A dbt model runs on schedule: deduplicates by `(order_line_id, _source_lsn)` keeping the latest, casts types, converts currency, drops test orders, enforces not-null assertions, and `MERGE`s into `silver.order_lines`. If an assertion fails, the orchestrator aborts and the run alerts — downstream models do not execute.
8. **Dimensions conformed.** `dim_book` and `dim_customer` are rebuilt with Type 2 logic: compare incoming attributes to the current row; if a tracked attribute changed, expire the old row (`valid_to = now`, `is_current = false`) and insert a new row with a fresh surrogate key.
9. **Facts loaded.** `gold.fact_order_line` is built by joining silver order lines to the dimension versions **valid at the order timestamp**, resolving natural keys to surrogate keys, and appending at the declared grain of one row per order line.
10. **Aggregates refreshed.** Pre-aggregated tables (`gold.daily_revenue_by_category`) are incrementally rebuilt for the affected partitions only — the last 3 days, to absorb late-arriving data.
11. **Orchestrator confirms.** Airflow/Dagster marks the DAG successful only when every step and every test passed; the dashboard's freshness indicator is driven by this timestamp, not by wall-clock.
12. **Query served.** The CFO opens the dashboard. The BI tool issues SQL against the gold table. The engine reads table metadata, prunes to the 90 partitions in range using manifest statistics, reads only the 4 columns referenced, and returns in a few seconds.
13. **Audit if needed.** A number looks wrong. An analyst queries `TIMESTAMP AS OF` yesterday to confirm whether the value changed, and reads lineage to identify which model produced it.

## 6. ASCII diagram

```
                         BOOKNOOK — OLTP ─► ANALYTICS

 SOURCES                INGEST              STORAGE (object store, one copy)
 ─────────────          ─────────           ───────────────────────────────────
 PostgreSQL  ──CDC──►  Debezium  ─►┐
 (orders,                          │
  order_lines)                     │        ┌──────────────────────────────┐
                                   ├──────► │  BRONZE  raw, append-only    │
 Clickstream ──────►   Kafka   ─►──┤        │  silver/gold derive from it  │
 (web/app events)                  │        └──────────────┬───────────────┘
                                   │                       │ dbt / Spark SQL
 Shipping API ──────►  Fivetran ─►─┤                       ▼
 (webhooks, JSON)                  │        ┌──────────────────────────────┐
                                   │        │  SILVER  typed, deduped,     │
 Supplier CSV ──────►  Airbyte  ─►─┘        │          conformed           │
 (SFTP drops)                               └──────────────┬───────────────┘
                                                           │ dimensional model
                                                           ▼
                                            ┌──────────────────────────────┐
                                            │  GOLD   star schema +        │
                                            │         aggregates           │
                                            └──────────────┬───────────────┘
                                                           │
  ┌────────────────────────────────────────────────────────┘
  │
  ▼                          QUERY ENGINES (elastic, separate from storage)
 ┌─────────────────────────────────────────────────────────────────────┐
 │  Trino / Spark SQL / Databricks SQL / Snowflake / Athena / DuckDB   │
 └───────────────┬─────────────────────────────────────────────────────┘
                 ▼
            Looker / Power BI / notebooks / reverse-ETL back to the app


            WHAT THE TABLE FORMAT ADDS  (why a lake becomes a lakehouse)

   s3://booknook-lake/gold/fact_order_line/
   ├── _metadata/                 ◄── THE TABLE IS DEFINED HERE
   │     ├── v00000119.json        (commit 119: + files F,G  − file C)
   │     ├── v00000120.json        (commit 120: + file H)      ◄── CURRENT
   │     └── manifests/            (per-file: row count, min/max per column)
   └── order_date=2026-09-14/
         ├── part-A.parquet   ✔ listed in v120  → part of the table
         ├── part-C.parquet   ✘ removed at v119 → invisible, still on disk
         │                                         (time travel reads it)
         └── part-H.parquet   ✔ listed in v120

   Writer:  write new Parquet files (invisible)  ──►  ONE atomic metadata
            commit  ──►  all new rows become visible simultaneously.
   Reader:  pin v120 for the whole query  ──►  snapshot isolation, no torn
            reads, no coordination with writers.


              GRAIN AND SHAPE                   STAR SCHEMA (gold)

   fact  = one row per ORDER LINE          dim_date    dim_customer
   10^9 rows, narrow, append-mostly              \        /
   dim   = one row per THING                      \      /
   10^5 rows, wide, Type-2 versioned           fact_order_line
                                                  /      \
   Never mix grains in one fact table.           /        \
                                           dim_book   dim_channel
```

## 7. Detailed example

### 7.1 BookNook

BookNook, three years in: 3.2 million customers, 1.1 million titles, 42 million order lines to date, growing at 95,000 order lines per day, plus ~180 million clickstream events per day. Four analytics demands arrive in the same quarter:

- **Finance** needs revenue by category by month, and it must match the accounting ledger to the penny.
- **Merchandising** needs "which titles were viewed but not bought last week" — requires joining clickstream to orders.
- **Data science** wants three years of raw event data to train a recommender.
- **Legal** needs to delete all personal data for a departed customer within 30 days, everywhere.

A pure warehouse satisfies Finance brilliantly and fails Data Science on cost (storing 180 M events/day at warehouse rates) and partly fails Merchandising (clickstream has no stable schema). A pure lake satisfies Data Science and fails Finance (no transactions, so an interrupted job can double-count revenue) and fails Legal (no row-level delete).

The lakehouse design chosen:

- **Bronze**: `bronze.order_lines_cdc`, `bronze.clickstream`, `bronze.shipping_events`, `bronze.supplier_catalog`. Append-only Iceberg tables, partitioned by ingest date, retained 400 days, lifecycle-tiered after 90.
- **Silver**: typed, deduplicated, conformed; `silver.order_lines`, `silver.page_views`, `silver.customers`.
- **Gold**: star schema below, plus aggregates.

Grain declaration, written down before any DDL: *"One row in `fact_order_line` represents one book line on one confirmed order, as of order confirmation. Cancellations are recorded as separate rows in `fact_order_line_adjustment`, never by mutating the original."*

Why that second sentence matters: if cancellations mutated the original row, two reports run a week apart over the same period would disagree, and Finance's reconciliation would fail. Append-only adjustments make history immutable.

SCD choices for `dim_book`:
- `title`, `isbn` — Type 1. If the title was mistyped, the correct behaviour is "it was always this".
- `category`, `list_price`, `publisher` — **Type 2**. A book moved from "General Fiction" to "Literary Fiction" in August; July's report must still say General Fiction, otherwise last month's board pack changes retroactively.
- `previous_category` — Type 3, added once, because merchandising specifically asked to compare the old and new taxonomy side by side during the reorganisation.

Legal's deletion requirement is satisfied because Iceberg supports row-level `DELETE`: `DELETE FROM silver.customers WHERE customer_id = 88213` writes delete files, commits atomically, and a subsequent compaction plus `expire_snapshots` physically removes the bytes once the retention window closes. In a bare lake this operation would require locating and rewriting every file that might contain that customer, with no atomicity and no proof of completion.

### 7.2 Contrasting example — a hospital trust

A regional hospital trust has the opposite balance of forces.

- **Volume**: ~40 GB per year of structured clinical and administrative data. Tiny.
- **Regulation**: every query against patient data must be audited; access is role-based down to the column; data must not leave jurisdiction.
- **Schema**: highly standardised (HL7, FHIR, SNOMED codes) and changes rarely.
- **Users**: clinical analysts and statutory reporting, not data scientists exploring raw streams.
- **Correctness**: a wrong mortality figure in a statutory return is a regulatory incident.

Here the lake's advantages are worth almost nothing — storage cost is irrelevant at 40 GB, and there is no unstructured firehose to explore. The warehouse's advantages are worth a great deal: schema-on-write rejects malformed records at the door, mature column-level access control is built in, and audit logging is a checkbox.

**The correct answer is a conventional data warehouse**, dimensionally modelled, with the lake pattern used only for one narrow case: medical imaging files, which are stored in object storage with *pointers* held in the warehouse.

The contrast is the lesson: **the right architecture follows from data volume, schema stability, variety, and governance intensity — not from what is fashionable.** BookNook has petabyte-trajectory semi-structured variety and picks a lakehouse. The trust has 40 GB of regulated, stable, structured data and picks a warehouse. Both are right.

## 8. Code or configuration example

> **Illustrative only — not production-ready.** Syntax varies by engine (this is written loosely in Spark SQL / Delta dialect); error handling, incremental logic, permissions, retries, data-quality tests, CI and backfill handling are all omitted. Treat it as a teaching artefact, not a template to deploy.

```sql
-- ============================================================
-- BOOKNOOK GOLD LAYER — star schema on a lakehouse table format
-- ============================================================

-- ---------- DIMENSION: DATE ----------
-- A date dimension is pre-generated, never derived at query time, because
-- it lets analysts group by fiscal period/holiday without date arithmetic.
CREATE TABLE gold.dim_date (
  date_key        INT     NOT NULL,   -- surrogate key as YYYYMMDD: 20260914
  full_date       DATE    NOT NULL,   -- the real date, for range predicates
  day_of_week     STRING  NOT NULL,   -- 'Monday'; precomputed to avoid per-query formatting
  is_weekend      BOOLEAN NOT NULL,   -- cheap boolean beats repeated CASE logic in BI tools
  month_num       INT     NOT NULL,   -- 1..12
  month_name      STRING  NOT NULL,   -- 'September'
  quarter         INT     NOT NULL,   -- 1..4 calendar quarter
  fiscal_year     INT     NOT NULL,   -- BookNook's FY starts in April, so this != calendar year
  is_promo_day    BOOLEAN NOT NULL    -- business flag; analysts filter on it constantly
) USING DELTA;                        -- Delta = the table format giving us ACID + time travel

-- ---------- DIMENSION: BOOK (Type 2 slowly changing) ----------
CREATE TABLE gold.dim_book (
  book_key        BIGINT  NOT NULL,   -- SURROGATE key: one row per VERSION of a book
  isbn            STRING  NOT NULL,   -- NATURAL key from the source system; NOT unique here,
                                      -- because one ISBN has many Type-2 versions over time
  title           STRING,             -- Type 1 attribute: corrections overwrite in place
  author_name     STRING,             -- denormalised on purpose: avoids a join, compresses well
  publisher_name  STRING,             -- deliberately NOT snowflaked into dim_publisher
  publisher_country STRING,           -- repeats 'UK' thousands of times; dictionary encoding
                                      -- makes that repetition almost free on disk
  category        STRING,             -- Type 2: history must be preserved for old reports
  previous_category STRING,           -- Type 3: exactly one level of history, by request
  list_price_gbp  DECIMAL(10,2),      -- Type 2: price at the time, not price today
  valid_from      TIMESTAMP NOT NULL, -- Type 2 window start (inclusive)
  valid_to        TIMESTAMP,          -- Type 2 window end (exclusive); NULL = still current
  is_current      BOOLEAN NOT NULL    -- redundant with valid_to, but makes BI filters trivial
) USING DELTA;

-- ---------- DIMENSION: CUSTOMER ----------
CREATE TABLE gold.dim_customer (
  customer_key    BIGINT  NOT NULL,   -- surrogate key, one row per customer version
  customer_id     BIGINT  NOT NULL,   -- natural key from PostgreSQL
  signup_date     DATE,               -- never changes, so Type 1 is safe
  city            STRING,             -- Type 2: a customer who moved must not rewrite history
  country         STRING,
  segment         STRING,             -- 'new' | 'returning' | 'vip'; recomputed nightly
  valid_from      TIMESTAMP NOT NULL,
  valid_to        TIMESTAMP,
  is_current      BOOLEAN NOT NULL
) USING DELTA;

-- ---------- FACT: ORDER LINE ----------
-- GRAIN (declared in English before any column was chosen):
--   "One row = one book line on one CONFIRMED order, as at confirmation time."
-- Cancellations/refunds go to a SEPARATE adjustment fact, never by mutating this row,
-- so that re-running last month's report always returns last month's number.
CREATE TABLE gold.fact_order_line (
  order_line_id   BIGINT  NOT NULL,   -- degenerate dimension: source key kept for drill-back
  order_id        BIGINT  NOT NULL,   -- degenerate dimension: groups lines into an order
  date_key        INT     NOT NULL,   -- FK -> dim_date
  book_key        BIGINT  NOT NULL,   -- FK -> dim_book: points at the version live AT ORDER TIME
  customer_key    BIGINT  NOT NULL,   -- FK -> dim_customer, same "as at" rule
  channel_key     INT     NOT NULL,   -- FK -> dim_channel (web, iOS, Android, partner)
  quantity        INT     NOT NULL,   -- MEASURE: fully additive across every dimension
  unit_price_gbp  DECIMAL(10,2),      -- MEASURE: NON-additive (never SUM a price); ratio only
  discount_gbp    DECIMAL(10,2),      -- MEASURE: additive
  line_amount_gbp DECIMAL(12,2),      -- MEASURE: additive; stored, not derived, so every tool
                                      -- computes revenue identically (one definition, one number)
  order_ts        TIMESTAMP NOT NULL  -- full timestamp; date_key alone loses time-of-day analysis
) USING DELTA
PARTITIONED BY (date_key);            -- partition on the column ~every query filters on.
                                      -- ~95k rows/day => ~1 small file/day. For a LARGER table
                                      -- you would partition monthly and SORT by date inside,
                                      -- to avoid the small-files problem (see section 4.7).

-- ---------- TYPE 2 SCD MAINTENANCE ----------
-- Step 1: close out rows whose TRACKED attributes changed in the source.
MERGE INTO gold.dim_book AS tgt
USING silver.books       AS src
  ON  tgt.isbn = src.isbn
  AND tgt.is_current = TRUE          -- only ever compare against the live version
WHEN MATCHED AND (                   -- an explicit list: ONLY these trigger a new version.
      tgt.category       <> src.category       -- changing title (Type 1) must NOT create one
   OR tgt.list_price_gbp <> src.list_price_gbp
   OR tgt.publisher_name <> src.publisher_name
) THEN UPDATE SET
      tgt.valid_to   = src.changed_at,        -- old version's window ends at the change instant
      tgt.is_current = FALSE;                 -- expired, but retained for historical facts

-- Step 2: insert the new current version for anything now unmatched.
INSERT INTO gold.dim_book
SELECT
  hash(src.isbn, src.changed_at) AS book_key, -- illustrative surrogate key; production uses a
                                              -- sequence/identity column to avoid hash collisions
  src.isbn, src.title, src.author_name, src.publisher_name, src.publisher_country,
  src.category,
  tgt.category                   AS previous_category,  -- Type 3: carry the prior value forward
  src.list_price_gbp,
  src.changed_at                 AS valid_from,
  CAST(NULL AS TIMESTAMP)        AS valid_to,           -- NULL = open-ended = current
  TRUE                           AS is_current
FROM silver.books src
LEFT JOIN gold.dim_book tgt
  ON tgt.isbn = src.isbn AND tgt.is_current = TRUE
WHERE tgt.isbn IS NULL                                  -- brand new book
   OR tgt.category <> src.category                      -- or a tracked attribute changed
   OR tgt.list_price_gbp <> src.list_price_gbp;

-- ---------- FACT LOAD: resolve keys "AS AT" event time ----------
INSERT INTO gold.fact_order_line
SELECT
  ol.order_line_id,
  ol.order_id,
  CAST(date_format(ol.order_ts, 'yyyyMMdd') AS INT) AS date_key,
  b.book_key,          -- the version that was live when the order happened...
  c.customer_key,
  ch.channel_key,
  ol.quantity,
  ol.unit_price_gbp,
  ol.discount_gbp,
  ol.quantity * ol.unit_price_gbp - ol.discount_gbp AS line_amount_gbp,
  ol.order_ts
FROM silver.order_lines ol
JOIN gold.dim_book b
  ON  b.isbn = ol.isbn
  AND ol.order_ts >= b.valid_from                       -- ...selected by the Type 2 window,
  AND (ol.order_ts <  b.valid_to OR b.valid_to IS NULL) -- NOT by is_current = TRUE.
                                                        -- Using is_current here is THE classic
                                                        -- Type 2 bug: it silently rewrites history.
JOIN gold.dim_customer c
  ON  c.customer_id = ol.customer_id
  AND ol.order_ts >= c.valid_from
  AND (ol.order_ts <  c.valid_to OR c.valid_to IS NULL)
JOIN gold.dim_channel ch
  ON  ch.channel_code = ol.channel_code
WHERE ol.order_ts >= :window_start                      -- incremental: never full-scan the source
  AND ol.order_ts <  :window_end;

-- ---------- A TYPICAL ANALYTICAL QUERY ----------
SELECT
  d.fiscal_year,
  d.month_name,
  b.category,                                   -- the category AS IT WAS, thanks to Type 2
  SUM(f.line_amount_gbp) AS revenue_gbp,        -- additive measure: SUM is valid
  SUM(f.quantity)        AS units,
  COUNT(DISTINCT f.order_id) AS orders
FROM gold.fact_order_line f
JOIN gold.dim_date d ON d.date_key = f.date_key
JOIN gold.dim_book b ON b.book_key = f.book_key -- joins the exact version, no time logic needed
WHERE f.date_key BETWEEN 20260401 AND 20260930  -- partition pruning: skips all other partitions
GROUP BY d.fiscal_year, d.month_name, b.category
ORDER BY revenue_gbp DESC;
-- Columns referenced: 6 of 11 in the fact. Columnar storage reads only those 6.

-- ---------- LAKEHOUSE OPERATIONS THE PLAIN LAKE CANNOT DO ----------
SELECT SUM(line_amount_gbp) FROM gold.fact_order_line
  VERSION AS OF 118;                 -- TIME TRAVEL: exactly what the table said at commit 118

DESCRIBE HISTORY gold.fact_order_line;  -- audit: who wrote what, when, with which operation

ALTER TABLE gold.fact_order_line
  ADD COLUMN gift_wrap_gbp DECIMAL(10,2);  -- SCHEMA EVOLUTION: metadata-only, milliseconds.
                                           -- Existing files are untouched; the column reads
                                           -- as NULL for rows written before the change.

DELETE FROM gold.fact_order_line
  WHERE customer_key IN (SELECT customer_key FROM gold.dim_customer
                         WHERE customer_id = 88213);
  -- GDPR erasure. Writes delete files + one atomic commit. In a bare lake this would mean
  -- locating and rewriting every candidate file with no atomicity and no proof of completion.

OPTIMIZE gold.fact_order_line
  WHERE date_key >= 20260901
  ZORDER BY (book_key);              -- COMPACTION: many small files -> few ~512MB files,
                                     -- co-locating similar book_key values so that Parquet
                                     -- min/max statistics can actually skip row groups.

VACUUM gold.fact_order_line RETAIN 168 HOURS;
  -- Physically removes files no longer referenced AND older than the retention window.
  -- Trade-off stated plainly: this is what BUYS BACK storage and what DESTROYS the ability
  -- to time-travel beyond 7 days. Setting it too low breaks in-flight long readers.
```


---

# Lesson 59 — CAP Theorem

*Phase 3, Topic 20*

## 1. Simple definition

**CAP theorem**: a distributed data store cannot simultaneously guarantee all three of the following properties.

- **C — Consistency**: every read observes the most recent write, or an error. Formally, **linearizability**: the system behaves as if there were a single copy of the data and every operation took effect atomically at some instant between its invocation and its response.
- **A — Availability**: every request that reaches a non-failed node receives a **non-error** response. Note what is missing: there is **no time bound**. A node that takes four hours to answer is "available" under CAP.
- **P — Partition tolerance**: the system keeps working when the network arbitrarily drops messages between nodes.

The theorem, proved by Gilbert and Lynch in 2002 after Brewer's 2000 conjecture, is that **in the presence of a partition you must sacrifice either consistency or availability**. It is not a menu of three where you pick two like toppings.

The reason "pick two of three" is the wrong mental model: **P is not a choice you make, it is a property of reality.** Packets get dropped. Switches reboot. A GC pause looks like a partition to the other side. A cross-region link fails at 03:00 on a Saturday. You cannot choose "CA"; you can only choose what your system does *when* P happens. So the honest statement of CAP is:

> **When a partition occurs, choose: refuse to answer (CP), or answer with possibly-stale data (AP).**

And the second half of the modern understanding, which matters more in practice (Lesson 60): **partitions are rare; the trade-off you make every millisecond of every normal day is latency versus consistency.** CAP only describes the rare half.

The one sentence to carry out of this lesson: **CAP is not a label you stick on a database, it is a decision you make per operation, per failure, about what a user sees when two machines disagree.**

## 2. Why it exists

Phases 1–3 built up a stack of statements that quietly contradicted each other, and CAP is the theorem that forces you to pick:

- Lesson 32 taught replication: the same data on multiple machines.
- Lesson 33 taught the trade-off: synchronous replication is slow but consistent; asynchronous is fast but a replica can be behind.
- Lesson 34 taught replication lag: a user can update their profile and not see the change.
- Lesson 55 taught that a read must return *a* value; it never asked *which* value.

Now add a network fault. BookNook's `inventory` table is replicated to three nodes: `eu-west-1a`, `eu-west-1b`, `eu-west-1c`. A routing change partitions `1a` from `1b`/`1c`. A customer is on a connection routed to `1a` and buys the last copy of a book. Moments later another customer is routed to `1b` and tries to buy the same book.

There is no clever engineering that resolves this. Only three possible behaviours:

1. `1a` **refuses the write** because it cannot reach a majority. The first customer sees an error. **Consistent, unavailable.**
2. `1a` **accepts the write locally** and reconciles later. Both customers are told they bought the last copy. **Available, inconsistent.**
3. `1a` **waits** for the partition to heal before responding. From the user's perspective this is indistinguishable from (1) with a longer timeout — and CAP explicitly does not count waiting as availability.

Every "distributed database" marketing page is describing which of these three it does, and under what conditions. The vendor's real answer is usually "1 for this operation, 2 for that one, and here is the knob". CAP is the vocabulary for reading those pages honestly.

The reason the theorem exists at all is that the twentieth-century default was **a single node**, where all three properties were free: one machine is trivially consistent, available and "partition tolerant" (it has no network to partition internally). Scaling out — Phase 2's whole subject — buys throughput and fault tolerance with money borrowed against exactly these properties. CAP is the term sheet.

## 3. Real-world analogy

Two branches of a library that share a card catalogue, connected by a phone line.

A member at the North branch borrows the last copy of a book. The North librarian must update the catalogue: mark it out. The South branch must know. Six options exist in reality, but only three classes:

- **Phone-first**: North calls South and only completes the loan once South confirms its copy is updated. If the line is down, **North cannot lend the book at all**. Correct, and the member is turned away during an outage. (CP.)
- **Local-first**: North marks its own catalogue and the member walks out with the book. If the line is down, South keeps lending the same copy to someone else, and the discrepancy is discovered when the line returns. Someone must decide who loses their book — or the library buys a second copy. (AP.)
- **Wait-for-the-line**: North stands at the desk with the member until the line comes back. Nobody is served, but nothing is wrong. (Neither, with a bad user experience.)

Now the observation that makes CAP practical rather than philosophical: **most of the time the line is up, and then phone-first costs almost nothing** — a few seconds at the desk. The interesting engineering question is not "which of the three are we?", it is **"how long is the phone call, and what happens to the member while it happens?"** That is PACELC (Lesson 60), and it is the question that decides whether a real system feels fast.

And the final twist, which is the numeric heart of the matter: **a librarian cannot tell a slow phone line from a broken one.** If North waits 30 seconds and hears nothing, has South crashed, or is just busy? The decision to declare "partition" is a *timeout*, and a timeout is a guess. Every CAP decision in a real system is therefore implemented as a timeout, which means every CAP decision is occasionally wrong in the direction of the configured threshold (Lesson 16 on tail latency).

## 4. Detailed technical explanation

### 4.1 The precise statement, and what each letter excludes

| Letter | Common misreading | Actual definition | What it is *not* |
|---|---|---|---|
| **C** | "ACID's C — constraints hold" | **Linearizability**: one logical copy, reads see the latest write | Not serializability (that is about transactions, multi-object), not "no constraint violations" |
| **A** | "Fast, no downtime, meets SLO" | Every non-failed node returns a **non-error** response. **No latency bound** | Not a 99.99 % SLO, not p99 latency, not "always up" |
| **P** | "We chose to be partition tolerant" | Arbitrary message loss between nodes is possible and the system continues operating | Not optional. Not a feature. It is the weather |

Gilbert and Lynch's proof is small and worth understanding, because it also tells you the escape hatches. Model the system as two nodes, `G1` and `G2`, with a partition dropping all messages between them. A client writes `v1` to `G1` (it must succeed, or A is violated). A client then reads from `G2`. If `G2` responds, it cannot know about `v1` — no message can cross — so it returns something older, violating C. If `G2` refuses, A is violated. **QED.** Every escape hatch is an attack on one of the proof's assumptions:

- **Make the client aware** → the client remembers `v1` and *sends* it with the read, or refuses to read from `G2`. This is what a "sticky session" or client-side monotonic read (Lesson 61) does.
- **Make the partition impossible** (Spanner's TrueTime + synchronised clocks) → shrinks the window but does not remove it; Spanner still becomes unavailable during a partition, it just wins because GPS clocks make "partition" detectable as clock uncertainty.
- **Make both writers agree on an order** (consensus, Lesson 81) → you still lose availability on the minority side, but you never lose consistency; this is the CP implementation.

### 4.2 The three behaviours, concretely, in a 3-replica system

Replication with `N = 3` replicas, and the classic knob: require a **quorum** of `W` acks to write and `R` acks to read (Lesson 80).

| Setting | Write path | Read path | Partition behaviour | CAP label | Example systems |
|---|---|---|---|---|---|
| `W=3, R=1` | Wait for all 3 | Any 1 | Minority **cannot** write; reads may be stale-ish but consistent with the last acked write | **CP** (write side) | Synchronous PostgreSQL replicas |
| `W=2, R=2` | Majority | Majority | Minority side (1 node) refuses both; majority continues. `W+R=4 > N=3` guarantees a read overlaps the latest write | **CP**, classic quorum | etcd, Consul, HBase, Cassandra at QUORUM |
| `W=1, R=1` | Any single node, immediately | Any single node, immediately | Both sides accept writes independently; conflict resolution on heal | **AP** | Cassandra at ONE, DynamoDB eventually-consistent reads, Riak |
| `W=1, R=3` | Fast writes | Slow, all-replica reads | Reads can be stale if a replica is behind, but `R=3` reads all and picks the newest | AP-ish with read repair | Cassandra at ONE/ALL |

The inequality `W + R > N` is the whole of quorum consistency, and it is a CAP decision expressed as arithmetic: it forces at least one replica in the read set to have seen the write, so a successful read cannot return a value older than a successful write. Lower `W` and you gain write availability during a partition, at the cost of possible conflict on heal.

### 4.3 What a partition actually does to requests

The part that surprises people: a partition does not have to be a network split. Anything that makes a node unreachable for longer than a timeout is functionally a partition:

```
 Cause                              │ Looks identical to the other side as
 ────────────────────────────────────┼──────────────────────────────────────
 cable/switch failure               │ no response within the timeout
 BGP route flap                     │ no response within the timeout
 kernel dropping SYNs under load    │ no response within the timeout
 a 40-second JVM GC pause           │ no response within the timeout
 a hypervisor live-migration freeze │ no response within the timeout
 the process being CPU-starved      │ no response within the timeout
 a full TCP accept queue            │ no response within the timeout
 ────────────────────────────────────┴──────────────────────────────────────
```

The last four are the dangerous ones, because they are *not* network faults, they correlate with load, and no monitoring dashboard labels them "partition". A GC pause long enough to trip the failure detector is a partition from the leader's point of view (Lesson 79), and the resulting unnecessary leader election is a well-documented cause of write unavailability in healthy clusters.

### 4.4 Yields and harvests — the softer, more useful framing

Brewer's earlier (1999, Harvest/Yield) framing is often more practical than CP/AP because it describes a *continuous* choice rather than a binary one:

- **Yield** = fraction of requests answered successfully. (Availability.)
- **Harvest** = fraction of the *complete* data reflected in an answer. (Consistency, measured.)

A search over BookNook's catalogue with a partition can answer **100 % of queries with 60 % of the index** (high yield, partial harvest — "some results may be missing"), or answer **60 % of queries with 100 % of the index** (full harvest, lower yield). Real systems choose per query: a product page tolerates partial harvest; an inventory decrement does not. This is the same insight as "per-operation CAP", expressed in numbers you can put on a dashboard.

### 4.5 Why "CA" is a category error (and when it isn't)

You will see diagrams offering "CA databases". The label is only meaningful if you add "on a single node, or on a network you have proven cannot partition". A single PostgreSQL primary is effectively CA — because it is one node, and there is no *between* to partition. The moment you add a replica, you are in CP-or-AP territory and the "CA" label becomes a claim about your network, not your database.

Two legitimate uses of the term:

- **A single-node deployment** ("BookNook on one box, with backups") is CA, and that is not a criticism — it is the correct architecture for the vast majority of systems (Lesson 86).
- **A cluster with a shared-nothing assumption violated** — e.g. nodes on the same switch with redundant power — is CA *up to the reliability of that infrastructure*. Still a claim, still needs a stated fault model.

The test to apply to any "CA" claim: **what does it do when node 2 cannot reach node 1 for 60 seconds?** If the answer is "that never happens", that is an assumption, not a property.

### 4.6 The two-meanings-of-consistency trap

This trips up more interviews than anything else in the phase:

| Term | Layer | Means |
|---|---|---|
| **ACID C** (Lesson 49) | Single database, single transaction | Constraints and invariants hold: no orphan rows, no negative stock, FKs valid |
| **CAP C** | Distributed system, single object | **Linearizability**: reads see the latest completed write |
| **Consistency models** (Lesson 61) | Distributed system, sessions | Weaker guarantees: causal, read-your-writes, monotonic reads, eventual |

A system can be **ACID-consistent and CAP-inconsistent** — Cassandra with lightweight transactions is exactly this: per-row invariants hold, but reads can return stale data. Conversely, a system can be linearizable for one object and not another. When an interviewer says "consistent", always ask **which of the three**, and at what granularity.

## 5. Request / data flow

A partition, minute by minute, from BookNook's perspective. Three inventory replicas in `eu-west-1`; a network policy change isolates `1a`.

```
T+0:00  ── Nominal. Leader = 1a (Raft term 7). Writes go 1a → 1b,1c (majority).
           Replication lag p99 = 4 ms. Everything is fine. Nobody is watching.

T+0:00  ── Partition begins (1a unreachable from 1b/1c, and vice versa).

T+0:00  Request A: "decrement stock of ISBN 978-… from 3 to 2"
           → routed by the load balancer to the LEADER 1a
           → 1a appends to its log, tries to replicate to 1b,1c
           → no response. It cannot commit (needs a majority of 3).
           → after the 500 ms election/ack timeout: REJECTS the write.
           → Customer sees "please try again" — a 503.
           → 1a steps down from leadership (it can no longer prove it is leader).

T+0:01  ── 1b and 1c elect a new leader (1b, term 8) after their own timeout.
           They are a majority (2 of 3) and they continue serving.

T+0:02  Request B: same ISBN, different customer, routed to 1b
           → 1b is now leader, stock is 3 (1a's write never committed)
           → accepted, committed to 1b+1c → stock = 2. Answered OK.

T+0:03  Request C: a READ of stock for that ISBN, routed to 1a
           → 1a still believes it is a follower in term 7 with stock = 3
           → a CP system REJECTS the read (cannot confirm leadership freshness)
           → an AP system would answer "3", which is stale.

T+0:30  ── Partition heals.
           → 1a rejoins, learns of term 8, truncates its uncommitted log entry.
           → Synchronises. Stock = 2. All three replicas agree again.
           → Total user impact: ~40 requests in 30 s got 503s on 1a.
             ZERO double-sells. Availability cost, consistency preserved.

T+0:30  ── The alarm fires. Incident review asks the CAP question:
           "we chose CP for 30 seconds; our SLO budget for the month was
            26 minutes; we spent 30 seconds of it; the choice was correct."
```

Change one design decision — set `W = 1` so a lone node can accept writes — and the same timeline produces **two customers told they bought the last copy**, at `1a` and `1b`, with the conflict resolved (arbitrarily, usually last-write-wins) on heal. That is the AP branch: 100 % availability during the partition, and a business problem afterwards. **Neither is wrong in general; one is wrong for inventory.**

## 6. ASCII diagram

The Gilbert–Lynch scenario, drawn:

```
              ┌──────────── PARTITION (all packets dropped) ────────────┐
              ╳                                                          ╳
      ┌───────┴────────┐                                    ┌────────────┴───────┐
      │   Node G1      │                                    │   Node G2          │
      │  stock = 3     │                                    │  stock = 3         │
      └───────┬────────┘                                    └────────────┬───────┘
              │                                                          │
      ┌───────┴────────┐                                    ┌────────────┴───────┐
      │ Client 1       │                                    │ Client 2           │
      │ buy last copy  │                                    │ buy last copy      │
      └────────────────┘                                    └────────────────────┘

  MUST choose one of these three outcomes for Client 2:

  (1) CP ─ refuse   ─→ "503, try again"          0 double-sells, 1 lost sale
  (2) AP ─ accept   ─→ "order confirmed"         1 double-sell, 0 lost sale
  (3) wait          ─→ hang until heal           both users time out;
                                                   CAP counts this as (1)
```

The choice matrix as a decision tree you can actually apply:

```
             Does this operation tolerate a stale read?
                        │
          ┌─────────────┴──────────────┐
         YES                           NO
          │                             │
     Can the user be told             Is a single authoritative
     "some data is missing"?          owner for this item possible?
          │                             │
    ┌─────┴─────┐                 ┌─────┴──────┐
   YES         NO                YES          NO
    │           │                 │            │
  AP with     AP with          CP: route     You need a
  partial     last-write-wins  all writes    coordination
  harvest     (product views,  to the owner  service
  (search,    (profile bio,    (inventory,   (consensus,
  reviews,    likes, presence) seats, money)  Lesson 81)
  catalogue)                                  or a business
                                              process that
                                              tolerates conflict
```

Notice the right-hand branch: **CP is not "use a CP database", it is "one owner, and everyone else asks it"**. Most CP systems in production are a single-writer pattern (a primary, a shard leader, a partitioned Kafka topic) rather than a quorum protocol.

## 7. Detailed example

### 7.1 BookNook's split — the same system, four different CAP decisions

BookNook runs one primary database and a handful of specialised stores. It is not "a CP system" or "an AP system"; it makes four different decisions:

| Feature | Store & setting | CAP choice during partition | Why |
|---|---|---|---|
| **Stock decrement at checkout** | PostgreSQL, synchronous standby + quorum read | **CP** — refuse rather than double-sell | A double-sell is a refund, an angry email, and a support ticket. A 503 is a retry. The asymmetry is enormous. |
| **Product page render** | Redis cache-aside over replicas, 60 s TTL | **AP** — serve possibly-stale price/description | A 60-second-stale price is invisible to 99.9 % of users; a broken product page is a lost sale. |
| **Customer reviews** | Cassandra-ish, `W=1, R=1`, LWW | **AP** — accept both, reconcile later | Two reviews from the same person during a partition is a curiosity, not a corruption. |
| **Password change / session revocation** | Primary only, no replica reads, fail closed | **CP** — reject the login | Serving a revoked-token session is a security incident; failing a login is an inconvenience. |

The transferable pattern: **the CAP decision is a property of the *operation's business risk*, not of the database vendor.** Same cluster, four answers. Any architecture diagram that labels the whole system "CP" is hiding the only interesting information in it.

### 7.2 The 30-second partition that used the month's error budget

Numbers, because this is where CAP becomes an operational decision rather than a philosophical one.

- BookNook's SLO: **99.95 %** of checkout requests succeed, measured monthly.
- Monthly error budget: `30 days × 24 h × 60 min × 0.0005 = 21.6 minutes`.
- A 30-second partition on the checkout path (CP branch: refuse) consumes `0.5 min / 21.6 min = 2.3 %` of the budget. Fine. Two of those a week is `~4.4 min/month = 20 %` of the budget — still acceptable, and now you know *why* the CP choice is affordable.
- A 30-**minute** partition consumes **139 %** of the entire month's budget in one event. That is not a CAP problem, that is a topology problem: it means a single switch failure or a single AZ link can isolate a majority, and the fix is to spread replicas across failure domains (Lesson 39) so that "a majority" survives any single failure.

The arithmetic you should say out loud in an interview: **CP is affordable exactly when partitions are shorter than your error budget divided by your expected partition frequency.** When that inequality fails, you either reduce partition duration (topology, faster failure detection — but faster detection risks false positives, Lesson 79) or you move that operation to AP and handle the conflict in business logic.

### 7.3 The Spanner exception, briefly

Google Spanner is the standard counter-example thrown at CAP: it claims external consistency (stronger than linearizability) *and* high availability. It does this by attacking the proof's assumption that nodes cannot know the ordering of events — with **TrueTime**, GPS and atomic clocks that bound clock uncertainty to a few milliseconds. A write waits out the uncertainty (`commit-wait`), typically **7 ms**, which is a latency cost paid on every write, in normal operation, forever.

This is worth understanding precisely because it does not refute CAP — it *illustrates PACELC*: Spanner chose **PC/EC** (consistent, and pays latency even with no partition). During an actual partition, Spanner still becomes unavailable on the minority side, exactly as CAP requires. The lesson: **you can buy your way out of a consistency problem with hardware and latency, but not out of CAP.**

## 8. Code or configuration example

```sql
-- ============================================================================
-- BookNook: the CAP decision, expressed as configuration. Illustrative.
-- Each block is ONE decision, taken from §7.1. The comment states what
-- happens during a partition — that is the only thing that matters.
-- ============================================================================

-- --- 1. CHECKOUT: CP. Never double-sell, even at the cost of an outage. ------
-- PostgreSQL synchronous replication with a quorum of standbys.
-- postgresql.conf on the primary:
--   synchronous_standby_names = 'ANY 1 (eu-west-1b, eu-west-1c)'
--   synchronous_commit        = 'on'          -- default; see Lesson 49 §durability
--   statement_timeout         = '2s'
-- Behaviour during a partition: if no standby in the set is reachable, COMMIT
-- BLOCKS and then fails. Writes rejected. That is the intended CP behaviour:
--   - the replica in the OTHER AZ must ack before we tell the customer "yes".
--   - RPO = 0 for acknowledged commits (Lesson 63).
-- The cost is exposed write latency: p99 goes from 3 ms local to ~6 ms
-- cross-AZ. That is the price of CP, paid on every single order, forever.

BEGIN;
  UPDATE inventory
     SET reserved = reserved + 1
   WHERE isbn = '978-0-00-000000-0'
     AND reserved < stock_on_hand        -- the invariant, enforced in the WHERE
  RETURNING reserved;
  -- Zero rows returned = sold out. Not an error: a legitimate business answer.
  -- Row-level locking means the second concurrent buyer blocks, then re-reads
  -- and finds reserved = stock_on_hand. Correctness from the database, not
  -- from a distributed lock (Lesson 78).
COMMIT;


-- --- 2. PRODUCT PAGE: AP. A stale price beats a blank page. -------------------
-- App-level cache-aside against read replicas. In the client:
--   GET product:{isbn}          -> Redis, TTL 60s
--   miss -> replica (may lag)   -> tolerate up to 60s staleness
--   on replica error            -> serve the last cached value, with a
--                                  "prices may have changed" banner
-- Behaviour during a partition: the read path NEVER 503s while any cache or
-- replica responds. Staleness bound is explicit: 60 s TTL + replication lag.
-- If the price is legally binding at checkout, the checkout path re-reads it
-- from the primary inside the transaction — the CAP decision is per operation.

-- Cassandra equivalent for the reviews table (AP, tunable):
--   CREATE TABLE reviews_by_book (... PRIMARY KEY (isbn, review_id))
--     WITH replication = {'class':'NetworkTopologyStrategy',
--                         'eu-west-1': 3, 'us-east-1': 3};
--   -- Write: consistency ONE   -> survives losing 2 of 3 local replicas
--   -- Read : consistency QUORUM -> may re-join an unreplicated write later
--   -- Conflict resolution: last-write-wins on a server-side timestamp.
--   --   This is an explicit decision that two concurrent edits to the SAME
--   --   review will silently lose one. Acceptable for reviews. NOT for stock.


-- --- 3. QUORUM ARITHMETIC AS A GUARD, not a comment --------------------------
-- The invariant W + R > N is what makes a quorum read consistent. Assert it
-- in application startup code so a config change cannot silently break it:
SELECT CASE
         WHEN 2 + 2 > 3 THEN 'ok: read quorum overlaps write quorum'
         ELSE 'BROKEN: stale reads possible (AP, not CP)'
       END AS quorum_check;
-- Change W to 1 and this starts returning 'BROKEN' — which is exactly the
-- moment someone must consciously accept AP for that table.


-- --- 4. THE DECISION MUST OUTLIVE THE MEETING ---------------------------------
-- Put it in the schema, where a future engineer will trip over it.
COMMENT ON TABLE inventory IS
  'CAP: CP during partitions. Writes require a synchronous standby in a
   different AZ; the API returns 503 rather than risk a double-sell.
   Owner: checkout team. Reviewed: 2026-10. See ADR-014.';
COMMENT ON TABLE reviews_by_book IS
  'CAP: AP. LWW on server timestamp. Duplicate/lost concurrent edits to the
   same review are accepted; the page tolerates eventual convergence.';
```

## 9. Numerical example

**Step 1 — the availability you actually get from a CP choice.**

Three replicas across three AZs, `W=2`. A partition isolates one AZ (the common case):

```
Reachable replicas: 2 of 3  →  2 >= W=2  →  MAJORITY SURVIVES
Writes continue. Reads continue. User-visible impact: ZERO.
```

A partition isolates two AZs (rare — usually a regional control-plane fault):

```
Reachable replicas: 1 of 3  →  1 < W=2  →  NO MAJORITY
All writes rejected for the duration. This is the CP outage window.
```

**Step 2 — putting a number on the risk.** AZ-level partition probability, from typical cloud incident data, is on the order of **1 event per AZ-pair per 2–3 years**, lasting **5–45 minutes**. Say 0.4 events/year and 20 minutes each:

```
Expected unavailable minutes per year (single-AZ isolation, survived): 0
Expected unavailable minutes per year (dual-AZ isolation):  ~0.02 events/yr × 20 min ≈ 0.4 min/yr
Availability attributable to partitions ≈ 1 - 0.4 / 525,600 ≈ 99.9999 %
```

Which tells you something important: **on a well-designed topology, CAP-driven unavailability is not what determines your availability.** Your 99.95 % SLO will be consumed by deploys, bad queries, dependency latency and traffic spikes (Phase 4, Lesson 89) long before by partitions. This is the quantitative case for the phase-4 ordering: partitions get the theory, bad deploys get the on-call rota.

**Step 3 — the price of CP on every request.** Checkout write to a local primary: **3 ms**. With `synchronous_standby_names = 'ANY 1 (...)'` in another AZ, add one cross-AZ round trip: **+1.5 ms**. So:

```
p50 checkout write:  3 ms  →  4.5 ms
p99 (cross-AZ tail): 9 ms  → 14 ms
```

At 40 checkout writes/sec, that is `40 × 1.5 ms = 60 ms` of extra wait per second — invisible. At 4,000 writes/sec it is `6 seconds of wait per second` spread across in-flight requests, and the *concurrency* needed to sustain throughput rises by 1.5 ms's worth per request (Little's Law, Lesson 14): `4,000 × 0.014 = 56 in-flight` versus `4,000 × 0.009 = 36`. **CP is nearly free at low write rates and expensive at high ones** — which is precisely why write-heavy systems drift toward AP or toward partition-tolerant-but-asynchronous designs (Lesson 33).

**Step 4 — the cost of getting it wrong.** Suppose BookNook had chosen AP for inventory (`W=1`). During the same 30-second partition, both sides accept writes. Modelled oversell rate: 40 writes/sec × 30 s = 1,200 accepted writes, of which the conflicting fraction is the fraction of orders for **the same ISBN at low stock**. BookNook's long tail means ~3 % of orders hit a title with <5 copies remaining, and ~10 % of those concurrently during the window:

```
conflicts ≈ 1,200 × 0.03 × 0.10 ≈ 3.6 double-sells per 30-second partition
```

Four customers told "you bought the last copy" out of a book that never existed. Each resolves as a refund + apology + support cost, and the trust damage is disproportionate to the number. **Three point six double-sells per event is the entire argument for paying 1.5 ms on every write.**

## 10. Trade-offs

| Choice | Buys you | Costs you | Use it for |
|---|---|---|---|
| **CP — quorum/leader, refuse on minority** | No conflicting writes; no reconciliation logic; a single source of truth | 503s during partitions; extra write latency for every commit; sensitivity to false-positive failure detection | Money, inventory, seats, unique identifiers, auth |
| **AP — accept everywhere, reconcile later** | Writes never fail; lowest write latency; locality | Conflict resolution is now *your* code; some conflicts are unresolvable; users can see stale data | Reviews, likes, presence, carts, view counts, telemetry |
| **AP with LWW timestamps** | Trivial to implement | Silently discards the "loser" of a conflict — including real user edits; clock skew makes "last" arbitrary (Lesson 66) | Data where one writer dominates or loss is acceptable |
| **CP with a single owner per shard** | Simple mental model; strong consistency; cheap in the normal case | Ownership failover is the hard part (Lesson 79); hot shard = hot owner | Per-entity ordering (orders per customer, queue per partition) |
| **Tunable per operation** | You pay for exactly what you need | Configuration surface is a foot-gun; `W+R>N` can be broken by a well-meaning tweak | Most real systems — but only with the invariant asserted in code |
| **Avoid the problem (idempotent, commutative operations)** | No conflict at all: order of application does not matter | Requires careful data modelling (counters, CRDTs); not always possible | Counters, sets, append-only logs, "add to cart" |
| **Choose CA (single node)** | The strongest properties, cheapest | No fault tolerance beyond one machine; a hard limit on scale | Anything under ~10k writes/sec with a good backup story |

## 11. Failure scenarios

**11.1 — Assuming partitions do not happen because "we're in one region".** A single-AZ deployment with a synchronous replica in the same rack: the top-of-rack switch fails and both "replicas" vanish together. The fault model was never written down, so nobody noticed the replicas shared a failure domain. **Symptom:** a total outage from a component you believed was redundant. **Fix:** name your failure domains explicitly (host, rack, AZ, region) and require each replica to differ from its peers in the highest domain you claim to survive.

**11.2 — CP everywhere, including where nobody cares.** A team makes the session cache a consensus-replicated store. Now every page view depends on a quorum round trip, and a partition logs users out. **Symptom:** availability that is strictly worse than the SLO for no user-visible benefit. **Fix:** the §4.4 harvest/yield question per operation. Sessions are AP: a sticky cookie and a cache are enough.

**11.3 — AP under a business invariant.** "We'll reconcile stock later" works for page views and fails for inventory. Discovered only when the reconciliation job writes a negative stock row and the CHECK constraint rejects it at 02:00. **Symptom:** constraint violations surfacing days after the partition, from a job nobody associates with availability. **Fix:** classify operations by *whether a conflict is resolvable* before choosing AP; if the answer is "no", you needed CP or a compensating business process (Lesson 84).

**11.4 — The false-positive partition.** A 12-second GC pause trips the 10-second failure detector. The leader is deposed, a new leader is elected, and the old leader returns — believing it still leads — and accepts writes. Without **fencing tokens** or term numbers enforced at the storage layer, both write. **Symptom:** split-brain writes with no network fault ever recorded. **Fix:** monotonic fencing tokens checked by the resource being protected (Lesson 78), not by the lock service.

**11.5 — Clock skew corrupting the LWW resolution.** Two AP writes resolved by "latest timestamp wins" pick the wrong winner because node A's clock is 900 ms ahead. The user's edit disappears and reappears as the old value. **Symptom:** "it randomly reverts my changes". **Fix:** do not use wall clocks for ordering (Lesson 66); use logical versions, or don't resolve — merge (CRDTs).

**11.6 — Partition durations nobody measured.** The incident review says "the partition lasted 4 minutes" because that is when alerts fired. The actual user impact started when the first request timed out, which was 40 s earlier. **Symptom:** SLO breaches that do not reconcile with incident timelines. **Fix:** measure at the **request** level (checkout success rate by minute), not the infrastructure level.

**11.7 — Reading CAP as a per-vendor label.** An architecture review concludes "we're a Cassandra shop, so we're AP" and the inventory table gets `W=1` by inheritance. **Symptom:** the §9 step 4 double-sell. **Fix:** the decision belongs in the schema (§8.4 `COMMENT ON TABLE`), reviewed like any other invariant.

## 12. Alternatives

| Instead of reasoning about CAP | You get | When it is right |
|---|---|---|
| **PACELC (Lesson 60)** | The other 99.99 % of the time: latency vs consistency with no partition at all | Always — it is the strictly more useful framework, and it subsumes CAP |
| **Explicit consistency models (Lesson 61)** | Per-session guarantees (read-your-writes, monotonic reads) users can actually feel | Session-scoped data, UX-driven consistency |
| **Consensus (Lesson 81)** | A concrete CP implementation: a replicated log with a leader | Metadata, configuration, leader election, small critical state |
| **Quorums with `W+R>N` (Lesson 80)** | Tunable CP/AP per operation | Large data, multi-DC, latency-sensitive |
| **CRDTs / commutative operations** | Conflict-free AP: no reconciliation needed | Counters, sets, presence, collaborative editing (with limits) |
| **Idempotency keys (Lesson 17, 74)** | Makes retries safe, sidestepping the ambiguous-outcome branch of CAP | Any write that follows a timeout |
| **Single-writer per shard** | The simplest CP: one owner, everyone asks | The default for most business data |
| **Business compensation (Lesson 84)** | Turns a consistency problem into a workflow problem | Reservations, bookings, multi-step purchases |

## 13. When to use it

- **In every design interview and design doc** that includes replication, multi-region, or more than one data store. One line: "this operation is CP during partitions because X; that one is AP because Y."
- **When choosing a consistency level or replication mode** — `QUORUM` vs `ONE` is a CAP decision, and the setting should be justified, not inherited.
- **When a dependency becomes a distributed system.** Adding a replica, a cache, a second region or a message broker all introduce the C/A choice; adding it silently is the common failure.
- **When writing the incident runbook.** "What should on-call do when the database reports no quorum?" has a different answer under CP and AP, and the answer should be pre-decided.
- **When someone proposes "just make it strongly consistent"** — that is a request for latency and availability costs, and CAP is how you make them explicit.

## 14. When *not* to use it

- **As the primary framework for reasoning about latency.** CAP says nothing about the normal case. Use PACELC (Lesson 60) or you will optimise for a rare event and ship a slow product.
- **To label an entire system.** CAP applies to a **single operation on a single data item**. "Is BookNook CP?" is not a well-formed question; "is checkout's stock decrement CP?" is.
- **To justify a vendor choice.** "We need AP, so Cassandra" skips the actual requirements and usually leads to the §11.3 failure.
- **When there is no replication.** One node has no partition to survive; spending a design review on CAP there is theatre.
- **To argue that consistency is optional.** AP still requires you to define the staleness bound and the merge rule. "Eventually consistent" is a design, not an excuse.
- **As a substitute for idempotency.** CAP tells you the write may be ambiguous; it does not make the retry safe (Lesson 17).

## 15. Common mistakes

1. **"Pick two of three."** P is mandatory, so the choice is binary and only during a partition. Saying this in an interview is an instant credibility loss.
2. **Confusing CAP's C with ACID's C.** Linearizability versus constraint integrity. Different layer, different meaning (Lesson 49).
3. **Reading A as uptime or SLO.** CAP's A has no latency bound; a system answering in 30 s is "available". Latency lives in PACELC.
4. **Calling a system CA without stating a fault model.** "CA" is a claim about your network, not your database.
5. **Choosing AP for data with unresolved conflicts.** Inventory, money, seats, unique usernames.
6. **Choosing CP for data with no business invariant.** Sessions, presence, view counts — you are buying outages you do not need.
7. **Forgetting that partitions include GC pauses and overload.** Any unresponsiveness longer than the failure-detector timeout is a partition.
8. **Resolving conflicts with wall-clock timestamps.** Skew makes LWW arbitrary; use versions or merge (Lesson 66).
9. **Tuning `W`/`R` without asserting `W+R>N`.** A single-config-change AP regression is very common.
10. **Never measuring partition duration or frequency**, so the CP trade-off is never priced against the error budget.
11. **Believing Spanner refutes CAP.** It pays latency on every write and still goes unavailable on a minority — which is the theorem, not an exception.
12. **Not writing the decision down.** Six months later a new engineer flips a consistency level and nobody can tell whether the old value was load-bearing.

## 16. Interview perspective

**What is actually being tested.** Two things. First, whether you know CAP **precisely** — the definitions, the fact that partition tolerance is not optional, and that the choice is per-operation. Second, and much more important, whether you can **turn the theorem into a design decision with a cost**: which of my operations are CP, what does the user see during a partition, and how long can that last before it breaks my SLO?

**Answers that land well:**

- *"Is your system CP or AP?"* — "Neither as a whole. Checkout's stock decrement is CP — a synchronous standby and a 503 rather than a double-sell — because a double-sell costs far more than a retry. Product pages and reviews are AP with an explicit 60-second staleness bound. The decision is per operation and it's written in the schema comments so it survives staff turnover."
- *"What happens to BookNook during a network partition?"* — "Depends which partition. One AZ isolated: nothing — `W=2` of 3 still has a majority, so writes continue. Two AZs isolated: writes on the minority are rejected, reads served from replicas continue, and the checkout SLO absorbs it. The 30-second version of that event costs 2.3 % of the monthly error budget, which is why I'm comfortable with CP there."
- *"Why not just be consistent everywhere?"* — "Because consistency is a latency bill paid on every write and an availability bill during partitions. For reviews, both bills buy nothing. For inventory, both are cheap relative to the alternative. That asymmetry, not the theorem, is the design."
- Raising **PACELC unprompted** — "and that's the partition case, which is rare; the everyday trade-off is latency versus consistency, which is what actually sets our p99" — signals you have read past the undergraduate version.

**Common interview tasks:** given a system, classify each operation CP or AP and justify it; explain what happens to a write during a partition; choose between `QUORUM` and `ONE` for a given request; explain why "CA" is a category error; reconcile "the database is consistent" with "the user sees stale data"; compute the availability cost of a sync replica.

**Red flags interviewers watch for:**

- "Pick two of three."
- Defining C as "ACID consistency" or A as "uptime".
- Treating CAP as a property of a product rather than of an operation.
- Choosing AP for a money or inventory path without mentioning reconciliation or compensation.
- Choosing CP everywhere and not noticing the availability cost.
- No mention of timeouts — every CAP decision is implemented as a timeout, and timeouts are guesses.
- Being unable to say what the *user* experiences during a partition.
- Claiming Spanner proves CAP wrong.
- Never mentioning that partitions are rare, which means never mentioning PACELC or latency.

## 17. Knowledge check

**Conceptual**

1. State CAP precisely, including the formal meaning of each letter. Then explain why "partition tolerance is not optional", and why the practical form of the theorem is "during a partition, choose C or A".
2. Explain the difference between **CAP consistency** and **ACID consistency**, and construct a concrete system that is ACID-consistent but CAP-inconsistent (state the operation, the invariant, and the anomaly a user could observe).
3. A colleague says "we run Cassandra, so we're AP and we don't need to worry about conflicts". Correct them precisely: name the knob that changes the answer, the guarantee that knob provides when set to `QUORUM`, and the specific class of data for which their statement is dangerous.

**Practical scenario**

4. BookNook is adding a "reserve at checkout, pay within 15 minutes" flow. Inventory must never go negative; the reservation must be visible to the user immediately on the checkout page, and the "X left in stock" badge on product pages may be up to a minute stale. There are three replicas in three AZs, and a partner integration that reads stock via API. Specify the CAP decision for each of the four interactions (reserve, user's own reservation view, product-page badge, partner API read), the replication settings, the behaviour during a single-AZ partition and during a dual-AZ partition, and the one measurement that would tell you whether your choice is affordable.

**Calculation**

5. An SLO requires 99.99 % monthly availability for checkout. (a) Compute the monthly error budget in minutes. (b) A CP design rejects writes during any partition that leaves fewer than 2 of 3 replicas reachable; if dual-AZ isolations occur 0.05 times per month and last 8 minutes on average, what fraction of the budget do they consume? (c) If single-AZ isolations (which the design survives) occur 1.5 times per month but each takes 45 s to *detect* and the design stalls writes during detection, what fraction of the budget does detection latency consume? (d) State whether the design meets the SLO, and the single change you would make if it did not.

6. A three-replica store is configured `N=3`. For each of (`W=2,R=2`), (`W=1,R=1`), (`W=3,R=1`), state (a) whether `W+R>N` holds, (b) the maximum number of replicas that can be lost while still accepting writes, (c) the maximum number that can be lost while still serving reads, and (d) the CAP behaviour during a partition that isolates exactly one replica and, separately, two replicas.

**Progress checklist**

- Phase 1 — Foundations (Lessons 1–17): **complete** ✅
- Phase 2 — Scaling (Lessons 18–39): **complete** ✅
- Phase 3 — Data and Storage:
  - [x] Topic 1 — Relational databases (L40)
  - [x] Topic 2 — Tables, keys, relationships, joins, constraints (L41)
  - [x] Topic 3 — SQL versus NoSQL (L42)
  - [x] Topic 4 — Key-value databases (L43)
  - [x] Topic 5 — Document databases (L44)
  - [x] Topic 6 — Wide-column databases (L45)
  - [x] Topic 7 — Graph databases (L46)
  - [x] Topic 8 — Search engines and full-text indexes (L47)
  - [x] Topic 9 — Selecting a database from access patterns (L48)
  - [x] Topic 10 — ACID transactions (L49)
  - [x] Topic 11 — Isolation levels (L50)
  - [x] Topic 12 — Dirty/non-repeatable/phantom reads, lost updates (L51)
  - [x] Topic 13 — Optimistic versus pessimistic concurrency control (L52)
  - [x] Topic 14 — Database indexes (L53)
  - [x] Topic 15 — B-tree, hash, composite, covering, secondary indexes (L54)
  - [x] Topic 16 — Query plans and index trade-offs (L55)
  - [x] Topic 17 — Normalisation and denormalisation (L56)
  - [x] Topic 18 — OLTP versus OLAP (L57)
  - [x] Topic 19 — Data warehouses, data lakes, lakehouses (L58)
  - [x] **Topic 20 — CAP theorem (L59) ← you are here**
  - [ ] Topic 21 — PACELC (L60)
  - [ ] Topic 22 — Consistency models (L61)
  - [ ] Topic 23 — Object, block, and file storage (L62)
  - [ ] Topic 24 — Data retention, archival, backups, disaster recovery (L63)

**NEXT LESSON: Phase 3, Topic 21 — PACELC**

---

# Lesson 60 — PACELC

*Phase 3, Topic 21*

## 1. Simple definition

**PACELC** is a two-clause rule that describes what a distributed data store must give up, and it is stated like this:

> **If** there is a network **P**artition, the system must choose between **A**vailability and **C**onsistency;
> **E**lse — that is, during normal operation with no partition — the system must choose between **L**atency and **C**onsistency.

Lesson 59 gave you CAP: during a partition, pick A or C. PACELC adds the half that CAP leaves out — **the choice you make when nothing is broken at all.**

Terms defined before use:
- **Partition (P)**: a network failure that splits the replicas into groups that cannot exchange messages, while each group is still alive and still receiving client traffic (Lesson 59).
- **Availability (A)**: every request to a non-failed node receives a non-error response.
- **Consistency (C)**: in CAP and PACELC this specifically means **linearizability** — every read returns the result of the most recently completed write, as if there were a single copy of the data (Lesson 61 defines this rigorously).
- **Latency (L)**: the wall-clock time between a client issuing a request and receiving the response (Lesson 14).
- **Else (E)**: the steady state. The overwhelming majority of a system's lifetime.

The whole point of PACELC is the second clause. **CAP describes an emergency; PACELC describes Tuesday.**

## 2. Why it exists

CAP, as usually taught, is close to useless as a design tool, for one reason: it only tells you what happens during an event that almost never happens.

Consider BookNook's production numbers (typical for a well-run multi-AZ deployment):

| Condition | Share of the year | Minutes per year |
|---|---|---|
| Normal operation, all replicas reachable | ~99.99% | ~525,547 |
| Partition or partition-like degradation | ~0.01% | ~53 |

If you design your database choice around the 53 minutes and ignore the 525,547, you have optimised for the rounding error. Worse, the CAP framing invites a specific mistake: *"we picked a CP system, so we have strong consistency and it costs us nothing except during partitions."* That is false. A CP system pays for consistency **on every single request, forever**, in coordination round trips.

Daniel Abadi formulated PACELC in 2010 precisely to name that hidden, permanent cost. His observation was that two systems can both be "CP" under CAP and still be wildly different to use, because one of them does a cross-region round trip before acknowledging a write and the other does not.

So PACELC exists to answer the question CAP cannot:

> *"Assuming the network is perfectly healthy — and it almost always is — what am I paying for consistency, and am I willing to pay it on this particular operation?"*

That reframing is what makes PACELC actionable. It turns an architecture-astronaut debate into an arithmetic problem: how many round trips, over what distance, at what speed of light.

## 3. Real-world analogy

**A bank with branches in London, New York, and Singapore, and a rule about joint accounts.**

You and your partner share an account. You want to withdraw £500 in London.

- **The consistent policy (PC/EC)**: before any teller hands over cash, London phones New York and Singapore to confirm the current balance and to record the withdrawal, and waits for both to confirm. Nobody can ever overdraw, even if you and your partner withdraw simultaneously on opposite sides of the planet. But *every single withdrawal*, including the 99.99% of withdrawals where nothing unusual is happening, waits for international phone calls. That is the **else** clause: you pay the phone call on calm days too.
- **The available/fast policy (PA/EL)**: the London teller trusts the balance written in the London ledger, pays you immediately, and mails a note to the other branches. Withdrawals are instant. Occasionally the two of you both withdraw before the notes arrive and the account goes negative; the bank reconciles afterwards with an overdraft fee.
- **The phone-lines-are-down case (P)**: if the international lines fail, the consistent bank refuses to pay anyone (chooses C, loses A); the available bank keeps paying from the local ledger (chooses A, loses C).

Notice that the interesting business decision is not "what do we do if the phone lines fail?" — that is rare. It is "do we make every customer wait for an international call?" — that is constant.

**Where the analogy breaks down:**

| Bank branches | Distributed data store |
|---|---|
| A phone call takes seconds; a human notices and accepts it | A cross-region round trip takes 60–250 ms and is invisible to the user as an event — it is only felt as the page being sluggish, so nobody attributes the pain to its cause |
| The teller can use judgement ("this customer is trustworthy") | The database applies the same rule to every request; nuance must be expressed as explicit per-operation configuration |
| Branches are a fixed, small, known set | Replica sets change membership (scale-out, failover, re-sharding), and the coordination cost changes with them |
| "Phone lines down" is an obvious, binary, observable event | Partitions are frequently **partial and asymmetric**: A can reach B but B cannot reach A, or packets flow with 40% loss. The system often cannot tell a partition from a slow node (Lesson 59) |
| Reconciling an overdraft later is normal and legal | Reconciling conflicting writes later may be *impossible* — if two writes both set `stock = 0`, there is no record of what the correct answer was |
| The bank chooses one policy for the whole bank | A real system chooses **per operation**: BookNook's inventory decrement and BookNook's "recently viewed" list can sit at opposite ends of the spectrum inside the same product |

That last row is the single most important takeaway of this lesson, and section 7 builds on it.

## 4. Detailed technical explanation

### 4.1 The formulation stated precisely

For a system that replicates data, PACELC asserts two independent choices:

```
if (partition) {
    choose A or C      // the CAP half
} else {
    choose L or C      // the PACELC addition
}
```

A system is labelled with one choice from each clause: `P{A|C}/E{L|C}`. The four combinations are `PA/EL`, `PA/EC`, `PC/EL`, `PC/EC`.

Two refinements that textbooks usually skip:

1. **Both choices are spectra, not binaries.** A quorum system (Lesson 58) with `N=3, W=2, R=2` is more consistent and slower than `W=1, R=1`, and less consistent and faster than `W=3, R=3`. PACELC labels the *default or characteristic* behaviour of a system; it does not mean the system has exactly two modes.
2. **The label describes the data store's configuration, not its brand.** Cassandra configured with `CONSISTENCY ALL` is not PA/EL for that query. Treat PACELC labels as the vendor default, then check what you actually configured.

### 4.2 Why the "else" half dominates in practice

A partition is an *event*; latency is a *tax*. Three reasons the tax matters more:

- **Frequency.** From the table in section 2: normal operation is roughly 10,000× more common than partitioned operation.
- **Compounding.** A single page view on BookNook's checkout triggers 5–12 dependent data operations. If each pays an extra 60 ms of coordination, the page pays 300–720 ms — multiplied across every user, every request, every day.
- **Revenue coupling.** The relationship between latency and conversion is well documented and roughly linear in the 100 ms–1 s band; the industry rule of thumb is ~1% conversion loss per 100 ms of added latency (approximate, and it varies by product). A 60 ms per-write penalty is therefore a continuously paid business cost, whereas a partition is an incident you can post-mortem.

The blunt version: **you will experience maybe one meaningful partition per year, and roughly 10 billion non-partitioned requests. Optimise accordingly — while still being correct during the partition.**

### 4.3 Why consistency costs latency even with zero failures

This is the part people resist, so it is worth being mechanical about it.

To guarantee linearizability across replicas, a write must be **durably acknowledged by enough replicas that no subsequent read can miss it** before the client is told "done". With `N` replicas and quorum sizes satisfying `W + R > N` (Lesson 58), the write must physically reach `W` machines. Those machines are, by design, in different failure domains — different availability zones, often different regions (Lesson 39). Therefore the write latency has an irreducible floor:

```
write_latency ≥ (one-way network time to the W-th closest replica) × 2   [request + ack]
             + fsync time on that replica
             + local processing
```

Nothing in that expression involves failure. It is the speed of light plus a disk. Light in fibre travels roughly 200,000 km/s (about two-thirds of *c* because of the refractive index of glass), and real paths are 1.3–2× longer than great-circle distance because cables follow coasts and conduits.

Typical, approximate round-trip times you should memorise:

| Path | Typical RTT | Notes |
|---|---|---|
| Same host (loopback) | ~0.05 ms | |
| Same rack | ~0.1–0.2 ms | |
| Same availability zone | ~0.3–0.5 ms | |
| Cross-AZ, same region | ~0.5–1.5 ms | The reason multi-AZ sync replication is cheap (Lesson 33) |
| `us-east-1` ↔ `us-west-2` | ~60–70 ms | |
| `us-east-1` ↔ `eu-west-1` | ~70–80 ms | |
| `us-east-1` ↔ `ap-southeast-1` | ~210–240 ms | |
| Antipodal (London ↔ Sydney) | ~250–290 ms | |

Lesson 33 established synchronous versus asynchronous replication; PACELC is the lens that tells you *where to put the replicas you are synchronising with*. Synchronous replication across three AZs costs ~1 ms and is almost always worth it. Synchronous replication across three continents costs ~200 ms and almost never is.

### 4.4 The four classifications

| Class | Partition behaviour | Normal-operation behaviour | Representative systems | What it feels like |
|---|---|---|---|---|
| **PA/EL** | Stay available, serve possibly stale data | Favour latency: ack writes locally, read from nearest replica | **Cassandra** (default `LOCAL_ONE`/`ONE`), **DynamoDB** (default eventually consistent reads), **Riak**, **Voldemort**, **Cosmos DB** session/eventual levels | Always up, always fast, sometimes shows you yesterday |
| **PA/EC** | Stay available during partition | Favour consistency when healthy: route reads to the primary, ack after the primary commits | **MongoDB** (default: single primary per replica set, `w:1`/`majority` writes, primary reads), **Couchbase** | Normally reads the truth; during a partition a minority side may serve or accept stale state before step-down completes |
| **PC/EL** | Refuse rather than diverge during partition | Still favour latency when healthy | **Yahoo PNUTS** (the canonical example), some tuned hybrids | Rare and awkward: strict when broken, relaxed when fine |
| **PC/EC** | Minority side rejects requests | Pay full coordination cost on every operation | **PostgreSQL with `synchronous_commit=remote_apply`** and synchronous standbys, **VoltDB**, **etcd**/**ZooKeeper**, **Google Spanner** (effectively) | Never wrong, never fast, predictable |

Two nuances that interviewers probe:

**MongoDB is not simply PA/EC.** Its classification depends on write concern and read preference. `w:majority` + `readConcern:"linearizable"` + primary reads behaves close to PC/EC. `w:1` + `readPreference:secondaryPreferred` behaves like PA/EL. The "PA/EC by default" label reflects the historical default: writes acknowledged by the primary, reads served by the primary, so a healthy cluster reads its own writes — but a partitioned old primary could briefly accept writes that are later rolled back.

**Spanner is the interesting case.** Abadi classifies Spanner as PC/EC, and that is correct in the sense that it never serves a non-linearizable result and a minority partition cannot commit. But Google's own position is that Spanner is "effectively CA" in practice, because its network is engineered — private fibre, redundant paths, controlled failure domains — so that partitions are rarer than the other causes of unavailability. Both statements are true: PACELC describes the *guarantee*, Google's claim describes the *empirical failure rate*. The intellectually honest answer in an interview is: **Spanner is PC/EC by guarantee and pays a real EC latency cost (commit wait plus Paxos round trips), and Google mitigates the P half with infrastructure rather than with protocol.**

### 4.5 Spanner and TrueTime, conceptually

Spanner offers **external consistency** — defined as: if transaction T1 commits before T2 starts (in real wall-clock time, as observed by any outside party), then T1's commit timestamp is less than T2's. This is strict serializability (Lesson 61, §4.3) applied globally.

The hard part is assigning timestamps that respect real time across machines whose clocks disagree. Spanner's answer is **TrueTime**, an API backed by GPS receivers and atomic clocks in every datacentre. Instead of returning a timestamp, TrueTime returns an **interval**:

```
TT.now() → [earliest, latest]      // the true instant is guaranteed to lie inside
ε = (latest − earliest) / 2        // the uncertainty bound
```

Typical published figures: ε sawtooths between roughly 1 ms and 7 ms, averaging around 4 ms, as local clocks drift between synchronisations with the time masters.

The protocol then does one deliberately counter-intuitive thing — **commit wait**:

1. The coordinator picks commit timestamp `s = TT.now().latest`.
2. It completes the Paxos replication of the transaction.
3. It then **deliberately sleeps** until `TT.now().earliest > s` — that is, until it is certain that `s` is in the past everywhere.
4. Only then does it release locks and reply to the client.

The sleep is what buys external consistency: by the time anyone can observe the commit, the timestamp is unambiguously in the past, so no later transaction can be assigned an earlier timestamp. The cost is approximately `2ε` of pure waiting — **typically ~5–10 ms per read-write transaction, on top of the Paxos round trips**. Published Spanner measurements put mean commit wait in the single-digit milliseconds.

This is PACELC's `E:C` made visible and measurable. Spanner did not escape the trade-off; it **bought the uncertainty down with hardware** (atomic clocks shrink ε, which shrinks the wait) and then paid the remainder honestly. Consensus itself — the Paxos round trips underneath — is Phase 4, Topic 18 (Consensus and Raft).

### 4.6 PACELC applies per operation, not per system

The practical reframing: do not ask "is BookNook a CP shop or an AP shop?" Ask, for each operation:

> *"If this value is stale by 500 ms, what is the worst thing that happens, and what does that cost in money, trust, or law?"*

That question has a different answer for `decrement_stock` than for `increment_view_count`, which is why mature systems are deliberately **polyglot in consistency** even when they are monoglot in database technology.

## 5. Request / data flow

A strongly consistent (`E:C`) write to a quorum-replicated store, start to finish:

1. The client sends `POST /orders` to the nearest edge/load balancer (Lesson 21).
2. The load balancer forwards to an application server in the same region.
3. The application server resolves which **shard** owns the key (Lesson 57) and looks up that shard's current **leader** replica.
4. If the leader is in another region, the request crosses the inter-region link — this is where most of the latency is born.
5. The leader acquires locks / validates the transaction against its local state.
6. The leader appends the write to its write-ahead log and issues an `fsync` (Lesson 49) so the record survives a crash.
7. In parallel, the leader ships the log record to all followers.
8. Each follower appends, `fsync`s, and returns an acknowledgement.
9. The leader **blocks** until `W − 1` follower acks have arrived (the quorum condition, Lesson 58). This blocking step is the entire `E:C` cost.
10. (Spanner only) The leader performs **commit wait**, sleeping until the commit timestamp is certainly in the past.
11. The leader marks the transaction committed, releases locks, and replies to the application server.
12. The application server replies to the client. The user sees the confirmation.
13. Any subsequent read — from any replica that participates in the read quorum — is now guaranteed to observe this write.

The eventually consistent (`E:L`) variant replaces steps 7–10 with: *the leader (or any replica that accepted the write) replies immediately after step 6, and ships the record to peers in the background.* Steps 11–13 collapse, and step 13's guarantee is dropped: a read may observe the pre-write value for as long as replication lag persists (Lesson 34).

## 6. ASCII diagram

```
                              PACELC DECISION TREE
                              ====================

                        Is the network partitioned?
                                   |
              +--------------------+--------------------+
              | YES  (rare: ~0.01% of time)             | NO  (~99.99% of time)
              v                                          v
      Choose A or C                               Choose L or C
              |                                          |
      +-------+-------+                          +-------+-------+
      |               |                          |               |
      v               v                          v               v
     PA              PC                         EL              EC
 serve stale     reject writes            ack locally,     wait for quorum
 keep serving    on minority side         read nearest     (+ commit wait)
      |               |                          |               |
      +-------+-------+                          +-------+-------+
              |                                          |
              +------------------+-----------------------+
                                 v
                   PA/EL   PA/EC   PC/EL   PC/EC


        LATENCY ANATOMY OF ONE WRITE  (the "E" half, no failures at all)
        ================================================================

  EL  (eventual)                         EC  (strong, 3 AZs, W=2 of N=3)
  --------------                         ------------------------------
  client                                 client
    | 0.3 ms                               | 0.3 ms
    v                                      v
  app ---> leader                        app ---> leader (AZ-a)
            | exec 1.0                              | exec 1.0 + fsync 0.5
            | fsync 0.5                             +----------+----------+
            | ACK  <-- replies NOW                  | 0.4 ms   | 0.4 ms   (one way)
            v                                       v          v
   (background replication)                    follower     follower
      ~20-400 ms later                          (AZ-b)       (AZ-c)
      peers converge                            fsync 0.5    fsync 0.5
                                                   | 0.4 ms   | 0.4 ms
                                                   +----+-----+
                                                        v
                                            leader has 2/3 --> ACK
    total ~ 2.1 ms                            total ~ 3.4 ms


  EC ACROSS REGIONS (leader us-east-1, quorum needs us-west-2 at 60 ms RTT)
  -------------------------------------------------------------------------
   app --0.3--> leader --[ exec 1.0 + fsync 0.5 ]--+
                                                   |  30 ms  (one way)
                                                   v
                                          us-west-2 follower
                                            fsync 0.5
                                                   |  30 ms  (ack)
                                                   v
                                          leader: quorum reached --> ACK
    total ~ 62.6 ms      <-- 30x the single-region strong write,
                             and ~30x the eventual write, with
                             ZERO failures anywhere in the system
```

## 7. Detailed example

### BookNook

BookNook now runs in three regions: `us-east-1` (primary market), `eu-west-1`, and `ap-southeast-1` (Lesson 39). The team's instinct after Lesson 59 was to "pick CP and be safe". PACELC forces a better conversation: a per-operation audit.

| BookNook operation | Cost of 500 ms of staleness | PACELC choice | Concrete configuration |
|---|---|---|---|
| Decrement stock on checkout (last copy of a signed first edition) | Two customers buy the same physical book. Refund, apology, support ticket, possible chargeback. Real money. | **PC/EC** | Single-region leader for the inventory shard, quorum write across 3 AZs, read from leader |
| Payment authorisation & order record | Double charge, or an order with no payment. Regulatory and trust impact. | **PC/EC** | Same as above; the order write is in the same transaction as the stock decrement |
| User password / email change | Stale auth state can allow a revoked credential to work. Security boundary. | **PC/EC** | Quorum write; auth reads from leader or use a short-TTL deny-list |
| Add item to cart | Cart briefly missing an item on a second device. Mildly annoying. | **PA/EC**-ish | Region-local strong write, async cross-region replication, sticky routing per user |
| Post a review | Review appears a second late to other users. Nobody notices. The *author* noticing is a different problem — see Lesson 61's read-your-writes. | **PA/EL** | Local ack, async replication, session token for the author's own reads |
| "Customers also bought" recommendations | Shows a slightly dated list. Zero harm. | **PA/EL** | Read nearest replica, accept any staleness up to minutes |
| Product page view count | A counter is off by 3 for a few seconds. | **PA/EL** | Local counters, periodic aggregation |
| Search index | A new title is unsearchable for 30 s. Acceptable, and search is a derived index anyway (Lesson 53). | **PA/EL** | Async indexing pipeline |

The outcome: **one small, expensive, strongly consistent core** (inventory, orders, payments, auth) and **a large, cheap, eventually consistent periphery** (reviews, recommendations, counters, search). That shape is not a compromise; it is the correct answer, and it is what PACELC is for.

The measurable result for BookNook: the strongly consistent core is ~4% of request volume and pays a ~60 ms coordination cost when the user is far from the leader; the remaining ~96% pays ~2 ms. If BookNook had made everything PC/EC globally, the median page would have gained roughly 300 ms — the entire latency budget from Lesson 14, burned on consistency nobody asked for.

### Contrasting real-world example: a multiplayer game lobby versus its billing system

A mobile game studio runs two data systems in the same product:

- **Player position, health, and chat** live in a PA/EL store (Cassandra-style, region-local, `LOCAL_ONE`). A 40 ms stale position is invisible because the client interpolates anyway; a 200 ms coordination wait would make the game unplayable. Here, **latency *is* the product**.
- **In-app purchases and the gem balance** live in a PC/EC store (a single-region relational primary with synchronous standbys). Here, a lost or duplicated write is a chargeback and an app-store dispute. The purchase flow shows a spinner, and the player tolerates 300 ms because they have just tapped "Buy" and expect a moment of ceremony.

Same company, same user, same session, opposite PACELC choices — chosen by the *consequence of staleness*, not by taste.

## 8. Code or configuration example

These snippets are **illustrative teaching examples, not production-ready**. They omit error handling, retries, idempotency keys, timeouts, connection pooling (Lesson 54), observability, and security.

```python
# ---------------------------------------------------------------------------
# BookNook: PACELC expressed as a PER-OPERATION choice, not a per-system one.
# Illustrative only.
# ---------------------------------------------------------------------------

from cassandra import ConsistencyLevel          # Cassandra driver enums
from cassandra.query import SimpleStatement

# --- PA/EL path: posting a review -----------------------------------------
# LOCAL_ONE = acknowledge as soon as ONE replica in the LOCAL datacentre has
# the write. No cross-region wait at all. This is the "EL" choice: we are
# explicitly buying latency with consistency.
post_review = SimpleStatement(
    "INSERT INTO reviews (book_id, review_id, user_id, body) "
    "VALUES (%s, %s, %s, %s)",
    consistency_level=ConsistencyLevel.LOCAL_ONE,   # <-- the EL decision
)

# Reading reviews: ONE replica, nearest, staleness tolerated.
read_reviews = SimpleStatement(
    "SELECT * FROM reviews WHERE book_id = %s",
    consistency_level=ConsistencyLevel.LOCAL_ONE,   # <-- EL again
)

# --- PC/EC path: decrementing stock ---------------------------------------
# LOCAL_QUORUM = a majority of replicas in the local datacentre must ack.
# With RF=3 in-region, QUORUM = 2, so W + R > N holds if reads also use
# LOCAL_QUORUM  ->  2 + 2 > 3. That inequality is the whole guarantee
# (Lesson 58). We pay ~1 extra cross-AZ round trip for it.
reserve_stock = SimpleStatement(
    "UPDATE inventory SET qty = qty - 1 "
    "WHERE book_id = %s IF qty > 0",            # IF = lightweight transaction
    consistency_level=ConsistencyLevel.LOCAL_QUORUM,   # <-- the EC decision
    serial_consistency_level=ConsistencyLevel.LOCAL_SERIAL,
    # LOCAL_SERIAL runs Paxos among local replicas so the compare-and-set is
    # linearizable. This is the expensive path: ~4 round trips, not 1.
)
```

```python
# ---------------------------------------------------------------------------
# DynamoDB: the same decision as a single boolean. Illustrative only.
# ---------------------------------------------------------------------------
import boto3
ddb = boto3.client("dynamodb", region_name="us-east-1")

# EL (default): eventually consistent read. Served by any of the 3 replicas,
# may be up to ~1 second stale. Costs 0.5 read request units.
ddb.get_item(
    TableName="Recommendations",
    Key={"user_id": {"S": "u-1042"}},
    ConsistentRead=False,            # <-- latency wins
)

# EC: strongly consistent read. Routed to the leader replica, reflects all
# writes acknowledged before it started. Costs 1.0 read request unit
# (2x the price) and has higher and more variable latency.
ddb.get_item(
    TableName="Inventory",
    Key={"book_id": {"S": "isbn-9780141182636"}},
    ConsistentRead=True,             # <-- consistency wins
)
# Note: ConsistentRead=True is NOT available on Global Secondary Indexes,
# and NOT available for cross-region reads in a Global Table. Geography
# imposes the EL choice on you whether you like it or not.
```

```sql
-- ---------------------------------------------------------------------------
-- PostgreSQL: PACELC tuned per TRANSACTION, inside one database.
-- Illustrative only.
-- ---------------------------------------------------------------------------

-- Cluster-wide default: wait for a synchronous standby before acking.
-- ANY 1 (...) means "any one of these standbys must confirm".
-- remote_apply is the strictest: the standby must have REPLAYED the WAL,
-- so a read on that standby is guaranteed to see the write.
ALTER SYSTEM SET synchronous_standby_names = 'ANY 1 (standby_az_b, standby_az_c)';
ALTER SYSTEM SET synchronous_commit = 'remote_apply';   -- EC by default

-- Per-transaction downgrade for a cheap, high-volume, loss-tolerant write.
-- synchronous_commit = off means: ack the client BEFORE the local fsync.
-- Losing the last ~200 ms of page-view counters on a crash is acceptable;
-- losing an order is not. Scope the downgrade tightly.
BEGIN;
  SET LOCAL synchronous_commit = 'off';                 -- <-- EL, this txn only
  UPDATE page_views SET views = views + 1 WHERE book_id = 91823;
COMMIT;

-- The order path keeps the cluster default (EC) and says so explicitly,
-- so that a future change to the cluster default cannot silently weaken it.
BEGIN;
  SET LOCAL synchronous_commit = 'remote_apply';        -- <-- EC, explicit
  UPDATE inventory SET qty = qty - 1
   WHERE book_id = 91823 AND qty > 0;                   -- guard prevents oversell
  INSERT INTO orders (user_id, book_id, cents) VALUES (1042, 91823, 1899);
COMMIT;
```

## 9. Numerical example

**Question:** what does a strongly consistent write actually cost BookNook, compared with an eventually consistent one, in a single region and across regions?

### Assumptions (all stated, all typical/approximate)

| Symbol | Meaning | Value |
|---|---|---|
| `t_app` | Client ↔ app server, one way, same region | 0.15 ms |
| `t_db` | App server ↔ database leader, one way, same AZ | 0.15 ms |
| `t_az` | Cross-AZ, one way | 0.40 ms |
| `t_wr` | `us-east-1` ↔ `us-west-2`, one way | 30 ms (60 ms RTT) |
| `t_eu` | `us-east-1` ↔ `eu-west-1`, one way | 38 ms (76 ms RTT) |
| `t_ap` | `us-east-1` ↔ `ap-southeast-1`, one way | 110 ms (220 ms RTT) |
| `t_exec` | Transaction execution on the leader | 1.00 ms |
| `t_fsync` | WAL `fsync` on NVMe SSD | 0.50 ms |
| `N, W` | 3 replicas, quorum write of 2 | — |

Simplifications, stated honestly: the leader's own `fsync` and the shipping of the log record to followers are modelled as **sequential** for clarity; production engines overlap them, which shaves a few tenths of a millisecond off the strong numbers. The follower that completes first determines the quorum, so we take the **minimum** of the follower latencies, not the maximum. Queueing delay, TLS, and serialisation are excluded.

### Case 1 — Eventually consistent write, single region (`EL`)

```
client → app                     0.15
app → leader                     0.15
execute                          1.00
leader fsync                     0.50
leader → app (ACK)               0.15
app → client                     0.15
---------------------------------------
TOTAL                            2.10 ms
```
Replication to the other two AZs happens afterwards, off the critical path.

### Case 2 — Strongly consistent write, single region, 3 AZs (`EC`)

```
client → app                     0.15
app → leader                     0.15
execute                          1.00
leader fsync                     0.50
leader → follower (cross-AZ)     0.40
follower fsync                   0.50
follower → leader (ack)          0.40
   (both followers run in parallel; first to reply satisfies W=2)
leader → app (ACK)               0.15
app → client                     0.15
---------------------------------------
TOTAL                            3.40 ms
```

**Difference: 1.30 ms, or +62% relative.** Relative cost looks alarming; absolute cost is invisible to a human (the perception threshold for interface responsiveness is ~100 ms). **Conclusion: within a region, strong consistency is nearly free. Take it.**

### Case 3 — Strongly consistent write, multi-region (`EC`, leader in `us-east-1`, quorum needs `us-west-2`)

Replicas: `us-east-1` (leader), `us-west-2`, `eu-west-1`. `W=2` means the leader plus the **nearest** remote region, which is `us-west-2` at 30 ms one way.

```
client → app (both in us-east-1)        0.15
app → leader                            0.15
execute                                 1.00
leader fsync                            0.50
leader → us-west-2 follower            30.00
follower fsync                          0.50
follower → leader (ack)                30.00
leader → app (ACK)                      0.15
app → client                            0.15
-----------------------------------------------
TOTAL                                  62.60 ms
```

### Case 4 — Eventually consistent write, multi-region

Identical to Case 1 — **2.10 ms** — because the local replica acks immediately and cross-region shipping is asynchronous.

### Case 5 — The one that actually hurts: a European user, leader in `us-east-1`

```
client (London) → app (eu-west-1)        0.15
app (eu-west-1) → leader (us-east-1)    38.00
execute                                  1.00
leader fsync                             0.50
leader → us-west-2 follower             30.00
follower fsync                           0.50
follower → leader (ack)                 30.00
leader → app (eu-west-1)                38.00
app → client                             0.15
------------------------------------------------
TOTAL                                  138.30 ms
```

A Singapore user on the same leader: replace `38.00` with `110.00` twice → **282.30 ms** for one write.

### Summary and user-visible impact

| Scenario | Latency | vs. eventual |
|---|---|---|
| Eventual, any region | 2.10 ms | 1.0× |
| Strong, single region, 3 AZs | 3.40 ms | 1.6× |
| Strong, multi-region, user co-located with leader | 62.60 ms | 29.8× |
| Strong, multi-region, European user | 138.30 ms | 65.9× |
| Strong, multi-region, Singapore user | 282.30 ms | 134.4× |

Now apply it to a real page. BookNook's checkout performs **5 sequential dependent writes** (reserve stock, create order, authorise payment, write address snapshot, emit confirmation record). Sequential means the latencies add:

| Scenario | 5 sequential writes |
|---|---|
| Strong, single region | 5 × 3.40 = **17.0 ms** |
| Strong, multi-region, US user | 5 × 62.60 = **313.0 ms** |
| Strong, multi-region, Singapore user | 5 × 282.30 = **1,411.5 ms** |

**The user-visible difference**: a Singapore shopper waits over 1.4 seconds of pure coordination on the checkout button — before any application logic, rendering, or third-party payment call. Against Lesson 14's 200 ms interaction budget, that is a 7× overrun, and at the industry rule of thumb of ~1% conversion loss per 100 ms, roughly a 14% conversion hit on that market.

**The design conclusion PACELC hands you:** do not make the whole system globally strong. Keep the strongly consistent core **region-local** (3 AZs, 3.4 ms), pin each shard's leader near the customers who write to it, and let everything else be `EL`. If a single global strong order of writes is genuinely required, that is precisely the problem Spanner solves — and Spanner's answer is still 60–140 ms of Paxos plus ~5–10 ms of commit wait. **There is no configuration that makes the speed of light optional.**

## 10. Trade-offs

| Axis | Choosing `EC` (consistency) | Choosing `EL` (latency) |
|---|---|---|
| **Write latency** | Bounded below by the round trip to the furthest quorum member — 3.4 ms intra-region, 60–280 ms inter-region | ~2 ms regardless of geography |
| **Read latency** | Reads must hit the leader or a read quorum; cannot be served from the nearest replica | Nearest replica, often an in-region cache |
| **Throughput** | Lower: locks and leader coordination serialise conflicting work; a single leader is a throughput ceiling | Higher: writes are absorbed by any replica, scaling horizontally |
| **Application complexity** | Low. The database enforces the invariant; the code reads like single-machine code | High. The application must handle stale reads, conflicts, retries, and compensating actions |
| **Failure blast radius** | A leader failure stalls writes until failover (typically 5–30 s) | A replica failure is absorbed; traffic shifts silently |
| **Correctness debugging** | Bugs are deterministic and reproducible | Bugs are timing-dependent, rare, and surface as "impossible" support tickets |
| **Cost** | More expensive per operation (DynamoDB literally charges 2× for `ConsistentRead=True`); also more cross-AZ/cross-region data transfer | Cheaper per operation and cheaper in network egress |
| **Geographic expansion** | Each new region either joins the quorum (slowing everyone) or becomes read-only | New regions are nearly free; add replicas and serve locally |
| **Reasoning cost for the team** | Cheap: "it just works" | Expensive: every engineer must understand the consistency model forever (Lesson 61) |

The honest summary: **`EC` moves complexity into the infrastructure and pays for it in milliseconds; `EL` moves complexity into the application and pays for it in engineering time and rare, nasty bugs.** Which is cheaper depends on two measurable things — how far apart your replicas are, and how much money a single anomaly costs you.

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| **Partition isolates a PC/EC minority** (1 of 3 AZs cut off) | Users routed to the minority AZ get write errors / timeouts; majority users unaffected | Leader election heartbeats fail; `pg_stat_replication` shows standby disconnected; error-rate alert on the minority AZ | Load balancer health checks eject the minority (Lesson 24); clients retry against the majority; minority catches up from the WAL on reconnect |
| **Partition in a PA/EL system** | Everyone keeps shopping; two regions independently accept conflicting writes (e.g. both sell the last copy) | Hinted handoff queue depth grows; cross-region replication lag metric spikes; later, a business alert on negative stock | Replication resumes; conflicts resolved by LWW/CRDT/merge (Lesson 61); **business-level compensation required** — refund and apology email for the oversold copy |
| **Leader is alive but slow** (GC pause, noisy neighbour, disk degradation) | In `EC`: every write stalls behind the slow leader, p99 goes from 3 ms to 800 ms, checkout times out. In `EL`: barely noticed | p99 write latency alert; leader `fsync` duration metric; followers report growing apply lag | Failover to a healthy replica (manual or automatic); the slow node is ejected. Note: slow is *harder* than dead, because heartbeats still succeed |
| **Quorum lost entirely** (2 of 3 replicas down) | `EC` writes fail outright with "cannot achieve quorum"; reads may still work from the survivor | Quorum/raft health metric; explicit "no quorum" errors in logs | Restore a replica from snapshot + WAL; until then the system is read-only. **Do not** manually force a one-node quorum unless you accept possible data loss |
| **Clock skew exceeds TrueTime's ε bound** (Spanner-class systems) | Potentially non-external-consistent commits — the one thing the system promised not to do | TrueTime monitors ε continuously and compares against local clock drift | Spanner's documented response is to **evict the offending node from the quorum** rather than violate the guarantee. The system sacrifices availability to preserve correctness |
| **Someone changes the global default from `EL` to `EC`** | Sitewide latency doubles or worse; checkout conversion drops; nobody connects it to the config change | Latency regression alert correlated with a deploy; missing if the change ships with 40 others | Revert; then pin consistency **per operation** in code (section 8) so a global default can never silently change behaviour |
| **Asymmetric partition** (A hears B, B does not hear A) | Flapping: leadership changes repeatedly, writes succeed then fail then succeed | Rapid term/epoch increments in the consensus log; "leader changed" counter | Fencing tokens and pre-vote protocols (Phase 4, Topic 18 — Consensus and Raft) suppress flapping; operator may need to isolate the bad link |

## 12. Alternatives

| Approach | What it is | PACELC position | When it beats plain `EC` | Main drawback |
|---|---|---|---|---|
| **Plain CAP reasoning** | Pick AP or CP for the whole system | Ignores the `E` clause | Never, as a design tool — but it is a fine 30-second explanation | Hides the permanent latency cost; leads to over-strong systems |
| **Tunable quorums** (Cassandra, Riak, DynamoDB) | Set `N`, `W`, `R` per query | Slides along the `EL`↔`EC` axis | When different queries on the *same table* need different guarantees | Easy to misconfigure; `W+R>N` is necessary but not sufficient for linearizability without read repair |
| **Leader-per-shard with geo-pinned leaders** | Each shard's leader lives near the customers who write it | `EC` locally, `EL` globally | When writes have strong geographic locality — most consumer products | Cross-shard transactions become expensive; re-pinning on user relocation is manual |
| **Spanner / TrueTime** | Hardware-assisted global external consistency | PC/EC with minimised `E` cost | When you genuinely need one global serial order and can pay 60–140 ms + commit wait | Cost; vendor lock-in; still bounded by light speed |
| **Calvin-style deterministic transactions** (FaunaDB, VoltDB-adjacent) | Agree the *order* of transactions first, then execute deterministically everywhere | PC/EC with one round trip instead of two-phase commit | High-contention workloads where 2PC thrashes | Transactions must declare their read/write set up front |
| **CRDTs** | Data types that merge deterministically without coordination (Lesson 61) | PA/EL with *no* lost updates | Counters, sets, collaborative text, presence | Only works for operations with a lawful merge; metadata grows |
| **Escrow / reservation pattern** | Pre-allocate quantity to each region; coordinate only when a region's allocation runs low | `EL` in the common case, `EC` on the rare refill | Inventory, rate limits, ticket sales — anything countable | Complex; can under-sell if allocations are stranded |
| **Single-region everything** | Refuse to go multi-region | `EC` at intra-AZ cost | Until you have real global traffic — which is later than most teams think | Latency for distant users; regional outage is total outage |

## 13. When to use it

Use PACELC — specifically, use it as the per-operation framework for choosing consistency:

1. **Whenever you are selecting or configuring a replicated data store.** Before comparing feature lists, classify each candidate's PACELC behaviour under *your* intended configuration, and compute the `E`-clause latency from your actual replica geography.
2. **When you are about to go multi-region** (Lesson 39). PACELC is the tool that tells you which operations can follow the user into the new region and which must stay pinned to a home leader. Doing this audit *before* the migration prevents the classic "we went global and checkout got 10× slower" incident.
3. **When a latency regression has no obvious cause.** A surprising share of p99 regressions trace to a consistency setting: someone changed a write concern, enabled `ConsistentRead`, added a synchronous standby in another region, or moved a shard leader. PACELC gives you the hypothesis to test.
4. **When designing a new write path.** For each write, ask the staleness-consequence question from section 4.6 and record the answer in code and in the design document, so the next engineer inherits the reasoning rather than the setting.
5. **In system design interviews.** Stating PACELC rather than CAP immediately signals that you understand the steady-state cost, which is the thing that actually shows up in production.

## 14. When not to use it

1. **Single-node systems.** If your data lives on one machine with no replicas, there is no partition to tolerate and no quorum to wait for. PACELC describes replicated systems; applying it to a single Postgres instance produces nonsense. (Durability versus latency still applies there — that is `fsync` tuning, not PACELC.)
2. **As a system-level label used to end a discussion.** "We're an AP shop" is a slogan, not a design. The framework is only useful at operation granularity; used at system granularity it becomes the same blunt instrument CAP was.
3. **When the bottleneck is demonstrably elsewhere.** If your write path spends 400 ms in an N+1 query (Lesson 46) and 3 ms in replication, tuning consistency is theatre. Profile first; PACELC addresses the coordination component only.
4. **For stateless or derived data.** A CDN cache, a search index rebuilt from a source of truth (Lesson 53), or an analytics rollup has no authoritative copy to be inconsistent with. Their correctness model is freshness and rebuild-ability, not linearizability.
5. **When a regulatory or contractual requirement already fixes the answer.** If the regulator requires a durable, ordered, auditable record before you may confirm a trade, you are `EC` regardless of what the latency arithmetic says. Compute the cost anyway — but to plan capacity and UX, not to relitigate the requirement.

## 15. Common mistakes

### Beginner mistakes

- **Thinking PACELC replaces CAP.** It extends it. The `PA`/`PC` half *is* CAP.
- **Reading "C" as the C in ACID.** In CAP/PACELC, C means linearizability — a property about *replica agreement over time*. In ACID, C means *the database enforces declared invariants* like foreign keys (Lesson 49). Same letter, unrelated concepts. Interviewers love this one.
- **Believing eventual consistency means "eventually, probably, who knows when".** In practice convergence is measurable and usually tens to hundreds of milliseconds; it is a monitored SLO, not a shrug (Lesson 61, §4.6).
- **Assuming a CP system is unavailable during partitions for everyone.** Only the minority side loses write availability. The majority keeps working — usually most of your users.
- **Setting `W=N`** ("all replicas must ack") because it sounds safest. It makes your availability the product of all replicas' availabilities: with three replicas at 99.9% each, you get 99.7%. Quorum (`W = ⌊N/2⌋ + 1`) gives the same consistency guarantee with far better availability.
- **Choosing a database by its PACELC label alone**, ignoring that the label describes a default you are about to override anyway.

### Experienced-engineer mistakes

- **Pinning all shard leaders in one region "for simplicity"** and then expanding to Europe and Asia. Every remote write silently inherits 76–220 ms. The fix — leader placement per shard keyed on customer geography — is far cheaper to design up front than to retrofit.
- **Confusing `W + R > N` with linearizability.** The quorum inequality guarantees that read and write sets *intersect*; it does not by itself prevent reading a partially completed write, nor does it order concurrent writes. Dynamo-style systems need read repair and anti-entropy, and true linearizability needs consensus (Phase 4, Topic 18 — Consensus and Raft).
- **Treating Spanner as "strong consistency with no latency cost".** It is strong consistency with *engineered-down* latency cost: Paxos round trips plus ~5–10 ms of commit wait, on hardware most organisations do not have.
- **Ignoring the read path.** Teams tune write concern carefully and then serve reads from a lagging replica, destroying the guarantee they just paid for. The read and write configurations are a single joint decision.
- **Forgetting that `EC` reduces throughput, not just increases latency.** Leader-based coordination serialises conflicting transactions; under contention the throughput ceiling arrives long before the CPU does.
- **Hiding the choice in infrastructure config instead of in code.** A write concern set in a Terraform variable is invisible at the call site. Six months later someone "standardises" it and silently breaks an invariant. Express consistency requirements where the business logic lives.
- **Overlooking that cross-AZ sync replication is nearly free** and therefore running async within a region to "save latency" — saving ~1 ms while accepting real data-loss windows on failover (Lesson 33).

## 16. Interview perspective

PACELC is a **differentiator question**. Most candidates can recite CAP; far fewer can explain what consistency costs when nothing is broken.

**What a strong answer contains:**

1. The formulation, stated crisply, in one sentence (section 1).
2. The insight that the `E` clause matters more, with the frequency argument — partitions are rare, requests are constant.
3. Concrete round-trip numbers: ~0.5–1.5 ms cross-AZ, ~60–80 ms US↔EU, ~220 ms US↔APAC. Numbers are what separate a memorised answer from an engineering one.
4. Correct classification of at least three real systems, *with the caveat that the label depends on configuration*.
5. Spanner handled with nuance: PC/EC by guarantee, with TrueTime shrinking clock uncertainty, commit wait costing ~5–10 ms, and Google mitigating the P half with private fibre rather than with protocol.
6. A per-operation decision table for the problem under discussion — this is what makes the answer *design* rather than *trivia*.

**How it typically gets asked:**

- *"You chose Cassandra. What does that cost you on a normal day?"* → The `E` clause: you chose latency, so reads can be stale by the replication lag; here is how I protect the operations that cannot tolerate it.
- *"Your users are in Europe and your database is in Virginia. Walk me through a write."* → The Case 5 arithmetic from section 9, then leader placement as the fix.
- *"Is Spanner a counterexample to CAP?"* → No. It is a demonstration that you can buy the partition probability down with infrastructure while still paying the latency cost honestly.
- *"Everything in your design is strongly consistent. Defend it."* → Either defend it with per-operation reasoning, or — better — notice that it is wrong and relax the periphery.

**Red flags interviewers watch for:**

- Saying "CAP means pick two" and stopping there.
- Claiming strong consistency is free, or that a faster network removes the trade-off.
- Conflating CAP's C with ACID's C.
- Giving no numbers at all, or giving absurd ones (1 ms cross-continent, 100 ms cross-AZ).
- Picking one consistency model for an entire e-commerce system without distinguishing inventory from recommendations.
- Answering any PACELC question with "it depends" and leaving it there. It depends on exactly four measurable things: the geographic distance between quorum members, the write rate, the cost in money or trust of one anomaly, and the latency budget for the operation. Say those.
- Not knowing what happens on the *minority* side of a partition in their own chosen system.

## 17. Knowledge check

**Conceptual**

1. State PACELC precisely, then explain why the `E` clause is the one you pay for on virtually every request, while the `P` clause applies to a tiny fraction of your system's lifetime. In your explanation, identify exactly which physical constraint makes the `E:C` cost irreducible.

2. A colleague says: "Spanner proves the CAP theorem is obsolete — Google has strong consistency, global availability, and partition tolerance." Identify precisely what is right and what is wrong in that statement, and explain where Spanner's cost actually shows up, naming both the protocol component and the clock-uncertainty component.

3. MongoDB is commonly labelled PA/EC while Cassandra is labelled PA/EL, yet both can be configured to behave like the other. Explain what the labels are really describing, and why "PACELC label of the product" is less useful than "PACELC behaviour of this query".

**Practical scenario**

4. BookNook is launching in Japan. Product wants Japanese customers to have the same sub-200 ms experience as US customers. The inventory for Japanese warehouse stock is physically separate from US stock, but the `orders`, `users`, and `reviews` tables are shared globally, with all leaders currently in `us-east-1`. Using PACELC per operation, produce a plan: for each of `reserve_stock`, `create_order`, `update_user_profile`, `post_review`, and `read_recommendations`, state the chosen `E`-clause position, where the leader should live, and what the worst-case anomaly is under your choice. Then name the one operation in your plan that will be hardest to make fast, and explain why geography — not software — is the reason.

**Calculation**

5. BookNook adds a fourth region, `sa-east-1` (São Paulo), with a one-way latency of 55 ms to `us-east-1`, 95 ms to `eu-west-1`, and 180 ms to `us-west-2`. The orders table is replicated across all four regions with `N=4` and a write quorum of `W=3`. The leader is in `us-east-1`. Using the assumptions table from section 9 (`t_exec = 1.00 ms`, `t_fsync = 0.50 ms`, app↔client and app↔leader hops of 0.15 ms each where applicable, `t_wr = 30 ms`, `t_eu = 38 ms`), compute: (a) the write latency for a user co-located with the leader; (b) the write latency for a user in São Paulo whose app tier is in `sa-east-1`; (c) how both figures change if the quorum is reduced to `W=2`; and (d) the total coordination time for a five-write checkout in case (b) under `W=3`, expressed both in milliseconds and as a multiple of a 200 ms interaction budget. Show every step.

**Progress checklist**

Phase 1 — Foundations: **complete** (Lessons 1–17)
Phase 2 — Scaling: **complete** (Lessons 18–39)

Phase 3 — Data & Storage:
- [x] Topic 1 — Data models and the database landscape (Lesson 40)
- [x] Topic 2 — Relational databases and the relational model (Lesson 41)
- [x] Topic 3 — Normalisation and denormalisation (Lesson 42)
- [x] Topic 4 — Indexing with B-trees (Lesson 43)
- [x] Topic 5 — Hash indexes, LSM trees, and write amplification (Lesson 44)
- [x] Topic 6 — Query planning and execution (Lesson 45)
- [x] Topic 7 — Key-value stores (Lesson 46)
- [x] Topic 8 — Document databases (Lesson 47)
- [x] Topic 9 — Wide-column stores (Lesson 48)
- [x] Topic 10 — ACID transactions (Lesson 49)
- [x] Topic 11 — Isolation levels and anomalies (Lesson 50)
- [x] Topic 12 — Graph databases (Lesson 51)
- [x] Topic 13 — Time-series databases (Lesson 52)
- [x] Topic 14 — Search engines and inverted indexes (Lesson 53)
- [x] Topic 15 — Connection pooling (Lesson 54)
- [x] Topic 16 — Federation and functional partitioning (Lesson 55)
- [x] Topic 17 — Sharding strategies (Lesson 56)
- [x] Topic 18 — Consistent hashing (Lesson 57)
- [x] Topic 19 — Quorums and read/write tuning (Lesson 58)
- [x] Topic 20 — CAP theorem (Lesson 59)
- [x] Topic 21 — PACELC (Lesson 60) ← you are here
- [ ] Topic 22 — Strong, eventual, causal, read-your-writes, and monotonic consistency
- [ ] Topic 23 — Object, block, and file storage
- [ ] Topic 24 — Data warehouses, data lakes, and OLTP vs OLAP

**NEXT LESSON: Phase 3, Topic 22 — Strong, eventual, causal, read-your-writes, and monotonic consistency**

---

# Lesson 61 — Strong, Eventual, Causal, Read-Your-Writes, and Monotonic Consistency

*Phase 3, Topic 22*

## 1. Simple definition

A **consistency model** is a contract between a data store and the application that states, precisely, **which results a read is permitted to return** when multiple replicas and concurrent operations are involved.

That is the whole idea. It is not a measure of quality — a "weaker" model is not a broken model. It is a *smaller promise*, which buys you lower latency (Lesson 60) and higher availability (Lesson 59), at the cost of your application having to tolerate more possible outcomes.

Terms defined before use:
- **Operation**: a single read or write of a data item, with an **invocation** (when the client sends it) and a **response** (when the client receives the result). The two are separated in time, and that gap is where all the subtlety lives.
- **Replica**: one copy of the data on one machine (Lesson 32).
- **Anomaly**: a result that a human would call wrong, but which the chosen model explicitly permits.
- **Guarantee**: a statement of the form "the system will never return a result such that X".

The practical skill this lesson teaches is **choosing the weakest model that still protects your business invariant**, and being able to name the anomaly you have accepted.

## 2. Why it exists

Lesson 34 introduced replication lag and showed one concrete anomaly: a user updates their profile, the read goes to a lagging replica, and the old name comes back. That lesson gave you the fix (read-your-writes) for that one case. This lesson generalises it, because replication lag produces an entire *family* of anomalies, and they are not all fixed by the same technique.

Consider four BookNook bug reports from one week, all caused by the same replication lag, all requiring different fixes:

| Report | Underlying anomaly | Model that prevents it |
|---|---|---|
| "I posted a review and the page says I have no reviews." | A client cannot see its own write | **Read-your-writes** |
| "I refreshed and my review disappeared, then came back." | A client sees a value, then an older value | **Monotonic reads** |
| "A reply to a review shows above the review it replies to." | Effect visible before cause | **Causal consistency** |
| "Two customers bought the last signed first edition." | Two writes both believed they were first | **Linearizability** |

One cause, four distinct guarantees, four distinct implementation techniques, four distinct costs. Without a vocabulary for these, teams do what teams always do: they add a `sleep(500)`, or they route everything to the primary, or they declare the bug unreproducible. Consistency models exist so that you can **name the guarantee you need, implement exactly that, and pay exactly that much**.

There is a second reason. Marketing material from database vendors uses words like "strong", "consistent", and "immediate" with no fixed meaning. A precise hierarchy lets you read a vendor's documentation and determine what they actually promise.

## 3. Real-world analogy

**A group chat about a surprise party, with messages delivered by unreliable couriers.**

Five friends. Each has their own notebook (replica) of the conversation. Couriers carry new messages between notebooks with variable delay.

- **Linearizability** = there is one shared whiteboard in one room, and everyone must walk into that room to read or write. The moment Ana writes "venue changed to the pub", nobody anywhere can read the old venue. Perfect agreement, but everyone walks to the room every time.
- **Sequential consistency** = everyone's notebook ends up with the messages in the *same order*, but that order need not match the real clock. All five agree the venue change came before the time change — even if, in real time, it was the other way round and nobody noticed.
- **Causal consistency** = if Bo's message is a *reply* to Ana's, every notebook shows Ana's first. But two unrelated messages from Cal and Dee may appear in different orders in different notebooks, and that is fine because neither caused the other.
- **Read-your-writes** = whatever else happens, when Bo looks at *his own* notebook, his own messages are always there.
- **Monotonic reads** = Bo's notebook never loses a message he has already seen. The courier may be slow, but it never takes pages away.
- **Monotonic writes** = Bo's two messages ("I'll bring cake", then "actually, I'll bring two cakes") are applied everywhere in that order, never reversed.
- **Writes-follow-reads** = if Bo read Ana's "venue is the pub" and then wrote "great, I'll book a table there", nobody ever sees Bo's booking message without Ana's venue message.
- **Eventual consistency** = every courier eventually arrives, so if everyone stops writing, all five notebooks will match. Until then, anything goes.

**Where the analogy breaks down:**

| Group chat | Distributed data store |
|---|---|
| Humans infer causality from meaning ("this is clearly a reply") | The system has no idea what a message *means*; causality must be tracked explicitly with version metadata, or it is lost |
| A person noticing an inconsistency can ask | An application silently acts on stale data and writes a wrong result based on it — the error is *amplified*, not surfaced |
| Messages only append; nothing is overwritten | Databases overwrite values, so concurrent writes can **destroy** data, not just reorder it (last-write-wins, §4.7) |
| Five participants, human timescales (seconds to minutes) | Thousands of replicas, microsecond timescales, millions of operations — anomalies that would be a once-a-year coincidence happen hundreds of times a day |
| Everyone eventually reads everything | Most data is never read again; the anomaly rate that matters is per *read*, not per *write* (section 9) |
| "Eventually" obviously means minutes | "Eventually" is formally **unbounded** — the model makes no promise about *when*. In practice it is a measurable distribution, and you must measure it (§4.6) |
| Nobody dies if the party venue is wrong | Oversold inventory, double charges, and leaked access revocations are money, trust, and sometimes law |

## 4. Detailed technical explanation

### 4.1 The hierarchy

Models form a partial order: model A is **stronger** than model B if every execution allowed by A is also allowed by B. Stronger = fewer permitted outcomes = fewer anomalies = more coordination.

```
                       STRONGEST (most coordination, highest latency)
                                      |
                        Strict serializability
                        (serializability + real-time order)
                                 /          \
                                /            \
                 Linearizability              Serializability
                 (single object,              (multi-object txns,
                  real-time order)              some legal order)
                                \            /
                                 \          /
                            Sequential consistency
                          (one global order, no real-time)
                                      |
                            Causal consistency
                     (happens-before order preserved)
                                      |
                 +--------------------+--------------------+
                 |          SESSION GUARANTEES             |
                 |  read-your-writes   monotonic reads     |
                 |  monotonic writes   writes-follow-reads |
                 +--------------------+--------------------+
                                      |
                            Eventual consistency
                        (replicas converge if writes stop)
                                      |
                                 (no guarantee)
                                      |
                       WEAKEST (no coordination, lowest latency)
```

Reading the implication arrows, downward:
- Strict serializability implies **both** linearizability and serializability.
- Linearizability implies sequential consistency implies causal consistency implies all four session guarantees implies eventual consistency.
- Linearizability and serializability are **incomparable in general**: neither implies the other, because they talk about different things (single objects versus multi-object transactions). Strict serializability is their combination.
- Causal consistency is the **strongest model available without sacrificing availability during a partition** — a genuinely important theoretical result, and the reason it keeps appearing in modern systems.

### 4.2 Linearizability, rigorously

> **Linearizability**: every operation appears to take effect **atomically at a single instant** between its invocation and its response, and that instant ordering is consistent with **real time** — if operation A's response happens before operation B's invocation in wall-clock time, then A's effect is ordered before B's.

Three parts, each load-bearing:

1. **Atomically at a single instant.** No partial visibility. The operation did not happen, then it did.
2. **Between invocation and response.** The system may place the instant anywhere in that window, which gives it freedom to reorder *concurrent* (overlapping) operations however it likes.
3. **Consistent with real time.** This is the clause that makes it expensive and that distinguishes it from everything below it. Non-overlapping operations are ordered by the clock, not by convenience.

The practical consequence, and the one-line definition worth memorising: **linearizability makes a replicated system behave exactly like a single copy of the data on a single machine.** That is why it is CAP's and PACELC's "C" (Lessons 59, 60).

A key implication: **a linearizable read must return a value at least as recent as any write that completed before the read began — even if the writer and reader are different clients that have never communicated.** That is why linearizability cannot be achieved by caching or by session stickiness. It requires the read to consult the authoritative state, which means a leader read or a read quorum, which means round trips (Lesson 60, §4.3).

### 4.3 Sequential consistency, serializability, strict serializability — disentangled

This is where almost everyone goes wrong. The confusion is resolvable with two questions: **what is being ordered**, and **does real time matter**.

| Model | Unit of ordering | Must respect real time? | Single global order? |
|---|---|---|---|
| **Sequential consistency** | Single operations | **No** | Yes |
| **Linearizability** | Single operations | **Yes** | Yes |
| **Serializability** | Multi-operation transactions | **No** | Yes (some serial order exists) |
| **Strict serializability** | Multi-operation transactions | **Yes** | Yes |

**Sequential consistency** requires that all replicas agree on *one* order of operations, and that each client's own operations appear in that order in the order the client issued them. It does **not** require that order to match wall-clock time. So: Ana writes `x=1` at 10:00:00 and gets a response; Bo reads `x` at 10:00:05 and legally gets the old value, as long as everyone agrees on a single consistent story. Sequential consistency is easy to implement with a single ordering node and no real-time coordination — which is also why it permits reads that look stale to an outside observer with a watch.

**Serializability** is the *transaction* concept from Lesson 50's isolation levels. It says: the outcome of executing a set of concurrent transactions is equivalent to *some* serial execution of them. It says nothing about which serial order, and nothing about real time. A serializable database is legally allowed to execute your transaction as though it ran at any point — including "before" a transaction that actually finished an hour earlier. In practice, that freedom permits a read-only transaction to see a stale snapshot and still be serializable.

**Strict serializability** = serializability + the real-time constraint. If transaction T1 committed before T2 started, every observer sees T1 before T2. This is what Spanner calls **external consistency** (Lesson 60, §4.5), and it is what most people *mean* when they say "strongly consistent", even when they say "serializable".

The one-paragraph summary to carry into an interview:

> Linearizability is about *single objects* and *real time*. Serializability is about *transactions* and *some* valid order. Neither implies the other: a linearizable key-value store has no transactions to serialise, and a serializable database may legally return a stale snapshot. Strict serializability is the intersection, and it is the most expensive guarantee in common use.

A concrete BookNook illustration of serializable-but-not-linearizable: BookNook runs a nightly serializable read-only transaction to total yesterday's revenue. A serializable system may legally place that transaction *before* an order that committed two minutes ago, so the total omits it. The database is not broken; it provided exactly what serializability promises. If the finance team needs the total to include everything committed before the report started, they need **strict** serializability, and they must ask for it by name.

### 4.4 Causal consistency and happens-before

**Causal consistency** guarantees: if operation A **happened before** operation B, then every replica applies A before B. Operations that are **concurrent** (neither happened before the other) may be applied in any order, and different replicas may choose different orders.

**Happens-before** (written `A → B`) is Lamport's relation, defined by exactly three rules:
1. If A and B are in the same process (same client session) and A occurs first, then `A → B`.
2. If A is a write and B is a read that returns A's value, then `A → B`.
3. Transitivity: if `A → B` and `B → C` then `A → C`.

If neither `A → B` nor `B → A`, the operations are **concurrent**, written `A ∥ B`. Concurrency here is not about wall-clock overlap; it means **no information flowed between them**.

**Vector clocks, conceptually.** To track happens-before without synchronised clocks, each replica keeps a vector of counters, one per replica:

```
VC = [ r1_count, r2_count, r3_count ]
```

Rules:
- A replica increments its own slot on every local write.
- Every write carries the writer's current vector.
- On receiving a write, a replica takes the element-wise maximum, then increments its own slot.

Comparison:
- `VA → VB` (A happened before B) iff every element of `VA` ≤ the corresponding element of `VB`, **and** at least one element is strictly less.
- If neither dominates, the writes are **concurrent** — a genuine conflict the system cannot resolve on its own.

```
VA = [2, 1, 0]   VB = [3, 1, 0]   ->  VA → VB      (B causally follows A)
VA = [2, 1, 0]   VB = [1, 2, 0]   ->  VA ∥ VB      (concurrent: CONFLICT)
```

The cost: metadata proportional to the number of replicas (or, in client-tracked variants, the number of actors), which must be stored, shipped, and pruned. This is the practical reason Dynamo-lineage systems that started with vector clocks often retreated to simpler schemes.

**Worked BookNook example — a reply appearing before the review it answers.**

BookNook replicates reviews asynchronously across three replicas, `R1` (us-east), `R2` (us-west), `R3` (eu-west).

```
t=0 ms    Ana (routed to R1) posts review A:
          "The binding on this edition falls apart after a month."
          R1 writes A with VC = [1,0,0]. R1 begins async replication.

t=15 ms   A arrives at R2.  R2 now has A, VC = [1,0,0].
          A has NOT yet arrived at R3 (slower trans-Atlantic path).

t=40 ms   Bo (routed to R2) READS A, then posts reply B:
          "Mine too — the publisher replaced it free of charge."
          Because Bo read A before writing B, rule 2 gives A → B.
          R2 writes B with VC = [1,1,0].  Replication begins.

t=55 ms   B arrives at R3 via a fast R2→R3 path.
          A has still not arrived at R3 (it took the congested R1→R3 path).

          WITHOUT causal consistency, R3 applies B immediately. A European
          reader now sees:
                "Mine too — the publisher replaced it free of charge."
          ...with no parent review. The reply is incoherent, and worse,
          it reads as an endorsement of a product whose defect is invisible.

          WITH causal consistency, R3 inspects B's VC = [1,1,0] and sees it
          requires R1's write #1, which R3 has not yet applied (R3's local
          vector is [0,0,0]). R3 BUFFERS B.

t=95 ms   A finally arrives at R3. R3 applies A ([1,0,0]), then releases the
          buffered B ([1,1,0]). European readers see A then B, always.
```

The cost of causal consistency here is **not** a synchronous round trip — R3 never blocked a client, it only delayed *applying* a write it already had. That is precisely why causal consistency is achievable while remaining available during a partition: the coordination is in the metadata, not in the critical path.

### 4.5 The four session guarantees

**Session guarantees** are promises scoped to a single client session rather than to the global system. They are cheap, they are what users actually notice, and they compose.

| Guarantee | Precise statement | BookNook anomaly without it | Implementation technique |
|---|---|---|---|
| **Read-your-writes** (read-after-write) | A read by a session always reflects all previous writes by that same session | Ana posts a review, the page reloads from a lagging replica, and her review is absent. She posts it again. BookNook now has duplicates. | (a) Route that user's reads to the leader for a window slightly longer than p99.9 replication lag; (b) **sticky sessions** to the replica that served the write; (c) **version/LSN tokens**: the write returns a log position, the client sends it with subsequent reads, and the replica waits (or redirects) until it has caught up to that position. Technique (c) is the most precise and is what Lesson 34 recommended |
| **Monotonic reads** | If a session reads a value, any later read by that session returns that value or a newer one — never an older one | Bo refreshes a product page three times. Reads land on replicas with 10 ms, 600 ms and 50 ms lag. The review count reads 42, then 41, then 42. Time appears to run backwards | Pin the session to one replica (consistent hashing on session ID, Lesson 57); or have the client remember the highest version it has seen and require replicas to meet or exceed it |
| **Monotonic writes** | Writes by a session are applied on every replica in the order the session issued them | Cal edits a review to fix a typo, then edits again to add a sentence. Replicas apply edit 2 then edit 1. The typo fix wins, and the added sentence silently vanishes | Serialise a session's writes through one replica/leader; or tag each write with a session sequence number and make replicas apply them in sequence |
| **Writes-follow-reads** (session causality) | If a session reads value V and then performs a write W, then W is ordered after V on every replica | Dee reads Ana's one-star review, then posts "I agree completely". A replica applies Dee's write before Ana's. Readers see agreement with nothing | Attach the causal dependency (version read) to the write, and have replicas buffer until the dependency is applied — the same mechanism as §4.4 |

**Read-your-writes plus monotonic reads** covers the overwhelming majority of complaints real users file. It is cheap — a token or a sticky route — and it does not require global coordination. If you adopt nothing else from this lesson, adopt these two.

A subtlety that bites in production: session guarantees are scoped to a *session*, and users have more than one. Ana posts a review on her laptop and checks on her phone. Different session, different token, no guarantee. If cross-device coherence matters, the token must be attached to the **user identity** (stored server-side in a session store, Lesson 2) rather than to the browser connection.

### 4.6 Eventual consistency, honestly

> **Eventual consistency**: if writes stop, all replicas will eventually converge to the same value.

Read that carefully, because the honest version is unflattering:

- **"If writes stop"** — a condition that never holds in a live system. The guarantee is stated about a quiescent state you will never observe.
- **"Eventually"** — **formally unbounded**. The model permits convergence after an arbitrary finite time. There is no SLA in the definition.
- **It says nothing about what reads return in the meantime.** A read may return any previously written value, in any order, including values older than one already returned to the same client — unless you add session guarantees on top.

So why is it everywhere? Because in practice, the *distribution* of convergence time is narrow and measurable, and the formal looseness never materialises. The engineering discipline is to **stop treating "eventually" as unknowable and start treating it as a monitored SLO**.

**How to measure convergence time** — four techniques, in increasing order of fidelity:

1. **Replica lag metric from the database.** Cassandra's hinted-handoff and `nodetool netstats`, Postgres's `pg_stat_replication.replay_lag`, DynamoDB's `ReplicationLatency`, Kafka's consumer lag. Cheap, always on, but measures the *pipeline*, not what a user would observe.
2. **Write-then-poll canary.** A background job writes a sentinel key with a timestamp every second, then reads it from **every** replica until it appears. Record the delta. This yields a real distribution: p50, p90, p99, p99.9, max.
3. **Read-path instrumentation.** Every read returns the version it served; the client compares against the highest version it has seen and emits the gap. This measures **user-visible staleness**, which is what you actually care about, and is the input to section 9's calculation.
4. **Probabilistically Bounded Staleness (PBS).** An analytical model (from Berkeley research) that estimates, for a given `N`, `W`, `R` and latency distribution, the probability that a read is stale and by how much. Useful for *predicting* the effect of a configuration change before you ship it.

BookNook's targets, typical for a three-region async setup: **p50 20 ms, p90 80 ms, p99 400 ms, p99.9 2 s, alert at p99 > 1 s**. Those numbers are the real meaning of "eventually" for BookNook, and they are what section 9 computes with.

### 4.7 Conflict resolution

When two replicas accept concurrent writes to the same key (`A ∥ B`), **something must decide**. Four strategies.

**1. Last-write-wins (LWW).** Attach a timestamp; the higher timestamp wins; the other write is discarded.

- **Pro**: trivial, stateless, O(1) metadata. Cassandra's default; DynamoDB's default for global tables.
- **The hazard, stated plainly**: LWW does not resolve conflicts, it **destroys data**, silently and permanently. There is no record that the losing write existed.
- **Worse**: the timestamp is usually a wall-clock reading from the writing node. If two nodes' clocks differ by 200 ms — ordinary, even with NTP — a write that genuinely happened *second* can lose to one that happened first. You then have data loss determined by clock skew, which is invisible in logs and unreproducible.
- **Safe when**: writes to a key come from one source, or the value is a full overwrite where losing an intermediate state is acceptable (e.g. a device's latest reported position).
- **Unsafe when**: the value is a set, a counter, a balance, or anything where two concurrent writes both carry real information. BookNook must never use LWW for a shopping cart — "add *Dune*" and "add *Neuromancer*" issued concurrently would resolve to one book.

**2. Vector clocks with sibling exposure.** Rather than silently picking, the store keeps both concurrent versions (**siblings**) and hands them to the application on read. Riak does this. Correct, but pushes work onto every reader, and sibling sets can grow without bound if the application never resolves them.

**3. CRDTs (Conflict-free Replicated Data Types).** Data types mathematically designed so that merging is **commutative, associative, and idempotent** — meaning replicas converge regardless of the order or duplication of updates, with no coordination at all.

*Counter example (G-Counter, grow-only).* Instead of one integer, store one counter per replica and define the value as the sum:

```
state: {R1: 3, R2: 5, R3: 2}         value = 3 + 5 + 2 = 10
R1 increments -> {R1: 4, R2: 5, R3: 2}   value = 11
merge(X, Y) = element-wise MAX of each replica's slot
```

Element-wise max is commutative, associative, and idempotent, so any merge order yields the same result, and no increment is ever lost. (A PN-Counter adds a second map for decrements, enabling subtraction.) This is exactly how BookNook can count "helpful" votes on a review across regions without coordination and without losing votes — whereas LWW on a plain integer would discard concurrent increments.

*Set example (OR-Set, observed-remove).* A naive "add/remove set" is not a CRDT: if one replica adds *Dune* and another concurrently removes it, the outcome depends on merge order. An OR-Set fixes this by tagging every add with a unique identifier:

```
adds:    { (Dune, tag-a1), (Neuromancer, tag-b7) }
removes: { tag-a1 }
value = elements in `adds` whose tag is not in `removes`
      = { Neuromancer }
```

A remove only cancels the specific add-instances it **observed**. A concurrent add with a new tag survives — giving "add wins" semantics for concurrent add/remove, which is almost always what a shopping cart should do: it is better to show the customer an item they removed than to lose an item they added.

- **Cost of CRDTs**: metadata growth (tombstones for removes must be retained until all replicas have seen them), and the hard constraint that **not every operation has a lawful merge**. "Decrement stock only if positive" is a conditional invariant across replicas; no CRDT provides it, because enforcing it requires knowing the global total — which requires coordination. CRDTs do not repeal Lesson 60.

**4. Application-level merge.** The store surfaces the conflicting versions and the application decides using domain knowledge. The most powerful option, and the most work. BookNook uses it for cart merge: union the items, sum quantities per ISBN, cap each at available stock, and prefer the most recent price quote.

| Strategy | Data loss risk | Metadata cost | Application burden |
|---|---|---|---|
| Last-write-wins | **High** (silent) | Lowest (one timestamp) | None |
| Vector clocks + siblings | None (nothing discarded) | O(replicas or actors) | High (must resolve on read) |
| CRDTs | None | Moderate–high (tombstones) | Low (merge is automatic) |
| Application merge | None if correct | Depends | Highest |

### 4.8 Choosing a model from the business invariant

The correct question is never "which model do I prefer?" It is:

> **"What statement must never be false, and what is the cost if it is false for 500 ms?"**

Write the invariant as a sentence first; the model follows mechanically.

| BookNook operation | Business invariant | Weakest acceptable model | Justification |
|---|---|---|---|
| Reserve the last copy of a title | "A physical copy is sold at most once" | **Linearizable** compare-and-set (strict serializable if it spans tables) | Two concurrent reservations must see each other. No session guarantee helps: the conflicting clients are strangers with no causal link |
| Charge a payment | "A customer is charged exactly once per order" | **Strict serializability** on the order+payment transaction, plus an idempotency key | Spans multiple objects and must respect real time against a retry arriving from the client |
| Revoke a session / change password | "A revoked credential stops working within the stated window" | **Linearizable** for the revocation write; bounded-staleness reads acceptable if the bound is published | Security boundary. The window must be an explicit, documented number, not an accident of lag |
| Apply a coupon | "A single-use coupon is redeemed once" | **Linearizable** CAS on the coupon record | Same shape as inventory; a counted resource |
| Add to cart | "A customer never loses an item they added" | **Causal + read-your-writes**, conflict resolution by OR-Set union | Concurrent adds must both survive; order between unrelated adds does not matter |
| Edit a review | "A user's own edits apply in the order made" | **Monotonic writes + read-your-writes** | The only actor is the author; a session-scoped guarantee is sufficient and cheap |
| Reply to a review | "A reply never appears without its parent" | **Causal** | Exactly the happens-before relation from §4.4 |
| Display review count | "The count trends toward the truth" | **Eventual** (G-Counter CRDT) | No decision depends on the exact value at an exact instant |
| Recommendations, "also bought" | "Suggestions are plausible" | **Eventual**, minutes of staleness fine | Derived data, rebuildable, no invariant |
| Search results | "New titles become findable promptly" | **Eventual**, with a published freshness target (e.g. 30 s) | Derived index (Lesson 53); the source of truth is elsewhere |

Notice what this table does: it assigns **four different models inside one product**, and each assignment is defended by a sentence about the business, not about the database. That is the deliverable from this lesson.


---

# Lesson 62 — Object, Block, and File Storage

*Phase 3, Topic 23*

## 1. Simple definition

There are exactly three ways to expose durable storage to a program, and almost every storage product you will ever use is one of them wearing a costume:

> **Block storage** hands you a numbered array of fixed-size chunks and no opinion about what you put in them.
> **File storage** hands you a tree of named files and directories that you open, seek within, and write to in place.
> **Object storage** hands you a flat dictionary of immutable blobs, addressed by a string key, over HTTP.

Terms defined before use:

- **Block**: a fixed-size unit of storage, typically 512 bytes or 4 KiB. A block device is addressed by **LBA** (logical block address) — an integer index. Block 0, block 1, block 9,218,604.
- **Namespace**: the set of names by which stored things can be referred to. Block storage has no namespace (only integers). File storage has a *hierarchical* namespace. Object storage has a *flat* namespace.
- **POSIX semantics**: the behaviour the C standard library promises for files — `open`, `read`, `write`, `seek`, `rename`, `flock`, byte-range partial writes, atomic rename within a directory, permission bits. "POSIX-compliant" is shorthand for "behaves like a local disk".
- **Immutable**: cannot be modified in place. To "change" an object you upload a whole new object under the same key, which creates a new version and replaces the old one.
- **Durability**: probability that stored data survives (Lesson 15). **Availability**: probability you can reach it right now (also Lesson 15). These are *different numbers* and conflating them is the single most common mistake in this topic.

The one-line mental model:

> Block is a disk. File is a shared folder. Object is a key-value store for large values over HTTP.

## 2. Why it exists

The three abstractions exist because three genuinely different problems exist, and nobody has produced one abstraction that solves all three without unacceptable cost.

**Problem 1 — a database needs to control its own layout.** PostgreSQL, MySQL/InnoDB and every LSM-tree engine (Lesson 47) care intensely about *where* bytes land, because they must guarantee that a write-ahead log record hits durable media before the corresponding page does (Lesson 43). They want a raw addressable array and the ability to issue `fsync` with meaningful semantics. Hand a database a shared network filesystem and it will either run slowly or, worse, run fast and lie about durability. That is block storage's job.

**Problem 2 — many machines need to see the same mutable files.** A legacy media-processing pipeline, a shared build cache, a CMS where twelve app servers all write into `/var/www/uploads`. These programs were written against `open()`/`write()` and cannot be rewritten today. That is file storage's job.

**Problem 3 — you have a hundred million whole blobs and essentially never modify one.** Book cover images. Review photos. Invoices. Log archives. Database backups. Video segments. Each one is written once, read many times, never edited, and must survive datacentre fires. The file abstraction is actively *wrong* here: you do not need `seek`, you do not need `flock`, you do not want a directory tree with a metadata server that becomes a bottleneck at a billion entries, and you certainly do not want to be limited to the capacity of one machine's disks. That is object storage's job.

There is also a hard architectural reason object storage exists: **application servers must be stateless** (Lesson 20). The moment a server writes an upload to its own local disk, that server is special — it holds the only copy of something. Autoscaling cannot terminate it. A load balancer (Lesson 21) cannot freely route the follow-up read anywhere. Deployments destroy data. Object storage is the escape hatch that keeps the compute tier disposable.

### The layering insight

These three are not three competing products. They are three *layers*, and the lower ones hold up the higher ones:

```
Object storage  (S3, GCS, Azure Blob, MinIO)
      ↑ built on
File storage    (XFS/ext4 on each storage node, or a distributed FS)
      ↑ built on
Block storage   (the physical or virtual disks in those nodes)
      ↑ built on
Physical media  (NVMe SSD, SATA SSD, spinning disk, tape)
```

A file system is a program that turns "array of numbered blocks" into "tree of named files" by storing its own index structures (inodes, extent trees, allocation bitmaps) inside those blocks. An object store is a distributed program that turns "lots of machines with file systems" into "one flat HTTP-addressable key space" by storing fragments across machines plus a replicated metadata index.

Understanding this stops you asking confused questions like "is S3 faster than EBS?" — they are at different layers, measuring different things, with the higher layer paying for durability and scale with latency.

## 3. Real-world analogy

**A warehouse district.**

- **Block storage** is a **row of identical numbered pigeonholes** in a vault you rent exclusively. The vault staff know nothing about contents. "Put this in slot 4,182." "Give me the contents of slot 9,004." It is *your* job to keep a ledger recording that the contract for Mrs Patel spans slots 4,182–4,195. Extremely fast, because the staff do nothing but fetch slots. Only one tenant may hold the vault key at a time.

- **File storage** is a **shared filing room** with cabinets, drawers and labelled folders. Many clerks can work in it simultaneously. You can open a folder and insert a page in the middle. Because many people share it, there must be rules: a clerk can put a "being edited" tag on a folder so others wait (that is a lock). If a clerk walks out mid-edit still holding the tag, everyone else is stuck.

- **Object storage** is a **vast self-storage depot with a front desk**. You hand over a sealed crate and a label: `users/4471/review-photos/9f3c.jpg`. You never go inside. To retrieve, you quote the exact label and the desk brings the whole crate. To change the contents you bring a new sealed crate with the same label; they replace it. The depot silently keeps pieces of your crate in three separate buildings across the city, so a fire destroys nothing. It is enormous, cheap, and you will *never* be allowed to walk in and edit page 3 of a document inside a crate.

The labels contain slashes and *look* like folders, but there are no folders. `users/4471/` is just a common prefix on a flat list of millions of labels; the desk can filter by prefix quickly, which makes it *feel* like a directory listing.

**Where the analogy breaks down:**

| Warehouse district | Real storage systems |
|---|---|
| The depot clerk physically walks to one crate | An object GET fans out to several machines, fetches erasure-coded fragments, and reconstructs the object — there is no single crate anywhere |
| Fetching a crate takes minutes | An object GET first byte is ~20–100 ms (typical); the cost is a *fixed latency floor*, not a wait |
| Vault slots are empty until used, and you pay per slot | Provisioned block volumes bill for allocated capacity whether or not you wrote a byte to it |
| A filing room clerk who leaves is noticed immediately | An NFS client that dies while holding a lock is invisible; the lock expires only after a timeout, often 45–90 seconds, during which other clients hang |
| The depot's "three buildings" is a safety policy | Eleven nines of durability is a *statistical model* of erasure coding and disk failure rates, not a guarantee — and it says nothing about you deleting the crate yourself |
| You can walk to the depot when the phone line is down | If the object store's API endpoint is unavailable, your data is perfectly durable and completely unreachable. Durability ≠ availability. |

That last row is the one that costs people their weekend. Eleven nines of durability and 99.9% availability means: your data is essentially never lost, and is *unreachable for roughly 43 minutes a month* within the SLA.

## 4. Detailed technical explanation

### 4.1 Block storage

A block device presents `N` fixed-size blocks, addressed `0 … N-1`. The only operations are: read block range, write block range, flush. There is no concept of a file, a name, or a user.

**Forms you will meet:**

| Form | What it is | Typical latency | Typical throughput |
|---|---|---|---|
| Local NVMe SSD (instance store) | A physical SSD inside the host machine | 50–150 µs | 2–7 GB/s, 500k+ IOPS |
| Network block volume (EBS gp3, Azure Managed Disk, GCP PD) | A virtual disk served over the provider's storage network | 0.5–2 ms | up to ~1 GB/s, 16k IOPS (gp3 default ceiling) |
| High-performance network volume (EBS io2 Block Express) | Same, with provisioned guarantees | 0.25–1 ms | ~4 GB/s, up to 256k IOPS |
| SAN LUN (on-premises) | A volume carved from a storage array over iSCSI/Fibre Channel | 0.3–3 ms | array-dependent |

**IOPS and throughput provisioning.** *IOPS* = I/O operations per second; *throughput* = bytes per second. They are separate limits and you hit whichever binds first. A volume rated 16,000 IOPS and 1,000 MB/s is doing 16,000 × 16 KiB = 256 MB/s with 16 KiB random reads (IOPS-bound), but 1,000 MB/s with 1 MiB sequential reads (throughput-bound). Cloud volumes also frequently have *burst credits*: a small volume may deliver 3,000 IOPS for a while, then collapse to a baseline proportional to its size. A database that was fine in testing and dies after 40 minutes in production has usually exhausted burst credit.

**Attachment.** A network block volume attaches to **exactly one instance at a time** in the normal configuration. This is not an arbitrary restriction — a conventional file system such as ext4 or XFS caches metadata in the host's RAM and assumes it is the only writer. Two hosts mounting the same ext4 volume read-write will corrupt it within seconds. Multi-attach modes exist but require a *cluster-aware* file system (GFS2, OCFS2) or raw access by a cluster-aware application; they are specialist tools, not a casual option.

**Snapshots.** A snapshot is a point-in-time, usually incremental, copy of a block volume, typically stored in object storage behind the scenes. Key properties to internalise:
- Snapshots are **crash-consistent** by default: they capture what was on disk at that instant, exactly as if you had pulled the power cable. A database can recover from that via its write-ahead log, but it is *not* the same as an application-consistent backup. For application consistency you must quiesce writes or flush and freeze the file system first.
- They are incremental in storage but *restore to a full volume*, and restored volumes are often **lazily hydrated** — blocks are pulled from the snapshot on first access, so the restored volume is slow for the first hours. This wrecks naive RTO estimates (Lesson 63).

### 4.2 File storage

File storage exposes a hierarchical namespace with POSIX semantics, usually over a network protocol:

- **NFS** (Network File System) — the Unix standard. NFSv3 is stateless with a separate lock manager; NFSv4 is stateful with leases and integrated locking.
- **SMB/CIFS** — the Windows standard, used by Azure Files and FSx.
- **Managed services** — Amazon EFS, Azure Files, Google Filestore, FSx for Lustre/NetApp/Windows.

The appeal is simple: unmodified applications work, and **many instances mount the same tree concurrently**. That is the one thing block storage cannot do.

**POSIX locking.** Two mechanisms: advisory whole-file locks (`flock`) and byte-range locks (`fcntl`). Both are *advisory* — they only work if every participant checks them. Over a network they require the server to track which client holds which lock, which means the server must detect dead clients, which means timeouts.

**Why network file systems have surprising failure modes.** This deserves its own list, because engineers coming from local disks are repeatedly caught out:

1. **Hard mounts hang forever.** The default NFS `hard` mount means a server outage turns `read()` into an *uninterruptible* block. Your process goes into `D` state. You cannot `kill -9` it. Thread pools fill, the app stops answering health checks, and the load balancer ejects a host whose *CPU is idle*. `soft` mounts return errors instead — and risk silent data corruption, because many applications do not check the return value of `write()`.
2. **Metadata operations are network round trips.** `ls -l` on a 50,000-entry directory is 50,000 `GETATTR` calls. Local: 20 ms. Over NFS: 30+ seconds.
3. **Close-to-open consistency.** NFS does not promise that client B sees client A's write until A closes the file and B re-opens it. Code that works locally because of the shared page cache breaks subtly over NFS.
4. **Throughput can be credit-based and capacity-linked.** EFS in bursting mode scales throughput with stored bytes; a nearly empty file system gets very little sustained throughput.
5. **Lock recovery storms.** After a server restart there is a grace period (typically 45–90 s) during which only lock reclaims are accepted. Every client stalls simultaneously.

### 4.3 Object storage

An object store maps **key → (bytes, metadata)**. The canonical API is Amazon S3; Google Cloud Storage, Azure Blob Storage, Cloudflare R2, Backblaze B2, MinIO and Ceph RGW all expose the same shape, and most are S3-API-compatible.

**The flat namespace.** A bucket contains objects with keys that are just strings up to 1,024 bytes. `covers/isbn/9780141036144/large.webp` contains slashes, and the API offers `prefix=` and `delimiter=/` on `ListObjectsV2` so tooling can *render* it as folders. But:
- There is no `mkdir`, and empty "folders" do not exist.
- There is no atomic `rename`. "Renaming" is COPY + DELETE: two operations, not atomic, and billed as a full copy. Renaming a 10 TB "directory" means copying 10 TB.
- Listing is lexicographic and paginated (1,000 keys per page by default).

**Immutability.** `PUT` of an existing key replaces the whole object. There is no byte-range write. There is byte-range *read* (`Range:` header), which is why formats like Parquet (Lesson 56) and video segmentation work so well on object storage.

**Durability: what eleven nines actually means.** The headline figure 99.999999999% (11 nines) is an annual durability estimate: the expected probability that a given object survives a year. Equivalently, store 10 million objects and you would expect to lose one roughly every 10,000 years.

It comes from **erasure coding**, not from "three copies". Erasure coding splits an object into `k` data fragments and computes `m` parity fragments, storing all `k+m` on independent failure domains (different disks, racks, and usually different datacentres within a region). Any `k` of the `k+m` fragments reconstruct the object. A common configuration is `k=6, m=3`: 9 fragments, 1.5× storage overhead, tolerant of any 3 simultaneous losses. Compare with 3× replication: 3.0× overhead, tolerant of 2 losses. Erasure coding is strictly better on both axes, at the cost of CPU and more network fan-out per read.

The system also runs continuous **scrubbing**: background processes read fragments, verify checksums, and rebuild anything missing or corrupt, usually within hours. The 11-nines number is the output of a model combining fragment count, failure domain independence, disk annualised failure rate (~1–2%), and repair time.

**What the number does *not* mean**, and this is the part that matters for Lesson 63:
- It does not protect you from `DELETE`. You deleting an object is a *successful operation*, not a failure.
- It does not protect you from your application writing garbage over a good key.
- It does not cover credential compromise.
- It is **durability, not availability**. S3 Standard's availability SLA is 99.9%; the design target is 99.99%. Durability eleven nines, availability four nines at best. Those differ by seven orders of magnitude.
- It is per-object-per-year, within one region. Regional destruction is outside the model — hence cross-region replication (Lesson 39).

**Consistency.** Historically S3 was eventually consistent for overwrites and list-after-write, and a generation of engineers built workarounds. **Since December 2020, S3 provides strong read-after-write consistency** for all operations: a successful `PUT` is immediately visible to any subsequent `GET` or `LIST`, globally within the region, at no extra cost. GCS has always been strongly consistent. This changes designs: the old "write to S3, then poll until it appears" pattern is obsolete. Note the remaining caveat: strong consistency applies to the object store, but *caches in front of it* (CDN, Lesson 31) still have their own TTLs — a stale CDN copy is not an object-store consistency problem.

**Storage classes.** You trade retrieval latency, retrieval cost and minimum storage duration for a lower per-GB price. Typical (approximate, us-east-1-like, per GB-month):

| Class | Storage $/GB-mo | Retrieval | First-byte latency | Min duration | Min billable size |
|---|---|---|---|---|---|
| Standard | 0.023 | free | ms | none | none |
| Intelligent-Tiering | 0.023 → 0.0036 | free | ms | none | 128 KB monitored |
| Standard-IA | 0.0125 | $0.01/GB | ms | 30 days | 128 KB |
| One Zone-IA | 0.01 | $0.01/GB | ms | 30 days | 128 KB |
| Glacier Instant Retrieval | 0.004 | $0.03/GB | ms | 90 days | 128 KB |
| Glacier Flexible Retrieval | 0.0036 | $0.01/GB (bulk) | 1–5 min (expedited) to 5–12 h (bulk) | 90 days | 40 KB |
| Glacier Deep Archive | 0.00099 | $0.02/GB (bulk) | 12–48 h | 180 days | 40 KB |

The **minimum duration** and **minimum billable size** rules are where people lose money. Put 15 KB thumbnails in Standard-IA and you are billed for 128 KB each — 8.5× your actual bytes, at 54% of the Standard price per GB, so you *increased* cost by about 4.6×. Delete a Deep Archive object after 10 days and you still pay for 180.

**Lifecycle policies** are declarative rules on a bucket: "objects under prefix `invoices/` transition to Standard-IA at 30 days, Glacier Deep Archive at 365 days, expire at 2,557 days (7 years)." They run asynchronously, typically once a day. Transitions cost a per-object fee (~$0.01–0.05 per 1,000), which dominates for small objects.

**Versioning** keeps every generation of a key. A `DELETE` on a versioned bucket writes a **delete marker** rather than removing bytes — the object is recoverable. This is the first real line of defence against accidental deletion, but note: *versions still cost money*, so pair versioning with a lifecycle rule expiring noncurrent versions after N days. A bucket with versioning on and no expiry rule grows forever.

**Presigned URLs.** A presigned URL embeds a signature, an expiry, and the permitted operation, computed with your credentials but usable by anyone holding the URL. It lets a browser `PUT` directly into the bucket, or `GET` a private object, *without the bytes passing through your servers and without exposing credentials*. Critical properties: they expire (minutes to hours), they are bearer tokens (anyone with the link can use it until expiry), and you can bind conditions — content type, maximum size, required key prefix — using POST policies.

**Multipart upload.** Large objects are uploaded as independent parts (5 MiB minimum, up to 10,000 parts), then completed with a manifest. Benefits: parallelism (saturate the link), resumability (retry one failed part, not 4 GB), and uploading streams of unknown length. Required above 5 GiB. Trap: **incomplete multipart uploads consume billable storage invisibly** — they do not appear in `ListObjectsV2`. Always add a lifecycle rule to abort incomplete uploads after 7 days.

**Event notifications.** The bucket emits an event (to a queue, topic, or function) when an object is created or deleted. This is the standard way to trigger derivative work — thumbnailing, virus scanning, indexing. Delivery is **at-least-once**, so consumers must be idempotent, and ordering is not guaranteed.

### 4.4 The rule that follows from all this

> **Application servers must never store user uploads on local disk.**

Not "should avoid" — must not. Local disk on an app server breaks statelessness (Lesson 20), survives neither autoscaling nor deployment, cannot be read by sibling instances behind the load balancer (Lesson 21), has no durability story, and silently fills up. The only legitimate uses of an app server's local disk are: the OS, the application binary, ephemeral scratch for a single request, and local caches that can be lost without consequence.

## 5. Request / data flow

The flow below is a BookNook reviewer uploading a photo from the browser, straight to object storage, with the application acting only as an authoriser.

1. The browser presents a review form and the user selects `kitchen-shelf.jpg`, 3.4 MB, `image/jpeg`.
2. The browser calls `POST /api/review-photos/presign` on the BookNook API with the filename, MIME type and byte size.
3. The API authenticates the session (Lesson 2), checks the user is allowed to review this book, and validates the declared size and MIME type against policy (≤ 10 MB, image types only).
4. The API generates a **server-chosen key**: `uploads/2026/10/09/usr_4471/01JAB5Z8QF.jpg`. It never uses the client-supplied filename as the key — that is a path-injection and overwrite vector.
5. The API creates a presigned `PUT` URL (or a POST policy) valid for 300 seconds, constrained to that exact key, that content type, and a maximum content length. It records a row in the database: `review_photo(id, user_id, key, state='pending')`.
6. The API returns `{ uploadUrl, key }`. **No image bytes have touched the API.**
7. The browser issues `PUT <uploadUrl>` with the raw bytes directly to the object store endpoint. For a file this size it is a single PUT; above ~100 MB the client would switch to multipart.
8. The object store validates the signature, expiry and conditions, writes the bytes, erasure-codes them across failure domains, and returns `200 OK` with an `ETag`.
9. The object store emits an **ObjectCreated** event to a queue.
10. A worker consumes the event, downloads the object, verifies it is genuinely a JPEG by magic bytes (not by extension), runs a content-safety check, and generates derivatives: `…/thumb.webp` (320 px), `…/medium.webp` (800 px). It PUTs each derivative under a deterministic key.
11. The worker updates the database row to `state='ready'` and stores image dimensions. The database holds **metadata and the key — never the bytes**.
12. The browser polls or receives a push, then renders the review.
13. A visitor later loads the review page. The HTML references `https://cdn.booknook.example/uploads/2026/10/09/usr_4471/01JAB5Z8QF/medium.webp`.
14. The CDN (Lesson 31) checks its edge cache. On a miss it fetches from the bucket as origin using an origin access identity, caches it with a long TTL (the key is immutable, so `Cache-Control: public, max-age=31536000, immutable` is safe), and returns it.
15. A lifecycle policy later transitions the original full-resolution object to Glacier Instant Retrieval after 90 days; derivatives stay in Standard because they are what gets served.

The property to notice: **at no point did a 3.4 MB payload traverse an application server.** The API handled two small JSON requests. That is the difference between needing 4 app servers and needing 40.

## 6. ASCII diagram

```
            ┌──────────────────────────────────────────────────────────┐
            │                        BROWSER                            │
            └───┬───────────────────────────────┬──────────────────────┘
                │ (1) POST /presign (JSON, ~1KB)│ (2) PUT bytes (3.4 MB)
                ▼                               │  DIRECT — bypasses app
      ┌───────────────────┐                     │
      │  Load Balancer    │  (Lesson 21)        │
      └─────────┬─────────┘                     │
                ▼                               │
      ┌───────────────────┐                     │
      │  App servers      │  STATELESS          │
      │  (Lesson 20)      │  no local uploads!  │
      └────┬─────────┬────┘                     │
           │         │ signs URL                │
           │         └──────────────────────────┤
           ▼                                    ▼
   ┌──────────────┐              ┌────────────────────────────────┐
   │  PostgreSQL  │              │       OBJECT STORAGE           │
   │  metadata    │              │  bucket: booknook-media        │
   │  key, state  │              │  flat keys, immutable objects  │
   └──────┬───────┘              │  erasure-coded k=6 m=9 across  │
          │                      │  3+ availability zones         │
          │                      └───────┬──────────────┬─────────┘
          │                              │ event        │ origin fetch
          │                              ▼              ▼
          │                      ┌──────────────┐  ┌──────────┐
          │                      │ Queue→Worker │  │   CDN    │ (Lesson 31)
          │                      │ derivatives  │  │  edge    │
          │                      └──────┬───────┘  └────┬─────┘
          └──────── updates ────────────┘               │ cached bytes
                                                        ▼
                                                    end users

   ── THE THREE ABSTRACTIONS, STACKED ──────────────────────────────

   ┌─────────────────────────────────────────────────────────────┐
   │ OBJECT   key → immutable blob, HTTP, flat namespace         │
   │          PUT/GET/DELETE/LIST · no seek-write · no rename    │
   ├─────────────────────────────────────────────────────────────┤
   │ FILE     /a/b/c.txt → inode, POSIX, hierarchical, shared    │
   │          open/read/write/seek/flock · many hosts mount it   │
   ├─────────────────────────────────────────────────────────────┤
   │ BLOCK    LBA 0..N-1 → 4 KiB chunks, no names, one host      │
   │          read(lba,n) / write(lba,n) / flush                 │
   ├─────────────────────────────────────────────────────────────┤
   │ MEDIA    NVMe · SSD · HDD · tape                            │
   └─────────────────────────────────────────────────────────────┘
```

## 7. Detailed example

### 7.1 BookNook

BookNook stores four distinct classes of bytes, and each lands on a different abstraction. Mapping them correctly is the entire exercise.

| Data | Abstraction | Where | Why |
|---|---|---|---|
| PostgreSQL data files and WAL | **Block** (gp3, 2,000 GB, 16,000 IOPS, 1,000 MB/s) | one volume, one primary instance | The engine needs `fsync` semantics and low-latency random 8 KiB page I/O; shared access is handled by replication (Lesson 32), not by sharing a disk |
| Book cover images (3 renditions each) | **Object** → CDN | `booknook-media/covers/...` | Write-once, read 60 M times/day, globally distributed, must survive AZ loss |
| User review photos | **Object** | `booknook-media/uploads/...` | Untrusted uploads, unbounded growth, uneven access (hot for 2 weeks then cold) |
| Invoices (PDF) | **Object**, lifecycle to archive | `booknook-docs/invoices/...` | Legally retained 7 years, read approximately never after 60 days |
| Nightly DB backups | **Object**, Glacier, cross-region | `booknook-backups/...` | Lesson 63 |
| Legacy royalty-reporting tool from 2011 that `mmap`s files in `/mnt/royalties` and is run by three machines | **File** (EFS/NFS) | `/mnt/royalties` | It cannot be rewritten this quarter; it needs POSIX and shared access. This is a *deliberate, bounded* exception |

The cover-image path in detail: a publisher's feed drops a 4,000 × 6,000 px TIFF. A worker converts it to `large.webp` (1,200 px, ~250 KB), `medium.webp` (600 px, ~60 KB), `thumb.webp` (160 px, ~15 KB), and PUTs each under `covers/isbn/9780141036144/{size}.webp`. The product page emits CDN URLs. Because every key is content-addressed by ISBN and size and never mutated, the CDN TTL is one year and the origin sees only cache misses — roughly 4% of requests after warm-up.

The review-photo path adds the trust problem. Uploads are untrusted, so: the key is server-generated; the content type is pinned in the presigned policy; the worker verifies magic bytes rather than extension; derivatives are re-encoded (which strips EXIF GPS coordinates, a real privacy leak); and the bucket serves through the CDN with `Content-Disposition` and a restrictive `Content-Security-Policy`, so a crafted "image" that is actually HTML cannot execute in BookNook's origin.

### 7.2 A contrasting example — a hospital PACS imaging archive

A hospital's **PACS** (Picture Archiving and Communication System) stores CT, MRI and X-ray studies. One CT study is 300–2,000 individual DICOM images totalling 150 MB–1.5 GB. The hospital produces ~400 studies a day.

Superficially identical to BookNook — big immutable files — but three constraints flip decisions:

1. **Retention is 7–30 years by law**, and volume grows ~180 TB/year. Deep archival tiers are not an optimisation, they are the only affordable option. Studies older than 12 months move to Glacier Deep Archive; a radiologist requesting a 2015 comparison study triggers a restore job and is told "available in 12 hours", which is clinically acceptable for planned comparisons and unacceptable for emergencies — so the last 12 months stay in instant-access tiers.
2. **The viewing workstations run a 1990s DICOM application that only speaks file paths.** So the hot tier is a **file system** (FSx/NetApp over NFS), fronted by a cache, with a gateway that stages objects out of the archive onto the NFS mount on demand. This is the classic hybrid: object storage for the archive, file storage for the legacy access path.
3. **Deletion is adversarial.** Records must be provably unaltered for litigation, so the archive uses **object lock in compliance mode**: no one, including the root account, can delete or overwrite an object before its retention date expires.

Same bytes-shaped problem; different legal and legacy constraints produce a different architecture. That is what "it depends" actually means — it depends on retention law, access latency tolerance, and whether the consuming application can speak HTTP.

## 8. Code or configuration example

> **Illustrative only — not production-ready.** Error handling is minimal, credentials are assumed to come from the instance role, policies are simplified, and nothing here has been hardened or load-tested. Treat it as a readable sketch of the shapes involved.

```python
# booknook/media.py — presigned direct-to-object-store upload (ILLUSTRATIVE)
import boto3, uuid, datetime
from botocore.config import Config

BUCKET = "booknook-media"
MAX_BYTES = 10 * 1024 * 1024          # hard cap enforced by the STORE, not just the browser

s3 = boto3.client(
    "s3",
    config=Config(
        signature_version="s3v4",      # v4 required for presigned POST policies + SSE-KMS
        retries={"max_attempts": 5, "mode": "adaptive"},  # adaptive backs off on 503 SlowDown
    ),
)

def presign_review_upload(user_id: int, content_type: str) -> dict:
    if content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise ValueError("unsupported type")          # never trust the client's declared type alone

    today = datetime.date.today()
    # Key is SERVER-generated. Never embed the client filename: it enables
    # path traversal ("../"), overwrite of other users' keys, and unicode spoofing.
    key = f"uploads/{today:%Y/%m/%d}/usr_{user_id}/{uuid.uuid4().hex}"

    post = s3.generate_presigned_post(
        Bucket=BUCKET,
        Key=key,
        Fields={"Content-Type": content_type},
        Conditions=[
            {"Content-Type": content_type},           # pin the type the browser may send
            ["content-length-range", 1, MAX_BYTES],   # the STORE rejects oversize uploads
            ["starts-with", "$key", f"uploads/{today:%Y/%m/%d}/usr_{user_id}/"],
        ],
        ExpiresIn=300,                                # 5 minutes: a presigned URL is a bearer token
    )
    return {"url": post["url"], "fields": post["fields"], "key": key}
```

```python
# Multipart upload for a large object (e.g. a 40 GB DB dump) — ILLUSTRATIVE
from boto3.s3.transfer import TransferConfig

cfg = TransferConfig(
    multipart_threshold=100 * 1024 * 1024,  # below this, a single PUT is cheaper (fewer requests)
    multipart_chunksize=64 * 1024 * 1024,   # 64 MiB parts: 40 GB / 64 MiB = 640 parts, well under 10,000
    max_concurrency=16,                     # 16 parallel parts to saturate a 10 Gbps link
    use_threads=True,
)
s3.upload_file(
    "/var/backups/booknook-2026-10-09.dump", "booknook-backups",
    "pg/2026/10/09/full.dump",
    ExtraArgs={
        "StorageClass": "STANDARD_IA",      # written once, read only during a restore
        "ServerSideEncryption": "aws:kms",  # see Lesson 63 on key management
        "ChecksumAlgorithm": "SHA256",      # store checks the hash; protects against wire corruption
    },
    Config=cfg,
)
```

```json
// Bucket lifecycle configuration — ILLUSTRATIVE
{
  "Rules": [
    {
      "ID": "abort-orphaned-multipart",
      "Status": "Enabled",
      "Filter": { "Prefix": "" },
      // Incomplete multipart uploads are BILLED but invisible to ListObjectsV2.
      // Without this rule they accumulate forever. Every bucket should have it.
      "AbortIncompleteMultipartUpload": { "DaysAfterInitiation": 7 }
    },
    {
      "ID": "review-photo-originals-cool-down",
      "Status": "Enabled",
      "Filter": { "Prefix": "uploads/" },
      "Transitions": [
        // 90 days matches Glacier IR's minimum storage duration: moving earlier
        // would incur an early-deletion charge if the object were later removed.
        { "Days": 90, "StorageClass": "GLACIER_IR" }
      ]
    },
    {
      "ID": "invoices-7-year-retention",
      "Status": "Enabled",
      "Filter": { "Prefix": "invoices/" },
      "Transitions": [
        { "Days": 30,  "StorageClass": "STANDARD_IA" },
        { "Days": 365, "StorageClass": "DEEP_ARCHIVE" }
      ],
      // 2557 days = 7 years + 2 leap days. Expiry is the legal retention ceiling.
      "Expiration": { "Days": 2557 }
    },
    {
      "ID": "expire-old-versions",
      "Status": "Enabled",
      "Filter": { "Prefix": "" },
      // Versioning protects against accidental delete, but versions cost money.
      // Without this, a bucket with versioning grows without bound.
      "NoncurrentVersionExpiration": { "NoncurrentDays": 30 }
    }
  ]
}
```

```bash
# Block storage: what the DB volume actually looks like (ILLUSTRATIVE)
lsblk                                   # nvme1n1 is the attached network block volume: raw blocks, no names
mkfs.xfs -f /dev/nvme1n1                # a FILE SYSTEM is now built ON TOP OF the block device
mount -o noatime /dev/nvme1n1 /var/lib/postgresql   # noatime: stop a read from causing a metadata write

# Measure the volume honestly before trusting its rated IOPS.
# 8k random reads mirror a Postgres page-read workload far better than a sequential test.
fio --name=pg-like --filename=/var/lib/postgresql/fio.tmp --size=8G \
    --rw=randread --bs=8k --iodepth=32 --numjobs=4 --direct=1 --runtime=300 --time_based

# File storage: the shared mount for the legacy royalty tool (ILLUSTRATIVE)
# hard   = retry forever on server outage (safe for data, DANGEROUS for liveness: uninterruptible hangs)
# intr   = allow signals to interrupt (ignored on modern kernels; shown for discussion)
# noresvport = reconnect from a new source port after a network blip, avoiding a stuck TCP session
mount -t nfs4 -o hard,noresvport,rsize=1048576,wsize=1048576 \
      fs-0abc.efs.internal:/ /mnt/royalties
```

## 9. Numerical example

**Goal:** monthly storage bill for BookNook's media, and the saving from correct tiering.

**Assumptions (stated explicitly; all prices are typical/approximate list prices for a us-east-1-like region, GB = 10⁹ bytes):**

- Catalogue: 2,000,000 books × 3 renditions = 6,000,000 cover objects. Sizes: thumb 15 KB, medium 60 KB, large 250 KB → 325 KB per book.
- Review photos: 8,000,000 originals averaging 1.2 MB, plus two derivatives averaging 0.2 MB combined → 1.4 MB per photo. Growth 120,000 photos/day.
- Invoices: 40,000,000 PDFs × 80 KB.
- Traffic: 60,000,000 cover-image requests/day; CDN hit ratio 96%; average delivered object 90 KB (most requests are thumb/medium).
- Prices: Standard $0.023/GB-mo; Standard-IA $0.0125/GB-mo (128 KB min billable, 30-day min); Glacier IR $0.004/GB-mo; Deep Archive $0.00099/GB-mo. PUT $0.005/1,000; GET $0.0004/1,000. Internet egress $0.09/GB. Month = 30 days.

**Step 1 — Cover images, total bytes.**
2,000,000 × 325 KB = 650,000,000 KB = 650,000 MB = **650 GB**.
Standard: 650 × $0.023 = **$14.95/month**. Trivial. Leave them in Standard — they are served constantly.

**Step 2 — The thumbnail trap, quantified.**
Someone proposes moving the 2,000,000 thumbnails to Standard-IA "because they're small".
- Actual bytes: 2,000,000 × 15 KB = 30,000,000 KB = **30 GB** → Standard cost 30 × $0.023 = **$0.69**.
- Billable bytes in IA: minimum 128 KB each → 2,000,000 × 128 KB = 256,000,000 KB = **256 GB** → 256 × $0.0125 = **$3.20**.
- Result: the "saving" makes it **4.6× more expensive**, before retrieval charges. Minimum billable size beats the per-GB price for small objects.

**Step 3 — Review photos, total bytes.**
8,000,000 × 1.4 MB = 11,200,000 MB = **11,200 GB**.
All-Standard: 11,200 × $0.023 = **$257.60/month**.

**Step 4 — Review photos, tiered.** Access is heavily recency-biased: assume the most recent 90 days (120,000/day × 90 = 10,800,000 photos… which exceeds the corpus, so instead assume 15% of the corpus is "recent/hot").
- Hot 15%: 11,200 × 0.15 = 1,680 GB in Standard → 1,680 × $0.023 = **$38.64**.
- Cold 85%: 11,200 × 0.85 = 9,520 GB. The *derivatives* (0.2 MB of the 1.4 MB, i.e. 14.3%) must stay instantly servable, so only the originals (85.7%) move: 9,520 × 0.857 = 8,159 GB to Glacier IR → 8,159 × $0.004 = **$32.64**; remaining 9,520 − 8,159 = 1,361 GB stays Standard → 1,361 × $0.023 = **$31.30**.
- Tiered total: 38.64 + 32.64 + 31.30 = **$102.58/month**, versus $257.60. Saving **$155.02/month (60%)**.

**Step 5 — Invoices.**
40,000,000 × 80 KB = 3,200,000,000 KB = 3,200,000 MB = **3,200 GB**.
- All-Standard: 3,200 × $0.023 = **$73.60**.
- Tiered: assume 12.5% (400 GB) is under one year old → Standard-IA 400 × $0.0125 = **$5.00**; 2,800 GB older → Deep Archive 2,800 × $0.00099 = **$2.77**. Total **$7.77**. Saving **$65.83/month (89%)**.
- Note the objects are 80 KB, under the 128 KB IA minimum: billed as 128 KB, i.e. 400 GB × (128/80) = 640 GB → 640 × $0.0125 = **$8.00**, not $5.00. Deep Archive's minimum is 40 KB, so 80 KB objects bill at true size there. Corrected tiered total: **$10.77**.

**Step 6 — Requests.**
- Origin GETs: 60,000,000/day × 4% miss = 2,400,000/day = 72,000,000/month → (72,000,000 ÷ 1,000) × $0.0004 = 72,000 × $0.0004 = **$28.80**.
- PUTs: 120,000 photos/day × 3 objects (original + 2 derivatives) = 360,000/day = 10,800,000/month → 10,800 × $0.005 = **$54.00**.
- Request total: **$82.80/month** — which is *larger than the cover storage bill*. Request cost is routinely underestimated.

**Step 7 — Egress, the line that dominates.**
- Naive (no CDN): 60,000,000/day × 90 KB = 5,400,000,000 KB/day = 5,400 GB/day = 162,000 GB/month × $0.09 = **$14,580/month**.
- With CDN at 96% hit: origin egress = 162,000 × 0.04 = 6,480 GB × $0.09 = **$583.20/month** to the CDN (and $0 on providers that waive egress to their own CDN), plus CDN delivery of 162,000 GB at a negotiated $0.02–0.085/GB = **$3,240–$13,770**.
- Teaching point: the CDN's value here is not primarily latency, it is that **egress, not storage, is the bill**. Storage of 15 TB costs ~$120/month; moving it costs ~$4,000–15,000/month.

**Step 8 — Totals.**

| Line | Untiered, no CDN | Tiered, CDN at $0.03/GB |
|---|---|---|
| Cover storage | $14.95 | $14.95 |
| Review photo storage | $257.60 | $102.58 |
| Invoice storage | $73.60 | $10.77 |
| Requests | $82.80 | $82.80 |
| Egress / delivery | $14,580.00 | $583.20 + $4,860.00 |
| **Total** | **$15,008.95** | **$5,654.30** |

A 62% reduction, of which the overwhelming majority comes from the CDN, not from storage classes. Optimise the largest line first.

## 10. Trade-offs

**Object storage buys scale and durability with latency, semantics and request cost.**

| You gain | You pay |
|---|---|
| Effectively unlimited capacity — no volume to resize, no sharding | Every access is an HTTP round trip: 20–100 ms first byte (typical), versus 0.1 ms on local NVMe. ~300× worse latency floor |
| 11 nines durability, multi-AZ, with no work from you | You lose `seek`-write, `rename`, `append`, and directory semantics. Workloads that mutate in place must be redesigned |
| Pay only for bytes stored, with tiering down to $0.00099/GB-mo | Per-request charges that can exceed storage charges for small-object workloads; retrieval fees that make a cheap tier expensive if you read it |
| Bytes bypass your servers (presigned URLs), so compute scales independently of payload size | Security moves into signature policies and bucket policies — a misconfigured bucket is a public breach, and there is no filesystem permission model to fall back on |
| Strong read-after-write consistency since 2020 | Caches in front of it (CDN) are still eventually consistent; you must version keys or purge |

**Block storage buys latency and semantics with a single-writer limit and a capacity ceiling.** You get microsecond-to-millisecond I/O and true `fsync`, which is why every serious database sits on it. You pay by being bound to one instance, by provisioning (and paying for) capacity and IOPS in advance whether you use them or not, and by being capped at the largest volume the provider offers.

**File storage buys compatibility with everything else.** Nothing beats "the application already works". You pay roughly 2–10× the per-GB price of object storage, you inherit NFS's hang-on-failure behaviour, metadata operations cost network round trips, and throughput is often capacity- or credit-linked.

**The cross-cutting trade-off: coupling to the provider.** The S3 API is a de-facto standard, so object storage is less locked-in than it looks (R2, MinIO, Ceph, GCS all speak it). Egress pricing, not the API, is the lock-in — moving 100 TB out costs roughly $9,000 at $0.09/GB. This is a *commercial* trade-off masquerading as a technical one.

**When latency genuinely matters**, the honest answer is a layered one: object storage as the durable system of record, with a block-backed or memory cache (Lesson 28) in front. You do not choose one; you choose which is authoritative.

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| Object store regional API outage (durability intact, availability lost) | Cover images and review photos fail to load; uploads return errors; the site otherwise works | Spike in 5xx/503 from the storage SDK; synthetic canary PUT/GET failing; provider status page | CDN continues serving cached objects (the 96% that are hot), so impact is partial. Uploads queue client-side and retry with exponential backoff and jitter. Full recovery when the provider restores; cross-region replica buckets (Lesson 39) can be promoted if the read path is region-aware |
| Throttling — `503 SlowDown` from too many requests on a hot key prefix | Intermittent image failures and slow uploads under load | Elevated 503 rate with normal provider status; request rate near the per-prefix ceiling | SDK adaptive retry absorbs short bursts. Structural fix: spread keys across prefixes (a hash prefix or date partitioning) so the store can split partitions; add CDN/caching to cut origin request rate |
| Block volume detach or host failure under the primary database | Writes fail; the whole site's dynamic content errors | Health check fails; `dmesg` I/O errors; volume status alarm | Failover to a replica (Lesson 32) promoted to primary; the old volume is reattached to a new instance. Data loss is bounded by replication lag, not by the volume |
| Block volume exhausts burst IOPS credits | Progressive slowdown: p99 latency climbs from 20 ms to 2,000 ms over ~40 minutes, then stays bad | Burst-balance metric trending to zero; I/O wait rising with flat CPU | Switch to a provisioned-IOPS class or enlarge the volume (baseline IOPS scale with size). Prevention: alert on burst balance < 40% |
| NFS server unreachable with `hard` mounts | Requests hang indefinitely; threads exhaust; the host stops responding entirely though CPU is idle | Thread-pool saturation, processes in `D` state, health checks timing out | The LB ejects the host. Recovery requires the NFS server returning; the stuck processes resume automatically. Mitigation: isolate NFS access in a separate worker pool so it cannot take down the web tier |
| NFS lock-recovery grace period after server restart | A 45–90 second total stall across every client at once | Synchronised latency spike across all hosts on the mount | Self-heals when grace expires. Cannot be avoided; must be designed around with timeouts and retries |
| Accidental `DELETE` of a key prefix by a script | Images 404; data appears gone | 404 spike; CDN hit ratio falls; user reports | Versioning means the objects are behind delete markers — remove the markers to restore. Without versioning, the 11 nines did nothing for you. This is why Lesson 63 exists |
| Credential leak in a public repo | Possible mass download or deletion; a surprise bill | Anomalous request volume; provider credential-exposure alert; cost anomaly detection | Revoke the key, rotate, audit access logs. Object Lock prevents deletion of locked objects even by a valid credential |
| Incomplete multipart uploads accumulating | Nothing visible — the bill grows silently | Bucket storage metrics exceeding the sum of listed object sizes | Lifecycle rule `AbortIncompleteMultipartUpload`; run a one-off cleanup |
| CDN caches a 404 or a corrupt object | Users see a broken image long after the origin is fixed | Reports persisting after an origin fix; cache-status headers showing HIT on a bad response | Purge the path, or publish under a new immutable key. Prevention: set short TTLs for error responses, long TTLs only for immutable keys |

## 12. Alternatives

| Approach | Namespace | Mutable in place | Shared access | Typical latency | Rough $/GB-mo | Best for | Worst for |
|---|---|---|---|---|---|---|---|
| **Object storage** (S3, GCS, Azure Blob, R2) | Flat keys | No (whole-object replace) | Unlimited readers/writers | 20–100 ms | $0.001–0.023 | Media, backups, logs, data lakes (Lesson 57), anything write-once | Low-latency random I/O; anything needing `rename` or append |
| **Block storage** (EBS, PD, managed disks) | None (LBA) | Yes | One host | 0.25–2 ms | $0.08–0.125 | Databases, boot volumes, anything needing `fsync` | Sharing between hosts; unbounded capacity |
| **Local NVMe / instance store** | None (LBA) | Yes | One host | 0.05–0.15 ms | bundled with instance | Caches, scratch, shard-local state with replication above it | Anything that must survive instance stop — data is gone |
| **Network file storage** (EFS, Azure Files, Filestore) | Hierarchical | Yes | Many hosts | 1–10 ms | $0.08–0.30 | Legacy POSIX apps, shared config, home directories | High-metadata workloads; anything latency-critical |
| **Parallel file systems** (Lustre, GPFS, FSx for Lustre) | Hierarchical | Yes | Many hosts | 0.5–2 ms | $0.14–0.60 | HPC, ML training over huge datasets | Small random files; cost-sensitive archival |
| **Blobs in a relational DB** (`BYTEA`, `BLOB`) | SQL rows | Yes | Via the DB | 1–5 ms | DB storage price (~$0.10–0.20) | Small blobs (< 100 KB) needing transactional consistency with a row | Anything large: it bloats backups, caches, replication (Lesson 32) and vacuum |
| **Tape / offline media** | Catalogue | No | Sequential | hours–days | $0.0005–0.002 | Regulatory archives, air-gapped copies (Lesson 63) | Anything with an RTO under a day |
| **Self-hosted object store** (MinIO, Ceph RGW, SeaweedFS) | Flat keys | No | Unlimited | 5–50 ms | hardware + ops | Data sovereignty, egress-heavy workloads, on-premises | Teams without storage-operations capacity — you now own durability |

The decision is rarely "which one". It is "which one is the **system of record**, and what sits in front of it". For BookNook: object storage is the record for media; the CDN is a cache; the database holds keys and metadata, never bytes.

## 13. When to use it

**Use object storage when:**

1. **The data is write-once, read-many, whole-blob.** Images, video, PDFs, audio, firmware, ML model artefacts, Parquet files in a lake (Lesson 57), exported reports. If you never need to modify byte 4,096 of an existing file, object storage fits perfectly.
2. **You need durability across failure domains without operating it.** Backups (Lesson 63), compliance archives, anything whose loss is unrecoverable. Eleven nines across multiple AZs is not reproducible by a team of three.
3. **Growth is unbounded or unpredictable.** User-generated content. You never want to be the person resizing a volume at 3 a.m. because uploads filled it.
4. **You want payload bytes to bypass your compute tier.** Presigned URLs turn a bandwidth problem into a signing problem; your app servers stay small and stateless (Lesson 20).
5. **The content is served globally through a CDN.** Object storage is the natural CDN origin (Lesson 31): immutable keys, long TTLs, high hit ratio.

**Use block storage when:**

1. **A database owns the data.** PostgreSQL, MySQL, MongoDB, Kafka, Elasticsearch (Lesson 55) — all need low-latency random I/O and honest `fsync`.
2. **You need the lowest achievable latency per operation** and can accept single-host attachment.
3. **The application assumes a local disk and performs small random writes**, and you are not rewriting it.

**Use file storage when:**

1. **Multiple hosts must read and write the same mutable tree** and you cannot change the application.
2. **A shared workspace is genuinely the model** — render farms, CI build caches, HPC scratch, shared home directories.
3. **Migration is staged.** A sensible path is: lift-and-shift onto NFS, then rewrite the data path onto object storage incrementally.

## 14. When not to use it

**Do not use object storage when:**

1. **The workload does small random reads and writes with latency targets under ~10 ms.** Putting a database's data directory on an object store via a FUSE mount is the classic disaster: a FUSE layer emulates POSIX over HTTP, so every `fsync` becomes a multi-megabyte re-upload, every partial write becomes read-modify-write, and `rename` becomes copy-then-delete. It will appear to work in development and collapse under load. (Modern "object storage as a database backend" systems — Neon, Snowflake, warehouse engines from Lesson 56 — do work, but only because they were *designed* around immutability and log-structured writes; they do not pretend object storage is a disk.)
2. **You need a POSIX filesystem's guarantees** — atomic rename for lock files, byte-range locking, append semantics, hardlinks. Object stores provide none of these, and emulations are unsafe.
3. **The objects are tiny and numerous relative to their value.** A billion 200-byte objects is $5,000 in PUT charges alone to write once, plus per-object metadata overhead. Pack them into larger files (this is exactly why data lakes use columnar files of 128 MB–1 GB, Lesson 57) or use a key-value database (Lesson 50).
4. **You will read the data frequently from an archival tier.** Glacier Deep Archive at $0.00099/GB-mo plus $0.02/GB retrieval means reading your data once a month costs 20× the storage price. Cheap-to-store is not cheap-to-use.

**Do not use block storage when:** you need many hosts to share one mutable dataset; your data grows past the largest volume available; or the data is cold — $0.08/GB-mo is 80× Deep Archive.

**Do not use file storage when:** the workload is metadata-heavy (millions of small files, frequent directory scans); you need multi-region durability (most managed file services are regional or single-zone); or you are choosing it purely out of familiarity — "it feels like a disk" is the reason most teams end up with an expensive, fragile dependency they later have to migrate off.

## 15. Common mistakes

**Beginner mistakes**

1. **Storing uploads on the app server's local disk.** Works perfectly on one server, loses data the moment you add a second or deploy. Violates Lesson 20 directly.
2. **Storing image bytes in the database.** A 2 TB `BYTEA` column makes every backup 2 TB, every replica resync 2 TB, and every `pg_dump` unusable. Store the key, not the bytes.
3. **Believing "folders" exist.** Writing code that creates a zero-byte `prefix/` marker object, or expecting `ListObjectsV2` to be free and ordered like a directory listing.
4. **Using the client-supplied filename as the object key.** Enables path traversal, overwriting another user's object, and unicode homoglyph attacks.
5. **Making the whole bucket public to "fix" a 403.** This is the single most common cause of real-world data breaches in cloud storage. Use a presigned URL or a CDN origin-access identity.
6. **Confusing durability with availability.** "It's 11 nines, we don't need a backup" and "it's 11 nines, so it can't be down" are both wrong, for different reasons.
7. **Forgetting retrieval cost when choosing a storage class.** Moving 10 TB to Glacier IR saves $190/month and costs $300 the first time someone runs a full re-index.
8. **No lifecycle rule for incomplete multipart uploads**, then wondering why the bucket bills for more than its contents.
9. **Serving images directly from the bucket to end users**, paying full egress and getting no edge caching.
10. **Assuming a snapshot is a backup.** It is crash-consistent, it lives in the same account, and `DeleteSnapshot` is one API call away (Lesson 63).

**Experienced-engineer mistakes**

1. **Mounting object storage with FUSE to avoid a rewrite.** It gives you POSIX-shaped behaviour with none of POSIX's guarantees and HTTP's latency on every operation. It is the "works in staging, corrupts in production" pattern.
2. **Designing keys that create a hot partition.** Keys prefixed with a monotonically increasing timestamp (`2026-10-09T11:19:58-...`) concentrate all writes into one partition range, producing `503 SlowDown` at scale. Prefix with a hash or shard the first path component.
3. **Enabling versioning without a noncurrent-version expiry**, so a bucket with 11 TB of live data quietly bills for 60 TB.
4. **Setting presigned URL expiry to 7 days "for convenience".** A presigned URL is a bearer token; it will end up in a Slack message, a log file, and a support ticket. Minutes, not days — and remember that revoking it requires rotating the signing credential.
5. **Treating cross-region replication as a backup.** CRR replicates deletes (unless configured otherwise) and replicates corruption. It is a *disaster-recovery* mechanism for regional loss, not a recovery mechanism for error (Lesson 63).
6. **Ignoring the minimum-billable-size and minimum-duration rules** when writing a lifecycle policy, then increasing cost while reporting a saving.
7. **Putting a write-heavy workload on a burstable block volume** and benchmarking for 10 minutes — exactly long enough to measure only burst performance.
8. **Letting an NFS dependency sit in the request path of the web tier**, so a storage blip becomes a total outage because `hard` mounts hang uninterruptibly.
9. **Relying on `ListObjectsV2` in the hot path.** Listing a million-key prefix is 1,000 paginated calls. Keep an index of keys in your database; the object store is not a query engine.
10. **Assuming eventual consistency workarounds are still needed.** Carrying forward 2018-era "write a marker then poll" patterns adds latency and bugs; S3 has been strongly read-after-write consistent since December 2020.
11. **Not verifying uploaded content server-side.** Trusting the `Content-Type` in the presigned policy; the policy constrains what the *browser claims*, not what the bytes are.

## 16. Interview perspective

This topic appears in almost every file-upload, media, or backup design question: "Design Instagram", "Design Dropbox", "Design a video platform", "Design a log pipeline". Interviewers use it to check whether you understand *statelessness* and *cost*, not whether you can name S3.

**What a strong answer sounds like.** When asked where uploads go, do not say "S3" and stop. Say:

> "Uploads go to object storage, not to the app servers, because the app tier must stay stateless (otherwise autoscaling and deploys destroy data). The browser gets a presigned URL from the API and PUTs the bytes directly, so a 3 MB payload never traverses our compute — that's the difference between sizing the fleet for bandwidth and sizing it for request rate. The database stores the key and metadata, never the bytes. An ObjectCreated event drives a worker that validates the file by magic bytes and generates derivatives. Reads go through a CDN with immutable keys and a one-year TTL, because egress, not storage, dominates the bill."

**Points that reliably earn credit:**
- Naming the three abstractions precisely and placing the database on block storage, media on object storage.
- The layering insight: file systems are built on block, object stores are built on both.
- Presigned URLs, and *why* — bytes bypassing compute.
- Distinguishing durability from availability, with numbers (11 nines vs 99.9%).
- Explaining erasure coding (`k` data + `m` parity, any `k` reconstructs) rather than hand-waving "replicated three times".
- Noting that eleven nines does not protect against `DELETE`, which bridges naturally to backups (Lesson 63) and gives you a clean segue.
- Mentioning key design to avoid hot partitions at high write rates.
- Costing: storage vs requests vs egress, and knowing egress usually wins.

**Follow-ups to expect:** "How do you handle a 5 GB upload?" (multipart, resumable, parts in parallel). "How do you stop someone uploading a 10 GB file?" (content-length-range in the POST policy — enforced by the store, not the client). "How do you serve private files?" (presigned GET, or signed CDN URLs). "The user deletes a photo — what actually happens?" (soft-delete the metadata row immediately, lifecycle-expire the object later; versioning for recovery).

**Red flags interviewers watch for:**
- Proposing to store uploaded files on the web server's filesystem.
- Storing large binaries in the relational database without acknowledging the backup, replication and vacuum consequences.
- Saying "S3 is eventually consistent" as current fact (five years out of date).
- Claiming 11 nines of durability removes the need for backups.
- Treating a read replica or cross-region replication as a backup.
- Using "it depends" without naming the dependency — the acceptable form is "it depends on whether the application can be changed to speak HTTP; if it can't, we need file storage".
- Ignoring cost entirely in a media-heavy design, or optimising storage class while leaving a no-CDN egress bill 100× larger.
- Proposing a FUSE mount of object storage as a database volume.

## 17. Knowledge check

**Conceptual**

1. Block storage, file storage and object storage are often described as three layers rather than three competitors. Explain precisely what each layer adds to the one below it, and state what information the lower layer does *not* have that the higher layer must therefore maintain itself.
2. A colleague says: "Our object store guarantees eleven nines of durability, so we don't need backups and we don't need a multi-region plan." Identify every distinct error in that sentence, and for each one name the specific class of event it fails to account for.
3. Object storage offers no atomic `rename` and no in-place byte-range write. Explain why these two omissions are deliberate consequences of how an object store achieves scale and durability, rather than features the vendors simply forgot.

**Practical scenario**

4. BookNook is adding author-uploaded manuscript drafts: Word documents averaging 4 MB, uploaded by 12,000 authors, revised roughly 20 times each over a six-month editing window, and then retained for seven years for contractual reasons. Editors must be able to open any revision, compare two revisions, and see who changed what. Describe the storage design: which abstraction holds what, how revisions are keyed, which storage classes and lifecycle transitions you apply and at what ages, how uploads reach storage, and how you would prevent an editor's accidental overwrite from destroying a draft. State any assumption you make.

**Calculation**

5. A video-analytics startup ingests 4,000 hours of footage per day. Each hour is stored as 240 segments of 15 seconds, averaging 9 MB per segment. Segments are read heavily for 7 days (average 3 GETs per segment in that window), then almost never. Retention is 400 days. Using Standard at $0.023/GB-mo, Glacier Instant Retrieval at $0.004/GB-mo with $0.03/GB retrieval, PUT at $0.005 per 1,000 and GET at $0.0004 per 1,000, and a 30-day month: compute (a) the steady-state stored volume in TB once the 400-day window is full, (b) the monthly PUT cost, (c) the monthly GET cost, (d) the monthly storage cost if everything stays in Standard, (e) the monthly storage cost if objects transition to Glacier IR at 90 days — and explain why the transition cannot sensibly be set at 7 days even though the data goes cold then. Show every step.

**Progress checklist**

- [x] **Phase 1 — Foundations** (Lessons 1–17) — complete
- [x] **Phase 2 — Scaling** (Lessons 18–39) — complete
- **Phase 3 — Data** (Lessons 40–63)
  - [x] Topic 1 — Relational databases and the relational model (Lesson 40)
  - [x] Topic 2 — SQL execution and the query planner (Lesson 41)
  - [x] Topic 3 — ACID transactions (Lesson 42)
  - [x] Topic 4 — Isolation levels and anomalies (Lesson 43)
  - [x] Topic 5 — Locking, MVCC and concurrency control (Lesson 44)
  - [x] Topic 6 — Indexes and B-trees (Lesson 45)
  - [x] Topic 7 — Index design: composite and covering indexes (Lesson 46)
  - [x] Topic 8 — LSM-trees and write-optimised storage (Lesson 47)
  - [x] Topic 9 — Schema design and normalisation (Lesson 48)
  - [x] Topic 10 — Denormalisation and read models (Lesson 49)
  - [x] Topic 11 — Key-value stores (Lesson 50)
  - [x] Topic 12 — Document databases (Lesson 51)
  - [x] Topic 13 — Wide-column stores (Lesson 52)
  - [x] Topic 14 — Graph databases (Lesson 53)
  - [x] Topic 15 — Time-series databases (Lesson 54)
  - [x] Topic 16 — Search engines and inverted indexes (Lesson 55)
  - [x] Topic 17 — Data warehouses, OLAP and columnar storage (Lesson 56)
  - [x] Topic 18 — Data lakes and lakehouses (Lesson 57)
  - [x] Topic 19 — Batch and stream processing (Lesson 58)
  - [x] Topic 20 — CAP theorem (Lesson 59)
  - [x] Topic 21 — PACELC and the latency/consistency trade-off (Lesson 60)
  - [x] Topic 22 — Consistency models (Lesson 61)
  - [x] Topic 23 — Object, block and file storage (Lesson 62) ← **you are here**
  - [ ] Topic 24 — Data retention, archival, backups and disaster recovery (Lesson 63)

**NEXT LESSON: Phase 3, Topic 24 — Data retention, archival, backups, and disaster recovery**


---

# Lesson 63 — Data Retention, Archival, Backups, and Disaster Recovery

*Phase 3, Topic 24*

## 1. Simple definition

Four ideas that are constantly confused, and that must be separate:

- **Retention**: *how long* you keep data, and *why*. A written rule — "orders for 7 years because tax law, clickstream for 30 days because nothing reads it, deleted-account data for 30 days because GDPR lets us" — with an owner and a deletion mechanism.
- **Archival**: *moving* data that must be kept but is rarely read onto cheaper, slower media. The data is still yours and still retrievable; it is just not on the fast disk any more.
- **Backup**: an **independent, restorable copy** of your data, taken at a point in time, stored such that a failure of the primary cannot destroy it. Its only purpose is to answer: *can I reconstruct the exact state of this database as of a specific instant?*
- **Disaster recovery (DR)**: the *plan and topology* that gets the service running again after losing a site, a region, or an account — including the parts a database backup does not cover: DNS, secrets, configuration, the deployment pipeline, and the knowledge of the humans involved.

And the two numbers that make all four measurable:

- **RPO (Recovery Point Objective)**: the maximum amount of data you are willing to lose, expressed as **time**. "RPO = 5 minutes" means a disaster may cost you the last 5 minutes of writes.
- **RTO (Recovery Time Objective)**: the maximum time to restore service. "RTO = 1 hour" means users may be down for an hour.

Both are **business decisions**, not engineering ones. Engineering's job is to price them: an RPO of 24 hours is nearly free, an RPO of 5 seconds costs a synchronous standby and its latency on every commit, and an RPO of zero across regions costs you your write latency budget entirely.

The one sentence to carry out of this lesson: **replication saves you from hardware; backups save you from yourself — and only one of those two failures actually happens regularly.**

## 2. Why it exists

Lesson 32 taught replication and everyone leaves that lesson believing they are safe. They are not, and the reason is a single sentence: **a replica applies the same changes the primary does, in the same order, including the wrong ones.**

- Someone runs `DELETE FROM orders WHERE customer_id = 4821` without the `WHERE` clause in a transaction they then commit. The primary did it; the replica did it **11 milliseconds later**. Both are wrong, identically and instantly.
- A migration drops a column. Replicated.
- A ransomware binary encrypts the files. Replicated.
- A code bug writes `total_pence = 0` for every order in a date range. Replicated.
- Someone drops the table. Replicated.

Replication has an RPO of milliseconds and is **useless for every one of those five failures**, because its purpose is to keep a copy of *the current state* — and the current state is exactly what is now wrong. Only a **point-in-time** copy taken *before* the mistake lets you go back. That is what a backup is, and the distinction is the most important sentence in the phase for anyone who has ever been on call.

The second reason this lesson exists is that backups are a **legal and financial** artefact, not just an operational one. BookNook has to keep order records for seven years (tax), delete marketing data on request within 30 days (data protection), keep a copy of nothing that a court has placed a hold on, prove the deletion happened, and do all of it while the primary database is 1.2 TB and growing 4 GB/day. Retention is where storage engineering meets compliance, and getting it wrong is a fine rather than an outage.

Third: cost. BookNook's 1.2 TB database, kept forever on fast replicated storage, is a line item that grows monotonically. Archival is how you keep the obligation without keeping the cost — **the data must exist; only the access path may be slow.**

## 3. Real-world analogy

Two accountants, and the difference between them is the entire lesson.

**The first accountant is a mirror.** Whatever you write in your ledger, they write in theirs, within a second, always in perfect agreement. This is replication, and it is genuinely valuable: if your ledger burns, theirs is up to date. But now suppose that on Tuesday you accidentally post a ₹40 lakh payment twice and only notice on Friday. The mirror has the same mistake, twice, on the same dates. **A mirror cannot tell you what the ledger said on Thursday.** Agreement is not the same as history.

**The second accountant photographs the ledger every night at midnight**, stores the photographs in a vault in a different city, and every quarter takes one out and does a full reconciliation to prove the photograph is legible and complete. This is a backup. It is 24 hours out of date at worst (its **RPO**), it takes hours to recover from (its **RTO**), and it is the only thing that can tell you what Monday looked like.

**Archival** is what happens to the 2014 ledgers: they move from the shelf behind the desk to the basement, then to off-site storage. Still yours, still retrievable, no longer in the way. The mistake people make is thinking "in the basement" means "gone" — a retrieval that takes 12 hours has changed your RTO, not your retention.

The quarterly reconciliation (**a restore drill**) is the part everybody skips, and it is the part that matters. An unphotographed snapshot is a rumour; a photograph nobody has ever opened is a *hope*. There is a well-known saying in the field — **"nobody has a backup strategy; they have a restore strategy, or they have an outage"** — and this lesson is mostly about making the restore real.

## 4. Detailed technical explanation

### 4.1 The taxonomy, with the distinction that actually matters

| Type | What it is | Restore granularity | Typical RPO | Typical RTO | BookNook use |
|---|---|---|---|---|---|
| **Logical dump** (`pg_dump`, `mysqldump`) | SQL text or custom-format stream of schema + rows | Down to one table or one row (with work) | Hours to a day | Hours (slow: index rebuild) | Small databases, cross-version migrations, per-table rescue |
| **Physical base backup** (`pg_basebackup`, `pgBackRest`, `XtraBackup`) | Byte-level copy of the data directory + WAL start point | Whole cluster, then PITR | Minutes (continuous WAL) | 30 min – 2 h for ~1 TB | **The real primary backup** |
| **Continuous WAL archiving** | Every 16 MB WAL segment shipped somewhere durable | Any instant, to the transaction | ~0 (or `archive_timeout`) | adds replay time | The component that makes PITR possible |
| **Filesystem / volume snapshot** (EBS, ZFS, SAN) | Copy-on-write block image, near-instant to *take* | Volume | Seconds | Minutes to mount, but **crash-consistent only** | Fast rollback before risky migrations |
| **Managed PITR** (RDS, Cloud SQL, Aurora) | Vendor runs base backups + WAL for you | Whole instance, to the second | ~5 min (or 0 with sync) | 20 min – hours | Almost always worth it |
| **Replica** | Live copy of current state | N/A | ~0 (sync) or seconds (async) | Seconds (promote) | Availability, not recovery |

Two rows in that table are the ones people get wrong:

- **A snapshot is not a backup.** It is crash-consistent: the filesystem image is as consistent as a power-cut would leave it, which a database recovers from — but the snapshot is usually stored **in the same account and often the same region** as the volume. A compromised account or a regional control-plane failure takes both.
- **A replica is not a backup**, for the five reasons in §2.

### 4.2 Point-in-time recovery: how "go back to 14:01:59" actually works

PITR is the single most valuable capability here, and its mechanism is worth knowing because it dictates your RPO.

```
   Base backup taken at 02:00 (Monday)          Target: 14:01:59
          │                                          ▲
          │                                          │
   ───────┴──────────────────────────────────────────┴──────► time
          │◄───────────── WAL segments ─────────────────►│
          02:00           (continuous archive)         14:01:59
                                                        │
   Restore = (1) restore base backup files              │
             (2) replay every WAL segment in order      │
             (3) STOP at the target:                     │
                 recovery_target_time = '2026-10-26 14:01:59+00'
                 recovery_target_action = 'promote'
                 recovery_target_inclusive = 'off'   ← excludes the bad txn
```

The RPO is therefore **governed by how much WAL is not yet in the archive**. With continuous streaming archiving that is typically **under a second**. With `archive_timeout = 60s` and low traffic, it is up to 60 seconds. With "we take a nightly `pg_dump`", it is **up to 24 hours**, and there is no way to recover anything more recent.

Replay speed is the RTO's hidden cost. WAL replay is CPU-bound single-threaded in stock PostgreSQL: expect **50–200 MB/s** of WAL, so a restore that must replay **8 hours of a 250 GB/day workload** (83 GB) takes **7–28 minutes** on top of restoring the files. This is why RTO arithmetic (§9) is dominated by files, then WAL, then verification — in that order.

### 4.3 The three rules, and the fourth that modern practice added

The classic **3-2-1** rule:

- **3** copies of the data,
- on **2** different media or systems,
- with **1** off-site (different region, account or provider).

Modern practice adds a fourth, because of ransomware and insider risk: **1 copy that is immutable or air-gapped** — write-once (object lock in compliance mode), or offline. A backup the attacker can delete is not a backup; the standard modern attack is to hunt for and destroy backup credentials *before* triggering the encryption, and every major ransomware family now does this.

What that looks like concretely for BookNook's 1.2 TB:

```
  copy 1: primary volume              (eu-west-1a)   — live
  copy 2: synchronous standby         (eu-west-1b)   — availability, NOT a backup
  copy 3: pgBackRest repo in S3       (eu-west-1, separate account, object-lock)
  copy 4: WAL archive + weekly full   (eu-central-1, different provider)  ← the
                                                             "off-site, immutable" leg
```

### 4.4 Retention: a schedule, not a feeling

Retention is expressed as a **schedule per data class**, with a legal or business reason attached to each row:

| Data class | Retention | Why | Mechanism |
|---|---|---|---|
| Orders, invoices, payments | **7 years** | Tax/audit law | Archived to Parquet in object storage; OLTP table keeps 24 months |
| Customer account (active) | Indefinite | Service delivery | Primary DB |
| Customer account (closed) | **30 days**, then erased | Data-protection commitments | Tombstone + hard delete job |
| Support tickets | 3 years | Contract | Archived |
| Clickstream / product views | **13 months** | Analytics; aggregation beyond that | Rolled up to daily aggregates, raw dropped |
| Application logs | **30–90 days** | Debugging window | Log store lifecycle policy |
| Security/auth logs | 1 year | Compliance | Append-only, tamper-evident |
| Database backups | **35 days** full PITR; 12 monthly after | Ransomware + audit | Backup repo lifecycle |
| Feature-flag / config history | 1 year | Change forensics | VCS |

Three rules that make a retention policy real rather than decorative:

1. **Every row has an owner and a legal basis.** If you cannot name why a class exists, it should not.
2. **Deletion is a job with a metric**, not an intention. "How many rows did the erasure job delete last night, and how many are past their retention date right now?" — if the second number grows, deletion is broken. This is the same drift-detection pattern as Lesson 56's denormalisation.
3. **Legal hold overrides everything.** A litigation hold means deletion *stops* for the affected records, and the mechanism must support "exclude these IDs" or you will delete evidence and the fine is much worse than the storage cost.

### 4.5 The GDPR/erasure-versus-immutability conflict

The genuinely hard case, and interviewers love it. A customer exercises their right to erasure. You delete their rows from the primary. What about:

- the **backups** containing their data, which you cannot rewrite because they are immutable and content-addressed?
- the **WAL archive**, which contains the original `INSERT` and which will be replayed if you ever restore?

The accepted answers, in order of preference:

1. **Crypto-shredding.** Encrypt each customer's sensitive fields with a **per-customer key** (envelope encryption). Erasure = **delete the key**. The ciphertext remains in every backup, forever, and is permanently unreadable. This is the only mechanism that genuinely satisfies erasure in immutable backups, and it is the answer to give in an interview.
2. **Bounded retention + documented commitment.** "Backups expire in 35 days; erasure completes within 35 days; this is disclosed." Legally common, operationally simple, and requires the retention job to actually work.
3. **Tombstone-and-filter on restore.** Keep a durable list of erased IDs outside the backup corpus; any restore runs a scrub step before serving traffic. Necessary as a **backstop even with crypto-shredding** (§11.6 shows why), and it must be tested, because a manual step in a restore is a step that will be forgotten at 03:00.

### 4.6 Archival tiers and the retrieval-latency trade

Archival moves cold data to cheaper media. The only thing that changes is **retrieval latency and cost**, and both are contractual:

| Tier | Typical cost (£/GB-month) | Retrieval time | Retrieval cost | Use for |
|---|---|---|---|---|
| Hot (S3 Standard) | ~0.021 | instant | free | Last 30 days of backups; anything you might restore today |
| Warm (S3 Infrequent Access) | ~0.011 | instant | per-GB fee | 30–90 day backups |
| Cold (Glacier Instant Retrieval) | ~0.004 | instant | per-GB fee | Quarterly/monthly archives you must be able to read quickly |
| Archive (Glacier Flexible) | ~0.0035 | **1–5 min** (expedited) / 3–5 h (standard) | per-GB fee | Yearly compliance snapshots |
| Deep Archive | ~0.001 | **12 h** | per-GB fee | 7-year retention where retrieval is a legal event |

The engineering rule: **archive tier must be chosen from your RTO, not from your CFO's enthusiasm.** Moving the only copy of a 90-day-old backup to Deep Archive silently turns a 1-hour RTO into a 13-hour one. The fix is layers — 30 days hot for fast recovery, 12 months cold for audits — which is exactly what a lifecycle policy does.

## 5. Request / data flow

The full path of one row from birth to vault, and the restore path that matters:

```
 ── WRITE PATH ────────────────────────────────────────────────────────────────
   app INSERT ──► primary heap + WAL ──► synchronous standby (eu-west-1b)
                                   │         └── availability, RPO≈0, NOT a backup
                                   └──► WAL archive (pgBackRest → S3, object-lock)
                                          pushed every 16 MB or 60 s
                                          ▲ this defines the real RPO

   app UPDATE/DELETE ──► same path. Note: every mistake is also in the archive.
                          Recovery is choosing an instant BEFORE the mistake.

   02:00 nightly ──► base backup (incremental since Sunday, full on Sunday)
                      1.2 TB raw → 380 GB compressed → uploaded in 33 min
                      → S3 eu-west-1 (separate account, object-lock compliance)
                      → lifecycle: 35 days Standard, then monthly to eu-central-1

 ── RESTORE PATH (the one that gets tested quarterly) ─────────────────────────
   14:02  Someone committed a bad DELETE.
   14:06  Detected (order-history 500s, dashboards, a customer tweet).
   14:07  DECISION: do NOT fail over to the standby — it has the DELETE too.
          Recovery target = 14:01:59.
   14:08  Provision a scratch instance (do not touch the production primary).
   14:09  Restore base backup from S3           ← 22 min  (download 380 GB @ 300 MB/s)
   14:31  Decompress + apply                    ← 50 min (1.2 TB @ 400 MB/s)
   15:21  Replay WAL to 14:01:59                ← 14 min (83 GB @ 100 MB/s)
   15:35  Verify: row counts, checksums, a query the incident proves must work
   15:44  Freeze writes on the broken primary; repoint the app; replay the
          DELTA (14:02→15:44 writes) from the old primary's WAL into the new one,
          or accept their loss (this is a business call — see §7.1)
   15:52  Service restored. RTO achieved: 1 h 46 m. RPO: 0 for pre-14:02 data.
          Cost: 4 months of the monthly error budget, in one afternoon.
```

The step most teams have never rehearsed is the last one: **what happens to the writes that arrived after the recovery target?** Restoring to 14:01:59 discards 1 h 42 m of customer orders that were *fine*. You either replay them (possible with logical decoding or an application-level queue, and complex) or you accept the loss and email the customers. Deciding this **before** the incident is the difference between a 2-hour outage and a 6-hour one.

## 6. ASCII diagram

RPO and RTO on one axis — the diagram to draw in an interview:

```
                     incident
                        ▼
 ───────────────────────╳──────────────────────────────►  time
                        │◄──── RTO ────►│
   ▲                    │               ▲
   │                    │      service restored
   │◄──── RPO ────►│    │
   │               ▲    │
 last good      last    │
 backup      transaction│
 (you lose everything in the ◄RPO► window: 5 minutes of orders)
 (users are down for the ◄RTO► window)

 THE TWO NUMBERS ARE INDEPENDENT:
   RPO is about COPIES and how OFTEN they are made    → WAL archiving frequency
   RTO is about SIZE and how FAST you can rebuild     → data volume ÷ throughput
   Buying one does not buy the other. Say both, always.
```

The 3-2-1-1 layout, drawn:

```
                  ┌──────────────────────── eu-west-1 ────────────────────────┐
                  │                                                          │
   app ──► PRIMARY (1a) ──sync──► STANDBY (1b)                                │
                  │                  │  copy 2: availability only             │
                  │                  │                                        │
                  └────────┬─────────┘                                        │
                           │ WAL + base backup                               │
                    ┌──────▼───────┐   copy 3: object-locked, separate acct   │
                    │  S3 (bucket) │   (365-day compliance mode, versioned)    │
                    └──────┬───────┘                                          │
                           │ lifecycle replication                            │
                  ┌────────▼─────────────────────────────────────────────┐    │
                  │                                                      │    │
                  └──────────────────────────────────────────────────────┘    │
                  └──────────────────────────────────────────────────────────┘
                           │ cross-provider copy
                  ┌────────▼───────────────────────┐
                  │ eu-central-1 · different vendor │  copy 4: THE off-site,
                  │ monthly fulls, Deep Archive     │   immutable leg. Slow to
                  │ restore: 12 h                   │   retrieve → RTO +12 h
                  └─────────────────────────────────┘

   Failure each leg survives:
     primary dies          → standby promoted          (seconds)
     AZ dies               → multi-AZ survives         (0)
     region dies           → restore from S3 elsewhere (hours)
     bad DELETE            → PITR to before it         (hours)   ← only backups
     ransomware/insider    → object-lock + other vendor (days)   ← only immutability
     account compromise    → different provider         (days)   ← only 3-2-1-1
```

The DR tier ladder, with the numbers that decide it:

```
 RTO   1 min        5 min        15 min       1 h          8 h        24 h
  │      │            │            │           │            │          │
  ▼      ▼            ▼            ▼           ▼            ▼          ▼
 multi-site      hot standby   warm standby  pilot light  backup &  tape in a
 active/active   (always on,   (running,     (core only,   restore   vault
 (2x cost)       auto-failover) scaled down)  start on      (cheapest
                                             demand)        that works)
  │
  └── cost rises ~2x per step to the left; complexity rises ~4x
      "Why not multi-site active/active?" → because it costs 2x forever,
      quadruples the consistency surface (Lesson 91), and buys 59 minutes
      of an RTO you have not proved you need.
```

## 7. Detailed example

### 7.1 The bad `DELETE` — the incident that defines why backups exist

**14:02:11.** An engineer runs a cleanup script to purge abandoned carts. The script was tested against a table called `carts`. In production, a migration renamed it to `cart_items` and reused the name `carts` for something else. The script begins:

```sql
DELETE FROM carts;   -- intended: DELETE FROM carts WHERE updated_at < now() - interval '90 days';
```

It deletes **2.1 million rows** of active carts, and commits. Replication carries it to the standby in 11 ms. The backup archive carries it too — that is its job.

**14:06.** Checkout starts failing: `cart_items` foreign-key violations. Alert fires.

**14:07.** The first instinct — "fail over to the standby" — is **wrong, and this is the crux of the lesson.** The standby has the identical `DELETE`. Failover changes nothing and burns 20 minutes.

**14:08–15:44.** PITR restore to `14:01:59`, as in §5, in a **new** instance. Total: **1 h 37 m**.

**Then the decision nobody had pre-made:** 1 h 42 m of *good* writes (4,900 carts created, 3,100 orders started) arrived after the recovery target and do not exist in the restored database. Options:

| Option | Cost | Risk |
|---|---|---|
| Accept the loss, email affected users | 8,000 users re-do their cart | Trust, support load |
| Replay the delta from the old primary's WAL via logical decoding | 3–4 hours of engineering, now | Complexity under incident pressure |
| Restore twice: once at target, once at 15:44, diff the tables, merge | 3 more hours | Merge conflicts, no transactions |

BookNook chose option 1 and changed three things afterwards, which is the real output of the incident:

1. **A warm standby with delayed replay** — a replica kept **1 hour behind** on purpose. For a bad `DELETE`, the delayed replica is a *correct* copy of the world as of 14:01 and can be promoted in **4 minutes** instead of a 96-minute restore. This is the single cheapest RTO improvement available for logical corruption, and it is what "PITR" looks like when it is engineered rather than documented.
2. **No `DELETE` without a `LIMIT`-style guard.** Their cleanup scripts now require a `WHERE` clause on a `created_at` column (enforced by a lint rule) and run in batches with a row-count ceiling.
3. **The restore drill became quarterly and mandatory.** The first real restore in company history found that the documented procedure referenced a bucket in the wrong account and a decryption key held by someone who had left.

### 7.2 The 09:00 ransomware morning

A developer's laptop is compromised; credentials for the CI role are stolen; the attacker spends two weeks discovering infrastructure, then:

- deletes the production volumes,
- deletes the S3 backup bucket,
- **is stopped at the replica bucket** because it is in **compliance-mode object lock** — no one, including the account root, can delete or shorten retention for 365 days,
- and the cross-provider copy was never reachable with those credentials at all.

Recovery: restore the latest base backup + WAL from the locked bucket, **skipping the attacker's window**, into a new account created from infrastructure-as-code. **RTO: 31 hours** (the account rebuild was the long pole, not the data). **RPO: 0** (WAL was continuous to the moment of deletion).

Three details worth taking away:

- The **account rebuild** dominated the RTO. Data recovery was 2 hours; IAM, VPC, DNS, secrets, certificates and the pipeline were 29. Backups cover **data**; DR covers **the ability to run the service**, and they are different documents.
- The **immutability was the whole defence.** Without it, RPO becomes "whatever was on a laptop".
- The restore ran with a **tombstone scrub** (§4.5) so that previously erased customers were not resurrected by the restore — which is precisely the step people forget.

## 8. Code or configuration example

```bash
# ============================================================================
# BookNook backup configuration. pgBackRest + object storage. Illustrative:
# verify against your provider's current options and your own throughput.
# ============================================================================

# --- 1. pgBackRest: base backups + CONTINUOUS WAL ---------------------------------
# /etc/pgbackrest/pgbackrest.conf
[global]
repo1-type=s3
repo1-s3-bucket=booknook-pgbackrest-euwest1
repo1-s3-region=eu-west-1
repo1-s3-key-type=auto
repo1-retention-full=4                # 4 weekly fulls; PITR window = 35 days
repo1-retention-full-type=time
repo1-retention-archive=35            # WAL kept 35 days: THE PITR WINDOW
repo1-cipher-type=aes-256-cbc         # encrypt at rest; key lives in KMS, NOT here
repo1-bundle=y                        # fewer, larger objects: cheaper + faster
process-max=8                         # parallelism on compress/transfer
compress-type=zst
compress-level=3

[global:archive-push]
archive-async=y
archive-push-queue-max=8gb            # if the repo is unreachable, buffer up to
                                      # 8GB locally, then WARN, then (finally)
                                      # stop accepting writes. DELIBERATE:
                                      # an unbounded queue means silently
                                      # accepting writes you cannot recover.
                                      # This is a CP choice for durability.

[booknook]
pg1-path=/var/lib/postgresql/16/main


# --- 2. The schedule (§4.4 retention, §9 arithmetic) --------------------------
#  Sunday 01:00  full      (1.2 TB → 380 GB compressed → ~33 min upload)
#  Mon–Sat 01:00 diff      (~40 GB incremental)
#  every 16MB or 60s: WAL archive
#  Quarterly: RESTORE DRILL (§4.3) — non-negotiable, calendar-invited
pgbackrest --stanza=booknook --type=full backup
pgbackrest --stanza=booknook --type=diff backup

# VERIFY THE BACKUP, not just that the job exited 0.
pgbackrest --stanza=booknook verify          # checksums every file in the repo
pgbackrest --stanza=booknook info            # what you actually have, and until when


# --- 3. PITR restore to a scratch instance (§5) -------------------------------
pgbackrest --stanza=booknook --type=time \
  --target="2026-10-26 14:01:59+00" \
  --target-action=promote \
  --delta \
  --pg1-path=/var/lib/postgresql/16/restore \
  --restore-command="systemctl start postgresql@restore" \
  restore
# --target-action=promote: stop replaying at the target and open read-write.
# PostgreSQL's own spelling of the same thing:
#   restore_command = 'pgbackrest --stanza=booknook archive-get %f "%p"'
#   recovery_target_time = '2026-10-26 14:01:59+00'
#   recovery_target_inclusive = 'off'    ← excludes the transaction AT the target
#   recovery_target_action = 'promote'


# --- 4. THE DRILL: prove it, every quarter, automatically --------------------
# This script is the backup strategy. Everything above is the mechanism.
set -euo pipefail
TARGET=$(date -u -d '7 days ago' +%Y-%m-%dT%H:%M:%SZ)
pgbackrest --stanza=booknook --type=time --target="$TARGET" \
           --target-action=promote --pg1-path=/tmp/drill restore
pg_ctl -D /tmp/drill start
# 1. does it start at all?
psql -h /tmp -d booknook -c 'SELECT 1'
# 2. is the data plausible? (compare against the production counts as of TARGET)
psql -h /tmp -d booknook -c "
  SELECT (SELECT count(*) FROM orders)      AS orders,
         (SELECT count(*) FROM order_items) AS items,
         (SELECT max(created_at) FROM orders) AS newest;"
# 3. does the RESTORE-SPECIFIC logic work? (tombstone scrub, §4.5)
psql -h /tmp -d booknook -c "SELECT count(*) FROM customers WHERE erased_at IS NOT NULL;"
# 4. record the timings → this is how RTO stays a number rather than a hope
echo "{\"drill\":\"$(date -u +%FT%TZ)\",\"target\":\"$TARGET\",\"rto_sec\":$SECONDS}" \
  >> /var/log/booknook/restore-drills.jsonl
pg_ctl -D /tmp/drill stop; rm -rf /tmp/drill


# --- 5. Immutability: the layer that stops ransomware ------------------------
aws s3api put-object-lock-configuration \
  --bucket booknook-pgbackrest-euwest1 \
  --object-lock-configuration '{
    "ObjectLockEnabled": "Enabled",
    "Rule": {"DefaultRetention": {"Mode": "COMPLIANCE", "Days": 365}}
  }'
# COMPLIANCE mode cannot be shortened or removed by ANY principal, including the
# account root. That property is the point: a stolen credential cannot delete it.
# The cost: you WILL pay to store 365 days of backups, and you cannot undo that
# even if you are wrong about needing them. Choose the number deliberately.
```

```sql
-- ============================================================================
-- Retention and erasure, as SQL you can actually run and monitor.
-- ============================================================================

-- --- 1. Is deletion keeping up? The one metric that proves retention is real.
SELECT 'orders_to_archive' AS class,
       count(*) AS overdue_rows
FROM   orders
WHERE  created_at < now() - interval '24 months'   -- OLTP keeps 24 months
  AND  archived_at IS NULL
UNION ALL
SELECT 'accounts_to_erase', count(*)
FROM   customers
WHERE  closed_at < now() - interval '30 days'
  AND  erased_at IS NULL
  AND  id NOT IN (SELECT customer_id FROM legal_holds);  -- §4.4 rule 3
-- Alert if any of these GROWS for two consecutive days: that is deletion broken,
-- and it is a compliance breach on a timer, not a performance problem.

-- --- 2. Crypto-shredding: erasure that works inside immutable backups (§4.5)
-- Each customer's PII columns are encrypted with a per-customer data key, which
-- itself is wrapped by a KMS master key. Erasure:
UPDATE customers SET pii_key_id = NULL,                         -- destroy the DEK
                     name = NULL, email = '(erased)', phone = NULL,
                     erased_at = now()
WHERE  id = $1;
-- The ciphertext still exists in every backup until it expires. It is now
-- permanently undecryptable, which is what "erased" has to mean when the
-- storage is immutable. The UPDATE itself is backed up too — and is a no-op
-- when replayed.

-- --- 3. Archival: move cold OLTP rows to object storage as Parquet (L58)
-- Then DELETE in BATCHES, never one enormous transaction (L49: WAL volume,
-- replication lag, and a 40-minute lock are all consequences of batch = ∞).
DELETE FROM orders
WHERE  order_id IN (
         SELECT order_id FROM orders
         WHERE  created_at < now() - interval '24 months'
           AND  archived_at IS NOT NULL
         LIMIT  10000          -- bounded: replication lag stays sub-second
       );
-- Repeat until 0 rows. Monitor replication lag after EACH batch (Lesson 34).
```

## 9. Numerical example

BookNook's primary is **1.2 TB**, growing **4 GB/day**, writing **250 GB/day of WAL**. Ingest peaks at 5,000 writes/sec across order, inventory and payment tables — about 10 customer orders per second at peak.

**Step 1 — backup size and the upload window.**

```
raw database                        = 1,200 GB
zstd level 3 on PostgreSQL heap     ≈ 3.1 : 1        → 387 GB
weekly full                         = 387 GB
daily differential (≈1 day of churn) ≈ 40 GB raw/4GB net churn → 14 GB
WAL per day                         = 250 GB → 62 GB compressed
```

Upload time for the weekly full, on a 1.6 Gbps (200 MB/s) link:

```
387 GB × 1,024 MB/GB ÷ 200 MB/s = 1,982 s ≈ 33 min
```

A 33-minute window is fine at 01:00 and would **not** be fine on a saturated business-hours link — which is why backups run at night and why a backup that overruns into peak is a latency incident (it competes for disk and network).

**Step 2 — RTO, broken down. This table is the answer to "what is your RTO?"**

| Phase | Time | Dominated by |
|---|---|---|
| Provision instance + attach storage | 4 min | automation (IaC), not data |
| Download base backup (387 GB @ 300 MB/s) | 22 min | **network** |
| Decompress + write 1.2 TB @ 400 MB/s | 50 min | **disk write throughput** |
| Replay WAL (83 GB @ 100 MB/s) | 14 min | **single-threaded replay** |
| Integrity verification (counts, checksums) | 5 min | must not be skipped |
| Repoint app, DNS/TTL, warm caches, connection pools | 10 min | **DNS TTL is a config decision** |
| **Total** | **105 min** | |

So the **documented** RTO of "1 hour" was **fiction** — the real RTO is **1 h 45 m**, and the only reason anyone knew was that they measured it in a drill. The two fixes that matter, in order of value per rupee:

1. **A delayed replica** (1 hour behind) turns the common case (logical corruption) into a **4-minute promotion**, because no download and no replay are needed — the files are already local.
2. **A warm standby in a second region** turns a regional loss into a **12-minute promotion**, at roughly **+70 % infrastructure cost** for the standby plus cross-region WAL shipping (~62 GB/day compressed, ~£1.30/day in egress… and note that egress, not storage, is usually the surprise line item).

**Step 3 — what an RPO costs.** Compare three architectures on the *same* database:

| Design | RPO | Cost per month (approx) | Real cost |
|---|---|---|---|
| Nightly logical dump only | **24 h** | ~£8 storage | 24 h × 5,000 writes/s = **432 M operations lost** |
| Nightly full + continuous WAL archive | **< 1 s** | ~£45 storage | WAL replay adds 14 min to RTO |
| Synchronous standby + WAL archive | **0** | ~£380 (compute + storage) | **+1.5 ms on every commit, forever** (Lesson 59 §9) |

The middle row is almost always the right answer, and the reason is arithmetic: it converts a 432-million-operation loss into a sub-second loss for **£37/month**.

**Step 4 — storage cost over time, because retention is a growing line item.**

```
35 days of PITR window:
  5 weekly fulls × 387 GB                       = 1,935 GB
  30 differentials × 14 GB                      =   420 GB
  35 days of WAL × 62 GB                        = 2,170 GB
                                          total = 4,525 GB  (4.4 TB)

Hot tier (35 days, ~£0.021/GB-mo):  4,525 × 0.021   = £95/mo
Then monthly fulls to cold, 12 months, Deep Archive (~£0.001):
  archiving 12 × 387 GB = 4,644 GB
  £4.64/mo, but retrieval is 12 h → RTO for anything older than 35 days = 12 h

Total steady state: ~£100/month, growing ~£3/month as the database grows 4 GB/day.
```

**Step 5 — the number that justifies all of it.** One hour of checkout downtime at BookNook's peak is **10 orders/sec × 3,600 s = 36,000 lost orders** at an average order value of **£18** = **£648,000 of gross revenue**, plus the trust cost. The backup and DR programme costs **£100/month plus one engineer-week a year for drills**. The break-even is **four minutes of downtime per year**. There is no other engineering investment in this course with that ratio, which is why "did you test the restore?" is a senior-interview question and not a formality.

## 10. Trade-offs

| Decision | Buys you | Costs you | Choose it when |
|---|---|---|---|
| **Nightly logical dump only** | Dead simple, portable, per-table restore | 24 h RPO, hours of RTO, index rebuild on restore | Databases under ~50 GB; non-critical internal tools |
| **Base backup + continuous WAL** | Seconds-level RPO, PITR to the transaction | Repo storage (4× the database over a 35-day window), replay time | **The default for any database you would page someone about** |
| **Delayed replica (1 h behind)** | Logical-corruption recovery in minutes | 1 h of extra compute; a replica you must remember not to promote accidentally | Any system where a human can run a bad `UPDATE` |
| **Synchronous standby** | RPO = 0 | +cross-AZ latency on every commit; availability coupling (Lesson 59) | Money, irrefutable records |
| **Managed PITR (RDS/Aurora)** | Someone else tests the restores; 5-min RPO for free | Cost premium; less control; restore is whole-instance | The default choice for teams without a dedicated DBA |
| **Object lock / WORM** | Survives ransomware and insider deletion | You cannot prune early, ever; you pay for the full window | Every backup corpus, no exceptions |
| **Cross-region + cross-provider copy** | Survives a region or an account loss | Egress cost; key management complexity; slow retrieval | When a regional outage is a stated business risk |
| **Deep Archive for everything** | Cheapest per GB | 12 h retrieval silently becomes your RTO | Only data you never expect to restore, or a legal-only copy |
| **Keep everything forever** | No retention decisions to make | Growing cost, growing blast radius, and a **compliance liability** | Never — indefinite retention of personal data is itself a violation |
| **Aggressive deletion (30 days)** | Cheap, minimal exposure | No forensic ability beyond a month; no year-over-year analysis | Data with genuinely short value (logs, clickstream) |

## 11. Failure scenarios

**11.1 — The backup job that stopped running in March.** Every monitoring check was "did the job exit 0?" and the job's cron entry was replaced during a base-image upgrade. Nobody noticed for **seven months**, because nothing alerts on "no new backup in the repo". **Symptom:** discovery at the worst possible moment. **Fix:** alert on **`pgbackrest info` age of the newest backup > 26 hours**, on repo size growth stalling, and on the *absence* of a success metric, not just on failures.

**11.2 — The restore that failed for want of one WAL segment.** `repo1-retention-archive=7` while the base backups were kept for 35 days. Restoring a 20-day-old backup needs 20 days of WAL; it had 7. The restore died at replay with `could not find file "00000001000000A20000004B"`. **Symptom:** a restore that fails *after* 40 minutes of work. **Fix:** `retention-archive` must be ≥ the full-backup retention window, and the drill (§8.4) proves it rather than assuming it.

**11.3 — The unencrypted bucket.** Backups were encrypted in transit only; the bucket was private. When a support engineer's credentials leaked, 1.2 TB of customer order history walked out. **Symptom:** a breach notification, not an outage — which is why it never appears in availability reviews. **Fix:** encryption at rest with **separate key custody** (KMS, not a key file next to the backup), and object-lock so deletion is not an option for the attacker.

**11.4 — Restoring the erased customer.** A restore for the §7.1 incident brought back 900 customers who had exercised erasure rights; the tombstone scrub was a manual wiki step performed by whoever was on call. It was not performed. **Symptom:** a compliance breach created *by the recovery procedure*. **Fix:** the scrub is part of the automated restore pipeline, and §8.4's drill asserts it.

**11.5 — Snapshot, not backup.** The team's "backup" was an EBS snapshot of the primary volume, taken hourly, in the same account. An insider deleted the volume and all its snapshots in 90 seconds. **Symptom:** total, unrecoverable loss. **Fix:** the snapshot was copy 2; there was no copy 3 or 4. §4.3.

**11.6 — Legal hold versus the deletion job.** The nightly erasure job deleted a customer's records that were under litigation hold; the hold was tracked in a spreadsheet. **Symptom:** spoliation, and a much larger problem than the original dispute. **Fix:** holds are **database rows**, joined by the deletion job (§8.1), with a test that a held ID is never erased.

**11.7 — Backup window colliding with peak.** A larger database pushed the diff backup past 07:00 into peak traffic; its sequential reads evicted the buffer pool, p99 went from 12 ms to 300 ms, and the cause was invisible in the application metrics. **Symptom:** a daily latency spike at the same hour. **Fix:** rate-limit backup I/O (`compress-level`, `process-max`, ionice/systemd `IOWeight`), schedule against the growth trend, and alert on backup duration approaching the window.

**11.8 — Restore worked; nothing else did.** After a region loss, the database was restored in 2 hours — and the service stayed down for 29 more because the DNS records, TLS certificates, secrets, IAM roles and deployment pipeline lived only in that region. **Symptom:** an RTO dominated by everything *except* data. **Fix:** DR covers the whole stack, and the drill restores the whole stack (or at least enumerates what is not covered and says so out loud).

**11.9 — WAL archive queue unbounded.** `archive-push-queue-max` was removed to "avoid write stalls", so the primary kept accepting writes while WAL piled up locally for two days — and then the disk filled and the database went read-only at the worst possible time. **Symptom:** an availability cliff caused by a durability setting. **Fix:** a **bounded** queue with an explicit, alarmed threshold (60 %/80 %), and an alarm on the *age* of the oldest unarchived segment. Whether writes block when the archive is unreachable is a CP-versus-AP choice (Lesson 59) and must be made deliberately, in writing.

## 12. Alternatives

| Instead of | Consider | When |
|---|---|---|
| Self-managed pgBackRest | **Managed PITR** (RDS/Aurora/Cloud SQL) | Team under ~5 engineers; the vendor's tested restores beat your untested ones |
| Full backups nightly | **Differential/incremental + WAL** | Database > ~200 GB, where a nightly full no longer fits the window |
| Backups for analytics history | **CDC into a warehouse** (Lesson 85, Phase 3 L58) | You want *history* for analysis, not recovery — a backup is the wrong tool for querying the past |
| Restoring to recover deleted rows | **Soft deletes + tombstones** in the primary | Deleting is a routine operation; restore for it is a 105-minute answer to a 1-second question |
| Delayed replica promotion | **PITR** | The corruption is older than the delay window, or partial (one table) |
| Multi-region active/active | **Warm standby + tested runbook** | Almost always. Active/active quadruples the consistency surface (Lesson 91) for an RTO gain you probably cannot justify |
| Object storage for everything | **Tiered lifecycle** | Cost matters and retrieval latency is bounded by an explicit RTO |

## 13. When to use it

- **Any database you would wake someone up for.** Non-negotiable: continuous WAL archiving, immutable repository, tested restore. That is the floor, not the ambition.
- **Before any destructive migration.** Take a fresh base backup and record its identifier in the change record — the cheapest possible insurance for a `DROP COLUMN`.
- **When a compliance regime applies** (tax, GDPR/DPDP, PCI, HIPAA): the retention schedule and the erasure mechanism are deliverables, not afterthoughts.
- **When storage cost grows faster than users.** Archival plus a retention schedule is usually the largest single cost lever in a data platform.
- **When designing the error budget** (Phase 4, Lesson 89): RTO and RPO are inputs to your SLO, and their *measured* values belong in the same dashboard as your latency.
- **Whenever someone says "we have replicas, we're safe"** — that sentence is the reason this lesson exists.

## 14. When *not* to use it

- **As a substitute for replication.** Backups do not give you availability; a 105-minute RTO is not a failover strategy for a disk failure.
- **As a substitute for idempotency** (Lesson 17). Restoring to fix a duplicate charge is a catastrophe; an idempotency key is a design.
- **_Only_ a backup, with no drill.** An untested backup is a hypothesis. If you cannot restore it, you do not have it.
- **Snapshot-only "backups" in the same account.** Not a backup (§4.1). Three failures in §11 have this root cause.
- **Retaining everything forever "just in case".** That is a growing cost and, for personal data, a liability. Retention is a decision with an owner.
- **Backing up into the same blast radius.** Same account, same region, same credentials as the primary means one compromise or one control-plane fault takes both.
- **Treating DR as a database problem.** §11.8: the data is rarely the long pole.

## 15. Common mistakes

1. **"We have a replica, so we have a backup."** Every `DELETE`, `DROP` and bad migration is in the replica within milliseconds.
2. **Never restoring.** The first restore of a backup's life is usually during an incident. Drills are the deliverable.
3. **Alerting on job failure instead of on backup age.** A disabled job fails nothing.
4. **Retention windows that do not nest.** Base backups kept longer than the WAL that makes them recoverable (§11.2).
5. **Deleting a replica's WAL because "it's not needed".** A standby needs WAL to catch up; pruning it strands the replica and silently inflates its lag until it cannot recover without a full rebuild.
6. **Backups in the same account, region or provider as production.** One credential compromise, one blast radius.
7. **No immutability.** The attacker's first step is to destroy your recovery path.
8. **Encryption keys stored with the backups.** Encryption that the attacker can undo is not encryption. Use KMS with separate custody.
9. **No tombstone/erasure handling in the restore path.** You will resurrect erased users (§11.4).
10. **Unbounded WAL archive queues.** Silent data-loss exposure (§11.9), or a full disk if you prefer to lose availability instead.
11. **Restoring over the production primary.** Always restore to a new instance and repoint; an in-place restore destroys your only remaining forensic copy of the incident.
12. **Not deciding what happens to the delta.** The writes after the recovery target are a business decision that must be made *before* the incident (§5).
13. **Retention expressed as intent, never as a job with a metric.** §8.1 is the difference between a policy and a wish.
14. **Backup window colliding with peak** (§11.7), which is a latency incident in disguise.
15. **Documenting an RTO instead of measuring one.** 105 minutes versus a documented 60 is the norm, not the exception.

## 16. Interview perspective

**What is actually being tested.** Three things, in order of signal value. First, whether you know that **replication is not backup**, and can articulate the failure class only backups address. Second, whether you can **derive RTO and RPO from a business requirement and price them**, rather than reciting "3-2-1". Third, whether you have internalised that **the restore is the product** — a tested, timed, rehearsed procedure with a named owner, not a cron job that exits 0.

**Answers that land well:**

- *"How do you protect the orders database?"* — "Continuous WAL archiving into an object-locked bucket in a separate account, with a weekly full and daily differentials, 35 days of PITR, plus a second copy at a different provider in Deep Archive for the audit tail. Measured RTO is 105 minutes for a full restore, so for logical corruption we keep a replica one hour behind — that promotes in 4 minutes instead of 105. RPO is under a second because WAL is streamed."
- *"A bad migration dropped a column and backfilled wrong data. What do you do?"* — "Do not fail over — the standby has it too. Freeze writes, note the exact timestamp, restore a base backup to a *new* instance with `recovery_target_time` just before the migration, verify against known-good counts, then decide what to do about writes since that point. Meanwhile the delayed replica would have made this a 4-minute promotion, which is why we keep one."
- *"How do you handle GDPR erasure with immutable backups?"* — "Crypto-shredding: per-customer keys, deletion of the key, ciphertext stays unreadable in the backups until they expire. Plus a tombstone list applied automatically by the restore pipeline, because a 35-day window and a manually applied scrub both fail eventually."
- Volunteering **"what is the measured RTO, and when did you last restore?"** signals the whole mindset in one sentence.

**Common interview tasks:** design a backup and DR strategy for a given RPO/RTO; compute a restore time from data volume and throughput; explain why a replica is not a backup; explain PITR mechanics; handle a bad `DELETE` step by step; reconcile GDPR erasure with immutable backups; choose a DR tier for a stated budget.

**Red flags interviewers watch for:**

- "We have RPO zero because we have synchronous replication" — confusing availability with recoverability.
- Confusing RPO and RTO, or being unable to state them as time.
- No mention of testing the restore.
- Backups stored in the same blast radius as the primary (same account, region, credentials).
- Not knowing that a restore creates a *new* instance, in parallel, not in place.
- No answer for "the writes after the recovery point".
- "3-2-1" recited with no rationale, and no immutability in the design.
- Treating backups as purely an ops concern with no legal/retention dimension, or vice versa.
- Saying "we keep everything forever" and not noticing the compliance implication.
- No numbers: no sizes, no throughputs, no restore times.

## 17. Knowledge check

**Conceptual**

1. Explain precisely why a synchronous replica does not protect you against a mistaken `DELETE`, and identify the class of failures in which it *is* the right recovery mechanism. Then state the one design that recovers from the `DELETE` in minutes rather than hours, and explain why it is not simply "a backup".
2. Define RPO and RTO and explain why they are independent. Then explain why a snapshot taken every hour in the same account does **not** achieve RPO = 1 hour from a risk perspective, even though its data age is 1 hour.
3. Describe how crypto-shredding satisfies an erasure request in the presence of immutable 35-day backups, state the remaining gap it does not close, and name the compensating control.

**Practical scenario**

4. BookNook's database is 1.2 TB, grows 4 GB/day, writes 250 GB/day of WAL, and peak ingest is 5,000 writes/sec. The business states: **RPO ≤ 5 seconds, RTO ≤ 30 minutes, 35-day PITR, erasure within 30 days, and ransomware must not be able to destroy recoverability.** Current measured full-restore time is 105 minutes and the database sits behind a 1.6 Gbps link. Design the complete solution: repository topology with failure domains, backup types and schedules, retention nesting for base backups and WAL, the mechanism that achieves the 30-minute RTO, the erasure mechanism and where it hooks into the restore path, the immutability control, three alerts, and the quarterly drill you would automate with the pass criteria for each step.

**Calculation**

5. A database is 800 GB raw with a 3.2:1 compression ratio; WAL is 180 GB/day (3.5:1 compressed); the backup link runs at 300 MB/s; restore writes at 500 MB/s; WAL replay runs at 120 MB/s. (a) Compute the compressed full-backup size and its upload time. (b) Compute the restore time for a restore requiring 6 hours of WAL replay, broken into download, decompress/write, replay, and a fixed 15 minutes for provisioning, verification and repointing. (c) Compute the 30-day repository footprint assuming 5 fulls, 25 differentials of 12 GB compressed, and 30 days of compressed WAL. (d) Compute the monthly cost at £0.021/GB hot for the first 30 days and £0.001/GB Deep Archive for a further 12 months of monthly fulls.

6. An incident requires restoring to a point 1 hour 42 minutes in the past. During that window the system accepted 5,000 writes/sec, of which 10 per second were customer orders with an average value of £18. Calculate (a) the total number of write operations that must be discarded or replayed, (b) the number of orders affected, (c) the gross revenue at risk, and (d) the percentage of a 99.95 % monthly error budget consumed if the outage lasts 105 minutes.

**Progress checklist**

- Phase 1 — Foundations (Lessons 1–17): **complete** ✅
- Phase 2 — Scaling (Lessons 18–39): **complete** ✅
- Phase 3 — Data and Storage:
  - [x] Topic 1 — Relational databases (L40)
  - [x] Topic 2 — Tables, keys, relationships, joins, constraints (L41)
  - [x] Topic 3 — SQL versus NoSQL (L42)
  - [x] Topic 4 — Key-value databases (L43)
  - [x] Topic 5 — Document databases (L44)
  - [x] Topic 6 — Wide-column databases (L45)
  - [x] Topic 7 — Graph databases (L46)
  - [x] Topic 8 — Search engines and full-text indexes (L47)
  - [x] Topic 9 — Selecting a database from access patterns (L48)
  - [x] Topic 10 — ACID transactions (L49)
  - [x] Topic 11 — Isolation levels (L50)
  - [x] Topic 12 — Dirty/non-repeatable/phantom reads, lost updates (L51)
  - [x] Topic 13 — Optimistic versus pessimistic concurrency control (L52)
  - [x] Topic 14 — Database indexes (L53)
  - [x] Topic 15 — B-tree, hash, composite, covering, secondary indexes (L54)
  - [x] Topic 16 — Query plans and index trade-offs (L55)
  - [x] Topic 17 — Normalisation and denormalisation (L56)
  - [x] Topic 18 — OLTP versus OLAP (L57)
  - [x] Topic 19 — Data warehouses, data lakes, lakehouses (L58)
  - [x] Topic 20 — CAP theorem (L59)
  - [x] Topic 21 — PACELC (L60)
  - [x] Topic 22 — Consistency models (L61)
  - [x] Topic 23 — Object, block, and file storage (L62)
  - [x] **Topic 24 — Retention, archival, backups, disaster recovery (L63) ← you are here**
  - [x] **Phase 3 complete — see the Phase 3 wrap-up next**

**NEXT: Phase 3 Wrap-Up — Data and Storage**

---

# Phase 3 Wrap-Up — Data and Storage

Lessons 40–63, covering Phase 3 Topics 1–24.

Phase 1 (L1–17) built the foundations — protocols, latency, caching, the request path. Phase 2 (L18–39) scaled the stateless tier and then ran into the wall every system eventually hits: **the data layer is the part that cannot be trivially cloned.** Phase 3 was about that layer. This wrap-up is the revision artefact: everything that matters, compressed, plus the work you should actually do before Phase 4.

The continuing example throughout is **BookNook**, an online bookstore.

---

## 1. Revision sheet

### Topic 1 — Relational databases (L40)

| Fact | Detail |
|---|---|
| Definition | A database that stores data as **relations** (tables) of **tuples** (rows) over a fixed **schema**, manipulated by relational algebra and queried declaratively via SQL. |
| The core bargain | You declare *what* you want; the **query planner** decides *how*. You give up control over execution in exchange for the planner adapting as data and indexes change. |
| Why it has survived 50 years | Three properties almost nothing else offers together: **declarative querying**, **ACID transactions across arbitrary rows**, and **enforced referential integrity**. |
| Physical reality | Rows are packed into fixed-size **pages** (PostgreSQL: 8 KB, MySQL/InnoDB: 16 KB). The **buffer pool** caches hot pages in RAM. Every change is written to the **write-ahead log** before the data page. |
| The number that matters | A random 8 KB page read from NVMe SSD is **~100 µs**; from the buffer pool it is **~100 ns**. A **1,000× gap** — which is why buffer-pool hit ratio, not disk speed, usually determines query latency. |
| Typical sizing rule | Size the buffer pool at **25 % of RAM** (PostgreSQL `shared_buffers` default guidance) or **~70 % of RAM** (InnoDB `innodb_buffer_pool_size`), because PostgreSQL also relies on the OS page cache and InnoDB does not. |
| When it stops being the right answer | When a single primary cannot hold the **write** volume and the workload genuinely has no cross-entity transactions — not merely when the data is large. Large is solved by indexes, partitioning and read replicas (L31). |

### Topic 2 — Tables, keys, relationships, joins, constraints (L41)

| Concept | Precise definition | BookNook example |
|---|---|---|
| **Primary key** | Minimal set of columns that is unique and NOT NULL; identifies exactly one row. | `books.book_id` |
| **Candidate key** | Any minimal unique identifier; one is chosen as primary, the rest become unique constraints. | `books.isbn13` |
| **Natural key** | A key with business meaning (ISBN, email). | `isbn13` |
| **Surrogate key** | A key with no business meaning (sequence, UUID) used so the identifier never changes when the business facts change. | `book_id BIGINT` |
| **Foreign key** | Column(s) whose values must exist as a primary/unique key in another table. Enforces **referential integrity**. | `order_items.book_id → books.book_id` |
| **Composite key** | Primary key spanning 2+ columns; column order matters for the backing index. | `(order_id, line_no)` |

| Join type | Returns | Cost note |
|---|---|---|
| `INNER` | Only matching pairs | Cheapest; planner can choose any algorithm |
| `LEFT OUTER` | All left rows + matched right, NULLs otherwise | Blocks some join reorderings |
| `FULL OUTER` | All rows from both sides | Usually hash join only |
| `CROSS` | Cartesian product, N×M rows | An accidental cross join is the single most common cause of a query that "hangs" |
| `SEMI` (`EXISTS`) | Left rows that have ≥1 match, no duplication | Often faster than `IN` on large right sides |
| `ANTI` (`NOT EXISTS`) | Left rows with no match | Beware `NOT IN` + NULL: returns zero rows, silently |

| Join algorithm | Cost | Planner picks it when |
|---|---|---|
| **Nested loop** | O(N × M), or O(N × log M) with an index on the inner side | Outer side is small (typically <1,000 rows) and inner has a usable index |
| **Hash join** | O(N + M), needs the smaller side to fit in `work_mem` | Both sides large, join is on equality |
| **Merge join** | O(N log N + M log M), or O(N + M) if both already sorted | Both inputs sorted (e.g. by index), or sort is needed anyway for `ORDER BY` |

| Constraint | Guarantees |
|---|---|
| `NOT NULL` | Value present |
| `UNIQUE` | No duplicates (NULLs usually exempt — a classic bug) |
| `CHECK` | Row-level predicate, e.g. `CHECK (price_cents >= 0)` |
| `FOREIGN KEY` | Referential integrity; `ON DELETE CASCADE / RESTRICT / SET NULL` |
| `EXCLUDE` (PostgreSQL) | Generalised uniqueness, e.g. no two overlapping reservation ranges |

**Rule that matters:** a constraint in the database is enforced for *every* writer — the app, the migration script, the 3 a.m. manual `UPDATE`. A constraint only in application code is enforced for none of them.

### Topic 3 — SQL versus NoSQL (L42)

| Axis | SQL (relational) | NoSQL (the four families) |
|---|---|---|
| Schema | Declared up front, enforced on write | Schema-on-read, or per-document |
| Query | Declarative, planner-optimised, ad hoc joins | Access-path-first; you design for known queries |
| Transactions | ACID across arbitrary rows/tables | Usually single-item or single-partition atomicity |
| Scale-out | Vertical first; sharding is bolted on (L33) | Horizontal partitioning is native |
| Joins | Server-side | Usually absent — you denormalise or join in the app |

| Myth | Correction |
|---|---|
| "NoSQL has no schema" | It has an **implicit** schema, enforced by every reader. You moved the cost, not removed it. |
| "NoSQL is faster" | It is faster **for the access pattern it was modelled for**, and dramatically slower for any other. |
| "SQL doesn't scale" | A single PostgreSQL primary on modern hardware handles roughly **10k–50k writes/sec** and terabytes of data. Most systems never reach that. |
| "NoSQL means no transactions" | DynamoDB, MongoDB and Cassandra all offer transactions now, with restrictions (single partition, or costly 2-phase protocols). |

**The actual decision rule:** choose NoSQL when (a) your access patterns are few and known, (b) you need horizontal write scale beyond one primary, *and* (c) you can tolerate the consistency model. If only (a) is true, relational is still the cheaper answer.

### Topic 4 — Key-value databases (L43)

| Fact | Detail |
|---|---|
| Model | `key → opaque value`. Operations: `GET`, `PUT`, `DELETE`, sometimes `CAS` (compare-and-swap) and TTL. |
| Why it's fast | Exactly one access path — a hash or ordered lookup. No planner, no joins, no secondary structures to maintain. |
| Typical latency | Redis in-memory: **p50 ~0.2 ms, p99 ~1 ms** in-datacentre. DynamoDB: **p50 ~3 ms, p99 ~10 ms** single-digit by design. |
| Partitioning | `partition = hash(key) mod N` or consistent hashing (L34). Key choice *is* the shard key. |
| The hard limit | No queries by value. "Find all carts over £50" requires a full scan or a separately maintained index. |
| BookNook uses | Session store, cart (`cart:{user_id}`), rate-limit counters, idempotency keys, page-fragment cache. |
| Sizing example | 2 M active carts × 2 KB = **4 GB** — comfortably one Redis node; with replication, 2 nodes. |

### Topic 5 — Document databases (L44)

| Fact | Detail |
|---|---|
| Model | Self-describing documents (JSON/BSON) in collections; nested arrays and sub-objects are first-class. |
| Core win | **Locality** — the entire aggregate is one disk read. A product page that would need 6 joins becomes 1 document fetch. |
| Atomicity unit | A single document. Multi-document transactions exist in MongoDB ≥4.0 but cost roughly **2–5×** a single-document write. |
| Indexing | Secondary indexes on any field, including nested paths and array elements (multikey indexes). |
| Aggregation pipeline | Staged transformation (`$match → $group → $sort → $project`). Put `$match` first so it can use an index; a `$match` after `$group` scans everything. |
| Document size limit | MongoDB: **16 MB**. Unbounded arrays inside a document (e.g. every review on a book) are the standard modelling mistake. |
| The 6 MB / 1,000-element heuristic | If an embedded array can exceed ~1,000 elements or the document ~1 MB, reference instead of embed. |
| BookNook uses | Book catalogue: title, authors, formats, editions, per-edition pricing, publisher metadata — highly variable shape per genre. |

### Topic 6 — Wide-column databases (L45)

| Fact | Detail |
|---|---|
| Model | `(partition key, clustering key) → columns`. A partition is a sorted, co-located set of rows; this is **not** columnar storage. |
| Engine | **LSM-tree**: writes go to memtable + commit log, flush to immutable **SSTables**, merged by **compaction**. Deletes write **tombstones**. |
| Write cost | O(1) append — Cassandra routinely sustains **tens of thousands of writes/sec per node**. |
| Read cost | May touch several SSTables; mitigated by bloom filters and partition-key indexes. |
| Hard rule | **You must know the partition key to read efficiently.** Queries without it either fail or scan the whole cluster (`ALLOW FILTERING` — effectively a loaded gun). |
| Partition sizing | Keep partitions under **~100 MB** and **~100,000 rows**. A partition that grows unbounded (e.g. all events for one popular book) is the classic failure. |
| Tunable consistency | `R + W > RF` gives read-your-writes within a datacentre. Typical: RF=3, W=QUORUM(2), R=QUORUM(2) → 2+2 > 3 ✓. |
| BookNook uses | Order/event history by `(user_id, order_ts DESC)`, page-view time series, inventory movement log. |

### Topic 7 — Graph databases (L46)

| Fact | Detail |
|---|---|
| Model | Nodes, typed directed edges, properties on both. Query languages: **Cypher** (Neo4j), **Gremlin**, **SQL/PGQ**. |
| Core win | **Index-free adjacency** — each node stores direct pointers to its edges, so traversing one hop is a pointer dereference, **O(1)**, independent of total graph size. |
| The crossover | A relational self-join costs roughly O(log N) per hop *and* the planner's row estimates degrade multiplicatively. By **3–4 hops** a graph database typically wins by **10–100×**; at 1–2 hops SQL is usually fine and often faster. |
| Variable-length queries | `MATCH (a)-[:BOUGHT*1..4]->(b)` — expressing this in SQL requires recursive CTEs that are hard to read and harder to tune. |
| Weakness | Graph DBs scale out poorly: partitioning a graph means cutting edges, and cut edges become network hops. Most production graphs are **single-machine with replicas**. |
| BookNook uses | "Readers who bought this also bought", author co-authorship networks, fraud rings (shared card/device/address between accounts). |

### Topic 8 — Search engines and full-text indexes (L47)

| Concept | Detail |
|---|---|
| **Inverted index** | Maps `term → posting list of (doc_id, positions, term frequency)`. The inverse of a normal row store. |
| Analysis chain | tokenise → lowercase → remove stop words → **stemming**/lemmatisation → optional synonyms → n-grams for prefix search. Query text must go through the *same* chain or nothing matches. |
| **TF-IDF** | `tf(t,d) × log(N / df(t))` — a term is informative if frequent in this doc and rare across the corpus. |
| **BM25** | The modern default. Adds term-frequency **saturation** (`k₁`, typically **1.2**) and **length normalisation** (`b`, typically **0.75**), so the 20th occurrence of a word adds almost nothing and long documents are not unfairly favoured. |
| Latency | Elasticsearch/OpenSearch typical: **p50 10–30 ms, p99 100–200 ms** for a sharded catalogue of millions of docs. |
| Freshness | Near-real-time, not real-time: default **1 s refresh interval**. A book edited now is typically searchable in ≤1 s, not instantly. |
| Critical rule | **A search engine is an index, not a system of record.** It must be rebuildable from the primary store, because reindexing after a mapping change is routine. |
| PostgreSQL alternative | `tsvector` + **GIN index** handles up to roughly **1–10 M documents** with modest ranking needs — worth using before adding a whole extra system. |

### Topic 9 — Selecting a database from access patterns (L48)

**The method, in order:**

1. List every read and write the system performs.
2. For each: QPS (peak, not average), latency SLO, row/object size, consistency requirement, growth rate.
3. Identify the *entry key* for each access — the value you have in hand when the query starts.
4. Group accesses sharing an entry key; each group is a candidate store/table.
5. Pick the engine whose native access path matches the entry key.
6. Only then consider operational cost: can your team run it at 3 a.m.?

| BookNook access pattern | Entry key | Store | Why |
|---|---|---|---|
| Place order, decrement stock | `order_id` / `book_id` | PostgreSQL | Multi-row ACID, money |
| Fetch book detail page | `book_id` | Document DB + cache | Whole aggregate, one read |
| Search "science fiction 2020" | free text | Search engine | Inverted index, ranking |
| User's order history | `user_id` | Wide-column | Time-ordered partition |
| Cart, sessions | `user_id` | Key-value | Ephemeral, hot, TTL |
| "Also bought" | `book_id`, multi-hop | Graph | Index-free adjacency |
| Monthly revenue by genre | none (scan) | Warehouse/columnar | Aggregate over columns |

**Polyglot persistence is a cost, not a prize.** Each additional store adds a replication path, a backup regime, an on-call runbook and a consistency seam. The defensible version is: **one system of record, N derived stores**, all rebuildable from it.

### Topic 10 — ACID transactions (L49)

| Letter | Precise meaning | Mechanism |
|---|---|---|
| **A**tomicity | All statements in the transaction take effect or none do. *Abortability*, not concurrency. | WAL + undo/rollback |
| **C**onsistency | The transaction moves the database from one state satisfying all declared constraints to another. **This is the application's property**, not the database's — the DB only enforces what you declared. | Constraints, triggers |
| **I**solation | Concurrent transactions produce a result equivalent to *some* serial order (at the strongest level). | Locking or MVCC |
| **D**urability | Once commit returns, the effect survives crash. | WAL `fsync`, replication |

| Fact | Detail |
|---|---|
| Durability is a spectrum | `fsync` to local disk survives process+OS crash, not disk loss. Synchronous replication to another node survives machine loss. Multi-AZ survives rack/AZ loss. Pick deliberately. |
| Cost of `fsync` | **~0.5–2 ms** on NVMe with power-loss protection; **~5–10 ms** on network storage. This sets the floor on commit latency and therefore on write throughput per connection. |
| Group commit | Batches many commits into one `fsync`, converting per-transaction cost into per-batch cost — the reason a DB can do 20k commits/sec on a device doing 2k fsyncs/sec. |
| BASE contrast | **B**asically **A**vailable, **S**oft state, **E**ventual consistency — the deliberate opposite trade, chosen for availability and write scale. |

### Topic 11 — Isolation levels (L50)

| Level | Dirty read | Non-repeatable read | Phantom | Lost update | Write skew |
|---|---|---|---|---|---|
| READ UNCOMMITTED | possible | possible | possible | possible | possible |
| READ COMMITTED | prevented | possible | possible | possible | possible |
| REPEATABLE READ (SQL standard, lock-based) | prevented | prevented | possible | prevented | possible |
| **Snapshot isolation** (PostgreSQL's "REPEATABLE READ") | prevented | prevented | prevented | prevented (first-updater-wins abort) | **possible** |
| SERIALIZABLE | prevented | prevented | prevented | prevented | prevented |

| Fact | Detail |
|---|---|
| Defaults you must memorise | PostgreSQL: **READ COMMITTED**. MySQL/InnoDB: **REPEATABLE READ**. Oracle: READ COMMITTED (snapshot-based). SQL Server: READ COMMITTED (lock-based unless RCSI is on). |
| The naming trap | PostgreSQL REPEATABLE READ is **snapshot isolation** — stronger than the standard (no phantoms) but still allows **write skew**. MySQL REPEATABLE READ uses next-key locking and blocks phantoms in many but not all cases. |
| PostgreSQL SERIALIZABLE | Implemented as **SSI** (serializable snapshot isolation) — optimistic, no extra locks, but transactions can abort with SQLSTATE **40001**. Your application *must* have a retry loop. |
| Typical overhead | SSI costs roughly **0–10 %** throughput on low-conflict workloads and can collapse under high conflict due to abort storms. Measure before assuming either. |
| Statement-level snapshots | Under READ COMMITTED each *statement* sees a fresh snapshot — so two `SELECT`s in one transaction can disagree. This surprises people more than any other single fact in Phase 3. |

### Topic 12 — Dirty, non-repeatable, phantom reads and lost updates (L51)

| Anomaly | Precise definition | BookNook scenario | Minimum level that prevents it |
|---|---|---|---|
| **Dirty read** | Reading a row version written by a transaction that has not committed. | Order total read mid-write, then the writer rolls back; the confirmation email quotes a price that never existed. | READ COMMITTED |
| **Dirty write** | Overwriting an uncommitted write of another transaction. | Two concurrent address updates interleave into a mixed address. | READ COMMITTED (all levels prevent it) |
| **Non-repeatable read** | Re-reading the same row in one transaction returns different committed values. | Stock shown as 5 at the top of checkout, 3 at the bottom; totals and reservation disagree. | REPEATABLE READ / SI |
| **Phantom read** | Re-running the same *predicate query* returns a different set of rows because another transaction inserted/deleted matching rows. | `COUNT(*) WHERE status='pending'` changes between the check and the action. | SI (PostgreSQL) or SERIALIZABLE |
| **Lost update** | Two read-modify-write cycles overlap; one update silently vanishes. | Two warehouse workers each read `stock = 10`, each write `9`; two units sold, one deducted. | REPEATABLE READ/SI, or atomic write, or explicit lock |
| **Write skew** | Two transactions read an overlapping set, each sees a constraint satisfied, each writes a *different* row, and the combined result violates the constraint. | Two refunds each check "total refunds < order total" against the same snapshot; combined they exceed it. | **SERIALIZABLE only** |

**Memorise this hierarchy:** lost update is a *same-row* conflict; write skew is a *different-row* conflict on a shared premise. Snapshot isolation catches the first and misses the second. That one sentence explains why SERIALIZABLE exists.

### Topic 13 — Optimistic versus pessimistic concurrency control (L52)

| | Pessimistic | Optimistic |
|---|---|---|
| Assumption | Conflicts are likely | Conflicts are rare |
| Mechanism | Acquire locks before access (`SELECT … FOR UPDATE`, 2PL) | Read freely, validate at write (version column / CAS / SSI) |
| Failure mode | **Blocking**, lock convoys, **deadlocks** | **Aborts** and retries, livelock under contention |
| Cost when uncontended | Lock acquisition overhead on every access | Near zero |
| Cost when contended | Serialised throughput, but every transaction eventually succeeds | Wasted work; throughput can collapse |
| Holds locks across user think-time? | Never do this | N/A — this is optimistic locking's main use case |

| Fact | Detail |
|---|---|
| Crossover heuristic | Below roughly **10 % conflict rate**, optimistic wins; above roughly **20–30 %**, pessimistic wins. Between, measure. |
| Optimistic pattern | `UPDATE books SET stock=?, version=version+1 WHERE book_id=? AND version=?` → `rowcount = 0` means conflict; re-read and retry. |
| Deadlock reality | Databases **detect** deadlocks (PostgreSQL checks after `deadlock_timeout`, default **1 s**) and abort one victim with SQLSTATE **40P01**. Prevention = always acquire locks in a consistent order, e.g. ascending primary key. |
| Retry policy | Exponential backoff with jitter, cap at **3–5 attempts**. Unbounded retries turn a conflict spike into an outage. |
| Lock granularity ladder | Row → page → table. Escalation (SQL Server) or too-coarse locking turns a row conflict into a table-wide stall. |

### Topic 14 — Database indexes (L53)

| Fact | Detail |
|---|---|
| Definition | An auxiliary data structure mapping column values to row locations, trading **write cost and storage** for **read speed**. |
| Lookup cost | B+tree height for N rows with fanout f ≈ `log_f(N)`. With f≈400 and N=100 M: **~3–4 levels**, so ~4 page reads — and the top levels are always cached. |
| Write cost | Every index must be updated on `INSERT`/`DELETE` and on `UPDATE` of an indexed column. **Rule of thumb: each additional index costs ~5–10 % write throughput.** |
| Storage cost | Typically **10–30 %** of the table size per index; wide composite indexes can exceed the table. |
| Selectivity | `distinct_values / total_rows`. Below ~**5 %** selectivity (i.e. a predicate returning >5 % of rows) the planner usually prefers a sequential scan — random I/O for many rows is slower than streaming. |
| Non-usable predicates | `WHERE lower(email) = ?` cannot use an index on `email` — use an **expression index**. Leading wildcard `LIKE '%son'` cannot use a B-tree — use trigram/GIN. Implicit type casts silently disable indexes. |
| NULL handling | PostgreSQL B-trees index NULLs; Oracle B-trees do not. This changes whether `WHERE col IS NULL` can use the index. |
| Maintenance | Indexes **bloat** under heavy updates. `REINDEX CONCURRENTLY` (PG 12+) rebuilds without blocking writes. |

### Topic 15 — B-tree, hash, composite, covering, secondary indexes (L54)

| Index type | Structure | Supports | Does not support |
|---|---|---|---|
| **B+tree** | Balanced tree, all values in sorted leaves, leaves linked | `=`, `<`, `>`, `BETWEEN`, prefix `LIKE 'ab%'`, `ORDER BY`, `MIN/MAX` | Leading wildcards, arbitrary expressions |
| **Hash** | Hash table | `=` only | Ranges, ordering |
| **GIN** | Inverted index over composite values | Array containment, `jsonb` keys, full-text `tsvector`, trigrams | Range on the whole value; writes are expensive |
| **GiST** | Generalised search tree | Geometry, ranges, nearest-neighbour, exclusion constraints | Exact-match speed of B-tree |
| **BRIN** | Min/max summary per block range | Huge, naturally-ordered tables (append-only timestamps) | Randomly-ordered data — degenerates to a full scan |
| **Bitmap** | Bit per row per value | Low-cardinality columns, analytical `AND`/`OR` combinations | High-update OLTP (lock granularity is terrible) |

| Concept | Rule |
|---|---|
| **Composite index** | Column order is everything. An index on `(a, b, c)` serves `WHERE a=`, `WHERE a= AND b=`, `WHERE a= AND b= AND c=` and `WHERE a= ORDER BY b`. It does **not** serve `WHERE b=` alone. |
| Leftmost-prefix rule | A composite index is usable only from the leftmost column rightwards, with equality on every column before the first range predicate. |
| Ordering heuristic | Equality columns first, then the range column, then columns only needed for ordering or output. |
| **Covering index** | Contains every column the query needs, so the heap is never touched → **index-only scan**. PostgreSQL: `CREATE INDEX … INCLUDE (cols)`. Typical speedup: **2–10×**. |
| **Clustered index** | The index *is* the table; leaves hold full rows (InnoDB primary key, SQL Server clustered). One per table. Secondary indexes then store the **primary key**, not a row pointer, so every secondary lookup is a double traversal. |
| **Secondary (non-clustered) index** | Separate structure pointing at rows. PostgreSQL has only this kind; the table is a **heap**. |
| **Partial index** | `WHERE` clause on the index itself: `CREATE INDEX … ON orders(created_at) WHERE status='pending'`. If 0.5 % of rows are pending, the index is **200× smaller** and stays cached. |
| Primary-key choice | **UUID v4 is a pathological clustered key** — random inserts scatter across the B-tree, causing page splits and write amplification. **UUID v7** (time-ordered, RFC 9562) or a `bigint` sequence keeps inserts at the right edge. |

### Topic 16 — Query plans and index trade-offs (L55)

| Element | What it tells you |
|---|---|
| `EXPLAIN` | The plan the planner *would* choose, with **estimated** costs and rows. |
| `EXPLAIN ANALYZE` | Actually executes, giving **actual** time and rows. Always compare estimated vs actual rows. |
| `BUFFERS` | Pages hit (cache) vs read (disk). The real measure of I/O, far better than wall-clock on a warm machine. |
| Row estimate error | If `rows=10` but `actual rows=120,000`, every decision above that node is wrong. **Fix the statistics, not the query.** |
| Cost units | Arbitrary, anchored at `seq_page_cost = 1.0`. `random_page_cost` defaults to **4.0** (spinning-disk assumption); on SSD set it to **1.1** — this single change flips many bad sequential scans into index scans. |

| Node | Meaning | Red flag when |
|---|---|---|
| **Sequential scan** | Read every page | Table is large *and* the filter is selective |
| **Index scan** | Walk the index, fetch each matching heap row | Returning >5–10 % of the table (random I/O storm) |
| **Index-only scan** | Satisfied entirely from the index | `Heap Fetches` is high → run `VACUUM` to refresh the visibility map |
| **Bitmap heap scan** | Build a bitmap of matching pages, then read pages in physical order | Normal for mid-selectivity; `Recheck Cond` with `lossy` means `work_mem` was too small |
| **Nested loop** | Row-at-a-time inner lookup | Outer actual rows ≫ estimate — the classic "fast in staging, hangs in production" |
| **Hash join** | Build hash on smaller side | `Batches > 1` means it spilled to disk; raise `work_mem` |
| **Sort** | Explicit ordering | `Sort Method: external merge Disk: …kB` means it spilled |

**Index trade-off accounting, explicitly:** adding an index buys you one access path and costs you (1) ~5–10 % write throughput, (2) 10–30 % of table size on disk and in the buffer pool, (3) a longer `VACUUM`/rebuild window, (4) a slightly larger planner search space. Adding an index is justified when the query it serves is frequent or latency-critical; it is *not* justified "just in case".

### Topic 17 — Normalisation and denormalisation (L56)

| Form | Requirement | Violation example |
|---|---|---|
| **1NF** | Atomic values; no repeating groups in a column | `authors = "Gibson, Sterling"` in one text field |
| **2NF** | 1NF + no partial dependency on part of a composite key | `(order_id, book_id) → book_title` — title depends only on `book_id` |
| **3NF** | 2NF + no transitive dependency (non-key → non-key) | `order_id → postcode → city` |
| **BCNF** | Every **functional dependency**'s determinant is a superkey | Overlapping candidate keys where a non-superkey determines a key attribute |

| Fact | Detail |
|---|---|
| Normalisation buys | One fact in one place → no update anomalies, no insert anomalies, no delete anomalies; smaller rows, better cache density. |
| Normalisation costs | More joins per query; a 5-table join for one product page. |
| Denormalisation buys | Fewer joins, fewer round trips, pre-computed aggregates. |
| Denormalisation costs | **Every copy must be kept in sync**, forever, by code you must write and test. The duplicate is now a *cache*, with all of caching's invalidation problems (L13). |
| Discipline | **Normalise to 3NF first; denormalise only with a measurement that proves the join is the bottleneck.** Record *why* in a comment next to the column. |
| Safer middle grounds | **Materialised views** (DB-maintained copy, refreshed on demand or concurrently), generated/computed columns, summary tables rebuilt by a job, counter caches updated in the same transaction. |
| BookNook example | `books.review_count` and `books.avg_rating` denormalised because the catalogue page is read ~2,000× per write and the aggregate over 50 M reviews costs ~400 ms. |

### Topic 18 — OLTP versus OLAP (L57)

| Dimension | OLTP | OLAP |
|---|---|---|
| Unit of work | Short transaction touching few rows | Long query scanning millions of rows, few columns |
| Rows per query | 1–100 | 10⁶–10¹⁰ |
| Columns per query | Most of the row | 3–10 of 200 |
| Concurrency | Thousands of sessions | Tens of analysts/jobs |
| Latency target | **p99 < 50 ms** | **seconds to minutes** |
| Storage layout | **Row-oriented** (whole row on one page) | **Column-oriented** (each column stored contiguously) |
| Indexing | Many selective B-trees | Few; zone maps / min-max / sort order instead |
| Schema | Normalised (3NF) | **Star schema** — denormalised dimensions |
| Typical engines | PostgreSQL, MySQL, Aurora | Snowflake, BigQuery, Redshift, ClickHouse, DuckDB |

| Fact | Detail |
|---|---|
| Why columnar wins analytics | Reads only the columns referenced (**10–50× less I/O**), and homogeneous columns compress **5–20×** (dictionary, run-length, delta, bit-packing). Combined: often **100×** less bytes read. |
| Why columnar loses OLTP | Fetching one whole row means touching every column chunk; a single-row update rewrites or marks a block in many column files. |
| Core operational rule | **Never run analytics on the OLTP primary.** A single 90-second report scan evicts the buffer pool and raises transaction p99 for everyone. Route to a replica (L31) at minimum, a warehouse properly. |
| **HTAP** | Hybrid systems (TiDB, SingleStore, Aurora zero-ETL) keep a row store for writes and a columnar replica for scans. Real, but the columnar side still lags by seconds. |

### Topic 19 — Data warehouses, data lakes, lakehouses (L58)

| | Data warehouse | Data lake | Lakehouse |
|---|---|---|---|
| Stores | Modelled, cleaned, typed tables | Raw files of any format in object storage | Open columnar files + transactional metadata layer |
| Schema | **Schema-on-write** | **Schema-on-read** | Schema-on-write, enforced by the table format |
| Load pattern | **ETL** (transform before load) | **ELT** (load raw, transform later) | ELT with ACID tables |
| Formats | Proprietary internal | Parquet, ORC, Avro, JSON, CSV | **Parquet** + Delta Lake / **Apache Iceberg** / Hudi |
| Cost | Highest per TB | Lowest per TB (~**$0.023/GB-month** S3 Standard, typical) | Lake cost + metadata |
| Risk | Rigid; schema change is a project | **Data swamp** — unqueryable without catalogue + governance | Newer tooling, more moving parts |

| Concept | Definition |
|---|---|
| **Star schema** | One central **fact table** (one row per event: `order_item`, with measures and dimension FKs) surrounded by **dimension tables** (`dim_book`, `dim_customer`, `dim_date`). Snowflake schema = dimensions themselves normalised. |
| **Grain** | The precise meaning of one fact row. Declaring the grain first is the single most important modelling step; mixed grain causes double-counted revenue. |
| **Slowly changing dimension** | Type 1 = overwrite (no history). Type 2 = new row with `valid_from`/`valid_to` + surrogate key (full history). Type 3 = previous-value column. Type 2 is the default for anything audited. |
| **Predicate pushdown** | The engine passes `WHERE` filters down to the file reader, which skips row groups using Parquet footer min/max statistics. This is why Parquet + partitioned layout beats CSV by orders of magnitude. |
| Parquet physical facts | Columnar, per-column encoding + compression, organised into **row groups** (typically **128 MB**) each with per-column statistics; self-describing schema in the footer. |

### Topic 20 — CAP theorem (L59)

| Fact | Detail |
|---|---|
| Precise statement | In an asynchronous network, a distributed data store cannot simultaneously guarantee **Consistency** (linearizability), **Availability** (every request to a non-failing node returns a non-error response) and **Partition tolerance** (the system continues despite arbitrary message loss). |
| What "consistency" means here | **Linearizability** — the system behaves as a single copy with real-time ordering. This is *not* the C in ACID. |
| What "availability" means here | *Every* node answers. A system where 99 % of nodes answer is not "A" in CAP terms, though it is excellent in practice. |
| The correct reading | Partitions are a fact of networks, not a design choice. So the theorem says: **when a partition occurs, choose CP (refuse writes / return errors) or AP (accept writes, reconcile later).** |
| CP example | A system requiring quorum: minority side rejects writes. ZooKeeper, etcd, HBase, MongoDB with `w:majority`. |
| AP example | Cassandra/Riak/DynamoDB with low consistency levels: both sides accept writes, conflicts resolved by last-write-wins, vector clocks or CRDTs. |
| Common misuse | "We chose AP so we gave up consistency." No — you gave up linearizability **during partitions**. Normal operation can still be strongly consistent. |
| BookNook application | Payment capture → CP (better to fail than double-charge). Shopping cart → AP (better to accept the item and merge later). |

### Topic 21 — PACELC (L60)

| Fact | Detail |
|---|---|
| Statement | **If P**artition, choose **A**vailability or **C**onsistency; **E**lse (normal operation), choose **L**atency or **C**onsistency. |
| Why it matters more than CAP | Partitions are rare — maybe hours per year. The **E** branch describes the trade you make **every millisecond of every day**. |
| Classification | PostgreSQL w/ sync replication: **PC/EC**. MySQL async replication: PC/EL. Cassandra default: **PA/EL**. DynamoDB: PA/EL (eventually consistent reads) or PC/EC (strongly consistent reads, **2× the RCU cost**). MongoDB w:majority, readConcern majority: PC/EC. Spanner: **PC/EC** (and pays for it with ~**5–10 ms** commit latency from TrueTime waits). |
| The latency arithmetic | A quorum read across 3 AZs costs ~**1–2 ms** extra; across regions (e.g. London↔Virginia, RTT ~**75 ms**) a cross-region quorum costs ~**40–80 ms** per operation. This is the whole reason multi-region strong consistency is expensive. |
| Practical rule | Choose per-operation, not per-system. Same database, different read concern for "order confirmation" (EC) vs "recommended books" (EL). |

### Topic 22 — Consistency models (L61)

| Model | Guarantee | Cost | BookNook use |
|---|---|---|---|
| **Strong / linearizable** | Every read sees the most recent committed write, in real-time order | Quorum or leader read; cross-region = full RTT | Stock decrement at checkout, payment capture |
| **Sequential** | All nodes see the same order of operations, not necessarily real time | Cheaper than linearizable | Replicated log consumers |
| **Causal** | Operations with a happens-before relation are seen in that order; concurrent ones may differ per node | Track causality (vector clocks / dependency metadata) | Review + reply-to-review ordering |
| **Read-your-writes** | A client always sees its own prior writes | Sticky routing to primary, or read from the replica once its LSN ≥ your write LSN | User edits their delivery address and reloads the page |
| **Monotonic reads** | A client never sees time go backwards | Pin the client to one replica (session affinity) | Order status must not flip "Shipped" → "Processing" |
| **Monotonic writes** | A client's writes are applied in the order issued | Per-client ordering key | Sequential profile edits |
| **Eventual** | If writes stop, all replicas converge | Cheapest | Review counts, "customers also bought", search index |

| Fact | Detail |
|---|---|
| Hierarchy | Linearizable ⊃ sequential ⊃ causal ⊃ {read-your-writes, monotonic reads, monotonic writes} ⊃ eventual. |
| Session guarantees | Read-your-writes + monotonic reads + monotonic writes + writes-follow-reads = **session consistency** — the practical sweet spot; users almost never notice they are on an eventually consistent store. |
| Replication lag reality | Same-AZ async: typically **1–10 ms**. Cross-region async: **50–500 ms**, with multi-second spikes under write bursts or long transactions. Always measure the p99, not the mean. |
| Convergence mechanisms | Read repair, **anti-entropy** (Merkle-tree comparison), hinted handoff, **CRDTs** (mathematically conflict-free merges, e.g. G-Counter, OR-Set), last-write-wins (lossy — requires synchronised clocks and silently drops data). |

### Topic 23 — Object, block, file storage (L62)

| | Block | File | Object |
|---|---|---|---|
| Unit | Fixed-size blocks, no inherent meaning | Files in a directory hierarchy | Immutable-ish objects with a flat key + metadata |
| Interface | SCSI/NVMe/iSCSI — a raw device | POSIX (NFS/SMB) — open, seek, write, lock | HTTP REST — `PUT`, `GET`, `DELETE`, `LIST` |
| Mutability | Any block in place | In-place writes, partial writes, append | **Whole-object replace**; no partial write |
| Latency (typical) | **0.1–1 ms** (EBS gp3) | **1–5 ms** | **20–100 ms** first byte (S3) |
| Throughput | Per-volume cap (gp3: **1,000 MB/s**, 16,000 IOPS) | Scales with the NAS | Effectively unlimited with parallel requests |
| Capacity | Per-volume (gp3 max **16 TiB**) | Per-filesystem | **Effectively unlimited**; object max 5 TB on S3 |
| Sharing | One attachment (mostly) | Many clients, POSIX locking | Many clients, no locking |
| Cost (typical) | ~**$0.08/GB-month** | ~**$0.30/GB-month** | ~**$0.023/GB-month**, ~$0.004 for infrequent/glacier tiers |
| Use for | Databases, boot volumes | Legacy apps, shared home dirs, render farms | Images, backups, logs, data lake, static assets |

| Fact | Detail |
|---|---|
| Why databases demand block | They need in-place page writes, `fsync` ordering guarantees and sub-millisecond random I/O — none of which object storage provides. |
| Durability figures | S3 advertises **11 nines (99.999999999 %)** annual object durability via erasure coding across AZs; EBS gp3 advertises ~**99.8–99.9 %** annual volume durability — roughly **0.1–0.2 % annual failure rate**, hence snapshots are mandatory. |
| **Erasure coding** | Split an object into *k* data fragments + *m* parity fragments; any *k* of *k+m* reconstruct it. Reed-Solomon(10,4) stores **1.4×** the data and tolerates 4 losses, versus 3× replication tolerating 2. |
| Consistency | S3 has provided **strong read-after-write consistency** for all operations since Dec 2020; the old "eventually consistent LIST" caveats are obsolete. |
| BookNook mapping | Book cover images and PDFs → object. PostgreSQL data files → block. A shared NFS mount for a legacy royalty-report tool → file. |

### Topic 24 — Retention, archival, backups, disaster recovery (L63)

| Term | Precise definition |
|---|---|
| **Retention policy** | How long each data class is kept, and why (legal, business, cost). |
| **Archival** | Moving cold data to cheaper, slower storage while remaining retrievable. |
| **Backup** | A copy that can restore the system after loss or corruption. |
| **RPO** (Recovery Point Objective) | Maximum acceptable **data loss**, measured in time. Determined by backup/replication frequency. |
| **RTO** (Recovery Time Objective) | Maximum acceptable **downtime**. Determined by restore speed and automation. |
| **PITR** (point-in-time recovery) | Restore a base backup, then replay WAL to an exact timestamp or transaction ID. |

| Fact | Detail |
|---|---|
| Backup types | **Full** (everything), **incremental** (changes since last backup of any kind), **differential** (changes since last full). Restore chain length is the operational difference: differential needs 2 files, incremental needs the whole chain. |
| PostgreSQL mechanics | `pg_basebackup` or `pgBackRest` for the base; continuous WAL archiving (`archive_command`); restore with `recovery_target_time`. **WAL archive interval sets RPO** — archiving every 60 s gives RPO ≈ 60 s. |
| **3-2-1 rule** | **3** copies, on **2** different media/systems, with **1** off-site (and ideally **1** immutable/air-gapped against ransomware). |
| The only test that counts | **A backup you have never restored is not a backup.** Schedule restore drills quarterly and record the measured RTO. |
| Replication is not backup | Replication faithfully copies `DROP TABLE orders;` to every replica in milliseconds. Backups protect against *logical* corruption; replication protects against *hardware* loss. |
| Typical RPO/RTO tiers | Tier 0 (payments ledger): RPO **0** (synchronous replication), RTO **<5 min**. Tier 1 (orders, users): RPO **1 min**, RTO **1 h**. Tier 2 (catalogue, derived search index): RPO **1 h**, RTO **4 h** (rebuildable). Tier 3 (analytics, logs): RPO **24 h**, RTO **24 h**. |
| Restore-time arithmetic | Restoring 2 TB from S3 at a sustained **200 MB/s** takes **~2.8 hours** *before* WAL replay. If your stated RTO is 1 hour, the number is a fiction — you need warm standbys or snapshot-based volume restore. |
| Cost arithmetic | 2 TB of daily fulls kept 30 days = 60 TB ≈ **$1,380/month** at $0.023/GB. Incrementals + weekly fulls typically cut this **5–10×**. |
| Legal retention | Financial records are commonly retained **7 years**; GDPR/erasure requests require *deletion* of personal data — including **from backups**, usually satisfied by documented backup expiry plus crypto-shredding. |
| **Crypto-shredding** | Encrypt per-user data with a per-user key; delete the key to render all copies, including archived backups, unrecoverable. |

---

## 2. Glossary

| Term | Definition |
|---|---|
| **ACID** | Atomicity, Consistency, Isolation, Durability — the four guarantees a classical transactional database makes about a transaction. |
| **Aggregation pipeline** | MongoDB's staged query framework (`$match`, `$group`, `$sort`, `$lookup`, `$project`) where each stage consumes the previous stage's output. |
| **Anti-entropy** | A background process that compares replicas (typically via Merkle trees) and repairs divergence, independent of read traffic. |
| **Atomicity** | The guarantee that all statements in a transaction take effect or none do; more accurately described as abortability. |
| **B+tree** | A balanced tree index in which all keys live in sorted, horizontally linked leaf pages, giving O(log N) point lookups and O(1)-per-row range scans. |
| **BASE** | Basically Available, Soft state, Eventual consistency — the availability-first design posture contrasted with ACID. |
| **BCNF** | Boyce-Codd Normal Form: every functional dependency's determinant is a superkey; strictly stronger than 3NF. |
| **Bitmap heap scan** | A PostgreSQL plan node that first builds a bitmap of qualifying pages from one or more indexes, then reads those pages in physical order to avoid random I/O. |
| **Block storage** | Storage exposing a raw array of fixed-size blocks over SCSI/NVMe, with in-place writes and sub-millisecond latency; the only storage class suitable for database data files. |
| **Bloom filter** | A probabilistic set-membership structure with false positives but no false negatives, used by LSM engines to skip SSTables that cannot contain a key. |
| **BM25** | The default modern relevance function: TF-IDF plus term-frequency saturation (`k₁`≈1.2) and document-length normalisation (`b`≈0.75). |
| **BRIN** | Block Range INdex — stores min/max per range of blocks; tiny and effective only when table physical order correlates with the indexed column. |
| **Buffer pool** | The database's in-memory cache of data and index pages; its hit ratio, not disk speed, usually determines query latency. |
| **CAP theorem** | Under network partition, a distributed store can provide linearizable consistency or total availability, not both. |
| **Cardinality** | The number of distinct values in a column, or the estimated number of rows a plan node produces. |
| **Causal consistency** | Operations related by happens-before are observed in that order by all nodes; concurrent operations may be observed in different orders. |
| **Clustered index** | An index whose leaves contain the full rows, so the index *is* the table's physical storage; at most one per table. |
| **Column-oriented storage** | Layout storing each column's values contiguously, enabling column pruning and high compression ratios for analytical scans. |
| **Compaction** | The LSM-tree background process that merges SSTables, discards superseded versions and purges expired tombstones. |
| **Composite index** | An index over several columns in a defined order, usable only from the leftmost column rightwards. |
| **Consistency (ACID)** | The transaction leaves the database satisfying all declared constraints; an application-defined property that the DB enforces only insofar as you declared it. |
| **Consistency (CAP)** | Linearizability — every read observes the most recently committed write in real-time order, as if there were one copy. |
| **Consistent hashing** | Mapping keys and nodes onto a hash ring so adding or removing a node relocates only ~1/N of keys instead of nearly all. |
| **Covering index** | An index containing every column a query needs, enabling an index-only scan with no heap access. |
| **CRDT** | Conflict-free Replicated Data Type — a structure whose merge operation is commutative, associative and idempotent, so replicas converge without coordination. |
| **Data lake** | Raw files of arbitrary format in cheap object storage, interpreted schema-on-read at query time. |
| **Data warehouse** | A modelled, cleaned, typed analytical store loaded schema-on-write, optimised for aggregate queries. |
| **Deadlock** | A cycle of transactions each waiting on a lock held by another; resolved by detection and aborting a victim (PostgreSQL SQLSTATE 40P01). |
| **Denormalisation** | Deliberately duplicating data to remove joins or precompute aggregates, accepting the obligation to keep copies in sync. |
| **Dimension table** | A warehouse table of descriptive attributes (customer, book, date) joined to fact rows by surrogate key. |
| **Dirty read** | Reading a row version written by a transaction that has not yet committed. |
| **Document database** | A store of self-describing, nested documents (JSON/BSON) where the document is both the storage unit and the atomicity unit. |
| **Durability** | The guarantee that a committed transaction survives a crash, achieved by WAL `fsync` and/or replication. |
| **Erasure coding** | Encoding data into k data plus m parity fragments so any k reconstruct the whole, giving replication-like durability at ~1.2–1.5× storage instead of 3×. |
| **ELT** | Extract, Load, Transform — load raw data first and transform inside the warehouse/lakehouse, exploiting elastic compute. |
| **ETL** | Extract, Transform, Load — transform before loading, so only modelled, validated data lands in the warehouse. |
| **Eventual consistency** | If writes stop, all replicas eventually converge to the same value; no bound on when, and no ordering guarantee before then. |
| **Fact table** | The central warehouse table with one row per business event, holding numeric measures and foreign keys to dimensions. |
| **Foreign key** | Columns constrained to match a primary or unique key in another table, enforcing referential integrity. |
| **Full table scan** | Reading every row of a table; the correct plan when the predicate is unselective or no usable index exists. |
| **Functional dependency** | X → Y: each value of X determines exactly one value of Y; the basis of all normal forms. |
| **GIN index** | Generalised Inverted Index — maps each element of a composite value (array item, `jsonb` key, text lexeme) to the rows containing it. |
| **Graph database** | A store of nodes and typed edges using index-free adjacency, making each traversal hop O(1) regardless of graph size. |
| **Hash join** | Join algorithm that builds an in-memory hash table on the smaller input and probes it with the larger; O(N+M) for equality joins. |
| **Heap** | Unordered table storage where rows sit wherever there is space; PostgreSQL's only table layout, addressed by physical tuple ID. |
| **Hot partition** | A shard or partition receiving disproportionate traffic because of skewed keys, capping cluster throughput at one node's capacity. |
| **HTAP** | Hybrid Transactional/Analytical Processing — one system serving OLTP writes and OLAP scans, typically via a lagging columnar replica. |
| **Idempotency key** | A client-supplied unique token stored with the result so a retried request returns the original outcome instead of duplicating it. |
| **Index-only scan** | A plan node answering a query entirely from an index, with no heap fetch (PostgreSQL additionally requires the visibility map to be current). |
| **Inverted index** | Mapping from term to the list of documents (and positions) containing it; the core structure of every search engine. |
| **Isolation level** | The contractual degree to which concurrent transactions are shielded from one another's intermediate states. |
| **Key-value store** | A database exposing only `GET`/`PUT`/`DELETE` by primary key, with no server-side querying by value. |
| **Lakehouse** | A data lake plus a transactional table format (Delta Lake, Apache Iceberg, Hudi) that adds ACID tables, schema enforcement and time travel over Parquet files. |
| **Linearizability** | The strongest single-object consistency model: operations appear to take effect instantaneously at some point between invocation and response, in real-time order. |
| **LSM-tree** | Log-Structured Merge tree — buffers writes in memory, flushes immutable sorted files, and merges them by compaction; optimised for write throughput. |
| **Lost update** | Two concurrent read-modify-write cycles on the same row where one update silently overwrites the other. |
| **Materialised view** | A physically stored, database-maintained result of a query, refreshed on demand or incrementally. |
| **Merge join** | Join algorithm that advances in lockstep over two sorted inputs; O(N+M) when both are already ordered. |
| **Monotonic reads** | A client never observes a value older than one it has already seen; prevents time appearing to run backwards. |
| **MVCC** | Multi-Version Concurrency Control — writers create new row versions rather than overwriting, so readers never block writers and vice versa. |
| **Nested loop join** | For each outer row, probe the inner relation; efficient only when the outer side is small or the inner side is indexed. |
| **Non-repeatable read** | Re-reading the same row within one transaction returns a different committed value. |
| **Normalisation** | Decomposing tables so every non-key fact depends on the key, the whole key and nothing but the key, eliminating update/insert/delete anomalies. |
| **Object storage** | Flat-namespace, HTTP-addressed storage of immutable-replace objects with metadata; effectively unlimited, cheapest per GB, highest latency. |
| **OLAP** | Online Analytical Processing — few long queries scanning many rows and few columns, tolerating seconds-to-minutes latency. |
| **OLTP** | Online Transaction Processing — many short transactions touching few rows, requiring tens-of-milliseconds latency. |
| **Optimistic concurrency** | Proceed without locks and validate at commit (version column, CAS or SSI), aborting and retrying on conflict. |
| **PACELC** | If Partition, trade Availability against Consistency; Else, trade Latency against Consistency — extends CAP to normal operation. |
| **Parquet** | Open columnar file format with per-column encoding/compression, row groups (typically 128 MB) and footer statistics enabling predicate pushdown. |
| **Partial index** | An index built only over rows matching a `WHERE` predicate, dramatically smaller when the predicate is selective. |
| **Partition key** | The value hashed or ranged to decide which partition/shard stores a row; determines data distribution and which queries can be served efficiently. |
| **Pessimistic concurrency** | Acquire locks before accessing data so conflicts block rather than abort; safest under high contention. |
| **Phantom read** | Re-executing a predicate query within one transaction returns a different *set* of rows because of concurrent inserts or deletes. |
| **Point-in-time recovery** | Restoring a base backup and replaying the write-ahead log to a chosen timestamp or transaction ID. |
| **Predicate pushdown** | Passing filter conditions down to the storage/file layer so non-matching blocks or row groups are never read. |
| **Primary key** | The chosen minimal, unique, non-null identifier of a row. |
| **Query planner** | The optimiser component that enumerates execution plans and selects the lowest estimated cost using table statistics. |
| **Quorum** | A subset of replicas that must acknowledge an operation; with R+W > RF, reads and writes overlap on at least one up-to-date replica. |
| **Read repair** | Repairing a stale replica during a read, when the coordinator notices divergence among the responses. |
| **Read-your-writes** | A session guarantee that a client always observes its own earlier writes, even on a replicated, eventually consistent store. |
| **Referential integrity** | The invariant that every foreign-key value references an existing row. |
| **Repeatable read** | An isolation level guaranteeing that rows read once in a transaction do not change if read again; in PostgreSQL implemented as snapshot isolation. |
| **Replication lag** | The delay between a write committing on the primary and being visible on a replica; the direct cause of most read-your-writes violations. |
| **RPO** | Recovery Point Objective — the maximum tolerable amount of data loss, expressed in time. |
| **RTO** | Recovery Time Objective — the maximum tolerable duration of outage before service is restored. |
| **Secondary index** | An index separate from the table's primary storage, mapping column values to row locations (or to the primary key in a clustered-index engine). |
| **Selectivity** | The fraction of rows a predicate returns; low selectivity (few rows) favours index access, high selectivity favours sequential scan. |
| **Sequential scan** | Reading a table's pages in physical order; cheap per page and highly prefetchable, hence preferred above roughly 5–10 % row selectivity. |
| **Serializability** | The guarantee that the outcome of concurrent transactions equals *some* serial execution of them. |
| **Sharding** | Horizontally partitioning data across independent nodes, each owning a disjoint key range or hash range. |
| **Slowly changing dimension** | A dimension-modelling technique for attribute change: Type 1 overwrites, Type 2 adds a versioned row with validity dates, Type 3 keeps a previous-value column. |
| **Snapshot isolation** | Each transaction reads a consistent snapshot taken at start; prevents dirty, non-repeatable and phantom reads but permits write skew. |
| **SSTable** | Sorted String Table — an immutable, sorted, on-disk file of key-value pairs produced by an LSM flush or compaction. |
| **Star schema** | A dimensional model with one fact table joined directly to denormalised dimension tables. |
| **Stemming** | Reducing inflected words to a common root ("running", "ran" → "run") so query and document terms match. |
| **Strong consistency** | Informally, reads always reflect the latest committed write; formally, linearizability for single objects or strict serializability for transactions. |
| **Surrogate key** | A business-meaningless identifier (sequence, UUID) chosen so the key never changes when business facts change. |
| **TF-IDF** | Term Frequency × Inverse Document Frequency — a term scores highly when frequent in a document and rare in the corpus. |
| **Tombstone** | A marker recording a deletion in an append-only store; the row is only truly removed after compaction past the grace period. |
| **Transaction** | A unit of work treated as a single logical operation with respect to atomicity, isolation and durability. |
| **Two-phase locking (2PL)** | A protocol with a growing phase (acquire locks only) and a shrinking phase (release only); guarantees serializability at the cost of blocking and deadlocks. |
| **UUID v7** | A time-ordered UUID (RFC 9562) whose leading 48 bits are a Unix millisecond timestamp, giving UUID uniqueness with sequential-insert locality. |
| **Vacuum** | PostgreSQL's reclamation of dead row versions created by MVCC; also refreshes the visibility map that index-only scans depend on. |
| **Vector clock** | A per-replica counter vector attached to a value, allowing a system to distinguish causally ordered updates from genuinely concurrent ones. |
| **Wide-column store** | A partitioned store keyed by `(partition key, clustering key)` where each partition holds sorted rows with flexible column sets. |
| **Write amplification** | The ratio of bytes actually written to storage versus bytes logically written by the application; inflated by WAL, index maintenance, page splits and LSM compaction. |
| **Write-ahead log (WAL)** | A durable sequential log of changes written and flushed before the corresponding data pages, providing atomicity, durability and the basis of replication and PITR. |
| **Write skew** | Two transactions each read an overlapping set, each find a constraint satisfied, and each write *different* rows such that the combined result violates the constraint; prevented only by serializable isolation. |
| **Zone map** | Per-block min/max (and sometimes null-count) metadata in a columnar store used to skip blocks that cannot satisfy a predicate. |

---

## 3. Twenty quiz questions

Answer in your own words. Calculation questions expect a number *and* the working.

1. PostgreSQL's REPEATABLE READ prevents phantom reads, which the SQL standard permits at that level. Name the anomaly it still permits, define it precisely, and explain why snapshot isolation structurally cannot prevent it.
2. BookNook's `orders` table has 400 M rows. A query filters `WHERE status = 'pending'`, and 0.3 % of rows are pending. Compute the approximate selectivity, state whether the planner should choose an index scan or a sequential scan, and name the index type that would shrink the index by roughly 300×.
3. A B+tree index has fanout 400. How many levels are needed for 250 million keys, and how many of those levels are realistically served from the buffer pool on a warm server?
4. Explain the exact difference between the C in ACID and the C in CAP. Give one concrete BookNook operation that needs the first but not the second.
5. BookNook runs 3 replicas across 3 AZs with RF=3. The team sets W=1 and R=1 for speed. State which consistency guarantees are lost, and give the minimum `(R, W)` pair that restores read-your-writes within the cluster.
6. A 2 TB PostgreSQL base backup is stored in S3. Restore throughput is sustained at 180 MB/s and WAL replay adds 25 % to the restore duration. Calculate the achievable RTO. If the stated RTO is 60 minutes, state precisely what architectural change is required.
7. Two sessions each run `SELECT stock FROM books WHERE book_id=7` (both see 10), then `UPDATE books SET stock = 9 WHERE book_id=7`. Name the anomaly, and give three distinct fixes, one of which must not involve changing the isolation level.
8. A composite index exists on `(user_id, created_at, status)`. For each of these predicates say whether the index is usable and why: (a) `WHERE user_id=? AND created_at > ?`; (b) `WHERE created_at > ?`; (c) `WHERE user_id=? ORDER BY created_at DESC`; (d) `WHERE user_id=? AND status='paid'`.
9. BookNook's catalogue is 8 M books averaging 3 KB each in a row store, and an analytics query reads 3 of 60 columns. Estimate the bytes read by the row store and by a columnar store at 8× compression, and state the approximate ratio.
10. Classify each of the following in PACELC notation and justify in one line: PostgreSQL with synchronous replication; Cassandra with `CL=ONE`; DynamoDB with eventually consistent reads; Google Spanner.
11. Define write amplification, then list four distinct sources of it in a PostgreSQL OLTP table that has five secondary indexes and a random-UUID primary key.
12. A search index returns results 1.2 seconds after a book is edited. Explain the mechanism responsible in Elasticsearch, give the default value of the relevant setting, and state whether this makes the search index unsuitable as a system of record.
13. Explain why `random_page_cost = 4.0` is the wrong default on NVMe storage, what value is typically correct, and which specific plan choices change when you set it.
14. Cassandra is chosen for BookNook's order-event history, partitioned by `user_id`. One corporate account places 4,000 orders per day. After two years, estimate the partition row count and state which Cassandra sizing guideline is violated and how to fix it without losing query locality.
15. Describe exactly what happens, step by step, when PostgreSQL commits a transaction: list the WAL write, the `fsync`, the replication acknowledgement (if synchronous) and the data-page write, in the order they occur.
16. Give the precise definition of RPO and RTO, then determine the RPO of a system that takes a full backup nightly at 02:00 and archives WAL segments every 5 minutes, assuming the WAL archive is intact.
17. BookNook wants a "recommended by friends" feature requiring 3-hop traversal across 40 M users. Explain why a relational recursive CTE degrades faster than a graph traversal, naming the specific property of graph databases responsible.
18. A denormalised `books.review_count` column drifts from the true `COUNT(*)` of reviews. List three mechanisms that would have prevented the drift, ranked by strength of guarantee.
19. Compute the monthly storage cost of retaining 90 days of 1.5 TB daily full backups at $0.023/GB-month, then recompute assuming weekly fulls plus daily incrementals averaging 4 % of the full size.
20. BookNook must support GDPR erasure requests, but backups are immutable for 35 days. Explain the technique that satisfies erasure without rewriting backups, and name the exact artefact that must be destroyed.

*Reply with your answers and they will be assessed question by question — marked correct, partially correct or incorrect, with the reasoning and the corrected version where needed. Answers are deliberately not shown here.*

---

## 4. Five practical exercises

All five run locally. Assume PostgreSQL 15+ and DuckDB 0.10+; install via your package manager or Docker (`docker run -e POSTGRES_PASSWORD=pw -p 5432:5432 postgres:16`).

### Exercise A — BookNook schema and turning a sequential scan into an index scan

**Goal:** observe, with real timings, the exact point at which an index changes the plan.

**Steps**

1. Create the schema:

```sql
CREATE TABLE books (
  book_id    bigserial PRIMARY KEY,
  isbn13     char(13) UNIQUE NOT NULL,
  title      text NOT NULL,
  genre      text NOT NULL,
  price_cents integer NOT NULL CHECK (price_cents >= 0),
  published  date NOT NULL
);
CREATE TABLE customers (
  customer_id bigserial PRIMARY KEY,
  email       citext UNIQUE NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE orders (
  order_id    bigserial PRIMARY KEY,
  customer_id bigint NOT NULL REFERENCES customers,
  status      text NOT NULL CHECK (status IN ('pending','paid','shipped','cancelled')),
  created_at  timestamptz NOT NULL DEFAULT now(),
  total_cents integer NOT NULL
);
CREATE TABLE order_items (
  order_id bigint NOT NULL REFERENCES orders ON DELETE CASCADE,
  line_no  smallint NOT NULL,
  book_id  bigint NOT NULL REFERENCES books,
  qty      smallint NOT NULL CHECK (qty > 0),
  PRIMARY KEY (order_id, line_no)
);
```

2. Load data: 200,000 books, 100,000 customers, 5,000,000 orders, ~12,000,000 order items. Use `generate_series` so it takes seconds, not hours:

```sql
INSERT INTO books (isbn13, title, genre, price_cents, published)
SELECT lpad(g::text,13,'0'), 'Book '||g,
       (ARRAY['scifi','crime','history','romance','tech'])[1+g%5],
       500 + (g % 4000),
       DATE '1990-01-01' + (g % 12000)
FROM generate_series(1,200000) g;
```
Write the equivalent inserts for `customers`, `orders` and `order_items`. Then run `ANALYZE;` — without it the planner is working from empty statistics and every later measurement is meaningless.

3. Baseline the query with **no** supporting index:

```sql
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT order_id, total_cents FROM orders
WHERE customer_id = 4242 AND status = 'paid'
ORDER BY created_at DESC LIMIT 20;
```
Record: node type, `actual time`, `rows`, `Buffers: shared hit/read`. Run it three times and use the third (warm cache).

4. Add the index and re-measure:

```sql
CREATE INDEX idx_orders_cust_created ON orders (customer_id, created_at DESC) WHERE status = 'paid';
ANALYZE orders;
```

5. Now add a covering variant and compare again:

```sql
CREATE INDEX idx_orders_cover ON orders (customer_id, created_at DESC) INCLUDE (total_cents) WHERE status='paid';
VACUUM orders;   -- refresh the visibility map so index-only scans can work
```

6. Measure the write cost you just bought: time `INSERT`ing 100,000 new orders before and after the indexes exist (drop and recreate to compare).

**Success criterion:** you can produce a three-row table showing `Seq Scan` → `Index Scan` → `Index Only Scan` with measured `actual time` and `shared read` counts for each, where the index-only scan reports `Heap Fetches: 0`, and you can state the measured percentage write slowdown caused by the two indexes.

### Exercise B — Reproduce a lost update, then fix it three ways

**Goal:** see the anomaly happen, then prove each fix prevents it.

**Steps**

1. Open two terminals, both `psql` on the same database. Set stock: `UPDATE books SET price_cents = price_cents; ALTER TABLE books ADD COLUMN stock int NOT NULL DEFAULT 10;`
2. **Reproduce.** In both sessions:
   - S1: `BEGIN; SELECT stock FROM books WHERE book_id=1;`  → 10
   - S2: `BEGIN; SELECT stock FROM books WHERE book_id=1;`  → 10
   - S1: `UPDATE books SET stock = 9 WHERE book_id=1; COMMIT;`
   - S2: `UPDATE books SET stock = 9 WHERE book_id=1; COMMIT;`
   - `SELECT stock FROM books WHERE book_id=1;` → **9**, though two units were sold.
3. **Fix 1 — atomic write.** Replace the read-modify-write with `UPDATE books SET stock = stock - 1 WHERE book_id=1 AND stock > 0;` in both sessions. Check `stock` is 8 and that the second `UPDATE` blocked until the first committed.
4. **Fix 2 — pessimistic lock.** `BEGIN; SELECT stock FROM books WHERE book_id=1 FOR UPDATE;` in both sessions, then compute and update in the application. Observe S2 blocking at the `SELECT`.
5. **Fix 3 — optimistic version column.** Add `version int NOT NULL DEFAULT 0`. Each session reads `(stock, version)` then runs `UPDATE books SET stock=?, version=version+1 WHERE book_id=1 AND version=?`. Confirm the loser gets `UPDATE 0` and implement a retry.
6. Bonus: repeat step 2 with `BEGIN ISOLATION LEVEL REPEATABLE READ;` and observe the `ERROR: could not serialize access due to concurrent update` (SQLSTATE 40001) instead of silent loss.

**Success criterion:** you have a written log showing stock = 9 in the broken run and stock = 8 in all four fixed runs, plus a one-sentence note on each fix's failure mode (blocking, retry storms, or application-code discipline).

### Exercise C — Reproduce write skew, then defeat it with SERIALIZABLE

**Goal:** demonstrate the one anomaly snapshot isolation cannot stop.

**Steps**

1. Model a refund constraint: *total refunds for an order must never exceed the order total.*

```sql
CREATE TABLE refunds (refund_id bigserial PRIMARY KEY, order_id bigint NOT NULL, amount_cents int NOT NULL);
INSERT INTO orders (customer_id, status, total_cents) VALUES (1,'paid',10000);  -- £100 order
```

2. **Reproduce under REPEATABLE READ.** In two sessions, substituting the real `order_id`:
   - Both: `BEGIN ISOLATION LEVEL REPEATABLE READ;`
   - Both: `SELECT coalesce(sum(amount_cents),0) FROM refunds WHERE order_id = 1;` → both see 0
   - S1: `INSERT INTO refunds (order_id, amount_cents) VALUES (1, 6000);`
   - S2: `INSERT INTO refunds (order_id, amount_cents) VALUES (1, 6000);`
   - Both: `COMMIT;` — **both succeed.** Total refunded is £120 on a £100 order.
3. Note precisely why no conflict was detected: the two transactions wrote **different rows**, so there is no first-updater-wins collision; the shared premise was only *read*.
4. **Fix with SERIALIZABLE.** Repeat identically but with `BEGIN ISOLATION LEVEL SERIALIZABLE;`. The second `COMMIT` must fail with `ERROR: could not serialize access due to read/write dependencies among transactions` (SQLSTATE **40001**), with a hint suggesting retry.
5. Implement a retry loop (max 5 attempts, exponential backoff with jitter) and confirm the retried transaction correctly sees £60 already refunded and rejects the second £60.
6. Compare the two alternatives: (a) `SELECT … FOR UPDATE` on the parent `orders` row — materialise the conflict onto a real row; (b) a `CHECK`-style exclusion enforced by a trigger or a `sum` column updated in the same transaction. Note which one scales better under contention.

**Success criterion:** you can show the exact error text and SQLSTATE 40001 from the SERIALIZABLE run, the £120 over-refund from the REPEATABLE READ run, and a working retry that leaves the invariant intact.

### Exercise D — Row store versus Parquet with DuckDB

**Goal:** quantify column pruning and compression on a real analytical query.

**Steps**

1. Generate a wide fact dataset of ~20 M rows and ~25 columns (order items with timestamps, genre, price, quantity, region, channel, etc.). Export it as CSV.
2. Load into PostgreSQL (`COPY`) and record the table size: `SELECT pg_size_pretty(pg_total_relation_size('order_items_wide'));`
3. In DuckDB, convert to Parquet with compression:

```sql
COPY (SELECT * FROM read_csv_auto('order_items_wide.csv'))
TO 'order_items_wide.parquet' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 122880);
```

4. Compare file sizes on disk: CSV vs PostgreSQL heap+TOAST vs Parquet. Expect roughly **5–20×** compression versus CSV.
5. Run the same analytical query on both, three times each, taking the warm timing:

```sql
SELECT genre, date_trunc('month', ordered_at) AS m, sum(qty*price_cents)/100.0 AS revenue
FROM <source>
WHERE ordered_at >= DATE '2024-01-01'
GROUP BY 1,2 ORDER BY 3 DESC;
```
   In PostgreSQL use `EXPLAIN (ANALYZE, BUFFERS)`; in DuckDB use `EXPLAIN ANALYZE` and `PRAGMA enable_profiling`.
6. Demonstrate **predicate pushdown**: partition the Parquet output by year/month (`PARTITION_BY (year, month)`), rerun the filtered query and compare rows scanned.

**Success criterion:** a four-column table — engine, file size, warm query time, bytes/rows scanned — showing Parquet+DuckDB materially faster on the scan and smaller on disk, plus a written explanation of *which* two mechanisms (column pruning and row-group statistics skipping) produced the gap, with numbers attributed to each.

### Exercise E — Base backup and point-in-time recovery

**Goal:** measure a real RTO instead of asserting one.

**Steps**

1. Enable archiving on a throwaway cluster:

```
wal_level = replica
archive_mode = on
archive_command = 'test ! -f /backups/wal/%f && cp %p /backups/wal/%f'
archive_timeout = 60
```
Restart and confirm segments appear in `/backups/wal`.

2. Take the base backup and record the wall-clock time and size:
```
pg_basebackup -D /backups/base -Ft -z -P -X stream
```
3. Generate activity, then note the exact timestamp: `SELECT now();` — call it **T₀**. Wait ~60 seconds with ongoing writes.
4. Run the destructive statement deliberately: `DELETE FROM order_items WHERE order_id % 7 = 0;` then `COMMIT;` and record the row count lost.
5. Restore to a new data directory: extract the base backup, create `recovery.signal`, and set
```
restore_command = 'cp /backups/wal/%f %p'
recovery_target_time = '<T₀>'
recovery_target_action = 'promote'
```
Start the cluster and **time it from process start to "database system is ready to accept connections"**.
6. Verify: the deleted rows are present, and any transaction committed after T₀ is absent.
7. Repeat once more with `recovery_target_time` set 1 second *before* the `DELETE` rather than a minute before, to prove you can land precisely.

**Success criterion:** you can state three measured numbers — base backup duration, restore + replay duration (your real RTO), and the data-loss window implied by `archive_timeout` (your real RPO) — and you have a verified row count proving the deleted rows came back and post-T₀ writes did not.

---

## 5. One mini system-design exercise

### Design the data layer for *ShelfSpace*, an online marketplace

ShelfSpace is a marketplace where independent sellers list used and rare books. It is BookNook's model inverted: inventory is owned by thousands of sellers, not one warehouse. Design **only the data layer**.

#### Functional requirements

1. Sellers create, edit, suspend and delete listings (title, author, ISBN, condition, price, photos, free-text description).
2. Buyers search listings by free text, and filter by price range, condition, seller rating, shipping country and availability.
3. Buyers browse a listing detail page showing listing, seller profile, seller rating aggregate and up to 20 recent reviews.
4. Buyers add listings to a cart and check out; checkout must atomically reserve stock, create an order, capture payment and write an immutable ledger entry.
5. Order lifecycle: `pending → paid → shipped → delivered → (refunded | disputed)`, with a full audit trail of every transition.
6. Buyers and sellers view their own order history, newest first, paginated.
7. Buyers leave reviews on sellers; the seller's aggregate rating updates.
8. "Buyers who bought this also bought" recommendations, and seller fraud detection via shared payment instruments, devices and shipping addresses.
9. Finance runs monthly GMV, take-rate and payout reconciliation over all historical orders; analysts run ad-hoc cohort queries.
10. Every listing photo must be served fast globally.

#### Non-functional requirements

| Requirement | Target |
|---|---|
| Listing detail page read latency | **p99 < 120 ms** server-side |
| Search latency | **p99 < 250 ms** |
| Checkout write latency | **p99 < 800 ms** end-to-end, excluding the payment provider's own network time |
| Availability | **99.95 %** for browse/search; **99.99 %** for checkout and ledger |
| Durability | Zero tolerated loss of committed orders, payments and ledger entries |
| Correctness | No overselling of a unique (quantity-1) listing; no double-charge; no double-payout |
| Compliance | PCI-DSS — never store raw PANs; GDPR erasure within 30 days |
| Region | Primary EU-West, read traffic from EU + North America |

#### Scale numbers (treat as given; all approximate/typical)

| Metric | Value |
|---|---|
| Registered users | 40 M (25 M buyers active in last year, 300 k sellers) |
| Live listings | 180 M, growing 6 M/month |
| Average listing record | 2.5 KB structured + 1.2 MB of photos (avg 4 photos) |
| Orders per day | 600,000 (peak hour = 8 % of daily volume) |
| Order items per order | 1.4 average |
| Read:write ratio | **100 : 1** overall; **500 : 1** on listing detail |
| Listing detail page views | 240 M/day |
| Search QPS | **4,500 average, 14,000 peak** |
| Reviews | 90 M total, +250 k/day |
| Payment events | 600 k/day captures, 18 k/day refunds |
| Analytics | Full history (5 years, ~1.1 B order items) scanned for monthly reporting; daily refresh acceptable |
| Clickstream | 3.5 B events/day, 400 bytes each |
| Infrastructure budget | **$95,000/month** for the data layer (storage + compute + managed services), excluding CDN egress |

#### Explicitly out of scope

- Payment provider integration internals, PSP selection, 3-D Secure flows.
- The CDN and image-resizing pipeline (assume object storage + CDN exists).
- Front-end, mobile, and API gateway design.
- The recommendation *model* — you design where the data lives and how it is queried, not the ML.
- Multi-region active-active writes. Assume a single write region with read replicas elsewhere; justify that this is adequate against the stated SLOs.
- Team structure, hiring, vendor negotiation.

#### Decisions you must justify, with calculations

1. **Store placement.** Produce a table with one row per access pattern (all ten functional requirements, decomposed into reads and writes) and columns: entry key, QPS, store chosen, consistency required, latency budget. Justify every store with the access path it provides, and explicitly name the **single system of record** from which every other store can be rebuilt.
2. **Capacity arithmetic for the system of record.** Compute the size of `listings`, `orders`, `order_items`, `payments` and `reviews` at 5 years of growth, including index overhead at ~25 % per index, and state the resulting total. Show whether it fits one primary (state your assumed instance: e.g. 64 vCPU / 512 GB RAM / 10 TB NVMe) or requires partitioning.
3. **Write-throughput arithmetic.** Convert 600 k orders/day at an 8 %-of-daily peak hour into peak writes per second, including every table written per checkout (order, items, payment, ledger, stock decrement, outbox). State the peak TPS and compare against a realistic single-primary ceiling of 10k–20k write TPS.
4. **Shard key choice.** If and only if the arithmetic in (2) or (3) demands it, choose a shard key for the largest table(s). Evaluate at least three candidates (e.g. `seller_id`, `buyer_id`, `listing_id`, `order_id` with time prefix), and for each state: resulting distribution skew (ShelfSpace's top 1 % of sellers hold 22 % of listings — use this), which queries become cross-shard scatter-gather, and how re-sharding would be performed. State your choice and the specific query you have made expensive by choosing it.
5. **Index set.** For each of `listings`, `orders`, `order_items` and `reviews`, list the exact indexes (columns, order, partial predicate, included columns), the query each serves, and the estimated size. Then compute the aggregate write-amplification penalty: if each index costs ~7 % write throughput, what is the total cost of your index set, and is it within the budget from (3)?
6. **Consistency model per operation.** Give a table with one row per operation (stock reservation, payment capture, ledger append, order status read by buyer, seller rating aggregate, search result freshness, recommendations, clickstream) and columns: required model (linearizable / read-your-writes / monotonic / causal / eventual), mechanism that delivers it, and the user-visible symptom if you got it wrong. Explicitly justify *why* linearizability is not required where you did not choose it.
7. **Preventing overselling on quantity-1 listings.** Specify the exact mechanism (atomic conditional update, `SELECT … FOR UPDATE`, version column, or SERIALIZABLE with retries), the isolation level, and the expected conflict rate given 600 k orders/day over 180 M listings. Prove your mechanism is correct under two concurrent buyers and state its behaviour under 50 concurrent buyers on one hot listing.
8. **Derived-store synchronisation.** Specify how search, recommendations and the warehouse stay in sync with the system of record. Name the mechanism (transactional outbox + CDC, or dual writes — and if dual writes, defend it), the expected end-to-end lag, and what happens when the derived store is down for 4 hours.
9. **Search sizing.** 180 M listings with a text field averaging 600 bytes. Estimate the inverted-index size (assume index ≈ 40 % of source text plus stored fields), the shard count at a 30–50 GB-per-shard guideline, and the node count needed to serve 14,000 peak QPS assuming ~1,200 QPS per node. State the replica count that satisfies the 99.95 % availability target.
10. **Analytics path.** Choose warehouse vs lakehouse and justify against the budget. Define the star schema: declare the **grain** of the fact table in one sentence, list the dimensions, and choose SCD type for `dim_seller` (sellers change name, country and tier) with a reason. Compute daily and 5-year storage for 1.1 B order-item facts in Parquet at an assumed 6× compression.
11. **Clickstream.** 3.5 B events/day × 400 bytes = compute the daily and annual raw volume. Choose a store, a partitioning layout, a compression format and a retention tier schedule. Show the cost at $0.023/GB-month hot and $0.004/GB-month archival, and justify the hot/cold boundary.
12. **Object storage layout.** 180 M listings × 4 photos × 300 KB. Compute total volume and monthly cost. Specify the key naming scheme, the storage class per age band, and why block or file storage is wrong here.
13. **Backup strategy with RPO and RTO per data class.** Produce a table with one row per data class (ledger/payments, orders, listings, reviews, search index, recommendation graph, clickstream, warehouse) and columns: RPO, RTO, mechanism, where stored, restore-drill frequency, and the measured-restore arithmetic justifying the RTO (size ÷ restore throughput). Explicitly mark which classes are **rebuildable** rather than backed up, and state what they are rebuilt *from* and how long that takes.
14. **Budget reconciliation.** Sum your storage and compute costs into a single table and show the total against the $95,000/month limit. If you are over, state exactly which requirement you would renegotiate and by how much — not a vague "optimise later".
15. **Failure analysis.** For each of these, state the blast radius and the recovery action: primary database loses its disk; search cluster loses 2 of 6 nodes; the CDC pipeline stalls for 6 hours; a bad migration drops a column; an EU-West AZ fails.

**What "done" looks like:** a document of roughly 2,000–3,000 words containing at least six tables, every number derived rather than asserted, and no sentence containing the phrase "it depends" without the dependency named explicitly.

---

## 6. Common interview questions

| # | Question | What the interviewer is actually probing |
|---|---|---|
| 1 | "Walk me through how you'd choose between PostgreSQL and DynamoDB for this feature." | Whether you start from access patterns or from fashion. |
| 2 | "What does the C in ACID mean, and how is it different from the C in CAP?" | Precision; most candidates conflate them, which signals memorised rather than understood material. |
| 3 | "Explain how a B+tree index makes a lookup fast, and what it costs you." | Whether you understand indexes as a trade rather than as free speed. |
| 4 | "You added an index and the query got slower. How?" | Depth: stale statistics, low selectivity causing random I/O, or the planner switching to a worse plan. |
| 5 | "Here's an `EXPLAIN ANALYZE` output — what's wrong?" | Can you read estimated vs actual rows and identify the first node where the estimate diverges. |
| 6 | "Two users buy the last copy at the same time. Prevent the oversell." | Whether you reach for isolation levels, locks, atomic conditional updates — and whether you know when each fails. |
| 7 | "What's write skew, and which isolation level stops it?" | Separates people who read the isolation table from people who understand *why* snapshot isolation misses it. |
| 8 | "How would you shard this table? What breaks?" | Whether you can name the queries your shard key makes expensive — the real test. |
| 9 | "When would you denormalise, and how do you keep the copy correct?" | Engineering maturity: denormalisation is a cache, and you must own invalidation. |
| 10 | "Why can't we just run the analytics query on the production database?" | Understanding of buffer-pool pollution, long-running snapshots blocking vacuum, and p99 impact on unrelated traffic. |
| 11 | "Describe CAP and tell me what you'd actually choose and when." | Whether you know partitions are not a design choice and that the real decision is per-operation. |
| 12 | "What is PACELC and why would you prefer it to CAP?" | Awareness that the latency/consistency trade applies continuously, not only during rare partitions. |
| 13 | "A user updates their address, refreshes, and sees the old one. Diagnose and fix." | Read-your-writes, replication lag, sticky routing or LSN-aware reads — a very common production bug. |
| 14 | "Design the storage for 500 M product images." | Object vs block vs file, cost arithmetic, CDN placement, key design, storage classes. |
| 15 | "What is your RPO and RTO, and how do you know?" | Whether your numbers come from measured restore drills or from a slide. |
| 16 | "How do you keep a search index in sync with the database?" | Transactional outbox / CDC vs dual writes; whether you understand dual writes can lose data on partial failure. |
| 17 | "When would you use a graph database instead of recursive SQL?" | Index-free adjacency and the hop-count crossover, not "graphs are for graphs". |
| 18 | "What happens, physically, between `COMMIT` returning and the data being on disk?" | WAL, `fsync`, group commit, replication acknowledgement, checkpointing — real durability understanding. |
| 19 | "Your writes have slowed 10× over six months with no traffic change. Why?" | Index growth, bloat and vacuum starvation, random-UUID page splits, buffer pool no longer holding the working set. |
| 20 | "Why is a random UUID a bad primary key and what would you use instead?" | B-tree locality, page splits, write amplification; UUID v7 or snowflake IDs as the fix. |
| 21 | "Explain OLTP vs OLAP and why one storage layout can't serve both well." | Row vs column layout, I/O per query, compression — and awareness that HTAP exists but lags. |
| 22 | "A replica is 40 seconds behind. What are the likely causes?" | Long-running transaction on the primary, single-threaded apply, write burst, network saturation, vacuum storm. |
| 23 | "How do you delete one user's data for GDPR when it's in backups?" | Crypto-shredding, documented backup expiry, and knowing that rewriting immutable backups is not the answer. |
| 24 | "Give me the smallest change that would double this system's read capacity." | Pragmatism: caching, read replicas, covering index, or fixing one N+1 query — rather than a rewrite. |

---

## 7. Topics to revisit if you struggled

| If you found this hard | Re-read | Why it matters later |
|---|---|---|
| Deciding which database fits a feature | L48 (selecting from access patterns), L42 (SQL vs NoSQL) | Phase 4's distributed-transaction and saga topics assume you already know *why* data is split across stores; if you cannot justify the split, the sagas have no motivation. |
| The difference between the two meanings of "consistency" | L49 (ACID), L59 (CAP), L61 (consistency models) | Phase 4 consensus (Raft/Paxos) is defined entirely in terms of linearizability; confusing it with ACID-C makes every consensus discussion unreadable. |
| Isolation levels and which anomaly each prevents | L50, L51 | Phase 4's distributed transactions, two-phase commit and sagas are attempts to recover the isolation you lose when data crosses nodes. |
| Write skew specifically | L51, L52 | Phase 5's idempotency and exactly-once-effect discussions rest on the same "shared premise, different rows" reasoning applied across services. |
| Optimistic vs pessimistic concurrency | L52 | Distributed locks (Phase 4) are pessimistic concurrency over a network, with all the same failure modes plus lease expiry and fencing tokens. |
| Reading an `EXPLAIN ANALYZE` plan | L55, L53 | Phase 5 performance and capacity work starts with "where is the time going?"; without plan-reading you will guess, and guessing at p99 is how outages are made. |
| Composite index column ordering | L54 | Shard-key and clustering-key design (Phase 4 partitioning) uses the identical leftmost-prefix logic; get it wrong and every query becomes a scatter-gather. |
| Selectivity and why the planner ignores your index | L53, L55 | Phase 5's query-level autoscaling and caching decisions depend on knowing which queries are *inherently* scans versus fixable. |
| Normalisation vs denormalisation | L56 | Event-driven architectures (Phase 4) are denormalisation taken to its conclusion: every consumer builds its own read model and must keep it correct. |
| LSM-trees, SSTables and compaction | L45, L54 | Phase 4's replication-and-compaction interaction, and Phase 5's write-amplification and storage-cost tuning, both start here. |
| Partition keys and hot partitions | L45, L48 | Phase 4 partitioning/rebalancing and Phase 5 capacity planning both fail first at the hottest partition, never at the average. |
| CAP as a per-operation choice rather than a system label | L59, L60 | Phase 4's quorum systems, leader election and failover design are nothing but applied CAP decisions with specific numbers. |
| PACELC's E branch (latency vs consistency in normal operation) | L60 | Multi-region design in Phase 5 is dominated by the E branch; a 75 ms cross-region RTT either is or is not in your request path. |
| Session guarantees (read-your-writes, monotonic reads) | L61 | Phase 5's caching, CDN and edge-serving topics reintroduce every one of these anomalies at a new layer. |
| Eventual consistency and conflict resolution | L61 | CRDTs, vector clocks and last-write-wins reappear in Phase 4's replication and Phase 5's offline/mobile sync. |
| OLTP vs OLAP separation | L57, L58 | Phase 4's CDC and event-streaming topics exist primarily to move data from the OLTP side to the OLAP side without touching the primary. |
| Star schemas, grain and slowly changing dimensions | L58 | Phase 5's analytics, experimentation and metrics pipelines are unusable if the fact grain is undeclared — double-counted revenue is the classic symptom. |
| Object vs block vs file storage | L62 | Phase 5's media pipelines, CDN origins and lakehouse storage all assume you know why a database cannot live on object storage. |
| WAL mechanics and durability levels | L49, L63 | Phase 4 replication *is* WAL shipping; Phase 4 consensus logs are the same idea with voting. Without the WAL model, replication is magic. |
| RPO/RTO arithmetic and restore testing | L63 | Phase 5's reliability, incident-response and chaos-engineering topics all measure against RPO/RTO; an untested number is not a target. |
| Backups vs replication | L63 | Phase 4 multi-region failover design routinely confuses the two, producing architectures that survive hardware loss but not a bad migration. |
| Full-text analysis chains and relevance scoring | L47 | Phase 5's search ranking, personalisation and vector-search topics build directly on inverted indexes and BM25. |
| Graph traversal and index-free adjacency | L46 | Phase 5's fraud detection, social features and recommendation serving need the hop-count reasoning to choose the right store. |
| Capacity arithmetic in general (sizes, QPS, costs) | L53, L57, L63 | Every Phase 4 and Phase 5 design interview is scored on whether your numbers are derived or asserted. This is the single most transferable skill in the course. |

---

**NEXT LESSON: Phase 4, Topic 1 — Why distributed systems are difficult**
