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
