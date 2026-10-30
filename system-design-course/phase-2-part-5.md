# Lesson 35 — Database Partitioning and Sharding

*Phase 2, Topic 18*

## 1. Simple definition

**Partitioning** is splitting one logical table into several smaller physical pieces so that any given query touches only the pieces it needs.

**Sharding** is partitioning *across independent machines*, where each machine owns a disjoint slice of the data and knows nothing about the others.

Those two sentences are the whole lesson in miniature, but the words get used interchangeably in blog posts and that confusion causes real architectural mistakes. Pin down the vocabulary before anything else:

- **Partition** — a physical subset of a table's rows (or columns). The database still presents one logical table. All partitions live in **one database instance**, sharing one buffer pool, one write-ahead log, one transaction manager, one backup.
- **Shard** — a partition that lives on its **own database instance**, with its own CPU, RAM, disk, WAL, connection pool and failure domain. Shards do not share a transaction manager. There is no single process that can see all the data.
- **Shard key** (also *partition key*, *distribution key*) — the column or expression whose value decides which shard a row belongs to.
- **Routing layer** — the code that looks at a shard key and decides which database connection to use. It may live in your application, in a proxy (Vitess, Citus, ProxySQL), or inside the database itself (MongoDB, Cassandra, DynamoDB).
- **Fan-out / scatter-gather** — a query that cannot be answered by one shard, so it is sent to many shards and the results are merged. Lesson 37 is largely about how expensive this is.

The one-sentence test that separates the two concepts:

> **Can a single `BEGIN ... COMMIT` atomically modify rows in both pieces, enforced by one database process?**
> If yes, you have **partitioning**. If no, you have **sharding**.

Everything painful about sharding — lost joins, lost unique constraints, lost transactions, lost `ORDER BY` — follows mechanically from that one "no".

## 2. Why it exists

Replication (Lessons 32–34) gave you more read capacity and a durability story. It gave you **nothing** on three axes:

1. **Write throughput.** Every write still goes to exactly one primary. Ten replicas do not make the primary commit faster; they make it slower, because it now has ten extra streams to feed.
2. **Dataset size.** Every replica holds a *complete copy*. If the primary needs 12 TB, each replica needs 12 TB. Replication multiplies your storage bill without reducing any single machine's burden.
3. **Working-set-to-RAM ratio.** A database is fast mostly because the pages it needs are already in memory. Define **working set**: the subset of data actually touched by queries in a given window (say, an hour). When the working set no longer fits in the buffer pool, every query starts hitting disk, and latency rises by one to two orders of magnitude — a buffer-pool hit is ~100 nanoseconds, an NVMe read is ~100 microseconds, roughly 1,000× slower. Replication does not help; each replica has the same bad ratio.

Sharding is the only technique that attacks all three at once, because it is the only one that gives each machine **less data and fewer writes**, not more.

There is a fourth, less glamorous and often decisive reason: **operational time scales with size**. A 12 TB PostgreSQL instance takes hours to back up, hours to restore, hours to `pg_upgrade`, and its autovacuum cycles on the largest tables start overlapping each other. Eight 1.5 TB shards do each of those in a fraction of the time, in parallel, and a restore only ever loses or delays one-eighth of your customers. That blast-radius argument persuades more engineering directors than throughput graphs do.

### Why sharding is the last resort

Sharding is permanent. Once your application has been rewritten to route by shard key, to avoid cross-shard joins, and to generate its own IDs, you do not go back. So before you shard, you must genuinely exhaust the cheaper options, in roughly this order:

| # | Exhaust this first | Typical gain | Cost to you | How you know it's exhausted |
|---|---|---|---|---|
| 1 | **Indexes and query fixes** | 10×–1000× on specific queries | Hours of work | `pg_stat_statements` shows no single query dominating; no sequential scans on big tables; `EXPLAIN` plans are index scans |
| 2 | **Connection pooling** (PgBouncer) | 2×–5× effective capacity | A day | Backend process count is stable and well under `max_connections` |
| 3 | **Caching** (Lessons 26–31) | 80–99 % of reads removed | Invalidation complexity | Cache hit ratio >95 % and DB read QPS no longer tracks traffic |
| 4 | **Read replicas** (Lessons 32–34) | Near-linear read scale | Replication lag, stale reads | Replicas are not saturated; primary is still the bottleneck |
| 5 | **Vertical scaling** (Lesson 18) | 2×–30× | Money, a reboot, a ceiling | You are on the largest instance your provider sells, or the next size up is no longer cost-effective |
| 6 | **Archiving / tiering cold data** | 50–90 % size reduction | A second store to query | Only the genuinely hot rows remain; cold rows are in object storage or a columnar store |
| 7 | **Moving the wrong workload out** | Huge | A new system to run | Analytics, event logs and search have been moved off OLTP (Phase 3, Topics on OLAP and search) |
| 8 | **Functional/vertical split** (one service, one database) | Divides load by the number of domains | Distributed transactions appear | Each domain already has its own database and one of them is *still* too big |

Only when all eight are genuinely done — not "we talked about it", *done and measured* — is horizontal sharding the right answer. Teams that shard at step 2 spend a year building a distributed system to solve a missing index.

## 3. Real-world analogy

A **public library that has outgrown its building**.

- **Vertical partitioning** is realising that the reference encyclopaedias take up half the shelf space and are consulted once a month, so you move them into a back room. Same books, same catalogue, but the stuff people actually browse now fits in the front hall and is faster to reach.
- **Horizontal partitioning** is splitting the fiction section into eight bays, A–C, D–F, G–I and so on. Still one library, one librarian, one card catalogue. A reader looking for "Tolkien" is sent directly to the S–U bay and never walks past the others. That directed walk is **partition pruning**.
- **Sharding** is opening eight *separate branch libraries* across the city. Each branch has its own building, its own librarian, its own opening hours, and its own fire risk. Nobody can see the whole collection at once. If you want "every book by Tolkien in the city", you must phone all eight branches and merge the answers yourself — and if one branch does not answer, your list is incomplete and you have to decide whether to show a partial list or an error.

**Where the analogy breaks down**

| Library | Real sharded database |
|---|---|
| A librarian can phone another branch and ask them to hold a book, and the whole thing works out | There is no cross-branch "hold". A two-shard change needs two-phase commit or a saga, and both can leave you half-done (Phase 4, Topics 19–21) |
| Moving books between branches happens overnight while closed | Resharding happens while the system is live, serving writes, with no window where you may stop (Lesson 37) |
| Each branch gets roughly the same number of visitors | Real key distributions are power laws. One shard routinely gets 10× the traffic of another — the hot-partition problem (Lesson 37) |
| The card catalogue is cheap to maintain and always correct | A directory/lookup service is an extra network hop on every query and a single point of failure (Lesson 36) |
| Book IDs are assigned centrally by the city and are unique | There is no central counter any more. `AUTO_INCREMENT` is dead and you must generate globally unique IDs yourself (Section 4) |
| Closing a branch is a planning decision taken over months | Losing a shard is an unplanned event that takes 12.5 % of your users offline in milliseconds |

The deepest mismatch is the last row of row three: humans tolerate "the Tolkien list might be incomplete". Users and auditors do not tolerate "your order history might be incomplete".

## 4. Detailed technical explanation

### 4.1 Vertical versus horizontal partitioning

These names are confusing because they refer to how you slice a drawn table, not to how you scale.

**Vertical partitioning** splits a table **by column**. You take a wide table and divide its columns into groups, keyed by the same primary key.

```
books (wide, 40 columns, 8 KB/row)
┌────┬───────┬───────┬────────┬────────────────────────┬──────────────┐
│ id │ title │ price │ stock  │ description (6 KB TEXT) │ cover_blob   │
└────┴───────┴───────┴────────┴────────────────────────┴──────────────┘
                 ↓ vertical split ↓
books_core (hot, 180 B/row)              books_detail (cold, 7.8 KB/row)
┌────┬───────┬───────┬───────┐           ┌────┬─────────────┬────────────┐
│ id │ title │ price │ stock │           │ id │ description │ cover_blob │
└────┴───────┴───────┴───────┘           └────┴─────────────┴────────────┘
```

Why this works: databases read whole **pages** (8 KB in PostgreSQL, 16 KB in InnoDB) from disk into the buffer pool. If each row is 8 KB, one page holds one row, and a scan of 1,000 rows reads 1,000 pages. If the hot row is 180 bytes, ~44 rows share a page and the same scan reads ~23 pages — **43× less I/O for identical logic**. For BookNook's catalogue listing, which needs `title`, `price` and `stock` and never needs `description`, this is free performance.

Vertical partitioning taken to its extreme *across services* — `users` in one database, `orders` in another — is called **functional partitioning** or **vertical sharding**, and it is how most companies get their first real relief. It is also how you accidentally acquire distributed transactions.

**Horizontal partitioning** splits a table **by row**. Same columns, different rows in different places. This is the one people mean 95 % of the time, and the only one that solves write throughput.

```
orders (2,000,000,000 rows)
                 ↓ horizontal split on hash(user_id) ↓
orders_p0            orders_p1            ...     orders_p7
(250 M rows)         (250 M rows)                 (250 M rows)
same columns         same columns                 same columns
```

| | Vertical partitioning | Horizontal partitioning |
|---|---|---|
| Splits by | Columns | Rows |
| Row count per piece | Unchanged | Divided by N |
| Helps with | Row width, I/O per scan, cold-column bloat | Row count, write throughput, dataset size |
| Query that gets worse | Any query needing columns from both pieces (now a join) | Any query spanning many key values (now a fan-out) |
| Natural stopping point | You run out of cold columns | None — you can keep splitting |

### 4.2 Partitioning inside one database (declarative partitioning)

This is the step most teams skip, and it is the highest-value, lowest-risk step in the whole lesson.

Modern relational databases let you declare that one logical table is physically stored as many child tables. PostgreSQL calls this **declarative partitioning** (since v10); MySQL has `PARTITION BY`; SQL Server has partition functions and schemes. Crucially:

- There is still **one database process**, one connection string, one transaction manager.
- `SELECT`, `INSERT`, `UPDATE`, `DELETE`, joins, foreign keys to other tables, and `BEGIN/COMMIT` all behave normally.
- The application **does not change at all**.

What you gain:

**Partition pruning** — the planner examines the `WHERE` clause, works out which partitions *could possibly* contain matching rows, and skips the rest entirely. If `orders` is partitioned monthly and you ask for `WHERE created_at >= '2026-09-01'`, a 60-partition table is read as one partition: 60× less work from a declaration, not from code.

**Partition-wise joins and aggregates** — when two tables are partitioned the same way on the join column, the planner can join partition-to-partition and run those joins in parallel.

**Cheap bulk deletion** — `DROP TABLE orders_2023_01` is a metadata operation taking milliseconds. `DELETE FROM orders WHERE created_at < '2023-02-01'` on 40 million rows takes minutes, writes 40 million WAL records, bloats the table, and leaves autovacuum work behind. For any time-series retention policy, this one property justifies partitioning on its own.

**Smaller indexes** — a B-tree over 2 billion rows is roughly 5–6 levels deep; over 33 million rows it is 4 levels. Each level is a potential page fault. And an index that fits in RAM behaves categorically differently from one that does not.

**Faster maintenance** — `VACUUM`, `ANALYZE`, `REINDEX` run per partition, in parallel, and never need to lock the whole dataset.

What you do **not** gain: write throughput beyond one machine, dataset size beyond one machine's disk, or failure isolation. All partitions share one WAL, one `fsync` path, one crash.

> **Rule to remember:** partitioning is a *query-planner and maintenance* optimisation. Sharding is a *capacity* decision. Partition early and often; shard late and reluctantly.

Declarative partitioning also has real costs, so state them: the partition key must be part of the primary key and every unique constraint; cross-partition unique constraints are impossible; too many partitions (thousands) slows planning because the planner must consider each one; and queries that omit the partition key read every partition, which is *slower* than an unpartitioned table because of per-partition overhead.

### 4.3 Sharding across machines

Sharding takes the same horizontal split and puts each piece behind its own connection.

```
                      app server (holds the routing map)
                               │
        ┌──────────────┬───────┴───────┬──────────────┐
        ▼              ▼               ▼              ▼
   shard 0 (PG)   shard 1 (PG)    shard 2 (PG)   shard 3 (PG)
   own WAL        own WAL         own WAL        own WAL
   own primary    own primary     own primary    own primary
   + 2 replicas   + 2 replicas    + 2 replicas   + 2 replicas
```

Note the composition: **sharding and replication are orthogonal and you almost always need both.** Sharding divides the data; replication protects each slice. A 4-shard cluster with 2 replicas each is 12 PostgreSQL instances, and each shard still needs its own failover logic, its own lag monitoring, and its own backup schedule. The operational surface multiplies. Budget for it honestly: if one database took 0.2 of an engineer to run, twelve do not take 0.2 — they take closer to 1.0 plus tooling.

**Where the routing lives** — three options, and the choice is architectural:

| Approach | Where the map lives | Pros | Cons | Examples |
|---|---|---|---|---|
| **Client-side routing** | In your app, as a library | No extra hop, lowest latency, full control | Every service/language must reimplement it; map changes need a deploy | Shopify, Instagram's early sharding, most in-house systems |
| **Proxy routing** | A middle tier speaking the DB wire protocol | App thinks it has one database; central place to change the map | +0.3–1 ms per query, another tier to run and scale | Vitess (MySQL), Citus (PostgreSQL), ProxySQL |
| **Database-native** | Inside the cluster itself | Transparent; rebalancing is automatic | You adopt that database's model wholesale; less control | MongoDB sharded clusters, Cassandra, DynamoDB, CockroachDB, YugabyteDB |

### 4.4 Choosing the shard key — the single most consequential decision

A shard key is harder to change than a programming language. Changing it means rewriting every row's location and usually every query. Choose it with four explicit criteria:

**1. High cardinality.** The number of distinct values must vastly exceed the number of shards — thousands of times more, not twice. Cardinality is the hard ceiling on how many shards you can ever have. Sharding by `country_code` caps you at ~195 shards and in practice at about 5, because traffic concentrates. Sharding by `order_status` (6 values) caps you at 6 and is catastrophic.

**2. Even distribution.** Values must spread load evenly, both in **rows** and in **requests**. These are different: `tenant_id` may give you 50,000 distinct values (great cardinality) while one enterprise tenant holds 40 % of the rows (terrible distribution). Always check both the row histogram *and* the query histogram.

**3. Present in the overwhelming majority of queries.** If the shard key is not in the `WHERE` clause, the router cannot prune and the query fans out to every shard. Take your top 20 queries by volume from `pg_stat_statements`, and count what fraction contain the candidate key. Below ~90 % and you are designing a scatter-gather system by accident.

**4. Stable — effectively immutable.** Changing a row's shard key means **deleting it from one shard and inserting it into another**, which is a cross-shard transaction you cannot have. `user_id` never changes. `email` changes. `seller_id` changes when a marketplace migrates an account. `current_warehouse_id` changes constantly and is a disastrous shard key.

A fifth, softer criterion: **co-location**. Rows that are read together should land on the same shard. If `orders`, `order_items`, `addresses` and `payments` are all sharded on `user_id`, then "show me my order with its items and payment" is a single-shard, single-transaction query — exactly as simple as before you sharded. This is the difference between a sharded system that feels normal and one that feels broken.

### 4.5 What you permanently lose

Be blunt about this in interviews; it is what separates people who have sharded from people who have read about it.

| Lost capability | Why it's lost | What you do instead |
|---|---|---|
| **Cross-shard joins** | No process can see both sides | Denormalise (copy the needed columns into the same shard), co-locate by shard key, or join in the application / an aggregation service |
| **Global unique constraints** | `UNIQUE(email)` can only be enforced within one shard | A separate uniqueness service, or a dedicated "directory" shard holding only `email → user_id`, written first |
| **Cross-shard transactions** | No shared transaction manager | Keep transactions inside one shard by design; otherwise 2PC or sagas with compensation (Phase 4, Topics 19–21) |
| **Global `ORDER BY` / `LIMIT`** | Each shard sorts only its own rows | Scatter-gather with over-fetch and merge (Lesson 37), or precompute into a search index |
| **Global aggregates** (`COUNT(*)`, `SUM`) | Each shard knows only its own total | Fan out and sum (expensive, and non-atomic across shards), or maintain counters, or accept approximation from an analytics store |
| **`AUTO_INCREMENT` / `SERIAL` IDs** | Independent counters on 8 machines collide | Distributed ID generation — Section 4.6 |
| **Foreign keys across shards** | The database cannot verify a row it cannot see | Enforce in the application; accept eventual repair via reconciliation jobs |
| **Simple backup/restore semantics** | 8 independent backups taken at 8 slightly different instants | Coordinated snapshots, or accept that a full-cluster restore is not point-in-time consistent |

### 4.6 Distributed ID generation

The moment you shard, `SERIAL`/`AUTO_INCREMENT` stops being safe. Four workable schemes, and they trade off differently:

| Scheme | Size | Sortable by time? | Coordination needed | Index behaviour | Collision risk | Leaks information? |
|---|---|---|---|---|---|---|
| **UUID v4** (random) | 128 bit / 36 char text | No | None | **Bad** — random inserts scatter across the whole B-tree, causing page splits and poor cache locality | ~0 (2⁻¹²²) | No |
| **UUID v7** (time-ordered, RFC 9562) | 128 bit | Yes (48-bit ms prefix) | None | Good — near-sequential, appends to the right edge | Negligible (74 random bits) | Reveals creation time |
| **ULID** | 128 bit / 26 char text | Yes (48-bit ms prefix) | None | Good | Negligible (80 random bits) | Reveals creation time |
| **Snowflake** (Twitter) | 64 bit | Yes | Needs unique worker IDs | **Best** — compact, monotonic, half the index size of a UUID | Zero if worker IDs are unique | Reveals time, worker, and rate |
| **Database ticket server / hi-lo** | 64 bit | Yes | A central allocator | Best | Zero | Reveals count — competitors can measure your order volume |

Snowflake's 64-bit layout, which you should be able to draw from memory:

```
 1 bit    41 bits                     10 bits        12 bits
┌──────┬──────────────────────────┬──────────────┬──────────────┐
│ sign │ ms since custom epoch    │ worker/node  │ sequence     │
│  0   │ ≈69.7 years of range     │ 1,024 nodes  │ 4,096 per ms │
└──────┴──────────────────────────┴──────────────┴──────────────┘
```

That gives 4,096 × 1,000 = **4.096 million IDs per second per node**, and 4.19 billion per second across 1,024 nodes. The practical hazards are **clock skew** (if NTP steps the clock backwards, you can emit duplicates — real implementations refuse to issue IDs until the clock catches up) and **worker-ID assignment** (two pods accidentally given the same worker ID silently collide; derive it from ZooKeeper/etcd, a StatefulSet ordinal, or the pod's IP).

Why the index behaviour row matters so much: a random UUID v4 primary key on a 2-billion-row table means every insert lands in a random leaf page. With a 512 GB buffer pool over a 400 GB index you might be fine; over a 4 TB index you will take a disk read *per insert*, and your write throughput collapses by roughly 50–90 % versus a sequential key. Teams routinely blame "sharding made us slow" when the real cause was switching to UUID v4. Default recommendation for new sharded systems: **UUID v7 if you want zero coordination, Snowflake if you want 64-bit compactness.**

A useful trick: **embed the shard in the ID.** If the top bits of the ID encode the shard number, then any service holding only an `order_id` can route without a lookup. The cost is that the ID now pins the row to a shard forever, which makes resharding harder. Prefer deriving the shard from the *shard key* (e.g. `user_id`) and carrying that key alongside the ID.

## 5. Request / data flow

A BookNook customer (`user_id = 918273645`) opens "My Orders" on a sharded cluster of 8 shards.

1. Browser issues `GET /api/orders?page=1`, carrying a session cookie (Lesson 6).
2. The load balancer (Lesson 21) picks any stateless app server (Lesson 20).
3. The app server authenticates the session and resolves `user_id = 918273645`. **This step is now critical**: without the shard key, nothing below can be routed.
4. The app checks Redis for `orders:user:918273645:p1` (Lesson 28, cache-aside). Suppose a miss.
5. The **routing layer** computes the shard: `shard = crc32(918273645) % 8 = 3`.
6. The routing layer selects a connection pool. Because this is a read, it picks the **read replica** of shard 3, not its primary (Lesson 32). If the user wrote in the last few seconds, read-your-writes logic (Lesson 34) forces the primary instead.
7. Shard 3 executes `SELECT ... FROM orders WHERE user_id = $1 ORDER BY created_at DESC LIMIT 20`. Because `orders` is *also* declaratively partitioned by month inside shard 3, the planner prunes to the two most recent monthly partitions.
8. The same shard, in the **same transaction**, executes the `order_items` lookup — possible only because `order_items` is co-located on `user_id`.
9. The app enriches each row with book titles. Those live in the `books` table, which is **not sharded** — it is 50,000 rows and is replicated in full to every shard (a *reference table*), so this is still a single-shard join, not a fan-out.
10. The app writes the result to Redis with a 60-second TTL (Lesson 29).
11. The response is serialised and returned.

Now contrast with an admin request: `GET /admin/orders?status=PENDING_REFUND&sort=created_at&limit=50`.

1. There is **no `user_id`** in this query.
2. The router cannot prune. It must query **all 8 shards**.
3. Each shard returns its own top 50 by `created_at`, because no shard can know the global order.
4. The app merges 8 × 50 = 400 rows in memory, sorts, and takes 50.
5. The request's latency is the latency of the **slowest** of 8 shards, not the average (Lesson 16 — tail-latency amplification; quantified in Lesson 37).

Those two flows, side by side, *are* the argument for choosing the shard key by query pattern.

## 6. ASCII diagram

```
BEFORE  — one primary, three replicas (Lesson 32). Reads scale, writes do not.

                     writes 900/s ──┐
                                    ▼
                          ┌────────────────────┐
                          │  PRIMARY  (12 TB)  │  ← the wall
                          └────────┬───────────┘
                     ┌─────────────┼─────────────┐
                     ▼             ▼             ▼
                 replica(12TB) replica(12TB) replica(12TB)
                 reads only    reads only    reads only


AFTER  — 8 shards × (1 primary + 2 replicas). Writes AND data divided by 8.

                         ┌───────────────────────┐
   client ──────────────▶│  app + routing layer  │   shard = f(user_id)
                         └───────┬───────────────┘
            ┌──────────┬─────────┼─────────┬──────────┐
            ▼          ▼         ▼         ▼          ▼
        ┌───────┐  ┌───────┐ ┌───────┐ ┌───────┐  ┌───────┐
        │shard0 │  │shard1 │ │shard2 │ │shard3 │… │shard7 │
        │1.5 TB │  │1.5 TB │ │1.5 TB │ │1.5 TB │  │1.5 TB │
        │113 w/s│  │113 w/s│ │113 w/s│ │113 w/s│  │113 w/s│
        └───┬───┘  └───┬───┘ └───┬───┘ └───┬───┘  └───┬───┘
            │          │         │         │          │
         2 replicas each (async, Lesson 33) — 24 instances total


INSIDE one shard — declarative partitioning by month (same machine, one WAL)

        shard 3  ┌─────────────────────────────────────────────┐
                 │ orders (logical)                            │
                 │  ├ orders_2026_07   ← pruned away           │
                 │  ├ orders_2026_08   ← pruned away           │
                 │  ├ orders_2026_09   ◀── query reads only    │
                 │  └ orders_2026_10   ◀── these two           │
                 │ books (reference copy, 50k rows, replicated)│
                 └─────────────────────────────────────────────┘
```

## 7. Detailed example

### 7.1 BookNook reaches the wall

BookNook has grown through Phase 2. Current state, and these are the numbers we will carry through Lessons 36–39:

| Dimension | Value (approximate, measured) |
|---|---|
| Registered users | 40 million |
| Daily active users | 4 million |
| Peak app-tier RPS | 12,000 |
| Peak DB **read** QPS (after a 96 % cache hit ratio) | 2,400 — comfortably served by 5 replicas |
| Peak DB **write** TPS on the primary | 900 |
| `orders` rows | 2.0 billion |
| `order_items` rows | 6.4 billion |
| `reviews` rows | 380 million |
| `books` rows | 50,000 |
| Primary instance | 64 vCPU, 512 GB RAM, NVMe at ~60,000 write IOPS |
| Total primary data + indexes | 2.9 TB |
| Measured write ceiling before p99 commit > 20 ms | ≈ 4,000 TPS |
| Year-on-year growth in writes and data | 2.5× |

The reads are fine — caching and replicas did their job. The problem is the write path and the growth rate, which Section 9 turns into a date.

**The decision BookNook makes:** shard `orders`, `order_items`, `payments`, `addresses` and `carts` on **`user_id`**, into 8 physical shards built from **1,024 logical shards** (the reasoning for logical shards is in Lesson 37). `books` is not sharded — at 50,000 rows and ~400 MB it is a reference table copied to every shard. `reviews` is the interesting case and is deliberately deferred to Lesson 36, because the obvious key is the wrong one.

**What BookNook must change in the application:**

1. Every request path must be able to produce a `user_id` before it touches the database. Three endpoints could not (`/orders/{order_id}` lookups from email links, the refunds admin screen, and the warehouse picking queue). Two were fixed by embedding the user ID in the URL token; one became a deliberate scatter-gather with a 2-second budget.
2. `SERIAL` order IDs replaced with **UUID v7**, with a six-week dual-ID period where both columns exist and the old `bigint` stays as a secondary unique key inside its own shard.
3. `UNIQUE(email)` on `users` could no longer be enforced by the database, because `users` is also sharded by `user_id` and email is not the key. BookNook added a tiny `email_directory` table (`email → user_id`), itself sharded on `hash(email)`, written **before** the user row, with a periodic reconciliation job to clean up orphans from failed signups.
4. The monthly finance report, which did `SELECT SUM(total) FROM orders WHERE created_at BETWEEN …`, was moved off OLTP entirely to a nightly export (Phase 3).

### 7.2 Contrasting example — a multi-tenant B2B SaaS

Compare with **Hirelight**, an applicant-tracking SaaS with 12,000 corporate customers. Superficially the same problem; structurally very different.

| | BookNook (B2C) | Hirelight (B2B multi-tenant) |
|---|---|---|
| Natural shard key | `user_id` | `tenant_id` |
| Cardinality | 40,000,000 | 12,000 |
| Distribution | Mild skew (a power-shopper has 400 orders, not 4 million) | **Extreme skew** — the largest tenant has 31 % of all rows |
| Query pattern | ~97 % of queries carry `user_id` | ~99.9 % of queries carry `tenant_id` — perfect pruning |
| Cross-key queries | Common (admin, reporting, search) | Almost never — tenants are legally isolated |
| Right answer | Hash-shard on `user_id` across many small shards | **Directory sharding** (Lesson 36): a tenant→shard map, so big tenants can be given their own dedicated shard |

The lesson: Hirelight's low cardinality would be disqualifying under criterion 1, yet it is the correct key, because its query locality is near-perfect and a directory lets it place tenants manually. Criteria are inputs to judgement, not a checklist to pass. The decisive question is always *"what do the top 20 queries look like?"*, and Hirelight's answer is unusually clean.

Hirelight also gets something BookNook cannot: because each tenant lives entirely on one shard, it can offer per-tenant backup/restore, per-tenant data residency (Lesson 39), and "noisy neighbour" isolation as a **paid product feature**. Sharding stopped being a scaling tax and became a pricing tier.

## 8. Code or configuration example

**This is illustrative teaching code, not production-ready.** It omits connection pooling, retries, timeouts, circuit breakers, metrics, replica selection, transaction management and failure handling — every one of which you need in reality. Use Vitess, Citus, or your database's native sharding before you write this yourself.

First, declarative partitioning *inside* one database — the step before sharding:

```sql
-- PostgreSQL 15+. ONE database, ONE transaction manager. The app does not change.
-- We partition by RANGE on created_at because the dominant admin query is time-bounded
-- and because dropping old data must be O(1), not O(rows).
CREATE TABLE orders (
    order_id    uuid        NOT NULL,          -- UUID v7, generated by the app
    user_id     bigint      NOT NULL,          -- the future SHARD key
    status      text        NOT NULL,
    total_paise bigint      NOT NULL,          -- money in integer paise; never float
    created_at  timestamptz NOT NULL,          -- the PARTITION key
    -- The partition key MUST be part of every unique constraint. This is not a
    -- PostgreSQL quirk; it is a mathematical necessity: an index on one partition
    -- cannot see rows in another, so uniqueness can only be guaranteed per partition.
    PRIMARY KEY (order_id, created_at)
) PARTITION BY RANGE (created_at);

-- Each child holds one month. Bounds are [FROM, TO) -- inclusive start, exclusive end.
CREATE TABLE orders_2026_09 PARTITION OF orders
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
CREATE TABLE orders_2026_10 PARTITION OF orders
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');

-- DEFAULT catches rows outside every defined range. Without it, such an INSERT
-- fails outright. With it, rows silently pile up here -- so alert on its row count.
CREATE TABLE orders_default PARTITION OF orders DEFAULT;

-- Indexes declared on the parent are created on every child automatically,
-- including children created later.
CREATE INDEX ON orders (user_id, created_at DESC);

-- PRUNING IN ACTION: this reads ONE partition out of N.
EXPLAIN (COSTS OFF)
SELECT * FROM orders
WHERE created_at >= '2026-10-01' AND created_at < '2026-11-01' AND user_id = 918273645;
-- Expect: "Index Scan using orders_2026_10_user_id_created_at_idx"  -- a single child.

-- PRUNING DEFEATED: no created_at predicate, so EVERY partition is scanned.
-- This is the #1 way teams make a partitioned table slower than an unpartitioned one.
SELECT * FROM orders WHERE user_id = 918273645;

-- Retention in O(1): detach first so the drop never blocks concurrent readers.
ALTER TABLE orders DETACH PARTITION orders_2023_01 CONCURRENTLY;
DROP TABLE orders_2023_01;   -- milliseconds, versus minutes for a 40M-row DELETE
```

Now application-level **sharding**, with the logical-shard indirection that Lesson 37 depends on:

```python
import hashlib
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# KEY IDEA: never hash directly to a PHYSICAL shard. Hash to a large, FIXED
# number of LOGICAL shards, then map logical -> physical. Moving capacity later
# means editing the map, not rehashing 2 billion rows. 1024 is chosen because it
# is comfortably more logical shards than we will ever have machines.
# ---------------------------------------------------------------------------
LOGICAL_SHARDS = 1024

# The map is data, not code: it lives in etcd/ZooKeeper/a config service so it can
# change WITHOUT a deploy, and so every app server sees the same version.
# Here: 1024 logical shards spread evenly over 8 physical databases.
LOGICAL_TO_PHYSICAL = {lg: lg % 8 for lg in range(LOGICAL_SHARDS)}

PHYSICAL_DSN = {
    0: "postgres://app@shard0.booknook.internal/booknook",
    1: "postgres://app@shard1.booknook.internal/booknook",
    # ... shards 2..7 omitted for brevity
}


def logical_shard(shard_key: int | str) -> int:
    """Map a shard key to one of LOGICAL_SHARDS buckets.

    blake2b, not Python's built-in hash(): hash() is randomly SALTED per process
    (PYTHONHASHSEED), so two app servers would route the SAME key to DIFFERENT
    shards. This is a genuine production outage, not a style preference.
    Any stable hash works -- md5, crc32, xxhash -- as long as every process and
    every language in your stack computes the identical value forever.
    """
    raw = str(shard_key).encode("utf-8")
    digest = hashlib.blake2b(raw, digest_size=8).digest()
    return int.from_bytes(digest, "big") % LOGICAL_SHARDS


def route(shard_key: int | str) -> str:
    """Return the DSN that owns this key."""
    return PHYSICAL_DSN[LOGICAL_TO_PHYSICAL[logical_shard(shard_key)]]


@dataclass
class OrderRepo:
    def get_orders(self, user_id: int, limit: int = 20):
        # SINGLE-SHARD read: fast, simple, transactional, bounded. The goal is
        # for ~97% of production traffic to look exactly like this.
        dsn = route(user_id)
        return query(dsn,
                     "SELECT * FROM orders WHERE user_id = $1 "
                     "ORDER BY created_at DESC LIMIT $2",
                     user_id, limit)

    def find_pending_refunds(self, limit: int = 50):
        # CROSS-SHARD read: no user_id, so we must ask everyone.
        # Cost notes, all quantified in Lesson 37:
        #   * work  = N shards x limit rows (here 8 x 50 = 400 rows fetched for 50)
        #   * p99   = the SLOWEST of N shards, not the average
        #   * a single dead shard makes the answer INCOMPLETE -- decide explicitly
        #     whether that is an error or a partial result, and tell the caller which.
        results = []
        for phys in set(LOGICAL_TO_PHYSICAL.values()):
            results += query(PHYSICAL_DSN[phys],
                             "SELECT * FROM orders WHERE status = 'PENDING_REFUND' "
                             "ORDER BY created_at DESC LIMIT $1", limit)
        # Global ordering can only be reconstructed HERE, after the merge.
        results.sort(key=lambda r: r["created_at"], reverse=True)
        return results[:limit]
```

What makes this a toy: the fan-out is **sequential**, so 8 shards at 20 ms each cost 160 ms instead of 20 ms — real implementations fan out in parallel with a per-shard timeout and an overall deadline. There is no handling of a shard being down, no read-replica selection, no instrumentation of which queries fanned out (you want a metric for exactly that), and `query()` is imaginary.

## 9. Numerical example

**Question: at what date must BookNook shard, and into how many shards?**

Every assumption is stated. Change an assumption and the date changes — that is the point of writing them down.

**Assumptions**

- A1. Current peak write TPS on the primary: **900**.
- A2. Measured write ceiling of the current instance before p99 commit latency exceeds the 20 ms SLO: **4,000 TPS**. (Measured, not guessed — found by replaying production writes at increasing rates.)
- A3. You must never plan to run above **70 %** of a measured ceiling, because headroom absorbs retries, backups, vacuum and traffic spikes. Effective ceiling = 4,000 × 0.70 = **2,800 TPS**.
- A4. Write growth: **2.5× per year**, compounding, observed over the last three years.
- A5. Current OLTP data + indexes: **2.9 TB**. Data grows at the same 2.5×/year.
- A6. Buffer pool (RAM): **512 GB**. The largest instance available is 128 vCPU / 2 TB RAM — a one-time 4× vertical headroom (Lesson 18).
- A7. Target after sharding: no shard above **40 %** of its effective ceiling at launch, giving ~2 years of growth before the next reshard.
- A8. A sharding project of this size takes **6 months** of engineering.

**Step 1 — when does write throughput hit the planning ceiling?**

```
Write TPS after t years = 900 × 2.5^t
Set 900 × 2.5^t = 2,800
            2.5^t = 2,800 / 900 = 3.111
                t = ln(3.111) / ln(2.5)
                  = 1.1350 / 0.9163
                  = 1.239 years ≈ 14.9 months
```

**Step 2 — when does it hit the hard ceiling?**

```
900 × 2.5^t = 4,000
      2.5^t = 4.444
          t = ln(4.444)/ln(2.5) = 1.4917/0.9163 = 1.628 years ≈ 19.5 months
```

So: degradation starts at ~15 months, hard wall at ~19.5 months.

**Step 3 — subtract the project.** With A8 (6 months of work), the project must *start* by month 15 − 6 = **month 9**. You do not get to start when the graph crosses the line; you start when the graph *minus the build time* crosses the line. This is the single most commonly botched part of capacity planning.

**Step 4 — does vertical scaling buy enough time to skip this?** A6 offers 4× the RAM and 2× the vCPU. Assume optimistically that doubles the write ceiling to 8,000 TPS (effective 5,600):

```
900 × 2.5^t = 5,600  →  2.5^t = 6.222  →  t = 1.8265/0.9163 = 1.993 years
```

Vertical scaling buys **≈ 24 months**, then you are on the biggest box in the catalogue with nowhere to go. Honest verdict: it is worth doing *and* it does not remove the need to shard — it converts "shard in 9 months under pressure" into "shard in ~18 months calmly". That is a good trade, and you should say so out loud rather than treating vertical scaling as defeat.

**Step 5 — data size and the working-set ratio.**

```
t=0: 2.9 TB  / 512 GB RAM = 5.7 : 1
t=1: 7.25 TB / 512 GB      = 14.2 : 1
t=2: 18.1 TB / 512 GB      = 35.4 : 1
```

Assume (A9) the hot working set is 8 % of total data — typical for an e-commerce OLTP store where recent orders dominate:

```
t=0: 0.08 × 2,900 GB = 232 GB  → fits in 512 GB. Buffer hit ratio ~99.3%.
t=1: 0.08 × 7,250 GB = 580 GB  → EXCEEDS 512 GB. Hit ratio starts falling.
t=2: 0.08 × 18,100 GB = 1,448 GB → 2.8× the buffer pool. Thrashing.
```

The working set escapes RAM at **about 11 months** — *before* the write ceiling is reached. Two independent clocks, and you must plan against the earlier one.

**Step 6 — how many shards?**

```
Write TPS at the planned launch date (month 18, t = 1.5):
    900 × 2.5^1.5 = 900 × 3.953 = 3,558 TPS

Per-shard budget (A7): 4,000 × 0.40 = 1,600 TPS
Shards needed by writes:  3,558 / 1,600 = 2.22 → 3 shards

Data at t = 1.5: 2.9 TB × 3.953 = 11.5 TB
Per-shard data target: keep the 8% working set inside a 512 GB buffer pool,
    so max data per shard = 512 GB / 0.08 = 6.4 TB; plan at 50% of that = 3.2 TB
Shards needed by data:   11.5 / 3.2 = 3.6 → 4 shards

Binding constraint = data, not writes. Minimum = 4 shards.
```

**Step 7 — round up for growth and for failure.** Taking 4 as the minimum, choose **8 shards**:

```
Per shard at launch: 3,558 / 8 = 445 TPS  (11% of the 4,000 ceiling)
Per shard data:      11.5 TB / 8 = 1.44 TB
Runway before any shard hits the 2,800 effective ceiling:
    445 × 2.5^t = 2,800  →  2.5^t = 6.292  →  t = 1.8376/0.9163 = 2.0 years
Blast radius per shard: 1/8 = 12.5% of users
```

Why 8 and not 4: (a) 4 shards gives only ~0.5 years of runway, which is absurd for a 6-month project; (b) powers of two make a future split-in-half trivial; (c) 12.5 % blast radius is tolerable, 25 % is not. Why not 32: each shard needs 3 instances (1 primary + 2 replicas), so 8 shards is already 24 databases; 32 shards would be 96 databases for a system doing 3,558 writes per second, and the operational cost would exceed the benefit.

**Step 8 — logical shards.** Create **1,024 logical shards** mapped 128-per-physical onto 8 machines. This costs nothing today (it is an integer in a config map) and makes every future capacity change a *map edit plus a data move*, never a rehash. Lesson 37 shows the exact difference in rows moved.

**A number worth noticing.** BookNook also logs `book_events` (impressions and clicks):

```
4,000,000 DAU × 30 events/day = 120,000,000 events/day
120,000,000 × 200 bytes       = 24 GB/day = 8.76 TB/year
```

That single table generates **three times** the annual growth of the entire transactional dataset. The correct response is not to shard it — it is to **remove it from PostgreSQL** and send it to an append-only analytics store (Phase 3, Topics on OLAP, columnar storage and data warehousing). Always check whether the table forcing you to shard actually belongs in that database at all.

## 10. Trade-offs

**What you gain**

- **Write throughput scales near-linearly** with shard count — the only technique in Phase 2 that does.
- **Dataset size per machine falls**, restoring the working-set-to-RAM ratio that makes databases fast.
- **Operational times divide**: backups, restores, upgrades, vacuum and index rebuilds all run per shard, in parallel.
- **Blast radius shrinks**: one shard down is 1/N of users affected, not 100 %.
- **Independent tuning**: a shard holding heavy tenants can get bigger hardware than one holding light tenants.
- **Natural fit for data residency** (Lesson 39): EU users' shards can physically live in the EU.

**What you lose** — restated because it is the exam question: cross-shard joins, global unique constraints, cross-shard transactions, global sorting and aggregation, auto-increment IDs, and simple point-in-time restore. See Section 4.5.

**Operational costs, concretely**

- N× the database instances to monitor, patch, back up, fail over and pay for. 8 shards × 3 instances = 24 PostgreSQL servers.
- N× the replication topologies, each with its own lag to watch (Lesson 34).
- Schema migrations must now run on N databases and be tolerant of being half-applied. A migration that takes 4 minutes on one database is a 32-minute orchestrated rollout where the app must work against both schema versions simultaneously.
- Every new engineer must learn the routing rules before writing their first query, and the system must make "forgot the shard key" loud — ideally a linter or a router that refuses un-keyed queries outside an allowlist.
- Debugging becomes "which shard?" before it becomes "which row?". Put the shard ID in every log line and every trace span.

**When it becomes unnecessary complexity**

When the forcing number does not exist. If the primary is at 20 % CPU, the working set fits in RAM, and the growth curve gives you three years, sharding buys you nothing and costs you a year. The honest version of "it depends" here is specific: **it depends on whether (a) peak write TPS exceeds ~70 % of the measured single-node ceiling within your build time plus six months, or (b) the hot working set exceeds the buffer pool within the same window, or (c) operational windows — backup, restore, upgrade — have grown past what your SLA tolerates.** If none of the three is true, do not shard.

## 11. Failure scenarios

| Failure | What the user sees | How it's detected | Recovery |
|---|---|---|---|
| **One shard's primary dies** | 12.5 % of users get errors on every write; the other 87.5 % are completely unaffected | Per-shard health checks (Lesson 24) and per-shard error-rate alerts; a cluster-wide average will *hide* this, so alert per shard | Promote that shard's replica (Lesson 32); routing map updates; 10–60 s of write unavailability for 1/8 of users |
| **Routing map is stale on some app servers** | Wrong shard queried → "row not found" on data that exists, or worse, a write lands on the wrong shard and is later invisible | Version the map; every app reports its map version; alert on version skew. Also alert on `row not found` rate | Force config refresh; fence writes during map changes; reconcile misplaced rows from the WAL |
| **Shard key missing from a hot query path** | Gradual latency rise everywhere; p99 degrades first (Lesson 16) | A counter incremented on every fan-out query, broken down by endpoint. This is the single most valuable sharding metric | Add the key to the request contract, or add a secondary index table mapping the alternate key → shard |
| **Hot shard** (one key or one tenant dominates) | A subset of users is slow or erroring while dashboards show low average utilisation | Per-shard QPS/CPU, plus a top-N key sampler | Split that shard, salt the key, give the tenant a dedicated shard, or cache it — all covered in Lesson 37 |
| **Cross-shard "transaction" partially applied** | Money debited, order not created (or the reverse) | Reconciliation job comparing invariants across shards; alert on non-zero drift | Compensating transaction (saga) and manual repair. Prevent by design: never span shards in one logical operation (Phase 4, Topics 19–21) |
| **Duplicate IDs from two workers with the same Snowflake worker ID** | Random, baffling data corruption — one order overwrites another | Primary-key violations in a shard, or an alert on duplicate IDs across shards | Fix worker-ID assignment (derive from etcd / StatefulSet ordinal); repair affected rows by hand |
| **Clock skew on an ID generator** | IDs go backwards; time-ordered pagination skips or repeats rows | Monitor NTP offset; the generator must refuse to issue IDs when the clock moves backwards | Block issuance until the clock catches up; never "just use the old timestamp" |
| **Uneven growth** — shard 5 grows 3× faster than shard 2 | Slow for 12.5 % of users, fine for the rest | Per-shard size and growth-rate dashboards | Rebalance logical shards from 5 to 2 — cheap *only* because logical shards exist |
| **Schema migration half-applied across shards** | Errors on some shards, success on others, non-deterministically | Per-shard schema-version check in CI and at app startup | Roll forward; design every migration to be backward-compatible so a half-state is still serviceable |

## 12. Alternatives

| Approach | What it actually does | Scales writes? | Scales data? | App changes | Cost / downside | Use when |
|---|---|---|---|---|---|---|
| **Vertical scaling** (L18) | Bigger machine | Yes, up to a hard ceiling | Yes, to one disk | None | Expensive at the top end; finite; a reboot | You have not yet reached the largest instance |
| **Read replicas** (L32) | Copies for reads | **No** | No (full copy each) | Minor (route reads) | Replication lag, stale reads | Read-heavy, write-light |
| **Caching** (L26–31) | Avoids the query | No (but removes read pressure) | No | Moderate | Invalidation, stampedes | High read:write ratio with repeated keys |
| **Declarative partitioning** (this lesson) | Splits within one DB | No | No | **None** | Planning overhead; key must be in queries | Always — before sharding |
| **Archiving / tiering** | Moves cold rows out | Indirectly | **Yes** | Moderate | Second store to query | Most rows are old and rarely read |
| **Functional / vertical split** | One DB per domain | Yes, by domain | Yes, by domain | Large | Cross-domain transactions die | Several distinct domains share one DB |
| **CQRS + read model** | Separate write and read stores | Partly | Partly | Large | Eventual consistency | Read and write shapes differ wildly (Phase 4) |
| **Horizontal sharding** (this lesson) | Splits across machines | **Yes, near-linearly** | **Yes** | **Large and permanent** | Everything in §4.5 | All of the above are exhausted |
| **NewSQL** (CockroachDB, Spanner, YugabyteDB, TiDB) | Shards automatically, keeps SQL and distributed transactions | Yes | Yes | Small to moderate | Higher write latency (consensus per write, often 5–20 ms); cost; fewer operators who know it | You want horizontal scale without hand-rolling routing, and can accept consensus latency |
| **Managed sharded NoSQL** (DynamoDB, Cassandra, MongoDB sharded) | Sharding is the product | Yes | Yes | Large — data model must change | No joins, limited transactions, access patterns must be fixed up front | Access patterns are known, simple and key-based |

Observe where NewSQL sits: it does not eliminate sharding, it *automates* it. The data is still partitioned into ranges on separate machines, cross-partition transactions still cost more than local ones, and a bad key still creates a hot shard. What you buy is that the database does the routing, splitting and rebalancing for you, and preserves SQL semantics using distributed consensus (Phase 4, Topics on Raft and consensus). What you pay is latency per write and a smaller hiring pool.

## 13. When to use it

1. **Write throughput exceeds a single primary.** You have measured the ceiling, you are within ~70 % of it, and growth says you cross it inside your build time plus a safety margin. This is the textbook case and the one with no alternative.
2. **The working set no longer fits in RAM on the largest available instance.** Buffer-pool hit ratio is falling, disk read IOPS is climbing, and p99 is degrading while average utilisation looks healthy.
3. **Operational windows have broken.** A full backup takes 9 hours, a restore 14, and a major-version upgrade requires a maintenance window your SLA does not permit. Eight 1.5 TB shards fix this even if throughput were fine.
4. **Blast radius is unacceptable.** A single database failure takes down 100 % of a business where 12.5 % would have been survivable. Regulated and enterprise contexts often make this the *primary* driver.
5. **Data residency is legally required.** GDPR, India's DPDP Act, or a customer contract requires EU/India data to stay in-region. Sharding by geography is the mechanism (Lessons 36 and 39).
6. **Multi-tenant isolation is a product feature.** You want to sell "your data on dedicated infrastructure", or to stop one tenant's batch job from affecting others.

## 14. When *not* to use it

1. **You have not exhausted the eight steps in §2.** By far the most common case. Adding an index, fixing an N+1, raising the cache hit ratio or buying a bigger instance solves the problem in a week instead of two quarters.
2. **No stable, high-cardinality, query-present key exists.** If more than ~20 % of your traffic cannot supply the key, you are not building a sharded system, you are building a scatter-gather system that will be slower and less reliable than what you have.
3. **Your workload is read-heavy and already cacheable.** Replicas plus cache scale reads almost indefinitely; sharding adds complexity without touching your bottleneck.
4. **Strong cross-entity transactions are core to the domain.** Double-entry ledgers, inventory reservations across sellers, and anything with a hard global invariant are far better served by a single strong database, or by NewSQL, than by hand-rolled sagas.
5. **The dataset is small and merely busy.** 200 GB at 6,000 TPS is a tuning problem (indexes, connection pooling, batching, a bigger box), not a sharding problem.
6. **The team cannot operate N databases.** If one database is already under-monitored, under-backed-up, and owned by nobody, multiplying it by 24 converts a slow system into an unreliable one.
7. **The offending table does not belong there.** Event logs, clickstreams, audit trails and full-text search indexes are the usual culprits. Move them out (Phase 3) before you reshape your whole architecture around them.

## 15. Common mistakes

**Beginner mistakes**

- **Confusing partitioning with sharding**, then claiming a single-machine `PARTITION BY` setup scales writes. It does not — same WAL, same CPU, same `fsync`.
- **Sharding too early**, before indexes, caching and replicas are exhausted. You will spend a year building a distributed system to solve a missing composite index.
- **Choosing a low-cardinality shard key** — `country`, `status`, `category`, `is_premium`. You cap the shard count and guarantee hot shards.
- **Choosing a mutable shard key.** Updating it means a cross-shard move, which is a cross-shard transaction, which you do not have.
- **Hashing with a language-local hash function.** Python's `hash()` is salted per process; Java's `String.hashCode()` is stable but differs from every other language. Use blake2b/md5/crc32/xxhash and pin it forever.
- **Forgetting that `books` does not need sharding.** Small reference tables should be replicated everywhere, not split.
- **Keeping `AUTO_INCREMENT`** and discovering duplicate IDs across shards in production.
- **Adopting UUID v4 as a sharded primary key** and then blaming sharding for the write collapse caused by random B-tree inserts.
- **Partitioning on a column that the hot queries never filter on**, making every query read every partition — strictly slower than not partitioning.

**Experienced-engineer mistakes**

- **Hashing directly to physical shards** instead of to a large fixed number of logical shards. This single decision determines whether your first reshard is a config change or a six-month outage-prone migration (Lesson 37).
- **Not instrumenting fan-out.** Without a per-endpoint counter of cross-shard queries, scatter-gather creeps in one feature at a time until the whole system is slow and nobody knows why.
- **Averaging metrics across shards.** A cluster-wide "95 % healthy" dashboard hides a completely dead shard. Every shard metric must be dimensioned by shard ID.
- **Ignoring the replica multiplier.** 8 shards does not mean 8 machines; with 2 replicas each it means 24, plus their failover logic, backups and lag monitors.
- **Assuming even key distribution** because the hash is uniform. The hash is uniform over *keys*; load is distributed over *requests per key*, and requests follow a power law.
- **Forgetting global uniqueness** until a duplicate-email bug reaches production, then bolting on a directory service without making it the first write in the sequence.
- **Running schema migrations without a half-applied story.** The app must work against both the old and new schema simultaneously for the entire rollout.
- **Sharding and introducing a new database engine in the same project.** Two irreversible, high-risk changes at once. Do one, stabilise, then the other.
- **Sizing shards for today.** Pick a count that gives ~2 years of runway; resharding is expensive enough that you want to do it rarely.

## 16. Interview perspective

Sharding is where mid-level and senior answers separate most visibly, because the junior answer ("we'll shard the database") and the senior answer ("here's the key, here's what breaks, here's what I'd do first") use the same first five words.

**How to handle it**

- **Earn the right to shard.** Say explicitly: "Before sharding I'd check indexes, caching, replicas and vertical scaling. Let me compute whether we actually need it." Then do the arithmetic out loud, as in Section 9. Interviewers are testing restraint as much as knowledge.
- **Name the forcing number.** "Writes are 3,500/s and one primary tops out near 4,000 — that's the trigger." A number beats an opinion every time.
- **State the shard key and defend it against all four criteria**, by name: cardinality, distribution, query presence, stability. Then add co-location: "orders, order_items and payments all shard on user_id so a checkout stays in one transaction."
- **Volunteer what you lose** before being asked. "This kills cross-shard joins, global uniqueness on email, and `AUTO_INCREMENT`. Here's my plan for each." This is the single highest-signal thing you can say.
- **Mention logical shards unprompted.** "I'd hash to 1,024 logical shards mapped onto 8 machines so rebalancing is a map change." Very few candidates do this, and it is exactly what production systems do.
- **Say what you would *not* shard.** "Books is 50,000 rows — I'd replicate it to every shard as a reference table." Showing where the technique does not apply demonstrates you understand it.
- **Have an ID plan ready.** "UUID v7 for zero coordination, or Snowflake if I need 64-bit keys; not UUID v4, because random inserts destroy B-tree locality."

**Red flags interviewers watch for:** reaching for sharding before any measurement; picking a low-cardinality or mutable shard key; being unable to name a single thing you lose; claiming cross-shard joins "work fine"; confusing partitioning with sharding; keeping auto-increment IDs; ignoring that each shard still needs replicas; and presenting resharding as trivial.

## 17. Knowledge check

Answer these before reading on. Write your answers down — even one line each.

**Conceptual**

1. A teammate says "we enabled PostgreSQL declarative partitioning on `orders` last week, so our write-throughput problem is solved." Explain precisely why this is wrong, name the one resource that is still shared, and describe two real problems that partitioning *did* solve.
2. BookNook is considering `created_at` (truncated to the day) as the shard key for `orders`, reasoning that it spreads 2 billion rows evenly over thousands of days. Evaluate it against all four shard-key criteria and identify the specific failure mode that makes it the worst option on the list.
3. Explain why switching a sharded table's primary key from `SERIAL` to UUID v4 can reduce insert throughput by more than half, and why UUID v7 does not have this problem. Your answer must mention B-tree page locality and the buffer pool.

**Practical scenario**

4. BookNook must support an email link `https://booknook.com/orders/{order_id}` that opens an order page for a logged-out user who then signs in. Orders are sharded on `user_id`, and the URL contains only `order_id`. Describe three different ways to route this request to the correct shard, state the latency and failure cost of each, and say which you would ship and why. Then describe one new failure mode your chosen design introduces.

**Calculation**

5. A different service, Hirelight, has 12,000 tenants, 4 TB of data, 2,200 peak write TPS, and a measured single-primary ceiling of 5,000 TPS. Data and writes both grow 1.8× per year. Its largest tenant holds 31 % of all rows and generates 26 % of writes. RAM per instance is 256 GB and the hot working set is 15 % of data.
   (a) Using a 70 % planning ceiling, how many months until writes force a change?
   (b) In how many months does the working set exceed RAM?
   (c) If Hirelight hash-shards on `tenant_id` into 8 shards, what share of writes lands on the shard holding the largest tenant, and what is that shard's TPS today and in 24 months?
   (d) What is the minimum number of shards that would make the largest tenant's shard stay under the 70 % planning ceiling for 24 months — and what does your answer tell you about hash sharding here?
   Show every step and state every assumption you add.

Reply with your answers and I'll assess them, correct anything shaky, and give model answers before Lesson 36.

---

**Progress checklist**

Phase 1 — Internet and System-Design Foundations: all 17 topics `[x]` complete.

Phase 2 — Scaling
- [x] 1–17. Vertical scaling through replication lag and read-after-write consistency
- [x] 18. Database partitioning and sharding
- [ ] 19. Range, hash, directory, geographic sharding
- [ ] 20. Resharding, hot partitions, cross-shard queries
- [ ] 21. Consistent hashing and virtual nodes
- [ ] 22. Autoscaling and multi-region architecture

Phases 3–5: not started.

**NEXT LESSON: Phase 2, Topic 19 — Range, hash, directory, geographic sharding**




# Lesson 36 — Range, Hash, Directory, and Geographic Sharding

*Phase 2, Topic 19*

## 1. Simple definition

A **sharding strategy** is the rule that converts a shard key into a shard number. Lesson 35 established *that* you shard and *which column* you shard on. This lesson is about the function in the middle:

> `shard_id = f(shard_key)`

Everything that makes sharding pleasant or painful comes from the choice of `f`.

Terms defined before use:

- **Shard**: one independent database instance (or replica set) holding a disjoint subset of the rows. Lesson 35.
- **Shard key**: the column, or tuple of columns, whose value is fed into `f`. Also called a *partition key* or *distribution key*.
- **Shard map / routing table**: the data structure that tells the application which shard a key lives on.
- **Routing layer**: the code that evaluates `f` — a library inside the app, a proxy (Vitess, ProxySQL), or a feature of the database itself (Citus, MongoDB `mongos`, DynamoDB).
- **Skew**: the ratio of the busiest shard's load to the average shard's load. A skew of 1.0 is perfect; 8.0 on an 8-shard cluster means one shard is doing *all* the work.
- **Scatter-gather**: a query that cannot be answered by one shard, so the router sends it to many shards and merges the results. Covered in depth in Lesson 37.

There are exactly four families of `f` in wide production use:

| Family | `f` in one line |
|---|---|
| **Range** | Sort the key space; give each shard a contiguous interval. |
| **Hash** | `shard = hash(key) mod N`, or a hash ring (Lesson 38). |
| **Directory** | Look the key up in an explicit table that says where it lives. |
| **Geographic** | Shard by a location attribute — country, region, data-residency zone. |

Everything else is a composite or a refinement of these four.

## 2. Why it exists

Lesson 35 ended with a shard key chosen for BookNook's `orders` table. But choosing `user_id` as the key does not yet tell you where `user_id = 48_201_773` lives. You still need a mapping, and the four mappings above have *radically* different properties.

The reason this is a whole lesson rather than a footnote is that **the strategy decision is close to irreversible**. Changing the shard key means rewriting every row. Changing the strategy — range to hash, say — also means rewriting every row. Lesson 37 shows that a well-run migration of a 4 TB sharded table is a multi-week project with a dual-write window, a backfill, a verification phase, and a rollback plan. You get this decision cheaply exactly once, at the start.

Three concrete failures motivate the whole lesson:

1. **A range shard on a monotonically increasing key puts 100 % of writes on one shard, forever, no matter how many shards you add.** Section 9 quantifies this: BookNook's write ceiling with 8 range shards on `order_id` is identical to its write ceiling with 1 shard. You paid for eight machines and bought zero write throughput.
2. **A hash shard makes `WHERE created_at BETWEEN ... ` a scatter-gather over every shard.** A query that was one index range scan becomes N queries plus a merge, with the tail-latency amplification of Lesson 16.
3. **A geographic shard is *never* evenly loaded**, because populations and time zones are not evenly distributed. You accept 30–40 % overprovisioning in exchange for latency and legal compliance.

Each of those is a correct, deliberate trade in some context and a catastrophe in another. The job is to know which context you are in.

## 3. Real-world analogy

**A chain of warehouses storing customer parcels.**

- **Range sharding** = "Warehouse 1 holds surnames A–F, Warehouse 2 holds G–L…". Finding everyone whose surname starts with "Mc" is one trip. But if your business signs up a wave of new customers and you file by *signup date* instead of surname, every new parcel for the next month goes to the same warehouse while the other five sit empty.
- **Hash sharding** = a coat check. You hand over a coat, a machine stamps a number, and the number tells you the rack. Beautifully even. But "show me every coat checked in this afternoon" means walking every rack in the building.
- **Directory sharding** = a hotel front desk with a guest register. Nothing about the guest's name determines the room; the register says where they are. You can move any guest to any room by editing one line. But every single visitor must stop at the front desk first, and if the register burns, nobody can find anyone.
- **Geographic sharding** = regional distribution centres. A Munich customer's parcels live in the Munich warehouse: fast delivery, and German goods stay under German law. But the Munich warehouse is busy at 18:00 CET while the Seattle one is asleep, and you must build both for their own peak.

**Where the analogy breaks down:**

| Warehouses | Sharded database |
|---|---|
| A parcel is physically in one place, and you know which when you look at it | A row's location is derivable only by evaluating `f`; if two services evaluate `f` differently, you get silent data loss |
| Moving a parcel takes a van and an afternoon | Moving a row means dual-writing, backfilling, verifying and cutting over while reads and writes continue (Lesson 37) |
| Warehouses can be any size; you just build a bigger one | Shards are usually required to be interchangeable and uniform, because the routing function assumes it |
| The register is consulted by a human who can improvise if it's wrong | The routing layer is consulted 20,000 times a second and has no judgement; a stale entry routes writes into the wrong shard at full speed |
| A "range" over surnames is a human convenience | A range over a *monotonically increasing* key is a structural defect: the insertion point and the hot spot are the same place, permanently |
| You can ask all six warehouses and wait | Asking all N shards makes your latency the **maximum** of N samples, not the average — the tail amplification of Lesson 16 |
| No legal difference between racks | Geographic shards exist partly because moving a row across a border can be *illegal*, not merely slow |

That last row is the one most engineers underweight. GDPR-style data residency turns a performance decision into a compliance decision, and compliance decisions are not negotiable with a benchmark.

## 4. Detailed technical explanation

### 4.1 Range sharding

Define boundaries over an ordered key space and assign each shard a half-open interval.

```
shard 0 : key <  1_000_000
shard 1 : 1_000_000 <= key <  2_000_000
shard 2 : 2_000_000 <= key <  3_000_000
...
```

The shard map is tiny — N boundary values — and routing is a binary search, O(log N), typically under 100 ns in process.

**What it buys you.** Range queries are local. `WHERE order_id BETWEEN 1_100_000 AND 1_150_000` touches exactly one shard. `ORDER BY order_id LIMIT 20` starts at one shard. Bulk deletion of old data becomes "drop shard 0", which is a metadata operation rather than a 400-million-row `DELETE`. For time-series data this is the single most valuable property in the entire lesson.

**Splitting is cheap and local.** If shard 2 grows too large, split its interval at the median and move half its rows to a new shard. No other shard is affected. This is why range sharding is the native model in HBase, Bigtable, CockroachDB, TiDB and YugabyteDB — they split ranges automatically when a range exceeds a size or load threshold (typical default: 512 MB in CockroachDB, 96 MB in TiKV regions).

**The monotonically-increasing-key hot spot.** This is the defining failure of range sharding and you must be able to explain it precisely.

A key is **monotonically increasing** if every newly inserted row has a key greater than every previously inserted row. `SERIAL` / `AUTO_INCREMENT` primary keys, `created_at` timestamps, Snowflake IDs, UUID v7, ULIDs and KSUIDs are all monotonic or near-monotonic (Lesson 35 covered why UUID v7 was *designed* to be monotonic, for B-tree locality).

If the shard key is monotonic, then by definition every insert's key falls into the **highest** range. Therefore:

```
Writes to the last shard  = 100 % of inserts
Writes to all other shards= 0 % of inserts
Skew                      = N  (where N is the shard count)
```

And here is the part people miss: **adding shards makes the skew worse, not better.** With 8 shards the skew is 8×; with 32 shards the skew is 32×. The last shard's write rate is unchanged. The cluster's aggregate write ceiling equals the write ceiling of one machine, for all N. You have bought storage capacity and zero write capacity. Section 9 puts numbers on this.

Reads suffer a softer version of the same problem. "Recent" data is nearly always the hottest data — BookNook's order-status pages, the `/orders/recent` admin view, the fraud checks — so the newest shard holds a small fraction of the rows and a large fraction of the reads.

**Secondary range problem: non-uniform key distribution.** Range sharding on `email` with boundaries at `a…`, `b…`, `c…` gives you shards of wildly different sizes, because surnames and email prefixes are not uniform. `s` and `m` are heavy; `q`, `x` and `z` are nearly empty. You must choose boundaries from the actual data distribution, and re-choose them as the distribution drifts.

**Mitigations that actually work:**
- Make the range key non-monotonic: shard on `hash(user_id)` ranges rather than `order_id` ranges. At that point you have a hash shard with a range-shaped map, which is a perfectly reasonable hybrid (and is what CockroachDB's `HASH SHARDED INDEX` does explicitly).
- Use a composite key whose *leading* component is non-monotonic: `(tenant_id, created_at)`. Writes spread across tenants; range scans within a tenant stay local. Section 4.5.
- Pre-split: create ranges ahead of time at the expected future boundaries, so the database is not constantly splitting one hot range under load. This reduces split churn but does **not** fix the hot spot — all writes still land on the newest range.

### 4.2 Hash sharding

```
shard_id = hash(shard_key) mod N
```

The hash must be fast, deterministic across languages and versions, and well-distributed. Use a non-cryptographic hash designed for this — **MurmurHash3**, **xxHash**, or **CityHash**. Do **not** use a language's built-in `hash()`: Python's string hash is randomised per process by default (PYTHONHASHSEED), and Java's `String.hashCode()` has poor avalanche properties on short, structured strings. A deterministic cross-language hash is a hard requirement, because your Python batch job and your Go API must agree on where a row lives.

**What it buys you.** With a good hash and a key of reasonable cardinality, distribution is near-uniform. For K keys over N shards the expected per-shard count is K/N and the standard deviation is approximately `sqrt(K/N)`. At K = 40,000,000 users and N = 16, each shard holds 2,500,000 ± 1,581 — a relative deviation of 0.06 %. Statistically, hash sharding solves distribution completely.

Note the crucial qualifier: **distribution of keys**, not distribution of *load*. If one key is 40 % of your traffic — a celebrity user, a bestselling book — hashing places that key on one shard and that shard burns. Hash sharding does nothing about hot *keys*; it only fixes hot *ranges*. Lesson 37 is largely about this.

**What it costs you.**

1. **Range scans are gone.** `WHERE created_at > now() - interval '1 day'` has no locality; the router must ask all N shards. So does `ORDER BY created_at LIMIT 20`. So does `MIN`, `MAX`, `COUNT(*)` and every `GROUP BY` that is not on the shard key.
2. **Resharding is brutal.** `mod N` means changing N remaps nearly every key. The exact fractions are computed in Lesson 37 §9; the headline is that going from 8 shards to 9 moves 88.9 % of all rows.
3. **Ordering is lost.** The physical layout carries no semantic order, so you cannot drop old data by dropping a shard. Deleting last year's orders is a full `DELETE` across every shard.

**The standard fix for (2) — logical shards.** Instead of hashing directly onto physical shards, hash onto a large, *fixed* number of **logical shards** (also called virtual buckets, vnodes, or slots), then maintain a small map from logical shard to physical shard.

```
logical  = hash(key) mod 1024        # NEVER changes
physical = logical_to_physical[logical]   # a 1024-entry array you can edit
```

Now resizing the cluster means reassigning some logical shards, not rehashing keys. Going from 8 to 9 physical shards moves 1024/9 ≈ 114 logical shards ≈ 11.1 % of the data. Choose the logical shard count once, generously — 1,024 or 4,096 are typical — because it is the one number you can never change. Redis Cluster hardcodes 16,384 slots for exactly this reason. This is the same idea that Lesson 38 (**Phase 2, Topic 21 — Consistent hashing and virtual nodes**) formalises with a hash ring.

### 4.3 Directory (lookup-table) sharding

Keep an explicit mapping from key to shard, stored as data.

```
shard_map(entity_id BIGINT PRIMARY KEY, shard_id SMALLINT)
```

Routing is now a lookup, not a computation.

**What it buys you — maximum flexibility.** Because the mapping is data, you can:
- Move one tenant to its own shard by updating **one row**. No rehash, no range split.
- Give your largest customer a dedicated shard for isolation (the standard answer to the noisy-neighbour problem in B2B SaaS).
- Run shards of different sizes and capacities, weighting assignment accordingly.
- Migrate incrementally, entity by entity, with per-entity rollback.
- Keep European tenants on EU shards and everyone else on hash-assigned shards — a *hybrid* policy that no pure function can express.

This is the only strategy where "move this specific hot thing somewhere else" is a one-line change. That is a very large operational advantage and it is why Slack, Notion, Figma and most multi-tenant SaaS platforms with large enterprise customers use directory or directory-assisted sharding.

**Cost 1 — an extra hop.** Every query needs the mapping before it can be routed. Uncached, that is a network round trip: typically 0.3–1.5 ms within a datacentre (Lesson 14's latency table). In practice you cache the map aggressively:
- The map is small. 40 M entries × ~40 bytes ≈ 1.6 GB — it fits in RAM on every app server for mid-size systems, and entirely in Redis for larger ones.
- Mappings change rarely (only on migration), so a long TTL is safe, and you invalidate on change.
- With a 99.5 % in-process cache hit rate, average added latency is roughly `0.995 × 0.02 ms + 0.005 × 1.2 ms ≈ 0.03 ms`. Negligible on average — but see Lesson 16: the *average* is not the user experience, and the 0.5 % of requests that miss pay the full round trip.

**Cost 2 — the directory is a single point of failure, and it is on the critical path of 100 % of requests.** This is the serious objection. If 100 % of queries depend on the directory, your system's availability is bounded above by the directory's availability. Serial dependency multiplies:

```
App tier     99.95 %
Directory    99.90 %
Combined     0.9995 × 0.9990 = 99.85 %  →  13.1 hours/year of downtime
(vs 4.4 hours/year for the app tier alone)
```

You have made your whole system *less* available than its weakest new component. Mitigations, in order of value:

1. **Cache the map in every app process** with a long TTL and serve stale on directory failure. If the directory is down and the map has not changed, nothing breaks. This converts the directory from a hard dependency to a soft one, and it is the single most important design decision in directory sharding.
2. **Replicate the directory** (Lesson 31/32) with automatic failover; read from replicas.
3. **Make the directory boring**: a tiny, highly replicated key-value store with a trivial schema and no business logic. Many teams use the same store as their service discovery (etcd, Consul, ZooKeeper).
4. **Version the map** so a stale cache can be *detected*. Stamp every map with a monotonically increasing epoch; include the epoch in writes; have the shard reject writes carrying a stale epoch. Without this, a stale cache silently writes rows to the wrong shard during a migration — a corruption class that is extremely hard to detect after the fact.

**Cost 3 — map size at very high cardinality.** Directory sharding per *user* at 40 M users is fine. Per *order* at 2 billion rows is not: the map becomes larger than some of the shards it indexes, and you have re-created the original problem. **The directory must be keyed on a low-cardinality entity** — tenant, user, region — not on the highest-cardinality row.

### 4.4 Geographic sharding

Shard on a location attribute: `region`, `country`, `data_residency_zone`. Each shard is a full stack (database, often app tier and cache too) in or near that region.

**Benefit 1 — latency.** A round trip from Frankfurt to us-east-1 is typically 85–95 ms; Frankfurt to eu-central-1 is 1–5 ms. A page that makes 4 sequential database round trips saves roughly `4 × 85 = 340 ms` by being served locally. That is a user-visible difference, and no amount of caching fixes writes.

**Benefit 2 — data residency and sovereignty.** GDPR, India's DPDP Act, China's PIPL, and many sectoral rules (health, financial) restrict where personal data may be *stored* and *processed*. Geographic sharding is the architecture that makes "EU customer data never leaves the EU" a structural property rather than a policy document. This is frequently the actual reason, with latency as a bonus.

**Benefit 3 — blast radius.** A region-wide outage takes out that region's customers only. Combined with asynchronous cross-region replication (Lesson 32) you get a disaster-recovery story almost for free.

**The inherent cost — skew is guaranteed.** Two independent sources:

1. **Population skew.** Typical consumer-internet split for a US-origin product: North America ~55 % of traffic, Europe ~28 %, Asia-Pacific ~17 %. You will never see 33/33/33.
2. **Diurnal skew.** Each region peaks in its own evening. A typical consumer peak-to-trough ratio within one region is 3–4×. Because the peaks are offset in UTC, the *global* peak is substantially lower than the sum of the *regional* peaks — which means a single global pool needs less total hardware than a set of regional pools. Section 9 computes this; the typical penalty is 30–40 % extra capacity.

**Second cost — cross-region operations.** A user who moves from Germany to Japan must be *migrated*, not re-routed. A query like "global sales this quarter" is a cross-region scatter-gather with 100–250 ms inter-region latency in it. Global uniqueness (one email address across all regions) needs either a global directory or a partitioned namespace. Lesson 35 already warned that global unique constraints are among the things you permanently lose.

**Third cost — operational multiplication.** Three regions means three of everything: deploy pipelines, monitoring dashboards, on-call rotations, schema migrations that must be applied N times and may be half-applied. Treat the regional count as a multiplier on operational cost, typically 1.5–2× rather than 3× (tooling amortises), but never 1×.

### 4.5 Composite and hierarchical shard keys

Real systems rarely use a single bare column.

**Composite shard key** — hash a tuple:

```
shard = hash(tenant_id, bucket) mod N
```

Used when one component has too few distinct values to distribute well (10 tenants over 16 shards) or too much skew (one tenant is 30 % of the data). Adding a second component splits the heavy value across multiple shards. The price: any query that knows only `tenant_id` now fans out to all the buckets. This is **salting**, and Lesson 37 treats it properly because it is primarily a hot-partition remedy.

**Hierarchical sharding** — two levels with different functions:

```
Level 1 (which shard?):      hash(user_id) mod N        ← even distribution
Level 2 (which partition
         inside the shard?): RANGE (created_at)         ← local range scans,
                                                          cheap bulk deletion
```

This is the configuration most mature systems converge on, and it is worth internalising: **hash across machines, range within a machine.** You get even write distribution from the hash, and you still get "drop last year's partition" and efficient `WHERE user_id = ? AND created_at > ?` from the local range partitioning (which is exactly the declarative partitioning from Lesson 35, applied inside each shard).

**Key-grouping / entity groups.** Choose a shard key such that everything you need to read together lands together. If `users`, `orders`, `order_items` and `addresses` all shard on `user_id`, then the entire "my account" page is answerable by **one** shard with ordinary local joins. Google's Spanner calls these *interleaved tables*; DynamoDB calls it *single-table design*. The general principle: **the shard key should be the access-path root of your dominant query**, not the primary key of the table.

### 4.6 Comparison

| Strategy | Even distribution | Range query support | Resharding cost | Fails when |
|---|---|---|---|---|
| **Range** | Poor — depends entirely on key distribution; **catastrophic** (skew = N) on a monotonic key | **Excellent** — one shard answers `BETWEEN`, `ORDER BY`, `LIMIT`; dropping old data is a metadata op | **Low** — split one range at its median; no other shard touched | The key is monotonic (`SERIAL`, `created_at`, Snowflake ID), or the key's distribution is lumpy (`email`, `country`) |
| **Hash** | **Excellent** — ±0.06 % at 40 M keys over 16 shards | **None** — every range query becomes an N-way scatter-gather with Lesson 16 tail amplification | **Very high** with raw `mod N` (88.9 % of rows move on 8→9); **low** with logical shards or a hash ring | A single key is hot (celebrity problem); or you need ordered scans, `MIN`/`MAX`, or bulk time-based deletion |
| **Directory** | **Controllable** — you can rebalance to any distribution you choose, including deliberately uneven | Only if you also range-order the assignment; by default none | **Lowest** — move one entity by updating one row; per-entity rollback | The directory is unavailable or stale (SPOF on 100 % of requests); or the key's cardinality is so high the map rivals the data |
| **Geographic** | **Poor by construction** — 55/28/17 population split plus 3–4× diurnal swing | Within a region only | **Medium-high** — moving a user across regions is a data migration, sometimes a legal event | Users are globally mobile; you need global aggregates or global uniqueness; or one region dominates and you cannot scale *within* it |

Read that table as a menu of *failure modes to choose between*, not a ranking. There is no row without a "fails when".

## 5. Request / data flow

A BookNook customer loads `/orders` after signing in. `orders` is hash-sharded on `user_id` across 16 shards, with `books` kept as an unsharded replicated reference table and `reviews` hash-sharded on `book_id`.

1. The request arrives at the load balancer and is routed to an app server (Lessons 21–24).
2. The app validates the session cookie and extracts `user_id = 48_201_773` (Lesson 2). **The shard key must come from the authenticated identity, not from a URL parameter** — otherwise a user can address another user's shard.
3. The routing library computes `logical = murmur3_32(b"48201773") mod 1024` → `logical = 617`.
4. It looks up `logical_to_physical[617]` in a 1,024-entry array held in process memory → `physical shard 9`. Cost: one array index, well under 100 ns. No network call.
5. It resolves `shard 9` to a connection pool. Because the request is a read, it selects a **read replica** of shard 9 (Lesson 31), unless the session carries a recent-write marker, in which case it reads the primary to preserve read-after-write consistency (Lesson 33).
6. It issues one local query: `SELECT ... FROM orders WHERE user_id = 48201773 ORDER BY created_at DESC LIMIT 20`. Inside shard 9, `orders` is range-partitioned by month, so the planner touches only the newest one or two local partitions.
7. The shard returns 20 rows in ~4 ms. **No scatter-gather occurred**, because the shard key was known. This is the whole point of choosing `user_id`.
8. The rows reference 20 `book_id` values. `books` is a 40 GB reference table replicated in full to every shard, so the app issues a second query to shard 9: `SELECT id, title, cover_url FROM books WHERE id = ANY($1)`. Still one shard, still a local join.
9. The page also shows "3 reviews you wrote". Those live in `reviews`, sharded on `book_id`, so the 20 book IDs hash to up to 20 different shards. The app instead reads the denormalised `user_review_summary` table — co-located on shard 9 by `user_id` — avoiding a 20-way fan-out. This denormalisation exists *solely* because of the shard-key choice.
10. The response is assembled and cached in the application cache with key `orders:v3:48201773`, TTL 30 s (Lessons 27–30).
11. The user clicks "Cancel order". The app writes `UPDATE orders SET status='cancelled' WHERE order_id = ... AND user_id = 48201773` — note the shard key is carried in the `WHERE` clause so the router can target shard 9 without a lookup.
12. The write goes to shard 9's **primary**. The session records `last_write_shard=9, lsn=...` so step 5 can force a primary read for the next few seconds (Lesson 33).
13. The cache key from step 10 is invalidated (Lesson 29).

The decisive observation is steps 7 and 9: with the right shard key, the common path touches exactly one shard; with the wrong one, the same page becomes a 20-way gather. Nothing about the hardware changed.

## 6. ASCII diagram

```
RANGE SHARDING on a monotonic key  —  the structural hot spot
  order_id grows forever ─────────────────────────────▶
  ┌───────────┬───────────┬───────────┬───────────┐
  │ shard 0   │ shard 1   │ shard 2   │ shard 3   │
  │ 0–500M    │ 500M–1B   │ 1B–1.5B   │ 1.5B–2B   │
  │ COLD      │ COLD      │ COLD      │ ███ HOT ██│◀── 100% of INSERTs
  │ 0 w/s     │ 0 w/s     │ 0 w/s     │ 3,200 w/s │    100% of recent READs
  └───────────┴───────────┴───────────┴───────────┘
  Adding shard 4 does NOT help: inserts simply follow the new maximum.
  Cluster write ceiling with N shards == write ceiling with 1 shard.


RANGE SHARDING done right  —  key is non-monotonic
  ┌───────────┬───────────┬───────────┬───────────┐
  │ hash 0-1k │ 1k–2k     │ 2k–3k     │ 3k–4k     │
  │ 800 w/s   │ 800 w/s   │ 800 w/s   │ 800 w/s   │  skew ≈ 1.0
  └───────────┴───────────┴───────────┴───────────┘


HASH SHARDING  —  even keys, no order
         key = user_id 48201773
                   │
          murmur3(key) mod 1024 = 617        ← logical shard (NEVER changes)
                   │
      logical_to_physical[617] = 9           ← a 1,024-entry array you CAN edit
                   │
  ┌────┬────┬────┬─▼──┬────┬────┐
  │ s0 │ s1 │ …  │ s9 │ …  │ s15│   every shard ≈ 2.5 M users ± 1,581
  └────┴────┴────┴────┴────┴────┘
  SELECT ... WHERE user_id = ?   →  1 shard.          GOOD
  SELECT ... WHERE created_at > ? →  16 shards + merge. TAIL AMPLIFIED (L16)


DIRECTORY SHARDING  —  flexible, but one more thing on the critical path
   app ──1──▶ ┌──────────────────┐
              │  shard directory │  tenant 8821 → shard 3
              │  (replicated,    │  tenant 9104 → shard 7  (dedicated: big customer)
              │   cached in app) │  tenant 1277 → shard 3
              └────────┬─────────┘
   app ◀─2── shard 7   │
        ──3──▶ ┌───────▼───┐
               │  shard 7  │   Move a tenant = UPDATE one row.
               └───────────┘   Directory down + no cache = EVERYTHING down.


GEOGRAPHIC SHARDING  —  latency and law; never evenly loaded
   ┌────────── us-east-1 ──────────┐  55% of traffic   peak 20:00 EST
   │  app + cache + db (US users)  │  11,000 req/s peak
   └───────────────────────────────┘
   ┌────────── eu-central-1 ───────┐  28% of traffic   peak 20:00 CET
   │  app + cache + db (EU users)  │  5,600 req/s peak   ← GDPR boundary
   └───────────────────────────────┘
   ┌────────── ap-southeast-1 ─────┐  17% of traffic   peak 20:00 SGT
   │  app + cache + db (APAC users)│  3,400 req/s peak
   └───────────────────────────────┘
   Sum of regional peaks = 20,000.  Observed GLOBAL peak = 14,500.
   Geographic sharding therefore costs ~38% more hardware. That is the price
   of 85 ms → 5 ms, and of being allowed to operate in the EU at all.


HIERARCHICAL  —  hash across machines, range within a machine
   hash(user_id) ──▶ shard 9 ──▶ ┌ orders_2026_01 ┐
                                 │ orders_2026_02 │ ← local range partitions
                                 │ orders_2026_03 │   DROP = instant purge
                                 └────────────────┘
```

## 7. Detailed example

### BookNook — choosing a strategy for three tables

Recap of the system (continuing from Lesson 35): 40 M users, 18 M books, 2.0 B rows in `orders` (4.0 TB), 400 M rows in `reviews`, peak 20,000 application requests/second and 3,200 database writes/second. A single primary tops out at approximately 5,000 writes/second; the planning ceiling is 70 % of that, i.e. 3,500.

**Table 1 — `orders`.**

*The obvious choice: range-shard on `order_id`.* It is the primary key, it is already indexed, the ranges are trivially computed, and "2 billion rows split into 8 ranges of 250 M" sounds perfectly balanced. It is the worst available option, for two independent reasons.

- `order_id` is monotonic, so 100 % of inserts hit the newest shard. Section 9 Part A shows the cluster's write ceiling is unchanged from one machine and the headroom runs out in under two months.
- The dominant read is "show me *my* orders", keyed on `user_id`. Under range-on-`order_id`, a user's orders are scattered across every shard, so the single most common authenticated page becomes an 8-way scatter-gather. BookNook would be paying the tail-latency penalty of Lesson 16 on its most-loaded endpoint.

*The chosen strategy: hash on `user_id`, via 1,024 logical shards onto 16 physical shards, with local range partitioning by month inside each shard.*

- Writes spread evenly: 3,200/s ÷ 16 = 200/s per shard, 4 % of each shard's ceiling.
- "My orders" is one shard, one index lookup, ~4 ms.
- Monthly local partitions keep `WHERE created_at > ?` cheap within a shard and make data retention a `DROP PARTITION`.
- The `order_id` lookup problem (an email link contains only `order_id`) is solved as Lesson 35 described: embed the shard identifier in the generated ID, so `order_id` is self-routing. No directory needed.

**Table 2 — `books`.**

*The obvious choice: hash-shard on `book_id`, because that is what we did for everything else.* This is wrong, and the reason is the most useful single idea in this lesson: **the first question is not "which strategy", it is "does this table need sharding at all".**

- `books` is 18 M rows, roughly 40 GB including cover metadata. That fits comfortably in RAM on a single instance.
- It is read-heavy and almost never written: maybe 2,000 new or updated books a day, which is 0.02 writes/second. There is no write-scaling problem to solve.
- It is joined by `orders`, `reviews`, search, recommendations and the cart. Sharding it turns every one of those joins into a cross-shard join.

The correct answer is: **do not shard `books`. Replicate it in full to every shard** as a *reference table* (Citus calls this a `reference table`; Vitess calls it an `unsharded keyspace`; the generic term is a *dimension table*). Each of the 16 shards carries its own 40 GB copy — 640 GB of total storage, which is 16 % of the 4 TB `orders` footprint and an obviously good trade for turning 16-way joins into local joins. Updates are published to all shards by change-data-capture, with typical propagation lag of seconds (Lesson 32's asynchronous replication, with the same staleness caveats from Lesson 33).

**Table 3 — `reviews`.**

*The obvious choice: hash-shard on `user_id`, to match `orders` and keep a user's data in one entity group.* This is the case where the obvious answer is most seductive and most expensive. Work out the actual access pattern:

| Query | Share of review reads | Under `user_id` sharding | Under `book_id` sharding |
|---|---|---|---|
| "Reviews on this book page" | ~95 % | **16-way scatter-gather** | 1 shard |
| "Reviews I have written" | ~4 % | 1 shard | 16-way scatter-gather |
| "Average rating for this book" | ~1 % (usually cached) | 16-way gather + merge | 1 shard, local aggregate |

Book pages are BookNook's highest-traffic endpoint at roughly 20,000 requests/second. Sharding `reviews` on `user_id` would turn that into `20,000 × 16 = 320,000` shard queries per second — a 16× multiplier on database load for the single busiest path in the product, plus the tail amplification of Lesson 16 (with a 1 % per-shard slow rate, ~15 % of book pages would contain at least one slow shard).

So `reviews` shards on `book_id`, and the 4 % case ("reviews I have written") is handled by a small denormalised `user_review_summary` table co-located with the user on the `orders` shards. That is the trade: **optimise the shard key for the dominant query, and buy back the minority query with denormalisation.**

Two consequences to carry into Lesson 37:
- `book_id` is high-cardinality (18 M) but **not uniformly accessed**. A newly released bestseller can take 40 % of all review traffic. Hash sharding places it on exactly one shard. That is the celebrity problem, and it is Lesson 37's main subject.
- BookNook keeps a small **directory** alongside the hash function, mapping a handful of specific `book_id` values to override shards. On the day a mega-release lands, one `UPDATE` moves it to a dedicated shard. The hash handles 17,999,990 books; the directory handles the ten that matter.

### Contrasting example — a fleet telemetry platform

A logistics company ingests 180,000 sensor readings per second from 60,000 vehicles into a `telemetry` table. Retention is 90 days. The two queries that matter are "last 15 minutes for vehicle X" and "drop everything older than 90 days".

Here the conclusions **invert**:

- **Range sharding on time is correct**, and the monotonic hot spot is *accepted deliberately* — because this workload is append-only, uniform-cost, and the newest range is also the only range being read. The hot shard is hot for both reads and writes, which is a cache-friendly property, not a problem.
- **Retention is the dominant operational cost.** Deleting 90-day-old rows from a hash-sharded table would mean a continuous `DELETE` of ~15 billion rows/quarter across every shard, generating vacuum/compaction load forever. With time ranges, retention is `DROP` on yesterday's oldest chunk: a metadata operation taking milliseconds. This single property is worth more than perfect write distribution.
- The write hot spot is solved not by abandoning ranges but by a **composite key**: `(hash(vehicle_id) mod 16, time_bucket)`. Sixteen concurrent "newest" ranges instead of one. Writes spread 16×; retention still drops whole chunks; "last 15 minutes for vehicle X" still hits exactly one chunk because `vehicle_id` is the leading component.

This is what purpose-built time-series stores do internally — TimescaleDB calls the result "space-time partitioning", InfluxDB calls them "shard groups". The lesson: **BookNook's answer (hash across machines, range within) and telemetry's answer (range across machines, hash as a sub-divider) are the same two primitives composed in opposite order, because the dominant query and the dominant operational cost are different.** Strategy follows access pattern; it does not follow fashion.

## 8. Code or configuration example

> Illustrative only — not production-ready. Error handling, connection management, retries, timeouts, observability, authorisation and schema migration tooling are all omitted for clarity. Do not copy this into a real system.

### 8.1 A routing layer covering all four strategies

```python
import bisect
import mmh3  # MurmurHash3: fast, deterministic ACROSS LANGUAGES AND VERSIONS.
             # Never use Python's built-in hash() for sharding -- it is
             # randomised per process (PYTHONHASHSEED), so two app servers
             # would disagree about where a row lives. That is silent data loss.

# ---------------------------------------------------------------- RANGE
class RangeRouter:
    """Contiguous intervals over an ordered key space."""

    def __init__(self, boundaries, shards):
        # boundaries = [1_000_000, 2_000_000, 3_000_000]
        # shards     = ["s0", "s1", "s2", "s3"]  (always len(boundaries) + 1)
        self.boundaries = boundaries
        self.shards = shards

    def route(self, key: int) -> str:
        # Binary search: O(log N), ~50 ns. The shard map is N numbers.
        return self.shards[bisect.bisect_right(self.boundaries, key)]

    def route_range(self, lo: int, hi: int) -> list:
        # THE PAYOFF: a range query touches a CONTIGUOUS SLICE of shards,
        # usually exactly one. Compare with HashRouter.route_range below.
        i = bisect.bisect_right(self.boundaries, lo)
        j = bisect.bisect_right(self.boundaries, hi)
        return self.shards[i:j + 1]


# ---------------------------------------------------------------- HASH
class HashRouter:
    """Hash onto a FIXED number of logical shards, then map to physical."""

    LOGICAL_SHARDS = 1024   # Chosen ONCE, forever. Changing it rehashes
                            # every row in the system. Pick it generously:
                            # it bounds your maximum physical shard count.

    def __init__(self, logical_to_physical: list):
        # A 1024-entry list: index = logical shard, value = physical shard.
        # Growing the cluster edits THIS LIST, it does not change the hash.
        # 8 -> 9 physical shards moves 1024/9 ~= 114 logical shards ~= 11.1%
        # of the data, instead of the 88.9% that raw hash(k) % N would move.
        assert len(logical_to_physical) == self.LOGICAL_SHARDS
        self.map = logical_to_physical

    def logical(self, key) -> int:
        # Normalise to bytes so that int 42 and str "42" cannot diverge.
        # signed=False keeps the value non-negative; a negative hash with
        # Python's % would still work, but in C/Java/Go it would not. Be explicit.
        return mmh3.hash(str(key).encode(), signed=False) % self.LOGICAL_SHARDS

    def route(self, key) -> str:
        return self.map[self.logical(key)]

    def route_range(self, lo, hi) -> list:
        # THE COST: hashing destroys order, so there is no way to narrow this.
        # EVERY range query is a scatter-gather over every physical shard.
        # Latency becomes max(N samples), not avg -- see Lesson 16 and L37 §9.
        return sorted(set(self.map))


# ---------------------------------------------------------------- DIRECTORY
class DirectoryRouter:
    """Explicit key -> shard mapping stored as data. Maximum flexibility."""

    def __init__(self, store, fallback: HashRouter):
        self.store = store          # replicated KV store (etcd/Redis/Postgres)
        self.fallback = fallback    # hybrid: hash by default, directory to override
        self.cache = {}             # in-process cache -- see note below
        self.epoch = 0              # map version; see _assert_fresh()

    def route(self, tenant_id) -> str:
        # Cache FIRST. This is the design decision that makes directory
        # sharding viable: with ~99.5% hit rate the directory is OFF the
        # critical path, so its availability no longer bounds the system's.
        if tenant_id in self.cache:
            return self.cache[tenant_id]
        try:
            shard = self.store.get(f"shardmap:{tenant_id}")
        except StoreUnavailable:
            # SERVE STALE / FALL BACK rather than fail. A directory outage must
            # degrade the ability to *change* placement, not the ability to read.
            return self.fallback.route(tenant_id)
        # Only a few thousand entities need an explicit override; everything
        # else falls through to the hash. The map stays small on purpose --
        # a directory keyed on 2 BILLION order_ids would be bigger than a shard.
        shard = shard or self.fallback.route(tenant_id)
        self.cache[tenant_id] = shard
        return shard

    def relocate(self, tenant_id, new_shard):
        # The entire advantage of directory sharding is this method existing.
        # Moving a noisy 30%-of-traffic tenant to a dedicated shard is ONE write.
        # Under pure hash sharding the equivalent action does not exist.
        self.store.put(f"shardmap:{tenant_id}", new_shard)
        self.epoch += 1
        self.store.put("shardmap:epoch", self.epoch)


# ---------------------------------------------------------------- GEOGRAPHIC
RESIDENCY = {
    # Mapping is POLICY, not computation: legal rules, not load, decide this.
    "DE": "eu-central-1", "FR": "eu-central-1", "IT": "eu-central-1",
    "US": "us-east-1",    "CA": "us-east-1",
    "SG": "ap-southeast-1", "JP": "ap-southeast-1", "AU": "ap-southeast-1",
}

def route_geo(user_country: str) -> str:
    # Unknown country must NOT silently default to a region that may be
    # illegal for that user's data. Fail closed and make a human decide.
    region = RESIDENCY.get(user_country)
    if region is None:
        raise UnroutableResidency(user_country)
    return region
```

### 8.2 Hierarchical layout — hash across machines, range within

```sql
-- Executed IDENTICALLY on each of the 16 physical shards.
-- The hash decided WHICH machine; declarative partitioning (Lesson 35)
-- decides the layout INSIDE that machine.
CREATE TABLE orders (
    order_id    BIGINT      NOT NULL,   -- carries the shard id in its high bits
                                        -- (Lesson 35), so it is self-routing
    user_id     BIGINT      NOT NULL,   -- THE SHARD KEY
    status      TEXT        NOT NULL,
    total_cents BIGINT      NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL,
    -- The shard key MUST be part of every unique constraint: uniqueness can
    -- only ever be enforced within one shard (Lesson 35).
    PRIMARY KEY (user_id, order_id, created_at)
) PARTITION BY RANGE (created_at);

-- Monthly local partitions. Two benefits, both operational:
--  1. "WHERE created_at > ..." prunes to 1-2 partitions instead of scanning 125M rows
--  2. Retention becomes DROP TABLE (metadata, milliseconds) instead of a
--     125-million-row DELETE that would generate vacuum load for days.
CREATE TABLE orders_2026_01 PARTITION OF orders
    FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
CREATE TABLE orders_2026_02 PARTITION OF orders
    FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');

-- A DEFENSIVE GUARD. If the routing layer has a bug, or an app server is
-- running a stale shard map during a migration, this constraint turns a
-- silent misplaced write into a loud, immediate error. Without it, wrong-shard
-- writes are invisible until someone notices missing data weeks later.
-- (31 is this shard's assigned bucket range; each shard gets its own values.)
ALTER TABLE orders ADD CONSTRAINT orders_belongs_here
    CHECK (murmur3_32(user_id::text) % 1024 BETWEEN 576 AND 639) NOT VALID;
```

### 8.3 Vitess — declaring a hash keyspace plus an unsharded reference table

```yaml
# VSchema: Vitess's declaration of how a logical table maps onto shards.
# The point of showing this is that production sharding systems make you
# state the strategy EXPLICITLY, per table -- there is no sensible default.
keyspaces:
  booknook_sharded:
    sharded: true
    vindexes:
      hash_user:
        type: hash          # Vitess's "vindex" IS the sharding function f().
                            # xxhash/hash here; "lookup" vindexes implement
                            # directory sharding (see unique_isbn below).
      lookup_isbn:
        type: consistent_lookup_unique   # a DIRECTORY, maintained transactionally
        params:
          table: isbn_lookup             # maps isbn -> keyspace_id
          from: isbn
          to: keyspace_id
        owner: books
    tables:
      orders:
        column_vindexes:
          - column: user_id             # SHARD KEY. Every query that omits it
            name: hash_user             # becomes a scatter-gather.
      reviews:
        column_vindexes:
          - column: book_id             # DIFFERENT key from orders -- deliberate.
            name: hash_user             # 95% of review reads are by book.

  # A SEPARATE, UNSHARDED keyspace. `books` is 40 GB, read-heavy, and joined
  # by everything, so the correct strategy is "do not shard it". Vitess will
  # route joins against it locally rather than fanning out.
  booknook_reference:
    sharded: false
    tables:
      books: {}
      categories: {}
```

## 9. Numerical example

### Part A — The monotonic range hot spot, quantified

**Assumptions, stated explicitly:**
- BookNook `orders`: 2.0 billion rows, 4.0 TB, peak 3,200 inserts/second.
- 8 shards, range-partitioned on `order_id` into 8 equal intervals of 250 M.
- Each shard's primary sustains approximately 5,000 writes/second (a typical figure for a well-tuned mid-size instance on NVMe; it is not a universal constant).
- Planning ceiling is 70 % of measured capacity = 3,500 writes/second, per Lesson 35's practice of never planning to the cliff.
- Data and traffic grow 1.8× per year, compounding continuously.
- `order_id` is generated by a monotonic Snowflake-style generator (Lesson 35).

**Write distribution:**

```
Every new order has order_id > every existing order_id.
⇒ every insert falls into the highest interval, i.e. shard 7.

Shard 7 writes = 3,200 / s   (100 % of inserts)
Shards 0–6     =     0 / s   (0 %)

Mean per shard = 3,200 / 8 = 400 / s
Skew           = 3,200 / 400 = 8.0×   ← equal to N, exactly
Utilisation of shard 7 = 3,200 / 5,000       = 64 % of hard capacity
                       = 3,200 / 3,500       = 91 % of the planning ceiling
```

**Time until the planning ceiling is breached:**

```
Need: 3,200 × 1.8^(m/12) = 3,500
      1.8^(m/12) = 3,500 / 3,200 = 1.09375
      (m/12) × ln(1.8) = ln(1.09375)
      (m/12) × 0.58779 = 0.08961
       m/12 = 0.15245
       m    = 1.83 months
```

**Under hash sharding on `user_id`, same cluster, same hardware:**

```
Per-shard writes = 3,200 / 8 = 400 / s      (skew ≈ 1.0)
Need: 400 × 1.8^(m/12) = 3,500
      1.8^(m/12) = 8.75
      (m/12) × 0.58779 = ln(8.75) = 2.16905
       m/12 = 3.690
       m    = 44.3 months
```

**The comparison that matters:**

```
Range on a monotonic key : 1.8 months of headroom
Hash on user_id          : 44.3 months of headroom
Ratio                    : 24×
Hardware difference      : NONE. Same 8 machines. Same money.
```

**And now the part that surprises people — does adding shards help?**

```
Range on order_id, N = 8 : hot shard does 3,200 w/s. Skew 8×.
Range on order_id, N = 16: hot shard does 3,200 w/s. Skew 16×.
Range on order_id, N = 64: hot shard does 3,200 w/s. Skew 64×.

Cluster write ceiling = single-shard ceiling = 5,000 w/s, for ALL N.
```

Doubling the cluster buys storage and read replicas. It buys **exactly zero** write throughput. That is the single most important number in this lesson: with a monotonically increasing range key, write scalability is not merely poor, it is *absent*.

**Read skew on the same cluster.** Assume 70 % of order reads concern orders from the last 30 days.

```
Orders created in 30 days = 3,200 /s × 86,400 s/day × 30 days = 8.29 billion
```

That already exceeds the 2 B total, so let's use the real historical rate instead — assume the 2 B rows accumulated over 4 years, and that the newest 30 days contain about 249 M rows at the current rate (3,200/s × 86,400 × 30 ÷ 33.3 to account for peak-vs-average of ~3.3×):

```
Recent rows ≈ 249,000,000  →  12.4 % of the 2 B total
Those rows live in shard 7 (and the tail of shard 6).
⇒ ~1.5 shards hold 12.4 % of the data and serve 70 % of the reads.
Read skew ≈ 0.70 / (1.5/8) = 0.70 / 0.1875 = 3.73×
```

So even the read path is nearly 4× skewed. Range sharding on a monotonic key is bad in both directions.

### Part B — Hash distribution quality

**Assumptions:** 40 M users, 16 shards, MurmurHash3 (a well-distributed hash), keys treated as independent.

```
Expected per shard  = 40,000,000 / 16 = 2,500,000
Standard deviation  ≈ sqrt(K/N × (1 − 1/N))
                    = sqrt(2,500,000 × 0.9375)
                    = sqrt(2,343,750) = 1,531

3σ band             = 2,500,000 ± 4,593
Relative deviation  = 4,593 / 2,500,000 = 0.18 %
```

**Interpretation:** with 40 M keys over 16 shards, you can expect every shard to be within one-fifth of one percent of the mean. Hash sharding does not *approximately* solve key distribution; it solves it. Every remaining imbalance in a hash-sharded system comes from **per-key load skew**, not from key count — which is precisely why Lesson 37 exists.

Contrast with low cardinality. If BookNook sharded on `country_code` (about 195 values) hashed over 16 shards:

```
Expected per shard = 195 / 16 = 12.2 countries
σ ≈ sqrt(12.2 × 0.9375) = 3.4  → ±28 % at 1σ
```

and, far worse, the *rows* are not uniform across countries — the US alone might be 40 % of users, so one shard would hold ~40 % of the data regardless of how many countries landed on it. **Hashing fixes distribution only when the key has high cardinality AND roughly uniform weight per value.** Both conditions, not one.

### Part C — Directory sharding: latency and availability

**Assumptions:** 20,000 requests/second; directory of 40 M entries; in-process cache hit rate 99.5 %; cached lookup 0.02 ms; uncached round trip 1.2 ms; app tier availability 99.95 %; a single directory instance's availability 99.90 %.

**Added latency:**

```
Mean added latency = 0.995 × 0.02 ms + 0.005 × 1.2 ms
                   = 0.0199 + 0.0060
                   = 0.0259 ms   ≈ 0.03 ms
```

Negligible — but apply Lesson 16's discipline and look at the tail rather than the mean:

```
Directory misses per second = 20,000 × 0.005 = 100 /s
Those 100 requests each pay +1.2 ms.
If a page makes 3 routed calls, P(at least one miss) = 1 − 0.995³ = 1.49 %
⇒ ~300 requests/second pay at least one extra round trip.
```

Still acceptable. The latency objection to directory sharding is weak when you cache.

**Availability — the real objection:**

```
Single directory on the critical path (no cache fallback):
  0.9995 × 0.9990 = 0.99850  →  (1 − 0.99850) × 8,766 h = 13.1 h/year down
  Baseline app tier alone:        (1 − 0.9995) × 8,766 h =  4.4 h/year down
  ⇒ the directory TRIPLED annual downtime.

Directory replicated 3× with independent failure (0.999 each):
  P(all three down) = 0.001³ = 1e−9  →  effectively 99.9999999 %
  0.9995 × 0.999999999 ≈ 0.99950  →  4.4 h/year. Impact erased.

Directory cached in-process with serve-stale on failure:
  The directory is only needed for CHANGES. A directory outage with a warm
  cache causes ZERO request failures; it only blocks relocations.
  ⇒ availability impact ≈ 0.
```

**Conclusion, stated as a rule rather than "it depends":** directory sharding is safe when, and only when, (a) the map is cached in every app process, (b) stale map entries are served rather than erroring during a directory outage, and (c) the map carries a version/epoch so that stale entries are *detectable* during migrations. Without all three, a directory triples your downtime and can silently misroute writes.

### Part D — Geographic skew and the overprovisioning cost

**Assumptions:** 55 % North America / 28 % Europe / 17 % APAC traffic split. Each region's peak-to-trough ratio is 3.5×, with peaks at 20:00 local time. Regional peaks (requests/second): NA 11,000, EU 5,600, APAC 3,400. Measured *global* peak is 14,500 requests/second, because the regional peaks are offset by 6–9 hours of UTC.

```
Sum of regional peaks  = 11,000 + 5,600 + 3,400 = 20,000 req/s
Observed global peak   = 14,500 req/s
Overprovisioning ratio = 20,000 / 14,500 = 1.379  →  ~38 % extra capacity
```

At a 70 % planning ceiling, converted to machines serving 1,000 req/s each:

```
Geographic:  NA   ceil(11,000 / 0.70 / 1,000) = 16 machines
             EU   ceil( 5,600 / 0.70 / 1,000) =  8 machines
             APAC ceil( 3,400 / 0.70 / 1,000) =  5 machines
                                        total = 29 machines

Single global pool: ceil(14,500 / 0.70 / 1,000) = 21 machines

Extra cost of geographic sharding = 8 machines = 38 %
```

**What the 38 % buys:**

```
EU user → us-east-1 round trip   ≈ 90 ms
EU user → eu-central-1 round trip ≈  4 ms
Saving per round trip             ≈ 86 ms

Page requiring 4 sequential DB round trips:
  4 × 86 ms = 344 ms removed from EVERY EU page load.
EU share of traffic = 28 % of 20,000 = 5,600 req/s of users affected.
```

Plus the thing you cannot price: lawful operation in jurisdictions with data-residency rules. If GDPR-style residency applies to you, the 38 % is not a trade-off calculation at all — it is the cost of doing business, and the correct comparison is 38 % versus not having a European business.

**The skew you cannot engineer away.** Even within the geographic design, APAC's 5 machines sit at 3.5× below peak for most of the UTC day:

```
APAC trough = 3,400 / 3.5 = 971 req/s
APAC utilisation at trough = 971 / (5 × 1,000) = 19.4 %
APAC utilisation at peak   = 3,400 / 5,000      = 68 %
Average over 24 h          ≈ 35–40 %
```

That is structural. The only mitigations are autoscaling (**Phase 2, Topic 22 — Autoscaling and multi-region architecture**, Lesson 39), running batch work in the regional trough, or accepting it.

## 10. Trade-offs

**Range sharding**
- *Advantages:* local range scans, `ORDER BY`/`LIMIT` and `MIN`/`MAX` stay on one shard; splitting is cheap and affects one shard only; bulk retention becomes a metadata `DROP`; the shard map is N numbers and trivially debuggable.
- *Disadvantages:* catastrophic hot spot on monotonic keys (skew = N, and worse as N grows); boundaries must be derived from the real data distribution and re-derived as it drifts; a single hot range needs splitting, which is reactive rather than preventive.
- *Operational cost:* moderate — you own boundary management, or you adopt a database that automates it (CockroachDB, TiDB, HBase).
- *Unnecessary complexity when:* your workload has no range queries and no retention policy, in which case hashing gives you better distribution for free.

**Hash sharding**
- *Advantages:* near-perfect key distribution (±0.18 % at realistic cardinality); O(1) routing with a stateless function; no boundary maintenance; no coordination.
- *Disadvantages:* all range queries become scatter-gather; resharding with raw `mod N` moves ~90 % of rows; no ordered access; cannot isolate a specific noisy entity; does nothing about hot keys.
- *Operational cost:* low day-to-day, high at resize time — unless you adopt logical shards from day one, which costs almost nothing and removes most of the pain.
- *Unnecessary complexity when:* your table is small enough not to need sharding at all (BookNook's `books`).

**Directory sharding**
- *Advantages:* arbitrary, revisable placement; per-entity migration and rollback; dedicated shards for large tenants; supports heterogeneous shard sizes; expresses hybrid policies (residency *and* load) that no pure function can.
- *Disadvantages:* a new stateful component on 100 % of the request path; stale map entries can misroute writes silently; the map must stay small, so it cannot be keyed on your highest-cardinality column.
- *Operational cost:* highest of the four — you now operate, replicate, back up, version and cache a mapping service, and you must handle the migration-time consistency problem.
- *Unnecessary complexity when:* all entities are roughly equal in size and load. If you have no outliers, you have nothing to relocate, and the directory earns nothing.

**Geographic sharding**
- *Advantages:* 50–90 ms of latency removed per round trip for non-home-region users; data residency as a structural guarantee; regional blast-radius containment.
- *Disadvantages:* guaranteed skew (population + diurnal) costing ~38 % extra capacity; cross-region queries and global uniqueness become genuinely hard; user relocation is a data migration; operational surface multiplied by the region count.
- *Operational cost:* high — N deploy pipelines, N monitoring stacks, N-way schema migration, and a cross-region consistency story.
- *Unnecessary complexity when:* your users are overwhelmingly in one region. Serving 95 % of traffic from one region with a CDN (Lesson 30) in front usually beats a second database region.

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| Range shard on monotonic key saturates the newest shard | Checkout times out or errors for **all** users during peak, while 7 of 8 shards idle; read latency fine, write latency terrible | Per-shard write TPS dashboard showing one shard at 90 %+ and the rest near zero; skew metric `max_shard_tps / mean_shard_tps` ≈ N | Short term: split the hot range and pre-split ahead of the insert point (buys hours, not weeks). Real fix: change the shard key to a non-monotonic one — a full resharding project (Lesson 37) |
| Hash function differs between services (Python `hash()` vs Java `hashCode()`) | Rows written by one service are invisible to another; orders "disappear"; the bug is intermittent and keyed on specific values | Row-count reconciliation between shards and source-of-truth; the `CHECK` constraint in §8.2 firing; customer reports of missing data | Freeze the divergent writer, identify misplaced rows via a full scan with the correct hash, move them, deploy a single shared hashing library with pinned behaviour |
| Hash range query fans out to every shard under load | Search and admin list pages become slow and erratic; p50 fine, p99 catastrophic | Query latency distribution per endpoint; shard query count per request; p99/p50 ratio (Lesson 16) | Add a covering secondary index store, a denormalised read model, or a cache (Lessons 27–30). Lesson 37 §4 covers scatter-gather mitigation in full |
| Directory service unavailable with cold caches | **Total outage** — no request can be routed anywhere | Directory error rate and latency; "unroutable" error class in the app; availability drop with healthy shards | Replicas take over; app serves stale cached map; long-term fix is cache-with-serve-stale plus 3-way replication (§9 Part C) |
| Stale shard map during a migration | A subset of writes lands on the old shard; reads from the new shard return missing or outdated rows; looks like replication lag but is not | Map **epoch** mismatch rejected at the shard; wrong-shard `CHECK` constraint violations; dual-read comparison mismatches | Reject writes carrying a stale epoch; force cache refresh; replay misplaced writes from the shard's WAL or from the dual-write log |
| Geographic shard loses its region | 100 % outage for that region's users; other regions unaffected | Regional health checks (Lesson 24); synthetic probes per region; DNS/anycast health signals | Fail over to a cross-region replica (Lesson 32), accepting replication-lag data loss; **check first whether residency law permits serving that data from another region** |
| User relocates countries (moves from DE to JP) | Persistently slow experience; or, worse, a residency violation if the row stays in the wrong region | Mismatch between the user's detected region and their assigned shard; latency outlier analysis by user geography | A per-user migration job with dual writes and cutover (Lesson 37 §4) — and an explicit legal review before moving personal data across the boundary |
| Directory map grows to 2 billion entries | Routing latency climbs; directory memory exhausted; lookups start missing cache | Directory memory and p99 lookup latency trending up; cache hit rate falling | Re-key the directory onto a lower-cardinality entity (tenant/user), with a pure hash handling the long tail |
| Low-cardinality shard key (e.g. `country_code`) | One shard holds 40 % of data and traffic; it is slow for everyone on it, fine for everyone else | Per-shard row count and disk usage diverging; per-shard QPS skew > 2× | Add a second component to make a composite key (salting, Lesson 37); or move to a higher-cardinality key — a full reshard |

## 12. Alternatives

| Approach | How placement is decided | Pros | Cons |
|---|---|---|---|
| **Application-level sharding** (routing library in your code) | Your `f()`, in your process | No extra hop; total control; works with any database; free | Every service must embed and agree on the logic; schema and map changes are a multi-service deploy; easy to get subtly wrong |
| **Sharding proxy** (Vitess, ProxySQL, Citus) | A proxy tier that speaks the DB wire protocol | Apps issue near-normal SQL; cross-shard query support, online resharding tooling; one place to change policy | A new tier to operate and scale; an extra hop (~0.5–1 ms); SQL feature gaps and surprising query plans |
| **Natively sharded SQL** (CockroachDB, TiDB, Spanner, YugabyteDB) | The database, automatically, by range with auto-split/rebalance | Range splitting and rebalancing are automatic; distributed transactions included; range scans preserved | Higher per-query latency from consensus (Phase 4); cost; you lose some control over placement; operational learning curve |
| **Managed NoSQL with built-in partitioning** (DynamoDB, Cassandra, MongoDB) | Hash on a declared partition key | Resharding is the vendor's problem; scales to very large sizes; predictable | The partition key is effectively immutable; hot-partition limits are hard (DynamoDB: ~3,000 RCU / 1,000 WCU per partition); limited query shapes |
| **Don't shard — scale up and use read replicas** | No placement decision at all | Vastly simpler; keeps joins, transactions, unique constraints; a 128-core/4 TB-RAM instance is enormous | Caps out on **write** throughput and working-set size; Lesson 35 showed replicas never add write capacity |
| **Functional / service decomposition** | Split by *table*, not by row | Often the cheaper next step: move `reviews` to its own database before sharding anything | Only buys one step of headroom; introduces cross-service joins and consistency concerns |
| **Offload the query, not the data** (search index, OLAP store, read models) | Placement stays simple; a second store answers the hard queries | Removes the pressure that pushed you toward range sharding; Elasticsearch/ClickHouse answer scatter-gather shapes natively | A second store to keep in sync; eventual consistency; duplicated data |

The sixth and seventh rows deserve emphasis, because they are the ones candidates and teams skip. A very large fraction of "we need to shard" problems are actually "one table is hot" or "one query shape is wrong for this engine". Sharding is the heaviest tool in Phase 2; check that the two lighter ones do not already solve it.

## 13. When to use it

1. **Range sharding when the dominant query is a range over an ordered key, *and* that key is not monotonic at insert time.** Time-series per entity, ledger entries per account, log data with a composite `(entity, time)` key. The decisive extra benefit is retention: `DROP PARTITION` instead of a billion-row `DELETE`.
2. **Range sharding when retention or archival is your biggest operational cost.** If you delete more rows per quarter than you keep, ordered placement is worth more than even distribution — see the telemetry example in §7.
3. **Hash sharding when the dominant query is a point lookup by a high-cardinality key with roughly uniform weight.** `user_id`, `tenant_id`, `session_id`, `device_id`. This is the default for OLTP applications and should be your starting assumption for BookNook-shaped systems.
4. **Hash sharding whenever write distribution is the binding constraint**, as §9 Part A shows: 24× more headroom on identical hardware.
5. **Directory sharding when entity sizes are wildly unequal** — B2B SaaS where the largest tenant is 30 % of the data, or a consumer product with a handful of celebrity accounts. The ability to give one entity a dedicated shard with a single `UPDATE` is worth the extra component.
6. **Directory sharding when you need incremental, reversible migration** — moving tenants one at a time, with per-tenant rollback, is far safer than a cluster-wide cutover.
7. **Geographic sharding when data-residency law applies**, full stop. Latency is the bonus; legality is the reason.
8. **Geographic sharding when a large share of users is far from your primary region** and the workload is write-heavy enough that a CDN (Lesson 30) and read replicas cannot fix it.
9. **Composite/hierarchical keys whenever one of the above is *almost* right** — hash across machines for distribution, range within a machine for scans and retention. This is the configuration most mature systems end up at.

## 14. When *not* to use it

1. **When the table does not need sharding at all.** BookNook's `books` is 40 GB, 0.02 writes/second, and joined by everything. Sharding it would convert every local join into a cross-shard join for zero capacity benefit. Replicate it as a reference table instead. Always ask "does this table need to be sharded?" before "how should it be sharded?".
2. **Range sharding on any monotonically increasing key** — `SERIAL`, `created_at`, Snowflake IDs, UUID v7, ULID. There is no configuration that rescues this; the hot spot is structural and adding shards makes the skew worse.
3. **Hash sharding when you need ordered scans, `MIN`/`MAX`, global `ORDER BY ... LIMIT`, or bulk time-based deletion.** You will pay for it on every single one of those queries, forever, and the fix is a reshard.
4. **Directory sharding keyed on your highest-cardinality column.** A 2-billion-entry map keyed on `order_id` is not a routing table, it is a second database with all the same problems.
5. **Directory sharding without an in-process cache and a serve-stale path.** §9 Part C showed this triples annual downtime and puts a hard availability ceiling on your entire system.
6. **Geographic sharding when 90 %+ of users are in one region.** You are paying a 38 % capacity premium and 3× operational cost to help a small minority; a CDN plus a read replica is the right-sized answer.
7. **Any sharding strategy chosen before you know your dominant query.** The shard key follows the access pattern. If you cannot name the query that must stay on one shard, you are not ready to choose.

## 15. Common mistakes

**Beginner mistakes**
- Sharding on the primary key because it is the primary key, rather than on the key the dominant query filters by. §7's `reviews` example shows this costs a 16× multiplier on your busiest endpoint.
- Range-sharding on `created_at` or an auto-increment ID and being surprised that adding shards did not increase write throughput.
- Using a language's built-in `hash()` as the sharding function, then discovering two services disagree about where rows live. Python randomises string hashing per process by default.
- Believing hash sharding solves hot spots. It solves hot *ranges*. A single hot key still lands on one shard (Lesson 37).
- Sharding a small table because the big one needed sharding. Reference tables should be replicated, not split.
- Forgetting that the shard key must be part of every unique constraint, because uniqueness can only be enforced within a shard (Lesson 35).
- Choosing a low-cardinality key (`country`, `status`, `category`) and expecting a hash to even it out. Hashing fixes distribution only at high cardinality with uniform per-value weight.

**Experienced-engineer mistakes**
- Hashing directly onto physical shards instead of onto a large fixed set of logical shards. It costs nothing on day one and saves you from moving 88.9 % of your rows on the first resize.
- Choosing the logical shard count too small (64), capping your maximum physical shard count forever. Pick 1,024 or 4,096; the only cost is a slightly bigger map.
- Building a directory without a map epoch, so stale caches silently write to the wrong shard during a migration. Misrouted writes are usually discovered weeks later, by a customer.
- Operating a directory as a hard dependency rather than caching it with serve-stale. You have taken a well-understood capacity problem and converted it into an availability problem.
- Treating geographic sharding as a performance optimisation and discovering mid-project that it is actually a legal constraint with a different, stricter set of requirements (no cross-region failover for EU data, for example).
- Sizing geographic regions from global average traffic rather than each region's own peak, then being under-provisioned every evening in every region.
- Omitting the defensive wrong-shard `CHECK` constraint from §8.2 because "the router is correct". Routers are correct until a deploy is half-rolled-out.
- Picking a strategy without writing down the top five queries by volume first. Every bad shard-key decision in this lesson traces back to this omission.
- Assuming the strategy can be changed later. It can, but Lesson 37 shows the cost: weeks of dual writes, a multi-terabyte backfill, verification, and a cutover with a rollback plan.

## 16. Interview perspective

This topic separates candidates who have read about sharding from candidates who have done it. The signal is specificity.

**How to use it:**

1. **Never say "we'll shard the database" without naming the key and the strategy.** Say: *"I'd hash-shard `orders` on `user_id` across 1,024 logical shards mapped onto 16 physical ones, because the dominant query is 'my orders' and hashing keeps that on a single shard."* That is one sentence and it answers four questions.
2. **Volunteer the monotonic hot spot before you are asked.** *"I wouldn't range-shard on `order_id` — it's monotonic, so 100 % of inserts land on the newest shard, the skew equals the shard count, and adding shards makes it worse, not better."* Very few candidates say the last clause, and it is the one that proves understanding.
3. **Always ask whether the table should be sharded at all.** Proposing "replicate `books` as a reference table, shard only `orders` and `reviews`" demonstrates judgement. Sharding everything uniformly demonstrates the opposite.
4. **Choose different keys for different tables and justify each.** `orders` on `user_id`, `reviews` on `book_id`, `books` unsharded. Then name the cost you accepted: *"That means 'reviews I wrote' is a fan-out, so I'd denormalise a per-user summary."* Naming the cost is more convincing than claiming there isn't one.
5. **Use logical shards unprompted.** *"I'd hash to 1,024 buckets and map buckets to machines, so growing from 8 to 9 shards moves 11 % of data instead of 89 %."* This is a small detail with a large payoff and interviewers notice it.
6. **Bring up residency when the system is global.** *"If we have EU users, geographic sharding may be a compliance requirement rather than a latency optimisation, which changes the failover design — I can't fail EU data over to us-east-1."*
7. **Quantify.** "1.8 months versus 44 months of headroom on identical hardware", "±0.18 % distribution at 40 M keys", "38 % capacity premium for geographic sharding". Concrete numbers, labelled as approximate, are what gets remembered.
8. **Forward-reference honestly.** *"Resizing and hot keys I'd handle with consistent hashing and a dedicated shard — happy to go into that."* Showing you know what comes next is better than pretending this lesson is the whole story.

**Red flags interviewers watch for:**
- Saying "shard the database" with no shard key, no strategy, and no mention of what breaks.
- Proposing range sharding on an auto-increment ID or a timestamp without flagging the hot spot.
- Claiming hash sharding eliminates hot spots.
- Sharding every table on the same key by reflex, including small reference tables.
- No awareness that cross-shard joins, global uniqueness and multi-row transactions are lost (Lesson 35).
- Treating the shard key as changeable later, with no sense of the migration cost.
- Suggesting a directory without mentioning caching, replication, or that it now sits on 100 % of the request path.
- Describing geographic sharding purely as a latency play, with no mention of residency, skew, or the overprovisioning cost.
- Using the words "it depends" without immediately stating what it depends on — here, the dominant query, the key's cardinality and weight distribution, the monotonicity of the key, the retention policy, and whether residency law applies.

## 17. Knowledge check

Write your answers down before continuing.

**Conceptual**

1. A colleague argues: "Range sharding on `created_at` is fine for us — yes the newest shard is hot, but we'll just add more shards when it gets hot." Explain precisely why adding shards does not help, derive the cluster's aggregate write ceiling as a function of N, and then describe the one modification to the shard key that would make range sharding work for this table while preserving its range-scan and retention benefits.

2. BookNook hash-shards `reviews` on `book_id` across 16 shards and measures near-perfect key distribution (every shard within 0.2 % of the mean row count). A week later, one shard is at 95 % CPU and the other 15 are at 12 %. Explain how both statements can be true simultaneously, name the phenomenon, and explain why no change of *hash function* can fix it. Then state which of the four strategies in this lesson offers a one-line operational remedy, and what that remedy is.

3. Explain why a directory-sharded system's availability is bounded above by its directory's availability when the directory is a hard dependency, show the arithmetic for a 99.95 % app tier and a 99.90 % directory, and then describe the three specific design changes that remove this bound. For each change, state what it protects against and what it does *not* protect against.

**Practical scenario**

4. BookNook is launching in the EU and must guarantee that European customers' personal data is stored and processed only within the EU. The existing system hash-shards `orders` on `user_id` across 16 shards in `us-east-1`, with `order_id` carrying the shard number in its high bits (Lesson 35). Design the new placement scheme. Your answer must cover: how a request is routed to the correct region before the user is authenticated; what happens to a user who moves from Germany to Japan; how `books` (the shared reference table) is handled; whether a European shard may fail over to a US replica and what you would do instead; and how a global "total revenue this quarter" report is produced. Then identify the single largest new failure mode your design introduces and how you would detect it.

**Calculation**

5. A B2B analytics product, Gridline, has 3,000 tenants and 900 GB of data, growing 2.1× per year. Peak writes are 2,400/second. Each shard sustains 6,000 writes/second; the planning ceiling is 65 % of that. Tenant size is heavily skewed: the largest tenant holds 22 % of all rows and generates 19 % of writes; the next four tenants hold 8 % each; the remaining 2,995 tenants share the rest roughly evenly.
   Assumptions you may add if needed, but state them.
   (a) If Gridline hash-shards on `tenant_id` into 8 shards, what is the write rate on the shard holding the largest tenant today, and what is its utilisation against the planning ceiling? Show the arithmetic.
   (b) How many months until that shard breaches the planning ceiling? How many months until an *average* shard does? What is the ratio, and what does that ratio tell you about the real binding constraint?
   (c) What is the minimum number of hash shards that keeps the largest tenant's shard under the planning ceiling for 24 months? What total write capacity does that cluster have, and what percentage of it is actually usable?
   (d) Gridline instead uses directory sharding: the top 5 tenants get dedicated shards and the remaining 2,995 are hash-assigned to M shared shards. Compute the minimum M for a 24-month horizon, the total shard count, and the total cluster write capacity. Compare the machine count with your answer to (c) and state, in one sentence, the general principle this illustrates about skewed workloads.
   Show every step and state every assumption you add.

Reply with your answers and I'll assess them, correct anything shaky, and give model answers before Lesson 37.

---

**Progress checklist**

Phase 1 — Internet and System-Design Foundations: all 17 topics `[x]` complete (Lessons 1–17).

Phase 2 — Scaling
- [x] 1–17. Vertical scaling through replication lag and read-after-write consistency
- [x] 18. Database partitioning and sharding
- [x] 19. Range, hash, directory, geographic sharding
- [ ] 20. Resharding, hot partitions, cross-shard queries ← next
- [ ] 21. Consistent hashing and virtual nodes
- [ ] 22. Autoscaling and multi-region architecture

Phases 3–5: not started.

**NEXT LESSON: Phase 2, Topic 20 — Resharding, hot partitions, cross-shard queries**

---

# Lesson 37 — Resharding, Hot Partitions, and Cross-Shard Queries

*Phase 2, Topic 20*

## 1. Simple definition

Lesson 36 chose a sharding strategy. This lesson is about the three problems that arrive *after* the strategy is live and that no strategy choice can fully avoid:

- **Resharding** (also called *rebalancing* or *repartitioning*): changing the number of shards, or the mapping of keys to shards, on a system that is serving production traffic and cannot be stopped.
- **Hot partitions**: one shard receiving disproportionate load even though the *keys* are evenly distributed — usually because one key is extremely popular. The special case where a single key is the cause is the **celebrity problem**.
- **Cross-shard queries**: any query the router cannot answer from a single shard, so it must contact several and combine the results.

Terms defined before use:

- **Scatter-gather** (also *fan-out/fan-in*): the router sends a sub-query to multiple shards (scatter) and merges the responses (gather). The request's latency is the **maximum** of the sub-query latencies, not the average.
- **Dual write**: a transitional period in which the application writes each row to both the old and the new location.
- **Backfill**: the bulk copy of pre-existing rows to their new location, run concurrently with live traffic.
- **Cutover**: the moment the read path switches from old to new.
- **Key salting**: appending a small random or derived suffix to a hot key so it maps to several shards instead of one.
- **Over-fetch**: deliberately retrieving more rows than the client asked for, so the merge step can produce a correct globally-ordered page.
- **Heavy hitter**: a key whose request share greatly exceeds `1/cardinality`.

If Lesson 36 was about making a good decision, this lesson is about living with it.

## 2. Why it exists

Three forces guarantee you will meet all three problems.

**Force 1 — growth invalidates every capacity decision.** BookNook sized 16 shards for 3,200 writes/second. At 1.8× annual growth that is 5,760/second in a year and 10,370/second in two. You *will* resize. And naive `hash(key) mod N` is viciously hostile to resizing: Section 9 Part A computes that going from 16 shards to 17 relocates **94.1 %** of all rows. On a 4 TB table that is 3.76 TB of data movement, under live traffic, with correctness required throughout.

**Force 2 — real-world popularity is not uniform.** Lesson 36 §9 Part B showed hash sharding distributes *keys* to within 0.18 %. It said nothing about *load*. Human attention follows a power law: the most popular book on BookNook gets perhaps 10,000× the traffic of the median book. Hashing places it on exactly one shard. That shard melts while fifteen others idle, and no change of hash function helps, because the problem is one key, not the distribution of keys.

**Force 3 — Lesson 35 told you cross-shard joins were gone, but the product still needs them.** Users still want "newest orders across the whole site", support still wants "find this order by email address", finance still wants "revenue this quarter". These queries did not disappear when you sharded; they became expensive. Somebody has to pay for them, and the mechanisms — scatter-gather, denormalisation, aggregation services, separate read models — are what this lesson is about.

There is a fourth force worth naming: **the cost of getting resharding wrong is data loss, not slowness.** A misrouted write during a migration does not error; it succeeds, into the wrong shard, and becomes invisible. That is why the migration playbook in §4.2 is long and pedantic. It is long and pedantic because every step in it exists because somebody skipped it.

## 3. Real-world analogy

**Reorganising a chain of warehouses while the trucks keep arriving.**

- **Resharding** = you are opening a seventh warehouse. If parcels were filed by "customer number modulo 6", almost every parcel is now in the wrong building. You cannot close for the week. So you start putting every new parcel in *both* the old and the new building (dual writes), hire a fleet to move the backlog (backfill), spot-check that both buildings agree (verification), then one morning tell drivers to collect from the new building (cutover) — keeping the old building intact for a fortnight in case you need to go back (rollback).
- **Hot partition** = a celebrity endorses one product. One warehouse now handles 40 % of all shipments. You can split the stock across warehouses (salting), put a pop-up kiosk out front (caching), or give that product its own dedicated building (dedicated shard).
- **Cross-shard query** = "how many units of this item do we have, nationwide?" You must phone all six warehouses and wait for the slowest one. If each warehouse answers slowly 1 % of the time, your nationwide count is slow about 6 % of the time — your slowness is the *union* of theirs.

**Where the analogy breaks down:**

| Warehouses | Sharded database |
|---|---|
| A parcel in two places is obviously duplicated | A row dual-written to two shards looks correct from both; divergence is silent until someone reconciles |
| You can see a truck arrive at the wrong depot | A misrouted write returns `200 OK`; nothing is visibly wrong for weeks |
| Phoning six warehouses is slow but linear | Scatter-gather latency is the **max** of N samples — the gather's median approaches each shard's p96 at N=16 (§9 Part C) |
| A warehouse manager can say "I'm swamped, send it elsewhere" | A shard does not negotiate; it queues until it falls off the utilisation cliff (Lesson 14) |
| Moving one parcel is independent of moving another | Moving rows must preserve ordering, uniqueness and in-flight transactions, concurrently |
| Splitting a popular product across depots is trivial | Splitting a single hot *key* is impossible by range; it requires salting, which makes every read fan out |
| You can run 10 % faster by hiring more drivers | Backfill speed is capped by what the live workload can tolerate — usually 10–25 % of shard capacity |
| "Nationwide stock count" is approximate and nobody minds | A cross-shard financial total is expected to be exact, which is why distributed transactions exist (Phase 4) |

## 4. Detailed technical explanation

### 4.1 Why `hash(key) mod N` is hostile to resizing

Take a key `k` whose hash is uniformly distributed. Under `mod N` it sits on shard `h mod N`; under `mod M` it sits on `h mod M`. It does not have to move only if `h mod N == h mod M`.

For `M = N + 1`, the moduli are coprime-adjacent and the Chinese Remainder Theorem gives a clean result. Over one full period of `N(N+1)` consecutive hash values there are `N(N+1)` distinct residue pairs `(a, b)`, each occurring exactly once. The pairs with `a == b` require `a < min(N, N+1) = N`, so there are exactly `N` of them.

```
P(key stays) = N / (N × (N+1)) = 1 / (N + 1)
P(key moves) = N / (N + 1)
```

So the fraction that moves rises toward 1 as N grows. Adding one shard to a large cluster moves essentially everything. Section 9 Part A tabulates this.

**The one exception: doubling.** If `M = 2N`, then `h mod 2N` is either `h mod N` or `h mod N + N`, with equal probability. Exactly half the keys stay.

```
P(key moves on N → 2N) = 1/2, exactly, for every N
```

This is why so many systems only ever resize by doubling. It is a real technique with a real limitation: doubling is a 100 % capacity-cost step. Going from 16 shards to 17 is a 6 % cost increase; going 16 → 32 is a 100 % increase, and you may be paying for 15 idle machines for a year.

**The two good answers.**

1. **Logical shards** (Lesson 36 §4.2). Hash onto a large fixed count — 1,024 or 4,096 — and keep an editable `logical → physical` map. Adding a physical shard means reassigning `1024/M` logical shards. Going 16 → 17 physical moves `1024/17 ≈ 60` logical shards ≈ **5.9 %** of the data, versus 94.1 %. The hash never changes; only the map does.
2. **Consistent hashing** (**Phase 2, Topic 21 — Consistent hashing and virtual nodes**, Lesson 38). Place keys and shards on a hash ring; adding a shard moves only the keys in the arc it claims, approximately `K/M` of them — `1/17 ≈ 5.9 %` for the same resize. With virtual nodes, that movement is also spread evenly across all existing shards rather than taken from one neighbour.

Both get you to the same ~6 %. Logical shards are simpler to reason about and debug (the map is a list you can print); consistent hashing handles dynamic membership and heterogeneous capacity more gracefully. Lesson 38 covers the distinction properly.

**Important caveat, so you do not over-learn this:** reducing the *fraction* moved does not make resharding free. Moving 5.9 % of 4 TB is still 236 GB of live data movement requiring dual writes, verification and a cutover. Consistent hashing changes a 3.76 TB migration into a 236 GB migration. It does not change a migration into a config change.

### 4.2 The resharding migration playbook

This is a sequence, and the order is not negotiable. Each phase has an exit criterion and a rollback.

**Phase 0 — Decide and freeze.** Fix the target topology (e.g. 16 → 32), confirm it gives at least 18–24 months of headroom (Lesson 35's capacity method), and freeze schema changes on the affected tables for the duration. A schema migration landing mid-backfill is a classic way to corrupt a migration.

*Exit criterion:* a written target map and a frozen schema. *Rollback:* trivial — nothing has changed.

**Phase 1 — Provision and prepare.** Stand up the new shards with identical schema, indexes and configuration. **Create the destination tables without secondary indexes** — building indexes during a bulk load is dramatically slower than building them afterwards, typically 3–10× for large tables. Add the defensive wrong-shard `CHECK` constraint from Lesson 36 §8.2 to each new shard.

*Exit criterion:* new shards accept writes and pass a smoke test. *Rollback:* delete them.

**Phase 2 — Dual writes.** Deploy application code (behind a feature flag) that writes every mutation to both the old and the new location, inside the same logical operation.

The hard question here is what to do when the second write fails. Three options, in increasing order of quality:

- *Best-effort*: log and continue. Fast, but creates silent divergence. Only acceptable if Phase 4's verification is genuinely thorough.
- *Fail the request*: the user sees an error if either write fails. Correct, but you have just doubled your failure rate for the duration of the migration.
- **Write-ahead log / outbox**: write to the old shard in a local transaction that also inserts a row into an `outbox` table; a consumer replays the outbox into the new shard with at-least-once delivery and idempotent application. This is the production answer. It decouples the second write from the request path, so new-shard problems cannot fail user requests, and it guarantees eventual convergence.

Dual writes must be **idempotent**, because the backfill in Phase 3 and the outbox consumer will both touch the same rows. Use `INSERT ... ON CONFLICT DO UPDATE` keyed on the primary key, and include a version or `updated_at` so a stale replay cannot overwrite a newer value.

*Exit criterion:* dual-write error rate below target (typically < 1 in 10⁵) and outbox lag stable under a few seconds. *Rollback:* turn off the flag; the old shard is still authoritative and untouched.

**Phase 3 — Backfill.** Copy pre-existing rows to their new locations. Rules that matter:

- **Chunk by primary key**, not by `OFFSET`. `OFFSET 10000000` makes the database scan ten million rows to skip them; keyset iteration (`WHERE pk > :last_seen ORDER BY pk LIMIT 10000`) is constant-cost per chunk.
- **Throttle to a fixed share of shard capacity** — typically 10–25 %. Use a live feedback signal (replication lag on the source shard, or p99 of production queries) and back off automatically. An unthrottled backfill is indistinguishable from an outage.
- **Backfill must not overwrite newer dual-written data.** Apply with `ON CONFLICT DO UPDATE ... WHERE excluded.updated_at > target.updated_at`, or use `DO NOTHING` if dual writes are authoritative for anything they have already touched.
- **Record progress durably** so the job is resumable. A backfill that must restart from zero after an 11-hour run is how migrations slip by weeks.
- **Build the secondary indexes after the bulk load**, then `ANALYZE`.

*Exit criterion:* every chunk marked complete; indexes built; statistics refreshed. *Rollback:* stop; truncate the new shards; nothing user-visible happened.

**Phase 4 — Verification.** Never cut over on faith. Three layers, used together:

1. **Row counts** per logical shard. Cheap, catches gross errors, catches nothing subtle.
2. **Checksums**: compute a rolling hash of `(primary_key, updated_at)` — or of the full row for high-stakes tables — in key-ordered chunks on both sides and compare. Chunking lets you narrow a mismatch to a 10,000-row window rather than "somewhere in 2 billion rows".
3. **Shadow reads (dark reads)**: send a sampled percentage of production reads to *both* old and new, return the old result to the user, and compare asynchronously. This is the only layer that validates the *query path*, not just the data — it catches routing bugs, index differences and planner surprises that checksums cannot.

Set an explicit mismatch budget (e.g. fewer than 1 in 10⁶ shadow reads, with every mismatch individually explained, not just counted). "Mismatch rate is low" is not an exit criterion; "every observed mismatch has a known, benign cause" is.

*Exit criterion:* checksums equal for 100 % of chunks and the shadow-read budget met over a full traffic cycle (at least 24 hours, to include the nightly batch window). *Rollback:* fix and re-backfill the affected ranges.

**Phase 5 — Cutover.** Flip the read path. Do it **incrementally and reversibly**:

- Flip one logical shard first (≈ 0.1 % of traffic) and soak for an hour.
- Then 1 %, 10 %, 50 %, 100 %, with a defined soak and a defined abort metric at each step (error rate, p99, mismatch rate).
- Keep dual writes running throughout. **This is what makes rollback instant**: if the new path misbehaves, flip reads back, and the old shard is still current because you never stopped writing to it.

*Exit criterion:* 100 % of reads on the new topology, metrics nominal for a full week. *Rollback:* flip the read flag back — seconds, no data movement.

**Phase 6 — Decommission.** Only after a soak of at least one full business cycle (a week minimum; a month is common for financial data): stop dual writes, make the new shards authoritative, take a final backup of the old shards, and keep them read-only but intact for a further retention period before deleting.

**The rule to remember:** rollback must be a *flag flip*, never a reverse migration. If your rollback plan is "migrate back", you have no rollback plan. Dual writes are what buy you this, and they are why the dual-write window extends through cutover and beyond rather than ending at it.

### 4.3 Hot partitions and the celebrity problem

A hot partition is a shard whose load is far above the mean while key distribution is fine. Causes, in rough order of frequency:

1. **A single hot key** — the celebrity problem. One book, one user, one tenant, one product.
2. **Low-cardinality or skewed shard key** — sharding on `status` where 92 % of rows are `'completed'` (Lesson 36 §9 Part B).
3. **Monotonic key with range sharding** — everything lands on the newest shard (Lesson 36 §4.1).
4. **Correlated access** — keys that are individually cold but always requested together and happen to hash to the same shard.
5. **Temporal spikes** — a flash sale, a ticket on-sale, a push notification driving every user to the same object at the same second.

**Detection.** You cannot mitigate what you cannot see, and shard-level dashboards are too coarse to find a hot *key*.

- **Shard-level skew metric**: `max_shard_qps / mean_shard_qps`, graphed continuously. Alert above 2.0. This tells you *that* you have a problem, not *which key*.
- **Heavy-hitter tracking at the routing layer.** Maintain a top-K structure over shard keys in a rolling window. A **count-min sketch** (a fixed-size 2-D array of counters, hashing each key into one counter per row, reading the minimum as the estimate) with width 2,048 and depth 4 is 8,192 counters — about 64 KB — and gives per-key frequency estimates with bounded error. Sampled 1-in-100 with a 10-second window, the CPU overhead is typically under 1 %. This is the tool that names the key.
- **Storage-engine telemetry**: DynamoDB `ConsumedCapacity` and throttle events per partition; Cassandra's `nodetool toppartitions`; Vitess per-keyspace QPS; Kafka per-partition lag. Use the native signal where one exists.
- **Latency distribution per shard.** A hot shard's p99 degrades before its CPU alarms, because queueing starts well before saturation (Lesson 14).
- **Cache-layer heavy hitters.** If you already run a cache (Lessons 27–30), its hot-key tracking often finds the problem first and more cheaply than the database's.

**Mitigation 1 — cache in front.** Almost always the first thing to try, and usually the best. A hot key is by definition requested repeatedly, which is the ideal cache profile. A 98 % hit rate turns 48,000 reads/second into 960. Section 9 Part E does the arithmetic. Combine with the single-flight/request-coalescing and probabilistic early refresh techniques from Lesson 29, because a hot key is exactly where a cache stampede is most damaging.

*Limits:* caching helps reads, not writes, and introduces staleness. If the hot key is write-heavy, or must be strongly consistent, you need something else.

**Mitigation 2 — key salting.** Replace the shard key `K` with `K || ':' || salt` where `salt ∈ [0, S)`, so the key spreads over up to S shards.

```
write: shard_key = f"{book_id}:{random.randrange(16)}"
read : must query all 16 salts and merge
```

The trade is explicit and severe: **salting divides write load by S and multiplies read fan-out by S.** For BookNook's hot book — 48,000 reads/second and 1,200 writes/second — salting by 16 reduces writes to 75/second per salt but raises reads to 768,000 shard queries/second. That is catastrophically wrong. Salting is correct only when writes dominate, or when reads always carry the salt (e.g. a counter you read in aggregate once per minute rather than per request).

*Refinement:* **adaptive salting** — salt only the keys identified as heavy hitters, and store the salt count in the directory. Cold keys stay unsalted with no fan-out; hot keys pay fan-out only on themselves. This requires readers to consult the directory for the salt count, which is exactly the directory-sharding hop from Lesson 36.

**Mitigation 3 — partition splitting.** Split the hot shard's key range in two and move half elsewhere. Automatic in range-sharded systems (CockroachDB, HBase, Bigtable split on size or load thresholds).

The critical limitation, which people routinely miss: **a single key cannot be split by range.** If 40 % of your load is `book_id = 9780000000001`, there is no boundary that divides it. Splitting helps with hot *ranges* and with shards that are hot because they hold many warm keys. It does nothing for a true celebrity. Know which situation you are in before reaching for this.

**Mitigation 4 — a dedicated shard.** Move the hot entity to its own shard so its load is isolated and its neighbours are protected.

Under pure hash sharding this is impossible — the function decides, and you cannot override it. Under **directory sharding (Lesson 36 §4.3) it is one `UPDATE`.** This is the strongest practical argument for keeping a directory layer alongside a hash: the hash routes 17,999,990 books, and the directory overrides the ten that matter. Isolation also contains the blast radius: when the celebrity's shard saturates, only the celebrity is affected.

**Mitigation 5 — change the access pattern.** Often the real fix. A live "reviews count" on a mega-release does not need to be exact or current. Replace the per-request read with a counter updated asynchronously from a stream and served from cache with a 10-second TTL. Pre-compute the top-50 reviews for hot books and serve a static blob from the CDN (Lesson 30). The best way to survive a hot key is frequently to stop reading it on the request path.

**A ranking to carry away**, since "it depends" is not an answer: for a **read-hot** key, try cache → dedicated shard → access-pattern change → salting. For a **write-hot** key, try salting → dedicated shard → write batching/aggregation → partition splitting. The ordering flips because caching cannot absorb writes and salting cannot absorb read fan-out.

### 4.4 Cross-shard queries: scatter-gather

When the shard key is absent from the `WHERE` clause, the router must ask every shard.

**Cost 1 — resource multiplication.** One client query becomes N shard queries. BookNook at 20,000 requests/second over 16 shards is 320,000 shard queries/second if every request fans out. Your database tier now needs 16× the capacity for the same user-visible throughput.

**Cost 2 — tail-latency amplification.** This is Lesson 16's fan-out formula, applied directly. If each shard independently exceeds some latency threshold with probability `p`, then the gather exceeds it with probability:

```
P(slow gather) = 1 − (1 − p)^N
```

With `p = 1 %` (the per-shard p99) the gather is "p99-slow" 14.9 % of the time at N = 16 and 63.4 % at N = 100. Section 9 Part C tabulates it.

A sharper way to state the same fact: because the gather's latency is the maximum of N samples, the gather's **median** corresponds to each shard's `0.5^(1/N)` quantile.

```
N = 16  :  0.5^(1/16)  = 0.9576  →  the gather's p50 ≈ each shard's p95.8
N = 100 :  0.5^(1/100) = 0.9931  →  the gather's p50 ≈ each shard's p99.3
```

**Half of your scatter-gather queries are as slow as a single shard's worst 4 %.** The typical user of a fan-out query experiences what, on a single shard, would be an outlier.

**Mitigations:**
- **Avoid the fan-out**: add the shard key to the query. Most fan-outs are accidental — an endpoint that takes `order_id` where it could take `(user_id, order_id)`. Audit them; this is usually the cheapest win available.
- **Self-routing IDs** (Lesson 35): embed the shard number in the generated ID so a lookup by ID routes to one shard.
- **Secondary index store**: maintain `email → user_id` or `isbn → book_id` in a small, separately-sharded lookup table. Two single-shard lookups beat one 16-way gather every time (≈ 2 × 2 ms versus a max-of-16).
- **Hedged requests**: if a shard has not replied by its p95, send a duplicate to another replica and take the first answer. Typical cost: ~5 % extra load. Typical benefit: a large p99 reduction. Requires idempotent reads.
- **Partial results with a deadline**: return what arrived within 200 ms and mark the response incomplete. Correct for search and recommendations; wrong for anything financial.
- **Bounded fan-out**: design so queries touch a known small subset of shards. Hierarchical keys (Lesson 36 §4.5) achieve this.
- **Separate read model**: push the fan-out shapes into a store built for them — Elasticsearch for search, ClickHouse for analytics — fed by change-data-capture. The sharded OLTP store stops being asked questions it is bad at.

### 4.5 Cross-shard pagination and sort-merge with over-fetch

Globally-ordered pagination across shards is the most commonly botched cross-shard operation, so work through it concretely.

Goal: "the 20 newest orders across the whole site, ordered by `created_at DESC`", with `orders` hash-sharded on `user_id` across 16 shards.

**Why you cannot just take `20/16` from each shard.** The global top 20 could, in principle, all come from one shard. Asking each shard for 2 rows and merging gives a *plausible but wrong* answer, and the error is invisible — the page looks fine.

**The correct naive algorithm — sort-merge with over-fetch.** To return rows `[offset, offset + limit)` globally, each shard must return its own first `offset + limit` rows in the same sort order. Then merge-sort the N sorted streams and take the window.

```
Page 1   (offset 0,  limit 20): 16 × 20    =    320 rows fetched,  20 returned → 16× over-fetch
Page 51  (offset 1000, limit 20): 16 × 1,020 = 16,320 rows fetched, 20 returned → 816× over-fetch
Page 501 (offset 10000,limit 20): 16 × 10,020 = 160,320 rows fetched, 20 returned → 8,016× over-fetch
```

At 400 bytes per row, page 501 moves 64 MB across the network to render a page of 20 items. The cost grows **linearly with page depth and linearly with shard count**. This is why deep pagination over a sharded dataset is a reliable way to take down a cluster, and why public APIs cap page depth.

**The fix — keyset (cursor) pagination.** Instead of an offset, carry the sort position of the last row seen as an opaque cursor. Because `created_at` is not unique, the cursor must be a *tuple* that is total-ordered: `(created_at, order_id)`.

```sql
-- Each shard, every page, regardless of depth:
SELECT ... FROM orders
WHERE (created_at, order_id) < ($cursor_ts, $cursor_id)   -- tuple comparison
ORDER BY created_at DESC, order_id DESC
LIMIT 20;
```

```
Any page, any depth: 16 × 20 = 320 rows fetched, 20 returned → 16× over-fetch, constant
Page 501: 160,320 rows  →  320 rows.   A 500× reduction.
Bytes:        64 MB     →  128 KB.
```

Keyset pagination removes the depth term entirely. The `16×` over-fetch remains, and that is irreducible for a correct global sort — you cannot know which shard holds the next row without asking.

**Reducing the residual over-fetch — fetch-k-then-top-up.** Ask each shard for `k < limit` rows, merge, and then *verify* the result is provably correct: the merged page is correct only if the 20th merged row sorts **after** the last row returned by every shard. If any shard's last returned row still sorts after the cutoff, that shard might hold more qualifying rows, so re-query just that shard for more.

With 16 shards and uniform distribution, the number of the global top 20 coming from any one shard is Binomial(20, 1/16) with mean 1.25. Choosing `k = 10` makes a top-up almost never necessary:

```
Rows fetched with k = 10 : 16 × 10 = 160  (vs 320)
P(one given shard contributes > 10 of the top 20) ≈ 3 × 10⁻⁸
P(any of 16 shards does)                          ≈ 5 × 10⁻⁷
⇒ roughly 1 request in 2 million needs a second round trip.
```

Halving the fetch cost for a one-in-two-million extra round trip is a good trade **under uniform distribution**. If distribution is skewed — the data is clustered by shard, or you are sorting by something correlated with the shard key — the top-up rate rises sharply and the optimisation can cost more than it saves. Measure the actual top-up rate in production and set `k` from it; do not assume.

### 4.6 Cross-shard joins

Lesson 35 said joins across shards are gone. They are not *literally* impossible — a proxy like Vitess will execute some of them — but doing so at scale means shipping rows between shards, which is slow, memory-hungry and unpredictable. The production patterns are:

**Pattern 1 — reference-table replication.** Small, slowly-changing tables are copied in full to every shard, so the join is local. BookNook's `books` (40 GB, 0.02 writes/second) is replicated to all 16 shards (Lesson 36 §7). Updates propagate by change-data-capture with typical lag of seconds. Cost: 640 GB of duplicated storage and eventual consistency on book metadata — both acceptable here.

**Pattern 2 — denormalisation.** Copy the few columns you need into the table that needs them. `reviews` stores `user_display_name` alongside `user_id`, so rendering a review needs no join at all. Cost: when a user renames themselves, you must fan out an update across every shard holding their reviews, and until it completes the name is stale. Decide explicitly whether staleness is acceptable (for a display name, almost always yes; for a price or a permission, almost always no).

**Pattern 3 — application-side join / aggregation service.** Query shard A, collect the keys, query shard B with `WHERE id IN (...)`, join in memory. Workable when the first query returns a bounded, small number of keys (tens to low hundreds). It becomes an anti-pattern when the first query returns thousands — and it is the mechanism behind the classic **N+1 query** problem, now multiplied by network hops. Always batch the second query; never issue it in a loop.

**Pattern 4 — per-shard partial aggregates.** For `COUNT`, `SUM`, `MIN`, `MAX` and `AVG`, push the aggregation down: each shard computes its own partial result, and the coordinator combines them. `COUNT` and `SUM` combine by addition; `MIN`/`MAX` by extremum; `AVG` must be transmitted as `(sum, count)` and combined before dividing — combining pre-divided averages is simply wrong unless the shards hold equal counts. `COUNT(DISTINCT x)` does **not** decompose at all; it needs either full key shipping or an approximate sketch such as HyperLogLog (typical error ~2 % for 1.5 KB of state), which is usually the right answer for dashboards.

**Pattern 5 — a separate read model.** Stream changes into a store designed for the query shape: a search index, a columnar analytics store, or a materialised view maintained by a stream processor. This is how "revenue by category this quarter" should be answered — not by a 16-way gather against your OLTP shards. The pipeline is covered in **Phase 4, Topic 5 — Message queues**; the consistency model is eventual, with typical end-to-end lag of seconds to a few minutes.

### 4.7 Cross-shard transactions

A single-shard transaction keeps full ACID semantics — this is why Lesson 36 stressed co-locating an entity group on one shard. A transaction spanning shards does not: there is no shared write-ahead log, no shared lock manager, and no common commit point.

Your options, in brief:

- **Design so you never need one.** Make the shard key the transactional boundary. Overwhelmingly the best answer and the one interviewers want to hear.
- **Two-phase commit (2PC).** A coordinator asks all participants to prepare, then to commit. Correct, but it is a *blocking* protocol: if the coordinator fails after prepare, participants hold locks indefinitely. It also makes availability multiplicative — all N shards must be up for any transaction to commit.
- **Sagas.** Decompose into a sequence of local transactions, each with a compensating action to undo it. You trade atomicity for availability and accept that intermediate states are visible.
- **Idempotent, eventually-consistent workflows** with reconciliation — common in payments, where a nightly reconciliation job is a first-class component rather than an admission of defeat.

All of this is treated properly in **Phase 4, Topics 19–21 — distributed transactions, two-phase commit, and the saga pattern**. For now, the thing to internalise is the design rule: *if two rows must change atomically, they should be on the same shard, and that requirement should drive your shard key.*

## 5. Request / data flow

BookNook is resharding `orders` from 16 physical shards to 32, and simultaneously handling a mega-release that has made one book a celebrity. Here is a single production write passing through the system mid-migration, followed by the hot-key read path.

1. `POST /orders` arrives; the app authenticates the session and extracts `user_id = 48_201_773` (Lesson 2). The shard key comes from the authenticated identity, never from the request body.
2. The routing library computes `logical = murmur3(user_id) mod 1024 = 617`.
3. It reads **two** maps: `old_map[617] = shard 9` and `new_map[617] = shard 25`. Both are in process memory, each stamped with a map **epoch**.
4. The migration flag for logical shard 617 is `DUAL_WRITE`, so the old shard remains authoritative.
5. The app opens a transaction on **shard 9** (the authoritative primary), inserts the order row, and in the *same local transaction* inserts a row into shard 9's `outbox` table containing the full new-shard payload. Atomicity is preserved because both writes are on one shard — the design rule from §4.7.
6. The transaction commits. The user receives `201 Created`. **The new shard has not been touched on the request path**, so nothing about shard 25 can fail this request.
7. An outbox consumer polls shard 9, reads the new row, and applies it to **shard 25** using `INSERT ... ON CONFLICT (user_id, order_id) DO UPDATE ... WHERE excluded.updated_at > orders.updated_at`. Idempotent, and safe to replay. Typical lag: 200 ms–2 s.
8. Shard 25's `CHECK (murmur3(user_id) % 1024 BETWEEN 576 AND 639)` constraint validates that this row genuinely belongs here. A stale map in any writer would raise a loud error here rather than silently misplacing the row.
9. Meanwhile the backfill job iterates shard 9 by keyset (`WHERE (user_id, order_id) > :cursor LIMIT 10000`), throttled to 20 % of shard 9's capacity, backing off whenever shard 9's p99 exceeds 15 ms. It applies rows to shard 25 with the same idempotent upsert, so a backfilled row can never clobber a newer dual-written one.
10. A verification job computes chunked checksums over both sides and records per-chunk equality. A shadow-read job sends 1 % of production reads to both shards and compares results asynchronously; the user always receives the shard 9 result.
11. When all chunks match and the shadow-read mismatch budget is met over a full 24-hour cycle, the operator flips logical shard 617 to `READ_NEW`. Reads now go to shard 25; **dual writes continue**, so flipping back is instant and lossless.
12. Separately, a read arrives for the celebrity book's review page: `GET /books/9780000000001`. The review router hashes `book_id` to `reviews` shard 4.
13. Before touching shard 4, the app checks the application cache (Lesson 28). The key `reviews:top:9780000000001:v7` hits 98 % of the time, so 47,040 of 48,000 requests/second never reach the database.
14. On a miss, a single-flight lock (Lesson 29) ensures exactly one request rebuilds the entry; the other concurrent misses wait for it. Without this, a cache expiry on a celebrity key is a stampede that takes the shard down.
15. The heavy-hitter sketch at the routing layer has already flagged `9780000000001` as 41 % of all `reviews` traffic. An operator writes one row into the shard directory: `9780000000001 → shard 17 (dedicated)`. The map epoch increments; app caches refresh within their TTL; the celebrity's load is now isolated and shard 4 returns to normal.

The pattern across both flows: **make the risky path asynchronous and idempotent, keep the authoritative write single-shard, and make every transition reversible by a flag.**

## 6. ASCII diagram

```
WHY hash(k) % N IS HOSTILE TO RESIZING
  N = 16 → 17                               N = 16 → 32 (doubling)
  keys that stay = 1/17 = 5.9 %             keys that stay = 1/2 = 50 %
  keys that MOVE = 94.1 %                   keys that MOVE = 50 %
  4 TB table ⇒ 3.76 TB moved                4 TB table ⇒ 2.0 TB moved
                                            but you bought 16 idle machines

LOGICAL SHARDS — the fix
  key ──murmur3 % 1024──▶ logical 617 ──▶ [ map ] ──▶ physical 9
                          (NEVER changes)   (editable)
  16 → 17 physical: reassign 1024/17 ≈ 60 logical shards = 5.9 % of data.
  Same 5.9 % as a consistent-hash ring (Lesson 38), with a map you can print.


THE MIGRATION PLAYBOOK — rollback is a FLAG FLIP at every stage
  0 FREEZE ──▶ 1 PROVISION ──▶ 2 DUAL WRITE ──▶ 3 BACKFILL ──▶
                                   │                  │
                                   │  outbox, idempotent upsert, throttled
                                   ▼                  ▼
  4 VERIFY (counts + chunk checksums + shadow reads) ──▶
  5 CUTOVER  0.1% → 1% → 10% → 50% → 100%   (dual writes STILL ON)
                   │
                   └── abort = flip the read flag back. Seconds. No data moved.
  6 DECOMMISSION  (only after ≥1 full business cycle)


THE CELEBRITY PROBLEM — even keys, uneven LOAD
  reviews sharded on book_id, 16 shards, key distribution ±0.2 %
  ┌────┬────┬────┬════╦═══┬────┬────┬────┐
  │ s0 │ s1 │ s2 │▓ s4 ▓│ s5 │ …  │s15 │
  │12%│12%│11%│▓95%▓│12%│    │12%│   ← CPU
  └────┴────┴────┴════╩═══┴────┴────┴────┘
                   ▲
       one book_id = 41 % of all review traffic.
       No hash function fixes this. The key, not the distribution, is the problem.

  READ-hot  : cache (98% hit: 48,000/s → 960/s) → dedicated shard → change
              the access pattern → salt (LAST: salting multiplies reads by S)
  WRITE-hot : salt (1,200/s ÷ 16 = 75/s) → dedicated shard → batch → split
              (NOTE: range-splitting CANNOT split a single key)


SCATTER-GATHER — your latency is the MAX of N, not the average
                    ┌── s0  ──  3 ms ──┐
  query ──────▶ ────┼── s1  ──  4 ms ──┼──▶ merge ──▶ 180 ms
  (no shard key)    ├── s2  ──  3 ms ──┤      ▲
                    ├── …   ──  … ─────┤      │
                    └── s13 ── 180 ms ─┘   ONE slow shard sets the latency

  p = 1 % per shard ⇒ P(slow gather) = 1 − 0.99^N
     N=4 : 3.9 %      N=16 : 14.9 %     N=64 : 47.4 %     N=100 : 63.4 %
  Gather's p50 ≈ each shard's p95.8 at N=16, p99.3 at N=100.


CROSS-SHARD PAGINATION — offset is a trap, cursors are not
  "20 newest orders site-wide", 16 shards, 400 B/row

  OFFSET 10000 :  each shard returns 10,020 rows →  160,320 rows,  64 MB
                  ████████████████████████████████████████  for 20 items
  KEYSET cursor:  each shard returns     20 rows →      320 rows, 128 KB
                  ▌                                        at ANY depth
  500× fewer rows. The remaining 16× over-fetch is irreducible for a
  correct global sort — you cannot know which shard holds the next row.
```

## 7. Detailed example

### BookNook — a reshard and a celebrity, in the same quarter

**The trigger.** Eighteen months after sharding, BookNook's `orders` cluster is at 16 shards carrying 4.0 TB and 5,100 writes/second at peak. Per-shard writes are 319/second against a 5,000/second ceiling — comfortable. But the *working set* is the binding constraint: 4.0 TB across 16 shards is 250 GB per shard, the hot fraction is 18 %, i.e. 45 GB, and each shard has 64 GB of RAM. At 1.8× annual growth the hot set crosses 64 GB in about 8 months, after which the buffer-pool hit rate collapses and p99 latency rises sharply (Lesson 35's capacity method). BookNook decides to go to 32 shards.

**Why it is not a disaster.** Because BookNook adopted logical shards on day one (Lesson 36 §4.2), the reshard is a reassignment of 512 of the 1,024 logical shards, not a rehash. Going to 32 shards by doubling moves exactly 50 % of the data — 2.0 TB, 1.0 B rows. Had they hashed directly onto 16 physical shards and wanted 17, they would have moved 94.1 %.

**The migration, with real numbers.**

```
Data to move          : 2.0 TB  (1.0 B rows)
Per-source-shard copy : 25 MB/s sustained (20 % of each shard's capacity)
Parallel sources      : 16
Aggregate copy rate   : 400 MB/s
Pure copy time        : 2,000,000 MB / 400 MB/s = 5,000 s = 1.39 hours
```

That 1.39 hours is the number people quote in planning meetings, and it is wrong by about an order of magnitude. Add: index builds on 16 new shards (typically 2–3 hours), checksum verification across 1,024 chunked ranges, automatic throttle back-offs whenever a source shard's p99 exceeds 15 ms (which happens during every evening peak, roughly halving effective throughput for 5 hours a day), and restarts. The realistic backfill window is **12–20 hours of wall-clock time spread over 2–3 days**, which is why the planning rule of thumb is 3–5× the pure-copy estimate.

Then the parts that dominate the calendar: a 7-day dual-write soak before cutover, a 24-hour verification cycle, a staged cutover over 3 days, and a 14-day decommission soak. **Total project: about 4 weeks of elapsed time for 1.39 hours of copying.** That ratio is the single most useful thing to remember about resharding, and it is why Lesson 36 insisted the strategy decision is effectively made once.

**The celebrity, simultaneously.** Mid-migration, a mega-release lands. One `book_id` takes 41 % of all `reviews` traffic: 48,000 review reads/second and 1,200 review writes/second, all hashing to `reviews` shard 4, whose ceilings are approximately 30,000 reads/second and 5,000 writes/second.

- Reads at 48,000/second exceed the 30,000/second ceiling by 1.6×. Shard 4 is past the queueing cliff; its p99 goes from 8 ms to 2,400 ms, and because book pages also read `books`, the damage spreads.
- Writes at 1,200/second are only 24 % of ceiling. **Writes are not the problem**, which immediately rules out salting as the primary fix.

What BookNook does, in order, and why:

1. **Cache first** (minutes to deploy, because the cache already exists). `reviews:top:{book_id}` with a 60-second TTL, single-flight on miss (Lesson 29). Measured hit rate 98 % → 960 reads/second reach shard 4. Shard 4's p99 returns to 9 ms. **This alone solved it.**
2. **Dedicated shard** (hours). The heavy-hitter sketch names the key; one row in the directory moves it to shard 17. This is insurance: it isolates the celebrity so that a cache failure degrades one book rather than the whole `reviews` keyspace.
3. **Access-pattern change** (days). The review *count* and *average rating* move to counters updated from a stream and served from cache; the top-50 reviews for the hot book are pre-rendered and served from the CDN (Lesson 30). Peak database reads for that book fall to roughly 50/second.
4. **Salting: explicitly rejected.** Salting by 16 would cut writes from 1,200/second to 75/second per salt — solving a problem that did not exist — while raising reads from 48,000 to 768,000 shard queries/second and adding a 16-way gather with 14.9 % tail amplification to the busiest page in the product. Writing down *why* a technique was rejected is as valuable as the technique you chose.

### Contrasting example — a ticketing platform's on-sale

A ticketing company sells concert tickets. Its `inventory` table is sharded on `event_id`. When a stadium tour goes on sale at 09:00, one `event_id` receives 140,000 requests/second, and — critically — the dominant operation is a **write**: decrementing seat inventory. Every conclusion from BookNook inverts.

- **Caching does not help.** You cannot serve a cached seat count and then sell the same seat twice. Correctness forbids the primary mitigation.
- **Salting is now the right tool**, because writes dominate. Inventory for the event is pre-split into 200 **buckets** of seats, each a separate row keyed `event_id:bucket`, hashed across shards. A purchase picks a bucket (randomly, or by seat section), decrements it in a single-shard transaction, and the aggregate count is the sum of 200 rows. Writes spread over up to 200 shards; the 200-way read fan-out is acceptable because the "how many left" display is refreshed once a second, not per request, and can be approximate.
- **The hot key is known in advance.** Unlike BookNook's unpredictable bestseller, an on-sale is scheduled. The directory assigns the event a dedicated shard (or an entire dedicated cluster) *before* 09:00, and tears it down afterwards. Predictability converts a reactive incident into a planned provisioning task.
- **The queue is the real answer.** Above a threshold the platform stops trying to serve 140,000 concurrent writes and instead admits users through a virtual waiting room, converting a load problem into a scheduling problem (**Phase 4, Topic 5 — Message queues**). This mirrors Lesson 23's conclusion that pull-based work distribution sidesteps balancing problems entirely.
- **Resharding is irrelevant here.** The hot event is hot for four hours and cold forever after. Migrating data would take longer than the problem lasts. The correct response to a *transient* hot partition is isolation and admission control, not rebalancing.

The transferable lesson: **the same symptom — one shard at 95 % while fifteen idle — has opposite correct responses depending on whether the load is reads or writes, whether it is predictable, and whether it is permanent or transient.** Those are the three questions to ask before choosing a mitigation, and they are what "it depends" actually depends on here.

## 8. Code or configuration example

> Illustrative only — not production-ready. Transaction management, connection pooling, retries with backoff, timeouts, metrics, authorisation, and schema-migration tooling are omitted for clarity. In particular, the migration code below is a sketch of the control flow; real resharding should use battle-tested tooling (Vitess `MoveTables`/`Reshard`, gh-ost, pt-online-schema-change, Debezium) rather than hand-rolled scripts.

### 8.1 Resharding: dual writes, outbox, throttled backfill

```python
import enum, hashlib, time

class Mode(enum.Enum):
    OLD_ONLY   = 1   # before migration
    DUAL_WRITE = 2   # writes to both; OLD is authoritative; reads from OLD
    READ_NEW   = 3   # writes to both; NEW is authoritative; reads from NEW
    NEW_ONLY   = 4   # after decommission

# Per-LOGICAL-shard migration state. Flipping one logical shard at a time is
# what makes cutover incremental: shard 617 alone is ~0.1 % of traffic, so a
# bad flip affects 1 user in 1,000 for the minute it takes to flip back.
migration_mode = {}          # logical_shard -> Mode
map_epoch      = 7           # bumped on EVERY map change; see check below


def write_order(order):
    logical = murmur3(order.user_id) % 1024
    mode    = migration_mode.get(logical, Mode.OLD_ONLY)
    old     = old_map[logical]
    new     = new_map[logical]

    # The AUTHORITATIVE shard is chosen by mode, not by which map is "newer".
    primary = new if mode in (Mode.READ_NEW, Mode.NEW_ONLY) else old

    with transaction(primary) as tx:
        tx.execute(
            "INSERT INTO orders (order_id, user_id, status, total_cents,"
            "                    created_at, updated_at)"
            " VALUES (%s,%s,%s,%s,%s,now())",
            (order.id, order.user_id, order.status, order.total, order.created_at),
        )
        if mode in (Mode.DUAL_WRITE, Mode.READ_NEW):
            # OUTBOX, written in the SAME local transaction as the row itself.
            # This is the whole trick: the second write never touches the
            # request path, so a sick destination shard cannot fail user
            # requests, yet the two shards are guaranteed to converge because
            # the outbox row commits atomically with the data row.
            tx.execute(
                "INSERT INTO outbox (dest_shard, payload, epoch)"
                " VALUES (%s,%s,%s)",
                (new if primary is old else old, order.to_json(), map_epoch),
            )
    # NOTE: we do NOT write synchronously to the second shard. Doing so would
    # double the request's failure probability for the entire migration window.


def apply_to_destination(dest_shard, payload, epoch):
    """Outbox consumer. Runs at-least-once, so it MUST be idempotent."""
    if epoch < current_epoch_for(dest_shard):
        # STALE MAP GUARD. A writer running old code with an old map would
        # otherwise silently place rows on the wrong shard -- a corruption
        # that is typically discovered weeks later, by a customer.
        raise StaleEpoch(epoch)

    execute(dest_shard,
        "INSERT INTO orders (order_id, user_id, status, total_cents,"
        "                    created_at, updated_at)"
        " VALUES (%(id)s,%(uid)s,%(st)s,%(tot)s,%(cat)s,%(upd)s)"
        # Idempotent upsert: replaying the same message is a no-op.
        " ON CONFLICT (user_id, order_id) DO UPDATE SET"
        "   status = EXCLUDED.status, total_cents = EXCLUDED.total_cents,"
        "   updated_at = EXCLUDED.updated_at"
        # LAST-WRITE-WINS GUARD: without this clause a delayed replay (or the
        # backfill below) could overwrite a NEWER value with an OLDER one.
        " WHERE EXCLUDED.updated_at > orders.updated_at",
        payload)


def backfill(source_shard, dest_resolver, chunk=10_000):
    """Copy pre-existing rows. Keyset iteration + adaptive throttling."""
    cursor = load_checkpoint(source_shard) or (0, 0)   # resumable: an 11-hour
                                                       # job must never restart
                                                       # from zero
    while True:
        rows = execute(source_shard,
            # KEYSET, never OFFSET. "OFFSET 10000000" forces the database to
            # scan and discard ten million rows; this is constant-cost per chunk.
            "SELECT * FROM orders"
            " WHERE (user_id, order_id) > (%s, %s)"
            " ORDER BY user_id, order_id LIMIT %s",
            (*cursor, chunk))
        if not rows:
            break

        for r in rows:
            # Same idempotent upsert as the outbox path, so backfill and live
            # dual writes can race freely without corrupting each other.
            apply_to_destination(dest_resolver(r.user_id), r, map_epoch)

        cursor = (rows[-1].user_id, rows[-1].order_id)
        save_checkpoint(source_shard, cursor)

        # ADAPTIVE THROTTLE. An unthrottled backfill is indistinguishable from
        # an outage. Back off on the signal that actually predicts user pain --
        # production p99 -- not on a fixed sleep.
        p99 = production_p99_ms(source_shard)
        if p99 > 15:
            time.sleep(2.0)      # hard brake
        else:
            time.sleep(0.05)     # ~20 % duty cycle at this chunk size


def verify_chunk(source_shard, dest_shard, lo, hi):
    """Chunked checksums localise a mismatch to 10,000 rows, not 2 billion."""
    sql = ("SELECT md5(string_agg(user_id||':'||order_id||':'||updated_at, ','"
           "            ORDER BY user_id, order_id))"
           " FROM orders WHERE (user_id, order_id) >= %s"
           "              AND (user_id, order_id) < %s")
    return execute(source_shard, sql, (lo, hi)) == execute(dest_shard, sql, (lo, hi))
```

### 8.2 Hot-key detection and the three mitigations

```python
import random

class HeavyHitterSketch:
    """Count-min sketch: fixed memory, per-key frequency ESTIMATES.

    width 2048 x depth 4 = 8,192 counters ~= 64 KB for the whole keyspace.
    Estimates never UNDER-count, which is the property we want: we would
    rather investigate a false hot key than miss a real one.
    """
    WIDTH, DEPTH = 2048, 4
    SAMPLE_RATE  = 0.01          # 1-in-100 sampling keeps overhead under ~1 % CPU

    def __init__(self):
        self.c = [[0] * self.WIDTH for _ in range(self.DEPTH)]
        self.top = {}            # exact counts for the few keys we care about

    def observe(self, key):
        if random.random() > self.SAMPLE_RATE:
            return
        est = []
        for i in range(self.DEPTH):
            j = murmur3(f"{i}:{key}") % self.WIDTH
            self.c[i][j] += 1
            est.append(self.c[i][j])
        # The MINIMUM across rows is the estimate: collisions only inflate,
        # so the smallest counter is the tightest upper bound.
        freq = min(est) / self.SAMPLE_RATE
        if freq > HOT_THRESHOLD:
            self.top[key] = freq


# --- Mitigation 1: CACHE. First choice for a READ-hot key. --------------
def get_reviews(book_id):
    ck = f"reviews:top:{book_id}:v7"
    if (hit := cache.get(ck)) is not None:
        return hit
    # SINGLE-FLIGHT (Lesson 29). Without this, the moment a celebrity key
    # expires, every concurrent request misses simultaneously and the
    # stampede alone can take the shard down -- worse than the original load.
    with cache.single_flight(ck, ttl=5):
        if (hit := cache.get(ck)) is not None:   # double-check after the lock
            return hit
        rows = query(shard_for(book_id),
                     "SELECT * FROM reviews WHERE book_id=%s"
                     " ORDER BY helpful_votes DESC LIMIT 50", (book_id,))
        cache.set(ck, rows, ttl=60)
        return rows


# --- Mitigation 2: SALTING. Correct ONLY when WRITES dominate. ----------
SALTS = 16

def write_vote(book_id, delta):
    # Spreads 1,200 writes/s over 16 shards -> 75/s each.
    salt = random.randrange(SALTS)
    shard = shard_for(f"{book_id}:{salt}")
    execute(shard, "INSERT INTO vote_counter (k, n) VALUES (%s,%s)"
                   " ON CONFLICT (k) DO UPDATE SET n = vote_counter.n + %s",
            (f"{book_id}:{salt}", delta, delta))

def read_votes(book_id):
    # THE PRICE, stated plainly: every read now fans out to SALTS shards and
    # inherits the tail amplification of Section 9 Part C. For BookNook's
    # 48,000 reads/s this would mean 768,000 shard queries/s -- which is why
    # salting was REJECTED there and chosen for the ticketing example instead.
    return sum(execute(shard_for(f"{book_id}:{s}"),
                       "SELECT n FROM vote_counter WHERE k=%s",
                       (f"{book_id}:{s}",)) or 0
               for s in range(SALTS))


# --- Mitigation 3: DEDICATED SHARD, via the directory (Lesson 36). ------
def shard_for(key):
    # One directory lookup overrides the hash for the handful of keys that
    # matter. 17,999,990 books are routed by the hash; the 10 celebrities are
    # routed by this table. Isolating a celebrity is ONE row, not a reshard.
    if (override := directory.get(key)) is not None:
        return override
    return physical_map[murmur3(key) % 1024]
```

### 8.3 Cross-shard pagination: offset versus keyset

```python
import heapq

def page_offset(offset, limit, shards):
    """NAIVE. Correct, and a reliable way to take down a cluster."""
    per_shard = []
    for s in shards:
        # Each shard must return offset+limit rows, because in the worst case
        # the entire global window could come from this one shard.
        per_shard.append(query(s,
            "SELECT created_at, order_id, * FROM orders"
            " ORDER BY created_at DESC, order_id DESC LIMIT %s",
            (offset + limit,)))
    # offset=10,000, limit=20, 16 shards -> 160,320 rows fetched for 20 shown.
    # At 400 B/row that is 64 MB across the network, PER PAGE VIEW.
    merged = heapq.merge(*per_shard, key=lambda r: (r.created_at, r.order_id),
                         reverse=True)
    return list(itertools.islice(merged, offset, offset + limit))


def page_keyset(cursor, limit, shards):
    """CORRECT AND CONSTANT-COST. Use this."""
    ts, oid = cursor or (MAX_TS, MAX_ID)
    per_shard = []
    for s in shards:
        per_shard.append(query(s,
            # TUPLE comparison, not "created_at < ts". created_at is not
            # unique; ties would silently skip or duplicate rows across pages.
            # The cursor must be a TOTAL order, hence (created_at, order_id).
            "SELECT created_at, order_id, * FROM orders"
            " WHERE (created_at, order_id) < (%s, %s)"
            " ORDER BY created_at DESC, order_id DESC LIMIT %s",
            (ts, oid, limit)))
    # 16 x 20 = 320 rows at ANY depth. Page 1 and page 50,000 cost the same.
    merged = heapq.merge(*per_shard, key=lambda r: (r.created_at, r.order_id),
                         reverse=True)
    page = list(itertools.islice(merged, limit))
    # The next cursor is the sort position of the last row RETURNED, which is
    # why it must be opaque to the client -- it encodes sort state, not an index.
    next_cursor = (page[-1].created_at, page[-1].order_id) if page else None
    return page, next_cursor


def cross_shard_avg(shards):
    """Partial aggregates. Note what does and does not decompose."""
    totals = [query(s, "SELECT sum(total_cents) AS s, count(*) AS c"
                       " FROM orders WHERE created_at >= %s", (Q_START,))
              for s in shards]
    # AVG must be shipped as (sum, count) and combined BEFORE dividing.
    # Averaging the shards' averages is wrong unless every shard holds an
    # identical row count -- a mistake that produces plausible, wrong numbers.
    return sum(t.s for t in totals) / sum(t.c for t in totals)
    # COUNT(DISTINCT x) does NOT decompose at all. Use HyperLogLog
    # (~2 % error for ~1.5 KB of state) or ship the keys. There is no third option.
```

## 9. Numerical example

### Part A — How much data moves when N changes

**Assumptions:** hash values uniformly distributed; keys uniformly distributed across shards; 4.0 TB table; moving to `M` shards from `N`.

Derivation (from §4.1): `P(key stays) = 1 / (N + 1)` for `M = N + 1`.

```
N → N+1      fraction that MOVES        data moved from a 4.0 TB table
-----------------------------------------------------------------------
 2 → 3       2/3   = 66.7 %             2.67 TB
 4 → 5       4/5   = 80.0 %             3.20 TB
 8 → 9       8/9   = 88.9 %             3.56 TB
16 → 17      16/17 = 94.1 %             3.76 TB
32 → 33      32/33 = 97.0 %             3.88 TB
64 → 65      64/65 = 98.5 %             3.94 TB
100 → 101    100/101 = 99.0 %           3.96 TB
```

**Doubling is the exception** (`h mod 2N` is either `h mod N` or `h mod N + N`):

```
N → 2N       fraction that MOVES = 1/2 exactly, for every N
 8 → 16      50.0 %                     2.00 TB
16 → 32      50.0 %                     2.00 TB
```

**With 1,024 logical shards, or an equivalent consistent-hash ring:**

```
Resize       logical shards reassigned   fraction of data    data moved
-----------------------------------------------------------------------
16 → 17      1024/17 = 60.2              5.9 %               0.24 TB
16 → 32      1024/32 × 16 = 512          50.0 %              2.00 TB
32 → 33      1024/33 = 31.0              3.0 %               0.12 TB
```

**The comparison:**

```
16 → 17 with raw hash(k) % N : 3.76 TB moved
16 → 17 with logical shards  : 0.24 TB moved
Reduction                    : 15.7×
```

**Read this carefully, because it is the trap:** logical shards reduce the 16 → 17 case by 15.7×, but they do **nothing** for the 16 → 32 case — doubling moves half the data either way, because half the data genuinely belongs elsewhere. Logical shards and consistent hashing eliminate *gratuitous* movement, not *necessary* movement. The real benefit is that they make small, cheap resizes (16 → 17 → 18) possible at all, so you stop being forced into 100 %-cost doublings.

### Part B — How long the migration actually takes

**Assumptions:** 16 → 32 shards; 2.0 TB and 1.0 B rows to move; each source shard sustains 25 MB/s of copy without harming production (20 % of its capacity); 16 sources in parallel; peak hours occupy 5 hours/day during which the adaptive throttle roughly halves throughput; index builds take 2.5 hours per destination shard but run in parallel across the 16 new shards; verification reads both sides at 150 MB/s aggregate.

```
Aggregate copy rate        = 16 × 25 MB/s = 400 MB/s
Pure copy time             = 2,000,000 MB / 400 MB/s = 5,000 s = 1.39 h

Throttle derating:
  19 h/day at full rate, 5 h/day at 50 %
  effective daily throughput = (19 × 400 + 5 × 200) / 24 = (7,600 + 1,000)/24
                             = 358 MB/s  (89.6 % of nominal)
Derated copy time          = 2,000,000 / 358 = 5,587 s = 1.55 h

Index builds (parallel)    = 2.5 h
Verification (both sides)  = 2 × 2,000,000 MB / 150 MB/s = 26,667 s = 7.4 h
Restarts / operator time   ≈ 3 h  (typical, and always underestimated)
--------------------------------------------------------------------------
Technical work total       ≈ 1.55 + 2.5 + 7.4 + 3 = 14.5 hours
```

```
Calendar time, including the non-negotiable soaks:
  Dual-write soak before backfill        :  2 days
  Backfill + index + verify (14.5 h,
    spread across maintenance windows)   :  3 days
  Shadow-read verification cycle         :  1 day
  Staged cutover 0.1 → 1 → 10 → 50 → 100 :  3 days
  Post-cutover soak before decommission  : 14 days
--------------------------------------------------------------------------
  Total elapsed                          : ~23 days  (≈ 4 weeks)

Ratio of elapsed time to pure copy time = 23 days / 1.39 h ≈ 397×
```

**The conclusion to internalise:** resharding is not bounded by disk or network. It is bounded by *the time required to be confident you have not lost data*. Any plan that quotes the copy time as the project time is a plan that has not included verification or rollback.

### Part C — Scatter-gather tail amplification

**Assumptions:** each shard independently exceeds its 150 ms threshold with probability `p = 1 %` (i.e. 150 ms is its p99); shard latencies are independent (optimistic — correlated failures make this worse); the gather waits for all shards.

Formula from Lesson 16: `P(slow gather) = 1 − (1 − p)^N`.

```
N      (1 − 0.01)^N       P(gather exceeds 150 ms)
----------------------------------------------------
  4    0.960596           3.94 %
  8    0.922745           7.73 %
 16    0.851458          14.85 %
 32    0.724980          27.50 %
 64    0.525596          47.44 %
100    0.366032          63.40 %
```

Equivalent statement via quantiles. The gather's latency is `max` of N samples, so the gather's q-th quantile corresponds to each shard's `q^(1/N)` quantile:

```
Gather p50, N = 16  : 0.5^(1/16)  = 0.9576  → each shard's p95.8
Gather p50, N = 100 : 0.5^(1/100) = 0.9931  → each shard's p99.3
Gather p99, N = 16  : 0.99^(1/16) = 0.99937 → each shard's p99.94
```

**Interpretation.** At 16 shards, the *typical* scatter-gather query is as slow as a single shard's worst 4 % of queries, and the gather's p99 is governed by per-shard behaviour you almost certainly do not measure (p99.94 is 6 requests in 10,000 — invisible on a standard dashboard). This is why "just add shards" degrades fan-out queries even when each shard gets faster.

**Resource cost at BookNook scale:**

```
Requests/second needing fan-out = 20,000
Shards                          = 16
Shard queries/second            = 320,000
Capacity required vs a single-shard-routable query = 16×
```

**Effect of hedged requests** (send a duplicate to another replica if no answer by the shard's p95):

```
Extra load          = ~5 % (only the slowest 5 % are duplicated)
P(both the original AND the hedge are slow) ≈ 0.01 × 0.01 = 0.0001 per shard
P(slow gather), N=16 = 1 − (1 − 0.0001)^16 = 0.16 %
Improvement vs 14.85 % = 93× fewer slow gathers, for 5 % more load.
```

That is one of the best cost-to-benefit ratios available anywhere in Phase 2, and it costs nothing but idempotent reads.

### Part D — Cross-shard pagination

**Assumptions:** 16 shards; page size 20; rows average 400 bytes on the wire; global sort on `(created_at DESC, order_id DESC)`; data distributed uniformly across shards.

**Offset pagination — rows fetched to render 20:**

```
Page    Offset    Per shard (offset+limit)   Total fetched    Bytes     Over-fetch
-----------------------------------------------------------------------------------
   1         0                          20              320    128 KB         16×
  11       200                         220            3,520    1.41 MB       176×
  51     1,000                       1,020           16,320    6.53 MB       816×
 501    10,000                      10,020          160,320   64.13 MB     8,016×
5001   100,000                     100,020        1,600,320  640.13 MB    80,016×
```

**Keyset pagination — rows fetched to render 20:**

```
Any page, any depth: 16 × 20 = 320 rows, 128 KB, 16× over-fetch. Constant.

Page 501  :  160,320 rows → 320 rows    = 501× fewer rows
Page 5001 : 1,600,320 rows → 320 rows   = 5,001× fewer rows
Bytes at page 5001: 640 MB → 128 KB     = 5,001× less network
```

**Fetch-k-then-top-up, k = 10:**

```
Rows fetched            = 16 × 10 = 160   (half of 320)
Correctness condition   : the 20th merged row must sort AFTER every shard's
                          10th returned row; otherwise re-query that shard.
Contribution of one shard to the global top 20 ~ Binomial(20, 1/16), mean 1.25
P(one shard contributes > 10)                  ≈ 3 × 10⁻⁸
P(any of 16 shards does) ≈ 16 × 3 × 10⁻⁸       ≈ 5 × 10⁻⁷
⇒ ~1 request in 2,000,000 needs a second round trip.
Expected cost = 160 rows + (5×10⁻⁷ × extra round trip) ≈ 160 rows.
```

**Stated as a rule, not as "it depends":** use keyset pagination whenever the sort key can be made totally ordered by appending a unique tiebreaker. Use `k = max(limit/N × 3, 10)` with a top-up check **only** when the distribution across shards is measured to be near-uniform; if any shard holds a correlated cluster of the sort key, the top-up rate rises and you should fetch the full `limit` per shard.

### Part E — The celebrity problem, quantified

**Assumptions:** `reviews` on 16 shards; shard read ceiling ≈ 30,000 queries/second, write ceiling ≈ 5,000/second; total review traffic 117,000 reads/second; one `book_id` accounts for 41 % of it; that book also takes 1,200 writes/second.

```
Celebrity reads  = 0.41 × 117,000 = 47,970 ≈ 48,000 /s  → all on one shard
Remaining reads  = 69,000 /s spread over 16 shards = 4,313 /s each
Mean shard reads = 117,000 / 16 = 7,313 /s
Skew             = 48,000 / 7,313 = 6.6×
Hot shard util   = 48,000 / 30,000 = 160 % of ceiling  → SATURATED
Cold shard util  = 4,313 / 30,000 = 14 %
```

**Mitigation 1 — cache, 98 % hit rate:**

```
DB reads after cache = 48,000 × 0.02 = 960 /s
Hot shard util       = (960 + 4,313) / 30,000 = 17.6 %   → healthy
Reduction            = 50×
```

**Mitigation 2 — salting by 16, analysed before adopting:**

```
Writes per salt = 1,200 / 16 = 75 /s   → write util 75/5,000 = 1.5 %
                                          (but writes were only 24 % to begin with)
Reads           = 48,000 × 16 = 768,000 shard queries/s
Required shards = 768,000 / 30,000 = 25.6 shards' worth of READ capacity
Plus tail amplification at N=16: 14.85 % of book pages hit a slow shard.
⇒ REJECTED. Salting solved a non-problem and created a much larger one.
```

**Mitigation 3 — dedicated shard:**

```
Celebrity alone on shard 17: 48,000 / 30,000 = 160 % → still saturated!
⇒ A dedicated shard ISOLATES the blast radius; it does not add capacity.
  Combined with the cache: 960 /s on a dedicated shard = 3.2 % util, and a
  cache failure now degrades ONE book instead of the entire reviews keyspace.
```

**Mitigation 4 — access-pattern change:**

```
Counts/ratings from a stream-maintained counter, cached 10 s   : −60 % of reads
Top-50 reviews pre-rendered and served from the CDN (L30)      : −38 % of reads
Residual DB reads for the celebrity                            ≈ 50 /s
Total reduction from 48,000 /s                                 ≈ 960×
```

**The ordering that falls out of the arithmetic:** cache (50×) → access pattern (960× cumulative) → dedicated shard (isolation, 0× capacity) → salting (rejected, net negative for a read-hot key). For a write-hot key the first and last swap places, because a cache cannot absorb writes and salting is the only technique that divides write load. That is the concrete content of "it depends" here: it depends on the read/write ratio, on whether the hot key is predictable, and on whether the heat is transient or permanent.

## 10. Trade-offs

**Resharding by doubling**
- *Advantages:* exactly 50 % of keys move regardless of N; the arithmetic is trivial; no logical-shard layer needed; easy to reason about during an incident.
- *Disadvantages:* a 100 % capacity-cost step — you may pay for 16 idle machines for a year; it gives no option for a small, cheap resize.
- *Operational cost:* one large migration every doubling period, rather than small frequent ones.
- *Unnecessary when:* you already have logical shards, which let you add one machine at a time for ~6 % data movement.

**Resharding with logical shards / consistent hashing**
- *Advantages:* data movement falls from 94.1 % to 5.9 % on an N → N+1 resize; you can add capacity incrementally and match spend to demand; the map is small and inspectable.
- *Disadvantages:* a mapping layer to maintain, cache and version; the logical shard count is permanent and caps your physical shard count; it does **not** reduce *necessary* movement (a doubling still moves half the data).
- *Operational cost:* near zero if adopted on day one; a full reshard to retrofit if not.

**Dual writes via an outbox**
- *Advantages:* the destination shard is never on the request path, so migration problems cannot fail user requests; guarantees eventual convergence; makes rollback a flag flip.
- *Disadvantages:* a consumer to operate and monitor; lag to watch; every write path must be made idempotent; a longer migration calendar.
- *Operational cost:* moderate, and the single highest-value investment in the whole playbook.
- *Alternative that is almost always wrong:* synchronous dual writes, which double your request failure probability for the entire migration window.

**Caching a hot key**
- *Advantages:* fastest to deploy; 50× reduction at a realistic 98 % hit rate; usually uses infrastructure you already run.
- *Disadvantages:* reads only; introduces staleness; a cache expiry on a celebrity key is itself a stampede risk (Lesson 29); a cache-tier failure returns the full load to the shard instantly, with a cold buffer pool.
- *Unnecessary when:* the hot key is write-dominated, or correctness forbids stale reads (the ticketing example).

**Key salting**
- *Advantages:* the only technique that genuinely divides *write* load on a single key; works when the hot key is known in advance.
- *Disadvantages:* multiplies read fan-out by the salt count and inherits scatter-gather tail amplification; makes point reads impossible without a gather; complicates every query touching that key.
- *Operational cost:* high — the salt count becomes part of your data model and is painful to change later. Prefer adaptive salting driven by the directory.

**Scatter-gather**
- *Advantages:* works for any query shape without changing the data model; the fallback that keeps the product functional.
- *Disadvantages:* N× resource cost; tail latency becomes the max of N; correlated slowness makes it worse than the independence model predicts.
- *Unnecessary when:* the query could carry the shard key, or a secondary index table could turn one gather into two point lookups. Audit your fan-outs; most are accidental.

**Separate read model (search index / OLAP store)**
- *Advantages:* removes the hardest query shapes from the sharded OLTP store entirely; purpose-built engines are orders of magnitude better at them.
- *Disadvantages:* a second store to operate, secure, back up and keep in sync; eventual consistency with typical lag of seconds to minutes; duplicated data.
- *Unnecessary when:* the fan-out queries are low-volume admin or reporting queries that can simply run slowly.

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| Backfill run unthrottled | Site-wide slowdown or outage during the migration window; checkout timeouts; looks like a traffic spike with no traffic | Source-shard p99 and replication lag spike exactly when the backfill job starts; CPU and IO saturated by a non-user workload | Kill the job; resume from the durable checkpoint with an adaptive throttle bound to production p99, not a fixed sleep |
| Dual write succeeds on old shard, fails on new (best-effort mode) | Nothing — until cutover, when a subset of recent orders vanishes from the user's history | Chunk checksum mismatch; shadow-read mismatch rate above budget | Replay from the outbox; if there is no outbox, re-backfill the affected key ranges. This is the failure the outbox pattern exists to prevent |
| Stale shard map on one app server during cutover | A small percentage of writes land on the wrong shard and are silently invisible to readers on the right one | Map **epoch** rejection at the destination; the wrong-shard `CHECK` constraint firing; a per-shard write-rate anomaly | Reject stale-epoch writes at the shard; force map refresh; replay misplaced rows from the WAL and delete them from the wrong shard |
| Cutover performed with dual writes already disabled | A bad cutover cannot be rolled back; the old shard is now stale, so going back loses every write since cutover | Error-rate or p99 regression after the flip, with no safe way to revert | Roll **forward** only — the worst position in a migration. Prevention is the rule: keep dual writes on through cutover and for a full soak after |
| Celebrity key saturates one shard | Severe slowdown or errors for every user of that shard, not just the celebrity's page; neighbours are collateral damage | `max_shard_qps / mean_shard_qps` above 2.0; heavy-hitter sketch naming the key; per-shard p99 diverging before CPU alarms | Cache the key (minutes); move it to a dedicated shard via the directory (hours); change the access pattern (days) |
| Cache failure in front of a hot key | Instant return to full load on a shard with a **cold** buffer pool — frequently worse than the original incident | Cache hit-rate collapse followed within seconds by shard saturation | Single-flight on miss; a small in-process L1 cache as a second line; serve stale on cache-tier error; keep the dedicated shard as structural insurance |
| Salting applied to a read-hot key | Every read fans out; p99 on the busiest endpoint rises sharply; database tier runs out of capacity | Shard query count per request jumps by the salt factor; gather p50 approaches per-shard p96 | Remove salting; replace with cache plus dedicated shard. Removing a salt from live data is itself a migration — which is why you analyse before adopting |
| Deep offset pagination over shards | Admin or "load more" pages take seconds and progressively worsen; occasionally takes the cluster down | Rows-examined per query growing linearly with page number; memory pressure on the coordinator | Switch to keyset cursors; cap maximum page depth in the public API |
| One slow shard in a scatter-gather | 15 % of fan-out requests are slow at N = 16, with no single component looking unhealthy | Per-shard latency within the gather, not just end-to-end; the gather's p50 tracking one shard's p96 | Hedged requests to a second replica; partial results with a deadline; eject the slow shard's replica (Lesson 24) |
| `AVG` computed by averaging shard averages | Reports are subtly wrong; nobody notices for months; finance eventually disagrees with the dashboard | Reconciliation against a known-good total; the error only appears when shard row counts differ | Ship `(sum, count)` per shard and divide after combining; add a test that uses deliberately unbalanced shards |
| Cross-shard write assumed atomic | Money debited on one shard, credit never applied on another; user sees an inconsistent balance | Reconciliation job; customer complaints; invariant checks across shards | Compensating transaction (saga); long term, redesign the shard key so the invariant lives on one shard (**Phase 4, Topics 19–21**) |

## 12. Alternatives

| Approach | What it changes | Pros | Cons |
|---|---|---|---|
| **Logical shards (fixed bucket count)** | Decouples the hash from physical topology | ~6 % data movement on N → N+1; map is a printable list; trivial to debug | Bucket count is permanent; a mapping layer to maintain; does not reduce necessary movement |
| **Consistent hashing with virtual nodes** (Lesson 38) | Keys and nodes on a ring | ~K/N movement; handles dynamic membership and heterogeneous node capacity; spreads movement across all nodes | More complex to reason about; needs vnodes to avoid imbalance; still a real data migration |
| **Vendor-managed resharding** (DynamoDB, Vitess `Reshard`, Citus rebalancer, CockroachDB auto-split) | Someone else runs the playbook | Battle-tested; online; far lower risk than hand-rolled scripts | Less control over timing and throttling; vendor lock-in; opaque when it misbehaves |
| **Don't reshard — scale shards vertically** | Bigger machines, same shard count | No migration at all; days not weeks; fully reversible | Caps out; does not reduce per-shard working set relative to a *growing* dataset indefinitely |
| **Tiering / archival instead of resharding** | Move cold data out of the hot store | Often removes 60–80 % of rows and defers resharding by years; cheaper than any migration | Needs a retention policy and an archive query path; not applicable if all data is hot |
| **Dedicated shard for a hot entity** (directory) | Placement, not partitioning | One `UPDATE`; isolates blast radius; no data model change | Adds no capacity for the hot key itself; needs a directory layer; manual unless automated |
| **Salting** | Data model | The only technique that divides single-key *write* load | Multiplies read fan-out; hard to undo; complicates every query |
| **Cache in front** (Lessons 27–30) | Request path | Fastest and usually largest win for read-hot keys; infrastructure you already have | Reads only; staleness; stampede risk; cold-start cliff on cache failure |
| **Separate read model / CQRS** | Query path | Removes fan-out shapes from OLTP entirely; purpose-built engines | A second store; eventual consistency; duplicated data |
| **Admission control / queueing** (Phase 4, Topic 5) | Demand, not supply | Converts an unservable spike into a scheduled workload; the right answer to transient heat | Users wait; only acceptable where waiting is culturally normal (ticketing, launches) |

The last row generalises Lesson 23's closing observation. Every technique above is an attempt to make supply meet demand. Admission control changes the demand instead, and when the heat is transient — an on-sale, a launch, a push notification — it is frequently the only approach whose cost does not persist after the spike ends.

## 13. When to use it

1. **Reshard when a measured capacity dimension will breach its planning ceiling inside your migration lead time.** Given a 4-week elapsed-time project (§9 Part B), start when you have 3–6 months of headroom, not 3 weeks. Resharding under duress is how migrations skip verification.
2. **Reshard when the per-shard working set is about to exceed RAM**, which is usually the binding constraint before write TPS is — BookNook hit RAM at 8 months while writes still had years of headroom.
3. **Use logical shards or consistent hashing from day one**, before you ever need to resize. The cost is one indirection; the saving is 15.7× less data movement on your first incremental resize.
4. **Use dual writes with an outbox for any migration of data you cannot afford to lose** — which is all of it. The outbox is what makes rollback a flag flip.
5. **Cache a hot key as the first mitigation whenever the key is read-dominated.** It is the fastest to deploy and gives the largest single reduction (50× at a 98 % hit rate).
6. **Use salting when a single key is write-dominated and the hot key is known in advance** — inventory counters, like/view counters, on-sale events.
7. **Use a dedicated shard whenever one entity's load threatens its neighbours**, even if the dedicated shard does not add capacity: isolation of blast radius is valuable on its own, and it is a one-row change under directory sharding.
8. **Use scatter-gather when the query genuinely cannot carry the shard key and volume is low** — admin tools, support lookups, back-office reporting. It is a legitimate answer at low QPS.
9. **Use keyset pagination for every paginated list over a sharded dataset**, without exception. There is no page depth at which offset pagination is the better choice.
10. **Use a separate read model when a fan-out query shape is both high-volume and permanent** — site search, analytics, recommendations.

## 14. When *not* to use it

1. **Do not reshard to fix a hot key.** Resharding redistributes *keys*; a celebrity is *one* key and will simply land on a different shard. The fix is caching, isolation or an access-pattern change. This is the most common wasted migration in the industry.
2. **Do not reshard for transient load.** The ticketing on-sale is hot for four hours; a migration takes four weeks. Use isolation and admission control.
3. **Do not use synchronous dual writes.** They double your request failure probability for the whole migration window, for no correctness benefit over an outbox.
4. **Do not cut over before verification passes**, and do not treat "mismatch rate is low" as passing. Every observed mismatch needs a known, benign explanation.
5. **Do not disable dual writes at cutover.** That converts an instant rollback into a forward-only migration — the worst place to be during an incident.
6. **Do not salt a read-hot key.** §9 Part E shows this turns 48,000 reads/second into 768,000 and adds 14.9 % tail amplification to your busiest endpoint.
7. **Do not use offset pagination across shards.** At page 501 it moves 64 MB to render 20 rows, and the cost grows without bound.
8. **Do not build cross-shard transactions if a shard-key change could put the invariant on one shard.** Two-phase commit makes availability multiplicative and blocks on coordinator failure. Redesign first; distribute only if you must (**Phase 4, Topics 19–21**).
9. **Do not scatter-gather a high-volume user-facing endpoint.** At 20,000 requests/second over 16 shards that is 320,000 shard queries/second and a p50 equal to each shard's p96. Build a read model instead.
10. **Do not reshard when archival would do.** If 70 % of rows are older than your query horizon, tiering them out is days of work versus weeks, and it is reversible.

## 15. Common mistakes

**Beginner mistakes**
- Assuming adding a shard is a configuration change. With raw `hash(key) mod N`, going from 16 to 17 shards relocates 94.1 % of your data.
- Quoting the pure copy time (1.39 hours) as the migration time (≈ 4 weeks). Verification and soak dominate.
- Using `OFFSET` for pagination across shards, then being surprised that page 500 takes seconds.
- Believing hash sharding prevents hot spots. It prevents hot *ranges*; a single hot key is untouched.
- Reaching for salting first, because it sounds like the "real" solution, without checking the read/write ratio.
- Averaging per-shard averages to compute a global average. Correct only if every shard has an identical row count, which is never guaranteed.
- Running a backfill with a fixed `sleep()` instead of a throttle bound to production latency, so it is harmless at 03:00 and an outage at 20:00.
- Not making dual writes and backfill idempotent, so a replay or a race overwrites newer data with older data.
- Treating `COUNT(DISTINCT ...)` as decomposable across shards. It is not; use HyperLogLog or ship the keys.

**Experienced-engineer mistakes**
- Omitting the map **epoch**, so a single app server running stale code writes silently to the wrong shard. These rows are usually found weeks later, by a customer.
- Turning off dual writes at cutover to "clean up", destroying the rollback path at the exact moment it is most needed.
- Backfilling with `ON CONFLICT DO UPDATE` without a `WHERE excluded.updated_at > target.updated_at` guard, so the backfill overwrites live dual-written rows with stale copies.
- Verifying with row counts alone. Counts match while contents diverge — the most common way a migration passes verification and still loses data.
- Skipping shadow reads, so routing and query-plan bugs survive until cutover; checksums validate the data, not the query path.
- Choosing a logical shard count of 64 "because we'll never have more than 64 shards", permanently capping the cluster.
- Letting a backfill build secondary indexes during the load instead of after it, paying a 3–10× penalty.
- Assuming independent shard latencies when computing fan-out tail risk. Shards share racks, networks and dependencies; correlated slowness makes the real numbers worse than `1 − (1−p)^N`.
- Adding hedged requests without making reads idempotent, turning a latency optimisation into a duplicate-side-effect bug.
- Designing a saga before checking whether a different shard key would have kept the invariant on one shard. Distributed transactions are a last resort, not a design pattern to reach for.
- Automating hot-key detection but not hot-key *remediation*, so the sketch names the key at 03:00 and nobody acts until 09:00.
- Treating a resharding project as an engineering task with no product involvement, then discovering mid-cutover that a reporting feature depended on a cross-shard query that no longer works.

## 16. Interview perspective

This topic is where senior candidates separate themselves, because it is about what happens *after* the clean whiteboard design meets production.

**How to use it:**

1. **Volunteer the resize problem before being asked.** *"I'd hash onto 1,024 logical shards rather than directly onto machines, because `hash(k) % N` moves 94 % of rows when you go from 16 to 17 shards, versus about 6 % with a bucket map."* One sentence, two concrete numbers, and it pre-empts the obvious follow-up.
2. **Name the migration playbook as a sequence.** Dual writes → backfill → verify → staged cutover → soak → decommission, with *rollback is a flag flip, never a reverse migration*. Saying that last clause is a strong signal you have actually done one.
3. **Separate the copy time from the project time.** *"The data movement is about 90 minutes; the project is about four weeks, because verification and soak dominate."* Interviewers remember this because most candidates quote only the first number.
4. **Distinguish hot ranges from hot keys immediately.** *"Hashing fixes uneven ranges. It does nothing for one celebrity key — that needs caching, isolation, or an access-pattern change."*
5. **Pick the hot-key mitigation from the read/write ratio, out loud.** *"It's read-hot at 48,000 reads and 1,200 writes per second, so caching first — salting would cut writes I don't care about and multiply reads by 16."* Explaining a *rejected* option is the strongest move available in this topic.
6. **Use the fan-out formula by name and number.** *"With 16 shards and a 1 % per-shard slow rate, about 15 % of gathers are slow — the gather's median is roughly each shard's p96."* Then offer hedged requests: 5 % more load for a ~93× reduction in slow gathers.
7. **Fix pagination unprompted.** *"I'd use keyset cursors on `(created_at, order_id)`, not offsets — at page 500 an offset query pulls 160,000 rows across 16 shards to show 20."*
8. **Push back on distributed transactions.** *"I'd try to choose a shard key that keeps the invariant on one shard. If I genuinely can't, it's a saga with compensations, and I'd accept visible intermediate states."*
9. **Say what you'd measure.** Shard skew ratio, heavy-hitter sketch, per-shard latency within the gather, backfill throttle signal, shadow-read mismatch rate. Naming metrics is what distinguishes an operator from a reader.

**Red flags interviewers watch for:**
- Describing adding a shard as a configuration change, with no awareness of data movement.
- No migration plan beyond "we'd copy the data", with no verification, no rollback, and no mention of dual writes.
- Proposing resharding as the fix for a celebrity key.
- Reaching for salting without first asking whether the key is read- or write-hot.
- Claiming scatter-gather is "fine, it's parallel" — missing that latency is the max of N, not the average.
- Using offset pagination in a sharded design.
- Assuming cross-shard transactions are ACID, or naming 2PC without mentioning that it blocks on coordinator failure and makes availability multiplicative.
- Quoting the copy time as the project duration.
- Saying "it depends" about hot-key mitigation without naming the three things it depends on: read/write ratio, predictability, and whether the heat is transient or permanent.
- No mention of how a hot key would be *detected* in the first place.

## 17. Knowledge check

Write your answers down before continuing.

**Conceptual**

1. Derive, from first principles, why moving from N shards to N+1 under `hash(key) mod N` relocates `N/(N+1)` of all keys, and why moving from N to 2N relocates exactly one half regardless of N. Then explain precisely what logical shards change about this and — importantly — what they do **not** change. Your answer must state the one resize case where logical shards provide no benefit at all, and why.

2. A team reports: "One shard is at 95 % CPU, the other fifteen are at 12 %, and we've confirmed key distribution is within 0.2 % of uniform. We're planning to reshard from 16 to 32 shards next month." Explain why this plan will almost certainly fail to fix the problem, what the team has actually diagnosed versus what they have assumed, and what you would do in the next hour, the next day, and the next week instead. For each action, state what it does and does not solve.

3. Explain why a scatter-gather query's *median* latency approaches each individual shard's *p96* at 16 shards, deriving the `q^(1/N)` relationship. Then explain why the real-world numbers are typically worse than this model predicts, and describe two mitigations — one that reduces the number of shards contacted and one that reduces the impact of a slow shard — stating the cost of each.

**Practical scenario**

4. BookNook is six days into a 16 → 32 shard migration. Dual writes have been on for six days, the backfill completed yesterday, and chunk checksums all match. A shadow-read comparison shows a mismatch rate of 1 in 40,000 — above the 1-in-1,000,000 budget. The mismatches all involve orders whose `status` was changed within the last two seconds before the read. The migration lead proposes to proceed with cutover, arguing the mismatches are "just replication lag" and will disappear once the new shards are authoritative.
   Evaluate this argument. State whether the explanation is plausible and what evidence would confirm or refute it. Identify at least two *other* causes that produce exactly this signature, and for each, explain the consequence of cutting over while it is unresolved. Then describe the specific sequence of checks you would require before approving cutover, and the one condition under which you would abort the migration entirely rather than fix and retry.

**Calculation**

5. A video platform, Reelbox, shards `view_events` on `video_id` across 24 shards. Each shard sustains approximately 40,000 reads/second and 8,000 writes/second. Total traffic is 260,000 view-event writes/second and 95,000 analytics reads/second. A single video goes viral and takes 33 % of all writes and 28 % of all reads. Row size is 250 bytes; the table holds 9.0 TB.
   State any assumptions you add.
   (a) Compute the viral video's write rate and read rate, the mean per-shard write and read rates, the skew factor for each, and the hot shard's utilisation against both ceilings. Which dimension is actually saturated?
   (b) Reelbox considers salting the viral `video_id` across S shards. Find the minimum S that brings the hot shard's **write** utilisation below 70 %. Then compute the resulting **read** fan-out load in shard queries/second, and determine whether the cluster can serve it. State your verdict on salting and justify it with the numbers.
   (c) Using `P(slow) = 1 − (1 − p)^N` with a per-shard slow probability of 0.8 %, compute the probability that an analytics query fanning out to all 24 shards is slow, and the per-shard quantile that corresponds to the gather's median. Then compute both figures again for the salted read path from (b), and state what this adds to your verdict.
   (d) Independently, Reelbox must grow from 24 to 25 shards. Compute the data moved under raw `hash % N`, under 4,096 logical shards, and under a doubling to 48 shards. Assuming an aggregate sustainable copy rate of 650 MB/s and a realistic elapsed-to-copy-time ratio of 300×, estimate the calendar duration of each option, and recommend one with a one-sentence justification.
   Show every step and state every assumption you add.

Reply with your answers and I'll assess them, correct anything shaky, and give model answers before Lesson 38.

---

**Progress checklist**

Phase 1 — Internet and System-Design Foundations: all 17 topics `[x]` complete (Lessons 1–17).

Phase 2 — Scaling
- [x] 1–17. Vertical scaling through replication lag and read-after-write consistency
- [x] 18. Database partitioning and sharding
- [x] 19. Range, hash, directory, geographic sharding
- [x] 20. Resharding, hot partitions, cross-shard queries
- [ ] 21. Consistent hashing and virtual nodes ← next
- [ ] 22. Autoscaling and multi-region architecture

Phases 3–5: not started.

**NEXT LESSON: Phase 2, Topic 21 — Consistent hashing and virtual nodes**


# Lesson 38 — Consistent Hashing and Virtual Nodes

*Phase 2, Topic 21*

## 1. Simple definition

**Consistent hashing** is a way of assigning keys to servers such that **adding or removing a server moves only a small fraction of the keys** — roughly `1/N` of them — instead of almost all of them.

It works by mapping both keys and servers onto the same circular number line (the **hash ring**) and giving each key to the first server found by walking clockwise.

Terms defined before use:

- **Key**: the thing being placed. For BookNook this is a cache key like `book:9780143127741`, a shard key like `user_id=88214`, or a session ID.
- **Node**: one physical destination — a Memcached server, a Cassandra instance, a CDN edge server, a backend behind a Layer 4 load balancer.
- **Hash space**: the full range of values a hash function can produce, e.g. `0 … 2³²−1`. Treated as a circle by declaring that the value after `2³²−1` is `0`.
- **Ring position / token**: a point in the hash space that a node occupies.
- **Virtual node (vnode)**: one of *many* ring positions owned by the *same* physical node. A node with 100 vnodes appears at 100 places on the ring.
- **Key movement / remapping**: a key whose owning node changes because the set of nodes changed. Movement is the cost we are minimising.
- **Membership change**: a node joining or leaving (scale-out, scale-in, crash, deploy, replacement).

Lesson 23 introduced the idea in one paragraph as a load-balancing strategy. This lesson is the full treatment: the mathematics, the uneven-distribution problem, virtual nodes, weighting, bounded load, and the two main alternatives.

## 2. Why it exists

Lesson 37 quantified the resharding problem precisely. If you place keys with modulo arithmetic:

```
node_index = hash(key) % N
```

then growing the fleet from `N` to `N+1` nodes remaps almost everything. The derivation:

```
Let h = hash(key), uniformly distributed.
Old owner = h mod N
New owner = h mod (N+1)

A key KEEPS its owner only when  h mod N  ==  h mod (N+1).

By the Chinese Remainder Theorem, h mod N(N+1) determines both values,
and the pairs (a, b) with a ∈ [0, N), b ∈ [0, N+1) are in bijection with
the N(N+1) residues. We need a == b, which is only possible when b < N,
giving exactly N satisfying residues out of N(N+1).

P(key keeps its owner) = N / (N(N+1)) = 1/(N+1)
P(key MOVES)           = 1 − 1/(N+1) = N/(N+1)
```

That is the Lesson 37 number. Concretely:

| Nodes `N` → `N+1` | Fraction of keys that move under `% N` |
|---|---|
| 4 → 5 | 4/5 = **80.0 %** |
| 10 → 11 | 10/11 = **90.9 %** |
| 50 → 51 | 50/51 = **98.0 %** |
| 100 → 101 | 100/101 = **99.0 %** |

**The larger your fleet, the worse modulo gets.** This is the opposite of what you want from a scaling technique.

What does "a key moved" actually cost?

- **For a cache** (Lesson 26–30): a moved key is a guaranteed miss. Moving 90.9 % of keys means the cache hit rate collapses from, say, 95 % to about 9 % in an instant, and every one of those misses becomes a database query. Lesson 30's stampede problem arrives fleet-wide, simultaneously, *as a direct result of adding capacity*. Scaling out takes the site down.
- **For a shard** (Lesson 35–37): a moved key means physically copying a row from one database to another, with a cut-over window where writes must be blocked or dual-written. Moving 90.9 % of the data is a full-fleet data migration.
- **For stateful routing**: a moved key means a WebSocket room, a user session, or an in-memory rate-limit counter is now on the wrong server.

Consistent hashing exists to change that `N/(N+1)` into `1/(N+1)` — a factor of exactly `N` improvement. At N = 10, ten times less movement. At N = 100, a hundred times less.

**The precise thing it buys you: it decouples the identity of a key's owner from the total count of nodes.** Under modulo, every key's answer depends on `N`, so every key's answer changes when `N` changes. Under consistent hashing, a key's answer depends only on *which node happens to sit next to it on the ring*, which is unaffected by changes elsewhere on the ring.

## 3. Real-world analogy

**A circular corridor of numbered offices, and a mail-sorting rule.**

Imagine a circular corridor numbered 0 to 999 around a single floor. Four departments have desks placed at arbitrary door numbers: Accounts at 42, Legal at 310, Sales at 625, HR at 880.

The mail rule is: *drop each letter at the door number matching its reference number; the letter is then handled by the next department you reach walking clockwise.*

- A letter with reference 500 lands at door 500, walks clockwise, and reaches Sales at 625.
- A letter with reference 950 lands at 950, walks clockwise past 999, wraps to 0, and reaches Accounts at 42.

Now **a new department, Support, moves in at door 700**. Which letters change hands? Only those with references between 626 and 700 — letters that previously walked past 700 to reach HR at 880. Every other letter in the building is completely unaffected. Nobody had to re-sort the whole mailroom.

Compare with the modulo rule — *"reference number mod 4 tells you the department"*. Add a fifth department and you must re-sort **four out of five letters in the entire building**.

And the virtual-node idea: rather than each department having one desk, each department has **100 desks scattered around the corridor**. Now the corridor is finely subdivided, every department gets a statistically even share of the floor, and when Support moves in with its 100 desks it takes a small slice from *each* of the four existing departments rather than one huge slice from HR alone.

**Where the analogy breaks down:**

| Circular corridor | Consistent hash ring |
|---|---|
| Door numbers are chosen deliberately by a facilities manager | Ring positions are the output of a hash function — effectively random, and *nobody chooses them*. This is exactly why distribution is uneven with few nodes. |
| 1,000 doors | 2³² or 2¹²⁸ positions, so collisions are rare but must still be handled in code |
| A letter physically walks and can see where it is going | The client does a binary search over a sorted array of positions it holds in memory; there is no "walking", and the client must already know the full membership list |
| Everyone in the building agrees on where the desks are | **Different clients can have different views of the ring.** A client with a stale membership list routes to the wrong node and silently gets cache misses or, worse, writes to the wrong shard. Membership propagation is the hard operational problem, and the analogy hides it entirely. |
| Departments have the same amount of staff | Nodes differ in RAM, CPU and disk, which is why weighting exists |
| A letter is a letter | Keys have wildly different traffic. A single celebrity key (Lesson 30's hot key) can overwhelm one node no matter how perfectly the ring is balanced. Consistent hashing balances **key count**, not **request volume**. |

That last row is the one to carry forward: **consistent hashing solves key *placement*, not key *popularity*.** Hot keys are a separate problem with separate solutions.

## 4. Detailed technical explanation

### 4.1 The ring, step by step

1. **Choose a hash function and a hash space.** Typically MD5 or SHA-1 truncated to 32 bits (`0 … 4,294,967,295`), or MurmurHash3 / xxHash for speed. Cassandra uses Murmur3 over a signed 64-bit space. Cryptographic strength is *not* required; uniformity and speed are. Note: `hash()` in Python and `Object.hashCode()` in Java are **not** suitable — Python's string hash is randomised per process by default (`PYTHONHASHSEED`), so two clients would compute different rings.
2. **Place each node.** For node `A`, compute `pos_A = hash("A")`. That number is A's position on the ring.
3. **Place each key.** For key `k`, compute `pos_k = hash(k)`.
4. **Assign.** Walk clockwise (in increasing hash value) from `pos_k` to the first node position ≥ `pos_k`. If you run off the end of the space, wrap to position 0 and continue. That node owns the key.
5. **Implement the walk as a binary search.** Keep node positions in a sorted array; `bisect(sorted_positions, pos_k)` gives the index in `O(log M)` where `M` is the number of ring positions. The "walk" is never an actual scan.

Each node therefore owns the **arc** of hash space between its own position and the position of the node counter-clockwise from it (its *predecessor*). The size of that arc is the node's share of the keyspace.

### 4.2 Why only ~K/N keys move — derived

Let `K` be the total number of keys and `N` the number of nodes, with positions uniformly distributed over the ring.

**Adding a node.** The new node `E` lands at a uniformly random position. It becomes the owner of the arc from its predecessor up to itself — an arc that previously belonged entirely to `E`'s clockwise successor. Afterwards there are `N+1` nodes dividing the same ring, so by symmetry each owns an expected `1/(N+1)` of the space:

```
E(keys owned by the new node) = K / (N+1)
Those keys, and ONLY those keys, changed owner.
⇒ expected fraction moved = 1/(N+1) ≈ 1/N

Compare with modulo:  N/(N+1)
Ratio of movement:    (N/(N+1)) / (1/(N+1)) = N
```

**Removing a node.** Node `C` leaves. Its arc is inherited wholly by its clockwise successor. The keys that move are exactly those `C` owned:

```
E(keys moved) = K / N
```

**The crucial structural property**, which modulo lacks: when `E` joins, keys owned by `A`, `B` and `D` are *provably untouched*, because their arcs did not change. Movement is not merely small on average — it is **localised to one boundary**, which is what makes online migration tractable.

**Caveat on the "only K/N" claim.** It is `K/N` *of the key space*, assuming keys hash uniformly. If 40 % of your traffic hits one key, the ring says nothing useful about traffic movement. Always read `K/N` as "fraction of distinct keys", never "fraction of load".

### 4.3 The uneven-distribution problem with few nodes

Here is the simplification that most explanations stop at, and why it is wrong in production.

With `N` node positions placed **uniformly at random** on a circle, the `N` arc lengths are *not* `1/N` each. They follow a Dirichlet(1,…,1) distribution — equivalently, they behave like `N` independent exponential random variables normalised to sum to 1.

For a single arc `S`:

```
E[S]   = 1/N
Var[S] ≈ 1/N²     (exactly  (N−1)/(N²(N+1)) )
SD[S]  ≈ 1/N

Coefficient of variation  CV = SD/mean = 1.0  =  100 %
```

**The standard deviation of a node's load equals its mean.** That is the argument. It is as bad as random distribution ever gets. Some node will own roughly twice its fair share and some node will own almost nothing, and no amount of adding more keys fixes it, because the imbalance comes from the *positions*, not the keys.

The expected **largest** arc among `N` uniform points is approximately `ln(N)/N`:

```
N = 4   : largest arc ≈ ln(4)/4   = 0.347  →  34.7 % of keys on one node (ideal 25 %)  = 1.39×
N = 10  : largest arc ≈ ln(10)/10 = 0.230  →  23.0 % of keys on one node (ideal 10 %)  = 2.30×
N = 100 : largest arc ≈ ln(100)/100 = 0.046 → 4.6 % of keys on one node (ideal 1 %)    = 4.60×
```

(Approximate; the `ln N / N` result is the asymptotic expectation of the maximum spacing.)

So the naive ring gives you a node carrying **2–4× its fair share**. For a cache that means that node's eviction rate is 2–4× higher and its hit rate correspondingly worse; for a shard it means one database runs out of disk long before the others.

There is a second, nastier consequence. When node `C` dies, **100 % of its load lands on one single successor**, instantly doubling that node's traffic. If the fleet was running at 60 % utilisation, the successor jumps to 120 % and fails too — then *its* load lands on the next node. This is a **cascading ring failure**, and it is the strongest practical argument for virtual nodes.

### 4.4 Virtual nodes

**Definition:** a virtual node (vnode, or *token* in Cassandra terminology) is one ring position belonging to a physical node. Give each physical node `V` positions instead of one, generated as `hash("nodename#0")`, `hash("nodename#1")`, … `hash("nodename#V−1")`.

The ring now has `M = N × V` positions. A physical node's share is the **sum of `V` independent arcs**:

```
Share_i  = sum of V arcs out of M total arcs
E[Share] = V/M = V/(N·V) = 1/N          (unchanged — still fair on average)
Var      ≈ V × (1/M²) = V/(N·V)² = 1/(N²·V)
SD       ≈ 1/(N·√V)
CV       = SD / mean = (1/(N√V)) / (1/N) = 1/√V
```

**`CV = 1/√V`.** That single formula is the whole design rule for choosing `V`. Averaging `V` independent arcs reduces the relative spread by `√V` — the standard error of the mean, applied to load balancing.

Load imbalance versus virtual-node count, for a 10-node fleet and 1,000,000 keys (ideal share = 10 % = 100,000 keys):

| vnodes per node `V` | Ring positions `M` | CV = 1/√V | Typical heaviest node | Peak / mean | Lookup cost `log₂ M` | Client ring memory (N=10) |
|---|---|---|---|---|---|---|
| **1** | 10 | 100 % | ≈ 230,000 keys (23 %) | **≈ 2.3×** | 3.3 compares | ~0.3 KB |
| **10** | 100 | 31.6 % | ≈ 150,000 keys (15 %) | **≈ 1.5×** | 6.6 compares | ~3 KB |
| **100** | 1,000 | 10.0 % | ≈ 115,000 keys (11.5 %) | **≈ 1.15×** | 10.0 compares | ~30 KB |
| **256** | 2,560 | 6.25 % | ≈ 110,000 keys (11 %) | **≈ 1.10×** | 11.3 compares | ~75 KB |

*Numbers are approximate and typical: the "heaviest node" column uses `peak ≈ mean × (1 + z·CV)` with `z ≈ 1.5` (the expected maximum of 10 standard normal samples), except for `V = 1`, where the arc distribution is exponential rather than normal and the asymptotic `ln(N)/N` result is used instead. Simulate for your own `N`; do not treat these as exact.*

Read the table as a diminishing-returns curve:

- `1 → 10` vnodes: imbalance 2.3× → 1.5×. **Enormous win, nearly free.**
- `10 → 100`: 1.5× → 1.15×. Solid win, still cheap.
- `100 → 256`: 1.15× → 1.10×. Marginal.
- Beyond 256: you are paying linear memory and `O(M log M)` ring-rebuild time for a few tenths of a percent.

**Industry defaults, for calibration (typical values):** libketama (the de-facto Memcached client ring) uses **160 points per server**. Cassandra's original default was **256 tokens per node**; modern Cassandra defaults to **16** tokens combined with a smarter allocation algorithm that places tokens deliberately rather than randomly — which beats random placement at far lower `V`. Dynamo-style systems commonly use **100–256**.

**The second benefit, which matters more than balance:** when a node dies, its `V` vnodes are scattered across the ring, so its load is inherited by **many different successors** rather than one. With `N = 10` and `V = 100`, a failed node's 10 % of traffic spreads across the surviving 9 nodes at roughly 1.1 % each. Nobody doubles. The cascading ring failure disappears.

**Costs of virtual nodes, stated honestly:**

- Memory and ring-build time grow linearly in `V`: a 1,000-node fleet at `V = 256` is 256,000 ring entries per client, roughly 6 MB plus structure overhead, rebuilt on every membership change.
- Lookup is `O(log M)`, so cost grows only logarithmically — negligible.
- For **replicated** systems (Dynamo, Cassandra), more vnodes means each node shares data with more peers. A node's replica set grows, which **increases the probability that some combination of simultaneous failures loses a replica set entirely**. This is a genuine and underappreciated reliability trade-off, and it is part of why Cassandra lowered its default from 256 to 16.
- Repair/streaming operations run per token range, so very high `V` multiplies operational overhead.

### 4.5 Weighted nodes for heterogeneous hardware

Hardware is rarely uniform. BookNook's cache tier might be six `r6g.large` (16 GB) nodes and two `r6g.2xlarge` (64 GB) nodes. An even key split would waste 75 % of the big nodes.

**The mechanism is trivially simple: vnode count *is* the weight.**

```
vnodes(node) = BASE_VNODES × capacity_weight(node)

r6g.large   (16 GB) → weight 1.0 → 100 vnodes
r6g.2xlarge (64 GB) → weight 4.0 → 400 vnodes
```

A node with 4× the ring positions owns ~4× the arc, hence ~4× the keys. No separate weighting logic is needed anywhere in the lookup path.

Two consequences that follow directly:

1. **Weight resolution is `1/BASE_VNODES`.** With `BASE_VNODES = 100`, you can express weights to 1 % granularity. With `V = 1` you cannot express weights at all. **Weighting is a second, independent reason to use virtual nodes.**
2. Use capacity ratios derived from the binding resource. For a cache that is **RAM**; for a shard it is usually **disk** or **IOPS**; for an L4 load balancer it is **CPU or NIC throughput**. Weighting a cache by vCPU count is a classic mistake.

Stale weights are a real operational hazard — see Lesson 23's identical warning about load-balancer weights. Derive weights from instance metadata automatically rather than hardcoding them.

### 4.6 Bounded-load consistent hashing

Virtual nodes fix *statistical* imbalance in key counts. They do nothing about **traffic** imbalance from hot keys (Lesson 30) or from a sudden burst to one arc.

**Consistent hashing with bounded loads** (Mirrokni, Thorup and Zadimoghaddam, Google, 2016) adds a hard cap:

```
capacity(node) = ceil( (1 + ε) × average_load )

where average_load = total_in_flight_requests / N
      ε            = the slack parameter, typically 0.1 – 0.5
```

Lookup becomes: hash the key, walk clockwise; **if the owner is already at capacity, keep walking** to the next node with spare capacity. The result:

- **No node ever exceeds `(1+ε)` times the average.** This is a *guarantee*, not a statistical tendency.
- The scheme remains consistent: when membership changes, the expected movement is still `O(K/N)`, with a modest multiplier that grows as `ε` shrinks.
- A hot key no longer pins a single node; its overflow spills to neighbours.

The price:

- **Affinity becomes conditional.** The same key can land on different nodes at different moments, depending on instantaneous load. For a *cache* that means duplicate entries and a lower hit rate. For a *shard holding the authoritative copy of data* it is simply **wrong** — you cannot "spill" a user's row to a neighbouring database. **Bounded load is appropriate for stateless-ish routing and caches; it is not appropriate for authoritative data placement.**
- You now need a real-time load signal, which means the router must track in-flight requests — reintroducing the partial-view problem from Lesson 23 when there are many routers.
- Small `ε` (say 0.05) causes constant spilling and destroys locality; large `ε` (say 1.0) barely constrains anything. `ε ≈ 0.25` (a 125 % cap) is a typical starting point.

In production you meet this as HAProxy's `hash-balance-factor 125` and Envoy's `hash_balance_factor` on the `RING_HASH` and `MAGLEV` policies.

### 4.7 Rendezvous (highest random weight) hashing

**Rendezvous hashing** (Thaler and Ravishankar, 1996 — actually published a year *before* the consistent-hashing paper) solves the same problem with no ring at all:

```
owner(key) = argmax over all nodes n of  hash(key, n)
```

For each key, hash it jointly with every node name, and pick whichever node produces the largest value. That is the entire algorithm.

Its properties, compared point by point with a vnode ring:

- **Distribution is exactly uniform in expectation, with no tuning.** Every node is equally likely to win any key, so the CV is that of a binomial distribution: `CV = √((1−1/N)/K) ≈ 1/√K` where `K` is the key count. With a million keys this is ~0.1 %, far better than a 256-vnode ring, **and there is no `V` to choose**.
- **Minimal disruption is exact, not statistical.** When a node is removed, only keys that node won move, and they redistribute evenly across all survivors. When a node is added, a key moves only if the new node outscores the previous winner — probability exactly `1/(N+1)`.
- **Ranked replicas come free.** Sort the scores and you have an ordered preference list: 1st, 2nd, 3rd choice. This is excellent for replica placement and failover ordering. On a ring you must walk clockwise skipping duplicate physical nodes — more code, and awkward with vnodes.
- **No ring state.** Nothing to build, nothing to rebuild on membership change, no memory proportional to `N × V`.
- **Weighting works** via `score = weight / −ln(u)` where `u` is the hash mapped to `(0,1)` — "weighted rendezvous hashing". Mathematically clean and exact, unlike vnode-count weighting which is granular.
- **The cost: lookup is `O(N)` hashes**, versus `O(log(N·V))` comparisons on a ring. This is the only real downside, and it is decisive at scale.

**Exactly when rendezvous is preferable:**

1. **`N` is small — roughly under 100 nodes** — so `O(N)` hashing (about 100 × 20 ns ≈ 2 µs) is irrelevant next to a network round trip of 500 µs.
2. **You need ranked replicas** (primary, secondary, tertiary) — e.g. choosing 3 storage nodes for an object.
3. **You need precise weights** rather than vnode-granular approximations.
4. **Membership changes frequently** and you do not want to rebuild a 256,000-entry ring each time.
5. **You want fewer moving parts**: it is five lines of code with no tuning parameter, so it cannot be misconfigured.

**When a ring wins:** very large `N` (thousands), or very high lookup rates where `O(N)` per lookup becomes the bottleneck — a Layer 4 load balancer making millions of decisions per second cannot afford to hash against 5,000 node names per packet.

### 4.8 Jump consistent hash

**Jump consistent hash** (Lamping and Veach, Google, 2014) maps a key to a bucket number in `[0, num_buckets)`:

```
jump_hash(key, num_buckets) → integer in [0, num_buckets)
```

Properties:

- **Zero memory.** No ring, no node list, no state whatsoever.
- **Perfectly balanced** — each bucket gets exactly `1/num_buckets` of keys in expectation, with no vnodes and no tuning.
- **`O(ln N)` time**, about 5 iterations of a tiny loop for 100 buckets. Faster than both alternatives.
- **Optimal movement**: going from `n` to `n+1` buckets moves exactly `1/(n+1)` of keys, all of them into the new bucket.

**The hard limitation, and it is severe:** buckets are *numbered* `0 … n−1` and you can only grow or shrink at the **top of the range**. You can go from 10 buckets to 11; you **cannot** remove bucket 4 while keeping 0–3 and 5–10. There is no concept of a named server that might die.

So jump hash is right when your destinations are **logical shard numbers** that you control (Lesson 36's hash sharding), and you map logical shards onto physical servers through a separate lookup table (Lesson 36's directory-based sharding). It is wrong when the thing you hash to is a physical server that can fail independently.

### 4.9 Where this is used in the real world

| System | Use | Notable details |
|---|---|---|
| **Amazon Dynamo** (2007 paper) | Partitioning *and* replication of key-value data | Originated the vnode terminology in this space; a key is stored on the `N` distinct physical nodes found walking clockwise — the **preference list** |
| **Apache Cassandra / ScyllaDB** | Token-ring partitioning | `num_tokens` = vnodes per node; Murmur3 over signed 64-bit space; default moved 256 → 16 with an improved allocation algorithm |
| **Riak, Voldemort** | Dynamo-style partitioning | Fixed ring of 2ⁿ partitions mapped onto nodes — a hybrid of ring and directory |
| **Memcached clients** (libketama, spymemcached, pylibmc) | Client-side shard selection | **160 points per server**, MD5-based; the server knows nothing — the ring lives entirely in each client, which is why stale client config is the classic failure |
| **CDN request routing** (Lesson 31) | Mapping a URL to a cache server inside a POP | Descends directly from Karger et al., 1997, "Consistent Hashing and Random Trees", written for exactly this problem |
| **Layer 4 load balancers** (Lesson 22) | Mapping a flow (5-tuple) to a backend | Google's **Maglev** uses a related lookup-table scheme optimised for near-perfect balance plus minimal disruption; Envoy exposes `RING_HASH` and `MAGLEV` |
| **nginx** | `hash $key consistent;` (seen in Lesson 23's config) | ketama-compatible ring |
| **Kafka, Elasticsearch** | *Not* consistent hashing | Fixed partition/shard counts with explicit reassignment — a deliberate alternative covered in Section 12 |

## 5. Request / data flow

A BookNook product page is requested and the application needs `book:9780143127741` from the distributed cache tier (Lesson 27), which is 10 Memcached nodes with 100 vnodes each, addressed by a client-side consistent hash ring.

1. `GET /books/9780143127741` arrives at an app server (routed there by the load balancer, Lesson 23).
2. The app builds the cache key string `book:9780143127741`.
3. The cache client checks its **ring generation**. It holds a membership list received from service discovery; the list is 4 seconds old and marked current.
4. The client hashes the key: `md5("book:9780143127741")`, first 4 bytes big-endian → `pos_k = 2,418,773,002`.
5. The client runs `bisect_right(sorted_positions, 2418773002)` over its in-memory array of 1,000 sorted vnode positions → index 603, in roughly 10 comparisons.
6. Index 603 holds position `2,419,004,881`, whose owner map entry is `cache-07`. (Had the index run past the end of the array, it would wrap to index 0 — the ring's only special case.)
7. The client opens or reuses a pooled TCP connection to `cache-07:11211` and issues `get book:9780143127741` with a 20 ms timeout (Lesson 17).
8. **Hit path:** `cache-07` returns the serialised book record. The app renders the page. Done in ~1 ms.
9. **Miss path:** the client gets `END`. The app queries the primary database, gets the row in ~14 ms, and issues `set book:9780143127741` — which hashes to `cache-07` again, because the ring is deterministic. Locality is preserved: the same key always lands on the same node, which is the entire point.
10. **Meanwhile, `cache-07` crashes.** Health checking (Lesson 24) marks it down after 3 failed probes at 2-second intervals — about 6 seconds.
11. Service discovery publishes a new membership list without `cache-07`. Each client independently rebuilds its ring, removing 100 vnode positions and re-sorting — about 1,000 entries, well under a millisecond.
12. **During the propagation window**, clients disagree: some still route `book:9780143127741` to the dead `cache-07` and fail (then fall back to the database), while others already route to the clockwise successor of each affected vnode. **This disagreement window is normal and must be designed for**, not prevented.
13. After the ring rebuild, that key's 100 orphaned vnode arcs are inherited by many different surviving nodes — not one. `cache-07`'s ~10 % share spreads over nine survivors at ~1.1 % each.
14. Database QPS rises by roughly the fraction of keys that moved, for roughly one TTL period, then settles as the new owners warm up. With `N = 10`, that is about 10 % of cache traffic becoming misses — survivable. Under `hash % N` it would have been about 90 %.
15. `cache-07` is replaced. Its replacement registers with **the same node identity** (`cache-07`), so it hashes to the **same 100 ring positions** and reclaims exactly the arcs it previously held. Using a new random name instead would have caused a second, entirely avoidable reshuffle — a very common operational mistake.

## 6. ASCII diagram

```
=============================================================================
 (A) THE HASH RING — 32-bit space, 4 nodes, no virtual nodes
     Clockwise = increasing hash value. A key is owned by the FIRST
     node position found walking clockwise from the key's own position.
=============================================================================

                          0 / 2³²  ── wrap point ──
                                 │
                    ╭────────────┴────────────╮
               ╭────╯                         ╰────╮
          ╭────╯      ✦ k1 = 0.31e9               ╰────╮
       ╭──╯              │ clockwise               ● A  ╰──╮
     ╭─╯                 ▼                      0.42e9     ╰─╮
    ╭╯            ─────────────▶ owned by A                  ╰╮
   ╭╯                                                         ╰╮
   │   ✦ k4 = 3.90e9                                           │
   │     │ clockwise, WRAPS past 2³², owned by A               │
 ● D ◀───╯                                                  ● B
 3.55e9                                                    1.30e9
   │                                                          │
   ╰╮   ✦ k3 = 3.40e9 ──▶ owned by D         ✦ k2 = 2.05e9  ╭╯
    ╰╮                                          │ clockwise ╭╯
     ╰─╮                                        ▼          ╭─╯
       ╰──╮                              owned by C     ╭──╯
          ╰────╮                                   ╭────╯
               ╰────╮         ● C            ╭────╯
                    ╰─────── 2.60e9 ────────╯

  ARC OWNERSHIP (each node owns from its PREDECESSOR up to ITSELF):
    A owns (3.55e9 → 2³² → 0.42e9]   = 1.17e9  = 27.2 %   ← heaviest
    B owns (0.42e9 →      1.30e9]    = 0.88e9  = 20.5 %
    C owns (1.30e9 →      2.60e9]    = 1.30e9  = 30.3 %   ← heaviest
    D owns (2.60e9 →      3.55e9]    = 0.95e9  = 22.1 %   ← lightest
    Ideal would be 25.0 % each.  Peak/min = 30.3 / 20.5 = 1.48×
    THIS IS THE UNEVEN-DISTRIBUTION PROBLEM, with only 4 nodes.

=============================================================================
 (B) SAME RING, UNROLLED FLAT — easier to reason about, same structure
=============================================================================

  0                                                                  2³²
  ├──────┬──────────────┬──────────────────┬───────────────┬──────────┤
  │  k1  │      A       │        B         │       C       │    D     │ wrap
  ╰──────┴──────┬───────┴─────────┬────────┴──────┬────────┴────┬─────╯
             0.42e9            1.30e9          2.60e9        3.55e9
     ✦k1=0.31 ─▶A      ✦k2=2.05 ─▶C     ✦k3=3.40 ─▶D    ✦k4=3.90 ─▶wrap─▶A

=============================================================================
 (C) ADDING NODE E AT 1.90e9 — ONLY ONE BOUNDARY MOVES
=============================================================================

  BEFORE:   ...──┤ B @1.30e9 ├────────── C owns 1.30e9→2.60e9 ──────┤ C
  AFTER:    ...──┤ B @1.30e9 ├─ E owns ─┤ E ├─ C owns ──────────────┤ C
                               1.30→1.90  1.90e9  1.90→2.60

   Keys moved : those in (1.30e9, 1.90e9]  = 0.60e9 = 14 % of ring
   Taken from : C, and ONLY C.
   A, B, D    : completely untouched. Zero cache misses on those nodes.

   Under hash % N (4 → 5 nodes):  80 % of ALL keys move, on ALL nodes.
   Under the ring:                ~1/5 = 20 % expected, from ONE node.

=============================================================================
 (D) VIRTUAL NODES — the same 4 physical nodes, 8 positions each
=============================================================================

  0                                                                  2³²
  ├─┬──┬─┬───┬──┬─┬──┬───┬─┬──┬──┬─┬───┬──┬─┬──┬─┬───┬──┬─┬──┬─┬──┬───┤
  │C│A │B│ D │C │A│ D│ B │A│C │ B│D│ A │C │B│ D│A│ C │ B│D│C │A│ B│ D │
  ╰─┴──┴─┴───┴──┴─┴──┴───┴─┴──┴──┴─┴───┴──┴─┴──┴─┴───┴──┴─┴──┴─┴──┴───╯
   Each physical node now owns 8 small scattered arcs instead of 1 big one.

   ► BALANCE:  shares converge to 25 % each (CV drops by √8 ≈ 2.8×)

   ► FAILURE:  if C dies, each of C's 8 arcs is inherited by whichever
               node happens to sit clockwise of it — a MIX of A, B and D:

      before C dies:  A=25%  B=25%  C=25%  D=25%
      after  C dies:  A=33%  B=34%  D=33%      ← spread over all survivors
      (with V=1 it would have been: A=25% B=25% D=50%  ← D DOUBLES, then dies)

   ► WEIGHTS:  give a 4× bigger machine 32 positions instead of 8,
               and it simply owns ~4× the arc. No extra logic anywhere.
```

## 7. Detailed example

### BookNook — the cache tier scale-out that took the site down, then didn't

**The setup.** BookNook runs 10 Memcached nodes (Lesson 27) holding book metadata, rendered price blocks and search-result fragments. Roughly 40 million distinct keys. Steady-state hit rate 94 %. The primary database serves the 6 % of misses at about 1,100 QPS and sits at 35 % CPU.

**Phase 1 — modulo sharding, and the scale-out that caused an outage.** The client library used `crc32(key) % 10`. Traffic grew, memory pressure rose, and the team added an 11th node at 19:40 on a Tuesday.

```
Fraction of keys remapped = N/(N+1) = 10/11 = 90.9 %
Cache hit rate immediately after    : 94 % → ~8.5 %
Request rate                        : 18,000 cache lookups/s
Misses before : 18,000 × 0.06 = 1,080 /s
Misses after  : 18,000 × 0.915 = 16,470 /s
Database load multiplier            : 16,470 / 1,080 = 15.3×
```

The database went from 1,100 QPS at 35 % CPU to a demanded 16,470 QPS. It saturated in under four seconds, connection pools exhausted, and every app server began timing out. Lesson 30's stampede ran fleet-wide because thousands of concurrent requests for the *same* newly-missing keys all hit the database at once. **Adding capacity caused a full outage.** The fix on the night was to remove the 11th node — which remapped 90.9 % of keys *again*, causing a second outage while the cache re-warmed.

**Phase 2 — moving to a consistent hash ring.** The team switched the client to a ketama-style ring with **160 vnodes per node**, and crucially deployed the change with the *existing 10 nodes* first, during a low-traffic window, accepting one final ~100 % remap at 04:00 when traffic was 7 % of peak. Re-warm took 22 minutes with the database peaking at 61 % CPU. Survivable.

**Phase 3 — the same scale-out, done again.** Two weeks later they added the 11th node at 19:40 on a Tuesday:

```
Fraction of keys remapped = 1/(N+1) = 1/11 = 9.1 %
Hit rate                            : 94 % → ~85.5 % momentarily
Misses after  : 18,000 × 0.145 ≈ 2,610 /s
Database load multiplier            : 2,610 / 1,080 = 2.4×
Database CPU                        : 35 % → 68 %, recovering over ~6 minutes
```

A visible blip on the dashboard. No user impact. **Identical operation, 15.3× load spike versus 2.4×, and the only difference was the key-placement function.**

**Phase 4 — a node dies, and vnodes prove their worth.** `cache-04` crashes at peak.

```
With V = 1 (naive ring):
  cache-04 owned ~10 % of keys; ALL of it inherits to one successor.
  Successor's key count: 10 % → 20 %. Its memory requirement DOUBLES.
  Memcached does not grow; it EVICTS. Successor's hit rate collapses,
  its miss traffic doubles, its CPU and NIC saturate → it degrades, and
  its share would then cascade to the NEXT node. Ring cascade.

With V = 160:
  cache-04's 160 arcs are inherited by the 9 survivors, ~17.8 arcs each.
  Each survivor: 10.0 % → 11.1 % of keys  (+11 %, not +100 %)
  Extra evictions are marginal. Nobody cascades.
```

**Phase 5 — heterogeneous hardware.** BookNook replaces four of the eleven nodes with machines having 4× the RAM. Weighting by vnode count:

```
7 small nodes × 160 vnodes = 1,120 positions   → weight 1 each
4 large nodes × 640 vnodes = 2,560 positions   → weight 4 each
Total positions = 3,680

Small node share = 160/3,680 =  4.35 %  of keys
Large node share = 640/3,680 = 17.39 %  of keys
Ratio = 4.0×  ✓ exactly proportional to RAM
```

Total effective cache capacity rose by 4× on four nodes with no change to any lookup code.

### Contrasting example — a Layer 4 load balancer for a video-streaming service

Same algorithm, a completely different set of pressures.

A streaming provider runs 400 edge servers behind an anycast Layer 4 load balancer (Lessons 4 and 22). The LB must map each **flow** — the TCP/UDP 5-tuple — to a backend, and it must do so **tens of millions of times per second on commodity hardware**.

What changes:

- **The unit is a flow, not a key, and the requirement is different.** A TCP connection *must* keep reaching the same backend for its whole life or it resets. Here consistent hashing is not an optimisation for cache warmth — it is a correctness requirement for connection survival.
- **Lookup cost dominates everything.** At 20 M packets/s, an `O(log M)` binary search over a 100,000-entry ring is ~17 cache-missing memory accesses per packet. That is unaffordable. This is why Google's **Maglev** precomputes a flat **lookup table** (typically 65,537 entries) that maps a hash directly to a backend in **one array index** — `O(1)` with one cache line touched. The table is built to be near-perfectly balanced *and* to change minimally when membership changes, giving most of the ring's property at a fraction of the per-packet cost.
- **Rendezvous hashing is disqualified**, despite its elegance. `O(N)` = 400 hash computations per packet is three orders of magnitude too slow.
- **Bounded load is also disqualified.** Spilling a flow to a different backend mid-connection breaks the connection outright.
- **Imbalance tolerance is tighter.** With 400 nodes at 70 % utilisation, a 1.15× peak means the hottest node sits at 80 % — acceptable. A 2.3× peak would mean 161 %, i.e. packet loss. The high-`V` end of the table is mandatory here, whereas BookNook's cache would survive at `V = 10`.

**The transferable lesson:** the *concept* (minimal disruption under membership change) is universal; the *implementation* is chosen by the cost of a lookup and the consequence of a misroute. For BookNook a misroute costs one cache miss. For the video LB a misroute costs a dropped stream.

## 8. Code or configuration example

> **Illustrative only — not production-ready.** Error handling, membership propagation, metrics, connection pooling, replica placement, thread safety and security hardening are omitted for clarity. In production, use a maintained library (`hash_ring`, `uhashring`, Guava's `consistentHash`, your database's built-in partitioner) rather than this code.

### A working consistent hash ring with virtual nodes (Python 3)

```python
import bisect
import hashlib

HASH_SPACE = 2 ** 32          # 32-bit ring, matching libketama/ngx_http_upstream_hash


class ConsistentHashRing:
    """Maps keys to physical nodes with minimal movement on membership change."""

    def __init__(self, nodes=None, vnodes=160):
        # vnodes = ring positions PER PHYSICAL NODE. 160 is libketama's default.
        # Imbalance CV = 1/sqrt(vnodes): 160 vnodes => ~7.9% spread. See section 4.4.
        self.vnodes = vnodes
        self._ring = {}        # ring position (int) -> physical node name
        self._sorted = []      # the SAME positions, kept sorted for binary search
        self._weights = {}     # physical node name -> capacity weight (1.0 = baseline)
        for node in (nodes or []):
            self.add_node(node)

    @staticmethod
    def _hash(value: str) -> int:
        # MD5 is used for COMPATIBILITY with ketama clients, not for security.
        # Any fast, well-distributed hash works (murmur3, xxhash).
        # NEVER use Python's builtin hash(): it is randomised per process via
        # PYTHONHASHSEED, so two clients would build DIFFERENT rings and
        # silently disagree about who owns every key.
        digest = hashlib.md5(value.encode("utf-8")).digest()
        return int.from_bytes(digest[:4], "big")      # take 32 bits

    def _position_count(self, node: str) -> int:
        # Weighting IS vnode count. A node with weight 4.0 gets 4x the positions,
        # therefore ~4x the arc, therefore ~4x the keys. No other code changes.
        return max(1, int(self.vnodes * self._weights.get(node, 1.0)))

    def add_node(self, node: str, weight: float = 1.0) -> None:
        self._weights[node] = weight
        for i in range(self._position_count(node)):
            # The vnode label MUST be deterministic and stable across clients
            # and across restarts. "node#i" means a replacement server reusing
            # the same NAME reclaims exactly the same arcs -- no reshuffle.
            pos = self._hash(f"{node}#{i}")
            while pos in self._ring:
                # Collisions are rare in a 2^32 space but are NOT impossible
                # (birthday bound: ~50% chance once you exceed ~77,000 positions).
                # Probing forward keeps the mapping deterministic for all clients.
                pos = (pos + 1) % HASH_SPACE
            self._ring[pos] = node
            bisect.insort(self._sorted, pos)   # O(M) per insert; fine at build time

    def remove_node(self, node: str) -> None:
        for pos in [p for p, n in self._ring.items() if n == node]:
            del self._ring[pos]
            # list.pop(index) after a binary search keeps _sorted consistent
            self._sorted.pop(bisect.bisect_left(self._sorted, pos))
        self._weights.pop(node, None)

    def get_node(self, key: str):
        """The hot path: one hash + one binary search = O(log(N*vnodes))."""
        if not self._sorted:
            return None
        pos = self._hash(key)
        idx = bisect.bisect(self._sorted, pos)   # first position STRICTLY greater
        if idx == len(self._sorted):
            idx = 0                              # THE WRAP: past the end => back to 0.
                                                 # This single line is what makes it a
                                                 # ring rather than a line.
        return self._ring[self._sorted[idx]]

    def get_nodes(self, key: str, count: int):
        """Preference list for replication: walk clockwise collecting DISTINCT
        physical nodes. Without the distinctness check you would happily place
        all 3 replicas of a key on 3 vnodes of the SAME machine -- a real bug
        that silently destroys durability."""
        if not self._sorted:
            return []
        start = bisect.bisect(self._sorted, self._hash(key)) % len(self._sorted)
        chosen = []
        for offset in range(len(self._sorted)):
            node = self._ring[self._sorted[(start + offset) % len(self._sorted)]]
            if node not in chosen:
                chosen.append(node)
                if len(chosen) == count:
                    break
        return chosen


# --------------------------------------------------------------------------
# Demonstration: measure key movement and imbalance. Run this; the numbers
# reproduce section 9 within sampling error.
# --------------------------------------------------------------------------
if __name__ == "__main__":
    from collections import Counter

    KEYS = [f"book:{i}" for i in range(100_000)]

    for V in (1, 10, 100, 256):
        ring = ConsistentHashRing([f"cache-{i:02d}" for i in range(10)], vnodes=V)

        before = {k: ring.get_node(k) for k in KEYS}
        spread = Counter(before.values())
        ideal = len(KEYS) / 10
        peak = max(spread.values()) / ideal          # observed peak/mean imbalance

        ring.add_node("cache-10")                    # scale out 10 -> 11
        after = {k: ring.get_node(k) for k in KEYS}
        moved = sum(1 for k in KEYS if before[k] != after[k]) / len(KEYS)

        # Expected moved ~= 1/(N+1) = 1/11 = 9.1%, INDEPENDENT of vnode count.
        # Expected peak  ~= 1 + 1.5/sqrt(V).
        print(f"V={V:4d}  moved={moved:6.2%}  peak/mean={peak:4.2f}x")

    # Contrast: the modulo scheme this replaces.
    mod_before = {k: hash_mod for k in KEYS
                  for hash_mod in [ConsistentHashRing._hash(k) % 10]}
    mod_after = {k: ConsistentHashRing._hash(k) % 11 for k in KEYS}
    mod_moved = sum(1 for k in KEYS if mod_before[k] != mod_after[k]) / len(KEYS)
    print(f"hash % N      moved={mod_moved:6.2%}   <-- expected 10/11 = 90.9%")
```

### Rendezvous (HRW) hashing — the simpler alternative, in full

```python
import hashlib
import math

def hrw_score(key: str, node: str, weight: float = 1.0) -> float:
    """The 'highest random weight' of the name HRW."""
    h = hashlib.md5(f"{key}:{node}".encode()).digest()
    u = int.from_bytes(h[:8], "big") / 2 ** 64            # map the hash into (0, 1)
    u = min(max(u, 1e-18), 1 - 1e-18)                     # keep log() finite
    # Weighted HRW (Jason Resch's formulation): -1/ln(u) is exponentially
    # distributed, so scaling it by weight gives EXACT proportional shares --
    # unlike vnode-count weighting, which is granular to 1/vnodes.
    return weight / -math.log(u)

def rendezvous(key: str, nodes, weights=None):
    """O(N) hashes, zero state, exactly uniform, no vnode tuning.
    Preferable when N is small (< ~100) or you need ranked replicas."""
    w = weights or {}
    return max(nodes, key=lambda n: hrw_score(key, n, w.get(n, 1.0)))

def rendezvous_ranked(key: str, nodes, k: int, weights=None):
    """Ranked preference list comes FREE -- sort by SCORE instead of taking the
    max. Note it must sort by the score, not by calling rendezvous() per node,
    which would just return the node name. Doing this on a ring requires
    walking the ring and de-duplicating vnodes by physical owner."""
    w = weights or {}
    return sorted(nodes, key=lambda n: hrw_score(key, n, w.get(n, 1.0)),
                  reverse=True)[:k]
```

### Jump consistent hash — zero memory, but buckets must be numbered

```python
def jump_hash(key_int: int, num_buckets: int) -> int:
    """Lamping & Veach, 2014. O(ln n) time, O(1) memory, perfect balance.
    CONSTRAINT: returns a bucket NUMBER in [0, num_buckets). You may only
    grow/shrink at the TOP of the range -- you cannot remove bucket 4 and
    keep 0-3 and 5-10. Therefore: right for LOGICAL shard numbers that you
    then map to physical servers via a directory (Lesson 36); WRONG for
    physical servers that fail independently."""
    b, j = -1, 0
    while j < num_buckets:
        b = j
        key_int = (key_int * 2862933555777941757 + 1) & 0xFFFFFFFFFFFFFFFF
        j = int((b + 1) * ((1 << 31) / float((key_int >> 33) + 1)))
    return b
```

### Production configuration — the same idea in three systems

```nginx
# nginx: ketama-compatible ring. The word "consistent" is doing ALL the work --
# omit it and you get plain modulo hashing with the 90.9% remap problem.
upstream booknook_cache {
    hash $request_uri consistent;
    server 10.0.4.11:11211 weight=1;    # weight multiplies the vnode count
    server 10.0.4.12:11211 weight=1;
    server 10.0.4.13:11211 weight=4;    # 4x RAM => 4x the ring positions
}
```

```yaml
# Envoy: ring hash with virtual nodes and BOUNDED LOAD.
clusters:
- name: booknook_sessions
  lb_policy: RING_HASH
  ring_hash_lb_config:
    minimum_ring_size: 1024       # total ring points; Envoy divides across hosts,
                                  # so 10 hosts => ~102 vnodes each (CV ~= 10%)
    maximum_ring_size: 8388608    # guard rail: a huge ring costs memory + rebuild time
    hash_function: MURMUR_HASH_2  # faster than the default XX_HASH for short keys
  common_lb_config:
    consistent_hashing_lb_config:
      # BOUNDED LOAD: no host may exceed 125% of the fleet average in-flight count.
      # Overflow walks clockwise to the next host with spare capacity.
      # Correct for caches and stateless-ish routing; WRONG for authoritative
      # data placement -- you cannot "spill" a user's row to a neighbour.
      hash_balance_factor: 125
```

```yaml
# Cassandra (cassandra.yaml): the token ring in a real database.
partitioner: org.apache.cassandra.dht.Murmur3Partitioner   # signed 64-bit ring
num_tokens: 16                 # vnodes per node. The old default was 256.
                               # It was LOWERED because more vnodes means each node
                               # shares replica ranges with more peers, which RAISES
                               # the chance that some pair/triple of simultaneous
                               # failures wipes out a whole replica set.
allocate_tokens_for_local_replication_factor: 3
                               # Picks tokens DELIBERATELY to balance ranges rather
                               # than randomly -- which is why 16 deliberate tokens
                               # now beat 256 random ones.
```

## 9. Numerical example

### Part A — Adding a node: modulo versus ring, with and without vnodes

**Assumptions, stated explicitly:**
- `K` = 40,000,000 distinct cache keys, hashing uniformly over a 32-bit space.
- `N` = 10 cache nodes, growing to 11.
- Keys are equally popular (deliberately unrealistic — Part D relaxes this).
- Ring positions are uniformly random (true for MD5-style hashing of node names).
- Ideal share per node after the change = `1/11` = 9.09 % = 3,636,364 keys.

**A1 — `hash(key) % N`:**

```
P(key keeps owner) = 1/(N+1) = 1/11 = 0.0909
Fraction moved     = 10/11            = 0.9091
Keys moved         = 40,000,000 × 0.9091 = 36,363,636 keys

Those moves are spread across ALL 11 nodes, so every node is disrupted.
```

**A2 — consistent hashing, `V = 1` (one position per node):**

```
The new node lands at a uniformly random ring position and claims the arc
from its predecessor to itself. After the change it owns 1/(N+1) in expectation.

Expected keys moved = K/(N+1) = 40,000,000/11 = 3,636,364 keys  (9.09 %)
Reduction versus modulo = 36,363,636 / 3,636,364 = 10.0×   ( = N, as derived )

BUT: all 3,636,364 moved keys come from exactly ONE node -- the new node's
clockwise successor. If that successor previously held its own fair ~10 %
(4,000,000 keys), it now holds 4,000,000 − 3,636,364 = 363,636 keys.
It dropped to 0.9 % of the fleet while the other nine are untouched at ~10 %.
Post-change imbalance: heaviest/lightest ≈ 4,000,000 / 363,636 = 11×
```

That is the vnode-free ring's dirty secret: *total* movement is minimal, but it is concentrated, so the fleet becomes badly unbalanced after each change.

**A3 — consistent hashing, `V = 100`:**

```
Ring positions before = 10 × 100 = 1,000
Ring positions after  = 11 × 100 = 1,100
The new node contributes 100 positions scattered around the ring.

Expected keys moved = K/(N+1) = 3,636,364 keys  (9.09 %)  ← IDENTICAL to A2.
   Vnodes do NOT change how MANY keys move. They change WHERE FROM.

Each of the new node's 100 arcs is carved out of whichever node sits clockwise
of it. With 10 candidates and 100 draws, each existing node contributes
roughly 100/10 = 10 arcs:

Expected keys taken from each existing node = 3,636,364 / 10 = 363,636
As a share of that node's own 4,000,000 keys = 9.1 %

Every node goes 4,000,000 → ~3,636,364 keys. Fleet stays balanced.
Post-change imbalance ≈ 1.15× (from the section 4.4 table), not 11×.
```

**The headline comparison:**

| Scheme | Keys moved | Nodes disrupted | Post-change peak/mean | Cache hit rate immediately after (94 % baseline) | Resulting DB QPS multiplier |
|---|---|---|---|---|---|
| `hash % N` | 36,363,636 (90.9 %) | all 11 | ~1.0× | ~8.5 % | **15.3×** |
| Ring, `V = 1` | 3,636,364 (9.1 %) | **1** | ~11× | ~85.5 % | **2.4×** |
| Ring, `V = 100` | 3,636,364 (9.1 %) | all 10, evenly | **~1.15×** | ~85.5 % | **2.4×** |

Note carefully: **vnodes do not reduce movement.** They fix *balance*, both before and after the change. Movement is already minimal with `V = 1`. Conflating these two benefits is the single most common misunderstanding of this topic.

### Part B — Removing a node (the failure case, which is the one that matters)

**Assumptions:** same fleet, now 11 nodes each holding ~3,636,364 keys; `cache-04` dies. Each node has 16 GB of cache RAM and is 85 % full at steady state. Fleet request rate 18,000 lookups/s, i.e. ~1,636 lookups/s per node.

**B1 — `V = 1`:**

```
cache-04's entire arc inherits to ONE successor, cache-07.

cache-07 key count : 3,636,364 → 7,272,728    (+100 %)
cache-07 traffic   : 1,636/s  → 3,272/s       (+100 %)
cache-07 memory needed: 0.85 × 16 GB × 2 = 27.2 GB required vs 16 GB available

Memcached does not grow -- it EVICTS (Lesson 30). It must evict down to 16 GB,
i.e. discard ~41 % of what it is now asked to hold:
   working set 27.2 GB, capacity 16 GB → 16/27.2 = 59 % retained
   cache-07 local hit rate: 94 % → ~55 %
   cache-07 miss traffic:  3,272 × 0.45 = 1,472 misses/s from one node alone

If cache-07's CPU/NIC saturates at ~2,800 lookups/s, it now fails health checks.
Its 7,272,728 keys then inherit to the NEXT node, which needs 3× its capacity.
   ⇒ CASCADING RING FAILURE: 11 → 10 → 9 → ... in minutes.
```

**B2 — `V = 100`:**

```
cache-04's 100 arcs inherit to the 10 survivors, ~10 arcs each.

Keys gained per survivor = 3,636,364 / 10 = 363,636
Survivor key count : 3,636,364 → 4,000,000   (+10.0 %)
Survivor traffic   : 1,636/s  → 1,800/s      (+10.0 %)
Memory needed      : 0.85 × 16 GB × 1.10 = 14.96 GB  vs 16 GB available  ✓ fits

Hit rate dip: ~94 % → ~85 % fleet-wide for one TTL period, then recovers.
No node approaches saturation. No cascade.
```

**The ratio that justifies virtual nodes:** `+100 %` versus `+10.0 %` load on the surviving nodes. The same failure is a cascade in one case and a dashboard blip in the other. Generalising: **on the loss of one node, the load increase on each survivor is `1/(N−1)` with vnodes, versus `100 %` on one node without them.**

### Part C — Choosing `V` from a target imbalance

**Assumptions:** you want the heaviest node to stay within 15 % of the mean, in a fleet of `N = 20`. Use `peak/mean ≈ 1 + z·CV` with `CV = 1/√V`, and `z ≈ 1.87` (the approximate expected maximum of 20 standard normal samples).

```
Required:  1 + z/√V  ≤  1.15
           1.87/√V   ≤  0.15
           √V        ≥  1.87 / 0.15  =  12.47
           V         ≥  155.4   →  V = 160

Check the costs at V = 160, N = 20:
  Ring positions M       = 20 × 160 = 3,200
  Lookup                 = log₂(3,200) = 11.6 comparisons   (negligible)
  Client memory          ≈ 3,200 × 24 B ≈ 77 KB             (negligible)
  Ring rebuild O(M log M)= 3,200 × 11.6 ≈ 37,000 ops ≈ 0.1 ms  (negligible)

Now check V = 1,000 for comparison:
  peak/mean = 1 + 1.87/31.6 = 1.059   -- only 9 points better than V=160
  memory    = 20,000 × 24 B = 480 KB  -- 6× the cost
  ⇒ not worth it. The √V curve has flattened.
```

That is why 160 (ketama) and 256 (Dynamo-era) are the conventional answers: they are where `1/√V` stops paying.

### Part D — The limit of the model: a hot key

**Assumptions:** `N = 10`, `V = 160`, so key *counts* are balanced within ~12 %. But BookNook puts one book on the home page and it receives 22 % of all cache reads.

```
Total reads                     = 18,000/s
Reads for the one hot key       = 18,000 × 0.22 = 3,960/s, ALL to one node
That node's other keys          = 18,000 × 0.78 / 10 = 1,404/s
That node's total               = 3,960 + 1,404 = 5,364/s
Fleet mean per node             = 1,800/s
Traffic imbalance               = 5,364 / 1,800 = 2.98×

Key-count imbalance on the same fleet = 1.12×
```

**Consistent hashing with 160 vnodes balanced key count to 1.12× and traffic to 2.98×.** It cannot do better, because every request for that key hashes to the same place *by design* — that determinism is the feature. The fixes are different tools entirely: client-side local caching of hot keys, key splitting (`book:X#0 … book:X#7`), a CDN in front (Lesson 31), or bounded-load consistent hashing (Section 4.6). **Always state this limit out loud; it is the difference between reciting the algorithm and understanding it.**

## 10. Trade-offs

**Consistent hashing (ring) versus `hash % N`**
- *Advantages:* movement drops from `N/(N+1)` to `1/(N+1)` — a factor of `N`. Movement is localised to arc boundaries, making online migration feasible. Scale-out stops being an outage.
- *Disadvantages:* every client must know the full membership list and agree on it; a stale client silently misroutes. The ring is state that must be built, propagated and versioned. Debugging "which node owns this key?" requires tooling rather than a mental `%`.
- *Operational cost:* membership propagation, ring-version observability, and a migration plan for the one-time switch (which itself remaps ~100 % of keys).
- *Unnecessary complexity when:* the node set is genuinely fixed for the system's lifetime and the keys are stateless — then `% N` is simpler and equally correct.

**Virtual nodes**
- *Advantages:* imbalance `2.3× → 1.15×`; failures spread over all survivors instead of doubling one; weighting becomes trivial; cascading ring failure is eliminated.
- *Disadvantages:* client memory and ring-rebuild time grow linearly in `V`; in replicated systems, higher `V` enlarges each node's peer set and **increases the probability that a multi-node failure destroys a full replica set**; repair and streaming operations multiply per token range.
- *Operational cost:* one tuning parameter that is easy to leave at a bad default in both directions.
- *Unnecessary complexity when:* `N` is large (say >1,000) and random placement is already smooth — but note this is rare, and vnodes still help failure spreading.

**Bounded-load consistent hashing**
- *Advantages:* a hard guarantee, not a statistical tendency — no node exceeds `(1+ε)×` average. Hot keys stop pinning one node.
- *Disadvantages:* affinity becomes conditional on instantaneous load, so a cache gets duplicate entries and a lower hit rate; requires a live load signal; incompatible with authoritative data placement and with long-lived connections.
- *Unnecessary complexity when:* your load is already even and you have no hot keys — you are paying for a guarantee you do not need.

**Rendezvous hashing**
- *Advantages:* exactly uniform with no tuning; exact minimal disruption; free ranked replicas; exact weights; no state; ~5 lines of code that cannot be misconfigured.
- *Disadvantages:* `O(N)` per lookup, which disqualifies it above a few hundred nodes or at packet-level rates.
- *Unnecessary complexity when:* nothing — it is *less* complex than a ring. The only reason not to use it is the `O(N)` cost.

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| Scale-out performed under `hash % N` | Site becomes unusable within seconds: timeouts and 5xx while the database saturates from a 15× miss spike | Cache hit-rate dashboard collapses at the exact moment of a deploy; DB CPU vertical; both correlated with a scale event | Revert the node change (causing a *second* remap), then migrate to a consistent hash ring during low traffic; thereafter, blips instead of outages |
| Node dies with `V = 1` | Progressive slowdown then a wave of errors as nodes fail one after another | One successor's key count, eviction rate and CPU exactly double; the next node follows minutes later | Raise `V` to 100–256 and rebuild; short term, add capacity to break the cascade |
| Client holds a **stale membership list** | Intermittent, user-specific wrongness: some users see stale prices, some sessions vanish; hit rate sags with no obvious cause | Per-client ring-version metric diverges; a "key owner" debug endpoint disagrees between hosts | Emit and alert on a ring hash/version per client; fail closed (serve from origin) when the version is older than N seconds |
| Node replaced with a **new name** (`cache-04` → `cache-04b`) | A surprise 9 % miss spike for an operation that should have been free | Movement metric shows ~2/N of keys moved on a 1-for-1 replacement instead of ~0 | Use stable node identities (DNS name, persistent ID), never ephemeral hostnames or IPs, as the hash input |
| Non-deterministic hash across clients (e.g. Python's `hash()`, or different library versions) | Random cache misses on a fraction of requests; hit rate stuck at an inexplicable level | Two app servers return different owners for the same key | Pin the hash function and digest length explicitly; add a cross-client conformance test asserting a fixed key→node vector |
| **Hot key** on a perfectly balanced ring | One product page is slow or errors while the rest of the site is fine | One node at 3× fleet traffic while key counts are even — the giveaway pairing | Local client cache for hot keys, key splitting, CDN (Lesson 31), or bounded load |
| `V` set very high in a replicated store | Rare but catastrophic: a two-node failure loses a replica set and data is unavailable or lost | Replica-set overlap analysis; unexpected unavailability from a failure count the RF should have tolerated | Lower `num_tokens` and use deliberate token allocation; consider rack/zone-aware placement |
| Flapping node joins and leaves repeatedly | Repeated small miss spikes; ring churn; migration tasks start and abort | Ring rebuild counter climbing; membership event rate | Hysteresis in membership (a node must be stable for N seconds before rejoining); fix the underlying health-check flapping (Lesson 24) |
| Ring rebuilt with a different `V` than peers | Half the fleet routes a key one way, half another; ~50 % miss rate | Ring-version/parameter mismatch in config audit | Treat `V` as part of the ring identity; refuse to join on mismatch |

## 12. Alternatives

| Approach | How placement is decided | Movement on membership change | Balance | Lookup cost | Weights | Best for |
|---|---|---|---|---|---|---|
| **`hash(key) % N`** | Modulo of hash by node count | **`N/(N+1)` — nearly everything** | Perfect | `O(1)` | No | Fixed node sets; stateless backends where movement is free |
| **Consistent hash ring + vnodes** | Clockwise successor on a ring | `1/(N+1)` | ~1.15× at `V=160` | `O(log(N·V))` | Yes, granular to `1/V` | Caches, Dynamo-style stores, large fleets, L7 affinity |
| **Bounded-load ring** | Ring, skipping full nodes | `O(1/N)`, slightly higher | **Guaranteed ≤ `(1+ε)×`** | `O(log(N·V))` + probing | Yes | Caches and routing with hot keys |
| **Rendezvous (HRW)** | `argmax hash(key, node)` | `1/(N+1)`, exact | ~1.00× exactly | **`O(N)`** | Yes, exact | `N` < ~100; ranked replicas; precise weights |
| **Jump consistent hash** | Closed-form bucket computation | `1/(n+1)`, optimal | Perfect | `O(ln N)`, zero memory | No | Numbered logical shards you control |
| **Maglev / lookup table** | Precomputed table, one array index | Small, near-minimal | Near-perfect | **`O(1)`, one memory access** | Yes | L4 load balancing at packet rates |
| **Fixed partitions + directory** (Kafka, Elasticsearch, Vitess, Lesson 36) | `hash % P` into a large fixed `P` (e.g. 1,024), then a table maps partitions → servers | **Zero key movement**; only whole partitions are reassigned | Controlled explicitly by the operator | `O(1)` + a directory lookup | Yes, exact | Databases needing operator control; the most common production choice |

That last row deserves emphasis, because it is what most mature systems actually do. **Pre-split into far more partitions than you have servers** — say 1,024 partitions on 10 servers — and keep a directory mapping partition → server. Keys never move between partitions, so `hash % 1024` is permanently stable; scaling means moving *whole partitions*, which you can schedule, throttle, verify and roll back. You get minimal movement *and* exact operator control *and* `O(1)` lookup. The price is the directory: a small piece of strongly consistent, highly available metadata (which is itself a hard problem — see **Phase 3, consensus and coordination**, and the directory-based sharding discussion in Lesson 36).

Consistent hashing is the right answer when you want **zero coordination** — every client computes the answer independently with no shared metadata service. Fixed partitions plus a directory is the right answer when you want **control**. Choose based on whether you can afford to run a strongly consistent directory.

## 13. When to use it

1. **Distributed caches with client-side sharding** (Lesson 27). The canonical case: Memcached or a Redis client ring where the fleet size changes and a 90 % remap would stampede the database (Lesson 30). Use `V = 100–256`.
2. **Dynamo-style data stores** where a key's home must be computable by any node with no coordinator — Cassandra, Riak, ScyllaDB. Consistent hashing *is* the partitioning scheme, and the clockwise walk also yields the replica preference list.
3. **Stateful request routing** where a backend holds per-key in-memory state: WebSocket rooms, game sessions, rate-limit counters, streaming aggregation partitions. The Lesson 23 chat example — hash the *room ID*, never the client IP.
4. **CDN and edge request routing** (Lesson 31): mapping a URL to a cache server inside a POP so each object is cached once, not once per server, while edge servers come and go constantly.
5. **Layer 4 flow affinity** (Lesson 22), where a TCP connection must keep reaching the same backend for its whole life — though at packet rates, prefer a Maglev-style lookup table.
6. **Any fleet with frequent, routine membership change** — spot instances, Kubernetes pods, rolling deploys — where `% N` would mean a remap on *every single deployment*.

## 14. When *not* to use it

1. **Stateless backends.** If any server can handle any request equally well (Lesson 20), consistent hashing only *reduces* distribution quality compared with least-connections or power-of-two-choices (Lesson 23), in exchange for a locality you do not need. This is the most common misuse.
2. **A fixed, rarely changing node set.** Three database shards that will be three shards for the next two years: `hash % 3` is simpler, debuggable with mental arithmetic, and costs nothing. Add consistent hashing when membership actually starts changing.
3. **Authoritative data placement without a migration mechanism.** The ring tells you *where a key should be*; it does not move the bytes. If you adopt it for a database without building copy, verify, cut-over and rollback machinery (Lesson 37), you have a correct map pointing at empty servers.
4. **When load is dominated by a few hot keys.** Perfect key-count balance and 3× traffic imbalance can coexist (Section 9 Part D). Fix popularity skew with caching layers, key splitting or bounded load — not with a better hash.
5. **When you need operator control over placement.** Compliance may require that EU users' data sits only on EU nodes (Lesson 39, Section 4), or you may need to pin a noisy tenant to dedicated hardware. A hash function is deliberately uncontrollable. Use fixed partitions plus a directory (Lesson 36).
6. **Small `N` at low request rates.** Under ~100 nodes, rendezvous hashing gives better balance, exact weights and free ranked replicas in a fraction of the code. Reach for the ring only when `O(N)` lookup actually hurts.

## 15. Common mistakes

**Beginner mistakes**
- Believing virtual nodes reduce key *movement*. They do not — movement is `1/(N+1)` either way. Vnodes fix **balance** and **failure spreading**.
- Using `hash % N` and discovering the problem during a production scale-out rather than on a whiteboard.
- Using a non-deterministic or per-process-seeded hash (`hash()` in Python, `Object.hashCode()` across JVM versions) so different clients build different rings.
- Hashing the **node's IP address or ephemeral hostname** instead of a stable identity, so every replacement reshuffles the ring.
- Forgetting the wrap-around case, so keys hashing above the highest node position throw an index error or get dropped.
- Assuming keys are equally popular and then being baffled when a balanced ring produces an unbalanced node.
- Placing replicas on the next `R` **ring positions** rather than the next `R` **distinct physical nodes** — silently putting all three copies of a key on one machine.

**Experienced-engineer mistakes**
- Setting `num_tokens` very high in a replicated store because "more vnodes is better balance", without accounting for the increased replica-set overlap and the resulting *lower* tolerance to simultaneous failures.
- Treating the ring as the migration plan. The ring is a *map*; you still need streaming, verification, dual-reads and a rollback path (Lesson 37).
- Omitting a ring-version metric, leaving stale-client misrouting invisible until someone correlates it by hand during an incident.
- Choosing a ring at `N = 12` where rendezvous hashing would have been simpler, exactly balanced, exactly weighted, and would have given ranked replicas for free.
- Enabling bounded load on a cache and then being puzzled by a falling hit rate — spilling duplicates the same key onto multiple nodes.
- Enabling bounded load on an authoritative data tier, where "spill to the next node" is not a degradation but a correctness bug.
- Letting node weights go stale after a fleet hardware refresh, exactly as with load-balancer weights in Lesson 23.
- Applying consistent hashing to a stateless app tier because it sounded sophisticated, thereby converting a well-balanced least-connections fleet into a hash-balanced one with worse tails and no benefit.
- Not planning the **one-time cutover** from modulo to ring, which itself remaps nearly everything — it must be done at low traffic, or with a dual-read shim that checks the old location on a miss.

## 16. Interview perspective

Consistent hashing appears in almost every design involving a cache or a sharded store, and most candidates can only recite "keys and servers on a circle, walk clockwise". The depth is in the numbers and the limits.

**How to use it:**

1. **Lead with the problem, quantified.** *"Under `hash % N`, going from 10 to 11 nodes remaps 10/11 — 91 % — of keys, because a key keeps its owner only when `h mod N == h mod (N+1)`, which happens for 1 in `N+1` keys. For a cache that turns a scale-out into a database stampede."* Stating the fraction and *why* immediately separates you.
2. **State the benefit as a ratio.** *"The ring moves `1/(N+1)`, so the improvement factor is exactly `N`."* Clean, memorable, and shows you derived it rather than memorised it.
3. **Volunteer the uneven-distribution problem unprompted.** *"With `N` random positions the arc lengths have a coefficient of variation of 100 % — the standard deviation equals the mean — and the largest arc is about `ln N / N`, so one node carries 2–3× its share."* This is the step most candidates skip entirely.
4. **Introduce vnodes as the fix, with the formula.** *"`V` positions per node averages `V` arcs, so `CV = 1/√V`. At `V = 100` that's 10 %, at 256 it's 6 %. Diminishing returns past ~160, which is why ketama uses 160."*
5. **Separate the two benefits of vnodes.** *"Vnodes don't reduce movement — that's already `1/N`. They fix balance, and more importantly they spread a failed node's load across all survivors instead of doubling one, which is what prevents a cascading ring failure."*
6. **Name the limit before the interviewer does.** *"This balances key count, not traffic. A hot key still pins one node — I'd handle that with a client-side local cache, key splitting, or bounded-load consistent hashing."*
7. **Know when the answer is something else.** *"At 12 nodes I'd actually use rendezvous hashing — exactly uniform, exact weights, free ranked replicas, five lines, no tuning. The ring earns its complexity around a few hundred nodes, or at packet rates where I'd use a Maglev table."*
8. **Mention the production alternative.** *"Many real databases pre-split into 1,024 fixed partitions and keep a directory mapping partitions to servers. Zero key movement, operator control, `O(1)` lookup — at the cost of running a strongly consistent directory."*
9. **Close the operational loop.** *"Clients must agree on membership; I'd version the ring and alert on divergence, and I'd use stable node identities so a replacement reclaims its own arcs."*

**Red flags interviewers watch for:**
- Saying "consistent hashing" without being able to state the movement fraction, or guessing "about half".
- Not knowing why virtual nodes exist, or claiming they reduce key movement.
- Claiming consistent hashing solves hot keys.
- Never mentioning that all clients must agree on membership — treating the ring as if it were a property of the server fleet rather than state held by every client.
- Proposing it for a stateless application tier, where it strictly degrades distribution.
- Drawing the ring but not knowing what happens when a node is removed (load goes to the clockwise successor, which doubles if `V = 1`).
- Being unaware that migrating *to* consistent hashing itself causes a one-time near-total remap.
- Unable to name a single real system that uses it, or naming one that does not (Kafka, Elasticsearch).

## 17. Knowledge check

Write your answers down before continuing.

**Conceptual**

1. Derive, from first principles, the fraction of keys that change owner when a fleet grows from `N` to `N+1` nodes under `hash(key) % N`, and then under a consistent hash ring. State the ratio between the two. Then explain why the ring's advantage *grows* as the fleet gets larger, and describe the one operational step required to migrate from modulo to a ring that is *itself* as expensive as the problem you are solving.

2. A colleague argues: "We should use 4,096 virtual nodes per server — more vnodes means fewer keys move when a server fails, and better balance, so more is strictly better." Identify **both** factual errors and the one genuine risk in this statement. Use the `CV = 1/√V` relationship to quantify what is actually gained going from 256 to 4,096, and explain the specific reliability mechanism by which a very high vnode count can make a replicated store *less* tolerant of simultaneous failures.

3. BookNook's cache ring has 10 nodes and 160 vnodes each. Monitoring shows key counts per node within 11 % of the mean, yet one node is receiving 3× the fleet-average request rate and is CPU-saturated. Explain precisely why consistent hashing cannot fix this, identify what property of the workload is responsible, and describe three distinct mitigations — stating for each what it costs and what it breaks.

**Practical scenario**

4. BookNook is replacing its session store. Sessions are held in memory across 14 servers; a session lost means a user is logged out. The fleet runs on spot instances, so roughly 3 servers are replaced per hour, and deploys roll the whole fleet twice a week. The team proposes a consistent hash ring with `V = 200`, hashing the server's current IP address as the node identity, with each session stored on exactly one server. Identify every design flaw in this proposal — there are at least four — and for each, state the user-visible symptom it produces and the specific fix. Then state whether consistent hashing is the right tool for this problem at all, and justify your answer with reference to the alternatives in Section 12.

**Calculation**

5. BookNook's search-fragment cache has `N = 25` nodes, `V = 100` virtual nodes each, and `K = 200,000,000` distinct keys. Each node has 32 GB of cache RAM, holds an average object of 140 bytes, and the fleet serves 60,000 lookups/second with a 92 % hit rate. The database can sustain 9,000 QPS before saturating.
   Assumptions: keys hash uniformly; all keys are equally popular; ring positions are uniformly random; use `peak/mean ≈ 1 + z/√V` with `z ≈ 1.95` for `N = 25`.
   (a) What is the expected key count on the mean node and on the heaviest node? How much RAM does each require, and does the heaviest node fit in 32 GB?
   (b) Three nodes are added simultaneously (25 → 28). How many keys move in total, what is the resulting momentary hit rate, what database QPS does that imply, and does the database survive? Show the arithmetic.
   (c) Repeat (b) for `hash(key) % N` with the same 25 → 28 change. (Note: the `N/(N+1)` formula is for a single node addition — derive the correct fraction for `25 → 28` directly.) What database QPS results, and by what factor is the database over its limit?
   (d) Two nodes fail simultaneously. By what percentage does each surviving node's key count and request rate increase, and what is the smallest vnode count `V` at which the heaviest survivor would still fit within 32 GB of RAM after the failure?
   Show every step and state any additional assumptions you make.

Reply with your answers and I'll assess them.

---

**Progress checklist**

Phase 1 — Internet and System-Design Foundations: all 17 topics `[x]` complete.

Phase 2 — Scaling
- [x] 1–17. Vertical scaling through replication lag and read-after-write consistency
- [x] 18. Database partitioning and sharding
- [x] 19. Range, hash, directory, geographic sharding
- [x] 20. Resharding, hot partitions, cross-shard queries
- [x] 21. Consistent hashing and virtual nodes
- [ ] 22. Autoscaling and multi-region architecture ← next

Phases 3–5: not started.

**NEXT LESSON: Phase 2, Topic 22 — Autoscaling and multi-region architecture**

---

# Lesson 39 — Autoscaling and Multi-Region Architecture

*Phase 2, Topic 22*

## 1. Simple definition

**Autoscaling** is the automatic adjustment of how much capacity a system runs, in response to how much work it is being asked to do — adding servers when demand rises, removing them when demand falls.

**Multi-region architecture** is running the same system in two or more geographically separate data-centre regions, so that users are served from somewhere near them and the system survives the loss of an entire region.

They are taught together because they are the two answers to the same question — *"how much capacity, and where?"* — and because they share one hard truth: **the compute tier is easy and the data tier is where all the difficulty lives.**

Terms defined before use:

- **Region**: a geographically distinct cluster of data centres (e.g. `us-east-1` in Virginia, `eu-west-1` in Ireland). Regions are far apart — tens to hundreds of milliseconds.
- **Availability Zone (AZ)**: an isolated data centre *within* a region, with independent power, cooling and network. AZs in a region are ~0.5–1 ms apart.
- **Scaling out / in**: adding / removing instances (horizontal, Lesson 19).
- **Scaling up / down**: changing an instance's size (vertical, Lesson 18).
- **Desired capacity**: the instance count an autoscaler is currently trying to achieve.
- **Warm-up time**: the interval between "launch this instance" and "this instance is serving traffic at full speed".
- **Cooldown**: a period after a scaling action during which further actions are suppressed.
- **Flapping**: repeated scale-out/scale-in oscillation with no change in real demand.
- **Blast radius**: the set of users or functionality affected by a single failure.
- **Failure domain**: a boundary inside which a failure is contained — process, host, rack, AZ, region, or global.
- **RTO (Recovery Time Objective)**: how long you are willing to be down. **RPO (Recovery Point Objective)**: how much recently written data you are willing to lose.

## 2. Why it exists

### The autoscaling case

BookNook's traffic is not flat. A typical day:

| Hour (UTC) | Requests/second | Instances needed at target utilisation |
|---|---|---|
| 03:00 | 400 | 4 |
| 09:00 | 2,200 | 20 |
| 13:00 | 3,400 | 30 |
| 20:00 | 4,400 | 40 |
| Black Friday 20:00 | 13,200 | 120 |

If you provision for the peak you run 40 instances at 03:00 to serve work that needs 4 — paying 10× for ten hours a day. If you provision for the average you fail every evening. Autoscaling exists to track the curve.

But note the second column carefully. **The daily peak-to-trough ratio is 10:1; the annual peak-to-trough ratio is 33:1.** Provisioning for Black Friday all year means running at 3 % utilisation for 364 days. That is the real economic argument, and it is why autoscaling became standard.

### The multi-region case

Three independent drivers, which are frequently conflated and should not be:

1. **Latency.** A user in Singapore talking to `us-east-1` pays ~220 ms per round trip. A TLS handshake (Lesson 7) is 2 RTTs; a page needing three sequential API calls is 3 more. That is well over a second before any work is done. Serving from `ap-southeast-1` cuts it to ~10 ms. *Fix: put compute and read-only data near users.*
2. **Availability.** A region is a single failure domain. Regional outages are rare but real — typically once every year or two per cloud region, lasting 1–6 hours. If your availability target is 99.99 % (52 minutes/year, Lesson 15) and a single regional event costs 3 hours, **one such event blows four years of error budget**. *Fix: be able to serve from another region.*
3. **Data residency / legal.** GDPR, China's PIPL, India's DPDP Act, Russia's 242-FZ and various sectoral rules constrain where personal data may be stored and processed. *Fix: place data by jurisdiction — which is a sharding problem (Lesson 36), not a replication problem.*

These three want different architectures. Latency wants reads everywhere. Availability wants full redundancy. Residency wants data *not* to be everywhere. **Decide which driver you actually have before choosing a topology**; designing for all three at once is how teams end up with a system nobody can operate.

## 3. Real-world analogy

**A restaurant chain.**

**Autoscaling** is staffing. The manager watches the dining room and calls in extra staff when it fills up. Three things the manager learns the hard way:

- **Staff take time to arrive.** Calling someone at 19:00 does not produce a waiter at 19:00; it produces one at 19:40 who then needs ten minutes to find the aprons. By then the rush is over or the restaurant has already failed.
- **"How busy are the staff" is the wrong signal.** A waiter standing still because the kitchen is backed up looks idle, but the restaurant is drowning. The right signal is the *number of tables waiting to be served* — the queue, not the busyness.
- **Sending staff home too eagerly is expensive.** Dismiss someone at 21:00 because it went quiet, and when a party of twelve arrives at 21:05 you cannot get them back. And you cannot send home a waiter who is mid-order — you let them finish their tables first.

**Multi-region** is opening branches in other cities. The compute part — chairs, tables, ovens, staff — is just money and replication. The hard part is **the single shared recipe book and the single shared loyalty-card database**. If every branch may edit the recipe book, two branches will edit the same recipe on the same day and you must decide whose version wins. If only head office may edit it, every branch is slow to change anything and a fire at head office stops all edits chain-wide.

**Where the analogy breaks down:**

| Restaurant chain | Distributed system |
|---|---|
| A manager sees the whole dining room | No component sees global state; the autoscaler reasons from metrics that are 30–120 seconds old |
| Staff arrive in a continuous trickle | Instances arrive in discrete steps, after a fixed and surprisingly long boot + warm-up, and may fail to arrive at all (capacity exhaustion in a hot region) |
| A waiter is instantly productive after a short orientation | A new instance is *slower than average* at first: cold JIT, cold local caches, cold connection pools. Sending it a full share of traffic immediately makes things **worse** before they get better |
| Branches are genuinely independent | Regions share global dependencies — DNS, the cloud control plane, your deploy pipeline, your identity provider, your TLS certificates. **These shared dependencies are what make "two regions" far less than twice as reliable.** |
| A branch can be opened and left running | A passive region that is never exercised **does not work when you need it.** Untested failover fails roughly 1 time in 5 to 1 time in 20 |
| Head office can phone a branch instantly | Cross-region communication costs 60–230 ms *per round trip*, and that is physics (speed of light in fibre), not an engineering defect you can optimise away |
| Hiring more staff always helps | Adding instances does nothing if the bottleneck is the single database behind them — and may actively hurt by exhausting its connection pool |

That last row is the most important: **autoscaling the stateless tier can overload the stateful tier.** Doubling app servers doubles database connections, which is how a scale-out event becomes a database outage.

## 4. Detailed technical explanation

### 4.1 Horizontal versus vertical autoscaling

| | **Vertical** (scale up/down) | **Horizontal** (scale out/in) |
|---|---|---|
| Mechanism | Replace the instance with a bigger/smaller one, or resize in place | Change the instance count |
| Granularity | Coarse — usually powers of 2 (2 → 4 → 8 vCPU) | Fine — one instance at a time |
| Disruption | Usually requires a restart; in-place resize is limited and cloud-specific | None if the tier is stateless (Lesson 20) |
| Ceiling | Hard — the largest instance type exists (Lesson 18) | Soft — limited by the shared tier behind it |
| Speed | Minutes, plus a restart | Minutes, but parallel: 20 instances launch as fast as 1 |
| Where it is used | Databases, caches, single-writer components, dev environments | Stateless app tiers, workers, anything behind a load balancer |
| Availability effect | **Negative during the change** (restart) | **Positive** (more independent units) |

**The rule:** autoscale horizontally for anything stateless; use vertical scaling for the data tier, and treat it as a planned, human-approved operation rather than an automatic one. Kubernetes expresses this split directly as the Horizontal Pod Autoscaler (HPA) versus the Vertical Pod Autoscaler (VPA), and explicitly warns against running both on the same metric for the same workload — they fight.

### 4.2 Reactive, scheduled and predictive scaling

**Reactive (metric-threshold) scaling** observes a metric and acts when it crosses a threshold.

- *Simple/step scaling*: "if `metric > X` for `D` seconds, add `n` instances."
- *Target tracking*: "keep `metric` at target `T`" — the autoscaler solves for instance count itself, typically as `desired = ceil(current_instances × current_metric / target_metric)`. This is a proportional controller, and it is almost always the right default because you specify an *objective*, not a *procedure*.
- **Strength:** needs no knowledge of the future; handles unexpected load.
- **Weakness:** it is, by construction, **always late**. It cannot act until load has already arrived and been measured. Section 4.4 quantifies how late.

**Scheduled scaling** sets capacity by the clock: "40 instances at 18:00 UTC daily, 120 instances from 2025-11-28T00:00Z."

- **Strength:** capacity is already there *before* the load. Zero lag. Trivially understandable.
- **Weakness:** wrong whenever reality deviates from the calendar. Must be combined with reactive scaling as a safety net, never used alone.
- **Use it for:** known business events — a marketing email send, a product launch, Black Friday, market open, a batch window. **This is the single most effective and most under-used autoscaling technique.**

**Predictive scaling** forecasts load from historical patterns (typically 1–2 weeks of history, often with a simple seasonal model or a small ML model) and pre-provisions ahead of the forecast.

- **Strength:** automates what scheduled scaling does manually; captures weekly and daily seasonality without anyone maintaining a calendar.
- **Weakness:** requires a stable, repeating pattern — at least a couple of weeks of similar traffic. It is blind to novel events, and it will confidently pre-scale for a pattern that no longer applies. A forecast that is wrong *downward* is worse than no forecast.
- **Reality:** predictive handles the *shape* of the day; reactive handles the *surprises*. Run both. AWS, GCP and Kubernetes (via KEDA/custom metrics) all support this layering.

**The production answer is almost always all three:** predictive or scheduled for the known shape, reactive for deviations, and a **floor** (minimum capacity) high enough that a sudden spike is absorbed without any scaling at all.

### 4.3 Choosing the scaling metric — why CPU is usually wrong

CPU utilisation is the default in every tutorial and it is frequently the wrong signal. Five concrete reasons:

1. **Most web services are I/O-bound, not CPU-bound.** A BookNook app server spends most of a request waiting on the database or the cache. At 400 requests/second it might sit at 12 % CPU while **every one of its 50 worker slots is occupied** and requests are queueing. CPU says "idle"; the user sees 4-second latency. The autoscaler does nothing.
2. **CPU is not linear in useful work.** Garbage-collection pauses, TLS handshakes, logging and sidecar containers all consume CPU without serving more users. A change in GC tuning shifts your scaling behaviour for no real reason.
3. **CPU is distorted by the platform.** cgroup CPU throttling, noisy neighbours, burstable instance credits and hyperthreading mean reported CPU does not map cleanly to headroom.
4. **CPU is a lagging, averaged signal.** It is typically sampled over 60 seconds, so a 20-second saturation is invisible.
5. **CPU cannot see the queue.** By the time CPU rises, the backlog may already be minutes deep.

**Better signals, in rough order of preference:**

| Metric | Why it is good | When it is the right choice | Caveats |
|---|---|---|---|
| **In-flight concurrency per instance** | By Little's Law (Lesson 14), `L = λ × W`, this is the *exact* measure of occupied capacity regardless of whether the work is CPU or I/O | **The best general-purpose signal for request/response services** | Must be measured by the app or the proxy, not the OS |
| **Queue depth / backlog** | For async workers it is literally the unserved demand | **The best signal for queue consumers** | A deep queue of fast messages is fine; prefer *oldest-message age* |
| **Oldest message age (queue lag)** | Directly expresses "how far behind are we, in seconds" — which is the SLO | Async pipelines where the SLO is "processed within N seconds" | Needs the queue to expose it |
| **Requests per second per instance** | Simple and predictive when request cost is uniform | Static-ish workloads; CDN origins | Breaks badly when request costs vary (Lesson 23's 80,000× spread) |
| **p99 latency** | Directly expresses user pain | A supplementary guard rail | **Lagging and non-monotonic**: latency explodes only *after* saturation (Lesson 14's queueing cliff), so scaling on it is always too late |
| **Connection-pool saturation** | Detects the real bottleneck when it is downstream | Services fronting a constrained database | Scaling out here makes things *worse* — alert, do not scale |
| **CPU** | Cheap, universal, no instrumentation | Genuinely CPU-bound work: transcoding, compression, ML inference, rendering | Everything in the list above |

**The practical recipe:** scale on **concurrency per instance** for synchronous services and **queue age** for asynchronous workers. Use Little's Law to set the target:

```
Target concurrency per instance = (worker slots) × (target utilisation)

BookNook app server: 50 worker slots, target utilisation 60 %
  → target 30 in-flight requests per instance

Required instances at 4,400 rps with 120 ms mean service time:
  Fleet concurrency L = λ × W = 4,400 × 0.120 = 528 in-flight
  Instances = ceil(528 / 30) = 18 instances
```

Why 60 % and not 90 %? Lesson 14's queueing cliff: at 90 % utilisation, M/M/1 queueing delay is 9× the service time; at 60 % it is 1.5×. The 40 % headroom is not waste — **it is the buffer that absorbs spikes during the minutes your autoscaler needs to react.** This is the single most important idea connecting autoscaling to latency.

### 4.4 Warm-up time, and why autoscaling cannot handle sudden spikes

This is the part that is almost always glossed over, and it is the reason autoscaling disappoints people.

An autoscaled instance is not available when it is launched. The full chain, with **typical** values:

| Stage | Typical duration | Why |
|---|---|---|
| Metric aggregation window | 60 s | Metrics are published in 1-minute buckets (10 s with high-resolution metrics, at extra cost) |
| Alarm evaluation (breach for N periods) | 60–180 s | Usually 1–3 datapoints to avoid acting on noise |
| Scaling decision + API call | 5 s | Control-plane latency |
| VM/instance boot | 40–90 s | Hypervisor placement, kernel boot, cloud-init |
| Container image pull (if not cached) | 10–120 s | Depends heavily on image size — a 2 GB image is a scaling liability |
| Application start | 15–60 s | Framework init, config fetch, DB connection pool, JIT warm-up |
| Health check passing (2 consecutive at 15 s) | 30 s | Readiness probe (Lesson 24) |
| LB registration + slow start ramp | 30–60 s | Gradual traffic ramp so the cold instance is not immediately saturated |
| **Total: launch request → full-speed serving** | **≈ 4–7 minutes** | |

Now put that against a spike. **Worked timeline — a 30-second spike against a 3-minute instance boot** (and a ~5-minute total pipeline):

```
ASSUMPTIONS: BookNook runs 20 instances, each with 50 worker slots, serving
2,200 rps at 120 ms, i.e. 264 in-flight = 26 % slot utilisation. A marketing
email lands and traffic ramps to 6,600 rps (3×) over 30 seconds.
Target-tracking on concurrency, 1-minute metrics, 1 breaching datapoint.
Instance boot 3 min; app start + health + LB ramp adds ~2 min.

t = 0:00   Traffic begins ramping.  2,200 rps.
t = 0:30   Ramp complete. 6,600 rps. Required concurrency = 6,600 × 0.12 = 792.
           Available slots = 20 × 50 = 1,000.  Utilisation 79 %.
           Queueing already begins; p99 starts to climb.
t = 1:00   First 1-minute metric window CLOSES. It AVERAGES the spike with the
           30 calm seconds before it → reports ~55 % utilisation. BELOW threshold.
           ⇒ NO SCALING ACTION. The averaging window alone cost a full minute.
t = 2:00   Second window closes, now fully inside the spike → 79 %. Alarm breaches.
t = 2:05   Autoscaler computes desired = ceil(20 × 79/60) = 27. Launches 7.
t = 5:05   Instances booted (3 min).
t = 7:05   App started, health checks passed, LB slow-start complete.
           ⇒ CAPACITY ARRIVES 7 MINUTES AND 5 SECONDS AFTER THE SPIKE BEGAN.

The spike lasted 30 seconds to establish and the site was degraded for 7 minutes.
Ratio of response time to spike time:  425 s / 30 s = 14×  TOO SLOW.
```

**Conclusion, stated plainly: reactive autoscaling does not protect you from sudden spikes. It protects you from gradual trends.** A spike that arrives faster than ~10 minutes must be absorbed by capacity you already have.

What actually handles spikes:

1. **Headroom.** Run at 50–60 % utilisation, not 85 %. The 20-instance fleet above would have survived at 1,667 spare slots. Headroom is the primary spike defence and it is simply bought.
2. **Scheduled scaling** for anything you can predict — including that marketing email. Scaling *before* the send costs nothing and removes the problem entirely.
3. **Load shedding and queueing at the edge.** Reject or queue beyond capacity rather than degrading everyone (Lesson 24; **Phase 4 — rate limiting and backpressure**).
4. **Caching and CDN** (Lessons 26–31). A CDN absorbs a flash crowd for cacheable content at effectively unlimited scale, instantly, with no autoscaling at all. For many spikes this is the entire answer.
5. **Faster warm-up:** small container images, pre-pulled on the node; lazy-free app init; pre-created connection pools; snapshot-based starts (AWS Lambda SnapStart, CRaC); keeping a warm pool of stopped-but-initialised instances.
6. **Serverless for spiky edges.** Function platforms scale in ~100 ms–2 s per concurrent request. The trade-off is cold starts, per-request cost, and connection pressure on the database (usually needing a proxy/pooler).

### 4.5 Scale-in safety and connection draining

Scale-out is low-risk. **Scale-in is where autoscaling causes outages.**

Terminating an instance that is serving traffic drops in-flight requests. The correct sequence — identical in shape to the deregistration flow in Lesson 24:

1. Autoscaler selects a victim instance (choose by oldest, or by least in-flight work).
2. **Deregister from the load balancer first.** New requests stop arriving.
3. **Deregistration delay / connection draining**: the LB keeps existing connections alive for a configured window (typically 30–300 s) while refusing new ones.
4. Send `SIGTERM` to the application. The app must respond by **failing its readiness probe** while continuing to serve existing requests.
5. The app finishes in-flight work, flushes buffers, commits offsets, and exits.
6. Only then is the instance terminated. A hard `SIGKILL` timeout (typically 30–120 s) bounds the wait.

Specific hazards:

- **Long-running work.** An instance running an 8-minute report with a 30 s drain window will be killed mid-job. Fix with **scale-in protection** (a flag the instance sets while busy) or by moving long work to a queue (Lesson 13).
- **WebSockets and long polling.** Connections may last hours; draining cannot wait. You must tell clients to reconnect and rely on client-side retry (Lesson 17).
- **Consumers with uncommitted state.** A queue consumer must finish and acknowledge its current message before exiting, or the message is redelivered — which is fine only if processing is idempotent (Lesson 17).
- **Stateful instances.** If the instance holds a shard or a consistent-hash arc (Lesson 38), terminating it triggers a membership change. Scale-in on a stateful tier must be coordinated, never automatic.
- **Spot/preemptible instances** give only a ~2-minute warning. The drain budget is not negotiable.
- **Scaling in too many at once.** A target-tracking controller that drops from 40 to 20 instances in one step doubles the load on the remainder instantaneously. Cap scale-in step size (e.g. "at most 10 % of the fleet per action").

### 4.6 Flapping and cooldowns

**Flapping** is oscillation: scale out, then in, then out, with no real change in demand. The mechanism:

```
10 instances, each at 65 % utilisation. Scale-in threshold: < 70 %.
  → remove 1 instance.
Load redistributes over 9: 65 % × 10/9 = 72 %. Scale-out threshold: > 70 %.
  → add 1 instance.
Load redistributes over 10: back to 65 %.  → remove 1 instance.  → ...
```

The fleet oscillates forever. Each cycle costs a cold start, a drain, cache-warming misses, and — because the new instance is cold — a latency bump for the users routed to it.

Four fixes, used together:

1. **Hysteresis.** Separate the thresholds: scale out above 70 %, scale in below 50 %. The gap must be wider than the redistribution effect, i.e. wider than `1/(N−1)`. With 10 instances that is 11 %, so a 20-point gap is safe.
2. **Cooldown periods.** Suppress further actions for a fixed interval after a scaling action. **Use asymmetric values: short scale-out cooldown (60 s) and long scale-in cooldown (300–600 s).** The asymmetry encodes the right risk posture — being too big is cheap, being too small is an outage.
3. **Instance warm-up exclusion.** Exclude newly launched instances from the metric average until warm, or their cold, low-throughput metrics will drag the average and trigger more scaling.
4. **Evaluate over multiple datapoints** (e.g. 3 of 5 minutes breaching) for scale-in, but **1 datapoint for scale-out**. Again asymmetric, for the same reason.

> **Simplified teaching model versus production reality:** textbooks present autoscaling as a thermostat. In production it is a feedback control loop with ~5 minutes of dead time, a noisy sensor, and an actuator whose output is initially *negative* (a cold instance consumes capacity before providing it). Control systems with large dead time oscillate unless heavily damped — which is exactly why real configurations are so conservative about scale-in.

### 4.7 Cost modelling

The pitch is "pay only for what you use". The reality has four terms people forget:

1. **Headroom.** You cannot run at 100 %, so autoscaled capacity ≈ `demand / target_utilisation`, typically `demand / 0.6`.
2. **Minimum floor.** You keep a floor for spike absorption and for availability (you need at least 2–3 instances across AZs regardless of load).
3. **Discount instruments.** Reserved Instances / Savings Plans / Committed Use Discounts give 30–60 % off for a commitment — but only on *baseline* capacity you are sure to use. Autoscaling and commitments are complementary: **commit the trough, autoscale the peak.**
4. **Engineering and incident cost.** Building, tuning and operating autoscaling has a real price, and a badly tuned autoscaler causes outages.

Section 9 Part B works a full comparison. The headline result to internalise: **autoscaling typically saves 25–45 % on a diurnal workload, and essentially nothing on a flat one** — and below a certain fleet size the saving is less than the engineering cost.

### 4.8 Multi-region topologies

| Topology | How it works | Read latency | Write latency | RTO | RPO | Cost | Hardest part |
|---|---|---|---|---|---|---|---|
| **Single region, multi-AZ** | One region, instances and database replicas across 2–3 AZs | Local | Local (+0.5–1 ms for sync replication) | Seconds (AZ loss) | ~0 | 1× | Nothing — **this is the correct default** |
| **Active–passive (warm standby)** | Region B runs a scaled-down copy; data replicates asynchronously; traffic fails over on disaster | Local to A only | Local to A | Minutes to hours | Seconds to minutes (replication lag, Lesson 34) | ~1.3–1.6× | **Failover is never tested, so it does not work.** Also: data loss equal to replication lag |
| **Active–passive (pilot light)** | Region B has only data replication; compute is created on demand | n/a | n/a | Hours | Seconds to minutes | ~1.1× | Capacity may not be available in region B during a regional event — everyone else is failing over too |
| **Active–active, read-local / write-global** | All regions serve reads from local replicas; **all writes go to one primary region** | **Local everywhere** | One cross-region RTT for non-local users | Minutes (promote a replica) | Replication lag | ~1.8× | Read-after-write consistency (Lesson 34) across regions; a write from Singapore is visible locally only after a 220 ms+ round trip plus replication |
| **Active–active, true multi-master** | Every region accepts writes; conflicts resolved by last-write-wins, CRDTs, or application logic | Local | Local | Seconds | ~0 for the surviving regions | ~2×+ | **Write conflicts.** There is no general solution — you must either partition writes by key or accept conflict resolution semantics (see **Phase 3 — CAP, consistency models and conflict resolution**) |
| **Regionally partitioned (cell / home-region)** | Each user is assigned a home region; all of that user's data lives there and writes go there | Local for the user's own data | Local | Minutes (per-region) | ~0 within a region | ~1.8× | Cross-region queries and users who move; this is **geographic sharding (Lesson 36)** applied globally |

**The honest ranking for most teams:** multi-AZ single region → active–passive with *tested* failover → read-local/write-global → home-region partitioning → true multi-master. Each step up multiplies operational complexity. Most organisations that "need multi-region" actually need multi-AZ plus better testing.

### 4.9 The hard part is always the data tier

Compute in a second region is a solved problem: copy the deployment, point it at a load balancer, done. Everything difficult is data, and it reduces to one physical fact:

> **Writes must be ordered, and ordering requires communication, and communication across regions costs 60–230 ms.**

Three consequences:

- **Synchronous cross-region replication** gives you RPO = 0 but adds a full cross-region RTT **to every write**. A 70 ms US-coast-to-coast RTT turns a 10 ms write into an 80 ms write. For a checkout flow with 4 sequential writes, that is 320 ms added.
- **Asynchronous cross-region replication** keeps writes fast but gives RPO > 0: on sudden region loss you lose everything written in the last `lag` seconds. Lesson 34's replication lag is now measured across an ocean — typically 100 ms–5 s under normal conditions, and **unbounded during network congestion or a replica stall**.
- **Quorum across three regions** (e.g. Spanner, CockroachDB, YugabyteDB) gives strong consistency and survives one region, but every write waits for the *second-fastest* region. With regions at 70 ms, 90 ms and 160 ms, writes cost ~90 ms minimum.

**Design rule:** decide *per data type*, not globally.

| BookNook data | Consistency need | Placement |
|---|---|---|
| Book catalogue, descriptions, covers | Eventual; stale by minutes is fine | Replicate read-only to every region; serve locally; CDN the images (Lesson 31) |
| Inventory count | Strong — overselling is a business error | Single global primary, or per-region inventory pools reconciled asynchronously |
| Shopping cart | Read-your-writes for one user | Home-region per user; replicate asynchronously for DR |
| Orders / payments | Strong, durable, auditable | Single primary region with synchronous replication to a second region; accept the write latency |
| Sessions | Read-your-writes; loss is tolerable (re-login) | Region-local, not replicated; fail over by forcing re-auth |
| Analytics events | Eventual; order not required | Write locally, ship asynchronously to a central store |

Notice the catalogue, the cart and the order get three different answers. **"Multi-region" is not a single decision.**

### 4.10 Data residency

Residency rules say *where personal data may be stored and processed*, and they are categorical — you cannot satisfy them with "mostly".

- **GDPR (EU/EEA):** does not forbid transfer outright, but transfers outside the EEA require a legal basis; many organisations simply keep EU personal data in EU regions to avoid the question.
- **China (PIPL / Cybersecurity Law):** personal information of Chinese users generally must be stored in China, with a security assessment for export.
- **India (DPDP Act 2023):** government may restrict transfers to specified countries.
- **Russia (242-FZ):** personal data of Russian citizens must be stored primarily in Russia.
- **Sectoral:** health, financial and government workloads often add their own regional constraints.

**Architecturally this means home-region partitioning (Lesson 36's geographic sharding), not replication.** The implementation pattern:

1. A **globally replicated, small, low-write directory** maps `user_id → home_region`. This is the one piece of truly global data, and it must be highly available — its outage is a total outage. Keep it tiny and cache it aggressively.
2. The edge resolves the user's home region (from the directory, a token claim, or a cookie) and **routes the request there**, even if it is not the nearest region.
3. Personal data never leaves its home region. Only **non-personal, aggregated or pseudonymised** data is replicated globally.
4. Analytics must be designed around this from day one — a global data warehouse containing EU personal data is exactly the thing the rules prohibit.

Note the direct tension with the latency driver: a German user travelling in Japan is routed back to `eu-west-1` at ~230 ms because compliance outranks latency.

### 4.11 Cross-region latency budget

Approximate, typical round-trip times over cloud backbones. Treat these as planning figures, not guarantees; measure your own.

| Path | Typical RTT | What one round trip costs you |
|---|---|---|
| Same host (loopback) | < 0.1 ms | Free |
| Same AZ | 0.2–0.5 ms | Negligible |
| AZ ↔ AZ, same region | 0.5–1.5 ms | Why synchronous multi-AZ replication is cheap and should be the default |
| `us-east-1` ↔ `us-east-2` | ~12–18 ms | Cheap DR pairing within a country |
| `us-east-1` ↔ `us-west-2` | ~60–75 ms | 30 % of a 200 ms budget |
| `us-east-1` ↔ `eu-west-1` | ~70–85 ms | Transatlantic; the classic DR pair |
| `eu-west-1` ↔ `eu-central-1` | ~15–25 ms | Cheap EU-internal pairing |
| `us-west-2` ↔ `ap-northeast-1` | ~95–115 ms | Trans-Pacific, shortest path |
| `eu-west-1` ↔ `ap-southeast-1` | ~165–185 ms | |
| `us-east-1` ↔ `ap-southeast-1` | ~215–235 ms | **One round trip exceeds a typical 200 ms page budget** |
| `us-east-1` ↔ `sa-east-1` | ~115–130 ms | |

**How to use this table.** Take your user-facing latency budget — say 200 ms for a page to become interactive — and subtract:

```
Budget                                   200 ms
− TLS handshake to the edge (2 RTT, 10 ms local)  −20 ms
− CDN/edge processing                              −5 ms
Remaining for the application            175 ms

If the write must reach a primary in us-east-1 from Singapore:
  one cross-region RTT                  −225 ms   ⇒ ALREADY OVER BUDGET
If the read is served from a local replica in ap-southeast-1:
  local DB round trip                      −3 ms  ⇒ 172 ms remaining ✓
```

**This is why "read-local, write-global" exists.** Reads vastly outnumber writes (typically 10:1 to 1000:1), so serving reads locally fixes 99 % of the latency problem, and the remaining writes pay the crossing. Also note: **you can afford roughly one cross-region round trip per user action, and no more.** Any design with two sequential cross-region hops is broken. Batch, parallelise, or move the logic.

### 4.12 Traffic steering: GeoDNS and anycast

**GeoDNS** (Lesson 4) returns different IP addresses depending on where the DNS query appears to come from.

- **Strength:** simple, works for any protocol, supports weighted, latency-based and failover policies.
- **Weakness 1 — TTL.** Failover is bounded below by the record's TTL plus resolver behaviour. A 60 s TTL means *at least* a minute of blackholing. Lower TTLs increase DNS query load and cost.
- **Weakness 2 — resolvers ignore TTL.** Some ISP resolvers, and many application runtimes (notably JVM DNS caching defaults), cache far longer than the TTL. **Plan for a tail of clients stuck on the old address for 10+ minutes.**
- **Weakness 3 — the resolver's location, not the user's.** GeoDNS sees the recursive resolver's IP. A user on `8.8.8.8` may appear to be somewhere else entirely. **EDNS Client Subnet (ECS)** passes a truncated client subnet to mitigate this, but support is uneven.
- **Weakness 4 — it is a coarse signal.** DNS cannot see per-request health.

**Anycast** (Lesson 4) advertises the *same* IP address from multiple locations via BGP; the internet's routing fabric delivers packets to the topologically nearest advertisement.

- **Strength:** failover is a routing change — seconds, with no client involvement and no TTL. Excellent for DDoS absorption (attack traffic is spread across all POPs). Used by every major CDN and by public DNS resolvers.
- **Weakness 1 — "nearest" is BGP-nearest, not latency-nearest.** BGP optimises for AS-path length and policy, not speed.
- **Weakness 2 — route flaps break TCP.** If a route changes mid-connection, packets land at a different POP with no knowledge of the connection and it resets. This is tolerable for short HTTP requests and for UDP/QUIC (which survives path changes via connection IDs, Lesson 9), and painful for long-lived TCP.
- **Weakness 3 — requires your own IP space and BGP relationships**, which is why most teams consume it via a CDN or cloud global load balancer rather than operating it.

**The standard production pattern:** anycast at the edge (CDN/global LB) for fast, routing-level regional failover, **plus** application-layer steering behind it — the edge terminates TLS near the user and then forwards to a healthy region over the provider's backbone, with per-request health awareness that DNS alone cannot provide. GeoDNS is then used only for coarse initial placement, with low-ish TTLs (60 s) and the explicit understanding that it is not your failover mechanism.

### 4.13 Failure domains and blast radius

Failure domains nest:

```
thread → process → container → host → rack → AZ → region → GLOBAL
```

Each boundary should contain failures. The boundary teams forget is the last one. **Global failure domains** — things that, when broken, break every region simultaneously:

- DNS (your zone, your registrar, your provider)
- TLS certificates (a certificate expiring is a global, simultaneous, self-inflicted outage)
- The deployment pipeline and the artefact it deploys — **a bad binary rolled everywhere is the single most common "multi-region" outage**
- Global configuration and feature flags
- Identity/auth providers
- Cloud provider control planes and IAM
- Shared global data: the home-region directory, licence checks, billing
- Shared libraries and their configuration defaults

**The techniques that actually shrink blast radius:**

- **Staggered deployment** across regions with soak time: one AZ → one region → wait 30–60 minutes with automatic rollback on error-budget burn → remaining regions. This converts "bad binary everywhere" into "bad binary in 10 % of capacity".
- **Cell-based architecture:** partition users into many independent cells, each a full vertical slice (LB + app + data). A failure affects one cell — `1/number_of_cells` of users. This is how large providers actually achieve high availability, and it is a **Phase 4** topic (cell-based architecture and shuffle sharding) that you should name here.
- **Shuffle sharding:** assign each customer a random *combination* of servers rather than a single shard, so no two customers share the same full set — one bad tenant degrades a small, overlapping subset rather than a whole shard.
- **Regional independence as a hard rule:** region A must never make a synchronous call to region B on the request path. If it does, you have one failure domain wearing two region names.
- **Static stability:** a region must keep serving with its *current* configuration even if the control plane, the directory and the deploy pipeline are all unavailable. Fail static: cache the last good config and keep running.

## 5. Request / data flow

A BookNook customer in Singapore browses, adds to cart and checks out, in an active–active read-local / write-global deployment with primaries in `us-east-1` and read replicas plus full compute in `eu-west-1` and `ap-southeast-1`. Simultaneously, the marketing team sends a campaign email and the autoscaler reacts.

1. The user's browser resolves `booknook.com`. The authoritative DNS (Lesson 4) is latency-based GeoDNS with a 60 s TTL and returns an **anycast** edge address.
2. BGP delivers the TCP SYN to the Singapore POP, ~4 ms away. TLS is terminated at the edge (Lesson 7), so the expensive handshake round trips are local, not transpacific.
3. The edge checks its CDN cache (Lesson 31). The book cover images and the catalogue JSON fragment are hits — served in ~6 ms with no origin contact at all.
4. The personalised shell is a miss. The edge forwards over the provider's backbone to the `ap-southeast-1` application tier.
5. A regional load balancer selects an app instance using least-connections with outlier ejection (Lessons 23, 24).
6. The app reads the user's `home_region` claim from the session token. It is `ap-southeast-1` — this user's personal data lives locally. (Had it been `eu-west-1`, the edge would have forwarded to Europe instead, accepting ~165 ms for compliance.)
7. **Reads:** catalogue, pricing and recommendations come from the local `ap-southeast-1` read replica in ~3 ms. Replication lag from `us-east-1` is currently 420 ms (Lesson 34) — invisible for catalogue data.
8. **Cart write:** the cart is home-region data, so `POST /cart/items` writes to the Singapore regional primary in ~5 ms. The user immediately sees the updated cart — read-your-writes holds because both read and write are local.
9. **Checkout write:** `POST /orders` is strongly consistent, globally ordered data. It must reach the global primary in `us-east-1`: ~225 ms RTT plus ~12 ms of work = ~237 ms. The UI shows a spinner; this is the one cross-region crossing the design permits.
10. **Inventory decrement** happens inside the same `us-east-1` transaction as the order insert — not as a second cross-region call, which would have doubled the crossing to ~470 ms.
11. The order write is synchronously replicated to `us-west-2` before acknowledging (+70 ms, included above), giving RPO = 0 for orders.
12. A confirmation event is published to a queue and processed asynchronously (Lesson 13); the user is not kept waiting for email, analytics or the warehouse feed.
13. **Meanwhile — the campaign email lands.** Singapore traffic triples over 40 seconds.
14. The CDN absorbs the image and catalogue load entirely; origin traffic rises far less than total traffic. **This is the single biggest reason the spike is survivable.**
15. App-tier concurrency per instance rises from 18 to 54 against a target of 30. The target-tracking autoscaler needs two 1-minute windows to see it clearly.
16. At `t ≈ 2:05` it computes `desired = ceil(12 × 54/30) = 22` and launches 10 instances. They begin serving at `t ≈ 7:00`.
17. **In the meantime**, the fleet survives only because (a) a scheduled scaling action had already raised the regional floor from 8 to 12 instances ahead of the known send time, and (b) the target of 30-of-50 slots left 40 % headroom. Without both, step 16's lateness would have been an outage.
18. Load shedding is armed but not triggered: beyond 48 in-flight per instance, the edge would return `503` with `Retry-After` for anonymous catalogue traffic while protecting checkout (Lesson 24).
19. **90 minutes later** traffic falls. Scale-in waits for 3 consecutive breaching datapoints plus a 600 s cooldown, then removes at most 10 % of the fleet per action.
20. Each terminating instance is deregistered from the LB first, drains for 120 s, fails its readiness probe, finishes in-flight requests, then exits (Lesson 24).
21. **Later that night, `us-east-1` suffers a regional network event.** Order writes begin failing.
22. Health checks at the global load balancer detect the primary region as unhealthy within ~30 s.
23. Promotion of the `us-west-2` replica to primary is **not** automatic — an engineer confirms, because an incorrect promotion during a network partition creates two primaries and split-brain writes. Confirmed promotion completes at ~4 minutes.
24. Reads and cart writes in Singapore and Europe **never stopped**, because they are region-local. Only checkout was affected — a blast radius of one function rather than the whole site, which is precisely what the per-data-type placement in Section 4.9 bought.
25. When `us-east-1` returns, it is reintroduced as a replica of `us-west-2`, never automatically re-promoted, and the divergence window is reconciled before it serves any traffic.

## 6. ASCII diagram

```
=============================================================================
 (A) AUTOSCALING IS ALWAYS LATE — the 30-second spike vs the 5-minute pipeline
=============================================================================

 traffic
 6600 ┤        ╭──────────────────────────────────────────────
      │       ╱   ◄── DEGRADED: queueing, p99 climbing, some 503s ──►
 4400 ┤      ╱
      │     ╱
 2200 ┤────╯
      └──┬────┬────┬────┬────┬────┬────┬────┬────┬────┬────┬────
       0:00 0:30 1:00 2:00 2:05 3:00 4:00 5:05 6:00 7:05 8:00

 capacity (usable slots)
 1400 ┤                                                 ╭──────   ← help arrives
      │                                                 │
 1000 ┤─────────────────────────────────────────────────╯
      └──┬────┬────┬────┬────┬────┬────┬────┬────┬────┬────┬────
        │    │    │    │    │                        │
        │    │    │    │    └ launch API call        └ serving at full speed
        │    │    │    └ alarm breaches (2nd window)
        │    │    └ 1st metric window AVERAGES the spike away → no action
        │    └ spike fully arrived
        └ spike begins

   30 s of spike  →  425 s of response.  14× too slow.
   THE FIX IS NOT A FASTER AUTOSCALER. It is headroom, scheduled scaling,
   a CDN in front, and load shedding.

=============================================================================
 (B) FLAPPING — why thresholds need a gap
=============================================================================

   10 inst @65%  ──scale in──▶  9 inst @72%  ──scale out──▶  10 inst @65%
        ▲                                                          │
        └──────────────────────────────────────────────────────────┘
   Redistribution effect = 1/(N−1) = 11 %. A 70 % in / 70 % out pair oscillates
   forever. Fix: out>70 %, in<50 % (20-point gap), 60 s out-cooldown,
   600 s in-cooldown, exclude warming instances from the average.

=============================================================================
 (C) MULTI-REGION: READ-LOCAL / WRITE-GLOBAL
=============================================================================

        SINGAPORE user                          LONDON user
             │ 4 ms                                  │ 6 ms
             ▼                                        ▼
      ┌─────────────┐                          ┌─────────────┐
      │ ANYCAST EDGE│                          │ ANYCAST EDGE│   ← TLS + CDN
      │  (CDN POP)  │                          │  (CDN POP)  │     terminate here
      └──────┬──────┘                          └──────┬──────┘
             │ backbone                               │ backbone
  ╔══════════▼═══════════╗               ╔════════════▼═════════╗
  ║   ap-southeast-1     ║               ║      eu-west-1       ║
  ║  ┌────────────────┐  ║               ║  ┌────────────────┐  ║
  ║  │ app tier (ASG) │  ║               ║  │ app tier (ASG) │  ║
  ║  └───┬────────┬───┘  ║               ║  └───┬────────┬───┘  ║
  ║ READ │        │ WRITE║               ║ READ │        │ WRITE║
  ║  3 ms▼        │      ║               ║  3 ms▼        │      ║
  ║ ┌─────────┐   │      ║               ║ ┌─────────┐   │      ║
  ║ │ REPLICA │◄──┼──────╫───┐       ┌───╫─│ REPLICA │◄──┼──────╫─┐
  ║ └─────────┘   │      ║   │       │   ║ └─────────┘   │      ║ │
  ╚═══════════════╪══════╝   │ async │   ╚═══════════════╪══════╝ │
                  │          │ repl. │                   │        │
                  │ 225 ms   │ ~400ms│                   │ 80 ms  │
                  │          │       │                   │        │
                  └──────────┼───────┼───────────────────┘        │
                             │       │                            │
                  ╔══════════▼═══════▼════════╗                   │
                  ║        us-east-1          ║───────────────────┘
                  ║   ┌───────────────────┐   ║
                  ║   │  GLOBAL PRIMARY   │   ║  ◄── ALL WRITES LAND HERE
                  ║   │  (orders, stock)  │   ║
                  ║   └─────────┬─────────┘   ║
                  ╚═════════════╪═════════════╝
                                │ SYNCHRONOUS, 70 ms  (RPO = 0)
                  ╔═════════════▼═════════════╗
                  ║        us-west-2          ║
                  ║   ┌───────────────────┐   ║
                  ║   │  STANDBY PRIMARY  │   ║ ◄── promote on regional loss
                  ║   └───────────────────┘   ║     (HUMAN-CONFIRMED: an automatic
                  ╚═══════════════════════════╝      promotion during a partition
                                                     creates TWO primaries)

  READS  : local, ~3 ms, serve 99 % of traffic        ✓
  WRITES : one cross-region hop, never two            ✓
  LOSS OF us-east-1 : reads + carts keep working; only checkout stops.
                      Blast radius = one function, not the whole site.

=============================================================================
 (D) FAILURE DOMAINS — the one at the top is the one teams forget
=============================================================================

  ┌──────────────── GLOBAL (no redundancy exists here) ────────────────┐
  │  DNS zone · TLS certs · deploy pipeline · feature flags · IAM      │
  │  identity provider · home-region directory · the BINARY itself     │
  │  ⇒ A bad deploy or an expired cert takes out EVERY region at once. │
  │  ⇒ Mitigation is NOT redundancy. It is staggered rollout, soak     │
  │     time, automatic rollback, static stability, and cell isolation.│
  │  ┌──────────── REGION ─────────┐  ┌──────────── REGION ─────────┐  │
  │  │ ┌──── AZ ────┐ ┌──── AZ ──┐ │  │ ┌──── AZ ────┐ ┌──── AZ ──┐ │  │
  │  │ │ rack│host  │ │ rack│host│ │  │ │ rack│host  │ │ rack│host│ │  │
  │  │ └────────────┘ └──────────┘ │  │ └────────────┘ └──────────┘ │  │
  │  └─────────────────────────────┘  └─────────────────────────────┘  │
  └────────────────────────────────────────────────────────────────────┘
```

## 7. Detailed example

### BookNook — Black Friday, and the year they got it right

**Year 1 — fixed capacity, reactive autoscaling, one region.**

BookNook ran 40 instances fixed, with CPU-based autoscaling (scale out above 70 % CPU) up to a maximum of 80. Black Friday traffic was expected at 3× normal.

What happened at 20:00:

```
Normal evening : 4,400 rps, 40 instances, each 50 slots → 2,000 slots
                 in-flight = 4,400 × 0.12 = 528 → 26 % slot utilisation
                 CPU reported: 22 %   (the service is I/O-bound)

Black Friday   : 13,200 rps over ~4 minutes
                 required in-flight = 13,200 × 0.12 = 1,584 → 79 % of 2,000 slots
                 queueing begins; service time degrades 0.12 s → 0.40 s
                 required in-flight at degraded latency = 13,200 × 0.40 = 5,280
                 ⇒ 264 % of available slots. The fleet is 2.6× undersized.

CPU during the meltdown: 51 %.    ◄── BELOW THE 70 % SCALE-OUT THRESHOLD.
⇒ THE AUTOSCALER NEVER FIRED. Not once. For 46 minutes.
```

Threads were blocked on the database, not burning CPU. The scaling signal was measuring the wrong thing, so the system had autoscaling *configured* and *no autoscaling in effect*. Revenue impact for the evening was significant; the post-mortem action item "increase max instances to 200" would have changed nothing.

**Year 2 — the five changes.**

1. **Changed the metric from CPU to in-flight concurrency per instance**, target 30 of 50 slots (60 %). This alone made the autoscaler functional.
2. **Added scheduled scaling**: floor raised from 40 to 130 instances at 18:00 on Black Friday, lowered at 02:00. Capacity was present *before* the traffic, so the 4-minute ramp needed no reactive action at all.
3. **Pushed the catalogue behind the CDN** (Lesson 31) with a 10-minute TTL on product fragments and `stale-while-revalidate`. Origin request volume fell ~72 %, so the 3× traffic spike became a ~1.4× origin spike.
4. **Added load shedding**: above 46 in-flight per instance, anonymous catalogue requests get `503` with `Retry-After: 5`, while `/cart` and `/orders` are never shed. Degrade the cheap path, protect revenue.
5. **Shrunk the container image** from 1.9 GB to 240 MB and pre-pulled it onto nodes. Warm-up fell from 6:10 to 2:40, which made reactive scaling genuinely useful as a second line of defence.

Result: peak of 14,100 rps served at p99 = 310 ms, no shedding triggered, 136 instances at peak. Reactive scaling fired exactly twice, adding 6 instances — the scheduled floor did almost all the work.

**Year 3 — going multi-region, and discovering it is a data problem.**

With 31 % of revenue from Europe and 14 % from Asia-Pacific, BookNook added `eu-west-1` and `ap-southeast-1`.

The compute took two weeks. The data took seven months:

- **Catalogue** (read-mostly, eventually consistent) — replicated read-only to both regions. Easy. Done in week 3.
- **Sessions** — made region-local and non-replicated; a region failover forces re-authentication, which was deemed acceptable. Easy once someone made the *product* decision.
- **Carts** — moved to home-region storage keyed by user. Required a `home_region` claim in the session token and edge routing by that claim. Six weeks.
- **Inventory** — the hard one. Inventory must not oversell, so it cannot be eventually consistent. The chosen design: inventory stays in a single global primary, but each region holds a **reserved pool** (e.g. 50 units of a title allocated to EU) that it may decrement locally, replenished asynchronously. When a pool runs low, the region falls back to a synchronous call to the global primary. This converts the common case to a local write and keeps the correctness guarantee. Four months, including the reconciliation and leak-detection work.
- **Orders** — single global primary in `us-east-1` with synchronous replication to `us-west-2`. Non-US checkout latency rose by ~225 ms for Asia-Pacific. Measured conversion impact: a 0.4 % drop, judged acceptable against the alternative (distributed transactions across regions).
- **GDPR** — EU personal data pinned to `eu-west-1`, with the analytics pipeline reworked to pseudonymise before export. Three months, mostly legal review and data-flow auditing.

**The honest summary:** moving compute to three regions was easy and cheap. Six of the seven months went into deciding, per data type, what consistency was actually required — and the single most valuable artefact produced was the table in Section 4.9.

### Contrasting example — a video-transcoding pipeline

A media company transcodes uploaded videos. The same two topics, almost inverted.

**Autoscaling is easy and nearly perfect here:**
- The work is **asynchronous and queued** (Lesson 13), so the scaling metric is unambiguous: **oldest-message age**. "Keep the oldest job under 120 seconds" is simultaneously the SLO and the autoscaling target.
- Warm-up lateness barely matters. A job waiting 5 extra minutes in a queue is invisible to users who expect "your video will be ready shortly". **The latency-insensitivity of the workload is what makes autoscaling work well** — the exact property BookNook's checkout path lacks.
- Scale-in is safe with simple rules: finish the current job, acknowledge it, then exit. Jobs are idempotent (Lesson 17), so a preempted job is just redelivered.
- Because of all of this, the fleet can run at **95 % utilisation** rather than 60 %. There is no queueing cliff to fear when queueing *is* the design.
- **Spot instances** become viable, cutting cost 60–80 %, since interruption costs at most one job's re-run.

**Multi-region is also easy, for a reason worth naming:**
- The data is **immutable**. An uploaded video never changes. Immutable data has no write conflicts, no ordering requirement and no replication lag semantics — you copy it and you are done.
- Transcoding can run in whichever region has spare capacity or cheaper spot pricing, because the work is **location-independent**.
- The only genuinely global state is a small job-metadata database with a modest write rate, which comfortably fits a single primary with cross-region replicas.

**The transferable lesson:** autoscaling and multi-region are easy in proportion to how **asynchronous**, **idempotent** and **immutable** your workload is, and hard in proportion to how much **synchronous, mutable, strongly consistent state** sits on the request path. If a design feels impossible to scale or distribute, the productive question is usually not "which autoscaler?" but **"which part of this must really be synchronous and mutable?"**

## 8. Code or configuration example

> **Illustrative only — not production-ready.** IAM, networking, encryption, observability, quotas, tagging and disaster-recovery runbooks are omitted for clarity. Numbers are typical starting points, not recommendations for your workload — measure and tune.

### Kubernetes HPA — scaling on concurrency, not CPU

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: booknook-app
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: booknook-app

  minReplicas: 12          # THE FLOOR. This is your spike absorber and your
                           # AZ-redundancy guarantee. Setting it to 1 or 2 to
                           # "save money" is how a predictable spike becomes an
                           # outage -- reactive scaling cannot arrive in time.
  maxReplicas: 200         # Guard rail against a runaway loop or a traffic flood
                           # scaling you into a surprise bill. Alert when reached.

  metrics:
  # PRIMARY SIGNAL: in-flight requests per pod (Little's Law, Lesson 14).
  # Measured by the service-mesh sidecar or the app, NOT by the OS.
  # Target 30 against a 50-slot worker pool = 60 % utilisation, leaving 40 %
  # headroom to absorb load during the ~4 minutes scaling actually takes.
  - type: Pods
    pods:
      metric:
        name: http_inflight_requests
      target:
        type: AverageValue
        averageValue: "30"

  # SECONDARY GUARD RAIL ONLY. CPU is a poor primary signal for an I/O-bound
  # service -- BookNook sat at 51 % CPU while 264 % oversubscribed on worker
  # slots. Kept high so it fires only for genuine CPU runaway.
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 85

  behavior:
    scaleUp:
      stabilizationWindowSeconds: 0     # React immediately. Being too big is cheap.
      policies:
      - type: Percent
        value: 100                      # May DOUBLE the fleet in one step ...
        periodSeconds: 60
      - type: Pods
        value: 20                       # ... but never add more than 20 pods/min,
        periodSeconds: 60               # which bounds the thundering herd of new
      selectPolicy: Max                 # pods opening DB connections at once.

    scaleDown:
      stabilizationWindowSeconds: 600   # ASYMMETRY IS THE POINT. Look back over
                                        # 10 minutes and use the HIGHEST
                                        # recommendation. Kills flapping dead.
      policies:
      - type: Percent
        value: 10                       # Remove at most 10 % of the fleet per
        periodSeconds: 120              # minute-ish. Removing 50 % at once would
      selectPolicy: Min                 # instantly overload the remainder.
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: booknook-app
spec:
  template:
    spec:
      # SCALE-IN SAFETY (Lesson 24). Without these three settings, every
      # scale-in event drops in-flight requests and users see 502s.
      terminationGracePeriodSeconds: 150   # hard SIGKILL deadline
      containers:
      - name: app
        lifecycle:
          preStop:
            exec:
              # Sleep BEFORE the app starts shutting down. This gives the load
              # balancer time to notice the pod is leaving and stop sending it
              # new requests. Skipping this is the #1 cause of 502s on deploys
              # and scale-in: the pod dies before the LB has updated.
              command: ["/bin/sh", "-c", "sleep 20"]
        readinessProbe:
          httpGet: { path: /ready, port: 8080 }
          periodSeconds: 5
          failureThreshold: 2            # ~10 s to be pulled from the LB
        startupProbe:
          httpGet: { path: /ready, port: 8080 }
          periodSeconds: 5
          failureThreshold: 30           # allow 150 s to boot WITHOUT the liveness
                                         # probe killing a slow-starting pod --
                                         # otherwise you get a crash loop that
                                         # looks like an application bug
```

### KEDA — queue-depth scaling for asynchronous workers

```yaml
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: booknook-transcoder
spec:
  scaleTargetRef:
    name: transcoder
  minReplicaCount: 0        # SCALE TO ZERO is safe here and nowhere near the
                            # request path: a queued job can wait for a cold start.
                            # NEVER do this for a synchronous user-facing service.
  maxReplicaCount: 500
  cooldownPeriod: 300       # wait 5 min of empty queue before scaling to zero
  triggers:
  - type: aws-sqs-queue
    metadata:
      queueURL: https://sqs.eu-west-1.amazonaws.com/123456789012/transcode
      # queueLength is messages-per-replica. 5 messages/pod with ~40 s per job
      # targets roughly 200 s of backlog per pod.
      queueLength: "5"
      # ApproximateNumberOfMessagesVisible counts only unclaimed work -- in-flight
      # jobs are excluded, so the autoscaler does not double-count what is already
      # being processed.
      awsRegion: eu-west-1
```

### AWS Auto Scaling group — target tracking, scheduled, and predictive together

```json
{
  "AutoScalingGroupName": "booknook-app",
  "MinSize": 12,
  "MaxSize": 200,
  "DesiredCapacity": 20,
  "HealthCheckType": "ELB",
  "HealthCheckGracePeriod": 180,

  "_comment_hc": "ELB health, not EC2 health: EC2 health only says the VM is on.",
  "_comment_grace": "180 s grace so a still-warming instance is not killed and
                     replaced in a loop -- a classic self-inflicted outage.",

  "TargetTrackingPolicy": {
    "TargetValue": 30.0,
    "CustomizedMetricSpecification": {
      "MetricName": "InFlightRequestsPerInstance",
      "Namespace": "BookNook/App",
      "Statistic": "Average"
    },
    "EstimatedInstanceWarmup": 240,
    "_comment_warmup": "EXCLUDE instances younger than 240 s from the metric
                        average. Without this, cold instances report low
                        in-flight counts, drag the average down, and the
                        autoscaler scales IN during a scale-OUT. Real bug.",
    "DisableScaleIn": false
  },

  "ScheduledActions": [
    {
      "ScheduledActionName": "black-friday-floor",
      "StartTime": "2026-11-27T18:00:00Z",
      "EndTime":   "2026-11-28T06:00:00Z",
      "MinSize": 130,
      "_comment": "THE HIGHEST-VALUE AUTOSCALING FEATURE AND THE LEAST USED.
                   Capacity is present BEFORE the traffic, so the 4-7 minute
                   reactive lag is irrelevant. Raise the FLOOR, never the
                   DesiredCapacity, so reactive scaling can still go higher."
    }
  ],

  "PredictiveScalingPolicy": {
    "MetricSpecifications": [{ "TargetValue": 30.0 }],
    "Mode": "ForecastAndScale",
    "SchedulingBufferTime": 600,
    "_comment_buffer": "Provision 10 minutes AHEAD of forecast demand, covering
                        the full boot + warm-up pipeline.",
    "MaxCapacityBreachBehavior": "IncreaseMaxCapacity",
    "_comment_mode": "Needs ~14 days of history and a REPEATING pattern.
                      Handles the shape of the day; reactive handles surprises.
                      Run both -- predictive alone is blind to novel events."
  },

  "TargetGroupAttributes": {
    "deregistration_delay.timeout_seconds": "120",
    "_comment_drain": "CONNECTION DRAINING (Lesson 24). On scale-in the LB stops
                       sending NEW requests but lets in-flight ones finish for
                       120 s. Set to 0 and every scale-in event emits 502s.",
    "slow_start.duration_seconds": "60",
    "_comment_slow": "Ramp traffic to a NEW instance over 60 s. A cold instance
                      has empty caches and an unwarmed JIT; a full share of
                      traffic on arrival makes latency WORSE before better."
  }
}
```

### Route 53 — multi-region traffic steering with health-checked failover

```hcl
# Latency-based routing: send each user to the region with the lowest measured
# latency FROM THEIR RESOLVER. Note the caveat -- this is the RESOLVER's
# location, not the user's, unless EDNS Client Subnet is supported end to end.
resource "aws_route53_record" "app_eu" {
  zone_id        = var.zone_id
  name           = "booknook.com"
  type           = "A"
  set_identifier = "eu-west-1"

  latency_routing_policy { region = "eu-west-1" }

  # WITHOUT a health check, DNS happily keeps sending users to a dead region.
  # DNS has no idea whether anything behind the address works.
  health_check_id = aws_route53_health_check.eu.id

  alias {
    name                   = aws_lb.eu.dns_name
    zone_id                = aws_lb.eu.zone_id
    evaluate_target_health = true
  }
}

resource "aws_route53_health_check" "eu" {
  fqdn              = aws_lb.eu.dns_name
  type              = "HTTPS"
  resource_path     = "/health/deep"   # DEEP check: verifies the region can
                                       # actually serve -- DB reachable, cache
                                       # reachable. A shallow "200 OK from nginx"
                                       # keeps a brain-dead region in rotation.
  request_interval  = 10               # 10 s (fast) vs the 30 s default
  failure_threshold = 3                # 3 failures => ~30 s to detect
}

# TTL REALITY CHECK: with a 60 s TTL, failover takes
#   30 s (detection) + 60 s (TTL) + resolver/app caching misbehaviour
#   = 90 s BEST CASE, with a long tail of clients stuck for 10+ minutes
#     (JVM DNS caching, ISP resolvers that ignore TTL).
# ⇒ DNS IS NOT A FAST FAILOVER MECHANISM. If you need seconds, put an
#   ANYCAST global load balancer in front (Lesson 4) and let BGP + the
#   edge's per-request health checks do the steering.
```

## 9. Numerical example

### Part A — The 30-second spike versus the 3-minute boot, costed in failed requests

**Assumptions, stated explicitly:**
- 20 instances, 50 worker slots each = **1,000 total slots**.
- Baseline 2,200 rps; mean service time 120 ms when the system is healthy.
- A marketing email triples traffic to 6,600 rps over 30 seconds.
- Metrics in 1-minute windows; 1 breaching datapoint required; instance boot 3 min; app start + health checks + LB slow start adds 2 min → **5-minute total pipeline**, plus up to 2 minutes of metric/alarm lag.
- Database service time degrades under 3× load, pushing app service time from 120 ms to 400 ms.
- M/M/1 per instance is used as a teaching approximation; it is pessimistic for a multi-slot server, but the stability condition `λ ≥ μ` it predicts is exact.

**Stage 1 — the first 60 seconds, before the database degrades:**

```
Per instance:
  arrival rate λ  = 6,600 / 20            = 330 requests/s
  service rate μ  = 50 slots / 0.120 s    = 416.7 requests/s
  utilisation ρ   = 330 / 416.7           = 0.792  (79.2 %)
  queueing wait   = 1/(μ − λ) = 1/(416.7 − 330) = 1/86.7 s = 11.5 ms
  total latency   = 120 + 11.5            = 131.5 ms

Degraded but serving. No requests lost yet.
```

**Stage 2 — the database saturates and service time rises to 400 ms:**

```
Per instance:
  service rate μ  = 50 slots / 0.400 s    = 125 requests/s
  arrival rate λ  = 330 requests/s
  λ / μ = 330 / 125 = 2.64   ⇒  λ > μ

λ exceeding μ means the queue grows WITHOUT BOUND (Lesson 14's queueing cliff).
Requests do not slow down gracefully; they time out and fail.

  Fleet throughput ceiling = 20 × 125 = 2,500 requests/s
  Offered load             = 6,600 requests/s
  Shortfall                = 4,100 requests/s
```

**Stage 3 — how long the shortfall lasts (the autoscaler timeline from Section 4.4):**

```
t = 0:00  spike begins
t = 1:00  first metric window closes — it AVERAGES 30 calm seconds with 30 hot
          seconds, reporting ~55 % against a 60 % target ⇒ NO ACTION
t = 2:00  second window closes, fully inside the spike ⇒ alarm breaches
t = 2:05  launch API call issued
t = 5:05  instances booted                     (the "3-minute boot")
t = 7:05  app started, healthy, LB ramp done   ⇒ capacity finally usable

Time from spike start to usable capacity = 425 s
Spike establishment time                 =  30 s
Ratio                                    = 425 / 30 = 14.2× too slow
```

**Stage 4 — the damage:**

```
Failed/timed-out requests = shortfall × duration
                          = 4,100 req/s × 425 s
                          = 1,742,500 requests

At a 2.1 % conversion rate and a £24 average order value, if even 5 % of those
were purchase-intent sessions rather than catalogue browsing:
  1,742,500 × 0.05 × 0.021 × £24 ≈ £43,900 of lost orders from one email send.
```

**Stage 5 — what headroom would have cost instead:**

```
To hold 6,600 rps at 120 ms with 60 % slot utilisation:
  in-flight needed = 6,600 × 0.120 = 792
  slots needed     = 792 / 0.60    = 1,320
  instances        = ceil(1,320/50) = 27  (vs the 20 actually running)

Extra instances = 7, running 24/7:
  7 × $0.096/h × 730 h = $490.56 per month

Or, because the send time was KNOWN:
  scheduled scaling to 27 instances for a 2-hour window
  = 7 × $0.096 × 2 = $1.34 for the event.
```

**$1.34 of scheduled capacity versus roughly £44,000 of lost orders.** The conclusion is not "tune the autoscaler". It is: **reactive autoscaling is a trend-follower, and spikes must be met with capacity you already own, a CDN that absorbs them, or load shedding that bounds the damage.**

### Part B — Fixed versus autoscaled capacity, fully costed

**Assumptions:**
- Instance price $0.096/hour on demand; 3-year commitment price $0.0576/hour (40 % discount).
- 730 hours/month; 30.4 days/month.
- Demand profile (instances required at a 60 % utilisation target):

| Hours (UTC) | Duration | Instances required |
|---|---|---|
| 00:00–06:00 | 6 h | 8 |
| 06:00–10:00 | 4 h | 20 |
| 10:00–16:00 | 6 h | 30 |
| 16:00–22:00 | 6 h | 40 |
| 22:00–24:00 | 2 h | 16 |

```
Daily instance-hours actually demanded
  = (6×8) + (4×20) + (6×30) + (6×40) + (2×16)
  =  48    +  80    + 180    + 240    + 32
  = 580 instance-hours/day
Average instance count = 580 / 24 = 24.2

Fixed-at-peak provisioning
  = 40 instances × 24 h = 960 instance-hours/day
```

**Autoscaling cannot achieve the theoretical 580.** Two real corrections:

```
(1) A minimum floor of 12 (spike absorption + AZ redundancy), not 8:
    adds (12 − 8) × 6 h = 24 instance-hours/day      → 604
(2) Control-loop overshoot: cooldowns, integer step sizes and warm-up
    exclusion mean the autoscaler sits ~10 % above true demand on average:
    604 × 1.10 = 664.4 instance-hours/day
```

Monthly totals:

```
Fixed      : 960   × 30.4 = 29,184 instance-hours
Autoscaled : 664.4 × 30.4 = 20,198 instance-hours
Reduction in instance-hours = 1 − 20,198/29,184 = 30.8 %
```

Four costing options:

| Option | Instance-hours | Rate | Monthly cost | Saving vs Option 1 |
|---|---|---|---|---|
| **1. Fixed 40, all on-demand** | 29,184 | $0.096 | **$2,801.66** | — |
| **2. Fixed 40, 3-yr commitment on all 40** | 29,184 | $0.0576 | **$1,680.99** | $1,120.67 (40.0 %) |
| **3. Autoscaled, all on-demand** | 20,198 | $0.096 | **$1,939.01** | $862.65 (30.8 %) |
| **4. Autoscaled + commit the 12-instance baseline** | 8,755 committed + 11,443 on-demand | $0.0576 / $0.096 | **$1,602.84** | $1,198.82 (42.8 %) |

Option 4 arithmetic:

```
Committed baseline : 12 × 24 × 30.4 = 8,755 instance-hours × $0.0576 =   $504.29
On-demand remainder: 20,198 − 8,755 = 11,443 instance-hours × $0.096 = $1,098.53
Total                                                                 = $1,602.82
```

**Now the term everyone omits — engineering cost:**

```
Initial build and tuning : 60 engineer-hours
Ongoing maintenance      : 12 engineer-hours/year
Loaded cost              : $120/engineer-hour

3-year total = (60 × 120) + (3 × 12 × 120) = $7,200 + $4,320 = $11,520
Amortised    = $11,520 / 36 months = $320/month

Net saving of Option 4 vs Option 1 = $1,198.82 − $320 = $878.82/month  ✓ worth it
```

**The crossover — when autoscaling stops paying:**

```
Saving scales roughly with fleet size:
  $1,198.82 / 40 peak instances = $29.97 per peak instance per month
Engineering cost is roughly fixed at $320/month.

Break-even peak fleet size = $320 / $29.97 = 10.7 instances

⇒ Below roughly 11 peak instances on this demand shape, autoscaling COSTS
  MORE than it saves. Build it anyway only if you need it for AVAILABILITY
  (instance replacement) rather than cost — which is a legitimate reason.
```

**And the case where autoscaling saves nothing at all:**

```
Flat workload, 40 instances required 24/7:
  Fixed      = 29,184 instance-hours
  Autoscaled = 29,184 instance-hours (the autoscaler never moves)
  Saving     = $0
  Engineering cost = $320/month  ⇒ a pure loss.
```

Autoscaling's value is **entirely** a function of the peak-to-trough ratio of your demand. State that ratio before proposing it.

### Part C — Availability: single region versus multi-region, with correlated failures

**Assumptions, stated explicitly:**
- Each region is already multi-AZ internally, so the figure below is *residual regional* failure only.
- Regional unavailability `p = 5 × 10⁻⁴` (99.95 %) — approximately one 4–5 hour event per year.
- `c = 0.20`: 20 % of outage probability comes from **correlated/global causes** — a bad binary rolled everywhere, an expired certificate, a DNS or IAM failure, a global config push. This is a *typical* figure for an organisation with staggered-but-fast deploys; measure your own from post-mortems.
- `f = 0.95`: failover succeeds 95 % of the time. Untested failover is worse; `0.80–0.95` is realistic for a procedure exercised once or twice a year.
- Successful failover still takes 5 minutes (detection + DNS/steering + promotion).
- 525,600 minutes per year.

**C1 — Single region:**

```
U_single = 5 × 10⁻⁴
Downtime = 5e-4 × 525,600 = 262.8 min/year  = 4 h 23 min
Availability = 99.950 %
```

**C2 — Two regions, the naive calculation everybody does first:**

```
U_naive = p² = (5e-4)² = 2.5 × 10⁻⁷
Downtime = 2.5e-7 × 525,600 = 0.131 min/year = 7.9 seconds/year
Availability = 99.999975 %   ("six nines!")
```

**This number is wrong, and dangerously so**, because it assumes the two regions fail independently and that failover always works instantly. Neither is true.

**C3 — Two regions, decomposed honestly:**

```
Split the regional failure probability into correlated and independent parts:
  p_corr  = c × p       = 0.20 × 5e-4 = 1.0 × 10⁻⁴   (hits BOTH regions)
  p_indep = (1−c) × p   = 0.80 × 5e-4 = 4.0 × 10⁻⁴   (hits one region only)

Term 1 — correlated failure takes out both regions:
  U₁ = p_corr                                   = 1.000 × 10⁻⁴

Term 2 — both regions fail independently at once:
  U₂ = p_indep²        = (4e-4)²                = 1.600 × 10⁻⁷

Term 3 — one region fails independently and FAILOVER DOES NOT WORK:
  P(exactly one region down) = 2 × 4e-4 × (1 − 4e-4) = 7.997 × 10⁻⁴
  U₃ = 7.997e-4 × (1 − f) = 7.997e-4 × 0.05     = 3.999 × 10⁻⁵

Term 4 — failover works, but takes 5 minutes each time:
  Expected single-region events per year, assuming 2.5 h mean duration:
    (4e-4 × 8,760 h) / 2.5 h = 1.40 events/year per region
    × 2 regions = 2.80 events/year
  Downtime = 2.80 × 5 min = 14.0 min/year
  U₄ = 14.0 / 525,600                            = 2.663 × 10⁻⁵

TOTAL U_two_region = 1.000e-4 + 1.600e-7 + 3.999e-5 + 2.663e-5
                   = 1.666 × 10⁻⁴
Downtime = 1.666e-4 × 525,600 = 87.6 min/year
Availability = 99.9833 %
```

**Where the downtime actually comes from:**

| Term | Contribution | Share |
|---|---|---|
| Correlated / global failures | 1.000 × 10⁻⁴ | **60.0 %** |
| Failover fails when needed | 3.999 × 10⁻⁵ | 24.0 % |
| Failover execution time | 2.663 × 10⁻⁵ | 16.0 % |
| **Both regions fail independently** | 1.600 × 10⁻⁷ | **0.10 %** |

**Read that table carefully. The term the naive calculation modelled — both regions failing independently — contributes one thousandth of the real downtime.** 99.9 % of your multi-region downtime comes from the three things the naive model ignores entirely.

**C4 — The honest comparison:**

```
Single region : 262.8 min/year  (99.950 %)
Two regions   :  87.6 min/year  (99.9833 %)
Improvement   : 262.8 / 87.6 = 3.0×   — NOT the 2,000× the naive maths promised

Cost          : ~1.8–2.0× infrastructure, plus months of data-tier work (Section 7)
A 99.99 % target is 52.56 min/year.  87.6 > 52.56.
⇒ TWO REGIONS DID NOT REACH FOUR NINES.
```

**C5 — What actually buys the nines:**

```
Improve the two inputs that dominate, without adding a third region:
  c: 0.20 → 0.05  via staggered regional deploys with soak time, automatic
                  rollback, cell-based isolation, and static stability
  f: 0.95 → 0.999 via quarterly failover game days and automated runbooks

  p_corr  = 0.05 × 5e-4 = 2.5 × 10⁻⁵
  p_indep = 0.95 × 5e-4 = 4.75 × 10⁻⁴

  U₁ = 2.500 × 10⁻⁵
  U₂ = (4.75e-4)²                              = 2.256 × 10⁻⁷
  U₃ = 2 × 4.75e-4 × (1−4.75e-4) × 0.001       = 9.496 × 10⁻⁷
  U₄ = (4.75e-4 × 8,760 / 2.5) × 2 × 5 / 525,600
     = (1.664 events/region/yr × 2 × 5 min) / 525,600 = 3.166 × 10⁻⁵
  TOTAL = 2.500e-5 + 2.256e-7 + 9.496e-7 + 3.166e-5 = 5.783 × 10⁻⁵

Downtime = 5.783e-5 × 525,600 = 30.4 min/year
Availability = 99.9942 %   ⇒ COMFORTABLY INSIDE 99.99 %
Improvement over single region = 262.8 / 30.4 = 8.6×
```

**Same two regions. Same hardware. Same money. 87.6 minutes became 30.4 minutes** purely by reducing correlation and testing the failover. This is the single most important quantitative result in this lesson: **multi-region availability is bought with deployment discipline and failover testing, not with a second region.** A second region you never fail over to is an expense, not a redundancy.

### Part D — A cross-region latency budget for checkout

**Assumptions:** Singapore user; edge POP 4 ms RTT away; `ap-southeast-1` app 2 ms from the POP; global order primary in `us-east-1` at 225 ms RTT from Singapore; synchronous order replication `us-east-1` → `us-west-2` at 70 ms RTT; payment service provider reachable in 20 ms RTT from `us-east-1` with 400 ms of its own processing; user-tolerable checkout budget 1,000 ms.

**D1 — The naive design, with two cross-region crossings:**

```
TLS 1.3 session resumption to edge (1 RTT)            4 ms
Edge → ap-southeast-1 app                             2 ms
Cart read (local replica)                             3 ms
Inventory reserve from local pool (local write)       6 ms
CROSSING 1: order write SG → us-east-1              225 ms
            + primary processing                     12 ms
            + synchronous replication to us-west-2   70 ms
CROSSING 2: payment auth SG → PSP via us-east-1     225 ms
            + PSP processing (20 ms RTT + 400 ms)   420 ms
Confirmation render                                  10 ms
                                                   -------
TOTAL                                               977 ms
Of which pure distance: 225 + 225 = 450 ms = 46 % of the budget
```

Inside the 1,000 ms budget, but with only 23 ms of margin — one retry, one GC pause or one congested transpacific path and it fails.

**D2 — The fixed design: orchestrate in `us-east-1`, one crossing only:**

```
TLS 1.3 session resumption to edge                    4 ms
Edge → ap-southeast-1 app                             2 ms
Cart read (local replica)                             3 ms
Inventory reserve from local pool (local write)       6 ms
CROSSING: SG → us-east-1                            225 ms
   inside us-east-1, no further crossings:
     payment auth (20 ms RTT + 400 ms PSP)          420 ms
     order insert + synchronous repl to us-west-2    82 ms
Confirmation render                                  10 ms
                                                   -------
TOTAL                                               752 ms
Saved = 977 − 752 = 225 ms (23 %) by REMOVING ONE ROUND TRIP
Margin to budget = 248 ms
```

**D3 — The comparison that sets the design rule:**

```
Same flow for a us-east-1 user:
  4 + 2 + 3 + 6 + 12 (local primary) + 420 + 70 + 10 = 527 ms

Singapore penalty = 752 − 527 = 225 ms = +42.7 %
                  = exactly ONE cross-region round trip.

A third crossing would add another 225 ms → 977 ms, leaving 23 ms of margin.
A fourth → 1,202 ms ⇒ OVER BUDGET.

DESIGN RULE: you can afford ONE cross-region round trip per user action.
Any design with two sequential crossings must be restructured — batch the
calls, parallelise them, or move the orchestration into the remote region.
```

Note what this rule implies about reads: at 225 ms per crossing, **no read may cross a region on the request path**, which is the entire justification for read-local/write-global and for local replicas.

## 10. Trade-offs

**Autoscaling**
- *Advantages:* tracks demand, so you stop paying for the peak 24/7 (typically 25–45 % on a diurnal workload); automatically replaces failed instances; makes capacity planning a target rather than a forecast; absorbs gradual growth with no human action.
- *Disadvantages:* **always late** (4–7 minutes), so it does not protect against spikes; scale-in can drop requests if draining is wrong; flapping wastes money and hurts latency; it can overload the database behind it; it adds a control loop that must itself be monitored and can fail in both directions.
- *Operational cost:* metric instrumentation, threshold and cooldown tuning, graceful-shutdown handling, warm-up tuning, and roughly $320/month-equivalent of engineering time amortised.
- *Unnecessary complexity when:* demand is flat (saving = zero), or the fleet peaks below ~11 instances, or the real bottleneck is a single database that adding app servers cannot help.

**Multi-region**
- *Advantages:* survives a regional failure; serves reads near users (100–200 ms saved for distant users); satisfies data-residency law; forces the architectural discipline — explicit consistency requirements per data type — that improves the system regardless.
- *Disadvantages:* ~1.8–2× infrastructure cost; cross-region data transfer charges; every data decision becomes a consistency decision; the realistic availability gain is ~3× rather than the ~1,000× the naive maths suggests; debugging, deployment and on-call all get harder; and **a region you never fail over to does not work**.
- *Operational cost:* failover game days, cross-region observability, staggered deployment pipelines, replication-lag monitoring, and a reconciliation story for every asynchronously replicated dataset.
- *Unnecessary complexity when:* you are not yet reliably multi-AZ; when your availability target is 99.9 % (8.8 h/year), which a well-run single region meets; when you have no users outside one continent; when there is no legal requirement.

**The ordered ladder of availability spend**, cheapest-return-first:
1. Multi-AZ within one region (large gain, ~1.0–1.2× cost).
2. Staggered deploys with automatic rollback (large gain, near-zero infrastructure cost — this reduces `c`, which Part C showed dominates everything).
3. Load shedding and graceful degradation (large gain, low cost).
4. Tested failover to a warm standby region (moderate gain, ~1.4× cost).
5. Full active–active (moderate gain, ~2× cost plus months of data work).

**Most teams attempt step 5 before step 2**, which is why they spend 2× and still miss their availability target.

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| Autoscaler scales on CPU for an I/O-bound service | Site becomes slow and then fails during a traffic surge while the fleet never grows; dashboards look "fine" at 51 % CPU | Latency and in-flight concurrency climb while CPU stays flat; scaling-activity log is empty during the incident | Change the metric to in-flight concurrency per instance; keep CPU only as a high guard rail |
| Reactive scaling against a sudden spike | 4–7 minutes of timeouts and 503s beginning the moment the spike lands | Error rate spikes ~5 minutes before the scaling activity appears in the log | Headroom (target 60 %, not 85 %), scheduled scaling for known events, CDN absorption, load shedding |
| Scale-in with no connection draining | Random 502s during quiet periods, which is baffling because load is *low* | 502 count correlates exactly with scale-in events, not with traffic | `deregistration_delay` 120 s, a `preStop` sleep, readiness failing before shutdown (Lesson 24) |
| Flapping between two thresholds | Elevated p99 every few minutes as traffic hits cold instances; cost rises rather than falls | Scaling-activity log alternates in/out indefinitely at constant traffic | Hysteresis gap wider than `1/(N−1)`, asymmetric cooldowns (60 s out, 600 s in), warm-up exclusion |
| Scale-out exhausts the database connection pool | A scale-out event causes a *site-wide* outage — the opposite of its intent | DB connection count jumps with instance count; "too many connections" errors | Connection pooler (PgBouncer/RDS Proxy), per-instance pool caps, cap the scale-out step size |
| Cold instances drag the metric average down | The autoscaler scales **in** during a scale-**out**, oscillating while load rises | Desired capacity falls while load rises | `EstimatedInstanceWarmup` / HPA warm-up exclusion; slow start on the LB |
| Max capacity reached during a genuine surge | Hard ceiling: queueing, shedding and timeouts despite "autoscaling being on" | Desired capacity pinned at max in the scaling log | Alert on *reaching* max, not just on breaching it; raise limits ahead of known events; verify account/instance quotas |
| Capacity unavailable in the target region | Launches fail with an insufficient-capacity error, often **during** a regional event when everyone else is failing over too | Launch-failure rate in the scaling log | Multiple instance types and AZs in the launch template; pilot-light designs must pre-provision, not assume |
| Region fails with untested failover | Total outage lasting hours while engineers discover the standby has stale schema, missing secrets, or no capacity | The outage itself; the runbook does not work | Quarterly game days with real traffic; continuously exercise the standby by serving some production traffic from it |
| Automatic cross-region promotion during a network partition | Split brain — two primaries accept conflicting writes; some orders vanish on reconciliation | Divergence detected at reconciliation, often hours later | Human-confirmed promotion, or quorum-based election with a fencing token; never promote on a single health signal |
| Replication lag spikes, then the primary region dies | Orders placed in the last `lag` seconds are permanently lost (RPO > 0) | Replication-lag metric; customer reports of missing orders | Synchronous replication for the data that must not be lost; alert when lag exceeds the RPO target, and shed writes rather than widen the gap |
| Bad binary deployed to all regions simultaneously | Global outage — exactly what multi-region was meant to prevent | Error rate rises in every region within minutes of a deploy | Staggered regional rollout with soak time and automatic rollback; this is the `c` term in Part C |
| TLS certificate expires | Every region fails at the same instant with handshake errors | Certificate-expiry monitoring (if it exists); otherwise a total outage | Automated renewal with alerting at 30/14/7 days; treat certificates as a global failure domain |
| GeoDNS failover blocked by resolver caching | A tail of users keeps hitting the dead region for 10+ minutes after failover "completed" | Traffic to the failed region does not reach zero after the TTL | Anycast edge rather than DNS as the failover mechanism; shorter TTLs help but do not solve it |
| Home-region directory unavailable | **Total global outage** — no request can be routed anywhere | Every region reports routing failures while itself healthy | Make the directory tiny, replicated everywhere, aggressively cached, and **fail static**: use the last known mapping when it is unreachable |

## 12. Alternatives

| Approach | What it does | Pros | Cons | Best for |
|---|---|---|---|---|
| **Fixed capacity at peak** | Provision for the maximum and leave it | Simplest possible; no control loop; instantly spike-proof | Pays peak price 24/7; still needs manual growth planning | Flat demand; small fleets (< ~11 peak instances); regulated environments where change is expensive |
| **Reactive autoscaling** | Act on a breached metric | Handles the unexpected; needs no forecast | 4–7 min late; can flap; can overload downstream | Gradual trends, unpredictable but slow-moving load |
| **Scheduled scaling** | Capacity by the clock | Zero lag; trivially understandable; near-zero cost | Wrong when reality deviates from the calendar | Known events — campaigns, launches, market open, batch windows. **Pair with reactive as the safety net** |
| **Predictive scaling** | Forecast from history, provision ahead | Automates scheduled scaling; captures seasonality | Needs ~2 weeks of repeating pattern; blind to novel events | Stable diurnal/weekly workloads |
| **Serverless (functions)** | Platform scales per concurrent request | Scales in ~100 ms–2 s; no idle cost; no capacity planning | Cold starts; higher per-request price at scale; execution limits; database connection pressure | Spiky, bursty, low-average workloads; event handlers; glue |
| **Queue + worker pull** | Workers take work only when free | **Perfect** load distribution; scale-in is trivially safe; spot-instance friendly; scales to 95 % utilisation | Only for asynchronous work (Lesson 13; **Phase 4 — message queues**) | Batch, transcoding, indexing, email, report generation |
| **Overprovision + load shedding** | Fixed headroom plus controlled rejection | Bounded, predictable failure; protects revenue paths | Some users are rejected by design | Any system where partial service beats total collapse — pairs with everything above |
| **CDN / edge caching** (Lesson 31) | Serve from the edge; never reach the origin | Absorbs flash crowds instantly at any scale; also fixes global read latency | Only for cacheable content; invalidation complexity (Lesson 29) | **The first thing to try for both spikes and global latency** |
| **Multi-AZ single region** | Redundancy inside one region | Large availability gain at ~1.0–1.2× cost; no consistency problems | Does not survive a regional event or serve distant users | **The correct default for the overwhelming majority of systems** |
| **Active–passive multi-region** | Warm standby, failover on disaster | Meaningful DR at ~1.4× cost | RPO > 0; failover rots unless tested | Regulatory DR requirements; RTO in minutes–hours |
| **Active–active multi-region** | All regions serve traffic | Local latency everywhere; no failover to "activate" | ~2× cost; every data decision becomes a consistency decision | Global user base with a real latency or availability mandate |
| **Globally distributed database** (Spanner, CockroachDB, YugabyteDB, DynamoDB global tables) | The database solves cross-region consistency for you | Removes the hardest part of multi-region from your application | Write latency bounded by the second-fastest region (~90 ms+); cost; lock-in; still needs residency design | Teams that would otherwise build conflict resolution by hand — **often the right buy-versus-build call** |

That last row deserves the same emphasis Lesson 23 gave to queue-based pull. **If the data tier is the hard part, consider buying a data tier that has already solved it.** The engineering cost of hand-rolling cross-region consistency (BookNook's seven months in Section 7) frequently exceeds the licence cost of a database designed for it.

## 13. When to use it

**Autoscaling:**

1. **Demand has a high peak-to-trough ratio** — a 5:1 or greater daily swing. Below about 2:1 the savings do not pay for the complexity.
2. **The tier is stateless** (Lesson 20) and sits behind a load balancer, so instances are interchangeable and removal is safe.
3. **The workload is asynchronous and queued.** Worker pools scaled on queue age are the best case in every respect: perfect signal, latency-insensitive, safe scale-in, spot-friendly, 95 % utilisation.
4. **Growth is unpredictable but gradual** — a new product whose steady-state size you cannot forecast.
5. **You need automatic instance replacement**, even at constant capacity. An autoscaling group with `min = max` is a legitimate and common pattern purely for self-healing.
6. **You have a known event** — use *scheduled* scaling, which is the highest-value and least-used variant.

**Multi-region:**

1. **Availability target above ~99.95 %** with a genuine business cost to multi-hour outages — *and* only after multi-AZ, staggered deploys and load shedding are already in place.
2. **A substantial user base more than ~80 ms away** from your single region. Serving reads locally is the single largest latency improvement available to such users.
3. **A legal data-residency requirement.** This one is categorical: it is not an optimisation and it is not negotiable.
4. **Regulatory DR mandates** with specific RTO/RPO numbers (common in finance, health and government) — these frequently require a *tested*, documented failover to a geographically separate region.
5. **The workload is naturally partitionable by user geography**, so home-region placement gives local reads *and* local writes with no cross-region writes at all. This is the best-case multi-region design and should be chosen deliberately at the outset.
6. **The data is immutable or append-only** (media, logs, events), which removes write conflicts entirely and makes multi-region nearly free.

## 14. When *not* to use it

**Autoscaling:**

1. **Flat demand.** The autoscaler never moves, you save nothing, and you have added a control loop that can fail. Use a fixed fleet with instance replacement.
2. **Sudden spikes as the primary risk.** Autoscaling is 4–7 minutes late; a 30-second spike will beat it every time. Buy headroom, schedule capacity, cache at the edge, and shed load.
3. **Stateful tiers** — databases, caches holding unique data, consistent-hash ring members (Lesson 38), queue consumers with uncommitted state. Automatic scale-in triggers membership changes and data movement. Scale these deliberately and with human approval.
4. **When the bottleneck is downstream.** If the database is saturated, adding app servers adds connections and makes it worse. Autoscaling cannot fix a bottleneck it is not part of.
5. **Small fleets.** Below about 11 peak instances on a typical diurnal profile, the engineering cost exceeds the saving.
6. **Latency-critical paths with slow warm-up.** A service needing 6 minutes to warm its caches should run a stable fleet; churning it degrades the very latency you care about.

**Multi-region:**

1. **Before you are reliably multi-AZ.** Multi-AZ is far cheaper, far simpler and removes the most common failure (one data centre). Skipping it to build multi-region is building the roof before the walls.
2. **For a 99.9 % target.** 8.8 hours/year is achievable in one well-run region. Multi-region to reach 99.9 % is spending 2× for a target you already meet.
3. **When all your users are in one geography and there is no legal requirement.** You are paying for latency improvements nobody experiences.
4. **When you will not test failover.** Part C quantified it: an untested failover (`f = 0.80–0.95`) contributes more downtime than the independent-failure term by a factor of hundreds. **An untested standby region is a line item, not a redundancy.**
5. **When the data tier cannot be made region-aware.** If every request requires a strongly consistent read of globally mutable state, multi-region adds a 225 ms crossing to every request and makes the product *worse*. Fix the data model first.
6. **As a response to a single outage.** The post-incident reflex "we need multi-region" usually mis-diagnoses the cause. Check first whether the outage was correlated/global — a bad deploy, an expired certificate, a config push — in which case a second region would have failed too, and the fix is deployment discipline.

## 15. Common mistakes

**Beginner mistakes**
- Scaling on CPU for an I/O-bound service, producing an autoscaler that is configured but never fires.
- Expecting autoscaling to handle a flash sale or a marketing email, with no headroom, no schedule and no CDN.
- Setting `min` to 1 or 2 "to save money", so the first spike arrives against a fleet that cannot absorb it and cannot grow in time.
- Setting the scale-out and scale-in thresholds to the same value, guaranteeing flapping.
- Omitting connection draining, then being baffled by 502s that correlate with low traffic.
- Using a liveness probe where a readiness probe is needed, so slow-starting instances are killed in a restart loop (Lesson 24).
- Believing two regions multiply availability — quoting `p²` without the correlation, failover-success and failover-duration terms.
- Treating a standby region as "done" once it is created, and never failing over to it.
- Assuming DNS failover is fast. It is 90 seconds at best and has a 10-minute tail.
- Replicating *everything* to *every* region because it sounds safer, thereby creating both a residency violation and an unbounded cost.

**Experienced-engineer mistakes**
- Scaling out the app tier straight into a database connection limit, converting a load problem into an outage.
- Not excluding warming instances from the scaling metric, so the autoscaler scales in while load is rising.
- Target-tracking with no cap on the scale-in step, so a drop from 40 to 20 instances in one action instantly overloads the survivors.
- Running HPA and VPA on the same metric for the same workload and watching them fight.
- Building active–active before fixing deployment staging — then shipping a bad binary to all regions simultaneously and taking a global outage, which is exactly the failure mode multi-region was purchased to prevent.
- Automating cross-region primary promotion on a single health signal, producing split brain during a network partition.
- Designing a flow with two sequential cross-region calls, doubling the unavoidable latency; the fix is to move the orchestration into the remote region.
- Letting a global dependency — the home-region directory, a licence service, a feature-flag service — become a hard synchronous requirement on the request path, recreating a single global failure domain inside a multi-region architecture.
- Measuring replication lag but never alerting on it against the stated RPO, so the actual RPO is discovered during the outage.
- Picking a region pair for cost rather than for correlation: two regions on the same provider backbone, sharing a control plane and a deployment pipeline, are far less independent than the price list implies.
- Forgetting that cross-region data transfer is charged per gigabyte, so a chatty replication design produces a surprise bill larger than the compute it protects.
- Failing to plan for capacity exhaustion in the surviving region — if everybody fails over at once, the instances you planned to launch may not exist.

## 16. Interview perspective

These two topics appear in nearly every senior system-design interview, and both are usually answered with slogans. Specific numbers are what distinguish a strong answer.

**How to use it:**

1. **Make the metric choice explicitly and justify it.** *"I'd scale on in-flight requests per instance, not CPU — this service is I/O-bound, so it can be fully saturated on worker slots at 20 % CPU and the autoscaler would never fire."* This single point is the highest-value thing you can say about autoscaling.
2. **State the lag out loud.** *"Reactive autoscaling takes four to seven minutes end to end — a metric window, alarm evaluation, boot, app start, health checks and LB ramp. So it handles trends, not spikes."* Then give the actual defence: headroom, scheduled scaling, CDN, load shedding.
3. **Use Little's Law to size the fleet.** *"At 4,400 rps and 120 ms, fleet concurrency is 528. At 30 in-flight per instance — 60 % of a 50-slot pool — that's 18 instances."* Deriving the number rather than guessing it is strongly differentiating.
4. **Justify the headroom.** *"I target 60 %, not 85 %, because the queueing cliff means latency explodes between 90 % and 100 %, and because the 40 % headroom is what carries me through the minutes scaling actually takes."*
5. **Address scale-in safety unprompted.** *"Deregister first, drain for 120 seconds, fail readiness, finish in-flight work, then terminate. Without that, every scale-in event emits 502s."*
6. **Mention flapping and asymmetric cooldowns.** *"Scale out fast on one datapoint, scale in slowly over ten minutes — being too big is cheap, being too small is an outage."*
7. **For multi-region, lead with the driver.** *"Is this latency, availability or data residency? They want different architectures, and designing for all three at once produces something nobody can operate."*
8. **Say 'the hard part is the data tier' and then prove it** with a per-data-type table: catalogue replicated everywhere, carts home-region, orders single global primary, sessions region-local and disposable.
9. **Do the availability maths honestly.** *"Naively two regions give you `p²` — about eight seconds a year. Realistically, 20 % of failures are correlated — a bad deploy, an expired cert, DNS — and failover succeeds maybe 95 % of the time. That puts you around 88 minutes a year, a 3× improvement, not a thousand-fold one. And you still miss 99.99 %. The way to actually hit four nines is to cut the correlated fraction with staggered deploys and raise failover reliability with game days."* **This answer alone will put you in the top few percent of candidates on this topic.**
10. **Apply the one-crossing rule.** *"Singapore to us-east-1 is about 225 ms. You get one cross-region round trip per user action. Two sequential crossings is a broken design — move the orchestration into the remote region."*
11. **Know the steering mechanisms and their limits.** *"GeoDNS for coarse placement, but it's not a failover mechanism — TTL plus resolver caching gives a ten-minute tail. For fast failover, anycast at the edge with per-request health checks."*
12. **Name the cheaper alternative before the expensive one.** *"Before active–active, I'd want multi-AZ, staggered deploys with automatic rollback, and load shedding. Those three reduce the dominant correlated-failure term at a fraction of the cost."*

**Red flags interviewers watch for:**
- "We'll just autoscale" as the answer to a spike, with no mention of warm-up time.
- Scaling on CPU without a word about whether the service is CPU-bound.
- No awareness that scale-in can drop requests, or that draining exists.
- Claiming two regions give "five nines" or "six nines" by multiplying probabilities, with no correlation term.
- Treating multi-region as a compute problem and never mentioning the data tier, consistency, replication lag or RPO.
- Proposing active–active multi-master without being asked how write conflicts are resolved.
- Proposing automatic cross-region promotion without acknowledging split brain.
- Never mentioning that failover must be tested, or that a passive region rots.
- Reaching for multi-region before multi-AZ, or before the availability target even requires it.
- Ignoring cost entirely — or claiming autoscaling "saves money" without reference to the peak-to-trough ratio.
- Designing a global flow with two or three sequential cross-region calls and not noticing the latency.
- No mention of data residency in a design that explicitly has European or Chinese users.

## 17. Knowledge check

Write your answers down before continuing.

**Conceptual**

1. BookNook's app tier is autoscaled on CPU with a 70 % scale-out threshold, a maximum of 200 instances, and a fleet that routinely serves peak traffic at 22 % CPU. During a 3× traffic event the service becomes unusable for 46 minutes and the autoscaler never takes a single action. Explain the mechanism in full: why CPU reported a low value while the service was saturated, what metric would have reported the truth and why, and how Little's Law lets you derive the correct target value for that metric from the worker-pool size and a utilisation goal. Then explain why raising the maximum instance count — the obvious post-mortem action item — would have changed nothing.

2. Explain precisely why reactive autoscaling cannot protect a service against a spike that establishes itself in 30 seconds. Break the total response time into its named stages and state a typical duration for each. Then describe **four** different defences that *do* work, and for each one state what it costs and what class of spike it fails to handle.

3. A colleague presents this calculation: "Each region is 99.95 % available. Two regions give 1 − 0.0005² = 99.999975 %, so going multi-region takes us from 4 hours 23 minutes of annual downtime to under 8 seconds." Identify every assumption in that calculation that does not hold in practice. For each one, name the real-world mechanism that violates it, and state whether it makes the true figure better or worse. Then explain which single term dominates real multi-region downtime, and what investment reduces it — noting that the investment is *not* a third region.

**Practical scenario**

4. BookNook must add a European presence. Requirements: EU customers' personal data must remain in the EU for GDPR; EU page loads must be under 300 ms; the company must survive the loss of either region; and inventory must never oversell. Current state is a single `us-east-1` region, multi-AZ, with one PostgreSQL primary, a Redis cache, an autoscaled stateless app tier, and a CDN already serving static assets.
   Produce the design. For each of these six datasets — book catalogue, product images, user sessions, shopping carts, inventory counts, orders and payments — state where it lives, how it is replicated, what consistency model applies, and what the user experiences when `us-east-1` is lost. Then state your traffic-steering mechanism and justify it against the alternative; state your RTO and RPO per dataset; identify every remaining **global** failure domain in your design and what you would do about each; and finally, name the one part of this project that will take the longest and explain why.

**Calculation**

5. BookNook's search tier serves a demand profile of 6 instances for 8 hours, 18 instances for 8 hours, and 36 instances for 8 hours each day. Instances cost $0.11/hour on demand and $0.066/hour on a 3-year commitment. Each instance has 40 worker slots; mean service time is 180 ms; the utilisation target is 55 %. A month is 730 hours and 30.4 days. Engineering cost for autoscaling is 50 hours to build plus 10 hours/year to maintain, at $130/loaded engineer-hour, amortised over 3 years. Assume the autoscaler runs 12 % above true demand because of cooldowns and integer step sizes, and that the minimum floor must be 9 instances rather than 6.
   (a) What request rate can one instance sustain at the 55 % utilisation target? Use Little's Law and show every step. What peak request rate does the 36-instance peak correspond to?
   (b) Compute monthly instance-hours and cost for: fixed-at-peak on demand; fixed-at-peak fully committed; autoscaled on demand; and autoscaled with the 9-instance floor committed and the remainder on demand. Rank them.
   (c) Including the amortised engineering cost, what is the net monthly saving of the best autoscaled option versus fixed-at-peak on demand? At what peak fleet size does autoscaling break even on this demand shape?
   (d) Now the availability question. Each region is 99.96 % available; 15 % of failure probability is correlated across regions; failover succeeds 97 % of the time and takes 6 minutes; single-region events last 3 hours on average. Compute annual downtime in minutes for a single region, for the naive two-region calculation, and for the honest two-region calculation with all four terms. State which term dominates, and compute what annual downtime would result if correlation were reduced to 4 % and failover success raised to 99.9 %.
   Show every step and state any additional assumptions you make.

Reply with your answers and I'll assess them.

---

**Progress checklist**

Phase 1 — Internet and System-Design Foundations: all 17 topics `[x]` complete.

Phase 2 — Scaling
- [x] 1–17. Vertical scaling through replication lag and read-after-write consistency
- [x] 18. Database partitioning and sharding
- [x] 19. Range, hash, directory, geographic sharding
- [x] 20. Resharding, hot partitions, cross-shard queries
- [x] 21. Consistent hashing and virtual nodes
- [x] 22. Autoscaling and multi-region architecture

**Phase 2 — Scaling: all 22 topics complete.**

Phases 3–5: not started.

**NEXT: Phase 2 Wrap-Up**

# Phase 2 Wrap-Up — Scaling

Covers Lessons 18–39 (Phase 2, Topics 1–22). Continuing example: **BookNook**, an online bookstore.

Phase 1 (Lessons 1–17) gave you the measurement vocabulary: latency vs throughput, Little's Law (`L = λW`), availability maths, percentiles, and idempotency. Phase 2 spent twenty-two lessons on one question: **when one machine is not enough, what exactly do you do, and what does each choice cost you?**

This wrap-up is a revision document, not a new lesson. Nothing here is new material; everything is a compressed, checkable restatement of what you should now be able to reconstruct from memory.

---

## 1. Revision sheet

### Topic 1 — Vertical scaling (Lesson 18)

| Fact | Detail |
|---|---|
| Definition | Increase the resources of a single machine: more vCPU, more RAM, faster NVMe, higher network bandwidth. The *topology* of the system does not change. |
| Why it is first | It requires zero code change and zero distributed-systems complexity. A 4 vCPU → 32 vCPU move is a 10-minute maintenance window; a horizontal move is a quarter of engineering work. |
| Hard ceiling | Largest commodity cloud instances land around 128–448 vCPU and 1–24 TB RAM (typical, 2024-era). Beyond that you cannot buy your way out. |
| Economic ceiling arrives first | Instance price is roughly linear in vCPU up to mid-range, then super-linear. Doubling from 64→128 vCPU typically costs 2× but yields **well under 2×** throughput because of lock contention, NUMA effects and memory-bandwidth saturation. |
| Availability ceiling | One machine = one failure domain. Max availability of a single node is bounded by its own MTBF; no amount of CPU fixes "the host rebooted". |
| The number that matters | Amdahl's law: speedup ≤ `1 / (s + (1−s)/N)` where `s` is the serial fraction. With 5 % serial work, the maximum speedup from infinite cores is **20×**. |

**BookNook application:** vertical scaling is still the right first answer for the single-primary PostgreSQL instance; it is the wrong answer for stateless app servers, which should scale out.

---

### Topic 2 — Horizontal scaling (Lesson 19)

| Fact | Detail |
|---|---|
| Definition | Add more machines of the same role and distribute work across them. Topology changes; code usually must change too. |
| Precondition | Work must be **partitionable** and nodes must not depend on each other's local state. |
| Availability gain | With `n` independent redundant nodes each at availability `a`, system availability (parallel) = `1 − (1 − a)^n`. Three nodes at 99 % each → `1 − 0.01³` = **99.9999 %** *for the redundancy term only*. |
| Availability loss | Components **in series** multiply: LB (99.99 %) × app (99.95 %) × DB (99.9 %) = **99.84 %** ≈ 14 h/yr downtime. Adding components always lowers serial availability. |
| Capacity headroom formula | Safe utilisation `U_max = 0.85 × (N − k) / N`, where `N` is node count and `k` the number you must survive losing. At N=10, k=2: `U_max = 0.85 × 0.8 = 68 %`. Run above that and losing two nodes overloads the rest. |
| Cost of scaling out | Coordination: service discovery, config distribution, distributed tracing, more network hops (each hop adds ~0.5 ms same-AZ, ~1–2 ms cross-AZ, ~30–80 ms cross-region). |

**Rule of thumb:** scale up until the next instance size costs more than two smaller instances *plus* the engineering cost of statelessness. Then scale out.

---

### Topic 3 — Stateless application servers (Lesson 20)

| Fact | Detail |
|---|---|
| Definition | A server is stateless when **any** request can be served correctly by **any** instance, because no request-spanning state lives in that instance's memory or local disk. |
| What "state" actually means | Session data, in-process caches that affect correctness, uploaded file temp dirs, in-memory counters, scheduled-job locks, WebSocket connection registries. |
| Where state goes instead | Redis/Memcached (sessions), signed JWT or signed cookie (client-held), S3/object store (uploads), the database (durable truth), a distributed lock service (leader election). |
| The payoff | Instances become **cattle**: you can add, remove, restart, or lose one with zero user-visible effect. Autoscaling, rolling deploys and blue/green all become possible. |
| Signed-cookie trade-off | Client-held session state is free to scale but cannot be revoked before expiry. Mitigation: short TTL (5–15 min typical) plus a server-side deny-list keyed by token ID. |
| The sneaky violation | "Just a small local cache" is fine for *performance*, fatal for *correctness* if two instances can disagree about something a user observes (e.g. a feature flag, a rate-limit counter). |

---

### Topic 4 — Load balancers (Lesson 21)

| Fact | Detail |
|---|---|
| Definition | A component that accepts client connections and distributes them across a pool of backends, presenting one stable address. |
| Four jobs | Distribution, health-based removal, TLS termination, and a stable public endpoint decoupled from backend IPs. |
| Deployment types | Hardware appliance (F5), software (nginx, HAProxy, Envoy), cloud managed (ALB/NLB, GCLB), client-side (gRPC round-robin picker, service mesh sidecar). |
| It is a SPOF unless made redundant | Standard pattern: ≥2 LB nodes in different AZs, fronted by DNS round robin or anycast VIP. |
| Capacity planning | Governed by Little's Law. An LB holding `L` concurrent connections at arrival rate `λ` and mean hold time `W` needs `L = λW` file descriptors. 20,000 req/s × 0.25 s = **5,000** concurrent connections minimum. |
| Essential headers (L7 only) | `X-Forwarded-For` (original client IP), `X-Forwarded-Proto`, `X-Request-ID`. At L4, use the **PROXY protocol** to preserve client IP. |

---

### Topic 5 — Layer 4 versus Layer 7 (Lesson 22)

| Dimension | Layer 4 (transport) | Layer 7 (application) |
|---|---|---|
| Sees | IP addresses, ports, TCP flags | Full HTTP: method, path, headers, cookies, body |
| Decision basis | 5-tuple hash or connection counts | Any request attribute |
| Throughput | Millions of packets/s; can use DSR | Tens of thousands of req/s per core (typical) |
| Added latency | ~0.05–0.3 ms typical | ~0.5–2 ms typical (parse + buffer + re-serialise) |
| TLS | Passthrough (end-to-end encryption preserved) | Terminates (can inspect, re-encrypt to backend) |
| Can do | Any TCP/UDP protocol, extreme scale | Path routing, header rewriting, retries, rate limiting, compression, canary by header |
| Cannot do | Anything content-aware; cannot balance per-request on a keep-alive connection | Non-HTTP protocols cheaply; match L4 packet rates |
| Typical BookNook use | NLB in front of the L7 tier; Postgres and Redis proxies | ALB/nginx/Envoy in front of app servers |

**The single most important consequence:** on an HTTP/1.1 keep-alive connection, an L4 LB balances *connections*, so one connection carrying 1,000 requests and another carrying 10 produces a 100:1 imbalance. HTTP/2 makes this worse — one long-lived connection multiplexes everything. L7 is mandatory when per-request fairness matters.

---

### Topic 6 — Load-balancing strategies (Lesson 23)

| Strategy | Cost | Correct when | Fails when |
|---|---|---|---|
| Round robin | O(1) | Uniform request cost, identical servers | Variable cost; keep-alive connection reuse |
| Weighted round robin | O(1) amortised (smooth WRR) | Heterogeneous servers, uniform request cost | Weights drift after fleet upgrades |
| Least connections | O(N) scan or O(log N) heap | Variable request cost — **best general default** | Fast-failing backend becomes a traffic magnet |
| Weighted least connections | O(N) | Heterogeneous servers + variable cost | Same black-hole risk |
| Least response time (EWMA) | O(N) + EWMA state | Degraded-but-alive servers (GC pause, disk thrash) | Errors are fast → must exclude errors from the average |
| IP / source hash | O(1) | Cheap affinity without cookies | NAT collapses many clients to one key; `% N` reshuffles everything on membership change |
| Consistent hash | O(log V) | Affinity that must survive membership change | Still needs virtual nodes for balance |
| Power of two choices | O(1) — two random probes | Large fleets where scanning N is expensive | Needs reasonably fresh load signal |

**The key number:** with `n` servers and random placement, expected max load is `~log n / log log n`. With **power of two choices** it collapses to `~log log n / log 2` — for n = 1,000, roughly 10 vs 3.3 units above average. Two random probes gets you ~95 % of the benefit of a full scan at ~0.2 % of the cost.

**The key failure mode:** a backend returning instant 500s has near-zero in-flight requests, so least-connections routes *more* traffic into it. Strategy and health checking must be designed together.

---

### Topic 7 — Health checks and failover (Lesson 24)

| Fact | Detail |
|---|---|
| Active vs passive | **Active**: LB probes an endpoint on a timer. **Passive (outlier detection)**: LB watches real traffic and ejects backends exceeding an error/latency threshold. Production needs both. |
| Three probe types | **Liveness** (is the process alive? restart if not), **readiness** (can it serve traffic now? remove from pool if not), **startup** (give a slow-booting process grace before liveness applies). |
| Shallow vs deep | Shallow (`200 OK` from the process) misses broken dependencies. Deep (checks DB, cache) risks **correlated ejection**: one slow DB marks the entire fleet unhealthy and takes down the site. Compromise: deep checks with cached results and a "degraded but serving" state. |
| **Detection time formula** | `detection = unhealthy_threshold × interval + timeout`. With threshold 3, interval 5 s, timeout 2 s → **17 s** worst case before a dead node is removed. |
| Tuning trade-off | Shorter interval = faster detection, more probe load and more false positives from transient GC pauses. Typical production: interval 5–10 s, timeout 2–3 s, unhealthy threshold 2–3, healthy threshold 2. |
| Failover hygiene | **Connection draining** (stop new, let in-flight finish, typical 30–300 s), **graceful shutdown** (fail readiness first, then stop), **slow start / warm-up** (ramp traffic to a new node over 30–60 s so JIT, connection pools and caches warm). |
| RTO / RPO | **RTO** = how long until service is restored. **RPO** = how much data loss is acceptable. Both are business decisions, not technical ones; they determine your replication mode (Topic 16). |

---

### Topic 8 — Reverse proxies and API gateways (Lesson 25)

| Fact | Detail |
|---|---|
| Reverse proxy | Sits in front of servers, accepts client requests on their behalf. Adds TLS termination, caching, compression, static file serving, request buffering (protects slow backends from slow clients). |
| Forward proxy ≠ reverse proxy | A forward proxy acts for the *client* (egress control); a reverse proxy acts for the *server* (ingress control). |
| API gateway | A reverse proxy plus API-specific policy: authentication/authorisation, rate limiting and quotas, request/response transformation, API-key management, versioning, aggregation/fan-out, usage metering. |
| Where to put logic | Cross-cutting and uniform → gateway. Business-specific → service. Pushing business logic into the gateway recreates the ESB anti-pattern and makes the gateway a deployment bottleneck. |
| Risk | The gateway is on the critical path for 100 % of traffic: its blast radius is total. It must be stateless, horizontally scaled, and have a strictly bounded feature set. |
| Request buffering | nginx buffering lets an app server finish in 20 ms and move on, while nginx spends 2 s dribbling the response to a mobile client — turning a C10K problem into a C10K-on-nginx problem instead of on your app. |

---

### Topic 9 — Caching fundamentals (Lesson 26)

| Fact | Detail |
|---|---|
| Definition | Storing a copy of data closer (in time or space) to the consumer so that a repeat request avoids the expensive original computation or fetch. |
| **Effective latency formula** | `effective = hit_ratio × hit_latency + (1 − hit_ratio) × miss_latency`. |
| Worked example | BookNook product page: hit 1 ms, miss 40 ms. At 90 % hit: `0.9×1 + 0.1×40 = 4.9 ms`. At 95 %: `2.95 ms`. At 99 %: `1.39 ms`. |
| Non-linearity — the headline insight | Going 90 % → 95 % removes **half** the remaining miss cost. Going 99 % → 99.5 % saves 0.2 ms. **Hit-ratio improvements pay off geometrically at the low end and barely at all at the high end.** |
| Miss cost must include the cache probe | A miss costs `cache_probe + origin_fetch`, so a low-hit-ratio cache is *slower* than no cache. Break-even hit ratio ≈ `probe_latency / (origin_latency − hit_latency)`. |
| Working set | Cache size should hold the working set — the data actually requested within one TTL window. Catalogue of 2 M books, 50 KB each = 100 GB total; but if 95 % of reads hit 40,000 titles, you need **2 GB**, not 100 GB. |
| Why caches work at all | Access is never uniform. Zipf/Pareto distribution: the top ~1 % of items typically serve 50–70 % of reads. |

---

### Topic 10 — Browser / application / database / distributed caches (Lesson 27)

| Layer | Where | Typical latency | TTL range | Invalidation control |
|---|---|---|---|---|
| Browser cache | User's device | ~0 ms | seconds–1 year (immutable assets) | **None** — you cannot reach it. Use content hashing in filenames. |
| CDN / edge | POP near user | 5–30 ms | minutes–days | Purge API (seconds–minutes to propagate) |
| Reverse proxy cache | Your DC, in front of app | 1–5 ms | seconds–minutes | Direct, immediate |
| Application in-process | App server heap | 0.01–0.1 ms | seconds | Hard: N instances, N copies, no coordination |
| Distributed cache (Redis/Memcached) | Separate tier | 0.5–2 ms (same AZ) | minutes–hours | Direct, single source of cached truth |
| Database buffer pool | Inside the DB | 0.1 ms vs 5 ms disk | managed by DB | Automatic |
| Materialised view | Inside the DB | query-dependent | refresh schedule | Explicit `REFRESH` |

**Key trade-off:** closer to the user = faster and cheaper, but harder to invalidate. In-process caches are the fastest and the most dangerous — with 40 app servers you have 40 independent, mutually inconsistent copies.

**Rule:** only cache in-process data that is (a) tiny, (b) read-mostly, and (c) tolerant of up to one TTL of staleness across instances.

---

### Topic 11 — Cache-aside, read-through, write-through, write-behind (Lesson 28)

| Pattern | Read path | Write path | Strength | Weakness |
|---|---|---|---|---|
| **Cache-aside** (lazy loading) | App checks cache; on miss reads DB and populates cache | App writes DB, then **deletes** the cache key | Simple, cache failure is survivable, only requested data is cached | Every miss is a full origin round trip; race between a slow read's populate and a concurrent write |
| **Read-through** | App asks cache only; cache library fetches from DB on miss | Usually paired with write-through | App code is clean; loading logic centralised | Cache becomes a hard dependency; first request still pays the miss |
| **Write-through** | Same as read-through | Write goes to cache **and** DB synchronously before acking | Cache is never stale; no invalidation needed | Adds write latency; caches data that may never be read |
| **Write-behind** (write-back) | From cache | Write to cache, ack immediately, flush to DB asynchronously in batches | Huge write throughput; absorbs bursts; batches/coalesces | **Data loss window** if the cache dies before flush; DB and cache are transiently inconsistent |
| **Refresh-ahead** | Serve from cache; proactively refresh entries nearing TTL expiry that are being read | — | Hides miss latency for hot keys entirely | Wastes work on keys that would not have been requested again |

**Why delete-on-write beats update-on-write in cache-aside:** updating writes a value that may be immediately stale and may never be read; deleting is idempotent, cheap, and defers the cost to the next reader who actually wants it.

**The classic cache-aside race:** reader misses → reads DB (value `v1`) → writer updates DB to `v2` and deletes the key → reader writes `v1` into the cache. The key is now permanently stale until TTL. Fixes: always set a TTL (bounds the damage), use versioned keys, or use a **lease**/single-flight token so a stale fill is rejected.

---

### Topic 12 — Cache invalidation and TTL (Lesson 29)

| Fact | Detail |
|---|---|
| The three strategies | **TTL expiry** (time-based, eventually correct), **explicit invalidation** (event-driven, immediately correct but requires knowing every dependent key), **versioned keys** (`book:123:v7` — the old key is simply orphaned and evicted). |
| TTL is a staleness budget | Choose TTL = the maximum staleness the business can tolerate. BookNook price: 60 s. Book description: 24 h. Stock count: 5 s or don't cache it. Recommendation list: 1 h. |
| **Add jitter** | Fixed TTL on items populated together causes synchronised mass expiry (**cache avalanche**). Use `TTL × (1 + rand(−0.1, +0.1))` at minimum. |
| `stale-while-revalidate` | Serve the stale value immediately while refreshing in the background. Converts a latency spike into a bounded staleness window. The single highest-leverage caching directive. |
| Invalidation is hard because of dependency fan-out | Changing one author's name may invalidate: the author page, 40 book pages, 3 category listings, 12 search result pages, and a sitemap. You must *enumerate* this. Tag-based invalidation (Varnish `xkey`, Fastly surrogate keys) is the practical answer. |
| Versioned keys trade space for safety | No purge call, no propagation delay, no race — at the cost of holding both versions until the old one is evicted. |

---

### Topic 13 — Cache stampedes, hot keys, penetration, eviction (Lesson 30)

| Pathology | Mechanism | Fix |
|---|---|---|
| **Cache stampede** (dogpile / thundering herd) | A hot key expires; N concurrent readers all miss and all hit the origin simultaneously | **Single-flight / request coalescing** (one fetch, others wait), **lease** (first miss gets a token, others serve stale or retry), **probabilistic early expiry (XFetch)** |
| **Cache avalanche** | Many keys expire at the same instant (bulk warm, or cache restart) | TTL jitter; staged warm-up; `stale-while-revalidate` |
| **Cache penetration** | Requests for keys that **do not exist** always miss and always hit the DB — a trivial DoS vector | **Negative caching** (cache the "not found" with a short TTL, 30–60 s typical) + **Bloom filter** of known-existent keys |
| **Hot key** | One key (celebrity book, flash sale) exceeds a single cache node's capacity | Replicate the key across N nodes with suffixed keys `book:123#0..N`; add a local in-process L1 cache with a 1 s TTL; **tiered caching** |
| **Eviction pressure** | Working set exceeds cache memory; useful entries evicted early, hit ratio collapses | Size to the working set; segregate cache pools by TTL class; pick the right policy |

**XFetch formula (probabilistic early expiry):** recompute when
`now − delta × beta × ln(rand()) ≥ expiry`, where `delta` is the measured recompute time and `beta ≈ 1`. Hot keys (requested often) recompute early and smoothly; cold keys do not.

**Eviction policies:**

| Policy | Evicts | Best for | Weakness |
|---|---|---|---|
| **LRU** | Least recently used | General workloads with temporal locality | A single large scan flushes the whole cache |
| **LFU** | Least frequently used | Stable popularity distributions | Slow to adapt; old hot items ossify (fix: decay counters) |
| **FIFO** | Oldest inserted | Trivially cheap | Ignores access patterns entirely |
| **Random** | Arbitrary | Surprisingly decent, O(1), no metadata | No locality awareness |
| **TTL-only** | Expired entries | Caches sized above the working set | Does nothing under memory pressure |
| **ARC / W-TinyLFU** | Adaptive recency+frequency mix | Scan-resistant, best modern default (Caffeine, Redis `allkeys-lfu`) | More memory/CPU for metadata |

---

### Topic 14 — CDNs and edge caching (Lesson 31)

| Fact | Detail |
|---|---|
| Definition | A geographically distributed network of **POPs** (points of presence) that cache and serve content close to users. |
| What it buys you | RTT reduction (dominant win: 150 ms transatlantic → 10 ms local), origin offload (typically 85–99 % of bytes), DDoS absorption, TLS termination at the edge. |
| Routing | **Anycast** (same IP advertised from many POPs; BGP picks the nearest) or **DNS-based GSLB** (resolver returns a POP-specific address). Anycast is faster to fail over; DNS gives finer control. |
| Cache key | By default URL + selected headers. **A badly chosen cache key destroys the hit ratio** — including `Set-Cookie` or a per-user header fragments one object into millions. Strip query params you don't need; normalise ordering. |
| **Origin shield** | A designated mid-tier POP that all other POPs fetch through, so a cold object is fetched from origin **once** rather than once per POP. With 100 POPs this is a 100× origin-load reduction on cold content. |
| Static vs dynamic | Static assets: `Cache-Control: public, max-age=31536000, immutable` + content-hashed filenames. Dynamic HTML: short TTL (5–60 s) + `stale-while-revalidate` + tag-based purge. Personalised content: `private, no-store`, or edge-side personalisation. |
| Edge compute | Run small functions at the POP (Cloudflare Workers, Lambda@Edge): A/B bucketing, auth token validation, header rewriting, geo redirects — without a round trip to origin. |
| Purge latency | Typical global purge propagation: 1–30 s. Design so correctness never depends on purge speed; use versioned URLs when it must be instant. |

---

### Topic 15 — Database replication (Lesson 32)

| Fact | Detail |
|---|---|
| Definition | Maintaining copies of the same dataset on multiple database nodes. |
| Topologies | **Single-leader** (one writable primary, N read replicas — the overwhelming default), **multi-leader** (multiple writable nodes, needs conflict resolution), **leaderless** (quorum reads/writes, Dynamo-style). |
| Mechanism | The primary ships its **WAL** (write-ahead log) / binlog to replicas, which apply it in order. Physical replication ships byte-level page changes; logical replication ships row-level change events (slower, but allows cross-version and partial replication). |
| What it solves | Read scaling (read replicas), availability (promote a replica on primary failure), locality (replica in each region), and offloading analytics/backups. |
| What it does **not** solve | **Write scaling.** Every write still goes through one primary and is applied by every replica. Replication multiplies *total* write work by N. For write scaling you need sharding (Topic 18). |
| Read-scaling arithmetic | With a 95:5 read:write ratio, moving reads to replicas removes ~95 % of primary query load — a ~20× effective read capacity gain. With a 50:50 ratio, replicas barely help. |
| Failover | Promotion must be coordinated (Patroni, Orchestrator, RDS multi-AZ) and fenced to prevent **split-brain** (two nodes both believing they are primary). |

---

### Topic 16 — Synchronous versus asynchronous replication (Lesson 33)

| Mode | Primary acks after | Write latency | RPO | Risk |
|---|---|---|---|---|
| **Asynchronous** | Local commit only | Fast (local fsync, ~1 ms) | **> 0** — lose the last N ms–s of writes on failover | Silent data loss; unbounded replication lag |
| **Semi-synchronous** | ≥1 replica **received** (not necessarily applied) the WAL | +1 network RTT (~0.5–2 ms same region) | ~0 for received writes | A stalled replica stalls writes unless there is a timeout fallback to async |
| **Synchronous** | ≥1 replica **applied and committed** | +RTT + replica fsync + apply | **0** | Availability of the write path = availability of primary **AND** replica (multiplicative — it goes *down*) |
| **Quorum** (e.g. `k` of `n`) | `k` replicas acked | +slowest-of-k RTT | 0 if `k ≥ 2` | Tunable; the standard compromise |

**The core trade-off, stated precisely:** synchronous replication converts a *durability* risk into an *availability* risk. Two nodes at 99.9 % each, both required for a write, give `0.999² = 99.8 %` write availability — worse than one node. Asynchronous replication keeps write availability at 99.9 % but accepts a non-zero RPO.

**Cross-region synchronous replication is almost always wrong:** a 70 ms RTT added to every commit caps write throughput per connection at ~14 writes/s. Use async cross-region + synchronous within region.

---

### Topic 17 — Replication lag and read-after-write consistency (Lesson 34)

| Fact | Detail |
|---|---|
| Replication lag | The delay between a write committing on the primary and becoming visible on a replica. Typical healthy: 10–500 ms. Under bulk writes, schema migrations, or long transactions: seconds to minutes. |
| Causes | Single-threaded apply on the replica, long-running read queries blocking apply, network saturation, replica hardware weaker than primary, write bursts. |
| **Read-after-write (read-your-writes)** | A user must see their own write immediately. Violation is the classic bug: user posts a review, is redirected to the page, and the review is not there. |
| Five fixes | 1) Route reads to the **primary** for a window after a write (simplest, typical window = p99 lag × 2). 2) **Sticky routing** to the replica that acked. 3) **LSN/GTID token**: client carries the write position; replica waits until caught up. 4) Read from a **cache** populated by the writer. 5) Render the write optimistically client-side. |
| **Monotonic reads** | A user must never see time go backwards (read from a fresh replica, then a stale one). Fix: pin a user's session to one replica via consistent hashing on user ID. |
| Monitoring | Alert on **lag in bytes and seconds**, not just seconds — a replica with zero traffic shows low second-lag even when far behind. Treat lag > 5 s as a readiness failure and eject the replica from the read pool. |

---

### Topic 18 — Database partitioning and sharding (Lesson 35)

| Fact | Detail |
|---|---|
| Partitioning | Splitting data into pieces. **Vertical** = split columns/tables across stores. **Horizontal** = split rows. |
| Sharding | Horizontal partitioning across **separate database instances** (not just separate files on one instance). This is what scales writes. |
| Why it is a last resort | It permanently removes: cross-shard transactions, cross-shard joins, global `AUTO_INCREMENT`, global unique constraints, and cheap `ORDER BY ... LIMIT` across the whole dataset. |
| Try first, in order | Index tuning → read replicas → caching → vertical scaling → archiving cold data → functional/vertical split by service → **then** shard. |
| **Shard key choice is the whole game** | A good shard key gives (a) high cardinality, (b) even access distribution, (c) alignment with the dominant query pattern so most queries hit one shard. |
| BookNook examples | `orders` → shard by `customer_id` (customer order history is single-shard; "all orders today" becomes scatter-gather). `books` → shard by `book_id` hash (reads are by ID; search goes to Elasticsearch anyway). Sharding orders by `order_date` would be catastrophic — today's shard takes 100 % of writes. |
| Shard count | Pick a number you will not outgrow, and over-provision **logical** shards (e.g. 1,024) mapped onto fewer **physical** nodes. Splitting logical→physical later is a config change, not a data migration. |

---

### Topic 19 — Range / hash / directory / geographic sharding (Lesson 36)

| Scheme | Mapping | Range queries | Rebalancing | Hot-spot risk |
|---|---|---|---|---|
| **Range** | Contiguous key ranges (`A–F`, `G–M`, …) | **Excellent** — one shard serves a range scan | Easy: split a range in two | **High** — sequential keys (timestamps, auto-increment IDs) all land on the last shard |
| **Hash** | `shard = hash(key) % N` | **Impossible** — a range scans all shards | **Terrible** with `% N`: changing N remaps ~`(N−1)/N` of keys | Low (unless one key is itself hot) |
| **Consistent hash** | Key → position on a ring | Impossible | **Good** — only `~K/N` keys move | Low with virtual nodes |
| **Directory / lookup** | An explicit `key → shard` lookup table | Depends on allocation | **Best** — move anything anywhere by editing one row | Controllable, but the directory is a SPOF and a hot read (must be cached/replicated) |
| **Geographic** | Shard by user region | Within region, yes | Hard (users move) | **High** — regions have wildly different populations and diurnal peaks |

**Geographic sharding exists mainly for two non-performance reasons:** data residency law (GDPR, data-localisation rules) and latency for write-heavy regional workloads. It is rarely chosen for load distribution, because population is never evenly distributed.

---

### Topic 20 — Resharding, hot partitions, cross-shard queries (Lesson 37)

| Fact | Detail |
|---|---|
| Resharding | Changing the number of shards or the key→shard mapping while serving traffic. The hardest routine operation in data infrastructure. |
| Safe procedure | 1) Dual-write to old and new. 2) Backfill historical data. 3) Verify by checksum/row count. 4) Shift reads gradually (1 % → 100 %) with a kill switch. 5) Stop dual-writes. 6) Drop old data after a safety window. |
| Avoiding it | **Logical shards**: create 1,024 logical shards on day one, map many per physical node, move logical shards between nodes later. This is how Vitess, Citus and Slack's architecture work. |
| **Hot partition** | One shard receives disproportionate traffic. Causes: low-cardinality shard key, a celebrity entity, a monotonic key, a bot hammering one ID. |
| Hot-partition fixes | **Key salting** (`hash(user_id) + "#" + rand(0..k)` — spreads writes, makes reads scatter-gather over k), **split the hot key** into sub-keys, **cache in front** of the hot shard, **dedicated shard** for the celebrity entity. |
| **Cross-shard query = scatter-gather** | Fan out to all shards, merge results in the app/proxy. Latency is bounded by the **slowest shard**: `p99_query ≈ p99_shard` for a single shard but `≈ p99.9_shard` when fanning out to 100 (tail amplification). |
| Mitigations | Denormalise; maintain a second, differently-sharded copy ("index table"); push aggregation to a search/analytics store (Elasticsearch, ClickHouse); precompute with materialised views. |
| Cross-shard transactions | Require 2PC (slow, blocking, coordinator SPOF) or **sagas** (compensating transactions, eventually consistent). Prefer designing so that transactions never cross shards. |

---

### Topic 21 — Consistent hashing and virtual nodes (Lesson 38)

| Fact | Detail |
|---|---|
| Problem solved | With `hash(key) % N`, changing N remaps ~`(N−1)/N` of all keys. Going 4→5 nodes remaps **80 %** of keys — a total cache wipe or a total data migration. |
| Mechanism | Hash both keys and nodes onto a circular space (e.g. 0 … 2³²−1). A key belongs to the first node clockwise from its position. |
| **The key guarantee** | Adding or removing one node moves only **~K/N keys**, where K = total keys, N = node count. Going 4→5 nodes moves ~20 %, not 80 %. |
| Why plain consistent hashing is not enough | With few nodes, random ring positions produce uneven arcs — standard deviation of load is high; one node can own 2–3× its fair share. |
| **Virtual nodes (vnodes)** | Place each physical node at V positions on the ring (typically V = 100–500). Load standard deviation falls roughly as `1/√V`. At V=160, imbalance is typically within ±5 %. |
| Extra vnode benefits | Heterogeneous capacity (a 2× machine gets 2× vnodes); load from a removed node is redistributed across **many** nodes, not dumped on one successor. |
| **Rendezvous hashing (HRW)** | Alternative: compute `hash(key, node)` for every node, pick the max. Gives near-perfect distribution and the same minimal-disruption property, with no ring or vnode bookkeeping — at O(N) per lookup instead of O(log V). Better for small N (< ~100). |
| Where it is used | Memcached/Redis client sharding, Cassandra/DynamoDB/Riak partitioning, Envoy's `ring_hash` LB policy, CDN cache-node selection. |

---

### Topic 22 — Autoscaling and multi-region architecture (Lesson 39)

| Fact | Detail |
|---|---|
| Autoscaling types | **Reactive** (metric crosses a threshold), **scheduled** (known daily/weekly pattern), **predictive** (ML forecast). Use scheduled for known peaks and reactive as the safety net. |
| Metrics to scale on | Request rate and **queue depth / concurrency** are better signals than CPU. CPU lags; by the time it is at 80 %, latency has already degraded. Target-tracking on `requests_per_instance` is the most robust default. |
| **Hysteresis and cooldown** | Scale out fast and aggressively; scale in slowly and conservatively. Typical: scale-out cooldown 60 s, scale-in cooldown 300–600 s, plus different thresholds for out (70 %) and in (40 %) to prevent **flapping**. |
| The lag problem | Instance boot + app start + warm-up = typically 60–300 s. A traffic spike that arrives in 30 s cannot be met by autoscaling. You must hold **static headroom** for spikes: `U_max = 0.85 × (N − k) / N` still applies. |
| What breaks | Databases do not autoscale horizontally. Connection pools multiply: 50 instances × 100 connections = 5,000 DB connections. Use a pooler (PgBouncer, RDS Proxy). Cold caches on new instances cause a latency spike — use warm-up/slow-start. |
| Multi-region patterns | **Active-passive** (DR standby; RTO minutes, RPO = async lag), **active-active read / single-write-region** (the practical default), **active-active full write** (needs conflict resolution: CRDTs, last-write-wins, or partitioned write ownership). |
| Cost of multi-region | ≥2× infrastructure, cross-region data transfer charges, 30–150 ms inter-region RTT, and a permanent consistency decision. Do not go multi-region for availability alone until you are already at 99.99 % in one region. |
| Routing | Latency-based DNS or anycast for reads; write traffic pinned to the home region of the data (or of the user, if sharded geographically). |

---

### Phase 2 formula card

| Formula | Use |
|---|---|
| `effective_latency = h × hit_lat + (1 − h) × miss_lat` | Cache value; break-even analysis |
| `availability_series = a₁ × a₂ × … × aₙ` | Adding components always reduces availability |
| `availability_parallel = 1 − (1 − a)ⁿ` | Redundancy gain from n independent replicas |
| `U_max = 0.85 × (N − k) / N` | Safe steady-state utilisation surviving k node losses |
| `detection = unhealthy_threshold × interval + timeout` | How long a dead backend keeps receiving traffic |
| `keys_moved ≈ K / N` | Consistent hashing disruption on ±1 node |
| `max_load ≈ log log n / log 2` | Power of two choices imbalance (vs `log n / log log n` for pure random) |
| `L = λW` | Little's Law — connection/thread/pool sizing (Phase 1) |
| `speedup ≤ 1 / (s + (1−s)/N)` | Amdahl — ceiling on vertical scaling |

---

## 2. Glossary

| Term | Definition |
|---|---|
| **active-active** | A multi-node or multi-region deployment where all nodes serve live traffic simultaneously; requires conflict handling if all accept writes. |
| **active-passive** | A deployment where one node/region serves traffic and a standby stays idle and ready to be promoted; simpler, but the standby is paid-for idle capacity. |
| **Amdahl's law** | `speedup ≤ 1/(s + (1−s)/N)`; the serial fraction `s` of a workload caps the benefit of adding parallelism or cores. |
| **anycast** | Advertising one IP address from many network locations so BGP routes each client to the topologically nearest one; used by CDNs and global load balancers. |
| **API gateway** | A reverse proxy specialised for APIs, adding authentication, rate limiting, quotas, transformation, versioning and usage metering at the edge of a service estate. |
| **ARC (Adaptive Replacement Cache)** | An eviction policy that dynamically balances recency and frequency lists, making it resistant to scans that would flush a pure LRU. |
| **autoscaling** | Automatically adding or removing instances in response to a metric, a schedule, or a forecast. |
| **backpressure** | Deliberately signalling upstream to slow down (queue limits, 429/503, flow control) rather than accepting work you cannot complete. |
| **blast radius** | The set of users, requests or data affected when a given component fails; minimising it is the main reason for cells, shards and AZ isolation. |
| **Bloom filter** | A compact probabilistic set that answers "definitely not present" or "possibly present"; used in front of caches/stores to cheaply reject lookups for keys that do not exist. |
| **cache-aside** | The pattern where application code checks the cache, loads from the origin on a miss, populates the cache, and deletes the key on write. |
| **cache avalanche** | A mass simultaneous expiry (or a cache restart) causing a flood of misses to the origin; prevented with TTL jitter and staged warm-up. |
| **cache invalidation** | Removing or superseding a cached entry so stale data is no longer served; achieved by TTL expiry, explicit purge, or key versioning. |
| **cache key** | The exact string/tuple identifying a cached entry; its composition (URL, headers, params) determines hit ratio and correctness. |
| **cache penetration** | Repeated requests for keys that do not exist in the backing store, so every request misses the cache and loads the origin. |
| **cache stampede** | Many concurrent requests missing on the same expired key and all hitting the origin at once; also called dogpile. |
| **CDN (content delivery network)** | A distributed network of POPs that cache and serve content near users, offloading the origin and cutting RTT. |
| **celebrity problem** | A single entity (famous author, viral product) whose traffic exceeds what one shard or cache node can serve; the general form of a hot key. |
| **cold start** | The elevated latency of a freshly started instance or function before caches, JIT compilation and connection pools are warm. |
| **connection draining** | Removing a backend from the pool while allowing in-flight requests to complete before shutdown; typical window 30–300 s. |
| **consistent hashing** | Mapping keys and nodes onto a hash ring so adding/removing a node relocates only ~K/N keys instead of nearly all of them. |
| **cooldown period** | A minimum interval after a scaling action during which further actions are suppressed, preventing oscillation. |
| **cross-shard query** | A query whose result requires data from more than one shard; executed as scatter-gather and bounded by the slowest shard. |
| **denormalisation** | Deliberately duplicating data across rows/tables/shards to avoid joins at read time, trading write cost and consistency for read speed. |
| **directory sharding** | Using an explicit lookup table to map keys to shards, giving maximum placement flexibility at the cost of a lookup hop and a critical dependency. |
| **edge compute** | Running small application functions in CDN POPs (auth checks, A/B bucketing, redirects) to avoid an origin round trip. |
| **eviction policy** | The rule deciding which entry to discard when a cache is full: LRU, LFU, FIFO, random, ARC, W-TinyLFU. |
| **failback** | Returning traffic to the original primary after it recovers; should always be a deliberate, manual-or-verified action, never automatic. |
| **failover** | Automatically shifting traffic or write authority from a failed component to a healthy one. |
| **fan-out** | One incoming request producing many downstream requests (or one write producing many derived updates); multiplies load and tail latency. |
| **geographic sharding** | Partitioning data by user region, chiefly for data-residency compliance and regional write latency rather than load balance. |
| **graceful shutdown** | Failing readiness first, draining connections, finishing in-flight work, then exiting — so no request is dropped on deploy. |
| **GSLB (global server load balancing)** | DNS-level routing that returns different addresses based on client geography, latency or POP health. |
| **hash sharding** | Assigning keys to shards by a hash function; distributes evenly but destroys range-scan locality. |
| **health check** | A periodic probe (active) or traffic observation (passive) used to decide whether a backend should receive requests. |
| **hit ratio** | Fraction of cache lookups served from cache: `hits / (hits + misses)`; the primary input to effective latency. |
| **horizontal scaling** | Adding more machines of the same role and distributing work across them (scale out). |
| **hot key** | A single cache or shard key receiving a disproportionate share of traffic, exceeding one node's capacity. |
| **hot partition** | A shard receiving disproportionate read or write traffic, typically from a low-cardinality or monotonic shard key. |
| **hysteresis** | Using different thresholds for scaling out and scaling in (e.g. 70 % out / 40 % in) so the system does not flap at a single boundary. |
| **idempotency key** | A client-supplied unique token letting the server safely deduplicate retried writes; essential when LBs and clients retry. |
| **jitter** | Randomised variation added to timers (TTLs, retries, health-check offsets) to prevent synchronised thundering herds. |
| **keep-alive** | Reusing one TCP connection for multiple HTTP requests; saves handshakes but breaks per-request fairness at Layer 4. |
| **Layer 4 load balancing** | Routing on TCP/UDP information (IPs, ports) without reading payload; extremely fast, protocol-agnostic, content-blind. |
| **Layer 7 load balancing** | Routing on application data (HTTP path, headers, cookies); enables content-aware routing, retries and rewriting at higher CPU cost. |
| **lease** | A short-lived exclusive token granted to the first requester of a missing key, authorising only that caller to recompute it while others wait or serve stale. |
| **least connections** | A strategy sending each request to the backend with the fewest in-flight requests; adapts to variable request cost. |
| **LFU (least frequently used)** | Eviction of the entry with the lowest access count; strong for stable popularity, needs counter decay to adapt. |
| **liveness probe** | A check answering "is this process alive?"; failure triggers a restart, not just removal from the pool. |
| **logical replication** | Shipping row-level change events rather than physical pages, enabling cross-version, cross-schema and partial replication. |
| **LRU (least recently used)** | Eviction of the entry untouched for the longest time; good default, vulnerable to cache-flushing scans. |
| **materialised view** | A physically stored, precomputed query result refreshed on a schedule or on write; a database-native cache for expensive aggregations. |
| **monotonic reads** | A guarantee that a client never observes data going backwards in time; maintained by pinning a session to one replica. |
| **MTTR (mean time to recovery)** | Average time to restore service after a failure; availability ≈ `MTBF / (MTBF + MTTR)`, so halving MTTR is as valuable as doubling MTBF. |
| **multi-region** | Running the system in two or more geographic regions for latency, disaster recovery or data residency. |
| **negative caching** | Caching "this does not exist" responses with a short TTL to defeat cache penetration. |
| **origin shield** | A designated intermediate CDN tier through which all POPs fetch, collapsing N cold-object origin fetches into one. |
| **outlier detection** | Passive health checking that ejects a backend whose observed error rate or latency deviates from its peers. |
| **POP (point of presence)** | A CDN edge location housing caching servers close to a population of users. |
| **power of two choices** | Probing two random backends and choosing the less loaded; reduces maximum imbalance from `~log n/log log n` to `~log log n/log 2` at O(1) cost. |
| **PROXY protocol** | A small header prepended to a TCP stream by an L4 proxy to convey the original client IP and port to the backend. |
| **quorum** | Requiring acknowledgement from a majority (or configured `k` of `n`) of nodes before a read or write is considered successful. |
| **range sharding** | Assigning contiguous key ranges to shards; preserves range scans but hot-spots on sequential keys. |
| **read-after-write consistency** | A guarantee that a client immediately sees its own writes, even when reads normally go to lagging replicas. |
| **read replica** | A read-only database copy fed by replication from the primary, used to scale reads and offload analytics. |
| **readiness probe** | A check answering "can this instance serve traffic right now?"; failure removes it from the load-balancer pool without restarting it. |
| **refresh-ahead** | Proactively recomputing cache entries that are both popular and near expiry, so readers never experience the miss. |
| **rendezvous hashing (HRW)** | Selecting the node maximising `hash(key, node)`; minimal-disruption like consistent hashing, perfectly balanced, O(N) per lookup. |
| **replication lag** | The time (or byte offset) by which a replica trails the primary; the direct cause of stale reads. |
| **resharding** | Changing shard count or key mapping on a live system, normally via dual-write, backfill, verify, gradual read cutover. |
| **reverse proxy** | A server that accepts client requests on behalf of backends, adding TLS termination, caching, buffering and routing. |
| **RPO (recovery point objective)** | The maximum acceptable amount of data loss, expressed as a time window; determines replication synchrony. |
| **RTO (recovery time objective)** | The maximum acceptable time to restore service after a failure; determines failover automation. |
| **scatter-gather** | Fanning a query out to all shards and merging the partial results; latency is governed by the slowest responder. |
| **semi-synchronous replication** | The primary acks a write once at least one replica has *received* (not necessarily applied) the log record. |
| **shard key** | The column or expression determining which shard a row lives on; the single most consequential sharding decision. |
| **sharding** | Horizontal partitioning of data across independent database instances in order to scale writes and storage. |
| **single-flight** | Collapsing concurrent identical cache misses into one origin fetch whose result is shared with all waiters. |
| **SLO (service level objective)** | An internal target for a reliability metric (e.g. p99 < 200 ms for 99.9 % of 30-day windows) that drives engineering trade-offs. |
| **slow start** | Gradually ramping traffic to a newly added backend so its caches, pools and JIT warm before full load. |
| **split-brain** | Two nodes simultaneously believing they are primary, producing divergent writes; prevented by fencing, quorum or STONITH. |
| **stale-while-revalidate** | A cache directive permitting the stale value to be served immediately while a background refresh runs. |
| **startup probe** | A check that suppresses liveness/readiness failures until a slow-booting process has finished initialising. |
| **sticky session (session affinity)** | Binding a client to one backend via cookie or hash so in-memory session state remains valid; an anti-pattern once servers are stateless. |
| **synchronous replication** | The primary acks only after a replica has committed; RPO 0, but write availability becomes the product of both nodes' availabilities. |
| **tail latency amplification** | The effect where fanning out to N backends makes the p99 of the whole request approach the p99.9 of a single backend. |
| **thundering herd** | Many clients simultaneously retrying or recomputing the same thing after an expiry, restart or outage. |
| **tiered caching** | Layering caches (in-process L1 → distributed L2 → CDN) so hot keys are absorbed close to the caller. |
| **TLS termination** | Decrypting TLS at the proxy/LB so it can inspect and route on content; backend traffic is then plaintext or re-encrypted. |
| **TTL (time to live)** | The lifetime of a cached entry; functionally a declaration of the maximum staleness the business will tolerate. |
| **vertical partitioning** | Splitting a table's columns (or a schema's tables) across separate stores, typically to isolate large blobs or a hot subset. |
| **vertical scaling** | Increasing a single machine's CPU, memory, disk or network capacity (scale up). |
| **virtual node (vnode)** | One of many ring positions assigned to a single physical node in consistent hashing; raising V reduces load variance roughly as `1/√V`. |
| **WAL (write-ahead log)** | The durable ordered log of changes written before data pages; the source stream for replication and crash recovery. |
| **warm-up** | Deliberately populating caches and connection pools before a node takes full production traffic. |
| **weighted round robin** | Round robin biased by per-backend capacity weights; smooth implementations interleave rather than burst. |
| **working set** | The subset of data actually accessed within a given time window; the correct basis for sizing a cache. |
| **write amplification** | One logical write causing multiple physical writes (replication, index updates, denormalised copies, compaction). |
| **write-behind (write-back)** | Acking a write at the cache and flushing to the database asynchronously; maximum throughput, non-zero data-loss window. |
| **write-through** | Writing synchronously to both cache and database before acking; cache is never stale, writes are slower. |
| **X-Forwarded-For** | The HTTP header an L7 proxy uses to convey the original client IP; must be sanitised at the trust boundary or it is spoofable. |
| **XFetch** | Probabilistic early expiration: recompute when `now − delta × beta × ln(rand()) ≥ expiry`, so hot keys refresh before they expire. |

---

## 3. Twenty quiz questions

Answer these in your own words before checking anything. Calculation questions expect a number and the working.

1. BookNook's product page currently reads from PostgreSQL at 45 ms. You add Redis with a 1.2 ms hit latency and measure a 92 % hit ratio. What is the effective latency? If you raise the hit ratio to 97 %, what is the new effective latency, and what percentage of the original database-only latency have you removed in each case?

2. BookNook's stack is: ALB (99.99 %) → app tier (99.95 %) → Redis (99.9 %) → PostgreSQL primary (99.9 %). What is the end-to-end availability, and how many minutes of downtime per 30-day month does that imply? Now add a second PostgreSQL node in parallel at the same 99.9 % — what is the new end-to-end figure?

3. You run 12 app servers and must survive the loss of a full AZ containing 4 of them. Using `U_max = 0.85 × (N − k) / N`, what is your maximum safe steady-state CPU utilisation, and how many servers would you need to safely run at 70 %?

4. Your health check uses interval 10 s, timeout 3 s, unhealthy threshold 3. A backend hard-crashes. What is the worst-case time before it stops receiving traffic, and how many requests are lost if the LB sends it 400 req/s? Give one change that halves the detection time and state its cost.

5. Explain precisely why "least connections" can send *more* traffic to a failing backend than to a healthy one, and name the two mechanisms that fix it.

6. BookNook has 60 app servers and 40,000 req/s. Compare the per-second comparison cost of exhaustive least-connections against power of two choices. Roughly what maximum imbalance does each produce?

7. A customer updates their shipping address and is immediately redirected to the account page, which reads from a replica with 400 ms p99 lag. Describe three distinct fixes, and state exactly what each one costs.

8. Why is synchronous replication across regions (70 ms RTT) effectively unusable for a transactional write path? Give the maximum sequential writes per second per connection and explain the arithmetic.

9. You have 4 cache nodes using `hash(key) % 4` and need to add a fifth. What fraction of keys change owner? What fraction changes with consistent hashing? Show both numbers.

10. With consistent hashing and 6 physical nodes, you observe one node holding 2.1× the average load. What is the likely cause, what is the fix, and roughly what value of the fix parameter would you choose?

11. BookNook's `orders` table is sharded by `order_date`. Describe exactly what goes wrong, and give a better shard key together with the one query pattern your new key makes expensive.

12. Define cache penetration precisely, describe how an attacker exploits it against `GET /books/{isbn}`, and give two complementary defences with concrete TTL/parameter values.

13. A flash sale puts one book at 80,000 req/s against a Redis cluster where a single node tops out around 100,000 ops/s. Explain why this is dangerous even though it is below the limit, and give two mitigations with their trade-offs.

14. Compare write-through and write-behind for BookNook's "view count" counter and for its "order total" field. Which pattern suits which field, and why is using the wrong one dangerous in each direction?

15. Your CDN hit ratio is 31 % for product pages and you expected 90 %. List four cache-key or header mistakes that produce exactly this symptom, and say how you would confirm each one.

16. Explain origin shield. With 180 POPs and a newly published catalogue page, how many origin requests occur with and without shield for the first cold fetch?

17. Autoscaling is configured to add instances when average CPU exceeds 75 %, with a 300 s cooldown. Instances take 180 s to become ready. A marketing email drives traffic from 4,000 to 16,000 req/s over 45 seconds. What happens, and what two configuration changes plus one architectural change would you make?

18. Distinguish liveness, readiness and startup probes by giving one concrete BookNook failure that each — and only each — handles correctly.

19. A scatter-gather search fans out to 40 shards. Each shard has p50 = 8 ms and p99 = 90 ms. Explain, with reasoning, why the overall p50 is far worse than 8 ms, and name two techniques that reduce the effect.

20. BookNook must satisfy RPO = 0 for payments and RPO = 5 minutes for browsing-history data, while keeping write availability above 99.95 %. Describe a replication topology that satisfies all three constraints simultaneously, and state where the constraint forces you to accept higher write latency.

> **Note:** answers are deliberately not provided here. Reply with your answers — all twenty, or any subset — and they will be assessed individually, with corrections, the working you missed, and a mark on each.

---

## 4. Five practical exercises

Each exercise is runnable on one laptop. Docker is assumed; everything else is standard tooling.

### Exercise A — nginx load balancing: round robin vs least connections under skewed cost

**Goal:** observe empirically that round robin degrades badly when request costs vary, and that `least_conn` recovers most of the loss.

**Steps**

1. Write a trivial backend (Node, Python Flask, or Go) exposing:
   - `GET /fast` → sleep 5 ms, return 200
   - `GET /slow` → sleep 500 ms, return 200
   - `GET /health` → return 200 immediately
   It must log its own instance ID and the in-flight count.
2. Run three instances on ports 9001, 9002, 9003.
3. Configure nginx with an `upstream booknook { server 127.0.0.1:9001; ... }` block and `proxy_pass`. Start with the default (round robin).
4. Drive load with a **skewed mix**: 95 % `/fast`, 5 % `/slow`, 200 concurrent clients, 60 seconds. Use `wrk -t4 -c200 -d60s` with a Lua script that selects the path, or `hey`/`k6` equivalently.
5. Record: overall p50/p95/p99, and per-instance request counts and busy time.
6. Change the upstream block to add `least_conn;`. Reload nginx (`nginx -s reload`). Re-run the identical load.
7. Repeat both runs with an artificially weakened backend: make instance 9003 sleep 3× longer on every path.
8. Finally, make 9003 return `500` instantly for all paths and re-run with `least_conn` **without** any `max_fails`/`fail_timeout`. Then add `server 127.0.0.1:9003 max_fails=2 fail_timeout=10s;` and re-run.

**Success criterion:** you can produce a table showing (a) p99 at least 25 % lower under `least_conn` than round robin in the skewed-cost run, (b) a measurably more even busy-time distribution across instances under `least_conn`, and (c) the black-hole effect — instance 9003 receiving a *disproportionately large* share of requests in step 8 before `max_fails` is configured, and a near-zero share after.

---

### Exercise B — Measuring cache hit ratio and effective latency against real Redis

**Goal:** derive the effective-latency formula from measurements rather than taking it on trust.

**Steps**

1. `docker run -p 6379:6379 redis:7`. Seed a "database" that is deliberately slow: a SQLite or Postgres table of 100,000 book rows, with an artificial 25 ms delay added in your data-access function.
2. Implement cache-aside: `GET book:{id}` from Redis; on miss, read the store, `SET` with `EX 60`.
3. Generate requests with a **Zipfian** key distribution (`numpy.random.zipf(a=1.2)` modulo 100,000 is adequate). Zipf is what real catalogue traffic looks like; uniform random is not and will mislead you.
4. Instrument four counters and two histograms: hits, misses, hit latency, miss latency.
5. Run 100,000 requests. Record measured hit ratio `h`, mean hit latency, mean miss latency, and measured mean overall latency.
6. Compute `predicted = h × hit_lat + (1 − h) × miss_lat` and compare with the measured mean.
7. Repeat at three cache sizes by setting `maxmemory` to values that hold roughly 100 %, 10 % and 1 % of the keyspace, with `maxmemory-policy allkeys-lru`. Record hit ratio and mean latency at each.
8. Plot hit ratio against cache size, and effective latency against hit ratio.

**Success criterion:** your predicted mean latency is within 5 % of the measured mean at all three cache sizes, **and** your plot visibly shows the non-linear payoff — the latency improvement from 50 % → 75 % hit ratio is dramatically larger than from 95 % → 97.5 %.

---

### Exercise C — Trigger a cache stampede, then fix it

**Goal:** see the dogpile effect as a real latency and load spike, then eliminate it with single-flight.

**Steps**

1. Reuse Exercise B's setup. Pick one key, `book:1`, and make its origin fetch cost 300 ms (simulating an expensive join).
2. Add a counter on the **origin** function so you can count exactly how many times it is invoked.
3. Warm the key, then set its TTL to 5 seconds. Drive 300 concurrent clients all requesting `book:1` continuously for 60 seconds.
4. Record: origin invocation count, p99 latency, and a time-series graph of origin calls per second. You should see a sharp spike of up to ~300 simultaneous origin calls every 5 seconds.
5. **Fix 1 — single-flight.** Add in-process request coalescing (Go `golang.org/x/sync/singleflight`, Python `asyncio` future-per-key map, Node promise-per-key map). Re-run. Origin calls should drop to roughly one per instance per expiry.
6. **Fix 2 — distributed lease.** Use `SET book:1:lock <uuid> NX EX 10`. The lock winner recomputes; losers either serve the stale value (store it under `book:1:stale` with a longer TTL) or back off with jitter and retry. Re-run and record.
7. **Fix 3 — XFetch.** Store `delta` (measured recompute time) alongside the value and refresh when `now − delta × beta × ln(rand()) ≥ expiry` with `beta = 1`. Re-run and graph origin calls per second.
8. Finally, test the avalanche case: populate 10,000 keys all with the identical TTL, let them expire together, and then repeat with `TTL × (1 + uniform(−0.15, +0.15))`.

**Success criterion:** a table showing origin invocation count and p99 latency for: baseline, single-flight, lease, and XFetch. Single-flight and lease must reduce origin calls by at least 95 % versus baseline, and the XFetch graph must show a flat origin-call line rather than periodic spikes. The jittered TTL run must show the avalanche spike spread across a window rather than concentrated in one second.

---

### Exercise D — Consistent hash ring with virtual nodes, and measured key movement

**Goal:** prove the `K/N` movement guarantee and quantify how virtual nodes reduce imbalance.

**Steps**

1. Implement a ring in any language: a sorted structure mapping `hash(node_id + "#" + i) → node` for `i in 0..V`, and a lookup that binary-searches clockwise from `hash(key)`.
2. Use a well-distributed hash (`xxhash`, `murmur3`, or `sha1` truncated to 32 bits). Do **not** use a language's built-in string hash — several are randomised per process.
3. Generate 1,000,000 synthetic keys (`book:{uuid}`).
4. **Balance test.** With 10 nodes, record the per-node key count and compute the ratio `max/mean` for `V ∈ {1, 5, 20, 50, 100, 200, 500}`.
5. **Movement test.** With `V = 150` and 10 nodes, record each key's owner. Add an 11th node. Record owners again. Count how many keys changed owner and compare to the predicted `K/N = 1,000,000 / 11 ≈ 90,909` (i.e. ~9.1 %).
6. **Modulo comparison.** Repeat step 5 using `hash(key) % N` with N going 10 → 11. Count the movement.
7. **Removal test.** Remove a node with `V = 150` and verify the departed node's keys are spread across *all* remaining nodes rather than dumped on one successor. Report the per-node increase.
8. **Weighted test.** Give one node 2× the vnodes and verify it receives approximately 2× the keys.

**Success criterion:** a table of `max/mean` against V showing imbalance falling from roughly 1.5–2.5× at V=1 to under 1.10× at V≥150; a measured movement of 9 ± 1 % for consistent hashing versus approximately 91 % for modulo; and evidence that removal spreads load across all survivors.

---

### Exercise E — PostgreSQL streaming replication and lag under write load

**Goal:** build a real primary/replica pair, observe lag grow under write pressure, and measure the read-after-write window.

**Steps**

1. Start two PostgreSQL 16 containers on a shared Docker network: `pg-primary` and `pg-replica`.
2. On the primary set `wal_level = replica`, `max_wal_senders = 10`, `hot_standby = on`. Create a replication role.
3. Build the replica with `pg_basebackup -h pg-primary -D /var/lib/postgresql/data -U repl -R -X stream`, then start it. Confirm with `SELECT pg_is_in_recovery();` returning `t`.
4. Create a `books` table on the primary; confirm rows appear on the replica.
5. Build a lag monitor polling once per second on the replica:
   - time lag: `SELECT now() - pg_last_xact_replay_timestamp();`
   - byte lag on the primary: `SELECT client_addr, sent_lsn, replay_lsn, pg_wal_lsn_diff(sent_lsn, replay_lsn) FROM pg_stat_replication;`
6. **Baseline:** idle system. Record lag (should be single-digit milliseconds).
7. **Write load:** run `pgbench -i -s 100` then `pgbench -c 32 -j 4 -T 180`. Graph lag over the run. Also run a deliberately pathological load: a 2 M-row `INSERT ... SELECT` in one transaction.
8. **Read-after-write test:** in a loop, `INSERT` a row on the primary, immediately `SELECT` it on the replica, and record how many attempts/microseconds pass before it appears. Build a histogram over 1,000 iterations and extract p50, p95, p99 and max.
9. **Fix and verify:** implement LSN-token routing — capture `pg_current_wal_lsn()` after the write, and before reading on the replica call `SELECT pg_wal_replay_wait()` (PG 17) or poll `pg_last_wal_replay_lsn() >= :token`. Re-run step 8.
10. **Synchrony comparison:** set `synchronous_standby_names = 'ANY 1 (*)'` and `synchronous_commit = on`, then re-run step 8 and also re-measure `pgbench` TPS. Then stop the replica and observe what happens to writes on the primary.

**Success criterion:** you can present (a) a lag-vs-time graph showing lag rising from < 10 ms at idle to clearly measurable values (typically hundreds of ms to seconds) under `pgbench`, (b) a read-after-write histogram with a non-zero p99 in asynchronous mode, (c) zero read-after-write violations in 1,000 iterations after implementing LSN-token routing, and (d) a recorded TPS drop plus a demonstrated **write stall** when the synchronous replica is stopped — the concrete proof that synchronous replication converts durability risk into availability risk.

---

## 5. One mini system-design exercise

### Design the read path and caching strategy for the BookNook product catalogue

You are designing **only** the read path for product/catalogue pages. Produce a design document with a diagram, a capacity model with arithmetic, and justification for each numbered decision below.

#### Functional requirements

1. `GET /books/{isbn}` returns title, authors, description, cover URL, category tags, list price, current promotional price, average rating, review count, and an in-stock boolean.
2. `GET /categories/{slug}?page=n` returns a paginated list of 24 books with cover, title, price and rating.
3. `GET /books/{isbn}/related` returns 12 recommended books.
4. Price changes must become visible to all users within **60 seconds**.
5. In-stock status must be accurate within **10 seconds**; a user must never be able to add a long-out-of-stock item to the basket without seeing a warning.
6. Book descriptions and cover images may be up to **24 hours** stale.
7. Logged-in users see a personalised "recently viewed" strip on the product page.

#### Non-functional requirements

| Requirement | Target |
|---|---|
| p50 latency (product page HTML, user-perceived) | ≤ 120 ms |
| p99 latency (product page API) | ≤ 250 ms |
| Availability of the read path | 99.95 % monthly |
| Correctness | Price never stale by > 60 s; stock never stale by > 10 s |
| Origin database load | Primary must stay below 40 % CPU at peak |
| Cost | ≤ $6,000/month for the read path (compute + cache + CDN egress) |

#### Scale numbers

| Quantity | Value |
|---|---|
| Daily active users | 3,000,000 |
| Page views per DAU per day | 9 |
| Read:write ratio on catalogue data | 500 : 1 |
| Catalogue size | 4,000,000 titles |
| Mean serialised product record | 14 KB (JSON, excluding images) |
| Mean cover image | 95 KB |
| Share of reads hitting the top 50,000 titles | 86 % |
| Peak-to-average traffic multiplier | 4.5× (18:00–22:00 local, plus 9× on Black Friday) |
| Origin DB read (uncached, product record assembly across 5 tables) | 38 ms p50, 140 ms p99 |
| Redis GET, same AZ | 0.9 ms p50, 4 ms p99 |
| CDN hit from POP | 14 ms p50 |
| Cross-AZ RTT | 1.1 ms |

#### Out of scope

- Search and faceted browse (assume Elasticsearch, already built).
- The write path: catalogue ingestion, pricing updates, inventory events (assume a Kafka topic `catalogue.changes` exists and is reliable).
- Checkout, basket, payments, user accounts, authentication.
- Image generation, resizing and storage (assume S3 + an existing image CDN).
- Multi-region. Design for one region.
- Analytics, recommendation model training.

#### Decisions you must justify with calculations

1. **Derive peak RPS.** Compute average RPS from DAU and page views, then peak using the 4.5× multiplier and again using the 9× Black Friday multiplier. State both numbers explicitly. Then compute the RPS reaching the database under your design.
2. **Size the cache to the working set.** Using the 86 %/50,000 figure and the 14 KB record size, compute the memory needed for the hot set, for the top 500,000 titles, and for the full catalogue. Choose a size and state the hit ratio you expect at that size.
3. **Compute effective latency** for your chosen hit ratio using `h × hit_lat + (1 − h) × miss_lat`, with both the Redis and CDN layers. Show that the result satisfies the p50 target, and separately argue the p99 — noting that p99 is dominated by misses, so `p99 ≈ miss-path p99` whenever `(1 − h) > 0.01`.
4. **Decompose the product record by TTL class.** Requirement 4 says 60 s for price, 6 says 24 h for description, 5 says 10 s for stock. Decide whether to cache one composite object at the shortest TTL (60 s / 10 s) or to split into independently-TTL'd fragments. Quantify the cost of each: the composite forces the whole 14 KB to be re-fetched every 60 s; the fragmented approach adds N cache round trips per page. Compute both.
5. **Decide where stock is read from.** It cannot live in a 60 s cache. Choose between: a 5-second-TTL dedicated cache entry, a separate low-latency store read on every request, or a client-side fetch after page render. Justify against requirement 5 and the p50 budget.
6. **Choose a cache pattern** (cache-aside, read-through, write-through, write-behind, refresh-ahead) for each of: the product record, the category listing page, and the related-books list. Justify each separately — they should not all be the same.
7. **Design invalidation.** The `catalogue.changes` Kafka topic emits events. Enumerate the complete dependency fan-out for a single price change on one ISBN: which cache keys, at which layers, must be invalidated or allowed to expire? Decide between explicit purge, versioned keys and TTL for each layer, and state the propagation delay you are accepting.
8. **Design the CDN layer.** State the exact cache key (URL components, which headers are included, which query parameters are stripped). Explain how requirement 7 (personalised strip) is satisfied without destroying the hit ratio — the expected answer involves separating the cacheable shell from the personalised fragment. Compute the CDN hit ratio you need to meet the $6,000 budget given egress pricing of roughly $0.085/GB (typical) and your byte volume.
9. **Protect against stampede and penetration.** Specify the mechanism, parameters and expected effect for: a hot key during a Black Friday promotion on one title, mass expiry of the top 50,000 records, and a scraper requesting 500,000 non-existent ISBNs. Give concrete TTLs, lock durations and Bloom filter sizing (use ~10 bits/element for ~1 % false positives — compute the memory for 4 M titles).
10. **Size the origin and prove the 40 % constraint.** Given your computed cache-miss RPS and a 38 ms p50 origin read, use Little's Law to derive required concurrency, choose a replica count and connection-pool size, and show the primary stays under 40 % CPU. State explicitly how many read replicas you need and what their maximum tolerable replication lag is before you eject them from the read pool.
11. **Define degradation behaviour.** Specify exactly what the system serves when: Redis is entirely unavailable, a read replica lags by 30 s, and the origin database is down. For each, state whether you serve stale, serve partial, or fail — and what the user sees. Requirement: "Redis down" must not cascade into a database outage; show the arithmetic of what happens to origin RPS if you simply pass all misses through, and specify the concurrency limit or load-shedding threshold that prevents it.
12. **Produce the final cost model.** Line-item compute, cache memory, CDN egress and database instances against the $6,000 ceiling. If you exceed it, state which requirement you would renegotiate first and why.

**Deliverable:** one document, one architecture diagram, a capacity table with all arithmetic shown, and twelve numbered justifications. Submit it and it will be reviewed against each requirement.

---

## 6. Common interview questions

| # | Question | What the interviewer is actually probing |
|---|---|---|
| 1 | "When would you scale vertically instead of horizontally?" | Whether you understand that statefulness, engineering cost and time-to-fix often beat theoretical scalability — and that you know the ceilings. |
| 2 | "Walk me through making this application tier stateless." | Whether you can *enumerate* state (sessions, uploads, locks, counters, WebSockets) rather than just saying "put sessions in Redis". |
| 3 | "How does a load balancer know a server is unhealthy, and how fast?" | Whether you can produce `threshold × interval + timeout` and discuss the false-positive/detection-speed trade-off. |
| 4 | "What is the difference between L4 and L7 load balancing, and when does the choice actually matter?" | Whether you cite concrete consequences (keep-alive imbalance, TLS inspection, path routing, packet rate) rather than reciting the OSI model. |
| 5 | "Least connections or round robin — which do you pick and why?" | Whether you reason about request-cost variance, and whether you know the black-hole failure mode. |
| 6 | "You added a cache and p99 got worse. Why?" | Whether you understand that misses pay cache probe + origin, and that caching shifts load patterns, adds a dependency and can mask a capacity problem until it fails. |
| 7 | "How do you choose a TTL?" | Whether you frame TTL as a business staleness budget per data class, not a single global number — and whether you mention jitter. |
| 8 | "Explain cache stampede and three different fixes." | Whether you can distinguish single-flight, leases, XFetch and `stale-while-revalidate`, and say which is appropriate at which layer. |
| 9 | "How would you cache data that is read constantly and updated constantly?" | Whether you can decompose by field TTL class, or correctly conclude that some fields should not be cached. |
| 10 | "Design cache invalidation for a product whose author changes their name." | Whether you can enumerate dependency fan-out and reach for tag/surrogate-key invalidation or versioned keys. |
| 11 | "What is a CDN cache key and how do you destroy a hit ratio with it?" | Practical CDN experience: cookies, `Vary`, tracking query params, unnormalised ordering. |
| 12 | "Read replicas: what do they solve and what do they not solve?" | Whether you know they scale reads but *increase* total write work and do nothing for write throughput. |
| 13 | "Synchronous or asynchronous replication for a payments table?" | Whether you can articulate RPO vs write availability as a multiplicative-availability trade-off, and reach for semi-sync/quorum. |
| 14 | "A user updated their profile and does not see the change. Diagnose and fix." | Replication lag recognition, plus the full menu of read-after-write fixes with their costs. |
| 15 | "How do you choose a shard key?" | Cardinality, access-distribution evenness, and query alignment — plus which query you are sacrificing. |
| 16 | "You sharded by user ID and one customer is 30 % of your traffic. Now what?" | Hot-partition handling: salting, dedicated shard, caching, and the read-side cost of each. |
| 17 | "How do you reshard a live system with zero downtime?" | Dual-write → backfill → verify → gradual read cutover → cleanup, and whether you mention logical shards to avoid the problem entirely. |
| 18 | "Explain consistent hashing and why virtual nodes exist." | Whether you can state `K/N` precisely and explain variance reduction — not just draw a circle. |
| 19 | "Why is a cross-shard query slower than its slowest shard suggests?" | Tail latency amplification under fan-out, and whether you know hedged requests and denormalisation as mitigations. |
| 20 | "Your autoscaler is flapping. Fix it." | Hysteresis, separate out/in thresholds, cooldowns, better metric choice (concurrency over CPU), and warm-up. |
| 21 | "Traffic will 10× in 60 seconds. Can autoscaling handle it?" | Honesty about boot latency — the expected answer is "no, you need pre-provisioned headroom or a queue plus load shedding". |
| 22 | "Should we go multi-region?" | Whether you ask for the actual driver (latency, DR, compliance) and can price the consistency and cost consequences, rather than defaulting to yes. |
| 23 | "What breaks first when you autoscale the app tier to 200 instances?" | Database connection exhaustion, cold caches, service-discovery churn, downstream rate limits — systems thinking beyond the tier in question. |
| 24 | "Give me three things that make a system *less* available when you add them for scale." | Series availability multiplication, synchronous replication, and any new dependency on the critical path (gateway, cache, discovery). |

---

## 7. Topics to revisit if you struggled

| If you found this hard | Re-read | Why it matters later |
|---|---|---|
| Deciding between adding a bigger machine and adding more machines | **L18–L19** (vertical/horizontal scaling), plus Phase 1 Little's Law | Phase 3's relational-vs-NoSQL decision is largely a question of whether your write path can be scaled vertically; Phase 5 cost modelling is built on the same arithmetic. |
| Identifying hidden state in an application server | **L20** (stateless servers) | Phase 3 session and user-data storage, and Phase 4's microservice decomposition, both assume you can reliably classify state. Container orchestration in Phase 5 breaks outright on hidden state. |
| Series vs parallel availability arithmetic | **L19**, plus Phase 1 availability maths | Phase 4's distributed-transaction and saga material depends on reasoning about compounded failure probability across services. |
| Choosing between L4 and L7 | **L22** (Layer 4 vs Layer 7) | Phase 4's service mesh, mTLS and sidecar proxy topics are entirely L7 concepts; you cannot evaluate Envoy without this. |
| Why least connections can make an outage worse | **L23–L24** (strategies, health checks) | Phase 5's circuit breakers, outlier ejection and cascading-failure prevention build directly on this failure mode. |
| Setting health check parameters | **L24** (health checks and failover) | Phase 5's observability and SLO chapters use detection time as a direct input to MTTR and error budgets. |
| Deciding what belongs in a gateway vs a service | **L25** (reverse proxies and API gateways) | Phase 4's API design, versioning and BFF patterns; Phase 5's rate limiting and authentication placement. |
| The effective-latency formula and its non-linearity | **L26** (caching fundamentals) | Every latency budget in Phases 3–5 is built from this; you cannot do Phase 5 capacity planning without it. |
| Sizing a cache (working set vs total data) | **L26–L27** | Phase 3's index and buffer-pool sizing uses the identical working-set argument; Phase 5 cost modelling depends on it. |
| Choosing a cache pattern for a given field | **L28** (cache-aside/read-through/write-through/write-behind) | Phase 4's event-driven architecture and CQRS read models are generalisations of write-behind and materialised views. |
| Invalidation and dependency fan-out | **L29** (invalidation and TTL) | Phase 4's event-driven invalidation, change-data-capture and outbox patterns are the production answer to exactly this problem. |
| Stampede, penetration, hot keys | **L30** | Phase 5's load shedding, rate limiting, circuit breaking and retry-storm prevention are the same thundering-herd family. |
| CDN cache keys and personalisation | **L31** (CDNs and edge caching) | Phase 4's BFF and edge-rendering patterns; Phase 5's cost optimisation, where egress is often the largest line item. |
| Replication topologies and what they scale | **L32** (database replication) | Phase 3's entire database chapter — primary/replica, read scaling, and the leap to NoSQL — assumes fluency here. |
| RPO/RTO and the synchrony trade-off | **L33** (sync vs async replication) | Phase 3's consistency models and Phase 4's CAP/PACELC discussion are the formal version of this trade-off. |
| Replication lag and read-your-writes | **L34** | Phase 3's consistency models (monotonic reads, causal consistency) and Phase 4's eventual-consistency UX patterns depend on it entirely. |
| Choosing a shard key | **L35–L36** (partitioning, sharding schemes) | Phase 3's NoSQL partition keys (DynamoDB, Cassandra) are the *same decision* with less tooling to rescue a bad choice. |
| Resharding safely | **L37** | Phase 5's zero-downtime migration, dual-write and backfill patterns reuse this procedure for schema changes and store migrations. |
| Scatter-gather and tail amplification | **L37** | Phase 4's microservice fan-out and Phase 5's distributed tracing and tail-latency work are the same phenomenon at service granularity. |
| Consistent hashing mechanics | **L38** | Phase 3's Cassandra/DynamoDB partitioning, and Phase 4's sharded-queue and partitioned-consumer designs. |
| Autoscaling metrics and hysteresis | **L39** | Phase 5's capacity planning, Kubernetes HPA, and cost optimisation are a direct continuation. |
| Multi-region patterns and conflict handling | **L39** | Phase 4's CRDTs, conflict resolution and global consistency; Phase 5's disaster recovery and compliance chapters. |
| Reasoning about "what breaks when I add this for scale" | **L19, L24, L33, L39** together | This is the core skill assessed in every Phase 5 design review; each of these lessons contains one instance of the general principle that scaling adds failure modes. |

---

**NEXT LESSON: Phase 3, Topic 1 — Relational databases**

