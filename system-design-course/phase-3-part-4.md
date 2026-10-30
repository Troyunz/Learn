# Lesson 52 — Optimistic versus Pessimistic Concurrency Control

*Phase 3, Topic 13*

## 1. Simple definition

**Concurrency control** is the set of mechanisms a database uses to stop two transactions that run at the same time from corrupting each other's work. Lesson 50 covered *isolation levels* — the contract the database offers. Lesson 51 covered the *anomalies* that leak through weak levels, including lost update and write skew. This lesson covers the two families of mechanism you can actually build with.

They differ in a single assumption:

- **Pessimistic concurrency control (PCC)** assumes a conflict *is likely*. It takes a lock **before** touching the data, and holds it until commit. Other transactions wait. Conflicts are *prevented*.
- **Optimistic concurrency control (OCC)** assumes a conflict is *unlikely*. It takes no lock, does the work on a private copy, and **validates at commit time** that nothing it read has changed. If something changed, the transaction is aborted and retried. Conflicts are *detected*.

Terms defined before use:

- **Lock**: a database-held reservation on a row, page, or table that blocks incompatible access by other transactions until it is released at commit or rollback.
- **Conflict**: two concurrent transactions where at least one writes an item the other read or wrote. Two readers never conflict.
- **Conflict rate (`p`)**: the fraction of transaction attempts that overlap in time with another transaction touching the same item. This number, not your taste, decides the answer.
- **Validation**: the commit-time check that the data an optimistic transaction read is still in the state it was read in.
- **Compare-and-swap (CAS)**: a conditional write — "set `x = new` *only if* `x` is still `old`". The atomic primitive underneath all optimistic schemes.

The whole lesson reduces to one sentence: **pessimistic pays a fixed cost on every transaction; optimistic pays nothing when there is no conflict and a large cost when there is.** Which is cheaper is arithmetic, not opinion.

## 2. Why it exists

Lesson 51 showed the lost update anomaly concretely. BookNook has one copy of a limited-edition print left:

```
T1 (customer A)                T2 (customer B)
read stock = 1                 read stock = 1
check 1 >= 1  → ok             check 1 >= 1  → ok
write stock = 0                write stock = 0
commit                         commit
```

Two customers are charged. One copy exists. The database did nothing wrong at READ COMMITTED (Lesson 50) — both transactions read a committed value and wrote a committed value. The *application's* read-modify-write cycle was not atomic.

You have exactly three ways out:

1. **Make the database refuse the interleaving** — raise the isolation level to SERIALIZABLE and let the engine sort it out. Correct, but it pushes the cost into the engine where you cannot tune it, and under SERIALIZABLE in PostgreSQL you must still handle serialization-failure retries, so you have not escaped retry logic anyway.
2. **Lock first** — pessimistic. `SELECT ... FOR UPDATE` on the stock row. T2 blocks until T1 commits, then re-reads `stock = 0` and correctly refuses.
3. **Validate at commit** — optimistic. Both read `stock = 1, version = 7`. Both try `UPDATE ... WHERE version = 7`. Exactly one succeeds; the loser sees zero rows affected and retries, re-reads `stock = 0`, and correctly refuses.

Options 2 and 3 are this lesson. They are not interchangeable: at BookNook's checkout, where a hot bestseller is bought 400 times a second, the choice between them is the difference between 9,000 orders per second and 1,400.

A second reason OCC exists: **you cannot always hold a lock.** If a human edits a book description in an admin UI for four minutes, you cannot hold a row lock for four minutes — you would block every other writer and risk the client's browser closing with the lock still held. The edit is a *stateless* HTTP exchange (Lesson 2), spanning two separate requests. The only workable mechanism across request boundaries is optimistic validation, which is exactly what HTTP `ETag` / `If-Match` is (Lesson 6).

## 3. Real-world analogy

**A shared library book and a shared Google Doc.**

- **Pessimistic** = the old university short-loan desk. You ask for the single reference copy, the librarian hands it to you and records that it is out. Nobody else can touch it. When you return it, the next person gets it. No two people ever write in it at once — because no two people ever *hold* it at once. Guaranteed correct, and the queue is visible and orderly.
- **Optimistic** = a wiki page. Anyone can start editing at any time. When you click Save, the wiki compares the revision number you loaded against the current revision number. If they match, your edit lands. If someone saved before you, you get "this page has changed since you began editing" and must redo your edit on top of theirs.

When the page is edited once a week, the wiki model is strictly better — zero waiting, zero bookkeeping. When forty people edit the same paragraph during a live event, the wiki model degenerates into everyone retyping their edit repeatedly and nobody making progress, while the short-loan desk would have quietly served all forty in sequence.

**Where the analogy breaks down:**

| Library / wiki | Database concurrency control |
|---|---|
| The librarian can see who is holding the book and for how long | The database knows who holds a lock, but your *application* usually does not — a lock held by a transaction stuck on a slow third-party HTTP call looks identical to a healthy one |
| A human waiting in a queue gets bored and leaves | A blocked transaction waits silently until `lock_timeout` or `innodb_lock_wait_timeout` fires; without that setting it waits essentially forever, and connections pile up until the pool is exhausted (Phase 3, Topic 17 — Connection pooling) |
| Only one book is involved | Transactions touch many rows. Two transactions each holding one row and wanting the other deadlock — a failure mode with no library equivalent, because a librarian would never hand out half of something |
| The wiki's "someone else saved first" is shown to a human who can re-read and decide | Optimistic retries happen in code. Blind retries can re-apply a side effect (an email, a card charge) unless the operation is idempotent (Lesson 17) |
| Retrying a wiki edit costs you 30 seconds of your own time | Retrying a database transaction costs *the database* the full work of the aborted attempt. Wasted work reduces the capacity available for successful work, which raises the conflict rate, which causes more waste. That feedback loop has no analogy in the library and is the single most important thing in §9 |
| The librarian lends the whole book | Databases lock at a *granularity* — row, page, or table — and may silently promote from one to another (lock escalation), so locking 10,000 rows can end up locking the table |

That second-to-last row is the crux. Optimistic concurrency control does not degrade gracefully. It degrades **catastrophically**, because its failure mode consumes the resource that would have fixed it.

## 4. Detailed technical explanation

### 4.1 Pessimistic: locks

A lock is a reservation in the database's in-memory lock table. Two properties matter: **mode** and **granularity**.

**Modes.** The two basic modes are:

| Mode | Also called | Compatible with another S? | Compatible with another X? |
|---|---|---|---|
| **S** — shared | read lock | Yes | No |
| **X** — exclusive | write lock | No | No |

Shared locks let many readers coexist; an exclusive lock excludes everybody. In SQL:

- `SELECT ... FOR SHARE` (PostgreSQL) / `SELECT ... LOCK IN SHARE MODE` (MySQL) takes **S**. Use it when you need to guarantee a row you read will not change before you commit, but you are not going to write it.
- `SELECT ... FOR UPDATE` takes **X**. Use it when you intend to write the row.
- A plain `UPDATE` or `DELETE` takes **X** on the rows it modifies, automatically.

A detail that surprises people: in PostgreSQL and MySQL InnoDB, a plain `SELECT` takes **no row lock at all**. It reads a consistent snapshot through MVCC (multi-version concurrency control — the engine keeps old row versions so readers never block writers). This is why the lost update in §2 happens: neither read took a lock, so there was nothing to conflict.

**Granularity.** Locks can be held on:

- **Row** — the finest and the default for InnoDB and PostgreSQL. Maximum concurrency, maximum bookkeeping (one lock-table entry per locked row).
- **Page** — a whole 8 KB/16 KB block of rows. Used by some engines (historically SQL Server, Sybase) as a middle ground. Cheap bookkeeping, but locks unrelated rows that happen to live nearby — **false contention**.
- **Table** — one entry locks everything. Nearly free to track, catastrophic for concurrency.

**Lock escalation** is the automatic promotion from fine to coarse granularity when the lock count crosses a threshold, because the lock table itself has a memory budget. SQL Server escalates to a table lock at roughly 5,000 locks in one statement (approximate; the threshold is dynamic). A `DELETE FROM orders WHERE created_at < '2023-01-01'` touching 80,000 rows can therefore become a table lock and stall the whole application. **PostgreSQL does not escalate** — it stores row locks as a bit on the row itself in the heap plus an entry in the multixact structure, so its row-lock count is effectively unbounded; it pays for this with heap writes instead. InnoDB also does not escalate. Knowing which engine you are on changes the shape of your bulk-delete strategy (chunk it into 1,000-row batches on SQL Server; on PostgreSQL the constraint is long-transaction vacuum impact instead).

### 4.2 `SELECT ... FOR UPDATE SKIP LOCKED`

Normal `FOR UPDATE` **waits** for a conflicting lock. Two variants change that:

- `FOR UPDATE NOWAIT` — error immediately instead of waiting. Useful when you would rather tell the user "someone else is editing this" than hang.
- `FOR UPDATE SKIP LOCKED` — silently *skip* rows that are already locked and return the rest.

`SKIP LOCKED` turns a table into a **work queue**. Ten worker processes each run `SELECT ... FOR UPDATE SKIP LOCKED LIMIT 10` against a `jobs` table; each gets a disjoint set of ten rows with no coordination, no external broker, and no possibility of two workers claiming the same job. BookNook uses this for its outbound email queue and its warehouse-dispatch queue. The guarantee is exactly-once *claiming*; whether the job's effect is exactly-once still depends on it being idempotent (Lesson 17), because a worker can crash after doing the work and before committing the claim.

Note what `SKIP LOCKED` is **not**: it is not a general-purpose fix for contention. Skipping a row means not processing it *now*. That is correct for a queue (another worker will take it) and wrong for a checkout (you cannot skip the customer's order).

### 4.3 Deadlocks

A **deadlock** is a cycle of transactions each waiting for a lock held by another. Nobody can proceed; no amount of waiting helps.

Worked example — BookNook transfers stock between two warehouse bins:

```
time   T1 (move 5 copies bin A → bin B)   T2 (move 3 copies bin B → bin A)
----   --------------------------------   --------------------------------
t0     BEGIN                               BEGIN
t1     UPDATE bins SET qty=qty-5
         WHERE id='A'        → X lock on A
t2                                         UPDATE bins SET qty=qty-3
                                             WHERE id='B'   → X lock on B
t3     UPDATE bins SET qty=qty+5
         WHERE id='B'        → blocks, B held by T2
t4                                         UPDATE bins SET qty=qty+3
                                             WHERE id='A'   → blocks, A held by T1
```

**Detection.** The database maintains a **wait-for graph**: a directed graph with one node per transaction and an edge `T1 → T2` meaning "T1 is waiting for a lock T2 holds". A deadlock exists exactly when the graph contains a cycle. Above:

```
T1 ──waiting for B──▶ T2
 ▲                     │
 └──waiting for A──────┘        cycle ⇒ deadlock
```

InnoDB runs cycle detection on every lock wait and resolves it immediately. PostgreSQL does not check on every wait (that would be expensive); it waits `deadlock_timeout` (default 1 second, typical) and only then builds the graph and looks for a cycle. Consequence: **on PostgreSQL a deadlock costs you a full second of latency before you even learn about it.** The engine then picks a **victim** — usually the transaction that has done the least work, or in InnoDB the one holding the fewest row locks — rolls it back, and returns an error (SQLSTATE `40P01` in PostgreSQL, error 1213 in MySQL). The survivor proceeds.

**Prevention.** The standard and almost always correct fix is to **acquire locks in a globally consistent order**. If every transaction that touches multiple bins locks them in ascending `id` order, no cycle can form — a cycle requires some transaction to go "high then low" while another goes "low then high".

```sql
-- Lock both rows up front, in a deterministic order, before any write.
SELECT id, qty FROM bins WHERE id IN ('A','B') ORDER BY id FOR UPDATE;
```

Two other levers, both weaker:

- **Shorten transactions.** A lock held for 2 ms can barely participate in a deadlock; one held for 2 s is a magnet. Never do network I/O (payment gateway, S3, email) inside an open transaction.
- **Reduce lock footprint.** Lock the minimum rows for the minimum time, and avoid taking an S lock that you will later need to upgrade to X — **lock upgrade** (S→X) is a classic deadlock source, because two transactions can both hold S and both wait to upgrade. Take X directly if you know you will write.

**Deadlock is not the same as lock wait timeout.** A deadlock is a *cycle* and is detected and killed deterministically. A **lock wait timeout** is a transaction that waited longer than a configured bound for a lock that was never part of a cycle — someone was simply slow. Settings:

- PostgreSQL: `lock_timeout` (per-statement, default `0` = infinite — **set this**, 1–5 s is a typical starting point) and `idle_in_transaction_session_timeout` (kills a transaction that opened and then went idle holding locks).
- MySQL InnoDB: `innodb_lock_wait_timeout` (default 50 s — far too long for an interactive path; 2–10 s is typical for web workloads).

Leaving these at defaults is how a single slow transaction turns into a site-wide outage: blocked connections accumulate, the connection pool empties, and requests that never needed that row start failing too.

### 4.4 Optimistic: version columns and CAS

The optimistic scheme has four parts.

1. **A version column.** Add `version BIGINT NOT NULL DEFAULT 1` (or `updated_at TIMESTAMPTZ`) to the table.
2. **Read without locking.** `SELECT stock, version FROM books WHERE id = 101` — a plain snapshot read, no lock, no blocking.
3. **Compare-and-swap on write.** `UPDATE books SET stock = :new, version = version + 1 WHERE id = 101 AND version = :version_i_read`. The `AND version = ...` is the compare; the `SET` is the swap. The whole statement is atomic because the engine takes an X lock for the duration of *that single statement* — microseconds, not the length of your transaction.
4. **Detect zero affected rows.** If the `UPDATE` reports `0` rows modified, someone else committed in between. This is the *only* signal, and it is easy to miss: the statement does not raise an error. Every ORM exposes a row count (`cursor.rowcount`, `ResultSet.getUpdateCount()`, ActiveRecord's optimistic locking raises `StaleObjectError`). **Code that ignores the row count has silently reintroduced the lost update.**

**Use a version counter, not a timestamp,** when you can. Timestamps fail in three ways: clock resolution (two updates inside the same millisecond get the same value), clock skew across nodes, and clock adjustment moving time backwards. A monotonically incremented integer has none of these problems. Use `updated_at` only when you are retrofitting a table you cannot alter and the update rate per row is far below the clock resolution.

**The retry loop.** On detecting a conflict, the transaction must restart from the read — not from the write, because the value it computed was based on stale data. Retry policy matters enormously and is exactly the material from **Lesson 17**:

- **Bounded attempts** (3–5 is typical). Unbounded retry under sustained contention is an availability incident.
- **Exponential backoff** — wait `base × 2^attempt` before retrying, so that load falls as contention rises.
- **Full jitter** — randomise the wait into `[0, base × 2^attempt]`. Without jitter, all losers retry at the same instant and collide again; this **retry convoy** is the mechanism that turns a brief contention spike into a sustained one.
- **Idempotency** (Lesson 17 again) — the retried transaction must not duplicate external side effects. In practice: do the database transaction first, and perform the side effect (charge, email) exactly once afterwards keyed by an idempotency key.

### 4.5 OCC at the API layer: ETag and If-Match

The same pattern, one layer up. Lesson 6 introduced `ETag` as a cache-validation header. It is also the HTTP-native optimistic concurrency primitive:

```
GET /api/books/101                 →  200 OK
                                      ETag: "v7"
                                      { "title": "...", "price": 1299 }

PUT /api/books/101                 →  412 Precondition Failed
If-Match: "v7"                        (because the stored ETag is now "v9")
{ "price": 1199 }
```

The server compares `If-Match` against the current version and returns `412 Precondition Failed` on mismatch. This is a version-column CAS expressed in HTTP, and it works across the stateless request boundary (Lesson 2) where a lock could not. Pair it with `If-None-Match` for caching and you get both behaviours from one header. A `PUT` with no `If-Match` is a last-writer-wins blind overwrite; requiring the header (responding `428 Precondition Required` when absent) is how you make the API safe by default.

### 4.6 Beyond one database

Everything above relies on a single database being the arbiter. The moment the resource you are protecting lives *outside* one database — a file in object storage, a third-party API, a cron job that must run on exactly one node — you need a **distributed lock**: a lease held in a coordination service (ZooKeeper, etcd, Consul) or a Redis key, with a timeout so a crashed holder's lock is eventually released. Distributed locks are genuinely harder than they look, because a lease can expire while the holder still believes it holds it, and the standard mitigation is a monotonically increasing **fencing token** checked by the resource. This is covered in **Phase 4, Topic 15 — Distributed locks and leases**. Until then, the rule is: if a single database row can be the arbiter, make it the arbiter.

## 5. Request / data flow

**Pessimistic — BookNook checkout, `POST /orders` for one book:**

1. The request reaches an app server via the load balancer (Lesson 23).
2. The app checks out a connection from the pool and issues `BEGIN`.
3. `SELECT stock FROM books WHERE id = 101 FOR UPDATE`. The engine takes an **X** row lock on book 101.
4. If another transaction already holds that lock, this statement **blocks**. The connection is now idle-but-held. A `lock_timeout` bounds the wait; on expiry the statement errors and the app returns `503` with `Retry-After`.
5. Lock acquired. The app reads `stock = 12`, verifies `12 >= 1`.
6. `UPDATE books SET stock = stock - 1 WHERE id = 101` — no extra lock needed, already held.
7. `INSERT INTO orders (...)` — takes an X lock on the brand-new row, which nobody else can see, so it is uncontended.
8. `COMMIT`. The write-ahead log is flushed and **all locks are released at this instant**. Any waiter is woken.
9. The app returns `201 Created`. The next waiting transaction now re-reads `stock = 11` and proceeds.

**Optimistic — same endpoint:**

1. Request arrives, connection checked out.
2. `SELECT stock, version FROM books WHERE id = 101` — **no lock**, a snapshot read. Returns `stock = 12, version = 7`.
3. The app verifies `12 >= 1` in application code. No database state is held.
4. `BEGIN`.
5. `UPDATE books SET stock = 11, version = 8 WHERE id = 101 AND version = 7`. The engine takes an X lock for the duration of this one statement only.
6. The app inspects the **affected row count**:
   - `1` → the CAS succeeded. Continue to step 7.
   - `0` → someone else committed version 8 first. `ROLLBACK`, sleep `random(0, 10 ms × 2^attempt)` (Lesson 17), and **go back to step 2** to re-read. After 4 failed attempts, give up and return `409 Conflict`.
7. `INSERT INTO orders (...)`.
8. `COMMIT`.
9. Return `201 Created`.

The structural difference: in the pessimistic flow the *waiting* happens inside step 3, invisible to the app, consuming a connection. In the optimistic flow the waiting happens in step 6, visible to the app, consuming CPU on work that is then thrown away.

## 6. ASCII diagram

```
PESSIMISTIC — lock first, nobody conflicts, everybody queues
=============================================================

  T1  ──BEGIN──[ SELECT ... FOR UPDATE ]══════work══════[COMMIT]
                      │ acquires X(101)                    │ releases
                      ▼                                    ▼
  T2  ──BEGIN──[ SELECT ... FOR UPDATE ]░░░░░BLOCKED░░░░░░[acquires X]══work══[COMMIT]
                                         └── waits here, connection held ──┘
  T3  ──BEGIN──[ SELECT ... FOR UPDATE ]░░░░░░░░░░░BLOCKED░░░░░░░░░░░░░░░░░[acquires]…

  Throughput on one hot row = 1 / (work + lock overhead)   ← strictly serial
  Wasted work = 0.   Latency = queue depth × service time.


OPTIMISTIC — no lock, validate at commit, losers redo
=============================================================

  T1  ──[read v=7]────compute────[UPDATE ... WHERE version=7]──▶ 1 row  ✓ COMMIT
  T2  ──[read v=7]────compute────[UPDATE ... WHERE version=7]──▶ 0 rows ✗ ROLLBACK
                                                                    │
                                     backoff + jitter (Lesson 17) ◀─┘
                                                                    │
  T2' ─────────────[read v=8]──compute──[UPDATE ... WHERE version=8]──▶ 1 row ✓ COMMIT

  Throughput = attempts/sec × (1 − p).   Wasted work = p × attempts.
  Latency = work × attempts_needed + total backoff.


DEADLOCK — the pessimistic failure mode
=============================================================

        holds X(A)                        holds X(B)
   ┌──────────────┐                  ┌──────────────┐
   │      T1      │ ──wants X(B)──▶  │      T2      │
   │              │ ◀──wants X(A)──  │              │
   └──────────────┘                  └──────────────┘
          └────────── wait-for cycle ─────────┘
   Engine detects cycle → kills the cheaper victim → survivor commits.
   Prevention: always lock A before B (global ordering).


THE CROSSOVER
=============================================================

  cost per
  committed  │                                    ╱ optimistic
  transaction│                                 ╱  (explodes as p→1)
             │                             ╱
             │━━━━━━━━━━━━━━━━━━━━━━━━━╱━━━━━━━━━  pessimistic
             │                     ╱               (flat + queueing)
             │_______________╱
             └──────────────┼─────────────────────────▶ conflict rate p
                           p*
              p < p* → use optimistic     p > p* → use pessimistic
```

## 7. Detailed example

### 7.1 BookNook — reserving inventory for a bestseller

BookNook is running a signed-edition drop: 5,000 copies of one title, announced on social media, with roughly **1,200 checkout attempts per second** arriving in the first thirty seconds. Every one of those transactions touches **the same row** in `books`. The conflict rate on that row is effectively 100%.

**Attempt 1 — no concurrency control.** Read stock, check, write stock. Lost update (Lesson 51). BookNook oversells by several hundred copies, has to cancel orders, and takes a reputational hit. This is the baseline failure.

**Attempt 2 — optimistic.** Every transaction reads `version = v`, computes `stock - 1`, and issues the CAS. At 1,200 attempts/sec on one row with a ~5 ms window, essentially one transaction per 5 ms window wins and the rest fail. Observed behaviour: ~200 successful reservations/sec, ~1,000 wasted attempts/sec, each burning a connection and a chunk of CPU. The wasted attempts raise database CPU to 95%, which lengthens the window, which raises the conflict rate further. Retries bounded at 4 attempts means most customers see `409 Conflict`. **Optimistic is the wrong tool here**, not because it is wrong in general but because `p ≈ 1`.

**Attempt 3 — pessimistic.** `SELECT stock FROM books WHERE id = 101 FOR UPDATE`. Transactions queue. With a 5 ms critical section, the row sustains ~190 commits/sec — about the same successful rate as optimistic — but with **zero wasted work**, so database CPU sits at 30% instead of 95%, and the remaining customers *wait* rather than *fail*. The complaint shifts from "it said conflict" to "it took four seconds", which is a far better complaint. Critically, the other 400,000 books in the catalogue are completely unaffected, because the lock is a row lock.

**Attempt 4 — fix the data model, which is the real answer.** Both schemes are bottlenecked by a single row. BookNook splits the 5,000 copies into **50 stock buckets of 100 copies each** (`stock_buckets(book_id, bucket_no, qty)`), and each transaction picks a bucket at random:

```sql
SELECT bucket_no, qty FROM stock_buckets
 WHERE book_id = 101 AND qty > 0
 ORDER BY random() LIMIT 1
   FOR UPDATE SKIP LOCKED;     -- skip buckets another txn is holding
```

`SKIP LOCKED` means a transaction never waits — it takes the first free bucket. Fifty buckets × ~190 commits/sec/bucket ≈ **9,500 reservations/sec**, 50× the single-row ceiling, with pessimistic-grade correctness. The cost is complexity: a bucket can hit zero while others still have stock, so the "sold out" check becomes `SUM(qty) = 0` across buckets, and a background job must rebalance buckets. This is **sharding a hot row**, and it is the standard production answer to extreme contention. Note that it does not replace concurrency control — it reduces `p` so that concurrency control is cheap.

**Where optimistic is right at BookNook.** The admin "edit book description" screen. A human loads the form, types for four minutes, and saves. Per-row conflict rate is far below 0.1% (two admins almost never edit the same book simultaneously), and a lock would have to be held for four minutes across two HTTP requests, which is impossible. The form carries the `version`; the save is a CAS; on `0` rows the UI says "this book was changed by someone else — here is what changed". Same for `PUT /api/books/{id}` with `If-Match` (§4.5).

### 7.2 A contrasting real-world example — airline seat selection versus hotel room booking

**Airline seat map (pessimistic, deliberately).** When you click seat 14C on a seat map, the airline places a **hold** — a short-lived exclusive reservation, typically 5–15 minutes — before you have paid. That hold is pessimistic concurrency control exposed as a product feature. The airline accepts the cost (seats sit unsellable while someone dithers, and the hold must expire on a timer because the user may close the tab) because the alternative — telling a customer at payment time that the seat they picked is gone — is a much worse experience and a conversion killer. Note the lock here is *not* a database row lock held open for 15 minutes; it is a row *written* into a `holds` table with an `expires_at`, enforced by a short database transaction. **A long-lived business lock is implemented as data, not as a database lock.** That distinction is worth more than any other sentence in this lesson.

**Git (optimistic, deliberately).** `git push` is a pure compare-and-swap: "move `refs/heads/main` from commit `abc123` to `def456`, *only if* it is still `abc123`". If someone pushed first, the server rejects with `non-fast-forward` and you must `pull --rebase` (re-read) and push again (retry). Nobody locks the repository, because conflict rates on a given branch are low and the cost of a conflict — a rebase — is acceptable. When conflict rates *do* get high (a 500-engineer monorepo pushing to `main`), teams add a **merge queue**, which is precisely a reversion to pessimistic serialisation: one change at a time, queued. The same crossover, in a completely different domain.

## 8. Code or configuration example

> **Illustrative only — not production-ready.** Error classes, connection management, observability, timeouts, and transaction-boundary management are simplified. Do not paste this into a service.

### 8.1 Pessimistic reservation (PostgreSQL / MySQL InnoDB)

```sql
-- Bound how long we are willing to block. PostgreSQL default is 0 = wait forever,
-- which is how one slow transaction exhausts the whole connection pool.
SET lock_timeout = '3s';                      -- MySQL: SET innodb_lock_wait_timeout = 3;

BEGIN;

-- FOR UPDATE takes an EXCLUSIVE row lock on book 101 and holds it until COMMIT.
-- Any other transaction doing FOR UPDATE / UPDATE / DELETE on this row now blocks.
-- Plain SELECTs are unaffected: MVCC readers do not block on writers.
SELECT stock
  FROM books
 WHERE id = 101
   FOR UPDATE;                                -- ← the entire concurrency control

-- The value we just read cannot change under us, so a plain arithmetic update is safe.
-- Guarding with stock > 0 makes the database, not the app, the final authority.
UPDATE books
   SET stock = stock - 1
 WHERE id = 101
   AND stock > 0;                             -- 0 rows affected ⇒ sold out, roll back

INSERT INTO orders (customer_id, book_id, qty, status)
VALUES (42, 101, 1, 'reserved');

COMMIT;                                       -- locks released here, and ONLY here
```

Multi-row version, deadlock-free by construction:

```sql
BEGIN;
-- ORDER BY id makes the lock acquisition order global and deterministic.
-- Every transaction in the system that locks multiple books must use the SAME order.
-- Without this, T1 locking (101,202) and T2 locking (202,101) will deadlock.
SELECT id, stock
  FROM books
 WHERE id IN (101, 202, 303)
 ORDER BY id                                  -- ← the deadlock prevention
   FOR UPDATE;
-- ... updates ...
COMMIT;
```

Queue claiming with `SKIP LOCKED`:

```sql
BEGIN;
-- SKIP LOCKED returns only rows no other transaction currently holds.
-- Ten workers running this concurrently receive ten disjoint batches, with no broker.
SELECT id, payload
  FROM email_jobs
 WHERE status = 'pending'
 ORDER BY created_at                          -- oldest first = fair
 LIMIT 10
   FOR UPDATE SKIP LOCKED;                    -- ← never blocks, never double-claims

UPDATE email_jobs SET status = 'processing', claimed_at = now()
 WHERE id = ANY(:claimed_ids);
COMMIT;
-- Send the emails AFTER commit, keyed by an idempotency key (Lesson 17),
-- because a crash between COMMIT and send must not lose or duplicate the email.
```

### 8.2 Optimistic reservation with a version column

```sql
-- One-off schema change. BIGINT, not a timestamp: integers have no clock-skew,
-- no resolution limit, and no risk of time moving backwards.
ALTER TABLE books ADD COLUMN version BIGINT NOT NULL DEFAULT 1;
```

```sql
-- Step 1: read with NO lock. Nothing blocks; nothing is held across the think time.
SELECT stock, version FROM books WHERE id = 101;        -- → stock=12, version=7

-- Step 2: compare-and-swap. The WHERE clause is the "compare"; the SET is the "swap".
-- version = version + 1 must be in the SET, or the next CAS has nothing to detect.
UPDATE books
   SET stock   = 11,                          -- value computed by the app from stock=12
       version = version + 1                  -- ← makes this write visible to other CASes
 WHERE id      = 101
   AND version = 7                            -- ← fails if anyone committed since our read
   AND stock   > 0;                           -- belt-and-braces invariant

-- The database returns an AFFECTED ROW COUNT, not an error:
--   1 → we won.  0 → someone else committed first (or stock hit 0). MUST be checked.
```

```python
# Illustrative retry loop. Policy numbers (4 attempts, 10 ms base) are examples.
import random, time

MAX_ATTEMPTS = 4          # bounded: unbounded retry under contention is an outage
BASE_MS      = 10         # first backoff ceiling

def reserve_copy(conn, book_id, qty=1):
    for attempt in range(MAX_ATTEMPTS):
        # Re-READ on every attempt. Retrying the UPDATE alone would reapply a value
        # computed from stale data — the lost update you were trying to prevent.
        stock, version = conn.query_one(
            "SELECT stock, version FROM books WHERE id = %s", book_id)

        if stock < qty:
            return "OUT_OF_STOCK"             # a real answer, not a conflict

        with conn.transaction():
            rows = conn.execute(
                """UPDATE books
                      SET stock = %s, version = version + 1
                    WHERE id = %s AND version = %s""",
                stock - qty, book_id, version)

            if rows == 1:                     # CAS won
                conn.execute(
                    "INSERT INTO orders (book_id, qty, status) VALUES (%s,%s,'reserved')",
                    book_id, qty)
                return "RESERVED"             # transaction commits on context exit

            # rows == 0 ⇒ lost the race. The context manager rolls back.

        # Exponential backoff with FULL JITTER (Lesson 17). The random lower bound is
        # what breaks the retry convoy: without it every loser retries in lockstep.
        time.sleep(random.uniform(0, BASE_MS * (2 ** attempt)) / 1000.0)

    # Give up rather than retry forever. 409 tells the client the state moved on.
    return "CONFLICT"                         # → HTTP 409
```

### 8.3 The same thing at the HTTP layer (Lesson 6)

```python
@app.put("/api/books/<int:book_id>")
def update_book(book_id):
    # Refusing the request when the header is absent makes the API safe by default:
    # a blind PUT would be last-writer-wins and would silently destroy a concurrent edit.
    if_match = request.headers.get("If-Match")
    if not if_match:
        return "", 428                        # 428 Precondition Required

    expected = int(if_match.strip('"').lstrip("v"))   # ETag was 'v7' → 7

    rows = db.execute(
        """UPDATE books SET price = %s, version = version + 1
            WHERE id = %s AND version = %s""",
        request.json["price"], book_id, expected)

    if rows == 0:
        return "", 412                        # 412 Precondition Failed: client must re-GET

    return "", 204, {"ETag": f'"v{expected + 1}"'}    # hand back the new version
```

### 8.4 Timeout configuration worth setting on day one

```ini
# PostgreSQL (postgresql.conf or per-session). Values are typical starting points.
lock_timeout                          = 3s     # cap how long a statement blocks on a lock
statement_timeout                     = 10s    # cap total statement runtime
idle_in_transaction_session_timeout   = 30s    # kill txns that opened and then went idle
deadlock_timeout                      = 1s     # how long before the cycle check runs

# MySQL InnoDB
innodb_lock_wait_timeout              = 5      # seconds; default 50 is far too long
innodb_deadlock_detect                = ON     # OFF only for extreme-throughput tuning,
                                               # and then you MUST rely on lock wait timeout
```

## 9. Numerical example

**Goal:** compute committed throughput under both schemes at conflict rates of 1%, 10% and 50%, and derive the crossover point.

**Assumptions (illustrative; all numbers are typical orders of magnitude, not measurements):**

| Symbol | Meaning | Value |
|---|---|---|
| `W` | critical-section window: time from the read to the commit | 5 ms |
| `L` | pessimistic lock acquire + release overhead per transaction | 0.5 ms |
| `B` | mean backoff after a failed optimistic attempt | 10 ms |
| `H` | hot set: distinct rows receiving the contended writes | 50 rows |
| `λ` | offered load, committed transactions required per second | varies |
| `A` | attempt rate: transactions *started* per second (≥ λ under OCC) | varies |

We assume conflicts are uniformly spread over the hot set and that attempt arrivals are Poisson. We ignore CPU saturation so that the conflict feedback is isolated.

### Step 1 — express conflict rate as a function of attempt rate

Concurrent attempts in flight = `A × W`. Spread over `H` rows, the expected number of *other* attempts overlapping a given attempt on the same row is `A × W / H`. Under Poisson arrivals, the probability of at least one such overlap is:

```
p = 1 − e^(−A·W/H)
```

With `W = 0.005 s` and `H = 50`:  `A·W/H = A × 0.005 / 50 = A / 10,000`

```
p = 1 − e^(−A/10,000)
```

### Step 2 — optimistic goodput

Only the non-conflicting fraction commits, so committed throughput (**goodput**) is:

```
G_opt = A × (1 − p) = A × e^(−A/10,000)
```

Evaluate at the three required conflict rates. Invert step 1 to get `A` for each `p`:

**p = 1%:**
```
A/10,000 = −ln(1 − 0.01) = −ln(0.99)   = 0.010050
A        = 100.5 attempts/sec
G_opt    = 100.5 × 0.99                = 99.5 commits/sec
wasted   = 100.5 − 99.5                = 1.0 attempts/sec  (1.0% of capacity burned)
```

**p = 10%:**
```
A/10,000 = −ln(0.90)                   = 0.105361
A        = 1,053.6 attempts/sec
G_opt    = 1,053.6 × 0.90              = 948.2 commits/sec
wasted   = 1,053.6 − 948.2             = 105.4 attempts/sec (10% burned)
```

**p = 50%:**
```
A/10,000 = −ln(0.50)                   = 0.693147
A        = 6,931.5 attempts/sec
G_opt    = 6,931.5 × 0.50              = 3,465.7 commits/sec
wasted   = 6,931.5 − 3,465.7           = 3,465.8 attempts/sec (50% burned)
```

### Step 3 — find where optimistic peaks and collapses

Maximise `G_opt = A·e^(−A/10,000)`:

```
dG/dA = e^(−A/10,000) × (1 − A/10,000) = 0
      ⇒ A = 10,000 attempts/sec
      ⇒ p = 1 − e^(−1) = 0.632  (63.2%)
      ⇒ G_max = 10,000 × e^(−1) = 3,678.8 commits/sec
```

**Past 63.2% conflict, adding load reduces committed throughput.** Check:

```
p = 80%:  A/10,000 = −ln(0.20) = 1.6094 → A = 16,094 → G = 16,094 × 0.20 = 3,218.8
p = 95%:  A/10,000 = −ln(0.05) = 2.9957 → A = 29,957 → G = 29,957 × 0.05 = 1,497.9
p = 99%:  A/10,000 = −ln(0.01) = 4.6052 → A = 46,052 → G = 46,052 × 0.01 =   460.5
```

Goodput at 99% conflict is **less than one eighth** of the peak, while the database is doing **4.6× more work**. That is the collapse: the retries are the load. This is a congestion-collapse curve, structurally identical to the one in Lesson 17 that motivates backoff, and the mitigation is the same — reduce the attempt rate.

### Step 4 — pessimistic throughput

Pessimistic serialises per row, so each hot row sustains `1/(W + L)` commits/sec:

```
per-row ceiling = 1 / (0.005 + 0.0005) = 1 / 0.0055 = 181.8 commits/sec
fleet ceiling   = 181.8 × 50 rows       = 9,090 commits/sec
```

This ceiling is **flat** — it does not depend on `p`, because pessimistic performs zero wasted work by construction. Excess load becomes queueing *latency*, not lost *throughput*.

### Step 5 — side-by-side

| Conflict rate `p` | Optimistic goodput | Pessimistic ceiling | Wasted work (OCC) | Winner |
|---|---|---|---|---|
| 1% | 99.5 /s (load is only 100 /s) | 9,090 /s | 1% | **Optimistic** — no lock overhead, no blocking |
| 10% | 948 /s | 9,090 /s | 10% | **Optimistic**, marginally — overhead now visible |
| 50% | 3,466 /s | 9,090 /s | 50% | **Pessimistic** — 2.6× more throughput |
| 63.2% (OCC peak) | 3,679 /s | 9,090 /s | 63% | **Pessimistic** — 2.5× |
| 99% | 461 /s | 9,090 /s | 99% | **Pessimistic** — 20× |

### Step 6 — derive the crossover analytically

Compare *cost per committed transaction*. Under OCC, expected attempts per commit is `1/(1−p)`, each failed attempt wasting `W` of work plus `B` of backoff:

```
Cost_opt  = W/(1−p) + (p/(1−p)) × B
Cost_pess = W + L                       (ignoring queueing, i.e. at low utilisation)
```

Set them equal:

```
W/(1−p) + pB/(1−p) = W + L
W + pB             = (W + L)(1 − p)
W + pB             = W + L − pW − pL
pB + pW + pL       = L
p* = L / (B + W + L)
```

Substituting `L = 0.5 ms`, `B = 10 ms`, `W = 5 ms`:

```
p* = 0.5 / (10 + 5 + 0.5) = 0.5 / 15.5 = 0.0323  →  3.2%
```

**Below ~3% conflict, optimistic is cheaper. Above it, pessimistic is.**

Sensitivity — the crossover is dominated by the backoff `B`, which you control:

```
B = 10 ms (jittered backoff):   p* = 0.5 / 15.5 = 3.2%
B =  1 ms (immediate retry):    p* = 0.5 /  6.5 = 7.7%
B = 50 ms (conservative):       p* = 0.5 / 55.5 = 0.9%
```

And on `L`, which the engine controls — a database with cheap row locks (`L = 0.1 ms`) pushes `p*` down to `0.1/15.1 = 0.66%`, favouring pessimistic almost everywhere; a system where "locking" means a network round trip to a coordination service (`L = 2 ms`, see Phase 4, Topic 15) pushes `p*` up to `2/17 = 11.8%`, favouring optimistic.

**The practical rule:** measure `p` per table. Below ~5%, optimistic. Above ~20%, pessimistic. Between, measure both — and seriously consider that a sustained `p` above 20% means the real problem is a hot row that should be sharded (§7.1), not a concurrency-control choice.

## 10. Trade-offs

| Dimension | Pessimistic | Optimistic |
|---|---|---|
| Cost when there is no conflict | Lock acquire/release on **every** transaction (`L`, typically 0.1–1 ms) | **Zero** |
| Cost when there is a conflict | Waiting — latency rises, throughput preserved | Wasted work — throughput falls, and the waste raises `p` further |
| Degradation shape | Graceful: queue grows linearly | Catastrophic: goodput peaks then falls (§9 step 3) |
| Throughput ceiling on one row | `1/(W+L)` — hard and predictable | `1/(e·W/H)`-shaped — soft and load-dependent |
| Latency distribution | Long tail from queueing; p50 good, p99 bad under load | Bimodal: fast on first try, `k × (W+B)` on retry |
| Failure modes | Deadlock, lock wait timeout, lock escalation, connection-pool exhaustion | Starvation (one transaction losing repeatedly), retry convoy, `409`s surfaced to users |
| Works across request boundaries | **No** — you cannot hold a database lock across two HTTP requests | **Yes** — this is `ETag`/`If-Match` (Lesson 6) |
| Works across heterogeneous resources | Only with a distributed lock (Phase 4, Topic 15) | Yes, if every resource supports CAS |
| Application complexity | Low: `FOR UPDATE` and consistent lock ordering | Higher: version column, row-count check, retry loop, backoff, idempotency (Lesson 17) |
| Silent-failure risk | Low — blocking and deadlocks are loud | **High** — ignoring the affected-row count reintroduces lost update with no error |
| Read scalability | Readers unaffected (MVCC), unless you use `FOR SHARE` | Readers unaffected |
| Fairness | Roughly FIFO on the lock queue | Unfair — a slow transaction can lose indefinitely (**starvation**) |
| Observability | Excellent: `pg_locks`, `pg_stat_activity`, `SHOW ENGINE INNODB STATUS` | Must be instrumented by you: count CAS misses per table, or you are blind |

Two asymmetries deserve naming. First, **pessimistic converts contention into latency; optimistic converts it into errors.** Latency degrades a user's experience; a `409` denies it. Second, **pessimistic failures are loud and optimistic failures are silent.** A deadlock throws. A forgotten row-count check throws nothing and quietly corrupts inventory for months.

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| **Deadlock** (two transactions, cyclic lock wait) | One request fails after ~1 s (PostgreSQL `deadlock_timeout`) or ~instantly (InnoDB) with "deadlock detected"; the other succeeds normally | Engine builds the wait-for graph and finds a cycle; logged as SQLSTATE `40P01` / MySQL error 1213; alert on deadlocks/minute | Engine rolls back the victim automatically. App must catch the error and **retry the whole transaction** (it is a legitimate retry, unlike a constraint violation). Permanent fix: consistent lock ordering (§4.3) |
| **Lock wait timeout** (no cycle, just a slow holder) | Request hangs for the timeout then returns `503`/`500`; under load, many requests hang together | `lock_timeout`/`innodb_lock_wait_timeout` fires; `pg_locks` joined to `pg_stat_activity` shows the blocking PID and its query | Statement errors, transaction rolls back, lock released. Fix the *holder*: usually a transaction doing network I/O or user think-time inside `BEGIN…COMMIT` |
| **Lock held across an external call** (payment gateway inside the transaction) | Site-wide stall: every checkout blocks behind one 30 s gateway timeout | Spike in `idle in transaction` sessions; connection-pool wait time climbs; p99 on unrelated endpoints rises | `idle_in_transaction_session_timeout` kills the session and releases locks. Permanent fix: move the external call outside the transaction and make it idempotent (Lesson 17) |
| **Connection-pool exhaustion from blocking** | Every endpoint returns `503`, including ones that touch no contended table | Pool `wait_count`/`wait_time` metrics; "timed out acquiring connection" errors far outnumber database errors | Pool drains once locks release. Prevent with a `lock_timeout` strictly smaller than the pool acquisition timeout, so blocked statements die before the pool does |
| **Lock escalation to a table lock** (SQL Server-style engines) | Entire table unavailable for writes during a bulk `DELETE`/`UPDATE`; unrelated users see timeouts | Lock-escalation events in the engine's event stream; a single `TAB`-granularity lock visible in the lock view | Finishes when the statement finishes. Prevent by chunking the statement into batches below the escalation threshold (e.g. 1,000 rows per commit) |
| **Optimistic conflict storm** (`p` driven past the peak by retries) | Checkout latency balloons, then a flood of `409 Conflict`; database CPU near 100% while committed orders *fall* | Goodput falls while attempt rate rises — the diagnostic signature. Requires a **CAS-miss counter** per table; without it this looks like unexplained CPU | Bounded attempts shed load; jittered backoff breaks the convoy. Real fix: shard the hot row (§7.1) or switch that path to pessimistic |
| **Ignored affected-row count** | Nothing. Orders are accepted, inventory silently goes negative, and it is discovered weeks later at stock-take | Not detected by the database at all. Caught only by a `CHECK (stock >= 0)` constraint, a reconciliation job, or an auditor | Database-level `CHECK` turns the silent corruption into a loud error. The code must be fixed to branch on the row count |
| **Starvation under OCC** (one long transaction always loses) | A specific operation — usually a large batch or report-backed update — *never* succeeds while short transactions always do | Per-operation retry-exhaustion rate; one endpoint at 100% `409` while others are at 0% | Give the starving operation a pessimistic path (`FOR UPDATE`), or run it in a maintenance window, or split it into smaller transactions |
| **Timestamp used as the version column** | Two updates inside the same clock tick both "win"; one is silently lost. Or an NTP step backwards makes every CAS fail | Essentially undetectable in logs; shows up as inconsistent data | Replace `updated_at` with a monotonically incremented `BIGINT version`. There is no safe way to keep the timestamp under high per-row update rates |
| **Crash after commit, before the side effect** (email sent / card charged) | Order is reserved but no confirmation email, or the reverse | Reconciliation between the orders table and the side-effect ledger | Idempotency keys + an outbox table drained by a `SKIP LOCKED` worker (§8.1), so the side effect is retried at-least-once and deduplicated |

## 12. Alternatives

| Approach | How it works | Conflict handling | Best when | Main cost |
|---|---|---|---|---|
| **Pessimistic locking** | `SELECT … FOR UPDATE`; X lock held to commit | Prevented | `p` above ~20%; short transactions; single database | Latency under queueing; deadlocks; connection held while blocked |
| **Optimistic (version CAS)** | Read version, `UPDATE … WHERE version = v`, check row count | Detected and retried | `p` below ~5%; long think-time; across request boundaries | Wasted work; retry logic; silent failure if row count ignored |
| **SERIALIZABLE isolation (SSI)** | PostgreSQL tracks read/write dependencies and aborts transactions that would create a non-serialisable cycle (Lesson 50) | Detected by the engine | You cannot enumerate every invariant by hand; write skew is a real risk (Lesson 51) | Still needs a retry loop (SQLSTATE `40001`); 10–30% overhead typical; predicate tracking memory |
| **Database constraints** | `CHECK (stock >= 0)`, `UNIQUE`, exclusion constraints | Prevented by the engine, declaratively | The invariant is expressible in one row or one index | Cannot express cross-row invariants (the classic doctor-on-call write-skew case from Lesson 51) |
| **Atomic single-statement update** | `UPDATE books SET stock = stock - 1 WHERE id = 101 AND stock > 0` | Prevented — read and write are one atomic statement | The new value is a pure function of the old value; no app-side decision needed | Only works for simple arithmetic; cannot express "if stock > 0 then also insert an order and call pricing" |
| **Serialise through one actor** | Route all writes for a key to a single-threaded owner (actor model, Kafka partition keyed by `book_id`) | Impossible by construction — one writer per key | Extreme contention on well-partitioned keys; event-driven systems (Phase 2, Topic 18) | Availability of that partition; ordering and replay complexity; eventual consistency for readers |
| **Hot-row sharding / bucketing** | Split one contended row into N rows; pick randomly with `SKIP LOCKED` (§7.1) | Reduces `p` by N× rather than handling it | One row is the bottleneck and the quantity is fungible | "Sold out" becomes a `SUM`; buckets drift and need rebalancing |
| **CRDTs / commutative operations** | Design the operation so order does not matter (counters, sets) | No conflict exists | Counters, likes, analytics, offline-first clients | Only works for operations that genuinely commute; inventory does **not** — you cannot allow stock below zero |
| **Distributed lock / lease** | Lock lives in etcd, ZooKeeper, or Redis with a TTL and a fencing token | Prevented, probabilistically | The protected resource is not a single database | Clock assumptions, lease expiry during use, fencing tokens. See Phase 4, Topic 15 |
| **Queue the work** | Enqueue the intent; a worker applies changes serially per key | Prevented by serialisation | Writes can be asynchronous | User gets "pending" rather than a confirmed result; needs status polling or push |

The choices are not exclusive. The production BookNook checkout combines four of them: an atomic guarded `UPDATE`, a `CHECK` constraint as a backstop, bucketed hot rows, and `SKIP LOCKED` claiming — with optimistic CAS on the admin paths.

## 13. When to use it

**Use pessimistic (`FOR UPDATE`) when:**

1. **Measured conflict rate on the row is above roughly 20%.** Flash sales, limited editions, last-seat booking, a per-account balance on a busy account. Above the crossover derived in §9, pessimistic wins on throughput *and* on user experience, because waiting beats failing.
2. **The invariant spans multiple rows and must hold exactly.** Transferring stock between bins, moving money between accounts, decrementing a budget while inserting a spend record. Lock all participating rows in a deterministic order (§8.1) and the invariant is trivially safe.
3. **You are implementing a work queue.** `FOR UPDATE SKIP LOCKED` is the cleanest at-least-once claiming mechanism available without adding a broker. It never blocks and never double-claims.
4. **The cost of a retry is high or the retry is not safe.** If the transaction's work includes something expensive to recompute, or the operation is awkward to make idempotent, prevention beats detection.
5. **The transaction is short and entirely inside the database.** Sub-10 ms, no network calls, no user think-time. These are exactly the conditions under which locks are cheap and deadlock windows are tiny.

**Use optimistic (version CAS) when:**

1. **Measured conflict rate is below roughly 5%.** The overwhelming majority of tables: user profiles, addresses, book metadata, settings, per-customer carts. You pay literally nothing for correctness.
2. **The read and the write are in different requests.** Any "load a form, edit, save" flow. Human think-time is seconds to minutes; a lock cannot survive it and an abandoned tab must not wedge a row. This is also the correct answer for mobile clients on flaky networks.
3. **You want the API to be safe by default.** `If-Match` + `412` (Lesson 6, §8.3) gives every API consumer conflict detection without any server-side session state (Lesson 2).
4. **The client can meaningfully resolve the conflict.** When a `0`-row result lets you show "this record changed; here is the diff", the conflict becomes a *feature* rather than an error.
5. **You are writing to something that only supports CAS.** DynamoDB conditional writes, S3 `If-Match` on `PutObject`, etcd compare-and-swap, Git refs. Optimistic is the only available mechanism.

## 14. When not to use it

**Do not use pessimistic when:**

1. **The lock would be held across user think-time or an external network call.** A lock held for the duration of a payment-gateway call means your checkout throughput equals the gateway's. A lock held while a human fills a form means one abandoned tab blocks a row until the timeout. Model long-lived business holds as *rows with an `expires_at`* (§7.2), not as database locks.
2. **Conflict rates are genuinely tiny and transaction volume is enormous.** At `p = 0.1%` across 40,000 writes/sec, pessimistic costs you 40,000 lock acquire/release pairs per second to prevent 40 conflicts. That is pure overhead and extra lock-table pressure.
3. **You cannot establish a global lock ordering.** If the set of rows to lock depends on data discovered mid-transaction, deterministic ordering may be impossible, and deadlock becomes routine rather than exceptional. Prefer optimistic, or restructure so the key set is known up front.
4. **The coordinating resource is not one database.** Locks across services are distributed locks, with all the failure modes deferred to Phase 4, Topic 15. Do not fake one with a database row plus a sleep loop.

**Do not use optimistic when:**

1. **Conflict rate exceeds roughly 20%, and especially when it is near 100%.** §9 shows goodput at `p = 99%` is one eighth of the peak while the database does 4.6× the work. You have built a load amplifier.
2. **A conflict has an unacceptable user-visible consequence.** Telling a customer at the payment step that their chosen seat is gone is worse than making them wait 400 ms for a lock.
3. **The retry has non-idempotent side effects you cannot isolate.** If a retried transaction can send a second email or issue a second charge, the retry loop is a correctness bug, not a performance tuning knob (Lesson 17).
4. **Your team will not reliably check the affected-row count.** This is an honest engineering constraint, not a slight. Pessimistic fails loudly; optimistic fails silently. If the codebase has many write paths maintained by many people, the mechanism that cannot be forgotten is the safer default.
5. **The operation is a long batch competing with short transactions.** It will starve: by the time it commits, something has always changed. Give it a pessimistic path or a quiet window.

## 15. Common mistakes

**Beginner mistakes**

1. **Ignoring the affected-row count.** The single most common and most damaging error. `UPDATE … WHERE version = 7` returning `0` is not an error; it is a *result*. Unchecked, the lost update returns with no symptom.
2. **Retrying only the `UPDATE`, not the whole transaction.** The second CAS must be computed from a *fresh* read. Re-issuing the same `UPDATE` with a bumped version writes a value derived from stale data — precisely the bug you set out to fix.
3. **Thinking `SELECT … FOR UPDATE` protects a row you did not select.** `FOR UPDATE` locks the rows the query *returned*. If the row does not exist yet, there is nothing to lock, and two transactions can both insert — you need a `UNIQUE` constraint or `INSERT … ON CONFLICT` for that case.
4. **Retrying without backoff or jitter.** Immediate retry under contention is the retry convoy. Lesson 17's full-jitter formula exists for exactly this.
5. **Retrying forever.** Unbounded retries turn a transient hot spot into sustained congestion collapse.
6. **Using `updated_at` as the version.** Clock resolution, skew, and backward steps all break it (§4.4, §11).
7. **Leaving `lock_timeout` at the default.** PostgreSQL's default is infinite; InnoDB's is 50 seconds. Both are far longer than any web request should live, and both end in connection-pool exhaustion rather than a clean error.
8. **Assuming a higher isolation level removes the need for retries.** `SERIALIZABLE` in PostgreSQL *increases* the need — it aborts transactions with `40001` and expects you to retry (Lesson 50).
9. **Doing a payment call inside `BEGIN … COMMIT`.** Your transaction duration — and therefore every lock it holds — is now the third party's p99.
10. **Confusing a deadlock with a lock wait timeout.** They have different causes (cycle versus slow holder) and different fixes (lock ordering versus shortening the holder). Treating one as the other wastes days.

**Experienced-engineer mistakes**

1. **Choosing by preference instead of by measurement.** "We use optimistic locking" is not an architecture; it is a habit. The right answer is per-table and follows from `p` (§9). Most real systems should use both.
2. **Not instrumenting CAS misses.** Pessimistic contention appears in `pg_locks` for free. Optimistic contention appears *nowhere* unless you emit a counter on the `rows == 0` branch. Teams run for years with no idea what their conflict rate is, which makes §9 unanswerable.
3. **Missing the retry-feedback loop.** Engineers reason linearly ("10% conflicts means 10% more work") and miss that retries *raise* `p`, which raises retries. The system is stable right up to the peak in §9 step 3 and unstable immediately after — so load tests at 60% of peak look fine.
4. **Letting ORM lazy loading extend transaction scope.** A lazily loaded association triggers a query inside an open transaction that was supposed to have ended, silently stretching lock hold time by hundreds of milliseconds.
5. **Relying on an ORM's `@Version` without understanding its scope.** Most ORMs only bump the version on fields they manage; a raw-SQL write path or a bulk `UPDATE` bypasses it entirely, so some writers participate in the protocol and others do not.
6. **Inconsistent lock ordering across teams.** Service A locks `books` then `inventory`; service B locks `inventory` then `books`. The deadlock only appears at peak traffic, months later. Lock ordering must be a written, enforced convention.
7. **Not realising `SKIP LOCKED` silently skips.** Used on a checkout path instead of a queue, it returns "no stock" when stock exists but is momentarily locked. Correct for queues, a correctness bug elsewhere.
8. **Disabling InnoDB deadlock detection for throughput without reducing the lock wait timeout.** Detection off means cycles are resolved only by timeout; with `innodb_lock_wait_timeout = 50`, a deadlock now stalls two connections for 50 seconds instead of failing in 1 ms.
9. **Treating a persistently high conflict rate as a tuning problem.** `p > 50%` on a row is a *data model* signal. The fix is bucketing, partitioning the key space, or moving to an append-only event log — not a different retry policy.
10. **Building a distributed lock on a Redis `SETNX` with a TTL and assuming it is safe.** Lease expiry during execution means two holders can believe they hold it. Without a fencing token checked by the protected resource, it is a performance optimisation, not a correctness mechanism (Phase 4, Topic 15).
11. **Forgetting that a lock is released at `COMMIT`, not at the end of the statement.** Engineers reason about the `UPDATE` and forget the 40 ms of application code after it, during which the lock is still held.
12. **Combining optimistic retries with non-idempotent outbound effects, then adding a message queue in the middle.** Now the duplicate is asynchronous and invisible. The outbox pattern plus an idempotency key is the fix (Lesson 17).

## 16. Interview perspective

This topic appears in two shapes. The first is explicit: *"How do you prevent two customers buying the last item?"* The second is implicit, hidden inside almost every design question involving inventory, booking, balances, or counters — and interviewers are watching for whether you notice it unprompted.

**The answer that scores well, in order:**

1. **Name the anomaly.** "This is a lost update (Lesson 51) caused by a read-modify-write that is not atomic at READ COMMITTED."
2. **Give both mechanisms precisely.** Pessimistic locks before reading and prevents; optimistic validates at commit and detects. Say what each costs.
3. **Choose as a function of the conflict rate, with a number.** "Across the catalogue, `p` is well under 1%, so optimistic CAS on a version column. On the flash-sale SKU, `p` approaches 100%, so pessimistic `SELECT … FOR UPDATE` on that path — the two paths coexist." This single sentence separates strong candidates from the rest, because it shows the choice is measured, not preferred.
4. **Show you know the collapse.** "Optimistic goodput peaks at around 63% conflict and falls after that, because retries are themselves load." Being able to sketch that curve is memorable.
5. **Volunteer the deadlock discussion before you are asked.** Two-transaction example, wait-for graph cycle detection, prevention by consistent lock ordering. Mention that PostgreSQL waits `deadlock_timeout` (≈1 s) before checking, so a deadlock has a latency cost even when handled.
6. **Mention retry policy explicitly.** Bounded attempts, exponential backoff, full jitter, idempotency key — and reference that this is the same machinery as Lesson 17.
7. **Reach for the data model under extreme contention.** "At 1,200 attempts/sec on one row, neither scheme gets past ~200/sec. I would shard the stock into 50 buckets and claim with `FOR UPDATE SKIP LOCKED` for ~9,500/sec." Interviewers at senior level are specifically testing whether you stop tuning and start redesigning.
8. **Extend to the API and the distributed case.** `ETag`/`If-Match`/`412` (Lesson 6); and if the resource is not one database, name distributed locks and fencing tokens as a known-hard area rather than hand-waving.

**Red flags interviewers watch for:**

- Saying "I'd use a transaction" and stopping. A transaction alone does not prevent lost update at READ COMMITTED, which is the default on both PostgreSQL and MySQL. This is the most common fatal answer.
- Expressing a blanket preference ("optimistic locking is better") with no reference to conflict rate.
- Describing optimistic concurrency without mentioning the affected-row check — it reveals the candidate has read about it rather than shipped it.
- Retry logic with no bound, no backoff, and no jitter.
- Not knowing that a lock is held until `COMMIT`.
- Proposing an application-level mutex or an in-process lock to protect a database row across a horizontally scaled fleet — it protects nothing once there are two app servers.
- Suggesting `SERIALIZABLE` as a zero-cost fix, unaware it also requires a retry loop.
- Placing a payment-gateway call inside an open transaction.
- Reaching for Redis as a distributed lock without mentioning lease expiry or fencing tokens.
- Being unable to explain how a deadlock is *detected*, only that "the database handles it".
- Treating `SKIP LOCKED` as a general contention fix rather than a queue-claiming primitive.
- Never asking what the conflict rate or write volume actually is. The question is unanswerable without it, and good candidates ask within the first minute.

## 17. Knowledge check

**Conceptual**

1. Pessimistic concurrency control performs zero wasted work while optimistic performs none when there is no conflict. Explain precisely which cost each scheme pays in the *uncontended* case, and why that makes the conflict rate — rather than engineering preference — the deciding variable.
2. An optimistic `UPDATE … WHERE version = 7` that affects zero rows does not raise a database error. Explain why the database is behaving correctly by staying silent, what the application must therefore do, and what class of data corruption results if it does not.
3. Describe how a database detects a deadlock using a wait-for graph, and explain why consistently ordering lock acquisition prevents deadlocks entirely rather than merely making them rarer. Then state the difference between a deadlock and a lock wait timeout, including why the two require different fixes.

**Practical scenario**

4. BookNook's admin tool lets a merchandiser open a book's pricing form, review competitor data for two to three minutes, and click Save. Separately, an automated repricing job updates the same `books` rows every 15 minutes, touching roughly 8% of the catalogue each run. Merchandisers report that their edits "sometimes vanish". Design the concurrency control for both writers. State which scheme each path uses and why, what schema change is required, what the merchandiser sees when a conflict occurs, what HTTP status code the API returns, and how you would instrument the system to measure the actual conflict rate so the choice can be revisited with data.

**Calculation**

5. BookNook's `gift_cards` table has a critical-section window `W = 8 ms`, pessimistic lock overhead `L = 0.4 ms`, and a hot set of `H = 20` heavily used corporate gift-card rows. The optimistic retry policy uses a mean backoff of `B = 25 ms`.
   (a) Using `p = 1 − e^(−A·W/H)`, compute the attempt rate `A` required to sustain a conflict rate of 15%, and the resulting optimistic goodput `A × (1 − p)`.
   (b) Compute the attempt rate at which optimistic goodput is maximised, the conflict rate at that point, and the peak goodput.
   (c) Compute the flat pessimistic ceiling `H / (W + L)`.
   (d) Using `p* = L / (B + W + L)`, compute the crossover conflict rate, and state which scheme you would deploy if measurement showed the real conflict rate to be 6%.

**Progress checklist**

```
Phase 1 — Foundations .......................................... COMPLETE (Lessons 1–17)
Phase 2 — Scaling .............................................. COMPLETE (Lessons 18–39)

Phase 3 — Data & Storage
  [x]  1. Relational databases and the relational model .............. Lesson 40
  [x]  2. Schema design, keys, and normalisation ..................... Lesson 41
  [x]  3. Joins and join algorithms .................................. Lesson 42
  [x]  4. SQL versus NoSQL ........................................... Lesson 43
  [x]  5. Key-value and document stores .............................. Lesson 44
  [x]  6. Wide-column stores ........................................ Lesson 45
  [x]  7. Graph databases ............................................ Lesson 46
  [x]  8. Search engines and inverted indexes ........................ Lesson 47
  [x]  9. Choosing a database from access patterns ................... Lesson 48
  [x] 10. ACID ....................................................... Lesson 49
  [x] 11. Isolation levels ........................................... Lesson 50
  [x] 12. Read phenomena: lost update, write skew, phantoms .......... Lesson 51
  [x] 13. Optimistic versus pessimistic concurrency control .......... Lesson 52  ◀ you are here
  [ ] 14. Database indexes
  [ ] 15. B-tree, hash, composite, covering, and secondary indexes
  [ ] 16. Query planning and EXPLAIN
  [ ] 17. Connection pooling
  [ ] 18. Leader–follower replication
  [ ] 19. Multi-leader and leaderless replication
  [ ] 20. Read replicas and replication lag
  [ ] 21. Partitioning and sharding strategies
  [ ] 22. Rebalancing and resharding
  [ ] 23. Caching database results
  [ ] 24. Schema migrations and evolution
```

**NEXT LESSON: Phase 3, Topic 14 — Database Indexes**

---

# Lesson 53 — Database Indexes

*Phase 3, Topic 14*

## 1. Simple definition

An **index** is a separate, ordered data structure that the database maintains alongside a table, mapping *column values* to *locations of rows*, so that finding rows by those values does not require reading the whole table.

Terms defined before use:

- **Heap** (or heap table): the unordered file where the table's rows actually live. "Heap" literally means a pile — rows sit wherever there was free space when they were inserted. There is no ordering guarantee whatsoever.
- **Page** (or block): the fixed-size unit the database reads from and writes to storage. **8 KB in PostgreSQL, 16 KB in MySQL InnoDB** (both configurable at build/instance creation, rarely changed). The database never reads one row from disk; it reads the whole page containing it. This single fact drives every number in this lesson.
- **Tuple**: one physical row version inside a page.
- **TID / row pointer** (PostgreSQL calls it a `ctid`, Oracle a `rowid`): the physical address of a tuple, written as `(page_number, item_number)` — "page 48,112, slot 7". This is what an index entry actually stores: **not** the row, and **not** a byte offset, but a page number plus a slot number within that page's line pointer array.
- **Full table scan** (sequential scan): read every page of the heap from the first to the last and test every row against the predicate. Cost is proportional to the *table size*, completely independent of how many rows match.
- **Index scan**: descend the index to find matching entries, then follow each TID to fetch the row from the heap. Cost is proportional to the *number of matching rows* plus the index height.

The fundamental trade-off, stated once and never softened:

> **An index makes reads matching its columns dramatically faster, makes every write to the table slower, and consumes storage and memory that the table itself would otherwise use.**

An index is not free, not magic, and not always a win. Every index is a bet that the reads it accelerates are worth more than the writes it slows. Making that bet deliberately, with numbers, is the skill this lesson teaches.

## 2. Why it exists

BookNook's `orders` table has 50 million rows and occupies about 10 GB (§9 derives this). A customer opens "My Orders":

```sql
SELECT id, placed_at, total_cents FROM orders WHERE customer_id = 90210;
```

Without an index, the database has exactly one option: read all 1.25 million pages of the heap, test `customer_id` on each of the 50 million rows, and return the ~20 that match. At a typical 500 MB/s sequential read rate that is **20 seconds** — to return 20 rows. And it is 20 seconds *per concurrent request*, so ten customers viewing their orders simultaneously saturate the disk and the latency becomes 200 seconds.

With a B-tree index on `customer_id`, the same query reads roughly **24 pages** and completes in about **2.4 ms** (§9). Same data, same hardware, same SQL. The difference is a data structure that costs about 1.1 GB and roughly 11% of insert throughput.

Indexes exist because of a physical asymmetry that no amount of hardware fixes: **storage is read in pages, and finding an arbitrary value in an unordered pile of pages requires reading all of them.** Sorting the data would fix lookups on one column and break lookups on every other. An index is the resolution — keep the heap unordered and cheap to append to, and maintain *separate* ordered structures, one per access pattern you care about.

The second reason indexes exist is **ordering**. `ORDER BY placed_at DESC LIMIT 20` without an index requires sorting 50 million rows (a disk-based external sort, tens of seconds) to throw away 49,999,980 of them. With an index on `placed_at`, the database walks the index backwards and stops after 20 entries. An index is a *pre-sorted* structure, and sorting is the second thing it gives you for free.

The third is **uniqueness enforcement**. A `UNIQUE` constraint is implemented *as* a unique index — checking "does this email already exist?" on every insert would otherwise be a full scan.

## 3. Real-world analogy

**The index at the back of a textbook.**

The book's pages are the heap: content sits in whatever order the author wrote it. To find every mention of "mitochondria" you would otherwise read all 900 pages. The index at the back is alphabetically sorted, so you binary-search to "M", find `mitochondria … 142, 318, 509`, and turn directly to three pages. You read four pages (one index page plus three content pages) instead of nine hundred.

The properties map precisely:

- The index is **sorted**; the book is not.
- The index stores **page numbers, not content** — exactly what a TID is.
- The index **takes up extra pages** in the book — storage cost.
- If the author inserts a new chapter and everything shifts, the **index must be rebuilt** — write cost.
- Looking up "the" in the index is useless because it appears on every page — **low selectivity**.
- If the index entry itself contains everything you needed (a glossary entry giving the definition inline), you never turn to the content page at all — that is an **index-only scan**.

**Where the analogy breaks down:**

| Textbook index | Database index |
|---|---|
| Built once, at publication | Maintained **synchronously on every INSERT, UPDATE and DELETE**, inside the same transaction. The write does not complete until every index is updated |
| One index per book | Typically 3–8 per table, each multiplying the write cost (§9) |
| A sorted list you scan linearly | A **B-tree**: a balanced multi-level tree, typically 3–4 levels deep for 50 M rows, so a lookup is 3–4 page reads, not a scan of the index |
| You always use the index | The **query planner decides**, per execution, using statistics. It will deliberately ignore your index and scan the whole table when that is cheaper (§4.3) — and it is often right |
| Page numbers never change | Rows physically move (PostgreSQL rewrites a row on `UPDATE`; `VACUUM FULL` and `CLUSTER` relocate everything), so TIDs change and indexes must be updated or rebuilt |
| The index is always accurate | The planner relies on **statistics** that are sampled and stale between `ANALYZE` runs. A wrong estimate picks the wrong plan, which is the single most common cause of "the query was fast yesterday" |
| Entries are removed when the topic is removed | Deleted index entries leave **dead space (bloat)** until vacuum reclaims it; an index can grow to several times its necessary size while the table shrinks |
| There is one kind of index | B-tree, hash, GIN, GiST, BRIN, bitmap, full-text… each suited to different predicates (Phase 3, Topic 15 — B-tree, hash, composite, covering, and secondary indexes) |

That fourth row matters most and surprises people most. **You do not use an index. You offer one, and the planner chooses.** An index that exists but is never chosen is pure cost.

## 4. Detailed technical explanation

### 4.1 Heap versus index: what is actually stored

A **heap page** (8 KB in PostgreSQL) contains:

```
┌────────────────────────────────────────────────┐
│ page header (24 bytes)                         │
│ line pointer array → (offset,length) per tuple │  ← slot numbers live here
│ ░░░░░░░░ free space ░░░░░░░░                   │
│ tuple N … tuple 2, tuple 1   (grows upward)    │
└────────────────────────────────────────────────┘
```

Rows are appended wherever space exists. There is no order. A `TID` of `(48112, 7)` means "page 48,112, line pointer 7" — the line pointer indirection is what lets the database move a tuple *within* its page (during defragmentation) without invalidating every index entry pointing at it.

A **B-tree index page** contains sorted `(key, pointer)` entries. Leaf pages point into the heap; internal pages point to lower index pages. The tree is **balanced**: every leaf is at the same depth, so every lookup costs exactly the height of the tree in page reads. Height grows logarithmically — a table of 50 million rows and one of 500 million differ by *one* level.

### 4.2 Clustered versus non-clustered — and why PostgreSQL and InnoDB differ fundamentally

- A **clustered index** *is* the table. The rows are stored **inside the index's leaf pages**, in key order. There is no separate heap.
- A **non-clustered (secondary) index** is a separate structure whose leaves hold pointers to rows stored elsewhere.

**PostgreSQL has no clustered indexes at all.** Every index — including the primary key — is non-clustered and points into the heap via a TID. The `CLUSTER` command physically reorders a heap once to match an index; it is a one-off operation that is not maintained, and new rows immediately break the ordering.

**MySQL InnoDB is the opposite: every table is a clustered index on the primary key.** The row data lives in the leaf pages of the primary-key B-tree. If you do not declare a primary key, InnoDB uses the first `NOT NULL UNIQUE` index, and failing that invents a hidden 6-byte `DB_ROW_ID`.

This produces a consequence that bites people constantly:

> **In InnoDB, a secondary index does not store a row pointer — it stores the primary key.** Looking up a row via a secondary index therefore requires *two* B-tree traversals: one through the secondary index to find the primary key, and a second through the clustered index to find the row.

```
InnoDB:  secondary index on email
         ─ descend (3 levels) → leaf entry: ("a@b.com", pk=90210)
         ─ descend clustered PK index (3 levels) → the actual row
         = ~6 page reads

PostgreSQL: index on email
         ─ descend (4 levels) → leaf entry: ("a@b.com", ctid=(48112,7))
         ─ fetch heap page 48112 directly
         = ~5 page reads
```

Three practical consequences of InnoDB's design:

1. **Keep the primary key small.** Every secondary index stores a full copy of it. A 16-byte UUID primary key on a table with five secondary indexes stores 80 bytes of key per row *in addition* to the row itself.
2. **Prefer monotonically increasing primary keys** for insert-heavy tables. A random UUID inserts into random leaf pages of the clustered index, splitting pages across the whole structure; an auto-increment key always appends to the rightmost leaf. (The counter-argument — rightmost-page contention and enumerable IDs — is why ULIDs and UUIDv7 exist: time-ordered but not sequential.)
3. **Primary-key range scans are exceptionally fast**, because the rows are physically adjacent. `WHERE id BETWEEN 1000 AND 2000` reads contiguous pages.

PostgreSQL's design has the mirror consequences: a uniform cost for every index, no secondary-index double traversal, but no free clustering either, and heap fetches after an index scan are *random* I/O.

### 4.3 Selectivity, cardinality, and when a full scan is correct

- **Cardinality** = the number of **distinct values** in a column.
  ```
  cardinality(col) = COUNT(DISTINCT col)
  ```
  `orders.customer_id` across 50 M rows for 2.5 M customers has cardinality 2,500,000. `orders.is_gift` has cardinality 2.

- **Selectivity** = the fraction of the table a predicate is expected to return. Lower is better — "more selective" means "returns fewer rows".
  ```
  selectivity(pred) = estimated_matching_rows / total_rows
  ```
  For an equality predicate on a uniformly distributed column:
  ```
  selectivity(col = ?) ≈ 1 / cardinality(col)
  ```
  So `customer_id = 90210` has selectivity `1/2,500,000 = 0.00004%` — superb. `is_gift = false` has selectivity ≈ 98% — useless.

**Why a full scan is sometimes genuinely correct, not a failure.** An index scan reads the index (a few pages) and then performs **one random heap read per matching row**. A sequential scan reads every page, but **sequentially**, which storage handles far faster: readahead prefetching, no seek, and on PostgreSQL a synchronised scan that can share a scan already in progress with another query.

So the index scan wins only while the *number of random reads it causes* costs less than the *entire sequential read of the table*. PostgreSQL models this with planner cost constants (defaults; units are arbitrary and relative):

| Constant | Default | Meaning |
|---|---|---|
| `seq_page_cost` | 1.0 | cost of one sequentially-read page |
| `random_page_cost` | 4.0 | cost of one randomly-read page — 4× a sequential one |
| `cpu_tuple_cost` | 0.01 | cost of processing one row |
| `cpu_index_tuple_cost` | 0.005 | cost of processing one index entry |

`random_page_cost = 4.0` encodes a spinning disk. **On SSD/NVMe this is wrong and should be lowered to 1.1–1.5**, which is one of the highest-value single configuration changes on a modern PostgreSQL instance: it makes the planner willing to use indexes for far less selective predicates. §9 computes exactly how much the crossover moves.

The crossover point — the selectivity at which the planner flips from index scan to sequential scan — is derived numerically in §9 step 5. Approximately: **with `random_page_cost = 4.0` the switch happens near 0.9% selectivity; with `random_page_cost = 1.1` it moves to about 3.2%.** Above that, the full scan is not a mistake. It is the cheaper plan.

A related and commonly misdiagnosed case: a **bitmap heap scan**. When a predicate matches too many rows for a plain index scan but too few for a full scan, PostgreSQL builds a bitmap of matching pages from the index, sorts it, and reads the heap pages in *physical order* — converting random reads into near-sequential ones. If you see `Bitmap Heap Scan` in `EXPLAIN`, the planner found the middle ground; it is usually right.

### 4.4 Index-only scans

If **every column the query needs is present in the index**, the database can answer entirely from the index and never touch the heap. This is an **index-only scan** and it is the single largest speedup available after merely having an index at all.

```sql
CREATE INDEX idx_orders_cust ON orders (customer_id) INCLUDE (placed_at, total_cents);
SELECT placed_at, total_cents FROM orders WHERE customer_id = 90210;
-- every needed column is in the index → zero heap reads
```

Why it is so fast: it eliminates the random heap I/O, which is 20 of the 24 page reads in BookNook's query (§9). It also keeps a much smaller working set in the buffer cache — the index for one column is a fraction of the table's size, so it is far more likely to be fully cached in RAM.

**The simplified teaching model** is "all needed columns in the index ⇒ no heap access". **Production reality in PostgreSQL adds one condition:** the index does not store row visibility information (which transactions can see which row versions — Lesson 49/50 MVCC), so PostgreSQL must consult the **visibility map**, a tiny bitmap with one bit per heap page meaning "every tuple on this page is visible to everyone". If the bit is set, no heap read. If not — because the page was recently modified and not yet vacuumed — PostgreSQL falls back to a heap fetch for that row. **Therefore index-only scans only stay fast on tables that are vacuumed regularly.** An index-only scan on a heavily-updated, under-vacuumed table silently degrades into an ordinary index scan. InnoDB's equivalent is called a **covering index** and has no visibility-map caveat because undo information is reached differently, though it has its own read-view cost.

### 4.5 What each write costs, per index

| Operation | Heap work | Work **per index** |
|---|---|---|
| `INSERT` | append the tuple to a page with free space (usually the last page — cheap, sequential) | insert one entry into the correct leaf page of **every** index. That leaf page is at a *random* location determined by the key, so each index adds one random page touch, plus a possible **page split** if the leaf is full |
| `DELETE` | mark the tuple dead (PostgreSQL) / mark and link to undo (InnoDB) | mark the corresponding entry dead in **every** index; reclaiming the space is deferred to vacuum/purge |
| `UPDATE` | PostgreSQL writes a **new tuple version** (MVCC) and marks the old dead; InnoDB updates in place and writes an undo record | **This is the one with a conditional answer.** See below |

The `UPDATE` rule, stated precisely:

- **PostgreSQL:** because an update creates a new tuple with a new TID, *every* index would normally need updating — even indexes on columns you did not change. The escape is a **HOT update** (Heap-Only Tuple): if (a) **no indexed column was modified** and (b) **the new version fits on the same heap page**, PostgreSQL chains the new version to the old within the page and **updates zero indexes**. This makes `fillfactor` meaningful: setting `fillfactor = 85` on an update-heavy table leaves 15% of every page free so HOT updates keep succeeding.
- **InnoDB:** a non-key update modifies the row in the clustered index leaf and touches only the secondary indexes whose columns changed.

So the practical quantification is:

```
physical writes per INSERT  = 1 heap + N index writes          = N + 1
physical writes per UPDATE  = 1 heap + (number of indexes whose
                                        columns you touched) writes
                              …unless HOT applies, in which case = 1
```

§9 step 6 turns this into concrete numbers. The headline: **five indexes turn one logical insert into six physical writes**, and because five of those six go to random locations, the throughput cost is worse than 6× suggests.

### 4.6 Choosing indexes from access patterns

Lesson 48 taught enumerating access patterns before choosing a database. The same enumeration chooses the indexes. The method:

1. **List every query the application issues**, with its frequency and its latency requirement. Not the queries you imagine — the ones in the logs.
2. **For each, extract the predicate shape**: which columns appear in `WHERE`, which in `ORDER BY`, which in `GROUP BY`, which in join conditions.
3. **Estimate selectivity** for each predicate using `1/cardinality` (or the real distribution if skewed).
4. **Keep only predicates below the crossover selectivity** (§9 step 5 — roughly 1–3%). Everything else will be a sequential scan no matter what you build.
5. **Merge overlapping needs.** A composite index on `(customer_id, placed_at)` serves `WHERE customer_id = ?`, `WHERE customer_id = ? ORDER BY placed_at`, and `WHERE customer_id = ? AND placed_at > ?` — three patterns, one index. A separate index on `customer_id` alone is then redundant and should not exist. (Column ordering within a composite index has strict rules — **leftmost prefix** — covered in Phase 3, Topic 15.)
6. **Check the write budget.** Multiply the insert rate by the number of indexes; if that exceeds what the storage can absorb, drop the lowest-value index.
7. **Verify by measurement, not by hope:** `EXPLAIN (ANALYZE, BUFFERS)` before and after (Phase 3, Topic 16 — Query planning and EXPLAIN).

### 4.7 Finding missing indexes

**From the slow query log.** Enable `log_min_duration_statement = 200ms` (PostgreSQL) or `slow_query_log` with `long_query_time = 0.2` (MySQL). Better, install `pg_stat_statements`, which aggregates by normalised query text and ranks by *total* time — the right metric, because a 15 ms query run 2 million times a day costs more than a 9-second report run twice.

```sql
SELECT query, calls, mean_exec_time, total_exec_time
  FROM pg_stat_statements ORDER BY total_exec_time DESC LIMIT 20;
```

**From table-level statistics.** `pg_stat_user_tables.seq_scan` counts sequential scans; `seq_tup_read` counts rows they read. A large table with a high `seq_scan` count and a huge `seq_tup_read / seq_scan` ratio is being repeatedly scanned and is a prime candidate. (Small tables with high `seq_scan` are fine and expected — a 40-row lookup table lives in one page and should *always* be scanned.)

### 4.8 Finding unused indexes

`pg_stat_user_indexes` records how many times the planner actually chose each index:

```sql
SELECT relname, indexrelname, idx_scan, pg_size_pretty(pg_relation_size(indexrelid))
  FROM pg_stat_user_indexes WHERE idx_scan = 0 ORDER BY pg_relation_size(indexrelid) DESC;
```

`idx_scan = 0` means the index has never been used **since statistics were last reset** — which a `pg_stat_reset()` or, historically, a major-version upgrade will have done. Three cautions before dropping:

1. **Check the window.** `pg_stat_reset_time` (from `pg_stat_database`) tells you how long the counter has accumulated. A week is not enough; a quarterly report's index will look unused for 89 days.
2. **Never drop an index backing a constraint.** A `UNIQUE` or primary-key index may show `idx_scan = 0` while doing essential work on every insert, because constraint checks are not counted as scans.
3. **Check every replica.** Read replicas (Phase 3, Topic 20) have their own statistics; an index unused on the primary may be the backbone of the analytics replica.

The safe procedure is `DROP INDEX CONCURRENTLY` (PostgreSQL — does not take a long exclusive lock), after recording the exact `CREATE INDEX` statement so it can be restored.

### 4.9 Bloat and REINDEX CONCURRENTLY

**Index bloat** is accumulated dead space: entries for deleted or updated-away rows, and half-empty leaf pages left behind by page splits. Under a workload of random inserts and heavy updates, an index can reach **two to four times** its minimal size (typical for an under-vacuumed, update-heavy table). Bloat is not just wasted disk — it is wasted *buffer cache*, so a bloated index evicts useful pages and raises the physical read rate across the whole instance.

`VACUUM` reclaims dead entries for reuse but does **not** return space to the filesystem or re-balance the tree. To actually rebuild:

```sql
REINDEX INDEX CONCURRENTLY idx_orders_customer_id;   -- PostgreSQL 12+
```

`CONCURRENTLY` builds a new index alongside the old, then swaps — so reads and writes continue. The costs: it takes roughly **2–3× longer** than a plain `REINDEX`, requires **enough free disk for both copies simultaneously**, and if it fails it leaves an invalid index (`_ccnew` suffix) that must be dropped manually. Without `CONCURRENTLY`, `REINDEX` takes an `ACCESS EXCLUSIVE` lock — the table is unavailable for the duration, which on a 1 GB index is minutes.

### 4.10 Low-cardinality columns and partial indexes

An index on `orders.is_gift` (cardinality 2) is usually useless:

- `WHERE is_gift = false` matches ~98% of 50 M rows. The planner correctly ignores the index and scans.
- `WHERE is_gift = true` matches ~2% — 1 million rows. An index would produce up to a million random heap reads, which is *worse* than the sequential scan (§9 step 5 shows the threshold is below this).

But the column is not worthless — the *index design* is. A **partial index** indexes only the rows matching a predicate:

```sql
CREATE INDEX idx_orders_pending ON orders (placed_at) WHERE status = 'pending';
```

If 0.3% of orders are `pending` at any moment, this index contains 150,000 entries instead of 50 million — roughly **0.4% of the size** of a full index. It is small enough to stay permanently in cache, it is only updated when a row enters or leaves the `pending` state, and the planner uses it for any query whose `WHERE` clause implies the index predicate. The low-cardinality column becomes valuable precisely *because* the distribution is skewed: **the rarer the value, the better the partial index**.

The same reasoning applies to soft deletes (`WHERE deleted_at IS NULL`), feature-flagged rows, and any status column where queries only ever ask about a minority state.

## 5. Request / data flow

**BookNook `GET /customers/90210/orders` — with an index on `orders(customer_id)`:**

1. The HTTP request reaches an app server; the app issues `SELECT id, placed_at, total_cents FROM orders WHERE customer_id = 90210 ORDER BY placed_at DESC LIMIT 20`.
2. The database **parses** the SQL into a syntax tree and **rewrites** it (expanding views, applying rules).
3. The **planner** enumerates candidate plans: sequential scan with a filter, index scan on `idx_orders_customer_id`, bitmap heap scan.
4. For each candidate it consults **statistics** in `pg_statistic` — gathered by `ANALYZE`, consisting of `n_distinct` per column, a histogram of value boundaries, and a list of most-common values with their frequencies. From these it estimates **selectivity**: `n_distinct = 2,500,000` over 50 M rows ⇒ an estimated 20 matching rows.
5. It **costs** each plan with the constants in §4.3. Sequential scan ≈ 1,750,000 units; index scan ≈ 100 units. The index scan wins by four orders of magnitude.
6. The **executor** descends the B-tree: read the **root** page, binary-search it, follow the pointer to a **level-2** page, then a **level-3** page, then the **leaf** page. Four page reads. Each is checked against the **shared buffer cache** first; the root and upper levels are almost always already cached, so typically only the leaf is a physical read.
7. The leaf page yields 20 entries: `(90210, ctid=(48112,7))`, `(90210, ctid=(911004,2))`, … each a key plus a heap TID.
8. For each TID the executor fetches the heap page and extracts the tuple. These 20 pages are at **unpredictable, scattered locations** — this is the random I/O that dominates the cost.
9. Each fetched tuple is checked for **MVCC visibility** against the transaction's snapshot (Lesson 49): is this version committed and visible to me? Invisible versions are discarded and do not count towards the `LIMIT`.
10. Because the index is on `customer_id` only and the query sorts by `placed_at`, the executor performs a small in-memory sort of the 20 rows, then applies `LIMIT 20`.
11. Rows are serialised onto the wire; the app renders JSON; the response returns.

**And the same request with no index** — steps 3–10 collapse into: the planner has exactly one option, and the executor reads heap pages 1 … 1,250,000 in order, testing all 50,000,000 rows, discarding 49,999,980 of them, then sorts the survivors. ~20 seconds, and the 10 GB it streams evicts most of the buffer cache, so every *other* query on the instance also slows down for the next few minutes. **An unindexed scan is not just slow for itself; it is an attack on the cache.**

**And the write path — `POST /orders` on a table with five indexes:**

1. `INSERT INTO orders (...) VALUES (...)`.
2. The executor finds a heap page with free space (usually the last page) and writes the tuple. **1 page dirtied.**
3. For **each of the five indexes**, it computes the key from the new row, descends that index to the correct leaf, and inserts an entry. **5 more pages dirtied, at five unrelated random locations.** If any leaf is full, it **splits** — allocating a new page, moving half the entries, and updating the parent, which can cascade upward.
4. Every one of those six page modifications is first written to the **write-ahead log** (WAL), which is what actually must reach durable storage before `COMMIT` returns (Lesson 49). A page split writes a **full-page image** to WAL, which is 8 KB of log for one 30-byte index entry.
5. `COMMIT` flushes WAL. The dirty pages themselves are written later by the background writer/checkpointer.
6. `201 Created`.

Step 3 is the entire write cost of indexing, and §9 step 6 quantifies it.

## 6. ASCII diagram

```
HEAP + INDEX — what is actually stored
==========================================================================

  B-TREE INDEX on orders(customer_id)          HEAP (orders table)
  height 4, ~1.1 GB                            1,250,000 pages, ~10 GB

            ┌───────────┐                      page 1     ┌──────────────┐
  root      │ 1k│50k│…  │  1 page              (unordered)│ row, row,row │
            └─┬─┴─┬─┴───┘                                 └──────────────┘
         ┌────┘   └────┐                       page 2     ┌──────────────┐
  level2 │…│…│       │…│…│  2 pages                       │ row, row,row │
         └┬┘           └┬┘                                └──────────────┘
      ┌───┘             └───┐                     …
  lvl3│…│…│…│          │…│…│…│  386 pages
      └┬┘                 └┬┘                 page 48112  ┌──────────────┐
   ┌───┘                   └──┐                           │ …,row#7,…    │◀┐
  leaf ┌──────────────────────────────┐                   └──────────────┘ │
  pages│ (90210,(48112,7)) (90210,…) …│ 139,000 pages                      │
       └──────────────┬───────────────┘               page 911004 ┌──────┐ │
                      │  TID = (page, slot)                       │ row#2│◀┤
                      └───────────────────────────────────────────┴──────┘ │
                        index stores POINTERS, never the row ───────────────┘


COST OF THE SAME QUERY, THREE WAYS  (customer_id = 90210 → 20 rows)
==========================================================================

  SEQ SCAN       ████████████████████████████████████████  1,250,000 pages
                 sequential, 500 MB/s                      ≈ 20,000 ms

  INDEX SCAN     ▌                                         24 pages
                 4 index (sequentialish) + 20 heap (RANDOM) ≈ 2.4 ms

  INDEX-ONLY     ▏                                         5 pages
                 4 index + visibility map, 0 heap          ≈ 0.5 ms


WRITE AMPLIFICATION — one logical INSERT, five indexes
==========================================================================

        INSERT INTO orders VALUES (…)          ← 1 logical write
                      │
        ┌─────────────┼───────────────────────────────────┐
        ▼             ▼        ▼        ▼       ▼         ▼
     heap page     idx_pk  idx_cust  idx_date idx_status idx_sku
     (append,      (random)(random) (random)  (random)  (random)
      cheap)
        └──────────── 6 physical page writes ─────────────┘
                      + WAL record for each
                      + possible page split per index (8 KB full-page image)

     reads on 1 column : 8,000× faster
     writes            : ~6× more physical work, 5/6 of it RANDOM


SELECTIVITY — where the planner flips
==========================================================================

   cost │                          ╱ index scan (random reads × matches)
        │                       ╱
        │━━━━━━━━━━━━━━━━━━━╱━━━━━━━━━━━━━━━━━━  seq scan (flat, 1.75 M)
        │               ╱
        │_________╱
        └────────┼──────────────────────────────▶ selectivity
               0.9%                (random_page_cost = 4.0, spinning disk)
               3.2%                (random_page_cost = 1.1, NVMe)
        left of the line: index wins.  right: the full scan is CORRECT.
```

## 7. Detailed example

### 7.1 BookNook — indexing the `orders` table

BookNook's `orders` table, 50 M rows, grows by ~2,000 inserts/sec at peak. The team enumerates its access patterns exactly as Lesson 48 prescribed:

| # | Query | Frequency | Predicate columns | Est. selectivity |
|---|---|---|---|---|
| A | Customer's order history | 4,000/min | `customer_id`, order by `placed_at` | 0.00004% |
| B | Order detail by id | 9,000/min | `id` (primary key) | 0.000002% |
| C | Warehouse pulls pending orders | 600/min | `status = 'pending'`, order by `placed_at` | 0.3% |
| D | Finance daily revenue | 2/day | `placed_at` within one day | 0.3% |
| E | "Did this customer buy this book?" | 800/min | `customer_id` + `book_id` | 0.0000004% |
| F | Marketing: all gift orders | 1/day | `is_gift = true` | 2% |
| G | Admin search by email | 30/min | `customer_email` | 0.00004% |

The resulting index set, with the reasoning for each:

```sql
-- B: already present — the primary key. Free, mandatory.
-- A + E: ONE composite index serves both, because (customer_id) is a leftmost
--        prefix of (customer_id, book_id). Column order rules: Topic 15.
CREATE INDEX idx_orders_cust_book ON orders (customer_id, book_id);

-- C: PARTIAL. Only ~0.3% of rows are pending at any time, so this index holds
--    ~150,000 entries instead of 50,000,000 — about 0.4% of a full index's size.
CREATE INDEX idx_orders_pending ON orders (placed_at) WHERE status = 'pending';

-- D: runs twice a day over 150,000 rows (0.3%) — right at the crossover.
--    A BRIN index costs ~50 KB instead of ~1.1 GB because placed_at correlates
--    almost perfectly with physical insert order. (Index types: Topic 15.)
CREATE INDEX idx_orders_placed_brin ON orders USING brin (placed_at);

-- G: high selectivity, low frequency, but 30/min × 20 s unindexed is unacceptable.
CREATE INDEX idx_orders_email ON orders (customer_email);
```

And deliberately **not** created:

- **No index on `is_gift` (F).** Cardinality 2, and the interesting value still matches 1 M rows — well past the crossover (§9 step 5). The daily job does a sequential scan, taking 20 seconds once a day, and that is the correct outcome. If the job became hourly, the answer would be a partial index `WHERE is_gift = true`, not a full one.
- **No standalone index on `customer_id` (A).** It would be entirely redundant with `idx_orders_cust_book`, while costing a full second index write on every insert.
- **No index on `status` generally.** The partial index covers the only selective value.

**The result, measured:**

| Query | Before | After | Mechanism |
|---|---|---|---|
| A | 20,000 ms | 2.4 ms | index scan, 24 pages (§9) |
| C | 20,000 ms | 1.1 ms | partial index, ~6 pages |
| D | 20,000 ms | 900 ms | BRIN narrows to ~4,000 heap pages |
| G | 20,000 ms | 1.9 ms | index scan |
| **Insert throughput** | **~5,200/s** | **~2,300/s** | **4 indexes + PK = 5 writes per insert** |
| Table + index storage | 10.0 GB | 13.4 GB | +34% |

That insert row is the honest half of the story and is exactly what §9 step 6 predicts. BookNook accepted it because peak insert load is 2,000/sec against a 2,300/sec ceiling — which is uncomfortably tight, and the team's follow-up action was to move `customer_email` out of `orders` entirely (it was denormalised duplication of `customers.email`), removing one index and restoring headroom to ~2,900/sec.

**Six months later: bloat and an unused index.** A routine audit runs the `pg_stat_user_indexes` query from §4.8 and finds `idx_orders_email` at `idx_scan = 41` over 90 days, 1.3 GB in size — the admin search had been replaced by a search service (Lesson 47) and nobody removed the index. It is dropped with `DROP INDEX CONCURRENTLY`, recovering 1.3 GB and ~12% of insert throughput. Separately, `idx_orders_cust_book` has grown to 2.9 GB against a theoretical 1.6 GB — 81% bloat from status updates. `REINDEX INDEX CONCURRENTLY` returns it to 1.6 GB, and `fillfactor = 85` is set on the table so that future status updates qualify as HOT updates and stop touching indexes at all.

### 7.2 A contrasting real-world example — an append-only metrics store

A time-series monitoring system ingests 400,000 measurements per second into a table `(metric_id, ts, value)`. Reads are almost always "this metric, this time range". Naively, you index `(metric_id, ts)` — and the system immediately falls over, because 400,000 random B-tree leaf insertions per second is far beyond what any single node can sustain, and the index would be larger than the data.

The resolutions are instructive, because each one dodges the trade-off rather than paying it:

- **BRIN instead of B-tree.** Because `ts` increases monotonically with physical insert order, a Block Range INdex stores only the min/max `ts` per 128-page range. For the same 50 M rows, BRIN costs roughly **50 KB versus 1.1 GB**, and its insert cost is near zero. It cannot do point lookups, only range narrowing — which is exactly what this workload asks for.
- **Partitioning instead of indexing.** Split the table by day (Phase 3, Topic 21 — Partitioning and sharding). A query for "yesterday" touches one partition; the planner eliminates the other 364 without reading a single index page. **Partition pruning is a form of indexing with zero write cost**, because the "index" is the table's physical organisation.
- **LSM-trees instead of B-trees.** Cassandra and RocksDB buffer writes in memory and flush sorted, immutable files, converting random index writes into sequential ones. Write amplification moves from "random, synchronous, 6×" to "sequential, background, 10–30× over the file's lifetime". The trade is read amplification — a read may consult several files.

The contrast with BookNook is the point: **BookNook has 2,000 writes/sec and demands single-row point lookups, so B-tree indexes are correct and their write cost is affordable. The metrics store has 400,000 writes/sec and only needs ranges, so B-tree indexes are wrong at any price.** The access pattern, not the row count, decides.

## 8. Code or configuration example

> **Illustrative only — not production-ready.** Sizes, statistics targets, and thresholds are starting points to be measured, not defaults to be copied. Index creation on large production tables must use `CONCURRENTLY` and be scheduled.

### 8.1 Creating indexes, with the reasoning inline

```sql
-- ── BASELINE ────────────────────────────────────────────────────────────────
-- PRIMARY KEY creates a unique index automatically. In InnoDB it also decides
-- the physical row order (clustered); in PostgreSQL it is an ordinary index.
CREATE TABLE orders (
    id             BIGSERIAL PRIMARY KEY,      -- monotonic: appends to the rightmost
                                               -- leaf. A random UUID here would split
                                               -- pages across the whole index.
    customer_id    BIGINT      NOT NULL,
    book_id        BIGINT      NOT NULL,
    status         TEXT        NOT NULL,
    is_gift        BOOLEAN     NOT NULL DEFAULT false,
    total_cents    INTEGER     NOT NULL,
    placed_at      TIMESTAMPTZ NOT NULL DEFAULT now()
) WITH (fillfactor = 85);                      -- leave 15% free per page so UPDATEs
                                               -- qualify as HOT and touch NO index

-- ── CONCURRENT CREATION ─────────────────────────────────────────────────────
-- Plain CREATE INDEX takes a SHARE lock: it blocks every INSERT/UPDATE/DELETE
-- for the whole build — minutes to hours on 50 M rows. CONCURRENTLY does two
-- passes and never blocks writes, at the cost of ~2-3x the build time.
-- It CANNOT run inside a transaction block, and on failure it leaves an INVALID
-- index that must be dropped manually — so always verify afterwards.
CREATE INDEX CONCURRENTLY idx_orders_cust_book ON orders (customer_id, book_id);

-- Verify the build actually succeeded. indisvalid = false means a failed
-- CONCURRENTLY build left a dead index that still costs you on every write.
SELECT indexrelid::regclass, indisvalid FROM pg_index WHERE NOT indisvalid;

-- ── PARTIAL INDEX ───────────────────────────────────────────────────────────
-- Indexes ONLY rows matching the predicate. 'pending' is ~0.3% of the table, so
-- this holds ~150k entries instead of 50M, and is only written when a row ENTERS
-- or LEAVES the pending state — not on every insert of a completed order.
CREATE INDEX CONCURRENTLY idx_orders_pending
    ON orders (placed_at)
 WHERE status = 'pending';                     -- planner uses it only when the query
                                               -- predicate IMPLIES this one

-- ── COVERING INDEX (index-only scans) ───────────────────────────────────────
-- INCLUDE columns are stored in the LEAF pages only; they are not part of the key,
-- so they do not affect ordering or uniqueness and do not bloat internal pages.
-- Result: queries selecting only these columns never touch the heap at all.
CREATE INDEX CONCURRENTLY idx_orders_cust_covering
    ON orders (customer_id)
    INCLUDE (placed_at, total_cents);

-- ── STATISTICS ──────────────────────────────────────────────────────────────
-- The planner's estimates come from sampled statistics. A new index is useless
-- if the planner's row estimate is wrong, so ANALYZE after any bulk change.
ANALYZE orders;

-- For a skewed column, raise the sample size: default 100 buckets in the
-- histogram, up to 10000. Higher = better estimates, slower ANALYZE, bigger
-- catalog. Only worth it on columns with provably bad estimates.
ALTER TABLE orders ALTER COLUMN customer_id SET STATISTICS 500;
ANALYZE orders (customer_id);
```

### 8.2 Proving the index is used — and that it helps

```sql
-- EXPLAIN alone shows the PLAN and the planner's GUESSES.
-- ANALYZE actually RUNS the query and shows reality. BUFFERS shows PAGE COUNTS,
-- which is the only honest measure of work — timings vary with cache state.
EXPLAIN (ANALYZE, BUFFERS, COSTS)
SELECT placed_at, total_cents FROM orders WHERE customer_id = 90210;
```

```
-- BEFORE the index: the planner has one option.
Seq Scan on orders  (cost=0.00..1750000.00 rows=20 width=12)
                    (actual time=0.9..19842.1 rows=20 loops=1)
  Filter: (customer_id = 90210)
  Rows Removed by Filter: 49999980          ← 50 M rows touched to return 20
  Buffers: shared read=1250000              ← EVERY page of the table

-- AFTER the index:
Index Scan using idx_orders_cust_book on orders
                    (cost=0.57..84.21 rows=20 width=12)
                    (actual time=0.07..2.31 rows=20 loops=1)
  Index Cond: (customer_id = 90210)
  Buffers: shared hit=4 read=20             ← 4 index pages cached, 20 heap reads

-- AFTER the COVERING index — the heap is never touched:
Index Only Scan using idx_orders_cust_covering on orders
                    (actual time=0.03..0.41 rows=20 loops=1)
  Index Cond: (customer_id = 90210)
  Heap Fetches: 0                           ← 0 means the visibility map was set.
                                            -- A NON-ZERO value here means the table
                                            -- needs vacuuming and the "index-only"
                                            -- scan is silently doing heap I/O.
  Buffers: shared hit=4 read=1
```

The three lines that matter, in order: **`Buffers`** (actual work done), **`rows=N (actual rows=M)`** (if the estimate and reality differ by more than ~10×, the plan was chosen on bad statistics), and **`Heap Fetches`** (whether an index-only scan is really index-only).

### 8.3 Finding missing and unused indexes

```sql
-- MISSING: large tables being sequentially scanned repeatedly.
-- seq_tup_read / seq_scan = average rows read per scan. A big number on a big
-- table means a predicate that should be indexed. Small tables are EXCLUDED
-- because scanning a 40-row table is correct and an index there is pure cost.
SELECT relname,
       seq_scan,
       seq_tup_read,
       seq_tup_read / NULLIF(seq_scan, 0) AS avg_rows_per_scan,
       idx_scan,
       pg_size_pretty(pg_relation_size(relid))     AS table_size
  FROM pg_stat_user_tables
 WHERE seq_scan > 0
   AND pg_relation_size(relid) > 100 * 1024 * 1024   -- ignore anything under 100 MB
 ORDER BY seq_tup_read DESC
 LIMIT 20;

-- MISSING, from actual query cost. pg_stat_statements ranks by TOTAL time, which
-- is the correct metric: a 15 ms query run 2 M times/day costs far more than a
-- 9 s report run twice. Ranking by mean_exec_time hides the real problem.
SELECT calls,
       round(mean_exec_time::numeric, 2)  AS mean_ms,
       round(total_exec_time::numeric)    AS total_ms,
       round(100 * total_exec_time / SUM(total_exec_time) OVER (), 1) AS pct,
       left(query, 90)                    AS query
  FROM pg_stat_statements
 ORDER BY total_exec_time DESC
 LIMIT 20;

-- UNUSED: idx_scan counts how often the PLANNER CHOSE this index.
-- CAUTION 1: this is "since stats were last reset" — check the window first.
-- CAUTION 2: indisunique/indisprimary indexes do essential constraint work that
--            is NOT counted as a scan. Never drop them on this evidence.
-- CAUTION 3: replicas keep their own counters. Check every node before dropping.
SELECT s.relname                                        AS table_name,
       s.indexrelname                                   AS index_name,
       s.idx_scan,
       pg_size_pretty(pg_relation_size(s.indexrelid))   AS index_size
  FROM pg_stat_user_indexes s
  JOIN pg_index i ON i.indexrelid = s.indexrelid
 WHERE s.idx_scan < 50                                  -- near-zero, not just zero
   AND NOT i.indisunique                                -- exclude constraint-backing
   AND NOT i.indisprimary
 ORDER BY pg_relation_size(s.indexrelid) DESC;

-- How long have those counters been accumulating? Without this, the query above
-- is meaningless — a quarterly report's index looks unused for 89 days.
SELECT datname, stats_reset, now() - stats_reset AS window
  FROM pg_stat_database WHERE datname = current_database();

-- Dropping safely. CONCURRENTLY avoids the ACCESS EXCLUSIVE lock that a plain
-- DROP INDEX takes. Record the exact CREATE statement first so it can be restored.
SELECT indexdef FROM pg_indexes WHERE indexname = 'idx_orders_email';
DROP INDEX CONCURRENTLY idx_orders_email;
```

### 8.4 Bloat and rebuilding

```sql
-- Rough bloat signal: compare the index's actual size against the number of live
-- rows it should contain. A precise calculation needs pgstattuple; this is a
-- cheap first filter, not an authority.
SELECT i.indexrelname,
       pg_size_pretty(pg_relation_size(i.indexrelid)) AS actual_size,
       c.reltuples::bigint                            AS est_rows
  FROM pg_stat_user_indexes i
  JOIN pg_class c ON c.oid = i.relid
 WHERE i.relname = 'orders';

-- Rebuild without blocking reads or writes (PostgreSQL 12+).
-- COSTS: ~2-3x slower than a plain REINDEX; needs free disk for BOTH copies at
-- once; on failure leaves an invalid "<name>_ccnew" index to drop by hand.
-- Without CONCURRENTLY, REINDEX takes ACCESS EXCLUSIVE — the table is offline.
REINDEX INDEX CONCURRENTLY idx_orders_cust_book;

-- Verify no failed rebuild was left behind.
SELECT indexrelid::regclass FROM pg_index WHERE NOT indisvalid;

-- Reduce FUTURE bloat rather than repeatedly curing it:
-- 1. fillfactor leaves free space per heap page so UPDATEs stay HOT (no index write)
ALTER TABLE orders SET (fillfactor = 85);
-- 2. make autovacuum far more aggressive on this hot table than the global default
ALTER TABLE orders SET (autovacuum_vacuum_scale_factor = 0.02);  -- default 0.2
```

### 8.5 Planner configuration that changes every decision

```ini
# postgresql.conf — values here are TYPICAL starting points for an NVMe instance
# with 64 GB RAM. Every one of them must be validated against your own workload.

random_page_cost     = 1.1    # DEFAULT IS 4.0, which models a spinning disk.
                              # On SSD/NVMe random reads cost nearly the same as
                              # sequential ones. Leaving it at 4.0 makes the planner
                              # irrationally avoid indexes. See §9 step 5: this one
                              # change moves the index/seq-scan crossover from
                              # 0.9% to 3.2% selectivity.
seq_page_cost        = 1.0    # the reference unit; leave at 1.0 and tune the others
                              # relative to it.

effective_cache_size = 48GB   # NOT an allocation. It TELLS the planner roughly how
                              # much of the data is likely cached (shared_buffers +
                              # OS page cache). Too low and the planner assumes every
                              # index page is a physical read and avoids indexes.
shared_buffers       = 16GB   # the actual allocation, typically ~25% of RAM

# Diagnostics
log_min_duration_statement = 200ms   # log anything slower; the raw material for §8.3
shared_preload_libraries   = 'pg_stat_statements'
```

## 9. Numerical example

**Goal:** compute BookNook's `orders` query latency with and without an index, including the exact number of pages read; derive the selectivity crossover; and quantify write amplification.

**Assumptions — every one of them stated, all typical or approximate rather than measured:**

| Symbol | Meaning | Value |
|---|---|---|
| `P` | page size (PostgreSQL default) | 8,192 bytes |
| `U` | usable bytes per page after the 24-byte header and special space | ≈ 8,100 bytes |
| `N` | rows in `orders` | 50,000,000 |
| `r` | average physical row size: 176 bytes of data + 24-byte tuple header + 4-byte line pointer | ≈ 204 bytes |
| `S_seq` | sequential read throughput (typical cloud NVMe volume) | 500 MB/s |
| `t_rand` | latency of one random page read from NVMe, uncached | 0.1 ms |
| `t_cache` | latency of one page read from the buffer cache | 0.001 ms |
| `f_idx` | B-tree leaf fill factor (PostgreSQL default) | 90% |
| `m` | orders belonging to customer 90210 | 20 |
| `R` | storage budget for random page writes with fsync (typical mid-range NVMe volume) | 12,000 pages/s |
| `C` | WAL commit ceiling for this instance | 5,200 commits/s |

We assume `customer_id` is uniformly distributed, that the 20 matching rows were placed over several years and therefore live on 20 **distinct, scattered** heap pages (worst case, and realistic), and that the upper levels of every B-tree are already in the buffer cache while leaf and heap pages are not.

### Step 1 — size of the heap

```
rows per page   = floor(U / r) = floor(8,100 / 204) = 39.7  →  40 rows/page
heap pages      = N / 40 = 50,000,000 / 40            = 1,250,000 pages
heap size       = 1,250,000 × 8,192 bytes             = 10,240,000,000 bytes
                                                      ≈ 10.2 GB  (9.5 GiB)
```

### Step 2 — cost of the full table scan

```
time = heap size / S_seq = 10,240 MB / 500 MB/s = 20.48 s  ≈ 20,500 ms
rows examined = 50,000,000
rows returned = 20
rows discarded = 49,999,980   (99.99996% of the work is wasted)
pages read = 1,250,000
```

Note what does **not** appear in that calculation: `m`. A sequential scan costs the same whether the query returns 1 row or 1 million. This is the defining property of a full scan, and the reason it is predictable but unscalable.

### Step 3 — size and height of a B-tree index on `customer_id`

```
index entry = 8 (BIGINT key) + 6 (TID) + 4 (line pointer) + 2 (alignment) ≈ 20 bytes
usable per leaf page = U × f_idx = 8,100 × 0.90                           = 7,290 bytes
entries per leaf     = 7,290 / 20                                         = 364

leaf pages    = N / 364        = 50,000,000 / 364   = 137,363   →  137,400
level-3 pages = 137,400 / 364                       =     378
level-2 pages =     378 / 364                       =       2
root          =                                       =       1
───────────────────────────────────────────────────────────────────────────
total index pages = 137,400 + 378 + 2 + 1           = 137,781 pages
index size        = 137,781 × 8,192                 ≈ 1.13 GB  (11% of the heap)
tree height       = root + L2 + L3 + leaf           = 4 levels
```

Observe how slowly height grows: with 364-way fanout, 4 levels address `364⁴ ≈ 17.6 billion` entries. Growing `orders` from 50 million to 500 million rows adds **zero** levels. **A B-tree lookup is effectively constant-cost at any realistic table size** — this is why index scans scale and scans do not.

### Step 4 — cost of the index scan

```
index page reads  = 4   (root, level-2, level-3, leaf)
  of which cached = 3   (upper levels are hot; only the leaf is a physical read)
heap page reads   = m   = 20   (random, one per matching row)
───────────────────────────────────────────────────────────
total pages touched = 24
physical random reads = 1 (leaf) + 20 (heap) = 21
cached reads          = 3

time = 21 × t_rand + 3 × t_cache + CPU
     = 21 × 0.1 ms + 3 × 0.001 ms + ≈0.3 ms
     = 2.1 ms + 0.003 ms + 0.3 ms
     ≈ 2.4 ms
```

**Comparison:**

```
pages read:  1,250,000  →  24            = 52,083× fewer pages
latency:     20,500 ms  →  2.4 ms        ≈  8,500× faster
```

Note which term dominates: **20 of the 24 pages are heap fetches, costing 2.0 of the 2.4 ms.** The index itself is responsible for only 0.1 ms. That is the motivation for step 4b.

### Step 4b — cost of the index-only scan

With `INCLUDE (placed_at, total_cents)` the leaf entries grow:

```
index entry = 8 (key) + 6 (TID) + 8 (timestamptz) + 4 (integer) + 10 (hdr/align) ≈ 36 bytes
entries per leaf = 7,290 / 36                                   = 202
leaf pages       = 50,000,000 / 202                             = 247,525
index size       = ~248,000 pages × 8,192                       ≈ 2.03 GB
tree height      = still 4 (internal entries are unchanged at 20 bytes)

reads = 4 index pages + 1 visibility-map page = 5
      of which physical: 1 leaf + 1 VM page   = 2
time  = 2 × 0.1 ms + 3 × 0.001 ms + 0.2 ms CPU ≈ 0.4 ms
```

```
index scan 2.4 ms  →  index-only scan 0.4 ms   = 6× faster
versus the full scan                           ≈ 51,000× faster
```

The price is explicit and must be stated: the covering index is **2.03 GB versus 1.13 GB — 80% larger**, so it consumes 80% more cache and costs proportionally more on every write. And the `Heap Fetches: 0` result holds **only while the visibility map is current**, i.e. only while autovacuum keeps up (§4.4).

### Step 5 — deriving the selectivity crossover

Using PostgreSQL's cost model (§4.3). Sequential scan cost is fixed:

```
cost_seq = heap_pages × seq_page_cost + N × cpu_tuple_cost
         = 1,250,000 × 1.0        + 50,000,000 × 0.01
         = 1,250,000              + 500,000
         = 1,750,000 cost units
```

Index scan cost for a predicate of selectivity `f`, assuming scattered rows (one random heap page per matching row):

```
matching rows  = N × f
cost_idx ≈ N·f × random_page_cost + N·f × (cpu_tuple_cost + cpu_index_tuple_cost)
         = N·f × 4.0              + N·f × (0.01 + 0.005)
         = N·f × 4.015
```

Set them equal and solve for the crossover `f*`:

```
50,000,000 × f* × 4.015 = 1,750,000
200,750,000 × f*        = 1,750,000
f*                      = 0.008717   →  0.87%
rows at the crossover   = 50,000,000 × 0.008717 = 435,850 rows
```

**With the default `random_page_cost = 4.0`, the planner abandons the index above ~0.87% selectivity.** Repeat with the NVMe-appropriate value:

```
cost_idx = N·f × (1.1 + 0.015) = N·f × 1.115
50,000,000 × f* × 1.115 = 1,750,000
55,750,000 × f*         = 1,750,000
f*                      = 0.031390   →  3.14%
rows at the crossover   = 1,569,500 rows
```

**Lowering `random_page_cost` from 4.0 to 1.1 moves the crossover from 0.87% to 3.14% — a 3.6× widening of the range over which indexes are used.** One configuration line, applied to every query on the instance.

One important refinement. The derivation above assumed scattered rows. If the matching rows are **physically clustered** — which PostgreSQL measures per column as `correlation` in `pg_stats`, ranging from −1 to +1 — then 40 matching rows share one page, and the heap cost divides by up to 40:

```
cost_idx (perfectly correlated) ≈ (N·f / 40) × 4.0 + N·f × 0.015
f* ≈ 1,750,000 / (50,000,000 × (4.0/40 + 0.015)) = 1,750,000 / 5,750,000 = 0.304  → 30.4%
```

**A perfectly correlated column keeps the index viable up to ~30% selectivity; an uncorrelated one gives up at 0.87%.** This is why the same index on the same-sized table behaves completely differently on `placed_at` (correlation ≈ 1.0, because orders are inserted in time order) and on `customer_id` (correlation ≈ 0.0).

### Step 6 — write amplification, quantified

One logical `INSERT INTO orders` with `n` indexes:

```
physical page writes = 1 heap page (sequential append, amortised 1/40 of a page)
                     + n index leaf pages (RANDOM, one per index)
                     = n + 1 page modifications
```

With `n = 5` (primary key + four secondary indexes):

```
1 logical write  →  6 physical page modifications, 5 of them at random locations
```

WAL volume per insert:

```
heap tuple WAL record   ≈ 204 bytes payload + 30 bytes header   = 234 bytes
index insert WAL record ≈  20 bytes payload + 40 bytes header   =  60 bytes each
total with 5 indexes    = 234 + (5 × 60)                        = 534 bytes
total with 1 index (PK) = 234 + 60                              = 294 bytes
WAL amplification       = 534 / 294                             = 1.8×
```

Plus **full-page images**: the first modification of any page after a checkpoint writes the entire 8 KB page to WAL. Five random index touches means up to `5 × 8 KB = 40 KB` of WAL for a single 204-byte row immediately following a checkpoint.

Insert throughput as a function of index count, using the budget assumptions:

```
ceiling(n) = min( C , R / n )          C = 5,200 commits/s, R = 12,000 random pages/s

n = 1 (PK only)   → min(5,200, 12,000)  = 5,200 inserts/s   ← WAL-bound
n = 2             → min(5,200,  6,000)  = 5,200 inserts/s   ← still WAL-bound
n = 3             → min(5,200,  4,000)  = 4,000 inserts/s   ← now index-bound
n = 4             → min(5,200,  3,000)  = 3,000 inserts/s
n = 5             → min(5,200,  2,400)  = 2,400 inserts/s
n = 8             → min(5,200,  1,500)  = 1,500 inserts/s

bottleneck switches at n = R / C = 12,000 / 5,200 = 2.3 indexes
```

Read these numbers carefully. Going from 1 index to 2 is **free** — the commit rate is the bottleneck and the second index hides underneath it. Going from 4 to 5 costs **20% of insert throughput**. Going from 1 to 8 costs **71%**. The marginal cost of an index is not constant; it is near zero below the crossover at ~2.3 indexes and steeply negative above it. **This is why "just add an index" is harmless on a lightly-indexed table and dangerous on a heavily-indexed one.**

This matches §7.1's measurements: 5,200 inserts/s with the primary key alone, 2,300/s with five indexes (predicted 2,400), and ~2,900/s after dropping `idx_orders_email` (predicted 3,000).

### Step 7 — storage

```
heap                                                            10.2 GB
primary-key index   (20 B/entry, 137,800 pages)                  1.13 GB
idx_orders_cust_book (28 B/entry → 260/leaf → 192,300 pages)     1.58 GB
idx_orders_email    (28 B/entry → 192,300 pages)                 1.30 GB
idx_orders_pending  (150,000 entries / 364 = 412 pages)          0.003 GB
idx_orders_placed_brin (1,250,000 pages / 128 per range
                        = 9,766 ranges × 32 B)                   0.0003 GB
────────────────────────────────────────────────────────────────────────
total                                                          ≈ 14.2 GB
index overhead over the heap alone                             ≈ 39%
```

Two things to notice. The **partial index is 0.03% the size of a full one** on the same column — the single best size-to-value ratio available. And the **BRIN index is 0.03% the size of the equivalent B-tree** (0.3 MB versus 1.13 GB) at the default `pages_per_range = 128`; raising that to 1,024 shrinks it to roughly 40 KB at the cost of coarser range narrowing.

### Step 8 — bloat

After six months of status updates, `idx_orders_cust_book` measures 2.9 GB:

```
bloat = (actual − theoretical) / theoretical = (2.9 − 1.58) / 1.58 = 0.84  →  84% bloat
wasted cache = 1.32 GB of a 16 GB shared_buffers = 8.3% of the entire cache
```

`REINDEX INDEX CONCURRENTLY` returns it to 1.58 GB. Setting `fillfactor = 85` so that status updates become HOT updates (§4.5) prevents the bloat from recurring, at the cost of a heap ~18% larger: `1,250,000 / 0.85 = 1,470,000 pages = 12.0 GB`. That is the trade — **18% more heap storage and a correspondingly slower sequential scan, in exchange for updates that touch zero indexes.** On an update-heavy table it is almost always worth it.

## 10. Trade-offs

| Dimension | With an index | Without |
|---|---|---|
| Point/range read on the indexed columns | O(tree height) ≈ 4 page reads, constant at any table size | O(table pages) — 1,250,000 reads, growing linearly forever |
| Write cost | +1 random page modification **per index, per insert**; §9 step 6 shows 5 indexes cost 54% of insert throughput | 1 cheap sequential heap append |
| Storage | +11% of the heap per simple B-tree index; +80% more again for a covering variant | zero |
| Buffer cache | Indexes compete with heap pages for RAM; a bloated or unused index evicts useful data | the heap has the whole cache |
| `ORDER BY` / `GROUP BY` on indexed columns | free — the index is already sorted | external sort of the full result set |
| Uniqueness enforcement | O(height) check per insert | impossible without a full scan |
| Predictability | plan depends on statistics; can flip to a scan when estimates drift | always the same cost, always slow |
| Maintenance burden | vacuum, bloat monitoring, periodic `REINDEX`, unused-index audits | none |
| `UPDATE` of an indexed column | updates the heap **and** that index; in PostgreSQL may disqualify a HOT update and touch *all* indexes | heap only |
| Schema change / bulk load | each index must be built or maintained; bulk loads are 3–10× faster with indexes dropped and rebuilt after | trivially fast |

Three asymmetries worth naming:

1. **Read benefit is bounded; write cost is not.** Once a query is at 4 page reads, a second index cannot make it meaningfully faster. But the tenth index costs exactly as much on every write as the first did. **Indexes have diminishing returns on reads and constant marginal cost on writes.** This is the whole argument against speculative indexing.
2. **The benefit is per-query; the cost is per-row.** An index that helps a query run 100 times a day is paid for by 173 million inserts a day. Frequency matters as much as speedup.
3. **Indexes trade a predictable cost for a conditional benefit.** The write cost is certain and continuous. The read benefit only materialises if the planner chooses the index, which depends on statistics being fresh, the predicate matching the index's leading columns, and the selectivity being on the right side of the crossover.

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| **Missing index on a growing table** | Page loads fine at 100k rows, takes 4 s at 5 M rows, times out at 50 M. Degradation is gradual and correlates with growth, not with a deploy | `pg_stat_user_tables.seq_scan` climbing on a large table; `pg_stat_statements` showing the query at the top by `total_exec_time`; `EXPLAIN` shows `Seq Scan` with a huge `Rows Removed by Filter` | `CREATE INDEX CONCURRENTLY`, then `ANALYZE`. Latency drops the moment the index becomes valid |
| **Stale statistics after a bulk load** | A query that was 2 ms yesterday is 20 s today with no code change. Classic "it was fast last week" | `EXPLAIN ANALYZE` shows a large gap between `rows=` (estimate) and `actual rows=`; `pg_stat_user_tables.last_analyze` is old | `ANALYZE <table>`. Prevent by running `ANALYZE` at the end of every bulk-load job and tuning `autovacuum_analyze_scale_factor` down on volatile tables |
| **Planner correctly refuses the index** | A query is slow despite the index existing; engineers "prove" the index is broken | `EXPLAIN` shows `Seq Scan` even with the index present. Check the selectivity against the §9 step 5 crossover | Often **nothing to fix** — the scan is cheaper. If the hardware is NVMe, lower `random_page_cost` to 1.1 and re-test; if the predicate is genuinely selective, the real problem is statistics or a non-sargable predicate (§15) |
| **Index not usable because the predicate is not sargable** | Query ignores an obviously relevant index. `WHERE lower(email) = ?` or `WHERE created_at::date = ?` | `EXPLAIN` shows `Seq Scan` with a `Filter` containing a function call on the column | Create a matching **expression index** (`CREATE INDEX ... ON orders (lower(email))`) or rewrite the predicate as a range (`created_at >= d AND created_at < d+1`) |
| **`CREATE INDEX` without `CONCURRENTLY` on a live table** | Total write outage on that table for minutes to hours; every `INSERT`/`UPDATE`/`DELETE` blocks | Locks visible in `pg_locks`; a flood of lock-wait timeouts across unrelated endpoints | Cancel the build and reissue with `CONCURRENTLY`. Prevent with a migration lint rule that rejects bare `CREATE INDEX` on tables above a size threshold |
| **Failed `CREATE INDEX CONCURRENTLY`** | No visible symptom, but writes are permanently slower than expected | `SELECT indexrelid::regclass FROM pg_index WHERE NOT indisvalid` returns rows | An invalid index **is still maintained on every write while never being used for reads** — the worst possible state. `DROP INDEX CONCURRENTLY` it and retry |
| **Index bloat** | Gradual, instance-wide latency increase; physical read rate climbs while the dataset has not grown | Index size far above the theoretical size (§9 step 8); rising `blks_read` relative to `blks_hit` in `pg_stat_database` | `REINDEX INDEX CONCURRENTLY`. Prevent with `fillfactor` and more aggressive autovacuum on the hot table |
| **Over-indexing** | Writes get progressively slower; batch jobs that finished in an hour now take four. Reads are fine, so nobody suspects indexes | Insert latency rising while row count rises only modestly; WAL generation rate climbing; §9 step 6 arithmetic | Audit with `pg_stat_user_indexes`; drop unused and redundant indexes with `DROP INDEX CONCURRENTLY` |
| **Dropping an index that a quarterly job needed** | A report that ran in 30 s takes 40 minutes, three months after the index was dropped, with no obvious connection to the change | The report's `EXPLAIN` now shows a `Seq Scan`; the drop is buried in a migration from the previous quarter | Recreate from the `indexdef` recorded before dropping. Prevent by checking `stats_reset` age (§8.3) before trusting `idx_scan = 0` |
| **Index on a low-cardinality column** | No improvement at all; the index is never chosen but is written on every insert | `idx_scan = 0` on an index over a boolean/enum column | Drop it. If one value is rare and queried, replace it with a **partial index** on that value (§4.10) |
| **`idx_scan` reset by a major-version upgrade** | An essential index is dropped as "unused" shortly after an upgrade, causing a sudden outage on a core query | `pg_stat_database.stats_reset` is only days old | Recreate the index. Always check the statistics window before acting on `idx_scan` |
| **Index-only scan silently degrading** | A query that was 0.4 ms creeps to 2.4 ms under write load, with the same plan shape | `EXPLAIN ANALYZE` shows `Index Only Scan` with a **non-zero `Heap Fetches`** | Vacuum the table so the visibility map is set. Chronic cases need more aggressive autovacuum, not a different index |
| **Random UUID primary key on InnoDB** | Insert throughput degrades sharply once the clustered index exceeds the buffer pool | Rising page-split rate and buffer-pool miss rate; insert latency growing with table size | Migrate to a time-ordered key (UUIDv7/ULID) or an auto-increment surrogate with the UUID as a secondary unique index |

## 12. Alternatives

| Approach | How it works | Read benefit | Write cost | Best when |
|---|---|---|---|---|
| **B-tree index** | Balanced sorted tree; leaves point into the heap (or contain rows, in InnoDB) | Equality, ranges, prefix matches, `ORDER BY`, `MIN`/`MAX` | 1 random page write per insert | The default. High-cardinality columns with selective predicates. Covered in depth in Phase 3, Topic 15 |
| **Hash index** | Hash of the key → bucket | Equality **only**; slightly smaller and faster than B-tree for that one case | similar to B-tree | Rarely worth it — a B-tree does equality nearly as well *and* ranges |
| **Partial index** | B-tree over a filtered subset of rows | Full index speed on the subset | Written only when a row enters/leaves the predicate | A skewed column where queries target the rare value: `status='pending'`, `deleted_at IS NULL` |
| **Covering index (`INCLUDE`)** | Extra non-key columns in the leaves | Index-only scan: eliminates all heap I/O (§9 step 4b, 6× faster) | Larger index (+80% in §9) ⇒ proportionally more write cost | A hot query reading few columns, on a well-vacuumed table |
| **Expression index** | Index on `lower(email)` or `(payload->>'sku')` | Makes an otherwise non-sargable predicate indexable | Expression evaluated on every write | Case-insensitive lookups, JSON field access, computed keys |
| **BRIN** | Min/max summary per block range | Narrows a scan to a few ranges; ~0.03% of a B-tree's size (§9 step 7) | Near zero | Huge append-only tables where the column correlates with physical order (timestamps, sequential IDs) |
| **GIN / inverted index** | Maps each *element* (word, array item, JSON key) to the rows containing it | Full-text search, array containment, JSONB queries (Lesson 47) | Expensive; mitigated by a pending-list buffer | Multi-valued columns where a B-tree cannot express the query |
| **Partitioning** | Split the table by range or hash; the planner eliminates whole partitions (Phase 3, Topic 21) | Prunes 99%+ of data with **zero** index maintenance | None beyond routing | Time-series and tenant-scoped data where nearly every query filters on the partition key |
| **Materialised view** | Pre-compute and store the result of an expensive query | Turns an aggregation over 50 M rows into a single-row read | Refresh cost, and staleness between refreshes | Dashboards and reports tolerant of minutes-old data |
| **Application cache** | Keep hot results in Redis/memcached (Phase 2, Topics 10–12) | Microseconds; removes the database from the path entirely | Invalidation complexity; stale reads | Read-heavy, repeat-access data with an acceptable staleness window |
| **Denormalisation** | Store a pre-joined or pre-aggregated column | Removes the join and the index needed for it | Every write must keep the duplicate consistent | A join that cannot be made fast and whose inputs change rarely |
| **Search engine** | Push the query to Elasticsearch/OpenSearch (Lesson 47) | Relevance ranking, fuzzy matching, faceting — none of which a B-tree does | A second system to sync, with eventual consistency | Text search and faceted browse. BookNook moved admin email search here (§7.1) |
| **LSM-tree storage** | Buffer writes in memory, flush sorted immutable files | Still indexed, but reads may consult several files | Sequential rather than random writes; much higher ingest rate | Write-dominated workloads (§7.2) |
| **Column store** | Store each column contiguously, compressed | Scans one column of 50 M rows in milliseconds; indexes are largely unnecessary | Poor single-row updates | Analytics and aggregation over few columns, many rows |

The important framing: the row of this table you reach for depends on the **shape of the access pattern**, not on how slow the query currently is. A slow aggregate over 40% of the table is not an index problem — no index helps at 40% selectivity (§9 step 5). It is a materialised-view, partitioning, or column-store problem.

<!--CONT2-->




---

# Lesson 54 — B-Tree, Hash, Composite, Covering, and Secondary Indexes

*Phase 3, Topic 15*

## 1. Simple definition

An **index type** is the *data structure* a database uses to physically organise the lookup keys it maintains on your behalf.

Lesson 53 covered *what an index is* — a separate, ordered, redundant copy of one or more columns plus a pointer back to the row — and *what it costs you* (selectivity, write amplification, storage). It deliberately treated the index as a black box. This lesson opens the box.

> *"I know an index makes reads faster. But **which** index, on **which** columns, in **which** order, and what exactly can it and cannot it do?"*

Terms defined before use:
- **Page** (also **block**): the fixed-size unit the database reads from and writes to disk. PostgreSQL uses 8 KB; MySQL InnoDB uses 16 KB; SQL Server uses 8 KB. The database never reads "one row" from disk — it reads the whole page containing that row.
- **Heap**: the unordered collection of pages where the actual table rows live. "Heap" means "pile" — rows sit wherever there was space when they were inserted.
- **Fanout**: how many child pointers a single index page can hold. High fanout means a shallow tree.
- **Tuple identifier** (PostgreSQL **TID**, Oracle **ROWID**): a physical address of a row, typically `(page_number, slot_number)`. Six bytes in PostgreSQL.
- **Index-only scan**: answering a query entirely from the index, never touching the heap.
- **Fill factor**: the percentage of each index page the database deliberately leaves empty on creation, so later inserts have room without splitting the page.

The one-sentence summary of the whole lesson: **a B+tree is the right answer roughly 90% of the time, and the other 10% is worth knowing precisely because the failure mode of using the wrong structure is silent — the index exists, consumes storage, slows writes, and is never used.**

## 2. Why it exists

BookNook's `orders` table has 50 million rows. Lesson 53 established that a sequential scan of that table reads roughly 4 GB and takes several seconds. An index turns that into a handful of page reads. But "an index" is not one thing, and the differences are not cosmetic — they change what queries are *possible*:

| BookNook query | B+tree | Hash | GIN |
|---|---|---|---|
| `WHERE order_id = 918273` | ✅ | ✅ | ❌ |
| `WHERE created_at > '2026-01-01'` | ✅ | ❌ (no ordering) | ❌ |
| `ORDER BY created_at LIMIT 20` | ✅ (free) | ❌ | ❌ |
| `WHERE tags @> ARRAY['signed']` | ❌ | ❌ | ✅ |
| `WHERE metadata->>'gift' = 'true'` | ⚠️ expression index only | ⚠️ | ✅ |

A hash index on `created_at` is not "a slightly worse B+tree". It is a structure that **physically cannot** answer a range query, because hashing destroys ordering by design. Choosing it is not a performance mistake; it is a correctness-of-design mistake that shows up as a sequential scan in production.

Three distinct pressures created this zoo of index types:

1. **Disk is read in pages, not rows.** Any structure with low fanout (a binary search tree, a skip list) needs ~30 page reads for 1 billion rows. A B+tree needs 3–4. On spinning disks that was a 1000× difference; on NVMe SSDs it is still a 10× difference, and in terms of *buffer-cache pressure* it remains decisive.
2. **Different access patterns have different optimal structures.** Equality, range, prefix, containment, proximity, and similarity are mathematically different questions.
3. **Reads and writes pull in opposite directions.** A structure optimised for in-place ordered reads (B+tree) pays random-write cost; a structure optimised for sequential writes (LSM-tree, Lesson 45) pays read-amplification cost.

Without this lesson, the common outcome is an `orders` table with eleven indexes, nine of which are never used by any query, each adding ~15% to every `INSERT` and `UPDATE` (Lesson 53's write amplification), and the one query that is actually slow is still slow.

## 3. Real-world analogy

**A very large reference library, with several different finding aids.**

- **B+tree** = the classic card catalogue in drawers. Drawers are labelled `A–C`, `D–F`, … Inside drawer `D–F` are dividers `DA–DH`, `DI–DN`, … Inside those are the actual cards, filed alphabetically, and — crucially — **each card tray has a note on the back saying "continued in the next tray"**. You can open at "Dickens" and then walk forward through "Dickinson, Diderot, …" without going back to the top. Three or four levels of drawer-opening gets you to any card out of millions.
- **Hash index** = a cloakroom ticket. Ticket 4471 goes directly to peg 4471. One step, no searching. But there is no sense in which ticket 4472 is "next to" 4471's coat in any meaningful order, so "give me all coats checked in between 2pm and 3pm" is impossible.
- **Composite index on (a, b, c)** = cards filed by *Author*, then *Series*, then *Volume*. You can find "Pratchett", or "Pratchett / Discworld", or "Pratchett / Discworld / 12". You **cannot** use this catalogue to find "all Volume 12s" — those cards are scattered across every author drawer in the building.
- **Covering index** = the card itself has the summary, the publication year and the shelf location printed on it. For many questions you never walk to the shelf at all.
- **Secondary index** = a second catalogue filed by ISBN that gives you the *author/title*, which you then look up in the main catalogue. Two lookups, not one.
- **Partial index** = a small separate card box on the desk containing only "currently on loan" items. 5% of the cards, 100% of the answers for loan questions.
- **GIN index** = the subject index at the back of a book: one entry per *keyword*, each listing many page numbers. Built for "which documents contain X", not "sort documents by X".

**Where the analogy breaks down:**

| Reference library | Database index |
|---|---|
| A librarian refiles cards overnight, in bulk, while nobody is reading | Every `INSERT` updates every index **synchronously, inside the transaction**, before the user's write is acknowledged (Lesson 49) |
| Opening a drawer costs the same whether it is a popular drawer or not | The top 2–3 levels of a B+tree are almost always in RAM; only the leaf page read is genuinely physical. Cost is wildly non-uniform |
| A card either exists or does not | Under MVCC (Lesson 52) an index entry may point to a row version that is invisible to *your* transaction, so the database must still visit the heap or a visibility map to check |
| The catalogue is exactly as big as the number of books | A B+tree index is typically 30–60% larger than the raw key data because of fill factor, page headers, and internal levels |
| Adding a second catalogue costs shelf space only | Adding a second index costs shelf space **and slows every write to every other part of the system**, because WAL volume and buffer-cache competition increase (Lesson 53) |
| A card points at a fixed shelf location forever | In PostgreSQL every `UPDATE` can relocate the row, which may require updating every index; in InnoDB the primary key is the address, so a changing primary key is catastrophic |

That penultimate row is the one people forget. **An index is not a read-only addition to a system.** It is a write-path obligation that happens to help reads.

## 4. Detailed technical explanation

### 4.1 The B+tree — the default for almost everything

Nearly every "B-tree index" in a modern database (PostgreSQL, MySQL InnoDB, SQL Server, Oracle, SQLite) is in fact a **B+tree**. The distinction matters:

- In a **B-tree**, data pointers can live in internal nodes as well as leaves.
- In a **B+tree**, **internal nodes contain only keys and child pointers; all data pointers live in the leaf level**, and **leaf pages are linked to their siblings**.

Both properties exist for the same reason: *more keys per internal page → higher fanout → shallower tree → fewer page reads*, and *linked leaves → range scans are sequential walks, not repeated root-to-leaf descents*.

**Internal node** (also called a *branch* node) contains, per entry: a separator key and a child page pointer. It answers exactly one question: "which child page do I descend into?" The separator keys are *routing* values; they do not necessarily correspond to any existing row.

**Leaf node** contains, per entry: the indexed key value and a TID pointing at the heap row (or, in a clustered index, the row itself). Leaf nodes also contain `prev` and `next` page pointers.

**Root node** is the single entry page. It is, in practice, permanently resident in the buffer cache of any busy database.

#### Why depth stays at 3 or 4 — the arithmetic, explicitly

Assume PostgreSQL defaults. State every assumption:

- Page size: **8,192 bytes**.
- Page header + special area overhead: ≈ **24 bytes**, leaving ≈ **8,168 usable bytes**.
- Key type: `bigint` = **8 bytes**.
- Leaf entry = 8-byte key + 6-byte TID + 4-byte item pointer + alignment ≈ **20 bytes**.
- Internal entry = 8-byte key + 4-byte page pointer + 4-byte item pointer ≈ **16 bytes**.
- Default B-tree fill factor: **90%** at build time, but pages drift toward ≈ **70%** occupancy after random inserts and splits. Use 70% for a steady-state estimate (approximate, typical).

Raw capacities:

```
Leaf entries per page     = 8,168 / 20  ≈ 408   → at 70% occupancy ≈ 285
Internal fanout per page  = 8,168 / 16  ≈ 510   → at 70% occupancy ≈ 357
```

Now build upward for **1 billion rows**:

```
Level 4 (leaves)   : 1,000,000,000 / 285 =  3,508,772 pages
Level 3 (internal) :     3,508,772 / 357 =      9,829 pages
Level 2 (internal) :         9,829 / 357 =         28 pages
Level 1 (root)     :            28 / 357 =          1 page
```

**Four levels for a billion rows.** A point lookup touches 4 index pages plus 1 heap page = **5 page reads**, of which levels 1–2 (29 pages, ~232 KB) are certainly cached and level 3 (9,829 pages, ~77 MB) is usually cached. The realistic physical I/O is **1–2 reads**.

The reason this works is that depth grows as `log_fanout(N)`. With fanout 357, each extra level multiplies capacity by 357. Going from 1 billion to 357 billion rows adds **one** level. This is why B+trees are described as having "effectively constant" depth over any realistic data size — it is not literally constant, it is logarithmic with a base of several hundred.

> **Simplified teaching model vs production reality.** The arithmetic above assumes fixed-width keys and uniform occupancy. Production reality: variable-length `text` keys reduce fanout dramatically (a 40-byte key gives fanout ≈ 120, and 1 billion rows then needs 5 levels); PostgreSQL applies *suffix truncation* to separator keys in internal pages to claw some of that back; and heavily updated indexes bloat below 70% until `REINDEX` or a `VACUUM` cycle reclaims space.

#### Leaf-level linked lists and range scans

Because leaves are doubly linked, `WHERE created_at BETWEEN '2026-01-01' AND '2026-01-31'` costs: one root-to-leaf descent (4 page reads) to find the first qualifying entry, then a **sequential walk** along `next` pointers until the key exceeds the upper bound. The per-row cost after the descent is near zero. This single property is why B+trees dominate: range scans, `ORDER BY`, `LIMIT`, `MIN`, `MAX`, prefix matching (`LIKE 'Pratch%'`), and merge joins (Lesson 55) all fall out of it for free.

A B+tree index also provides **free sorted output**. `ORDER BY created_at LIMIT 20` with an index on `created_at` reads 20 leaf entries and stops. Without the index it must sort 50 million rows. That is not a 2× difference; it is the difference between 0.5 ms and 12 seconds.

#### Node splits and merges

- **Split:** inserting into a full leaf page splits it into two roughly half-full pages, pushing a separator key up into the parent. If the parent is full, it splits too, recursively. If the root splits, a **new root is created and the tree grows one level** — the only way a B+tree grows in height, and the reason height increases are rare and uniform (every leaf stays at the same depth; the tree is always perfectly balanced).
- **Right-most insert optimisation:** monotonically increasing keys (`bigserial` order IDs, `created_at` timestamps) always insert at the right edge. A naive 50/50 split would leave every page half empty forever. Real implementations detect this and perform a **90/10 split**, keeping pages densely packed. This is why a `bigserial` primary key index is ~40% smaller than a random-UUID index over the same row count.
- **Merge / deletion:** classic textbook B-trees merge under-full sibling pages. **PostgreSQL does not merge pages.** It marks entries dead, and a page is only recycled when it becomes *completely* empty. The consequence is **index bloat**: delete 90% of a table and the index stays nearly the same size until `REINDEX CONCURRENTLY`. InnoDB does merge pages when occupancy falls below `MERGE_THRESHOLD` (default 50%). This is a genuine, observable behavioural difference between engines — not a detail.

#### Why B+trees remain the default

1. They serve equality, range, prefix, ordering, min/max and uniqueness with one structure.
2. Their depth is effectively constant across any realistic data size.
3. They are perfectly balanced by construction — there is no pathological input that degrades them, unlike an unbalanced BST.
4. They degrade gracefully: a partially cached B+tree is still fast because the hot upper levels are small.
5. Every optimiser, replication system, and backup tool has decades of engineering invested in them.

A specialised structure must beat all of that on a specific axis to be worth it. Most do not.

### 4.2 Hash indexes

A hash index stores `hash(key) → TID` in buckets. Lookup is **O(1)**: compute the hash, go to the bucket, scan a handful of entries.

What you gain:
- Equality lookups avoid the root-to-leaf descent entirely — typically 1–2 page reads instead of 4–5.
- For **long keys**, the index is much smaller: hashing a 200-byte URL to 4 bytes means the index stores 4 bytes, not 200. PostgreSQL B-trees outright **reject** keys larger than ~2,704 bytes (one third of a page); a hash index accepts any length.

What you lose, permanently and by design:
- **No range queries.** Hashing is order-destroying. `WHERE x > 5` cannot use it.
- **No `ORDER BY` support**, no `MIN`/`MAX` shortcut, no prefix matching.
- **No multi-column support** in PostgreSQL (hash indexes are single-column only).
- **No index-only scans** — the hash is stored, not the value, so the original value cannot be reconstructed.
- **No uniqueness enforcement** in PostgreSQL.

When hash indexes are *genuinely* worth it: a single very large-text column, queried only by exact equality, at high volume. BookNook's `sessions(session_token TEXT)` — 64-character tokens, looked up by exact match on every single request, never ranged, never sorted — is the textbook case. Measured benefit is typically 10–30% on lookup latency and a 2–4× smaller index. That is real but modest.

> Historical note worth knowing for interviews: PostgreSQL hash indexes were **not WAL-logged before version 10**, meaning they were not crash-safe and not replicated. Many engineers' blanket "never use hash indexes in Postgres" advice dates from that era and is now out of date — but the ordering limitations are permanent.

### 4.3 Composite (multi-column) indexes and the leftmost-prefix rule

A composite index on `(a, b, c)` sorts entries by `a`, then by `b` within equal `a`, then by `c` within equal `(a, b)`. It is **one** index with a compound sort key — not three indexes.

This sort order produces the **leftmost-prefix rule**: the index is usable only for predicates that form an unbroken prefix of the key, with at most one range at the end.

Definitive table for an index on `(a, b, c)`:

| Query predicate | Usable? | How much of the index is used | Why |
|---|---|---|---|
| `WHERE a = 1` | ✅ Yes | `a` | Prefix of length 1 |
| `WHERE a = 1 AND b = 2` | ✅ Yes | `a, b` | Prefix of length 2 |
| `WHERE a = 1 AND b = 2 AND c = 3` | ✅ Yes | `a, b, c` | Full key |
| `WHERE a = 1 AND c = 3` | ⚠️ Partially | `a` only; `c` applied as an in-index filter | `b` is skipped, so `c` is not contiguous |
| `WHERE b = 2` | ❌ No (seek) | none | `b` values are scattered across all `a` groups |
| `WHERE c = 3` | ❌ No (seek) | none | Same reason, worse |
| `WHERE b = 2 AND c = 3` | ❌ No (seek) | none | No leading column |
| `WHERE a = 1 AND b > 5` | ✅ Yes | `a, b` | Equality then range — ideal shape |
| `WHERE a > 1 AND b = 5` | ⚠️ Partially | `a` only; `b` filtered | A range on `a` makes `b` non-contiguous |
| `WHERE a = 1 ORDER BY b` | ✅ Yes | `a`, plus free sort on `b` | Entries already in `b` order within `a = 1` |
| `WHERE a = 1 ORDER BY c` | ❌ Sort required | `a` | `c` is ordered only within `(a, b)` pairs |
| `WHERE a = 1 AND b IN (2,3)` | ✅ Yes | `a, b` | Treated as two equality probes |
| `ORDER BY a, b, c LIMIT 10` | ✅ Yes | whole key, no filter | Index order matches sort order exactly |
| `ORDER BY a, c` | ❌ Sort required | — | Not a prefix of the sort key |
| `WHERE a = 1 AND b = 2 ORDER BY c DESC` | ✅ Yes | full key, backward scan | Leaves are doubly linked |

Two important nuances that qualify the ❌ rows:

- **Index skip scan / loose index scan.** If `a` has very few distinct values (say 5), some engines (Oracle for many years; PostgreSQL 18+; MySQL 8 for a restricted set of cases) can enumerate the distinct `a` values and perform 5 sub-searches on `b`. This makes `WHERE b = 2` usable *when the leading column has low cardinality*. Do not rely on it as a design principle.
- **Full index scan.** Even with no usable prefix, the planner may scan the *entire index* rather than the table if the index is much smaller than the heap and covers all needed columns. This is not a "seek"; it is cheaper than a heap scan but still O(index size).

#### Choosing the column order — the rule, with BookNook examples

The ordering rule, in priority order:

1. **Equality columns first.** Every equality predicate narrows the scan to a contiguous block.
2. **Then the column used for ordering**, if a sort can be absorbed.
3. **Then range columns**, and only one range column can be used for seeking.
4. Among equality columns, if several are available, put the **more selective** one first — but this matters far less than people think, because once all equality columns are in the key, the leaf block reached is the same size either way. It matters mainly for *index reuse by other queries*: putting the column that appears in the most queries first lets one index serve several.

BookNook worked examples:

```
Query A: WHERE customer_id = ? ORDER BY created_at DESC LIMIT 20
  → INDEX (customer_id, created_at DESC)
  Equality column first, sort column second. The planner descends to the
  customer_id block, then reads 20 leaf entries backwards. No sort node at all.

Query B: WHERE customer_id = ? AND status = 'shipped' AND created_at > ?
  → INDEX (customer_id, status, created_at)
  Two equality columns, then the range. The range must be last.
  INDEX (customer_id, created_at, status) is WRONG: the range on created_at
  breaks contiguity, so status degrades to a filter and the scan reads every
  order that customer ever placed in the period, not just shipped ones.

Query C: WHERE status = 'pending' AND warehouse_id = ? ORDER BY created_at
  → INDEX (status, warehouse_id, created_at)   -- or a partial index, see 4.7
  status has 6 distinct values, warehouse_id has 40. By pure selectivity you
  would lead with warehouse_id. Lead with status instead, because every
  fulfilment query filters status and only some filter warehouse_id — one
  index then serves the whole query family.
```

**Equality-before-range is the single highest-value rule in this lesson.** It is also the most common thing experienced engineers get wrong under time pressure.

### 4.4 Covering indexes and `INCLUDE`

An **index-only scan** is a plan where the database answers the query from index leaf pages alone, never visiting the heap. For a 50M-row table this typically removes one random heap read per result row — on a 1,000-row result set, that is 1,000 random reads eliminated.

An index **covers** a query when every column the query references (in `SELECT`, `WHERE`, `ORDER BY`, `GROUP BY`) is present in the index.

Two ways to cover:

- **Add columns to the key:** `INDEX (customer_id, created_at, total_cents)`. The extra column participates in sorting and uniqueness, enlarges internal nodes, and reduces fanout.
- **`INCLUDE` (PostgreSQL 11+, SQL Server, Oracle "index with included columns"):** `INDEX (customer_id, created_at) INCLUDE (total_cents)`. The payload column is stored **only in leaf pages**, is not sorted, cannot be used for seeking, and does not affect uniqueness. Fanout of internal nodes is unaffected.

Use `INCLUDE` when the column is only *returned*, never *filtered or sorted on*. Use a key column when it is filtered or sorted on. `INCLUDE` on a `UNIQUE` index is especially valuable: you can cover a query without weakening the uniqueness constraint.

**The MVCC caveat that catches everyone.** In PostgreSQL an index entry does not record whether its row version is visible to your transaction. An index-only scan therefore consults the **visibility map** — one bit per heap page saying "all tuples on this page are visible to everyone". If the bit is set, the heap is skipped. If not, the heap *is* visited and you get no benefit. The visibility map is maintained by `VACUUM`. Consequence: **on a heavily updated table that is not being vacuumed aggressively, index-only scans silently stop being index-only.** `EXPLAIN ANALYZE` reveals this as `Heap Fetches: <large number>` (Lesson 55). InnoDB and SQL Server use undo-log / version-store designs and do not have this exact behaviour.

### 4.5 Secondary indexes and the InnoDB double lookup

A **primary / clustered index** determines the physical order of rows. A **secondary index** is any other index.

Two fundamentally different designs:

**PostgreSQL (heap-organised).** The table is a heap; *all* indexes are secondary; every index leaf stores a TID. The primary key index is not special. Every index lookup costs: descend index → get TID → read heap page.

**MySQL InnoDB (index-organised / clustered).** The table *is* the primary key B+tree — leaf pages contain the full rows. A secondary index leaf stores `(secondary_key, primary_key_value)` — **not** a physical address. So a secondary index lookup is a **double lookup**:

```
1. Descend secondary index on email   (≈3 page reads) → yields order_id = 918273
2. Descend clustered PK index         (≈3 page reads) → yields the full row
   Total ≈ 6 page reads instead of ≈ 4
```

Three consequences that follow directly and are frequently asked about in interviews:

1. **Keep the InnoDB primary key narrow.** It is duplicated inside *every* secondary index. A 16-byte `UUID` primary key with 5 secondary indexes on a 50M-row table stores 16 × 5 × 50M = **4 GB** of redundant primary key data versus 2 GB for an 8-byte `bigint`.
2. **Random UUID primary keys are pathological in InnoDB**, because inserts land at random positions in the clustered index, causing constant page splits and destroying the 90/10 right-edge optimisation. `UUIDv7` / ULID (time-ordered) fix this and are the correct modern choice if UUIDs are required.
3. **Covering indexes are worth more in InnoDB**, because they eliminate the entire second descent, not merely one heap page read.

### 4.6 Unique indexes as constraint enforcement

A `UNIQUE` index is simultaneously a performance structure and a **correctness mechanism**. The uniqueness check happens inside the index insert path, under a page-level lock, which is what makes it atomic and race-free. This is the only reliable way to enforce uniqueness in a concurrent system — an application-level `SELECT ... then INSERT` check is a textbook race condition (Lesson 52).

In PostgreSQL, `PRIMARY KEY` and `UNIQUE` constraints are *implemented as* unique B-tree indexes; dropping the index means dropping the constraint. `NULL`s are distinct from each other by default, so multiple `NULL`s are permitted — `UNIQUE NULLS NOT DISTINCT` (PostgreSQL 15+) changes that.

### 4.7 Partial / filtered indexes

A **partial index** (PostgreSQL) or **filtered index** (SQL Server) indexes only rows matching a `WHERE` clause. MySQL has no equivalent — the closest workaround is a generated column plus an index on it.

The win is proportional to how small the qualifying subset is, and it is a win on **three** axes at once: storage, write cost (rows that do not match the predicate are never touched on insert), and cache residency.

The planner uses a partial index only when it can *prove* the query's predicate implies the index's predicate. `WHERE status = 'pending'` matches an index defined `WHERE status = 'pending'`. `WHERE status = $1` with a parameter does **not** — the proof cannot be made at plan time for a generic plan. This is a sharp edge: partial indexes work best with literal predicates or constants, not bind parameters.

### 4.8 Expression / functional indexes

An index on a *computed value*: `CREATE INDEX ON customers (lower(email));`

This exists because **any function applied to an indexed column in a `WHERE` clause disqualifies the plain index**. `WHERE lower(email) = 'a@b.com'` cannot use `INDEX (email)` — the index is sorted by `email`, not by `lower(email)`, and the database will not assume anything about what `lower()` does to ordering. An expression index stores the computed result, restoring usability. The expression in the query must match the index's expression essentially textually.

Caveats: the function must be `IMMUTABLE` (same input always gives same output, forever). `now()`-dependent expressions are rejected. The function is executed on every insert and update, adding CPU to the write path.

### 4.9 GIN and GiST — multi-value and non-scalar data

A B+tree maps **one key to one row**. GIN inverts that.

**GIN (Generalised Inverted Index)** stores, for each *element* extracted from a column, a compressed posting list of all rows containing it. Built for arrays, `JSONB` containment, and full-text search (Lesson 47 covered the search layer; GIN is the index that makes `tsvector` matching feasible). Reads are fast; writes are expensive because one row touches many index entries. PostgreSQL mitigates this with the `fastupdate` pending list, which batches insertions — meaning a GIN index can report excellent write throughput right up until the pending list flushes, which is a latency spike you should expect.

**GiST (Generalised Search Tree)** is a *framework* — a balanced tree where you supply the consistency, union and penalty operators. Used for geometric data, ranges (`tstzrange` exclusion constraints), nearest-neighbour ordering (`ORDER BY point <-> point LIMIT 10`), and trigram similarity. GiST is **lossy**: it can return false positives which must be re-checked against the heap, but never false negatives.

Rule of thumb (approximate): for `JSONB` and full-text, GIN is 3–10× faster to query and 2–5× slower to update than GiST. For geometry and nearest-neighbour, GiST is the only option.

### 4.10 BRIN — Block Range INdex

BRIN stores, per *range of heap pages* (default 128 pages = 1 MB), the min and max value of the indexed column. A query scans the tiny BRIN summary, discards ranges whose min/max cannot match, and sequentially scans only the surviving ranges.

- **Size:** astonishingly small. A BRIN index on a 50M-row, 4 GB table is typically **under 1 MB** — roughly 1/1000th the size of the equivalent B-tree.
- **Precondition:** the column must be **physically correlated** with heap order. Append-only time-series data (`created_at` on an insert-only log) is nearly perfectly correlated. If correlation is poor, every range's min/max spans the whole domain, nothing is discarded, and BRIN degenerates to a full sequential scan *plus* wasted effort.
- Check `correlation` in `pg_stats` before considering BRIN. Above ~0.9 it works; below ~0.5 it is useless.

BRIN is the correct answer for BookNook's `order_events` audit log — 2 billion append-only rows, queried only by time window, where a B-tree would cost 40 GB and BRIN costs 1 MB.

### 4.11 LSM-tree indexes versus B+trees

Lesson 45 introduced **LSM-trees** (Log-Structured Merge trees) as the storage engine behind RocksDB, Cassandra, ScyllaDB, HBase, and MyRocks. The contrast with B+trees is the fundamental read/write trade-off in storage engineering:

| | B+tree | LSM-tree |
|---|---|---|
| Write path | Locate page, modify **in place**, WAL-log. Random I/O. | Append to in-memory memtable; flush sorted runs sequentially. Sequential I/O. |
| Write amplification | ~2–10× (WAL + page writes + full-page images) | ~10–30× over the file's lifetime from compaction, but **off the critical path** |
| Read path | ~4 page reads, deterministic | Check memtable, then each SSTable level; bloom filters prune most. Variable, typically 1–3 reads |
| Read amplification | Low and predictable | Higher and variable |
| Space amplification | Bloat from splits and dead entries; ~1.3–1.6× | Compaction reclaims aggressively; ~1.1× with good compression |
| Range scans | Excellent (linked leaves) | Good but must merge across levels |
| Latency profile | Stable | **Spiky** — compaction stalls cause p99 excursions |

Choose LSM when the workload is write-dominated, keys are written once and read rarely (events, metrics, logs), and p99 spikes during compaction are tolerable. Choose B+tree when reads dominate, latency must be predictable, and you need rich range/ordering semantics. BookNook's `orders` table is B+tree territory; BookNook's clickstream ingestion is LSM territory.

## 5. Request / data flow

A single BookNook request, `GET /customers/4471/orders?limit=20`, traced from arrival to response, assuming `INDEX (customer_id, created_at DESC) INCLUDE (total_cents, status)`:

1. The application issues `SELECT order_id, created_at, total_cents, status FROM orders WHERE customer_id = 4471 ORDER BY created_at DESC LIMIT 20`.
2. The parser produces a parse tree; the rewriter expands views and rules.
3. The **planner** (Lesson 55) enumerates access paths: sequential scan, each candidate index. It consults statistics and estimates that `customer_id = 4471` matches ≈ 28 rows.
4. The planner notices the index key order `(customer_id, created_at DESC)` already satisfies the requested `ORDER BY`, so **no sort node is required**, and the `LIMIT` can terminate the scan early. It selects an index-only scan.
5. The executor asks the buffer manager for the index **root page**. It is in shared buffers. Hit.
6. Binary search within the root yields the child pointer for key range containing `4471`. Request **internal level 2** page. Buffer hit.
7. Binary search again → request **internal level 3** page. Buffer hit (this level is ~77 MB and mostly cached).
8. Request the **leaf page**. Buffer miss. A physical read is issued to the OS, which may itself hit the page cache or the SSD. Cost: ~0.1 ms from NVMe.
9. Binary search within the leaf locates the first entry with `customer_id = 4471`. Because the key is `DESC` on `created_at`, that first entry is already the newest order.
10. For each entry, the executor checks the **visibility map** bit for the referenced heap page. All bits set → no heap access required; `total_cents` and `status` are read from the `INCLUDE` payload in the leaf.
11. Entries are emitted upward. After the 20th, the `LIMIT` node signals "no more needed" and the scan stops. If the 20 entries spanned a leaf boundary, the `next` sibling pointer is followed — one extra page read.
12. Rows are serialised into the wire protocol and streamed to the application.
13. **Meanwhile, on the write path:** a concurrent `INSERT INTO orders` must add one entry to *this* index and to every other index on `orders`, inside the same transaction, WAL-logging each change before the commit is acknowledged. If the target leaf page is full, a split occurs — up to 3 pages written plus a parent update.

The entire read is 4–5 logical page accesses, typically 1 physical read, ~0.3 ms. Step 13 is what you paid for it.

## 6. ASCII diagram

A B+tree on `orders.order_id`, showing internal nodes, leaf nodes, sibling links and heap pointers. (Fanout is shown as 3–4 for legibility; real fanout is ~350.)

```
                                  ROOT (internal, level 1)
                         ┌────────────────────────────────────┐
                         │  [ 400 | 800 ]                     │
                         └───┬──────────┬──────────┬──────────┘
            keys < 400 ──────┘          │          └────── keys >= 800
                                 400 <= keys < 800
                 ┌──────────────────────┼───────────────────────┐
                 v                      v                       v
        ┌────────────────┐     ┌────────────────┐      ┌────────────────┐
        │ [ 150 | 280 ]  │     │ [ 520 | 660 ]  │      │ [ 900 | 970 ]  │   internal, level 2
        └──┬────┬─────┬──┘     └──┬────┬─────┬──┘      └──┬────┬─────┬──┘
           │    │     │           │    │     │            │    │     │
   ┌───────┘    │     └────┐      │    │     │            │    │     │
   v            v          v      v    v     v            v    v     v
┌──────┐    ┌──────┐   ┌──────┐ ┌──────┐  ┌──────┐     ┌──────┐   ┌──────┐
│ 101  │    │ 150  │   │ 280  │ │ 400  │  │ 520  │ ... │ 900  │   │ 970  │   LEAF LEVEL
│ 118  │<──>│ 166  │<─>│ 291  │<│ 455  │<>│ 544  │ ... │ 931  │<─>│ 988  │
│ 132  │    │ 203  │   │ 344  │ │ 489  │  │ 601  │     │ 962  │   │ 999  │
└──┬───┘    └──┬───┘   └──┬───┘ └──┬───┘  └──┬───┘     └──┬───┘   └──┬───┘
   │  ^        │          │        │         │            │          │
   │  └────────┴──────────┴────────┴─────────┴────────────┴──────────┘
   │        doubly-linked sibling pointers  →  range scans walk sideways
   │
   │  each leaf entry = (key, TID)       TID = (heap_page, slot)
   v
┌──────────────────────────────────────────────────────────────────────┐
│  HEAP (unordered table pages)                                        │
│  page 17: [ row#3: id=118 ... ] [ row#1: id=931 ... ] [ ... ]        │
│  page 42: [ row#7: id=101 ... ] [ row#2: id=544 ... ] [ ... ]        │
│  → note: physically UNORDERED. The index supplies the order.         │
└──────────────────────────────────────────────────────────────────────┘

POINT LOOKUP  id = 544:   root → level2 → leaf → heap      = 4 page reads
RANGE SCAN    520..999:   root → level2 → leaf, then walk  = 3 + k reads
                          `next` pointers (no re-descent)
INDEX-ONLY:   if all needed columns live in the leaf,
              the heap box above is never touched.
```

Contrast, same data, as a **hash index**:

```
   hash(544) % 8 = 3
   ┌───┬───┬───┬────────────┬───┬───┬───┬───┐
   │ 0 │ 1 │ 2 │     3      │ 4 │ 5 │ 6 │ 7 │   bucket array
   └───┴───┴───┴─────┬──────┴───┴───┴───┴───┘
                     v
              [544→TID] [117→TID] [982→TID]   ← one bucket, NO ORDER
   Lookup of 544: O(1), one bucket read.
   Range 520..999: IMPOSSIBLE. 520 and 521 are in unrelated buckets.
```

## 7. Detailed example

### Example A — BookNook, the continuing example

BookNook's `orders` table: 50 million rows, ~80 bytes per row, heap ≈ 4.2 GB. Schema (simplified):

```
orders(order_id bigserial PK, customer_id bigint, status text,
       warehouse_id int, created_at timestamptz, total_cents bigint,
       coupon_code text, metadata jsonb, tags text[])
```

Five real query shapes from the service, and the index each one needs:

**Shape 1 — "My orders" page.** `WHERE customer_id = ? ORDER BY created_at DESC LIMIT 20`, 3,000 req/s.
`INDEX (customer_id, created_at DESC) INCLUDE (status, total_cents)`.
Equality then sort; `INCLUDE` makes it index-only. Before: 4.2 s sequential scan. After: ~0.3 ms. The `INCLUDE` removes 20 random heap reads per request — at 3,000 req/s that is 60,000 random reads/s of eliminated I/O, which is the difference between needing provisioned IOPS and not.

**Shape 2 — Warehouse fulfilment queue.** `WHERE status = 'pending' AND warehouse_id = ? ORDER BY created_at`, 50 req/s, but `status = 'pending'` is only 1.4 million of 50 million rows (2.8%).
`INDEX (warehouse_id, created_at) WHERE status = 'pending'` — a partial index. Key observation: `status` does not need to be in the key at all, because the index *only contains* pending rows. The index is 2.8% the size of a full one, and inserts of already-shipped historical rows never touch it. Section 9 quantifies this.

**Shape 3 — Case-insensitive coupon lookup.** `WHERE upper(coupon_code) = ?`.
`INDEX (upper(coupon_code))` — an expression index. Without it, `INDEX (coupon_code)` is silently unusable and the query sequentially scans. This is the single most common "I have an index, why is it not used" cause.

**Shape 4 — Admin search by tag.** `WHERE tags @> ARRAY['pre-order']`.
`INDEX USING GIN (tags)`. A B-tree on `tags` would index the *whole array as one value* — it could answer `tags = ARRAY['pre-order']` exactly, and nothing useful.

**Shape 5 — Finance monthly export.** `WHERE created_at >= '2026-01-01' AND created_at < '2026-02-01'`, run 4× per month, scanning ~1.3 million rows.
Here the correct answer is arguably **no index**, or BRIN. 1.3 M of 50 M rows is 2.6% — borderline. A B-tree index scan fetching 1.3 M scattered heap rows may be *slower* than a sequential scan of 4.2 GB, because 1.3 M random reads beats 525,000 sequential reads only if the data is cached. Since `orders` is insert-ordered by `created_at`, BRIN gives 99% of the benefit for 0.02% of the storage. Lesson 55 explains exactly how the planner makes this call.

The anti-pattern BookNook actually had in production: eleven indexes on `orders`, including `(status)`, `(warehouse_id)`, `(customer_id)` and `(created_at)` as four separate single-column indexes. The composite `(customer_id, created_at DESC)` made two of them redundant; the partial index made another two redundant. Dropping four indexes cut `INSERT` latency on `orders` from 2.1 ms to 1.3 ms (≈38%) and reclaimed 5.8 GB.

### Example B — a contrasting real-world system: a payments ledger

A card-payments processor maintains an append-only `ledger_entries` table: 8 billion rows, immutable, 40,000 inserts/sec sustained. The access patterns are nothing like BookNook's:

- **Idempotency check** on every single write: `WHERE idempotency_key = ?` where the key is a 64-character client-supplied string. This is an exact-equality lookup on a long, high-cardinality, never-ranged value — the genuine hash-index case, *and* it must be `UNIQUE` for correctness, which in PostgreSQL forces a B-tree. The real-world resolution: a `UNIQUE` B-tree on `md5(idempotency_key)` — an expression index that is simultaneously a constraint and a size reduction from 64 bytes to 16.
- **Reconciliation by time window:** `WHERE posted_at BETWEEN ? AND ?` over billions of rows. A B-tree here would be ~200 GB. BRIN is ~12 MB and works perfectly because the table is strictly append-ordered by `posted_at`. Correlation measured at 0.9998.
- **Per-merchant statements:** `WHERE merchant_id = ? AND posted_at BETWEEN ? AND ?`. Equality then range → `INDEX (merchant_id, posted_at)`. Reversing that order would be a serious, hard-to-spot regression.
- **No updates, ever.** Because rows are immutable, index bloat never occurs and fill factor can be set to 100, raising fanout and shrinking every index by ~30%. BookNook cannot do this because its `orders` rows are updated as status changes.

The contrast is instructive: identical index *types*, radically different index *choices*, driven entirely by mutability, cardinality and physical correlation — not by table size.

## 8. Code or configuration example

The following SQL is **illustrative teaching material, not production-ready**. It omits `CONCURRENTLY` in places for readability, omits transaction management, omits migration tooling, omits tablespace and storage-parameter tuning, and uses literal values a real system would parameterise. Do not paste it into a production migration.

```sql
-- =====================================================================
-- BookNook: index design for the orders table (50M rows)
-- ILLUSTRATIVE ONLY.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Plain B+tree. The default. USING btree is implicit and can be omitted.
-- ---------------------------------------------------------------------
CREATE INDEX idx_orders_created_at
    ON orders USING btree (created_at);        -- supports =, <, >, BETWEEN,
                                               -- ORDER BY, MIN/MAX, merge joins

-- ---------------------------------------------------------------------
-- 2. Composite index: equality column FIRST, sort column SECOND.
--    Serves: WHERE customer_id = ? ORDER BY created_at DESC LIMIT n
-- ---------------------------------------------------------------------
CREATE INDEX idx_orders_cust_created
    ON orders (customer_id,                    -- equality predicate -> leftmost
               created_at DESC)                -- DESC stored in the key so the
                                               -- planner needs NO sort node
    INCLUDE (status, total_cents);             -- payload: leaf pages only,
                                               -- not sorted, not seekable,
                                               -- enables an INDEX-ONLY SCAN

-- ---------------------------------------------------------------------
-- 3. Equality, equality, THEN range. Order is load-bearing.
-- ---------------------------------------------------------------------
CREATE INDEX idx_orders_cust_status_created
    ON orders (customer_id,                    -- equality
               status,                         -- equality
               created_at);                    -- range LAST. Putting
                                               -- created_at before status
                                               -- would demote status to a
                                               -- post-filter and read far
                                               -- more leaf entries.

-- ---------------------------------------------------------------------
-- 4. Partial index: index only the 2.8% of rows that are actually queried.
--    Note status is NOT in the key -- every row in this index IS pending.
-- ---------------------------------------------------------------------
CREATE INDEX idx_orders_pending_queue
    ON orders (warehouse_id, created_at)       -- key columns for the queue view
    WHERE status = 'pending';                  -- PREDICATE. The planner uses
                                               -- this index only if it can
                                               -- PROVE the query implies it.
                                               -- A literal matches; a bind
                                               -- parameter ($1) usually does not.

-- ---------------------------------------------------------------------
-- 5. Expression index. Required because ANY function on an indexed column
--    in WHERE disqualifies the plain index.
-- ---------------------------------------------------------------------
CREATE INDEX idx_orders_coupon_upper
    ON orders (upper(coupon_code));            -- the function must be IMMUTABLE;
                                               -- the query must spell the
                                               -- expression the same way:
                                               --   WHERE upper(coupon_code) = 'X'

-- ---------------------------------------------------------------------
-- 6. Unique index = constraint + structure. The uniqueness check happens
--    atomically inside the index insert, which is why it is race-free.
-- ---------------------------------------------------------------------
CREATE UNIQUE INDEX uq_orders_idempotency
    ON orders (idempotency_key);               -- also rejects duplicate
                                               -- retries from the client;
                                               -- NULLs are distinct by default

-- ---------------------------------------------------------------------
-- 7. Hash index. Equality ONLY. No ranges, no ORDER BY, no uniqueness,
--    no multi-column, no index-only scans. Single-column by definition.
-- ---------------------------------------------------------------------
CREATE INDEX idx_sessions_token_hash
    ON sessions USING hash (session_token);    -- justified because the column
                                               -- is a 64-char token queried
                                               -- ONLY by exact equality.
                                               -- Crash-safe only on PG >= 10.

-- ---------------------------------------------------------------------
-- 8. GIN for containment: arrays, JSONB, full-text (see Lesson 47).
-- ---------------------------------------------------------------------
CREATE INDEX idx_orders_tags_gin
    ON orders USING gin (tags);                -- answers: tags @> ARRAY['signed']
CREATE INDEX idx_orders_meta_gin
    ON orders USING gin (metadata jsonb_path_ops);
                                               -- jsonb_path_ops: ~40% smaller,
                                               -- supports @> only (not ? / ?|)

-- ---------------------------------------------------------------------
-- 9. BRIN: viable ONLY when the column correlates with physical heap order.
-- ---------------------------------------------------------------------
CREATE INDEX idx_orders_created_brin
    ON orders USING brin (created_at)
    WITH (pages_per_range = 64);               -- smaller range = finer
                                               -- granularity = bigger index.
                                               -- Default is 128 pages (1 MB).

-- Verify the precondition BEFORE creating it. Correlation near 1.0 = good,
-- below ~0.5 = BRIN is useless.
SELECT attname, n_distinct, correlation
FROM   pg_stats
WHERE  tablename = 'orders' AND attname = 'created_at';

-- ---------------------------------------------------------------------
-- 10. Build without blocking writes. ALWAYS use this on a live table.
--     CONCURRENTLY cannot run inside a transaction block, takes ~2x longer,
--     and can leave an INVALID index behind if it fails -- which you must
--     then drop and rebuild.
-- ---------------------------------------------------------------------
CREATE INDEX CONCURRENTLY idx_orders_warehouse
    ON orders (warehouse_id);

-- Find failed CONCURRENTLY builds:
SELECT indexrelid::regclass FROM pg_index WHERE NOT indisvalid;

-- ---------------------------------------------------------------------
-- 11. Find indexes nobody uses, before adding more.
--     Caution: idx_scan resets on restart and is per-node, so a replica
--     may be using an index the primary reports as unused.
-- ---------------------------------------------------------------------
SELECT  relname              AS table_name,
        indexrelname         AS index_name,
        idx_scan             AS times_used,       -- 0 = strong drop candidate
        pg_size_pretty(pg_relation_size(indexrelid)) AS size
FROM    pg_stat_user_indexes
WHERE   idx_scan < 50
  AND   NOT indisunique                            -- never drop constraint indexes
ORDER BY pg_relation_size(indexrelid) DESC;

-- ---------------------------------------------------------------------
-- 12. MySQL / InnoDB equivalents, showing the clustered-index difference.
-- ---------------------------------------------------------------------
-- The PRIMARY KEY *is* the table. Keep it narrow and monotonically
-- increasing; every secondary index stores a full copy of it.
CREATE TABLE orders (
  order_id    BIGINT UNSIGNED AUTO_INCREMENT,   -- 8 bytes, sequential: good
  customer_id BIGINT UNSIGNED NOT NULL,
  status      VARCHAR(16)     NOT NULL,
  created_at  DATETIME(6)     NOT NULL,
  PRIMARY KEY (order_id),                       -- clustered: leaves hold rows
  KEY idx_cust_created (customer_id, created_at)-- secondary: leaves hold
                                                -- (customer_id, created_at,
                                                --  order_id) -> then a SECOND
                                                -- descent into the PK tree
) ENGINE=InnoDB;

-- Covering index in InnoDB avoids that second descent entirely:
ALTER TABLE orders
  ADD KEY idx_cust_created_cov (customer_id, created_at, status);
  -- MySQL has no INCLUDE; extra columns must join the key.
  -- Confirm with EXPLAIN: Extra column must read "Using index".
```

## 9. Numerical example

### 9.1 B+tree depth and page reads for BookNook's 50 M row `orders` table

**Assumptions, stated explicitly:**
- PostgreSQL, 8,192-byte pages; ~24 bytes of header/special area ⇒ **8,168 usable bytes**.
- Index on `order_id bigint` ⇒ 8-byte key.
- Leaf entry = 8 (key) + 6 (TID) + 4 (item pointer) + 2 (alignment) = **20 bytes**.
- Internal entry = 8 (key) + 4 (downlink) + 4 (item pointer) = **16 bytes**.
- Steady-state page occupancy after random inserts: **70%** (typical; a freshly built index is ~90%).
- Row count: **50,000,000**.

**Step 1 — entries per page.**
```
Leaf capacity    = 8,168 / 20 = 408.4 → 408 entries
Leaf effective   = 408 × 0.70 = 285.6 → 285 entries per leaf page
Internal capacity= 8,168 / 16 = 510.5 → 510 entries
Internal effective = 510 × 0.70 = 357 downlinks per internal page
```

**Step 2 — leaf level.**
```
Leaf pages = 50,000,000 / 285 = 175,438.6 → 175,439 pages
```

**Step 3 — build upward until one page remains.**
```
Level 3 (internal) = 175,439 / 357 =   491.4 →   492 pages
Level 2 (internal) =     492 / 357 =     1.4 →     2 pages
Level 1 (root)     =       2 / 357 =   0.006 →     1 page
```
**Depth = 4 levels** (root, two internal, leaf).

**Step 4 — the same table with a freshly built, 90%-full index:**
```
Leaf    = 408 × 0.90 = 367 entries → 50,000,000 / 367 = 136,240 leaf pages
Level 2 = 510 × 0.90 = 459 downlinks → 136,240 / 459 = 297 pages
Root    = 297 / 459 = 0.65 → 1 page
```
**Depth = 3 levels.** This is why "a Postgres B-tree on 50 M rows is 3 or 4 levels deep" is both true and imprecise — bloat costs you a level.

**Step 5 — index size.**
```
Total pages (70% case) = 175,439 + 492 + 2 + 1 = 175,934 pages
Size = 175,934 × 8,192 bytes = 1,441,250,  ≈ 1.44 × 10^9 bytes ≈ 1.34 GiB
Size (90% case) = 136,538 × 8,192 ≈ 1.12 × 10^9 bytes ≈ 1.04 GiB
```
Note the index is ~1.3 GB to index 400 MB of raw key data (50 M × 8 bytes). **An index costs roughly 3× its key payload.** That ratio is the one to remember.

**Step 6 — page reads for a point lookup `WHERE order_id = 918273`.**
```
Logical page accesses = 4 index pages + 1 heap page = 5
```
Physical reads, assuming a 16 GB buffer cache:
```
Root (1 page, 8 KB)          → always cached        → 0 physical reads
Level 2 (2 pages, 16 KB)     → always cached        → 0
Level 3 (492 pages, 3.9 MB)  → always cached        → 0
Leaf (175,439 pages, 1.3 GB) → partially cached, say 60% hit → 0.4 reads
Heap (525,000 pages, 4.2 GB) → say 30% hit           → 0.7 reads
Expected physical reads ≈ 1.1
```
At ~0.1 ms per NVMe random read: **≈ 0.11 ms of I/O**, plus ~0.05 ms CPU for four binary searches ⇒ **≈ 0.16 ms total**.

**Step 7 — compare with the sequential scan.**
```
Heap = 4.2 GB. At 1.5 GB/s effective sequential throughput: 2.8 s
Plus ~50,000,000 tuple-visibility checks at ~20 ns = 1.0 s
Total ≈ 3.8 s
Speed-up from the index ≈ 3,800 ms / 0.16 ms ≈ 23,750×
```

**Step 8 — the same arithmetic for 1 billion rows (to show depth growth).**
```
Leaf pages = 1,000,000,000 / 285 = 3,508,772
Level 3    = 3,508,772 / 357     =     9,829
Level 2    =     9,829 / 357     =        28
Root       =        28 / 357     =         1
Depth = 4 levels. Logical reads = 5.
```
**20× more rows, the same depth and the same number of page reads.** This is the entire argument for B+trees in one line.

### 9.2 The partial index — quantifying the 95% reduction

**Assumptions:** `orders` has 50,000,000 rows. Status distribution, measured (typical for a mature bookstore):

| status | share | rows |
|---|---|---|
| `delivered` | 88.0% | 44,000,000 |
| `cancelled` | 6.0% | 3,000,000 |
| `returned` | 3.5% | 1,750,000 |
| `shipped` | 1.8% | 900,000 |
| `pending` | 0.5% | 250,000 |
| `processing` | 0.2% | 100,000 |

The fulfilment queue only ever reads `pending` and `processing` — **350,000 rows = 0.7%** of the table.

**Full index** on `(warehouse_id, created_at)` — entries are 4 + 8 + 6 + 4 + 2 = 24 bytes:
```
Entries per leaf = 8,168 / 24 = 340 → ×0.70 = 238
Leaf pages = 50,000,000 / 238 = 210,084
Internal   = 210,084 / 357 = 589; 589 / 357 = 2; root = 1
Total pages = 210,676 → × 8,192 = 1.726 × 10^9 bytes ≈ 1.61 GiB
```

**Partial index**, same key, `WHERE status IN ('pending','processing')`:
```
Leaf pages = 350,000 / 238 = 1,471
Internal   = 1,471 / 357 = 5; root = 1
Total pages = 1,477 → × 8,192 = 12,099,584 bytes ≈ 11.5 MiB
```

**Reduction:**
```
1,726,000,000 → 12,100,000 bytes
Saving = 1 − (12,100,000 / 1,726,000,000) = 0.99299 → 99.3% smaller
```

That is better than the 95% target. To show the 95% case explicitly, take a looser predicate — index every order from the last 90 days (`created_at > now() - interval '90 days'`), which at BookNook's growth rate is **2,500,000 rows = 5%** of the table:
```
Leaf pages = 2,500,000 / 238 = 10,505
Internal   = 10,505 / 357 = 30; root = 1
Total = 10,536 pages × 8,192 = 86,310,912 bytes ≈ 82.3 MiB
Reduction = 1 − (86,310,912 / 1,726,000,000) = 0.950 → exactly 95.0% smaller
```

**Write-path saving.** Of 50 M rows, only the 5% matching the predicate ever cause an index write on insert — but note the subtlety: with a *time-based* predicate, **every new insert matches**, so there is no write saving at all, only a storage saving (and the index must be rebuilt periodically as the window slides). With the *status-based* predicate, an insert that lands directly as `delivered` skips the index entirely, and a row transitioning `pending → delivered` causes one index **delete**, not an insert. Measured at BookNook: the status-based partial index added 0.04 ms to the average insert versus 0.31 ms for the full index — an **87% reduction in that index's write cost**.

**Cache residency — the effect that actually dominates.** At 11.5 MiB the partial index is permanently resident in the buffer cache. At 1.61 GiB the full index competes with the 4.2 GB heap for a 16 GB cache. The fulfilment queue query went from a measured p99 of 180 ms (cold leaf pages) to **2.4 ms** — a 75× improvement that the size arithmetic alone understates.

## 10. Trade-offs

| Decision | You gain | You pay | The condition that decides it |
|---|---|---|---|
| B+tree (default) | Ranges, ordering, uniqueness, prefix, predictable depth | Largest index of the general-purpose options; random write I/O | Choose unless you have a specific, measured reason not to |
| Hash instead of B+tree | 1–2 fewer page reads on equality; much smaller for long keys | Loses ranges, ordering, uniqueness, multi-column, index-only scans | Only when the column is long, high-cardinality, and queried *exclusively* by `=` |
| One composite index vs several single-column | One index satisfies multi-predicate queries without a bitmap merge; enables index-only scans | Only usable left-to-right; larger per entry; serves fewer distinct query shapes | Composite wins when the predicates co-occur in the same queries; separate wins when they appear independently |
| Adding a column to the key vs `INCLUDE` | Key column is seekable and sortable | Reduces internal-node fanout, deepens the tree, affects uniqueness | Key if filtered/sorted on; `INCLUDE` if merely returned |
| Covering index (index-only scan) | Eliminates one random heap read per result row; huge in InnoDB | Wider index, more write amplification, and in PostgreSQL depends on `VACUUM` keeping the visibility map current | Worth it when the query returns many rows and the covered column set is stable |
| Partial index | 90–99% smaller, cheaper writes, permanent cache residency | Only usable when the planner can prove implication; invisible to parameterised queries; fragile to predicate drift | Worth it when a small, well-defined subset carries the query load |
| Expression index | Makes `WHERE f(col) = ?` indexable at all | Function runs on every write; must be `IMMUTABLE`; the query must match the expression textually | Required, not optional, if the application cannot be changed to stop wrapping the column |
| GIN | Orders-of-magnitude faster containment and full-text queries | Expensive updates; pending-list flush latency spikes; large | Multi-valued columns queried by containment |
| BRIN | ~1000× smaller than B-tree | Useless without physical correlation; returns page ranges, so a heap re-check is always required | Append-only, naturally ordered, very large tables |
| LSM-tree engine (Lesson 45) | Sequential writes, high ingest throughput, better compression | Read amplification, compaction-induced p99 spikes, weaker range performance | Write-dominated, read-rarely workloads |
| One more index, in general | One query gets faster | **Every** insert/update/delete on that table gets slower; more WAL; more cache pressure; more to `VACUUM` and back up | Add only when the query is measured-slow *and* the index is measured-used |

The meta-trade-off: **indexes trade write throughput and storage for read latency.** There is no index that is free. A table with twelve indexes has roughly 12× the index-maintenance work per write of a table with one.

## 11. Failure scenarios

| Failure | What the user experiences | How it is detected | How it recovers |
|---|---|---|---|
| Index silently unused because the query wraps the column in a function (`WHERE date(created_at) = ...`) | Page that was 50 ms now takes 6 s; gets worse as the table grows | `EXPLAIN` shows `Seq Scan` despite the index existing; `pg_stat_user_indexes.idx_scan` stays at 0 | Rewrite the predicate as a range (`created_at >= d AND < d+1`) or add a matching expression index |
| Implicit type cast (`WHERE varchar_col = 12345`) disqualifies the index | Same as above, but only for *some* call sites, so it looks intermittent | `EXPLAIN` shows a cast in the filter; the index is unused | Fix the parameter type in the application; add an expression index only as a stopgap |
| Wrong composite column order (range before equality) | Query is "fast enough" at 100 ms but degrades linearly with data growth; nobody notices for a year | `EXPLAIN ANALYZE` shows `Rows Removed by Filter` far exceeding rows returned | `CREATE INDEX CONCURRENTLY` with the corrected order, then drop the old one |
| Index bloat after a mass delete (PostgreSQL does not merge pages) | Gradual slowdown; cache hit rate falls; disk fills | Index size disproportionate to row count; `pgstattuple` / `pg_stat_user_indexes` size vs rows | `REINDEX INDEX CONCURRENTLY`; prevent via autovacuum tuning |
| Index-only scan regresses to heap fetches because autovacuum fell behind | p99 latency triples overnight with no deploy and no query change | `EXPLAIN ANALYZE` shows `Heap Fetches: 18,400` where it was previously 0 | Run `VACUUM`; lower `autovacuum_vacuum_scale_factor` on the table |
| Too many indexes on a hot write table | Write latency creeps up; replication lag grows; checkpoint storms | `INSERT` p99 rising with no code change; WAL volume per transaction climbing | Drop unused indexes identified by `idx_scan`; consolidate overlapping composites |
| `CREATE INDEX` without `CONCURRENTLY` on a live table | **Total outage** — all writes to the table block for the build duration (minutes to hours) | Lock-wait alerts; `pg_locks` shows `ShareLock` on the relation; connection pool exhausts | Cancel the build; redo with `CONCURRENTLY`. Prevention: lock timeouts in migration tooling |
| `CREATE INDEX CONCURRENTLY` fails midway | No visible effect at first; later, disk usage is unexplained and the index is never used | `pg_index.indisvalid = false` | `DROP INDEX CONCURRENTLY`, then retry. Never leave an invalid index in place |
| Random UUID primary key in InnoDB | Insert throughput degrades severely once the table exceeds the buffer pool | Page-split rate climbs; `innodb_buffer_pool` hit rate collapses; insert p99 grows 5–10× | Migrate to `UUIDv7`/ULID or a `bigint` surrogate key; requires a table rebuild |
| Partial index not used because the query uses a bind parameter instead of a literal | The index exists, is tiny, is perfect — and is never chosen | `idx_scan = 0` on a freshly created index that should obviously be hot | Inline the constant, or widen the index to a non-partial one |
| Hash index chosen for a column that later needs a range query | New feature ships; the range query sequentially scans | `EXPLAIN` shows `Seq Scan`; no index can be used | Replace with a B-tree. Rarely reversible without a rebuild |
| GIN pending list flush under sustained write load | Periodic multi-second latency spikes in writes, uncorrelated with traffic | Write p99 spikes on a regular cadence; `gin_pending_list_limit` reached | Lower `gin_pending_list_limit`, or disable `fastupdate` to trade average throughput for predictability |
| BRIN on a column whose correlation decayed (after `UPDATE`s or a `CLUSTER` on a different column) | Query that was 200 ms is now a full sequential scan | `pg_stats.correlation` has dropped below ~0.5 | `CLUSTER` the table on that column, or replace BRIN with a B-tree |

The recurring theme: **index failures are mostly silent.** Nothing errors. The system merely becomes slower, and usually gradually. This is why `EXPLAIN` discipline (Lesson 55) and index-usage monitoring are not optional extras — they are the only detection mechanism you have.

## 12. Alternatives

| Approach | What it is | Best at | Worst at | Choose it when |
|---|---|---|---|---|
| **B+tree index** | Balanced ordered tree, data in linked leaves | Everything general-purpose: `=`, ranges, `ORDER BY`, uniqueness, prefix | Multi-valued columns; very large keys; extreme write rates | The default. Deviate only with evidence |
| **Hash index** | Bucket array keyed by hash | Equality on long, high-cardinality keys | Any ordering question; multi-column; uniqueness | A long token column queried only by `=` |
| **GIN** | Inverted index: element → posting list | Array/JSONB containment, full-text (Lesson 47) | Write-heavy tables; range queries | Multi-valued columns with containment predicates |
| **GiST** | Extensible balanced tree with user-defined operators | Geometry, ranges, nearest-neighbour, exclusion constraints | Raw text-search throughput vs GIN | Spatial data, range exclusion, `ORDER BY distance` |
| **SP-GiST** | Space-partitioned trees (quadtree, radix) | Non-balanced partitionable data; IP prefixes; point data | General-purpose use | `inet`/prefix matching, point sets |
| **BRIN** | Min/max summary per block range | Enormous, naturally ordered tables | Anything uncorrelated with heap order | Append-only time-series |
| **Bloom index** | Hashed signature per row | Many low-selectivity columns queried in arbitrary combinations | Precision — always needs a heap re-check | Wide fact tables with unpredictable `WHERE` combinations |
| **Clustered index (InnoDB/SQL Server)** | Table physically stored in key order | Range scans on the key; primary-key lookups | Secondary lookups (double descent); random key inserts | Primary access path is a range on one key |
| **LSM-tree (Lesson 45)** | Sequential-write memtable + compacted SSTables | Very high ingest rates | Predictable p99; complex range queries | Write-dominated workloads |
| **Materialised view** | Precomputed, stored query result | Complex aggregates read far more often than written | Freshness; refresh cost | An expensive aggregate tolerating staleness |
| **External search engine (Lesson 47)** | Inverted index in Elasticsearch/OpenSearch | Relevance ranking, fuzzy and faceted search | Consistency with the source of truth | Search is a product feature, not a predicate |
| **Denormalisation (Lesson 56)** | Store the answer alongside the data | Removing the query entirely | Write complexity; consistency | The join itself, not the scan, is the cost |
| **Caching (Lesson 62)** | Keep the result in Redis/memory | Repeated identical reads | Unique queries; invalidation | High read:write ratio on a small hot set |
| **No index at all** | Sequential scan | Small tables; low-selectivity predicates; rare queries | Large tables with selective predicates | Fewer than a few thousand rows, or the predicate matches >20% of rows |

The two most under-used entries are the last one and **denormalisation**. The best index is frequently the one you did not need because you restructured the data (Lesson 56) or removed the query.

## 13. When to use it

1. **High-selectivity equality or range predicates on a large table.** `WHERE customer_id = ?` on 50 M rows returning ~28 rows (selectivity 0.00000056) is the canonical case — use a B+tree. This is the single most common correct use and covers the majority of real indexes.
2. **Sorting and pagination.** `ORDER BY created_at DESC LIMIT 20` with a matching index key order removes an entire sort of the result set. Combine with keyset pagination (`WHERE (created_at, order_id) < (?, ?)`) rather than `OFFSET`, and the cost stays constant no matter how deep the user pages.
3. **Enforcing uniqueness.** A `UNIQUE` index is the only race-free way to guarantee one email per account or one order per idempotency key. Use it even if no query ever reads by that column.
4. **Supporting foreign keys.** A foreign key's *referencing* column should almost always be indexed. Without it, every `DELETE` or key `UPDATE` on the parent table triggers a full scan of the child table to check the constraint — a notorious source of mysterious slow deletes.
5. **Covering a hot, stable read path.** When one query runs thousands of times per second and touches a fixed small column set, an `INCLUDE`-covered index converting it to an index-only scan is among the highest-return changes available.
6. **Shrinking a hot subset with a partial index.** When a small, stable subset (active sessions, pending jobs, undelivered orders) carries nearly all the query traffic.
7. **Containment queries on multi-valued columns.** Arrays, `JSONB`, and full-text need GIN; nothing else will work.
8. **Very large append-only tables queried by time window.** BRIN, for a ~1000× storage saving.
9. **Join columns.** The inner side of a nested-loop join (Lesson 55) is effectively a repeated point lookup; without an index the planner is forced into a hash or merge join, which may or may not be what you want.

## 14. When not to use it

1. **Small tables.** Below roughly 1,000–5,000 rows (a few hundred pages), a sequential scan is faster than an index descent plus random heap reads. The planner knows this and will ignore your index — correctly.
2. **Low-selectivity predicates.** An index on `status` where 88% of rows are `delivered` will never be used for `WHERE status = 'delivered'`; fetching 44 M scattered heap rows is far worse than one sequential pass. The usual rule of thumb: above ~5–20% selectivity, a sequential scan wins (the exact crossover depends on `random_page_cost`, cache residency, and row width — Lesson 55 derives it).
3. **Boolean or very-low-cardinality columns**, unless combined into a composite or expressed as a partial index predicate. `INDEX (is_active)` is nearly always waste; `INDEX (created_at) WHERE is_active` is often excellent.
4. **Write-dominated tables with rare reads.** An event-ingestion table taking 40,000 inserts/sec and queried twice a day should carry the minimum indexes. Each additional index is a permanent tax on the ingest path for a benefit realised twice daily.
5. **Columns that are already covered by a leftmost prefix.** If `INDEX (a, b, c)` exists, `INDEX (a)` and `INDEX (a, b)` are redundant and should be dropped — they cost writes and storage for zero additional capability.
6. **Speculative "just in case" indexes.** An index that is never used is pure cost. Measure first.
7. **Columns that change on nearly every update.** Indexing a `last_seen_at` column that updates on every request means every request now performs an index delete plus insert, often causing a page split. Consider a separate table, a counter in Redis (Lesson 62), or accepting lower precision.
8. **As a substitute for fixing the query or the schema.** If a query is slow because it fetches 2 million rows the user will never look at, the fix is pagination, not an index.

## 15. Common mistakes

### Beginner mistakes

- **Creating one single-column index per column in the `WHERE` clause** and expecting them to combine. They can combine, via a bitmap index scan, but that is markedly slower than one correctly ordered composite index — and it cannot produce sorted output or an index-only scan.
- **Getting composite column order wrong**, especially putting a range column before an equality column. Memorise: *equality, then sort, then range*.
- **Assuming "I added an index" means "the index is used."** Always verify with `EXPLAIN` (Lesson 55) and `pg_stat_user_indexes.idx_scan`.
- **Wrapping the indexed column in a function** — `WHERE DATE(created_at) = '2026-03-01'`, `WHERE LOWER(email) = ?`, `WHERE id::text = ?`. Each silently disables the index.
- **Indexing a boolean or a 3-value status column** on its own.
- **Forgetting to index foreign key columns**, then being baffled by slow parent deletes.
- **Running `CREATE INDEX` without `CONCURRENTLY`** on a production table and taking an outage.
- **Believing a hash index is "a faster B-tree"** and discovering months later that `ORDER BY` now sequentially scans.
- **Using `OFFSET 100000` for pagination** and blaming the index when page 5,000 takes 4 seconds. The index is fine; `OFFSET` discards 100,000 rows it already fetched.
- **Indexing a column with `NULL` in most rows** and expecting `WHERE col IS NULL` to be fast and selective. It is indexable in PostgreSQL, but if 90% of rows are `NULL` it is also useless.

### Experienced-engineer mistakes

- **Optimising a composite index for one query in isolation**, producing six nearly identical indexes where two well-chosen ones would serve all six queries. Index *consolidation* is a design activity, not an afterthought.
- **Adding `INCLUDE` columns and declaring victory without verifying `Heap Fetches: 0`.** On a hot, frequently updated table, an index-only scan can silently degrade to heap access because the visibility map is stale.
- **Using a partial index with a bind parameter.** The index is perfect and never chosen, because implication cannot be proven at plan time.
- **Ignoring index bloat.** PostgreSQL does not merge B-tree pages. A table that regularly deletes 40% of its rows accumulates indexes two to three times larger than necessary, and the slowdown looks like generic "the database is getting slower".
- **Choosing a random UUID primary key in InnoDB** for distributed-ID-generation reasons without accounting for the clustered-index insert pattern. The symptom appears only once the table exceeds the buffer pool.
- **Adding a wide `INCLUDE` payload that makes the index larger than the table**, so index-only scans read more bytes than a sequential scan would.
- **Deploying BRIN without checking `pg_stats.correlation`**, or deploying it and then letting correlation decay through updates.
- **Treating `idx_scan = 0` as sufficient evidence to drop an index.** The counter resets on restart, is per-node (a read replica may depend on it), and quarterly reporting queries may not have run yet. Check across all replicas and over a full business cycle.
- **Over-indexing a table to satisfy an ORM's lazily generated queries** instead of fixing the ORM's query generation.
- **Forgetting that every index must be rebuilt during a major-version upgrade, a `pg_restore`, or a logical replication resync**, turning a 20-minute cutover into a three-hour one.
- **Not setting a `lock_timeout` in migration tooling**, so an `ALTER TABLE` queues behind a long transaction and then blocks every subsequent query on the table — an outage caused by a change that itself takes milliseconds.

## 16. Interview perspective

This topic is near-universal in backend and system-design interviews because it discriminates sharply between memorised answers and real understanding.

**What you will be asked, and what a strong answer contains:**

- *"Why a B-tree and not a binary search tree or a hash table?"* — The answer must be about **page-oriented I/O**: a BST has fanout 2, so a billion rows is 30 levels and 30 page reads; a B+tree has fanout in the hundreds, so it is 4 levels. Then add the ordering argument for why not a hash table.
- *"Walk me through what happens on a point lookup."* — Root → internal → leaf → heap, with the observation that the upper levels are cached so the physical I/O is 1–2 reads. Mention the visibility check.
- *"I have `INDEX (a, b, c)`. Which of these queries use it?"* — This is a direct test of the leftmost-prefix rule. Answer confidently, and add the nuance that a non-prefix query may still trigger a *full index scan* if the index covers the query, and that some engines support skip scans for low-cardinality leading columns.
- *"How would you order the columns?"* — Equality, then sort, then range, with a one-sentence justification about contiguity in the sort order. Then add the systems-level point: prefer an order that lets one index serve several queries.
- *"Why would the database ignore my index?"* — Give several causes, not one: low selectivity, function on the column, implicit cast, stale statistics, parameter type mismatch, partial-index predicate not provable, table too small. This breadth is what distinguishes senior answers.
- *"When would you not add an index?"* — Write-heavy tables, low selectivity, small tables, redundancy with an existing prefix. Volunteering this unprompted signals maturity.
- *"What is a covering index?"* — Define index-only scan, explain `INCLUDE`, then volunteer the PostgreSQL visibility-map caveat and the InnoDB double-lookup benefit. That last pair of details is a strong signal.
- *"What is the cost of an index?"* — Storage, write amplification, WAL volume, buffer-cache competition, vacuum/backup time, lock risk during creation, and upgrade/restore time. Seven costs, not one.

**How to structure the answer in a design round.** When asked to design BookNook's order service, do not list indexes. Instead: enumerate the query shapes with their frequencies, derive selectivity for each, propose the minimum index set, then state explicitly what each index costs on the write path and how you would verify it is used. That sequence — *access patterns → selectivity → index set → write cost → verification* — is the reusable template.

**Red flags interviewers watch for:**

- Saying "B-tree" and "B+tree" interchangeably without knowing the difference when pressed.
- Claiming indexes "make the database faster" with no mention of write cost.
- Proposing an index on every column in the `WHERE` clause.
- Not knowing the leftmost-prefix rule, or knowing the rule but not *why* it exists (sort order and contiguity).
- Putting a range column before an equality column in a composite index.
- Recommending a hash index without being asked about range queries, and not volunteering that limitation.
- Being unable to name a situation where a sequential scan is the correct plan.
- Never mentioning `EXPLAIN`, or treating it as something only DBAs run.
- Suggesting `CREATE INDEX` on a live 50 M-row table without `CONCURRENTLY` or a lock timeout.
- Confusing an index with a cache — they solve different problems and fail differently.
- Giving "it depends" without immediately saying *what* it depends on (selectivity, cardinality, read:write ratio, row width, correlation, cache size).

## 17. Knowledge check

**Conceptual**

1. A B+tree stores all data pointers in the leaf level and links leaves to their siblings, whereas a classic B-tree may store data pointers in internal nodes. Explain precisely how each of those two design choices changes the *number of page reads* required for (a) a point lookup and (b) a range scan returning 10,000 rows — and explain why high fanout matters more on a page-oriented storage device than it would in pure in-memory computation.

2. You have `CREATE INDEX idx ON orders (status, warehouse_id, created_at)`. For each of the following, state whether the index can be used to *seek*, used only as a *filter* after a partial seek, or not usefully used at all, and justify each answer in terms of the index's sort order: (a) `WHERE warehouse_id = 7`; (b) `WHERE status = 'pending' AND created_at > now() - interval '1 day'`; (c) `WHERE status = 'pending' ORDER BY created_at`; (d) `WHERE status = 'pending' AND warehouse_id = 7 ORDER BY created_at DESC`; (e) `WHERE status > 'p' AND warehouse_id = 7`.

3. In PostgreSQL, an index-only scan may still read heap pages. Explain the mechanism that causes this, name the subsystem responsible for preventing it, describe the specific `EXPLAIN ANALYZE` field that reveals it, and explain why MySQL InnoDB does not exhibit this exact behaviour. Then state one operational condition under which a previously index-only query silently regresses with no code change.

**Practical scenario**

4. BookNook is launching a gift-registry feature. The new query runs 800 times per second: `SELECT order_id, total_cents FROM orders WHERE customer_id = $1 AND metadata->>'registry_id' = $2 AND status <> 'cancelled' ORDER BY created_at DESC LIMIT 50`. The `orders` table has 50 M rows, already carries six indexes, and sustains 1,200 inserts/sec. `metadata` is `JSONB`; roughly 0.3% of orders have a `registry_id`. Design the index (or indexes) you would add. State the exact DDL, justify the column order, justify your choice of index type for the `JSONB` predicate, decide whether to use a partial index and on what predicate, decide what if anything to `INCLUDE`, estimate the added write cost, and describe exactly how you would verify after deployment that the index is being used as intended.

**Calculation**

5. BookNook adds an `order_lines` table with 400,000,000 rows. You are designing an index on `(order_id bigint, line_no int)`. Assume 8,192-byte pages with 8,168 usable bytes, a leaf entry of 8 + 4 + 6 + 4 + 2 = 24 bytes, an internal entry of 12 + 4 + 4 = 20 bytes, and steady-state occupancy of 75%. Calculate: (a) entries per leaf page and downlinks per internal page; (b) the number of pages at each level and the resulting tree depth; (c) the total index size in GiB; (d) the number of logical page accesses for a point lookup that must also fetch the heap row; (e) the expected *physical* reads if the buffer cache holds all levels above the leaves plus 40% of the leaf pages and 25% of the heap; and (f) how the depth changes if the table grows to 4 billion rows. Show every step.

**Progress checklist**

```
Phase 1 — Foundations (Lessons 1–17)              ...... [x] COMPLETE
Phase 2 — Scaling (Lessons 18–39)                 ...... [x] COMPLETE
Phase 3 — Data & Storage
  [x]  1. Relational databases and the relational model      (L40)
  [x]  2. Joins and relationship modelling                   (L41)
  [x]  3. SQL versus NoSQL                                   (L42)
  [x]  4. NoSQL family: key-value stores                     (L43)
  [x]  5. NoSQL family: document stores                      (L44)
  [x]  6. NoSQL family: wide-column stores and LSM-trees     (L45)
  [x]  7. NoSQL family: graph databases                      (L46)
  [x]  8. Search engines and full-text search                (L47)
  [x]  9. Choosing a database                                (L48)
  [x] 10. ACID guarantees                                    (L49)
  [x] 11. Isolation levels                                   (L50)
  [x] 12. Read phenomena                                     (L51)
  [x] 13. Concurrency control: locking and MVCC              (L52)
  [x] 14. Database indexes: selectivity, write amplification (L53)
  [x] 15. B-tree, hash, composite, covering, secondary       (L54) ← you are here
  [ ] 16. Query plans and index trade-offs                   (L55)
  [ ] 17. Database normalisation and denormalisation         (L56)
  [ ] 18. Partitioning and sharding                          (L57)
  [ ] 19. Replication and read replicas                      (L58)
  [ ] 20. Consistency models and CAP                         (L59)
  [ ] 21. Distributed transactions and sagas                 (L60)
  [ ] 22. Connection pooling and database performance        (L61)
  [ ] 23. Database caching strategies                        (L62)
  [ ] 24. OLTP versus OLAP and data warehousing              (L63)
```

**NEXT LESSON: Phase 3, Topic 16 — Query Plans and Index Trade-offs**

---
