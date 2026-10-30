# Phase 3 — Data and Storage

Phases 1 and 2 taught you how to move a request around. A client opens a connection (Lesson 3), speaks HTTP over TLS (Lessons 5–7), hits a load balancer that picks a healthy backend (Lessons 21–24), and that backend is stateless (Lesson 20) so it can be cloned horizontally (Lesson 19) and fronted by caches and CDNs (Lessons 27–33). Every one of those techniques works because the thing being moved is *disposable*. If an app server dies mid-request, you lose a request. You retry it (Lesson 17) and nobody notices.

Data is the opposite. Data is the one part of your system that **cannot be recreated by restarting it**. An app server is a pure function you can run anywhere; a database is a mutable fact about the world that someone is paying you to not lose. That asymmetry is why every hard problem in system design eventually lands on storage: you can cache a read, but you cannot cache a write; you can replicate a database, but replication introduces lag (Lesson 35) and lag introduces users who cannot see the order they just placed; you can shard (Lesson 37) and immediately lose the ability to join across shards or run a transaction that spans them. Phase 2 showed you that *stateless* tiers scale almost trivially. Phase 3 is about the tier where that trick stops working.

This phase delivers four things, in order. First, a working knowledge of the **storage engines themselves** — relational, key-value, document, wide-column, graph and search — and, more importantly, a repeatable method for choosing between them from access patterns rather than from fashion (Topics 1–9). Second, **correctness under concurrency**: what ACID actually guarantees, what isolation levels really prevent, and which anomalies your chosen level silently permits (Topics 10–13). Third, **performance mechanics**: indexes, query plans, the physical structures underneath them, and the normalisation/denormalisation trade-off that decides how much work a read has to do (Topics 14–19). Fourth, the **distributed-systems limits** that constrain all of the above — CAP, PACELC, the consistency-model spectrum — plus the unglamorous operational topics (object/block/file storage, retention, backups, disaster recovery) that decide whether your company survives a bad Tuesday (Topics 20–24).

A note on how to read this phase. Lessons 40–63 are deliberately front-loaded with the relational model, because relational databases are both the most common correct answer and the baseline against which every alternative defines itself — you cannot understand what a document database gives up until you know what a foreign key was doing for you. Throughout, the running example stays **BookNook**, the online bookstore from Phase 1, now with real tables: `books`, `authors`, `users`, `orders`, `order_items`, `reviews` and `inventory`. Numbers given are labelled *typical* or *approximate* and reflect commodity cloud hardware of roughly the mid-2020s; treat them as order-of-magnitude anchors for capacity reasoning, never as benchmark claims for your workload.

| # | Topic | Lesson |
|---|---|---|
| 1 | Relational databases | Lesson 40 |
| 2 | Tables, keys, relationships, joins, constraints | Lesson 41 |
| 3 | SQL versus NoSQL | Lesson 42 |
| 4 | Key-value databases | Lesson 43 |
| 5 | Document databases | Lesson 44 |
| 6 | Wide-column databases | Lesson 45 |
| 7 | Graph databases | Lesson 46 |
| 8 | Search engines and full-text indexes | Lesson 47 |
| 9 | Selecting a database from access patterns | Lesson 48 |
| 10 | ACID transactions | Lesson 49 |
| 11 | Isolation levels | Lesson 50 |
| 12 | Dirty reads, non-repeatable reads, phantom reads, lost updates | Lesson 51 |
| 13 | Optimistic versus pessimistic concurrency control | Lesson 52 |
| 14 | Database indexes | Lesson 53 |
| 15 | B-tree, hash, composite, covering and secondary indexes | Lesson 54 |
| 16 | Query plans and index trade-offs | Lesson 55 |
| 17 | Normalisation and denormalisation | Lesson 56 |
| 18 | OLTP versus OLAP | Lesson 57 |
| 19 | Data warehouses, data lakes and lakehouses | Lesson 58 |
| 20 | CAP theorem | Lesson 59 |
| 21 | PACELC | Lesson 60 |
| 22 | Strong, eventual, causal, read-your-writes and monotonic consistency | Lesson 61 |
| 23 | Object, block and file storage | Lesson 62 |
| 24 | Data retention, archival, backups and disaster recovery | Lesson 63 |

---

# Lesson 40 — Relational Databases

*Phase 3, Topic 1*

## 1. Simple definition

A **relational database** is a data store built on one idea: all data is held in **relations** — unordered sets of **tuples** that share a fixed set of named, typed attributes — and you retrieve data by *describing the result you want*, not by writing instructions for how to fetch it.

Terms defined before use:
- **Relation**: the mathematical object. In practice you call it a **table**.
- **Tuple**: one member of that set. In practice you call it a **row**.
- **Attribute**: one named, typed slot present in every tuple. In practice you call it a **column**.
- **Domain**: the set of legal values for an attribute — `INTEGER`, `TEXT`, `DATE`, `NUMERIC(10,2)`. The database refuses values outside the domain.
- **Declarative query**: you state *what* ("all books by authors from Ireland priced under £10"), and the database decides *how* (which index, which join order, which algorithm).
- **Query planner / optimiser**: the component that turns the *what* into the *how*.
- **RDBMS**: Relational Database Management System — the actual software: PostgreSQL, MySQL, SQL Server, Oracle, SQLite.

Two consequences follow from "a relation is a *set*", and they surprise newcomers:

1. **Rows have no inherent order.** A query without `ORDER BY` may return rows in any order, and that order may change tomorrow because the planner changed its mind. Relying on it is a bug that passes tests for two years and then fails in production.
2. **Relations are closed under their operations.** Filtering a table gives a table. Joining two tables gives a table. The output of a query can be the input of another query. This composability is why SQL scales as a language from one-liners to 300-line analytical reports.

The shortest honest summary: **a relational database is a set-theory engine with a cost-based compiler bolted to a crash-safe storage engine.**

## 2. Why it exists

### What came before: navigational databases

Before 1970, production databases were **navigational**. Two families dominated:

- **Hierarchical** (IBM's IMS, 1966): data is a tree. A `customer` record owns `order` records, which own `line-item` records. You read data by starting at a root and *walking pointers downward*.
- **Network / CODASYL** (1969): data is a graph. Records are linked by named **sets** (owner → member chains). You read data by issuing commands such as `FIND OWNER`, `FIND NEXT WITHIN SET`, `GET`.

Both worked, and both had the same four structural defects:

1. **The query was a program.** Fetching "all orders over £50 placed by Irish customers" meant writing a loop in COBOL or PL/I that walked pointer chains by hand, maintained its own cursors and accumulated results. Every question required a programmer and a deployment.
2. **Access paths were baked into the physical layout.** The pointer chains that made customer→orders fast were the *only* fast path. If the business later asked "which customers bought this product?", there was no chain for it, so the answer required a full scan or a schema redesign plus a data migration plus rewriting every program that touched it.
3. **No logical/physical independence.** Changing storage layout broke application code, because application code contained the navigation.
4. **Redundancy was the normal coping mechanism**, and redundancy without a consistency mechanism produces contradictory copies — the same customer address stored three ways.

### What Codd fixed

In 1970 E. F. Codd published *A Relational Model of Data for Large Shared Data Banks*. The contribution was not "tables" — people had tables. The contribution was **separating the logical data model from the physical access path**, and giving the logical model a formal algebra (selection, projection, join, union, difference) so that a machine could mechanically rewrite one expression into an equivalent, cheaper one.

That separation delivers four things you still rely on daily:

- **Ad-hoc querying.** Any question expressible in the algebra can be asked without changing the schema or writing navigation code. The "which customers bought this product?" question is just a join.
- **Physical independence.** Add an index, repartition a table, switch storage engines — queries keep working, they just get faster or slower. Compare this to the navigational world where layout *was* the API.
- **Automatic optimisation.** Because you did not specify an algorithm, the system is free to pick a better one as data volumes change. A query written in 2015 for 10,000 rows can switch from nested-loop to hash join at 10 million rows with no code change.
- **Centrally enforced integrity.** Because the model understands keys and references, the *database* can refuse an order row that points at a non-existent user — regardless of which of your six services tried to write it. Lesson 41 covers this in depth.

SQL (from IBM's System R, standardised 1986) is a pragmatic, slightly impure implementation of that algebra — notably, SQL tables are **bags** (duplicates allowed) rather than true sets, and SQL has `NULL`, which Codd's model handles far more cleanly than SQL does. Those two compromises are the source of a large fraction of all SQL bugs.

## 3. Real-world analogy

**A national library with a catalogue, versus a library where you must know the route.**

The navigational database is a library with **no catalogue** but excellent signage: "Irish authors are in Room 4; within Room 4, follow the chain of shelf-markers in publication order." You can find anything — *if* someone has already built the chain for the question you are asking. Ask a question nobody anticipated ("every book that mentions a shipwreck") and you walk every aisle by hand.

The relational database is a library with a **catalogue and a head librarian**. You hand over a request slip: *"Everything by Irish authors, published after 2015, under £10, sorted by price."* You do not say which catalogue drawer to open, whether to start from the author index or the price index, or whether to sort before or after filtering. The librarian decides, based on what she knows about the collection: *"only 40 Irish authors, so start there and check their prices — do not scan 2 million price cards."* Tomorrow the collection triples and she silently reverses that decision.

The rest of the mapping:
- **The catalogue cards** are indexes (Lesson 53). They duplicate a little data to make lookups fast, and they must be updated whenever the shelves change.
- **The reading-room desk** is the buffer pool: a small set of books kept to hand because they are requested constantly, rather than being fetched from the stacks every time.
- **The accession ledger** written in ink before any book is shelved is the write-ahead log. If the lights go out mid-shelving, the ledger tells you exactly what was in progress.
- **Borrowing rules** ("a book cannot be lent to a member who does not exist") are constraints — enforced at the desk, once, rather than hoped for in each reader's conscience.

**Where the analogy breaks down:**

| National library | Relational database |
|---|---|
| The librarian learns from experience and intuition | The planner uses **statistics** — histograms and distinct-value estimates sampled periodically. If statistics are stale, it makes confidently wrong decisions, and it never notices (Lesson 55) |
| Two readers can read the same book over each other's shoulder | Two transactions reading and writing the same row need an explicit concurrency mechanism (MVCC or locking) or they corrupt each other (Lessons 49–52) |
| A misfiled book is found eventually | A violated invariant propagates into derived data, invoices and reports within seconds, and is often unrecoverable |
| Fetching a book takes minutes; one trip is one trip | A page fetch takes ~0.1 ms from NVMe and ~0.0001 ms from RAM — a 1,000× ratio. Almost all database performance work is about **which of those two numbers you hit** |
| The library is one building | A production database is a primary plus replicas (Lesson 34) in different availability zones, and the copies disagree for a few milliseconds (Lesson 35) |
| A librarian handles one request at a time | A database serves thousands of concurrent requests, and its throughput *collapses* past a certain concurrency instead of plateauing — see §9 |

## 4. Detailed technical explanation

### 4.1 The logical model

A **schema** is the declared structure: tables, their columns, the domains of those columns, and the constraints among them. It is enforced on write — a `NUMERIC(10,2)` column cannot hold `"banana"`, and no application bug can make it hold `"banana"`. This is **schema-on-write**, and it is the single largest behavioural difference from document databases (Lesson 44), which are typically schema-on-read.

Every query is composed from a small set of operations:

| Operation | SQL | Meaning |
|---|---|---|
| Selection | `WHERE` | keep a subset of **rows** |
| Projection | `SELECT a, b` | keep a subset of **columns** |
| Join | `JOIN ... ON` | combine rows from two relations by a predicate |
| Aggregation | `GROUP BY`, `SUM` | collapse many rows into summary rows |
| Set operations | `UNION`, `INTERSECT`, `EXCEPT` | combine two union-compatible relations |

Lesson 41 covers joins and constraints in full. What matters here is the *closure* property: each of those produces a relation, so they nest arbitrarily.

### 4.2 The physical layer that actually affects your design

You can use a relational database for years without knowing this. You cannot *design* one, or debug it at 3 a.m., without it. Six structures matter.

**Pages.** Storage is divided into fixed-size blocks — **8 KiB** in PostgreSQL, **16 KiB** in MySQL/InnoDB (both defaults, configurable at initialisation). A page is the unit of I/O and the unit of caching. The database never reads "a row"; it reads the page the row lives on. *Design consequence:* if your row is 200 bytes, one 8 KiB read brings ~40 rows. If those 40 rows are ones you also wanted, you got 40 rows for the price of one I/O; if they are unrelated, you wasted 97 % of the read. That is the whole basis of **locality**, and it is why clustering, index-only scans and column ordering matter.

**Heap files and row addressing.** In PostgreSQL a table is a **heap**: an unordered collection of pages, with new rows placed wherever there is free space. Each row has a physical address, the **ctid** = (page number, slot number). Indexes store that address, so an index lookup is a two-step journey: search index → get address → fetch heap page. MySQL/InnoDB instead uses a **clustered index**: the table *is* a B-tree ordered by the primary key, so rows physically live in primary-key order, and secondary indexes store the *primary key value*, not a physical address. This single difference drives most of the PostgreSQL/MySQL behavioural divergence, and it is why primary key choice is far more performance-critical on MySQL (Lesson 41, §4 on UUIDs).

**The buffer pool (shared buffers).** A region of RAM caching recently used pages, managed by an eviction policy (a clock-sweep approximation of LRU in PostgreSQL; an LRU with a midpoint-insertion guard in InnoDB). Every read checks the buffer pool first. The metric that matters is the **buffer cache hit ratio**: the fraction of page requests served from RAM. *Design consequence:* your performance cliff is not "dataset size exceeds RAM" — it is "**working set** exceeds RAM", where working set means the pages actually touched by live traffic. A 4 TB table whose hot 30 GB is queried 99 % of the time performs like a 30 GB database. This is the most useful single sentence in database capacity planning.

**The write-ahead log (WAL / redo log).** Before any change to a data page is allowed to reach disk, a record describing that change is written to a sequential log and flushed (`fsync`) to durable storage. This is **write-ahead logging**, and it buys three things at once: (a) **durability** — a committed transaction survives a crash because the log survives; (b) **sequential writes** — commits are appends to one file, which is the fastest thing a storage device does, instead of scattered random writes to a hundred data pages; (c) **replication** — streaming the log to another machine is exactly how physical replication works (Lesson 34). *Design consequence:* commit throughput is bounded by how fast you can `fsync` the log. Databases amortise this with **group commit**, batching many concurrent commits into one flush — which is why 500 concurrent small transactions achieve far more commits/second in total than 1 client committing serially.

**Checkpoints.** Dirty (modified) pages sit in the buffer pool; a **checkpoint** periodically flushes them to the data files and records that the log before that point is no longer needed for recovery. Checkpoints trade recovery time against I/O smoothness: infrequent checkpoints mean less steady write I/O but a longer crash-recovery replay; aggressive checkpoints mean fast recovery but periodic I/O storms that show up as latency spikes in your p99 (Lesson 16). *Design consequence:* unexplained periodic latency spikes in a write-heavy relational database are checkpoint I/O until proven otherwise.

**MVCC — Multi-Version Concurrency Control (conceptually).** Rather than making readers wait for writers, the database keeps **multiple versions of each row**. An `UPDATE` in PostgreSQL does not overwrite in place: it writes a *new* row version and marks the old one as expired at this transaction's ID. Every transaction sees a **snapshot** — the set of row versions that were committed when it began. The headline consequence: **readers never block writers, and writers never block readers.** Two writers touching the same row still conflict, and that is what row locks are for. Full treatment is **Phase 3, Topic 10 — ACID transactions** (Lesson 49) and **Topic 11 — Isolation levels** (Lesson 50).

**Vacuum / purge.** MVCC creates garbage: old row versions nobody can see any more. PostgreSQL reclaims them with **autovacuum**; InnoDB with a background **purge** thread on its undo log. Two failure modes you must know:
- **Table bloat.** If vacuum cannot keep up, dead versions accumulate, the table's page count grows, the working set grows, and the cache hit ratio falls. Symptom: a table 5× larger than its live data, with queries slowing steadily over weeks.
- **The long-transaction hazard.** Vacuum may only remove versions invisible to *every* open transaction. One forgotten `BEGIN;` in a psql window — or an analytics query running for six hours, or an idle-in-transaction connection leaked by an ORM — pins the horizon and blocks cleanup **database-wide**. This is one of the most common real production incidents in PostgreSQL, and it is caused by application code, not by the database.

### 4.3 How a query executes, end to end

Four stages. Knowing the boundaries tells you which knob fixes which problem.

1. **Parse.** The SQL text is tokenised and checked for syntax, producing a parse tree. Errors here are syntax errors. Cost: microseconds, but non-zero — this is one reason prepared statements exist.
2. **Analyse / rewrite.** Names are resolved against the catalogue (does `books` exist? is `price` a column? what is its type?). Views are expanded inline, row-level security policies are injected, and some transformations are applied (e.g. a correlated subquery may be rewritten as a semi-join). Errors here are "relation does not exist" or type errors.
3. **Plan / optimise.** The cost-based optimiser enumerates candidate **plans** — different access methods (sequential scan, index scan, index-only scan), different join orders, different join algorithms (nested loop, hash, merge — Lesson 41 §4.6) — estimates the cost of each from **statistics** (row counts, value histograms, distinct-value counts, physical correlation), and picks the cheapest. Cost here is measured in an abstract unit, not milliseconds. *This stage is where almost all surprising performance behaviour originates*, because the estimate can be wrong by orders of magnitude when statistics are stale or when columns are correlated. Full treatment: **Phase 3, Topic 16 — Query plans and index trade-offs** (Lesson 55).
4. **Execute.** The plan is a tree of operators, each pulling rows from its children (the "Volcano"/iterator model). Rows flow upward through filters, joins and aggregations to the client. Memory-hungry operators (sort, hash) use a work-memory budget and **spill to disk** when they exceed it — a silent 10–100× slowdown with no error message.

The practical takeaway: `EXPLAIN` shows you stage 3's decision; `EXPLAIN ANALYZE` runs stage 4 and shows you estimate-versus-actual. A large gap between *estimated rows* and *actual rows* is the single most diagnostic signal in relational performance work.

### 4.4 Connections, and why pooling is mandatory

A client connection to PostgreSQL is a **TCP connection plus a dedicated server-side OS process** (MySQL uses a thread instead, which is cheaper but not free). Each PostgreSQL backend costs roughly **5–10 MB** of resident memory before it does any work, plus its share of `work_mem` per sort or hash operator in flight.

Three distinct costs make unpooled connections untenable:

- **Setup latency.** TCP three-way handshake (Lesson 6) + TLS handshake (Lesson 7) + authentication + session setup. Typically **5–15 ms** within a data centre. If your query takes 1 ms, you just spent 90 % of the request budget on connection setup.
- **Memory and scheduler pressure.** 2,000 connections × 8 MB ≈ 16 GB of RAM doing nothing, plus 2,000 processes for the OS to schedule. Throughput *falls* past the optimal concurrency — this is the same convex-collapse behaviour Little's Law warned about in Lesson 14, and it is why a database under overload gets slower rather than merely saturating.
- **Ephemeral port exhaustion.** This is the one from **Lesson 3**. Every outbound connection from an app server consumes a local ephemeral port, and the usable range is roughly **28,000 ports** (Linux default 32768–60999) *per (source IP, destination IP, destination port) tuple*. Worse, a closed connection sits in `TIME_WAIT` for **60 seconds** holding its port. An app server that opens and closes a connection per request at 500 requests/second accumulates 500 × 60 = **30,000 sockets in TIME_WAIT**, exhausts the range, and begins failing with "cannot assign requested address" — an error that looks like a network fault and is actually a connection-lifecycle bug.

A **connection pool** fixes all three by keeping a small, fixed set of connections open and lending them to requests for the duration of a single query or transaction. Two layers exist and they are not interchangeable:

| Layer | Example | Scope | Limit it solves |
|---|---|---|---|
| In-process pool | HikariCP, `psycopg_pool`, SQLAlchemy pool | One app process | Setup latency, TIME_WAIT |
| External pooler | PgBouncer, pgcat, ProxySQL | Whole fleet | Total backend count across *all* app instances |

The second layer becomes mandatory the moment you scale horizontally (Lesson 19): 40 app pods × an in-process pool of 20 = 800 database backends, regardless of how well-behaved each pod is. PgBouncer in **transaction pooling** mode multiplexes those 800 client connections onto perhaps 40 real backends, because at any instant most clients are idle. The constraint you must respect: transaction-mode pooling breaks session-scoped features — `SET` outside a transaction, session-level advisory locks, `LISTEN/NOTIFY`, and server-side prepared statements (unless the pooler explicitly supports them).

**Sizing rule of thumb (approximate):** `pool_size ≈ cores × 2 + effective_spindle_count`. For a 16-vCPU NVMe instance that is roughly **32–40 active connections**. Engineers reliably guess 10× too high. If 40 connections cannot keep up, more connections will make it *worse*, not better; the fix is faster queries, caching (Lessons 27–31) or read replicas (Lesson 34).

### 4.5 Transactions, at a high level

A **transaction** is a group of statements that the database treats as one indivisible unit, delimited by `BEGIN` and `COMMIT`/`ROLLBACK`. It provides the ACID properties:

- **Atomicity** — all of it happens, or none of it does. BookNook's order placement decrements `inventory` *and* inserts into `orders` *and* inserts `order_items`; a crash in the middle leaves none of them applied.
- **Consistency** — the transaction moves the database from one state satisfying all declared constraints to another such state; a violation aborts the whole transaction.
- **Isolation** — concurrent transactions do not see each other's partial work. *How much* they are isolated is a configurable spectrum, not a boolean.
- **Durability** — once `COMMIT` returns, the change survives a crash, because the WAL record was flushed first.

This is the single capability that is hardest to rebuild in application code, and it is the most common reason to choose relational in the first place. The full treatment, including the ways each property leaks, is **Phase 3, Topic 10 — ACID transactions** (Lesson 49), with isolation levels in **Topic 11** (Lesson 50) and the specific anomalies in **Topic 12** (Lesson 51).

### 4.6 The realistic performance envelope of one modern instance

This section exists so that you stop over-engineering. Figures are **approximate**, for a single PostgreSQL 16-class instance on **16 vCPU / 64 GB RAM / local NVMe SSD** (roughly an AWS `db.m6id.4xlarge` or `r6i.4xlarge`), with a properly sized connection pool and a working set that fits in the buffer pool.

| Workload | Typical sustained throughput | Typical latency (p50 / p99) |
|---|---|---|
| Primary-key point read, cached | **25,000–60,000 /s** | 0.2 ms / 2 ms |
| Indexed read returning ~10 rows | 10,000–25,000 /s | 0.5 ms / 4 ms |
| Simple single-row `INSERT`/`UPDATE`, each its own transaction | **5,000–15,000 commits/s** | 1 ms / 10 ms |
| Same writes, batched 100 rows per transaction | 50,000–200,000 rows/s | — |
| Multi-table join over a 10 M-row table, indexed | 2,000–8,000 /s | 3 ms / 25 ms |
| Analytical aggregate scanning 50 M rows | 0.2–2 /s | 2–20 s |

Capacity anchors:
- **Dataset size:** single instances run comfortably at **1–5 TB**; 10–30 TB is operationally painful but done routinely. The binding constraints are backup/restore time and index maintenance, not query execution.
- **Working set:** the real limit. Keep hot pages under ~70 % of RAM. 64 GB of RAM comfortably serves a hot set of ~40 GB — which for 500-byte rows is roughly **80 million hot rows**.
- **Rows per table:** hundreds of millions is routine with correct indexes, because a B-tree lookup is `O(log n)` — going from 1 M to 1 B rows takes a 3-level tree to about 5 levels. Query time roughly doubles while data grows 1,000×.
- **Connections:** 30–60 *active*; thousands of *client* connections via a pooler.
- **Read scaling:** add replicas (Lesson 34). 5 replicas ≈ 5× read throughput, at the cost of replication lag of typically 10–200 ms (Lesson 35) and the read-your-writes problem (Lesson 61).

Put that against BookNook's traffic. At 2 million daily page views with a 10:1 read:write ratio, peak is roughly 3× average: 2,000,000 / 86,400 ≈ 23 req/s average, ≈ 70 req/s peak, maybe 350 database queries/second at 5 queries per request. **That is under 2 % of one instance's read capacity.** A great many teams shard (Lesson 37) at a scale where a single well-indexed PostgreSQL instance was running at 3 % utilisation. Measure before you distribute.

### 4.7 PostgreSQL versus MySQL, compared fairly

Both are excellent, both run enormous systems, and most applications would succeed on either. The differences that genuinely affect design:

| Dimension | PostgreSQL | MySQL (InnoDB) |
|---|---|---|
| Table storage | **Heap** + separate indexes; indexes store physical `ctid` | **Clustered** by primary key; secondary indexes store the PK value |
| Consequence for PK choice | Random PK hurts the index, not the table | Random PK scatters the **whole table** — much more damaging (Lesson 41) |
| Secondary index lookup | index → heap (1 extra fetch) | secondary index → PK → clustered index (**2 B-tree traversals**) |
| MVCC implementation | Old versions in the table; needs **autovacuum** | Old versions in **undo log**; needs purge; table less prone to bloat |
| `UPDATE` cost | Writes a whole new row version; can be heavy on wide rows | Updates in place where possible; cheaper for narrow updates |
| Default isolation | Read Committed | **Repeatable Read** (with InnoDB's gap locks) |
| DDL | Transactional — `ALTER` inside `BEGIN`/`ROLLBACK` works | Mostly non-transactional; online DDL has improved but migrations often use `gh-ost`/`pt-online-schema-change` |
| Type system | Very rich: arrays, `JSONB`, ranges, `UUID`, geometric, custom types, extensions (PostGIS) | Narrower; JSON supported but less powerful indexing |
| Index types | B-tree, hash, **GIN** (inverted, for JSONB/arrays/full-text), **GiST**, SP-GiST, **BRIN** | B-tree, plus full-text and R-tree for spatial |
| Constraints | Includes **EXCLUDE** constraints (Lesson 41); `CHECK` fully enforced | No `EXCLUDE`; `CHECK` enforced since 8.0.16 |
| Replication | Physical streaming (byte-exact) + logical | Binlog-based logical; mature multi-source; group replication |
| Read-heavy simple queries | Excellent | Often marginally faster for pure PK lookups (clustered index) |
| Complex queries / CTEs / window functions | **Stronger planner**, richer SQL | Improving, historically weaker |
| Operational ecosystem | Extensions, logical replication, strong tooling | Huge hosting ecosystem, battle-tested at extreme read scale |

**Honest default:** choose **PostgreSQL** for a new system unless you have a specific reason not to — the richer type system, `JSONB`, stronger planner and constraint support remove whole categories of problem. Choose **MySQL** when your team's operational expertise is there, when you need its specific replication topologies, or when your workload is overwhelmingly primary-key point lookups at very high rates. "Which is faster" is the wrong question; the difference between a good and a bad index on either is 1,000×, while the difference between the two engines on the same workload is typically well under 2×.

### 4.8 "Relational databases don't scale" — what is actually true

This claim is the most repeated and least examined statement in system design interviews. Decompose it.

**What is false:**
- *They cannot handle large data.* They routinely hold multi-terabyte tables with billions of rows. A B-tree's `O(log n)` behaviour means volume alone is nearly free.
- *They cannot handle high read rates.* Reads scale almost linearly with replicas (Lesson 34), exactly as the stateless tier does, with the caveat of lag.
- *NoSQL is inherently faster.* On a single node, for a point lookup, a relational database and a key-value store are doing the same work. The difference appears only when you are willing to **give something up** — transactions, joins, secondary indexes, strong consistency.

**What is true — the four real limits:**
1. **Writes do not scale horizontally without sharding.** There is exactly one primary accepting writes. Its ceiling is the commit rate in §4.6 (typically 5,000–15,000 single-row transactions/second). Beyond that you must shard (Lesson 37) or batch. This is the genuine, hard limit.
2. **Sharding costs you the model's best features.** Once data is split across primaries you lose cross-shard joins, cross-shard foreign keys, cross-shard transactions and global `AUTO_INCREMENT`. You can buy these back with distributed SQL systems (CockroachDB, Spanner, Vitess, Citus) at the price of higher per-query latency, because consensus requires network round trips. Nothing is free; you are choosing which bill to pay.
3. **Operational heaviness at scale.** Schema migrations on a 500 M-row table, major-version upgrades, backups and restores of 5 TB, and failover (typically 10–60 s with an orchestrator) are real engineering projects.
4. **Workload mismatch.** Relational engines are tuned for OLTP: many small, indexed, transactional operations. They are not column stores; a 500 M-row analytical aggregate belongs in a warehouse (**Topic 18**, Lesson 57, and **Topic 19**, Lesson 58). They are not inverted indexes; fuzzy, ranked text search belongs in a search engine (**Topic 8**, Lesson 47). Using the wrong tool is a modelling failure, not a scaling failure.

**The accurate sentence to use in an interview:** *"Relational databases scale reads nearly linearly with replicas and handle terabytes without difficulty. The real ceiling is single-primary write throughput — typically five to fifteen thousand committed transactions per second — and crossing it means sharding, which costs you cross-shard joins and transactions. Most systems never reach that ceiling."*

## 5. Request / data flow

A BookNook customer places an order. Follow it from browser to durable disk.

1. Browser sends `POST /orders` with an `Idempotency-Key` header (Lesson 17) over TLS to the CDN edge, which passes it through uncached because it is a mutation (Lesson 32).
2. The load balancer picks a healthy app server using weighted least connections (Lesson 23) and forwards the request.
3. The app server's handler asks its **in-process connection pool** for a connection. The pool has one idle — no TCP or TLS handshake is paid.
4. That pooled connection in fact terminates at **PgBouncer** in transaction mode, which assigns one of its 40 real backend connections for the duration of the transaction only.
5. The app issues `BEGIN;`. The backend assigns a transaction ID and takes an **MVCC snapshot** — a record of which transactions were committed at this instant. Every read in this transaction will see exactly that version of the world.
6. The app issues `SELECT quantity FROM inventory WHERE book_id = $1 FOR UPDATE;`. **Parse** → **analyse** → **plan**: the planner sees a unique index on `inventory(book_id)`, estimates 1 row, chooses an index scan.
7. **Execute:** the B-tree root page is already in the buffer pool (**cache hit**, ~0.0001 ms); so are the internal pages. The leaf page is a **cache miss** and is read from NVMe (~0.1 ms) into the buffer pool, evicting a cold page.
8. `FOR UPDATE` takes a **row-level exclusive lock** on that inventory row. A second customer buying the last copy of the same book now *waits here* — this serialisation on the contended row is what prevents overselling.
9. Quantity is 3; the app proceeds. It issues `UPDATE inventory SET quantity = quantity - 1 WHERE book_id = $1;`. PostgreSQL writes a **new row version** marked created-by-this-transaction and marks the old one expired-by-this-transaction. A **WAL record** describing the change is appended to the in-memory WAL buffer. Nothing is on disk yet.
10. `INSERT INTO orders ...` returns the generated `order_id`; two `INSERT INTO order_items ...` follow. Each emits WAL records, and each checks the **foreign key** to `users` and `books` — a read of those tables' unique indexes (Lesson 41).
11. The app issues `COMMIT;`. Now the only expensive thing happens: the WAL up to this transaction's commit record is written and **`fsync`ed** to durable storage (~0.3–1 ms on NVMe with a battery-backed or capacitor-protected cache). **Group commit** batches this flush with the twelve other transactions committing in the same window, so all thirteen pay one flush.
12. If synchronous replication is configured, the primary also waits for at least one replica to acknowledge receipt of that WAL (Lesson 34) — adding one network round trip, typically 0.5–2 ms within a region, which is the price of not losing committed orders when the primary dies.
13. The backend returns success. **The data pages are still only in RAM**, dirty. Durability came from the log, not from the table.
14. The app releases the connection back to PgBouncer, which returns it to its pool; the row lock is released at commit, and the second customer's `FOR UPDATE` now proceeds and sees quantity 2.
15. Minutes later a **checkpoint** writes the dirty inventory and order pages to the data files and advances the redo point, allowing old WAL segments to be recycled.
16. Later still, **autovacuum** visits `inventory`, finds the old row version invisible to every live transaction, and reclaims its space for reuse.

Steps 11 and 13 together are the heart of it: *commit latency is a log flush, not a table write.* Steps 15 and 16 are the background work that pays for the trick, and both are where mysterious periodic latency comes from.

## 6. ASCII diagram

```
                      ONE QUERY, END TO END
  app ──SQL text──▶ ┌──────────────────────────────────────────┐
                    │ 1 PARSE      syntax → parse tree          │
                    │ 2 ANALYSE    names, types, view expansion │
                    │ 3 PLAN       cost-based: which index?     │
                    │              which join order? which algo?│
                    │              ▲ uses STATISTICS (sampled)  │
                    │ 4 EXECUTE    operator tree pulls rows     │
                    └───────────────────┬──────────────────────┘
                                        │ page requests
                                        ▼
   ┌─────────────────── BUFFER POOL (RAM, e.g. 16 GB) ───────────────────┐
   │  [pg 41][pg 7 ][pg 19*][pg 88][pg 3 ]   * = dirty (modified)        │
   │  HIT  ≈ 0.0001 ms            MISS ≈ 0.1 ms (NVMe)  → 1000× gap      │
   └────────────┬─────────────────────────────────┬──────────────────────┘
       miss → read page                  dirty pages flushed at CHECKPOINT
                │                                 │
                ▼                                 ▼
   ┌──────────── DATA FILES (heap pages + index pages) ──────────────────┐
   └─────────────────────────────────────────────────────────────────────┘
                ▲
                │ replay on crash
   ┌────────────┴──────────── WAL (sequential append) ───────────────────┐
   │  ...│ UPD inventory │ INS orders │ INS order_items │ COMMIT │...    │
   │  COMMIT = fsync THIS, not the data pages.  ← where durability lives │
   └──────────────┬──────────────────────────────────────────────────────┘
                  └── streamed to replicas (Lesson 34) ──▶ [replica]

                 WHY CONNECTION POOLING IS NOT OPTIONAL
  NO POOL:  40 pods × 500 req/s, new conn each time
            → TCP+TLS 10 ms per request  (90% of a 11 ms budget)
            → 500/s × 60 s TIME_WAIT = 30,000 sockets held
            → ephemeral range ≈ 28,000  ⇒ EADDRNOTAVAIL (Lesson 3)
            → 20,000 server processes × 8 MB = 160 GB RAM. Dead.

  POOLED:   40 pods ──20 each──▶ [PgBouncer, txn mode] ──40──▶ [Postgres]
            800 client conns multiplexed onto 40 real backends.
            Handshake paid once. TIME_WAIT ≈ 0. Memory bounded.
            Rule of thumb: active backends ≈ cores×2 + spindles ≈ 32–40
```

## 7. Detailed example

### BookNook

BookNook stores seven tables: `books`, `authors`, `users`, `orders`, `order_items`, `reviews`, `inventory`. Current scale: 1.2 M books, 400 K authors, 900 K users, 6 M orders, 15 M order items, 2 M reviews. Total about **180 GB** including indexes. Traffic peaks at 70 requests/second, roughly 350 queries/second.

The product manager asks for the catalogue page: *"Books by Irish authors published since 2015, under £10, in stock, sorted by average review score, 20 per page."*

In a navigational database this is a new access path: someone must design a chain from nationality to author to book, migrate the data, and rewrite the programs. In BookNook it is one declarative query:

```sql
SELECT b.book_id, b.title, b.price_pence, AVG(r.rating) AS avg_rating
FROM   books b
JOIN   authors   a ON a.author_id = b.author_id
JOIN   inventory i ON i.book_id   = b.book_id
LEFT   JOIN reviews r ON r.book_id = b.book_id
WHERE  a.country = 'IE'
  AND  b.published_on >= DATE '2015-01-01'
  AND  b.price_pence < 1000
  AND  i.quantity > 0
GROUP  BY b.book_id, b.title, b.price_pence
ORDER  BY avg_rating DESC NULLS LAST
LIMIT  20;
```

What the planner does with it, and why this is the whole point of the lesson:

- Statistics say `authors.country = 'IE'` matches about **3,100 of 400,000** rows (0.8 %). That is selective, so an index scan on `authors(country)` wins over a sequential scan.
- Statistics say `books.price_pence < 1000` matches about **38 %**. That is *not* selective; using an index on `price_pence` would mean fetching 38 % of the heap one page at a time — slower than scanning. The planner correctly ignores that index.
- So it drives from `authors`, joins to `books` via the foreign-key index `books(author_id)` with a **nested loop** (3,100 outer rows × a cheap index lookup each), then **hash joins** the ~9,000 surviving books against `inventory`.
- Six months later BookNook signs a large Irish publisher and `country = 'IE'` grows to 90,000 rows (22 %). After `ANALYZE` refreshes statistics, the planner **silently switches** to a hash join driven by a sequential scan on `authors`. No code changed. That is physical independence doing its job.

The failure version of the same story: BookNook disabled autovacuum on `authors` during a migration and never re-enabled it. Statistics froze at the old 3,100 estimate. The planner kept choosing nested loops with 90,000 outer rows, and the catalogue page went from 25 ms to 4.5 seconds. The query was fine. The *estimate* was wrong. This is the single most common relational performance incident, and §15 returns to it.

### Contrasting example — a bank's core ledger

A retail bank's ledger has the opposite shape to BookNook: modest data volume (a few hundred million rows), modest throughput (a few thousand transactions/second), and **absolutely unforgiving correctness requirements**. Money moved between two accounts must debit one and credit the other atomically; an account balance must never go below its agreed overdraft; every movement must be auditable forever.

Here the relational database is chosen not for querying power but for **transactions and constraints**:

```sql
BEGIN;
  UPDATE accounts SET balance_pence = balance_pence - 5000 WHERE account_id = 101;
  UPDATE accounts SET balance_pence = balance_pence + 5000 WHERE account_id = 202;
COMMIT;   -- both or neither; a CHECK constraint rejects a negative balance
```

The contrast with BookNook is instructive. BookNook could survive a lost review or a stale stock count for a few seconds — its pain is *read* shapes and *latency*. The bank cannot survive a lost half-transfer at any price — its pain is *correctness under concurrency*, and it will happily run at 5 % of the hardware's throughput to get it (serialisable isolation, synchronous replication to two zones, and no denormalised copies of the balance anywhere). Same engine, opposite tuning, because the requirements are opposite. Choosing a database is choosing which guarantee you are buying.

## 8. Code or configuration example

> **Illustrative only — not production-ready.** Error classification, retry/back-off, observability, migrations, secrets management, TLS certificate verification and schema evolution are all omitted or simplified. Numbers are tuned for a 16 vCPU / 64 GB example instance. Do not paste this into a production system.

```sql
-- ============ Minimal BookNook slice, showing the relational model ============
-- Lesson 41 gives the full schema; this is only enough to show transactions,
-- constraints and planning.

CREATE TABLE authors (
    author_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, -- surrogate key, monotonic
    full_name  TEXT    NOT NULL,                                -- NOT NULL = invariant, not advice
    country    CHAR(2) NOT NULL                                 -- fixed-width domain (ISO 3166-1)
);

CREATE TABLE books (
    book_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    isbn13       CHAR(13) NOT NULL UNIQUE,                     -- natural key, kept as a UNIQUE
    title        TEXT     NOT NULL,
    author_id    BIGINT   NOT NULL REFERENCES authors(author_id), -- referential integrity
    price_pence  INTEGER  NOT NULL CHECK (price_pence >= 0),   -- integer money: never FLOAT
    published_on DATE     NOT NULL
);

CREATE TABLE inventory (
    book_id   BIGINT PRIMARY KEY REFERENCES books(book_id),    -- 1:1 with books
    quantity  INTEGER NOT NULL CHECK (quantity >= 0)           -- DB refuses to oversell, full stop
);

-- Foreign keys are NOT auto-indexed in PostgreSQL. This index serves the join
-- in §7 and also makes deletes/updates of a parent row fast.
CREATE INDEX books_author_id_idx ON books (author_id);

-- Composite index supporting the catalogue query's leading filter.
-- Column order matters: equality column first, range column second (Lesson 54).
CREATE INDEX books_author_published_idx ON books (author_id, published_on DESC);

-- ============ The order-placement transaction ============
BEGIN;                                                          -- snapshot taken here

  -- FOR UPDATE takes a row lock so two concurrent buyers of the last copy
  -- serialise here instead of both reading quantity = 1.
  SELECT quantity FROM inventory WHERE book_id = 4711 FOR UPDATE;

  -- The CHECK (quantity >= 0) makes overselling impossible even if the
  -- application logic above is wrong. Defence in depth: the DB is the
  -- last line, not the first.
  UPDATE inventory SET quantity = quantity - 1 WHERE book_id = 4711;

  INSERT INTO orders (user_id, placed_at, status)
  VALUES (900123, now(), 'PENDING')
  RETURNING order_id;                                           -- avoids a second round trip

  INSERT INTO order_items (order_id, book_id, quantity, unit_price_pence)
  VALUES (currval(pg_get_serial_sequence('orders','order_id')), 4711, 1, 999);

COMMIT;                                                         -- WAL fsync happens HERE

-- ============ Seeing the planner's reasoning ============
-- BUFFERS shows cache hits vs disk reads; the estimate-vs-actual gap is the
-- single most diagnostic number in relational performance work.
EXPLAIN (ANALYZE, BUFFERS)
SELECT b.title FROM books b JOIN authors a USING (author_id)
WHERE a.country = 'IE' AND b.price_pence < 1000;
```

```python
# ============ Application side: pooling, retries, and short transactions =======
# Illustrative only. Production code needs structured logging, metrics,
# circuit breaking (Lesson 25), and real error taxonomy.

import psycopg
from psycopg_pool import ConnectionPool

# min_size keeps warm connections so no request pays TCP+TLS setup (Lesson 3/7).
# max_size is DELIBERATELY SMALL: cores*2 + spindles. Per *process*. With 40
# pods this is still 40 x 8 = 320 client conns, so PgBouncer sits in between.
pool = ConnectionPool(
    conninfo="host=pgbouncer.internal port=6432 dbname=booknook sslmode=verify-full",
    min_size=4,
    max_size=8,
    max_lifetime=30 * 60,     # recycle so a failed-over primary is picked up
    timeout=2.0,              # fail fast rather than queueing forever (Lesson 14)
)

def place_order(user_id: int, book_id: int, qty: int, unit_price: int) -> int:
    # `with pool.connection()` borrows a connection and returns it on exit --
    # ALWAYS scope it to the narrowest block. A connection held across an HTTP
    # call to a payment provider is how pools get exhausted and how vacuum gets
    # blocked by idle-in-transaction sessions (see section 4.2).
    with pool.connection() as conn:
        with conn.transaction():              # BEGIN ... COMMIT / ROLLBACK
            cur = conn.execute(
                # Parameterised: the driver sends values separately from SQL
                # text, so this is immune to SQL injection AND lets the server
                # reuse the plan for repeated executions.
                "UPDATE inventory SET quantity = quantity - %s "
                "WHERE book_id = %s AND quantity >= %s "   # guard makes it atomic
                "RETURNING quantity",
                (qty, book_id, qty),
            )
            if cur.rowcount == 0:             # 0 rows => insufficient stock
                raise OutOfStock(book_id)     # transaction rolls back on raise

            order_id = conn.execute(
                "INSERT INTO orders (user_id, placed_at, status) "
                "VALUES (%s, now(), 'PENDING') RETURNING order_id",
                (user_id,),
            ).fetchone()[0]

            # executemany batches round trips; 1 network round trip beats N.
            conn.execute(
                "INSERT INTO order_items (order_id, book_id, quantity, unit_price_pence) "
                "VALUES (%s, %s, %s, %s)",
                (order_id, book_id, qty, unit_price),
            )
            return order_id
    # Connection returns to the pool here, lock released, WAL already fsynced.

class OutOfStock(Exception):
    pass
```

```ini
; ============ PgBouncer, transaction pooling (illustrative) ============
[databases]
booknook = host=pg-primary.internal port=5432 dbname=booknook

[pgbouncer]
pool_mode = transaction      ; connection is held only for the length of a txn.
                             ; BREAKS: session-level SET, LISTEN/NOTIFY,
                             ; session advisory locks. Know this before enabling.
max_client_conn = 2000       ; cheap: these are just sockets at the pooler
default_pool_size = 40       ; expensive: these are REAL Postgres backends
reserve_pool_size = 5        ; small emergency headroom
server_idle_timeout = 600
```

## 9. Numerical example

**Question:** BookNook is at 350 queries/second and growing 8 % per month. The catalogue page has slowed from 25 ms to 180 ms. Do we need to shard, add replicas, or fix something else? Work it out from first principles.

**Stated assumptions** (all labelled; change any one and the conclusion may change):
- Instance: 16 vCPU, **64 GB RAM**, local NVMe.
- `shared_buffers` = 25 % of RAM = **16 GB**; OS page cache usefully holds another ~30 GB, so call the effective cache **40 GB**.
- Page size **8 KiB**.
- Buffer-pool hit ≈ **0.0001 ms**; NVMe random read ≈ **0.1 ms**; NVMe sustained random IOPS ≈ **100,000**.
- Total database size **180 GB**; the *hot* set (pages touched by live traffic) is **35 GB**.
- The catalogue query touches, on average, **420 pages**.
- Traffic: 350 queries/s overall, of which **40 /s** are catalogue queries.

**Step 1 — is the hot set resident?**
Hot set 35 GB vs effective cache 40 GB → 35 / 40 = **87.5 % of cache occupied by hot pages**. It fits, but with only 5 GB of headroom. Expected hit ratio should be high, roughly 98–99 %.

**Step 2 — what hit ratio do the observed 180 ms imply?**
Let *h* be the hit ratio. Time for 420 page accesses:

```
t = 420 × [ h × 0.0001 ms + (1 − h) × 0.1 ms ]
```

Set `t = 180 ms` (and ignore CPU time, which is conservative — it makes the implied miss rate a slight over-estimate):

```
180 / 420            = h × 0.0001 + (1 − h) × 0.1
0.4286               = 0.1 − 0.0999 h
0.0999 h             = 0.1 − 0.4286  →  negative
```

A negative result means **page I/O alone cannot explain 180 ms**: even at a 0 % hit ratio, 420 pages × 0.1 ms = **42 ms**. So at least 138 ms of the 180 ms is *not* page fetching. It is CPU: rows being examined, compared and discarded.

**Step 3 — how many rows are being examined?**
Assume ~**0.2 µs of CPU per row examined** (typical for a simple comparison inside an executor loop, in-cache). Then:

```
138 ms / 0.0002 ms per row = 690,000 rows examined
```

But the query *returns* 20 rows. **690,000 rows examined to return 20** is a ratio of 34,500:1. That is the signature of a plan that scans and filters rather than seeking — exactly the stale-statistics nested-loop failure from §7.

**Step 4 — compare the three candidate remedies.**

*Option A: shard (Lesson 37).* Splitting across 4 shards divides rows per shard by 4: 690,000 → 172,500 examined, so ≈ 34 ms of CPU, total ≈ 76 ms. A **2.4× improvement** for an enormous increase in operational complexity, loss of cross-shard joins, and a multi-month project. And it does nothing about the 34,500:1 ratio.

*Option B: add read replicas (Lesson 34).* Replicas multiply **throughput**, not per-query speed. Each replica still examines 690,000 rows and still takes 180 ms. Latency improves **0 %**. This is the most commonly proposed wrong answer.

*Option C: fix the plan.* Run `ANALYZE`, and add the composite index `books(author_id, published_on DESC)`. The planner now seeks the 3,100 Irish authors, index-joins to ~9,000 books, and examines perhaps **12,000 rows**:

```
CPU:   12,000 × 0.0002 ms                  =  2.4 ms
Pages: ~90 pages × [0.99×0.0001 + 0.01×0.1] = ~0.1 ms
Total                                      ≈  3 ms  (plus ~2 ms overhead) ≈ 5 ms
```

**180 ms → ~5 ms, a 36× improvement, from one `ANALYZE` and one index.**

**Step 5 — headroom check after the fix.**
At 5 ms per catalogue query and 40 of them per second, that workload consumes 40 × 0.005 = **0.2 CPU-seconds per second** — 1.25 % of 16 vCPU. Total traffic of 350 queries/s at an average of, say, 2 ms is 0.7 CPU-seconds/s, about **4.4 % utilisation**. At 8 % monthly growth, reaching 50 % utilisation takes:

```
1.08^n = 50 / 4.4 = 11.4   →   n = ln(11.4) / ln(1.08) = 2.434 / 0.0770 ≈ 32 months
```

**Conclusion:** sharding was never the answer; replicas were never the answer. One stale-statistics plan regression looked like a scaling wall, and the real runway on a single instance is **about two and a half years**. This is the arithmetic that should precede every "we need to go distributed" proposal.

## 10. Trade-offs

| You gain | You pay |
|---|---|
| **Declarative querying** — any question, no new access path | The planner can be wrong, and when it is, performance changes by orders of magnitude for reasons invisible in your code |
| **Schema-on-write** — invalid data is impossible, not merely discouraged | Schema changes require migrations; `ALTER TABLE` on a 500 M-row table is a project, and a careless one takes a lock that stops all traffic |
| **ACID transactions** — multi-row invariants hold under concurrency | Transactions serialise on contended rows; a hot row becomes a global throughput ceiling regardless of hardware |
| **Joins** — store each fact once, assemble at read time | Read cost grows with join count; the fix (denormalisation, Lesson 56) reintroduces the duplication the model removed |
| **Referential integrity** — no orphan rows, ever | Foreign-key checks cost a read per write; deletes of a parent row can cascade expensively |
| **Maturity** — 40+ years of tooling, documentation, operational knowledge, and people who know it | Decades of accumulated defaults and footguns: `NULL` semantics, implicit casts, isolation-level surprises |
| **Physical independence** — add an index, change nothing else | You must *own* performance; the database will not tell you that you are missing an index |
| **One durable source of truth** | One **primary** for writes: a single scaling ceiling and a single failure domain until you shard or go distributed-SQL |

The meta-trade-off: a relational database moves work and risk **from runtime to design time**. You must decide the schema up front, and you get correctness guarantees in exchange. A document database (Lesson 44) moves the same work in the opposite direction: start instantly, discover at runtime that six services wrote the `address` field six different ways.

## 11. Failure scenarios

| Failure | What the user experiences | How it is detected | How it recovers |
|---|---|---|---|
| **Primary crashes (process or host)** | Writes fail instantly with connection errors; reads from replicas keep working | Orchestrator (Patroni, RDS monitor) misses health checks for 2–3 intervals; app sees `connection refused` | Automatic failover promotes a replica (typically **10–60 s**). Committed WAL is replayed; with async replication, transactions committed in the last lag window (**10–200 ms**, Lesson 35) are **lost**. Synchronous replication prevents the loss at the cost of ~1 ms per commit |
| **Connection-pool exhaustion** | Requests hang, then time out; whole site looks down although the DB is idle | Pool wait-time metric spikes; `pg_stat_activity` shows many `idle in transaction`; CPU is *low* | Kill the leaking sessions; set `idle_in_transaction_session_timeout`; scope transactions to exclude network calls. Raising `max_size` makes it worse |
| **Ephemeral port exhaustion on an app host (Lesson 3)** | Intermittent 500s that correlate with traffic, not with DB load | `EADDRNOTAVAIL` / "cannot assign requested address"; `ss -s` shows tens of thousands of `TIME_WAIT` | Introduce pooling so connections are reused; this is a client-side bug and no database tuning fixes it |
| **Stale statistics → plan regression** | A page that was 25 ms now takes 4 s; nothing was deployed | p99 for one endpoint diverges; `EXPLAIN ANALYZE` shows estimated rows ≪ actual rows | `ANALYZE` the table; re-enable autovacuum; raise statistics target for skewed columns; add the missing composite index |
| **Table bloat from blocked vacuum** | Steady week-over-week slowdown across many queries; disk usage grows without data growing | Dead-tuple count and table size climb; `pg_stat_activity` shows a transaction open for hours | Terminate the long transaction, let autovacuum catch up; `VACUUM FULL` or `pg_repack` to reclaim space (the former takes an exclusive lock) |
| **Disk full on the WAL volume** | All writes fail; the database may refuse to start | Free-space alarm; `PANIC: could not write to file` | Free space, archive or drop stale replication slots (an abandoned slot pins WAL forever and is the usual culprit), then restart |
| **Lock contention on a hot row** | Checkout latency climbs sharply at peak; throughput plateaus then falls | Lock-wait metrics; `pg_locks` shows a queue on one row | Shorten transactions; reorder statements so the hot row is touched last; replace `SELECT ... FOR UPDATE` with a conditional `UPDATE ... WHERE quantity >= n`; shard the hot counter |
| **Long-running analytics query on the primary** | Everyone's writes slow down; vacuum stops working | A query with hours of runtime; dead tuples rising | Kill it; move analytics to a replica or a warehouse (Lesson 57); set `statement_timeout` |
| **Replica falls far behind** | Users see their own just-written data disappear on refresh (read-your-writes violation, Lesson 61) | Replication-lag metric; WAL-apply delay | Route post-write reads to the primary for a short window; throttle the writer; increase replica resources |
| **Silent data corruption (bad disk/firmware)** | Wrong results, or errors on reads of specific pages | Checksums (`data_checksums = on`) report failures; backup verification fails | Restore from backup + WAL replay to a point in time (**Topic 24**, Lesson 63). Without checksums enabled, corruption can replicate silently into all copies |

## 12. Alternatives

| Option | Data model | Strongest at | Gives up | Choose it when |
|---|---|---|---|---|
| **Relational (PostgreSQL, MySQL)** | Tables, typed columns, enforced relationships | Multi-entity correctness, ad-hoc queries, transactions | Single-primary write ceiling; no free horizontal write scaling | Default. Entities relate to each other and correctness matters |
| **Key-value (Redis, DynamoDB)** (Lesson 43) | Opaque value under a key | Enormous throughput, O(1) lookup, trivial horizontal scaling | Joins, ad-hoc queries, multi-key transactions (mostly), server-side filtering | Access is always "fetch by exact key" — sessions, caches, feature flags |
| **Document (MongoDB, DocumentDB)** (Lesson 44) | Nested JSON documents | Aggregates read and written whole; flexible fields | Cross-document joins and integrity; schema drift becomes your problem | The aggregate boundary matches the access pattern — a CMS page, a product with variants |
| **Wide-column (Cassandra, ScyllaDB)** (Lesson 45) | Partition key + clustered rows | Extreme write rates, linear scale-out, multi-DC writes | Ad-hoc queries (you design tables per query), joins, strong consistency by default | Time series, event logs, feeds at very high write volume |
| **Graph (Neo4j)** (Lesson 46) | Nodes and edges | Variable-depth traversal ("friends of friends of friends") | General-purpose throughput; operational familiarity | Relationships *are* the data and depth is unbounded |
| **Search engine (Elasticsearch, OpenSearch)** (Lesson 47) | Inverted index over documents | Ranked, fuzzy, faceted full-text search | Being a source of truth; transactional writes; near-real-time only | Text relevance matters. Run it *alongside* the relational source of truth |
| **Columnar warehouse (BigQuery, Snowflake, ClickHouse)** (Lessons 57–58) | Columns, compressed | Aggregating billions of rows | Single-row updates, low-latency point reads, transactions | Analytics, BI, reporting — never as the OLTP store |
| **Distributed SQL (CockroachDB, Spanner, Vitess, Citus)** | Tables, sharded transparently, consensus-replicated | Horizontal **write** scaling while keeping SQL and transactions | Higher per-transaction latency (consensus round trips), cost, operational depth | You have genuinely exceeded single-primary write throughput and still need transactions |
| **Embedded (SQLite, DuckDB)** | Tables, in-process | Zero operations, extremely fast local access | Concurrent multi-writer access over a network | Mobile, edge, tests, single-node analytics |

The pattern you will use most in real systems is **not** one of these: it is **relational as the source of truth, plus a cache (Lessons 27–31), plus a search index, plus a warehouse** — each derived from the relational store. Choosing one database for everything is usually the error.

## 13. When to use it

1. **Your data has entities that reference each other and those references must stay valid.** BookNook's `order_items` must point at real `orders` and real `books`. A foreign key makes orphan rows *impossible* at any concurrency; application-level checks make them merely unlikely, and "unlikely" times ten million rows is a cleanup script.
2. **You need multi-row, multi-table atomicity.** Decrement inventory, insert an order, insert line items, record a payment intent — all or nothing. Rebuilding this with sagas over a non-transactional store is weeks of work and a permanent source of partial-failure bugs.
3. **Your query patterns are not fully known in advance.** This is almost always true for a new product, and it is the decisive argument. Relational is the only model where "the business asked a new question" does not mean "rewrite the data layout".
4. **Correctness and auditability dominate.** Finance, healthcare, inventory, compliance, anything with money or a regulator. Constraints enforced once in the database beat the same rule re-implemented in six services, of which five are correct.
5. **Your scale is below the single-primary ceiling** — under roughly 10,000 write transactions/second and a few terabytes — which covers the overwhelming majority of systems, including most that believe otherwise (§9).
6. **Reporting and operational data must agree.** A single consistent store means your dashboard and your checkout page cannot disagree about how many orders exist.

## 14. When not to use it

1. **Write throughput genuinely exceeds a single primary.** Hundreds of thousands of writes per second — IoT telemetry, ad impressions, clickstream, metrics — is a wide-column (Lesson 45) or time-series workload. Do not try to make one primary absorb it.
2. **Analytical scans over hundreds of millions of rows.** A row store reads whole rows to aggregate one column. A column store reads only that column, compressed — often 100× less I/O. Use a warehouse (Lessons 57–58) and leave the OLTP store alone.
3. **Ranked, fuzzy, multilingual full-text search.** `LIKE '%term%'` cannot use a B-tree, so it scans. Even PostgreSQL's good built-in full-text search does not match a dedicated engine for relevance tuning, faceting and typo tolerance (Lesson 47).
4. **Pure cache or session storage at very high rates.** Ephemeral data with a TTL, accessed by exact key, does not need durability, joins or transactions. Redis does it with a fraction of the resources (Lessons 28–30, Lesson 43).
5. **Deep, variable-length graph traversal.** "Everyone within six hops" is a recursive CTE that explodes combinatorially in SQL; a graph database traverses pointers natively (Lesson 46).
6. **Huge binary objects.** Images, video and PDFs belong in object storage (**Topic 23**, Lesson 62) with only the URL and metadata in the database. Storing multi-megabyte blobs in rows inflates the table, wrecks the buffer-pool hit ratio and makes backups enormous.

## 15. Common mistakes

### Beginner mistakes

- **Opening a connection per request.** The direct cause of the ephemeral-port exhaustion in Lesson 3 and §11. Pool from day one.
- **Setting the pool far too large.** "Slow database, so raise `max_size` to 500." Throughput *drops*: more concurrency than cores means context-switching and lock contention, and this is Little's Law (Lesson 14) punishing you. Start at `cores × 2`.
- **Storing money as `FLOAT`.** `0.1 + 0.2 != 0.3` in binary floating point. Use `NUMERIC`/`DECIMAL`, or integer minor units (`price_pence`) as in §8.
- **No index on a foreign-key column.** PostgreSQL does not create one automatically. Every join and every parent delete then scans the child table.
- **`SELECT *` everywhere.** Pulls columns you discard, inflates network and memory, and — importantly — prevents **index-only scans** (Lesson 54), because the index cannot satisfy the query alone.
- **Misunderstanding `NULL`.** `NULL = NULL` is not true, it is `NULL`. `WHERE status != 'SHIPPED'` silently excludes rows where `status IS NULL`. This causes wrong *answers*, not errors, which is worse.
- **String-concatenating SQL.** SQL injection. Always use parameterised queries, as in §8.
- **Believing rows come back in insertion order.** Without `ORDER BY` there is no order. The bug appears the day the planner switches to a parallel scan.
- **Running `COUNT(*)` on a huge table for a dashboard.** In PostgreSQL that is an exact count requiring a full scan. Use an approximate count from statistics, or maintain a counter.

### Experienced-engineer mistakes

- **Reaching for sharding before measuring.** §9 is the antidote: compute current utilisation and the growth runway before proposing distribution. The cost of sharding is permanent; the cost of an index is five minutes.
- **Letting an ORM dictate the query shape.** Lazy-loading generates the N+1 problem (Lesson 41 §9) and `JOIN`s nobody reviewed. Log the generated SQL in CI and assert on query counts per endpoint.
- **Holding a transaction open across a network call.** `BEGIN;` → call Stripe → `COMMIT;` pins an MVCC snapshot for the duration of a third-party request, blocks vacuum database-wide, and converts a payment-provider slowdown into a database outage. Do external I/O outside the transaction.
- **Treating replicas as a latency fix.** They multiply throughput, not speed (§9, Option B). Worse, routing reads to a replica breaks read-your-writes (Lesson 61) unless you route post-write reads to the primary.
- **Ignoring vacuum and autovacuum tuning on write-heavy tables.** Default autovacuum thresholds are proportional to table size and are far too lax for a 200 M-row hot table. Tune per table.
- **Migrating with a naive `ALTER TABLE`.** Some forms take an `ACCESS EXCLUSIVE` lock and rewrite the table; because lock requests queue, one blocked `ALTER` blocks *every* subsequent query on that table. Use `lock_timeout`, add columns as nullable without defaults where rewrites are implied, and backfill in batches.
- **Enforcing invariants only in application code.** Six services, six implementations, one of them wrong. Put the invariant in a `CHECK`, `UNIQUE` or `FOREIGN KEY` and it holds for every writer forever (Lesson 41).
- **Leaving an abandoned replication slot.** It pins WAL indefinitely and silently fills the disk weeks later.
- **Disabling foreign keys "for performance" without measuring.** The cost is typically a few percent on write-heavy paths. The benefit — the impossibility of orphan rows — is usually worth far more than the few percent.
- **Using `SERIALIZABLE` without a retry loop.** Serialisable transactions can abort with a serialisation failure by design; code that does not retry simply fails under load (Lessons 50–52).

## 16. Interview perspective

Interviewers use "would you use a relational database here?" to test judgement, not recall. Four things score.

**1. Choose from access patterns, not from vocabulary.** Say: *"Writes are a few hundred per second, entities reference each other, and we need multi-row atomicity for checkout, so PostgreSQL is the source of truth. Search goes to a separate index because relevance ranking is not a B-tree workload, and analytics goes to a warehouse so reporting cannot slow the checkout path."* That single sentence demonstrates the whole phase.

**2. Quantify the ceiling before escaping it.** Give the numbers: *"One primary handles roughly 5–15 thousand write transactions per second and terabytes of data. We're projecting 400 writes per second, so we're at a few percent. I'd revisit sharding at ten thousand."* Candidates who propose sharding at 400 writes/second are revealing that they have never measured anything.

**3. Show you know where durability lives.** *"Commit means the WAL record was fsynced; the data pages are flushed later at a checkpoint. That is why commit latency is a sequential log write and why replication is just shipping that log."* This separates people who have operated a database from people who have read about one.

**4. Name the trade-off you are accepting.** Every choice has one. *"I'm choosing async replication, so a primary failure loses up to the lag window — tens to a couple of hundred milliseconds of committed orders. If that's unacceptable for payments, I'd make the payments path synchronous and pay about a millisecond per commit."*

**Red flags interviewers watch for:**
- "SQL doesn't scale" offered as a fact, with no number attached.
- Proposing sharding or a distributed SQL engine before stating current and projected throughput.
- Suggesting read replicas to fix **latency** (they fix throughput).
- Not knowing that a commit is a log flush, and thinking the table is written at commit time.
- Opening a connection per request, or proposing a 500-connection pool.
- Saying "we'll enforce that in the application" for an invariant that is one `CHECK` constraint.
- Choosing MongoDB or DynamoDB for highly relational data because it is "faster", without naming what is being given up.
- Treating ACID as a single on/off switch and being unable to name an isolation level.
- Storing images or multi-megabyte blobs in table rows.
- Saying "it depends" and stopping, instead of *"it depends on write rate, on whether reads are by exact key, and on whether we need multi-row atomicity — here is how each answer changes the choice."*
- Designing a schema with no foreign keys and no `NOT NULL`, then calling it "flexible".

## 17. Knowledge check

**Conceptual**

1. A transaction commits successfully and the server loses power 50 milliseconds later, before any modified data page has been written to the table files. Explain precisely why the committed change is not lost, naming the structure responsible and the exact moment durability was established.
2. PostgreSQL stores tables as unordered heaps with indexes pointing at physical row addresses, while MySQL's InnoDB stores the table itself as a B-tree clustered on the primary key. Explain two concrete, observable behavioural differences this causes — one about secondary-index lookups and one about what happens when primary keys are inserted in random order.
3. A query that ran in 20 ms for a year suddenly takes 4 seconds. No code was deployed, no data was deleted, the hardware is unchanged, and the row count grew by only 3 %. Explain the most likely mechanism, identify which of the four query-execution stages is responsible, and state the one diagnostic comparison you would look at first.

**Practical scenario**

4. BookNook's checkout endpoint starts timing out during a flash sale. Database CPU sits at 12 %, disk I/O is low, and `pg_stat_activity` shows 480 sessions in state `idle in transaction`. Deployments that day added a call to an external fraud-scoring API inside the order-placement transaction. Explain the causal chain from that code change to the timeouts — including the effect on connection pooling *and* the effect on vacuum — and give the correct fix plus two safeguards that would have surfaced the problem earlier.

**Calculation**

5. BookNook is considering a new "recently viewed" feature that writes one row per book view. Assume: 2,000,000 page views per day, 60 % of them book pages; peak traffic is 4× the daily average; each view is written as its own single-row transaction; the database is the 16 vCPU instance from §4.6 with a sustained commit ceiling of 12,000 transactions per second; and each row is 120 bytes with two indexes adding 100 % overhead. Calculate (a) the average and peak writes per second, (b) the percentage of the write ceiling consumed at peak, (c) the storage growth per year in GB, and (d) the peak writes per second at which this single design would hit 60 % of the commit ceiling, and roughly how many times current traffic that represents. State every assumption you use.

**Progress checklist**

```
Phase 1 — Foundations (Lessons 1–17) ................................ [x] complete
Phase 2 — Scaling (Lessons 18–39) ................................... [x] complete
Phase 3 — Data and Storage
  [x]  1. Relational databases ...................................... Lesson 40
  [ ]  2. Tables, keys, relationships, joins, constraints ........... Lesson 41
  [ ]  3. SQL versus NoSQL .......................................... Lesson 42
  [ ]  4. Key-value databases ....................................... Lesson 43
  [ ]  5. Document databases ........................................ Lesson 44
  [ ]  6. Wide-column databases ..................................... Lesson 45
  [ ]  7. Graph databases ........................................... Lesson 46
  [ ]  8. Search engines and full-text indexes ...................... Lesson 47
  [ ]  9. Selecting a database from access patterns ................. Lesson 48
  [ ] 10. ACID transactions ......................................... Lesson 49
  [ ] 11. Isolation levels .......................................... Lesson 50
  [ ] 12. Dirty / non-repeatable / phantom reads, lost updates ...... Lesson 51
  [ ] 13. Optimistic versus pessimistic concurrency control ......... Lesson 52
  [ ] 14. Database indexes .......................................... Lesson 53
  [ ] 15. B-tree, hash, composite, covering, secondary indexes ...... Lesson 54
  [ ] 16. Query plans and index trade-offs .......................... Lesson 55
  [ ] 17. Normalisation and denormalisation ......................... Lesson 56
  [ ] 18. OLTP versus OLAP .......................................... Lesson 57
  [ ] 19. Data warehouses, data lakes and lakehouses ................ Lesson 58
  [ ] 20. CAP theorem ............................................... Lesson 59
  [ ] 21. PACELC .................................................... Lesson 60
  [ ] 22. Consistency models ........................................ Lesson 61
  [ ] 23. Object, block and file storage ............................ Lesson 62
  [ ] 24. Retention, archival, backups, disaster recovery ........... Lesson 63
```

**NEXT LESSON: Phase 3, Topic 2 — Tables, Keys, Relationships, Joins, and Constraints**

---

# Lesson 41 — Tables, Keys, Relationships, Joins, and Constraints

*Phase 3, Topic 2*

## 1. Simple definition

Lesson 40 established *what* a relational database is. This lesson is the working vocabulary of actually building one: the five mechanisms you use to turn a set of facts about the world into a schema that cannot hold a wrong answer.

- A **table** holds rows of one entity type (`books`), each row described by typed **columns**.
- A **key** identifies a row uniquely. The **primary key** is the one the database guarantees is unique and non-null, and the one other tables point at.
- A **relationship** is a link between tables, declared with a **foreign key**: a column whose value must match an existing primary key elsewhere.
- A **join** reassembles, at query time, facts that were deliberately stored apart.
- A **constraint** is a rule the database enforces on every write, by every client, forever — a `NOT NULL`, `UNIQUE`, `CHECK`, `EXCLUDE` or foreign key.

Additional terms defined before use:
- **Referential integrity**: the property that every foreign-key value points at a row that actually exists. No orphans.
- **Cardinality**: how many rows on one side of a relationship relate to how many on the other — one-to-one, one-to-many, many-to-many.
- **Junction table** (also *join table*, *associative entity*): a table whose purpose is to represent a many-to-many relationship as two many-to-one relationships.
- **Invariant**: a statement that must be true of the data at all times — "quantity is never negative", "no two reviews by the same user for the same book".
- **DDL**: Data Definition Language — `CREATE`, `ALTER`, `DROP`. The statements that define structure, as opposed to DML (`SELECT`, `INSERT`, `UPDATE`, `DELETE`), which manipulate data.

The organising idea of the entire lesson: **an invariant enforced by the database is true; an invariant enforced by application code is true only of the code paths you remembered.**

## 2. Why it exists

Imagine BookNook stored everything in one wide table, one row per order line:

| order_id | user_email | user_address | book_title | author_name | author_country | price | qty |
|---|---|---|---|---|---|---|---|

It works until it is used. Four classes of defect appear, and they have names:

- **Update anomaly.** An author changes their name. The name is repeated on every order line for every book they wrote — 40,000 rows. Update 39,998 of them and the database now holds two contradictory truths, with no way to tell which is right.
- **Insertion anomaly.** You sign a new author who has not published yet. There is nowhere to put them: the row requires an `order_id`. The schema cannot represent a fact the business has.
- **Deletion anomaly.** The last order for a book is cancelled and deleted. The book's price, title and author vanish with it. Deleting a *transaction* destroyed *reference data*.
- **Inconsistent representation.** `"J. Smith"`, `"J Smith"`, `"Smith, J."` and `"j. smith"` are four authors to a computer and one person to a human. `GROUP BY author_name` now reports nonsense.

The relational response is to store each fact **exactly once**, in the table where it belongs, and link tables by keys — **normalisation** (**Topic 17**, Lesson 56, treats this formally). Authors go in `authors`, books in `books` with an `author_id` pointing at them. Renaming an author becomes one `UPDATE` of one row, correct by construction.

That decomposition creates two new requirements, and the rest of this lesson is the answer to them:

1. **Reassembly.** A catalogue page needs title *and* author name. Those now live in two tables, so you need **joins**.
2. **Trust.** If `books.author_id` can contain `99999` when no such author exists, you have decomposed your data and lost your integrity. So you need **foreign keys and constraints**.

The historical argument is worth stating plainly. In pre-relational systems, integrity was the application's job, and the result was decades of data-cleanup projects. Codd's insight (Lesson 40 §2) was that integrity rules are *part of the data model*, declarable once and enforced by the engine regardless of which of your programs — or which intern's one-off script — does the writing.

## 3. Real-world analogy

**A warehouse with a parts catalogue, a bin system and a receiving desk.**

- **Tables** are the shelving units: one for parts, one for suppliers, one for orders. You do not mix suppliers and parts on the same shelf.
- **The primary key** is the barcode on each bin. It is unique, it never changes, and every other document refers to the bin by barcode rather than by describing its contents. A **natural key** would be using the manufacturer's part number as the barcode; a **surrogate key** is issuing your own sequential sticker.
- **Foreign keys** are the "supplied by" field on each bin label. The receiving desk refuses to accept a bin whose supplier code is not in the supplier register — that refusal *is* referential integrity.
- **`ON DELETE RESTRICT`** is the rule "you may not delete a supplier while any bin still names them". **`ON DELETE CASCADE`** is "removing an order removes its line items too". **`ON DELETE SET NULL`** is "if a supplier closes, blank the field on the bins but keep the parts".
- **A join** is the picker walking from a bin label to the supplier register to fetch an address. Doing it once per bin for 500 bins is the N+1 problem (§4.7) made physical.
- **A junction table** is the cross-reference binder for "which parts fit which machines" — neither parts nor machines owns the fact, so it gets its own binder.
- **Constraints** are the checks at the receiving desk: weight must be positive (`CHECK`), no two bins may hold the same barcode (`UNIQUE`), the destination field may not be blank (`NOT NULL`), two deliveries may not book the same loading bay for the same hour (`EXCLUDE`).
- **Join algorithms** are how the picker works: fetch one at a time (nested loop), pre-sort both lists and walk them together (merge), or build a quick index of one list on a clipboard first (hash).

**Where the analogy breaks down:**

| Warehouse | Relational database |
|---|---|
| A picker chooses a route by habit | The planner re-chooses the join algorithm **per execution**, based on statistics; the same query can run three different ways in one day (Lesson 55) |
| A blank field is obviously blank | `NULL` is not "blank": it means *unknown*, it is not equal to itself, it breaks `NOT IN` subqueries, and `UNIQUE` permits many `NULL`s |
| A clerk refusing a bad delivery costs seconds | A foreign-key check costs a read per write, so it is a measurable (few-percent) write cost — real, and almost always worth paying |
| One clerk, one delivery at a time | Thousands of concurrent writers; a `UNIQUE` constraint is the *only* reliable way to prevent two of them inserting the same thing simultaneously. A `SELECT`-then-`INSERT` check in application code loses this race |
| Barcodes are issued in order | Choosing a *random* identifier (UUID v4) instead of a sequential one changes write performance by multiples, for reasons invisible at the logical level (§4.3) |
| Cross-reference binders are small | A junction table is often the **largest table in the database** — BookNook's `order_items` dwarfs `orders` |

## 4. Detailed technical explanation

### 4.1 Tables, rows, columns and types

A table declaration fixes, for every row: the set of columns, each column's **type** (its domain), whether it may be `NULL`, and what value appears when none is supplied (`DEFAULT`).

Type choice is a design decision, not a formality:

| Need | Use | Not | Because |
|---|---|---|---|
| Money | `NUMERIC(12,2)` or `INTEGER` minor units | `FLOAT`/`REAL` | Binary floating point cannot represent `0.10` exactly; errors accumulate and audits fail |
| Timestamps | `TIMESTAMPTZ` | `TIMESTAMP` | `TIMESTAMP` has no zone; the same value means different instants in different sessions. Store UTC instants |
| Identifiers | `BIGINT` or `UUID` | `INTEGER` | 32-bit overflows at 2.1 billion; this has taken real companies offline |
| Short codes | `CHAR(2)` / `TEXT` + `CHECK` | unconstrained `TEXT` | A domain the database can verify |
| Flexible attributes | `JSONB` | `TEXT` holding JSON | `JSONB` is parsed, indexable with GIN, and queryable |
| Small fixed sets | `TEXT` + `CHECK (x IN (...))`, or a lookup table | `ENUM` | PostgreSQL `ENUM` values are awkward to remove/reorder; a `CHECK` is edited with one `ALTER` |
| Boolean | `BOOLEAN` | `CHAR(1)` with `'Y'`/`'N'` | Three-valued logic is already hard; do not add a fourth representation |

Column *order* has a minor physical effect too: in PostgreSQL, placing fixed-width columns before variable-width ones can reduce per-row alignment padding. Worth knowing, rarely worth acting on.

### 4.2 Keys

**Candidate key**: any set of columns that uniquely identifies a row. **Primary key**: the candidate key you promote — it is implicitly `NOT NULL` and `UNIQUE`, and it gets a backing index automatically. **Alternate key**: another candidate key, declared `UNIQUE`. **Composite key**: a key of more than one column — `order_items(order_id, book_id)`.

**Natural key** = a key that already has business meaning (`isbn13`, email address, national insurance number). **Surrogate key** = a value invented by the system with no meaning (`book_id BIGINT GENERATED ALWAYS AS IDENTITY`).

| | Natural key | Surrogate key |
|---|---|---|
| Meaning | Carries business meaning | None |
| Stability | **Changes.** ISBNs get reassigned; emails change; "immutable" business identifiers are reissued after corporate restructures | Never changes, because nothing in the world maps to it |
| Width | Often wide (`CHAR(13)`, `TEXT`) — and every foreign key and index copies that width | 8 bytes |
| Joins | Wider keys mean larger indexes and more I/O | Compact |
| Exposure | Leaks business data into URLs and logs | Leaks row counts if sequential (mitigate with a separate public `UUID`) |
| Duplicates | Real-world identifiers *do* collide (two people, one email; the same ISBN reissued) | Guaranteed unique |

**Practical rule:** use a **surrogate primary key**, and declare the natural key as a `UNIQUE` constraint alongside it. You get stable joins *and* enforced business uniqueness. BookNook's `books` has `book_id BIGINT` as primary key and `isbn13 CHAR(13) UNIQUE` as an alternate key.

### 4.3 The UUID trap — v4 versus v7

UUIDs are attractive for distributed systems: a client or any service can generate an identifier with no coordination and no round trip to a sequence. But **UUID version 4 is 122 random bits**, and randomness interacts badly with B-trees.

Why randomness hurts, mechanically:

1. A B-tree index is kept in **sorted key order**. Inserting a monotonically increasing key (`1, 2, 3, …`) always targets the **rightmost leaf page**, which is in the buffer pool because it was touched microseconds ago. The index grows like a well-packed stack.
2. Inserting a *random* key targets a **uniformly random leaf page**. With an index larger than the buffer pool, each insert is a **cache miss** — a random read (~0.1 ms) before the write.
3. Random inserts also cause constant **page splits** in the middle of the tree. A page split copies half a page to a new page, writes full-page images to the WAL, and leaves both pages roughly half full — so the index physically inflates, often by **1.5–2×**, which further reduces the cache hit ratio in a self-reinforcing spiral.
4. **WAL amplification:** more page splits and more full-page writes after each checkpoint means substantially more log volume per row inserted — which is also more bytes to stream to every replica (Lesson 34).

The severity depends on the engine, and this is exactly the PostgreSQL/MySQL difference from Lesson 40 §4.7:

| | PostgreSQL (heap) | MySQL InnoDB (clustered) |
|---|---|---|
| What a random PK scatters | Only the **primary-key index** | The **entire table**, because the table *is* the PK B-tree |
| Secondary indexes | Store a physical `ctid`, unaffected by PK width | Store the **PK value** — so a 16-byte random PK bloats **every secondary index** |
| Typical insert-throughput loss at scale (approximate) | 20–50 % | **2–10×** |

**UUID v7** fixes this by construction: its first 48 bits are a Unix millisecond timestamp, with randomness only in the remaining bits. Values generated over time are therefore **nearly monotonic**, so inserts land on the rightmost pages like a sequence, while retaining decentralised generation and non-guessability. Other sortable options with the same property: ULID, Snowflake IDs, and `uuid_generate_v1mc`.

**Design rules:**
- Need decentralised ID generation? Use **UUID v7** (or ULID/Snowflake). Never v4 as a primary key on a large, write-heavy table.
- Store it as the native `UUID` type (16 bytes), never as `CHAR(36)` text — the text form is **2.25× larger** in every index and every foreign key, and comparisons are slower.
- Already stuck with v4 on a big table? The usual migration is to add a `BIGINT IDENTITY` as the physical primary key, demote the v4 UUID to a `UNIQUE` column for external use, and repoint foreign keys. On InnoDB this is often the single largest write-performance win available.
- Small table (< a few million rows, index fits in RAM)? v4 is fine. Know *why* it is fine — because every leaf page is cached — rather than concluding the problem is imaginary.

### 4.4 Foreign keys and referential integrity

```sql
ALTER TABLE books
  ADD CONSTRAINT books_author_fk
  FOREIGN KEY (author_id) REFERENCES authors(author_id);
```

This declares: every non-null `books.author_id` must match an existing `authors.author_id`. The database now enforces it on **every** `INSERT` and `UPDATE` of `books`, and on every `DELETE` or key-`UPDATE` of `authors` — for every client, including your migration scripts and the analyst with psql access.

What it costs: an index lookup on the parent per child write (a few percent of write throughput, typically), and a lock consideration — in PostgreSQL a child insert takes a `FOR KEY SHARE` lock on the parent row, which can contend if many children reference one very hot parent row.

**Referential actions** decide what happens to children when a parent disappears. Getting these wrong is one of the highest-consequence schema decisions you make, because `CASCADE` is a data-destruction primitive.

| Action | What it does on parent `DELETE` | Right when | Wrong when |
|---|---|---|---|
| `NO ACTION` (default) | Raises an error if children exist; checked at the **end of the statement** (deferrable) | You want safety and the flexibility to fix up inside the same transaction | You expected automatic cleanup |
| `RESTRICT` | Raises an error immediately; **not** deferrable | You want the failure to surface at exactly that statement | You need to delete-and-reinsert inside one transaction |
| `CASCADE` | Deletes the children too, recursively | The child **cannot exist** without the parent and has no independent value: `order_items` without its `order`; `cart_items` without its `cart` | The child is a business record someone may need — never cascade from `users` to `orders`. A `DELETE FROM users WHERE id = 7` must not silently erase seven years of financial history |
| `SET NULL` | Sets the child's FK column to `NULL` | The relationship is **optional** and the child survives alone: a book whose publisher is dissolved | The column is `NOT NULL` (the delete then just fails), or the orphan is meaningless |
| `SET DEFAULT` | Sets the FK to the column default | A sentinel row exists ("Unknown author", id 0) | No such sentinel exists — the delete fails with a new FK violation |

`ON UPDATE` has the same options and matters when parent keys can change — which is precisely the argument for surrogate keys (§4.2): with a surrogate key, parent keys never change and `ON UPDATE` is moot.

**BookNook's actual choices:**
- `order_items.order_id → orders` : **`ON DELETE CASCADE`** — a line item is meaningless without its order.
- `orders.user_id → users` : **`ON DELETE RESTRICT`** — deleting a customer must never destroy order history. For GDPR erasure the correct pattern is *anonymisation* (overwrite personal fields, keep the financial row), not deletion.
- `books.author_id → authors` : **`RESTRICT`** — you do not delete authors who have books.
- `reviews.book_id → books` : **`CASCADE`** — if a book is genuinely removed from the catalogue, its reviews go with it.
- `reviews.user_id → users` : **`SET NULL`** — a review survives as anonymous content.

**Soft deletes** (`deleted_at TIMESTAMPTZ`) side-step most of this, and bring their own cost: every query must filter `WHERE deleted_at IS NULL`, `UNIQUE` constraints must become partial indexes (`WHERE deleted_at IS NULL`), and foreign keys no longer protect you because the parent row still exists. Choose deliberately, not accidentally.

### 4.5 Relationship cardinalities

**One-to-one (1:1).** One row here, at most one row there. Implemented by putting a `UNIQUE` foreign key on one side, or by making the child's primary key *also* the foreign key — BookNook's `inventory` uses `book_id` as both PK and FK, which makes "at most one inventory row per book" structurally impossible to violate. Legitimate uses: splitting off rarely read, wide columns (a `book_descriptions.full_text` column of 40 KB, kept out of `books` so the hot table stays dense and cache-friendly); isolating sensitive data into a separately permissioned table; optional extension attributes.

**One-to-many (1:N).** The overwhelmingly common case. The **foreign key goes on the "many" side** — `books.author_id`, not an array of book ids on `authors`. One author, many books. Index the FK column; PostgreSQL does not do it for you, and without it both the join and any parent delete scan the child table.

**Many-to-many (M:N).** Cannot be represented with a column on either side, because a column holds one value. It requires a third table — the **junction table** — holding one row per pair:

```
books ──1:N──▶ book_categories ◀──N:1── categories
```

Design points for junction tables:
- The natural primary key is the **composite** `(book_id, category_id)`, which simultaneously prevents duplicate pairings. Adding a surrogate `id` *and* keeping that composite as `UNIQUE` is also defensible; adding a surrogate and **omitting** the unique constraint is a bug that fills the table with duplicate pairs.
- Index **both directions**. The composite PK on `(book_id, category_id)` serves "categories of this book" but *not* "books in this category" (a B-tree can only seek on a leading column prefix — Lesson 54). Add a second index on `(category_id, book_id)`.
- Junction tables frequently carry their own attributes, at which point they are full entities: `order_items` carries `quantity` and `unit_price_pence`, and is BookNook's largest table.

A subtle and important modelling point: `order_items.unit_price_pence` **duplicates** `books.price_pence` at the time of sale — deliberately. This is not denormalisation for speed; it is capturing a *different fact*. `books.price_pence` is the price now; `order_items.unit_price_pence` is the price the customer agreed to. Joining to `books` for a historical invoice total would silently rewrite history every time a price changed. Distinguishing "current value" from "value at the time of the event" is one of the most commonly missed requirements in schema design.

### 4.6 Joins — semantics and physical execution

A join combines rows from two relations where a predicate holds. The join **type** determines which non-matching rows survive.

Set-up for the examples: `books` has 1.2 M rows; every book has exactly one `author_id`; `reviews` exists only for books that have been reviewed (about 35 % of books); some users have never ordered.

**`INNER JOIN`** — only rows with a match on both sides.

```sql
-- Books with their author's name. Books with no matching author DISAPPEAR
-- (impossible here, because author_id is NOT NULL with an FK -- which is
-- exactly the kind of guarantee that lets you reason about join results).
SELECT b.title, a.full_name
FROM books b
INNER JOIN authors a ON a.author_id = b.author_id;
-- Row count: exactly 1.2 M (1:1 match on a NOT NULL FK).
```

**`LEFT OUTER JOIN`** — all left rows; right-side columns become `NULL` where there is no match.

```sql
-- Every book, with its average rating; unreviewed books still appear,
-- with avg_rating = NULL. This is the join you want ~80% of the time.
SELECT b.title, AVG(r.rating) AS avg_rating
FROM books b
LEFT JOIN reviews r ON r.book_id = b.book_id
GROUP BY b.book_id, b.title;
-- Row count BEFORE grouping: 1.2 M unmatched-or-matched books EXPANDED by
-- the number of reviews each -- a book with 50 reviews produces 50 rows.
-- This row multiplication is why SUM() over a multi-join is so often wrong.
```

The classic `LEFT JOIN` trap:

```sql
-- WRONG: putting a right-table filter in WHERE turns a LEFT JOIN into an
-- INNER JOIN, because NULL >= 4 is NULL, which is not true.
SELECT b.title FROM books b
LEFT JOIN reviews r ON r.book_id = b.book_id
WHERE r.rating >= 4;

-- RIGHT: filter in the ON clause; the filter is applied BEFORE the outer
-- join preserves unmatched left rows.
SELECT b.title FROM books b
LEFT JOIN reviews r ON r.book_id = b.book_id AND r.rating >= 4;
```

**`RIGHT OUTER JOIN`** — the mirror image: all right rows preserved.

```sql
-- Every user, including those who never ordered. Identical in meaning to a
-- LEFT JOIN with the tables swapped. Most teams standardise on LEFT only,
-- because mixing directions in a 5-table query is genuinely hard to read.
SELECT o.order_id, u.email
FROM orders o
RIGHT JOIN users u ON u.user_id = o.user_id;
```

**`FULL OUTER JOIN`** — all rows from both sides; `NULL`s on whichever side lacks a match.

```sql
-- Reconciliation: find inventory rows with no book AND books with no
-- inventory row, in one pass. Primarily a data-quality tool.
SELECT b.book_id, i.book_id, b.title, i.quantity
FROM books b
FULL OUTER JOIN inventory i ON i.book_id = b.book_id
WHERE b.book_id IS NULL OR i.book_id IS NULL;
```

**`CROSS JOIN`** — the Cartesian product: every left row paired with every right row. `N × M` rows.

```sql
-- Deliberate and useful: generate a complete grid of (book, month) pairs so
-- that months with zero sales still appear as a row in a report.
SELECT b.book_id, m.month
FROM books b
CROSS JOIN generate_series(DATE '2025-01-01', DATE '2025-12-01', INTERVAL '1 month') AS m(month)
WHERE b.book_id IN (4711, 4712);
-- 2 books x 12 months = 24 rows.
```

An *accidental* cross join — omitting the `ON` clause, or joining on a non-selective predicate — is the most dramatic failure mode in SQL: 1.2 M × 900 K is **1.08 trillion** rows. The query does not error; it runs until the disk fills.

**`SELF JOIN`** — a table joined to itself, which requires aliases to disambiguate.

```sql
-- "Customers who also bought" -- pairs of books appearing in the same order.
-- The b1 < b2 predicate does two jobs: removes self-pairs (a book with
-- itself) and removes mirrored duplicates ((A,B) and (B,A)).
SELECT oi1.book_id AS book_a, oi2.book_id AS book_b, COUNT(*) AS together
FROM order_items oi1
JOIN order_items oi2
  ON oi1.order_id = oi2.order_id
 AND oi1.book_id  < oi2.book_id
GROUP BY oi1.book_id, oi2.book_id
HAVING COUNT(*) >= 25
ORDER BY together DESC;
```

Also worth naming: **`SEMI JOIN`** (`WHERE EXISTS (...)` / `IN`) returns left rows that have *at least one* match, **without** multiplying rows — which is the correct tool when you only want existence, not data, from the right side. **`ANTI JOIN`** (`WHERE NOT EXISTS (...)`) returns left rows with *no* match. Preferring `NOT EXISTS` over `NOT IN` matters: `NOT IN` returns **zero rows** if the subquery yields a single `NULL`, because `x NOT IN (1, NULL)` evaluates to `NULL`, never true. This is a silent wrong-answer bug.

#### How joins actually execute

The *type* is logical; the *algorithm* is the planner's choice, made fresh each time from statistics. There are three, and knowing them explains most plan surprises.

| Algorithm | How it works | Cost (approx) | Chosen when | Pathological when |
|---|---|---|---|---|
| **Nested loop** | For each row of the outer input, look up matches in the inner input — efficiently only if the inner side has a usable index | `O(N_outer × log N_inner)` with an index; `O(N × M)` without | The outer side is **small** (tens to a few thousand rows) and the inner has an index on the join column | The outer-row estimate is wrong. 3,000 estimated vs 900,000 actual turns a 20 ms query into 4 s — this is the single most common plan regression, and it is Lesson 40 §7's incident |
| **Hash join** | Build an in-memory hash table from the **smaller** input, then stream the larger input and probe it | `O(N + M)`, needs memory for the build side | Both inputs are large, the join is an **equality**, and no useful index exists | The build side exceeds `work_mem` and **spills to disk** — multiple passes, often 10–100× slower, with no error |
| **Merge join** | Sort both inputs on the join key (or read them pre-sorted from indexes), then walk both in lockstep | `O(N log N + M log M)`, or `O(N + M)` if both are already ordered | Both sides are huge and already sorted — e.g. both scanned via indexes on the join key | A sort is required *and* spills; also unusable for non-equality joins |

Only nested loop can handle non-equality join predicates (`ON a.ts BETWEEN b.start AND b.end`), which is why range joins are often slow.

Two practical consequences:
- **A missing index does not just slow a join; it changes the algorithm.** Adding an index on the inner side's join column can flip a 900 ms hash join into a 4 ms nested loop.
- **`work_mem` is per-operator, per-connection, not per-query.** A query with three hash joins and a sort can use 4 × `work_mem`; 40 such connections can use 160 ×. Setting `work_mem = 1GB` to stop a spill is how servers run out of memory.

### 4.7 Constraints, and why they belong in the database

| Constraint | Enforces | BookNook example |
|---|---|---|
| `NOT NULL` | A value is always present | `orders.user_id NOT NULL` — an order always has a buyer |
| `UNIQUE` | No two rows share a value. **Note:** multiple `NULL`s are allowed (they are "unknown", so not equal) unless declared `UNIQUE NULLS NOT DISTINCT` | `books.isbn13 UNIQUE`; `reviews (user_id, book_id) UNIQUE` — one review per user per book |
| `PRIMARY KEY` | `UNIQUE` + `NOT NULL`, plus it is the row's identity | `books(book_id)` |
| `FOREIGN KEY` | The referenced row exists | `order_items.order_id → orders` |
| `CHECK` | An arbitrary boolean over **one row** | `CHECK (price_pence >= 0)`, `CHECK (rating BETWEEN 1 AND 5)` |
| `DEFAULT` | A value when none is supplied | `status TEXT NOT NULL DEFAULT 'PENDING'`, `created_at TIMESTAMPTZ NOT NULL DEFAULT now()` |
| `EXCLUDE` (PostgreSQL) | A generalised `UNIQUE`: no two rows may satisfy a given operator pairwise — including **overlap** (`&&`) on ranges | No two warehouse reservations may overlap in time for the same bay |
| `GENERATED` column | A value derived deterministically from others | `total_pence GENERATED ALWAYS AS (quantity * unit_price_pence) STORED` |

`EXCLUDE` deserves a moment, because it has no equivalent in most engines and it solves a problem people usually solve badly:

```sql
-- No two reservations for the same loading bay may overlap in time.
-- Enforced by the engine, atomically, under any concurrency.
CREATE EXTENSION IF NOT EXISTS btree_gist;
ALTER TABLE bay_reservations
  ADD CONSTRAINT no_overlapping_bays
  EXCLUDE USING GIST (bay_id WITH =, during WITH &&);
```

Without it, the usual application-level implementation is "query for conflicts, then insert" — which has a race window between the two statements that **no amount of careful coding closes** at Read Committed isolation (Lesson 50).

**Why the database, not the application?** Five concrete reasons:

1. **It applies to every writer.** Your API, the batch importer, the admin console, the data-fix script, and the engineer in psql at 2 a.m. Application validation covers only the paths that call it.
2. **It is atomic under concurrency.** A `UNIQUE` constraint is enforced by the index at write time. An application "check then insert" is two statements with a gap; at 500 requests/second that gap *will* be hit (this is exactly the idempotency problem of Lesson 17, solved structurally).
3. **It survives bugs and refactors.** A `CHECK (quantity >= 0)` means overselling is impossible even if the inventory service has an off-by-one error. The invariant outlives the code that was supposed to maintain it.
4. **It is self-documenting and machine-readable.** The schema states the rules; tooling can read them; the planner can even *use* them (a `CHECK` can let the planner prove a partition is irrelevant and skip it).
5. **It is cheap relative to the alternative.** A `CHECK` is a predicate evaluation. The alternative is a data-cleanup project plus the reconciliation of whatever downstream reports consumed the bad rows.

The correct posture is **defence in depth**: validate in the application for good error messages and fast feedback, *and* constrain in the database for truth. They are not alternatives. The application's job is UX; the database's job is being right.

### 4.8 The N+1 query problem

The most common performance defect in application code, and it is invisible in any single query's timing.

Rendering BookNook's catalogue page for 50 books, with the author's name on each:

```python
books = db.query("SELECT book_id, title, author_id FROM books LIMIT 50")   # 1 query
for b in books:
    b.author = db.query(                                                    # 50 queries
        "SELECT full_name FROM authors WHERE author_id = %s", b.author_id)
```

**1 + 50 = 51 round trips.** Each individual query is fast — perhaps 0.3 ms of server time. The damage is not server time; it is the **network round trip**, which is paid 51 times. §9 of this lesson computes exactly what that costs.

ORMs produce this by default through **lazy loading**: `book.author` looks like an attribute access and is actually a database query. The code reads as if it has no I/O in it. It is also unbounded — the query count scales with result-set size, so it passes every test on 5 rows of fixture data and melts in production at 200.

Four fixes, in order of preference:

1. **Join and fetch in one query.** `SELECT b.title, a.full_name FROM books b JOIN authors a USING (author_id) LIMIT 50` → **1 round trip**. Best when the relationship is to-one.
2. **Batch the second query (the "IN-clause" or dataloader pattern).** Collect the 50 author ids, issue one `WHERE author_id = ANY($1)` → **2 round trips**. Best for to-many relationships, where a join would multiply rows (50 books × 30 reviews = 1,500 rows fetched to render 50 cards). ORMs call this *eager loading* / `selectinload` / `preload`.
3. **Cache the lookup side.** Authors change rarely; a cache (Lessons 27–31) removes most of the 50 calls. Mitigation, not a cure — and a cold cache restores the original problem at the worst moment.
4. **Denormalise.** Store `author_name` on `books`, accepting the update cost when an author is renamed (Lesson 56). The right answer only when 1 and 2 are genuinely insufficient.

**How to catch it**: count queries per request and alert on the count, not just on latency. A middleware that logs `request_id, query_count, total_db_ms` and a CI test asserting "this endpoint issues ≤ 4 queries" prevents the regression permanently. Query *count* per request is one of the highest-value, least-used metrics in web engineering.

## 5. Request / data flow

A BookNook customer opens the product page for one book. Follow the data from request to rendered page, showing where each mechanism in §4 does work.

1. `GET /books/9780141036144` arrives at an app server via the CDN (miss, because the page is personalised) and the load balancer (Lesson 23).
2. The handler borrows a pooled connection (Lesson 40 §4.4) and begins a **read-only** transaction, taking an MVCC snapshot so every statement below sees one consistent instant.
3. **Query 1 — the book, its author, and its stock, in one statement.** The planner resolves `isbn13` through its `UNIQUE` index: one index seek, one heap fetch. It then joins to `authors` via the `books.author_id` foreign-key index — the outer side is a **single row**, so it chooses a **nested loop**, which is optimal here. It joins to `inventory` on `book_id`, also one row, also a nested loop.
4. The `FOREIGN KEY` on `books.author_id` is `NOT NULL`, so the planner and the engineer both know the join cannot drop the row. No `LEFT JOIN` is needed — the constraint has removed a class of defensive code.
5. **Query 2 — review summary.** `SELECT COUNT(*), AVG(rating) FROM reviews WHERE book_id = $1`. The index on `reviews(book_id, created_at DESC)` is used; because only indexed columns and `rating` are needed, adding `INCLUDE (rating)` would make it an **index-only scan** (Lesson 54) and skip the heap entirely.
6. **Query 3 — the first page of reviews, with reviewer names.** `LEFT JOIN users` — `LEFT`, not `INNER`, because `reviews.user_id` is nullable by design (`ON DELETE SET NULL`, §4.4), and an `INNER JOIN` would silently hide anonymised reviews. One wrong word here deletes content from the page.
7. **Query 4 — batched, not N+1.** The "customers also bought" strip needs five books. The handler collects the five ids from the self-join result and issues **one** `WHERE book_id = ANY($1)` rather than five separate queries (§4.8). Total so far: **4 round trips**, not 1 + 5 + 20.
8. The transaction ends. No locks were taken beyond snapshot visibility, because MVCC readers never block writers (Lesson 40 §4.2). The connection returns to the pool immediately.
9. The handler renders the page. Elapsed database time ≈ 3 ms across four queries; total server time ≈ 12 ms.
10. The customer clicks **Buy**. `POST /orders` begins a write transaction.
11. `UPDATE inventory SET quantity = quantity - 1 WHERE book_id = $1 AND quantity >= 1` — a conditional update, atomic in itself. If it affects 0 rows, stock was insufficient; the app rolls back. The `CHECK (quantity >= 0)` constraint stands behind it as a second line: even a buggy app cannot persist a negative quantity.
12. `INSERT INTO orders (...)`. The `FOREIGN KEY` to `users` fires a lookup on `users(user_id)`; if the id does not exist, the insert **fails** rather than creating an orphan order. `DEFAULT now()` fills `placed_at`; `DEFAULT 'PENDING'` fills `status`; `CHECK (status IN (...))` validates it.
13. `INSERT INTO order_items (...)` once per line. Its composite primary key `(order_id, book_id)` makes a duplicate line physically impossible, so a double-submitted form cannot create two identical lines — a structural companion to the idempotency keys of Lesson 17.
14. `COMMIT` → WAL fsync (Lesson 40 §5, step 11). All foreign-key checks and all constraint evaluations happened *before* this point; if any had failed, the whole transaction would have rolled back, leaving inventory untouched.
15. A later nightly job runs `DELETE FROM carts WHERE abandoned_at < now() - INTERVAL '30 days'`. Because `cart_items.cart_id` is `ON DELETE CASCADE`, the children are removed in the same transaction. Because `orders.user_id` is `ON DELETE RESTRICT`, no analogous job can ever remove a customer who has order history.

The point of walking it this way: **in steps 4, 11, 12, 13 and 15, the schema did work that would otherwise have been application code** — and application code that, at this concurrency, would occasionally have been wrong.

## 6. ASCII diagram

```
            BOOKNOOK SCHEMA — CARDINALITIES AND FK ACTIONS
  ┌──────────┐ 1        N ┌──────────┐ 1        1 ┌───────────┐
  │ authors  │────────────│  books   │────────────│ inventory │
  │ PK author│  RESTRICT  │ PK book  │  CASCADE   │ PK/FK book│
  └──────────┘            │ UQ isbn13│            │ CHECK q>=0│
                          └────┬─────┘            └───────────┘
                     1         │        N                1:1 = PK is also FK
       ┌──────────────┬────────┴──────────┐
       │ N            │ N                 │
  ┌────▼─────┐   ┌────▼──────┐      ┌─────▼────────┐
  │ reviews  │   │order_items│      │book_categories│  ← junction (M:N)
  │UQ(user,  │   │PK(order,  │      │PK(book, cat)  │
  │   book)  │   │   book)   │      └─────┬─────────┘
  └────┬─────┘   └────┬──────┘            │ N
       │ N            │ N            ┌────▼──────┐
       │ SET NULL     │ CASCADE      │categories │
  ┌────▼─────┐   ┌────▼──────┐       └───────────┘
  │  users   │◀──│  orders   │   RESTRICT: deleting a user with
  │ PK user  │ 1 │ PK order  │ N  orders is REFUSED, forever.
  └──────────┘   └───────────┘

      JOIN TYPES — WHICH ROWS SURVIVE   (L = books, R = reviews)
   INNER        LEFT          RIGHT        FULL          CROSS
   ┌───┐        ┌───┐         ┌───┐        ┌───┐         every L
   │L▓R│        │███│R        L│▓██│       │███│         paired with
   └───┘        └───┘         └───┘        └───┘         every R
   match        all L         all R        all both      N × M  ⚠
   only         +nulls        +nulls       +nulls        1.2M×900K
                                                         = 1.08 TRILLION

      JOIN ALGORITHMS — THE PLANNER'S CHOICE, NOT YOURS
  NESTED LOOP            HASH JOIN                MERGE JOIN
  for r in OUTER:        build hashtable          both inputs SORTED
    seek INNER index       from SMALLER side      walk in lockstep
                         then probe with larger
  small outer +          both big, equality,      both big and already
  indexed inner          no useful index          ordered by join key
  ✗ dies if outer row    ✗ dies if build side     ✗ dies if a sort is
    estimate is wrong      exceeds work_mem         needed AND spills

      N+1 — WHY 50 FAST QUERIES ARE SLOWER THAN 1 SLOW ONE
  BAD:  [books]──▶ then per book: ──▶──▶──▶ ... 50 round trips
        1 + 50 = 51 × (0.5 ms RTT + 0.3 ms work) = 40.8 ms
  GOOD: [books JOIN authors] ──▶ 1 round trip
        1 × (0.5 ms RTT + 1.2 ms work)          =  1.7 ms   (24× faster)
```

## 7. Detailed example

### BookNook — the schema, and the four decisions inside it

BookNook's seven tables are given in full DDL in §8. Four decisions in that schema are worth reading as *reasoning*, not as syntax.

**Decision 1 — `order_items` has a composite primary key `(order_id, book_id)`, no surrogate id.** The pair *is* the identity: one line per book per order. This simultaneously (a) prevents duplicate lines, (b) clusters a single order's items together in the index so fetching an order's lines is one index range scan, and (c) saves 8 bytes per row across 15 M rows. The cost: if the business later allows the same book on two lines of one order (different gift wrapping, say), the key must change — a real migration. That is the trade-off, stated explicitly.

**Decision 2 — `reviews` has `UNIQUE (user_id, book_id)`.** The product rule is "one review per customer per book". Implemented in application code, this is `SELECT` then `INSERT`, and at 300 requests/second two concurrent submissions from a double-clicked button both see "no existing review" and both insert. With the `UNIQUE` constraint, the second insert fails with a constraint violation that the app converts into "you've already reviewed this book". **The race is not handled; it is made impossible.**

**Decision 3 — `inventory` is a separate 1:1 table rather than a `quantity` column on `books`.** `books` is read on every catalogue and product page and almost never written. `inventory.quantity` is written on every single order. Keeping them apart means (a) hot `UPDATE`s do not create new row versions of the wide `books` row and churn its indexes (Lesson 40 §4.2 — in PostgreSQL an `UPDATE` rewrites the whole row), (b) row-level locks during checkout contend only on a narrow `inventory` row, and (c) `books` pages stay dense, which protects the buffer-pool hit ratio. This is a physical-design decision expressed through logical modelling.

**Decision 4 — `orders.user_id` is `ON DELETE RESTRICT` and there is an `anonymise_user()` path instead.** The naive GDPR implementation is `DELETE FROM users WHERE user_id = $1`, and with `ON DELETE CASCADE` on orders it would erase order and line-item history — breaking financial reporting, tax obligations and the fraud model, irreversibly, from one API call. `RESTRICT` turns that catastrophe into an error message, and the correct implementation overwrites personal fields while keeping the financial record.

**The incident that justified all four.** Before the `UNIQUE (user_id, book_id)` constraint existed, BookNook shipped a "rate this book" prompt that retried on timeout. Over six weeks it accumulated **41,000 duplicate reviews**, which skewed `AVG(rating)` on the catalogue page, which fed the recommendation ranking, which promoted the wrong books on the homepage for a month. Nobody noticed, because every individual component was working as written. One line of DDL would have prevented all of it — and did, once added, in under a second, with no application change.

### Contrasting example — an airline seat-assignment system

Take a system with the opposite shape. An airline assigns seats: `flights`, `seats`, `bookings`, `seat_assignments`. Data volume is small (a few hundred million rows); query shapes are simple; **the hard part is that two passengers must never hold seat 14A on flight BA117 of 3 March**.

The relational mechanisms do almost all the work:

- `seat_assignments` has `UNIQUE (flight_id, flight_date, seat_number)` — the double-booking invariant, enforced at the index level, under any concurrency, for any client.
- `seat_assignments.booking_id → bookings` is `ON DELETE CASCADE`: cancel the booking, release the seats automatically, with no cleanup job that can be skipped or fail.
- An `EXCLUDE` constraint on the aircraft-maintenance table prevents two maintenance windows overlapping for the same airframe — expressible as a constraint, not as a query.
- A `CHECK (seat_number ~ '^[0-9]{1,2}[A-K]$')` rejects malformed seats at the boundary.

Contrast with BookNook explicitly:

| | BookNook | Airline seating |
|---|---|---|
| Dominant pressure | **Read shapes and latency** — catalogue, search, reviews | **Write correctness under contention** — one seat, many claimants |
| Join complexity | High: 4–6 tables per page | Low: 2–3 tables |
| What a constraint buys | Data quality over time (the 41,000 duplicates) | Prevention of an immediate, customer-visible, legally awkward double-booking |
| Acceptable staleness | Seconds (a stale stock count is survivable) | **Zero** on seat assignment |
| Scaling approach | Replicas + cache for reads (Lessons 27–34) | Partition by flight; contention is naturally per-flight |

Same five mechanisms, two completely different reasons for using them. That is the argument for learning the mechanisms rather than memorising a schema.

## 8. Code or configuration example

> **Illustrative only — not production-ready.** It omits partitioning, auditing, row-level security, migration ordering, `lock_timeout` guards, index-creation concurrency (`CREATE INDEX CONCURRENTLY` in production to avoid blocking writes), retention policies and realistic index tuning. Types and sizes are chosen to teach, not to benchmark. Written for PostgreSQL 16.

```sql
-- =====================================================================
--  BOOKNOOK SCHEMA
--  Convention: surrogate BIGINT PKs; natural keys kept as UNIQUE;
--  money as INTEGER pence (never FLOAT); all timestamps TIMESTAMPTZ.
-- =====================================================================

CREATE TABLE authors (
    author_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    full_name   TEXT        NOT NULL,
    country     CHAR(2)     NOT NULL,          -- ISO 3166-1 alpha-2
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT authors_country_upper CHECK (country = UPPER(country))
);

CREATE TABLE users (
    user_id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- Public-facing opaque id: UUID **v7**, time-ordered, so it does NOT
    -- scatter B-tree inserts the way v4 would (section 4.3). The internal
    -- BIGINT stays the join key because it is 8 bytes, not 16.
    public_id     UUID        NOT NULL UNIQUE DEFAULT uuidv7(),
    email         CITEXT      NOT NULL UNIQUE,  -- case-insensitive: one account per human
    display_name  TEXT        NOT NULL,
    status        TEXT        NOT NULL DEFAULT 'ACTIVE',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT users_status_valid CHECK (status IN ('ACTIVE','SUSPENDED','ANONYMISED'))
);

CREATE TABLE books (
    book_id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    isbn13        CHAR(13) NOT NULL UNIQUE,     -- natural key as ALTERNATE key
    title         TEXT     NOT NULL,
    -- NOT NULL + FK: every book provably has an author, so queries can use
    -- INNER JOIN and never worry about dropping rows.
    author_id     BIGINT   NOT NULL REFERENCES authors(author_id) ON DELETE RESTRICT,
    price_pence   INTEGER  NOT NULL,
    published_on  DATE     NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT books_price_nonneg  CHECK (price_pence >= 0),
    CONSTRAINT books_isbn13_digits CHECK (isbn13 ~ '^[0-9]{13}$')
);

-- 1:1 with books. PK **is** the FK: "at most one inventory row per book"
-- is structurally impossible to violate. Split out because quantity is
-- written constantly while books is read constantly (section 7, decision 3).
CREATE TABLE inventory (
    book_id     BIGINT PRIMARY KEY REFERENCES books(book_id) ON DELETE CASCADE,
    quantity    INTEGER     NOT NULL DEFAULT 0,
    reserved    INTEGER     NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT inventory_qty_nonneg      CHECK (quantity >= 0),      -- overselling: impossible
    CONSTRAINT inventory_reserved_sane   CHECK (reserved >= 0 AND reserved <= quantity)
);

CREATE TABLE orders (
    order_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- RESTRICT, never CASCADE: deleting a customer must NOT delete financial
    -- history. GDPR erasure is handled by anonymisation, not deletion.
    user_id     BIGINT      NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    status      TEXT        NOT NULL DEFAULT 'PENDING',
    placed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Idempotency key from Lesson 17, enforced structurally: a retried
    -- POST /orders cannot create a second order.
    idempotency_key UUID UNIQUE,
    CONSTRAINT orders_status_valid
        CHECK (status IN ('PENDING','PAID','SHIPPED','DELIVERED','CANCELLED'))
);

CREATE TABLE order_items (
    order_id          BIGINT NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
    -- RESTRICT on books: a book that has been sold cannot be hard-deleted,
    -- because its line items are financial records.
    book_id           BIGINT NOT NULL REFERENCES books(book_id)  ON DELETE RESTRICT,
    quantity          INTEGER NOT NULL,
    -- NOT a duplicate of books.price_pence: it is a DIFFERENT FACT -- the
    -- price agreed at the time of sale. Joining to books for an old invoice
    -- would silently rewrite history (section 4.5).
    unit_price_pence  INTEGER NOT NULL,
    line_total_pence  INTEGER GENERATED ALWAYS AS (quantity * unit_price_pence) STORED,
    -- Composite PK = identity AND duplicate-line prevention AND clustering
    -- of one order's lines together in the index.
    PRIMARY KEY (order_id, book_id),
    CONSTRAINT order_items_qty_positive CHECK (quantity > 0),
    CONSTRAINT order_items_price_nonneg CHECK (unit_price_pence >= 0)
);

CREATE TABLE reviews (
    review_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    book_id     BIGINT NOT NULL REFERENCES books(book_id) ON DELETE CASCADE,
    -- SET NULL, so an anonymised user's review survives as anonymous content.
    -- This is WHY the column is nullable -- and why queries must LEFT JOIN
    -- users, not INNER JOIN (section 5, step 6).
    user_id     BIGINT     REFERENCES users(user_id) ON DELETE SET NULL,
    rating      SMALLINT    NOT NULL,
    body        TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT reviews_rating_range CHECK (rating BETWEEN 1 AND 5),
    -- The one line that would have prevented 41,000 duplicate reviews.
    CONSTRAINT reviews_one_per_user_book UNIQUE (user_id, book_id)
);

-- Many-to-many via a junction table.
CREATE TABLE categories (
    category_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    slug        TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL
);

CREATE TABLE book_categories (
    book_id     BIGINT NOT NULL REFERENCES books(book_id)          ON DELETE CASCADE,
    category_id BIGINT NOT NULL REFERENCES categories(category_id) ON DELETE RESTRICT,
    PRIMARY KEY (book_id, category_id)      -- duplicate pairings impossible
);

-- =====================================================================
--  INDEXES.  PostgreSQL does NOT index foreign-key columns automatically.
--  Without these, every join and every parent DELETE scans the child table.
-- =====================================================================
CREATE INDEX books_author_idx        ON books (author_id);
CREATE INDEX orders_user_placed_idx  ON orders (user_id, placed_at DESC);
CREATE INDEX order_items_book_idx    ON order_items (book_id);  -- reverse of the PK
CREATE INDEX reviews_book_recent_idx ON reviews (book_id, created_at DESC) INCLUDE (rating);
-- Reverse direction of the junction PK: "books in this category".
-- A B-tree can only seek on a LEADING column prefix (Lesson 54).
CREATE INDEX book_categories_cat_idx ON book_categories (category_id, book_id);

-- Partial index: only PENDING orders matter to the fulfilment dashboard,
-- and they are ~0.2% of rows. The index is therefore ~500x smaller.
CREATE INDEX orders_pending_idx ON orders (placed_at) WHERE status = 'PENDING';

-- =====================================================================
--  EXCLUDE: a generalised UNIQUE. No two promotional campaigns may overlap
--  in time for the same book. There is no way to do this correctly in
--  application code at Read Committed isolation (section 4.7).
-- =====================================================================
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE TABLE promotions (
    promotion_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    book_id      BIGINT NOT NULL REFERENCES books(book_id) ON DELETE CASCADE,
    discount_pct NUMERIC(4,1) NOT NULL CHECK (discount_pct > 0 AND discount_pct <= 90),
    during       TSTZRANGE NOT NULL,
    EXCLUDE USING GIST (book_id WITH =, during WITH &&)   -- && = "overlaps"
);
```

```python
# ============ Fixing N+1 the two correct ways (illustrative) ============

# --- BAD: 1 + N round trips. Looks like plain attribute access. ---
def render_catalogue_bad(db, limit=50):
    books = db.fetchall("SELECT book_id, title, author_id FROM books LIMIT %s", (limit,))
    for b in books:
        # One network round trip per book. Fast query, catastrophic pattern.
        b["author"] = db.fetchone(
            "SELECT full_name FROM authors WHERE author_id = %s", (b["author_id"],))
    return books

# --- GOOD (to-one): 1 round trip, resolved by the planner as a nested loop
#     over 50 outer rows against the authors PK index. ---
def render_catalogue_join(db, limit=50):
    return db.fetchall(
        "SELECT b.book_id, b.title, a.full_name AS author "
        "FROM books b JOIN authors a USING (author_id) "   # INNER is safe: FK is NOT NULL
        "ORDER BY b.published_on DESC LIMIT %s", (limit,))

# --- GOOD (to-many): 2 round trips. A JOIN here would multiply rows
#     (50 books x 30 reviews = 1500 rows to render 50 cards), so batch instead. ---
def render_catalogue_batched(db, limit=50):
    books = db.fetchall("SELECT book_id, title FROM books LIMIT %s", (limit,))
    ids = [b["book_id"] for b in books]
    # ANY(%s) sends ONE query with an array parameter. Keep the array bounded --
    # a 50,000-element array is its own performance problem.
    rows = db.fetchall(
        "SELECT book_id, COUNT(*) AS n, AVG(rating)::numeric(3,2) AS avg_rating "
        "FROM reviews WHERE book_id = ANY(%s) GROUP BY book_id", (ids,))
    by_book = {r["book_id"]: r for r in rows}
    for b in books:
        b["reviews"] = by_book.get(b["book_id"], {"n": 0, "avg_rating": None})
    return books
```

## 9. Numerical example

**Question:** BookNook's catalogue page shows 50 books; for each it needs the author name and the review summary. Quantify the N+1 cost, the fixed cost, and the point at which the fix stops mattering.

**Stated assumptions** (every one changes the answer; all are labelled *typical*):
- Network round-trip time between app server and database, same availability zone: **0.5 ms**.
- Server-side execution of a single-row primary-key lookup: **0.3 ms** (includes parse, plan, execute; the plan is cached for a prepared statement).
- Server-side execution of a 50-row join across `books`, `authors` and an aggregate over `reviews`: **1.4 ms**.
- Server-side execution of one batched `WHERE book_id = ANY(array of 50)` aggregate: **0.9 ms**.
- Driver/ORM overhead per statement (object hydration, parameter binding): **0.1 ms**.
- Connection pool has an idle connection available: **0 ms** acquisition. (If it does not, add the pool wait time, which is where real incidents live.)
- Page is rendered server-side; database time is on the critical path.
- Traffic: **40 catalogue requests/second** at peak.

**Step 1 — cost of the naive N+1 implementation.**

Queries issued: 1 for the book list, 50 for authors, 50 for review summaries = **101 statements**.

```
per statement  = RTT + server + driver
               = 0.5 + 0.3 + 0.1  = 0.9 ms
total          = 101 × 0.9 ms     = 90.9 ms
```

Of that, pure network round-trip time is `101 × 0.5 = 50.5 ms`, i.e. **56 % of the time is the network, not the database**.

**Step 2 — cost of the join + batch implementation.**

Queries issued: 1 join (books + authors) + 1 batched review aggregate = **2 statements**.

```
query 1 = 0.5 RTT + 1.4 server + 0.1 driver = 2.0 ms
query 2 = 0.5 RTT + 0.9 server + 0.1 driver = 1.5 ms
total                                       = 3.5 ms
```

**Improvement: 90.9 ms → 3.5 ms = 26× faster**, with no new hardware, no cache and no index.

**Step 3 — what it costs the database server, not just the page.**

Server CPU consumed per request:

```
N+1   : 101 statements × 0.3 ms = 30.3 ms of server time
Fixed :   2 statements, 1.4 + 0.9 = 2.3 ms of server time
```

At 40 requests/second:

```
N+1   : 40 × 30.3 ms = 1,212 ms of CPU per second = 1.21 cores
Fixed : 40 ×  2.3 ms =    92 ms of CPU per second = 0.09 cores
```

On the 16-vCPU instance from Lesson 40 §4.6, that is **7.6 % versus 0.6 %** — a **13× reduction in database load** from the same change. The N+1 version was also issuing `40 × 101 = 4,040 statements/second`, which on its own is a meaningful fraction of the instance's statement-handling capacity.

**Step 4 — the cross-availability-zone version, which is where this turns fatal.**

Suppose a misconfiguration routes the app to a database in another AZ, raising RTT from 0.5 ms to **2.0 ms** (a typical intra-region cross-AZ figure).

```
N+1   : 101 × (2.0 + 0.3 + 0.1) = 101 × 2.4 =  242.4 ms   (was 90.9 ms)
Fixed :   2 × (2.0 + ~1.15 avg) ≈              6.3 ms     (was  3.5 ms)
```

The N+1 page degrades by **+151 ms (2.7×)**; the fixed page degrades by **+2.8 ms (1.8×)**. **Round-trip count is the multiplier on every network regression you will ever have.** This is the same principle as HTTP request batching in Lesson 9, applied one tier down.

**Step 5 — the break-even point: when does N+1 stop mattering?**

Let *n* be the number of books on the page. Assume the fixed version's server time grows mildly with *n*: `1.4 + 0.012n` ms for the join and `0.9 + 0.008n` for the batch (approximate linear scaling).

```
N+1 total(n)   = (1 + 2n) × 0.9 ms
Fixed total(n) = 1.2 + 2.3 + 0.020n  = 3.5 + 0.020n ms   (at n = 50: 4.5 ms — close to step 2)
```

Setting them equal:

```
0.9 + 1.8n = 3.5 + 0.020n
1.78n      = 2.6
n          = 1.46
```

**N+1 is cheaper only for n ≤ 1.** At `n = 2` the batched version is already ahead, and the gap grows **linearly and without bound**. There is no page size at which lazy loading is the right default — which is precisely why the pattern must be caught by a query-count assertion in CI (§4.8) rather than by judgement at review time.

## 10. Trade-offs

| You gain | You pay |
|---|---|
| **Foreign keys** — orphan rows become impossible for every writer | A parent-index read per child write (typically a few percent of write throughput); a `FOR KEY SHARE` lock on a hot parent row can contend |
| **Surrogate keys** — stable, narrow, fast joins | An extra column and index; the natural key still needs its own `UNIQUE`; sequential ids leak row counts if exposed publicly |
| **Normalisation** — each fact stored once, no update anomalies | Reads must join; a page may touch 5–6 tables, and join count is the main driver of read latency (Lesson 56 covers buying it back) |
| **`CHECK` / `UNIQUE` / `EXCLUDE`** — invariants that survive bugs and refactors | Constraint violations surface as database errors the app must translate into good UX; adding a constraint to a large existing table requires a validating scan (use `NOT VALID` then `VALIDATE CONSTRAINT` to avoid a long lock) |
| **`ON DELETE CASCADE`** — no cleanup jobs to forget | A data-destruction primitive one careless `DELETE` away from erasing a subtree; cascades are also invisible in the statement you wrote |
| **Composite keys** — identity and uniqueness in one structure | Every child FK must carry all columns; changing the key later is a real migration |
| **UUID v7 / decentralised ids** — generate without a round trip, merge across systems | 16 bytes versus 8 in every index; still slightly worse locality than a sequence |
| **Joins at read time** — flexible, always current | Join order and algorithm are the planner's choice, so a statistics change can alter latency by 100× with no deploy |
| **Database-enforced invariants** | Business rules now live in two places (schema and code); schema changes need migrations and a deployment discipline |

The recurring shape: **every mechanism in this lesson trades a small, predictable, continuous cost for the elimination of a rare, expensive, discontinuous failure.** A few percent of write throughput buys the impossibility of orphan rows. That trade is almost always correct, and the engineers who get it wrong are usually optimising the measurable cost while ignoring the unmeasured one.

## 11. Failure scenarios

| Failure | What the user experiences | How it is detected | How it recovers |
|---|---|---|---|
| **Foreign-key violation on insert** | "Something went wrong" (if unhandled) or a clear validation message (if handled) | `SQLSTATE 23503` in logs; error-rate alert on the endpoint | Map the constraint name to a user-facing message; fix the upstream code that invented a non-existent id. The row was never written — integrity held |
| **Unique violation from a double-submitted form** | A second click shows an error instead of creating a duplicate | `SQLSTATE 23505` | Catch it and treat as success (the idempotent outcome, Lesson 17), or use `INSERT ... ON CONFLICT DO NOTHING`. **The constraint did its job** |
| **Accidental `ON DELETE CASCADE` on a parent** | Large amounts of history silently vanish; discovered days later by a report | Row-count monitoring; audit log; an angry finance team | Point-in-time restore from backup + WAL (Lesson 63) — expensive and lossy. **Prevention is the only real fix:** `RESTRICT` by default, `CASCADE` only for owned children |
| **Missing index on a FK column** | Parent deletes and joins crawl; one `DELETE FROM authors` locks for minutes | `EXPLAIN` shows a sequential scan on the child; lock-wait alerts | `CREATE INDEX CONCURRENTLY` on the FK column. Audit the whole schema — if one is missing, more are |
| **N+1 introduced by an ORM change** | A page that was 15 ms becomes 400 ms; worse from another AZ | Query-count-per-request metric spikes; statement rate rises while CPU per statement stays flat | Switch to eager/`selectinload` or an explicit join; add a CI assertion on query count |
| **Accidental cross join** | The request never returns; database disk fills with temp files | A query running for minutes; `temp_bytes` climbing; `work_mem` spill messages | `statement_timeout` kills it; fix the missing `ON` clause. Set `statement_timeout` *before* you need it |
| **Nested-loop plan with a wrong row estimate** | One endpoint goes from 20 ms to 4 s with no deploy | `EXPLAIN ANALYZE` shows estimated 3,000 vs actual 900,000 | `ANALYZE`; raise the statistics target; add extended statistics for correlated columns; add the missing composite index (Lesson 55) |
| **`NOT IN` with a `NULL` in the subquery** | A list page silently shows **zero** results | No error at all — this is the dangerous part; caught only by tests or a user complaint | Replace with `NOT EXISTS`; add `NOT NULL` where the column should never be null |
| **`LEFT JOIN` filtered in `WHERE`** | Rows that should appear (unreviewed books, anonymised reviews) silently disappear | No error; a count that is "a bit low" | Move the right-table predicate into the `ON` clause |
| **`ALTER TABLE ADD CONSTRAINT` on a huge table** | The whole site stops for the duration — because a blocked `ALTER` queues behind readers and then **blocks every subsequent query** | Lock-wait pile-up; sudden total latency collapse | Use `ADD CONSTRAINT ... NOT VALID` then `VALIDATE CONSTRAINT` (short lock, then a non-blocking scan); always set `lock_timeout` before DDL |
| **UUID v4 primary key on a growing table** | Insert throughput degrades steadily over months; no single bad query | Index size ≫ expected; WAL volume high; buffer hit ratio falling | Migrate to UUID v7 or a `BIGINT` identity with the v4 demoted to a `UNIQUE` column. Rebuild indexes (`pg_repack`) |

## 12. Alternatives

| Approach | How relationships and invariants are handled | Strength | Cost | Choose when |
|---|---|---|---|---|
| **Normalised relational with FKs and constraints** (this lesson) | Declared in DDL, enforced by the engine | Correctness is structural; ad-hoc queries work | Reads must join; migrations needed | Default for transactional data |
| **Relational, FKs deliberately omitted** | Application code only | Marginally faster writes; easier bulk loads and shard splits | Orphans become inevitable at scale; every writer must be correct | Very high write rates, sharded clusters where cross-shard FKs are impossible anyway — and only with compensating data-quality checks |
| **Denormalised relational** (Lesson 56) | Duplicate columns, maintained by triggers or app code | Fewer joins, faster reads | Update anomalies return; duplication must be maintained consistently | A specific read path is provably too slow after indexing |
| **Embedded documents** (Lesson 44) | Related data nested inside one document; no cross-document FKs | One read fetches the whole aggregate; no join | Duplication across documents; no referential integrity; 16 MB document limits; unbounded arrays are a trap | The aggregate boundary matches the access pattern and nothing else needs the nested data |
| **Document + manual references** | Store an id, resolve it in the application | Flexible | You have reimplemented joins in application code, without the planner, and without integrity | Rarely the right answer; usually a sign the model should have been relational |
| **Wide-column, one table per query** (Lesson 45) | No joins at all; duplicate data into a table per access pattern | Linear write scaling, predictable latency | Every new query shape is a new table plus a backfill; consistency is yours to maintain | Write-dominated workloads with a fixed, known query set |
| **Graph edges as first-class** (Lesson 46) | Relationships are stored objects with their own properties | Variable-depth traversal is cheap | Everything else is harder | Depth is unbounded and traversal is the main workload |
| **Materialised views / derived tables** | Precomputed join results, refreshed periodically | Read-time join cost removed | Staleness; refresh cost; another thing to monitor | Expensive aggregates that tolerate minutes of staleness |
| **Application-layer validation only** | Code checks before writing | Rich error messages, no migration | Correct only for paths that call it; loses every race | Use **alongside** constraints, never instead of them |

The honest summary: the alternatives are not "better schemas", they are **different places to put the enforcement**. The question is always *who guarantees this invariant, and what happens the day that guarantor is bypassed?* The database is the only guarantor that cannot be bypassed.

## 13. When to use it

1. **Any time a fact in one table references a fact in another.** `order_items` → `orders` → `users`. Declare the foreign key. The cost is a few percent of write throughput; the benefit is that an orphan line item — which corrupts every revenue report that touches it — cannot exist.
2. **Any time a business rule can be written as a predicate over one row.** "Rating is 1–5", "quantity is never negative", "status is one of five values". These become `CHECK` constraints in one line and are then true forever, including for the data-fix script written at midnight.
3. **Any time a uniqueness rule exists.** "One account per email", "one review per user per book", "one idempotency key per order". A `UNIQUE` constraint wins every race; a `SELECT`-then-`INSERT` loses some of them, and the ones it loses are invisible until a report looks wrong.
4. **Many-to-many relationships — always via a junction table.** Books and categories, students and courses, users and roles. Never a comma-separated string column, never an array when the pairing carries attributes or needs to be queried from both directions.
5. **When the same entity is written by more than one service or process.** The API, the importer, the admin tool, the migration. Shared invariants belong in the shared place.
6. **When history must be preserved.** `order_items.unit_price_pence` captures the agreed price, distinct from the current price. Any time you find yourself joining to get a value "as it was", you have found a missing column.
7. **When you need non-overlap or another pairwise rule.** `EXCLUDE` constraints handle time-range, room-booking and resource-allocation invariants that application code cannot enforce correctly under concurrency.

## 14. When not to use it

1. **Cross-shard references in a sharded cluster.** Once `orders` and `users` live on different physical databases (Lesson 37), a foreign key between them is not enforceable — the engine cannot see both sides. You must replace it with application-level validation, periodic reconciliation jobs, and a deliberate acceptance that temporary inconsistency exists.
2. **Bulk loading and ETL into staging tables.** Loading 500 M rows with every FK and index active can be several times slower. The standard pattern is: load into an unconstrained staging table, validate in bulk with set-based queries, then add constraints and indexes, then swap. Do *not* generalise this into "constraints are slow" for your OLTP tables.
3. **`CASCADE` on anything with independent business value.** Never cascade from `users` to `orders`, from `accounts` to `transactions`, or from `products` to `sales`. Cascade only to children that are meaningless alone — line items, cart contents, join-table rows, attachments.
4. **Rules that span multiple rows or tables.** `CHECK` constraints see one row. "A customer may have at most three open orders" and "the sum of line totals equals the order total" are **not** `CHECK` constraints. They need a transaction with appropriate isolation (Lessons 49–52), a trigger with explicit locking, or a deliberately denormalised maintained column. Attempting them with a subquery in a `CHECK` is a correctness bug, not a performance one.
5. **Extremely volatile, short-lived data.** Rate-limit counters, session state, ephemeral job progress — low value, high churn, no relationships. Key-value storage (Lesson 43) is a better fit than a constrained table.
6. **Random UUID v4 as a primary key on a large write-heavy table** (§4.3). Not a constraint problem, but belongs in this list: it is the single most common avoidable schema-level performance mistake.

## 15. Common mistakes

### Beginner mistakes

- **Omitting foreign keys "to keep it simple".** The complexity does not disappear; it relocates into a data-cleanup script you will write in eighteen months.
- **Not indexing foreign-key columns.** PostgreSQL indexes the *parent* side automatically (it is the PK) and the child side **never**. Every join and parent delete then scans.
- **Using a natural key as the primary key.** Emails change, ISBNs get reissued, "permanent" business codes get restructured. When the key changes, every child row and index entry must change with it.
- **Storing a list in a string.** `categories = "fiction,irish,2015"`. Unqueryable, unindexable, unjoinable, and `LIKE '%irish%'` also matches "irishistory". Use a junction table.
- **Filtering the right table of a `LEFT JOIN` in `WHERE`.** Silently converts it to an inner join (§4.6).
- **`SELECT COUNT(*)` after a one-to-many join and wondering why the number tripled.** The join multiplied rows. Use `COUNT(DISTINCT ...)` or an `EXISTS` semi-join.
- **Using `NOT IN` with a nullable subquery column.** Returns zero rows with no error. Use `NOT EXISTS`.
- **Omitting the `ON` clause.** Accidental cross join; 1.08 trillion rows.
- **Making every column nullable "for flexibility".** `NULL` is a third logical value that infects comparisons, aggregates and indexes. Default to `NOT NULL` and justify each exception.
- **Assuming `UNIQUE` prevents duplicate `NULL`s.** It does not; `NULL` is not equal to `NULL`. Use `UNIQUE NULLS NOT DISTINCT` (PG 15+) or a partial unique index if you need that.

### Experienced-engineer mistakes

- **Using UUID v4 as a clustered primary key.** On InnoDB this is a 2–10× insert-throughput loss and a silent index-bloat spiral. Use v7. (§4.3)
- **Storing UUIDs as `CHAR(36)`.** 2.25× the bytes in every index, every FK and every comparison, forever.
- **Shipping `ON DELETE CASCADE` on a user-facing delete path.** One `DELETE FROM users` and seven years of financial history is gone. Default to `RESTRICT`; make cascade a deliberate, reviewed decision per relationship.
- **Letting the ORM choose join strategy silently.** Lazy loading creates N+1; naive eager loading of several to-many relations creates a Cartesian explosion (`50 books × 30 reviews × 4 categories = 6,000 rows` to render 50 cards). Use separate batched queries for to-many relationships.
- **Running `ALTER TABLE ... ADD CONSTRAINT` without `NOT VALID` on a big table.** Takes a strong lock for a full validating scan, and because lock requests queue behind it, every subsequent query on that table blocks too — a total outage from a "safe" migration.
- **Setting `work_mem` globally high to stop one hash-join spill.** It is per-operator, per-connection. Set it for the one session or query that needs it.
- **Modelling "current value" and "value at the time of the event" as the same column.** Joining to `books.price_pence` for a two-year-old invoice silently rewrites financial history.
- **Adding a surrogate id to a junction table and dropping the composite unique.** Duplicate pairings accumulate invisibly and corrupt every aggregate over that table.
- **Indexing the junction table in only one direction.** A B-tree seeks only on a leading prefix, so `(book_id, category_id)` does nothing for "books in this category".
- **Soft deletes without matching partial unique indexes.** `UNIQUE (email)` plus soft-deleted rows means a user who deletes their account can never sign up again. The fix is `UNIQUE (email) WHERE deleted_at IS NULL`.
- **Trusting application-level uniqueness checks under concurrency.** The `SELECT`-then-`INSERT` window is small, and at a few hundred requests per second "small" means "several times a day".

## 16. Interview perspective

Schema design appears in nearly every system-design interview, usually disguised as "sketch the data model". Interviewers are reading four things.

**1. Do you declare cardinality explicitly?** Say *"a book has one author, an author has many books, so the foreign key goes on `books`. Books and categories are many-to-many, so that needs a junction table with a composite primary key on the pair, indexed in both directions."* This is thirty seconds of speech that demonstrates the entire topic.

**2. Do you state what the schema makes impossible?** The strongest sentence available to you is: *"`UNIQUE (user_id, book_id)` means the duplicate-review race condition cannot happen, so I don't need to handle it in the application."* Interviewers are specifically listening for candidates who use the database to **eliminate** classes of bug rather than to store the results of handling them.

**3. Do you make referential-action decisions deliberately?** *"`order_items` cascades from `orders` because a line item is meaningless alone. `orders` restricts from `users` because deleting a customer must never destroy financial history — GDPR erasure is anonymisation, not deletion."* That is a senior answer, and it is two sentences long.

**4. Do you know the physical consequences of logical choices?** UUID v4 versus v7 is the highest-signal example and it comes up often. *"I'd use UUID v7 for the public identifier. v4 is 122 random bits, so every insert lands on a random B-tree leaf page — cache misses, page splits, index bloat and WAL amplification. v7 is timestamp-prefixed, so inserts stay at the right-hand edge like a sequence. On InnoDB, where the table itself is clustered on the primary key, the v4 penalty is several times worse than on PostgreSQL."*

A fifth, cheaper point: when asked "how would you make this page faster", answer **"first I'd check the query count per request"** before reaching for caching or replicas. N+1 is the most common real cause and the least commonly named.

**Red flags interviewers watch for:**
- Drawing boxes and lines but never stating cardinality or where the foreign key lives.
- "We'll enforce that in the application" for an invariant that is one `UNIQUE` or `CHECK`.
- Not knowing that `ON DELETE CASCADE` can delete data transitively, or defaulting to it everywhere.
- Proposing UUID v4 primary keys on a high-write table with no awareness of the index behaviour.
- Representing many-to-many with a comma-separated column or an unqueryable array.
- Being unable to say what a `LEFT JOIN` returns for non-matching rows, or what happens when the right table is filtered in `WHERE`.
- Never mentioning indexes on foreign-key columns.
- Claiming joins are inherently slow, with no distinction between nested loop, hash and merge, and no mention of indexes.
- Describing a lazy-loading ORM pattern without recognising the N+1 it generates.
- Using `FLOAT` for money, or naked `TIMESTAMP` for an instant.
- Saying "it depends" about `CASCADE` versus `RESTRICT` without finishing the sentence — the correct completion is *"it depends on whether the child has independent business value: cascade for owned children like line items, restrict for records anyone may need later."*
- Treating normalisation as a rule to obey rather than a trade-off against read cost (Lesson 56).

## 17. Knowledge check

**Conceptual**

1. Explain precisely why a `UNIQUE (user_id, book_id)` constraint prevents duplicate reviews under high concurrency while an application-level "check whether a review exists, then insert" does not — even if the application code is written carefully and reviewed by three engineers. Name the exact window that exists in one approach and not the other, and state which isolation level would be required to close it in application code.
2. A table's primary key is a randomly generated UUID v4. Describe the physical mechanism by which this degrades insert throughput as the table grows, naming at least three distinct effects on the B-tree and the write path. Then explain why the penalty is substantially larger on MySQL/InnoDB than on PostgreSQL, and why UUID v7 does not have the problem.
3. You have `books LEFT JOIN reviews ON reviews.book_id = books.book_id`. Explain what changes about the result set if the predicate `reviews.rating >= 4` is placed in the `WHERE` clause instead of the `ON` clause, why that happens in terms of `NULL` semantics, and give one realistic BookNook bug that this distinction would cause.

**Practical scenario**

4. BookNook's fulfilment team asks you to add a feature letting an administrator delete a customer account entirely. An engineer implements it as `DELETE FROM users WHERE user_id = $1` and, when it fails, "fixes" it by changing `orders.user_id` to `ON DELETE CASCADE`. Describe everything that goes wrong — including the effect on `order_items`, on revenue reporting and on recoverability — explain why the original error was the system working correctly, and design the correct implementation including which columns change, which stay, and which constraint changes (if any) are needed to keep the rest of the schema valid afterwards.

**Calculation**

5. BookNook's order-history page shows a customer's last 25 orders, and for each order it lists the line items with book titles. The current implementation issues 1 query for the orders, then 1 query per order for its items, then 1 query per item for the book title; assume an average of 3 items per order. Assume: round-trip time 0.6 ms; server time per single-row lookup 0.3 ms; server time for one 25-row join across `orders`, `order_items` and `books` 1.8 ms; driver overhead 0.1 ms per statement; the page is requested 15 times per second at peak; the database has 16 vCPU. Calculate (a) the total number of statements per page render, (b) total database latency for the current implementation, (c) total latency for a single-join implementation, (d) the speed-up factor, and (e) the percentage of the 16 vCPU consumed by server-side execution at peak for each implementation. Then state what happens to both numbers if the round-trip time rises to 2.5 ms, and explain which implementation is more sensitive and why.

**Progress checklist**

```
Phase 1 — Foundations (Lessons 1–17) ................................ [x] complete
Phase 2 — Scaling (Lessons 18–39) ................................... [x] complete
Phase 3 — Data and Storage
  [x]  1. Relational databases ...................................... Lesson 40
  [x]  2. Tables, keys, relationships, joins, constraints ........... Lesson 41
  [ ]  3. SQL versus NoSQL .......................................... Lesson 42
  [ ]  4. Key-value databases ....................................... Lesson 43
  [ ]  5. Document databases ........................................ Lesson 44
  [ ]  6. Wide-column databases ..................................... Lesson 45
  [ ]  7. Graph databases ........................................... Lesson 46
  [ ]  8. Search engines and full-text indexes ...................... Lesson 47
  [ ]  9. Selecting a database from access patterns ................. Lesson 48
  [ ] 10. ACID transactions ......................................... Lesson 49
  [ ] 11. Isolation levels .......................................... Lesson 50
  [ ] 12. Dirty / non-repeatable / phantom reads, lost updates ...... Lesson 51
  [ ] 13. Optimistic versus pessimistic concurrency control ......... Lesson 52
  [ ] 14. Database indexes .......................................... Lesson 53
  [ ] 15. B-tree, hash, composite, covering, secondary indexes ...... Lesson 54
  [ ] 16. Query plans and index trade-offs .......................... Lesson 55
  [ ] 17. Normalisation and denormalisation ......................... Lesson 56
  [ ] 18. OLTP versus OLAP .......................................... Lesson 57
  [ ] 19. Data warehouses, data lakes and lakehouses ................ Lesson 58
  [ ] 20. CAP theorem ............................................... Lesson 59
  [ ] 21. PACELC .................................................... Lesson 60
  [ ] 22. Consistency models ........................................ Lesson 61
  [ ] 23. Object, block and file storage ............................ Lesson 62
  [ ] 24. Retention, archival, backups, disaster recovery ........... Lesson 63
```

**NEXT LESSON: Phase 3, Topic 3 — SQL versus NoSQL**





---

# Lesson 42 — SQL versus NoSQL

*Phase 3, Topic 3*

## 1. Simple definition

**SQL databases** (more precisely, **relational databases** — Lessons 40 and 41) store data in tables of rows and columns, enforce a schema declared up front, and let you ask questions in a *declarative* query language, SQL, where you state *what* you want and an optimiser decides *how* to get it.

**NoSQL databases** are everything else. That is not a joke — it is the literal definition, and it is the first problem with the term.

Terms defined before use:
- **Schema**: the declared shape of your data — which fields exist, their types, which are required, how tables relate. Lesson 41 covered schemas in the relational sense.
- **Declarative query**: you write `SELECT title FROM books WHERE author_id = 7`. You do not write the loop, the index lookup, or the join order. The database's **query optimiser** chooses those.
- **Imperative access**: you write the loop. You fetch by key, then fetch again, then combine in application code. Most NoSQL access is imperative.
- **Join**: combining rows from two tables on a matching key, done inside the database (Lesson 41).
- **ACID**: Atomicity, Consistency, Isolation, Durability — the transactional guarantees Lesson 40 introduced and Topic 7 of this phase will cover in full.
- **Horizontal write scaling**: adding machines to increase the rate of *writes* the system accepts. Lesson 36 (sharding) showed why this is the hard direction.

The honest one-line summary, which the rest of this lesson will justify:

> **Relational databases give you flexibility at query time. NoSQL databases give you scale and speed at the cost of fixing your queries at design time.**

## 2. Why it exists

### Why the name is misleading

"NoSQL" was coined in 2009 as the hashtag for a San Francisco meetup. It was never a technical category. It meant, roughly, "we are not using MySQL and we would like to talk about it". Three separate problems with the name:

1. **It defines a category by what it is not.** "Non-elephant biology" is not a field. A graph database and a key-value store have almost nothing in common except that neither is PostgreSQL.
2. **Many NoSQL databases now speak SQL or something close to it.** Cassandra has CQL. DynamoDB has PartiQL. Couchbase has N1QL. The "No" became retroactively reinterpreted as "Not Only SQL", which is marketing applied to a mistake.
3. **It implies SQL is the problem.** SQL is a query language. The things NoSQL systems actually gave up were *joins*, *multi-record transactions*, and *schema enforcement* — none of which are SQL the language. You could design a relational database with SQL and no joins, or a document store with joins and no SQL. The language and the data model are independent axes that the name conflates.

**What the category actually contains** is a set of databases that each made a *specific* trade: they gave up one or more of {joins, cross-record transactions, enforced schema, arbitrary ad-hoc queries} in exchange for one or more of {horizontal write scaling, predictable low latency, schema flexibility, operational simplicity}.

That is the useful mental model. Not "SQL versus NoSQL" but **"what did this database give up, and did it give up something I need?"**

### The historical pressure

By the mid-2000s three forces hit at once:

- **Data volume** grew faster than single-machine capacity. Lesson 35 covered vertical scaling limits.
- **Write rates** grew. Read scaling has an easy answer — replicas (Lesson 33) and caches (Lessons 27–30). Write scaling does not; it requires sharding (Lesson 36), and sharding a relational database breaks joins and transactions *anyway*.
- **Schema change** on a huge table was an outage. `ALTER TABLE` on a 500 GB MySQL 5.1 table locked it for hours.

The insight the early NoSQL systems had was blunt: *if sharding destroys joins and cross-shard transactions anyway, stop pretending. Build a database that never promised them, and spend the savings on scale and predictability.*

### The four families, one line each

| Family | Model | Representative | Covered in |
|---|---|---|---|
| **Key-value** | An opaque value addressed by a single key; the database does not interpret the value | Redis, DynamoDB, Memcached | Lesson 43 (next) |
| **Document** | A self-describing nested record (JSON-like) that the database *can* index and query inside | MongoDB, Couchbase, DocumentDB | Topic 5 |
| **Wide-column** | Rows with a partition key and a sorted set of columns per row; optimised for huge, sparse, write-heavy tables | Cassandra, HBase, ScyllaDB | Topic 6 |
| **Graph** | Nodes and edges as first-class citizens; traversal is the primary operation | Neo4j, Neptune, JanusGraph | Topic 8 |

Plus adjacent categories that get swept into "NoSQL" loosely: **time-series** (InfluxDB, TimescaleDB), **search** (Elasticsearch — Phase 4), and **object stores** (S3). Those are specialised enough that lumping them in obscures more than it reveals.

## 3. Real-world analogy

**A filing system for a law firm.**

- **The relational database is a central records department.** Every document is filed on a standard form. The forms cross-reference each other by case number. If a partner asks "every client in Leeds who has an open matter with an unpaid invoice over £5,000", a clerk can answer it *today*, because the records department can cross-reference any form against any other. Nobody anticipated that question; the filing system is general enough to answer it anyway. The cost: every new document must be put on an approved form, there is a queue at the records desk, and the department lives in one building.

- **The key-value store is a wall of numbered lockers.** Give the number, get the contents, instantly. You cannot ask "which lockers contain red folders" without opening all of them.

- **The document store is a shelf of client dossiers.** Everything about one client in one folder, including things no form had a box for. Pull one dossier, everything is there, no cross-referencing. But "all clients in Leeds" means going through every dossier unless someone kept a separate index.

- **The wide-column store is a warehouse of timestamped logbooks.** Enormous, append-mostly, sorted by date within each case. Brilliant for "show me everything for case 9912 in March". Useless for "find the case with the oddest fee".

- **The graph database is the corkboard with photos and string.** "Who introduced the client who introduced this client?" is one walk along the strings. In the records department it is a self-join three levels deep that nobody wants to write.

**Where the analogy breaks down:**

| Law firm filing | Real databases |
|---|---|
| A clerk can improvise and look in two places when the form is wrong | A database executes exactly what it was asked; there is no improvisation. A missing index does not produce "a bit slower", it produces a 400× full scan |
| Adding a locker wall is a purchase decision | Adding a shard is a data-migration project with a rebalancing window (Lesson 38) |
| One central records department is obviously a bottleneck | A single PostgreSQL instance on modern hardware handles far more than intuition suggests — see §9. The "obvious bottleneck" is frequently not one |
| Forms and dossiers are mutually exclusive choices | PostgreSQL has JSONB; MySQL has JSON columns. You can have forms *with* a free-text dossier section in the same row. §4 covers why this matters enormously |
| A dossier that is internally inconsistent is a visible mess | An inconsistent document is invisible until a code path reads it six months later and throws `undefined is not a function` in production |
| The clerk enforces the form rules | In a schemaless store, *your application code* is the clerk — and you have three services and two batch jobs all acting as clerks, disagreeing |

That last row is the single most important idea in this lesson, and §4 develops it.

## 4. Detailed technical explanation

### What relational databases genuinely give you

These are not nostalgia. Each is a concrete capability you lose by leaving.

**1. Declarative queries with a cost-based optimiser.**
You write *what*. The optimiser decides *how*: which index, which join algorithm (nested loop, hash join, merge join), which order to join four tables, whether to parallelise. It uses **statistics** — sampled histograms of column value distributions, refreshed by `ANALYZE` — to estimate how many rows each step produces. When BookNook's catalogue grows from 10,000 books to 10 million, the same query text may silently switch from a nested loop to a hash join because the optimiser noticed. Nobody redeployed anything.

That is forty years of compiler engineering working on your behalf. In a key-value store you are the optimiser, you have no statistics, and your plan is frozen in code at deploy time.

**2. Joins.**
The ability to store a fact *once* and combine it at read time. BookNook stores an author's name once in `authors`. Rename "Robert Galbraith" to "J. K. Rowling" and it is one `UPDATE` of one row; all 14 books reflect it immediately. In a document store where the author name is embedded in each book document, that is 14 writes, non-atomic, and if your process dies after 9 of them you have a permanently inconsistent catalogue with no error anywhere.

**3. ACID transactions across multiple records.**
`BEGIN; UPDATE inventory SET stock = stock - 1 WHERE isbn = '...' AND stock > 0; INSERT INTO orders ...; COMMIT;` Either both happen or neither does, even if the machine loses power between them. Phase 3 Topic 7 covers isolation levels in detail. For now: this is the guarantee that makes "we sold the last copy twice" impossible rather than merely unlikely.

**4. Declarative constraints (Lesson 41).**
`NOT NULL`, `CHECK (price >= 0)`, `UNIQUE (isbn)`, `FOREIGN KEY`. The database refuses bad data regardless of *which* client sent it — your Node API, the Python batch importer, a DBA's manual `psql` session at 2 a.m., or next year's service written by someone who never read your validation module. Constraints are the only form of data validation that cannot be bypassed.

**5. Decades of operational knowledge.**
Backup, point-in-time recovery, replication (Lesson 33), connection pooling, query plan analysis, monitoring, index tuning — all of it documented, all of it with experienced practitioners available to hire, all of it with known failure modes and known fixes. This is a genuine engineering asset and it is routinely undervalued because it is boring.

### What relational databases genuinely cost

**1. Write scaling is hard.** A single primary accepts all writes (Lesson 33). Scaling past it means sharding (Lesson 36), and once sharded, cross-shard joins and cross-shard transactions require distributed coordination (two-phase commit — Phase 4) which is slow and adds failure modes. The honest statement is not "relational databases don't scale"; it is **"a relational database scales writes exactly as well as any other database once you shard it, and before you shard it, it scales further than people expect."**

**2. Schema migrations are coordinated events.** Adding a nullable column to a large table is fast on modern PostgreSQL and MySQL 8 (metadata-only). Adding a `NOT NULL` column with a default, changing a type, or adding an index on a hot 500 GB table still requires care — `pg_repack`, `gh-ost`, `pt-online-schema-change`, or a careful multi-deploy expand/contract dance.

**3. Impedance mismatch.** Objects in code are nested; rows are flat. An ORM bridges this and introduces its own costs: N+1 query patterns, surprising lazy loading, and a layer of abstraction that hides the query plan.

**4. The join is not free at extreme scale.** Joining two 2-billion-row tables is genuinely expensive. For a workload that *only ever* needs one access pattern, pre-joining at write time is faster.

**5. Rigid shape for genuinely heterogeneous data.** If every BookNook product category has different attributes — books have ISBN and page count, e-books have file format and DRM flag, audiobooks have narrator and duration, merchandise has size and colour — modelling that in pure relational form gives you either a wide sparse table, a pile of category tables, or an Entity-Attribute-Value table (which is a key-value store wearing a relational costume, and performs like one).

### What NoSQL genuinely gives you

**1. Horizontal write scaling as a built-in, not a project.** Cassandra and DynamoDB partition by key from day one (Lesson 38's consistent hashing). You do not "shard" them later; they were always sharded. Adding capacity is adding nodes. This is the single most defensible reason to choose NoSQL.

**2. Flexible schema at the storage layer.** Add a field to one document without touching the other 40 million. For genuinely heterogeneous or rapidly evolving data this is real value.

**3. Access-pattern optimisation.** If you know you will *only* ever read "the 20 most recent orders for customer X", a wide-column table partitioned by `customer_id` and clustered by `order_date DESC` answers it with one sequential disk read, no index lookup, no sort, no join. A relational database answers it with an index seek plus row fetches — fast, but not *that* fast, and not that predictable at p99 (Lesson 16).

**4. Operational simplicity for narrow cases.** Memcached has essentially no operational surface: it stores bytes in RAM and forgets them. That genuine simplicity is worth something.

**5. Predictable latency under load.** DynamoDB's promise is single-digit-millisecond reads *regardless of table size*, because the work for a key lookup does not grow with the number of items. A relational index lookup is O(log n) with a tree depth that grows, plus a plan that can change under you.

### What NoSQL genuinely costs

**1. No joins.** You denormalise (duplicate data) or you join in application code. Application-side joins are N+1 network round trips. At 1 ms each that is 100 ms for a 100-item page that SQL would have done in one query in 4 ms.

**2. Weaker transactions.** Many stores guarantee atomicity only within a single key/document/partition. DynamoDB has `TransactWriteItems` (up to 100 items, with a cost multiplier). MongoDB has multi-document transactions since 4.0 (with performance caveats). Cassandra has lightweight transactions via Paxos (slow — roughly 4× a normal write). These exist, but they are the exception path, not the default, and they cost more.

**3. Application-enforced integrity.** No foreign keys. Nothing stops an order referencing a deleted book. The *only* defence is code discipline across every writer, forever, including the ones written by people who have since left.

**4. Access patterns fixed at design time.** This is the one people underestimate. In relational modelling you model the *data*. In NoSQL modelling you model the *queries* — the DynamoDB single-table design methodology literally begins "list every access pattern your application will ever have." When product asks for a new query six months later and it does not match your partition key, your options are: a secondary index (extra cost, eventual consistency), a full table scan (slow and expensive), or a migration. In PostgreSQL the answer is `CREATE INDEX CONCURRENTLY` and you are done in an hour.

**5. Fewer, newer tools and shallower expertise.**

### Schema-on-write versus schema-on-read

**Schema-on-write**: the schema is declared in the database. Writes that violate it are rejected at write time. Errors surface immediately, at the point of the bug, with the offending data in hand. This is relational.

**Schema-on-read**: anything can be written. The schema is applied by whatever code reads the data later. Errors surface at read time, possibly months later, in a different service, with no information about who wrote the bad record or when.

**The crucial point: "schemaless" does not mean "no schema". It means the schema moved from the database into your application code.**

Your application absolutely has a schema. It is the union of every field access in every code path:

```javascript
// This code IS a schema declaration. It just isn't enforced or documented anywhere.
const total = order.items                      // requires: items exists, is an array
  .map(i => i.price_cents * i.quantity)        // requires: price_cents is a number (not a string!)
  .reduce((a, b) => a + b, 0);
const label = order.shipping.address.postcode; // requires: shipping.address.postcode exists
```

Three failure modes follow directly:
1. **Implicit version sprawl.** After two years your collection holds v1 documents (`price` in pounds as a float), v2 (`price_cents` as an integer), and v3 (`pricing: {amount_cents, currency}`). Every read path needs all three branches. Nobody can delete the v1 branch because nobody can prove no v1 documents remain.
2. **Silent type drift.** One service writes `quantity: 2`, another writes `quantity: "2"`. `"2" * 500` is 1000 in JavaScript, so it works — until someone writes `quantity: "2 "` or a sum becomes string concatenation and an order totals `"22"`.
3. **No single source of truth for the shape.** The schema exists in five services' code and nobody's documentation.

Mitigations that work: a schema registry (Avro, Protobuf, JSON Schema) validated in a shared library; MongoDB's `$jsonSchema` validator; a mandatory `schema_version` field on every document, written from day one. Note what all three are: **re-introducing schema-on-write, by hand, less rigorously.** If you find yourself building these, ask whether you wanted schema-on-read at all.

### The honest modern picture: the distinction has blurred

The 2010 framing is obsolete. What actually happened:

**Relational databases absorbed the document model.**
- PostgreSQL's **JSONB** stores binary JSON, indexes it with GIN (inverted index), and queries inside it: `SELECT * FROM products WHERE attrs @> '{"format":"epub"}'`. You get document flexibility *and* joins *and* ACID *and* constraints, in one engine, in one transaction.
- MySQL 5.7+ has a `JSON` type with generated columns you can index.
- Both have gained logical replication, partitioning, and parallel query.

**NoSQL databases absorbed relational features.**
- MongoDB added multi-document ACID transactions (4.0), schema validation, and `$lookup` (a join).
- Cassandra's CQL deliberately looks like SQL.
- DynamoDB added transactions and PartiQL.

**And a genuinely new category appeared: NewSQL / distributed SQL.** CockroachDB, YugabyteDB, TiDB, Google Spanner, Vitess. These give full SQL, joins, and ACID transactions *on top of* automatic horizontal sharding and replication. The historical justification for NoSQL — "you must give up SQL to scale writes" — is simply no longer true in 2026, though distributed SQL has its own costs: higher baseline write latency (cross-node consensus, typically 5–20 ms versus 1–2 ms single-node), operational complexity, and cost.

So the 2026 question is not "SQL or NoSQL". It is:

> *"Which engine's trade-offs match my access patterns, consistency requirements, scale, and the expertise of the team that will operate it at 3 a.m.?"*

### A decision framework based on access patterns, not data volume

Ask these, in order:

1. **Can I enumerate every access pattern, and will that list stay stable?**
   - Yes, small stable list → key-value or wide-column is viable.
   - No, or queries are exploratory and keep changing → relational. This is the dominant factor.
2. **Do I need to combine data across entities at read time?**
   - Yes → relational (joins) or accept denormalisation debt forever.
3. **Do I need atomic multi-entity writes?**
   - Yes, routinely (orders + inventory + payment) → relational.
   - Only occasionally → NoSQL with a transaction API is workable.
4. **What is my sustained write rate, and is it beyond one well-tuned primary?**
   - See §9 for what "one primary" actually means in numbers. Most teams are wrong about this by one to two orders of magnitude.
5. **Is the data genuinely heterogeneous in shape?**
   - Yes → document store, *or* a relational table with a JSONB column. Try the second first; it is strictly more capable.
6. **What latency do I need at p99, and how tight is the budget?**
   - Sub-millisecond, cache-like → in-memory key-value (Lesson 43).
7. **Who operates this?**
   - A three-person team that knows PostgreSQL will have better uptime on PostgreSQL than on a Cassandra ring they learned last month. This is a legitimate engineering input, not an excuse.

**Default recommendation, stated plainly:** start with PostgreSQL (or MySQL). Add a specialised store when you have a *measured* access pattern it serves badly. This is not conservatism; it is keeping options open, because moving *from* relational *to* a specialised store is a well-trodden path, and moving back is a rewrite.

### Polyglot persistence and its real operational cost

**Polyglot persistence** means using several database technologies in one system, each for the job it suits. BookNook might end with: PostgreSQL for orders and inventory, Redis for sessions and carts, Elasticsearch for search, S3 for cover images, Cassandra for the event log.

That is a reasonable mature architecture. The costs, which are routinely omitted from the diagram:

| Cost | Detail |
|---|---|
| **Operational surface** | Five technologies × (backup, restore drills, monitoring, alerting, upgrades, security patching, capacity planning, on-call runbooks) |
| **Expertise** | On-call must debug all five at 3 a.m. Realistically you have one expert per store, and they take holidays |
| **No cross-store transactions** | Writing an order to PostgreSQL and an event to Cassandra cannot be atomic. You need the **outbox pattern** or **sagas** (Phase 4) — which is real work |
| **Consistency windows** | Search index lags the database by seconds. The user edits a title and search shows the old one. You must design for this and explain it to product |
| **Cost floor** | Each managed store has a minimum spend. Five stores can be £1,200/month before serving a single user |
| **Cognitive load** | Every new engineer must learn five data models and five query languages |

Rule of thumb: **each additional datastore should earn its keep against a measured problem.** "We might need it" is not a measurement.

### Explicitly debunking "choose NoSQL because you have a lot of data"

This is the most common bad reason, and it is usually wrong by orders of magnitude. §9 works the numbers. The short version: a single modern relational instance comfortably handles tens of terabytes and tens of thousands of transactions per second. The overwhelming majority of systems that "needed NoSQL for scale" needed an index.

## 5. Request / data flow

Trace one BookNook request — *"show the order confirmation page for order 88123"*, which needs the order, its 3 line items, the book titles, and the customer's name — through both architectures.

**Path A — relational (PostgreSQL):**

1. The browser issues `GET /orders/88123`; the load balancer (Lesson 21) picks an app server.
2. The app server checks Redis for a cached rendering. Miss (Lesson 28).
3. The app takes a connection from the pool (a pre-opened TCP + TLS session, avoiding the ~3 ms handshake of Lesson 6).
4. It sends **one** SQL statement joining `orders`, `order_items`, `books`, and `customers`, with `WHERE orders.id = 88123`.
5. The parser produces a syntax tree; the planner enumerates join orders and consults table statistics.
6. The planner picks: index seek on `orders.id` (1 row) → index seek on `order_items.order_id` (3 rows) → nested-loop index seeks into `books` (3 rows) → index seek into `customers` (1 row).
7. The executor runs it. All index and row pages are in the buffer pool (RAM) — no disk I/O. ~0.4 ms server-side.
8. One result set of 3 denormalised rows crosses the network once. Total round trip ~1.2 ms.
9. The app renders, caches in Redis with a 60 s TTL, and returns. The CDN (Lesson 30) does not cache this — it is per-user.

**Path B — key-value / document store, naively modelled:**

1–3. Identical.
4. `GET order#88123` → the order document, containing 3 item records with ISBNs and a `customer_id`. ~1 ms.
5. The app discovers it needs 3 book titles. It issues 3 `GET book#<isbn>` calls — in parallel if the developer remembered, sequentially if not. 1 ms parallel, 3 ms sequential.
6. It issues `GET customer#4471` for the name. ~1 ms.
7. The app joins the 5 responses in memory — the join the database used to do.
8. Total 3–5 ms and 5 round trips instead of 1.

**Path C — key-value / document store, correctly modelled:**

4. `GET order#88123` returns a document that *already embeds* the titles and customer name, denormalised at write time. ~1 ms, one round trip. **Faster than Path A.**
5. Render and return.

Path C is the real NoSQL argument, and the cost is visible in the write path:

1. The app writes the order, embedding the current titles and customer name.
2. Later, a book title is corrected. Now every order document containing that ISBN holds a stale title.
3. You must decide: accept staleness (often correct — an invoice *should* show the title as sold), or run a background fan-out job updating potentially millions of documents, non-atomically, with retries and a reconciliation report.

**The trade is made at write time and paid for at write time.** Path C's read speed is real and so is its write complexity.

## 6. ASCII diagram

```
                 THE SAME QUERY, TWO ARCHITECTURES
                 ---------------------------------

 RELATIONAL (normalised, join at read time)
 ============================================

   app ──1 query──▶ ┌─────────── PostgreSQL ───────────┐
                    │  parse → plan (cost-based) → exec │
                    │                                   │
                    │   orders ──FK──▶ customers        │
                    │      │                            │
                    │      └─FK─▶ order_items ─FK─▶ books
                    │                                   │
                    │   JOIN happens HERE, in the engine│
                    └───────────────────────────────────┘
   app ◀─1 result─── 3 rows, fully assembled      ~1.2 ms, 1 round trip

   fact stored ONCE  →  update = 1 row  →  always consistent
   new query type    →  CREATE INDEX    →  done in an hour


 KEY-VALUE / DOCUMENT (denormalised, join at write time)
 =======================================================

   WRITE PATH (expensive, complex)
   app ──┬─ PUT order#88123  { items:[{isbn, title, price}], cust_name }
         ├─ PUT custIdx#4471#2026-10-09#88123   (duplicate for "my orders")
         └─ PUT bookSales#9780… #2026-10 += 1   (duplicate for reporting)
            ▲
            └── application is responsible for keeping all 3 in step.
                No transaction spans them. No constraint checks them.

   READ PATH (cheap, rigid)
   app ── GET order#88123 ──▶ ┌── partition = hash(key) ──┐
                              │  node 3 of 12             │
                              │  one lookup, no join,     │
                              │  no planner, no stats     │
                              └───────────────────────────┘
   app ◀── whole document, pre-assembled        ~1.0 ms, 1 round trip

   fact stored N TIMES → update = N writes → eventually consistent, maybe
   new query type      → new index or migration or full scan


 WHERE THE WORK MOVED
 ====================
      RELATIONAL            NoSQL
      ----------            -----
  write:  ▓                 write:  ▓▓▓▓▓▓
  read:   ▓▓▓               read:   ▓▓
  change: ▓                 change: ▓▓▓▓▓▓▓▓
         (new query = index)       (new query = redesign)
```

## 7. Detailed example

### BookNook — a concrete modelling decision

BookNook (our continuing example since Lesson 18) now has: 2.1 million books, 850,000 registered customers, 40,000 orders/day, and a new requirement — **customer browsing events**, to power recommendations.

The team has one relational database (PostgreSQL 16, primary + 2 read replicas, Lesson 33). Two new workloads arrive in the same sprint.

**Workload A — orders.** Place an order: decrement stock for each of up to 20 items, insert the order, insert line items, insert a payment record, apply a voucher, all atomically. ~40,000/day, peaking at ~15/second on a Saturday morning, ~120/second during a Black Friday flash sale.

Analysis:
- Multi-entity atomic write: *required*. Overselling the last copy is a refund, an apology email, and a support ticket.
- Access patterns: unstable. Finance will want revenue by category by month. Support will want "all orders from this customer containing a refunded item". Ops will want "orders stuck in PENDING over 30 minutes". None of these are known in advance.
- Write rate: 120/second peak.

**Decision: PostgreSQL.** 120 writes/second is roughly 0.4% of what this instance can do (§9). The ad-hoc query requirement alone settles it; the transaction requirement settles it twice.

**Workload B — browsing events.** Every page view, search, and scroll-depth event. ~45 million events/day (~520/second average, ~2,000/second peak). Each event is ~400 bytes. Written once, never updated, read only by a nightly batch job that scans a day's events, and occasionally by a support engineer replaying one customer's last hour.

Analysis:
- Write rate 2,000/second sustained, and growing with traffic.
- Volume: 45 M × 400 B ≈ 18 GB/day ≈ 6.5 TB/year.
- Access patterns: exactly two, and *stable*: (a) scan by time range, (b) fetch by `customer_id` ordered by time. Both known with high confidence because the data has no other consumer.
- Transactions: not needed. Losing one scroll event is not a business event.
- Joins: not needed at write time; the batch job joins against PostgreSQL offline.

**Decision: a wide-column store (Cassandra), partition key `customer_id`, clustering key `event_time DESC`.** This is the rare clean case: huge write volume, no transactions, no joins, two stable access patterns, and acceptable loss tolerance.

**What the team explicitly did *not* do:** migrate orders to Cassandra "for consistency of stack". That would have traded a working transactional system for a distributed one with application-enforced integrity, to solve a problem it did not have.

**What they also considered and rejected:** putting events in PostgreSQL with monthly partitioning. Honest assessment — it would have worked for about 18 months. 2,000 writes/second is within range. The reason to reject it was that 6.5 TB/year of write-heavy data in the same instance as the transactional workload competes for buffer pool and WAL bandwidth, degrading the p99 of the *order* path. **Workload isolation, not raw scale, was the deciding argument.** That is a more honest reason than "big data".

### A contrasting real-world example — Discord

Discord's message store is the clearest public case in the opposite direction, and the numbers are published.

- They began on **MongoDB** (2015). It worked until the working set exceeded RAM; then read latency became unpredictable and they could not keep the index hot.
- They moved to **Cassandra** (2017), partitioning messages by `(channel_id, bucket)`. It scaled to trillions of messages. But they hit Cassandra-specific pain: **tombstones** (deletion markers that must be scanned past, so a channel with many deleted messages got slower), GC pauses on the JVM causing latency spikes, and hot partitions when a large public channel got busy.
- They moved to **ScyllaDB** (2022) — same data model, C++ implementation, no JVM GC, shard-per-core. Node count dropped from 177 to 72 and p99 latency improved substantially.

Three lessons to take from this:
1. Messages are a *genuine* key-value/wide-column workload: enormous volume, one dominant access pattern ("recent messages in channel X"), no joins, no cross-record transactions.
2. **They still use PostgreSQL** for the things PostgreSQL is good at. This was never "replace relational".
3. The second migration was *within* the same data model. Once your access patterns are modelled correctly, swapping the engine is tractable; changing the access patterns is not. **The data model is the architecture; the product is an implementation detail.**

## 8. Code or configuration example

> **Illustrative only — not production-ready.** Error handling, connection management, retries, pagination, authentication, and parameterised-query hygiene beyond the examples shown are all omitted for clarity. Do not copy into a real system.

**(a) The relational version — one query, the engine does the work.**

```sql
-- BookNook: full order confirmation in ONE round trip.
-- Schema per Lesson 41: orders → order_items → books, orders → customers.
SELECT
    o.id                AS order_id,
    o.placed_at,                         -- stored as timestamptz; never a naive timestamp
    c.full_name,                         -- stored ONCE in customers; join keeps it current
    b.title,                             -- stored ONCE in books
    oi.quantity,
    oi.unit_price_cents                  -- integer cents: never FLOAT for money (rounding)
FROM orders        o
JOIN customers     c  ON c.id = o.customer_id      -- FK guarantees c exists; no orphan rows
JOIN order_items   oi ON oi.order_id = o.id        -- 1-to-many; 3 rows for this order
JOIN books         b  ON b.isbn = oi.isbn          -- FK guarantees the book exists
WHERE o.id = $1                                    -- $1: bound parameter, not interpolation
ORDER BY oi.line_number;                           -- explicit order; row order is NEVER implied
```

```sql
-- The atomic order placement. This is what you give up by leaving relational.
BEGIN;                                             -- opens the transaction

  -- Conditional decrement. The WHERE clause is the overselling guard:
  -- if stock is already 0 the UPDATE matches 0 rows and we abort below.
  UPDATE books
     SET stock_count = stock_count - 1
   WHERE isbn = '9780141036144'
     AND stock_count > 0;                          -- the check and the write are ONE atomic step

  -- Application checks rows-affected here; if 0, issue ROLLBACK instead of continuing.

  INSERT INTO orders (customer_id, status, placed_at)
  VALUES (4471, 'PENDING', now())
  RETURNING id;                                    -- returns the generated id without a 2nd query

  INSERT INTO order_items (order_id, isbn, quantity, unit_price_cents, line_number)
  VALUES (currval('orders_id_seq'), '9780141036144', 1, 899, 1);
  -- FOREIGN KEY on isbn means a typo here fails the transaction, not corrupts the data

COMMIT;                                            -- all of it, or none of it. Power loss included.
```

**(b) PostgreSQL JSONB — the "blurred line" in practice.**

```sql
-- Heterogeneous product attributes WITHOUT leaving the relational engine.
CREATE TABLE products (
    sku          TEXT PRIMARY KEY,
    name         TEXT        NOT NULL,             -- enforced: every product has a name
    price_cents  INTEGER     NOT NULL CHECK (price_cents >= 0),  -- enforced: no negative prices
    category     TEXT        NOT NULL,
    attrs        JSONB       NOT NULL DEFAULT '{}' -- UNenforced: the flexible part
);

-- GIN index: an inverted index over every key/value pair inside attrs.
-- Without it, the query below is a full table scan.
CREATE INDEX idx_products_attrs ON products USING GIN (attrs);

INSERT INTO products VALUES
  ('BK-001','Mythos',   1299,'book',      '{"isbn":"9780718188733","pages":416}'),
  ('EB-001','Mythos',    799,'ebook',     '{"format":"epub","drm":false,"mb":4.2}'),
  ('AB-001','Mythos',   1499,'audiobook', '{"narrator":"Stephen Fry","minutes":943}');

-- @> is "contains": find every DRM-free item. Uses the GIN index.
SELECT sku, name FROM products WHERE attrs @> '{"drm": false}';

-- ->> extracts a JSON field as text; ::int casts it. Mixing flexible and relational freely:
SELECT sku, attrs->>'narrator' AS narrator
FROM products
WHERE category = 'audiobook'                       -- indexed, enforced, typed column
  AND (attrs->>'minutes')::int > 600;              -- flexible column, cast at query time

-- And it STILL joins and STILL participates in transactions.
-- This single capability removes the most common reason teams reach for a document store.
```

**(c) The same data in a key-value store, showing what moved into the application.**

```python
# BookNook order confirmation against a key-value store (DynamoDB-style).
# Compare the volume of code to the ONE SQL statement in (a).

def get_order_confirmation(db, order_id):
    order = db.get(f"order#{order_id}")            # round trip 1
    if order is None:
        return None                                # no FK existed to prevent a dangling reference

    # The join the database used to do, now in Python:
    isbns = [item["isbn"] for item in order["items"]]
    books = db.batch_get([f"book#{i}" for i in isbns])   # round trip 2 (batched, if you remember)
    customer = db.get(f"customer#{order['customer_id']}") # round trip 3

    # Schema-on-read in action: every one of these .get() calls with a default
    # is a defence against a document written by an older code version.
    return {
        "order_id": order_id,
        "customer": customer.get("full_name", "(unknown)"),   # v1 docs used "name"
        "items": [
            {
                "title": books.get(f"book#{i['isbn']}", {}).get("title", "(deleted book)"),
                "qty":   int(i.get("quantity", i.get("qty", 1))),   # two historical field names
                # v1 stored pounds as float, v2 stores integer pence. Both are still in the table.
                "price_cents": i.get("price_cents") or int(float(i.get("price", 0)) * 100),
            }
            for i in order["items"]
        ],
    }
```

```python
# The denormalised ("Path C") write. Fast reads, but look at what you now own.
def place_order_denormalised(db, customer, items):
    customer_rec = db.get(f"customer#{customer['id']}")
    books = db.batch_get([f"book#{i['isbn']}" for i in items])

    doc = {
        "pk": f"order#{new_id()}",
        "customer_id": customer["id"],
        "customer_name": customer_rec["full_name"],   # COPIED. Stale if the customer renames.
        "schema_version": 3,                          # mandatory from day one. Non-negotiable.
        "items": [{
            "isbn":  i["isbn"],
            "title": books[f"book#{i['isbn']}"]["title"],  # COPIED (arguably correct for an invoice)
            "price_cents": books[f"book#{i['isbn']}"]["price_cents"],  # COPIED (definitely correct)
            "quantity": i["quantity"],
        } for i in items],
    }
    db.put(doc)                                      # atomic for THIS item only

    # Stock decrement lives in a DIFFERENT item, so it is a SEPARATE atomic operation.
    for i in items:
        db.update(f"book#{i['isbn']}",
                  add={"stock_count": -i["quantity"]},
                  condition="stock_count >= :q")     # per-item conditional write
    # If the process dies HERE: the order exists and stock was never decremented.
    # Nothing rolls back. You need an idempotent reconciliation job (Lesson 17) to repair it.
    # In the SQL version, the crash simply aborts the transaction and nothing happened.
```

## 9. Numerical example

**The question: at what scale does BookNook genuinely outgrow one PostgreSQL instance?**

**Stated assumptions** (all figures *typical* for 2026 cloud hardware; your mileage varies, and the point is the order of magnitude, not the digits):

| Assumption | Value | Basis |
|---|---|---|
| Instance | 32 vCPU, 256 GB RAM, NVMe SSD | ~`db.m7g.8xlarge` class, ~£1,100/month |
| NVMe random read IOPS | 200,000 | typical for cloud NVMe at this size |
| Sequential read throughput | 2 GB/s | typical |
| Disk write latency (fsync) | 0.1 ms | typical NVMe with write cache + power-loss protection |
| Average row size, `orders` | 400 bytes | measured |
| Average row size, `order_items` | 120 bytes | measured |
| PostgreSQL page size | 8 KB | default |
| Simple indexed read, all pages cached | 0.2 ms CPU | typical |
| Transactional write (the §8 order txn) | 2.5 ms CPU + 1 fsync | typical |
| Useful CPU headroom | 60% (never plan to 100%) | operational practice |
| BookNook today | 40,000 orders/day, 2.1 M books, 850 K customers | given in §7 |

**Step 1 — current data volume.**

- Orders per year: 40,000 × 365 = **14,600,000 rows**
- Orders bytes: 14,600,000 × 400 B = 5,840,000,000 B = **5.84 GB/year**
- Line items: assume 2.4 items per order → 14,600,000 × 2.4 = 35,040,000 rows
- Line-item bytes: 35,040,000 × 120 B = 4,204,800,000 B = **4.20 GB/year**
- Books: 2,100,000 × 1,500 B = 3,150,000,000 B = **3.15 GB**
- Customers: 850,000 × 800 B = 680,000,000 B = **0.68 GB**
- Indexes: assume indexes total 60% of table size (typical for 3–4 indexes per table).
  Tables = 5.84 + 4.20 + 3.15 + 0.68 = 13.87 GB → indexes = 13.87 × 0.60 = **8.32 GB**
- **Total year one: 13.87 + 8.32 = 22.19 GB.**

**Step 2 — how many years until RAM is exceeded?**

Annual growth = 5.84 + 4.20 = 10.04 GB of new order data, ×1.6 for indexes = 16.06 GB/year.
Books and customers also grow; assume +25%/year → +0.96 GB/year with indexes.
Total growth ≈ **17.0 GB/year**.

Usable buffer pool ≈ 70% of RAM = 256 × 0.70 = **179 GB**.

Years until the whole dataset no longer fits in RAM: (179 − 22.19) / 17.0 = 156.8 / 17.0 = **9.2 years**.

And that is the *entire* dataset. The **working set** — the rows actually touched in a typical hour — is far smaller, because nobody reads 2019's orders. If the working set is the last 90 days of orders plus all books and customers:
- 90 days of orders+items = (10.04 / 365) × 90 × 1.6 = 0.0275 × 90 × 1.6 = **3.96 GB**
- plus books+customers+indexes = 3.83 × 1.6 = **6.13 GB**
- **Working set ≈ 10.1 GB.** It fits in RAM 17× over. BookNook could grow 17× before RAM is even a conversation.

**Step 3 — read throughput ceiling.**

At 0.2 ms CPU per cached indexed read, one vCPU does 1,000 ms / 0.2 ms = **5,000 reads/second**.
32 vCPU × 5,000 = 160,000 reads/second theoretical.
At 60% headroom: 160,000 × 0.60 = **96,000 reads/second** on the primary alone.
With 2 read replicas (Lesson 33) carrying read traffic: ~**288,000 reads/second** fleet-wide.

BookNook's current read rate: assume 25 page views per order placed → 40,000 × 25 = 1,000,000 page views/day, each averaging 6 queries = 6,000,000 queries/day.
6,000,000 / 86,400 s = **69 queries/second average**. Peak at 5× average = **347/second**.

Utilisation of the primary alone: 347 / 96,000 = **0.36%**.
**Headroom factor: 277×.**

**Step 4 — write throughput ceiling.**

Two independent limits; the lower one binds.

*CPU limit:* 2.5 ms per transaction per vCPU → 1,000 / 2.5 = 400 txn/s/vCPU. ×32 = 12,800 txn/s. ×0.60 headroom = **7,680 transactions/second**.

*Durability limit:* every `COMMIT` needs an fsync of the write-ahead log. At 0.1 ms per fsync, serially that caps at 10,000 commits/second. But PostgreSQL uses **group commit**: concurrent transactions arriving within one fsync window share a single fsync. With a typical group size of 8, effective ceiling ≈ 10,000 × 8 = 80,000 commits/second. So durability is *not* the binding limit here; CPU is.

**Binding limit: ~7,680 write transactions/second.**

BookNook's peak: **120 orders/second** (Black Friday, from §7).
Utilisation: 120 / 7,680 = **1.56%**. **Headroom factor: 64×.**

**Step 5 — what scale would actually force a change?**

| Resource | Capacity | Current peak | Scale-out needed at |
|---|---|---|---|
| Write txn/s | 7,680 | 120 | **64× today** = 2.56 M orders/day |
| Read q/s (primary) | 96,000 | 347 | 277× today (and replicas push this further) |
| RAM for working set | 179 GB | 10.1 GB | 17× today |
| Disk | 10+ TB available | 22 GB | 450× today |

**Conclusion.** BookNook would need to grow **64×** — to roughly **2.5 million orders per day**, about the scale of a top-20 global e-commerce site — before write capacity on a *single* PostgreSQL instance becomes the binding constraint. And even then the first moves are cheaper than migration: a bigger instance (vertical scaling, Lesson 35 — 96 vCPU is readily available, a further 3×), moving the event stream out (already done in §7), and read replicas.

**This is the debunking.** "We have a lot of data" is almost never the real reason. For reference, published figures put a single well-tuned PostgreSQL or MySQL instance comfortably in the range of **10–50 TB of data** and **5,000–20,000 write transactions/second**. If your numbers are a hundredth of that and queries are slow, the cause is overwhelmingly likely to be:
1. A missing index (the single most common cause — a 400× difference between a seek and a scan).
2. N+1 queries from an ORM (600 round trips where 1 would do).
3. No connection pooling (a 3 ms handshake per request, Lesson 6).
4. No caching layer (Lessons 27–30).
5. A long-running transaction holding locks.

Each of those is a day's work. A NoSQL migration is six months and a permanent architectural commitment. **Measure before you migrate.**

## 10. Trade-offs

| Dimension | Relational | NoSQL (general) |
|---|---|---|
| **Query flexibility** | Any query the data supports, decided at run time | Only the patterns you designed for; anything else is a scan or an index addition |
| **Who optimises** | Cost-based optimiser with live statistics | You, in application code, with a plan frozen at deploy time |
| **Joins** | Native, optimised, one round trip | Application-side (N+1 round trips) or denormalisation |
| **Multi-record atomicity** | Default, free, unlimited scope | Limited scope, extra API, extra cost, sometimes absent |
| **Integrity enforcement** | Database (unbypassable) | Application (every writer must agree, forever) |
| **Schema change** | A coordinated migration, but a *visible, versioned* one | Free at write time; paid for by permanent branching in read code |
| **Write scaling** | One primary until you shard; sharding is a project | Built in from day one |
| **Read latency p99** | Good; can vary with plan changes and cache state | Excellent and *predictable* for the designed pattern |
| **Data duplication** | Minimal (normalised) | Deliberate and extensive |
| **Cost of a wrong model** | Add an index, refactor a query — hours | Rewrite the key design and migrate every record — months |
| **Expertise availability** | Very high | Lower, and store-specific |
| **Operational maturity** | 40+ years of tooling and known failure modes | 10–18 years; good but shallower |

**The single sentence to remember:** relational databases defer the decision about *how* to read your data; NoSQL databases force it at design time in exchange for scale and predictability. Deferring a decision is valuable precisely when you do not yet know the answer — which describes most products most of the time.

## 11. Failure scenarios

| Failure | What the user experiences | How it is detected | How it recovers |
|---|---|---|---|
| **Denormalised copy goes stale** (title updated in `books`, not in 4 M order documents) | Order history shows an old title; a support ticket says "this isn't what I bought" | Usually a customer report. Proactively: a nightly reconciliation job comparing samples of denormalised copies to the source of truth | Decide policy first: for invoices, staleness is *correct* and the fix is documentation. Otherwise a fan-out repair job, idempotent (Lesson 17), rate-limited, with a progress checkpoint |
| **Schema drift in a schemaless store** — service B writes `qty` where service A writes `quantity` | Order totals wrong by a factor; or a blank page from an uncaught `TypeError` | Error-rate alert on the read path, often days or weeks after the bad write | Add a defensive read branch to unblock, then backfill-normalise the bad documents, then add a schema validator so it cannot recur. The backfill is the expensive part |
| **Dangling reference** — a book deleted, orders still reference it | "(deleted book)" or a crash on the order history page | Read-path null-check alarms; a referential-integrity audit job | Soft-delete instead of delete (an `is_active` flag). Repairing existing dangling refs needs a scan of every order |
| **Relational primary saturates on writes** | Page loads slow across the whole site; checkout times out; p99 spikes while averages look fine (Lesson 16) | `pg_stat_activity` showing waiting sessions; connection-pool exhaustion; lock-wait and WAL-write metrics | Immediately: kill long transactions, add missing indexes, move read traffic to replicas. Then: vertical scale, extract the hot workload, and only then shard (Lesson 36) |
| **Partial write in a NoSQL multi-step operation** — order written, stock never decremented | Oversold item; the customer gets a cancellation email days later | Inventory reconciliation job comparing stock against sum-of-orders; a spike in cancellations | A compensating transaction (the saga pattern — Phase 4). Prevention: make each step idempotent and driven by a durable work queue, not by in-process control flow |
| **Hot partition in a NoSQL store** — one celebrity book's key gets 40% of traffic | That book's page is slow or throttled while the rest of the site is fine | Per-partition throttle metrics; a long tail on one key | Short term: cache that key in Redis (Lesson 28). Long term: **key salting** — split the key into `book#X#0`…`book#X#9` and fan out reads. Lesson 43 §4 covers this |
| **Query plan regression** (relational) — a query that used an index starts scanning after statistics shift | One page becomes 30× slower overnight with no deploy | `pg_stat_statements` showing a mean-time jump for an unchanged query hash | `ANALYZE` the table; fix stale statistics; adjust the autovacuum threshold; in extremis, pin the plan or restructure the query |
| **Cross-store inconsistency** (polyglot) — order in PostgreSQL, event never reached Cassandra | Recommendations ignore a recent purchase | A count-comparison job between the two stores per time bucket | The outbox pattern: write the event into the *same* PostgreSQL transaction as the order, then relay it asynchronously with at-least-once delivery and idempotent consumers (Lesson 17) |

## 12. Alternatives

| Option | Data model | Scales writes horizontally | Joins | Multi-record ACID | Flexible schema | Best for | Main cost |
|---|---|---|---|---|---|---|---|
| **PostgreSQL / MySQL** | Relational (+ JSONB/JSON) | No (until sharded) | Yes | Yes | Partly, via JSONB | Almost every system's first and often only database | Single-primary write ceiling; migration discipline |
| **Key-value** (Redis, DynamoDB) | Opaque value by key | Yes | No | Single-item (or limited txn API) | Yes | Caches, sessions, carts, high-rate simple lookups | Query rigidity; no ad-hoc access (Lesson 43) |
| **Document** (MongoDB) | Nested JSON-like records, queryable inside | Yes | Limited (`$lookup`) | Yes, since 4.0, with caveats | Yes | Self-contained aggregates; content and catalogue data | Schema drift; denormalisation debt (Topic 5) |
| **Wide-column** (Cassandra, Scylla) | Partition key + sorted clustering columns | Yes, excellently | No | Row-level only (LWT via Paxos is slow) | Per-row columns | Time series, event logs, messages, huge write volume | Query patterns frozen at table-design time (Topic 6) |
| **Graph** (Neo4j) | Nodes and edges | Poorly | Traversal is native | Yes | Yes | Deep relationship traversal: recommendations, fraud rings | Scales vertically; niche expertise (Topic 8) |
| **NewSQL / distributed SQL** (CockroachDB, Spanner, Vitess) | Relational | Yes | Yes | Yes | Partly | Teams that genuinely need both SQL and horizontal writes | Higher write latency (consensus, typically 5–20 ms); cost; operational complexity |
| **Search engine** (Elasticsearch) | Inverted index over documents | Yes | No | No | Yes | Full-text relevance, faceting, analytics | Not a system of record; eventual consistency (Phase 4) |
| **Time-series** (TimescaleDB, InfluxDB) | Timestamped metric points | Yes | Timescale: yes (it is PostgreSQL) | Timescale: yes | Tags | Metrics, IoT, monitoring | Narrow fit outside time-ordered data |
| **Object store** (S3) | Blob by key | Yes (effectively unlimited) | No | No | N/A | Images, backups, data-lake files, large payloads | High latency (tens of ms); no query capability |
| **Just add an index / a cache** | — | — | — | — | — | The majority of "we need to migrate" situations | Requires measuring first, which teams skip |

## 13. When to use it

**Choose relational when:**

1. **Your access patterns are unknown, exploratory, or will change.** Any product still discovering what it is. Any system with a reporting or analytics consumer. Any system where "can you pull the numbers for X?" is a sentence someone will say. This is the strongest single signal.
2. **You need multi-entity atomic writes.** Orders and inventory. Account balances and ledger entries. Bookings and seat allocation. Anything where a half-completed operation costs money or trust.
3. **Data integrity must be enforced regardless of client.** Multiple services, batch jobs, and humans write to this data. Foreign keys and check constraints are the only validation that cannot be forgotten or bypassed.
4. **Your scale is anywhere below the §9 numbers** — which, measured honestly, is most systems.
5. **Your team knows relational databases.** Operability at 3 a.m. is an architectural property.

**Choose NoSQL when:**

1. **Your write rate genuinely exceeds a single primary and you have measured it.** Event streams, telemetry, messaging, IoT ingest — thousands to millions of writes per second.
2. **You have a small, stable, enumerable set of access patterns.** Session lookup by session ID. Cart by customer ID. Recent messages by channel. You can write the complete list on one page and defend it.
3. **The data is a self-contained aggregate always read whole.** A user's preferences blob, a rendered page fragment, a shopping cart.
4. **You need predictable sub-millisecond latency.** Only an in-memory key-value store delivers this (Lesson 43).
5. **The data is genuinely heterogeneous in shape** and a JSONB column in a relational table has been tried and found wanting.
6. **The data is disposable or reconstructible.** Caches, derived indexes, analytics events. Weaker durability guarantees cost less when the data can be regenerated.

## 14. When not to use it

**Do not choose NoSQL when:**

1. **Your reason is "we have a lot of data."** Re-read §9. Then measure your actual write rate and working-set size. The threshold is 64× higher than most teams assume.
2. **You cannot list your access patterns.** If you cannot enumerate them today, you will design the wrong key, and the cost of a wrong key design is a full migration rather than a `CREATE INDEX`.
3. **You need transactions across entities and plan to "handle it in application code."** You will not. Distributed atomicity by hand is a research-grade problem that looks easy for the first six months.
4. **You are copying a company 1,000× your size.** Discord's choices follow from trillions of messages. Your 40,000 orders/day do not share that constraint. Copying the solution without the constraint imports all the cost and none of the benefit.
5. **The motivation is résumé-driven or stack-uniformity.** "One database technology everywhere" is a reasonable goal; making it Cassandra for a transactional workload is not.
6. **Reporting and analytics are first-class requirements** and nobody has planned how those queries will run.

**Do not choose relational when:**

1. **You genuinely need writes beyond one primary, now**, and distributed SQL's latency or cost is unacceptable.
2. **The workload is a pure cache or session store.** Putting sessions in PostgreSQL works but wastes the primary's write capacity on disposable data; a key-value store is simply the right tool (Lesson 43).
3. **The access pattern is a single-key blob fetch at very high rate with a hard sub-millisecond p99.** No disk-backed relational engine delivers that.

## 15. Common mistakes

**Beginner mistakes**

1. **Treating "SQL vs NoSQL" as one binary decision.** There are at least six categories with incompatible trade-offs. "NoSQL" as a choice is as meaningful as "a vehicle".
2. **Believing "schemaless" means no schema.** It means the schema lives in your code, undocumented and unenforced, in several versions at once.
3. **Choosing on data volume rather than access patterns.** §9 exists to make this impossible to justify.
4. **Thinking NoSQL is faster.** It is faster *for the pattern it was modelled for*. For anything else it is dramatically slower, because "anything else" means a full scan.
5. **Denormalising without a plan for keeping copies in step.** Duplication is a commitment to a fan-out update path, forever.
6. **Not knowing PostgreSQL has JSONB.** Teams migrate to MongoDB for schema flexibility they already had in a feature they had not read about.
7. **Using a cache as a database.** A Redis instance without persistence configured will lose everything on restart. Lesson 43 covers exactly what RDB and AOF do and do not guarantee.
8. **Adding a second datastore before exhausting the first.** Each store multiplies the operational surface (§4).

**Experienced-engineer mistakes**

1. **Underestimating how long access patterns stay stable.** A senior engineer enumerates seven access patterns correctly. Eighteen months later there are twenty-two, and six of them cut across the partition key. The forecast was accurate for the product as it existed and wrong about the product as it became.
2. **Polyglot persistence by accretion rather than decision.** Each store was individually justified. Nobody tracked the running total, and now five people are on call for nine technologies.
3. **Building a half-relational layer on top of a key-value store.** An in-house query planner, an in-house index maintainer, an in-house transaction coordinator — each a worse version of something PostgreSQL ships, maintained by three people instead of three hundred.
4. **Ignoring the write amplification of secondary indexes in NoSQL.** A DynamoDB global secondary index is a full second copy of the table with its own write costs. Four GSIs means roughly 5× the write cost. Lesson 43 §9 works through this.
5. **Assuming "eventually consistent" is a small window.** It is usually milliseconds; under partition or replica lag it is minutes. Your code must be correct for the worst case, not the median (Lesson 33).
6. **Sharding a relational database when extracting one workload would have sufficed.** §7's BookNook decision — moving events out — is far cheaper than sharding orders, and buys the same relief.
7. **Dismissing distributed SQL out of 2013 reflexes.** CockroachDB and Spanner invalidate the classic "you must give up SQL to scale" premise. The trade is now latency and cost, not capability.
8. **Treating the data model as reversible.** Code is cheap to change; a 40-million-record key design is not. Of everything you decide in the first month, the data model has the longest half-life.

## 16. Interview perspective

**What is actually being tested.** Not whether you can recite "MongoDB is a document store". The interviewer wants to see whether you reason from *requirements* to *data model* to *engine*, in that order, and whether you know what each choice costs.

**The structure that scores well:**

1. **Enumerate the access patterns first.** "Before I pick a database, let me list what we read and write: place order (multi-entity, atomic), order history by customer (recent-first, paginated), search by title and author (full text), browse by category with facets, nightly revenue reporting."
2. **Classify each by consistency, latency, volume, and query shape.**
3. **Pick per workload, not per system.** "Orders go in PostgreSQL — transactions and unknown reporting queries. Sessions go in Redis — disposable, sub-millisecond, high rate. Search goes in Elasticsearch — relevance ranking is not a SQL `LIKE`."
4. **State the cost of every choice out loud.** "Elasticsearch means the index lags the database by a few seconds, so a seller editing a title will not see it in search immediately. I'd surface the edit optimistically in their own view."
5. **Give numbers.** "At 40,000 orders a day, peak 120 writes a second, one PostgreSQL primary is at about 1.5% of capacity. We don't need to shard; we need an index and a cache."

**Strong phrases that signal depth:**
- *"Relational defers the query decision; NoSQL makes it at design time. I'd defer while requirements are still moving."*
- *"Schemaless just moves the schema into application code. I'd pay that cost only for genuinely heterogeneous data."*
- *"Before migrating I'd check for a missing index, N+1 queries, and connection pooling — those explain most slow databases."*
- *"PostgreSQL JSONB gives me document flexibility without giving up joins or transactions. I'd try that before adding a second datastore."*
- *"Each additional datastore adds backup, monitoring, on-call, and a consistency boundary. It has to earn that."*

**Red flags interviewers watch for:**

- Saying **"NoSQL scales better"** with no numbers and no qualifier about *writes* specifically.
- Choosing a database **before** stating the access patterns.
- Answering **"it depends"** without immediately saying *on what* — on write rate, on whether access patterns are stable, on whether multi-entity atomicity is required.
- Claiming **"relational databases can't handle big data"** when a single instance manages tens of terabytes.
- Proposing **five datastores for a system with 10,000 users**, with no mention of operational cost.
- Saying **"MongoDB doesn't support transactions"** (false since 4.0) or **"NoSQL means no schema"** (false in the way that matters) — both signal knowledge frozen around 2014.
- Not knowing **what you give up**. If you propose Cassandra and cannot explain that you lose joins, lose ad-hoc queries, and get only row-level atomicity, you have not made a decision — you have made a guess.
- Treating **eventual consistency as free**, with no user-visible scenario described.
- **No migration or rollback story.** "We'd move to DynamoDB" with no answer to how 40 million existing rows get there while the site stays up.

## 17. Knowledge check

**Conceptual**

1. "Schemaless" databases do not remove the schema — they move it. Explain where the schema goes, name three specific failure modes this creates, and describe one mitigation for each. For each mitigation, say whether it is re-implementing something a relational database already provides.

2. A colleague says: "We should move to Cassandra because our PostgreSQL database has 400 GB of data and it's getting slow." Identify everything wrong with the reasoning in that sentence, and list — in order — the five things you would investigate before agreeing to any migration. Explain why "400 GB" is or is not relevant evidence.

3. Explain precisely why losing joins is more consequential than losing SQL-the-language. Use BookNook's `books` and `authors` tables to show what a single author-rename costs in a normalised relational model versus a denormalised document model, and state what guarantee is lost in the second case.

**Practical scenario**

4. BookNook is adding a **wishlist** feature. Requirements: a customer has one wishlist holding up to 500 books; the wishlist page shows cover, title, current price and stock status, sorted by the date added; customers add and remove items frequently (roughly 8 writes per active customer per week); a weekly batch job emails customers when a wishlisted book drops in price; and marketing wants to ask, ad hoc, "which books appear on the most wishlists in the under-25 age bracket?" There are 850,000 customers, of whom about 120,000 keep a wishlist.

   Design the storage for this. Specify: which engine (or engines), the exact data model including keys and indexes, how each of the four access patterns is served, what you denormalise and what that commits you to, and what breaks if marketing's ad-hoc question changes next month. Justify each decision against a stated requirement, not against a general preference.

**Calculation**

5. BookNook's traffic grows 3× per year. Today: 40,000 orders/day, peak 120 write transactions/second, working set 10.1 GB, one 32 vCPU / 256 GB primary with a measured ceiling of 7,680 write txn/s and 179 GB of usable buffer pool (all from §9).

   Calculate, showing every step and stating every assumption you add:
   (a) In which year does peak write rate first exceed the primary's ceiling?
   (b) In which year does the working set first exceed the usable buffer pool, assuming the working set grows proportionally with traffic?
   (c) If vertical scaling to 96 vCPU / 768 GB gives 3× the write ceiling and 3× the buffer pool, how many additional years does that buy for each limit?
   (d) Given your answers, state when you would *begin* the work to shard or migrate, and justify the lead time you chose.

**Progress checklist**

- [x] **Phase 1 — Foundations (Lessons 1–17): complete**
- [x] **Phase 2 — Scaling (Lessons 18–39): complete**
- **Phase 3 — Databases & Storage**
  - [x] Topic 1 — Relational databases: the model (Lesson 40)
  - [x] Topic 2 — Tables, keys, joins and constraints (Lesson 41)
  - [x] Topic 3 — SQL versus NoSQL (Lesson 42) ← *you are here*
  - [ ] Topic 4 — Key-value databases (Lesson 43)
  - [ ] Topic 5 — Document databases
  - [ ] Topic 6 — Wide-column databases
  - [ ] Topic 7 — ACID and isolation levels
  - [ ] Topic 8 — Graph databases
  - [ ] Topic 9 — Indexing: B-trees and LSM-trees
  - [ ] Topic 10 — Query planning and optimisation
  - [ ] Topic 11 — Normalisation and denormalisation
  - [ ] Topic 12 — Transactions in distributed systems
  - [ ] Topic 13 — Object and blob storage
  - [ ] Topic 14 — Time-series databases
  - [ ] Topic 15 — Search engines and inverted indexes
  - [ ] Topic 16 — Data warehouses and OLAP
  - [ ] Topic 17 — Change data capture
  - [ ] Topic 18 — Backups and point-in-time recovery
  - [ ] Topic 19 — Schema migrations at scale
  - [ ] Topic 20 — Connection pooling and database proxies
  - [ ] Topic 21 — Multi-tenancy patterns
  - [ ] Topic 22 — Data retention and archival
  - [ ] Topic 23 — Encryption at rest and in transit
  - [ ] Topic 24 — Choosing a storage architecture

**NEXT LESSON: Phase 3, Topic 4 — Key-value databases**

---

# Lesson 43 — Key-Value Databases

*Phase 3, Topic 4*

## 1. Simple definition

A **key-value database** (or key-value store, KV store) is a database with the simplest possible data model: an enormous, distributed, persistent dictionary. You give it a **key** — a unique string or binary identifier — and it gives you back the **value** you previously stored under that key.

```
PUT  "cart#4471"  →  {"items":[{"isbn":"978...","qty":2}]}
GET  "cart#4471"  →  {"items":[{"isbn":"978...","qty":2}]}
DEL  "cart#4471"  →  (gone)
```

Terms defined before use:
- **Key**: the unique identifier. The *only* way to address data. Typically a string, often structured by convention (`cart#4471`, `session:ab12cd`).
- **Value**: the stored payload. In the purest stores it is **opaque** — the database treats it as a bag of bytes and has no idea what is inside. This is the defining characteristic.
- **Opaque**: the database cannot read into the value, cannot index it, cannot filter on it. `WHERE value.total > 50` is not a thing that exists.
- **Scan** (where supported): iterate keys in some order, usually only within one partition.
- **Partition** (or shard): one subset of the keyspace living on one set of nodes. Lesson 36 introduced the concept; Lesson 38 covered consistent hashing.

> **The one-line idea: a key-value store is a hash table that survives a power cut and spans a hundred machines.**

## 2. Why it exists

### Constraining the interface is what buys the performance

This is the central insight of the lesson, and it is counter-intuitive, so it is worth stating carefully.

A relational database supports arbitrary queries. To do that, it must carry machinery: a parser, a cost-based optimiser, table statistics, multiple join algorithms, a lock manager for arbitrary-scope transactions, MVCC version chains, a buffer pool with complex eviction. Every one of those is a per-query cost and, more importantly, a source of *variance* — the same query can take 0.3 ms or 300 ms depending on the plan, the cache state, and what else is running.

A key-value store supports exactly one access path: by key. That single constraint deletes the entire list above.

| Machinery | Needed by relational | Needed by key-value |
|---|---|---|
| SQL parser | Yes | No |
| Cost-based optimiser | Yes | No — there is one possible plan |
| Table statistics | Yes | No |
| Join algorithms | Yes | No |
| Lock manager for arbitrary scopes | Yes | No — atomicity is per key |
| Routing logic | Simple (one node) | `hash(key) → partition` — O(1), no coordination |

What remains is: hash the key, find the partition, look it up. That is why a key-value store can promise **single-digit-millisecond latency regardless of dataset size** — the work for one lookup does not grow with the number of items, and there is no plan that can regress.

And the same constraint is what makes horizontal scaling trivial. Because every operation touches exactly one key, and every key belongs to exactly one partition, **no operation ever needs two nodes to coordinate.** Add nodes, move partitions, done. A relational database cannot do this because a join may touch any two rows, which may be on any two nodes.

> **You are not buying a faster database. You are buying a database that was allowed to be fast because you promised never to ask it a hard question.**

### What problem it was built for

Three recurring shapes in real systems:

1. **Session storage.** HTTP is stateless (Lesson 2). A logged-in user needs server-side state keyed by a session ID. You *only* ever fetch by session ID. You never ask "all sessions where the user is in Leeds". Storing this in a relational primary wastes its write capacity on data that is disposable by design.
2. **Caching.** Lessons 27–30 established the pattern. A cache is definitionally a key-value store: a key (the cache key) and a value (the cached result).
3. **High-volume simple lookups.** Feature flags, rate-limit counters, user preferences, shopping carts, idempotency keys (Lesson 17), leaderboards, deduplication sets. All single-key, all high rate, none needing joins.

## 3. Real-world analogy

**A left-luggage office at a railway station versus a library.**

- **The left-luggage office is a key-value store.** You hand over a bag; you get a numbered ticket. Present the ticket, get the bag back. The transaction takes ten seconds *whether the office holds 50 bags or 50,000*, because the attendant walks to shelf position N — the search does not get harder as the office fills. The attendant has no idea what is in your bag and will not tell you which bags contain cameras. You cannot ask for "all bags deposited on Tuesday". Lose the ticket and the bag is effectively unreachable.

- **The library is a relational database.** You can find a book by title, by author, by subject, by publication year, by "books cited by this book", or by browsing the shelves. The catalogue makes any of these possible. But every book must be catalogued on arrival — a slower, more disciplined intake process — and a very large library needs a very large building.

- **Memcached is a left-luggage office with a strict policy**: when shelves fill, the oldest unclaimed bags are thrown away without notice, and if the office closes overnight everything is discarded. Perfect for coats at a one-day conference. Not for your passport.

- **Redis is the same office, but the attendant has learned a few specialised tricks**: they will keep your items in a sorted order, maintain a running count, or hold a queue of parcels for collection in order. Still ticket-based, but the attendant does a little processing for you.

- **DynamoDB is a chain of thousands of these offices**, with a rule that your ticket number determines which branch holds your bag. Capacity is effectively unlimited, but if everyone in the country happens to get a ticket routed to the Clapham branch, that branch has a queue while the rest stand idle.

**Where the analogy breaks down:**

| Left-luggage office | Real key-value store |
|---|---|
| The attendant could, if asked nicely, open bags and look | The store genuinely cannot see inside an opaque value. There is no "ask nicely" — filtering means transferring every value to your application and filtering there, which is catastrophically expensive |
| One office, one attendant, one queue | Partitioned across many nodes with replicas (Lesson 33). A write may be acknowledged by one replica and not yet visible on another — "put then get" can return the *old* value |
| A bag is a bag | Values have hard size limits: 400 KB per item in DynamoDB, 512 MB per value in Redis but anything above ~100 KB is an operational hazard |
| Bags sit there costing nothing | In-memory stores cost RAM continuously. 100 GB in Redis is roughly 50× the price of 100 GB on disk |
| The office never forgets a bag you paid for | Redis can be configured to **evict** data under memory pressure even when you intended it to be durable. §11 covers this; it is the single most common production surprise |
| The ticket is the only index, and that is fine | Real systems eventually want a second access path. Secondary indexes exist (DynamoDB GSI/LSI) but they are *separate copies* with their own cost and their own consistency lag |
| A busy branch just has a longer queue | A hot partition is *throttled*: DynamoDB returns an error, not merely a delay. Your code must handle rejection, not just latency |

## 4. Detailed technical explanation

### The minimal API, precisely

| Operation | Semantics | Universally available |
|---|---|---|
| `GET(key)` | Return the value, or a not-found signal | Yes |
| `PUT(key, value)` | Store, overwriting any existing value | Yes |
| `DELETE(key)` | Remove the key | Yes |
| `GET(key) with TTL` / `EXPIRE` | Auto-delete after N seconds | Almost always |
| **Conditional put** (compare-and-set) | Write only if the current value/version matches | Usually — essential for correctness |
| **Atomic counter** (`INCR`, `ADD`) | Increment server-side without read-modify-write | Usually |
| `SCAN` / `Query` | Iterate keys, typically only within one partition or by prefix | Sometimes, with caveats |
| **Batch get/put** | Multiple keys in one round trip | Usually |

Two of these deserve emphasis.

**Conditional put is what makes correctness possible without transactions.** Without it, "read the cart, add an item, write the cart" from two devices loses one update. With it, you write only if the version you read is still current; otherwise you retry. This is **optimistic concurrency control**, and in a key-value store it is your main concurrency primitive.

**`SCAN` is a trap in most stores.** Redis `KEYS *` is O(n) and blocks the entire server (§4, single-threaded model). Redis `SCAN` is cursor-based and safe but gives no consistency guarantee across the iteration. DynamoDB `Scan` reads the whole table and bills you for all of it. **If your design needs a scan, your key design is wrong.**

### Partitioning by key hash — connecting to Lesson 38

Every distributed key-value store answers "which node holds this key?" the same way in outline:

```
node = ring_lookup( hash(key) )
```

Lesson 38 covered **consistent hashing**: hashing both keys and nodes onto a ring so that adding or removing a node remaps only `1/N` of keys rather than nearly all of them (the `% N` catastrophe from Lesson 23). Two refinements matter here:

- **Virtual nodes.** Each physical node owns many points on the ring (typically 128–256). This smooths the distribution — without it, a 3-node ring can easily end up 50/30/20 rather than 33/33/33 — and makes rebalancing incremental.
- **Replication factor.** The key is stored on the node that owns its ring position *and* the next N−1 distinct nodes clockwise. With RF=3, losing one node loses no data.

**Hash slots** (Redis Cluster's variant) are a simpler fixed-bucket scheme: exactly **16,384 slots**, `slot = CRC16(key) mod 16384`, and each node owns a contiguous set of slot numbers. Rebalancing moves whole slots between nodes. Fewer moving parts than a ring with virtual nodes; the cost is a fixed upper bound on cluster size (you cannot usefully have more than 16,384 primaries).

**The consequence for your key design is absolute:** keys that must be accessed together must hash to the same partition, or the operation spans nodes and is either forbidden or slow. Redis solves this with **hash tags** — only the substring inside `{}` is hashed, so `user:{4471}:cart` and `user:{4471}:prefs` land on the same node and can be touched by one atomic script.

### In-memory versus on-disk

| | In-memory (Redis, Memcached) | On-disk / SSD-backed (DynamoDB, RocksDB, Aerospike) |
|---|---|---|
| Read latency | 0.1–1 ms (dominated by network, not lookup) | 1–10 ms |
| Cost per GB/month | ~£8–15 (RAM) | ~£0.20–0.30 (SSD) — roughly **50×** cheaper |
| Capacity ceiling | Node RAM; clusters reach low TB | Effectively unbounded |
| Durability | Configurable, with a real loss window (below) | Durable by default, replicated |
| Failure on restart | Must reload from disk or source; a cold cache can stampede the database | Transparent |
| Right for | Caches, sessions, counters, queues, leaderboards | Systems of record, large datasets, anything you cannot regenerate |

The 50× cost ratio is the thing to internalise. Holding 2 TB in Redis is a five-figure monthly bill; holding it in DynamoDB is a three-figure one. **In-memory is for the working set, not the dataset.**

---

### Redis in depth

**Redis** (Remote Dictionary Server) is an in-memory data-structure server. The distinction from "key-value store" is deliberate: the *value* is not opaque. Redis knows the value's type and offers type-specific operations executed **server-side**, which eliminates round trips.

#### Data structures, each with a BookNook use

**1. Strings** — bytes, up to 512 MB. Also the atomic-counter type.
```
SET  book:9780141036144:title "Mythos"
INCR book:9780141036144:views          # atomic; no read-modify-write race
```
*BookNook use:* cached rendered HTML fragments for book pages; page-view counters; rate-limit counters keyed by IP.

**2. Hashes** — a map of fields to values under one key.
```
HSET cart:4471 9780141036144 2  9780718188733 1
HINCRBY cart:4471 9780141036144 1      # change one item without rewriting the cart
```
*BookNook use:* shopping carts. Critical advantage over a JSON string: adding one item is `HINCRBY` — one field write, atomic, no read-modify-write. With a JSON string you must GET the whole cart, parse, modify, re-serialise, SET — and two concurrent tabs lose an update.

**3. Lists** — ordered sequence, push/pop at either end, O(1).
```
LPUSH  recent:viewed:4471 9780141036144   # newest at the head
LTRIM  recent:viewed:4471 0 19            # keep only 20; bounded memory, no cleanup job
LRANGE recent:viewed:4471 0 9             # the last 10 viewed
```
*BookNook use:* "recently viewed books" per customer; a simple job queue for sending order-confirmation emails (`RPOPLPUSH` gives at-least-once semantics with a processing list).

**4. Sets** — unordered, unique members, with set algebra server-side.
```
SADD  wishlist:4471 9780141036144
SADD  wishlist:9902 9780141036144
SINTER wishlist:4471 wishlist:9902        # books both customers want — computed IN Redis
SCARD wishlist:4471                       # size, O(1)
```
*BookNook use:* wishlists; tag membership ("books tagged mythology"); deduplication of processed event IDs.

**5. Sorted sets (ZSET)** — unique members each with a float **score**, kept in score order. O(log n) insert, O(log n + m) range read.
```
ZINCRBY bestsellers:2026-10 1 9780141036144   # +1 sale, re-sorts automatically
ZREVRANGE bestsellers:2026-10 0 9 WITHSCORES  # top 10, already sorted
ZRANGEBYSCORE price:fiction 500 1500          # books priced £5.00–£15.00
```
*BookNook use:* the bestseller leaderboard, maintained live at zero query cost — the alternative is `SELECT ... GROUP BY ... ORDER BY COUNT(*) DESC` over millions of order rows. Also delayed-job scheduling with the score as a timestamp.

**6. Streams** — an append-only log with consumer groups, offsets, and acknowledgements.
```
XADD  events * type page_view isbn 9780141036144 cust 4471
XREADGROUP GROUP recommender worker-1 COUNT 100 STREAMS events >
```
*BookNook use:* the browsing-event pipeline from Lesson 42 §7, buffered before it reaches Cassandra. Unlike lists, streams keep per-consumer-group offsets and track un-acknowledged messages, so a crashed worker's messages can be reclaimed.

**7. HyperLogLog** — probabilistic distinct-count in a fixed **12 KB** per counter, ~0.81% standard error.
```
PFADD   uniques:2026-10-09 cust:4471 cust:9902
PFCOUNT uniques:2026-10-09              # approximate distinct count
PFMERGE uniques:week uniques:2026-10-09 uniques:2026-10-08
```
*BookNook use:* "unique visitors today" and "unique viewers per book". Exact counting of 50 million distinct IDs with a Set costs roughly 50 M × ~70 B ≈ **3.5 GB**; HyperLogLog costs **12 KB**. A 290,000× saving in exchange for ±0.81% — an excellent trade for a dashboard, a terrible one for billing.

**8. Bitmaps** — bit operations on a string, addressed by offset.
```
SETBIT  active:2026-10-09 4471 1        # customer 4471 was active today
BITCOUNT active:2026-10-09              # how many active today
BITOP AND active:both active:2026-10-09 active:2026-10-08  # active both days
```
*BookNook use:* daily-active-customer tracking and retention cohorts. 850,000 customers = 850,000 bits = **~104 KB per day**, exact. Also feature-flag membership.

Two more worth naming for completeness and forward reference: **Geospatial** commands (`GEOADD`, `GEOSEARCH`) built on sorted sets, useful for "bookshops near me"; and **Bloom filters** via the RedisBloom module — a probabilistic set-membership structure with no false negatives, covered properly in Phase 3 Topic 9 alongside LSM-tree indexing.

#### The single-threaded execution model

Redis executes commands on **one thread**, one at a time, to completion. (Redis 6+ uses extra threads for network I/O, and 7+ can offload some background work, but *command execution* remains single-threaded.)

Why this is a feature:
- **Every command is atomic by construction.** No locks, no race conditions, no mutex overhead. `INCR` cannot interleave with another `INCR`.
- **No context switching** between commands; no lock contention; excellent CPU-cache locality.
- **Predictable behaviour.** The ordering you observe is the ordering that happened.

The implications you must design around:
- **One slow command blocks everything.** `KEYS *` on 10 million keys takes seconds, during which *every other client is stalled*. So does a 200 MB `LRANGE`, a `SMEMBERS` on a million-member set, or a `FLUSHALL`. Use `SCAN`, `HSCAN`, `SSCAN`, and `ZSCAN` — cursor-based, O(1) per call. **Never run `KEYS` against production.**
- **One core per instance.** A 32-core machine running one Redis process uses one core. To use the machine, run multiple instances (or Redis Cluster) and shard across them.
- **Throughput ceiling is per-instance**, typically **80,000–150,000 simple operations/second** on modern hardware, far more with pipelining.
- **Deleting a huge key stalls the server.** `DEL` on a 5 GB hash frees millions of allocations synchronously. Use `UNLINK`, which reclaims memory in a background thread.

#### Persistence: RDB versus AOF, and their distinct durability windows

Redis is in-memory. Persistence exists so that a restart does not lose everything — but **both mechanisms have a loss window, and you must know which one you have accepted.**

**RDB (Redis Database file) — point-in-time snapshots.**
- On a schedule (`save 900 1` = snapshot if ≥1 key changed in 900 s), Redis `fork()`s. The child writes the whole dataset to a compact binary file while the parent keeps serving.
- *Pros:* compact; fast to load on restart; near-zero steady-state cost; ideal for backups.
- *Cons:* **you lose everything written since the last snapshot.** With a 5-minute schedule, that is a 5-minute loss window. The `fork()` also transiently needs extra memory — copy-on-write means a write-heavy instance can approach **2× its dataset size** in RAM during the snapshot, and on a 100 GB instance the fork itself can pause the server for tens to hundreds of milliseconds.

**AOF (Append-Only File) — a log of every write command.**
- Every mutating command is appended to a log. On restart the log is replayed. `BGREWRITEAOF` periodically compacts it.
- The loss window is governed entirely by `appendfsync`:

| `appendfsync` | Behaviour | Loss window | Throughput cost |
|---|---|---|---|
| `always` | fsync after every write command | ~0 (one command) | Severe — ~1,000s of writes/s, not 100,000s |
| `everysec` *(default)* | fsync once per second | **up to ~1 second (often quoted as up to 2 s under load)** | Small |
| `no` | let the OS flush | up to ~30 s (OS-dependent) | None |

- *Pros:* much smaller loss window; the log is human-readable and recoverable.
- *Cons:* larger files; slower restart (replaying a log versus loading a snapshot).

**Production reality, stated plainly:**
- **Both enabled** is the common configuration: AOF `everysec` for the small loss window, RDB for fast restarts and cheap backups. On restart Redis prefers the AOF.
- **Even `appendfsync always` does not make Redis a durable system of record on a single node** — the acknowledgement is returned before replication, so a node loss after the ACK still loses the write. Redis replication is **asynchronous**: `WAIT numreplicas timeout` lets you block until N replicas have the write, which narrows but does not close the gap.
- **The teaching simplification is "Redis can be persistent." The production reality is "Redis has a configurable loss window, and the default is one second."** If losing one second of writes is unacceptable — payments, orders, ledgers — Redis is the wrong system of record. That is not a criticism of Redis; it is a statement about what it optimises.

#### Redis Cluster and hash slots

A single instance is limited by one core and one machine's RAM. **Redis Cluster** shards across primaries.

- **16,384 hash slots.** `slot = CRC16(key) mod 16384`. Each primary owns a contiguous range.
- Clients are **cluster-aware**: they cache the slot→node map. A request to the wrong node returns a `MOVED` redirect (permanent remap) or `ASK` (slot currently migrating); the client follows it and updates its map.
- Each primary has one or more **replicas**; failover is automatic via a gossip protocol and a node-majority vote.
- **Multi-key operations must stay within one slot.** `MGET a b c` fails with `CROSSSLOT` unless all three hash to the same slot. **Hash tags** force co-location: `{4471}:cart` and `{4471}:prefs` share a slot because only `4471` is hashed.
- **No cross-slot transactions.** `MULTI/EXEC` and Lua scripts are confined to a single slot.
- Rebalancing migrates whole slots, key by key, while serving — online but not free.

#### Lua scripting for atomicity

Many operations are read-modify-write, which is a race across round trips. Redis runs a **Lua script atomically** — the single-threaded model guarantees nothing interleaves.

```lua
-- Reserve stock only if enough remains. The check and the decrement are ONE atomic step.
-- KEYS[1] = stock key, ARGV[1] = quantity requested
local stock = tonumber(redis.call('GET', KEYS[1]) or '0')  -- read current stock
if stock >= tonumber(ARGV[1]) then                          -- check inside the same atomic unit
  redis.call('DECRBY', KEYS[1], ARGV[1])                    -- decrement
  return 1                                                  -- reserved
else
  return 0                                                  -- insufficient stock
end
```

Rules: scripts must be **deterministic** (no `TIME`, no unseeded `math.random` affecting writes) because they may be replicated verbatim; all keys must be declared in `KEYS` so Cluster can verify slot co-location; and **scripts must be short**, because a slow script blocks the entire server. Redis 7 adds **Functions**, which are persisted server-side rather than cached by SHA, removing the `NOSCRIPT`-after-restart failure mode.

---

### DynamoDB in depth

**Amazon DynamoDB** is a fully managed, SSD-backed, horizontally partitioned key-value and document store. It is the commercial descendant of the 2007 Dynamo paper that also gave us consistent hashing (Lesson 38). Its defining property: **latency does not degrade as the table grows** — 10 GB and 100 TB have the same single-digit-millisecond read latency.

#### Partition key and sort key

Every item has a **primary key**, in one of two forms:

- **Simple**: a **partition key** (PK) alone, which must be unique. `hash(PK)` determines the physical partition.
- **Composite**: a **partition key** plus a **sort key** (SK). The PK selects the partition; items within it are stored **sorted by SK**. Uniqueness is on the pair.

The composite form is what makes DynamoDB more than a dictionary, because it enables **range queries within a partition**:

```
PK = "CUST#4471", SK = "ORDER#2026-10-09#88123"
PK = "CUST#4471", SK = "ORDER#2026-10-07#88044"
PK = "CUST#4471", SK = "PROFILE"

Query: PK = "CUST#4471" AND SK begins_with "ORDER#"   → that customer's orders, newest-first
```

This is the basis of **single-table design**, where many entity types share one table with carefully constructed keys. It is powerful and genuinely difficult; it requires the complete access-pattern list from Lesson 42 §4 before you write a line of code.

**What you cannot do:** query without the partition key. `WHERE total > 50` across the table is a `Scan` — reads and bills for every item.

#### Item size limit

**400 KB per item**, including attribute names. This is a hard limit and it shapes design:
- Large payloads go in S3 with the key stored in the item (the **pointer pattern**).
- One-to-many relationships cannot grow without bound inside one item. A customer's order list as an array will eventually exceed 400 KB. Use separate items with a composite key instead.
- Attribute names count toward the limit, so `qty` beats `quantity_ordered` at scale. This genuinely matters at a billion items.

#### Capacity: provisioned versus on-demand, and the unit arithmetic

DynamoDB bills by **capacity units**, and the arithmetic is simple but must be learned exactly.

**Read Capacity Unit (RCU)** — per second:
- 1 RCU = **one strongly consistent read of an item up to 4 KB**.
- 1 RCU = **two eventually consistent reads** of up to 4 KB (half price).
- 1 RCU = **half a transactional read** (transactional reads cost 2×).
- Items larger than 4 KB round **up** to the next 4 KB boundary: a 9 KB item is `ceil(9/4) = 3` RCUs strongly consistent.

**Write Capacity Unit (WCU)** — per second:
- 1 WCU = **one write of an item up to 1 KB**.
- Rounds up per 1 KB: a 2.5 KB item costs `ceil(2.5/1) = 3` WCUs.
- Transactional writes cost **2×**.

**Provisioned capacity:** you declare RCU/WCU; you pay for them whether used or not; exceeding them causes **throttling** (`ProvisionedThroughputExceededException`). **Auto-scaling** adjusts the provision in response to utilisation, but it reacts over minutes — it does not protect you from a sudden spike.

**On-demand capacity:** no provisioning; pay per request at roughly **6–7× the per-request price** of fully-utilised provisioned capacity. It absorbs spikes instantly (within limits).

The decision rule, stated precisely rather than as "it depends": **if your sustained utilisation of provisioned capacity would exceed roughly 15–20%, provisioned is cheaper; below that, on-demand is cheaper.** Spiky, unpredictable, or new workloads go on-demand; steady, well-understood ones go provisioned with auto-scaling and a floor.

#### Hot partitions

Capacity is divided among partitions. Historically each partition got `total_capacity / partition_count`, so one popular key could be throttled while the table sat at 10% utilisation overall.

Modern DynamoDB mitigates this with **adaptive capacity** (shifting capacity toward busy partitions, within minutes) and **instant adaptive capacity** (faster, automatic). But hard limits remain:
- **3,000 RCU and 1,000 WCU per partition**, absolute.
- A **single item** cannot exceed that. If one BookNook book page gets 5,000 reads/second, no amount of table capacity helps.

Fixes, in order of preference:
1. **Cache the hot key** in Redis or DAX (DynamoDB Accelerator, a managed write-through cache) — Lesson 28.
2. **Write sharding / key salting**: store the item as `BOOK#9780…#0` … `BOOK#9780…#9`, write to a random suffix, read all ten and merge. Ten partitions, ten times the ceiling, ten times the read cost.
3. **Re-model** so the hot entity is naturally split (for example by date).

#### Secondary indexes

| | **LSI** (Local Secondary Index) | **GSI** (Global Secondary Index) |
|---|---|---|
| Partition key | **Same as the table** | **Any attribute** |
| Sort key | Alternative attribute | Any attribute |
| When created | **Table creation only** — never after | Any time |
| Consistency | Strong reads available | **Eventually consistent only** |
| Capacity | Shares the table's | **Its own RCU/WCU** |
| Limit | 5 per table; 10 GB per partition-key value | 20 per table (default) |

**The cost that surprises people:** a GSI is a *separate copy of the projected attributes*. Every write to the base table that touches a projected attribute also writes to every matching GSI, billed separately. **Four GSIs means roughly 5× the write cost.** §9 works this through with numbers. GSIs are also eventually consistent — typically under a second, but "write then immediately read the GSI" can miss.

---

### Memcached, and when it is still right

**Memcached** predates Redis (2003). It stores opaque byte blobs by key, in RAM, with LRU eviction, and nothing else. No persistence. No data structures. No replication. No cluster coordination — clients shard by hashing, consistently, on the client side.

It is still the right choice when **all** of these hold:
1. The data is a **pure, regenerable cache**. Losing all of it means a slower minute, not an incident.
2. You need **only** get/set/delete on opaque blobs.
3. You want **multi-threaded** scaling within one node. Memcached uses all cores, so one large node can exceed a single Redis instance's throughput without cluster complexity.
4. You want **minimal operational surface** — it does almost nothing, so almost nothing can go wrong.
5. **Memory efficiency for uniform objects** matters; its slab allocator has lower per-key overhead than Redis's (~50–70 bytes per Redis key of metadata).

Choose Redis instead when you want data structures, persistence, replication, pub/sub, streams, Lua, or TTL semantics richer than "evict when full". In 2026 that is most of the time — but "Memcached is obsolete" is wrong, and saying so in an interview is a tell.

### When a key-value store is being misused as a primary database

Specific symptoms, each diagnostic:

1. **You are maintaining your own secondary indexes by hand.** Writing `email:alice@example.com → cust#4471` alongside `cust#4471 → {...}` and keeping them in step in application code. You have built an index maintainer with no transactions. It *will* drift.
2. **You are scanning.** Any `SCAN`, `KEYS`, or DynamoDB `Scan` in a request path means the key design does not serve the access pattern.
3. **You are joining in application code on every request.** Lesson 42 §5 Path B — five round trips for one page.
4. **You need an atomic multi-key write and are "handling it carefully".** You are not. See Lesson 42 §11.
5. **You are storing the only copy of data you cannot regenerate, in Redis.** With the default `everysec` AOF you have accepted a one-second loss window and asynchronous replication. For a cache, correct. For orders, not.
6. **Ad-hoc questions require a data export.** "How many customers have more than three items in their cart?" requiring a full dump and an offline script means you needed a queryable database.
7. **Your "schema" is six undocumented key-naming conventions** that only two engineers know.

The honest framing: a key-value store is an outstanding *derived* or *disposable* data store, and a defensible system of record **only** when access patterns are few, stable, enumerable, and the durability model matches the data's value. DynamoDB genuinely is a system of record for many companies. Redis usually is not, and should not be made into one by accident.

## 5. Request / data flow

Trace **"add a book to the cart"** — BookNook, Redis-backed cart, Redis Cluster with 3 primaries and 3 replicas.

1. The browser sends `POST /cart/items {"isbn":"9780141036144","qty":1}` with a session cookie.
2. The CDN (Lesson 30) passes it through — `POST` is never cached. The load balancer (Lesson 21) picks an app server.
3. The app server reads the session cookie and must resolve it to a customer. It calls `GET session:ab12cd34`.
4. **Client-side routing**: the Redis client library computes `CRC16("session:ab12cd34") mod 16384 = slot 9,412`, and consults its cached slot map: slot 9,412 lives on primary node 2 at `10.0.3.17:6379`.
5. The command goes over a **pooled, already-open TCP connection** — no handshake (Lesson 6). Round trip ~0.3 ms within the availability zone.
6. Node 2 executes `GET` on its single command thread. The value is already in RAM; no disk I/O, no parsing of the value. Execution time is well under 10 µs; the 0.3 ms is almost entirely network.
7. The session returns `{"customer_id":4471}`. The app now builds the cart key `cart:{4471}` — note the **hash tag**: only `4471` is hashed, so every key for this customer co-locates on one node.
8. The app calls `HINCRBY cart:{4471} 9780141036144 1`. This is a **single atomic server-side operation**: it reads the field, adds 1, writes it back, with nothing able to interleave. Two browser tabs clicking "add" simultaneously produce 2, never 1.
9. The same call sets a sliding expiry: `EXPIRE cart:{4471} 2592000` (30 days). Abandoned carts clean themselves up; there is no reaper job to write, monitor, or page someone about.
10. **Replication is asynchronous.** Node 2 acknowledges to the client *immediately*, then streams the write to its replica. The acknowledgement does **not** mean the replica has it. If node 2 dies in that window, the write is lost and the replica is promoted without it.
11. **Persistence**: with AOF `everysec`, the command enters the AOF buffer and is fsynced within one second. Crash in that window loses this write.
12. The app returns `200 OK` with the new cart count. Total server time ~1 ms, of which ~0.6 ms is two Redis round trips.
13. Separately, the app writes `XADD events * type add_to_cart ...` to a Redis Stream, consumed asynchronously into Cassandra (Lesson 42 §7). The user does not wait for this.

**The checkout path diverges deliberately.** When the customer pays:
1. The app reads the cart from Redis.
2. It writes the **order and the stock decrement to PostgreSQL in one ACID transaction** (Lesson 42 §8).
3. Only after `COMMIT` does it `DEL cart:{4471}`.
4. If Redis has lost the cart, the user re-adds items — annoying, recoverable. If PostgreSQL had lost the order, the customer is charged for nothing.

**That ordering is the architecture in miniature: Redis holds what can be lost; PostgreSQL holds what cannot.**

## 6. ASCII diagram

```
            KEY-VALUE STORE: ROUTING, STRUCTURES, DURABILITY
            ================================================

 (A) HOW A KEY FINDS ITS NODE  — Redis Cluster, 16,384 hash slots (Lesson 38)
 ---------------------------------------------------------------------------

   client: HINCRBY  cart:{4471}  9780141036144  1
                         │
                         │  only "4471" is hashed  ← the hash tag {}
                         ▼
              CRC16("4471") mod 16384  =  slot 9412
                         │
      ┌──────────────────┼──────────────────┐
      ▼                  ▼                  ▼
 ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
 │  PRIMARY 1  │   │  PRIMARY 2  │   │  PRIMARY 3  │
 │ slots       │   │ slots       │   │ slots       │
 │ 0 – 5460    │   │ 5461–10922  │◀──│ 10923–16383 │
 └──────┬──────┘   └──────┬──────┘   └──────┬──────┘
        │ async           │ async           │ async      ← ACK is sent BEFORE this
        ▼                 ▼                 ▼
 ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
 │  REPLICA 1  │   │  REPLICA 2  │   │  REPLICA 3  │
 └─────────────┘   └─────────────┘   └─────────────┘

   wrong node? → "MOVED 9412 10.0.3.17:6379" → client updates its slot map


 (B) ONE THREAD, ONE COMMAND AT A TIME
 -------------------------------------

   clients ──▶ ┌───────────────────────────────────────┐
   c1 GET  ──▶ │  [ I/O threads ]  →  ONE EXEC THREAD  │
   c2 INCR ──▶ │                      ┌──────────────┐ │
   c3 KEYS*──▶ │                      │ GET   ~5 µs  │ │
   c4 HSET ──▶ │                      │ INCR  ~5 µs  │ │
               │                      │ KEYS* 4000ms │ │ ◀── EVERYONE WAITS
               │                      │ HSET  ~5 µs  │ │
               │                      └──────────────┘ │
               └───────────────────────────────────────┘
        atomicity is FREE          one slow command = a full outage


 (C) DURABILITY WINDOWS — what you lose on a hard crash
 -----------------------------------------------------

   write ──▶ RAM ──▶ AOF buffer ──fsync──▶ disk ──async──▶ replica
             │         │                    │               │
             │         │                    │               └─ lost if ACK→crash
             │         │                    └─ safe
             │         └─ LOST (up to 1 s with appendfsync everysec)
             └─ LOST on any crash without persistence

   RDB snapshot every 5 min:   |----5 min of writes AT RISK----|snapshot|
   AOF everysec (default):     |-1s-|                            ← default
   AOF always:                 ||     (and throughput collapses)


 (D) DYNAMODB: PARTITION KEY SELECTS, SORT KEY ORDERS
 ---------------------------------------------------

   Query: PK="CUST#4471" AND SK begins_with "ORDER#"

        hash("CUST#4471") ──▶ partition 7 of 340
                               │
     ┌─────────────────────────▼──────────────────────────┐
     │ partition 7      (max 3,000 RCU / 1,000 WCU)       │
     │  ┌──────────────────────────────────────────────┐  │
     │  │ CUST#4471 | ORDER#2026-10-09#88123  ◀┐       │  │
     │  │ CUST#4471 | ORDER#2026-10-07#88044   ├ sorted │  │
     │  │ CUST#4471 | PROFILE                 ◀┘ by SK  │  │
     │  │ CUST#9902 | ORDER#2026-10-08#88090           │  │
     │  └──────────────────────────────────────────────┘  │
     └────────────────────────────────────────────────────┘
        one contiguous read. no index lookup. no join. no sort.

   HOT PARTITION:           salting fixes it:
     BOOK#978…  ███████       BOOK#978…#0 ██
                ███████       BOOK#978…#1 ██    10 partitions,
                ███████  ✗    …           ██    10× ceiling,
     others     ░                BOOK#978…#9 ██   10× read cost
```

## 7. Detailed example

### BookNook — four key-value workloads, one decision each

BookNook's team adds a key-value layer. They identify four candidate workloads and decide each on its merits rather than adopting one store for everything.

**Workload 1 — Shopping carts.**
*Requirements:* 850,000 customers, ~40,000 with an active cart at peak. ~8 writes per cart per session. Must survive a browser refresh and ideally a few days. Losing a cart is an annoyance, not a financial event. Must be atomic per item so two tabs cannot lose an update.
*Decision:* **Redis Hash**, key `cart:{customer_id}`, field = ISBN, value = quantity, with a 30-day sliding `EXPIRE`.
*Why:* `HINCRBY` makes "add one of this book" a single atomic server-side operation — no read-modify-write race. TTL gives free cleanup. The loss window of one second is acceptable because the data is low-value and user-reconstructible.
*Rejected alternative:* a JSON string in Redis. It works, but every item change becomes GET-parse-modify-SET, which is four times the bytes and loses an update when two tabs act concurrently.
*Also rejected:* PostgreSQL. It would work at this volume, but 320,000 cart writes/day of disposable data competing with the order path's buffer pool and WAL is spending the scarcest resource on the least valuable data.

**Workload 2 — Bestseller leaderboard.**
*Requirements:* top 20 books this month, per category, updated within seconds of a sale, read on the homepage ~400,000 times/day.
*Decision:* **Redis Sorted Set**, key `bestsellers:{category}:2026-10`, member = ISBN, score = units sold. `ZINCRBY` on each sale; `ZREVRANGE key 0 19` to read.
*Why:* the alternative is `SELECT isbn, SUM(quantity) FROM order_items JOIN orders ... GROUP BY isbn ORDER BY 2 DESC LIMIT 20` over 35 million line-item rows — hundreds of milliseconds on the primary, 400,000 times a day. The sorted set makes the read O(log n + 20), roughly 0.3 ms, and keeps itself sorted on write at no extra cost.
*The trade accepted:* the leaderboard is **derived data**. If Redis loses it, it must be rebuilt from PostgreSQL. The team writes that rebuild job *before* shipping the feature, and runs it nightly anyway as a reconciliation.

**Workload 3 — Session storage.**
*Decision:* **Redis String**, key `session:{token}`, value = a serialised session blob, `EXPIRE 86400`.
*Why:* a single access pattern, high rate, intrinsically disposable (losing a session logs the user out), and TTL-based expiry is exactly the semantics required.

**Workload 4 — The order history page.**
*Requirements:* "my orders, newest first, paginated." 850,000 customers, 14.6 million orders/year.
*Decision:* **PostgreSQL, not a key-value store.** The data is already there, it must be transactionally consistent with the order write, and finance and support ask unpredictable questions about it (Lesson 42 §7). The page is served from an index on `(customer_id, placed_at DESC)` and cached in Redis for 60 seconds with explicit invalidation on a new order.
*Why this is worth stating:* it is the workload that *looks* most like a DynamoDB composite-key design (`PK=CUST#4471, SK=ORDER#<date>`) and is still correctly left relational. Shape is not the deciding factor; the transaction requirement and the ad-hoc query requirement are.

**Sizing the Redis instance** (figures *approximate*):
- Carts: 40,000 active × ~4 items × ~80 B per hash field ≈ 12.8 MB, plus ~70 B/key overhead ≈ **~16 MB**
- Sessions: 120,000 active × ~1.5 KB ≈ **~180 MB**
- Leaderboards: 24 categories × ~5,000 books × ~70 B ≈ **~8 MB**
- Page cache: budgeted **~2 GB**
- **Total ≈ 2.2 GB.** Provision ~6 GB to allow for fragmentation (Redis typically uses 1.2–1.5× the logical size), RDB fork copy-on-write headroom, and growth. A single `cache.r7g.large` suffices. **No cluster is needed**, and the team explicitly does not build one — Redis Cluster adds `CROSSSLOT` restrictions and failover complexity for a dataset that fits comfortably on one node with a replica.

### A contrasting real-world example — Amazon's shopping cart and the Dynamo paper

The canonical case, and the origin of much of this lesson.

By 2004 Amazon's shopping cart ran on a relational database and was a reliability bottleneck during peak. The requirement was unusual and worth stating precisely: **the cart must always accept a write.** A customer who cannot add an item leaves. Amazon measured this as directly lost revenue.

A single-primary relational database cannot promise that, because during a failover there is a window with no writable primary. So the 2007 **Dynamo** paper described a system that made the opposite trade:

- **Always writeable.** Any replica accepts a write, even during a network partition. This is the "AP" choice in CAP terms (Phase 4 covers CAP properly).
- **Consistent hashing with virtual nodes** (Lesson 38) for partitioning and incremental rebalancing.
- **Vector clocks** to detect conflicting concurrent versions.
- **Application-level conflict resolution.** When two versions of a cart conflict, Dynamo returns *both* and lets the application decide. Amazon's rule was **merge by union** — keep every item from both versions.

The consequence is the famous anomaly: **a deleted item could reappear in your cart.** If the delete landed on a partitioned replica and the union merge ran, the item came back.

That is a *bug* by relational standards. Amazon shipped it deliberately, because the business arithmetic was explicit: a resurrected item costs one customer one click of irritation; a cart that refuses writes during peak costs millions in abandoned baskets.

Three points to carry forward:
1. **The consistency model was chosen from a business requirement, not a technical preference.** "Always writeable, resolve conflicts later" was a product decision with a revenue number behind it.
2. **Amazon did not put orders in Dynamo.** Once you click Buy, you enter transactional systems. Carts tolerate anomalies; payments do not. The same split BookNook made in §5.
3. **DynamoDB (2012) is not Dynamo (2007).** DynamoDB is a managed service that chose a *different* trade: single-key strong consistency available on request, no vector clocks, no application-side conflict resolution, last-writer-wins by default. The names cause endless confusion; the lineage is real but the trade-offs differ.

## 8. Code or configuration example

> **Illustrative only — not production-ready.** Connection management, retry and backoff, error handling, serialisation hardening, authentication, TLS, metrics and key-eviction policy are omitted or simplified for clarity. Do not copy into a real system.

**(a) Redis — BookNook cart, leaderboard and sessions.**

```bash
# --- CART: a Hash, one field per ISBN. The hash tag {4471} pins every key for
# --- this customer to ONE cluster slot, so multi-key ops stay legal.
HSET   cart:{4471} 9780141036144 2            # set qty 2 for this ISBN
HINCRBY cart:{4471} 9780141036144 1           # +1 ATOMICALLY. No read-modify-write.
                                              # Two browser tabs → 4, never 3.
HDEL   cart:{4471} 9780141036144              # remove ONE item; the rest untouched
HGETALL cart:{4471}                           # whole cart, one round trip
EXPIRE cart:{4471} 2592000                    # 30d sliding TTL = free cleanup, no reaper job

# --- LEADERBOARD: a Sorted Set. Score = units sold; Redis keeps it ordered.
ZINCRBY bestsellers:fiction:2026-10 1 9780141036144   # +1 sale, re-sorts in O(log n)
ZREVRANGE bestsellers:fiction:2026-10 0 19 WITHSCORES # top 20 — ALREADY sorted, ~0.3 ms
ZCARD    bestsellers:fiction:2026-10                  # distinct books sold, O(1)
EXPIRE   bestsellers:fiction:2026-10 5184000          # 60d: old months expire themselves

# --- SESSION: a String with a TTL. Single access pattern, disposable data.
SET    session:ab12cd34 '{"customer_id":4471,"csrf":"x9f2"}' EX 86400
GET    session:ab12cd34
EXPIRE session:ab12cd34 86400                 # refresh on activity = sliding session

# --- RATE LIMIT: the classic fixed-window counter.
INCR   ratelimit:ip:203.0.113.9:1760000460    # bucket = unix_time / 60; atomic
EXPIRE ratelimit:ip:203.0.113.9:1760000460 120 # self-cleaning. Set TTL only when INCR
                                               # returns 1, or you reset the window.

# --- UNIQUE VISITORS: HyperLogLog. 12 KB fixed, ±0.81% error.
PFADD   uniq:2026-10-09 cust:4471 cust:9902   # 50 M distinct IDs still costs 12 KB
PFCOUNT uniq:2026-10-09                       # APPROXIMATE. Never use for billing.

# --- DAILY ACTIVE: a Bitmap. 850,000 customers = ~104 KB/day, EXACT.
SETBIT   active:2026-10-09 4471 1             # customer id used directly as the bit offset
BITCOUNT active:2026-10-09                    # exact count of active customers today

# --- NEVER DO THIS IN PRODUCTION:
# KEYS cart:*                                 # O(n), BLOCKS THE WHOLE SERVER for seconds
SCAN 0 MATCH cart:* COUNT 100                 # cursor-based, O(1) per call. Use this.
UNLINK bigkey                                 # frees memory in a BACKGROUND thread;
                                              # DEL on a 5 GB key stalls every client.
```

```ini
# redis.conf — the lines that actually decide your durability and failure behaviour.

maxmemory 6gb                     # HARD cap. Without it, Redis grows until the OOM killer wins.

# THE MOST IMPORTANT LINE IN THIS FILE:
maxmemory-policy allkeys-lru      # evict any key when full  → correct for a pure CACHE
# maxmemory-policy volatile-lru   # evict only keys that HAVE a TTL → use when the instance
                                  #   mixes cache data with data you must not silently lose
# maxmemory-policy noeviction     # reject writes when full → use for queues/sessions you
                                  #   must not lose. Your app MUST handle write errors.

appendonly yes                    # AOF on
appendfsync everysec              # DEFAULT: loss window up to ~1 s (often quoted up to 2 s
                                  #   under load). 'always' ≈ 0 loss but collapses throughput.
                                  #   KNOW WHICH ONE YOU HAVE ACCEPTED.
save 900 1                        # RDB snapshot if ≥1 key changed in 900 s — for fast restarts
save 300 100                      #   and cheap backups, NOT for a small loss window.

lazyfree-lazy-eviction yes        # free evicted keys off-thread; avoids stalls on big values
lazyfree-lazy-expire   yes

timeout 300                       # close idle client connections after 300 s
tcp-keepalive 300

# Replication is ASYNCHRONOUS. The primary ACKs BEFORE the replica has the write.
# min-replicas-to-write 1         # refuse writes unless ≥1 replica is connected and
# min-replicas-max-lag 10         #   lagging ≤10 s. Narrows the loss window; does NOT close it.
```

```lua
-- Atomic "reserve stock and add to cart" — the check and BOTH writes in ONE atomic unit.
-- Without this script these are three round trips with races between every pair.
-- KEYS[1] = stock key, KEYS[2] = cart key  (SAME hash tag required in Cluster!)
-- ARGV[1] = isbn,      ARGV[2] = quantity

local want  = tonumber(ARGV[2])
local stock = tonumber(redis.call('GET', KEYS[1]) or '0')   -- read current stock

if stock < want then
  return {0, stock}                        -- insufficient: NOTHING was written
end

redis.call('DECRBY',  KEYS[1], want)       -- reserve the stock
redis.call('HINCRBY', KEYS[2], ARGV[1], want)  -- add to the cart
redis.call('EXPIRE',  KEYS[2], 2592000)    -- refresh the cart TTL
return {1, stock - want}                   -- reserved, plus remaining stock

-- Rules this script obeys:
--  * deterministic (no TIME, no random) — it may be replicated verbatim to replicas
--  * every key declared in KEYS — Cluster verifies slot co-location before running it
--  * short — the server is single-threaded; a slow script is a site-wide outage
```

**(b) DynamoDB — single-table design for BookNook orders.**

```bash
# Composite primary key: PK selects the partition, SK orders items WITHIN it.
aws dynamodb create-table \
  --table-name BookNook \
  --attribute-definitions \
      AttributeName=PK,AttributeType=S \
      AttributeName=SK,AttributeType=S \
      AttributeName=GSI1PK,AttributeType=S \
      AttributeName=GSI1SK,AttributeType=S \
  --key-schema \
      AttributeName=PK,KeyType=HASH \
      AttributeName=SK,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST \
  --global-secondary-indexes '[{
      "IndexName": "GSI1",
      "KeySchema": [
        {"AttributeName":"GSI1PK","KeyType":"HASH"},
        {"AttributeName":"GSI1SK","KeyType":"RANGE"}],
      "Projection": {"ProjectionType":"INCLUDE",
                     "NonKeyAttributes":["status","total_cents"]}
  }]'
# ProjectionType INCLUDE copies ONLY the listed attributes into the index.
# ALL copies every attribute = a full second table = full second write cost.
# KEYS_ONLY is cheapest but forces a second fetch per item. Choose deliberately.
```

```python
# Access patterns FIRST — this list must be complete before the keys are designed.
#   1. get a customer's profile
#   2. list a customer's orders, newest first, paginated
#   3. get one order with its line items
#   4. list all orders in a given status (ops dashboard)   → needs GSI1

ITEMS = [
  # 1 + 2 + 3 all served by PK="CUST#4471": one partition, sorted by SK.
  {"PK": "CUST#4471", "SK": "PROFILE",
   "email": "a@example.com", "name": "Alice Chen"},

  # SK sorts lexicographically → ISO-8601 dates sort chronologically. This is WHY
  # the date goes first in the SK. "ORDER#9#2026" would sort by order id, uselessly.
  {"PK": "CUST#4471", "SK": "ORDER#2026-10-09#88123",
   "status": "SHIPPED", "total_cents": 2697,
   "GSI1PK": "STATUS#SHIPPED",              # GSI1 partition key = the status
   "GSI1SK": "2026-10-09T11:04:00Z"},       # GSI1 sort key = time, so the dashboard
                                            #   can range-query "shipped since X"

  # Line items live in the SAME partition as their order, under a longer SK prefix.
  # They are SEPARATE items because an order's items could otherwise push the
  # order item past the hard 400 KB limit.
  {"PK": "CUST#4471", "SK": "ORDER#2026-10-09#88123#ITEM#1",
   "isbn": "9780141036144", "qty": 1, "price_cents": 899},
]
```

```python
import boto3
from boto3.dynamodb.conditions import Key

tbl = boto3.resource("dynamodb").Table("BookNook")

# Pattern 2: a customer's orders, newest first. ONE partition, ONE contiguous read.
# No index lookup, no sort at query time — the items are ALREADY stored in SK order.
resp = tbl.query(
    KeyConditionExpression=Key("PK").eq("CUST#4471")
                         & Key("SK").begins_with("ORDER#"),
    ScanIndexForward=False,   # False = DESCENDING = newest first
    Limit=20,                 # page size; LastEvaluatedKey carries the cursor
)

# Pattern 4: the ops dashboard, via the GSI. NOT the base table.
resp = tbl.query(
    IndexName="GSI1",
    KeyConditionExpression=Key("GSI1PK").eq("STATUS#PENDING")
                         & Key("GSI1SK").gt("2026-10-09T00:00:00Z"),
)
# NOTE: GSIs are EVENTUALLY CONSISTENT. An order written 50 ms ago may not appear here.
# Fine for a dashboard. NOT fine for "did this order already exist?" — use the base table.

# Conditional write = optimistic concurrency. This is how you get correctness
# WITHOUT transactions: the condition and the write are evaluated atomically.
tbl.update_item(
    Key={"PK": "BOOK#9780141036144", "SK": "STOCK"},
    UpdateExpression="SET stock_count = stock_count - :q, version = version + :one",
    ConditionExpression="stock_count >= :q AND version = :v",   # fails → ConditionalCheckFailed
    ExpressionAttributeValues={":q": 1, ":one": 1, ":v": 14},
)
# On failure: re-read, re-check, retry with backoff. Never loop without a retry cap.

# THE ANTI-PATTERN. Reads and BILLS FOR every item in the table.
# If you find yourself writing this in a request path, the key design is wrong.
# bad = tbl.scan(FilterExpression=Attr("total_cents").gt(5000))
# FilterExpression is applied AFTER the read — it reduces the response, NOT the cost.
```

## 9. Numerical example

**The question: for BookNook's cart + session + leaderboard workload, what do Redis and DynamoDB each cost, and which is right?**

**Stated assumptions** (prices are *approximate* 2026 list prices in `eu-west-1`, rounded; they move, the method does not):

| Assumption | Value |
|---|---|
| Active customers at peak | 40,000 with a cart, 120,000 with a session |
| Cart operations | 8 writes + 12 reads per cart session |
| Cart sessions per day | 320,000 |
| Session reads | 1 per page view; 1,000,000 page views/day |
| Session writes | 1 per login + 1 refresh per 10 page views |
| Leaderboard writes | 1 `ZINCRBY` per item sold; 96,000 items/day |
| Leaderboard reads | 400,000/day |
| Average cart item size | 4 items × 80 B + overhead ≈ 400 B |
| Average session size | 1.5 KB |
| Peak-to-average traffic ratio | 5× |
| ElastiCache `cache.r7g.large` (13.07 GB) | ~£0.16/hour |
| DynamoDB on-demand write | ~£1.00 per million WCU-requests |
| DynamoDB on-demand read | ~£0.20 per million RCU-requests (eventually consistent counts as ½) |
| DynamoDB storage | ~£0.22 per GB-month |
| Hours per month | 730 |

---

**Step 1 — total operations per day.**

Cart writes:  320,000 sessions × 8 = **2,560,000/day**
Cart reads:   320,000 × 12 = **3,840,000/day**
Session reads: **1,000,000/day**
Session writes: logins ≈ 320,000, plus refresh 1,000,000 / 10 = 100,000 → **420,000/day**
Leaderboard writes: **96,000/day**
Leaderboard reads: **400,000/day**

Total writes = 2,560,000 + 420,000 + 96,000 = **3,076,000/day**
Total reads  = 3,840,000 + 1,000,000 + 400,000 = **5,240,000/day**
Total ops    = **8,316,000/day**

**Step 2 — convert to per-second rates.**

Average ops/s = 8,316,000 / 86,400 = **96.25 ops/s**
Peak ops/s = 96.25 × 5 = **481 ops/s**

Average writes/s = 3,076,000 / 86,400 = 35.60 → peak = **178 writes/s**
Average reads/s  = 5,240,000 / 86,400 = 60.65 → peak = **303 reads/s**

---

**Step 3 — Redis sizing.**

Logical data:
- Carts: 40,000 × 400 B = 16,000,000 B = **16.0 MB**
- Sessions: 120,000 × 1,500 B = 180,000,000 B = **180.0 MB**
- Leaderboards: 24 categories × 5,000 members × 70 B = 8,400,000 B = **8.4 MB**
- Page/object cache budget: **2,048 MB**
- Logical total = 16.0 + 180.0 + 8.4 + 2,048 = **2,252.4 MB ≈ 2.25 GB**

Apply a 1.4× fragmentation and overhead factor (typical for Redis with mixed small objects):
2.25 × 1.4 = **3.15 GB**

RDB fork copy-on-write headroom: budget a further 50% of the dataset:
3.15 × 1.5 = **4.73 GB required**

A `cache.r7g.large` provides **13.07 GB**. Utilisation = 4.73 / 13.07 = **36%**. Comfortable, with room to roughly double before the next size up.

**Redis throughput check.** A single instance sustains ~100,000 simple ops/s (typical). Peak demand is 481 ops/s.
Utilisation = 481 / 100,000 = **0.48%**. Headroom factor = **208×**.
**No cluster is needed**, and building one would add `CROSSSLOT` constraints for no benefit.

**Redis cost.**
Primary: £0.16/h × 730 = £116.80/month
Replica (for failover, in a second AZ): £116.80/month
**Redis total ≈ £233.60/month**, plus negligible cross-AZ data transfer.

---

**Step 4 — DynamoDB capacity arithmetic for the same workload.**

*Writes.* WCU = `ceil(item_size_KB / 1)` per write.
- Cart item 400 B → `ceil(0.4) = 1 WCU` each → 2,560,000 WCU-requests/day
- Session item 1.5 KB → `ceil(1.5) = 2 WCU` each → 420,000 × 2 = 840,000 WCU-requests/day
- Leaderboard: DynamoDB has no sorted-set type. A counter increment is an `UpdateItem` on a small item → 1 WCU → 96,000/day
- **Total = 2,560,000 + 840,000 + 96,000 = 3,496,000 WCU-requests/day**

*Reads* (eventually consistent, so each 4 KB read costs **0.5 RCU**):
- Cart 400 B → `ceil(0.4/4) = 1` unit × 0.5 = 0.5 RCU → 3,840,000 × 0.5 = 1,920,000 RCU-requests/day
- Session 1.5 KB → `ceil(1.5/4) = 1` unit × 0.5 = 0.5 RCU → 1,000,000 × 0.5 = 500,000/day
- **Leaderboard is the problem.** There is no `ZREVRANGE`. To get the top 20 you must either (a) query a GSI partitioned by category and sorted by sales — but the sort key cannot be a value that changes on every sale without rewriting the index entry — or (b) maintain a single precomputed "top 20" item and rewrite it. Take option (b): 400,000 reads of a ~2 KB item → `ceil(2/4) = 1` unit × 0.5 = 0.5 RCU → **200,000 RCU-requests/day**, plus a recompute job.
- **Total = 1,920,000 + 500,000 + 200,000 = 2,620,000 RCU-requests/day**

*Monthly totals:*
- WCU-requests: 3,496,000 × 30 = **104,880,000**
- RCU-requests: 2,620,000 × 30 = **78,600,000**

*On-demand cost:*
- Writes: 104,880,000 / 1,000,000 × £1.00 = **£104.88**
- Reads:  78,600,000 / 1,000,000 × £0.20 = **£15.72**
- Storage: carts+sessions+leaderboards ≈ 0.21 GB → 0.21 × £0.22 = **£0.05**
- **DynamoDB total ≈ £120.65/month**

---

**Step 5 — the GSI write-amplification correction.**

Suppose the ops team adds **two** GSIs over the cart table (by last-updated time, and by abandoned-cart status). Each GSI is a separate copy and incurs its own write cost for every base write that touches a projected attribute.

Cart writes with 2 GSIs = 2,560,000 × (1 base + 2 index) = 2,560,000 × 3 = **7,680,000 WCU-requests/day**
New daily write total = 7,680,000 + 840,000 + 96,000 = **8,616,000/day**
Monthly = 8,616,000 × 30 = 258,480,000 → **£258.48/month** in writes alone.

**Total with 2 GSIs ≈ £274.25/month** — a **127% increase** from adding two indexes. This is the single most under-estimated DynamoDB cost, and it is why §8 flags `ProjectionType` as a deliberate decision.

---

**Step 6 — comparison and decision.**

| | Redis (ElastiCache) | DynamoDB (on-demand, 0 GSI) | DynamoDB (2 GSIs) |
|---|---|---|---|
| Monthly cost | **£233.60** | **£120.65** | **£274.25** |
| Read latency (typical) | 0.2–0.5 ms | 3–8 ms | 3–8 ms |
| Cost scaling | **Flat** until RAM runs out | **Linear** with every request | Linear × (1 + #GSIs) |
| Leaderboard support | Native sorted set, one command | None — must be hand-built and recomputed | Same |
| Atomic multi-key | Lua within a slot | `TransactWriteItems`, ≤100 items, 2× cost | Same |
| Durability | Loss window ~1 s (AOF `everysec`) | Durable, replicated across 3 AZs | Same |
| Scaling effort | Resize the node, or build a cluster | **Zero** | Zero |
| Ops burden | Patching, failover testing, memory tuning | Effectively none | None |

**Step 7 — the break-even on cost scaling.**

Redis is flat-rate; DynamoDB is per-request. At what traffic do they cross, holding the Redis node constant?

DynamoDB cost ≈ £120.65 at 8.316 M ops/day. To reach Redis's £233.60:
£233.60 / £120.65 = 1.94 → **~16.1 million ops/day**.

But Redis stays at £233.60 all the way to its throughput and memory ceilings. Memory is at 36%, so it absorbs roughly 2.8× more data; throughput is at 0.48%, so it absorbs ~200× more ops. The realistic binding constraint is memory.

**So: below ~16 M ops/day DynamoDB is cheaper; above it, Redis is cheaper and the gap widens quickly.** At 10× BookNook's current traffic (83 M ops/day) DynamoDB would be roughly £1,207/month while Redis would need one size up (`cache.r7g.xlarge`, ~£467/month for a pair) — a 2.6× saving.

**The decision, and the reason it is not purely cost.** BookNook chooses **Redis**, despite being on the wrong side of the cost break-even today, for three requirement-driven reasons:
1. **The leaderboard needs a sorted set.** Rebuilding `ZINCRBY` + `ZREVRANGE` on DynamoDB means a precomputed item plus a recompute job plus a staleness window — real engineering cost that does not appear in the price comparison.
2. **Latency.** 0.3 ms versus 5 ms on the session lookup, which is on *every* page view. That is ~4.7 ms off every page, and at p99 the gap is wider.
3. **Cost trajectory.** Traffic is growing 3× per year (Lesson 42 §17). Redis is flat; DynamoDB is linear. The crossover arrives within the year.

**When the decision would flip to DynamoDB:** if the data were a system of record that must not lose one second of writes; if traffic were extremely spiky (DynamoDB on-demand absorbs a 50× spike instantly, a fixed Redis node does not); if the dataset were far larger than RAM economically allows; or if the team had no capacity to operate a stateful service. State the condition, not "it depends".

<!--CHUNK-MARKER-->
