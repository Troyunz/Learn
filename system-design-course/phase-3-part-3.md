# Lesson 48 — Selecting a Database from Access Patterns

*Phase 3, Topic 9*

## 1. Simple definition

**Selecting a database from access patterns** means choosing your storage engine by first writing down *every question your application will ask of its data, and every way it will change that data* — and only then picking the engine that answers those questions well.

The thesis of this entire lesson, stated once, bluntly:

> **You choose a database from access patterns. Not from data volume. Not from what is fashionable. Not from what the biggest company in your industry uses.**

Terms defined before use:

- **Access pattern**: one named, concrete operation the application performs against stored data. "Fetch a book by ISBN" is an access pattern. "Store books" is not — it has no query shape, no latency budget, no frequency.
- **Query shape**: the structural form of the lookup, independent of the values. `WHERE isbn = ?` is a point lookup by primary key. `WHERE author_id = ? ORDER BY published_at DESC LIMIT 20` is a sorted range scan on a secondary key. The shape, not the SQL text, determines what index and what engine can serve it.
- **Cardinality**: how many rows the pattern touches — both how many *exist* for a typical key, and how many are *returned*. Fetching one row out of 50 million is cheap on every engine. Fetching 500,000 rows is expensive on all of them, and catastrophic on some.
- **Read-to-write ratio**: reads per write for the same entity. BookNook's book catalogue might be 50,000:1; its shopping cart might be 3:1.
- **Store / engine / database family**: "family" means a class (relational, document, key-value, wide-column, graph, search — Lessons 40–47). "Engine" means a specific product (PostgreSQL, Redis, Cassandra). "Store" means a deployed instance of an engine that you operate.

Lesson 42 compared SQL and NoSQL as categories. Lessons 43–47 explained each family's mechanics. This lesson is the one that turns that knowledge into a *decision*, with a defensible written justification.

## 2. Why it exists

Database selection is the hardest decision in system design to reverse. Changing a load-balancing strategy (Lesson 23) is a config push. Changing a cache policy (Lesson 29) is a deploy. Changing the primary datastore of a live product is a multi-quarter project involving dual writes, backfill, shadow reads, reconciliation, and a cutover that can lose money if it goes wrong. The cost of being wrong is asymmetric, so the method of deciding has to be rigorous.

Three bad decision procedures dominate real teams, and this lesson exists to replace all three.

**Bad procedure 1 — deciding from data volume.** "We'll have 200 GB of data, that's big, so we need a distributed NoSQL store." 200 GB fits in RAM on a commodity cloud instance available today. A single PostgreSQL node comfortably serves multi-terabyte datasets. Volume alone tells you almost nothing: the binding constraint is usually *write throughput per key range*, *fan-out of a query*, or *p99 latency under concurrency* — not bytes at rest.

**Bad procedure 2 — deciding from fashion.** "Netflix uses Cassandra." Netflix's access pattern is enormous write volume of time-series viewing events, with tolerance for eventual consistency, operated by a dedicated storage team of specialists. If your access pattern is "180 writes per second of orders that must never be double-charged, operated by four engineers who also own the frontend", you have copied the answer to someone else's question.

**Bad procedure 3 — deciding from data shape alone.** "Our data is nested JSON, so document database." Data shape matters, but it is one criterion out of six. Nested JSON is stored perfectly well in PostgreSQL's `jsonb` column type. The question is not "does it look like a document" but "do I ever need to query across documents in ways a document store cannot index efficiently".

The positive reason the discipline exists: **access patterns are the only input that is simultaneously knowable in advance, stable over time, and directly predictive of engine fit.** You can enumerate them in an afternoon with a product manager. They change far more slowly than data volume. And every database family's strengths and weaknesses are expressed precisely in terms of which query shapes it can serve with which guarantees.

## 3. Real-world analogy

**Choosing vehicles for a delivery company.**

A new logistics company asks: "What vehicle should we buy?" The naive answers mirror the three bad procedures.

- *Deciding from volume*: "We'll move 40 tonnes a week, so buy an articulated lorry." But if those 40 tonnes are 9,000 separate small parcels delivered to residential doors in a city centre, a lorry is the worst possible choice — it cannot park, cannot turn, and needs a special licence.
- *Deciding from fashion*: "Amazon uses big distribution vans, let's buy those." Amazon's pattern is dense urban routes with 180 drops per shift and their own sortation centres.
- *Deciding from access patterns*: you first write down the actual journeys. "600 city-centre parcel drops per day, each under 5 kg, within a 4 km radius, must arrive same-day." "Two pallet deliveries per week to a warehouse 300 km away, next-day acceptable." "Emergency medical courier, under 2 kg, must arrive within 40 minutes, any time of day."

Only now is the answer obvious and *different for each pattern*: cargo bikes for the first, a hired lorry for the second, a motorbike for the third. And critically — the analogy's most important lesson — you notice that one well-chosen medium van could serve patterns 1 and 2 *adequately*, badly but acceptably, with one vehicle to insure, fuel, service, and find drivers for. Running three vehicle types triples your maintenance contracts, your spare-parts inventory, and your driver-training matrix. That is **polyglot persistence** and its hidden cost, which §4 covers in detail.

**Where the analogy breaks down:**

| Delivery vehicles | Databases |
|---|---|
| You can sell a vehicle you bought by mistake and recover most of its value | A wrong database choice costs a migration project; the data you wrote into it is the liability, not the licence fee |
| A driver trained on vans can drive a different van next week | Operational expertise in PostgreSQL does not transfer to Cassandra; repair, compaction, and tombstone semantics are entirely new knowledge (Lesson 45) |
| Vehicles operate independently; one breaking down does not corrupt another's cargo | Two stores holding related data can **diverge**. An order in PostgreSQL with no matching document in Elasticsearch is silent, permanent, invisible corruption |
| Journeys are known and stable for years | Access patterns get *added* constantly — "can we show customers what they viewed last week?" is a new pattern with a new shape, arriving mid-sprint |
| There is no transaction spanning two vehicles | Writing to two stores means you have **lost atomicity** (Lesson 49). There is no `COMMIT` across PostgreSQL and Redis |
| A bigger lorry really does carry more | A "bigger" database does not fix a query shape mismatch. A 128-core machine running a query that needs an index you cannot build is still slow |

That fourth row is the one that bites hardest in practice, and §4 and §11 return to it.

## 4. Detailed technical explanation

### 4.1 How to enumerate access patterns systematically

For **each** operation your application performs, capture these seven fields. Fewer fields and you cannot discriminate between engines; more and the exercise stalls.

| Field | What it means | Why it discriminates |
|---|---|---|
| **Entity** | Which thing is being read or written (Book, Order, Cart, Review) | Determines which store it would live in, and which entities must be co-located for joins or transactions |
| **Query shape** | `point lookup by PK`, `range scan by secondary key with sort`, `multi-field filter`, `full-text match`, `aggregate over range`, `graph traversal depth N`, `write-append`, `read-modify-write` | The single most predictive field. Each family serves some shapes natively and others only via full scans |
| **Read:write ratio** | Reads per write for that entity, approximate | Read-heavy entities are cacheable (Lesson 29) and replica-servable (Topic 15). Write-heavy ones determine your primary's sizing and may need LSM-tree storage (Topic 13) |
| **Latency requirement** | p99 budget in milliseconds, and whether it is user-facing or background | A 5 ms p99 forbids a cross-region call; an 8-second nightly report forbids nothing |
| **Consistency requirement** | `read-your-writes`, `strong`, `bounded staleness (N seconds acceptable)`, `eventual` | Decides whether you may serve from a replica, a cache, or an eventually-consistent store. This field is routinely left blank, and leaving it blank is how teams ship bugs |
| **Cardinality** | Rows existing per key, and rows returned per call | A pattern returning 20 rows is fine everywhere. A pattern returning 200,000 rows needs pagination or a different shape entirely |
| **Growth rate** | Rows added per day, and whether growth is bounded | Unbounded, high-rate growth (events, logs, audit) is a different problem from bounded growth (users, products) |

Two practical rules when enumerating:

1. **Write the shape, not the SQL.** `WHERE customer_id = ? AND status = 'open' ORDER BY created_at DESC LIMIT 50` is a *composite-key range scan with sort*. That abstraction is what you match against engines.
2. **Include the writes.** Teams enumerate reads enthusiastically and forget writes, then discover their chosen store cannot do a 40,000/sec append.

### 4.2 The six criteria that actually discriminate between families

Volume and fashion do not discriminate. These six do.

**Criterion 1 — Query flexibility needed.** How many *different* shapes must be served against the same entity, and will new shapes be added without warning?

- High flexibility (ad-hoc filters, new report next week, analysts with SQL access) → relational. A relational engine lets you add an index and serve a shape you never anticipated.
- Low flexibility (exactly three known shapes, fixed forever) → key-value or wide-column, where you design the key around the shape.

The hard rule: **wide-column stores (Lesson 45) require you to know your queries before you model your tables.** If you cannot promise your query set is stable, you cannot use one as a primary store without pain.

**Criterion 2 — Relationship depth.** How many hops between entities does a single operation traverse?

- 0–1 hops ("order and its lines") → any family; denormalise into one document if you like.
- 2–3 hops with known shape ("customer → orders → order lines → books") → relational joins, comfortably.
- Variable or unbounded depth ("people who bought books also bought, transitively, to depth 4") → graph database (Lesson 46), because recursive joins in SQL degrade sharply as depth grows and the result set branches.

**Criterion 3 — Write throughput, measured per partition.** Not total writes — writes to the hottest key range.

- Under roughly 5,000 writes/sec sustained to a single primary → single-node PostgreSQL, comfortably, on modern NVMe hardware (approximate; depends on row width, index count, and whether writes are appends or updates).
- 5,000–20,000 writes/sec → tune first (batching, fewer indexes, `UNLOGGED` staging, partitioning), then consider sharding (Topic 16).
- Over roughly 50,000 writes/sec sustained with no natural single-node partition → LSM-tree based distributed store (Cassandra, Lesson 45), which is designed for exactly this.

**Criterion 4 — Consistency requirement.** Per pattern, not per system.

- Must a read after a write by the same user reflect that write? (read-your-writes)
- Can two concurrent operations both succeed in a way that violates a business invariant — overselling the last copy of a book, double-charging a card?
- If the answer to the second is "that must never happen", you need transactions and constraints (Lesson 49), which pushes you to a relational engine or a document store with multi-document transactions.

**Criterion 5 — Data shape stability.** Does every record of an entity have the same fields?

- Stable, uniform fields → relational, with a schema that *documents* and *enforces* the shape.
- Genuinely heterogeneous per-record fields (supplier-specific product attributes, where every supplier sends a different set) → document store, or `jsonb` inside relational, which gets you both.
- The trap: teams choose schemaless to "move fast", then discover that *every reader* must now defensively handle every historical shape. The schema did not disappear; it moved from the database into every piece of application code, un-enforced.

**Criterion 6 — Operational maturity available.** The criterion most often omitted and most often decisive.

Honestly answer: how many engineers on this team have run this engine in production, through a failover, a version upgrade, a disk-full incident, and a restore-from-backup drill? If the answer is zero, the engine's theoretical fit must be *overwhelming* to justify it, because your real availability will be determined by your ability to operate it at 3 a.m., not by its benchmark numbers.

A managed service (RDS, Aurora, Atlas, DynamoDB) reduces but does not eliminate this cost: the provider handles backups and failover, but nobody else will debug your hot partition, your tombstone accumulation, or your query plan regression.

### 4.3 Polyglot persistence and its real costs

**Polyglot persistence** means deliberately running multiple database engines, each serving the access patterns it suits. It is often correct. It is never free, and the costs are systematically underestimated because only one of them appears on the cloud bill.

| Cost | What it actually looks like |
|---|---|
| **Operational burden** | Each engine needs: monitoring and dashboards, alert thresholds someone understands, a backup schedule, a *tested* restore procedure, version-upgrade planning, capacity planning, and security patching. Approximately 0.3–0.5 of an engineer's time per engine per year, steady-state, once it is stable — more in the first year |
| **Data synchronisation** | Data in two stores will diverge. You need a sync mechanism (dual write, change-data-capture, or batch reconcile), monitoring of sync lag, and a *reconciliation job* that detects and repairs divergence. This is a real service that someone must own |
| **Transaction boundaries lost** | You cannot atomically write to PostgreSQL and Redis. Every cross-store write becomes a distributed-coordination problem requiring the outbox pattern or sagas (Phase 4, Topics 19–21). Code that was three lines becomes an architecture |
| **More on-call surface** | Availability of a request path that touches N stores is the *product* of their availabilities. Four stores at 99.9% each give roughly 99.6% combined for a path touching all four — about 35 hours of expected annual downtime versus 8.8 hours for one |
| **Harder local development** | `docker compose up` now starts five containers, uses 6 GB of RAM, takes 90 seconds, and breaks differently on each engineer's laptop. Integration tests get slower and flakier, which quietly erodes test discipline |
| **Hiring and onboarding** | A new engineer must learn five data models, five query languages, and five failure modes before they can debug an incident unaided |

### 4.4 The strong default: start with PostgreSQL

**Recommendation, stated as a default to be argued against rather than a rule:** start with a single PostgreSQL instance. Add a second store only when a *specific, named access pattern* demonstrably cannot be served, with measured evidence.

Why PostgreSQL specifically, rather than "a relational database":

- It serves the widest range of query shapes of any single engine: relational joins, `jsonb` document storage with GIN indexes, full-text search via `tsvector`, arrays, ranges, geospatial via PostGIS, recursive CTEs for graph traversal to modest depth, window functions for analytics, and `LISTEN/NOTIFY` for lightweight pub-sub.
- It gives you real transactions and constraints (Lesson 49), which eliminate entire classes of data-corruption bugs by construction.
- Operational knowledge is widely available, and every cloud provider offers a managed version.

The honest limitation: PostgreSQL is a single-primary system. Writes do not scale horizontally without sharding (Topic 16). That is the boundary at which the default stops applying.

**Evidence thresholds that justify adding a specialised store.** Each of these is a *measurement you must have taken*, not a prediction:

| Add this store | Only when you have measured |
|---|---|
| **Redis** (cache / session, Lesson 43) | The same read query runs > 1,000/sec, p99 database latency on it exceeds your budget, and the data tolerates staleness you have quantified in seconds |
| **Elasticsearch / OpenSearch** (Lesson 47) | PostgreSQL full-text search p95 exceeds your budget on real data volume **and** product requires relevance ranking, typo tolerance, faceting, or synonyms that `tsvector` cannot express |
| **Cassandra / DynamoDB** (Lesson 45) | Sustained write rate to one entity exceeds roughly 20,000/sec after batching and index reduction, with no natural tenant-based shard key, or you need multi-region active-active writes |
| **Neo4j / graph** (Lesson 46) | A required traversal exceeds depth 3 with branching, recursive CTE p95 is unacceptable on production-size data, and the traversal is a core product feature rather than one report |
| **ClickHouse / analytical store** | Analytical scans over > 100 M rows are both required interactively and are degrading OLTP latency on the primary, and a read replica has not solved it |
| **S3 / object storage** | You are storing binaries. This one needs no threshold — never store images or PDFs as database rows |

The discipline is: **"we measured X, the budget was Y, X exceeded Y, we tried Z and it did not close the gap"**. If you cannot fill that sentence in, you do not yet have evidence.

## 5. Request / data flow

The flow here is the *decision procedure* itself, start to finish.

1. **Enumerate operations with product.** Walk every user-facing screen and every background job. For each, write one line: "customer views a book page", "nightly job recomputes bestseller list". Expect 20–60 lines for a real product.
2. **Expand each into the seven fields** of §4.1. Entity, query shape, read:write ratio, latency budget, consistency requirement, cardinality, growth rate. Mark any field you are guessing as a guess.
3. **Group by entity.** Patterns touching the same entity will usually live in the same store. Note which patterns must be *atomic together* — these cannot be split across stores without sagas.
4. **Rank by criticality.** Which patterns are on the checkout path (money), which are on the browse path (conversion), which are internal (tolerate degradation)? Design for the critical ones; let the rest fall where they may.
5. **Test each pattern against the PostgreSQL default.** For each: can a relational engine serve this shape, at this latency, at this throughput, with an index I can actually build? Most patterns pass. Record the answer explicitly.
6. **Collect the failures.** The patterns that fail step 5 are your *candidate justifications* for specialised stores. If there are zero, you are done — one store.
7. **Prototype the failures, with real data volume.** Load production-scale synthetic data and measure. Many "obvious" failures pass once a composite index exists. This step kills most proposed polyglot architectures, which is its purpose.
8. **For surviving failures, select a family by criteria** (§4.2), then an engine within that family weighted by operational maturity (criterion 6) and managed-service availability.
9. **Design the synchronisation path before you commit.** How does data get into the second store? Change-data-capture, outbox, or dual write? What is the acceptable lag? How is divergence detected and repaired? If you cannot answer, you are not ready to add the store.
10. **Define the degradation behaviour.** If the second store is down, does the product fail or degrade? Search down → fall back to a database `LIKE` query with a banner. Cache down → slower but correct. Decide before launch, not during the incident.
11. **Write the decision record.** One page per store: the pattern that justified it, the measurement, the rejected alternatives, and the *exit criterion* under which you would remove it. Future-you will not remember, and will otherwise re-litigate it annually.
12. **Re-run the exercise when patterns change.** New product features add patterns. Revisit at least annually, or whenever a store's cost or incident rate becomes a topic of conversation.

## 6. ASCII diagram

```
                    ACCESS-PATTERN → DATABASE DECISION TREE
                    (apply per access pattern, not per system)

                 ┌─────────────────────────────────────┐
                 │  Pattern enumerated with 7 fields?  │
                 └──────────────┬──────────────────────┘
                         no ────┴──── yes
                          │            │
                   ┌──────▼──────┐     │
                   │ STOP. Go    │     │
                   │ enumerate.  │     │
                   └─────────────┘     │
                                       ▼
                 ┌──────────────────────────────────────┐
                 │ Is it a binary blob (image/PDF/video)│
                 └──────────────┬───────────────────────┘
                        yes ────┴──── no
                         │             │
                  ┌──────▼───────┐     │
                  │ OBJECT STORE │     │
                  │ (S3), URL in │     │
                  │ the database │     │
                  └──────────────┘     ▼
                 ┌──────────────────────────────────────┐
                 │ Sustained writes to ONE entity's hot  │
                 │ key range > ~20k/sec after batching?  │
                 └──────────────┬───────────────────────┘
                        yes ────┴──── no
                         │             │
              ┌──────────▼─────────┐   │
              │ Need strong txns   │   │
              │ on those writes?   │   │
              └──────┬──────┬──────┘   │
                 yes │      │ no       │
         ┌───────────▼──┐ ┌─▼────────────────┐
         │ SHARD a      │ │ WIDE-COLUMN      │
         │ relational   │ │ Cassandra/Dynamo │
         │ store        │ │ (L45) — queries  │
         │ (Topic 16)   │ │ must be KNOWN    │
         └──────────────┘ └──────────────────┘
                                       │
                                       ▼
                 ┌──────────────────────────────────────┐
                 │ Traversal depth > 3 with branching,   │
                 │ as a CORE product feature?            │
                 └──────────────┬───────────────────────┘
                        yes ────┴──── no
                         │             │
                 ┌───────▼───────┐     │
                 │ GRAPH (L46)   │     │
                 │ Neo4j, or PG  │     │
                 │ + recursive   │     │
                 │ CTE if ≤ 3    │     │
                 └───────────────┘     ▼
                 ┌──────────────────────────────────────┐
                 │ Relevance ranking / typo tolerance /  │
                 │ faceting / synonyms REQUIRED?         │
                 └──────────────┬───────────────────────┘
                        yes ────┴──── no
                         │             │
              ┌──────────▼─────────┐   │
              │ Measured PG        │   │
              │ tsvector p95 over  │   │
              │ budget on real     │   │
              │ data volume?       │   │
              └──────┬──────┬──────┘   │
                 yes │      │ no       │
        ┌────────────▼─┐ ┌──▼────────┐ │
        │ SEARCH ENGINE│ │ PG FULL-  │ │
        │ Elastic/OS   │ │ TEXT      │ │
        │ (L47) + CDC  │ │ (tsvector)│ │
        └──────────────┘ └───────────┘ │
                                       ▼
                 ┌──────────────────────────────────────┐
                 │ Ephemeral, high-rate, tolerates loss  │
                 │ (session, rate-limit, hot cache)?     │
                 └──────────────┬───────────────────────┘
                        yes ────┴──── no
                         │             │
                 ┌───────▼────────┐    ▼
                 │ KEY-VALUE      │  ┌───────────────────────┐
                 │ Redis (L43)    │  │  DEFAULT: PostgreSQL  │
                 │ with a defined │  │  relational + jsonb   │
                 │ eviction rule  │  │  (L40, L41)           │
                 └────────────────┘  └───────────────────────┘

  RULE: every arrow leaving the default must carry a MEASUREMENT,
        not a prediction. No measurement → take the default.
```

## 7. Detailed example

### 7.1 BookNook — complete access-pattern enumeration

BookNook is an online bookstore: roughly 2 million titles, 900,000 registered customers, 180,000 orders per month, with a catalogue browse path, a search path, a cart and checkout path, reviews, recommendations, and an internal reporting need.

Here is the full enumeration. Numbers are *approximate steady-state figures* a team would estimate from current traffic plus 12 months of growth.

| # | Pattern | Entity | Query shape | Read:write | p99 latency | Consistency | Cardinality | Growth |
|---|---|---|---|---|---|---|---|---|
| 1 | Fetch book detail page by ISBN | Book | Point lookup by PK | 80,000 : 1 | 50 ms | Eventual OK (5 min stale fine) | 1 row returned of 2 M | +2k titles/mo, bounded |
| 2 | Browse books by category, newest first, paginated | Book | Composite range scan + sort + limit | 40,000 : 1 | 150 ms | Eventual OK | 20 of up to 150k per category | as above |
| 3 | Full-text search with typo tolerance, facets, relevance | Book | Inverted-index match + rank + facet | read-only | 200 ms | 60 s stale acceptable | 20 of 2 M, scored | reindex on catalogue change |
| 4 | Add / update / remove cart item | Cart | Read-modify-write by customer_id | 3 : 1 | 100 ms | **Read-your-writes required** | ~4 items/cart | 300k active carts, churns daily |
| 5 | Checkout: reserve stock, charge, create order + lines | Order, Inventory | Multi-entity transactional write | 1 : 1 | 1,200 ms | **Strong, atomic** | 1 order + ~3 lines + N stock rows | 180k orders/mo, unbounded |
| 6 | Customer order history, newest first | Order | Range scan by FK + sort + limit | 25 : 1 | 300 ms | Read-your-writes | 20 of ~12 per customer | grows with orders |
| 7 | Inventory decrement on sale | Inventory | Read-modify-write, contended on hot titles | 1 : 1 | 200 ms | **Strong — must not oversell** | 1 row, high contention on bestsellers | bounded by titles |
| 8 | Write a review | Review | Append by (book_id, customer_id) | — | 400 ms | Read-your-writes | 1 row | ~9k/mo, unbounded |
| 9 | Show reviews for a book, sorted by helpfulness | Review | Range scan by FK + sort + limit | 5,000 : 1 | 150 ms | Eventual OK | 10 of up to 40k on popular titles | as above |
| 10 | "Customers who bought this also bought" | Order lines | 2-hop co-occurrence aggregate | read-heavy, precomputed | 80 ms | Daily staleness fine | 10 of millions of pairs | recomputed nightly |
| 11 | Session lookup on every authenticated request | Session | Point lookup by token | 500 : 1 | **10 ms** | Read-your-writes | 1 row, ~400k live | churns, bounded by TTL |
| 12 | Rate-limit counter per IP per minute | Counter | Atomic increment with TTL | 1 : 1 | **5 ms** | Approximate acceptable | 1 counter | ~1 M counters/hour, expiring |
| 13 | Nightly finance report: revenue by category by day | Order lines | Aggregate scan over 90 days | 1 run/day | 10 min | Snapshot consistent | scans ~5 M rows | grows with orders |
| 14 | Audit log of admin price changes | AuditEvent | Append-only write; rare range read | 1 : 2,000 | 500 ms write | Durable, immutable | append | ~40k/mo, unbounded, retained 7 yrs |

### 7.2 Mapping each pattern to a store, with justification

| # | Store | Justification |
|---|---|---|
| 1 | PostgreSQL + Redis cache | Point lookup by PK is the easiest shape for any engine. At 80,000:1 read:write with tolerated staleness, it meets the Redis threshold — cache it. Redis is an *optimisation on top of* PG, not a replacement |
| 2 | PostgreSQL | Composite index on `(category_id, published_at DESC)` serves this as an index-ordered scan reading exactly 20 rows. No engine does better; nothing to add |
| 3 | Elasticsearch, fed by CDC from PG | Typo tolerance, relevance ranking, and faceting are explicitly required by product. `tsvector` can do matching but not BM25-tuned ranking plus faceting plus synonyms at this scale. This is the clearest justification in the list (Lesson 47) |
| 4 | PostgreSQL | Only 3:1 read:write and *read-your-writes required*. A cache here is a correctness hazard for a small gain. Low volume; PG is ample |
| 5 | PostgreSQL | Atomic multi-entity write with money involved. This single pattern makes a transactional relational store mandatory (Lesson 49). Everything atomic with it must live in the same store |
| 6 | PostgreSQL | Index on `(customer_id, created_at DESC)`; cardinality ~12 rows per customer. Trivial |
| 7 | PostgreSQL | Must not oversell. Needs row-level locking and a `CHECK (quantity >= 0)` constraint. Co-located with pattern 5 because it must be in the same transaction |
| 8, 9 | PostgreSQL | Writes are rare; reads are a sorted range scan on an indexed FK, returning 10 rows. Pattern 9's 5,000:1 ratio makes it a *candidate* for caching later, but measure first |
| 10 | PostgreSQL table, precomputed nightly | Tempting graph-database case, but it is a **2-hop aggregate recomputed daily**, not an interactive deep traversal. A nightly job writes a `related_books` table; reads become pattern-1-shaped point lookups. Does not meet the graph threshold |
| 11 | Redis | 10 ms p99 on a 500:1 point lookup, 400k live keys, natural TTL expiry. This is exactly what Redis exists for (Lesson 43). PG would work but wastes primary capacity on the hottest path in the system |
| 12 | Redis | Atomic `INCR` with `EXPIRE`, approximate counting acceptable, 1 M short-lived keys/hour. Writing this to PG means 1 M row inserts/hour of pure garbage and vacuum pressure |
| 13 | PostgreSQL **read replica** | Scans 5 M rows for 10 minutes. Correct answer is not a new engine — it is to run it on a replica so it cannot degrade checkout latency (Topic 15). Revisit only if scan volume exceeds ~100 M rows |
| 14 | PostgreSQL, partitioned by month | Append-only, 40k/mo — negligible volume. Seven-year retention is handled with declarative partitioning and cold-storage archival, not a new engine |

**Resulting architecture: PostgreSQL (primary + replica) + Redis + Elasticsearch.** Three stores, every one justified by a named pattern that PG demonstrably cannot serve within budget. Note what did *not* get added: no graph database (pattern 10 reduced to a precomputed table), no wide-column store (peak write rate is ~200/sec, three orders of magnitude below the threshold), no analytical warehouse (a replica suffices at this volume).

### 7.3 Contrasting example — a vehicle-telematics platform

Same method, completely different answer. A fleet-telematics company ingests GPS and engine telemetry from 400,000 vehicles, each emitting one reading every 2 seconds.

| Pattern | Shape | Rate | Requirement |
|---|---|---|---|
| Ingest telemetry reading | Append by `(vehicle_id, timestamp)` | **200,000 writes/sec sustained** | Durable, no transactions needed |
| Show one vehicle's track for a time window | Range scan by `(vehicle_id, time range)` | 400/sec | Eventual OK |
| Fleet dashboard: current position of all vehicles | Point lookup of latest per vehicle | 3,000/sec | 5 s stale fine, 20 ms p99 |
| Monthly mileage billing per customer | Aggregate over billions of rows | 1 run/month | Snapshot consistent |

Here the first pattern alone blows past every relational threshold: 200,000 appends/sec, naturally partitioned by `vehicle_id`, no cross-row transactions, and a query set that is genuinely fixed. The decision tree lands on **Cassandra** for telemetry (partition key `vehicle_id`, clustering key `timestamp` descending — exactly the shape Lesson 45 describes), **Redis** for the latest-position dashboard, and **PostgreSQL** for customers, vehicles, contracts, and billing, where relationships and transactions live. The monthly aggregate goes to an analytical store because scanning billions of rows in Cassandra is precisely what it is bad at.

The lesson from the contrast: **BookNook and the telematics platform have similar team sizes and similar total data volumes, and completely different correct architectures — because their access patterns differ, not because their volumes do.**

## 8. Code or configuration example

The following is **illustrative teaching code, not production-ready**. It omits connection pooling, retries, migrations, error taxonomy, observability, and security hardening. Its purpose is to make the §7 mapping concrete.

```sql
-- =====================================================================
-- BookNook: PostgreSQL schema shaped BY the access patterns in §7.1.
-- Every index below exists to serve a NUMBERED pattern. If you cannot
-- name the pattern an index serves, delete the index: each one taxes
-- every write to the table.
-- =====================================================================

CREATE TABLE books (
    isbn          TEXT PRIMARY KEY,              -- Pattern 1: point lookup by PK.
    title         TEXT        NOT NULL,
    author_id     BIGINT      NOT NULL REFERENCES authors(id),
    category_id   INT         NOT NULL REFERENCES categories(id),
    price_cents   INT         NOT NULL CHECK (price_cents >= 0),  -- money as integer cents:
                                                                 -- floats cannot represent
                                                                 -- 0.10 exactly (Lesson 41).
    published_at  DATE        NOT NULL,
    attributes    JSONB       NOT NULL DEFAULT '{}'::jsonb,       -- Criterion 5: supplier-specific
                                                                 -- fields that genuinely vary per
                                                                 -- record live here, NOT as 40
                                                                 -- mostly-NULL columns.
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()              -- drives CDC to Elasticsearch.
);

-- Pattern 2: browse by category, newest first, paginated.
-- Composite order matters: equality column FIRST, then the sort column.
-- Reversed, PostgreSQL would have to scan and sort the whole category.
CREATE INDEX books_category_recent_idx
    ON books (category_id, published_at DESC);

-- Pattern 1 cache-fill and CDC tailing: find rows changed since a watermark.
CREATE INDEX books_updated_idx ON books (updated_at);

-- =====================================================================
-- Patterns 5 and 7: the transactional core. These entities MUST live in
-- the same store because they must commit atomically (Lesson 49).
-- =====================================================================

CREATE TABLE inventory (
    isbn      TEXT PRIMARY KEY REFERENCES books(isbn),
    quantity  INT NOT NULL CHECK (quantity >= 0)   -- The CHECK is the oversell guard.
                                                   -- The database refuses to go negative even
                                                   -- if application logic is buggy. A
                                                   -- key-value store cannot enforce this.
);

CREATE TABLE orders (
    id           BIGSERIAL PRIMARY KEY,
    customer_id  BIGINT      NOT NULL REFERENCES customers(id),
    status       TEXT        NOT NULL CHECK (status IN ('pending','paid','shipped','cancelled')),
    total_cents  INT         NOT NULL CHECK (total_cents >= 0),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Pattern 6: order history for one customer, newest first.
-- Serves the equality filter AND the sort from one index.
CREATE INDEX orders_customer_recent_idx
    ON orders (customer_id, created_at DESC);

-- Pattern 10: nightly-recomputed co-purchase table. This is the cheap
-- alternative to adopting a graph database for a 2-hop aggregate.
-- Read shape becomes a point lookup, identical in cost to Pattern 1.
CREATE TABLE related_books (
    isbn          TEXT NOT NULL REFERENCES books(isbn),
    related_isbn  TEXT NOT NULL REFERENCES books(isbn),
    score         REAL NOT NULL,                   -- co-occurrence strength, approximate.
    PRIMARY KEY (isbn, score DESC, related_isbn)   -- top-N by score served directly from the PK.
);

-- Pattern 14: audit log, partitioned by month so that 7-year retention
-- is a DETACH PARTITION (instant) rather than a DELETE of millions of
-- rows (slow, bloating, vacuum-heavy — see Lesson 49 §4 on long transactions).
CREATE TABLE audit_events (
    id          BIGSERIAL,
    actor_id    BIGINT      NOT NULL,
    action      TEXT        NOT NULL,
    payload     JSONB       NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
) PARTITION BY RANGE (occurred_at);

CREATE TABLE audit_events_2026_10 PARTITION OF audit_events
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
```

```python
# ---------------------------------------------------------------------
# Illustrative only. Shows WHICH store serves WHICH pattern, and the
# degradation behaviour decided in §5 step 10. Not production code:
# no pooling, no circuit breaker, no tracing, no input validation.
# ---------------------------------------------------------------------

CACHE_TTL_SECONDS = 300          # Pattern 1 tolerates 5 minutes of staleness.
                                 # This number IS the consistency requirement,
                                 # written down rather than assumed.

def get_book(isbn):
    """Pattern 1: 80,000:1 read:write, 50 ms budget, staleness tolerated."""
    cached = redis.get(f"book:{isbn}")        # Redis: ~0.3 ms typical round trip.
    if cached:
        return json.loads(cached)

    row = pg.query_one("SELECT * FROM books WHERE isbn = %s", isbn)  # PG is the source of truth.
    if row is None:
        return None

    # Negative results are deliberately NOT cached here; doing so would need a
    # separate shorter TTL to avoid hiding a newly added book for 5 minutes.
    redis.setex(f"book:{isbn}", CACHE_TTL_SECONDS, json.dumps(row))
    return row


def search_books(query, filters):
    """Pattern 3: relevance + typo tolerance + facets => Elasticsearch."""
    try:
        return es.search(index="books", body=build_query(query, filters))
    except ElasticsearchUnavailable:
        # Degradation decided in advance (§5 step 10): the site stays UP with
        # worse search, rather than returning 500s. The banner is deliberate —
        # silent quality degradation generates untraceable support tickets.
        return {
            "results": pg.query(
                # Simple prefix match. No ranking, no typos, no facets.
                "SELECT * FROM books WHERE title ILIKE %s LIMIT 20",
                query + "%"
            ),
            "degraded": True,
        }


def check_rate_limit(ip):
    """Pattern 12: 5 ms budget, 1 M ephemeral counters/hour, approximate OK."""
    key = f"rl:{ip}:{int(time.time() // 60)}"   # Key embeds the minute bucket,
                                                # so expiry is automatic rather
                                                # than requiring a cleanup job.
    count = redis.incr(key)                     # Atomic server-side increment.
    if count == 1:
        redis.expire(key, 120)                  # TTL set only on creation.
    return count <= 600

    # Why not PostgreSQL? 1 M inserts/hour of data that is worthless after
    # 60 seconds would consume primary write capacity and generate vacuum
    # work, competing with Pattern 5 (checkout) — the pattern that earns money.
```

## 9. Numerical example

**Question: what does BookNook's three-store architecture actually cost compared with a single-store architecture, over three years, including engineering time?**

### Stated assumptions

Every number below is an **assumption, labelled typical or approximate** for a mid-size product in 2026. Change the inputs and the conclusion changes; the point is the *method*.

- Fully loaded engineer cost: **£120,000/year** (salary, employer taxes, benefits, equipment, office). Approximate for a senior engineer in a European market.
- Working year: **220 days**, so one engineer-day ≈ **£545**.
- BookNook traffic: 180,000 orders/month, peak ~2,000 requests/sec.
- Infrastructure is managed cloud services, list price, no committed-use discount.

### Architecture A — single PostgreSQL store

Everything in §7.1 served by PG: sessions in a table, rate limits in a table, search via `tsvector`, cache via... nothing.

**Infrastructure, monthly:**

| Item | Spec | Cost |
|---|---|---|
| Primary | 16 vCPU, 64 GB, 1 TB NVMe, managed | £1,150 |
| Read replica (Pattern 13) | same spec | £1,150 |
| Backups + storage | 1 TB with PITR | £180 |
| **Subtotal** | | **£2,480/mo** |

Three-year infrastructure: 2,480 × 36 = **£89,280**.

**Engineering time:**

- Initial setup, schema, backup drill, monitoring: 15 engineer-days = £8,175
- Steady-state operations: 0.4 engineer-days/month × 36 = 14.4 days = £7,848
- Upgrades (3 major versions over 3 years, 3 days each): 9 days = £4,905
- **Engineering subtotal: £20,928**

**Three-year total A: 89,280 + 20,928 = £110,208.**

**What it costs in product terms:** search has no typo tolerance and no faceting. Assume that costs **1.5% of conversion** on the search path. With 180,000 orders/month at an average basket of £28, monthly revenue is £5.04 M; search drives ~45% of orders. Lost revenue = 5.04 M × 0.45 × 0.015 = **£34,020/month**, or £1.22 M over three years. *This single line dwarfs the entire infrastructure argument*, which is exactly why Pattern 3 justified Elasticsearch.

### Architecture B — four stores (PG + Redis + Elasticsearch + Cassandra)

The fourth store, Cassandra, is added "for scale" — with no pattern meeting the 20,000 writes/sec threshold. It is the unjustified one, included deliberately.

**Infrastructure, monthly:**

| Item | Spec | Cost |
|---|---|---|
| PostgreSQL primary | 8 vCPU, 32 GB (smaller — sessions and rate limits moved out) | £620 |
| PostgreSQL replica | same | £620 |
| Backups + PITR | 700 GB | £130 |
| Redis | 2 nodes, 13 GB, HA | £310 |
| Elasticsearch | 3 nodes, 8 vCPU, 32 GB | £1,340 |
| Cassandra | 3 nodes, 8 vCPU, 32 GB, 2 TB | £1,480 |
| CDC pipeline (Debezium + Kafka, managed) | small tier | £420 |
| **Subtotal** | | **£4,920/mo** |

Three-year infrastructure: 4,920 × 36 = **£177,120**.

**Engineering time — this is where the real cost is:**

| Activity | Calculation | Days | Cost |
|---|---|---|---|
| Initial setup, 4 stores | 15 + 8 + 20 + 25 | 68 | £37,060 |
| CDC pipeline build (PG → ES, PG → Cassandra) | | 30 | £16,350 |
| Reconciliation job + divergence alerting | | 12 | £6,540 |
| Steady-state ops | 0.4 days/mo per store × 4 stores × 36 mo | 57.6 | £31,392 |
| Upgrades | 9 (PG) + 4 (Redis) + 12 (ES) + 15 (C*) | 40 | £21,800 |
| Incident response | see below | 25.5 | £13,898 |
| Local dev environment maintenance | 2 days/quarter × 12 | 24 | £13,080 |
| Onboarding overhead | 3 extra days × 5 hires | 15 | £8,175 |
| **Engineering subtotal** | | **272.1 days** | **£148,295** |

**Incident-response calculation, shown step by step:**

1. Assume each store generates **1.4 incidents per year** requiring engineer attention (typical for a competently run managed store).
2. Four stores × 1.4 × 3 years = **16.8 incidents**.
3. Assume mean **4.5 engineer-hours** per incident including investigation, fix, and write-up: 16.8 × 4.5 = **75.6 hours**.
4. Add cross-store divergence incidents — unique to polyglot. Assume **2 per year**, at **12 hours** each because diagnosis is much harder: 2 × 3 × 12 = **72 hours**.
5. Total 75.6 + 72 = **147.6 hours** ÷ 7.5 hours/day = **19.7 days**... plus a 30% coordination overhead (handover, comms, follow-ups): 19.7 × 1.3 = **25.6 days** ≈ the 25.5 used above.

**Three-year total B: 177,120 + 148,295 = £325,415.**

### Comparison and the actual conclusion

| | Architecture A (1 store) | Architecture B (4 stores) | Difference |
|---|---|---|---|
| Infrastructure (3 yr) | £89,280 | £177,120 | +£87,840 |
| Engineering (3 yr) | £20,928 | £148,295 | **+£127,367** |
| **Total cost** | **£110,208** | **£325,415** | **+£215,207** |
| Engineering share of the delta | — | — | **59%** |

Step-by-step reading of this result:

1. The infrastructure difference is £87,840 — the number that appears on the cloud bill and the only one most teams discuss.
2. The engineering difference is £127,367 — **1.45× larger**, and entirely invisible on any invoice.
3. Converted to headcount: £127,367 ÷ £120,000 ≈ **1.06 engineer-years**, or roughly **0.35 of an engineer permanently occupied** across the three years. On a team of six, that is 6% of total capacity consumed by having chosen four stores instead of one.
4. But Architecture A's product cost from poor search was **£1.22 M** over three years — 5.7× the entire cost difference. So the correct architecture is neither A nor B.

**Architecture C — the §7.2 answer: PostgreSQL + Redis + Elasticsearch.** Removing Cassandra removes £1,480/mo infrastructure (£53,280), 25 setup days, 12 upgrade days, roughly 12 CDC days, 14.4 ops days, and ~6 incident days — about 69 days or £37,605. Architecture C totals roughly **£234,530**, keeps the £1.22 M of search revenue, and costs £124,322 more than A for a benefit worth nearly ten times that.

**The generalisable rules this arithmetic produces:**

- Engineering time is typically **1.3× to 2× the infrastructure delta** when adding a store. Always price both.
- A store justified by a *revenue-bearing access pattern* (Elasticsearch here) pays for itself many times over. A store justified by *anticipated future scale* (Cassandra here) is pure cost until the anticipated load arrives — and it frequently never does.
- The deciding question is never "can we afford the servers". It is **"which named access pattern fails without this, and what is that failure worth?"**

## 10. Trade-offs

| Decision | What you gain | What you give up | The condition that decides |
|---|---|---|---|
| **Single store (PostgreSQL) vs polyglot** | Atomic transactions across all entities; one thing to operate, back up, monitor, and learn; simple local dev; joins across any entities | Each access pattern gets a *good* engine rather than the *best* one; a single write-scaling ceiling | Whether any named pattern provably exceeds PG's capability within budget. If none does, polyglot is pure cost |
| **Denormalise into documents vs normalise with joins** | Single-read fetch of a whole aggregate; no join cost; natural fit for an API response | Update anomalies — the author's name is copied into 400 book documents and must be updated in all of them; no engine enforces that | Whether the duplicated field is mutable. Immutable duplication (order line price at time of sale) is *correct*; mutable duplication is debt |
| **Schemaless vs enforced schema** | Ship a new field without a migration; genuinely heterogeneous records are natural | Every reader must handle every historical shape forever; bad data is accepted silently and discovered months later in a report | Whether records genuinely differ in shape, or you simply want to avoid migrations. The latter is not a reason |
| **Cache in front of the database** | Order-of-magnitude latency and load reduction on hot reads | A second source of truth that can be stale or wrong; invalidation bugs; cold-start thundering herd (Lesson 29) | Whether you can state the acceptable staleness *in seconds*. If you cannot, do not cache that pattern |
| **Precompute into a table vs query live** | Converts an expensive shape into a point lookup (BookNook pattern 10) | Staleness equal to the recompute interval; a batch job to own and monitor; a backfill problem when the logic changes | Whether the answer may be up to one recompute interval old |
| **Read replica vs new analytical engine** | Isolates heavy scans from OLTP for near-zero extra complexity; same SQL, same schema | Replication lag (typically 10 ms–2 s); row-store scans are still 10–100× slower than columnar for wide aggregates | Rows scanned per query. Under ~100 M, use a replica; well above, columnar earns its keep |
| **Managed service vs self-hosted** | Backups, failover, and patching become someone else's job; faster to adopt a new engine | 2–4× the raw infrastructure cost; less tuning control; version lag | Whether the engineering days saved exceed the premium — compute it with §9's method |
| **Choosing for today's patterns vs anticipated scale** | Simplicity now; the thing you build actually ships | A migration later *if* the growth materialises | Whether the growth is contracted and dated, or merely hoped for. Hoped-for scale is the most expensive thing teams buy |

## 11. Failure scenarios

| Failure | What the user experiences | How it is detected | How it recovers |
|---|---|---|---|
| **Access pattern never enumerated** — "show customers what they viewed last week" lands mid-sprint and needs a shape the schema cannot serve | Feature is quoted as 2 days, takes 3 weeks, or ships with a 4-second page load | Code review, or a p99 latency alert after release | Add the index or precomputed table. Prevent by re-running enumeration (§5 step 12) whenever a feature touches data |
| **Chose wide-column, then query set changed** — Cassandra tables keyed for pattern A; product now needs filter B | Feature is declared "not possible without a migration"; product roadmap bends around the database | Discovered at design time of the new feature | Create a new table with the new partition key and backfill the entire dataset — weeks of work. Prevent by applying criterion 1 honestly |
| **Cross-store divergence** — order committed to PG, CDC message to Elasticsearch dropped | Customer's purchased book never appears in search; order exists but is invisible in an admin search | Only by a reconciliation job comparing row counts and checksums per time bucket. **Monitoring each store's health detects nothing** | Reconciler re-emits missing documents. Without a reconciler, divergence is permanent and silent — the strongest argument against casual polyglot |
| **Dual write partially fails** — PG commit succeeds, Redis write throws | Stale or missing cached data; in write-through designs, a cart that loses an item | Error-rate alert on the Redis call; cache-hit-rate dip | Use the **outbox pattern**: write the intent in the same PG transaction, then a relay publishes it. Never dual-write and hope (Phase 4, Topics 19–21) |
| **Cache treated as source of truth** — session data only in Redis, Redis restarts | Every user logged out simultaneously; support queue floods | Auth-failure spike plus a Redis restart event correlating in time | Short-term: users re-login. Structural: accept it explicitly as a documented risk, or persist sessions with AOF/replication. Decide *before* it happens |
| **Hot partition in a distributed store** — a viral title concentrates writes on one Cassandra partition | Timeouts on one title while the rest of the catalogue is fine; "the site is down" reports that reproduce for nobody | Per-partition latency metrics. Cluster-average metrics look perfectly healthy and will mislead you | Re-key with a bucketing suffix (`isbn#bucket_0..7`) and backfill. Prevent by checking key distribution during design |
| **Analytical query on the primary** — pattern 13 run against the primary by mistake | Checkout p99 jumps from 400 ms to 4 s for ten minutes each night, then recovers | Latency spike correlated with a scheduled job; `pg_stat_activity` shows a long-running query | Move to the replica; set `statement_timeout` on the reporting role so this cannot recur |
| **Unjustified store becomes load-bearing** — Cassandra added "for scale", one small feature now depends on it | Nothing visible — until the one engineer who understood it leaves | Bus-factor review, or an incident nobody can diagnose | Migrate the feature back and decommission. Prevent by writing the *exit criterion* in the decision record (§5 step 11) |
| **Backup never restore-tested** on a secondary store | Catastrophic: data loss discovered during the worst possible incident | Only by an actual restore drill | None, after the fact. A backup that has not been restored is a hypothesis, not a backup. Drill every store quarterly |

## 12. Alternatives

Alternatives to the "enumerate patterns, default to PostgreSQL" method:

| Approach | How it works | Strengths | Weaknesses | Choose it when |
|---|---|---|---|---|
| **Access-pattern-driven with relational default** (this lesson) | Enumerate 7 fields per pattern; default to PG; add stores only against measured failures | Defensible, reversible, cheapest in engineering time; matches how patterns actually arrive | Requires discipline and a day of upfront work; feels slow to teams who want to start coding | Almost always, for products under roughly 50,000 writes/sec |
| **Single-table design (DynamoDB style)** | One table; composite PK encodes multiple entity types; every access pattern is a key-range query, designed up front | Predictable single-digit-ms latency at any scale; costs scale linearly with usage | Query set must be fully known *before* modelling; a new unanticipated shape may require a full remodel and backfill; steep learning curve | Pattern set is genuinely fixed and scale is genuinely extreme — ad-tech, IoT ingestion, large-scale session stores |
| **Data-shape-driven ("our data is JSON")** | Pick the family whose data model matches the data's shape | Fast decision; natural object mapping | Ignores query shape, consistency, and throughput — five of the six criteria. PG's `jsonb` gets you the shape benefit without giving up the rest | Rarely. Use it as one input, never as the decision |
| **Volume-driven ("we'll have lots of data")** | Pick distributed NoSQL above a size threshold | None in practice | Volume is nearly orthogonal to engine fit; leads to distributed systems for datasets that fit in RAM | Never, standalone |
| **Copy a well-known company** | Adopt a reference architecture from a conference talk | Fast; the architecture is at least internally coherent | Imports their access patterns, their scale, their team, and their operational staffing — none of which you have | As a source of *ideas* to evaluate against your own enumeration |
| **Managed multi-model database** (Cosmos DB, ArangoDB, FaunaDB) | One engine exposing document, graph, and key-value APIs | One system to operate while serving several shapes | Jack of all trades: each API is typically weaker than the specialist; vendor lock-in; smaller community when debugging | Mixed shapes, small ops team, and acceptance of lock-in |
| **HTAP / NewSQL** (CockroachDB, TiDB, Yugabyte) | Distributed SQL with horizontal write scaling and transactions | Keeps SQL and transactions past the single-primary ceiling; multi-region writes | Higher per-transaction latency from consensus (Phase 4); smaller ecosystem; more operational unknowns | Write volume genuinely exceeds a shardable single primary *and* transactions are non-negotiable |
| **Build it and refactor later** | Choose anything; migrate when it hurts | Zero upfront analysis; sometimes correct for a throwaway prototype | Migration cost is the single most expensive project type in this lesson; data written in the wrong model is the liability | Genuine prototypes with a known disposal date |

## 13. When to use it

Run the full access-pattern enumeration when:

1. **Starting any new service that owns persistent data.** The cost is one day with a product manager; the cost of skipping it is measured in migration quarters. This is the highest-return hour in system design.
2. **A team proposes adding a new datastore.** The enumeration is the forcing function that turns "we need Cassandra" into "pattern 7 writes at 24,000/sec sustained, we measured PG at 9,000/sec after batching and index reduction, here is the graph". Either the evidence exists or the proposal dissolves.
3. **Before a planned migration.** Migrations driven by frustration ("Mongo is terrible") usually re-encounter the same problems in the new engine because the problem was the data model, not the product. Enumerate first; often the answer is an index, not a migration.
4. **When p99 latency degrades as data grows.** Degradation that tracks data volume means some pattern's cardinality has outgrown its access path. Enumeration localises which one.
5. **During design review of a feature that adds a query shape.** New shapes are how schemas rot. Catching "this needs a scan of 4 M rows" in review costs minutes; catching it in production costs an incident.
6. **When costs grow superlinearly with traffic.** Usually one pattern is doing something pathological — an N+1 query (Lesson 41), an unindexed filter, or a cache with a near-zero hit rate.

Prefer the **PostgreSQL-first default** specifically when: write volume is below roughly 5,000/sec; the team has fewer than ~15 engineers; the query set is still evolving; any pattern requires multi-entity atomicity; or operational maturity in alternatives is low.

## 14. When not to use it

1. **Throwaway prototypes with a known disposal date.** If the code will be deleted in three weeks, use whatever is fastest to start, and say out loud that it is a prototype. The failure mode is prototypes that quietly become production — so set the disposal date in writing.
2. **When the constraint is organisational, not technical.** If your company has a platform team that operates exactly one database well and offers it as a paved road, the operational-maturity criterion dominates all others. A theoretically better engine you must operate alone is usually worse in practice.
3. **When a regulatory or contractual constraint pre-decides it.** Data residency, certification requirements, or a customer contract naming a specific engine removes the choice. Enumerate anyway — but to understand what you will have to work around, not to select.
4. **When the data is not really yours** — you are a thin layer over a system of record (a mainframe, Salesforce, a payment processor). Your job is caching and projection, not storage selection.
5. **When the enumeration becomes the project.** If week three is still spent debating field values for pattern 41, stop. Enumerate the top 15 patterns by criticality, take the default for everything else, and ship. Analysis has a cost too.
6. **For one-off analytical exploration.** Loading a CSV into DuckDB or a notebook to answer a question once requires no architecture.

## 15. Common mistakes

### Beginner mistakes

- **Choosing by data volume.** "200 GB is big" — it is not. A single node handles multi-terabyte datasets. Volume is roughly the least predictive input available.
- **Choosing by data shape alone.** "It's JSON, so document store." `jsonb` in PostgreSQL stores and indexes JSON while keeping joins, transactions, and constraints.
- **Enumerating reads and forgetting writes.** The write rate, write contention, and write atomicity requirements decide more architectures than reads do.
- **Leaving the consistency column blank.** Every unstated consistency requirement becomes a bug. Write "eventual, 5 minutes acceptable" or "read-your-writes required" for every pattern, explicitly.
- **Storing files in the database.** Images and PDFs as `BYTEA` or `BLOB` bloat backups, blow out the buffer cache, and slow replication. Object storage, with the URL in the database.
- **Adding a cache before measuring.** A cache on a pattern running 4 times a minute adds an invalidation bug surface for no gain.
- **Assuming "NoSQL is faster".** A point lookup by primary key is fast in every engine. Engines differ in *which shapes they can serve at all*, not in raw speed for identical shapes.
- **Believing schemaless means no schema.** It means the schema is unenforced and lives, implicitly and inconsistently, in every piece of application code that reads the data.
- **Copying an architecture from a conference talk** without copying the access patterns, scale, or the 40-person storage team that operates it.

### Experienced-engineer mistakes

- **Pricing infrastructure and not engineering time.** §9 shows engineering typically exceeds the infrastructure delta by 1.3–2×. The number that does not appear on an invoice is the larger one.
- **Adding a store for anticipated scale.** Building for 100× today's traffic is usually building for traffic that never arrives, while paying the complexity tax every day until then. Build for roughly 10× and know your next step.
- **Dual-writing to two stores in application code.** It looks like two lines. It is an unsolved distributed-transaction problem. Use the outbox pattern or CDC (Phase 4, Topics 19–21).
- **Shipping a sync pipeline with no reconciler.** CDC will drop or reorder messages eventually. Without a job that detects divergence, your second store is silently wrong and you will not know for months.
- **Designing for a theoretical write rate rather than the measured one.** Load-test the actual pattern with realistic row widths and index counts before concluding that a relational engine cannot cope. Many "PG can't do this" claims die at the first benchmark.
- **Treating the read replica as a free strong-consistency read.** Replicas lag. Serving a read-your-writes pattern from a replica produces the "I saved it and it vanished" bug class.
- **Sharding too early.** Sharding (Topic 16) removes cross-shard joins, cross-shard transactions, and simple `ORDER BY`. Exhaust vertical scaling, index tuning, and read replicas first.
- **Never revisiting the decision.** A store justified in 2023 by a pattern deleted in 2024 is still being paid for, patched, and carried on-call in 2026. Decision records need exit criteria.
- **Accepting a benchmark from a vendor** instead of running your own query shapes on your own data distribution. Benchmarks measure the shapes the vendor chose.

## 16. Interview perspective

Database selection appears in essentially every system-design interview, usually as "what database would you use for this?" — a question most candidates answer far too quickly.

**What strong candidates do:**

1. **Refuse to answer immediately.** "Before I pick, let me enumerate the access patterns." This single sentence separates the top decile. Interviewers are explicitly listening for whether you derive the storage layer or guess it.
2. **Enumerate out loud with structure.** Name the entity, the query shape, the read:write ratio, and the consistency requirement for each. Four or five patterns is plenty under time pressure.
3. **State the default and the burden of proof.** "I'd start with PostgreSQL. It serves patterns 1, 2, 4, and 6 directly. Let me check whether any pattern forces something else."
4. **Justify each additional store with one named pattern.** "Search needs typo tolerance and faceting, which `tsvector` can't rank well — that's my Elasticsearch justification, and it's the only one."
5. **Volunteer the cost of polyglot** before the interviewer raises it: sync, lost transactions, on-call surface, local dev. Naming the cost of your own proposal signals seniority more reliably than proposing the right thing.
6. **Quantify.** "180k orders/month is about 70 writes/sec average, maybe 200 at peak — three orders of magnitude below where I'd consider a distributed write store." Numbers end debates.
7. **Say what would change your mind.** "If search traffic stayed under 10/sec I'd keep it in PG and save the whole pipeline."

**A strong closing line:** *"My default is one PostgreSQL instance, and every additional store has to earn its place by name with a measurement. Here, exactly one did."*

**Red flags interviewers watch for:**

- Naming a database in the first 30 seconds, before any pattern is known.
- "We'll use NoSQL because it scales" — this phrase, unqualified, ends more interviews than any other in this topic.
- Proposing five datastores for a system with 200 writes/sec, with no mention of synchronisation or operational cost.
- Being unable to say *which specific query* the chosen database serves better than the alternative.
- Claiming relational databases "don't scale" without distinguishing read scaling (replicas — easy), write scaling (sharding — hard), and data volume (largely a non-issue).
- Not knowing that PostgreSQL has `jsonb`, full-text search, arrays, and recursive CTEs — it suggests the SQL-versus-NoSQL opinion was formed from blog posts rather than use.
- Writing data to two stores with no mention of atomicity, after Lesson 49's material.
- Treating a cache as a datastore — no TTL, no invalidation story, no answer for "what happens when it restarts?"
- Ignoring the operational question entirely: "who runs this, and have they run it before?"
- Being unable to state an *exit criterion* — if you cannot say when you would remove a store, you cannot say why you added it.

## 17. Knowledge check

**Conceptual**

1. Data volume is described in this lesson as "nearly the least predictive input" for database selection. Explain precisely *why* — and name the three inputs that are genuinely predictive, stating for each what it tells you that volume does not.

2. Explain the difference between a *query shape* and a *query*, and why the abstraction matters when matching an access pattern to a database family. Give one query shape that a wide-column store serves natively and one it effectively cannot, and explain the structural reason for the difference.

3. "Polyglot persistence loses transaction boundaries." Explain what is actually lost, why no mechanism in either store can restore it, and what architectural pattern is used instead.

**Practical scenario**

4. A team runs a ticketing platform on PostgreSQL. Seat inventory, orders, and payments are all in it. A new requirement arrives: when a major event goes on sale, 40,000 users arrive within 90 seconds, each repeatedly refreshing a seat map showing live availability for a venue of 18,000 seats. Current p99 on the seat-map endpoint under that load is 6 seconds; the budget is 400 ms. One engineer proposes moving seat inventory to Cassandra "because it handles write load". Enumerate the access patterns involved using the seven fields of §4.1, state whether the Cassandra proposal addresses the actual constraint, and describe the architecture you would recommend — including precisely what consistency guarantee each read in your design provides, and how a user who sees a seat as available but loses the race is handled.

**Calculation**

5. A company is deciding whether to add a dedicated search engine. Assumptions to use: 9 M documents; current PostgreSQL `tsvector` search p95 is 820 ms against a 300 ms budget; search is used on 38% of sessions; there are 240,000 sessions/month; current conversion on search sessions is 3.1% at an average order value of £46. Industry data (treat as approximate) suggests reducing search latency from 820 ms to 120 ms improves conversion on affected sessions by 7% relative, and adding typo tolerance and faceting improves it by a further 11% relative. The search engine costs £1,340/month in infrastructure, plus a CDC pipeline at £420/month; initial build is 32 engineer-days and ongoing operation is 0.5 engineer-days/month, at a fully loaded rate of £545/engineer-day. Calculate the three-year net position, the break-even month, and state the single assumption to which your answer is most sensitive — showing how much that assumption would have to change to reverse the decision.

**Progress checklist**

Phase 1 — Foundations (Lessons 1–17): **complete**
Phase 2 — Scaling (Lessons 18–39): **complete**

Phase 3 — Data & Storage:
- [x] Topic 1 — Relational databases (Lesson 40)
- [x] Topic 2 — Tables, keys, joins, constraints (Lesson 41)
- [x] Topic 3 — SQL versus NoSQL (Lesson 42)
- [x] Topic 4 — Key-value stores (Lesson 43)
- [x] Topic 5 — Document databases (Lesson 44)
- [x] Topic 6 — Wide-column stores (Lesson 45)
- [x] Topic 7 — Graph databases (Lesson 46)
- [x] Topic 8 — Search engines (Lesson 47)
- [x] **Topic 9 — Selecting a database from access patterns (Lesson 48)** ← you are here
- [ ] Topic 10 — ACID transactions
- [ ] Topic 11 — Isolation levels
- [ ] Topic 12 — Indexing
- [ ] Topic 13 — B-trees versus LSM-trees
- [ ] Topic 14 — Query planning and execution
- [ ] Topic 15 — Replication
- [ ] Topic 16 — Sharding and partitioning
- [ ] Topic 17 — Consistent hashing
- [ ] Topic 18 — The CAP theorem
- [ ] Topic 19 — Consistency models
- [ ] Topic 20 — Quorums and tunable consistency
- [ ] Topic 21 — Data caching strategies
- [ ] Topic 22 — Connection pooling
- [ ] Topic 23 — Backups and point-in-time recovery
- [ ] Topic 24 — Schema migrations and database operations

**NEXT LESSON: Phase 3, Topic 10 — ACID transactions**

---

# Lesson 49 — ACID Transactions

*Phase 3, Topic 10*

## 1. Simple definition

A **transaction** is a group of database operations that the database treats as **one indivisible unit**: either every operation in the group takes effect, or none of them does — and in between, no other user sees a half-finished state.

**ACID** is an acronym for the four guarantees a transactional database makes about that unit:

- **A — Atomicity**: all-or-nothing. The unit cannot partially apply.
- **C — Consistency**: the database moves from one valid state to another valid state, where "valid" means *your declared rules hold* (constraints, foreign keys, checks).
- **I — Isolation**: concurrent transactions do not see each other's unfinished work; the result is as if they ran in some serial order (to the degree your isolation level promises — Topic 11).
- **D — Durability**: once the database says "committed", that fact survives a crash, a power cut, or a process kill.

Terms defined before use:

- **Commit**: the instruction that makes a transaction's changes permanent and visible to others.
- **Rollback / abort**: the instruction that discards everything the transaction did, returning the database to the state before `BEGIN`.
- **Invariant**: a statement about the data that must always be true. "Inventory quantity is never negative." "The sum of all ledger entries for an order equals the order total."
- **Write-ahead log (WAL)**: an append-only file to which the database writes a description of every change *before* applying that change to the main data files. Also called the redo log (MySQL/InnoDB), transaction log (SQL Server), or journal.
- **fsync**: an operating-system call that tells a storage device "do not return until the bytes I wrote are genuinely on persistent media". The crucial word is *genuinely*, and §4.6 explains why that word is doing so much work.

## 2. Why it exists

Consider the single most important operation in BookNook: a customer buys the last copy of a book.

The steps are:

1. Check that `inventory.quantity >= 1` for that ISBN.
2. Decrement `inventory.quantity` by 1.
3. Insert a row into `orders`.
4. Insert rows into `order_lines`.
5. Record the payment capture.

Without transactions, these are five independent writes, and *every gap between them is a place the world can break*:

- The process is killed after step 2 and before step 3. **Stock has vanished.** A copy is gone from inventory and no order exists for it. Nobody will ever notice until a warehouse stock-take months later shows a discrepancy nobody can explain.
- The database crashes after step 3 and before step 5. **An order exists that was never paid for.** The warehouse ships it. BookNook loses £28.
- Two customers execute step 1 at the same moment, both see `quantity = 1`, and both proceed. **The last copy is sold twice.** One customer gets a cancellation email, a refund, and a reason to shop elsewhere.
- A network timeout causes the client to retry the whole sequence. **The customer is charged twice.**

Every one of these is a *real failure that real systems produce* when the five writes are not one unit. The list is not hypothetical; it is the standard inventory of bugs in any system that stores related facts without transactions.

Transactions exist because **correctness under partial failure and under concurrency cannot be achieved by careful application code alone.** You cannot write defensive code that handles "the process was killed between line 14 and line 15", because your code is not running at that moment. Only the storage engine — which controls the ordering of writes to disk and the visibility of uncommitted data — can provide that guarantee. Transactions move the problem from "write perfect code that is never interrupted" to "declare the unit, and let the engine enforce it".

The second reason transactions exist is **economy of reasoning**. Without them, every function that reads data must consider every possible half-applied state that every other function might have left behind. The number of states grows combinatorially. With transactions, you reason about one state before and one state after — a problem a human can actually hold in their head.

## 3. Real-world analogy

**A property purchase completed by a solicitor.**

Buying a house involves many simultaneous facts changing: money leaves the buyer's account, the mortgage is drawn down, the seller's mortgage is redeemed, the title register changes hands, the keys transfer, and stamp duty is paid.

Nobody would agree to do these one at a time with gaps between. "I've transferred the money; the title will move over next Tuesday" is how people lose houses. Instead a solicitor holds everything in escrow and performs **completion**: one moment at which every fact changes together, or nothing does.

- **Atomicity** = completion is all-or-nothing. If the buyer's funds fail to clear at 11 a.m., the title does not move and the keys do not transfer. Everyone returns to exactly where they stood.
- **Consistency** = the solicitor's checks enforce the rules: the seller actually owns the property, there is no undisclosed charge on the title, planning permissions are in order. A completion that would leave the register in an invalid state is refused.
- **Isolation** = while completion is in progress, nobody else can buy the same house. A second buyer's solicitor searching the register does not see a half-transferred title.
- **Durability** = once the change is registered at the Land Registry, it is permanent. The register surviving a fire in the solicitor's office is the whole point of a central register.
- **Rollback** = if completion fails, escrow funds return, and the matter is as if it never happened.
- **Savepoint** = a partial agreement within the chain — "exchange of contracts" — a committed intermediate point you can fall back to without unwinding everything.

**Where the analogy breaks down:**

| Property completion | Database transaction |
|---|---|
| Takes weeks and involves humans who can phone each other | Takes microseconds to milliseconds; nothing can ask a question mid-way |
| One completion at a time, per property, per day | Thousands of transactions per second, overlapping, on overlapping rows |
| The solicitor can pause and resume | A transaction holding locks while it waits blocks others; "pausing" is the single most common production hazard (§4.8) |
| The chain of buyers is a known, finite sequence | Concurrency is unbounded and unpredictable; the engine must handle any interleaving |
| Reversing after completion means a new legal process | Reversing after commit is **impossible**. The only remedy is a *compensating transaction* — a new transaction that does the opposite. This distinction is central to sagas (§4.9) |
| Escrow holds real money that physically exists | Uncommitted writes exist in memory and in the WAL; "holding" them costs locks and memory, which is why long transactions hurt |
| Only one registry | Across two services there is **no shared registry**, and therefore no shared completion. §4.9 explains why distributed transactions essentially do not exist in practice |

## 4. Detailed technical explanation

### 4.1 Atomicity — and the BookNook failure without it

**Definition:** a transaction's effects are applied entirely or not at all. There is no observable or persistent intermediate state after the transaction ends.

**BookNook failure without atomicity:** the process is killed between the inventory decrement and the order insert. Stock drops to 0, no order exists. The book is now unsellable and unshipped — it has fallen out of the system entirely. Reconciliation finds it months later, if ever.

**How it is implemented — mechanically.**

The naive mental model is "the database buffers changes and writes them at commit". That model is wrong and it matters, because it cannot explain how a transaction larger than memory works, nor how recovery works.

What actually happens in a WAL-based engine (PostgreSQL, InnoDB, SQL Server all work this way, with differences in detail):

1. **`BEGIN`** assigns a transaction ID. Nothing is written to disk yet.
2. **Each change** — update a row, insert a row, split a B-tree page — modifies the page *in the shared buffer cache, in memory*, and appends a **WAL record** describing the change to an in-memory WAL buffer.
3. The WAL record contains enough information to **redo** the change (re-apply it to a page that does not have it) and, depending on the engine, enough to **undo** it (revert a page that does have it). PostgreSQL does not use a separate undo log — old row versions remain in the table itself under MVCC (multi-version concurrency control), and are cleaned up later by `VACUUM`. InnoDB uses an explicit undo log segment.
4. **The write-ahead rule**, the single rule that makes this work: *a modified page may not be written to the main data files until the WAL record describing that modification has been flushed to durable storage.* "Log before data", always.
5. **`COMMIT`** appends a commit record to the WAL and **flushes the WAL up to and including that record** (§4.6). Only then does the database tell the client "committed". The modified data pages may still be sitting dirty in memory — that is fine, because the WAL can reconstruct them.
6. **`ROLLBACK`** does *not* need to undo anything on disk in PostgreSQL. It simply writes an abort record; the transaction's row versions are already marked with its transaction ID, which is now known to have aborted, so no reader will ever consider them visible. This is why rollback in PostgreSQL is typically fast regardless of transaction size. In InnoDB, rollback walks the undo log and reverses changes, which *does* scale with transaction size — a large rollback there can take longer than the original work.

**Crash recovery**, which is where atomicity is actually delivered. On restart the engine:

1. Finds the last **checkpoint** — a recorded position in the WAL at which all data pages up to that point were known to be flushed.
2. **Redo phase:** replays every WAL record from the checkpoint forward, re-applying changes to pages that lost them. This brings the data files up to date with everything that was logged, *including changes from transactions that never committed*.
3. **Undo phase:** any transaction that has no commit record in the WAL is known to have been in flight at the crash. Its effects are reverted (InnoDB: via the undo log; PostgreSQL: implicitly, by the fact that its transaction ID is not marked committed in the commit log, so its row versions are invisible to everyone forever).
4. The database opens. Every committed transaction is present; every uncommitted one has vanished. That is atomicity plus durability, delivered together by one mechanism.

**Teaching model versus production reality.** The simplified model above says "redo then undo". Real engines use variants of the **ARIES** algorithm, which adds an analysis pass before redo, supports nested rollbacks via savepoints, uses compensation log records so that an undo that is itself interrupted can be resumed, and interleaves checkpointing with normal operation so the system never stops. The three-phase structure is the right mental model; the production implementations are considerably more intricate.

### 4.2 Consistency — the weakest and most misunderstood letter

**Definition:** a transaction takes the database from one state satisfying all declared integrity rules to another state satisfying all declared integrity rules. If the transaction would violate a rule, it is aborted.

The rules in question are:

- **Primary key uniqueness** — no two orders share an ID.
- **Foreign keys** — `order_lines.isbn` must reference an existing book; you cannot delete a book that has order lines.
- **Check constraints** — `CHECK (quantity >= 0)`, `CHECK (status IN (...))`.
- **Not-null constraints**, **unique constraints**, and **exclusion constraints**.
- **Triggers and assertions** that encode more complex invariants.

**BookNook failure without consistency:** a bug in a new promotions feature writes `quantity = -3` to inventory. The warehouse interface reads it as "3 on back-order" and raises a purchase order to the supplier. Two weeks later BookNook has paid for stock it did not need, because a single `CHECK (quantity >= 0)` was missing. Or: an `order_lines` row references an ISBN that was deleted, so the nightly revenue report crashes on a null join every night for a week.

**Why this letter is weak.** Unlike A, I, and D — which the database provides whether you ask for them or not — **C is mostly something you provide.** The database enforces only the rules *you declare*. If you never write `CHECK (quantity >= 0)`, the database will cheerfully store `-3` and has violated nothing. Several database researchers have argued that C was included in the acronym largely because ACID is pronounceable and ACD is not. That is only half a joke: atomicity, isolation, and durability are properties of the engine, while consistency is a property of your schema design plus the engine's willingness to enforce it.

The engine's genuine contribution to C is real but narrow: it guarantees that your declared constraints are checked **within the transactional boundary**, so a constraint cannot be violated even transiently-visibly by a concurrent transaction, and a violation aborts the whole unit rather than leaving it half-applied.

**The critical clarification — this is NOT the C in CAP.**

State this explicitly, because it is one of the most common confusions in system-design interviews:

| | ACID "Consistency" | CAP "Consistency" |
|---|---|---|
| **What it means** | Application-defined invariants and declared constraints hold | All replicas return the same value; a read sees the most recent completed write (linearizability) |
| **Scope** | One database, one transaction | Multiple replicas or nodes, viewed from outside |
| **Who defines it** | You, via schema constraints | The system's replication protocol |
| **What breaks it** | A missing `CHECK` constraint, a bug in a trigger | Network partition plus a choice to remain available |
| **Relevant lesson** | This one | Phase 3, Topic 18 — The CAP theorem |

They are **different properties that share a word**. A single-node PostgreSQL instance has perfect ACID consistency and the CAP question does not even apply to it, because there are no replicas to disagree. A Cassandra cluster configured with quorum reads and writes can be CAP-consistent for a key while offering almost no ACID consistency, because it enforces no foreign keys or check constraints. Saying "we need consistency" without specifying which one is how teams end up with the wrong database (Lesson 48).

### 4.3 Isolation — introduced, with a forward reference

**Definition:** concurrently executing transactions do not observe each other's intermediate states. The strongest form, **serializable** isolation, guarantees the outcome is identical to *some* serial order of the same transactions.

**BookNook failure without isolation:** two customers buy the last copy simultaneously.

```
t=0ms   Transaction A: SELECT quantity FROM inventory WHERE isbn='X'  → 1
t=1ms   Transaction B: SELECT quantity FROM inventory WHERE isbn='X'  → 1
t=2ms   Transaction A: UPDATE inventory SET quantity = 0 WHERE isbn='X'
t=3ms   Transaction B: UPDATE inventory SET quantity = 0 WHERE isbn='X'
t=4ms   Both commit. Two orders exist. One copy of the book exists.
```

This is a **lost update**: B's write was computed from a value that A had already invalidated. Note carefully that *atomicity did not help here*. Both transactions were perfectly atomic. Both were perfectly durable. The problem is purely one of concurrent visibility, and it is a different guarantee.

Isolation is the most complex letter and carries the most severe performance trade-offs, because stronger isolation means more locking or more aborted transactions. Databases therefore offer a *ladder* of isolation levels — read uncommitted, read committed, repeatable read, serializable — each permitting specific anomalies (dirty reads, non-repeatable reads, phantom reads, write skew) in exchange for more throughput. Crucially, **most databases do not default to serializable**; PostgreSQL and SQL Server default to read committed, and MySQL/InnoDB to repeatable read, which means the lost update above is possible by default unless you take explicit locks.

**Phase 3, Topic 11 — Isolation levels** covers the full ladder, each anomaly, how MVCC implements the levels without read locks, and how to choose a level per transaction. For this lesson, retain two facts: isolation is a *separate* guarantee from atomicity, and the default is weaker than you probably assume.

The immediate practical fix for the BookNook race, which Topic 11 will generalise, is to take an explicit row lock on read:

```sql
SELECT quantity FROM inventory WHERE isbn = 'X' FOR UPDATE;
```

`FOR UPDATE` makes the second transaction *wait* at the `SELECT` until the first commits, at which point it reads `0` and correctly refuses the sale.

### 4.4 Durability — and the BookNook failure without it

**Definition:** once the database returns success for a `COMMIT`, that transaction's effects survive any subsequent crash of the database process, the operating system, or the machine's power supply.

**BookNook failure without durability:** a customer completes checkout; the payment processor charges the card; the database says "committed"; the server loses power 40 milliseconds later. On restart, the order is not there. BookNook has taken £28 and has no record of what to ship. The customer has a card statement and an email confirmation — and support has nothing to match them against. This failure is worse than losing the sale, because it destroys trust and creates a manual reconciliation burden.

### 4.5 How durability is implemented

**Step 1 — the WAL.** As in §4.1, the change description is appended to the WAL before the data pages are written. The WAL is a **sequential append**, which is the fastest possible disk access pattern; the data pages are scattered random writes, which are the slowest. Durability therefore costs one sequential flush, not many random ones. This is the central performance insight of WAL design.

**Step 2 — fsync at commit.** Appending to the WAL file writes into the OS page cache, which is *memory*. If the machine loses power, the page cache is gone. `fsync()` (or `fdatasync`, or `O_DSYNC` writes) forces those bytes down to the device. PostgreSQL's `wal_sync_method` selects which call is used.

**Step 3 — group commit.** An fsync is expensive — roughly 0.5–1.5 ms on an enterprise NVMe SSD with power-loss protection, roughly 5–10 ms on a spinning disk (both approximate). If every transaction fsyncs individually, your commit rate is capped at `1 / fsync_latency` — about 1,000/sec at 1 ms, regardless of how many CPU cores you have.

**Group commit** fixes this. The engine briefly delays a committing transaction (PostgreSQL: `commit_delay`, default 0; MySQL has an analogous binlog group commit) so that other transactions committing in the same window share **one** fsync. Thirty transactions commit; one fsync happens; all thirty are durable. Throughput becomes `transactions_per_group / fsync_latency`, and the cost is a small increase in individual commit latency. §9 works this through numerically.

**Step 4 — replication, for durability beyond one machine.** An fsync protects against process and power loss on *that machine*. It does not protect against the disk failing or the datacentre flooding. **Synchronous replication** extends durability: the commit is not acknowledged until at least one replica has also received (and optionally flushed) the WAL record. The cost is the network round trip added to every commit — typically 0.5–2 ms within an availability zone, 30–80 ms across continents. Topic 15 covers replication properly.

### 4.6 What `fsync=off` actually risks, and why disks lie

PostgreSQL has a setting `fsync = off`. It makes benchmarks look spectacular — commit throughput often rises 10–50×, because the commit path no longer waits for the device at all.

**What it actually means:** the database writes WAL records into the OS page cache and returns "committed" immediately. The data reaches the physical device whenever the OS decides to flush, typically within 5–30 seconds.

**What you risk:** not merely "losing the last few seconds of transactions". That would be a *bounded* loss and might be acceptable for some workloads. The real risk is **unrecoverable corruption of the entire database**. Here is why: the write-ahead rule (§4.1) requires that a WAL record reach durable storage before the corresponding data page does. With `fsync=off`, the OS may flush pages in any order it likes. It can write a modified *data page* to disk while the *WAL record* describing it is still in the page cache. If power is lost at that instant, you have a data file containing a change that the WAL knows nothing about. Recovery cannot redo it (no record) and cannot undo it (no record). The data file is now internally inconsistent in a way the engine cannot detect or repair: a B-tree with a page split half-applied, pointing into nowhere. The symptom is not "I lost 10 seconds"; it is "the database will not start" or, worse, "the database starts and silently returns wrong answers".

**`fsync=off` is acceptable** in exactly one situation: a database whose entire contents you are willing to delete and rebuild from scratch — CI pipelines, throwaway test fixtures, a bulk-load into a staging instance that you will re-run on failure. It is never acceptable for anything holding data you want to keep.

A safer middle setting is `synchronous_commit = off`. This keeps the write-ahead ordering guarantee (so the database cannot corrupt) but does not wait for the fsync before acknowledging. You lose a bounded window of recent commits on a power failure — typically up to `wal_writer_delay × 3`, around 600 ms by default — and the database still starts cleanly. This is a *legitimate, tunable* durability/latency trade-off, and it can be set per-transaction: strong durability for checkout, relaxed for analytics event ingestion.

**Why disks lie about flushing.** This is the part that surprises people.

Storage devices contain a **volatile write cache** — DRAM on the drive itself, typically 64–256 MB — used to coalesce writes and report completion early. When the operating system issues a flush command, a correctly behaving device writes its cache to persistent media before acknowledging. But:

1. **Consumer SSDs and some cheap drives acknowledge the flush immediately**, while data is still in volatile cache, because it makes their benchmark numbers better and the failure only shows up on sudden power loss. This is sometimes described as "lying about fsync", and it has been documented repeatedly in storage research and in post-mortems.
2. **Virtualisation and storage layers add more caches.** A hypervisor, a RAID controller with a battery-backed cache, a network storage protocol, or a container filesystem each may or may not honour flush semantics end to end. A single misconfigured layer silently voids durability for everything above it.
3. **Enterprise drives with power-loss protection (PLP)** contain capacitors with enough stored energy to flush the volatile cache to NAND after power is cut. These drives can honour a flush *and* be fast, which is exactly what you are paying for. PLP is the single specification to look for when buying storage for a database.
4. **RAID controllers with a battery-backed or flash-backed write cache** achieve the same effect at the controller level, and are why enterprise servers have long tolerated fast fsyncs.

**The practical consequence:** your database's durability guarantee is only as strong as the *weakest* layer between it and the physical medium. PostgreSQL ships a tool, `pg_test_fsync`, that measures fsync latency; a suspiciously fast result on consumer hardware — say 0.02 ms — is strong evidence that something in the stack is not actually flushing. On managed cloud databases the provider is responsible for this layer, which is one of the under-appreciated things you are buying.

### 4.7 The transaction lifecycle, and savepoints

```
BEGIN;                  -- start the unit; a transaction ID is assigned
  ... statements ...    -- changes are visible only to this transaction
COMMIT;                 -- make permanent and visible (flush WAL, then acknowledge)
  -- or --
ROLLBACK;               -- discard everything since BEGIN
```

Three details that matter in production:

**Autocommit.** Most clients run in autocommit mode: a statement issued outside an explicit `BEGIN` is wrapped in its own single-statement transaction. So you are *always* in a transaction; the question is only whether its boundary is where you want it. A loop issuing 10,000 inserts in autocommit performs 10,000 commits and 10,000 fsyncs. Wrapping the loop in one transaction performs one — often a 20–50× speed-up, and the most common easy win in data-loading code.

**Errors inside a transaction.** In PostgreSQL, once any statement in a transaction raises an error, the transaction enters an *aborted* state and every subsequent statement fails with `current transaction is aborted, commands ignored until end of transaction block`. You must `ROLLBACK` (or roll back to a savepoint). Other engines differ — MySQL may leave the transaction usable after some errors — which makes error handling non-portable and is a frequent source of ORM bugs.

**Savepoints** are named markers inside a transaction that you can roll back to without abandoning the whole unit:

```sql
BEGIN;
  INSERT INTO orders (...) VALUES (...);          -- must succeed

  SAVEPOINT loyalty_points;                        -- mark a fallback position
  UPDATE customers SET points = points + 28        -- optional, nice-to-have
    WHERE id = 4182;
  -- if this fails (say a check constraint on a points cap):
  ROLLBACK TO SAVEPOINT loyalty_points;            -- undo ONLY the points update;
                                                   -- the order insert survives
COMMIT;                                            -- the order is committed
```

Savepoints express "this sub-step is optional". They are also how ORMs implement nested transactions — there is no true nesting in PostgreSQL, so a "nested transaction" in your framework is a savepoint. They are not free: each savepoint consumes a subtransaction ID, and a transaction creating tens of thousands of them can trigger severe performance degradation (the `subtransaction overflow` problem, where the per-backend cache of 64 subtransaction IDs spills to shared memory and forces every other backend to do extra work on every visibility check). Use savepoints deliberately; do not generate them in a loop.

### 4.8 Long-running transactions as an operational hazard

A transaction that stays open for a long time — because it is waiting on an HTTP call, because a developer opened `psql` and typed `BEGIN;` before lunch, because a batch job processes a million rows in one unit — causes four distinct problems. This is one of the top causes of production database incidents, and it is almost never anticipated.

**Problem 1 — it holds locks.** Every row the transaction has modified is locked against modification by others until it commits or rolls back. A transaction that updated one popular row and then waited 90 seconds on a payment API has blocked every other transaction touching that row for 90 seconds. Those transactions hold their own locks while waiting, so the block propagates. This is how a single slow external call becomes a site-wide outage.

**Problem 2 — it blocks `VACUUM` (PostgreSQL specifically, with analogues elsewhere).** Under MVCC, an `UPDATE` does not overwrite a row; it writes a new version and leaves the old one. `VACUUM` reclaims old versions once no transaction could still need them. But an open transaction might still need any version that existed when it started — so **`VACUUM` cannot remove any row version newer than the oldest open transaction's snapshot, anywhere in the database.** One idle transaction open for six hours prevents cleanup of dead rows in *every* table for six hours.

**Problem 3 — bloat.** The consequence of problem 2. Dead row versions accumulate in table and index files. A table holding 2 GB of live data can grow to 40 GB of file space. Every sequential scan now reads 40 GB. The buffer cache fills with dead rows, so cache hit rate collapses. Query plans that were fine become slow. And the space is not returned to the OS by ordinary `VACUUM` — only `VACUUM FULL` (which takes an exclusive lock and rewrites the table) or tools like `pg_repack` reclaim it. Bloat is insidious because it degrades performance gradually, so it is usually diagnosed as "the database is getting slower" rather than as a transaction-duration problem.

**Problem 4 — transaction ID wraparound risk.** PostgreSQL transaction IDs are 32-bit and wrap around after about 2 billion. `VACUUM` is also responsible for "freezing" old rows so wraparound is safe. A very long transaction blocks freezing; if the margin is exhausted, PostgreSQL refuses new writes entirely to protect the data — a full write outage requiring a single-user-mode vacuum to resolve. Rare, but catastrophic, and several well-known public outages have had this cause.

**The rules that prevent all four:**

- **Never call an external service inside a transaction.** Call the payment API first, obtain a result, *then* open a transaction to record it. If the API call must be atomic with the database write, use the outbox pattern (§4.9), not a long transaction.
- **Set `idle_in_transaction_session_timeout`** (PostgreSQL) to something like 30 seconds. This kills sessions that open a transaction and then go idle — the `psql`-before-lunch case — automatically.
- **Set `statement_timeout`** per role: short for the web application (say 5 s), longer for reporting roles.
- **Batch large jobs into many small transactions.** Deleting 50 M rows should be 50,000 transactions of 1,000 rows each, with a brief pause, not one transaction. The job takes slightly longer in wall-clock time and does not take the site down.
- **Monitor the oldest transaction age** as a first-class metric, alerting above a threshold such as 60 seconds. In PostgreSQL: `SELECT max(now() - xact_start) FROM pg_stat_activity WHERE state <> 'idle';`

### 4.9 Transactions across services do not exist

Here is a statement to internalise: **there is no `COMMIT` that spans two independently deployed services with separate databases.** Not "it is hard". It does not exist as an available primitive in normal architectures.

The theoretical mechanism is **two-phase commit (2PC)**: a coordinator asks every participant "can you commit?", collects unanimous yes votes, then tells everyone to commit. The XA standard specifies it and some databases implement it (`PREPARE TRANSACTION` in PostgreSQL). It is nonetheless avoided in service architectures for concrete reasons:

- **It is blocking.** If the coordinator crashes after participants vote yes but before it issues the commit, every participant sits holding locks, unable to decide, until the coordinator returns. This is not a tuning problem; it is a proven property of the protocol.
- **Availability multiplies downward.** The transaction can only commit if *every* participant is reachable. Five services at 99.9% give roughly 99.5% for the combined operation.
- **Latency is the sum of the slowest paths**, with locks held throughout — directly contradicting §4.8.
- **Many systems cannot participate.** A third-party payment API has no XA interface. You cannot two-phase-commit a credit card charge.

**What replaces it: the saga.** A **saga** is a sequence of *local* transactions, each in one service, where each step has a defined **compensating action** that semantically undoes it. If step 4 fails, you execute the compensations for steps 3, 2, and 1 in reverse.

BookNook checkout as a saga across services:

```
Step 1  Order service     : create order, status=PENDING   | Compensate: mark CANCELLED
Step 2  Inventory service : reserve 1 copy                 | Compensate: release reservation
Step 3  Payment service   : charge card                    | Compensate: issue refund
Step 4  Order service     : mark order PAID                | Compensate: n/a (terminal)
```

The essential differences from a transaction, which you must design around rather than wish away:

- **There is no isolation.** Mid-saga states are *visible* to everyone. Another customer may see the reserved copy as unavailable before the payment succeeds, and available again after a compensation. Your product must define what users see in these windows.
- **Compensation is not rollback.** A refund is not the erasure of a charge; it is a second, visible financial event. The customer sees both on their statement. The audit trail records both. Some actions have no compensation at all — you cannot un-send an email, so you order the saga to put irreversible steps last.
- **Every step must be idempotent**, because retries are unavoidable. Step 3 must carry an idempotency key so that a retried charge does not become a second charge.
- **Someone must drive the saga to completion.** Either an orchestrator service owns the state machine, or each service reacts to events (choreography). Either way it is real infrastructure with real failure modes.

**Phase 4, Topics 19–21** cover sagas, the outbox pattern, and idempotency in full: orchestration versus choreography, how to persist saga state, and how the outbox pattern lets you atomically commit a database change *and* the intent to publish an event using one local transaction.

The design conclusion that connects to Lesson 48: **entities that must change atomically should live in the same database, owned by the same service.** Every service boundary you draw through a required atomic operation converts a `COMMIT` into a saga, and a saga is perhaps fifty times more code and vastly more failure modes. Draw boundaries along transaction boundaries, not across them.

### 4.10 What NoSQL databases actually offer

"NoSQL doesn't have transactions" was roughly true in 2010 and is substantially wrong today. The accurate picture is a spectrum, and knowing where each engine sits is directly useful.

| Engine | What is atomic | What is not | Notes |
|---|---|---|---|
| **Redis** (Lesson 43) | Each command. `MULTI/EXEC` runs queued commands with no other command interleaved. Lua scripts are atomic | **No rollback.** If a command inside `MULTI/EXEC` fails, earlier ones still applied. `WATCH` gives optimistic check-and-set, not real transactions | Single-threaded execution gives isolation almost for free; durability depends on AOF/RDB configuration and is often off by default |
| **MongoDB** (Lesson 44) | A single document write is atomic, including nested sub-documents and arrays. Since 4.0 (replica sets) and 4.2 (sharded), **multi-document ACID transactions** are supported | Multi-document transactions carry real cost: a default 60-second limit, higher latency, and more aborts under contention | The idiomatic answer remains to model the aggregate as one document so you need only single-document atomicity. Reach for multi-document transactions as an exception |
| **DynamoDB** | Single-item writes atomic. `TransactWriteItems` gives ACID across up to 100 items | Costs **2× the write capacity** of a normal write; items must be in one account and region; no long-running transactions | Conditional writes (`ConditionExpression`) handle most cases more cheaply than transactions |
| **Cassandra** (Lesson 45) | A write to one partition is atomic and isolated. Batches within a partition are atomic | Multi-partition batches are atomic (all eventually apply) but **not isolated** — readers can see some rows of the batch and not others. Logged batches are often slower than individual writes | **Lightweight transactions (LWT)**: `INSERT ... IF NOT EXISTS` / `UPDATE ... IF col = value` use Paxos consensus for linearizable compare-and-set on a single partition. Typically **4 round trips**, so roughly 4× the latency of a normal write. Correct for "claim this username", wrong for a hot path |
| **Couchbase, Riak, etc.** | Per-document atomicity; varying optimistic concurrency via CAS tokens | No general cross-document transactions in most | |
| **Distributed SQL** (CockroachDB, Spanner, TiDB, Yugabyte) | Full ACID transactions across a distributed cluster | Commit latency includes consensus round trips — typically 1–10 ms within a region, more across regions | This category exists precisely to keep ACID past the single-primary write ceiling discussed in Lesson 48 |

The decision rule: **if your invariant spans multiple records that can be written concurrently, you need either multi-record transactions or a data model that collapses them into one record.** Picking a store without that capability and planning to "handle it in the application" means reimplementing concurrency control yourself, in a language without the primitives, against a store that will not help you. That approach has a near-perfect record of failure.

## 5. Request / data flow

A BookNook checkout, from the HTTP request to a durable commit, showing exactly where each ACID property is delivered.

1. **Request arrives.** `POST /orders` with a cart ID, a payment token, and an **idempotency key** generated by the client. The key is essential: if the client retries after a timeout, this is what prevents a second order.
2. **Idempotency check, outside the transaction.** The application looks up the key in an `idempotency_keys` table. If a completed record exists, it returns the stored response immediately. No transaction, no charge, no duplicate.
3. **Payment is authorised first — outside any transaction.** The call to the payment provider takes 300–900 ms. Holding a database transaction open across it would violate every rule in §4.8. The result is an authorisation token, not yet captured.
4. **`BEGIN`.** The engine assigns a transaction ID and establishes an MVCC snapshot. Nothing has touched the disk.
5. **Lock inventory rows deterministically.** `SELECT ... FROM inventory WHERE isbn = ANY($1) ORDER BY isbn FOR UPDATE`. Two things are happening: `FOR UPDATE` provides **isolation** against the concurrent-oversell race (§4.3), and `ORDER BY isbn` imposes a global lock ordering so two carts containing the same two books cannot deadlock by acquiring them in opposite orders.
6. **Validate availability in application code.** If any requested quantity exceeds stock, `ROLLBACK` and return a clear error. No partial order exists — **atomicity**.
7. **Decrement inventory.** Each `UPDATE` modifies a page in the buffer cache and appends a WAL record to the WAL buffer. The `CHECK (quantity >= 0)` constraint is evaluated now — **consistency**. A violation aborts the whole transaction.
8. **Insert the order row and order-line rows.** Foreign keys to `customers` and `books` are verified — consistency again. More WAL records accumulate.
9. **Insert the payment record** holding the authorisation token, and **insert the idempotency key record** with the response payload. Both are inside the same transaction, so the idempotency record cannot exist without the order, nor the order without it.
10. **Insert an outbox row** describing the `OrderPlaced` event. Because this is in the same transaction, the event cannot be published for an order that did not commit — the problem dual writes cannot solve (Phase 4, Topics 19–21).
11. **`COMMIT` issued.** The engine appends a commit record to the WAL.
12. **WAL flush.** The WAL buffer is written and `fsync`ed up to and including the commit record. If group commit is active, this transaction's flush is shared with others committing in the same window — **durability**, amortised (§4.5).
13. **Synchronous replica acknowledgement, if configured.** The primary waits for a standby to confirm receipt of the WAL up to that position. Durability now survives loss of the entire primary machine.
14. **The engine acknowledges the commit** to the application. Only at this instant is the order real. Everything before step 14 could still have evaporated; nothing after it can.
15. **Capture the payment**, using the authorisation from step 3. Deliberately after the commit: if capture fails now, there is a committed order in a `payment_failed` state that a background job can retry or cancel — a recoverable state. Had capture happened before the commit and the commit then failed, money would have moved with no record of why.
16. **Relay publishes the outbox event.** A separate process polls the outbox table (or reads the WAL via change-data-capture) and publishes `OrderPlaced` to the message bus, marking the row as sent. At-least-once delivery, so consumers must be idempotent.
17. **Downstream consumers react**: the warehouse service schedules a pick, the search index is updated, the confirmation email is sent. None of these is in the transaction, because none of them can be rolled back.
18. **On any crash between steps 4 and 14**, recovery (§4.1) finds no commit record, discards everything, and the database contains no trace of the attempt. The client's retry hits step 2, finds no completed idempotency record, and safely tries again.

## 6. ASCII diagram

```
        BOOKNOOK CHECKOUT — WHERE EACH ACID PROPERTY IS DELIVERED

 CLIENT          APPLICATION                 DATABASE ENGINE              DISK
   │                  │                            │                       │
   │ POST /orders     │                            │                       │
   │ + idempotency-key│                            │                       │
   ├─────────────────►│                            │                       │
   │                  │ check idempotency table    │                       │
   │                  ├───────────────────────────►│                       │
   │                  │◄─────── not seen before ───┤                       │
   │                  │                            │                       │
   │                  │ ┌────────────────────────┐ │                       │
   │                  │ │ PAYMENT AUTH  ~600 ms  │ │                       │
   │                  │ │ OUTSIDE the transaction│ │                       │
   │                  │ │ (never hold locks over │ │                       │
   │                  │ │  a network call §4.8)  │ │                       │
   │                  │ └────────────────────────┘ │                       │
   │                  │                            │                       │
   │                  │ BEGIN                      │                       │
   │                  ├───────────────────────────►│ assign xid, snapshot  │
   │                  │                            │                       │
   │                  │ SELECT..FOR UPDATE         │  ╔═══════════════╗    │
   │                  │   ORDER BY isbn            │  ║  ISOLATION    ║    │
   │                  ├───────────────────────────►│  ║ row locks +   ║    │
   │                  │                            │  ║ MVCC snapshot ║    │
   │                  │                            │  ║ (Topic 11)    ║    │
   │                  │                            │  ╚═══════════════╝    │
   │                  │ UPDATE inventory -1        │                       │
   │                  ├───────────────────────────►│ page→buffer cache     │
   │                  │                            │ WAL rec→WAL buffer    │
   │                  │                            │  ╔═══════════════╗    │
   │                  │                            │  ║ CONSISTENCY   ║    │
   │                  │                            │  ║ CHECK(qty>=0) ║    │
   │                  │                            │  ║ FK, UNIQUE    ║    │
   │                  │                            │  ╚═══════════════╝    │
   │                  │ INSERT orders              │                       │
   │                  │ INSERT order_lines         │                       │
   │                  │ INSERT idempotency_key     │                       │
   │                  │ INSERT outbox (event)      │                       │
   │                  ├───────────────────────────►│ more WAL records      │
   │                  │                            │                       │
   │                  │ COMMIT                     │                       │
   │                  ├───────────────────────────►│ append COMMIT record  │
   │                  │                            ├──── write + fsync ───►│
   │                  │                            │  ╔═══════════════╗    │
   │                  │                            │  ║  DURABILITY   ║    │
   │                  │                            │  ║ group commit: ║    │
   │                  │                            │  ║ N txns share  ║    │
   │                  │                            │  ║ ONE fsync     ║    │
   │                  │                            │  ╚═══════════════╝    │
   │                  │◄───── committed ───────────┤◄───── durable ────────┤
   │                  │                            │                       │
   │                  │ ╔════════════════════════╗ │                       │
   │                  │ ║ ATOMICITY: a crash     ║ │                       │
   │                  │ ║ ANYWHERE above this    ║ │                       │
   │                  │ ║ line leaves NO trace.  ║ │                       │
   │                  │ ║ Recovery finds no      ║ │                       │
   │                  │ ║ COMMIT record → undo   ║ │                       │
   │                  │ ╚════════════════════════╝ │                       │
   │                  │ capture payment (after)    │                       │
   │◄─────────────────┤ 201 Created                │                       │
   │                  │                            │                       │
   │          relay ──┴──► outbox → message bus → warehouse / search / email
   │                        (at-least-once; consumers MUST be idempotent)


        WAL MECHANICS — WHY "LOG BEFORE DATA" IS THE WHOLE TRICK

   memory │  buffer cache (dirty pages)        WAL buffer
          │  ┌────┐ ┌────┐ ┌────┐              ┌──────────────────┐
          │  │ p7 │ │p12 │ │p40 │              │ rec rec rec COMMIT│
          │  └─┬──┘ └─┬──┘ └─┬──┘              └────────┬─────────┘
   ───────┼────┼──────┼──────┼───────────────────────────┼──────────
    disk  │    │      │      │   (later, lazily)         │ fsync NOW
          │    ▼      ▼      ▼                           ▼
          │  ┌──────────────────┐              ┌──────────────────┐
          │  │   DATA FILES     │   ◄── never  │   WAL (sequential │
          │  │ (random writes,  │     ahead of │    append, fast)  │
          │  │  slow, lazy)     │     the WAL  │                   │
          │  └──────────────────┘              └──────────────────┘

   RECOVERY:  find checkpoint → REDO all WAL records forward
                             → UNDO transactions with no COMMIT record
   fsync=off: the OS may flush a DATA page before its WAL record.
              Crash at that moment = a change with no log entry =
              UNRECOVERABLE CORRUPTION, not merely "lost seconds".
```

## 7. Detailed example

### 7.1 BookNook — exactly what breaks without a transaction

BookNook sells *The Midnight Library*, ISBN `9780525559474`. There is **one copy left**. Two customers, Priya and Marcus, press "Buy now" 40 milliseconds apart.

**Version A — no transaction, no locking.** The application performs five independent statements.

| Wall clock | Priya's request (app server 1) | Marcus's request (app server 2) | Database state |
|---|---|---|---|
| 0 ms | `SELECT quantity` → **1** | | qty=1, orders=0 |
| 12 ms | checks `1 >= 1` → proceed | | qty=1 |
| 40 ms | | `SELECT quantity` → **1** | qty=1 |
| 44 ms | | checks `1 >= 1` → proceed | qty=1 |
| 180 ms | payment auth OK (£8.99) | | qty=1 |
| 190 ms | `UPDATE inventory SET quantity = 0` | | **qty=0** |
| 210 ms | | payment auth OK (£8.99) | qty=0 |
| 215 ms | `INSERT orders` → order 88301 | | qty=0, orders=1 |
| 220 ms | | `UPDATE inventory SET quantity = 0` | qty=0 (already 0; the write is a no-op that *looks* successful) |
| 231 ms | | `INSERT orders` → order 88302 | **qty=0, orders=2** |
| 240 ms | 201 Created, email sent | | |
| 250 ms | | 201 Created, email sent | |

**Outcome:** one physical book, two paid orders, two confirmation emails. Note the detail that makes this so hard to catch in testing: Marcus's `UPDATE ... SET quantity = 0` *succeeded*. The application has no error to log. The bug is completely silent and surfaces three days later when the warehouse cannot pick order 88302. Cost: a refund, a support interaction, a customer-trust hit, and a stock-reconciliation task.

Now the second failure mode, in the same codebase.

**Version B — no transaction, process killed mid-sequence.** A deploy rolls the pods during Priya's checkout.

| Wall clock | Priya's request | Database state |
|---|---|---|
| 0 ms | `SELECT quantity` → 1 | qty=1, orders=0 |
| 180 ms | payment auth OK, card charged £8.99 | qty=1 |
| 190 ms | `UPDATE inventory SET quantity = 0` | **qty=0** |
| 196 ms | **SIGKILL — pod terminated mid-deploy** | qty=0, orders=0 |

**Outcome:** £8.99 taken, no order, and a copy of the book now invisible to the catalogue because stock reads zero. Two separate losses from one interruption: a charged customer with no order, and an unsellable book. Neither produces an error log, because the process that would have logged it no longer exists.

**Version C — with a transaction and `FOR UPDATE`.** Same two concurrent customers.

| Wall clock | Priya (txn A) | Marcus (txn B) | Database state |
|---|---|---|---|
| 0 ms | payment auth OK (outside txn) | | qty=1 |
| 180 ms | `BEGIN` | | qty=1 |
| 182 ms | `SELECT ... FOR UPDATE` → **1**, **row now locked** | | qty=1, lock held by A |
| 210 ms | | payment auth OK (outside txn) | |
| 215 ms | | `BEGIN` | |
| 216 ms | | `SELECT ... FOR UPDATE` → **BLOCKS** waiting for A | B is waiting |
| 218 ms | `UPDATE inventory SET quantity = quantity - 1` | *(still blocked)* | qty=0 in A's uncommitted view |
| 222 ms | `INSERT orders` → 88301 | *(still blocked)* | |
| 226 ms | `COMMIT` → WAL flushed, lock released | | **qty=0, orders=1, durable** |
| 227 ms | | `SELECT ... FOR UPDATE` unblocks → reads **0** | qty=0 |
| 228 ms | | application sees `0 < 1` → `ROLLBACK` | qty=0, orders=1 |
| 230 ms | | void the payment authorisation | |
| 232 ms | | `409 Conflict: out of stock` | |

**Outcome:** one book, one order, one charge. Marcus's authorisation is voided rather than captured, so no money ever moved. He sees an honest, immediate "out of stock" message rather than a confirmation followed by a cancellation.

Three mechanics did the work here, and each is a different ACID letter: `FOR UPDATE` made B wait (**isolation**); the `COMMIT` made A's state permanent (**durability**); and the `ROLLBACK` meant B's partial work left no trace (**atomicity**). The `CHECK (quantity >= 0)` constraint is a fourth line of defence (**consistency**) — if a future code path forgets the `FOR UPDATE`, the constraint still refuses to let stock go negative, converting a silent data-corruption bug into a loud, catchable error.

A note on `quantity = quantity - 1` versus `quantity = 0`: the first form computes the new value *inside the database* from the current value, so it is correct even without the lock in some isolation levels. The second form carries a stale value read earlier and is a lost update waiting to happen. Prefer relative updates for counters.

### 7.2 Contrasting example — a bank ledger and double-entry bookkeeping

A payments company runs an internal ledger. Every movement of money is recorded as **double entry**: each transaction writes two or more rows that must sum to exactly zero. Transferring £500 from account A to account B writes `-50000` (pence) against A and `+50000` against B.

The invariant is absolute: **the sum of all ledger entries, globally, is always zero.** If that is ever untrue, the company's books do not balance, and that is a regulatory event, not a bug report.

What each ACID property delivers here, and what the failure looks like without it:

- **Without atomicity**: the debit commits, the crash occurs, the credit never happens. £500 has been destroyed. The global sum is now `-50000`. Every subsequent reconciliation fails, and finding the offending transaction among millions requires a full audit.
- **Without consistency**: no `CHECK` ensuring entries reference a valid account, so a typo posts £500 to account `0`. The money exists but belongs to nobody.
- **Without isolation**: two concurrent withdrawals both read a balance of £500 and both succeed, overdrawing an account that has an explicit no-overdraft rule. This is the BookNook oversell, with money instead of books.
- **Without durability**: the customer's app shows the transfer completed; a power failure in the datacentre; on restart the transfer is gone but the recipient has already been notified.

The interesting structural difference from BookNook: the ledger is **append-only and immutable**. Rows are never updated or deleted, only inserted. That removes a whole class of lost-update problems — there is nothing to overwrite — but it does *not* remove the need for transactions, because the atomicity of the multi-row insert is exactly the invariant. It also means balances are derived (`SUM` of entries, or a periodically materialised snapshot) rather than stored, which trades a cheap read for a guaranteed-correct one.

The contrast with BookNook's inventory is instructive: BookNook can tolerate a rare oversell by apologising and refunding; a ledger cannot tolerate a single imbalance under any circumstances. **The strictness of the business invariant, not the data volume, is what determines how much transactional machinery you need** — the same lesson as Lesson 48, arriving from a different direction.

## 8. Code or configuration example

**Illustrative teaching code, not production-ready.** It omits connection pooling, retry-with-backoff on serialisation failures, structured logging, metrics, timeouts, authorisation, and input validation. Error handling is deliberately simplified to keep the transactional structure visible.

```sql
-- =====================================================================
-- The schema's job is to make incorrect states UNREPRESENTABLE.
-- Every constraint below is an invariant the database will enforce even
-- if every line of application code is wrong. This is the "C" in ACID:
-- the engine enforces only what you declare, so declare everything.
-- =====================================================================

CREATE TABLE inventory (
    isbn     TEXT PRIMARY KEY,
    quantity INT NOT NULL
        CHECK (quantity >= 0)      -- The last line of defence against overselling.
                                   -- Even if a future code path forgets FOR UPDATE,
                                   -- this turns a silent corruption into a loud error.
);

CREATE TABLE orders (
    id              BIGSERIAL PRIMARY KEY,
    customer_id     BIGINT      NOT NULL REFERENCES customers(id),
    status          TEXT        NOT NULL
        CHECK (status IN ('pending','paid','payment_failed','cancelled')),
                                   -- Enumerating states in the schema means a typo
                                   -- like 'Paid' is rejected at write time, not
                                   -- discovered by a report that silently excludes it.
    total_cents     INT         NOT NULL CHECK (total_cents > 0),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Idempotency: the key is UNIQUE, so a concurrent duplicate request fails
-- at the database rather than creating a second order. The uniqueness
-- guarantee comes from the index, not from application checking — an
-- application-level "check then insert" is itself a race condition.
CREATE TABLE idempotency_keys (
    key          TEXT PRIMARY KEY,
    order_id     BIGINT      REFERENCES orders(id),
    response     JSONB       NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Outbox: written in the SAME transaction as the order, so an event can
-- never describe an order that did not commit. Solves the dual-write
-- problem without distributed transactions (Phase 4, Topics 19-21).
CREATE TABLE outbox (
    id          BIGSERIAL PRIMARY KEY,
    topic       TEXT        NOT NULL,
    payload     JSONB       NOT NULL,
    published_at TIMESTAMPTZ,              -- NULL until the relay has published it.
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX outbox_unpublished_idx ON outbox (id) WHERE published_at IS NULL;
                                   -- Partial index: only unpublished rows are indexed,
                                   -- so the relay's polling query stays fast forever
                                   -- even as the table grows to millions of sent rows.
```

```sql
-- =====================================================================
-- The checkout transaction itself. Read the comments as a commentary on
-- §5's numbered flow.
-- =====================================================================

BEGIN;                                       -- xid assigned; MVCC snapshot taken.
                                             -- Nothing on disk yet.

-- ISOLATION. FOR UPDATE takes an exclusive row lock; a concurrent
-- transaction executing this same statement BLOCKS here until we commit
-- or roll back. ORDER BY isbn imposes a deterministic global lock order,
-- so two carts sharing two books cannot deadlock by locking in opposite
-- orders — a bug that appears only under load and is miserable to debug.
SELECT isbn, quantity
  FROM inventory
 WHERE isbn IN ('9780525559474', '9780241988268')
 ORDER BY isbn
   FOR UPDATE;

-- Relative update, NOT "SET quantity = 0". Computing the new value from
-- the current value inside the database is correct even if our earlier
-- read were stale. The CHECK constraint fires here if this would go
-- negative, aborting the whole transaction.
UPDATE inventory SET quantity = quantity - 1
 WHERE isbn = '9780525559474';
UPDATE inventory SET quantity = quantity - 1
 WHERE isbn = '9780241988268';

INSERT INTO orders (customer_id, status, total_cents)
VALUES (4182, 'pending', 2897)
RETURNING id;                                -- RETURNING avoids a second round trip
                                             -- AND avoids reading back a value another
                                             -- transaction could have changed.

INSERT INTO order_lines (order_id, isbn, quantity, unit_price_cents)
VALUES (88301, '9780525559474', 1,  899),
       (88301, '9780241988268', 1, 1998);
       -- unit_price_cents is COPIED, not joined to books.price_cents.
       -- This denormalisation is correct: the price at time of sale is an
       -- immutable historical fact. Joining live price would silently
       -- rewrite history every time the catalogue price changed.

-- Optional sub-step. If the loyalty update fails (say a cap constraint),
-- we do NOT want to lose the order — so we fence it with a savepoint.
SAVEPOINT loyalty;
UPDATE customers SET points = points + 28 WHERE id = 4182;
-- On error in the application layer:
--   ROLLBACK TO SAVEPOINT loyalty;   -- undoes ONLY the points update
-- The order, the inventory decrement, and everything else survive.

INSERT INTO idempotency_keys (key, order_id, response)
VALUES ('ik_9f2c...', 88301, '{"order_id":88301}'::jsonb);
       -- Inside the transaction: the key cannot exist without the order,
       -- and the order cannot exist without the key. A retry after a
       -- client timeout therefore finds a consistent answer either way.

INSERT INTO outbox (topic, payload)
VALUES ('order.placed', '{"order_id":88301}'::jsonb);
       -- The event's existence is now atomic with the order's existence.

COMMIT;   -- Appends the COMMIT record to the WAL and fsyncs it (possibly
          -- sharing one fsync with other committing transactions — group
          -- commit). Only after this returns is the order real. DURABILITY.
```

```ini
# =====================================================================
# postgresql.conf — the durability knobs. Values illustrate the
# trade-offs; tune against your own hardware and recovery objectives.
# =====================================================================

fsync = on
# NEVER off outside disposable CI/test databases. "off" does not mean
# "lose the last few seconds" — it means the OS may write a DATA page
# before its WAL record, producing UNRECOVERABLE corruption on power
# loss. The database may fail to start, or start and return wrong data.

synchronous_commit = on
# The legitimate, tunable durability knob, and NOT the same as fsync.
#   on          : commit waits for local WAL fsync. Safe default.
#   off         : commit returns before the fsync. The write-ahead
#                 ORDERING is still honoured, so no corruption is
#                 possible — you risk losing a BOUNDED window of recent
#                 commits (roughly wal_writer_delay x 3, ~600 ms default).
#   remote_write: also wait for a synchronous standby to receive the WAL.
#   remote_apply: also wait for the standby to APPLY it, so a read on the
#                 standby is guaranteed to see the commit. Slowest.
# Can be set PER TRANSACTION:
#   SET LOCAL synchronous_commit = off;   -- for analytics event ingestion
# Keep it 'on' for checkout; relax it for data you can afford to lose.

commit_delay = 0
commit_siblings = 5
# Group commit tuning. commit_delay is microseconds a committing backend
# waits so that others can join its fsync group, applied only when at
# least commit_siblings transactions are already active. Setting
# commit_delay to roughly HALF your measured fsync time (see
# pg_test_fsync) is the usual starting point on high-commit-rate systems
# with slow storage. On fast NVMe with PLP, 0 is usually correct because
# natural batching already occurs.

wal_sync_method = fdatasync
# Which syscall is used. fdatasync avoids flushing file METADATA that
# does not need flushing, so it is typically faster than fsync on Linux.
# Verify with: pg_test_fsync
# A result like 0.02 ms/op on consumer hardware is NOT good news — it is
# evidence that some layer (drive cache, hypervisor, RAID controller) is
# acknowledging flushes without performing them. Durability is then a
# fiction regardless of these settings.

# ---- Long-transaction defences (see §4.8) ----

idle_in_transaction_session_timeout = 30s
# Kills a session that ran BEGIN and then went idle. This single setting
# prevents the classic "developer opened psql, typed BEGIN, went to
# lunch, VACUUM stopped working database-wide" incident.

statement_timeout = 0
# Global default 0 (unlimited), but set it PER ROLE:
#   ALTER ROLE booknook_web      SET statement_timeout = '5s';
#   ALTER ROLE booknook_reporting SET statement_timeout = '15min';
# A web request that cannot finish in 5 seconds has already failed from
# the user's perspective; letting it run only holds locks for longer.
```

```sql
-- Monitoring query: the single most valuable transaction-health check.
-- Alert if the result exceeds ~60 seconds. A long-running transaction
-- blocks VACUUM across the ENTIRE database, not just its own tables.
SELECT pid,
       now() - xact_start AS txn_age,       -- how long the transaction has been open
       state,                               -- 'idle in transaction' is the danger state
       left(query, 80) AS query
  FROM pg_stat_activity
 WHERE xact_start IS NOT NULL
   AND now() - xact_start > interval '60 seconds'
 ORDER BY txn_age DESC;
```

## 9. Numerical example

**Question: how many transactions per second can BookNook's database commit, and what does group commit actually buy?**

### Stated assumptions

All figures are **typical or approximate** for 2026 hardware; substitute your own measurements from `pg_test_fsync`.

| Assumption | Value | Why this value |
|---|---|---|
| Storage | Enterprise NVMe SSD with power-loss protection | What a production database should be on |
| fsync latency | **0.9 ms** per flush | Typical measured range 0.5–1.5 ms for this class of device |
| CPU work per checkout transaction | **1.8 ms** | Parsing, planning, locking, constraint checks, buffer updates |
| Network round trip, app → database | **0.4 ms** | Same availability zone |
| Payment authorisation | 600 ms, **outside** the transaction | Per §4.8 — deliberately excluded from the transaction |
| CPU cores available for commits | 16 | |
| BookNook peak checkout rate | **200 transactions/sec** | From Lesson 48: 180,000 orders/month, peak ≈ 200/sec |

### Step 1 — commit latency and ceiling with NO group commit

Every transaction performs its own fsync.

```
Per-transaction database time:
  network round trip        0.4 ms
  CPU work                  1.8 ms
  fsync at commit           0.9 ms
  ------------------------------
  total                     3.1 ms
```

The fsync, however, is a **serialised** resource in this model: the WAL is a single file, and without grouping, flushes queue behind one another.

```
Max commit rate = 1 / fsync_latency
                = 1 / 0.0009 s
                = 1,111 transactions/sec
```

Critically, **this ceiling is independent of CPU count.** Adding cores does nothing; the WAL fsync is the bottleneck. This is the single most important number in transaction throughput, and the reason why "we'll just get a bigger machine" often fails to help commit-bound workloads.

CPU ceiling, for comparison:

```
CPU capacity = 16 cores / 0.0018 s per txn = 8,889 transactions/sec
```

So the binding constraint is the fsync at 1,111/sec, using only `1,111 / 8,889 = 12.5%` of available CPU. **87.5% of the hardware is idle while the database waits on the disk.**

### Step 2 — the same calculation on a spinning disk

Substituting a 7,200 rpm disk, where fsync costs roughly **8 ms**:

```
Max commit rate = 1 / 0.008 = 125 transactions/sec
```

BookNook's peak demand is 200/sec. **The system cannot keep up at peak**, and would queue, then time out. This is why "move the database to SSD" used to be the single highest-leverage change available, and why storage class is a correctness-adjacent decision, not merely a performance one.

### Step 3 — with group commit

Group commit lets N transactions arriving in the same window share one fsync. The group size depends on arrival rate and the delay window.

Assume `commit_delay = 450 µs` (half the fsync time — the usual starting heuristic) and an arrival rate of 200/sec.

```
Transactions arriving during one fsync + delay cycle:
  cycle time        = commit_delay + fsync = 0.45 ms + 0.9 ms = 1.35 ms
  arrivals per cycle = 200/sec x 0.00135 s = 0.27 transactions
```

At only 200/sec, **fewer than one transaction arrives per cycle, so there is nothing to group.** This is the crucial and widely missed point: *group commit only helps when you are already near the fsync ceiling.* At BookNook's actual load, setting `commit_delay` would add 0.45 ms of latency to every commit and buy nothing.

Now repeat at a load where it matters — a Black Friday surge of **900 transactions/sec**:

```
  arrivals per cycle = 900/sec x 0.00135 s = 1.215 transactions
```

Still thin. Push to **3,000/sec** (a hypothetical much larger retailer):

```
  arrivals per cycle = 3,000 x 0.00135 = 4.05 transactions per fsync
  effective commit rate = 4.05 / 0.00135 s = 3,000/sec   ← demand is met
```

Compare against no grouping at the same demand: the ceiling was 1,111/sec, so **1,889 transactions/sec would have been rejected or queued**. Group commit converted an impossible load into a served one.

**The latency cost, stated honestly:**

| | No group commit | With group commit (3,000/sec) |
|---|---|---|
| Commit latency | 0.9 ms (fsync only) | 0.45 ms delay + 0.9 ms fsync = **1.35 ms** |
| Total database time per txn | 3.1 ms | **3.55 ms** (+15%) |
| Max sustainable rate | 1,111/sec | **~3,000/sec** (2.7×) |

A 15% latency increase bought 2.7× throughput. That is an excellent trade *at high load* and a pure loss at low load — which is why PostgreSQL ships `commit_delay = 0` by default and expects you to measure before changing it.

### Step 4 — the cost of batching, for comparison

The other way to amortise fsync is to put more work in each transaction. A bulk import of 100,000 rows:

```
Autocommit (one transaction per row):
  100,000 x 0.9 ms fsync = 90,000 ms  = 90 seconds   (fsync-bound)

Batched at 1,000 rows per transaction:
  100 transactions x 0.9 ms fsync     = 0.09 seconds of fsync
  plus CPU: 100,000 rows x ~0.05 ms   = 5 seconds
  total                                ≈ 5.1 seconds

Speed-up: 90 / 5.1 ≈ 17.6x
```

Nearly 18× faster with no configuration change, no hardware change, and no durability compromise — purely by moving the transaction boundary. This is why §4.7 calls autocommit loops the most common easy win in data-loading code.

### Step 5 — what `synchronous_commit = off` would buy, and cost

Removing the fsync wait from the commit path:

```
Per-transaction database time: 0.4 + 1.8 + 0 = 2.2 ms   (from 3.1 ms)
New ceiling = CPU-bound = 8,889 transactions/sec        (from 1,111/sec)

Throughput gain: 8x
Latency gain:    29% lower
Risk:            up to ~600 ms of committed transactions lost on power failure
                 = 600 ms x 200 txn/sec = up to 120 orders
```

**Would you take it?** For BookNook's checkout: no. 120 lost paid orders is 120 customers charged with no record — exactly the §4.4 failure, and a support and reconciliation disaster. For BookNook's clickstream/analytics ingestion: yes, obviously. Losing 600 ms of page-view events costs nothing measurable.

The actionable conclusion: **set `synchronous_commit` per transaction, not globally.** `SET LOCAL synchronous_commit = off` on the analytics path gives you the 8× where it is free, while checkout keeps full durability.

### Step 6 — adding synchronous replication

With a synchronous standby in the same availability zone, adding a 0.8 ms round trip to each commit:

```
Commit path: fsync 0.9 ms + replica round trip 0.8 ms = 1.7 ms
Ceiling     = 1 / 0.0017 = 588 transactions/sec   (down from 1,111/sec, -47%)
```

Across regions (London → Frankfurt, ~15 ms round trip):

```
Commit path: 0.9 + 15 = 15.9 ms
Ceiling     = 1 / 0.0159 = 63 transactions/sec    (-94%)
```

**Cross-region synchronous replication costs 94% of commit throughput.** BookNook needs 200/sec and would get 63/sec — it simply does not work. The options are: accept asynchronous cross-region replication with a bounded data-loss window on regional failure, use group commit to amortise the round trip, or adopt a consensus-based distributed SQL engine designed for the geometry (Lesson 48's NewSQL row). Topic 15 covers this properly; the number here exists to show that durability guarantees have *quantifiable* throughput prices, and that the price of the strongest guarantee is often prohibitive.

## 10. Trade-offs

| Decision | What you gain | What you give up | The condition that decides |
|---|---|---|---|
| **Use transactions vs independent writes** | Correctness under crashes and concurrency; vastly simpler reasoning | Locks held for the transaction's duration; contention under load; the need to design boundaries | Whether any invariant spans more than one row. If yes, there is effectively no choice |
| **`synchronous_commit = on` vs `off`** | On: zero committed-data loss on power failure. Off: ~8× commit throughput and 29% lower latency | Off: a bounded window (~600 ms) of committed transactions can vanish | The value of the data in that window. Money: on. Telemetry: off. Set it per transaction, not globally |
| **`fsync = on` vs `off`** | Off: 10–50× faster benchmarks | Off: risk of **unrecoverable database corruption**, not bounded loss | Whether you are willing to delete and rebuild the entire database. CI: acceptable. Anything else: never |
| **Group commit (`commit_delay > 0`)** | 2–3× throughput near the fsync ceiling | ~0.45 ms added to every commit, including at low load when it buys nothing | Whether you are actually near the fsync ceiling. Measure first; §9 step 3 shows it is useless at 200/sec |
| **Long transaction vs many short ones** | Long: one atomic unit, simpler logic | Long: lock contention, blocked `VACUUM`, bloat, wraparound risk (§4.8) | Whether partial progress is acceptable. For bulk deletes and backfills it almost always is — batch them |
| **Pessimistic locking (`FOR UPDATE`) vs optimistic (version column / serializable)** | Pessimistic: no wasted work; a waiter simply waits. Predictable | Pessimistic: blocking, deadlock potential, worse under high contention. Optimistic: retries waste work and need retry logic everywhere | Contention level. High contention on few rows → pessimistic. Low contention → optimistic wins on throughput |
| **Synchronous vs asynchronous replication** | Sync: commits survive total loss of the primary | Sync: −47% throughput same-zone, −94% cross-region; and the primary stalls if the standby is down unless you configure a fallback | Your recovery point objective. "Zero data loss" is a business requirement with a measurable price; make someone state it |
| **Multi-record transaction vs single-record aggregate** | Transaction: flexible modelling, normalised data | Aggregate: forces denormalisation and update anomalies, but works on stores without transactions | Whether your chosen engine has multi-record transactions, and whether the records change together |
| **Distributed transaction (2PC) vs saga** | 2PC: true atomicity across services | 2PC: blocking on coordinator failure, availability multiplies down, most systems cannot participate. Saga: no isolation, visible intermediate states, compensation code | Essentially always choose the saga — or better, redraw service boundaries so the operation is local (Phase 4, Topics 19–21) |

## 11. Failure scenarios

| Failure | What the user experiences | How it is detected | How it recovers |
|---|---|---|---|
| **Crash mid-transaction** | Nothing — the request fails with a 500 or a timeout; no partial order exists | Application error rate; database restart event in logs | Automatic. Recovery replays the WAL and discards the transaction with no commit record (§4.1). The client's retry succeeds via the idempotency key |
| **Deadlock** — two transactions lock the same two rows in opposite orders | One request fails with "deadlock detected" after a short delay (PostgreSQL default 1 s); the other succeeds | `deadlock_timeout` fires; the engine logs both queries and the victim | The engine automatically aborts one transaction. The application must **retry** the aborted one. Prevent by locking rows in a deterministic order (`ORDER BY isbn`) |
| **Lock wait timeout under contention** on a hot row | Checkout latency spikes on a viral title; eventually 500s while other titles are fine | p99 latency by endpoint plus `pg_locks`/`pg_blocking_pids` showing a lock queue | Shorten the transaction; move external calls out; consider optimistic concurrency or per-title queueing for extreme hot spots |
| **Idle-in-transaction session** — a client opened `BEGIN` and stopped | Initially nothing. Hours later: general slowness, growing disk usage, degrading query plans across *unrelated* tables | `pg_stat_activity` showing `idle in transaction` with a large `xact_age`; table bloat metrics rising; `VACUUM` making no progress | Terminate the backend (`pg_terminate_backend`). Prevent permanently with `idle_in_transaction_session_timeout` (§4.8) |
| **Bloat from blocked `VACUUM`** | Queries that took 20 ms take 400 ms; disk usage climbs with no data growth | Dead-tuple ratio per table; table size versus estimated live rows; falling cache hit rate | `VACUUM` once the blocker clears; `pg_repack` or `VACUUM FULL` to reclaim space (the latter takes an exclusive lock). Fix the transaction-duration root cause or it recurs |
| **Transaction ID wraparound emergency** | **All writes refused.** Total write outage | Loud warnings in logs well before the threshold; `age(datfrozenxid)` metric | Emergency `VACUUM`, possibly in single-user mode. Entirely preventable by monitoring `datfrozenxid` age and preventing long transactions |
| **`fsync=off` plus power loss** | Database will not start, or starts and returns subtly wrong results | Checksum failures, "invalid page header", or inconsistencies found weeks later in reconciliation | **Restore from backup and replay WAL archives.** There is no repair. This is why §4.6 is categorical |
| **Lying disk** — flushes acknowledged but not performed | Identical to the above, despite `fsync = on` being set correctly | `pg_test_fsync` showing implausibly low latency; vendor specifications lacking power-loss protection | Replace the hardware or move to a managed service. Verify before you depend on it, not after |
| **Dual write without outbox** — order committed, event publish fails | The warehouse never receives the order. The customer's confirmation email arrives but nothing ships | Only by reconciling orders against downstream state — nothing errors at the time | Use the outbox pattern so the event is atomic with the order. Without it, a reconciliation job is mandatory |
| **Saga compensation fails** — refund API is down after a payment succeeded but inventory reservation failed | Customer charged for an order that was cancelled | Saga state machine stuck in `compensating`; alert on sagas in a non-terminal state beyond a threshold | Retry with backoff; escalate to a manual finance queue after N attempts. **Compensations must themselves be retried and monitored** — they are the most forgotten failure path in saga designs |
| **Retry after timeout, no idempotency key** | Customer charged twice; two identical orders | Duplicate-order alerting, or the customer's complaint | Manual refund. Prevent with an idempotency key written inside the same transaction (§8) |
| **Serialisation failure** under `SERIALIZABLE` isolation | Request fails with `could not serialize access due to read/write dependencies` | Error-rate metric by SQLSTATE `40001` | The application **must retry** — this is not a bug but the contract of the isolation level. Code that does not retry will appear broken under load (Topic 11) |

## 12. Alternatives

Alternatives to full ACID transactions for maintaining correctness:

| Approach | How it works | Strengths | Weaknesses | Choose it when |
|---|---|---|---|---|
| **Full ACID transactions** | `BEGIN`/`COMMIT` in a transactional engine | Strongest correctness; simplest reasoning; constraints enforced by the engine | Requires all participating data in one database; locks create contention; single-primary write ceiling | Default for anything with invariants spanning records — especially money, inventory, and identity |
| **Single-record atomicity (aggregate modelling)** | Collapse everything that must change together into one document or item | Works on stores with no multi-record transactions; very fast; scales horizontally | Forces denormalisation; duplicated mutable data drifts; the aggregate can grow past document size limits | Document or key-value stores where the aggregate boundary genuinely matches the transaction boundary |
| **Compare-and-set / optimistic concurrency** | Read a version number; write conditionally on it being unchanged; retry on conflict | No locks held; excellent under low contention; available in almost every store (DynamoDB `ConditionExpression`, Redis `WATCH`, a `version` column in SQL) | Retries waste work and grow quadratically with contention; retry logic must exist at every call site | Low-contention updates to single records |
| **Lightweight transactions (Cassandra Paxos)** | Consensus-based compare-and-set within one partition | Gives linearizable CAS in an eventually consistent store | ~4 round trips, so roughly 4× latency; single-partition only; degrades badly under contention | Rare, correctness-critical operations like claiming a unique username |
| **Sagas with compensating actions** | Sequence of local transactions, each with a defined undo | The only workable option across service boundaries; no blocking coordinator | No isolation — intermediate states are visible; compensation is not true rollback; much more code and more failure modes | Operations that genuinely must span services (Phase 4, Topics 19–21) |
| **Two-phase commit / XA** | Coordinator collects votes, then commits all participants | Real atomicity across participating resource managers | Blocks on coordinator failure; availability multiplies down; most external APIs cannot participate; poor operational tooling | Rare: legacy enterprise integration between two XA-capable databases under one operator |
| **Event sourcing** | Store an append-only sequence of events; derive state by replaying them | Append-only writes need no multi-row atomicity; complete audit trail; temporal queries for free | Reads require projections; projection lag; schema evolution of events is genuinely hard; higher conceptual load | Audit-critical domains (finance, healthcare) where the event history *is* the product requirement |
| **Idempotency keys + at-least-once delivery** | Make every operation safe to repeat, then retry freely | Removes whole classes of duplicate-side-effect bugs; composes with everything above | Not a substitute for atomicity — it prevents duplicates, not partial application | Always, in addition to whichever option above you choose |
| **Application-level locking** (advisory locks, Redis locks) | Acquire a named lock before a critical section | Can coordinate across processes and even across stores | Distributed locks are notoriously unsafe under network partitions and clock skew; lock holder may die holding it | Short, non-critical coordination. Never as the sole guard on a money invariant |


---

# Lesson 50 — Isolation Levels

*Phase 3, Topic 11*

## 1. Simple definition

**Isolation** is the "I" in ACID (Lesson 49). It answers one question, asked millions of times a second inside every database engine:

> *"My transaction is running. Another transaction is running at the same time and touching some of the same rows. **What am I allowed to see of its work, and what is it allowed to see of mine?**"*

An **isolation level** is the specific, named answer to that question that you select for a transaction. It is a *contract*: it tells you which concurrency bugs ("anomalies") the engine promises will never happen to you, and — by omission — which ones it is still free to inflict on you.

Terms defined before use:

- **Transaction**: a group of statements that commit or abort as a unit (Lesson 49). Written `BEGIN … COMMIT` / `ROLLBACK`.
- **Concurrent transactions**: two transactions whose lifetimes overlap in wall-clock time. T1 began before T2 committed, and T2 began before T1 committed.
- **Interleaving**: the actual order in which the engine executed the individual reads and writes of concurrent transactions. The engine chooses this; you do not.
- **Anomaly** (also **phenomenon**): an observable outcome that could not have been produced by running the transactions one at a time in *some* order. Lesson 51 defines each anomaly precisely.
- **Serial execution**: running transactions strictly one after another, no overlap. Zero anomalies by construction, and catastrophically slow.
- **Serializable execution**: an execution whose *final database state and every value returned to every client* is identical to **some** serial order of the same transactions. Not necessarily the order they arrived in — *some* order.

**Serializability is the ideal.** It is the only isolation level you can reason about without thinking about concurrency at all. Under serializability you may write every transaction as if it were the only transaction in the universe, and it will still be correct. Every weaker level is a deliberate trade: you give back some of that guarantee in exchange for throughput.

The hardest idea in this lesson, stated up front so you read the rest with it in mind:

> **The SQL standard defines isolation levels by which anomalies they forbid, not by how the engine achieves that. Two databases can both say "REPEATABLE READ" and behave differently — legally.** The level name is a floor, not a specification.

## 2. Why it exists

Isolation exists because the obvious implementation is unusably slow and the fast implementation is unusably wrong, and almost all of database engineering lives between those two poles.

**Start with the slow, correct end.** BookNook, our online bookstore, could execute every transaction one at a time with a single global lock. A checkout takes roughly 8 ms of database work (two index lookups, a stock decrement, an order insert, an fsync of the write-ahead log). One at a time, that caps the entire business at `1,000 ms ÷ 8 ms = 125 transactions per second`, on any hardware, forever. Adding CPUs does nothing. Black Friday at BookNook peaks at 3,000 transactions per second. Serial execution is off the table.

**Now the fast, wrong end.** Let every transaction read and write whatever it likes, whenever it likes, with no coordination. You get maximum throughput and these real BookNook failures:

| Uncontrolled behaviour | BookNook business consequence |
|---|---|
| Two checkouts both read `stock = 1`, both write `stock = 0` | Two customers buy the last copy. One gets a cancellation e-mail. (**Lost update** — Lesson 51, §Lost update.) |
| A report reads an order total that is later rolled back | The daily revenue figure includes a payment that never happened. (**Dirty read**.) |
| A refund job reads a customer's balance twice and gets two different numbers | Refund maths is computed against inconsistent inputs. (**Non-repeatable read**.) |
| A stock-count report runs `SELECT count(*)` twice and sees 412 then 419 | Reconciliation fails; an engineer spends a day on a "data corruption" that was never corruption. (**Phantom read**.) |
| Two warehouse operators each confirm the last reserved copy because each saw the other's reservation as not-yet-committed | Physical inventory goes negative. (**Write skew** — the anomaly that survives snapshot isolation.) |

So the engine must give back *some* coordination. But coordination costs throughput, and different applications need different amounts. A bank ledger needs far more than a page-view counter.

**Isolation levels exist as a small menu of pre-defined points on that curve**, so that you can buy exactly as much correctness as your invariant requires and no more. The SQL-92 standard named four of them. Every major engine implements that menu, and — critically — every major engine implements it *differently underneath*.

One more reason, often missed: isolation levels exist so that **the guarantee is written down**. Before the standard, "what can my transaction see?" was answered per-vendor, per-version, in forum posts. The standard turned a folklore question into a contract you can test against.

## 3. Real-world analogy

**A shared Google Doc versus a printed draft.**

Four writers are working on BookNook's Q4 inventory report.

- **READ UNCOMMITTED** = everyone types in the same live Google Doc and reads each other's half-finished sentences. You might read "Q4 revenue was £4,0" at the exact moment a colleague is mid-typing, then they delete the line entirely. You acted on a number that never existed.
- **READ COMMITTED** = you only ever read sentences your colleagues have finished and clicked "publish" on. But between your first read of paragraph 3 and your second read of paragraph 3, a colleague can publish an edit. Both reads were of *published* text; they just weren't the *same* published text.
- **REPEATABLE READ / snapshot isolation** = at the moment you start work, you print the whole document. For the rest of your session you read only your printout. Nothing anyone does can change what you see. Perfectly stable — but your printout is **stale**, and if you write back based on it you may overwrite work you never saw.
- **SERIALIZABLE** = the document can only be worked on in a way that is equivalent to each writer having had sole possession of it, in turn. Either the editors coordinate (locking) or someone audits the edits afterwards and tells a writer "your changes conflicted; redo them against the current version" (serializable snapshot isolation).

The printout analogy is the one to keep. **Snapshot isolation means you work from a consistent photograph of the past.** Everything confusing about REPEATABLE READ follows from that single image.

**Where the analogy breaks down:**

| Shared document | Database isolation |
|---|---|
| Your printout is obviously a printout — you *know* it is stale | Your snapshot looks exactly like live data. There is no visual cue. A query returning a row says nothing about whether that row still exists. |
| Two writers editing the same paragraph produces visibly mangled text | Two transactions updating the same row produces a *perfectly clean, perfectly wrong* value. Lost updates leave no trace in the data. |
| A human notices "this paragraph changed under me" | Nothing notices, unless the isolation level or an explicit version check is configured to notice. Silence is the default. |
| "Publish" is one instant | A commit is a multi-step protocol (write-ahead log append, fsync, visibility-map update). "Committed" and "visible to you" are different moments. |
| Writers can shout across the room to coordinate | Transactions cannot communicate. The *only* channel between them is the database state itself — which is exactly what isolation governs. |
| At worst you lose an afternoon's prose | At worst you ship a copy of a book you do not have, or double-charge a card. Anomalies are business events, not data events. |
| Everyone reads the same document | Under snapshot isolation, **every transaction may be reading a different version of the database simultaneously**, all of them internally consistent, none of them current. There is no single "the data" at all. |

That last row is the deepest break. Under MVCC (§4) there is genuinely no such thing as "the current state of the database" from inside a transaction. There is only *your* snapshot.

## 4. Detailed technical explanation

### 4.1 What the standard actually says

SQL-92 defines isolation levels **negatively**, by naming three phenomena and stating which levels must prevent them:

- **P1 — Dirty read**: T1 reads a row written by T2 before T2 has committed.
- **P2 — Non-repeatable read** (fuzzy read): T1 reads a row, T2 updates or deletes *that row* and commits, T1 re-reads the row and sees different data.
- **P3 — Phantom read**: T1 runs a query matching a predicate (`WHERE price < 20`), T2 inserts or updates a row so that it now matches that predicate and commits, T1 re-runs the same query and sees a **new row** that was not there before.

Note the precise difference between P2 and P3. **Non-repeatable read is about a row you already saw changing. Phantom read is about the *set of rows matching a condition* changing.** They are different because they need different machinery to prevent: P2 needs you to pin individual rows, P3 needs you to pin a *predicate* — including rows that do not exist yet. You cannot lock a row that has not been inserted.

Lesson 51 treats all six anomalies (these three plus lost update, read skew and write skew) with full interleaved timelines.

### 4.2 The four levels — the definitive table

| Level | Dirty read (P1) | Non-repeatable read (P2) | Phantom read (P3) | Lost update | Write skew | What it actually promises |
|---|---|---|---|---|---|---|
| **READ UNCOMMITTED** | **Possible** | Possible | Possible | Possible | Possible | Almost nothing. You may observe uncommitted, never-to-exist data. |
| **READ COMMITTED** | Prevented | **Possible** | Possible | Possible | Possible | Every value you read was committed by someone, at some moment. Not necessarily the *same* moment for two different reads — not even within one statement on some engines. |
| **REPEATABLE READ** | Prevented | Prevented | **Possible** *(per the standard)* | Possible *(per the standard)* | Possible | Rows you have already read will not change under you. |
| **SERIALIZABLE** | Prevented | Prevented | Prevented | Prevented | Prevented | The result is equivalent to **some** serial order. Reason about your transaction alone. |

Read that table as a **floor**, not a description. "Possible" means *the standard permits the engine to allow it*. A given engine may prevent it anyway and still be compliant. This is the source of essentially all real-world confusion about isolation.

### 4.3 The critical point: levels are defined by anomalies, not implementations

The standard says REPEATABLE READ must prevent dirty reads and non-repeatable reads. It says **nothing** about *how*, and it explicitly allows phantoms. So:

- An engine using **two-phase locking** that takes shared locks on every row it reads and holds them to commit satisfies REPEATABLE READ. It permits phantoms because it locks rows, and a row that does not exist cannot be locked.
- An engine using **snapshot isolation** gives every transaction a frozen view of the whole database. That trivially prevents dirty reads and non-repeatable reads — *and it also prevents phantoms*, because a snapshot cannot grow new rows. It is **strictly stronger** than the standard's REPEATABLE READ, and it is still legally called REPEATABLE READ.

Both are compliant. They behave visibly differently. Concretely:

**PostgreSQL `REPEATABLE READ` is snapshot isolation.** A snapshot is taken at the first statement of the transaction and never changes. `SELECT count(*) FROM books WHERE price < 20` returns the identical number every time you run it, for the life of the transaction. **Phantoms do not occur.** You get more than you asked for. In exchange, if your transaction writes a row that another transaction has written since your snapshot, PostgreSQL aborts you with SQLSTATE `40001` (`could not serialize access due to concurrent update`) rather than silently losing the update. **Under PostgreSQL REPEATABLE READ you must be prepared to retry** (Lesson 17 — retries, backoff and idempotency).

**MySQL InnoDB `REPEATABLE READ` is snapshot isolation for plain reads, and *not* for locking reads.** This split is the single most misunderstood behaviour in MySQL:

- A plain `SELECT` is a **consistent read**: served from the transaction's snapshot, established at the first read. Stable, no phantoms.
- `SELECT … FOR UPDATE`, `SELECT … FOR SHARE`, `UPDATE` and `DELETE` are **current reads** (sometimes "locking reads"): they bypass the snapshot and read the **latest committed** version, then lock it, typically with **next-key locks** (a row lock plus a gap lock on the index range before it) which block the inserts that would create phantoms.

So within one MySQL transaction you can observe two different realities:

```
-- MySQL InnoDB, REPEATABLE READ, T1
SELECT stock FROM inventory WHERE isbn = '978-1';        -- consistent read → 5 (snapshot)
SELECT stock FROM inventory WHERE isbn = '978-1' FOR UPDATE; -- current read → 3 (latest!)
```

Both statements are correct per MySQL's documented rules. Neither is correct per the mental model "REPEATABLE READ means my reads repeat". MySQL also does **not** raise a serialization error on write-write conflict at this level — it blocks on the lock and then proceeds, which is why MySQL applications lose updates in places PostgreSQL applications get an error.

**Oracle has no READ UNCOMMITTED at all.** Oracle's MVCC never exposes uncommitted data, so the level is meaningless and simply is not offered. Oracle offers READ COMMITTED (default) and SERIALIZABLE — and **Oracle's SERIALIZABLE is snapshot isolation**, not true serializability. It therefore permits write skew (§4.5). If you port a PostgreSQL SERIALIZABLE application to Oracle expecting identical guarantees, you have silently downgraded your correctness. Oracle also does not offer REPEATABLE READ as a named level.

**SQL Server** defaults to a *locking* READ COMMITTED, where readers take short shared locks and can therefore **block on writers**. Enabling the database option `READ_COMMITTED_SNAPSHOT ON` changes the same level name to a row-versioning implementation where readers never block. Same name, same database, two entirely different concurrency profiles — switched by a setting. SQL Server also offers `SNAPSHOT` as an explicitly named fifth level.

**Carry this rule:** *never* reason from the level name alone. Reason from `engine + version + level + whether the read is locking`.

### 4.4 Snapshot isolation, explained properly

**Snapshot isolation (SI)** means: at a defined instant, the transaction conceptually photographs the entire committed state of the database. All its reads are served from that photograph. Its writes go into a private workspace and become visible to others only at commit. At commit, the engine checks a **first-committer-wins** rule: if any row this transaction wrote was also written and committed by another transaction after the snapshot was taken, this transaction aborts.

SI prevents dirty reads (uncommitted data is never in a snapshot), non-repeatable reads (the snapshot is frozen), phantoms (the snapshot cannot acquire new rows) and lost updates on the *same row* (first-committer-wins catches them).

**And yet SI is not serializable.** This is the point that separates engineers who have read about isolation from engineers who have been burned by it.

The gap: SI guarantees that each transaction *reads* a consistent state and that no two transactions *write the same row*. It guarantees nothing about two transactions **reading an overlapping set of rows and then writing different rows based on what they read**. Neither write-write conflict exists, so first-committer-wins has nothing to catch. Both commit. The combined result matches no serial order.

That anomaly is called **write skew**.

### 4.5 Write skew — a full worked BookNook example

BookNook sells limited signed editions. The business invariant, enforced nowhere in the schema because it spans rows:

> **Across all open reservations for an ISBN, the total reserved quantity must never exceed the physical stock on hand.**

State: ISBN `978-0-11-111111-1`, *The Midnight Library* signed edition. `stock_on_hand = 2`. Existing open reservations total `0`.

Two customers check out simultaneously, each reserving **2** copies. The application logic for each is: *sum the open reservations, add my quantity, and only insert if the total does not exceed stock.*

```
PostgreSQL REPEATABLE READ (= snapshot isolation). stock_on_hand = 2.

  wall    T1 (customer Alice, qty 2)            T2 (customer Bob, qty 2)
  clock
  ─────────────────────────────────────────────────────────────────────────
  t0      BEGIN ISOLATION LEVEL REPEATABLE READ
  t1      ── snapshot S1 taken ──
  t2      SELECT coalesce(sum(qty),0)
            FROM reservations
            WHERE isbn='978-0-11-111111-1'
              AND status='open';
          → 0
  t3                                            BEGIN ISOLATION LEVEL REPEATABLE READ
  t4                                            ── snapshot S2 taken ──
  t5                                            SELECT coalesce(sum(qty),0)
                                                  FROM reservations
                                                  WHERE isbn='978-0-11-111111-1'
                                                    AND status='open';
                                                → 0        (S2 also sees nothing)
  t6      check: 0 + 2 <= 2  ✓ ALLOWED
  t7                                            check: 0 + 2 <= 2  ✓ ALLOWED
  t8      INSERT INTO reservations
            (isbn, customer, qty, status)
          VALUES ('978-0-11-111111-1',
                  'alice', 2, 'open');
  t9                                            INSERT INTO reservations
                                                  (isbn, customer, qty, status)
                                                VALUES ('978-0-11-111111-1',
                                                        'bob', 2, 'open');
  t10     COMMIT;   ← succeeds
  t11                                           COMMIT;   ← ALSO SUCCEEDS
  ─────────────────────────────────────────────────────────────────────────
  Final state: 2 open reservations, total qty = 4, stock_on_hand = 2.
  INVARIANT VIOLATED. No error was raised. No row was written twice.
```

Walk through why every safety mechanism missed it:

1. **No dirty read** — each transaction read only committed data.
2. **No non-repeatable read** — neither re-read anything.
3. **No phantom** — each snapshot was internally consistent.
4. **No lost update** — they wrote *different rows* (Alice's reservation row and Bob's reservation row). First-committer-wins compares written rows; the sets are disjoint; nothing conflicts.
5. **No constraint violation** — the invariant is an aggregate across rows. `CHECK` constraints in standard SQL operate on a single row. You cannot express "sum of a filtered set" as a row-level check.

Yet there is no serial order producing this outcome. Run T1 then T2: T2 reads sum = 2, computes 2 + 2 > 2, refuses. Run T2 then T1: symmetric. **Every serial order rejects one of them. The concurrent execution accepted both.** That is the definition of a non-serializable execution.

The pattern in the abstract: each transaction reads a set of rows, makes a decision based on the aggregate, and writes a row that would have changed the other's decision had it been visible. This is sometimes phrased as the two transactions' **read sets and write sets crossing**: `write_set(T1) ∩ read_set(T2) ≠ ∅` **and** `write_set(T2) ∩ read_set(T1) ≠ ∅`, with `write_set(T1) ∩ write_set(T2) = ∅`.

Four fixes, in increasing order of generality:

1. **Materialise the conflict.** Add a row that both transactions *write*, turning write skew into a write-write conflict SI already catches — e.g. `UPDATE inventory SET reserved = reserved + :qty WHERE isbn = :isbn` as part of the same transaction. Now both touch the same row and one aborts.
2. **Lock the predicate explicitly.** `SELECT … FROM inventory WHERE isbn = :isbn FOR UPDATE` before reading the sum, serialising all reservers of that ISBN on the inventory row.
3. **Use SERIALIZABLE.** PostgreSQL's SSI (§4.7) detects the read-write dependency cycle and aborts one transaction with `40001`.
4. **Redesign so the invariant lives in one row.** A single `available` counter decremented atomically removes the aggregate entirely. Lesson 51 §8 shows this.

Fix 1 and 2 require you to *know* write skew exists. Fix 3 does not. That is SERIALIZABLE's real value proposition: **it protects you from anomalies you did not think of.**

### 4.6 How MVCC implements all of this

**MVCC — multi-version concurrency control** — is the dominant implementation in PostgreSQL, Oracle, MySQL InnoDB, SQL Server (with row versioning on) and most modern engines. The core idea:

> **An `UPDATE` does not overwrite a row. It writes a new version of the row and marks the old version as expired-as-of a particular transaction. A `DELETE` only marks expiry. Readers choose which version to look at.**

Because old versions survive, **readers never block writers and writers never block readers**. That single property is why MVCC won.

The machinery, using PostgreSQL's vocabulary (others differ in detail, not in spirit):

- **Transaction ID (XID / `xid`)**: a monotonically increasing 32-bit integer assigned to each writing transaction.
- Every row version (**tuple**) carries two hidden system columns:
  - `xmin` — the XID of the transaction that **created** this version.
  - `xmax` — the XID of the transaction that **expired** this version (0 if still live).
- **Snapshot**: a triple `(xmin_horizon, xmax_horizon, in_progress_list)` — the lowest still-running XID, the next XID to be assigned, and the explicit list of XIDs in flight at snapshot time.
- **Visibility rule**, applied per tuple:

```
A tuple is visible to my snapshot if and only if:
    (1) its xmin committed, AND
    (2) its xmin is NOT in my snapshot's in-progress list, AND
    (3) its xmin < my snapshot's xmax_horizon, AND
    (4) its xmax is 0, OR xmax did not commit, OR xmax is in my in-progress list,
        OR xmax >= my snapshot's xmax_horizon
```

Conditions (1)–(3) say *"the version was created by a transaction that had already finished before I started."* Condition (4) says *"and it had not yet been deleted or superseded as of that moment."* That is the entire photograph, implemented as an integer comparison per row.

**Where isolation levels come from, mechanically:** they are nothing but **when you take the snapshot**.

| Level under MVCC | Snapshot policy |
|---|---|
| READ COMMITTED | A **new snapshot per statement**. Each statement sees everything committed before *that statement* began. This is exactly why two `SELECT`s in one transaction can disagree. |
| REPEATABLE READ / SNAPSHOT | **One snapshot per transaction**, taken at the first statement, held to the end. |
| SERIALIZABLE (PostgreSQL) | One snapshot per transaction, **plus** dependency tracking (§4.7). |
| READ UNCOMMITTED | No meaningful snapshot. Under PostgreSQL this level is accepted and silently behaves as READ COMMITTED — there is no code path to read a dirty tuple. |

**Why old versions accumulate, and why that is your problem.** Every `UPDATE` leaves a dead tuple behind. The dead tuple cannot be reclaimed while *any* snapshot might still need it. One long-running transaction — an analytics query, an idle-in-transaction connection from a buggy ORM, a forgotten `psql` session — **pins the horizon**, and every dead tuple produced since it began must be retained.

Consequences, in order of how they bite:

1. **Table bloat.** A 10 GB table can reach 60 GB. Disk fills; backups slow.
2. **Index bloat.** Index entries point at dead tuples; scans traverse more pages for the same results.
3. **Query slowdown.** A sequential scan reads dead rows and discards them. Throughput falls with no change in data volume.
4. **Transaction-ID wraparound.** PostgreSQL's XID is 32-bit (~4.3 billion values) and wraps. If vacuum cannot freeze old tuples before wraparound, the server **refuses new writes** to protect data. This is a full production outage, and it has taken down large sites.

**VACUUM** is the reclamation process: it finds tuples invisible to every possible snapshot and frees their space. `autovacuum` runs it automatically; `VACUUM FULL` rewrites the table and takes an `ACCESS EXCLUSIVE` lock (an outage on a hot table — rarely the right answer). MySQL InnoDB has the same problem under a different name: old versions live in the **undo log**, and a long transaction causes **undo log growth** and **history list length** alarms. Oracle hits `ORA-01555: snapshot too old` when the undo needed by a long reader has already been overwritten — the mirror-image failure, where the engine *discards* the version rather than retaining it.

> **Operational rule that follows directly:** long-running transactions are expensive even when they are read-only, because they prevent cleanup of everyone else's garbage. Monitor `idle in transaction` sessions. Set `idle_in_transaction_session_timeout`. This is a *correctness-adjacent* performance issue created entirely by how isolation is implemented.

### 4.7 The alternatives to MVCC: two-phase locking, and SSI

**Two-phase locking (2PL)** is the classical implementation and the one that true SERIALIZABLE was originally built on.

- **Phase 1 (growing)**: the transaction acquires locks — **shared (S)** locks to read, **exclusive (X)** locks to write — and never releases any.
- **Phase 2 (shrinking)**: at commit or abort, all locks are released at once. ("Strict 2PL", which is what engines actually use, releases *only* at the end; this is what prevents cascading aborts.)
- S locks are compatible with other S locks; X locks are compatible with nothing.

2PL prevents dirty reads, non-repeatable reads and lost updates. To prevent **phantoms** it needs more than row locks, because the problematic row does not exist yet. Two mechanisms:

- **Predicate locks**: lock the *condition* (`price < 20`), so any insert matching it blocks. Conceptually perfect, expensive to evaluate.
- **Index-range / next-key locks**: lock a contiguous range of an index, including the **gaps** between existing keys. MySQL InnoDB's next-key locking is this. Cheap and effective, but locks more than strictly necessary — and gap locks on a non-unique index are a leading cause of surprise deadlocks.

2PL's costs are blunt: **readers block writers and writers block readers**; throughput collapses under contention; and the lock-wait graph can contain cycles, i.e. **deadlocks**, which the engine resolves by picking a victim and aborting it. Your application must handle that abort — the same retry requirement as MVCC's serialization failures.

**Serializable Snapshot Isolation (SSI)** is the modern answer and what PostgreSQL's `SERIALIZABLE` has used since 9.1. It is optimistic rather than pessimistic:

1. Run the transaction under ordinary snapshot isolation — **no read locks, no blocking**.
2. Track, cheaply, which rows and index ranges each transaction read (**SIREAD locks**, which are bookkeeping markers, not real locks — they never block anyone).
3. Watch for the specific structural pattern that makes SI non-serializable: a **dangerous structure**, a cycle in the read-write dependency graph containing two consecutive *rw-antidependencies* (T1 read something T2 then wrote, and T2 read something T3 then wrote).
4. If detected, abort one transaction with `40001 could not serialize access due to read/write dependencies among transactions`.

SSI gives true serializability — write skew included — while keeping MVCC's "readers never block writers" property. Its costs:

- **False positives.** SSI detects a *sufficient* condition for non-serializability, not an exact one. Some aborted transactions would in fact have been fine. You pay in retries.
- **Memory for predicate tracking.** Controlled in PostgreSQL by `max_pred_locks_per_transaction`; when exceeded, tracking degrades to coarser granularity (page, then relation), which raises the false-positive rate further.
- **It does not work across read replicas** in the straightforward way; serializability is enforced on the primary.
- **It is useless unless the application retries.** An unretried `40001` is a 500 to the customer. Lesson 17's retry logic is a hard prerequisite, not an optional extra.

### 4.8 Default isolation levels

This table is worth memorising, because the defaults differ and almost nobody sets the level explicitly.

| Engine | Default level | What that default actually is, underneath | Notable deviations from the standard |
|---|---|---|---|
| **PostgreSQL** | READ COMMITTED | MVCC, a fresh snapshot per statement | READ UNCOMMITTED is accepted but behaves as READ COMMITTED; REPEATABLE READ is full snapshot isolation (no phantoms); SERIALIZABLE is SSI (true serializability) |
| **MySQL / InnoDB** | **REPEATABLE READ** | MVCC snapshot for plain reads; **current read + next-key locks** for locking reads | Prevents many phantoms via gap locks, but mixes snapshot and current reads in one transaction; no serialization error on write-write conflict |
| **SQL Server** | READ COMMITTED | **Locking** by default — readers take short S locks and *can block* | With `READ_COMMITTED_SNAPSHOT ON`, the same level becomes row-versioned and non-blocking. Also offers an explicit `SNAPSHOT` level. |
| **Oracle** | READ COMMITTED | MVCC using undo segments | **No READ UNCOMMITTED. No REPEATABLE READ.** `SERIALIZABLE` is snapshot isolation — it **permits write skew**. |

Two conclusions follow immediately. First, **a correct application on MySQL may be incorrect on PostgreSQL and vice versa, with identical SQL**, because the default levels differ (REPEATABLE READ vs READ COMMITTED). Second, **"we use SERIALIZABLE so we're safe" is only true on PostgreSQL and SQL Server**; on Oracle that sentence is false.

### 4.9 The performance cost of each level

All figures below are **approximate and illustrative**, taken from the shape of typical OLTP benchmarks on a single well-provisioned primary. Absolute numbers depend entirely on hardware, schema and — above all — **contention**, meaning the probability that two concurrent transactions touch the same rows. Treat them as *relative orders of magnitude*, never as predictions.

Baseline: BookNook's checkout workload, 64 concurrent connections, low contention (a large catalogue, requests spread over many ISBNs).

| Level | Approx. relative throughput, **low** contention | Approx. relative throughput, **high** contention (hot ISBN) | Where the cost comes from |
|---|---|---|---|
| READ UNCOMMITTED | 100 % | 100 % | Nothing to pay. Also nothing to rely on. |
| READ COMMITTED | ~98–100 % | ~95 % | One snapshot per statement; negligible unless statement rate is enormous |
| REPEATABLE READ (SI) | ~95–99 % | ~70–85 % | Longer version chains to walk; aborts on write-write conflict → retries |
| SERIALIZABLE (SSI) | ~90–95 % | ~40–70 % | Predicate tracking overhead, plus abort-and-retry traffic |
| SERIALIZABLE (2PL) | ~70–85 % | ~10–30 % | Readers block writers; lock convoys; deadlock victims |

The crucial reading of this table: **at low contention, SERIALIZABLE is nearly free.** The 5–10 % cost of SSI on a low-contention workload is smaller than the cost of one production incident caused by write skew. The expensive case is specifically *high contention on the same rows* — and if you have that, you have a design problem (a hot row) that weaker isolation will not fix, it will merely convert from slow into wrong.

A second, more honest framing: at SERIALIZABLE you do not pay in latency so much as in **retry rate**. If 3 % of transactions abort with `40001` and are retried once, your effective throughput cost is roughly 3 % extra work plus the latency of those retries — *provided* the retry logic exists. Without it, that 3 % is a 3 % error rate.

### 4.10 How to choose: start from the invariant

Do not choose a level by reputation. Choose it by writing down, in one sentence, **the invariant your transaction must preserve**, then asking what could break it.

```
1. Write the invariant.
     "Stock on hand never goes negative."
     "Total open reservations never exceed stock on hand."
     "An order's total equals the sum of its line items."

2. Does the invariant live in ONE ROW?
     YES → an atomic UPDATE with a CHECK constraint is enough.
           READ COMMITTED is sufficient. (Lesson 51 §8.)
     NO  → continue.

3. Does the transaction DECIDE based on rows it READ, then WRITE
   different rows?
     NO  → snapshot isolation (REPEATABLE READ) is sufficient.
     YES → you are exposed to WRITE SKEW.
           → SERIALIZABLE, or explicitly materialise the conflict.

4. Does the transaction need a stable multi-statement view
   (a report, an export, a consistency check)?
     YES → REPEATABLE READ at minimum, to avoid read skew.

5. Whatever you chose above READ COMMITTED:
     Can the application retry on SQLSTATE 40001?
     NO → you have not actually adopted that level. Fix this first.
```

Step 5 is the one teams skip. **Higher isolation levels are a contract that includes the application.** The engine's half is "I will detect the conflict and abort you". Your half is "I will retry". Half a contract is worse than none, because it converts invisible corruption into visible errors without converting them into correct behaviour.

## 5. Request / data flow

A BookNook checkout at `POST /orders`, configured for PostgreSQL `SERIALIZABLE`, start to finish:

1. The HTTP request reaches an application server (routed by the load balancer of Lesson 23). The handler borrows a pooled connection from the database connection pool.
2. The application issues `BEGIN ISOLATION LEVEL SERIALIZABLE`. **No snapshot is taken yet** — PostgreSQL defers snapshot acquisition to the first statement, so an idle `BEGIN` costs nothing and pins nothing.
3. The first statement executes: `SELECT stock FROM inventory WHERE isbn = $1`. The engine now assigns a snapshot `S = (xmin_horizon, xmax_horizon, in_progress_list)`.
4. The storage layer locates the index entry for the ISBN and follows it to the heap page.
5. For each row version on that page, the visibility rule of §4.6 is evaluated against `S`. Exactly one version is visible. Its `stock` value — say 7 — is returned.
6. Because the level is SERIALIZABLE, the engine additionally records an **SIREAD marker** on the row and on the index range scanned. This marker blocks nobody; it is bookkeeping for conflict detection.
7. The application applies business logic: 7 ≥ 1, so the sale is allowed.
8. `UPDATE inventory SET stock = stock - 1 WHERE isbn = $1` executes. The engine takes a row-level exclusive lock, re-reads the row under that lock (**this is a current read, not a snapshot read — critical, see Lesson 51 §8**), writes a **new tuple version** with `xmin` = my XID, and sets `xmax` = my XID on the old version.
9. `INSERT INTO orders (…) VALUES (…)` writes a new tuple into `orders`.
10. Each of these changes is appended to the **write-ahead log (WAL)** in memory. Nothing is visible to any other transaction yet.
11. The application issues `COMMIT`.
12. The engine runs **SSI conflict detection**: does this transaction participate in a dangerous structure — a cycle of read-write dependencies with two consecutive rw-antidependencies — with any concurrent transaction?
    - **No conflict** → proceed to step 13.
    - **Conflict** → abort with SQLSTATE `40001`. All versions written by this transaction become permanently invisible. Jump to step 16.
13. The WAL records are flushed and `fsync`ed to durable storage. **This is the moment of durability** (the D of ACID, Lesson 49) and typically the single largest latency component — on the order of 0.5–2 ms on a network-attached SSD.
14. The commit record is written to the commit log (`pg_xact`). From this instant the transaction's XID reads as committed, and its new tuple versions become visible to snapshots taken afterwards.
15. The application returns `201 Created`. The old `inventory` tuple is now dead and awaits vacuum. **Done.**
16. (Conflict path.) The application catches `40001`, waits a randomised backoff interval (Lesson 17), and **re-executes the entire transaction from step 2** — including re-reading the stock, because the previously read value is now known to be unusable. The retry must be idempotent at the business level: it must not, for example, charge the card twice.

Two observations. First, step 3 — not step 2 — is where the snapshot is born; `BEGIN` alone is cheap. Second, step 16 is application code, not database code. **There is no isolation level that removes the need for step 16 at anything above READ COMMITTED.**

## 6. ASCII diagram

```
READ COMMITTED — a NEW snapshot per statement
                 (PostgreSQL default)

  T1: BEGIN ─────────────────────────────────────────────────────▶
          │                      │                       │
       [snap A]              [snap B]                [snap C]
       SELECT stock          SELECT stock            SELECT stock
          → 7                   → 6                     → 3
          │                      │                       │
  T2: ────┼──BEGIN─ UPDATE stock=6 ─COMMIT──┼─ BEGIN UPDATE stock=3 COMMIT
          │                      ▲                       ▲
          └── every statement looks at the world again ──┘

  ⇒ THREE DIFFERENT ANSWERS inside ONE transaction.
    All three were committed values. None was "wrong".
    This is NON-REPEATABLE READ, and it is ALLOWED here.


REPEATABLE READ / SNAPSHOT ISOLATION — ONE snapshot, frozen
                 (PostgreSQL RR, MySQL RR plain reads)

  T1: BEGIN ─────────────────────────────────────────────────────▶
       [snap A taken once, at first statement]
          │                      │                       │
       SELECT stock          SELECT stock            SELECT stock
          → 7                   → 7                     → 7
          │                      │                       │
  T2: ────┼──BEGIN UPDATE stock=6 COMMIT──┼─ BEGIN UPDATE stock=3 COMMIT
          │                                                │
          └── T1 is reading a PHOTOGRAPH of time t0 ────────┘

  ⇒ Perfectly stable. Also perfectly STALE: real stock is 3,
    T1 still sees 7. If T1 now writes based on 7, PostgreSQL
    aborts it (40001). MySQL does not — it blocks, then proceeds.


WRITE SKEW — the anomaly SNAPSHOT ISOLATION CANNOT CATCH
             stock_on_hand = 2, both reserve 2

  T1 (alice)                          T2 (bob)
  ──────────────────────────────────────────────────────────────
  BEGIN  [snap S1]
  SELECT sum(qty) → 0
                                      BEGIN  [snap S2]
                                      SELECT sum(qty) → 0
  0 + 2 <= 2  ✓
                                      0 + 2 <= 2  ✓
  INSERT reservation(alice, 2)
                                      INSERT reservation(bob, 2)
  COMMIT  ✓ ok                        COMMIT  ✓ ok   ← BOTH SUCCEED
  ──────────────────────────────────────────────────────────────
         READ SETS OVERLAP        WRITE SETS DO NOT
                 │                        │
                 │                        └─ so first-committer-wins
                 │                           finds NOTHING to conflict
                 └─ but each wrote a row the other needed to see

  Result: 4 copies reserved, 2 in the warehouse. Silent.
  Only SERIALIZABLE (SSI or 2PL) rejects this.


MVCC VERSION CHAIN — what is physically on disk

   inventory row, isbn='978-0-11-111111-1'

   ┌──────────────────────────────────────────────────┐
   │ xmin=100  xmax=142   stock=7   ← DEAD, but kept  │
   │                                  while any old   │
   │                                  snapshot exists │
   ├──────────────────────────────────────────────────┤
   │ xmin=142  xmax=187   stock=6   ← DEAD            │
   ├──────────────────────────────────────────────────┤
   │ xmin=187  xmax=0     stock=3   ← LIVE            │
   └──────────────────────────────────────────────────┘
        ▲                       ▲
        │                       │
   snapshot with            snapshot with
   xmax_horizon=120         xmax_horizon=200
   sees stock=7             sees stock=3

   A long-running T with horizon=120 PINS the top row.
   Until it ends, VACUUM cannot reclaim ANY of this.
   ⇒ bloat ⇒ slower scans ⇒ eventually XID wraparound.


SSI CONFLICT DETECTION — the "dangerous structure"

      T1 ──rw──▶ T2 ──rw──▶ T3
         (T1 read x,     (T2 read y,
          T2 wrote x)     T3 wrote y)

   Two CONSECUTIVE rw-antidependencies in a cycle
   ⇒ the execution may not be serializable
   ⇒ PostgreSQL aborts one of them with 40001.
   Note: it may be a FALSE POSITIVE. Retry, do not investigate.
```

## 7. Detailed example

### 7.1 BookNook — the signed-edition incident

BookNook runs PostgreSQL 15 on the default level, READ COMMITTED. The catalogue is large, contention is normally negligible, and for two years nothing goes wrong.

Then marketing announces a signed, numbered edition: 500 copies, one per customer, announced at 19:00. At 19:00:00 roughly 4,000 customers hit `POST /orders` within six seconds. Every one of them touches the **same inventory row**. Contention goes from ~0 to total.

**What the code did.** The checkout handler read stock, decided, then wrote:

```sql
BEGIN;                                             -- READ COMMITTED (default)
SELECT stock FROM inventory WHERE isbn = '978-X';  -- app receives 500
-- application: if (stock > 0) { newStock = stock - 1 }
UPDATE inventory SET stock = 499 WHERE isbn = '978-X';  -- absolute value!
INSERT INTO orders (...) VALUES (...);
COMMIT;
```

**What happened.** Hundreds of transactions read `500` within the same few milliseconds, each computed `499`, and each wrote `499`. The row ended at a value nowhere near correct, 500 copies were nominally still available long after they were gone, and **1,180 orders were accepted for 500 books**. This is **lost update** (Lesson 51, §Lost update), and READ COMMITTED permits it by design.

**The first fix attempt, and why it only half worked.** An engineer set the level to `REPEATABLE READ`, reasoning that a stable snapshot would fix it. Under PostgreSQL REPEATABLE READ, the `UPDATE` of a row already modified and committed by another transaction since the snapshot raises `40001`. So the lost updates **stopped** — and were replaced by a **91 % error rate**, because the application had no retry logic. Customers saw 500s instead of overselling. Strictly better (no oversell) and operationally a disaster.

**The second fix: eliminate the read-modify-write.** The correct change was not an isolation level at all:

```sql
BEGIN;                                             -- READ COMMITTED is now FINE
UPDATE inventory
   SET stock = stock - 1                           -- relative, computed by the ENGINE
 WHERE isbn = '978-X'
   AND stock > 0;                                  -- the invariant, in the statement
-- if 0 rows affected → sold out → ROLLBACK and return 409 Conflict
INSERT INTO orders (...) VALUES (...);
COMMIT;
```

This is safe at READ COMMITTED because the `UPDATE` performs a **current read under an exclusive row lock** — it re-reads the latest committed value at the moment of writing, not the snapshot value. Concurrent updaters queue on the lock and each sees the previous one's result. Lesson 51 §8 dissects exactly why this works and the read-then-write version does not.

**The third fix: the invariant that spans rows.** Six weeks later BookNook added "reserve a copy for 15 minutes before payment". That invariant — *sum of open reservations ≤ stock* — is the write-skew shape of §4.5. The atomic `UPDATE` does not help, because the decision reads a *set* of reservation rows. Here the team did three things:

1. Set `SERIALIZABLE` **for that one endpoint only** (`SET TRANSACTION ISOLATION LEVEL SERIALIZABLE` after `BEGIN`), leaving every other endpoint on READ COMMITTED.
2. Added retry-on-`40001` with jittered exponential backoff, capped at 3 attempts (Lesson 17).
3. Added `reserved` as a counter column on `inventory` so the hot path could later be de-escalated to an atomic update if SSI retries became costly.

Measured result at 3,000 req/s on the reservation endpoint: **p50 latency +1.8 ms, p99 +14 ms, 2.7 % of transactions retried once, 0.04 % twice, zero invariant violations.** That is the real price of serializability on a well-designed schema, and it is cheap.

### 7.2 A contrasting example — a hotel booking engine

A hotel reservation system has a structurally different problem, and it is worth seeing because the *right answer is different*.

The invariant: *no two confirmed bookings may overlap in date range for the same room.* Room 412, nights of 3–5 March.

This is **not** a counter. It is a **range-overlap predicate across rows** — the hardest shape for isolation, because the conflicting row may not exist when you check:

```sql
-- T1 and T2 both run this for room 412, overlapping dates
SELECT count(*) FROM bookings
 WHERE room_id = 412
   AND date_range && daterange('2026-03-03','2026-03-06');  -- overlap operator
-- both get 0 under snapshot isolation
INSERT INTO bookings (room_id, date_range, guest) VALUES (412, ..., ...);
```

Identical write-skew shape to BookNook's reservations — and here even SERIALIZABLE, while correct, is not the best tool. A PostgreSQL **exclusion constraint** pushes the invariant into the engine:

```sql
ALTER TABLE bookings
  ADD CONSTRAINT no_overlap
  EXCLUDE USING gist (room_id WITH =, date_range WITH &&);
-- Now the second INSERT fails with 23P01 exclusion_violation at ANY isolation level.
```

The contrast to carry away:

| | BookNook stock | Hotel room-nights |
|---|---|---|
| Invariant shape | A single counter ≥ 0 | No overlapping ranges across rows |
| Expressible in one row? | **Yes** | No |
| Expressible as a constraint? | Yes — `CHECK (stock >= 0)` | Yes — `EXCLUDE USING gist` |
| Cheapest correct fix | Atomic relative `UPDATE`, READ COMMITTED | Exclusion constraint, any level |
| Needed SERIALIZABLE? | Only for the reservation variant | No |

**The generalisable lesson: before reaching for an isolation level, ask whether the invariant can be pushed into the schema.** A constraint is enforced at every level, by every client, in every language, forever. An isolation level is enforced only by whichever code paths remembered to set it.

## 8. Code or configuration example

> **These snippets are illustrative teaching examples, not production-ready code.** They omit connection handling, parameterisation details, error classification beyond the one code shown, metrics, timeouts, and the idempotency keys any real payment path requires. Do not paste them into a service.

### 8.1 Observing the levels differ — run these in two terminals

```sql
-- ========== SESSION T1 ==========
-- PostgreSQL. Setup, run once:
-- CREATE TABLE inventory (isbn text PRIMARY KEY, stock int NOT NULL CHECK (stock >= 0));
-- INSERT INTO inventory VALUES ('978-X', 7);

BEGIN TRANSACTION ISOLATION LEVEL READ COMMITTED;  -- the PostgreSQL default
SELECT stock FROM inventory WHERE isbn = '978-X';  -- → 7
                                                   -- snapshot taken for THIS statement only
-- now switch to T2 and run it to completion
```

```sql
-- ========== SESSION T2 ==========
BEGIN;
UPDATE inventory SET stock = 3 WHERE isbn = '978-X';  -- new tuple version written
COMMIT;                                               -- now visible to NEW snapshots
```

```sql
-- ========== BACK IN SESSION T1 ==========
SELECT stock FROM inventory WHERE isbn = '978-X';  -- → 3  ← NON-REPEATABLE READ
                                                   -- a fresh snapshot for this statement
COMMIT;
```

Now the identical interleaving one level higher:

```sql
-- ========== SESSION T1 ==========
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ;
SELECT stock FROM inventory WHERE isbn = '978-X';  -- → 3
                                                   -- snapshot FROZEN here, for the whole txn
-- (T2 again sets stock = 1 and commits)
SELECT stock FROM inventory WHERE isbn = '978-X';  -- → 3  ← STABLE. Also STALE.
SELECT count(*) FROM inventory WHERE stock < 5;    -- stable too: NO PHANTOMS in PostgreSQL RR
COMMIT;
```

And the write-conflict behaviour that REPEATABLE READ adds:

```sql
-- ========== SESSION T1 ==========
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ;
SELECT stock FROM inventory WHERE isbn = '978-X';  -- → 3, snapshot frozen
-- (T2 sets stock = 1 and COMMITs here)
UPDATE inventory SET stock = 2 WHERE isbn = '978-X';
-- ERROR:  could not serialize access due to concurrent update
-- SQLSTATE: 40001
-- ^ PostgreSQL REFUSES to lose the update. The whole transaction must be retried.
```

Contrast MySQL InnoDB at its default REPEATABLE READ, where **the same interleaving does not error**:

```sql
-- ========== MySQL SESSION T1 ==========
START TRANSACTION;                                      -- REPEATABLE READ (MySQL default)
SELECT stock FROM inventory WHERE isbn='978-X';         -- → 3   (consistent read, snapshot)
-- (T2 sets stock = 1 and COMMITs)
SELECT stock FROM inventory WHERE isbn='978-X';         -- → 3   (still the snapshot)
SELECT stock FROM inventory WHERE isbn='978-X' FOR UPDATE; -- → 1 ← CURRENT read! Bypasses snapshot.
UPDATE inventory SET stock = 2 WHERE isbn='978-X';      -- succeeds, NO error raised
COMMIT;                                                 -- final value 2, computed from a mix
```

**Two reads of the same row in one transaction returned 3 and 1.** That is MySQL behaving exactly as documented, at a level whose name is "repeatable read". This is the §4.3 point made concrete.

### 8.2 Reproducing write skew, then fixing it three ways

```sql
-- Setup
-- CREATE TABLE inventory (isbn text PRIMARY KEY, stock_on_hand int NOT NULL);
-- CREATE TABLE reservations (
--   id bigserial PRIMARY KEY, isbn text NOT NULL,
--   customer text NOT NULL, qty int NOT NULL,
--   status text NOT NULL DEFAULT 'open');
-- INSERT INTO inventory VALUES ('978-X', 2);

-- ===== T1 (alice) =====            -- ===== T2 (bob) =====
BEGIN ISOLATION LEVEL REPEATABLE READ;
SELECT coalesce(sum(qty),0)          -- reads the SET of reservation rows
  FROM reservations
 WHERE isbn='978-X' AND status='open';   -- → 0
                                     BEGIN ISOLATION LEVEL REPEATABLE READ;
                                     SELECT coalesce(sum(qty),0)
                                       FROM reservations
                                      WHERE isbn='978-X' AND status='open';  -- → 0
-- app check: 0 + 2 <= 2  → allowed
                                     -- app check: 0 + 2 <= 2 → allowed
INSERT INTO reservations
  (isbn,customer,qty) VALUES ('978-X','alice',2);   -- writes a DIFFERENT row ...
                                     INSERT INTO reservations
                                       (isbn,customer,qty) VALUES ('978-X','bob',2);
COMMIT;  -- ✓                        COMMIT;  -- ✓  ← no conflict detected, BOTH land
-- total reserved = 4, stock_on_hand = 2. Invariant broken, silently.
```

**Fix A — raise the level to SERIALIZABLE.** Identical SQL, one word changed:

```sql
BEGIN ISOLATION LEVEL SERIALIZABLE;   -- SSI now tracks the read set of the sum()
SELECT coalesce(sum(qty),0) FROM reservations
 WHERE isbn='978-X' AND status='open';
INSERT INTO reservations (isbn,customer,qty) VALUES ('978-X','bob',2);
COMMIT;
-- ERROR: could not serialize access due to read/write dependencies among transactions
-- SQLSTATE: 40001   ← one of the two is aborted. The application retries; on retry it
--                      reads sum = 2, computes 2 + 2 > 2, and correctly refuses.
```

**Fix B — materialise the conflict onto a shared row.** Works at REPEATABLE READ, and even at READ COMMITTED:

```sql
BEGIN;
UPDATE inventory                      -- BOTH transactions now write the SAME row,
   SET reserved = reserved + 2        -- converting write skew into a write-write conflict
 WHERE isbn = '978-X'
   AND reserved + 2 <= stock_on_hand; -- the invariant, evaluated by the engine under lock
-- 0 rows affected → not enough stock → ROLLBACK, return 409
INSERT INTO reservations (isbn,customer,qty) VALUES ('978-X','bob',2);
COMMIT;
```

**Fix C — lock the predicate's anchor row explicitly.** Serialises all reservers of this ISBN:

```sql
BEGIN;
SELECT stock_on_hand FROM inventory
 WHERE isbn='978-X' FOR UPDATE;       -- exclusive row lock; T2 BLOCKS here until T1 commits
SELECT coalesce(sum(qty),0) FROM reservations
 WHERE isbn='978-X' AND status='open';   -- now guaranteed to see T1's committed insert
INSERT INTO reservations (isbn,customer,qty) VALUES ('978-X','bob',2);
COMMIT;
```

Fix C is correct but **pessimistic**: every reserver waits, even when stock is plentiful. Fix B is correct and cheap but requires the counter to exist. Fix A is correct, requires no schema change, and costs retries. Lesson 52 (*Phase 3, Topic 13 — Optimistic versus pessimistic concurrency control*) is exactly the choice between B/A and C.

### 8.3 Setting the level, and retrying properly

```sql
-- Per transaction (preferred — scope it to the endpoint that needs it):
BEGIN TRANSACTION ISOLATION LEVEL SERIALIZABLE;

-- Or, after BEGIN but before the first statement:
BEGIN;
SET TRANSACTION ISOLATION LEVEL SERIALIZABLE;   -- fails if any statement already ran

-- Per session (affects every later transaction on this pooled connection — DANGEROUS,
-- because connection pools hand that connection to unrelated requests):
SET SESSION CHARACTERISTICS AS TRANSACTION ISOLATION LEVEL SERIALIZABLE;

-- Cluster-wide default (postgresql.conf — requires reload):
-- default_transaction_isolation = 'read committed'

-- Inspect what you actually have:
SHOW default_transaction_isolation;    -- the configured default
SELECT current_setting('transaction_isolation');  -- the level of THIS transaction
```

```python
# Illustrative retry wrapper. NOT production code: no metrics, no jittered cap tuning,
# no distinction between retryable business errors, no idempotency key on the payment.
import random, time, psycopg

SERIALIZATION_FAILURE = "40001"   # could not serialize access
DEADLOCK_DETECTED     = "40P01"   # deadlock victim — also retryable

def run_checkout(conn, isbn, customer, max_attempts=3):
    for attempt in range(max_attempts):
        try:
            with conn.transaction():                       # BEGIN ... COMMIT
                conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                total, = conn.execute(
                    "SELECT coalesce(sum(qty),0) FROM reservations "
                    "WHERE isbn=%s AND status='open'", (isbn,)).fetchone()
                stock, = conn.execute(
                    "SELECT stock_on_hand FROM inventory WHERE isbn=%s",
                    (isbn,)).fetchone()
                if total + 1 > stock:
                    raise SoldOut(isbn)                    # business error: do NOT retry
                conn.execute(
                    "INSERT INTO reservations (isbn,customer,qty) VALUES (%s,%s,1)",
                    (isbn, customer))
            return "reserved"
        except psycopg.errors.OperationalError as e:
            if e.sqlstate not in (SERIALIZATION_FAILURE, DEADLOCK_DETECTED):
                raise                                      # not a concurrency problem
            if attempt == max_attempts - 1:
                raise                                      # give up; surface a 503
            # Full jitter backoff (Lesson 17). Retrying immediately causes a thundering
            # herd of transactions that conflict with each other all over again.
            time.sleep(random.uniform(0, 0.05 * (2 ** attempt)))
    # The ENTIRE transaction is re-run: the earlier reads are known-unusable.
```

The two non-obvious details: the retry re-runs **the whole transaction including the reads** (retrying only the write would re-apply a decision made on stale data), and the backoff is **jittered** (a fixed delay re-synchronises the conflicting transactions and they collide again).

## 9. Numerical example

**Question: at BookNook's real traffic, how much does SERIALIZABLE cost, and how many retries will it generate?**

**Assumptions — all stated explicitly, all labelled.**

| Symbol | Meaning | Value | Basis |
|---|---|---|---|
| `R` | Checkout transactions per second at peak | 3,000 /s | Measured peak, Black Friday |
| `D` | Mean database-side transaction duration | 8 ms | Measured p50 |
| `C` | Catalogue size (distinct ISBNs in checkouts) | 50,000 | Typical for BookNook |
| `h` | Share of checkouts hitting the top 10 "hot" ISBNs | 20 % | Typical Zipf-ish skew; **approximate** |
| `f` | SSI false-positive abort rate at low contention | 0.5 % | Typical observed; **approximate** |
| `k` | Throughput penalty of SSI bookkeeping, low contention | 7 % | Mid-point of the 5–10 % band in §4.9 |

**Step 1 — how many transactions are concurrent at any instant?**

By Little's Law (`L = λ × W`), the mean number of in-flight transactions is:

```
L = R × D
  = 3,000 transactions/s × 0.008 s
  = 24 concurrent transactions
```

So at peak, on average **24 transactions overlap in time**. This is the number that matters: two transactions can only conflict if they overlap.

**Step 2 — how many of those are on a hot ISBN?**

```
Hot-path rate      = R × h          = 3,000 × 0.20 = 600 transactions/s
Hot-path in-flight = 600 × 0.008 s  = 4.8 concurrent transactions
Spread over 10 hot ISBNs           = 4.8 / 10 = 0.48 concurrent per hot ISBN
```

**Interpretation:** even on the hottest title, fewer than one transaction is in flight at a time *on average*. Contention is **low** under normal peak traffic. This is why SERIALIZABLE is affordable here.

**Step 3 — contention on the cold tail.**

```
Cold-path rate      = 3,000 × 0.80 = 2,400 transactions/s
Cold-path in-flight = 2,400 × 0.008 = 19.2 concurrent
Distinct cold ISBNs ≈ 50,000
Concurrent per cold ISBN = 19.2 / 50,000 = 0.000384
```

Effectively zero. 80 % of traffic will essentially never conflict with anything.

**Step 4 — expected conflict rate on a hot ISBN.**

Model arrivals to one hot ISBN as Poisson with rate `λ_hot = 600 / 10 = 60 /s`. A transaction is exposed to conflict for its full duration `D = 8 ms`. The probability that **at least one other** transaction on the same ISBN starts during that window:

```
P(conflict) = 1 − e^(−λ_hot × D)
            = 1 − e^(−60 × 0.008)
            = 1 − e^(−0.48)
            = 1 − 0.6188
            = 0.3812   →  38.1 %
```

**Step 5 — convert to aborts.** Not every overlap is a serialization failure; only those whose read/write sets actually interfere. Assume (**assumption, approximate**) that 25 % of overlapping hot-ISBN transaction pairs genuinely conflict:

```
Hot-path abort rate  = 0.381 × 0.25 = 0.0953  → 9.5 % of hot-path transactions
Hot-path aborts/sec  = 600 × 0.0953 = 57.2 aborts/s
Cold-path aborts/sec ≈ 2,400 × f    = 2,400 × 0.005 = 12.0 aborts/s (false positives)
Total aborts/sec     = 57.2 + 12.0  = 69.2 aborts/s
Overall abort rate   = 69.2 / 3,000 = 0.0231  → 2.31 % of all transactions
```

**Step 6 — the retry cost.** Each abort re-runs a transaction costing `D = 8 ms`:

```
Extra DB work  = 69.2 retries/s × 0.008 s = 0.554 seconds of DB time per second
Baseline work  = 3,000 × 0.008            = 24.0 seconds of DB time per second
Retry overhead = 0.554 / 24.0             = 2.31 %   (identical to the abort rate, as expected)
```

**Step 7 — total cost of SERIALIZABLE.**

```
Bookkeeping overhead (k)        = 7.0 %
Retry overhead (step 6)         = 2.31 %
Combined ≈ 1 − (1 − 0.070) × (1 − 0.0231)
         = 1 − 0.930 × 0.9769
         = 1 − 0.9085
         = 0.0915   →  about 9.2 % less throughput
```

**Step 8 — express it in money and risk.**

```
Effective capacity at SERIALIZABLE = 3,000 × (1 − 0.0915) = 2,725 transactions/s
To restore 3,000 /s you need      = 3,000 / 2,725 = 1.101  → 10.1 % more DB capacity
If the primary is one 16-vCPU instance, that is roughly 1.6 extra vCPU of headroom.
```

**Step 9 — latency impact for the 2.31 % that retry.** With full-jitter backoff on attempt 0, the mean added delay is `0.05 s / 2 = 25 ms`, plus a re-run of 8 ms:

```
Added latency for a retried transaction ≈ 25 + 8 = 33 ms
Contribution to the fleet-wide mean    = 0.0231 × 33 ms = 0.76 ms
p99 impact: since 2.31 % > 1 %, retries DO reach the 99th percentile.
p99 therefore shifts by roughly the retry cost, ≈ +33 ms in the worst case.
```

**Conclusion.** For about **10 % more database capacity and ~0.8 ms on the mean**, BookNook eliminates an entire class of silent correctness bugs. If `h` rose to 90 % (a single viral title), step 4 would give `λ_hot = 2,700/s`, `P(conflict) = 1 − e^(−21.6) ≈ 100 %`, and SERIALIZABLE would collapse. **The honest statement is therefore not "SERIALIZABLE is cheap" but "SERIALIZABLE is cheap when per-row concurrency `λ_row × D` is well below 1, and unusable when it exceeds about 1."** That single quantity — expected concurrent transactions per contended row — is the number to compute before choosing a level.

## 10. Trade-offs

| Dimension | Weaker isolation (READ UNCOMMITTED / READ COMMITTED) | Stronger isolation (REPEATABLE READ / SERIALIZABLE) |
|---|---|---|
| **Throughput** | Higher; near-zero coordination | 5–10 % lower at low contention; collapses at high contention |
| **Latency** | Predictable; no aborts | Mean barely moves; p99 is dominated by retries |
| **Application complexity** | You must hand-write every concurrency guard (locks, version columns, atomic updates) and remember them everywhere | Guards are centralised in the engine; but you **must** implement retry-on-40001 |
| **Failure mode when you get it wrong** | **Silent wrong data.** Oversold stock, wrong totals, no error, no log line | **Loud errors.** A retryable exception you can count, alert on, and graph |
| **Reasoning burden** | You must enumerate every interleaving of every pair of transactions, forever, including code written next year | Reason about one transaction in isolation |
| **Portability** | Behaviour differs wildly across engines (§4.3) | SERIALIZABLE is the one level that means roughly the same thing everywhere — except Oracle |
| **Debuggability** | Anomalies are non-deterministic, load-dependent and often unreproducible in staging | Conflicts surface as a specific SQLSTATE with a stack trace |
| **MVCC bloat** | Shorter transactions → less version retention | Longer transactions hold the vacuum horizon open |
| **Cost of being wrong later** | Fixing a lost-update bug in production requires auditing historical data | Over-isolating is a config change away from being relaxed |

Three asymmetries worth internalising:

1. **The failure modes are not symmetric.** Too-weak isolation fails silently and corrupts data. Too-strong isolation fails loudly and costs money. **Prefer loud failures.** You can always relax a level; you cannot un-oversell a book.
2. **The costs are not symmetric in time.** Isolation cost is paid continuously in CPU. Anomaly cost is paid rarely but in large, manual, reputational chunks.
3. **Contention, not the level, dominates.** A hot row is slow at every level and *wrong* at low levels. The highest-leverage change is almost always removing the hot row (sharding the counter, queueing the writes — *Phase 4, Topic 5 — Message queues*), not tuning isolation.

## 11. Failure scenarios

| Failure | What the user experiences | How it is detected | How it recovers |
|---|---|---|---|
| **Lost update at READ COMMITTED** (read-modify-write on a hot row) | Buys the last copy; receives a cancellation e-mail hours later. Or: a saved profile edit silently vanishes | Not detected by the database at all. Found by inventory reconciliation, customer complaints, or a `stock < 0` constraint violation much later | Manual data repair plus refunds. Code fix: atomic relative `UPDATE`, `SELECT FOR UPDATE`, or a version column (Lesson 51 §Fixes) |
| **Unhandled `40001` after raising the level** | A 500 error page at checkout; the order is not placed | Error-rate alert; logs full of `could not serialize access`; the spike correlates exactly with the deploy that changed the level | Deploy retry logic, or revert the level. The data is **correct** throughout — this failure is loud and harmless to data |
| **Retry storm** (retries without jitter) | Checkout latency rises from 40 ms to seconds; some requests time out | Abort rate and request rate rise together; DB CPU saturated while committed throughput falls | Add full-jitter backoff and an attempt cap (Lesson 17); shed load; reduce contention on the hot row |
| **Deadlock under `SELECT FOR UPDATE`** | Intermittent 500s on a specific endpoint, worse at peak | `40P01 deadlock detected` in logs; PostgreSQL prints both transactions' queries; MySQL `SHOW ENGINE INNODB STATUS` | Retry the victim. Permanent fix: acquire locks in a **consistent global order** (e.g. always ascending ISBN) so a cycle cannot form |
| **MVCC bloat from a long-running transaction** | Gradual, site-wide slowdown over days with no traffic change; disk usage climbs | `pg_stat_activity` shows a session `idle in transaction` for hours; table size ≫ live tuple count; autovacuum log shows rows "not yet removable" | Terminate the offending session; `VACUUM`. Prevent with `idle_in_transaction_session_timeout` and `statement_timeout` |
| **Transaction-ID wraparound** | Total write outage: the database refuses all new writes | `WARNING: database must be vacuumed within N transactions`; then a hard refusal | Emergency single-user vacuum; extended downtime. Prevent by alerting on `age(datfrozenxid)` long before the threshold |
| **`ORA-01555 snapshot too old`** (Oracle) / undo exhaustion | A long report fails mid-run after 40 minutes | The error itself, in the job log | Enlarge undo retention/tablespace, or shorten the reader. Structurally: run long reads on a replica |
| **Write skew surviving SERIALIZABLE on Oracle** | Invariant violated despite the level being set to the highest available | Only by business reconciliation — the engine reports success | Materialise the conflict (§8.2 Fix B) or lock explicitly (Fix C). Oracle's SERIALIZABLE cannot be relied on for this |
| **Level silently leaking across a connection pool** | Unrelated endpoints start throwing `40001`, or an endpoint that needs SERIALIZABLE silently runs at READ COMMITTED | Confusing, intermittent, endpoint-uncorrelated errors | Never use `SET SESSION CHARACTERISTICS` with a pool. Set the level **per transaction**, after `BEGIN` |
| **Replica reads at a different level** | A read-after-write check on a replica sees stale data despite high isolation on the primary | Replication-lag dashboard (Lesson 34) | Isolation is per-node; it says nothing about replication. Route read-after-write to the primary (Lesson 34) |

The row to re-read is the first one. **The most damaging isolation failure produces no error, no log entry and no alert.** Everything else in this table announces itself.

## 12. Alternatives

Isolation levels are one way to coordinate concurrent writers. These are the others, and most production systems use several at once.

| Approach | Mechanism | Prevents | Cost | Use when |
|---|---|---|---|---|
| **Isolation levels (SSI)** | Engine tracks read/write dependencies and aborts cycles | All anomalies, including ones you did not anticipate | 5–10 % throughput plus retries; collapses under high per-row contention | General-purpose correctness; complex multi-row invariants |
| **Two-phase locking** | Shared/exclusive locks held to commit | All anomalies | Readers block writers; deadlocks | Legacy engines; workloads with very short transactions |
| **Explicit pessimistic locks** (`SELECT … FOR UPDATE`) | Lock the specific rows you will decide on | Lost update, write skew (on locked rows) | Serialises contenders; deadlock risk; requires consistent lock ordering | One identifiable "anchor" row guards the invariant |
| **Optimistic concurrency / version column** | `UPDATE … WHERE version = :seen` and check rows-affected | Lost update | Wasted work on conflict; app must retry | Low conflict probability; long think-time (a user editing a form) |
| **Atomic relative updates** | `SET stock = stock - 1 WHERE stock > 0` — the engine computes under lock | Lost update, on that row | Essentially free | The invariant is a single-row counter. **Almost always the best answer when applicable** |
| **Database constraints** | `CHECK`, `UNIQUE`, `EXCLUDE USING gist`, foreign keys | Exactly the stated invariant, at every level, for every client | Index maintenance; an error to handle | The invariant is expressible declaratively. Push it here first |
| **Single-writer / serial execution** | Route all writes for a key to one thread or partition | Everything, by construction | Throughput capped by one core per key; a scaling ceiling | Redis, VoltDB-style designs; per-key actor models |
| **Message queue serialisation** | Writes become ordered events on a per-key partition, consumed one at a time | Everything for that key | Latency; eventual consistency; operational complexity | Extreme write contention on one key — flash sales. *Phase 4, Topic 5 — Message queues* |
| **CRDTs / commutative operations** | Design operations so that order does not matter | Conflicts, by making them impossible | Only some operations are expressible (counters, sets); not stock-with-a-floor | Multi-master or offline-first systems. *Phase 5 — Conflict-free replicated data types* |
| **Deterministic databases** (Calvin-style) | Agree the transaction order *before* execution | Everything | Transactions must declare their read/write sets up front | Specialised; rarely a choice you make |

The ordering heuristic that works in practice: **constraints → atomic relative updates → optimistic versioning → explicit locks → higher isolation level → architectural serialisation.** Move down the list only when the level above genuinely cannot express the invariant. Each step down costs more and is easier to get wrong.

## 13. When to use it

Raise the isolation level **above the default** when:

1. **The invariant spans multiple rows and the decision is read-then-write.** Reserve-then-confirm, double-booking, "at most N active X per Y", "the sum of these must not exceed that", balance checks across two accounts. This is the write-skew shape of §4.5, and SERIALIZABLE is the only general answer.
2. **A transaction makes several reads that must agree with each other.** A financial close, an invoice reconciliation, an export that must be internally consistent, a data-integrity checker. REPEATABLE READ (snapshot isolation) gives a stable photograph and prevents **read skew** (Lesson 51). Reading accounts A and B at READ COMMITTED can show money in neither or both.
3. **The business consequence of an anomaly is irreversible or regulated.** Money movement, medical records, ticketing, legal audit trails. The asymmetry argument of §10 applies with full force: a retry is cheap, a double-spend is not.
4. **You are writing a multi-statement report inside a transaction.** Any long `SELECT` sequence should be `REPEATABLE READ` so page 1 and page 7 of the report describe the same world. (Run it on a replica if the vacuum horizon matters — Lesson 34.)
5. **You have just discovered an anomaly in production and need to stop the bleeding today.** Raising the level plus adding retries is a smaller, safer, faster change than redesigning the schema, and it buys time for the proper fix.
6. **Per-row concurrency is low.** From §9: if `λ_row × D ≪ 1`, strong isolation costs almost nothing. Compute this number before assuming otherwise.

Lower the level **below the default**, or keep it at READ COMMITTED, when:

- The transaction is a single statement. A single statement is atomic at every level; raising the level buys nothing.
- The invariant is already a constraint or an atomic relative update.
- The read is an approximate dashboard metric where a few milliseconds of skew is irrelevant.

## 14. When not to use it

Do **not** raise the isolation level when:

1. **The application cannot retry.** At REPEATABLE READ and above you will receive `40001`. Without retry logic you have converted silent data corruption into a loud error rate — occasionally the right emergency trade, never the right end state. **Build the retry path first, then raise the level.**
2. **Per-row contention is already high.** With `λ_row × D > 1` (§9), raising the level produces an abort storm: transactions spend more time retrying than committing, and throughput can fall *below* the serial rate. The fix is to remove the hot row — shard the counter into N sub-counters, or serialise writes through a queue — not to tune isolation.
3. **The invariant is expressible as a constraint.** `UNIQUE`, `CHECK (stock >= 0)` and `EXCLUDE USING gist` are enforced at every level, by every client, including the script someone runs by hand at 2 a.m. An isolation level is only enforced by code that remembered to set it.
4. **You are on Oracle and expecting SERIALIZABLE to prevent write skew.** It will not (§4.3). Using it there creates false confidence, which is worse than known weakness.
5. **The transaction is long-running.** A 40-minute report at REPEATABLE READ pins the vacuum horizon for 40 minutes, bloating every table the rest of the system is writing to. Run long reads on a replica, or accept READ COMMITTED and chunk the work.
6. **You are "fixing" a bug you have not diagnosed.** Raising the level because a bug smells like concurrency, without identifying *which anomaly*, usually changes the symptom rather than the cause — and leaves a permanent throughput tax plus an unexplained config line.

And a case that looks like isolation but is not: **replication lag is not an isolation problem.** If a user writes to the primary and immediately reads a stale value from a replica, no isolation level on either node fixes it. That is read-after-write consistency, and it is solved by routing (Lesson 34).

## 15. Common mistakes

### Beginner mistakes

- **Believing the level name describes the behaviour.** "REPEATABLE READ" does not mean the same thing in PostgreSQL and MySQL (§4.3). Always check `engine + version + locking-or-not`.
- **Thinking SERIALIZABLE means transactions run one at a time.** It means the *outcome* is equivalent to some serial order. They run concurrently; the engine only intervenes on actual conflict.
- **Assuming the default is READ COMMITTED everywhere.** MySQL defaults to REPEATABLE READ. Code validated on one is not validated on the other.
- **Setting the level once in session configuration while using a connection pool.** The pool hands that connection to the next unrelated request, which inherits a level nobody chose.
- **Wrapping a single statement in a transaction and raising the level.** A single statement is already atomic. Nothing was gained.
- **Confusing isolation with atomicity.** Atomicity is all-or-nothing on failure. Isolation is what *concurrent* transactions see. Rolling back correctly says nothing about what others observed meanwhile.
- **Testing concurrency with one connection.** Anomalies require overlapping transactions. A sequential integration test proves nothing about isolation.
- **Reading, deciding, then writing an absolute value** — `UPDATE stock = 499` instead of `stock = stock - 1`. This is the single most common production concurrency bug in the world (Lesson 51).
- **Expecting a `CHECK` constraint to protect an aggregate.** `CHECK` sees one row. It cannot express "sum of a filtered set".

### Experienced-engineer mistakes

- **Assuming snapshot isolation is serializable.** It is not. Write skew is the gap, it is invisible, and it defeats Oracle's SERIALIZABLE and PostgreSQL's REPEATABLE READ alike (§4.5).
- **Mixing snapshot reads and locking reads in one MySQL transaction**, then reasoning about the result as if both came from the same view (§8.1).
- **Retrying only the failed statement rather than the whole transaction.** The earlier reads are precisely what became invalid. Retrying the write re-applies a decision made on data the engine has told you not to trust.
- **Retrying without jitter**, re-synchronising the conflicting transactions into a second, identical collision.
- **Treating `40001` as an error to investigate rather than a signal to retry.** In SSI it is frequently a *false positive* by design. Alert on the *rate*, not on individual occurrences.
- **Ignoring the vacuum horizon.** Adding one long-running REPEATABLE READ analytics transaction can degrade a completely unrelated OLTP table, days later, via bloat. The causal link is almost never guessed correctly on the first attempt.
- **Acquiring locks in data-dependent order** (iterating a set, locking as you go), producing deadlocks under load that never appear in testing. Sort the keys before locking.
- **Benchmarking isolation levels with uniformly random keys.** That measures the bookkeeping overhead only, and makes SERIALIZABLE look nearly free. Real workloads are skewed; benchmark with a realistic hot-key distribution.
- **Assuming the level applies across shards or services.** An isolation level is a guarantee of **one database node**. Two statements against two shards are two transactions. That is *Phase 3, Topic 21 — Distributed transactions and two-phase commit*.
- **Relying on gap locks for correctness while planning a MySQL→PostgreSQL migration.** The implicit phantom protection you depended on will not survive the move; PostgreSQL READ COMMITTED has no gap locks.
- **Forgetting that `SELECT FOR UPDATE` does not lock rows that do not exist**, so it cannot prevent an insert-based phantom unless the engine adds range locks.

## 16. Interview perspective

Isolation levels are a high-signal interview topic because the shallow answer and the deep answer are easy to tell apart, and the deep answer predicts whether a candidate has operated a real transactional system.

**Questions you will be asked:**

- "Name the four isolation levels and the anomalies each permits." — Table recall. Necessary, not sufficient.
- "What is the difference between a non-repeatable read and a phantom read?" — The expected answer: one is about a row you already read changing; the other is about the *set of rows matching a predicate* changing. The follow-up is always *why does that distinction matter?* — because preventing phantoms requires locking a predicate, and you cannot lock a row that does not exist yet.
- "Is snapshot isolation the same as serializable?" — **The single highest-signal question in this lesson.** The correct answer is no, followed immediately by write skew and a worked example. Candidates who say yes have read a table; candidates who say no and can draw the timeline have debugged one.
- "Your checkout oversells inventory. Walk me through diagnosis and fix." — They want: identify it as lost update → explain the read-modify-write → propose the atomic relative `UPDATE` first (cheapest), then `SELECT FOR UPDATE`, then a version column, then a higher level, and **say why you would pick each**.
- "What does PostgreSQL's SERIALIZABLE actually do?" — SSI: snapshot isolation plus read-write dependency tracking, aborting dangerous structures with `40001`, non-blocking reads, false positives possible, retries mandatory.
- "Why did your throughput drop after you raised the level?" — Distinguish bookkeeping overhead (constant, small) from abort-and-retry (super-linear in contention). Then ask about the key distribution.

**How to structure a strong answer** (this ordering alone separates candidates):

1. State the **invariant** the transaction must preserve.
2. Identify **which anomaly** threatens it.
3. Name the **cheapest mechanism** that prevents that anomaly — constraint, atomic update, lock, version, level — in that order.
4. State the **cost** in contention terms, ideally with `λ_row × D`.
5. State what the **application must do** in return (retry, backoff, idempotency).

**Things that earn credit immediately:** naming SQLSTATE `40001`; knowing MySQL defaults to REPEATABLE READ; knowing Oracle has no READ UNCOMMITTED and that its SERIALIZABLE is SI; mentioning vacuum/undo pressure from long transactions; saying "it depends on the per-row contention rate" **and then computing it**.

**Red flags interviewers watch for:**

- Reciting the four-level table but being unable to give a concrete example of any anomaly.
- Saying "we just use SERIALIZABLE, so concurrency is handled" with no mention of retries. This says the candidate has never run SERIALIZABLE in production.
- Claiming snapshot isolation prevents all anomalies, or not knowing write skew exists.
- Saying "it depends" about level choice without naming the dependency — the invariant's shape and the per-row contention rate.
- Proposing `SELECT FOR UPDATE` as the universal fix, without mentioning deadlocks or lock ordering.
- Treating a lost update as a database bug rather than an application read-modify-write bug.
- Believing a higher isolation level fixes replica staleness (that is Lesson 34).
- Never mentioning that isolation is a **single-node** guarantee when the question clearly involves two services or two shards.
- Dismissing isolation as "a DBA concern". The anomalies are produced by application code and are fixed in application code.

## 17. Knowledge check

**Conceptual**

1. The SQL standard defines REPEATABLE READ as permitting phantom reads, yet PostgreSQL's REPEATABLE READ does not produce them. Explain precisely why both statements are true simultaneously, and what this implies about reasoning from level names when porting an application between engines.
2. Explain why snapshot isolation prevents dirty reads, non-repeatable reads, phantoms and same-row lost updates, yet still permits write skew. Your explanation must refer to read sets, write sets, and the first-committer-wins rule.
3. Under MVCC, describe the complete causal chain from "an analytics query runs for three hours at REPEATABLE READ" to "unrelated OLTP queries on a different table become 40 % slower two days later." Name each intermediate mechanism.

**Practical scenario**

4. BookNook adds gift cards. The invariant: *the sum of all redemptions against a gift card must never exceed its balance.* Redemption logic reads the card's balance and the sum of prior redemptions, checks the total, then inserts a redemption row. The service runs on PostgreSQL at the default level. A customer discovers that submitting the same gift card from two browser tabs simultaneously redeems the full balance twice. Identify the anomaly by name; explain why raising the level to REPEATABLE READ would and would not help; then give three distinct fixes — one schema-level, one statement-level, one isolation-level — and state which you would ship first and why.

**Calculation**

5. BookNook's flash sale drives 1,200 checkout transactions per second, all against **one** ISBN. Mean database transaction duration is 12 ms. Using Little's Law, compute the mean number of concurrent transactions on that row. Then, modelling arrivals as Poisson, compute the probability that a given transaction overlaps at least one other on that row. Assuming 60 % of overlapping pairs genuinely conflict under SERIALIZABLE, compute the abort rate and the aborts per second. Finally, state with justification whether SERIALIZABLE is viable for this endpoint, and if not, what the per-row concurrency figure tells you to change instead.

**Progress checklist**

```
Phase 1 — Foundations of the Web and Networking ....... COMPLETE (Lessons 1–17)
Phase 2 — Scaling ..................................... COMPLETE (Lessons 18–39)

Phase 3 — Databases and Data Storage
  [x] Topic 1  — Relational databases and the relational model   (Lesson 40)
  [x] Topic 2  — Schema design, keys and normalisation           (Lesson 41)
  [x] Topic 3  — Joins and how they execute                      (Lesson 42)
  [x] Topic 4  — SQL versus NoSQL                                (Lesson 43)
  [x] Topic 5  — Key-value and document stores                   (Lesson 44)
  [x] Topic 6  — Wide-column stores                              (Lesson 45)
  [x] Topic 7  — Graph databases                                 (Lesson 46)
  [x] Topic 8  — Search engines and inverted indexes             (Lesson 47)
  [x] Topic 9  — Choosing a database                             (Lesson 48)
  [x] Topic 10 — ACID transactions                               (Lesson 49)
  [x] Topic 11 — Isolation levels                                (Lesson 50)  ← you are here
  [ ] Topic 12 — Dirty reads, non-repeatable reads, phantom reads, lost updates
  [ ] Topic 13 — Optimistic versus pessimistic concurrency control
  [ ] Topic 14 — Indexing deep dive: B-trees and LSM-trees
  [ ] Topic 15 — Query planning and execution plans
  [ ] Topic 16 — Connection pooling
  [ ] Topic 17 — Database caching strategies
  [ ] Topic 18 — Read replicas and replication topologies
  [ ] Topic 19 — Sharding and partitioning strategies
  [ ] Topic 20 — Rebalancing partitions
  [ ] Topic 21 — Distributed transactions and two-phase commit
  [ ] Topic 22 — Sagas and compensating transactions
  [ ] Topic 23 — CAP and PACELC applied to real databases
  [ ] Topic 24 — Schema migrations and zero-downtime change
```

**NEXT LESSON: Phase 3, Topic 12 — Dirty reads, non-repeatable reads, phantom reads, and lost updates**

---

# Lesson 51 — Dirty Reads, Non-Repeatable Reads, Phantom Reads, and Lost Updates

*Phase 3, Topic 12*

## 1. Simple definition

Lesson 50 described isolation levels as contracts defined by **which anomalies they forbid**. This lesson defines the anomalies themselves.

A **concurrency anomaly** (also called a **phenomenon**) is an observable outcome of two or more overlapping transactions that **could not have been produced by running those same transactions one after another, in any order**. If you can find no serial order that yields what you observed, you have witnessed an anomaly.

There are six that matter in practice. Three are named by the SQL standard; three are not, and the unnamed ones cause more production incidents than the named ones.

| # | Anomaly | In the SQL standard? | One-line definition |
|---|---|---|---|
| 1 | **Dirty read** | Yes (P1) | You read data another transaction wrote but has not committed — and may never commit |
| 2 | **Non-repeatable read** | Yes (P2) | You read a row twice and get two different values, because someone updated it in between |
| 3 | **Phantom read** | Yes (P3) | You run the same predicate query twice and the **set of matching rows** changes |
| 4 | **Lost update** | **No** | Two transactions read the same value, both modify it, and one update silently overwrites the other |
| 5 | **Read skew** | No | You read two related rows at different moments and observe a state that was never simultaneously true |
| 6 | **Write skew** | No | Two transactions read an overlapping set, each decides based on it, and each writes a *different* row, together violating an invariant neither could violate alone |

Terms used throughout and defined before use:

- **Read set**: the set of rows (and predicates) a transaction read.
- **Write set**: the set of rows a transaction modified.
- **Read-modify-write (RMW)**: the three-step pattern `SELECT x` → compute `f(x)` in application code → `UPDATE … SET x = f(x)`. **Almost every anomaly in this lesson is caused by this pattern.**
- **Vulnerable window**: the wall-clock interval between a transaction's read of a value and the commit of its write of that value. Anomalies require another transaction to act inside this window.

> **The single most important sentence in this lesson:** *lost update is not in the SQL standard's list of phenomena, and it is by a wide margin the most common concurrency bug in real applications.* The standard's table trains engineers to look for the wrong three things.

## 2. Why it exists

These anomalies are not bugs in database engines. They are the **direct, intended consequence** of the performance choices of Lesson 50. Every one of them exists because somebody decided that coordination is expensive and some of it could be skipped.

Walk the causal chain:

1. Serial execution is correct and far too slow (Lesson 50 §2 — 125 transactions/second at BookNook's 8 ms transaction cost).
2. So transactions are allowed to overlap.
3. Overlapping transactions must be given *some* view of each other's work.
4. Each rule for "what may I see" leaks a specific, enumerable set of anomalies.
5. The anomalies are catalogued and named so you can decide, per use case, which leaks you can tolerate.

The catalogue exists because **anomalies are invisible without it**. Lost update produces a perfectly well-formed integer in a perfectly well-formed row. There is no corrupt byte, no constraint violation, no log line. The only way to find it is to know it exists and to recognise its shape in your code. This lesson is about learning that shape.

A second reason: **the anomalies are the vocabulary of the fix.** "The checkout is buggy under load" is not actionable. "This is a lost update caused by a read-modify-write on `inventory.stock` with a 25 ms vulnerable window" names the fix (atomic relative update), the test (two concurrent sessions), and the metric (oversell count).

A third reason, the one that matters for architecture: **the standard's list is incomplete, and knowing where it is incomplete is what separates a correct design from a plausible one.** An engineer who knows only P1–P3 will reach for REPEATABLE READ, believe themselves safe, and ship write skew (Lesson 50 §4.5).

## 3. Real-world analogy

**A warehouse with a paper stock ledger and several clerks.**

The ledger sits on a desk. Clerks walk up, read a number, walk to the shelves, and come back to write a number.

- **Dirty read** — Clerk A writes "stock 40" in pencil, then walks off to verify. Clerk B reads 40 and ships 40 units. Clerk A returns and erases it: the count was wrong, it was really 12. B shipped goods that did not exist. *B acted on a number that was never true.*
- **Non-repeatable read** — Clerk C reads "stock 40" at 10:00 for the top of a report. At 10:04 she reads the same line again for the bottom of the report and it says 31. Both numbers were genuinely written in ink. Her report contradicts itself.
- **Phantom read** — Clerk D lists every item with fewer than 5 units: 7 items. He fetches a form, returns, re-lists: 9 items. Nothing he had already written down changed. **Two entirely new lines appeared.** He cannot "watch" a line that did not exist when he looked.
- **Lost update** — Clerks E and F both read "stock 40". Both ship 1 unit. Both cross out 40 and write 39. **Two units left; the ledger says one did.** Nothing looks wrong. The ledger is neat.
- **Read skew** — Clerk G reads the "received" ledger at 10:00 (shows 100 in) and the "shipped" ledger at 10:05 (shows 100 out, including a shipment made at 10:02 against stock recorded at 10:03). Each page is genuine; together they describe a warehouse that never existed.
- **Write skew** — The rule is "at least one clerk must stay on the floor at all times." Clerk H checks the board, sees Clerk I is on the floor, and goes to lunch. Simultaneously Clerk I checks the board, sees Clerk H, and goes to lunch. **Each checked correctly. Each wrote a different line. The floor is empty.**

**Where the analogy breaks down:**

| Warehouse ledger | Database |
|---|---|
| A clerk can see another clerk standing at the desk and wait | Transactions cannot perceive each other at all. The only channel is the data, which is exactly what isolation governs |
| Pencil versus ink is a visible difference | Uncommitted and committed data look identical to a reader; only the engine's visibility rules distinguish them |
| Erasing leaves smudges — evidence of the overwrite | A lost update leaves a clean, plausible value. **There is no forensic trace in the data** |
| A clerk takes seconds; you could film the desk | Vulnerable windows are milliseconds; the interleaving is non-deterministic and usually unreproducible |
| Six clerks | Hundreds of concurrent transactions, so the *probability* of a bad interleaving is high even if each pair is unlikely |
| One ledger on one desk | Replicas, shards and caches mean "the ledger" may not even be a single object (Lesson 34; *Phase 3, Topic 19 — Sharding*) |
| The clerk writes the number they computed | A database can be told to compute the number *itself*, under lock — `stock = stock - 1`. This option has no ledger equivalent, and it is the single most important fix in this lesson (§8.3) |

## 4. Detailed technical explanation

Each anomaly below is given: a precise definition, a full two-transaction interleaved timeline, the BookNook business consequence, and the exact SQL that produces it.

Throughout, the schema is:

```sql
CREATE TABLE inventory (
  isbn          text PRIMARY KEY,
  title         text NOT NULL,
  stock         int  NOT NULL,
  reserved      int  NOT NULL DEFAULT 0,
  price_pence   int  NOT NULL,
  version       int  NOT NULL DEFAULT 1    -- used in §8.4 only
);
CREATE TABLE orders (
  id bigserial PRIMARY KEY, isbn text, customer text, qty int, total_pence int
);
CREATE TABLE accounts (
  id bigserial PRIMARY KEY, customer text, kind text, balance_pence int
);
```

### 4.1 Dirty read (P1)

**Definition:** T1 reads a row version written by T2 while T2 is still in progress. If T2 subsequently rolls back, T1 has read a value that **never existed in any committed state of the database**.

**Permitted at:** READ UNCOMMITTED only. Prevented at READ COMMITTED and above. In PostgreSQL and Oracle it is impossible at any level, because MVCC has no code path to read an uncommitted tuple.

```
DIRTY READ — reading ink that gets erased

  T1 (nightly revenue report)        T2 (a checkout that will FAIL)
  ─────────────────────────────────────────────────────────────────────
  t0                                 BEGIN;
  t1                                 INSERT INTO orders
                                       (isbn,customer,qty,total_pence)
                                     VALUES ('978-X','bob',1,1999);
                                     -- written, NOT committed
  t2  BEGIN ISOLATION LEVEL
        READ UNCOMMITTED;
  t3  SELECT sum(total_pence)
        FROM orders
       WHERE created_at::date = CURRENT_DATE;
      → includes bob's 1999   ← DIRTY
  t4  COMMIT;  (report published)
  t5                                 -- card declined by the PSP
                                     ROLLBACK;
  ─────────────────────────────────────────────────────────────────────
  The report contains £19.99 of revenue from an order that does not
  exist and never did. Nothing in the database now disagrees with
  reality; only the published report does.
```

**BookNook consequence:** the finance team's daily revenue figure is overstated. It is reconciled against the payment provider's statement a month later, the numbers disagree by a few hundred pounds, and three people spend two days on it. The data is fine. The report was built on a value that never existed.

**Why the engine allows it:** reading uncommitted data requires no visibility check at all, so it is marginally faster. The trade is almost never worth it, which is why two of the four major engines do not implement it.

### 4.2 Non-repeatable read (P2) — also called fuzzy read

**Definition:** T1 reads row R. T2 updates or deletes R and commits. T1 re-reads R and sees a different value (or no row). Both of T1's reads returned committed data — just from different moments.

**Permitted at:** READ UNCOMMITTED, READ COMMITTED. Prevented at REPEATABLE READ and above.

**Mechanically, why READ COMMITTED permits it:** under MVCC, READ COMMITTED takes **a new snapshot for every statement** (Lesson 50 §4.6). Two statements, two snapshots, two answers.

```
NON-REPEATABLE READ — the same row, two answers

  T1 (BookNook pricing check)        T2 (a flash-sale price change)
  ─────────────────────────────────────────────────────────────────────
  t0  BEGIN;  -- READ COMMITTED
  t1  SELECT price_pence
        FROM inventory
       WHERE isbn='978-X';
      → 1999                          [snapshot for THIS statement]
  t2  -- app: build the basket line
      -- "The Midnight Library  £19.99"
  t3                                  BEGIN;
  t4                                  UPDATE inventory
                                         SET price_pence = 999
                                       WHERE isbn='978-X';
  t5                                  COMMIT;
  t6  SELECT price_pence * qty
        FROM inventory
       WHERE isbn='978-X';
      → 999                           [NEW snapshot for THIS statement]
  t7  INSERT INTO orders (... total_pence) VALUES (..., 999);
  t8  COMMIT;
  ─────────────────────────────────────────────────────────────────────
  The customer was SHOWN £19.99 and CHARGED £9.99, inside one
  transaction, from one row, with no error.
```

**BookNook consequence:** the order confirmation e-mail, the basket page and the charged amount disagree. At scale this triggers chargebacks, and in some jurisdictions a displayed-price obligation. The fix is one word: `BEGIN ISOLATION LEVEL REPEATABLE READ`, which freezes the snapshot so both reads return 1999.

**Distinguish carefully from lost update.** Here T1 only *reads* twice. Nothing was overwritten. The damage is an inconsistent decision, not inconsistent data.

### 4.3 Phantom read (P3)

**Definition:** T1 executes a query with a predicate (a `WHERE` clause). T2 inserts, deletes, or updates a row so that the **set of rows satisfying that predicate changes**, and commits. T1 re-executes the identical query and sees rows that were not there (or finds rows gone).

**Permitted at:** READ UNCOMMITTED, READ COMMITTED, and REPEATABLE READ *per the standard*. In practice: prevented by PostgreSQL REPEATABLE READ (snapshot isolation cannot gain rows), largely prevented by MySQL InnoDB REPEATABLE READ for consistent reads, and prevented for locking reads by next-key locks. Always prevented at SERIALIZABLE.

**Why phantoms are structurally harder than non-repeatable reads:** preventing P2 requires pinning rows you have seen. Preventing P3 requires pinning a **condition**, including rows that do not yet exist. You cannot take a lock on a row that has not been inserted. The engine needs either a predicate lock, an index-range (gap/next-key) lock, or a snapshot.

```
PHANTOM READ — the SET changes, not the rows

  T1 (BookNook "low stock" restock job)   T2 (a checkout)
  ───────────────────────────────────────────────────────────────────────
  t0  BEGIN;  -- READ COMMITTED
  t1  SELECT isbn, stock FROM inventory
       WHERE stock < 5;
      → 3 rows: 978-A(4), 978-B(2), 978-C(1)
  t2  -- app: raise purchase orders
      -- for these 3 titles
  t3                                       BEGIN;
  t4                                       UPDATE inventory
                                              SET stock = stock - 2
                                            WHERE isbn='978-D';  -- 6 → 4
  t5                                       INSERT INTO inventory
                                            VALUES ('978-E', 'New Title', 1, ...);
  t6                                       COMMIT;
  t7  SELECT count(*) FROM inventory
       WHERE stock < 5;
      → 5    ← TWO PHANTOM ROWS (978-D entered the set; 978-E is brand new)
  t8  -- the job's own summary line now
      -- contradicts the 3 POs it raised
  t9  COMMIT;
  ───────────────────────────────────────────────────────────────────────
  Note: NONE of the three originally-read rows changed. This is
  categorically different from a non-repeatable read.
```

**BookNook consequence:** the restock job logs "3 titles below threshold, 3 purchase orders raised" and its verification query reports 5 below threshold. The on-call engineer treats the mismatch as a data-integrity incident. Two titles stay out of stock for another day. The job was never wrong — the world moved.

### 4.4 Lost update

**Definition:** T1 reads value `x`. T2 reads the same value `x`. T1 writes `f(x)` and commits. T2 writes `g(x)` and commits. T2's write is based on a value that is **no longer current**, and T1's update is **silently destroyed**. The final state reflects only one of two modifications that both logically happened.

**Not in the SQL standard's phenomena list.** Prevented at: SERIALIZABLE everywhere; REPEATABLE READ *as implemented by PostgreSQL* (aborts with `40001`); **not** prevented by MySQL InnoDB REPEATABLE READ for the RMW pattern; **not** prevented at READ COMMITTED anywhere.

```
LOST UPDATE — the most common real-world concurrency bug

  T1 (Alice buys 1 copy)             T2 (Bob buys 1 copy)
  ───────────────────────────────────────────────────────────────────────
  t0  BEGIN;  -- READ COMMITTED
  t1  SELECT stock FROM inventory
       WHERE isbn='978-X';
      → 5
  t2                                 BEGIN;
  t3                                 SELECT stock FROM inventory
                                      WHERE isbn='978-X';
                                     → 5        ← BOTH saw 5
  t4  -- app computes 5 - 1 = 4
  t5                                 -- app computes 5 - 1 = 4
  t6  UPDATE inventory
         SET stock = 4              ← ABSOLUTE value from app memory
       WHERE isbn='978-X';
  t7  COMMIT;                        -- stock is now 4. Correct so far.
  t8                                 UPDATE inventory
                                        SET stock = 4   ← also absolute, also 4
                                      WHERE isbn='978-X';
  t9                                 COMMIT;
  ───────────────────────────────────────────────────────────────────────
  TWO books sold. Stock went 5 → 4, not 5 → 3.
  One unit of inventory has been conjured from nothing.
  No error. No warning. The row looks perfect.
```

**BookNook consequence:** under flash-sale load, hundreds of concurrent checkouts each read the same stock value. The counter drifts upward relative to reality by roughly one unit per lost pair. BookNook accepts 1,180 orders for 500 books (Lesson 50 §7.1), then sends 680 cancellation e-mails. The reputational cost dwarfs the engineering cost.

**The same bug in its other common disguises:**

```sql
-- Disguise 1: an ORM. The framework reads the whole entity, you mutate one
-- field, and it writes EVERY column back. Two users editing different fields
-- of the same profile: the later save silently reverts the earlier one.
--   SELECT * FROM users WHERE id = 7;         -- loads all 14 columns
--   user.phone = '0712...';                   -- you changed ONE field
--   UPDATE users SET name=?, email=?, phone=?, bio=?, ... WHERE id = 7;

-- Disguise 2: a counter.
SELECT view_count FROM articles WHERE id=7;   -- → 1000
UPDATE articles SET view_count = 1001 WHERE id=7;  -- lost under concurrency

-- Disguise 3: JSON document mutation.
SELECT prefs FROM users WHERE id=7;           -- read whole JSON blob
-- mutate one key in application memory
UPDATE users SET prefs = '<whole blob>' WHERE id=7;  -- clobbers other keys

-- Disguise 4: "check then act" across a transaction boundary (no txn at all).
SELECT count(*) FROM seats WHERE id=12 AND booked=false;  -- → 1
-- ... separate statement, separate implicit transaction ...
UPDATE seats SET booked=true WHERE id=12;      -- two users both "win"
```

All four are the same shape: **the application became the arithmetic unit, using a value that went stale in transit.**

### 4.5 Read skew (inconsistent read)

**Definition:** T1 reads row A, then later reads row B. Between the two reads, T2 atomically modifies both A and B and commits. T1's two values are individually committed but describe a state that was **never simultaneously true**. Sometimes called an *inconsistent read* or *non-repeatable read across rows*.

**Permitted at:** READ UNCOMMITTED, READ COMMITTED. Prevented at REPEATABLE READ (snapshot isolation) and above.

```
READ SKEW — each read is valid, the PAIR is impossible

  T1 (BookNook store-credit audit)   T2 (customer moves credit)
  ───────────────────────────────────────────────────────────────────────
  t0  BEGIN;  -- READ COMMITTED
  t1  SELECT balance_pence FROM accounts
       WHERE customer='alice' AND kind='gift_card';
      → 5000   (£50.00)
  t2                                 BEGIN;
  t3                                 UPDATE accounts SET balance_pence = 0
                                      WHERE customer='alice' AND kind='gift_card';
  t4                                 UPDATE accounts
                                        SET balance_pence = balance_pence + 5000
                                      WHERE customer='alice' AND kind='store_credit';
  t5                                 COMMIT;      -- total preserved: £50 moved
  t6  SELECT balance_pence FROM accounts
       WHERE customer='alice' AND kind='store_credit';
      → 5000   (£50.00, INCLUDING the transfer)
  t7  -- audit total = 5000 + 5000 = £100
  t8  COMMIT;
  ───────────────────────────────────────────────────────────────────────
  Alice has £50. The audit says £100. T2 preserved the invariant
  perfectly. T1 observed the money in BOTH places at once, because
  it looked at each account at a DIFFERENT instant.
```

**BookNook consequence:** the nightly ledger-integrity job reports a £50 discrepancy that does not exist. Repeated nightly with different customers, it becomes noise and is eventually muted — at which point a *real* discrepancy will also be missed. This is the classic argument for running every report inside `REPEATABLE READ`: not because the report must be current, but because it must be **self-consistent**.

**Note the asymmetry:** read skew damages *readers*, not data. No write was lost. The anomaly lives entirely in the conclusion drawn.

### 4.6 Write skew

**Definition:** T1 and T2 read an **overlapping set of rows**, each makes a decision based on what it read, and each then writes a **different** row. Neither violates the invariant alone. Together they do. Formally: `write_set(T1) ∩ read_set(T2) ≠ ∅`, `write_set(T2) ∩ read_set(T1) ≠ ∅`, and `write_set(T1) ∩ write_set(T2) = ∅`.

**Permitted at:** everything up to and including snapshot isolation — which means PostgreSQL REPEATABLE READ, MySQL REPEATABLE READ, SQL Server SNAPSHOT, **and Oracle SERIALIZABLE**. Prevented only by true serializability: PostgreSQL/SQL Server SERIALIZABLE, or 2PL.

**The classic example — on-call doctors.** A hospital requires at least one doctor on call at all times. Dr Alice and Dr Bob are both on call. Both feel ill and request leave simultaneously.

```
WRITE SKEW — the canonical doctors example
  Invariant: count(on_call = true) >= 1.  Currently alice=true, bob=true.

  T1 (alice requests leave)           T2 (bob requests leave)
  ───────────────────────────────────────────────────────────────────────
  t0  BEGIN;  -- SNAPSHOT ISOLATION
  t1  SELECT count(*) FROM doctors
       WHERE on_call = true;
      → 2
  t2                                  BEGIN;  -- SNAPSHOT ISOLATION
  t3                                  SELECT count(*) FROM doctors
                                       WHERE on_call = true;
                                      → 2        ← its own snapshot
  t4  2 >= 2, safe to remove one ✓
  t5                                  2 >= 2, safe to remove one ✓
  t6  UPDATE doctors SET on_call=false
       WHERE name='alice';            ← writes the ALICE row
  t7                                  UPDATE doctors SET on_call=false
                                       WHERE name='bob';   ← writes the BOB row
  t8  COMMIT;  ✓
  t9                                  COMMIT;  ✓   ← DIFFERENT ROW, no conflict
  ───────────────────────────────────────────────────────────────────────
  Nobody is on call. Both transactions were individually correct.
  Snapshot isolation had NOTHING to conflict on: disjoint write sets.
```

**The BookNook equivalent — inventory reservation.** The invariant: *total open reservations for an ISBN must never exceed `stock`*. Stock = 2; two customers each reserve 2 (Lesson 50 §4.5 gives this in full). Structurally identical: each reads the *set* of reservation rows, each inserts its *own* reservation row, the write sets are disjoint, both commit, four copies are promised against two.

**A second BookNook variant that is easy to miss** — the "one promo code per customer" rule:

```sql
-- Invariant: a customer may hold at most ONE active promo redemption.
-- T1 and T2 are two tabs redeeming two DIFFERENT codes simultaneously.
SELECT count(*) FROM redemptions WHERE customer='alice' AND active;  -- both → 0
-- both decide "allowed"
INSERT INTO redemptions (customer, code, active) VALUES ('alice','SAVE10',true);  -- T1
INSERT INTO redemptions (customer, code, active) VALUES ('alice','SAVE20',true);  -- T2
-- Different rows. Both commit. Alice holds two discounts.
```

**The recognition rule, worth memorising:** *if your transaction runs a `SELECT` whose result decides whether to perform an `INSERT` or an `UPDATE` to a different row, you are exposed to write skew at any level below SERIALIZABLE.* The query that detects it is `SELECT count(*)`, `SELECT sum(...)`, or `SELECT … LIMIT 1` used as a guard.

### 4.7 The six anomalies against the four levels

| Anomaly | READ UNCOMMITTED | READ COMMITTED | REPEATABLE READ (standard) | Snapshot isolation (PG RR, Oracle SERIALIZABLE) | SERIALIZABLE (SSI / 2PL) |
|---|---|---|---|---|---|
| Dirty read | **Possible** | Prevented | Prevented | Prevented | Prevented |
| Non-repeatable read | **Possible** | **Possible** | Prevented | Prevented | Prevented |
| Phantom read | **Possible** | **Possible** | **Possible** | Prevented | Prevented |
| Read skew | **Possible** | **Possible** | Prevented | Prevented | Prevented |
| **Lost update** | **Possible** | **Possible** | **Possible** | Prevented *(first-committer-wins → `40001`)* | Prevented |
| **Write skew** | **Possible** | **Possible** | **Possible** | **POSSIBLE** ← the gap | Prevented |

The bold cell in the snapshot-isolation column is the entire reason true serializability exists.

## 5. Request / data flow

The life of a lost update, from the customer's click to the cancellation e-mail, at BookNook on PostgreSQL READ COMMITTED. Two customers, Alice and Bob, click "Buy" on the last two copies within 4 ms of each other.

1. Alice's `POST /orders` arrives at app server 1; Bob's arrives at app server 2, routed independently by the load balancer (Lesson 23). **Neither server knows the other exists.**
2. Each handler checks out a pooled database connection and issues `BEGIN`. No snapshot is taken yet.
3. Alice's transaction (T1) runs `SELECT stock FROM inventory WHERE isbn = $1`. A statement-scoped snapshot is taken. The engine walks the version chain and returns the one visible version: `stock = 5`. **T1's vulnerable window opens here.**
4. 2 ms later, Bob's transaction (T2) runs the identical `SELECT`. T1 has not committed, so its (not-yet-written) change is invisible. T2 also receives `stock = 5`. **Both applications now hold the integer 5 in process memory. This is the moment the bug becomes inevitable.**
5. T1's application code evaluates `if (stock >= 1) newStock = stock - 1` → `4`.
6. T2's application code evaluates the same → `4`.
7. T1 issues `UPDATE inventory SET stock = 4 WHERE isbn = $1`. The engine takes an exclusive row lock, writes a new tuple version with `stock = 4`, and marks the old version expired.
8. T2 issues `UPDATE inventory SET stock = 4 WHERE isbn = $1`. It **blocks** on T1's row lock. (This blocking is the engine's only hint that something is contended — and the application never sees it.)
9. T1 inserts the order row, issues `COMMIT`, the WAL is flushed and fsynced, and T1's version becomes visible. Alice gets `201 Created`.
10. T2's lock wait ends. At READ COMMITTED, the `UPDATE` now performs a **current read**: it re-reads the latest committed version (`stock = 4`) and re-evaluates the `WHERE` clause against it. The `WHERE` clause is `isbn = $1`, which still matches. **The engine has no way to know T2's value of `4` was derived from a stale read**, because `4` is a constant literal in T2's statement. It writes `4`.
11. T2 inserts its order row and commits. Bob gets `201 Created`.
12. Final state: **two orders, `stock = 4`.** Started at 5, sold 2, should be 3. One phantom unit of inventory now exists.
13. Hours later the warehouse picks the orders, finds one copy short, and flags it. A human issues a refund and a cancellation e-mail. **This is the first and only detection event in the entire chain.**

Contrast step 10 with the atomic form. Had T2 issued `UPDATE inventory SET stock = stock - 1 WHERE isbn = $1`, the engine's current read at step 10 would have fetched `4` and computed `4 - 1 = 3` **itself, under the lock**. The staleness of T2's earlier `SELECT` would have been irrelevant, because the application's value never entered the arithmetic. §8.3 dissects exactly this.

## 6. ASCII diagram

```
THE SHAPE OF EVERY ANOMALY — where the window is

   T1:  ──[READ x]───────────── think ──────────[WRITE f(x)]──COMMIT──▶
                 │                                     │
                 └────── VULNERABLE WINDOW W ──────────┘
                              │
   T2:            ────────[WRITE x]──COMMIT───
                              ▲
             Any write landing inside W makes T1's x stale.
             P(anomaly) rises with W and with the arrival rate on x.
             SHORTEN W, or REMOVE the read, or MAKE THE ENGINE DO THE MATH.


1. DIRTY READ ─ reading what was never committed
   T1:  ─────────────── SELECT sum(total) ─→ 1999 included ── COMMIT ─▶
   T2:  ── BEGIN ── INSERT 1999 ──────────────────────── ROLLBACK ────▶
                        ▲ uncommitted            ▲ never existed
   Only possible at READ UNCOMMITTED (not possible at all in PG/Oracle).


2. NON-REPEATABLE READ ─ same row, two answers
   T1:  ── BEGIN ── SELECT price → 1999 ─────── SELECT price → 999 ──▶
   T2:  ──────────────────── UPDATE price=999 ─ COMMIT ──────────────▶
                                        ▲
   Both committed. Different moments. Fixed by REPEATABLE READ.


3. PHANTOM READ ─ the SET grows, the rows do not change
   T1:  ─ SELECT * WHERE stock<5 → {A,B,C} ──── same query → {A,B,C,D,E} ─▶
   T2:  ──────────── UPDATE D (6→4) ─ INSERT E ─ COMMIT ────────────────▶
                            ▲ joined the set    ▲ did not exist before
   You cannot lock a row that does not exist ⇒ needs a PREDICATE lock,
   a GAP lock, or a SNAPSHOT.


4. LOST UPDATE ─ the big one
   stock = 5
   T1:  ─ SELECT 5 ─────────── UPDATE SET stock=4 ─ COMMIT ────────────▶
   T2:  ──── SELECT 5 ─────────────────── UPDATE SET stock=4 ─ COMMIT ─▶
              ▲ both hold "5"                        ▲ overwrites with
                                                       the SAME stale math
   Sold 2. Decremented 1.  NOT in the SQL standard. Most common in reality.

   THE FIX, shown as a diagram:
   T1:  ─ UPDATE SET stock = stock-1 ─ COMMIT ─▶   engine reads 5, writes 4
   T2:  ──── UPDATE SET stock = stock-1 ───────────── COMMIT ─▶
              │                                  engine reads 4, writes 3
              └─ BLOCKS on T1's row lock, then RE-READS under the lock
        No application value ever enters the arithmetic. Correct at
        READ COMMITTED. Zero retries. Zero extra round trips.


5. READ SKEW ─ two rows, two moments, an impossible pair
   T1:  ─ SELECT gift_card → 5000 ────────── SELECT store_credit → 5000 ─▶
   T2:  ─────── BEGIN  gift_card=0  store_credit+=5000  COMMIT ──────────▶
                        └──── atomic, invariant preserved ────┘
   T1 concludes £100 exists. £50 exists. Fixed by REPEATABLE READ.


6. WRITE SKEW ─ the one snapshot isolation cannot see
   invariant: on_call >= 1       alice=true, bob=true

   T1:  ─ SELECT count(on_call) → 2 ──── UPDATE alice.on_call=false ─ COMMIT ✓
   T2:  ──── SELECT count(on_call) → 2 ── UPDATE bob.on_call=false ── COMMIT ✓
             ▲ read sets OVERLAP          ▲ write sets are DISJOINT
             └── so SI finds no write-write conflict to abort ──┘
   Result: on_call = 0.  Needs SERIALIZABLE (SSI or 2PL). Nothing less.


WHY THE SET MATTERS — P2 versus P3 in one picture

   NON-REPEATABLE READ        PHANTOM READ
   ┌───┬───┬───┐              ┌───┬───┬───┐
   │ A │ B │ C │  read        │ A │ B │ C │  read
   └───┴───┴───┘              └───┴───┴───┘
     │   ▲                      │   │   │   │
     │   └ B's VALUE changed    └───┴───┴───┴─── plus a NEW row D
   ┌───┬───┬───┐              ┌───┬───┬───┬───┐
   │ A │ B'│ C │  re-read     │ A │ B │ C │ D │  re-read
   └───┴───┴───┘              └───┴───┴───┴───┘
   Lock the ROWS you read     Locking read rows is USELESS —
   ⇒ fixed.                   D was not there to lock.
```

## 7. Detailed example

### 7.1 BookNook — the gift-card double-redemption

BookNook launches gift cards. The schema is naïve but entirely typical:

```sql
CREATE TABLE gift_cards (
  code text PRIMARY KEY, balance_pence int NOT NULL CHECK (balance_pence >= 0)
);
CREATE TABLE redemptions (
  id bigserial PRIMARY KEY, code text NOT NULL, order_id bigint, amount_pence int NOT NULL
);
```

The redemption handler, on PostgreSQL READ COMMITTED (the default), does:

```sql
BEGIN;
SELECT balance_pence FROM gift_cards WHERE code = 'GC-7741';          -- → 5000
SELECT coalesce(sum(amount_pence),0) FROM redemptions WHERE code='GC-7741';  -- → 0
-- app: remaining = 5000 - 0 = 5000;  requested = 5000;  5000 <= 5000 → allow
INSERT INTO redemptions (code, order_id, amount_pence) VALUES ('GC-7741', 91, 5000);
UPDATE gift_cards SET balance_pence = 0 WHERE code = 'GC-7741';
COMMIT;
```

**What a customer discovered.** Open two browser tabs, place two £50 orders, submit both within the same second. Both succeed. £100 of books for a £50 card.

**Which anomaly is it?** This is the subtle part, and it is worth being precise, because the fix depends on the answer.

- Both transactions read `gift_cards.balance_pence` **and both update it**. Write sets overlap on `gift_cards`. That part is a **lost update**.
- Both also read the *set* of `redemptions` rows and both **insert** a new row into it. Write sets there are disjoint. That part is **write skew**.

The single handler contains both anomalies. That is extremely common and it is why "which anomaly is this?" is better asked as "which invariants are at risk, and by which shape?"

**What each candidate fix does:**

| Fix | Effect on the gift-card bug | Verdict |
|---|---|---|
| Raise to PostgreSQL REPEATABLE READ | The `UPDATE gift_cards` write-write conflict is caught; the second transaction gets `40001` | **Fixes it on PostgreSQL** — but only because of the shared `gift_cards` update. If the handler only inserted redemptions, it would not |
| Raise to MySQL REPEATABLE READ | No serialization error is raised; the second `UPDATE` blocks, then writes `balance = 0` over `balance = 0` | **Does not fix it.** Same level name, different outcome |
| `SELECT … FOR UPDATE` on the card row | T2 blocks until T1 commits, then re-reads `balance = 0`, computes remaining = 0, refuses | **Fixes it at any level.** Pessimistic, one extra lock |
| Atomic conditional update | `UPDATE gift_cards SET balance_pence = balance_pence - 5000 WHERE code=? AND balance_pence >= 5000` — 0 rows affected on the loser | **Fixes it at any level, cheapest** |
| SERIALIZABLE | Both shapes caught; `40001` on the loser | **Fixes it**, costs retry logic |

BookNook shipped the atomic conditional update that afternoon and moved to SERIALIZABLE on that endpoint a sprint later as defence in depth. The atomic update is the load-bearing fix; the isolation level is the safety net for the next invariant someone adds without thinking.

**The telling detail:** this bug existed for eleven months. It was never caught by tests, because every integration test used **one** database connection, and a single connection cannot interleave with itself. **Concurrency bugs require concurrent tests.**

### 7.2 A contrasting example — a seat-booking system for a concert venue

Same family of anomalies, different shape, different correct answer.

A 2,400-seat venue sells reserved seating. Invariant: *each seat is sold at most once.* Naïve handler:

```sql
BEGIN;
SELECT booked FROM seats WHERE seat_id = 'A-14';   -- → false
-- app: if (!booked) { ... }
UPDATE seats SET booked = true, customer = 'bob' WHERE seat_id = 'A-14';
COMMIT;
```

This is a textbook lost update — but notice that the shape is **different from BookNook's counter**, because the value is a boolean state transition, not arithmetic. There is no "relative update" for a boolean. The correct fix is a **conditional update whose `WHERE` clause carries the precondition**:

```sql
UPDATE seats
   SET booked = true, customer = 'bob'
 WHERE seat_id = 'A-14'
   AND booked = false;      -- the precondition, evaluated by the ENGINE under lock
-- rows affected = 1 → you got the seat
-- rows affected = 0 → someone else got it first. No error, no retry, just a 409.
```

The generalisation: **move the precondition from an `if` in your application into the `WHERE` clause of the write.** `stock = stock - 1 WHERE stock > 0` and `booked = true WHERE booked = false` are the same technique applied to a counter and to a state machine.

| | BookNook gift card | Concert seat |
|---|---|---|
| Value type | Monetary counter | Boolean state transition |
| Anomalies present | Lost update **and** write skew | Lost update only |
| Relative update possible? | Yes — `balance - :amt` | No — a boolean has no arithmetic |
| Correct technique | Atomic relative update with a guard | Conditional update with the precondition in `WHERE` |
| How the loser finds out | 0 rows affected | 0 rows affected |
| Does it need retry logic? | No (at READ COMMITTED) | No |
| Does it need SERIALIZABLE? | Only for the multi-row reservation rule | No |

**Why the venue does *not* use `SELECT FOR UPDATE`:** at the moment tickets go on sale, 40,000 people hit 2,400 seats. Pessimistic locking would queue thousands of transactions behind individual seat rows, and most would wait only to discover the seat was taken. The conditional update lets every loser fail **immediately** with zero waiting. **At very high contention, optimistic conditional writes beat pessimistic locks precisely because losing is cheap.** That trade-off is the subject of the next lesson.

## 8. Code or configuration example

> **These are illustrative teaching examples, not production-ready code.** Error handling is minimal, there are no metrics, no timeouts, no idempotency keys on payment operations, and no input validation. Do not paste them into a service.

### 8.1 Reproducing every anomaly — two psql sessions, side by side

```sql
-- ===== SETUP (run once) =====
CREATE TABLE inventory (
  isbn text PRIMARY KEY, stock int NOT NULL CHECK (stock >= 0),
  price_pence int NOT NULL, version int NOT NULL DEFAULT 1);
INSERT INTO inventory VALUES ('978-X', 5, 1999, 1);
```

**Non-repeatable read (works on PostgreSQL READ COMMITTED):**

```sql
-- T1 -------------------------------------------------
BEGIN;                                              -- READ COMMITTED (default)
SELECT price_pence FROM inventory WHERE isbn='978-X';  -- → 1999
-- (now run T2 fully)
-- T2 -------------------------------------------------
BEGIN; UPDATE inventory SET price_pence=999 WHERE isbn='978-X'; COMMIT;
-- T1 -------------------------------------------------
SELECT price_pence FROM inventory WHERE isbn='978-X';  -- → 999  ← ANOMALY
COMMIT;
-- FIX: BEGIN ISOLATION LEVEL REPEATABLE READ;  → both reads return 1999
```

**Phantom read:**

```sql
-- T1 -------------------------------------------------
BEGIN;                                              -- READ COMMITTED
SELECT count(*) FROM inventory WHERE stock < 5;     -- → 0 (stock is 5)
-- T2 -------------------------------------------------
BEGIN; UPDATE inventory SET stock=3 WHERE isbn='978-X'; COMMIT;
-- T1 -------------------------------------------------
SELECT count(*) FROM inventory WHERE stock < 5;     -- → 1  ← PHANTOM: a NEW row
COMMIT;                                             --        entered the result set
-- FIX on PostgreSQL: REPEATABLE READ (snapshot cannot gain rows).
-- Note the standard ALLOWS phantoms at REPEATABLE READ; PostgreSQL exceeds it.
```

**Lost update — the whole point of this lesson:**

```sql
-- T1 -------------------------------------------------
BEGIN;
SELECT stock FROM inventory WHERE isbn='978-X';     -- → 5   (app remembers 5)
-- T2 -------------------------------------------------
BEGIN;
SELECT stock FROM inventory WHERE isbn='978-X';     -- → 5   (app ALSO remembers 5)
-- T1 -------------------------------------------------
UPDATE inventory SET stock = 4 WHERE isbn='978-X';  -- app computed 5-1
COMMIT;                                             -- stock = 4
-- T2 -------------------------------------------------
UPDATE inventory SET stock = 4 WHERE isbn='978-X';  -- app ALSO computed 5-1
COMMIT;                                             -- stock = 4  ← LOST UPDATE
-- Two sales. Stock fell by one. No error anywhere.
```

**Write skew — survives REPEATABLE READ:**

```sql
-- T1 -------------------------------------------------
BEGIN ISOLATION LEVEL REPEATABLE READ;
SELECT coalesce(sum(qty),0) FROM reservations
 WHERE isbn='978-X' AND status='open';              -- → 0
-- T2 -------------------------------------------------
BEGIN ISOLATION LEVEL REPEATABLE READ;
SELECT coalesce(sum(qty),0) FROM reservations
 WHERE isbn='978-X' AND status='open';              -- → 0  (own snapshot)
-- T1 -------------------------------------------------
INSERT INTO reservations (isbn,customer,qty,status)
VALUES ('978-X','alice',5,'open');                  -- row R1
COMMIT;                                             -- ✓
-- T2 -------------------------------------------------
INSERT INTO reservations (isbn,customer,qty,status)
VALUES ('978-X','bob',5,'open');                    -- row R2 — DIFFERENT row
COMMIT;                                             -- ✓  BOTH SUCCEED
-- 10 reserved against 5 in stock. Change both to SERIALIZABLE → one gets 40001.
```

### 8.2 The four fixes for lost update, as runnable SQL

**Fix 1 — `SELECT … FOR UPDATE` (pessimistic).**

```sql
-- T1 -------------------------------------------------
BEGIN;
SELECT stock FROM inventory
 WHERE isbn='978-X' FOR UPDATE;        -- exclusive row lock acquired here, held to COMMIT
                                       -- → 5
-- T2 -------------------------------------------------
BEGIN;
SELECT stock FROM inventory
 WHERE isbn='978-X' FOR UPDATE;        -- *** BLOCKS *** — waits for T1
-- T1 -------------------------------------------------
UPDATE inventory SET stock = 4 WHERE isbn='978-X';
COMMIT;                                -- lock released
-- T2 -------------------------------------------------
                                       -- unblocks, → 4  (the FRESH value)
UPDATE inventory SET stock = 3 WHERE isbn='978-X';   -- app computed 4-1, correctly
COMMIT;                                -- stock = 3. CORRECT.
```

Costs: one extra round trip; every contender serialises; deadlock risk if transactions lock multiple rows in different orders (**always lock in a consistent order, e.g. ascending primary key**); and a stuck transaction blocks everyone (set `lock_timeout`).

**Fix 2 — atomic relative update (the best answer when applicable).**

```sql
-- T1 -------------------------------------------------
BEGIN;
UPDATE inventory
   SET stock = stock - 1               -- the ENGINE reads and writes, under its own lock
 WHERE isbn='978-X'
   AND stock >= 1;                     -- the precondition, enforced by the engine
-- rows affected: 1 → proceed;  0 → sold out, ROLLBACK and return 409
INSERT INTO orders (isbn,customer,qty) VALUES ('978-X','alice',1);
COMMIT;
-- T2 does the identical thing concurrently and gets stock 4 → 3. Both correct.
```

Costs: essentially zero. Limitation: the new value must be computable **by the database** from the old one. If it depends on an external API call, a user's decision, or data the engine cannot see, this fix does not apply.

**Fix 3 — optimistic version column.**

```sql
-- T1 -------------------------------------------------
BEGIN;
SELECT stock, version FROM inventory WHERE isbn='978-X';   -- → stock=5, version=1
-- app does arbitrary work: call a pricing API, ask the user, whatever
UPDATE inventory
   SET stock = 4,                      -- an ABSOLUTE value is fine HERE ...
       version = version + 1
 WHERE isbn='978-X'
   AND version = 1;                    -- ... because THIS guards it
-- rows affected: 1 → committed safely
COMMIT;

-- T2 -------------------------------------------------
BEGIN;
SELECT stock, version FROM inventory WHERE isbn='978-X';   -- → stock=5, version=1
UPDATE inventory SET stock = 4, version = version + 1
 WHERE isbn='978-X' AND version = 1;   -- version is now 2 → *** 0 ROWS AFFECTED ***
-- The application MUST check rows-affected. If it does not, the bug is back.
ROLLBACK;                              -- then re-read and retry the whole operation
```

Costs: the application must check the affected-row count on **every** update — silently ignoring zero is the single most common way this fix fails. Benefit: it works across *think time*, including across HTTP requests (send `version` to the browser in a hidden field, receive it back), which no lock can do.

**Fix 4 — raise the isolation level.**

```sql
-- PostgreSQL only; MySQL will NOT raise this error at REPEATABLE READ.
-- T1 -------------------------------------------------
BEGIN ISOLATION LEVEL REPEATABLE READ;
SELECT stock FROM inventory WHERE isbn='978-X';    -- → 5, snapshot frozen
-- T2 -------------------------------------------------
BEGIN ISOLATION LEVEL REPEATABLE READ;
SELECT stock FROM inventory WHERE isbn='978-X';    -- → 5
UPDATE inventory SET stock = 4 WHERE isbn='978-X';
COMMIT;                                            -- ✓ T2 wins the race
-- T1 -------------------------------------------------
UPDATE inventory SET stock = 4 WHERE isbn='978-X';
-- ERROR: could not serialize access due to concurrent update
-- SQLSTATE 40001  ← the lost update was PREVENTED, by refusing it
ROLLBACK;                                          -- application must retry from the top
```

Costs: retry logic is mandatory; engine-dependent; no protection against write skew unless you go to SERIALIZABLE.

### 8.3 Why `stock = stock - 1` is safe and read-then-write is not — mechanically

This is the mechanism that most engineers half-know. Here it is in full.

**The unsafe version:**

```sql
SELECT stock FROM inventory WHERE isbn='978-X';     -- engine returns 5
                                                    -- ↓ the value leaves the database
                                                    -- ↓ travels over TCP
                                                    -- ↓ sits in app memory for N ms
                                                    -- ↓ arithmetic happens IN THE APP
UPDATE inventory SET stock = 4 WHERE isbn='978-X';  -- a CONSTANT literal arrives
```

Step by step, what the engine sees at the `UPDATE`:

1. It parses `SET stock = 4`. The right-hand side is a **constant**. It does not reference `stock`.
2. It locates the row and takes an exclusive row lock, waiting if another transaction holds it.
3. Under the lock it performs a **current read** of the latest committed version — this is real, and it is why `WHERE stock = 5` would have worked.
4. It evaluates the `WHERE` clause: `isbn = '978-X'`. True.
5. It writes `stock = 4`.

**The engine did everything right.** It locked, it re-read, it re-evaluated. The problem is that **nothing in the statement depends on what it re-read.** The value `4` carries no evidence of the `5` it was derived from. The engine cannot detect staleness it was never told about.

**The safe version:**

```sql
UPDATE inventory SET stock = stock - 1 WHERE isbn='978-X' AND stock >= 1;
```

Now the same five steps:

1. It parses `SET stock = stock - 1`. The right-hand side **references the column**.
2. It locks the row, waiting if necessary.
3. Under the lock it performs a current read: the latest committed `stock`, say `4`.
4. It evaluates `WHERE isbn='978-X' AND stock >= 1` **against that freshly-read value**.
5. It computes `4 - 1 = 3` **from the value it just read under the lock** and writes `3`.

**The decisive difference: the read and the write are a single atomic operation inside the engine, under one lock, with no gap.** The vulnerable window of §6 has width **zero**, because the application's value never participates. There is nothing to go stale.

```
UNSAFE                                    SAFE
──────                                    ────
app:  x = read()   ◄── window opens       db:  lock row
app:  y = x - 1    ◄── window             db:  x = read_latest()   │ atomic
app:  write(y)     ◄── window closes      db:  write(x - 1)        │ no gap
      └─ another txn can write here            db:  unlock
         and your y is already wrong           └─ nothing can interleave
```

**Three important caveats, so this is not over-generalised:**

1. **Relative updates do not help when the new value is not a function of the old.** `SET status = 'shipped'` cannot be expressed relatively. Use a conditional `WHERE status = 'paid'` instead (the §7.2 technique).
2. **Relative updates do not prevent write skew.** They fix *this row*. An invariant spanning rows still needs §8.2 Fix 1, Fix 4 at SERIALIZABLE, or a constraint.
3. **You must still check rows-affected.** `AND stock >= 1` makes the statement a no-op when out of stock. If you do not inspect the count, you will record an order for a book you did not decrement.

### 8.4 Choosing between the fixes — decision table

| Fix | Prevents | Works at READ COMMITTED? | Blocks other transactions? | Needs retry? | Works across think time / HTTP requests? | Extra round trips | Best when |
|---|---|---|---|---|---|---|---|
| **Atomic relative update** (`SET x = x - 1 WHERE x >= 1`) | Lost update on that row | **Yes** | Only briefly, at write time | **No** | No | **0** | The new value is computable by the engine from the old one. **Default choice.** |
| **Conditional update** (`SET s='sold' WHERE s='free'`) | Lost update on state transitions | **Yes** | Briefly | No | No | 0 | State machines, booleans, seat booking |
| **`SELECT … FOR UPDATE`** | Lost update, write skew on locked rows | **Yes** | **Yes — contenders queue** | No (but can deadlock) | **No — never hold a lock across a user's think time** | 1 | A short transaction with an identifiable anchor row; multi-row decisions |
| **Optimistic version column** | Lost update | **Yes** | No | **Yes** (re-read and re-apply) | **Yes** | 1 | Long think time, web forms, APIs, low conflict probability |
| **REPEATABLE READ** | Lost update (PostgreSQL), read skew, non-repeatable read | n/a — it *is* the level | No | **Yes (`40001`)** | No | 0 | A transaction needing a self-consistent multi-read view |
| **SERIALIZABLE** | **Everything, including anomalies you did not anticipate** | n/a | No (SSI) / Yes (2PL) | **Yes (`40001`)** | No | 0 | Multi-row invariants, write skew, high-consequence correctness |
| **Constraint** (`CHECK`, `UNIQUE`, `EXCLUDE`) | Exactly the stated invariant | **Yes** | No | No | Yes | 0 | The invariant is declaratively expressible. **Add this regardless of the other choices.** |

The practical algorithm:

```
Can the invariant be a CHECK / UNIQUE / EXCLUDE constraint?
   → Add it. It is enforced at every level, forever, for every client.
     (Then continue, because a constraint turns corruption into an error,
      not into correct behaviour.)

Is the new value computable by the engine from the old value?
   → Atomic relative or conditional UPDATE. Done. No retries, no locks.

Does a human or a slow external call sit between the read and the write?
   → Optimistic version column. Never hold a database lock across think time.

Does the decision depend on a SET of rows (count/sum/exists)?
   → SERIALIZABLE, or materialise the conflict onto a shared row,
     or SELECT FOR UPDATE on an anchor row.

Did you choose anything that can raise 40001 or 40P01?
   → Implement retry with jittered backoff FIRST (Lesson 17).
```

### 8.5 Detecting these anomalies in production

```sql
-- PostgreSQL: the error codes your application must classify as RETRYABLE
--   40001  serialization_failure   (SI write conflict, or SSI dependency cycle)
--   40P01  deadlock_detected       (lock cycle; the engine picked you as victim)
--   55P03  lock_not_available      (SELECT ... NOWAIT found the row locked)
-- These are the ONLY codes you should retry blindly. Everything else is a real error.

-- MySQL/InnoDB equivalents:
--   1213   ER_LOCK_DEADLOCK        → retry
--   1205   ER_LOCK_WAIT_TIMEOUT    → retry with care (the txn may be partially applied)

-- Find transactions currently blocking others (PostgreSQL):
SELECT blocked.pid        AS blocked_pid,
       blocked.query      AS blocked_query,
       blocking.pid       AS blocking_pid,
       blocking.query     AS blocking_query,
       now() - blocked.query_start AS blocked_for
  FROM pg_stat_activity blocked
  JOIN pg_stat_activity blocking
    ON blocking.pid = ANY(pg_blocking_pids(blocked.pid))
 WHERE cardinality(pg_blocking_pids(blocked.pid)) > 0;

-- Find long-running transactions (they cause BOTH anomalies and MVCC bloat, L50 §4.6):
SELECT pid, state, now() - xact_start AS txn_age, left(query, 80)
  FROM pg_stat_activity
 WHERE xact_start IS NOT NULL
   AND now() - xact_start > interval '1 minute'
 ORDER BY xact_start;

-- Deadlocks and conflicts since the last stats reset:
SELECT datname, deadlocks, conflicts, xact_commit, xact_rollback
  FROM pg_stat_database WHERE datname = current_database();

-- The invariant check that CATCHES a lost update after the fact.
-- Run it on a schedule. It is the only automated detection you will get.
SELECT i.isbn,
       i.stock                                   AS recorded_stock,
       i.initial_stock - coalesce(sum(o.qty),0)  AS expected_stock
  FROM inventory i
  LEFT JOIN orders o ON o.isbn = i.isbn
 GROUP BY i.isbn, i.stock, i.initial_stock
HAVING i.stock <> i.initial_stock - coalesce(sum(o.qty),0);   -- any row = a lost update
```

```sql
-- Configuration that limits the blast radius
SET lock_timeout = '3s';                         -- never wait forever on a row lock
SET statement_timeout = '30s';                   -- kill runaway statements
SET idle_in_transaction_session_timeout = '60s'; -- kill forgotten open transactions
-- log_lock_waits = on         (postgresql.conf) -- log any wait beyond deadlock_timeout
-- deadlock_timeout = '1s'                       -- how long before a cycle check runs
```

Two things to monitor as **business** metrics, not database metrics:

1. **The invariant-violation query above, as a scheduled job with an alert.** A non-zero count is a lost update that already happened. This is the only reliable detector.
2. **The `40001` / `40P01` rate as a percentage of transactions**, per endpoint. A rising rate means contention is growing. A rate above roughly 5 % means the retry budget is being consumed and you should remove the hot row rather than tune the level.

## 9. Numerical example

**Question: given BookNook's traffic, how often does a lost update actually happen — and is "it's rare, we'll fix it later" defensible?**

### Assumptions (all stated, all labelled)

| Symbol | Meaning | Value | Basis |
|---|---|---|---|
| `N` | Concurrent application threads performing checkouts | 120 | 4 app servers × 30 worker threads |
| `R` | Checkout rate at normal peak | 400 /s | Measured |
| `W` | Vulnerable window: `SELECT stock` → commit of the `UPDATE` | 25 ms | Measured: 3 ms query + 15 ms app logic + 5 ms update + 2 ms commit |
| `C` | Distinct ISBNs receiving checkouts | 50,000 | Catalogue size |
| `h` | Share of traffic on the single hottest ISBN | 5 % | **Typical** Zipf head; **approximate** |
| `H` | Promotion-day share on one ISBN | 70 % | Flash-sale measurement |
| `T` | Peak hours per day | 8 h | Operational |

### Step 1 — the arrival rate on the hot row, normal day

```
λ_hot = R × h
      = 400 /s × 0.05
      = 20 transactions per second on the hottest ISBN
```

### Step 2 — the probability that one transaction's window is invaded

A lost update needs **another** transaction on the *same row* to read before this one commits. Model arrivals as a Poisson process with rate `λ_hot`. Over a window of `W` seconds, the expected number of other arrivals is:

```
μ = λ_hot × W
  = 20 /s × 0.025 s
  = 0.5 arrivals expected inside the window
```

Probability of **at least one** other arrival:

```
P(overlap) = 1 − e^(−μ)
           = 1 − e^(−0.5)
           = 1 − 0.60653
           = 0.39347     →  39.3 %
```

### Step 3 — from overlap to an actual lost update

Not every overlap loses an update. The reads must both precede both writes. For two uniformly-positioned transactions overlapping in a window, the probability that the interleaving is the damaging `R1 R2 W1 W2` or `R2 R1 W2 W1` pattern is approximately **0.5** (**assumption**: uniform arrival phase within the window; this is an approximation, not an exact combinatorial result).

```
P(lost update | overlap) ≈ 0.5
P(lost update per hot-ISBN transaction) = 0.39347 × 0.5
                                        = 0.19674   →  19.7 %
```

### Step 4 — lost updates per second and per day, hot ISBN

```
Hot-ISBN transactions per second = 20
Lost updates per second          = 20 × 0.19674 = 3.935 /s
Lost updates per peak hour       = 3.935 × 3,600 = 14,166
Lost updates per 8-hour peak day = 14,166 × 8    = 113,328
```

That number is implausibly large as *oversold books*, and the reason is important: each lost update means the stock counter failed to decrement once, so the drift is one unit per event. In practice the counter hits its `CHECK (stock >= 0)` floor or the title sells out long before 113,328 drift accumulates. The correct interpretation is:

```
Counter drift per peak day on the hot ISBN ≈ 113,328 units
If that title's true daily stock is 2,000 copies,
the counter is wrong LONG before the day ends — within:
     2,000 / 3.935 per second = 508 seconds ≈ 8.5 minutes
```

**Within nine minutes of peak traffic, BookNook's stock figure for its hottest title is meaningless.**

### Step 5 — the cold tail, for contrast

```
λ_cold (per ISBN) = (400 × 0.95) / 50,000 = 380 / 50,000 = 0.0076 /s
μ_cold            = 0.0076 × 0.025        = 0.00019
P(overlap)        = 1 − e^(−0.00019)      = 0.00018998
P(lost update)    = 0.00018998 × 0.5      = 0.000095  → 0.0095 %
Lost updates/sec across the whole cold tail
                  = 380 /s × 0.000095     = 0.0361 /s
Per 8-hour peak day = 0.0361 × 28,800     = 1,039 events/day
```

**This is the result that kills "it's rare, we'll fix it later."** Even on the cold tail — where each individual ISBN sees a lost update roughly once in 10,500 transactions — the *fleet* produces **about a thousand incorrect stock values per day**. Rare per row, constant in aggregate.

### Step 6 — flash sale, one ISBN

```
λ_flash   = 400 × 0.70            = 280 /s on one row
μ         = 280 × 0.025           = 7.0
P(overlap)= 1 − e^(−7.0)          = 1 − 0.000912 = 0.999088  →  99.91 %
P(lost update per txn) ≈ 0.999088 × 0.5 = 0.4995  → ~50 %
Lost updates per second = 280 × 0.4995  = 139.9 /s
```

**Essentially every transaction overlaps another, and about half of all checkouts lose an update.** If the sale is 500 copies at 280 checkouts/second, the counter reaches zero after roughly `500 / (280 × 0.5) = 3.6 seconds` of *correct* decrements, while 280/s of orders keep arriving. This is precisely the Lesson 50 §7.1 incident, derived from first principles.

### Step 7 — what shortening the window buys

Suppose the 15 ms of application logic is moved out of the transaction, cutting `W` from 25 ms to 10 ms, on the normal-day hot ISBN:

```
μ_new          = 20 × 0.010       = 0.2
P(overlap)     = 1 − e^(−0.2)     = 0.18127
P(lost update) = 0.18127 × 0.5    = 0.09064   → 9.1 %  (was 19.7 %)
Reduction      = (0.19674 − 0.09064) / 0.19674 = 0.539  → 54 % fewer
```

**Halving the window roughly halves the anomaly rate — and still leaves 9.1 %.** Shortening transactions is a genuine mitigation and never a fix. The only values of `P` that matter are `0` and "not zero"; the atomic update of §8.3 sets `W = 0` and therefore `P = 0` exactly.

### Step 8 — the cost of the real fix, for comparison

```
Atomic relative UPDATE: removes one SELECT round trip.
   Round trips per checkout: 3 → 2
   Saved latency ≈ 3 ms (one query) per checkout
   Lost updates: 1,039 + 113,328 per day → 0
   Engineering cost: one line of SQL per write path
   Throughput cost: NEGATIVE (it is faster)
```

**The fix is cheaper than the bug in every dimension, including latency.** There is no trade-off to weigh here, which is unusual in system design and worth noticing when it happens.

## 10. Trade-offs

| Dimension | Tolerate the anomaly | Prevent it with atomic / conditional writes | Prevent it with pessimistic locks | Prevent it with optimistic versions | Prevent it with higher isolation |
|---|---|---|---|---|---|
| **Throughput** | Highest | Highest (often *better* — one fewer round trip) | Lower under contention; contenders serialise | High when conflicts are rare; wasteful when common | 5–10 % at low contention; collapses at high |
| **Latency** | Lowest | Lowest | Waiting time proportional to contention | Low, plus retry cost on conflict | Low, plus retry cost on `40001` |
| **Correctness** | **None** | Per-row only | Per-locked-row, and multi-row if you lock the anchor | Per-row only | **Complete at SERIALIZABLE** |
| **Code complexity** | Zero now, unbounded later | Minimal — rewrite the statement | Moderate — lock ordering, timeouts | Moderate — version plumbing through every layer, including HTTP | Moderate — one retry wrapper, reused everywhere |
| **Works across HTTP requests?** | n/a | No | **No — never hold a lock across think time** | **Yes** | No |
| **Failure visibility** | **Invisible** | 0 rows affected (must be checked) | Deadlock / lock timeout errors | 0 rows affected (must be checked) | `40001` with a clear message |
| **Generalises to new invariants?** | n/a | No — per-statement | Partly | No — per-row | **Yes — protects against anomalies you have not thought of** |
| **Portability** | n/a | Universal SQL | Universal, with syntax differences | Universal | Behaviour differs by engine (Lesson 50 §4.3) |

Three asymmetries specific to this lesson:

1. **Prevention is usually free; detection is always expensive.** The atomic update costs nothing and may be faster. Detecting a lost update after the fact requires a reconciliation job, historical data, and a human. Never trade "we'll detect it" for "we'll prevent it".
2. **Per-row fixes do not compose into multi-row correctness.** Making every statement atomic protects every counter and protects *no invariant that spans rows*. Write skew is immune to all of §8.2 Fixes 1–3 except the anchor-lock variant.
3. **Probability is per-row, impact is per-fleet.** Step 5 of §9 is the general lesson: anomalies that are vanishingly unlikely for any given row occur constantly across a large catalogue. Reasoning about "the chance this happens" without multiplying by the row count underestimates by three to four orders of magnitude.

## 11. Failure scenarios

| Failure | What the user experiences | How it is detected | How it recovers |
|---|---|---|---|
| **Lost update on a stock counter** | Buys the last copy; receives a cancellation hours later. Or sees a title "in stock" that cannot be fulfilled | Nothing in the database complains. Found by the scheduled invariant query (§8.5), by warehouse pick failures, or by customer complaints — typically days later | Manual stock recount and refunds. Code fix: atomic relative `UPDATE` (§8.3). Backfill the counter from the order history |
| **Lost update via ORM full-entity write** | A colleague's profile edit vanishes; a support note is overwritten | Almost never detected. Occasionally reported as "the system forgot my change" and closed as user error | Configure the ORM for dirty-field-only updates plus optimistic locking (`@Version`); add an audit table so overwrites are at least visible |
| **Dirty read in a report** | Finance publishes a revenue figure that is wrong by the value of rolled-back orders | Month-end reconciliation against the payment provider | Re-run the report at READ COMMITTED or higher. On PostgreSQL/Oracle this cannot occur at all |
| **Non-repeatable read mid-checkout** | Shown £19.99, charged £9.99 (or the reverse) | Customer complaint, chargeback, or a mismatch alert between displayed and charged amounts | Wrap the checkout in `REPEATABLE READ`, or capture the price into the order row at the first read and use that value thereafter |
| **Phantom read in a batch job** | A job's own summary contradicts its actions; duplicate purchase orders are raised | The job's self-check fails; treated as a data-integrity incident | Run the job at `REPEATABLE READ` so its snapshot is stable; make the job idempotent so a re-run is harmless |
| **Read skew in an audit job** | Nightly ledger check reports phantom discrepancies | The alert itself — which, repeated, trains the team to ignore it | `REPEATABLE READ` for the whole job. **Also treat recurring false alerts as a severity-raising bug**, because a muted alert will miss the real event |
| **Write skew in reservations** | Two customers each hold a reservation for the same physical copy; the second is cancelled at the warehouse | Only by business reconciliation — every transaction reported success | SERIALIZABLE, or materialise the conflict onto a shared counter row, or `SELECT FOR UPDATE` on the inventory row (Lesson 50 §8.2) |
| **Deadlock from inconsistent lock ordering** | Intermittent 500s at peak, unreproducible in staging | `40P01` / MySQL `1213` in logs; PostgreSQL logs both statements and the cycle | Retry the victim. Permanent fix: sort keys before locking so a cycle cannot form |
| **Lock wait timeout under `FOR UPDATE`** | Checkout hangs for seconds, then fails | `55P03`, MySQL `1205`, or client-side timeouts; `pg_blocking_pids` shows the chain | Set `lock_timeout`; shorten transactions; move think time outside the transaction; consider optimistic versioning instead |
| **Retry storm after adding retry logic** | Latency balloons; a self-sustaining collapse under load | Abort rate and request rate rise together while committed throughput falls | Full-jitter backoff, an attempt cap, and load shedding (Lesson 17). Then reduce contention |
| **Optimistic update whose rows-affected is ignored** | The operation reports success and does nothing. **Strictly worse than the original bug** | Only by noticing the data never changed | Make the data-access layer *throw* on an unexpected affected-row count. Never let a zero pass silently |
| **Anomaly reproduced only in production** | Intermittent, load-dependent, "works on my machine" | Never reproduced in CI, because CI uses a single connection | Add a concurrency test harness: two real connections, deterministic interleaving via advisory locks or sleeps |

The row that deserves re-reading is the last one. **Every anomaly in this lesson is invisible to a single-connection test suite.** A test suite that cannot interleave two transactions cannot detect any of them, and its green status is evidence of nothing.

<!--CHUNK-MARKER-->
