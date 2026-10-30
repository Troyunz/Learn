# Lesson 44 — Document Databases

*Phase 3, Topic 5*

## 1. Simple definition

A **document database** stores data as **documents**: self-describing, nested records — usually JSON or BSON — where each document carries its own field names and structure, and a whole object graph can live inside a single record.

Lesson 43 covered key-value databases, where the value is an opaque blob the database refuses to look inside. A document database is the next step: **the value stops being opaque.** The database can read inside it, index its fields, filter on nested properties, and update one field without rewriting the whole record.

Terms defined before use:
- **Document**: one record. In MongoDB it is a BSON object with a unique `_id`. Roughly "one row", except it can contain arrays and nested objects.
- **BSON** (Binary JSON): MongoDB's on-disk and on-wire encoding of JSON. It adds types JSON lacks — `Date`, `ObjectId`, `Decimal128`, `Binary`, 64-bit integers — and is length-prefixed so a field can be skipped without parsing it.
- **Collection**: a named group of documents. Roughly "one table", except there is no enforced column list.
- **Self-describing**: every document repeats its own field names. `{"title": "Dune"}` carries the string `title` on disk. A relational row does not — the column name lives once in the table definition.
- **Schema-less / flexible schema**: the database does not require two documents in a collection to have the same fields. This is the single most misunderstood property in this lesson, and §15 explains why "schema-less" is a lie told to beginners.

The one-sentence version: **a document database lets you store the shape your application already uses, and query inside that shape.**

## 2. Why it exists

Two distinct pressures produced document databases. They are often blurred together; they are not the same thing.

### Pressure 1 — object-relational impedance mismatch

**Impedance mismatch** is the friction between how application code represents data (objects, with nested objects and lists inside them) and how a relational database represents data (flat rows in normalised tables, Lesson 41).

Take BookNook's book-detail page. In application code, a book is one object:

```
Book {
  isbn, title, price,
  authors: [ {name, bio}, ... ],
  formats: [ {type, price, stock}, ... ],
  categories: [ "sci-fi", "classics" ],
  dimensions: {height_mm, width_mm, weight_g}
}
```

In the Lesson 41 relational schema that is **six tables**: `books`, `authors`, `book_authors`, `formats`, `categories`, `book_categories`. Every read reassembles the object from six places; every write decomposes it back into six places. The code that performs this translation is an **ORM** (Object-Relational Mapper, e.g. Hibernate, ActiveRecord, SQLAlchemy). ORMs work, but they leak: they generate surprising SQL, they cause the **N+1 query problem** (one query for 20 books, then 20 more queries for each book's authors), and they force developers to hold two mental models at once.

A document database removes the translation step. The object you have in memory is, near enough, the record on disk.

### Pressure 2 — aggregate-oriented design

This is the deeper and more architectural reason.

An **aggregate** (the term comes from Domain-Driven Design) is a cluster of data that is **always loaded together, updated together, and whose consistency boundary is one unit**. A BookNook order — the order, its line items, the shipping address snapshot, the payment status — is an aggregate. Nobody ever loads line item 3 of order 991 without the order.

Document databases are **aggregate-oriented**: they make the aggregate the unit of storage, the unit of atomicity, and the unit of distribution. Three consequences follow immediately:

1. **One read fetches the whole aggregate.** No joins, no N+1, one disk seek region.
2. **A single-document write is atomic without a transaction.** Updating the order and its line items is one write, so it cannot half-apply.
3. **Aggregates shard cleanly.** Because nothing inside a document references another document's internals, a document can move to another machine freely (Lesson 36, sharding). Joins are what make sharding painful; an aggregate-oriented model mostly avoids needing them.

That third point is the real reason document databases spread during the 2010s scale-out era. The flexible schema got the marketing; **shardability got the adoption.**

### What was actually wrong with relational

Nothing, for many workloads — Lesson 42 covered this honestly. The genuine pain points document databases targeted:

| Relational pain | Document answer |
|---|---|
| Six-table join per page view | One document read |
| `ALTER TABLE` on a 400 M-row table locks or takes hours | Add a field to new documents; old ones simply lack it |
| Sparse data (300 possible attributes, each product uses 12) means 300 mostly-NULL columns | Each document carries only the fields it has |
| Deeply variable structure (an e-book has `drm_type`, a hardback has `jacket_finish`) | Natural |
| Joins block clean horizontal sharding | Aggregates shard cleanly |

## 3. Real-world analogy

**A filing cabinet of client folders, versus a warehouse of index cards.**

A relational database is the index-card warehouse. One drawer holds name cards, another holds address cards, another holds invoice cards. Every card has an ID so cards can be cross-referenced. Nothing is duplicated: a client's address is written exactly once. To answer "show me everything about client 4471," a clerk must visit six drawers and assemble the answer on a desk. To correct a misspelled street name, the clerk edits exactly one card and every cross-reference is instantly correct.

A document database is the filing cabinet of **folders**. One folder per client. Inside the folder: their details, their addresses, a stapled stack of invoices, their contact notes. To answer "show me everything about client 4471," the clerk pulls **one folder**. Done. But if the same company's address appears in forty client folders, correcting it means opening forty folders — and if the clerk is interrupted after the twenty-third, the cabinet now holds two versions of the truth.

The folder is also a physical object with a physical limit. Stuff enough invoices in and it will not close. That is not a metaphor — it is MongoDB's 16 MB document limit, §4.

**Where the analogy breaks down:**

| Filing cabinet | Document database |
|---|---|
| A folder is pulled whole; you cannot read one page without opening it | A **projection** returns only the fields you ask for, and an index can answer a query without touching the document at all (a *covered query*) |
| Folders are findable only by the label on the tab | Any field, including fields nested five levels deep inside an array, can be indexed and queried |
| Correcting forty folders is forty manual, non-atomic edits | A single `updateMany` is one command — but it is still **not atomic across documents** unless wrapped in a multi-document transaction (§4), so the "interrupted clerk" risk is real, not imaginary |
| A folder has no rules about what goes in it | A collection can have a **JSON Schema validator** attached that rejects malformed documents — the flexible schema is opt-out, not mandatory |
| The cabinet sits in one room | The collection is spread across dozens of machines by **shard key**, and choosing that key badly is the most expensive mistake in this lesson |
| Pulling a folder is one action with one cost | A 16 MB document read costs ~16 MB of I/O, network, and RAM **even if you wanted three fields** — unless you project, and even then the server must read the document off disk first |

That last row matters more than it looks. The analogy suggests "one folder = cheap". In reality, **the cost of a document read scales with the size of the document, not the size of the answer.** That single fact drives most of §4's modelling rules.

## 4. Detailed technical explanation

This section uses **MongoDB** as the concrete system, because it is the dominant document database and because its specifics (16 MB limit, `_id`, aggregation pipeline, replica sets, write concern) are what interviewers actually ask about. Alternatives are in §12.

### 4.1 The document and `_id`

Every MongoDB document has a field named **`_id`**, which is:
- The **primary key**. Unique within the collection, immutable after insert.
- **Automatically indexed.** You cannot drop this index.
- Auto-generated as an **ObjectId** if you do not supply one. An ObjectId is 12 bytes: a 4-byte Unix timestamp (seconds), a 5-byte random value per process, and a 3-byte incrementing counter. Two important consequences: ObjectIds are **roughly time-ordered** (useful for `sort({_id: -1})` as a cheap "newest first"), and they are **monotonically increasing**, which makes them a *bad* shard key — see §4.6.

You may set `_id` yourself to anything unique: an ISBN, a UUID, a composite string like `"user:4471:2026-10"`. Doing so saves an index when your natural key is already unique.

### 4.2 The 16 MB document limit

**A single BSON document cannot exceed 16 MB.** This is a hard, non-configurable limit.

It is not an arbitrary annoyance — it is a design guardrail. It exists because the server must materialise a document in RAM to work on it, and because wire-protocol buffers and replication oplog entries must stay bounded. A 2 GB document would make every operation touching it catastrophically expensive.

The practical reading: **if your document design can grow without bound, it is wrong.** Not "will hit a limit someday" — *wrong now*, because long before 16 MB the document is already slow (§4.9).

For genuinely large binary blobs, MongoDB offers **GridFS**, which chunks a file into 255 KB documents in two collections (`fs.files`, `fs.chunks`). In practice, most teams put large blobs in object storage (S3) and store the URL in the document instead.

### 4.3 Embedding versus referencing — the central decision

**This is the modelling decision in document databases.** Almost every document-database performance disaster traces back to getting it wrong.

- **Embedding**: nest the related data inside the parent document.
- **Referencing**: store the child's `_id` in the parent (or the parent's `_id` in each child) and fetch separately, or use `$lookup` (§4.5) to join server-side.

Here are the decision criteria. Each must be checked; a single criterion is not enough.

**Criterion 1 — access pattern.** Is the child data *always* needed when the parent is read? If yes → embed. If it is needed 5% of the time → reference, because the other 95% of reads pay for bytes they discard.

**Criterion 2 — cardinality.** The useful categories:

| Relationship | Guidance |
|---|---|
| **One-to-few** (1 to ~2–20, bounded by nature) | Embed. A book has 1–5 authors. An order has 1–30 line items. |
| **One-to-many** (1 to hundreds/low thousands, bounded in practice) | Reference, or embed a **subset** (§4.8) — e.g. store the 5 most recent reviews inline plus a count, and reference the rest. |
| **One-to-squillions** (unbounded: events, log lines, reviews on a bestseller) | Reference, always. Put the parent `_id` on the child and index it. Embedding here is the unbounded-array failure of §4.9. |

**Criterion 3 — update frequency and ratio.** If the embedded data changes far more often than the parent is read, embedding causes constant document rewrites. Worse: if the same data is embedded in many parents, **every update must touch every copy**. Embedded data is **denormalised data** (Lesson 41), and denormalisation always trades write cost and consistency risk for read speed.

**Criterion 4 — document size.** Embedding must keep the parent comfortably under 16 MB — and realistically under a few hundred KB. The question is not "does it fit today" but "what is the maximum it can ever reach, and what does the 99th-percentile document look like?"

**Criterion 5 — atomicity requirement.** A single-document write is atomic. If two pieces of data must *never* be observed in an inconsistent state relative to each other, embedding gives you that free. An order's `status` and its `line_items` is a classic case.

**Criterion 6 — independent access.** Can the child be queried on its own, without the parent? Embedded documents can be queried and indexed individually, but they cannot be *returned* on their own cheaply, and they can never be referenced by a third entity. If three parents need to point at the same child, reference it.

The compressed rule, used honestly rather than as a slogan: **embed when the data is read together, bounded in size, and owned by exactly one parent. Reference when it is large, unbounded, shared, or independently accessed.**

### 4.4 Indexes

MongoDB indexes are B-trees, the same structure as Lesson 41's relational indexes. The document-specific types:

- **Single-field**: `{price: 1}` (1 = ascending, -1 = descending).
- **Compound**: `{category: 1, price: -1}`. Follows the **prefix rule** — this index serves queries on `category`, and on `category + price`, but **not** on `price` alone. Field order in a compound index is therefore a design decision, not a formality. The common guidance is **ESR**: **E**quality fields first, then **S**ort fields, then **R**ange fields.
- **Multikey**: automatically created when you index a field that holds an array. MongoDB stores **one index entry per array element**. Indexing `categories: ["sci-fi", "classics", "1960s"]` creates three entries pointing at one document. This is powerful — it makes `find({categories: "sci-fi"})` an index hit — and it is also a cost multiplier: an array of 500 tags means 500 index entries per document. You cannot create a compound index over two array fields (the entry count would be the product of their lengths).
- **Text**, **geospatial (2dsphere)**, **hashed** (used for shard keys), **partial** (`{partialFilterExpression: {status: "active"}}` — index only matching documents, smaller and cheaper), **TTL** (automatically delete documents N seconds after a date field — ideal for sessions and event expiry).

**Covered query**: if every field the query needs (filter, sort, and projection) is present in a single index, MongoDB answers from the index alone and never reads the document. This is the cheapest possible query and worth designing for on hot paths.

### 4.5 The aggregation pipeline

The **aggregation pipeline** is MongoDB's query language for anything beyond a simple find. Documents flow through an ordered array of **stages**; each stage transforms the stream and passes it to the next. It is conceptually Unix pipes over documents, and it is the rough equivalent of SQL's `GROUP BY`, `HAVING`, window functions, and joins.

Key stages:

| Stage | Does | SQL analogue |
|---|---|---|
| `$match` | Filter documents | `WHERE` |
| `$project` | Reshape / select fields | `SELECT` |
| `$group` | Aggregate by a key | `GROUP BY` |
| `$sort` | Order | `ORDER BY` |
| `$limit` / `$skip` | Paginate | `LIMIT` / `OFFSET` |
| `$lookup` | Left outer join to another collection | `LEFT JOIN` |
| `$unwind` | Explode an array into one document per element | (no direct equivalent) |
| `$facet` | Run several sub-pipelines over the same input | multiple queries |
| `$merge` / `$out` | Write results to a collection | `CREATE TABLE AS` |

Two rules that separate a fast pipeline from a disastrous one:

1. **`$match` and `$limit` as early as possible.** The optimiser moves some of these automatically, but not across `$group` or most `$project` stages. A `$match` after a `$group` has already paid to aggregate everything.
2. **`$lookup` is not a real join.** It executes roughly as a per-document lookup into the foreign collection. Without an index on the foreign field it is a collection scan *per input document*. Before MongoDB 5.1 it also could not use a sharded foreign collection in several cases. `$lookup` exists so you are not stuck; it is not a reason to model relationally.

Pipelines spill to disk when a stage exceeds 100 MB of RAM only if `allowDiskUse: true` is set; otherwise the query errors. This is a deliberate guardrail against accidental full-collection sorts.

### 4.6 Replica sets, elections, and durability

A **replica set** is MongoDB's replication unit (Lesson 33): typically **one primary** and **two or more secondaries**.

- All writes go to the primary. This is **single-leader replication** — exactly the model from Lesson 33.
- The primary records every write in the **oplog** (operations log), a capped collection. Secondaries tail the oplog and apply operations asynchronously.
- Members exchange **heartbeats** every 2 seconds (default). If a secondary hears nothing from the primary for `electionTimeoutMillis` (default **10 seconds**), it calls an **election**.
- Elections use a Raft-like protocol: a candidate needs votes from a **majority** of voting members. Majority is why replica sets should have an **odd** number of voters — 3 voters tolerate 1 failure, 4 voters also tolerate only 1 (3 of 4 needed), so the fourth voter adds cost and no availability.
- Typical failover window: **~10–12 seconds** of no writes, assuming default settings. During this window the application receives errors or retries. Modern drivers implement **retryable writes**, which transparently retry one write through a failover — but only for idempotent single-document operations.

**Write concern (`w`)** — how many members must acknowledge before the write is reported as successful:

| Write concern | Meaning | Durability | Typical latency cost |
|---|---|---|---|
| `w: 0` | Fire and forget; the driver does not wait for acknowledgement | None. A write can silently vanish | ~0 ms added |
| `w: 1` | Primary acknowledged in memory | Lost if the primary crashes before the journal flush and a failover occurs | ~0–1 ms |
| `w: "majority"` | A majority of members have applied it | Survives loss of the primary — this is the only setting that guarantees the write is not rolled back | +1 network round trip to the slowest member of the majority, typically **+1–3 ms** same-AZ, **+30–80 ms** cross-region |
| `j: true` | The member has flushed to its on-disk journal before acknowledging | Survives process crash and power loss on that member | +0–10 ms depending on journal commit interval and disk |

`w: "majority"` has been the MongoDB default since version 5.0. Before that the default was `w: 1`, which is the source of a great many "we lost writes during a failover" stories.

**Read preference** — which member a read goes to:

| Read preference | Reads from | Consistency you get |
|---|---|---|
| `primary` (default) | Primary only | Read-your-writes, linearizable with `readConcern: "linearizable"` |
| `primaryPreferred` | Primary; secondaries if no primary | Normally strong; silently degrades to stale during failover |
| `secondary` | Secondaries only | **Eventually consistent** — may be seconds stale under replication lag |
| `secondaryPreferred` | Secondaries; primary if none available | Eventually consistent |
| `nearest` | Lowest network latency member, primary or secondary | Eventually consistent; lowest latency |

**Read concern** is orthogonal and controls *which version* of the data you see: `local` (whatever this node has), `majority` (only data acknowledged by a majority — cannot be rolled back), `linearizable` (strongest; primary only; requires a confirming read round trip and is expensive), `snapshot` (a consistent point-in-time view, used in transactions).

The pairing to remember: **`w: "majority"` + `readConcern: "majority"` + `readPreference: primary`** gives you strong guarantees at the cost of latency. Any use of secondary reads means accepting staleness, which is a CAP-adjacent trade-off examined fully in **(Phase 3, Topic 20 — CAP theorem)**.

### 4.7 Sharding and chunk migration

Sharding (Lesson 36) splits a collection across multiple replica sets.

Components:
- **Shard**: a replica set holding a subset of the data.
- **`mongos`**: a stateless router the application connects to. It knows which shard holds which range and forwards accordingly.
- **Config servers**: a replica set holding the cluster metadata — the shard key ranges and chunk map.

**Shard key**: the field (or compound set of fields) that determines placement. The collection is divided into **chunks** (contiguous shard-key ranges, default max 128 MB). A **balancer** process migrates chunks between shards to keep them even.

Shard key selection, which is effectively irreversible in impact even though MongoDB 5.0+ allows **resharding** (an expensive, full-rewrite operation):

| Property | Why it matters | BookNook example |
|---|---|---|
| **High cardinality** | Low cardinality caps the number of chunks. Sharding on `country` with 3 values gives at most 3 chunks and no further splitting | `order_id` ✅, `country` ❌ |
| **Even frequency distribution** | A key value that appears in 40% of documents creates a **jumbo chunk** that cannot be split | `customer_id` is risky if one wholesale customer places 30% of orders |
| **Non-monotonic** | A monotonically increasing key (timestamp, ObjectId, auto-increment) sends **every new write to the single shard holding the top range** — a **hotspot**. Writes do not scale at all | `created_at` ❌; `hashed(order_id)` ✅ |
| **Present in queries** | Queries that include the shard key are **targeted** (one shard). Queries that don't are **scatter-gather** (broadcast to every shard, merged by mongos) — latency becomes the slowest shard's latency | Shard on `customer_id` if most queries are "my orders" |

**Hashed sharding** (`{_id: "hashed"}`) solves monotonicity by hashing the key before placement, distributing writes evenly. The cost: **range queries become scatter-gather**, because adjacent values land on different shards. This is the same spread-versus-locality trade-off as consistent hashing in Lesson 38 — indeed MongoDB's range map and Cassandra's ring (Lesson 45) are two answers to the same question.

**Chunk migration** moves a chunk between shards: the destination copies the documents, catches up on changes, then a brief metadata handover occurs. During migration the source still serves reads. Migrations consume I/O and network, which is why balancing windows are often restricted to off-peak hours.

### 4.8 Schema design patterns

These are named, reusable solutions. Knowing the names is genuinely useful in interviews.

**Bucket pattern.** Instead of one document per reading, store one document per *time bucket* containing an array of readings. For BookNook's page-view events: one document per `(book_id, hour)` holding up to 3,600 entries. Turns 3,600 inserts into ~1 insert plus 3,599 `$push` updates, and collapses 3,600 sets of field-name overhead and `_id` indexes into one. **Dramatically reduces index size and storage overhead for time-series data.** MongoDB 5.0+ ships native **time-series collections** that implement bucketing automatically.

**Subset pattern.** Keep the hot subset embedded, the rest in a separate collection. A book document carries `top_reviews` (the 5 most helpful) plus `review_count`; the full 12,000 reviews live in a `reviews` collection. The detail page — the 95% case — is one read. "See all reviews" is a second query that 5% of users make. **Directly targets the "document read costs scale with document size" problem.**

**Extended reference pattern.** Reference, but duplicate the handful of fields you always display. An order line item stores `{book_id, title, author_name, price_paid}` — not just `book_id`. Avoids the join on the order-history page. Note that for an order this duplication is *not* a denormalisation smell at all: `price_paid` and the title **must** be point-in-time snapshots, because the order must show what was bought at the price paid, even if the book is later renamed or repriced.

**Computed pattern.** Store the result of an expensive computation rather than recomputing it. `average_rating` and `review_count` on the book document, updated with `$inc` on each new review. Reads are free; writes do slightly more work; the value can briefly drift if an update fails.

**Outlier pattern.** Design for the 99%, and handle the 1% differently. Most books have fewer than 20 reviews, so embed them. A bestseller with 50,000 gets an `has_extras: true` flag, and the application knows to look in an overflow collection. Prevents the rare giant document from dictating the design for everything else.

Others worth knowing by name: **polymorphic** (different document shapes in one collection, distinguished by a `type` field), **attribute** (turn sparse key-value attributes into an array of `{k, v}` pairs so one index covers all of them), **approximation** (only persist the view counter every 10th increment — 90% fewer writes for a number nobody needs exact), **schema versioning** (a `schema_version` field so application code can handle old and new shapes during a rolling migration).

### 4.9 Unbounded array growth — the classic failure

This deserves its own subsection because it is the single most common document-database disaster.

The seductive design: embed comments in a post, events in a user, reviews in a book. It works beautifully in development with 10 elements. Then:

1. **The document grows.** Every read of the parent now transfers the entire array, even with a projection — the storage engine must read the full document off disk before projecting.
2. **Every update rewrites more.** WiredTiger updates are copy-on-write at the document level. Appending a 200-byte review to a 4 MB document writes ~4 MB, not 200 bytes. **Write amplification** climbs linearly with document size.
3. **Document moves and fragmentation.** A growing document eventually no longer fits its allocated space and must be relocated, with knock-on index updates.
4. **The oplog explodes.** Each of those rewrites replicates. A 4 MB document updated 100 times per minute generates ~400 MB/min of oplog traffic, saturating replication and shortening the oplog's time window (which is how long a secondary can be offline before needing a full resync).
5. **Multikey index entries multiply.** An index on a field inside the array creates one entry per element.
6. **Then it hits 16 MB and writes start failing outright** — in production, usually at 3 a.m., for your most important customer, because the biggest document always belongs to the biggest customer.

The failure is not "it hit the limit". The failure is that **performance degrades smoothly and continuously long before the limit**, so monitoring shows a slow creep rather than an alarm, and by the time it is obvious the data model is load-bearing across the whole application.

The fixes: reference instead of embed; the subset pattern; the bucket pattern; or `$slice` on `$push` to maintain a capped array (`{$push: {recent: {$each: [x], $slice: -50}}}` keeps only the newest 50).

### 4.10 Transactions

MongoDB added **multi-document ACID transactions** in **4.0** (replica sets) and **4.2** (sharded clusters). They are real transactions with snapshot isolation, and they are a genuine capability — but they carry real costs:

- They hold a snapshot, which makes the storage engine retain older versions and grows the WiredTiger cache.
- Default **60-second transaction lifetime** (`transactionLifetimeLimitSeconds`); exceeding it aborts.
- They can abort on **write conflict** when two transactions touch the same document, so application code **must** implement retry loops. This is not optional — a transaction without a retry handler is a latent bug.
- Across shards they require **two-phase commit**, which multiplies the latency and adds a coordinator that can stall participants.
- Throughput for transactional workloads is materially lower than for single-document writes — commonly cited as a **2–10× cost**, depending on contention and shard count.

The correct mental model: **transactions are an escape hatch, not a foundation.** If your design needs them on the hot path, the aggregate boundaries are probably wrong — the data that must change together should likely be in one document. MongoDB's own guidance says the same thing.

## 5. Request / data flow

A BookNook book-detail page request against a sharded MongoDB cluster, start to finish:

1. **Client requests** `GET /books/9780441013593`. The app server needs title, price, authors, formats, top reviews, and rating.
2. **Application calls the driver**: `db.books.findOne({_id: "9780441013593"})`. The driver has already discovered the cluster topology via the connection string and maintains pooled TCP connections.
3. **Driver selects a server.** Read preference is `primary` (default), so it picks the `mongos` router and, through it, the primary of the owning shard.
4. **`mongos` consults the cached chunk map** (sourced from the config servers). The shard key is `_id`, and the query contains `_id`, so this is a **targeted** query to exactly one shard. No scatter-gather.
5. **Request is serialised as BSON** over the MongoDB wire protocol (`OP_MSG`) and sent to shard 3's primary.
6. **Query planner on the shard** picks a plan. `_id` is indexed, so it is an index equality lookup. If several plans were viable the planner would race them briefly and cache the winner.
7. **Index seek** on the `_id` B-tree returns a record ID — typically 2–4 page reads, usually served from the WiredTiger cache.
8. **Document fetch** from the WiredTiger storage engine. On cache hit: microseconds. On cache miss: one or more disk reads (~0.1–0.5 ms on NVMe SSD, typical).
9. **Projection applied** server-side if the query specified one, reducing bytes on the wire but not bytes read from disk.
10. **Read concern honoured.** With `readConcern: "majority"` the node returns the latest majority-committed snapshot rather than its newest local write.
11. **Response flows back** shard → `mongos` → driver as a BSON document in a cursor batch (default first batch 101 documents or 16 MB, whichever first; irrelevant here since it is one document).
12. **Driver deserialises BSON** into a native application object. For large documents this deserialisation is non-trivial CPU — it is a real and frequently forgotten cost.
13. **Application renders the page.** One database round trip has produced the whole view, versus six joined tables in the Lesson 41 relational design.
14. **Write path for comparison** — a customer posts a review: the app issues `updateOne` on the book document with `$push ... $slice: -5` for `top_reviews` and `$inc` for `review_count`, **plus** an `insertOne` into `reviews`. The primary applies the update atomically per document, appends both to the oplog, waits for a majority of secondaries to acknowledge (`w: "majority"`), then returns. Secondaries tail the oplog and apply asynchronously; a `secondary` read in the next ~10–200 ms may not see the new review.

## 6. ASCII diagram

```
              BOOKNOOK — SHARDED MONGODB CLUSTER
              =================================

   ┌────────────────────┐        ┌──────────────────────────┐
   │  App servers       │        │   CONFIG SERVER REPLSET  │
   │  (driver pools)    │        │   chunk map / metadata   │
   └─────────┬──────────┘        │   cfg1   cfg2   cfg3     │
             │                   └────────────┬─────────────┘
             │ BSON / OP_MSG                  │ metadata
             ▼                                ▼
       ┌───────────────────────────────────────────────┐
       │   mongos ROUTERS  (stateless, horizontal)     │
       │   targeted query  ──► 1 shard                 │
       │   no shard key    ──► scatter-gather ──► ALL  │
       └───┬───────────────────┬───────────────────┬───┘
           │                   │                   │
   ┌───────▼──────┐    ┌───────▼──────┐    ┌───────▼──────┐
   │   SHARD 1    │    │   SHARD 2    │    │   SHARD 3    │
   │  hash range  │    │  hash range  │    │  hash range  │
   │  [min .. a)  │    │  [a .. b)    │    │  [b .. max)  │
   │              │    │              │    │              │
   │  ┌────────┐  │    │  ┌────────┐  │    │  ┌────────┐  │
   │  │PRIMARY │  │    │  │PRIMARY │  │    │  │PRIMARY │  │
   │  └───┬────┘  │    │  └───┬────┘  │    │  └───┬────┘  │
   │   oplog tail  │   │   oplog tail │    │   oplog tail │
   │  ┌───▼────┐  │    │  ┌───▼────┐  │    │  ┌───▼────┐  │
   │  │SEC  SEC│  │    │  │SEC  SEC│  │    │  │SEC  SEC│  │
   │  └────────┘  │    │  └────────┘  │    │  └────────┘  │
   └──────────────┘    └──────────────┘    └──────────────┘
         ▲                                        ▲
         └──────── balancer migrates chunks ──────┘


        ONE BOOK DOCUMENT  (embed vs reference)
        =======================================

   ┌──────────────────────────────────────────────┐
   │ _id: "9780441013593"            ← ISBN as PK │
   │ title, price, stock                          │
   │ authors:    [ {...}, {...} ]    ← EMBED      │ one-to-few, read together
   │ formats:    [ {...}, {...} ]    ← EMBED      │ bounded, read together
   │ dimensions: { ... }             ← EMBED      │ one-to-one
   │ categories: ["sci-fi", ...]     ← EMBED      │ multikey index
   │ rating_avg, review_count        ← COMPUTED   │
   │ top_reviews: [ 5 items ]        ← SUBSET     │
   └───────────────────┬──────────────────────────┘
                       │ book_id  (REFERENCE)
                       ▼
   ┌──────────────────────────────────────────────┐
   │ reviews collection — 12,000 docs, unbounded  │
   │ { _id, book_id, rating, body, created_at }   │
   └──────────────────────────────────────────────┘

   ✗ WRONG:  reviews: [ ...12,000 items... ]  inside the book
             → 4 MB doc, rewritten on every new review,
               oplog flood, and a hard wall at 16 MB
```

## 7. Detailed example

### 7.1 BookNook — the same data as the Lesson 41 relational schema

Lesson 41 modelled BookNook's catalogue relationally. The normalised schema was:

```
books(isbn PK, title, price_cents, stock, published_on, publisher_id FK)
authors(author_id PK, name, bio)
book_authors(isbn FK, author_id FK)            -- junction, many-to-many
formats(format_id PK, isbn FK, type, price_cents, stock)
categories(category_id PK, name)
book_categories(isbn FK, category_id FK)       -- junction
reviews(review_id PK, isbn FK, customer_id FK, rating, body, created_at)
```

The book-detail page needed a six-table join (or an ORM issuing 6+ separate queries). The document design collapses it:

```json
{
  "_id": "9780441013593",
  "title": "Dune",
  "subtitle": "Book One of the Dune Chronicles",
  "published_on": "1965-08-01",
  "publisher": { "id": 44, "name": "Ace Books" },

  "authors": [
    { "id": 1201, "name": "Frank Herbert", "role": "author" }
  ],

  "formats": [
    { "type": "hardback",  "price_cents": 2499, "stock": 14, "isbn13": "9780441013593" },
    { "type": "paperback", "price_cents": 1099, "stock": 230, "isbn13": "9780441172719" },
    { "type": "ebook",     "price_cents":  899, "stock": null, "drm": "adobe" }
  ],

  "categories": ["science-fiction", "classics", "space-opera"],

  "dimensions": { "height_mm": 198, "width_mm": 129, "weight_g": 420, "pages": 604 },

  "rating_avg": 4.6,
  "review_count": 12847,
  "top_reviews": [
    { "review_id": 99812, "customer": "R. Okafor", "rating": 5,
      "excerpt": "Still the benchmark for world-building.", "helpful": 412 }
  ],

  "schema_version": 3
}
```

Every modelling criterion from §4.3 applied explicitly:

| Data | Decision | Criterion that decided it |
|---|---|---|
| `publisher` | Embed **extended reference** (id + name only) | Always displayed; only the name is needed; full publisher record lives elsewhere |
| `authors` | Embed | One-to-few (1–5), always read together, rarely changes |
| `formats` | Embed | One-to-few (2–4), always read together, bounded by nature |
| `categories` | Embed as a string array | One-to-few, read together, multikey index makes `find({categories: "classics"})` fast |
| `dimensions` | Embed | One-to-one; it has no independent existence |
| `rating_avg`, `review_count` | **Computed pattern** | Recomputing an average over 12,847 reviews per page view is absurd |
| `top_reviews` | **Subset pattern**, 5 items, capped with `$slice` | Bounded, read together, but the full set is unbounded |
| All 12,847 reviews | **Reference** (separate `reviews` collection) | One-to-squillions, unbounded, independently queried and paginated |

**Reads this design serves in one query:** the book-detail page, the search-result card, the cart line, the category listing page. **Reads it does not:** "show me all reviews page 7" (one extra query, indexed on `{book_id: 1, helpful: -1}`), and "all books by Frank Herbert" (served by a multikey index on `authors.id` — still one query, still fast).

### 7.2 BookNook — getting it wrong, twice

**Wrong #1 — the unbounded embed.** The first version of the schema embedded all reviews in the book document. On launch, fine. Eighteen months later the three bestselling titles had 40,000+ reviews each, those documents were 9–11 MB, and:

- The detail page p99 went from 12 ms to **900 ms** on those three books, because every page view transferred 10 MB from disk through the cache.
- The WiredTiger cache thrashed: three documents were evicting thousands of others.
- Posting a review rewrote 10 MB, so the oplog filled and a secondary that had been down for 40 minutes for patching could no longer catch up and needed a **full initial sync** (hours).
- Then `Dune` crossed 16 MB and every new review on it returned `BSONObjectTooLarge`. The error surfaced as a 500 on the review form.

The fix was the subset pattern plus a reviews collection — a multi-week migration because by then four services read `book.reviews` directly.

**Wrong #2 — over-referencing.** Reacting to #1, the team then referenced *everything*, including authors and formats. The detail page became `findOne(book)` + `find(authors)` + `find(formats)` + `find(dimensions)` = four round trips, and the category-listing page (24 books per page) became **97 queries** — a textbook N+1, reproduced in a database that was supposed to have cured it. Latency went up, not down.

**The lesson:** embedding and referencing are not good and bad. They are two tools, and §4.3's criteria tell you which. Reviews are unbounded and independently paginated → reference. Authors are bounded and always read together → embed.

### 7.3 Second, contrasting example — a logistics telemetry platform

A fleet-tracking company ingests GPS pings from 40,000 delivery vans every 10 seconds. Shape of one ping: `{van_id, ts, lat, lon, speed, heading, engine_temp, fuel_pct}` — about 120 bytes of payload.

The naive design, one document per ping, produces **345 million documents/day** and a brutal ratio of overhead to data: each document carries its own field names (~60 bytes of repeated keys), its own `_id` (12 bytes + ~40 bytes of index entry), and its own record header. The *overhead* exceeds the *payload*.

The **bucket pattern** fixes it: one document per `(van_id, hour)`, holding an array of up to 360 readings, plus precomputed `min`/`max`/`count` for the hour.

```json
{
  "_id": "van-8841:2026-10-09T14",
  "van_id": 8841,
  "hour_start": "2026-10-09T14:00:00Z",
  "count": 360,
  "speed_max": 94, "speed_avg": 41.2, "temp_max": 97,
  "readings": [ { "t": 0, "lat": 51.5074, "lon": -0.1278, "s": 32 }, "...359 more..." ]
}
```

Results, typical for this pattern: **~960,000 documents/day instead of 345 million** (a 360× reduction), index size down by a comparable factor, and dashboard queries like "van 8841's max speed yesterday" answered from the precomputed `speed_max` field across 24 documents instead of scanning 8,640.

The contrast with BookNook is instructive. BookNook's documents mirror a **business entity** — a book is a thing in the world, and the document boundary is obvious. The telemetry documents mirror **nothing in the world**; a "van-hour" is a purely physical storage construct chosen to amortise overhead. Both are document modelling. Only one of them is object mapping. This is why "a document database stores your objects" is an incomplete description — **a document database stores whatever unit your access pattern makes cheapest to read and write.**

## 8. Code or configuration example

> **Illustrative only — not production-ready.** Error handling, connection management, authentication, retry logic, index build strategy (`background`/rolling builds), and schema-validation rollout are omitted or simplified. Do not copy this into a service.

```javascript
// ─────────────────────────────────────────────────────────────
// 1. CREATE THE COLLECTION WITH A SCHEMA VALIDATOR
//    "Schema-less" is a choice, not a destiny. Validators let the
//    database reject malformed documents at write time.
// ─────────────────────────────────────────────────────────────
db.createCollection("books", {
  validator: {
    $jsonSchema: {
      bsonType: "object",
      required: ["_id", "title", "formats"],      // reject docs missing these
      properties: {
        _id:   { bsonType: "string", description: "ISBN-13 used as the primary key" },
        title: { bsonType: "string" },
        formats: {
          bsonType: "array",
          minItems: 1,                            // a book must be buyable in some form
          items: {
            bsonType: "object",
            required: ["type", "price_cents"],
            properties: {
              type:        { enum: ["hardback", "paperback", "ebook", "audio"] },
              price_cents: { bsonType: "int", minimum: 0 }  // integer cents, never float money
            }
          }
        },
        top_reviews: {
          bsonType: "array",
          maxItems: 5                             // enforce the subset pattern at the DB level
        }
      }
    }
  },
  validationAction: "error"    // "warn" logs but still accepts — use during migration
});

// ─────────────────────────────────────────────────────────────
// 2. INDEXES — each one exists for a named query
// ─────────────────────────────────────────────────────────────
// Multikey: one index entry PER ARRAY ELEMENT. Serves "all sci-fi books".
db.books.createIndex({ categories: 1 });

// Multikey on a field nested inside an array of objects. Serves "books by author 1201".
db.books.createIndex({ "authors.id": 1 });

// Compound, ESR order: Equality (categories) → Sort (rating_avg) → Range (price).
// Serves: find({categories:"sci-fi", "formats.price_cents": {$lt: 1500}}).sort({rating_avg:-1})
// Prefix rule: this ALSO serves a query on categories alone, but NOT on rating_avg alone.
db.books.createIndex({ categories: 1, rating_avg: -1, "formats.price_cents": 1 });

// Partial index: only index in-print books. Smaller index, cheaper writes for the rest.
db.books.createIndex(
  { title: 1 },
  { partialFilterExpression: { in_print: true } }
);

// TTL index on a different collection: cart documents self-delete 7 days after creation.
db.carts.createIndex({ created_at: 1 }, { expireAfterSeconds: 604800 });

// ─────────────────────────────────────────────────────────────
// 3. THE BOOK-DETAIL READ — one round trip, one document
// ─────────────────────────────────────────────────────────────
db.books.findOne(
  { _id: "9780441013593" },                       // equality on the _id index → targeted query
  { title: 1, "authors.name": 1, formats: 1,      // PROJECTION: fewer bytes on the wire.
    rating_avg: 1, top_reviews: 1 }               // NOTE: does not reduce bytes read from disk.
);

// ─────────────────────────────────────────────────────────────
// 4. ADDING A REVIEW — the subset + computed patterns together
// ─────────────────────────────────────────────────────────────
// (a) The authoritative copy goes to the unbounded referenced collection.
db.reviews.insertOne({
  book_id: "9780441013593",
  customer_id: 55120,
  rating: 5,
  body: "Still the benchmark for world-building.",
  helpful: 0,
  created_at: new Date()
});

// (b) The book document is updated ATOMICALLY (single-document write — no transaction needed).
db.books.updateOne(
  { _id: "9780441013593" },
  {
    $inc: { review_count: 1 },                    // computed pattern: never COUNT() 12,847 rows
    $push: {
      top_reviews: {
        $each: [{ review_id: 99813, customer: "A. Patel", rating: 5,
                  excerpt: "Still the benchmark.", helpful: 0 }],
        $sort:  { helpful: -1 },                  // keep the most helpful first...
        $slice: 5                                 // ...and HARD-CAP the array at 5.
      }                                           // $slice is the guard against unbounded growth.
    }
  }
);

// ─────────────────────────────────────────────────────────────
// 5. AGGREGATION PIPELINE — monthly revenue per category
// ─────────────────────────────────────────────────────────────
db.orders.aggregate([
  // $match FIRST: filter before doing any expensive work. The optimiser
  // can push this into an index scan on created_at.
  { $match: { created_at: { $gte: ISODate("2026-09-01"), $lt: ISODate("2026-10-01") },
              status: "fulfilled" } },

  // Explode the line_items array: one intermediate document per item.
  // This MULTIPLIES the document count — do it as late as possible.
  { $unwind: "$line_items" },

  // Join to books to get categories. $lookup is a per-document lookup,
  // NOT a hash join. The foreign field MUST be indexed or this is a disaster.
  { $lookup: { from: "books",
               localField: "line_items.book_id",
               foreignField: "_id",
               as: "book" } },
  { $unwind: "$book" },
  { $unwind: "$book.categories" },                // a book has several categories

  // Aggregate. Note price_paid comes from the ORDER (a point-in-time snapshot),
  // not from the book — the book's current price may have changed since.
  { $group: { _id: "$book.categories",
              revenue_cents: { $sum: { $multiply: ["$line_items.price_paid_cents",
                                                   "$line_items.qty"] } },
              units: { $sum: "$line_items.qty" } } },

  { $sort: { revenue_cents: -1 } },
  { $limit: 10 }
], { allowDiskUse: true });   // permit spilling past the 100 MB per-stage RAM limit

// ─────────────────────────────────────────────────────────────
// 6. WRITE CONCERN / READ PREFERENCE — set per operation
// ─────────────────────────────────────────────────────────────
// Money: durability over latency. Majority ack + journal flush.
db.payments.insertOne(
  { order_id: 77120, amount_cents: 2499, provider_ref: "ch_9f2" },
  { writeConcern: { w: "majority", j: true, wtimeout: 5000 } }  // ALWAYS set wtimeout
);                                                              // or a stalled member hangs you

// Analytics: staleness is fine, so read from a secondary and spare the primary.
db.orders.find({ status: "fulfilled" })
  .readPref("secondaryPreferred")   // EVENTUALLY CONSISTENT: may lag by 10–200 ms, typical
  .readConcern("local");

// ─────────────────────────────────────────────────────────────
// 7. MULTI-DOCUMENT TRANSACTION — the escape hatch, with its cost
// ─────────────────────────────────────────────────────────────
const session = db.getMongo().startSession();
session.startTransaction({ readConcern: { level: "snapshot" },
                           writeConcern: { w: "majority" } });
try {
  // Two documents that genuinely cannot be merged into one aggregate:
  // inventory is owned by the catalogue, the order is owned by commerce.
  session.getDatabase("booknook").books.updateOne(
    { _id: "9780441013593", "formats.type": "hardback", "formats.stock": { $gte: 1 } },
    { $inc: { "formats.$.stock": -1 } },
    { session }
  );
  session.getDatabase("booknook").orders.insertOne(
    { _id: 77121, customer_id: 55120, status: "pending" },
    { session }
  );
  session.commitTransaction();     // across shards this is a TWO-PHASE COMMIT
} catch (e) {
  session.abortTransaction();
  // REQUIRED IN REAL CODE: TransientTransactionError must be retried in a loop.
  // A transaction without a retry handler is a latent production bug.
}

// ─────────────────────────────────────────────────────────────
// 8. SHARDING — choose the key before you have data, not after
// ─────────────────────────────────────────────────────────────
sh.shardCollection("booknook.orders", { customer_id: "hashed" });
// hashed  → writes spread evenly; no monotonic hotspot (created_at would hotspot badly)
// customer_id → "my order history" queries are TARGETED to one shard
// COST: range scans on customer_id become scatter-gather, because hashing destroys locality
//       (the same spread-vs-locality trade-off as consistent hashing, Lesson 38)

// Always verify the plan. "COLLSCAN" or "SHARD_MERGE" on a hot path is a red flag.
db.books.find({ categories: "sci-fi" }).explain("executionStats");
```

## 9. Numerical example

**Question:** for BookNook's book-detail page, how much faster is a single embedded-document read than the equivalent relational join, and what does it cost in storage?

### Stated assumptions (all labelled)

| Assumption | Value | Basis |
|---|---|---|
| Application ↔ database network round trip, same AZ | **0.5 ms** | typical |
| Indexed point lookup, data in buffer pool/cache | **0.2 ms** server time | typical |
| Indexed lookup, cache miss, NVMe SSD | **+0.3 ms** | approximate |
| Cache hit rate on hot catalogue data | **95%** | assumed |
| Relational: 6 tables to assemble the page | books, authors, book_authors, formats, categories, book_categories | from Lesson 41 |
| Page shows 24 books (category listing) | 24 | assumed |
| Book document size, embedded design | **4 KB** | measured-style estimate |
| Relational row bytes actually needed | **2.2 KB** per book across 6 tables | estimate |
| Catalogue size | **2,000,000 books** | assumed |
| BSON field-name overhead per document | **~420 bytes** | estimated from the §7.1 document |

### Case A — relational, ORM with lazy loading (the N+1 path)

```
Queries = 1 (books) + 24 × 5 (authors, book_authors, formats, categories, book_categories)
        = 1 + 120
        = 121 queries
```

Serialised (the ORM issues them one at a time, which is the default behaviour):

```
Per query  = 0.5 ms (RTT) + 0.2 ms (server) = 0.7 ms
Cache miss adds 0.3 ms on 5% of queries = 0.05 × 0.3 = 0.015 ms average
Effective per query = 0.715 ms

Total = 121 × 0.715 ms = 86.5 ms
```

### Case B — relational, single hand-written 6-way JOIN

```
Queries = 1
Server time: 6-way join over 24 books, all indexed.
  Index lookups      = 24 books × 5 joined tables × 2 (avg matching rows) = 240 lookups
  At 0.02 ms per in-cache index+row lookup (typical)   = 4.8 ms
  Join/merge CPU + result assembly (approximate)        = 2.0 ms
  Row explosion: 24 × 2 authors × 3 formats × 3 cats = 432 result rows
    (the cartesian fan-out; the app must de-duplicate)  = 1.0 ms
  Server total                                          ≈ 7.8 ms

Network = 0.5 ms RTT
Payload = 432 rows × ~600 bytes (duplicated book columns) = 259 KB
  at 1 Gbps effective = 259,000 × 8 / 1e9 s             = 2.1 ms
De-duplication in the application (approximate)          = 1.5 ms

Total = 7.8 + 0.5 + 2.1 + 1.5 = 11.9 ms
```

Note what the join actually cost: **432 rows to deliver 24 books**, because a 6-way join across two many-to-many relationships multiplies rows. This fan-out is invisible in the SQL and very visible in the wire bytes.

### Case C — document database, embedded design

```
Queries = 1   (db.books.find({categories: "sci-fi"}).limit(24))

Server time:
  Multikey index scan on categories, 24 entries   = 24 × 0.01 ms = 0.24 ms
  Document fetches, 95% from WiredTiger cache:
      22.8 docs × 0.02 ms (cache)                 = 0.46 ms
       1.2 docs × 0.35 ms (NVMe read)             = 0.42 ms
  BSON assembly into the cursor batch             = 0.30 ms
  Server total                                     ≈ 1.42 ms

Network = 0.5 ms RTT
Payload = 24 docs × 4 KB = 96 KB
  at 1 Gbps effective = 96,000 × 8 / 1e9           = 0.77 ms
Driver BSON → object deserialisation, 96 KB (approximate, 1.2 GB/s) = 0.08 ms

Total = 1.42 + 0.5 + 0.77 + 0.08 = 2.77 ms
```

### Comparison

| Design | Queries | Round trips | Wire bytes | Total latency | vs. best |
|---|---|---|---|---|---|
| A — relational + ORM N+1 | 121 | 121 | ~190 KB | **86.5 ms** | 31× slower |
| B — relational, one join | 1 | 1 | 259 KB | **11.9 ms** | 4.3× slower |
| C — document, embedded | 1 | 1 | 96 KB | **2.77 ms** | baseline |

**The honest reading.** The headline "31× faster" (A vs C) compares a document database against a *badly used* relational database. The fair comparison is B vs C: **4.3×**, and most of that gap is the join's row fan-out and de-duplication, not some inherent relational slowness. A relational team that materialises the catalogue into a denormalised `book_view` table, or caches the rendered object in Redis (Lesson 29), closes most of the gap. **Document databases do not have a magic fast path; they have a data layout that avoids a specific cost.**

### Storage cost of the same design

```
Relational, normalised:
  books row                                   =   900 bytes
  share of authors (1 author / 8 books avg)   =    40 bytes
  book_authors junction rows (1.3 avg × 24 B) =    31 bytes
  formats rows (3 × 120 B)                    =   360 bytes
  share of categories                         =    10 bytes
  book_categories rows (3 × 24 B)             =    72 bytes
  indexes (approx. 30% of data)               =   424 bytes
  ─────────────────────────────────────────────────────────
  Per book                                    = 1,837 bytes
  × 2,000,000 books                           = 3.67 GB

Document, embedded (before compression):
  payload fields                              = 3,100 bytes
  repeated BSON field names                   =   420 bytes   ← the self-describing tax
  duplicated author name/bio snippets         =   180 bytes   ← denormalisation cost
  top_reviews subset (5 × 220 B)              = 1,100 bytes
  _id + indexes (6 indexes, multikey)         =   800 bytes
  ─────────────────────────────────────────────────────────
  Per book                                    = 5,600 bytes
  × 2,000,000 books                           = 11.2 GB

  With WiredTiger snappy compression (typical 3–4× on repetitive
  field names — compression works WELL on exactly this overhead):
  data portion 4,800 B ÷ 3.5 = 1,371 B, plus 800 B uncompressed index
                                              = 2,171 bytes
  × 2,000,000 books                           = 4.34 GB
```

**Conclusion with every number in view:** the document design is **~4.3× faster** on the page read and costs **~18% more storage after compression** (4.34 GB vs 3.67 GB), rising to **3× more** if compression is disabled. The field-name overhead is real but compresses extremely well, because field names repeat identically across millions of documents. The trade is **latency and query simplicity bought with storage, write amplification, and the permanent obligation to keep duplicated data in sync.**

## 10. Trade-offs

| Dimension | What you gain | What you pay |
|---|---|---|
| **Read latency for aggregates** | One round trip, no join, no N+1 | Only for the access patterns you modelled; an unanticipated query may need `$lookup` or a full redesign |
| **Flexible schema** | Ship a new field without `ALTER TABLE`; handle sparse and polymorphic data naturally | The schema moves into application code, where nothing enforces it. Five years of accumulated shapes in one collection is a genuine maintenance burden |
| **Developer velocity** | Objects map directly; no ORM translation layer | Velocity is highest at the start and *decreases* as undocumented shape variations accumulate — the opposite of the relational curve |
| **Horizontal scale** | Aggregates shard cleanly; add shards to add capacity | The shard key is quasi-permanent; resharding is a full data rewrite |
| **Atomicity** | Free within one document | Across documents it needs transactions, which cost 2–10× and must be retried on conflict |
| **Denormalisation** | Reads avoid joins | Every duplicated field is a future inconsistency. Someone must own the fan-out update, and it is not atomic |
| **Storage** | Compresses well | Self-describing documents carry field names on every record; duplicated data is stored many times |
| **Write cost** | Single-document writes are fast | Copy-on-write means write cost scales with **document size**, not with the size of the change |
| **Query power** | Aggregation pipeline is genuinely capable | No declarative optimiser across collections; `$lookup` is a nested loop, not a planner-chosen join strategy |
| **Consistency** | Tunable via write concern / read concern | Every relaxation is an explicit staleness window you must reason about **(Phase 3, Topic 20 — CAP theorem)** |

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| **Document exceeds 16 MB** | Write fails with `BSONObjectTooLarge`; e.g. the review form 500s, but only on bestsellers | Application error rate on one endpoint; a `db.collection.aggregate([{$project:{sz:{$bsonSize:"$$ROOT"}}}])` size audit finds it | No automatic recovery. Requires a data-model change (subset/bucket/reference) plus a backfill migration — typically weeks, because other services read the field |
| **Unbounded array growth (pre-limit)** | Gradual latency creep on a few hot entities; p99 degrades while p50 looks fine | p99/p50 divergence per endpoint; growing average document size; rising oplog throughput | Cap arrays with `$slice`, migrate to a referenced collection, rebuild indexes |
| **Primary fails** | ~10–12 s of write errors; reads continue if read preference allows secondaries | Heartbeat timeout triggers an election; driver surfaces `NotWritablePrimary` | Automatic: a secondary wins the election and becomes primary. Retryable writes replay idempotent operations transparently |
| **Writes rolled back after failover** | A user sees an order confirmation, then the order does not exist | Only via reconciliation or user complaint — **this failure is silent** | Prevented, not recovered: use `w: "majority"`. With `w: 1` the write existed only on a primary that is now a rolled-back secondary |
| **Replication lag spike** | Stale reads: user posts a review, refreshes, review is gone | `rs.printSecondaryReplicationInfo()`; monitoring on oplog window and lag seconds | Route critical reads to `primary`, or use causal-consistency sessions so a client reads its own writes |
| **Hot shard from a monotonic shard key** | Write latency climbs; adding shards does not help at all | Per-shard ops/sec in monitoring is wildly uneven; `sh.status()` shows chunk imbalance | Reshard with a hashed or compound key — an expensive full rewrite. Pre-splitting chunks mitigates a known-bad key |
| **Jumbo chunk** | Uneven disk usage; the balancer logs repeated migration failures | Balancer errors; `sh.status()` flags the chunk as jumbo | The chunk cannot be split because its shard-key values are identical. Requires a finer-grained compound shard key |
| **Scatter-gather query storm** | A dashboard query slows every endpoint cluster-wide | `explain()` shows `SHARD_MERGE`; all shards show CPU spikes from one query | Add the shard key to the query, add a targeted index, or move analytics to secondaries / a separate store |
| **Transaction write conflicts under contention** | Intermittent failed checkouts under load, fine when load drops | `TransientTransactionError` rate; p99 spikes correlated with concurrency | Retry loop with backoff; better, redesign so contended data lives in one document |
| **Missing index after a schema change** | One endpoint suddenly 400× slower | Slow-query log; `COLLSCAN` in `explain()` | Build the index with a rolling build. On a large collection this takes minutes to hours |
| **Config server replica set unavailable** | New connections fail; `mongos` can serve from its cached chunk map briefly, then routing fails | Cluster-wide write failures; config replset health alerts | Restore config server majority. This is why config servers are a replica set, not a single node |

## 12. Alternatives

| Option | Data model | Strongest at | Weakest at | Choose it over a document DB when |
|---|---|---|---|---|
| **Relational (PostgreSQL, MySQL)** — Lesson 40/41 | Normalised tables, enforced schema, foreign keys | Ad-hoc queries, multi-entity transactions, referential integrity, reporting | Deeply nested/variable structures; cross-shard joins at scale | Relationships are many-to-many and queried in unplanned ways; integrity must be enforced by the database |
| **PostgreSQL with `JSONB`** | Tables **plus** indexable JSON columns per row | Having both: relational integrity for the core, documents for the variable parts, with real SQL and real transactions | Horizontal write scaling; JSONB updates rewrite the whole value | You want document flexibility but already have (or want) relational guarantees. **This is the most under-used option in the entire comparison** |
| **Key-value (Redis, DynamoDB)** — Lesson 43 | Opaque value under a key | Microsecond lookups by exact key; simple, predictable scaling | Any query that is not by primary key | Access is purely by key and you never filter on the contents |
| **DynamoDB (document mode)** | Items with partition + sort key, nested attributes | Fully managed, predictable single-digit-ms latency, true serverless scaling | Query flexibility — GSIs must be planned per access pattern; 400 KB item limit | You want AWS-managed scale and your access patterns are fully known in advance |
| **Couchbase** | JSON documents + a memory-first architecture + SQL++ (N1QL) | Built-in caching tier; SQL-like querying over JSON | Smaller ecosystem; more operational surface | You need cache and document store as one system |
| **CouchDB** | JSON documents, MVCC, replication-first | Offline-first and multi-master replication (mobile sync) | Query capability is comparatively weak | Occasionally-connected clients must sync and merge |
| **Elasticsearch** | JSON documents in an inverted index | Full-text relevance search, faceting, analytics over text | Being a system of record — no real transactions, eventual visibility (`refresh_interval`) | You need search, not storage. Frequently run *alongside* a document database, not instead |
| **Wide-column (Cassandra)** — Lesson 45 | Partition key + clustering key, query-first tables | Enormous write throughput, linear scale, multi-DC, time series | Ad-hoc queries, joins, strong transactions | Writes dominate and every query is known at design time |
| **Firestore / Realm** | Documents with real-time listeners | Client-facing sync, offline support, per-document security rules | Server-side analytics, large aggregations, cost at high read volumes | Mobile or web clients talk to the database directly |

## 13. When to use it

1. **Content and catalogue systems with variable structure.** BookNook's catalogue: a hardback has `jacket_finish`, an e-book has `drm_type`, an audiobook has `narrator` and `runtime_minutes`. Relationally this is either 300 sparse columns, an EAV table that destroys query performance, or four subtype tables. As documents it is simply four shapes in one collection, each carrying what it needs.

2. **Aggregate-shaped workloads with known access patterns.** Orders, user profiles, shopping carts, CMS pages, product listings. The defining test: *is there a natural root entity that is always loaded and saved as a whole?* If yes, the document boundary draws itself.

3. **Event, log, and time-series ingestion with the bucket pattern.** The telemetry platform in §7.3. High write volume, append-mostly, queried by entity and time range, with precomputed rollups inside the bucket.

4. **Rapidly evolving schemas in early-stage products.** When the shape genuinely changes weekly, avoiding `ALTER TABLE` on growing tables is real velocity — provided the team adds validators and a `schema_version` field before the flexibility turns into chaos.

5. **Real-time personalisation and session state.** Documents per user with heterogeneous preference structures, read whole on every request, written whole, with a TTL index for expiry.

6. **Horizontally scaling an aggregate-oriented workload.** When a single relational primary cannot take the write volume and the data decomposes into independent aggregates, sharded documents scale more cleanly than sharded tables with cross-shard joins.

## 14. When not to use it

1. **Highly relational data queried in unanticipated ways.** A finance or ERP system where entities are many-to-many in every direction and analysts write new queries weekly. Document modelling optimises *known* access patterns; there is no good document model for "all of them". `$lookup` across five collections performs worse than the equivalent SQL join, and you lose the optimiser.

2. **Workloads requiring multi-entity ACID transactions on the hot path.** Double-entry bookkeeping, inventory reservation across suppliers, bank transfers. MongoDB transactions exist and work, but at 2–10× cost with mandatory retry handling. A relational database does this natively, faster, and with less application code.

3. **When referential integrity must be enforced by the database.** Document databases have no foreign keys and no `ON DELETE CASCADE`. Delete a publisher and every embedded publisher reference silently becomes a dangling value. If correctness depends on the database refusing invalid states, use one that can.

4. **Analytical and OLAP workloads.** Large scans, multi-dimensional aggregation, and ad-hoc reporting belong in a columnar store (ClickHouse, BigQuery, Redshift). The aggregation pipeline can do it; it will be slower and will compete with your production traffic.

5. **When the team wants "no schema" as a way to avoid designing.** The schema does not disappear; it relocates into application code where no tool validates it. A collection with eleven undocumented shapes is strictly worse than a table with eleven columns.

6. **When the data is naturally flat and uniform.** A table of 60 million exchange-rate rows with four identical columns gains nothing from documents and pays the field-name tax on every record.

## 15. Common mistakes

### Beginner mistakes

- **Treating "schema-less" as "no design needed".** Flexible schema means the *database* does not enforce the schema. The schema still exists — it is just undocumented and unvalidated now. Use `$jsonSchema` validators and a `schema_version` field from day one.
- **Embedding unbounded arrays.** Reviews, comments, events, audit log entries, messages. §4.9. The most common document-database catastrophe, by a wide margin.
- **Modelling tables as collections and joining with `$lookup`.** Recreating a normalised relational schema in MongoDB gives you relational complexity *without* the optimiser, foreign keys, or transactions. Strictly worse than just using PostgreSQL.
- **Forgetting indexes.** A collection scan at 10,000 documents is imperceptible; at 10 million it is a 30-second query that locks up the connection pool. Always run `explain()` on new queries and look for `COLLSCAN`.
- **Using `w: 1` (or an old driver default) for money.** The write is acknowledged, then vanishes during a failover. Silent data loss with no error anywhere.
- **Storing money as a floating-point number.** `0.1 + 0.2 !== 0.3` applies to BSON doubles exactly as it does everywhere else. Use integer cents or `Decimal128`.
- **Reading from secondaries and being surprised by stale data.** `secondaryPreferred` is eventual consistency. The user posts a review, the refresh hits a lagging secondary, the review is "gone", a support ticket is filed.
- **Issuing N+1 queries.** The pattern that drove people away from ORMs is trivially reproducible in a document database (§7.2, wrong #2).

### Experienced-engineer mistakes

- **Choosing a monotonically increasing shard key.** `created_at` or a raw `ObjectId` sends every new write to one shard. The cluster is perfectly balanced *in storage* and completely unbalanced *in write traffic*, which is why it survives review — storage dashboards look fine.
- **Over-indexing.** Every index must be updated on every write, consumes RAM in the WiredTiger cache, and slows inserts. Fifteen indexes on a write-heavy collection can halve write throughput. Audit with `$indexStats` and drop what is unused.
- **Designing the model before enumerating the queries.** Document modelling is query-driven. Writing the document shape first and discovering the access patterns afterwards guarantees a `$lookup`-heavy rewrite.
- **Denormalising without an owner for the fan-out update.** Duplicating `author.name` into 40,000 book documents is fine — until the author changes their name and no service is responsible for the `updateMany`, which is itself non-atomic and can partially fail.
- **Using transactions to paper over wrong aggregate boundaries.** If every checkout needs a 4-document transaction, the aggregate is wrong. Transactions as an architectural foundation is a design smell that shows up as contention at exactly the load you cared about.
- **Ignoring working-set size.** MongoDB is fast when the working set fits the WiredTiger cache (default ~50% of RAM). Once it does not, you fall off a cliff into disk I/O — and the transition is sudden, not gradual.
- **Running `$lookup` against an unindexed foreign field.** It becomes a collection scan *per input document*. A pipeline that ran in 40 ms in staging takes 18 minutes in production.
- **Deleting without a plan.** Large `deleteMany` operations generate enormous oplog traffic and can blow the oplog window, forcing a secondary into full resync. Use TTL indexes or batched deletes.
- **Assuming a replica set is a backup.** Replication copies your mistakes instantly. `db.orders.deleteMany({})` is replicated to every secondary in milliseconds. You still need point-in-time backups.

## 16. Interview perspective

**What the question is really testing.** When an interviewer asks "would you use MongoDB here?", they are not testing product knowledge. They are testing whether you can **derive a data model from access patterns** and **articulate a trade-off without slogans**.

**The structure that scores well:**

1. **Enumerate the access patterns first.** "Before choosing, I'd list the reads and writes: book detail by ISBN, category browse with sort, search, order placement, order history, admin reporting. The model falls out of that list."
2. **Name the aggregate boundary explicitly.** "An order is an aggregate — always read and written whole — so it is a document. A book's reviews are not part of the book aggregate, because they are unbounded and independently paginated."
3. **Apply the embed/reference criteria by name.** Access pattern, cardinality, update ratio, size bound, atomicity, independent access. Saying all six shows you have a framework, not a preference.
4. **Volunteer the failure mode before being asked.** "The risk here is unbounded array growth on bestsellers, so I'd cap `top_reviews` with `$slice` and reference the full set."
5. **Be explicit about consistency.** "Writes use `w: majority` because losing an order on failover is unacceptable; the analytics dashboard reads from secondaries and tolerates ~200 ms staleness."
6. **Choose a shard key out loud, with the rejected options.** "I'd shard orders on hashed `customer_id`: high cardinality, even distribution, present in the dominant query. I'd reject `created_at` because it is monotonic and would hotspot a single shard, and `country` because cardinality is too low to split."
7. **Say what you would use instead for the parts that do not fit.** "Reporting goes to a columnar store; search goes to Elasticsearch; the document database stays the system of record for the operational aggregates."

**Strong answers often include:** admitting that PostgreSQL with `JSONB` covers many "we need documents" cases with better guarantees; noting that the real comparison is a *well-used* relational database, not an N+1-riddled one; and distinguishing the two reasons document databases exist (§2) rather than conflating them.

**Red flags interviewers watch for:**

- Saying **"MongoDB is schema-less so we don't need to design the schema."** This single sentence ends many interviews.
- Claiming **"NoSQL is faster than SQL"** with no qualifier. Faster at *what* access pattern, at *what* consistency level, under *what* data volume?
- Embedding an unbounded collection (reviews, comments, events) and not flagging it.
- Picking a shard key with no reasoning, or picking a timestamp and not recognising the hotspot.
- Not knowing **what happens on failover** or what `w: 1` actually risks.
- Reaching for multi-document transactions as the default rather than as the exception.
- Being unable to state **one thing relational databases do better.** An engineer who cannot name a weakness of their chosen tool has not evaluated it.
- Saying **"it depends"** and stopping. It depends on *read:write ratio*, *whether the aggregate is bounded*, *whether queries are known in advance*, and *whether integrity must be database-enforced* — name the variables.
- Confusing a replica set (availability) with a backup (recoverability).

## 17. Knowledge check

**Conceptual**

1. A document database removes the need for joins on your hot path. Explain precisely *what* you have traded away to get that, naming at least three distinct costs, and explain why the aggregate-oriented philosophy makes the trade worthwhile for some workloads and harmful for others.
2. MongoDB's 16 MB document limit is often described as a restriction. Argue instead that it is a **design guardrail**: describe the performance degradation that occurs long before a document reaches the limit, and explain why that gradual degradation is more dangerous than the hard error.
3. "Schema-less" and "flexible schema" are used interchangeably, but only one is accurate. Explain where the schema actually lives in a document database, who enforces it, and what concrete mechanisms exist to reintroduce enforcement when you want it.

**Practical scenario**

4. BookNook is adding a wishlist feature. A customer can have multiple named wishlists; each wishlist holds books; a book can appear on many wishlists; customers can share a wishlist with friends who can view (not edit) it; the "my wishlists" page shows each list's name, item count, and the covers of the first 4 books. Power users have up to 300 wishlists with up to 2,000 books each. Decide the full document model: what is embedded, what is referenced, what is duplicated, which indexes you create, and what the shard key is. For each decision, name the criterion from §4.3 that drove it, and state explicitly which query each index serves.

**Calculation**

5. BookNook's events collection stores one document per page view: `{_id: ObjectId, book_id, customer_id, ts, referrer, device}`, averaging 180 bytes of payload plus 420 bytes of BSON overhead and index entries. Traffic is 2,400 page views per second, sustained. (a) Compute documents and raw storage per day. (b) Now apply the bucket pattern with one document per `(book_id, minute)`, assuming 12,000 distinct books receive traffic in any given minute and the per-bucket fixed overhead is 500 bytes while each reading inside the bucket costs 95 bytes. Compute documents and storage per day. (c) State the percentage reduction in both document count and storage, and identify one query that becomes *harder* under the bucketed design. Show every step and state every assumption you add.

**Progress checklist**

```
Phase 1 — Foundations (Lessons 1–17)            [x] COMPLETE
Phase 2 — Scaling (Lessons 18–39)               [x] COMPLETE

Phase 3 — Databases & Storage
  [x] Topic 1  — Relational databases            (Lesson 40)
  [x] Topic 2  — Tables, keys, joins, constraints(Lesson 41)
  [x] Topic 3  — SQL vs NoSQL                    (Lesson 42)
  [x] Topic 4  — Key-value databases             (Lesson 43)
  [x] Topic 5  — Document databases              (Lesson 44)  ← you are here
  [ ] Topic 6  — Wide-column databases
  [ ] Topic 7  — Graph databases
  [ ] Topic 8  — Time-series databases
  [ ] Topic 9  — Search engines & inverted indexes
  [ ] Topic 10 — Object & blob storage
  [ ] Topic 11 — File systems & distributed file systems
  [ ] Topic 12 — Indexing deep dive (B-tree vs LSM)
  [ ] Topic 13 — Query planning & optimisation
  [ ] Topic 14 — Transactions & ACID
  [ ] Topic 15 — Isolation levels
  [ ] Topic 16 — Multi-version concurrency control
  [ ] Topic 17 — Write-ahead logging & durability
  [ ] Topic 18 — Backup, restore & point-in-time recovery
  [ ] Topic 19 — Data migration & schema evolution
  [ ] Topic 20 — CAP theorem
  [ ] Topic 21 — PACELC & consistency models
  [ ] Topic 22 — Distributed transactions & 2PC
  [ ] Topic 23 — Polyglot persistence
  [ ] Topic 24 — Choosing a database: a decision framework
```

**NEXT LESSON: Phase 3, Topic 6 — Wide-column databases**

---

# Lesson 45 — Wide-Column Databases

*Phase 3, Topic 6*

## 1. Simple definition

A **wide-column database** stores data in tables whose rows are grouped into **partitions**, where every query must name the partition, and where each row can hold a different and potentially enormous set of columns.

The compact version: **a wide-column database is a distributed, sorted, partitioned map.** Formally, the Bigtable paper described it as:

```
(row key, column key, timestamp) → value
```

Terms defined before use:
- **Partition**: the unit of storage and distribution. All rows sharing a partition key live together on the same node (and its replicas). This is the *only* unit a query can efficiently address.
- **Partition key**: the part of the primary key that determines *which node* holds the data. Hashed to a position on the ring (Lesson 38).
- **Clustering key** (Cassandra) / **column qualifier** (HBase): determines the **sort order of rows within a partition**. This is what makes range scans inside a partition cheap.
- **Wide row / wide partition**: a partition holding many rows — thousands to millions. The "wide" in the name.
- **Node**: one server in the cluster. Wide-column stores are **masterless**: every node is equal, any node can accept any request.
- **Replication factor (N)**: how many nodes hold a copy of each partition.
- **CQL**: Cassandra Query Language. Looks like SQL. **It is not SQL** — it has no joins, no subqueries, and refuses most queries SQL would accept. The familiar syntax is a deliberate on-ramp and an occasional trap.

### The critical clarification: this is NOT a column-oriented analytics store

**This confusion is the single most common error about wide-column databases, and it must be addressed before anything else.**

There are two completely different things with confusingly similar names:

| | **Wide-column store** (Cassandra, HBase, Bigtable, ScyllaDB) | **Column-oriented / columnar store** (ClickHouse, Redshift, BigQuery, Parquet, DuckDB) |
|---|---|---|
| **What it is for** | OLTP: high-volume operational reads and writes by key | OLAP: analytical scans and aggregations over whole datasets |
| **Physical layout** | Rows are stored **together**, grouped and sorted within a partition. It is fundamentally **row-oriented within a partition** | Each column is stored in its own contiguous file or block, separately from the other columns |
| **Typical query** | "Give me orders for customer 5512 from the last 30 days" — one partition, a few hundred rows | "Average order value by country for 2025" — two columns, 4 billion rows |
| **Why its layout helps** | All data for one key is physically adjacent → one seek | Reading 2 of 60 columns reads 3% of the bytes; identical values compress 10–50× |
| **Writes** | Extremely fast, millions/sec across a cluster | Slow for single rows; designed for bulk batch loading |
| **Where "column" in the name comes from** | Rows can have **different columns**, dynamically, and a row can have millions of them | Data is **physically stored by column** |

The name "wide-column" refers to **schema flexibility per row**, not to physical column-major storage. Cassandra stores a row's columns contiguously on disk, exactly like a row store. If an interviewer asks "is Cassandra a columnar database?", the correct answer is **no** — it is a partitioned, sorted row store with a flexible per-row column set, and columnar analytics stores are a different family entirely (covered in **(Phase 3, Topic 12 — Indexing deep dive)** and the OLAP material).

## 2. Why it exists

Wide-column databases exist because of a workload that neither relational nor document databases handle well: **colossal write volume, spread across many machines and often many data centres, with no single point of failure and no coordination on the write path.**

### The specific problems

**Problem 1 — the write ceiling of single-leader replication.** Lessons 33 and 44 both described single-leader replication: every write goes to one primary. That primary is a hard ceiling. Sharding (Lesson 36) relieves it by creating many primaries, but each shard still has one, and each shard's failover is a window of unavailability. At Facebook's inbox-search scale (the workload Cassandra was built for in 2008), and at Amazon's shopping-cart scale (the workload Dynamo was built for in 2007), that ceiling was the binding constraint.

**Problem 2 — the operational cost of failover.** A single-leader system is *unavailable for writes* for 10+ seconds during every primary failure. At 1,000 nodes, hardware failure is continuous, not exceptional. A system where any node can accept any write never has a failover window at all, because there is nothing to fail over.

**Problem 3 — multi-datacentre writes.** A single primary means writes in Sydney cross the Pacific to a primary in Virginia: ~200 ms of latency on every write. A masterless system can accept the write locally in Sydney and replicate asynchronously.

**Problem 4 — B-tree write amplification.** Relational and document databases use B-trees. A B-tree update is a **random write**: find the page, modify it, write it back. On spinning disks that was catastrophic; even on SSDs it causes write amplification and wear. A workload of 500,000 writes/sec of small records is pathological for a B-tree. **LSM-trees** (§4.4) turn every write into a sequential append, and that difference is roughly an order of magnitude.

### The design response

Cassandra is explicitly the combination of two papers:
- **Amazon's Dynamo (2007)** contributed the *distribution* model: consistent hashing, masterless peers, tunable quorums, hinted handoff, anti-entropy.
- **Google's Bigtable (2006)** contributed the *data* model and *storage engine*: the sorted sparse map, column families, memtable/SSTable/compaction.

The price paid for all of this is stated bluntly in §4 and §14: **you give up ad-hoc queries, joins, and strong transactional guarantees.** Wide-column is not a general-purpose database that happens to scale. It is a specialised engine that trades query flexibility for write throughput and availability, and using it for a workload that needs flexibility is a self-inflicted wound.

## 3. Real-world analogy

**A global chain of warehouse depots with pre-sorted aisles.**

Every depot in the chain is **identical in authority** — there is no head office that must approve a delivery. A lorry arriving in Osaka unloads in Osaka. Nothing crosses an ocean first. That is masterless writes.

Each depot is divided into **aisles**, and which aisle a pallet goes to is determined by a rule applied to the pallet's label: `aisle = hash(customer_id)`. You cannot browse for a pallet; **you must know the customer ID to compute the aisle.** Without it, the only option is to walk every aisle in every depot — which is exactly what an unpartitioned query does, and why Cassandra refuses to run one without `ALLOW FILTERING`.

Within an aisle, pallets are **kept in strict date order, newest at the front**. So "the last 20 deliveries for customer 5512" means walking to one aisle and taking the first 20 pallets. That is the clustering key, and it is why range queries within a partition are cheap.

Each pallet is stored in **three depots** (replication factor 3). When a lorry delivers, the driver does not wait for all three — they wait for **two** confirmations and drive off (write quorum). A reader checking stock also asks **two** depots, and because 2 + 2 > 3, at least one depot they ask must have seen the delivery.

New stock is never filed into the aisles immediately — that would mean constant reorganisation. It goes onto a **receiving bench** at the front (memtable), and once the bench is full the whole bench is sealed and wheeled into a new, immutable **sorted crate** (SSTable). Finding an item may mean checking the bench plus several crates. Periodically a crew **merges several crates into one** (compaction), discarding superseded items.

And crucially: **you cannot remove an item from a sealed crate.** To "delete" a pallet, you place a **bright red "this item is gone" marker** at the front (a tombstone). Everyone who looks must find the marker and honour it, including depots that were offline during the delete. The markers accumulate and are only cleared during a later merge.

**Where the analogy breaks down:**

| Warehouse depots | Wide-column database |
|---|---|
| A depot worker can walk any aisle if they are patient | A multi-partition scan is not merely slow — Cassandra **refuses it** by default, because its cost is unbounded and it would destabilise the whole cluster |
| One pallet is stored once per depot | The same logical data is **deliberately stored in 3–6 different tables**, each sorted differently for a different query. Duplication is the design, not a defect |
| A "gone" marker takes up little room | Tombstones are stored, replicated, read on every query touching their range, and can make a partition **completely unreadable** if enough accumulate. Deletes are a first-class hazard |
| Clearing markers is routine housekeeping | A tombstone can only be purged after `gc_grace_seconds` (default **10 days**), because purging early would let an offline replica **resurrect deleted data** |
| Stock levels are a single physical truth | Two depots can accept conflicting updates simultaneously; the conflict is resolved by **last-write-wins on timestamp**, which means one update is silently discarded |
| Merging crates is cheap background work | Compaction consumes substantial CPU, disk I/O, and **up to 50% spare disk space** with the default strategy. Compaction falling behind is a classic production incident |
| You could install a central ledger to coordinate | Coordination exists (lightweight transactions) but costs **~4 network round trips** — roughly 4× a normal write. It is an exception mechanism, not a foundation |

## 4. Detailed technical explanation

This section uses **Apache Cassandra** as the concrete system. HBase and Bigtable are compared in §12 and noted inline where they differ meaningfully.

### 4.1 The data model: partition key + clustering key

A Cassandra primary key has two parts:

```
PRIMARY KEY ( (partition_key_cols...), clustering_col_1, clustering_col_2, ... )
             └──────────┬───────────┘  └──────────────┬──────────────────┘
                 WHERE the data lives          HOW it is SORTED
                 (which node)                  (within the partition)
```

- The **partition key** is hashed to a 64-bit **token** (Murmur3 by default). The token determines which node owns the data. **Every efficient query must supply the full partition key.** This is not a guideline; it is enforced.
- The **clustering keys** define the sort order of rows *inside* the partition, which makes `WHERE ... AND ts > X AND ts < Y` a contiguous disk range read rather than a scan.

This is why the heading of this section could be: **the primary key IS the query.** In a relational database, the schema describes the data and indexes are added later to serve queries. In Cassandra the primary key *is* the access path, chosen before you write a byte, and changing it means creating a new table and migrating.

Supported query shapes on a table `PRIMARY KEY ((customer_id), order_date, order_id)`:

| Query | Allowed? | Why |
|---|---|---|
| `WHERE customer_id = 5512` | ✅ | Full partition key → one node |
| `WHERE customer_id = 5512 AND order_date > '2026-01-01'` | ✅ | Range on the first clustering column within a partition |
| `WHERE customer_id = 5512 AND order_id = 991` | ❌ | Skips `order_date`; clustering columns must be used left to right |
| `WHERE order_date > '2026-01-01'` | ❌ | No partition key → would need every node |
| `WHERE customer_id IN (5512, 5513, 5514)` | ⚠️ | Legal, but the coordinator fans out to several partitions; fine for 3, terrible for 300 |
| `WHERE customer_id = 5512 ORDER BY order_date ASC` | ✅ | Only along the clustering order (or its exact reverse) |
| `WHERE status = 'shipped' ALLOW FILTERING` | ☠️ | Full cluster scan. `ALLOW FILTERING` is a warning label, not a feature |

### 4.2 Query-first modelling and deliberate denormalisation

Because the primary key is the query, modelling proceeds in the opposite direction from relational design:

```
RELATIONAL:     entities → normalised tables → indexes → queries
WIDE-COLUMN:    queries  → one table per query → denormalised writes
```

The rule is uncompromising: **one table per query pattern.** If BookNook needs orders by customer *and* orders by ID *and* orders by status-and-day, that is **three tables**, each holding the same data sorted differently, all written on every order.

Objections and their answers:

- *"That's duplicated data."* Yes, deliberately. Disk is the cheapest resource in the system; a cross-partition join is the most expensive. Trading storage for the elimination of joins is the entire thesis.
- *"How do they stay in sync?"* The application writes all three, usually in a **logged batch** (§4.8) which guarantees all-or-nothing eventually, not atomically-visible. Some teams use **materialized views**, which Cassandra marks experimental and which have known correctness and repair issues — the common production advice is to maintain the tables yourself.
- *"What about a query nobody anticipated?"* You create a new table and backfill it. That is the real cost of this model, and it is why §14 lists ad-hoc querying as a disqualifier.

**Secondary indexes** exist (`CREATE INDEX`) but are a trap at scale: a standard secondary index is **local to each node**, so querying it without a partition key fans out to every node and merges — which is precisely the thing the architecture exists to avoid. **SASI** and **SAI** (Storage-Attached Index, Cassandra 5.0) improve this materially but do not change the fundamental guidance: *model the table for the query*.

### 4.3 The ring and consistent hashing

Cassandra arranges nodes in a **ring** of token values (−2⁶³ to 2⁶³−1). This is **consistent hashing**, exactly as taught in **Lesson 38** — including **virtual nodes** (`num_tokens`, commonly 16 in modern versions, historically 256), which assign each physical node many small token ranges so that load spreads evenly and so that adding a node pulls a little data from *every* existing node rather than a lot from one neighbour.

Writing a row:
1. Hash the partition key → token.
2. Walk clockwise from that token; the first node owning that range is the **first replica**.
3. Continue clockwise to collect **N** replicas total (N = replication factor), skipping nodes to satisfy the **snitch**'s topology rules — `NetworkTopologyStrategy` spreads replicas across racks and data centres so a rack failure cannot take all copies.

Every node knows the full ring via the **gossip** protocol — each node exchanges state with a few random peers each second, so membership and health information propagates across a 500-node cluster in seconds without any central registry.

The node a client contacts becomes the **coordinator** for that request. It is not special; it just forwards to the replicas, collects responses, and applies the consistency rules below. Modern drivers use a **token-aware** policy: they compute the token client-side and contact a replica directly, saving one network hop.

### 4.4 The LSM-tree storage engine

This is where wide-column's write performance actually comes from. A **Log-Structured Merge tree (LSM-tree)** is a storage engine built on the principle that **sequential writes are vastly cheaper than random writes**.

**The write path:**

1. **Commit log append.** The mutation is appended to an on-disk commit log — a pure sequential append, no seeking. This is the durability point (crash recovery replays it).
2. **Memtable write.** The mutation is applied to an in-memory sorted structure (typically a skip list) for that table.
3. **Acknowledge.** That is it. The write is done. **There is no read before the write** — Cassandra does not check whether the row exists, does not read the old value, does not update in place. An `UPDATE` and an `INSERT` are literally the same operation (an **upsert**).

That third point is the whole trick. A B-tree write must *find* the page first: a read, then a modify, then a write. An LSM write is an append plus an in-memory insert. This is why Cassandra write latency is commonly **sub-millisecond at p50** and why a cluster scales write throughput almost linearly with node count.

4. **Flush.** When the memtable exceeds a threshold, it is sealed and written to disk **sequentially** as an immutable **SSTable** (Sorted String Table). Immutable means: never modified again, ever. The corresponding commit log segment can then be discarded.

An SSTable on disk comes with companions: a **partition index**, a **partition summary** (a sampled index of the index, held in memory), a **Bloom filter**, and compression metadata.

**The read path** — and why reads are the expensive side:

1. Check the **memtable** (in RAM).
2. Check the **row cache** if enabled, then the **key cache**.
3. For each SSTable on disk, consult its **Bloom filter**. A Bloom filter is a probabilistic bit-array structure that answers "is this key definitely absent, or possibly present?" — it can produce **false positives but never false negatives**. It lets Cassandra skip most SSTables without touching them, typically at ~1% false-positive rate for ~10 bits per key.
4. For SSTables the filter does not rule out, use the partition summary → partition index → seek to the data.
5. **Merge the fragments.** The same row may have pieces in the memtable and in four SSTables (an insert here, an update there, a deleted column, a tombstone). The read must reconcile them all, **newest timestamp wins per column**.
6. Return the merged result.

So: **a write touches one place; a read may touch many.** This is the fundamental asymmetry of LSM-trees, and it is the exact inverse of the B-tree trade-off (B-trees: cheaper reads, more expensive writes). This is covered further in **(Phase 3, Topic 12 — Indexing deep dive: B-tree vs LSM)**.

**Compaction** is the background process that limits the damage: it merges several SSTables into fewer, larger ones, discarding superseded values and (eligible) tombstones. Strategies:

| Strategy | Merges by | Best for | Cost |
|---|---|---|---|
| **STCS** (Size-Tiered) — default | Groups SSTables of similar size | Write-heavy workloads | A row can live in many SSTables → **worse read amplification**. Needs up to **50% free disk** for a major compaction |
| **LCS** (Leveled) | Fixed-size SSTables arranged in levels, non-overlapping within a level | Read-heavy workloads; a read usually touches ≤ 1 SSTable per level | ~2× the write I/O (higher **write amplification**) |
| **TWCS** (Time-Window) | Groups SSTables by time window; whole windows expire together | **Time-series with TTL** — the right answer for BookNook's events table | Only appropriate for time-ordered, TTL'd, never-updated data |

**Compaction falling behind is a classic production incident.** Symptoms: SSTable count climbing, read latency rising steadily, disk filling. Cause: write rate exceeding compaction throughput, or compaction throttled too low. It does not announce itself — it looks like gradual, unexplained read degradation.

### 4.5 Tunable consistency and R + W > N

Cassandra lets you choose, **per query**, how many replicas must respond.

Definitions:
- **N** = replication factor (copies of each partition).
- **W** = replicas that must acknowledge a write before it is reported successful.
- **R** = replicas that must respond to a read before an answer is returned.

**The rule: if R + W > N, the read set and the write set must overlap in at least one replica, so a read is guaranteed to see the latest acknowledged write.** This is *strong consistency* in the quorum sense, previewed in Phase 2.

Why it works: with N = 3 and W = 2, the write is on 2 of 3 nodes. With R = 2, you read from 2 of 3. By pigeonhole, any two 2-subsets of a 3-set share at least one member. That shared node has the latest value, and Cassandra returns the one with the newest timestamp.

Consistency levels:

| Level | Replicas required | Guarantee | Typical latency impact |
|---|---|---|---|
| `ANY` (write only) | 0 real replicas — a **hint** on any node counts | Almost none. Data may be invisible to reads for a long time. Rarely justified | Lowest |
| `ONE` | 1 | Eventually consistent. Fast | ~0.5–1 ms intra-DC, typical |
| `TWO` / `THREE` | 2 / 3 | Fixed count regardless of N | — |
| `QUORUM` | ⌊N/2⌋ + 1 **across all DCs** | Strong if R + W > N. In multi-DC this means **cross-DC latency on every operation** | +50–150 ms cross-region, typical |
| `LOCAL_QUORUM` | ⌊N/2⌋ + 1 **within the local DC** | Strong within the DC; other DCs converge asynchronously. **The standard production choice for multi-DC** | ~1–3 ms |
| `EACH_QUORUM` (write only) | A quorum in *every* DC | Strongest multi-DC write | Highest; fails if any DC is down |
| `ALL` | N | Strongest, **zero fault tolerance** — one slow node fails the query | Worst; an availability anti-pattern |
| `LOCAL_ONE` | 1 in the local DC | Fast, local, eventually consistent | Lowest practical |

**Worked examples, all with N = 3:**

| W | R | R+W | > N? | Consequence |
|---|---|---|---|---|
| ONE (1) | ONE (1) | 2 | No | Fast both ways. A read may miss a just-written value entirely. Fine for metrics and page-view counters |
| QUORUM (2) | QUORUM (2) | 4 | **Yes** | Strong consistency; tolerates 1 node down for both reads and writes. **The default recommendation** |
| ALL (3) | ONE (1) | 4 | **Yes** | Reads are minimum-latency and strongly consistent; writes fail if *any* replica is down. Suits read-heavy, write-rare reference data — but availability for writes is now zero-fault-tolerant |
| ONE (1) | ALL (3) | 4 | **Yes** | Writes are fastest; reads fail if any replica is down. Suits write-heavy ingestion where the rare read must be correct |
| QUORUM (2) | ONE (1) | 3 | No | Eventually consistent reads. Common and often *fine* — but it must be a decision, not an accident |

Two critical caveats that separate understanding from recitation:

1. **R + W > N gives you overlap, not linearizability.** Two concurrent writes to the same column are resolved by **last-write-wins on timestamp**; one is silently discarded. Clock skew between coordinators can therefore cause the "wrong" write to win. Cassandra uses client/coordinator-supplied microsecond timestamps, so NTP discipline is a correctness concern, not just an ops nicety.
2. **A failed write is not a rolled-back write.** If W = QUORUM and only 1 of 3 replicas acknowledges, the client gets an error — but **the mutation persists on that one replica** and will be propagated by read repair or anti-entropy. There is no undo. Applications must therefore be designed so that retries are **idempotent**.

The full theoretical framing of these trade-offs is **(Phase 3, Topic 20 — CAP theorem)** and **(Phase 3, Topic 21 — PACELC & consistency models)**.

### 4.6 Tombstones — why deletes are a known hazard

In an LSM-tree, SSTables are immutable. **You cannot remove data from an immutable file.** So a delete is implemented as a **write**: a marker called a **tombstone**, carrying a timestamp, recording that this cell/row/range is deleted.

Consequences, each of which bites in production:

1. **Deletes make the database bigger, not smaller** — until compaction.
2. **Tombstones are read on every query that touches their range.** Reading a partition where 50,000 rows were deleted means reading and merging 50,000 tombstones to return zero rows. Cassandra warns at **1,000 tombstones** in a query (`tombstone_warn_threshold`) and **aborts the query** at **100,000** (`tombstone_failure_threshold`). A partition can become permanently unreadable.
3. **Tombstones cannot be purged for `gc_grace_seconds`, default 10 days.** Why: suppose replica C is offline when a row is deleted on A and B. If the tombstone were purged immediately and C came back, C's copy of the row would be the only version, and anti-entropy would **resurrect deleted data**. The grace period is the window in which repair must run. **The rule follows directly: you must run full repair at least once every `gc_grace_seconds`, or deleted data can come back.**
4. **TTL expiry creates tombstones too.** Data with a TTL does not vanish; it becomes a tombstone at expiry. A table with aggressive TTLs generates them continuously — which is precisely why **TWCS** exists, dropping entire expired SSTables without a merge.
5. **Range tombstones and partition deletes** are cheaper than per-row deletes (one marker covering a span), which is why `DELETE FROM t WHERE pk = ?` is far healthier than deleting a million individual rows.

The design guidance: **model so you rarely delete.** Use TTLs with TWCS for expiring data; use a new partition rather than clearing an old one; never use a Cassandra table as a queue (write, read, delete, repeat — the worst possible tombstone pattern, and a documented anti-pattern).

### 4.7 Repair: read repair, hinted handoff, anti-entropy

Because writes can succeed on a subset of replicas, Cassandra needs mechanisms to converge. There are three, and they are complementary:

**Hinted handoff.** If a replica is down when a write arrives, the coordinator stores a **hint** — a record of the missed mutation — locally and replays it when the node returns. Hints are kept for `max_hint_window_in_ms` (default **3 hours**). Beyond that they are dropped, and the gap must be closed by repair. Hints handle **brief** outages: a restart, a short network blip.

**Read repair.** On a read at a consistency level above ONE, the coordinator compares digests from the responding replicas. If they disagree, it fetches full data, determines the newest version per column, returns it to the client, and **writes the correct value back to the stale replicas**. There is also a probabilistic background variant (`read_repair_chance`, now deprecated in favour of the blocking repair that happens as part of the read). Read repair only fixes **data that is actually read** — cold data never converges through this path.

**Anti-entropy repair (`nodetool repair`).** The scheduled, comprehensive mechanism. Replicas build **Merkle trees** — hash trees over token ranges — and exchange them. Where the hashes differ, only the differing ranges are streamed. This is the only mechanism that fixes data nobody reads, and it is **mandatory**, not optional: it must run within `gc_grace_seconds` or deleted data resurrects (§4.6). It is I/O-intensive and is normally run incrementally, per-range, during off-peak hours, often via an orchestrator such as Reaper.

The relationship: **hinted handoff covers minutes, read repair covers hot data, anti-entropy covers everything else.** Omitting the third is one of the most common operational failures in Cassandra deployments.

### 4.8 Lightweight transactions and batches

**Lightweight transactions (LWT)** provide compare-and-set:

```
INSERT INTO users (email, id) VALUES ('a@b.com', 1) IF NOT EXISTS;
UPDATE orders SET status='shipped' WHERE id=991 IF status='packed';
```

They are implemented with **Paxos**, a consensus protocol, and require roughly **four round trips** (prepare/promise, read, propose/accept, commit) among the replicas instead of one. The practical costs:

- **~4× the latency** of a normal write; commonly cited as 10–20× worse throughput on contended partitions.
- They are **per-partition only** — there is no cross-partition atomicity.
- Under contention, Paxos rounds collide and retry, so throughput *degrades* as concurrency rises.
- Mixing LWT and non-LWT writes to the same partition breaks the guarantee entirely, because a plain write ignores the Paxos state.

LWTs are the right tool for genuine uniqueness constraints (one account per email) and rare state transitions. They are the wrong tool for anything on a hot path.

**Batches** are widely misunderstood. A Cassandra `BATCH` is **not** a performance optimisation and **not** a transaction:

- A **logged batch** writes a batch log to two nodes first, guaranteeing that all statements will *eventually* apply even if the coordinator dies. It is **atomic in the "all will happen" sense, but not isolated** — other readers can observe a partially-applied batch. It costs roughly **30% extra** over individual writes.
- An **unlogged batch** across many partitions is an anti-pattern: it makes one coordinator responsible for fanning out to many nodes, creating a hotspot and *reducing* throughput versus parallel individual writes.
- The legitimate use: **a single-partition batch** (all statements hitting one partition), which is applied atomically and isolated, and is genuinely efficient.

The correct mental model mirrors Lesson 44's: **batches exist to keep denormalised tables consistent, not to make writes faster.**

### 4.9 Partition sizing

A partition is the unit of storage, replication, repair, and read. Its size therefore governs everything.

Practical guidance (approximate, widely cited):
- **Target: under 100 MB per partition.**
- **Warn: over 100 MB.** **Hard trouble: over 1 GB** or over ~100,000 rows.
- Cassandra logs a `compaction large partition warning` past `compaction_large_partition_warning_threshold_mb` (default 100 MB).

A **hot partition** — one key receiving a disproportionate share of traffic — is the Cassandra equivalent of Lesson 44's hot shard, and it is worse here because the partition cannot be split: the partition key is the atom of distribution. The fix is **bucketing**: add a synthetic component to the partition key to break it apart.

```
BAD:   PRIMARY KEY ((book_id), ts)
         → a bestseller's events all land in one partition, growing forever

GOOD:  PRIMARY KEY ((book_id, day), ts)
         → one partition per book per day: bounded, and distributed across the ring
```

This is exactly the bucket pattern from Lesson 44, arrived at from a different direction. In Cassandra it is not an optimisation — it is the difference between a working table and a broken one.

## 5. Request / data flow

A BookNook write — recording a page-view event — followed by a read, through a 12-node, 2-DC Cassandra cluster with N = 3 per DC:

1. **Application issues** `INSERT INTO events_by_book_day (book_id, day, ts, customer_id, referrer) VALUES (...)` at `LOCAL_QUORUM`.
2. **Driver computes the token client-side.** It hashes the partition key `(book_id, day)` with Murmur3 → a 64-bit token. Because the driver is **token-aware**, it knows which nodes own that token and sends the request directly to one of them, saving a hop.
3. **That node becomes the coordinator.** It consults the ring (learned via gossip) and identifies the 3 local-DC replicas plus the 3 remote-DC replicas.
4. **Coordinator dispatches the mutation in parallel** to all 3 local replicas and (asynchronously, not waited on) to a single forwarding node in the remote DC, which fans out there.
5. **Each replica, independently:** appends to its **commit log** (sequential, durable), then applies the mutation to the in-memory **memtable**, then acknowledges. No disk seek, no read-before-write.
6. **If a replica is down**, the coordinator stores a **hint** for it and will replay the mutation when it returns (within the 3-hour hint window).
7. **Coordinator waits for 2 of 3 local acknowledgements** (`LOCAL_QUORUM` with N = 3) and returns success to the client. Typical total: **sub-millisecond to ~2 ms**.
8. **Later, asynchronously:** the memtable fills, is sealed, and is flushed **sequentially** to a new immutable **SSTable**; the covered commit-log segments are recycled.
9. **Later still:** **compaction** (TWCS for this table) merges SSTables within a time window and eventually drops whole expired windows without reading them.

The matching read — "page views for book 9780441013593 on 2026-10-09":

10. **Application issues** `SELECT * FROM events_by_book_day WHERE book_id = ? AND day = ? AND ts > ?` at `LOCAL_QUORUM`.
11. **Driver computes the token**, contacts a replica directly; that node coordinates.
12. **Coordinator requests full data from one replica and digests (hashes) from the other**, to minimise network bytes.
13. **On each replica:** check the memtable; consult each SSTable's **Bloom filter** to eliminate most of them; use the partition summary → partition index → disk seek for survivors; read the contiguous clustering-key range (`ts > ?` is a sequential scan within the sorted partition, which is why this is fast).
14. **Merge fragments** across memtable and SSTables, newest timestamp wins per column; apply any tombstones found.
15. **Coordinator compares digests.** If they match, return. If they differ, perform **read repair**: fetch full data from both, reconcile, return the newest to the client, and write the corrected value back to the stale replica.
16. **Client receives rows in clustering order.** Typical p50: **1–5 ms**; p99 depends heavily on SSTable count and tombstone load, and is the number to watch.

## 6. ASCII diagram

```
        BOOKNOOK — CASSANDRA RING (masterless, N=3)
        ===========================================

                        token 0
                           ●  node A
             node H  ●           ● node B
                  ╱                   ╲
        node G ●     hash(pk)=t          ● node C
                  ╲   lands here ↓      ╱        write goes to
             node F  ●     ✦      ● node D       the next 3 nodes
                           ●  node E              clockwise: E, F, G
                        token 2^63

   NO MASTER. Any node can coordinate any request.
   Virtual nodes (Lesson 38) give each physical node many
   small ranges, so adding a node pulls a little from everyone.


        PRIMARY KEY ANATOMY — the key IS the query
        ==========================================

   PRIMARY KEY (( book_id, day ), ts, event_id )
                └───────┬──────┘  └──────┬──────┘
                 PARTITION KEY      CLUSTERING KEYS
                 ↓ hashed→token     ↓ sort order ON DISK
                 WHICH NODE         CHEAP RANGE SCANS

   ┌── partition ("978044...", "2026-10-09") on nodes E,F,G ──┐
   │  ts=09:00:01 ▸ ts=09:00:04 ▸ ts=09:00:09 ▸ ... (sorted)  │
   │  ← one seek, then a contiguous sequential read →          │
   └───────────────────────────────────────────────────────────┘


        LSM-TREE WRITE PATH (why writes are so fast)
        ============================================

   write ──► ① COMMIT LOG (sequential append, durable)
         └─► ② MEMTABLE   (in RAM, sorted skip list)
                   │  ACK TO CLIENT HERE  ◄── no read, no seek
                   │
            memtable full
                   ▼
             ③ FLUSH ──► SSTable_1 ─┐  IMMUTABLE,
                         SSTable_2  ├─ sorted, never
                         SSTable_3 ─┘  modified again
                              │
                       ④ COMPACTION
                              ▼
                      SSTable_merged  (superseded values
                                       + expired tombstones
                                       discarded)

        READ PATH (why reads cost more)
        ===============================
   read ──► memtable? ──► row/key cache? ──► for EACH SSTable:
                                              bloom filter
                                              ├ "definitely not" → skip
                                              └ "maybe"          → seek
                      ──► MERGE all fragments, newest ts per column
                      ──► apply TOMBSTONES
                      ──► return
   ONE write touches ONE place. ONE read may touch MANY.


        QUORUM:  R + W > N   (N = 3)
        ============================
     replicas:   [ R1 ]  [ R2 ]  [ R3 ]
     W=QUORUM=2:   ✔      ✔       ✗(hint stored)
     R=QUORUM=2:   ✔              ✔
                   └── overlap guaranteed ──┘
     2 + 2 = 4 > 3  ⇒ the read MUST see the write


        SAME DATA, FOUR TABLES, FOUR QUERIES
        ====================================
   ┌──────────────────┐  ┌──────────────────────┐
   │ orders_by_id     │  │ orders_by_customer   │
   │ PK((order_id))   │  │ PK((customer_id),    │
   │                  │  │     order_date DESC) │
   └──────────────────┘  └──────────────────────┘
   ┌──────────────────┐  ┌──────────────────────┐
   │ orders_by_status │  │ events_by_book_day   │
   │ PK((status,day), │  │ PK((book_id,day),ts) │
   │     order_date)  │  │   TTL 90d + TWCS     │
   └──────────────────┘  └──────────────────────┘
        ▲ ONE logged batch writes all order tables ▲
          Duplication is the DESIGN, not a defect.
```


---

# Lesson 46 — Graph Databases

*Phase 3, Topic 7*

## 1. Simple definition

A **graph database** is a database whose storage engine is built so that *following a relationship is a pointer dereference, not a lookup*.

Lessons 40–42 covered relational databases, where relationships are expressed as foreign keys and resolved at query time by **joins**. Lessons 43–45 covered key-value, document and wide-column stores, all of which are optimised for retrieving *one record by its key*. A graph database is optimised for a third thing entirely: **walking from record to record to record, many times, cheaply**.

Terms defined before use:

- **Node** (also called a *vertex*): one entity. A customer, a book, an author, a category.
- **Relationship** (also called an *edge*): a named, directed connection between exactly two nodes. `(Alice)-[:PURCHASED]->(Dune)`.
- **Property**: a key-value pair stored *on* a node **or on a relationship**. `{price: 8.99}`, `{purchased_at: '2026-03-01'}`.
- **Label**: a tag on a node that groups it into a set. `:Customer`, `:Book`. One node may carry several labels.
- **Property graph model**: the combination of the four things above — labelled nodes, directed named relationships, and properties on both. This is the model used by Neo4j, Amazon Neptune (in property-graph mode), Memgraph, TigerGraph and JanusGraph.
- **Traversal**: starting at one or more nodes and following relationships outward. The fundamental operation.
- **Hop**: one step of a traversal — crossing exactly one relationship.
- **Degree**: how many relationships a node has. A popular book has a high degree; a new book has a degree of 1 or 2.

The single sentence to remember: **a relational database stores the *data* and computes the *relationships*; a graph database stores the *relationships* as first-class, pre-resolved physical structures.**

## 2. Why it exists

Take a real BookNook question: *"Customers who bought this book also bought…"*

In SQL (Lesson 41) you have `orders`, `order_items`, `books`. The query is a self-join through `order_items`:

```sql
-- 2 hops: book -> customers who bought it -> other books those customers bought
SELECT oi2.book_id, COUNT(*) AS freq
FROM order_items oi1
JOIN orders o1 ON o1.id = oi1.order_id
JOIN orders o2 ON o2.customer_id = o1.customer_id
JOIN order_items oi2 ON oi2.order_id = o2.id
WHERE oi1.book_id = 42 AND oi2.book_id <> 42
GROUP BY oi2.book_id ORDER BY freq DESC LIMIT 10;
```

That is fine. Two hops is fine. Now ask a four-hop question — *"recommend books liked by people who are similar to people who are similar to me"*, or *"which accounts are within four transfers of this flagged account"*. Written in SQL it looks like this:

```sql
-- 4 hops of "friend of a friend", written as a self-join chain
SELECT DISTINCT f4.to_id
FROM friends f1
JOIN friends f2 ON f2.from_id = f1.to_id
JOIN friends f3 ON f3.from_id = f2.to_id
JOIN friends f4 ON f4.from_id = f3.to_id
WHERE f1.from_id = 1001;
```

Here is the honest version of the claim you will hear repeated everywhere ("SQL joins degrade exponentially, graph traversal is constant"). It is *half* true, and the half that is false matters:

1. **The number of rows touched grows exponentially in both systems.** If the average degree is 30, hop 4 touches roughly 30⁴ = 810,000 relationships whether you use Postgres or Neo4j. Nobody escapes the combinatorics of the graph itself.
2. **The cost *per relationship touched* is what differs.** In SQL, each of those 810,000 steps is an index probe: a B-tree descent of 3–4 nodes plus a row fetch, roughly **1–2 µs** when everything is cached in RAM and 100× worse when it is not. In a graph database with index-free adjacency (§4), each step is a pointer dereference into a fixed-offset record: roughly **0.1–0.2 µs**.
3. **The SQL optimiser's plan is unstable.** Below some frontier size the planner picks nested-loop index joins; above it, it flips to hash joins that scan the whole relationship table. The flip is invisible to you until a query that took 40 ms takes 12 s.
4. **SQL must materialise intermediate results.** `LIMIT 10` cannot usually be pushed into the middle of a join chain, so the database computes 810,000 rows and then throws away 809,990. A traversal engine can stop as soon as it has 10 answers.

So the real reason graph databases exist is: **a constant factor of roughly 10×, multiplied by plan stability, multiplied by early termination, multiplied by a query language in which "four hops" is four characters instead of four joins.** At one hop that combination is worthless. At four or five hops, or at variable depth (`*1..6`), it is the difference between a feature that ships and a feature that does not.

There is a second, softer reason: **expressiveness**. "Find the shortest path between these two authors" has no sensible SQL formulation at all. Neither does PageRank. Those belong to §4's algorithm section.

## 3. Real-world analogy

**A city's road network versus the national address register.**

The address register (relational) is a perfect alphabetical index. Ask it "what is at 14 Oak Street?" and it answers instantly — one lookup, no matter how many million addresses exist. That is a primary-key read (Lesson 40).

Now ask it "what is the shortest route from 14 Oak Street to 87 Elm Road?" The register is useless. It knows *what is where*, not *what connects to what*. To answer, it must repeatedly re-consult itself: look up Oak Street, find its junctions, look each junction up again, find their junctions, look those up again. Every single step is another trip back to the alphabetical index.

A **road map** (graph) is the opposite. It is organised by connection. You put a finger on Oak Street and physically trace outward. Each junction *is* already next to its neighbours on the paper — you do not re-consult an index to discover what a road connects to. The index (the map's street-name gazetteer) is used **once**, to find where to put your finger down. After that, you never touch it again.

That last sentence is the whole lesson. In a graph database, **indexes are used only to find the starting node**. Everything after that is tracing.

**Where the analogy breaks down:**

| City road map | Graph database |
|---|---|
| Roads are physically local — nearby places are nearby on the paper | Graph neighbours can be anywhere in memory or on disk; "locality" is a pointer, not a position. On a cold cache every hop can be a random I/O |
| A junction has 3–5 roads | Node degree follows a power law: one BookNook bestseller may have 400,000 `PURCHASED` relationships while the median book has 3. Algorithms that assume uniform degree fall over on these **supernodes** |
| The map fits in one pair of hands | A graph that exceeds one machine is extremely hard to split, because a traversal crosses the split at arbitrary points (§10). Road maps tile neatly; social graphs do not |
| Tracing a route is free | Each hop is CPU and cache work; 30 million hops is 30 million × 0.15 µs ≈ 4.5 s |
| The map is static | BookNook's graph takes writes continuously, and a write must update the adjacency structures of *both* endpoint nodes |
| One kind of connection (road) | Real graphs are multi-relational: `PURCHASED`, `REVIEWED`, `WROTE`, `IN_CATEGORY`, `SIMILAR_TO`. Filtering by relationship type during traversal is the main performance lever |

## 4. Detailed technical explanation

### 4.1 The property graph model

Four building blocks, nothing else:

```
(:Customer {id: 1001, name: 'Ada', joined: '2024-02-11'})
       │
       │ [:PURCHASED {at: '2026-03-01', price: 8.99, channel: 'web'}]
       ▼
(:Book {isbn: '9780441013593', title: 'Dune', year: 1965})
       │
       │ [:WRITTEN_BY]
       ▼
(:Author {name: 'Frank Herbert'})
```

Three things are worth noticing, because they are exactly what a relational schema cannot do cheaply:

- **Relationships carry properties.** `price` and `at` live on the `PURCHASED` edge. In SQL this requires a junction table with extra columns — which is fine, but it means the relationship is now a *row* that must be found by index.
- **Relationships are typed and directed.** A traversal can say "follow only `PURCHASED`, only outgoing" and the engine skips every other edge on the node without reading it.
- **There is no fixed schema.** You can add a `:Prime` label or a `wishlisted` property to one node without migrating 50 million others. (The trade-off is the same one Lesson 44 made for document databases: no schema means no schema *enforcement*.)

### 4.2 Index-free adjacency — the core idea

This is the term that separates people who understand graph databases from people who have read the marketing page. Here is what it actually means at the storage level, using Neo4j as the concrete case.

Neo4j stores **fixed-size records in flat files**. A node record is 15 bytes. A relationship record is 34 bytes.

Because every record is the same size, **record ID *n* lives at byte offset *n* × record_size**. Finding node 1,000,001 is one multiplication and one seek. There is no B-tree, no hash table, no lookup structure of any kind. That is the "index-free" part.

The "adjacency" part: each node record contains a pointer to the ID of its **first** relationship. Each relationship record is a member of a **doubly linked list** — and because a relationship touches two nodes, it belongs to *two* lists at once, so it stores four pointers: previous/next relationship for the start node, and previous/next for the end node, plus both node IDs and a relationship-type ID.

```
 NODE RECORD (15 bytes, fixed)
 ┌────────┬──────────────────┬───────────────┬────────┐
 │ in_use │ first_rel_id ────┼─► 8842        │ labels │
 └────────┴──────────────────┴───────────────┴────────┘

 RELATIONSHIP RECORD 8842 (34 bytes, fixed)
 ┌──────────┬────────┬──────┬───────────────┬──────────────┐
 │ start=17 │ end=93 │ type │ start_prev/next│ end_prev/next│
 └──────────┴────────┴──────┴───────────────┴──────────────┘
```

To traverse from node 17: read node record at offset 17 × 15, get `first_rel_id = 8842`, read relationship record at offset 8842 × 34, note `end = 93`, follow `start_next` to the next relationship, repeat. **Every step is arithmetic plus a memory read. Cost per hop is independent of how many nodes exist in the database.**

Compare with a relational join on `friends(from_id, to_id)` holding 30 million rows: each step is a B-tree descent. With a fanout of about 200, the tree is 4 levels deep (200³ = 8 M < 30 M ≤ 200⁴). Four node accesses, each with a binary search inside the page, then a row fetch. That is the 1–2 µs figure from §2, and — this is the important part — **it grows as `log(N)` when the dataset grows**, whereas pointer chasing does not.

Two honest caveats that interviewers love:

1. **Index-free adjacency is a property of the *storage engine*, not of the word "graph".** Amazon Neptune, JanusGraph and Neo4j Fabric store adjacency in an underlying key-value/column store and therefore do *not* have true index-free adjacency; they have very good adjacency *indexes*. They still beat SQL on traversal ergonomics; they do not have the same constant factor.
2. **It only holds while the relevant part of the graph is in the page cache.** If the pointer chase hits disk, 0.15 µs becomes 100 µs on NVMe and the entire advantage evaporates. This is why graph databases are sized by *"does the working set fit in RAM?"* far more aggressively than relational ones.

### 4.3 Neo4j specifics: Cypher, traversal, and what indexes are for

**Cypher** is a declarative pattern-matching language. Its central idea is **ASCII art**: you draw the pattern you want and the engine finds every subgraph matching it.

```
(a)-[:WROTE]->(b)<-[:PURCHASED]-(c)
```

reads as "an `a` that wrote a `b` that a `c` purchased". There is no join order to write, no join key to repeat.

**Traversal versus scanning.** Every Cypher query has two phases:

1. **Find the anchor(s)** — the starting node or nodes. This is the *only* place an index is used. Without an index, finding `Book {isbn: '...'}` is a **label scan**: read every `:Book` node. With 50,000 books that is ~5 ms, tolerable; with 50 million it is not.
2. **Expand** — traverse outward by pointer chasing. No index, cost proportional to relationships touched.

A Cypher query that is slow is almost always slow because of phase 1, or because phase 2 was allowed to expand without a relationship-type filter or a depth bound. The two levers are: **index your anchors**, and **bound your expansion**.

Neo4j index types worth knowing: `RANGE` (B-tree-like, for equality and ranges), `TEXT` (substring), `POINT` (geospatial), `FULL-TEXT` (Lucene-backed — and note that this is literally Lesson 47's machinery embedded inside a graph database), plus `UNIQUE` constraints which also create an index.

### 4.4 RDF triple stores and SPARQL — the other graph family

There is a second, older graph tradition, and it is a *different data model*, not a different vendor.

An **RDF triple store** stores statements of exactly three parts: **subject – predicate – object**.

```
<booknook:book/9780441013593>  <dc:title>      "Dune"
<booknook:book/9780441013593>  <dc:creator>    <booknook:author/herbert>
<booknook:author/herbert>      <rdf:type>      <schema:Person>
```

**SPARQL** is its query language, and it looks like SQL over triple patterns:

```sparql
SELECT ?title WHERE {
  ?book <dc:creator> <booknook:author/herbert> .
  ?book <dc:title> ?title .
}
```

| | Property graph (Neo4j, Neptune, Memgraph) | RDF triple store (Virtuoso, GraphDB, Neptune-RDF, Stardog) |
|---|---|---|
| Unit of storage | Node / relationship with properties | One triple |
| Properties on relationships | Native and cheap | Impossible directly; requires **reification** (turning the statement into a node) or RDF-star |
| Identifiers | Internal IDs, arbitrary | Global URIs — data from two organisations merges automatically |
| Schema | Optional labels | RDFS / OWL ontologies, with **inference** (if `:Novel rdfs:subClassOf :Book`, a query for books returns novels without you asking) |
| Standardisation | Vendor-specific (GQL is the emerging ISO standard) | W3C standards since 2004; federated queries across endpoints |
| Typical use | Recommendations, fraud, network/IT topology, access control | Knowledge graphs, life sciences, government open data, library/bibliographic metadata, data integration across organisations |

For BookNook the property graph wins easily, because the valuable facts (`price`, `purchased_at`, `rating`) live **on the relationship**. If BookNook wanted to merge its catalogue with the Library of Congress and OpenLibrary datasets, RDF's global URIs and inference would be the better tool.

### 4.5 Graph algorithms that actually earn money

A graph database is not only a query engine; it ships a library of algorithms (Neo4j GDS, Neptune Analytics, NetworkX for offline work). Four matter in practice.

| Algorithm | What it computes | Typical cost | Business use |
|---|---|---|---|
| **Shortest path** (BFS for unweighted, Dijkstra/A\* for weighted) | Fewest hops/lowest cost between two nodes | O(V + E) for BFS | *"How is this flagged account connected to this known fraudster?"* Also BookNook's "6 degrees of authors" marketing feature, and delivery routing |
| **PageRank** | Importance of a node as a function of the importance of nodes pointing at it; iterative, usually 20 iterations | O(20 × E) | Ranking authors by influence in the citation/co-authorship graph; ranking books in a `SIMILAR_TO` graph; detecting link-farm review spam (sudden PageRank spikes) |
| **Community detection** (Louvain, label propagation) | Partitions nodes into clusters that are densely connected internally | Louvain ≈ O(E log V) | Customer taste segments for BookNook's email campaigns — discovered from behaviour, not from a marketer's guess. Also fraud *rings* (not just individuals) |
| **Centrality** (degree, betweenness, closeness) | How structurally important a node is. Betweenness = how often a node lies on shortest paths between others | Betweenness is the expensive one: O(V × E) exact, so sampled in practice | Betweenness finds **brokers** — the mule account that connects two otherwise separate fraud clusters, or the single supplier whose failure disconnects a supply chain |

A fifth, less glamorous but most used at BookNook: **node similarity** (Jaccard or cosine over neighbour sets) — *"these two books share 61 % of their purchasers"* — which is precisely the "also bought" feature, computed as a batch job and written back as `SIMILAR_TO` relationships with a `score` property.

### 4.6 When a recursive CTE is enough and a graph database is overkill

PostgreSQL, SQL Server, Oracle and MySQL 8+ all support **recursive common table expressions** (`WITH RECURSIVE`), which do iterative traversal inside the relational engine. This is the honest competitor, and for most teams it wins.

Use a recursive CTE, **not** a graph database, when *all* of the following hold:

- **Depth is bounded and small: ≤ 3–4 hops**, or an unbounded hierarchy whose real depth is under ~10 (category trees, org charts, bill-of-materials).
- **The frontier stays small: under roughly 10,000 rows at the widest level.** This is the number that actually decides it. A 10-level org chart is trivial; a 3-hop social traversal with degree 200 is 8 million rows and is not.
- **The relationship table is under roughly 10–50 million rows** and its index fits in RAM.
- **Traversal is a minority of your workload** — say under 10 % of queries — so it does not justify a second datastore with its own backups, monitoring, on-call runbook and sync pipeline (§11).
- **You do not need graph algorithms.** PageRank and Louvain in SQL are possible and horrible.

Switch to a graph database when: depth is **variable and ≥ 4**, or the frontier exceeds ~100,000, or path-finding/weighting is a product feature, or you want the algorithm library, or (the most common real reason) **the queries have become unreadable 200-line SQL that nobody dares change**.

The grey zone between those two lists is real. In it, the deciding factor is operational: *one database you already run well beats two databases you run badly.*

## 5. Request / data flow

A BookNook shopper opens the page for *Dune*. The page calls `GET /books/9780441013593/also-bought?limit=10`, served from Neo4j.

1. The API service receives the request and extracts the ISBN. It validates it against a regex — never string-concatenate user input into Cypher; use parameters (§15).
2. The service takes a connection from the **Bolt** driver pool (Bolt is Neo4j's binary wire protocol). Pooling matters for the same reason as in Lesson 40 — session setup costs more than the query.
3. The service sends a parameterised Cypher statement with `{isbn: '9780441013593', limit: 10}`.
4. Neo4j checks its **query plan cache**, keyed by the query text *without* parameter values. A cache hit skips planning entirely (~0.3 ms saved).
5. **Phase 1 — anchor.** The planner uses the `RANGE` index on `:Book(isbn)` to find exactly one node ID. This is one B-tree descent: ~4 page reads, ~2 µs. **This is the only index access in the entire query.**
6. **Phase 2 — hop 1.** From that node record, follow the `first_rel_id` pointer and walk the relationship chain, keeping only incoming `:PURCHASED` edges. *Dune* has 12,000 purchasers. 12,000 pointer dereferences.
7. **Phase 2 — hop 2.** For each of those 12,000 customer nodes, walk *their* outgoing `:PURCHASED` chain. Average 8 purchases each → 96,000 dereferences.
8. The engine streams rows rather than materialising them: each `(customer, otherBook)` pair feeds straight into the aggregation.
9. **Aggregate.** A hash map counts occurrences per `otherBook` node ID, excluding *Dune* itself. ~96,000 hash increments.
10. **Order and limit.** A bounded min-heap of size 10 keeps the top results; the remaining ~40,000 distinct books are discarded without sorting.
11. For the surviving 10 node IDs, read the property records to fetch `title`, `isbn`, `cover_url`.
12. Results stream back over Bolt. Total server-side time: ~35 ms (typical for this shape; see §9).
13. The API service caches the response in Redis with a 6-hour TTL (Lesson 29). This is essential: "also bought" changes slowly, and caching converts a 35 ms graph query into a 1 ms cache hit for 99.9 % of page views.

Step 13 is the design point. **Graph queries are frequently expensive but highly cacheable**, because recommendations do not need to be fresh to the second. Many production "graph" features are actually a nightly batch job writing `SIMILAR_TO` edges, read back at request time as a single one-hop lookup.

## 6. ASCII diagram

```
HOW A RELATIONAL JOIN WALKS A GRAPH  (index probe at EVERY step)

   start_id=1001
        │
        ▼
   ┌──────────────┐   probe   ┌─────────────────────────┐
   │  B-TREE on   │◄──────────│ hop 1: find f1.to_id    │
   │ friends.from │           └─────────────────────────┘
   │   30M rows   │◄──────────  hop 2 (×30 probes)
   │   4 levels   │◄──────────  hop 3 (×900 probes)
   │              │◄──────────  hop 4 (×27,000 probes)
   └──────────────┘
   Every single arrow = 4 B-tree node reads + 1 row fetch ≈ 1.5 µs
   Cost per step grows as log(N) when the table grows.


HOW INDEX-FREE ADJACENCY WALKS THE SAME GRAPH

   index used ONCE ──► node 1001
                        │
   [node 1001]──► rel 8842 ──► rel 8843 ──► rel 8844 ──► ...
                     │            │            │
                     ▼            ▼            ▼
                 node 2007    node 4411    node 9120
                     │            │            │
                 rel 1200     rel 3344     rel 7781   ... and onward
                     ▼            ▼            ▼
   Every arrow = offset arithmetic + one memory read ≈ 0.15 µs
   Cost per step is CONSTANT no matter how large the database grows.


WHAT STILL EXPLODES IN BOTH  (the honest picture)

   hop:      1        2         3          4            5
   frontier: 30  →   900  →  27,000  →  810,000  →  24,300,000
             ░        ░░        ░░░░       ░░░░░░░░      ░░░░░░░░░░░░░░░░
   The GRAPH's branching factor is the enemy. Index-free adjacency
   makes each step ~10x cheaper; it does NOT make the frontier smaller.
   You must bound depth, filter relationship types, or cap the frontier.


BOOKNOOK'S GRAPH (one subgraph)

        (:Author "Herbert")
                 ▲
                 │ :WRITTEN_BY
        (:Book "Dune")──:IN_CATEGORY──►(:Category "Sci-Fi")
          ▲   ▲   ▲                           │ :CHILD_OF
          │   │   └───────────┐               ▼
 :PURCHASED   :PURCHASED   :REVIEWED    (:Category "Fiction")
 {price:8.99} {price:9.50} {stars:5}
          │   │               │
      (:Customer Ada)   (:Customer Bo)
          │                   │
          └─── :PURCHASED ───►(:Book "Neuromancer")   ◄── the "also bought" answer
```

## 7. Detailed example

### 7.1 BookNook — three graph features on one graph

BookNook's relational database (Lessons 40–41) stays the system of record. The graph is a **derived, secondary store** holding only what traversals need: ~1.2 M customers, 50 k books, 180 k authors, 14 M `PURCHASED` edges, 900 k `REVIEWED` edges, 50 k `IN_CATEGORY` edges, 400 `CHILD_OF` edges.

**Feature 1 — "Customers who bought this also bought".** Exactly the flow in §5. Before the graph, this was the 4-join SQL at the top of §2 running against the OLTP primary; at 12,000 purchasers of a bestseller it took 2.1 s and was quietly removed from bestseller pages (the irony being that it was removed precisely where it was most valuable). In the graph it is 35 ms uncached and 1 ms cached. Business result: +3.1 % attach rate on product pages — which, against the sizing in Lesson 19, pays for the graph node four times over.

**Feature 2 — author collaboration networks.** Nodes `:Author`, relationships `:CO_AUTHORED {works: 3}` and `:EDITED_BY`. Two product features fall out of this almost free:
- *"How is Author X connected to Author Y?"* → `shortestPath`, rendered as a little chain on the author page. Engagement gimmick, cheap to build, impossible in SQL.
- *"Rising authors to feature"* → PageRank over `:CO_AUTHORED`, which surfaces authors who collaborate with influential authors but have low direct sales. Merchandising uses this for the "discover" shelf.

**Feature 3 — category hierarchies.** `(:Category)-[:CHILD_OF]->(:Category)`, 6 levels deep, 400 nodes. *"Show every book in Sci-Fi including all sub-categories"* is a variable-depth traversal: `MATCH (c:Category {slug:'sci-fi'})<-[:CHILD_OF*0..6]-(sub)<-[:IN_CATEGORY]-(b:Book)`.

And here is the honest counterpoint, because this is the most instructive part of the BookNook story: **feature 3 did not need the graph database.** 400 nodes, 6 levels, a frontier that never exceeds 80 — this is squarely inside the recursive-CTE thresholds from §4.6. BookNook kept it in PostgreSQL. Putting the category tree in Neo4j would have meant syncing a table that changes twice a month into a second datastore in order to save 0.4 ms. **The presence of a graph database is not a reason to put graph-shaped data in it.**

### 7.2 Contrasting example — a payments company's fraud ring detection

A card payments processor has 40 M accounts and 2 B transfers. The fraud question is not *"is this transaction unusual?"* (that is a model over features, Lesson 42's territory) but *"is this account structurally embedded in a ring?"*

- **Shared-attribute edges**: accounts connected if they share a device fingerprint, phone, address, or card BIN+last4. These edges are the signal — a ring of 40 "independent" accounts that share three devices is obvious in a graph and invisible in a row-by-row rules engine.
- **Query shape**: at authorisation time, a *bounded* 3-hop traversal from the acting account over shared-attribute edges, with a strict 50 ms budget and a frontier cap of 5,000, computing ring size and the betweenness of the acting node.
- **Why not SQL**: the traversal is variable-depth, the frontier is large, and the answer is needed *synchronously inside a payment authorisation*. The 10× constant factor and early termination are the difference between a 45 ms answer and a timeout.
- **Why not a graph database for everything**: the ledger stays in a strongly consistent relational database with real ACID transactions (Phase 3, Topic 15 — Transactions and ACID). The graph is a read-optimised projection fed by change data capture. **Money never lives in the graph.**

The contrast with BookNook is the point: BookNook uses graph traversal *asynchronously and cacheably* for revenue uplift; the payments company uses it *synchronously under a hard latency budget* for loss prevention. Same technology, completely different operational requirements — the payments graph needs multi-region replicas and a hot standby, BookNook's needs a nightly rebuild and nothing else.

## 8. Code or configuration example

> **These snippets are illustrative teaching examples, not production-ready code.** They omit authentication, TLS, retry/backoff, transaction functions, pagination, access control, result-size limits, and error handling. Do not paste them into a service.

### 8.1 Creating the BookNook graph (Cypher)

```cypher
// Constraints create an index AND enforce uniqueness.
// Without this, MERGE below would do a full label scan per statement.
CREATE CONSTRAINT book_isbn IF NOT EXISTS
FOR (b:Book) REQUIRE b.isbn IS UNIQUE;

CREATE CONSTRAINT customer_id IF NOT EXISTS
FOR (c:Customer) REQUIRE c.id IS UNIQUE;

// A plain index on a frequently-anchored, non-unique property.
// Anchors need indexes; traversals do not.
CREATE INDEX book_title IF NOT EXISTS FOR (b:Book) ON (b.title);

// MERGE = "match if it exists, otherwise create". Idempotent, so the
// sync job can replay safely. MERGE without an index is a label scan.
MERGE (b:Book {isbn: '9780441013593'})
  ON CREATE SET b.title = 'Dune', b.year = 1965   // only on first insert
  ON MATCH  SET b.last_seen = datetime();          // only on replay

MERGE (c:Customer {id: 1001})
  ON CREATE SET c.joined = date('2024-02-11');

// Relationship properties: the purchase facts live on the EDGE, which
// is the thing a document or key-value store cannot model cheaply.
MERGE (c)-[p:PURCHASED {order_id: 55123}]->(b)
  ON CREATE SET p.at = datetime('2026-03-01T10:14:00Z'),
                p.price = 8.99;
```

### 8.2 "Also bought" — the §5 query

```cypher
// $isbn and $limit are PARAMETERS. Two reasons, both important:
//   1. no Cypher injection;
//   2. the plan cache is keyed on query text, so parameterising
//      turns thousands of distinct queries into one cached plan.
MATCH (b:Book {isbn: $isbn})                 // ANCHOR: index lookup, 1 node
MATCH (b)<-[:PURCHASED]-(c:Customer)         // hop 1: incoming edges only
MATCH (c)-[:PURCHASED]->(other:Book)         // hop 2: outgoing edges only
WHERE other <> b                             // exclude the book itself
WITH other, count(DISTINCT c) AS shoppers    // DISTINCT: one vote per customer
WHERE shoppers >= 3                          // noise floor: ignore coincidences
RETURN other.isbn, other.title, shoppers
ORDER BY shoppers DESC
LIMIT $limit;                                // bounded min-heap, no full sort
```

The arrow directions (`<-` then `->`) are not decoration. They halve the edges read at each hop by telling the engine which linked list to walk.

### 8.3 Variable-depth traversal and shortest path

```cypher
// *1..4 = "between 1 and 4 hops". Always bound the upper limit:
// an unbounded *  on a 14M-edge graph will exhaust memory.
MATCH (me:Customer {id: $id})-[:FRIEND*1..4]-(fof:Customer)
WHERE fof.id <> $id
RETURN DISTINCT fof.id
LIMIT 100;                                  // early termination: the engine
                                            // stops expanding once 100 are found

// Shortest path is a first-class clause, not something you implement.
// The planner uses bidirectional BFS: it expands from BOTH ends and
// meets in the middle, so cost is ~2 x degree^(d/2) instead of degree^d.
MATCH (a:Author {name: $from}), (z:Author {name: $to})
MATCH p = shortestPath( (a)-[:CO_AUTHORED*..6]-(z) )   // cap depth at 6
RETURN [n IN nodes(p) | n.name] AS chain,              // list comprehension
       length(p) AS hops;

// Always inspect the plan before shipping. Look for:
//   NodeIndexSeek  = good anchor
//   NodeByLabelScan / AllNodesScan = you forgot an index
//   db hits column  = the real cost metric, not wall clock
PROFILE MATCH (b:Book {isbn: $isbn})<-[:PURCHASED]-()-[:PURCHASED]->(o:Book)
        RETURN o.title LIMIT 10;
```

### 8.4 The same question as a PostgreSQL recursive CTE

```sql
-- The relational equivalent of a variable-depth traversal.
-- This is genuinely good for shallow, narrow traversals (see §4.6).
WITH RECURSIVE reachable(id, depth, path) AS (
    -- ANCHOR MEMBER: the seed row, executed once
    SELECT 1001::bigint, 0, ARRAY[1001::bigint]

    UNION ALL                                  -- ALL, not UNION: UNION would
                                               -- dedupe on every iteration

    -- RECURSIVE MEMBER: joined against the PREVIOUS iteration's output
    SELECT f.to_id, r.depth + 1, r.path || f.to_id
    FROM reachable r
    JOIN friends f ON f.from_id = r.id         -- <= THE INDEX PROBE, per row
    WHERE r.depth < 4                          -- hard depth bound: without it
                                               -- a cycle loops forever
      AND NOT f.to_id = ANY(r.path)            -- cycle detection: the array
                                               -- carries the visited path
)
SELECT DISTINCT id FROM reachable WHERE depth > 0;

-- Required for this to be anything other than catastrophic:
CREATE INDEX ON friends (from_id) INCLUDE (to_id);   -- covering index:
-- "INCLUDE (to_id)" puts to_id in the leaf page, so the probe never has to
-- visit the heap at all. This alone is often a 2-3x win.

-- Read the plan. If you see "Seq Scan on friends" inside the recursive
-- term, the index is not being used and the query is O(depth x table).
EXPLAIN (ANALYZE, BUFFERS) /* the query above */ ;
```

Note what the CTE needs that Cypher gives for free: an explicit depth bound, an explicit visited-path array for cycle detection, an explicit covering index, and `UNION ALL` rather than `UNION`. Each is a correctness or performance trap. The Cypher version is four lines and has the depth bound inline. **That ergonomic gap is a legitimate engineering argument, separate from performance.**

### 8.5 SPARQL, for comparison

```sparql
# The same "also bought" idea over RDF triples.
# Note: no properties on relationships, so the purchase price would need
# a separate reified node — this is RDF's structural weakness here.
SELECT ?otherTitle (COUNT(DISTINCT ?customer) AS ?shoppers)
WHERE {
  ?customer  bn:purchased  bn:book_9780441013593 .   # hop 1
  ?customer  bn:purchased  ?other .                  # hop 2
  ?other     dc:title      ?otherTitle .             # fetch the label
  FILTER(?other != bn:book_9780441013593)
}
GROUP BY ?otherTitle
ORDER BY DESC(?shoppers)
LIMIT 10
```

## 9. Numerical example

**The question:** how does the cost of an *n*-hop traversal diverge between PostgreSQL and a graph database as *n* goes from 1 to 5?

**Every assumption stated:**

| Assumption | Value | Why this number |
|---|---|---|
| Customers (nodes) | 1,000,000 | BookNook's active base, Lesson 19 |
| Average degree | 30 | Typical for a co-purchase/affinity graph |
| Relationship rows / edges | 30,000,000 | 1 M × 30 |
| Postgres index | B-tree on `friends(from_id) INCLUDE (to_id)` | §8.4 |
| B-tree fanout | 200 entries per 8 KB page | Typical for an 8-byte key |
| B-tree height | 4 levels | 200³ = 8 M < 30 M ≤ 200⁴ = 1.6 B |
| Index size | ~900 MB | 30 M × ~24 B of key+pointer+overhead; **fits in RAM**, so we are being generous to Postgres — no disk I/O at all |
| Cost of one cached B-tree probe | ~1.0 µs | 4 page touches ≈ 400 ns + in-page binary search ≈ 8 comparisons/level |
| Cost of materialising + hashing one intermediate row | ~0.5 µs | Tuple construction, dedup hash insert |
| **Postgres cost per edge traversed** | **1.5 µs** | 1.0 + 0.5 |
| Cost of one graph pointer dereference | ~0.15 µs | Offset arithmetic + 1–2 cache-line reads, warm page cache |
| **Graph cost per edge traversed** | **0.15 µs** | — |
| Ratio | **10×** | 1.5 ÷ 0.15 |

All figures are **typical, approximate, single-threaded, fully-warm-cache** numbers. Cold cache makes both far worse and makes Postgres relatively *better*, because disk latency dominates and the 10× CPU gap becomes noise.

**Step 1 — frontier size per hop.** Frontier(n) = 30ⁿ, capped by the 1 M population and by the 30 M total edges.

```
frontier(1) = 30
frontier(2) = 30 × 30        = 900
frontier(3) = 900 × 30       = 27,000
frontier(4) = 27,000 × 30    = 810,000
frontier(5) = 810,000 × 30   = 24,300,000  → but only 30 M edges exist and
                                             only 1 M nodes; the traversal
                                             saturates. Cap at 30,000,000 edges.
```

**Step 2 — cumulative edges traversed** (a depth-*n* query must do every shallower hop too):

```
n=1 :        30
n=2 :        30 +        900                       =        930
n=3 :       930 +     27,000                       =     27,930
n=4 :    27,930 +    810,000                       =    837,930
n=5 : 837,930 +  (saturated)                       = 30,000,000
```

**Step 3 — multiply by per-edge cost.**

| Hops | Edges traversed | Postgres (× 1.5 µs) | Graph (× 0.15 µs) | Verdict |
|---|---|---|---|---|
| 1 | 30 | 45 µs = **0.045 ms** | 4.5 µs = **0.005 ms** | Both instant. Difference is unmeasurable against 0.5 ms of network. **Use SQL.** |
| 2 | 930 | 1,395 µs = **1.4 ms** | 140 µs = **0.14 ms** | Both fine. **Use SQL.** |
| 3 | 27,930 | 41,895 µs = **42 ms** | 4,190 µs = **4.2 ms** | SQL is now visible in p99. Grey zone. |
| 4 | 837,930 | 1,256,895 µs = **1.26 s** | 125,690 µs = **0.13 s** | SQL has failed the request budget. **Use a graph.** |
| 5 | 30,000,000 | 45,000,000 µs = **45 s** | 4,500,000 µs = **4.5 s** | Neither is an online query. Make it a batch job. |

**Step 4 — the effect the table hides: the plan flip.** At hop 4 the Postgres planner estimates 810,000 inner-loop probes and switches from a nested-loop index join to a **hash join**, which sequentially scans all 30 M rows of `friends` per join level. At ~0.1 µs per sequentially-scanned row that is 3 s per level × 4 levels ≈ **12 s**, roughly 10× worse than the 1.26 s the nested-loop model predicted. The measured number is usually worse than the modelled number, and *discontinuously* so.

**Step 5 — the effect the table also hides: early termination.** The real BookNook query is `LIMIT 100`. A traversal engine stops expanding once 100 distinct results exist — typically early in hop 3, so ~3,000 edges, **0.45 ms**. Postgres must complete the whole join chain before `LIMIT` applies. At hop 4 that is the full 1.26 s (or 12 s) to return 100 rows. **With `LIMIT`, the gap at 4 hops is not 10× — it is closer to 1,000×.**

**Step 6 — and the honest closing.** Look at hops 1 and 2 again. **90 % of all real "graph" queries are 1 or 2 hops**, and at 1–2 hops the graph database's advantage is 1.3 ms of wall clock that nobody will ever notice. The numerical case for a graph database is made entirely at hops ≥ 3 with a wide frontier. If you cannot name a query in your product that is 3+ hops, the numbers above are an argument *against* adopting one.

## 10. Trade-offs

**What you gain**

- **Constant per-hop cost** regardless of database size (§4.2), plus early termination and stable plans.
- **Query expressiveness.** `shortestPath`, `*1..6`, pattern matching. Four-line queries replacing 200-line SQL.
- **Properties on relationships** as a first-class, indexable, filterable thing.
- **Schema flexibility** — new relationship types without migrations.
- **A shipped algorithm library** — PageRank, Louvain, betweenness, node similarity.

**What you give up**

- **Sharding is genuinely, structurally hard.** This is the big one. A traversal crosses partition boundaries *at arbitrary, unpredictable points*. Within one machine a hop is 0.15 µs; across a network it is 200–500 µs — a **1,000–3,000× penalty per crossed edge**. Partitioning to minimise crossings is the **minimum-cut problem, which is NP-hard**, and worse, real graphs have power-law degree distributions, which means **no balanced low-crossing cut exists** — the supernodes connect everything to everything. The practical responses are all unsatisfying: (a) keep the graph small enough for one machine and scale reads with full replicas — 10 billion relationships × 34 bytes ≈ 340 GB, which does fit a single large server, and this is what most production deployments do; (b) **vertex-cut** partitioning (split high-degree vertices across machines, replicate their state) as in PowerGraph/JanusGraph; (c) shard by a domain key that traversals rarely cross, e.g. by country, accepting slow cross-country queries. Compare with Lesson 36's sharding of key-value stores, where every key is independent and the problem simply does not arise. **Sharding is why "just use a graph database for everything" fails.**
- **Analytical and aggregate queries are mediocre.** `SELECT AVG(price) FROM orders WHERE date > ...` over 14 M edges is a full scan with none of a columnar engine's advantages (Phase 3, Topic 13 — Columnar storage).
- **Operational weight.** A second datastore: backups, upgrades, monitoring, capacity planning, on-call knowledge, and a sync pipeline that can break silently.
- **Smaller ecosystem.** Fewer engineers know Cypher; fewer ORMs, migration tools, and hosted options than for PostgreSQL.
- **Memory-bound economics.** Performance depends on the working set fitting in RAM; RAM is the expensive resource.
- **Supernode pathology.** One node with 400,000 edges can turn a "cheap" two-hop query into a 400,000-edge scan. Mitigations (relationship-type filters, degree caps, pre-aggregated `SIMILAR_TO` edges) are manual.
- **Write amplification.** Creating one relationship updates the linked lists of both endpoints.

**The derived-store trade-off, stated explicitly.** Nearly every successful production graph deployment uses the graph as a **secondary, derived, read-mostly projection** of a relational system of record — exactly as in §7.1 and §7.2. You gain traversal power and you pay with **eventual consistency between the two stores** (Lesson 33) plus a sync pipeline. That is usually the right trade, but it must be a conscious one.

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| **Supernode traversal** — a query anchors on a bestseller with 400 k `PURCHASED` edges | Product page hangs, then times out at 30 s; *only on popular books*, so it looks random | p99 latency alert; Neo4j query log showing `db hits` in the hundreds of millions for one query | Add a relationship-type filter and a degree cap (`WITH c LIMIT 5000`); long-term, precompute `SIMILAR_TO` edges in a nightly job so the hot path is one hop |
| **Missing anchor index** — someone deploys a `MATCH (b:Book {title: $t})` with no index on `title` | Everything is 100× slower after a deploy, across all pages | `PROFILE` shows `NodeByLabelScan`; CPU pegged with low I/O | `CREATE INDEX`; add a CI check that fails the build if any production query plan contains `AllNodesScan` or `NodeByLabelScan` |
| **Unbounded variable-depth query** (`*` with no upper bound) | Request never returns; then the whole database becomes unresponsive for *all* users as heap fills | OOM / GC-thrash alerts; `dbms.memory.transaction.max` breaches | Kill the transaction; set per-transaction memory limits and a server-side query timeout; ban unbounded `*` in code review |
| **Sync pipeline lag** — CDC from PostgreSQL to the graph stalls | "Also bought" shows books the customer already purchased yesterday; new books never appear in recommendations | Freshness metric: `now() - max(edge.created_at)`; alert at > 30 min | Restart the consumer; replay from the last committed offset. The sync **must be idempotent** (`MERGE`, §8.1) so replay is safe |
| **Dual-write divergence** — the app writes to Postgres and the graph separately, and the second write fails | Silent, long-lived wrongness: a returned order still counts toward recommendations | Only by a periodic reconciliation job comparing counts per entity | Stop dual-writing. Use change data capture or the outbox pattern (**Phase 4, Topic 22 — Change data capture and the outbox pattern**). Reconcile and rebuild |
| **Single-instance failure** (no cluster) | Total outage of every graph-backed feature | Health check / connection failures | Degrade gracefully: the API should serve a static bestseller list when the graph is unreachable. Recover via causal-clustering failover (Raft, **Phase 3, Topic 19 — Consensus and quorums**) or restore from backup |
| **Working set exceeds RAM** | Gradual, creeping slowdown over weeks, with no deploy to blame | Page-cache hit ratio dropping below ~95 %; disk read IOPS climbing | Add RAM, or prune the graph (archive edges older than 24 months — recommendations rarely need 2015 purchases) |
| **Cold cache after restart** | First few minutes after any restart or failover are 50–100× slower | Latency spike correlated with process start | Warm the cache on startup by replaying a sample of common queries before accepting traffic |

## 12. Alternatives

| Option | Model | Best at | Worst at | Choose when |
|---|---|---|---|---|
| **Native graph DB** (Neo4j, Memgraph, TigerGraph) | Property graph, index-free adjacency | Deep (3+), variable-depth traversal; algorithms; path finding | Sharding; aggregates; operational simplicity | Traversal is a core product feature and depth ≥ 3 |
| **Graph layer over a distributed store** (JanusGraph, Amazon Neptune) | Property graph on Cassandra/HBase/managed storage | Horizontal scale, managed operations | Per-hop latency (adjacency is indexed, not pointer-based) | The graph genuinely exceeds one machine, or you want a managed service |
| **RDF triple store** (GraphDB, Stardog, Virtuoso) | Subject-predicate-object + ontology | Data integration across organisations, inference, standards | Properties on edges; write throughput | Knowledge graphs, life sciences, bibliographic/open data |
| **Relational + recursive CTE** | Tables + `WITH RECURSIVE` | Hierarchies, bounded traversal, transactional integrity, one datastore | Deep/wide traversal; readability; algorithms | Depth ≤ 3–4 **and** frontier ≤ ~10 k (§4.6) |
| **Relational + a closure/adjacency table** | Precomputed transitive closure | O(1) ancestor/descendant lookups on a tree | Write amplification: one insert updates O(depth) rows; explodes on non-tree graphs | Static, tree-shaped hierarchies read far more than written |
| **Document DB with embedded arrays** (Lesson 44) | Denormalised neighbour lists | One-hop "friends of X" | Two or more hops — you are now doing joins in application code | The relationship is strictly one-hop and read-only |
| **Precomputed edges in a key-value store** (Lesson 43) | `book:42:also_bought → [ids]` | Sub-millisecond reads at any scale, trivial sharding | Any query you did not precompute; staleness | The question is fixed and known in advance — **the most under-used option** |
| **Graph processing framework** (Spark GraphX, Pregel, NetworkX) | Batch, offline | PageRank/Louvain over billions of edges | Online queries — latency is minutes | Algorithms are needed but answers are not needed in real time |

The row to re-read is the key-value one. BookNook's "also bought" is a **fixed question**. A nightly Spark job over the order history, writing `book:{isbn}:also_bought` into Redis, gives a 0.4 ms p99 at any scale, shards trivially, and needs no new database. That is strictly better than an online graph query *for that one feature*. The graph earns its place only when the questions are **ad hoc, variable-depth, or path-shaped** — which is why BookNook's justification is the author network and exploratory analysis, not the recommendation widget.

## 13. When to use it

1. **Deep, variable-depth traversal is a product feature.** Fraud rings over shared attributes, "how are these two people connected", dependency/impact analysis in a CI system, lineage tracking across data pipelines. Concretely: **depth ≥ 3 with a frontier over ~10,000** (§4.6, §9).
2. **Path finding with semantics.** Shortest path, all-paths-under-cost, routing with weights and constraints. There is no acceptable SQL formulation; writing one by hand is reimplementing Dijkstra in a query language.
3. **Graph algorithms as a product input.** PageRank for influence ranking, Louvain for behavioural segmentation, betweenness for broker detection. You want these as library calls, not as research projects.
4. **Highly interconnected, irregular, evolving domains.** Identity and access management ("can this user reach this resource through any role-group-permission path?"), network/IT topology and blast-radius analysis, knowledge graphs, supply-chain dependency mapping. These share one trait: **the relationship types themselves keep changing**, which makes relational schema migration a constant tax.
5. **Exploratory analysis over connections.** Analysts asking questions nobody anticipated. The value here is iteration speed, not milliseconds.
6. **Relationships carry rich, queryable attributes** — strength, timestamp, confidence, cost — and you filter traversals on them.

## 14. When not to use it

1. **Your traversals are 1–2 hops.** By §9 the saving is under 1.5 ms. Use the joins you already have. This disqualifies the majority of "we need a graph database" proposals.
2. **Your hierarchy is small and shallow** — category trees, org charts, folder structures, bills of materials under ~10 levels with a narrow frontier. A recursive CTE (§8.4) is sufficient and keeps you on one datastore. BookNook's category tree, §7.1.
3. **The question is fixed and known in advance.** Precompute it into a key-value store (§12). Cheaper, faster, trivially shardable.
4. **Your workload is aggregation and reporting.** "Revenue by category by month" is a columnar/OLAP problem (Phase 3, Topic 12 — Data warehouses and OLAP), and a graph database is among the worst tools for it.
5. **You need a single, horizontally sharded, write-heavy system of record.** Graphs shard badly (§10). Keep the ledger relational.
6. **The data is not actually connected.** Logs, metrics, events, catalogue rows, user profiles. Adding edges nobody traverses is pure overhead.
7. **You cannot staff or operate a second datastore.** A graph database that nobody can tune, back up, or restore at 3 a.m. is a liability that exceeds the 10× it buys.

## 15. Common mistakes

### Beginner mistakes

- **Treating a graph database as a general-purpose replacement for the relational one.** Moving BookNook's orders, inventory and payments into Neo4j loses real transactional guarantees and gains nothing — those are key-lookup and aggregate workloads.
- **Forgetting the anchor index.** The single most common cause of "Neo4j is slow". Traversal needs no index; *finding where to start* absolutely does.
- **Unbounded variable-depth patterns.** `-[:FRIEND*]-` on a 14 M-edge graph is a memory exhaustion bug with a syntax. Always write `*1..4`.
- **String-concatenating user input into Cypher.** Cypher injection is as real as SQL injection, and it also destroys the plan cache. Use parameters.
- **Modelling attributes as nodes.** Making `(:Year {value: 1965})` a node creates an instant supernode joined to every book from 1965. Years are properties. Rule of thumb: **if you will never traverse *through* it, it is a property, not a node.**
- **Ignoring relationship direction and type in the pattern.** `(a)--(b)` walks every edge of every type in both directions. `(a)-[:PURCHASED]->(b)` may read 100× fewer records.
- **Believing "index-free adjacency" means "no indexes needed".** It means no index is needed *during traversal*.
- **Using `UNION` instead of `UNION ALL` in a recursive CTE**, or omitting cycle detection, and then blaming SQL for being slow.

### Experienced-engineer mistakes

- **Dual-writing from the application to Postgres and the graph.** It looks fine in testing and diverges silently in production forever (§11). The correct answer is change data capture or the outbox pattern (**Phase 4, Topic 22**) — never two writes in one request handler.
- **Planning to shard the graph "later".** There is no later. Re-read §10: minimum cut is NP-hard and power-law degree means no good cut exists. Decide at design time whether the graph fits one machine for its projected lifetime (340 GB ≈ 10 B relationships is a useful anchor).
- **Not budgeting for supernodes.** Capacity models built on *average* degree are wrong in exactly the places that matter: bestsellers, celebrity accounts, the "unknown" category node. Model the p99.9 degree.
- **Online traversal where a nightly batch would do.** If the answer may be 24 hours stale, compute it offline and serve one hop from cache. Many "we need sub-100 ms graph queries" requirements dissolve under this question.
- **Letting the graph become a second system of record.** Once someone writes data *only* to the graph, you have two sources of truth and no reconciliation path.
- **Benchmarking warm against cold.** A benchmark with a 100 % page-cache hit rate proves nothing about production at 3× the data size.
- **Over-indexing.** Every index slows writes and consumes RAM that the page cache needs. Index anchors only.
- **Ignoring the plan cache by not parameterising**, then wondering why p50 doubled when traffic diversified.
- **Choosing a graph database for a 2-hop recommendation feature because it is interesting**, and inheriting a decade of operational cost for 1.3 ms.

## 16. Interview perspective

Graph databases appear in system design interviews in three recognisable shapes: a social network ("friends of friends", "people you may know"), a recommendation engine, and a fraud-detection system. There is also a frequent *trap* variant: a problem that **sounds** graph-shaped but is not.

**What a strong answer contains:**

- **Index-free adjacency, explained at the storage level.** Fixed-size records, offset arithmetic, relationship linked lists, pointer dereference instead of B-tree descent. Then the key comparison: **O(1) per hop versus O(log N) per hop**, plus the honest caveat that the *frontier* still grows exponentially in both systems.
- **A quantified threshold, not a vibe.** "At 1–2 hops I would use joins — it is about 1 ms of difference. At 4 hops with a degree of 30 the frontier is 810,000 edges; Postgres is around a second and flips to a hash join, a graph traversal is around 100 ms and can terminate early. That is where I would introduce one."
- **Correct architecture: the graph as a derived store.** System of record stays relational; the graph is fed by CDC; the app degrades gracefully when the graph is down. This single point separates senior answers from mid-level ones.
- **Naming the sharding problem unprompted.** "Graphs are hard to shard because traversals cross partitions arbitrarily and minimum-cut is NP-hard on power-law graphs. My plan is to keep it on one machine with read replicas, and here is the size budget that makes that safe for three years."
- **Supernodes.** Mentioning them signals you have run one of these in anger.
- **Caching and precomputation.** "Recommendations tolerate 6 hours of staleness, so I would cache aggressively and precompute `SIMILAR_TO` nightly." Interviewers want to see you *avoid* the expensive query, not just optimise it.
- **Knowing when to say no.** Being asked for a category tree and answering "recursive CTE in the database we already have" is a strong answer, not a weak one.

**Red flags interviewers watch for:**

- Saying "graph databases are faster" without saying *at what* and *by how much*.
- Claiming graph traversal is O(1) overall, rather than O(1) *per hop with the frontier still exponential*. This is the single most common technical error on this topic.
- Proposing a graph database for a 1-hop lookup, or for aggregations and reporting.
- Not knowing what an index is for in a graph database (anchors only).
- Proposing dual writes from the application to keep two stores in sync.
- Hand-waving sharding — "we'll just shard it" — or claiming graph databases scale horizontally like key-value stores.
- Putting money, orders or inventory in the graph as the system of record.
- Never mentioning supernodes, unbounded depth, or what happens when the working set exceeds RAM.
- Treating Neo4j, Neptune and an RDF store as interchangeable.
- Being unable to write even an approximate Cypher pattern after claiming graph experience.

## 17. Knowledge check

**Conceptual**

1. Explain index-free adjacency at the storage level, then state precisely which part of a multi-hop query *does* still use an index and why that part cannot be eliminated.
2. A colleague says "graph traversal is O(1), so a graph database makes any depth of query cheap." Identify exactly what is right and what is wrong in that statement, and describe what actually grows as hop count increases.
3. Why are graph databases structurally harder to shard than key-value stores? Name the specific computational problem involved and the specific property of real-world graphs that makes a good partition unavailable, then give two practical responses used in production.

**Practical scenario**

4. BookNook wants a "readers like you also enjoyed" shelf. The product spec allows the data to be up to 24 hours old, the shelf is shown on 400,000 page views per day, and the underlying traversal is 3 hops with an average degree of 25. You already run PostgreSQL and Redis; you do not run a graph database. Decide whether to introduce one. Justify your answer with the traversal cost, the staleness allowance, the alternative architectures from §12, and the operational cost of a second datastore — and state what would have to change about the requirements to reverse your decision.

**Calculation**

5. A fraud graph has 8,000,000 accounts with an average degree of 18. A synchronous authorisation check must traverse 3 hops within a 50 ms budget. Using the per-edge costs from §9 (1.5 µs relational, 0.15 µs graph): compute the cumulative edges traversed, the time for each approach, and whether each meets the budget. Then compute the maximum frontier cap you would have to impose on the *relational* implementation to bring it inside 50 ms, and state what that cap costs you in answer quality.

**Progress checklist**

- **Phase 1 — Foundations (Lessons 1–17): complete**
- **Phase 2 — Scaling (Lessons 18–39): complete**
- **Phase 3 — Databases and Storage:**
  - [x] 1. Relational databases (Lesson 40)
  - [x] 2. Tables, keys and joins (Lesson 41)
  - [x] 3. SQL versus NoSQL (Lesson 42)
  - [x] 4. Key-value databases (Lesson 43)
  - [x] 5. Document databases (Lesson 44)
  - [x] 6. Wide-column databases (Lesson 45)
  - [x] 7. **Graph databases (Lesson 46) ← you are here**
  - [ ] 8. Search engines and full-text indexes
  - [ ] 9. Selecting a database from access patterns
  - [ ] 10. Time-series databases
  - [ ] 11. Object storage and blob stores
  - [ ] 12. Data warehouses and OLAP
  - [ ] 13. Columnar storage and analytics formats
  - [ ] 14. B-tree versus LSM-tree storage engines
  - [ ] 15. Transactions and ACID
  - [ ] 16. Isolation levels and concurrency anomalies
  - [ ] 17. Distributed transactions and two-phase commit
  - [ ] 18. CAP and PACELC
  - [ ] 19. Consensus and quorums
  - [ ] 20. Eventual consistency and conflict resolution
  - [ ] 21. Connection pooling and database proxies
  - [ ] 22. Schema migration and evolution
  - [ ] 23. Backup, restore and point-in-time recovery
  - [ ] 24. Data modelling case study — BookNook end to end

**NEXT LESSON: Phase 3, Topic 8 — Search engines and full-text indexes**

---

# Lesson 47 — Search Engines and Full-Text Indexes

*Phase 3, Topic 8*

## 1. Simple definition

A **search engine** is a database whose index is built the wrong way round on purpose.

A B-tree index (Lesson 40) maps **a row to its values**: given `isbn`, find the book. An **inverted index** maps **a value to its rows**: given the word `quantum`, find every book that contains it. "Inverted" is relative to that normal document-to-terms direction.

**Full-text search** is the ability to answer, quickly and in ranked order: *"which documents are about this phrase?"* Note the two halves — **finding** matches and **ranking** them. A relational database is bad at the first and has no concept at all of the second.

Terms defined before use:

- **Document**: the unit of search. For BookNook, one book — title, author, blurb, categories, price, rating, stock, flattened into one searchable record.
- **Term** (or *token*): one indexed unit of text, after processing. Usually a word, sometimes a word fragment.
- **Analysis**: the pipeline that turns raw text into terms (§4.3). The single most important concept in this lesson after the inverted index itself.
- **Posting**: one entry saying "term T occurs in document D", usually with the frequency and the positions.
- **Postings list**: all postings for one term, sorted by document ID.
- **Dictionary** (or *term dictionary*): the sorted set of all distinct terms, each pointing at its postings list.
- **Relevance score**: a number estimating how well a document answers a query. Higher is better. Entirely heuristic — there is no ground truth.
- **Index** (in search): the whole structure — dictionary plus postings plus stored fields. Note this word means something different here than in Lesson 40.

## 2. Why it exists

BookNook's first search implementation was one line:

```sql
SELECT * FROM books WHERE description LIKE '%quantum physics%';
```

This is wrong at scale for two independent reasons, and both need to be understood precisely rather than as folklore.

### 2.1 Why `LIKE '%term%'` cannot use a B-tree index

A B-tree stores keys **sorted left-to-right**. Search works by comparing prefixes: to find `'quantum%'`, descend to the first key ≥ `quantum`, then scan right until the prefix stops matching. The whole structure depends on knowing **where the string starts**.

A leading wildcard destroys that. `'%quantum%'` means "the substring `quantum` appears at *some unknown offset*". The sort order of the column gives zero information about which rows satisfy it: `'A book about quantum'` sorts under `A`, `'Quantum mechanics'` under `Q`, `'Modern quantum theory'` under `M`. They are scattered across the entire tree. The only correct plan is to **read every row and test every one** — a **sequential scan** with a per-row substring match.

So the cost is `O(rows × average_text_length)`:

| Books | Avg description | Bytes scanned | Approx time @ 1 GB/s scan + matching |
|---|---|---|---|
| 1,000 | 1.2 KB | 1.2 MB | ~3 ms — fine, ship it |
| 50,000 | 1.2 KB | 60 MB | ~150 ms — users notice |
| 5,000,000 | 1.2 KB | 6 GB | ~15 s — the feature is dead |

And it gets worse under concurrency: a sequential scan evicts the whole buffer pool, so every *other* query on that database slows down too. One search query degrades the entire OLTP workload.

(A trailing-wildcard `LIKE 'quantum%'` *can* use a B-tree, which is why autocomplete-by-prefix is sometimes feasible in SQL. Only the **leading** wildcard is fatal. And a `text_pattern_ops` index is required in PostgreSQL for non-C collations.)

### 2.2 Why substring matching is the wrong *semantics*, even if it were fast

Suppose scanning were free. `LIKE` still fails as a product feature:

| Query | `LIKE '%…%'` behaviour | What a user expects |
|---|---|---|
| `running shoes` | Matches only the exact adjacent string | Should match "shoes for running", "shoe running" |
| `Quantum` | Misses `quantum` unless you add `ILIKE`/`LOWER()` (which breaks the index further) | Case-insensitive |
| `physics` | Misses "physical", "physicist" | Word-family matching |
| `cat` | Matches "**cat**alogue", "edu**cat**ion", "**cat**astrophe" | Whole words only |
| `color` | Misses "colour" | Synonyms/locale |
| `quantom` (typo) | Zero results | Did-you-mean / fuzzy |
| any query | **Returns rows in arbitrary order** | Best match first |

That last row is the one that matters most commercially. **Relevance ranking is not an optimisation of `LIKE`; it is a capability `LIKE` does not have.** A search returning 4,000 unordered matches is as useless as one returning none.

So search engines exist to provide: **sub-linear matching via an inverted index (§4.2), linguistic normalisation via analysis (§4.3), and ranked ordering via a scoring function (§4.4).** Those three, together.

## 3. Real-world analogy

**The index at the back of a textbook.**

To find every mention of "entropy" in a 900-page textbook you have two options.

Option one: start at page 1 and read all 900 pages. Guaranteed correct, unbearably slow. That is `LIKE '%entropy%'` — a full scan.

Option two: turn to the back, where someone has pre-built an alphabetical list:

```
entropy  ..................  14, 88, 102-107, 340
enzyme   ..................  455
```

One lookup in a sorted list, then you have every page number instantly. That list **is** an inverted index: terms → document (page) IDs. Building it was expensive and happened once; using it is nearly free and happens thousands of times.

Three further details of the analogy map precisely:

- The indexer **chose which words to index.** "the", "and", "is" are not in the back of the book — they would list every page and carry no information. Those are **stop words** (§4.3).
- The index **normalises.** "Entropies", "entropy" and "Entropy" share one entry. That is **stemming** and **lowercasing**.
- A good index has **cross-references**: "disorder, *see* entropy". Those are **synonyms**.

**Where the analogy breaks down:**

| Textbook index | Search engine |
|---|---|
| Entries are unranked page numbers in numeric order | Results are **ranked by relevance** (BM25, §4.4) — the ordering *is* the product |
| Built once, by a human, before printing | Rebuilt continuously as documents change; freshness is an ongoing engineering problem (§4.6, §4.9) |
| The book never changes | BookNook adds books and changes prices constantly; the index must be kept in sync with a separate source of truth (§4.9) — the hardest operational problem in this lesson |
| Lists only page numbers | Stores term frequency, positions (for phrase queries), field lengths (for normalisation), and often the whole document |
| One index, one book | Indices are split into **shards** and copied into **replicas** across machines (§4.6) |
| You look up one word | Real queries combine many terms, with boolean logic, filters, boosts and fuzziness, scored together |
| Finding the entry ends the work | Finding candidates is the *cheap* half; scoring, sorting and aggregating thousands of candidates is often the expensive half |
| No concept of "about" | Vector search (§4.11) can match documents that share **no words at all** with the query |

## 4. Detailed technical explanation

### 4.1 The document model

A search engine is **not** normalised (contrast Lesson 41). Joins at query time are impossible or very limited, so you **denormalise everything a query needs into one flat document**:

```json
{
  "isbn": "9780441013593",
  "title": "Dune",
  "author": "Frank Herbert",
  "description": "A desert planet, a noble family, and a prophecy...",
  "categories": ["Science Fiction", "Classics"],
  "price": 8.99,
  "rating": 4.6,
  "in_stock": true,
  "published": "1965-08-01"
}
```

This is the same denormalisation trade-off as Lesson 44's document databases, for the same reason and with the same consequence: when an author's name changes, every document that embeds it must be reindexed.

### 4.2 Building an inverted index by hand

Three real BookNook descriptions, shortened:

```
D1: "Quantum Physics for Beginners"
D2: "The Quantum Universe: a physicist's guide"
D3: "Cooking for Beginners: simple recipes"
```

**Step 1 — tokenise** (split on non-letters, drop punctuation and possessives):

```
D1: [quantum] [physics] [for] [beginners]
D2: [the] [quantum] [universe] [a] [physicist] [guide]
D3: [cooking] [for] [beginners] [simple] [recipes]
```

**Step 2 — lowercase:** already lowercase except `Quantum`/`The` → `quantum`, `the`.

**Step 3 — remove stop words** (`the`, `a`, `for`):

```
D1: [quantum] [physics] [beginners]
D2: [quantum] [universe] [physicist] [guide]
D3: [cooking] [beginners] [simple] [recipes]
```

**Step 4 — stem** (Porter algorithm):

```
D1: [quantum] [physic] [beginn]
D2: [quantum] [univers] [physicist] [guid]
D3: [cook] [beginn] [simpl] [recip]
```

Note carefully: `physics → physic` but **`physicist → physicist`**. The Porter stemmer strips `-s`, `-ic`, `-ance`, `-ment` and many others, but not `-ist`. So a search for *physics* will **not** match D2, even though a human obviously would. This is not a contrived example — it is the normal condition of stemmers, and it is precisely why production systems add a **synonym** rule (`physics, physicist, physical`) on top of stemming. **Stemming is a crude heuristic that you then patch with explicit rules.**

**Step 5 — invert**, recording document ID, term frequency, and positions (positions are what make phrase queries possible):

| Term | df | Postings list `doc(freq)@[positions]` |
|---|---|---|
| beginn | 2 | D1(1)@[3], D3(1)@[2] |
| cook | 1 | D3(1)@[1] |
| guid | 1 | D2(1)@[6] |
| physic | 1 | D1(1)@[2] |
| physicist | 1 | D2(1)@[5] |
| quantum | 2 | D1(1)@[1], D2(1)@[2] |
| recip | 1 | D3(1)@[4] |
| simpl | 1 | D3(1)@[3] |
| univers | 1 | D2(1)@[3] |

**Step 6 — query it.** Search `Quantum Physics`. The query text goes through **the same analyser** (this is non-negotiable — §15) producing `[quantum, physic]`.

- `quantum` → {D1, D2}
- `physic` → {D1}
- OR (default): candidate set {D1, D2}; AND: {D1}.
- Merging two sorted integer lists is a linear walk with **skip pointers**, so the cost is proportional to the postings touched, **not** to the 50,000 documents in the collection. That is the sub-linearity.
- Score each candidate (§4.4) and return in order: **D1 first** (matches both terms), D2 second.

Two structural facts worth internalising. First, **postings lists are sorted by doc ID**, which is what makes intersection cheap and compression effective. Second, deltas between consecutive doc IDs are small, so postings compress extremely well (variable-byte, Frame-of-Reference, Roaring bitmaps) — typically 4–6 bytes per posting including positions (§9).

**Phrase queries** use positions: searching `"quantum physics"` as a phrase requires a document where `physic` occurs at position `pos(quantum) + 1`. In D1, quantum@1 and physic@2 → match. Positions roughly double index size; `index_options: docs` turns them off when you never need phrases.

### 4.3 The analysis pipeline, stage by stage

Analysis runs at **index time** and again at **query time**, and the two must agree.

1. **Character filters** — operate on the raw character stream before tokenisation. Strip HTML (`<b>Dune</b>` → `Dune`), map characters (`&` → `and`), normalise Unicode (NFKC), fold accents (`Pokémon` → `Pokemon`).
2. **Tokeniser** — splits the stream into tokens. `standard` (Unicode word boundaries) covers most Western text. `whitespace`, `keyword` (no split — for IDs and exact-match fields), `pattern` (regex), and language-specific ones. **Chinese, Japanese and Thai have no spaces**, so they need dictionary-based segmentation (ICU, Kuromoji, SudachiPy); using `standard` on Japanese produces garbage.
3. **Lowercasing** — the cheapest normalisation and almost always correct for search. (Keep a separate `keyword` sub-field if you need the original for sorting or exact match.)
4. **Stop words** — remove extremely common, low-information terms (`the`, `a`, `of`, `and`). Historically this saved index space: in English the top 10 words are ~25 % of all tokens. **Modern practice is to keep them**, because BM25 already assigns them near-zero weight via IDF, and removing them breaks real queries: *"To Be or Not to Be"*, *"The Who"*, *"Let It Be"* become empty queries. Remove stop words only when you have measured the space saving and confirmed no such titles matter.
5. **Stemming versus lemmatisation** — two different techniques, routinely confused:
   - **Stemming** chops suffixes with rules. Fast (microseconds), dictionary-free, language-specific, and *approximate*. `running → run`, but also `university → univers` and `universe → univers` — a **false conflation**, and `physicist → physicist` — a **false separation**. Porter, Snowball, KStem (less aggressive).
   - **Lemmatisation** uses a dictionary plus part-of-speech analysis to find the true base form. `better → good`, `ran → run`, `mice → mouse`. More accurate, 10–100× slower, needs a language model, and must disambiguate (`saw` → `see` or `saw`?).
   - Search engines default to **stemming** because it is fast, operates identically at index and query time, and its errors are usually symmetric (a false conflation affects query and document equally). Use lemmatisation where precision matters more than throughput — legal, medical, patents.
6. **Synonyms** — explicit term expansion: `laptop, notebook`; `sf, sci-fi, science fiction`. Can be applied at **index time** (expands the index, cannot be changed without reindexing, faster queries) or **query time** (changes instantly, slightly slower, and interacts badly with IDF because expanded terms carry their own document frequencies). **Query-time is the usual choice** — synonym lists change weekly and reindexing 50,000 documents to add one rule is absurd.
7. **N-grams, for autocomplete and fuzzy matching** — two distinct kinds:
   - **Edge n-grams** index every prefix: `dune → d, du, dun, dune`. Searching `du` hits a term directly, so prefix autocomplete is a normal term lookup. Cost: index size multiplies by roughly the average term length (3–8×).
   - **Sliding n-grams** index every substring of length *n*: `dune` with n=3 → `dun, une`. Enables mid-word and typo-tolerant matching, at a much larger size cost.
   - The alternatives are a **completion suggester** (an in-memory finite state transducer, very fast, limited flexibility) and `search_as_you_type` fields.

**The cardinal rule:** index-time and query-time analysis must be *compatible*. Index with stemming and query without, and `physics` will never match the indexed term `physic`. Zero results, no error, no warning. Engines let you set a separate `search_analyzer` precisely so you can make them deliberately different (e.g. edge n-grams at index time, plain tokens at query time) — but that must be a decision, not an accident.

### 4.4 Relevance scoring — TF-IDF, then BM25

**TF-IDF** combines two intuitions.

- **Term frequency (TF)**: a document mentioning `quantum` 8 times is probably more about quantum things than one mentioning it once.
- **Inverse document frequency (IDF)**: a term appearing in 48,000 of 50,000 documents (`book`) tells you nothing; a term in 40 documents (`entropy`) is highly discriminating.

```
tf-idf(t, D) = tf(t, D) × log(N / df(t))
```

where `tf` is the count of term *t* in document *D*, `N` is the total documents, and `df(t)` is the number of documents containing *t*.

TF-IDF has two real defects:
1. **TF grows without bound.** 100 occurrences scores 100× a single occurrence, but the 100th mention adds almost no new information. Relevance should **saturate**.
2. **No length normalisation.** A 5,000-word document mentions everything more often and unfairly outranks a focused 200-word one.

**BM25** ("Best Match 25", from the Okapi project) fixes both, and is the default in Lucene, Elasticsearch, OpenSearch and Solr.

```
                              f(t,D) · (k1 + 1)
score(D,Q) = Σ   IDF(t) · ─────────────────────────────────
            t∈Q            f(t,D) + k1 · (1 − b + b · |D|/avgdl)

IDF(t) = ln( 1 + (N − df(t) + 0.5) / (df(t) + 0.5) )
```

- `f(t,D)` — frequency of term *t* in *D*; `|D|` — length of *D* in tokens; `avgdl` — average document length.
- **`k1` (default 1.2)** controls TF **saturation**. As `f` grows, the fraction approaches `k1 + 1 = 2.2` and stops. `k1 = 0` ignores frequency entirely; large `k1` behaves like raw TF.
- **`b` (default 0.75)** controls **length normalisation**. `b = 0` disables it; `b = 1` fully normalises. 0.75 is a deliberate partial correction — long documents are penalised, but not as if length carried no information.
- The `+0.5` terms are smoothing, and the `1 +` inside the log keeps IDF non-negative for terms present in nearly every document.

A worked scoring example is in §9.

**Crucially: scores are not comparable across queries.** A score of 14.5 for one query and 3.2 for another says nothing about which result is better. Never threshold on an absolute score, never show it as a percentage, never persist it. It is an *ordering* device within one result set only.

### 4.5 Elasticsearch and OpenSearch: the concrete system

Both are distributed search engines built on **Apache Lucene** (the actual index library — Lucene does the inverted index, analysis and BM25; Elasticsearch/OpenSearch add distribution, an HTTP API and a query language). OpenSearch is a fork of Elasticsearch 7.10 created after a licence change; for everything in this lesson they are near-identical.

**Index, shard, replica, segment.**

- An **index** is a named collection of documents (`booknook-books-v3`).
- It is split into **primary shards**. **A shard is a complete, independent Lucene index.** It is the unit of parallelism and distribution.
- **The number of primary shards is fixed at creation.** Changing it requires reindexing or a split/shrink operation. This is the most consequential irreversible decision you make.
- Each primary has zero or more **replica shards** — exact copies on other nodes, serving reads and providing failover. Replica count *is* changeable at any time.
- A shard is physically a set of immutable **segments**. New documents go to a new segment. Updates are **delete-and-reinsert**: the old document is marked deleted in a tombstone bitmap and a new one is written. Background **merges** combine small segments into large ones and physically remove deletions. This is the same immutable-plus-compaction design as an LSM tree (Phase 3, Topic 14 — B-tree versus LSM-tree storage engines).

**Shard sizing, concretely.** Target **10–50 GB per shard**. Each shard carries fixed overhead (file handles, memory, a merge thread, cluster-state entries), so thousands of tiny shards is a classic self-inflicted outage — "oversharding". BookNook's 50,000 books (§9) are about 60 MB: **one primary shard, one replica. Not five.** The default of one primary in modern versions exists because so many teams got this wrong.

**Mapping.** The schema: which fields exist, their types, and their analysers. The two text types are constantly confused:

- **`text`** — analysed into terms, used for full-text search, **not** sortable or aggregatable by default.
- **`keyword`** — stored verbatim as a single term; used for exact match, sorting, aggregations and filters.

`"Science Fiction"` as `text` becomes terms `[scienc, fiction]`; as `keyword` it stays `"Science Fiction"`. For faceting on category you need `keyword`; for searching inside it you need `text`. The standard answer is a **multi-field**: index it both ways (§8.2).

**Dynamic mapping** infers types from the first document it sees. Convenient in development, dangerous in production: one document with `"price": "8.99"` as a string can lock the field to `text` forever, after which range queries fail. **Define explicit mappings.**

**Query DSL, and the critical `query` versus `filter` distinction.**

- **Query context** asks *"how well does this match?"* — it computes a BM25 score. Used for `match`, `multi_match`, `match_phrase`.
- **Filter context** asks *"does this match, yes or no?"* — no scoring at all. Used for `term`, `terms`, `range`, `exists`, and anything inside `bool.filter` or `bool.must_not`.

Filters are dramatically cheaper for two reasons:
1. **No scoring work.** No IDF lookup, no BM25 arithmetic, no float comparisons — just set membership.
2. **They are cacheable.** Because a filter's answer is a pure yes/no over a set of documents, the engine caches the resulting **bitset** (one bit per document) in the node query cache. A query like `in_stock: true` over 50,000 documents is a 6.25 KB bitmap; after the first execution, applying it is a bitwise AND taking microseconds. A *scored* clause cannot be cached this way, because its contribution depends on the rest of the query.

Lucene only caches filters that it sees repeated (it tracks frequency and segment size before caching), which is why filters work best when they are **low-cardinality and reused**: `in_stock`, `language`, `category`. A filter on `user_id` with 1 M distinct values will never usefully cache.

**Practical rule: everything that is a hard constraint goes in `filter`; only the user's actual words go in `must`.** Price ranges, stock, language, category selections — all filters. On a typical BookNook search this is a 3–10× latency reduction (§9).

**Aggregations** compute analytics over the matched set: counts per category, price histograms, average rating, date ranges, nested combinations. They read **doc values** — a column-oriented, on-disk, per-field structure (the inverted index read forward, essentially) rather than the inverted index itself. This is why `text` fields cannot be aggregated without enabling `fielddata`, which is an in-memory structure and a well-known cause of out-of-memory failures.

### 4.6 Near-real-time search and the refresh interval

Elasticsearch is **near-real-time**, not real-time, and the mechanism matters.

A newly indexed document is first written to an in-memory buffer and to the **translog** (a write-ahead log, for durability). It is **not searchable yet** — searches read segments, and no segment exists for it. A **refresh** turns the in-memory buffer into a new searchable segment.

- **Default `refresh_interval`: 1 second.** So "write, then immediately search" can miss the document for up to ~1 s.
- Refreshing is not free: each refresh creates a segment, and more segments means more merge work and slower queries.
- **Tuning:** for bulk loading, set `refresh_interval: -1` (disabled), load, then refresh once — commonly a **3–10× indexing throughput improvement**. For a catalogue like BookNook's, `30s` is perfectly acceptable and reduces merge pressure. Only use `?refresh=wait_for` on writes where a human will immediately re-read their own change.
- **Durability** is separate from visibility: the translog is `fsync`ed every 5 s by default (`index.translog.durability: request` makes it per-request, which is slower but loses nothing on a crash).

You must design around this. A UI that creates a book and then immediately lists books from the search index will show a missing row. The correct patterns are: read the newly created entity from the primary database rather than the index, or use `wait_for` on that specific write, or optimistically render it client-side.

### 4.7 Faceted search

**Facets** are the filter counts down the side of a results page:

```
Category          Price            Rating
 Sci-Fi    (412)   Under £10 (233)  ★★★★+ (180)
 Fantasy   (287)   £10–£20   (390)  ★★★+  (402)
 Classics  (96)    Over £20  (172)
```

Mechanically these are **aggregations computed over the current result set**, returned in the same round trip as the hits. Two subtleties that people get wrong:

- **Counts must reflect the other active filters but usually *not* the filter they belong to.** If the user has selected Sci-Fi, the category facet should still show Fantasy's count (so they can switch), while the price facet *should* be restricted to Sci-Fi. This requires **post-filters** or separate filtered aggregation buckets — not simply aggregating the final result set.
- **Cardinality is the cost driver.** Faceting on 12 categories is free. Faceting on 180,000 authors requires building global ordinals across segments, which is expensive on the first query after a refresh and is a common source of mysterious latency spikes.

### 4.8 Relevance tuning with boosting

Default BM25 treats all fields equally. That is wrong for a bookstore: a match in the **title** means far more than a match buried in a 400-word blurb.

- **Field boosting**: `title^5, author^3, description^1`. Multiplies that field's contribution. Start at title ≈ 3–5× description and tune against click data.
- **Query clause boosting**: a `bool.should` clause that adds score without being required — e.g. boost exact-phrase matches above term matches.
- **Function score / `rank_feature`**: multiply or add based on a numeric field — popularity, sales rank, recency. The classic BookNook rule: *relevance × log(1 + sales_rank)*, so that among equally relevant books, the one people actually buy ranks first.
- **Negative boosting**: push down out-of-stock items without removing them.

The discipline point: **relevance tuning without measurement is superstition.** You need click-through rate, zero-result rate, and ideally a human-judged set of query/result pairs (NDCG). Change one parameter at a time and A/B test. Every search team has a story about a boost added in 2021 that nobody can remove because nobody knows what it does.

### 4.9 The dual-write problem — keeping the index in sync

This is the hardest *operational* problem in the lesson, and the one most often skipped.

The primary database (PostgreSQL) is the system of record. The search index is a **derived copy**. Something must propagate changes. The obvious implementation is the wrong one:

```
// WRONG — the defining anti-pattern of this lesson
db.save(book);               // succeeds
searchClient.index(book);    // network blip -> throws
```

Now Postgres and the index disagree, permanently and silently. The failure modes are all bad:

- Second write fails → index is stale forever. Nothing detects it.
- Wrap both in a transaction → impossible; Elasticsearch is not a transaction participant.
- Retry in a `catch` block → the process can crash between the two writes.
- Two concurrent updates → they can reach the index **out of order**, leaving the older version. (Mitigate with external versioning: `?version=N&version_type=external`, which makes the engine reject an older version.)
- Elasticsearch is down for 10 minutes → every write during that window is lost from the index.

**The correct solutions, in order of preference:**

1. **Change data capture (CDC).** Read the database's own replication log (the PostgreSQL WAL via Debezium or `pgoutput`, MySQL's binlog) and stream changes to the indexer. Nothing changes in application code; the log is the single source of truth; ordering is guaranteed per row; the consumer can replay from an offset after an outage. This is the best answer for an existing system.
2. **The transactional outbox pattern.** In the *same database transaction* as the business write, insert a row into an `outbox` table. A separate relay process reads the outbox and publishes to the indexer, marking rows done. Because the business write and the outbox write are one atomic transaction, they cannot diverge. Delivery is **at-least-once**, so the indexer must be **idempotent** — which it naturally is, since indexing a document by its ID is an upsert.
3. **Periodic full reindex into a new index plus an alias swap.** Not a substitute for 1 or 2, but a mandatory safety net: run it weekly, compare counts, and have it ready as the recovery path.

Both CDC and the outbox pattern are covered properly in **Phase 4, Topic 22 — Change data capture and the outbox pattern**; the queue that carries the events is **Phase 4, Topic 5 — Message queues**.

Two operational rules that follow:

- **Always index through an alias**, never a concrete index name. `booknook-books` is an alias pointing at `booknook-books-v3`. To change a mapping you build `v4` in the background, then atomically repoint the alias. **Zero-downtime reindex.** Without this, every mapping change is an outage.
- **Monitor freshness as a first-class metric**: `now() − max(indexed_at)`. Alert at 5 minutes. A stale index does not throw errors; it quietly serves wrong results.

### 4.10 PostgreSQL full-text search — when it is genuinely sufficient

PostgreSQL has real full-text search built in, and it is good. It is not a toy.

- **`tsvector`** — a column type holding the analysed, sorted, positional term list of a document. This is a per-row inverted index.
- **`tsquery`** — an analysed query, supporting `&`, `|`, `!`, `<->` (phrase/followed-by).
- **GIN index** (Generalized Inverted Index) over the `tsvector` column — a genuine inverted index inside Postgres. (GiST is the alternative: smaller and faster to update, but lossy and slower to query. **Use GIN for search.**)
- **`ts_rank` / `ts_rank_cd`** — relevance ranking. Note: these implement a TF-IDF-ish weighting with field weights A/B/C/D, **not BM25**, and `ts_rank` must recompute per matching row rather than using cached statistics, so ranking large result sets is slow.
- **`pg_trgm`** — trigram index for fuzzy matching and, usefully, for making `LIKE '%x%'` indexable.
- **Generated columns** keep the `tsvector` automatically in sync with the source text — **and this is the decisive advantage: there is no dual-write problem at all.** The index is updated inside the same transaction as the data.

**Postgres FTS is genuinely sufficient when all of these hold:**

- Corpus under roughly **1–5 million documents** (and under ~10 GB of indexed text).
- Search traffic under roughly **50–100 queries/second**.
- Ranking requirements are modest — TF-IDF-ish ordering with field weights is good enough; you are not running a relevance tuning programme.
- You do not need faceted aggregations over large result sets, "did you mean", highlighting across many fields, or per-user personalised ranking.
- Latency budget is **tens of milliseconds**, not single digits.
- Search is a *feature* of your product, not the *core* of it.

**Move to Elasticsearch/OpenSearch when:** the corpus exceeds a few million documents, or search QPS is in the hundreds+, or relevance tuning is an ongoing product activity, or you need rich faceting, or you need the index to scale independently of the OLTP database (a strong argument on its own — a heavy search workload on the primary competes for the same buffer pool, Lesson 31).

For BookNook's 50,000 books (§9), **PostgreSQL full-text search is the correct engineering choice**, and the lesson deliberately says so.

### 4.11 Vector search and embeddings — a different problem

Everything so far is **lexical**: it matches terms. If the document says "cardiac arrest" and the user types "heart attack", there is no shared term and no amount of stemming helps. The old fix is a synonym list, which does not generalise.

**Vector search** fixes this differently. An **embedding model** maps a piece of text to a fixed-length vector of floats (typically 384–1,536 dimensions) such that **semantically similar texts land near each other**. Similarity is cosine distance or dot product.

Searching every vector is O(N × dimensions) — too slow — so engines use **approximate nearest neighbour (ANN)** indexes, most commonly **HNSW** (Hierarchical Navigable Small World, a layered proximity graph — note it is a *graph* traversal structure, exactly Lesson 46's idea applied to vectors). ANN trades a small, tunable amount of recall for a very large speedup.

The honest framing — **vector search solves a different problem, it does not replace lexical search:**

| | Lexical (BM25) | Vector (embeddings) |
|---|---|---|
| Matches | Exact terms after analysis | Meaning / semantic proximity |
| "heart attack" → "cardiac arrest" | Misses, unless a synonym rule exists | Matches |
| Exact ISBN `9780441013593` | Perfect | **Unreliable** — identifiers have no meaning to embed |
| Rare proper nouns, part numbers, model codes | Excellent — rare terms get high IDF | Poor |
| Explainability | High — you can show which terms matched | Low — "the vectors were close" |
| Cost | Cheap, CPU-only | Embedding generation per document and per query; large memory footprint (1 M × 768 floats × 4 B ≈ 3 GB) |
| Freshness | Reindex a document | Re-embed on model change — **changing the model invalidates every vector** |

Production systems therefore run **hybrid search**: execute both, combine with **Reciprocal Rank Fusion** (`score = Σ 1/(k + rank)`, typically `k = 60`), optionally rerank the top 50 with a cross-encoder. Lexical handles identifiers and rare terms; vectors handle paraphrase and intent. For BookNook, hybrid is a plausible later improvement — *"books like Dune but funnier"* is a vector query — but it is strictly an addition to BM25, never a replacement.

## 5. Request / data flow

A shopper types `quantum physics for beginners under £15` into BookNook's search box; the UI has parsed the price into a filter control. The request is `GET /search?q=quantum+physics+for+beginners&max_price=15&in_stock=true&page=1`.

1. The API service receives the request, validates and length-caps `q` (a 10 KB query string is a denial-of-service vector), and strips control characters.
2. It checks Redis for a cached response keyed by the normalised query plus filters plus page (Lesson 29). Head queries — the top ~200 searches — are often 40 % of traffic, so this alone removes a large fraction of load. Assume a miss.
3. The service builds a `bool` query: the user's words go in `must` as a `multi_match` over `title^5, author^3, description^1`; `max_price` becomes a `range` **filter**; `in_stock` becomes a `term` **filter**. Pagination becomes `from: 0, size: 20`. Facet aggregations for category, price buckets and rating are attached.
4. The request reaches a **coordinating node** in the cluster. Any node can coordinate.
5. The coordinating node resolves the alias `booknook-books` → `booknook-books-v3` and determines the target shards. BookNook has one primary and one replica, so it picks one copy (round-robin, or adaptive replica selection, which is essentially Lesson 23's least-response-time applied to shards).
6. **Query phase on the shard.** The query text runs through the **same analyser** used at index time → `[quantum, physic, beginn]` (`for` dropped as a stop word).
7. The shard evaluates the **filters first**, because filters are cheap and reduce the candidate set before any scoring. `in_stock: true` hits the cached bitset; `price ≤ 15` is evaluated against doc values. Result: a bitmap of ~31,000 eligible documents out of 50,000.
8. The shard walks the postings lists for the three terms, intersecting/unioning them with skip pointers and applying the filter bitmap. **Block-max WAND** skips entire blocks of postings whose maximum possible score cannot reach the current 20th-best score — this is why real engines score far fewer documents than match.
9. BM25 is computed for each surviving candidate, using cached per-term IDF and per-document length norms. A bounded min-heap of size 20 keeps the best.
10. The shard returns **only document IDs and scores** — not the documents themselves. With one shard this is trivial; with many shards the coordinating node merges the per-shard heaps, which is why `from: 10000` is expensive (every shard must return 10,020 entries) and why **`search_after` is the correct deep-pagination mechanism**.
11. **Fetch phase.** The coordinating node asks for the `_source` of the final 20 IDs only, plus highlighting (re-analysing those 20 documents to mark matched terms).
12. **Aggregations** run over the matched set using doc values, producing the facet counts.
13. The response (hits, total, facets, `took` in milliseconds) returns to the API service.
14. The service reshapes it into its own DTO — **never leak the raw engine response to clients**; it exposes your mapping and makes the engine impossible to replace — caches it in Redis with a 60 s TTL, and returns it.
15. Asynchronously, the service logs the query, result count and (later) which result was clicked. **This log is the raw material for all future relevance tuning (§4.8) and zero-result analysis.** A search system without query logging cannot be improved.

Meanwhile, continuously and independently: a CDC consumer (§4.9) reads the PostgreSQL WAL, transforms changed rows into denormalised documents, and bulk-indexes them. A refresh every 30 s makes them searchable.

## 6. ASCII diagram

```
WHY  LIKE '%quantum%'  CANNOT USE A B-TREE

  B-tree on description (sorted left-to-right):
        ┌──────────────┬──────────────┬──────────────┐
        │ "A desert…"  │ "Modern qu…" │ "Quantum p…" │
        └──────────────┴──────────────┴──────────────┘
  Looking for the substring "quantum":   ▲      ▲        ▲
  it can be at ANY offset in ANY row, so matches are scattered
  across the whole key space. Sort order carries NO information.
  ⇒ the only correct plan is SEQ SCAN of every row, every byte.


THE INVERTED INDEX  (the same data, organised by TERM)

   DICTIONARY (sorted terms)        POSTINGS (sorted doc ids, compressed)
   ┌───────────┬────┐
   │ beginn    │ df2│──────────────► D1(1)@[3] → D3(1)@[2]
   │ cook      │ df1│──────────────► D3(1)@[1]
   │ physic    │ df1│──────────────► D1(1)@[2]
   │ physicist │ df1│──────────────► D2(1)@[5]   ◄── stemmer did NOT
   │ quantum   │ df2│──────────────► D1(1)@[1] → D2(1)@[2]   merge these
   │ univers   │ df1│──────────────► D2(1)@[3]
   └───────────┴────┘
   query "Quantum Physics" ─► SAME ANALYSER ─► [quantum, physic]
         quantum: {D1, D2}
         physic : {D1}
         merge  : D1 scores on 2 terms, D2 on 1  ⇒  D1, D2


QUERY CONTEXT vs FILTER CONTEXT

  bool
   ├── must   : match "quantum physics"   ──►  SCORED. BM25 per doc.
   │                                            Not cacheable: the value
   │                                            depends on the whole query.
   └── filter : in_stock = true           ──►  YES/NO. No score.
                price <= 15                    Result is a BITSET:
                                               1 bit per doc, 50k docs
                                               = 6.25 KB, CACHED.
                                               Reuse = bitwise AND, µs.

  ┌────────────────────────────────────────────────────────────┐
  │ 50,000 docs ──filter bitset AND──► 31,000 eligible         │
  │              ──postings walk────►   2,500 candidates       │
  │              ──block-max WAND───►     600 actually scored  │
  │              ──min-heap(20)─────►      20 returned         │
  └────────────────────────────────────────────────────────────┘
   Each stage is an order of magnitude cheaper than scoring everything.


KEEPING THE INDEX IN SYNC

  WRONG — dual write                  RIGHT — CDC / outbox
  ┌─────┐                             ┌─────┐
  │ App │─1─► Postgres  OK            │ App │─► Postgres ──┐ ONE
  │     │─2─► Search    ✗ FAIL        └─────┘   + outbox ──┘ TXN
  └─────┘                                          │
   silent, permanent divergence                    ▼ WAL / relay
   nothing detects it                        ┌───────────┐
                                             │   queue   │
                                             └─────┬─────┘
                                                   ▼ idempotent upsert
                                             ┌───────────┐
                                             │  Search   │
                                             └───────────┘
   replayable from an offset • ordered per row • survives outages


NEAR-REAL-TIME: WHY YOUR NEW BOOK ISN'T THERE YET

  index request ─► in-memory buffer ─► translog (durable, fsync 5s)
                        │
                        │  refresh_interval (default 1s)
                        ▼
                   NEW SEGMENT  ──► now searchable
                        │
                        │  background merge
                        ▼
                  bigger segment, deletions physically removed
  Durability (translog) and visibility (refresh) are SEPARATE clocks.
```

## 7. Detailed example

### 7.1 BookNook — from `LIKE` to a real search box

**Stage 0 — the baseline.** `WHERE title ILIKE '%' || $1 || '%' OR description ILIKE ...` over 50,000 books. 180 ms p50, 900 ms p99, and — far worse than the latency — a **34 % zero-result rate**. One in three searches returned nothing. Analysis of the query log showed why: plurals (`vampires` vs `vampire`), typos (`heminway`), word order (`shoes running`), and multi-word queries where one word appeared in the title and another in the description, which the `OR` scattered and the `AND` eliminated.

**Stage 1 — PostgreSQL full-text search.** A generated `tsvector` column weighting title as `A`, author `B`, description `C`; a GIN index; `websearch_to_tsquery` for user-friendly syntax; `pg_trgm` on title for typo tolerance and "did you mean".

Results: p50 **4 ms**, p99 **22 ms**. Zero-result rate fell from 34 % to **7 %**. Index size: ~28 MB. Cost: **zero new infrastructure, zero sync problem** — the `tsvector` is maintained inside the same transaction as the row.

**This is where BookNook stopped, and it was the right call.** By §4.10's criteria — 50,000 documents, ~200 peak QPS, modest ranking needs — Postgres is sufficient with an order of magnitude of headroom. Adopting Elasticsearch at this point would have bought a few milliseconds and purchased a CDC pipeline, a cluster to operate, a reindex runbook, and a freshness alert.

**Stage 2 — what would force the move.** Two years later, three things changed together: the catalogue reached 4 M titles via a marketplace integration; merchandising wanted facets (category, price band, rating, format, language) with live counts; and the relevance team wanted per-query boosting driven by click data. Each of those is individually a weak argument. Together they are decisive: `ts_rank` over a 4 M-row corpus with five live facet counts is exactly what Postgres FTS is bad at, and the search workload had begun to evict the OLTP buffer pool, slowing checkout.

The migration: define an explicit mapping with multi-fields; write through an alias; backfill with the bulk API and `refresh_interval: -1`; stand up Debezium CDC from the WAL; run both systems in parallel with shadow traffic comparing result sets; cut over by feature flag; keep the Postgres path as a fallback for one quarter.

**Stage 3 — relevance work.** Field boosts `title^5, author^3, description^1`; a synonym list built from the zero-result log (`sci-fi → science fiction`, `ya → young adult`); a `rank_feature` on sales rank so that among similar matches the popular book wins; out-of-stock items given a negative boost rather than being filtered out (users still want to see them and be offered a pre-order). Measured by click-through on the top 3 results and zero-result rate, A/B tested one change at a time.

### 7.2 Contrasting example — log search at an observability platform

An observability vendor ingests **4 TB of logs per day** across thousands of customers. Superficially this is also "Elasticsearch", and almost every design decision is the opposite of BookNook's.

| | BookNook catalogue | Log search |
|---|---|---|
| Documents | 50,000, slowly changing | ~20 billion/day, append-only, never updated |
| Primary concern | **Relevance ranking** | **Time-bounded filtering and aggregation** — "errors from service X in the last 15 minutes" |
| Scoring | BM25, carefully tuned | Mostly irrelevant — results are sorted by **timestamp**, so nearly everything runs in **filter context** |
| Index layout | One index, one shard | **Time-based indices** (`logs-2026.10.09`) with rollover, so deleting old data is dropping an index — not a `DELETE` query, which would be ruinously expensive |
| Shards | 1 primary, 1 replica | Hundreds per day; sized at ~40 GB each |
| Retention | Forever | Hot (7 days, SSD) → warm (30 days) → cold/frozen on object storage → delete. **Index lifecycle management.** |
| Refresh | 30 s is fine | 1 s or less — operators are watching a live incident |
| Source of truth | PostgreSQL; the index is derived | **The index is the only copy** (or object storage is), so replica count and snapshots are a data-loss question, not a latency one |
| Analysis | Stemming, synonyms, boosts | Minimal — `keyword` fields everywhere, because you search for exact service names, trace IDs and status codes. Stemming a stack trace is actively harmful |

The instructive point: **the same engine, configured by opposite principles**, because the questions differ. BookNook asks "which of these 50,000 documents best answers a vague human phrase?" The log platform asks "which 200 of these 20 billion records exactly match these labels in this time window?" Confusing the two — stemming log fields, scoring timestamps, or putting a catalogue in time-based indices — produces systems that are slow and wrong simultaneously.

## 8. Code or configuration example

> **Illustrative teaching examples, not production-ready.** They omit authentication, TLS, index lifecycle policies, retries, bulk error handling, circuit breakers, resource limits and input sanitisation. **JSON does not support comments** — the `//` lines below are annotations for you and must be removed before sending any of this to a real cluster.

### 8.1 PostgreSQL full-text search — BookNook's actual Stage 1

```sql
-- A GENERATED column keeps the inverted index in sync inside the same
-- transaction as the row. This is why Postgres FTS has NO dual-write problem.
ALTER TABLE books ADD COLUMN search_vector tsvector
  GENERATED ALWAYS AS (
      -- setweight assigns A/B/C/D; ts_rank weights them {0.1,0.2,0.4,1.0}
      setweight(to_tsvector('english', coalesce(title, '')),       'A') ||
      setweight(to_tsvector('english', coalesce(author, '')),      'B') ||
      setweight(to_tsvector('english', coalesce(description, '')), 'C')
      -- coalesce is mandatory: NULL || anything = NULL, silently emptying the row
  ) STORED;

-- GIN, not GiST: GIN is a true inverted index — larger and slower to update,
-- but exact and much faster to query. GiST is lossy and rechecks every hit.
CREATE INDEX books_search_idx ON books USING GIN (search_vector);

-- websearch_to_tsquery understands quotes, OR and -negation, so it is safe
-- to hand raw user input to (unlike to_tsquery, which throws on bad syntax).
SELECT isbn, title,
       ts_rank_cd(search_vector, query) AS rank   -- _cd = cover density:
                                                  -- rewards terms appearing
                                                  -- close together
FROM books,
     websearch_to_tsquery('english', 'quantum physics beginners') AS query
WHERE search_vector @@ query          -- @@ is the "matches" operator; this is
                                      -- what the GIN index accelerates
  AND price <= 15.00                  -- ordinary predicates still apply
  AND in_stock
ORDER BY rank DESC
LIMIT 20;

-- Typo tolerance / "did you mean", using trigrams.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX books_title_trgm ON books USING GIN (title gin_trgm_ops);

SELECT title, similarity(title, 'heminway') AS sim   -- 0.0 .. 1.0
FROM books
WHERE title % 'heminway'        -- % = "similar enough", threshold default 0.3
ORDER BY sim DESC LIMIT 5;      -- NOTE: pg_trgm ALSO makes LIKE '%x%' indexable
```

### 8.2 Elasticsearch mapping — explicit, with multi-fields

```json
PUT /booknook-books-v3
{
  "settings": {
    "number_of_shards": 1,        // 60 MB of data (§9). ONE shard. Not five.
                                  // This value is IMMUTABLE after creation.
    "number_of_replicas": 1,      // changeable any time; 1 = survive one node loss
    "refresh_interval": "30s",    // a catalogue does not need 1s visibility;
                                  // longer interval = fewer segments = fewer merges
    "analysis": {
      "filter": {
        "booknook_synonyms": {
          "type": "synonym_graph",
          // Query-time synonyms: edit the list without reindexing 50k documents.
          "synonyms": [
            "sci-fi, scifi, science fiction",
            "ya, young adult",
            "physics, physicist, physical"   // patches the stemmer gap from §4.2
          ]
        },
        "english_stemmer": { "type": "stemmer", "language": "english" }
      },
      "analyzer": {
        "booknook_index": {                  // used when INDEXING
          "tokenizer": "standard",
          "filter": ["lowercase", "asciifolding", "english_stemmer"]
        },
        "booknook_search": {                 // used when QUERYING
          "tokenizer": "standard",
          // Identical to the index analyser PLUS synonyms. Deliberate asymmetry:
          // anything else here silently returns zero results (§15).
          "filter": ["lowercase", "asciifolding", "booknook_synonyms",
                     "english_stemmer"]
        }
      }
    }
  },
  "mappings": {
    "dynamic": "strict",          // reject unknown fields instead of guessing.
                                  // Prevents the "price became text" disaster.
    "properties": {
      "isbn":  { "type": "keyword" },               // exact match only, never analysed
      "title": {
        "type": "text",
        "analyzer": "booknook_index",
        "search_analyzer": "booknook_search",
        "fields": {
          // MULTI-FIELD: the same source text indexed three ways.
          "keyword": { "type": "keyword" },          // for sorting A-Z and exact match
          "autocomplete": {                          // for type-ahead (§4.3)
            "type": "search_as_you_type", "max_shingle_size": 3
          }
        }
      },
      "author":      { "type": "text",
                       "fields": { "keyword": { "type": "keyword" } } },
      "description": { "type": "text",
                       "analyzer": "booknook_index",
                       "search_analyzer": "booknook_search" },
      // keyword, NOT text: facets and filters need the whole value intact.
      // As "text", "Science Fiction" would become two terms and faceting breaks.
      "categories":  { "type": "keyword" },
      "price":       { "type": "scaled_float", "scaling_factor": 100 },
                                  // money as an integer of pence; never float
      "rating":      { "type": "half_float" },       // 0.0-5.0; 2 bytes is plenty
      "in_stock":    { "type": "boolean" },
      "sales_rank":  { "type": "rank_feature" },     // usable directly as a booster
      "published":   { "type": "date" },
      "indexed_at":  { "type": "date" }              // powers the freshness alert (§4.9)
    }
  }
}

// Always point an alias at the concrete index. Reindexing then becomes an
// atomic alias swap instead of an outage (§4.9).
POST /_aliases
{ "actions": [ { "add": { "index": "booknook-books-v3",
                          "alias": "booknook-books" } } ] }
```

### 8.3 The search query — correct use of query vs filter context

```json
GET /booknook-books/_search
{
  "from": 0, "size": 20,
  "query": {
    "bool": {
      "must": [                       // QUERY CONTEXT: scored with BM25
        {
          "multi_match": {
            "query": "quantum physics for beginners",
            "fields": ["title^5", "author^3", "description"],
            //          ^ title matches count 5x a description match (§4.8)
            "type": "best_fields",    // score = best single field, not the sum.
                                      // "cross_fields" if terms are split across
                                      // fields; "phrase" for strict adjacency.
            "fuzziness": "AUTO",      // edit distance by term length: 0 for 1-2
                                      // chars, 1 for 3-5, 2 for 6+. Typo tolerance.
            "minimum_should_match": "75%"   // with 4 terms, require 3. Cuts the
                                            // long tail of 1-term junk matches.
          }
        }
      ],
      "should": [                     // optional: adds score, never required
        { "match_phrase": { "title": { "query": "quantum physics", "boost": 4 } } }
      ],
      "filter": [                     // FILTER CONTEXT: no scoring, CACHEABLE (§4.5)
        { "term":  { "in_stock": true } },          // cached bitset, reused on
                                                    // essentially every search
        { "range": { "price": { "lte": 15.00 } } },
        { "terms": { "categories": ["Science Fiction", "Science"] } }
      ],
      "must_not": [                   // also filter context — unscored
        { "term": { "categories": "Adult" } }
      ]
    }
  },
  "aggs": {                           // FACETS, computed in the same round trip
    "by_category": { "terms": { "field": "categories", "size": 10 } },
    "price_bands": {
      "range": { "field": "price",
                 "ranges": [ { "to": 10 }, { "from": 10, "to": 20 },
                             { "from": 20 } ] }
    },
    "avg_rating": { "avg": { "field": "rating" } }
  },
  "highlight": { "fields": { "description": { "fragment_size": 150 } } },
  "track_total_hits": 1000,
  // Counting ALL matches forces full enumeration. Capping at 1000 lets the
  // engine stop early; the UI says "1,000+ results", which is what users read.
  "_source": ["isbn", "title", "author", "price", "rating"]
  // Return only the fields the page renders. _source fetch is real I/O.
}
```

### 8.4 Indexing: bulk load, and the sync pattern

```json
// Bulk API: two lines per document — action metadata, then the document.
// One HTTP request per ~5-15 MB of payload. Never index one doc per request:
// the per-request overhead dominates and throughput drops by 10-50x.
POST /booknook-books/_bulk
{"index":{"_id":"9780441013593","version":1696857600,"version_type":"external"}}
{"isbn":"9780441013593","title":"Dune","author":"Frank Herbert","price":8.99}
{"index":{"_id":"9780547928227","version":1696857601,"version_type":"external"}}
{"isbn":"9780547928227","title":"The Hobbit","author":"J.R.R. Tolkien","price":7.50}

// "version_type": "external" with the source row's updated_at as the version
// makes indexing SAFE UNDER REORDERING: an older event arriving late is
// rejected with 409 instead of overwriting newer data (§4.9).
```

```sql
-- The transactional outbox (§4.9): business write and event in ONE transaction,
-- so they cannot diverge. Full treatment in Phase 4, Topic 22.
BEGIN;
  UPDATE books SET price = 7.99, updated_at = now() WHERE isbn = '9780441013593';
  INSERT INTO outbox (aggregate_type, aggregate_id, payload, created_at)
  VALUES ('book', '9780441013593',
          jsonb_build_object('isbn','9780441013593','price',7.99), now());
COMMIT;
-- A relay polls outbox (or Debezium tails the WAL), publishes to the indexer,
-- and marks rows done. Delivery is AT-LEAST-ONCE, which is fine because
-- indexing by document id is an idempotent upsert.
```

```json
// Diagnose relevance, always, before changing a boost.
GET /booknook-books/_explain/9780441013593
{ "query": { "match": { "title": "quantum physics" } } }
// Returns the full BM25 arithmetic per term: idf, tf, length norm, boost.
// If you cannot explain why a result ranks where it does, do not tune it.
```
