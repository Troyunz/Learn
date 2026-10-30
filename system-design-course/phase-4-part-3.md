# Lesson 70 — Event Streams

*Phase 4, Topic 7*

## 1. Simple definition

An **event stream** is an **append-only, durable, replayable, ordered log of events**.

Four words carry the whole lesson, so define each before going further:

- **Event**: a record that *something happened*, expressed as an immutable fact in the past tense — `OrderPlaced`, `PaymentCaptured`, `ShipmentDispatched`.
- **Log**: here, not "logging" in the `console.log` sense. A log is a data structure: a sequence of records, each assigned a position, where new records are only ever added at the end. You met this structure already as the write-ahead log behind B-trees (Lesson 54) and as the memtable-plus-SSTable write path of LSM-trees (Lesson 45). An event stream is that same structure promoted from an internal implementation detail to the public interface of a system.
- **Append-only**: records are added at the end. They are never updated in place and never deleted individually.
- **Offset**: the position of a record in the log, a monotonically increasing integer. Offset 0 is the first record ever written; offset 4,812,993 is the 4,812,994th. The offset is the record's permanent address.
- **Durable**: the record is on disk, replicated to more than one machine, and survives broker restarts and disk failures (Lesson 15 defined durability precisely).
- **Replayable**: a consumer can set its read position to any offset it is allowed to reach and read the whole history forward again, as many times as it likes.
- **Ordered**: records have a defined sequence that every reader observes identically. (With the critical qualification that this holds *within a partition*, which is the entire subject of Lesson 72.)

A queue (Lesson 68) moves work. A pub/sub topic (Lesson 69) distributes notifications. An event stream does something categorically different: **it remembers.**

## 2. Why it exists

Lesson 68 gave BookNook a work queue and Lesson 69 gave it fan-out. Both have the same structural property: **consumption destroys the message.** Once the warehouse worker acknowledges `order-created`, that message is gone from the broker. That single property causes five concrete problems, each of which BookNook actually hit.

**Problem 1 — the new consumer arrives too late.** In March, the analytics team asks for a dashboard of orders by category for the last 90 days. The pub/sub topic delivers only to subscribers that existed at publish time. The 90 days of history were never retained by the broker; they exist only as whatever the orders table happens to look like now. The answer is "we can give you data starting from the day you subscribe", which is not the question that was asked.

**Problem 2 — the bug that corrupted a derived table.** BookNook's `recommendations` service consumed `order-created` for six weeks with an off-by-one in its category mapping. The fix is one line. Repairing the six weeks of damage is impossible, because the inputs that produced it were consumed and discarded. You can fix the code; you cannot re-run it.

**Problem 3 — the state lost its own history.** The `orders` table has a `status` column. It says `cancelled`. It cannot tell you when the order was placed, whether payment was captured before cancellation, whether the warehouse had already picked it, or who cancelled it. Every intermediate state was overwritten by the next `UPDATE`. The current row is a *summary* of a history that was never written down.

**Problem 4 — "can we also…" requires a migration.** Each new question about past behaviour ("how many customers abandoned after payment failure?") requires a new column, a backfill, and a deploy — and the backfill is usually impossible because the data was never captured.

**Problem 5 — two services disagree and nobody can adjudicate.** The billing service says the order was refunded; the warehouse says it shipped. With destructive messaging there is no shared, authoritative record of what actually happened in what order.

Event streams exist to solve all five with one design decision: **retain the events and delete them on a time or size policy rather than on consumption.** Everything else in this lesson follows from that choice.

## 3. Real-world analogy

**A ship's logbook, and a bank statement.**

The ship's logbook is written in ink, one line at a time, never erased. "0400 — changed course to 270°. 0630 — wind veered NW force 6. 0710 — reefed mainsail." The ship's *current* position, heading and sail configuration are not written anywhere as a single authoritative value. They are the result of applying every logbook entry in order from the moment the ship left port. If you doubt the current position, you re-derive it by reading the log forward again — a process sailors literally call *dead reckoning*.

A bank statement is the same idea with money. Your balance is not a stored fact that the bank edits; it is `sum(credits) − sum(debits)` over the transaction log. If the balance looks wrong, the bank does not "fix the balance" — that would be fraud. It appends a correcting transaction, and the balance re-derives itself.

**Where the analogy breaks down:**

| Logbook / bank statement | Event stream |
|---|---|
| Kept forever, in a vault, for legal reasons | Kept for a configured **retention period** (typically 7–90 days). Infinite retention is possible and is a deliberate, costed decision, not the default |
| One reader at a time, reading at their own pace, and the book is physically handed over | Hundreds of independent consumers read the same bytes concurrently; each tracks its own position and no reader can affect another's |
| Entries are written by one captain, so order is unambiguous | Order is only guaranteed **within one partition**. With 12 partitions you have 12 independent logbooks and no global order between them (Lesson 72) |
| A correction is written as a note in the margin | Events are strictly immutable. A correction is a *new event* (`OrderAddressCorrected`), never an edit, because consumers may already have read the original |
| The balance can be recomputed in minutes by hand | Recomputing from 90 days of events can take minutes to hours, which is why **snapshots** exist (§4) |
| The logbook has one meaning: the ship's state | One event stream feeds many *different* derived views — an orders table, a warehouse picking list, an analytics cube — each shaped for its own reader |
| Nobody ever changes the format of a logbook entry | Event schemas evolve over years, and old events in the log are still in the **old format** forever. This is the single most underestimated cost of the whole approach |

That last row is the one that bites teams eighteen months in, and §4 treats it seriously.

## 4. Detailed technical explanation

### 4.1 The four properties, and why each matters on its own

It is tempting to treat "append-only, durable, replayable, ordered" as one idea. They are four, and each buys something specific.

**Append-only** buys *immutability and cheap writes*. Because records are never modified, a writer never has to find an existing record, lock it, or rewrite a page. The write is "put these bytes at the end of the current file and advance a pointer", which is a sequential disk write — the fastest operation a storage device performs (quantified in Lesson 71, §4). Immutability also means a record that has been read by one consumer cannot be changed under another consumer's feet, which removes an entire class of race condition.

**Durable** buys *the right to treat the log as the source of truth*. If the log can lose records, every consumer must keep its own defensive copy, and you are back to five services disagreeing. Durability here means: written to the operating system, flushed or replicated to *f*+1 machines so that *f* simultaneous failures lose nothing, and surviving broker restart. Without it, replay replays an incomplete history, which is worse than no replay because it is confidently wrong.

**Replayable** buys *recovery from your own mistakes*. This is the property that distinguishes a stream from everything in Lessons 68 and 69. Fix the bug, reset the consumer's offset to where the bug started, let it re-process. It also buys free integration: a new service needs no backfill script, because the backfill *is* the stream.

**Ordered** buys *the ability to derive state deterministically*. `PaymentCaptured` then `OrderCancelled` means a refund is owed. `OrderCancelled` then `PaymentCaptured` means you charged a cancelled order. The same two facts in two orders are two different realities. Without ordering, a projection cannot be computed correctly, and — crucially — cannot be *recomputed* to the same answer twice.

### 4.2 The central insight: the log is the source of truth; state is a projection

Here is the inversion that makes this topic hard for people coming from relational thinking.

In the familiar model, the database table is the truth and events are optional notifications about changes to it:

```
TABLE (truth)  ──▶ emits ──▶ events (derived, optional, best-effort)
```

In the event-stream model, the arrow reverses:

```
LOG (truth)  ──▶ applied to produce ──▶ tables, caches, indexes, dashboards (derived)
```

A **projection** (also called a *read model*, *materialised view*, or *derived view*) is a data structure computed by folding a function over the events in order:

```
state = events.reduce(apply, initial_state)
```

This is exactly `reduce` / `fold` from functional programming, run over a log instead of an array. Three consequences follow immediately and they are the whole point:

1. **Any projection can be deleted and rebuilt**, because its inputs still exist. A corrupted cache is not a disaster; it is a 20-minute replay.
2. **New projections can be created at any time** covering all of history, with no migration.
3. **Projections are disposable, so their schemas are cheap to change.** You change the code, drop the table, replay. Compare that to an `ALTER TABLE` on a 400 GB table that is the only copy of the truth.

Storing the log as the source of truth and deriving all state from it is called **event sourcing**.

### 4.3 Events versus commands

A distinction that beginners collapse and that matters enormously.

| | **Command** | **Event** |
|---|---|---|
| Grammar | Imperative: `PlaceOrder`, `CancelOrder` | Past tense: `OrderPlaced`, `OrderCancelled` |
| Meaning | A *request* for something to happen | A *fact* that something happened |
| Can it be rejected? | Yes — validation, authorisation, insufficient stock | No. It already happened. Rejecting it is meaningless |
| Recipients | Exactly one handler, which owns the decision | Zero or many interested parties |
| Goes in the log? | Usually not. It is not a fact yet | Yes. The log is a log of facts |
| Failure mode of getting it wrong | — | A log full of commands is a queue wearing a costume: consumers start making decisions that the producer thought it had made |

The practical test: **if a consumer reads a record and can legitimately decide "no", it is a command, not an event.** `OrderPlaced` is a fact; the fraud service reading it may emit `OrderFlaggedForReview`, but it cannot un-place the order.

A second test is the naming test. If you cannot name the record in the past tense without it sounding odd (`SendEmail` → `EmailSent`? but it has not been sent yet), you are modelling an instruction, not a fact.

### 4.4 Retention-based deletion: the single design choice that enables everything

A queue deletes a message when it is acknowledged — **consumption-based deletion**. A stream deletes records when they age out of a configured window — **retention-based deletion**.

```
Queue:  delete(message)  when  all_required_consumers_acked(message)
Log:    delete(segment)  when  segment_age > retention_ms  OR  topic_size > retention_bytes
```

Note what the log's rule does *not* mention: consumers. The broker does not know, and does not care, whether anyone has read a record. That ignorance is the feature. It produces, directly:

- **Replay**, because the bytes are still there after reading.
- **Independent consumers**, because one consumer's progress cannot delete data another consumer still needs. Adding a twelfth consumer group costs the broker nothing but extra network reads — no extra copy of the data (contrast with the per-subscriber queue copies of Lesson 69's fan-out).
- **Reprocessing after a bug fix**, because the inputs survived the bug.
- **A new consumer seeing history**, because "when did you subscribe" is irrelevant; only "what offset do you want to start at" matters.
- **Decoupling of producer and consumer speed over long horizons.** A consumer down for six hours is not a backlog emergency as long as retention exceeds six hours; it just resumes at its stored offset.

The cost is equally direct: **you are paying to store data that may never be read**, and a consumer that is offline for longer than the retention period silently loses data — it restarts from the oldest surviving offset and the gap is gone. Retention is therefore not a storage tuning knob; it is a *recovery-time budget*. Setting `retention.ms = 7 days` is a statement that you can repair any consumer within seven days.

### 4.5 Event sourcing, honestly

**What it is.** Persist the sequence of state-changing events as the authoritative record. Derive every queryable structure from it.

**Genuine benefits:**

- **Complete audit trail, for free and by construction.** Not a separate audit table that someone forgets to write to. Regulated domains (payments, healthcare, trading) often want this independently of any technical argument.
- **Temporal queries.** "What did this order look like at 14:03 on 3 March?" is answerable by folding events up to that timestamp. In a mutable-state system that question is simply unanswerable.
- **Rebuild any projection.** New dashboard, new search index, new ML feature store — replay, do not backfill.
- **Debugging becomes reproducible.** A production bug is reproducible locally by replaying the exact event sequence that caused it.
- **Retroactive correction.** Fix the projection logic, replay, and history is recomputed correctly. In a mutable system, a logic bug has already destroyed the evidence.

**Genuine costs — state these in an interview before anyone asks:**

- **Schema evolution of historical events.** Version 1 of `OrderPlaced` has `price`. Version 2 splits it into `price_ex_tax` and `tax`. The three million v1 events in the log are v1 forever; you cannot migrate them in place without violating immutability and breaking any consumer that already read them. You must therefore maintain **upcasters** — functions that transform old event versions into current ones at read time — effectively forever, or until retention expires them. With infinite retention, "forever" is literal. This is the number one long-term cost.
- **Snapshotting.** Folding 40,000 events to answer "what is this account's balance" is absurd. A **snapshot** is a periodically persisted copy of a projection's state plus the offset it was computed at; recovery loads the snapshot and replays only events after that offset. Snapshots are an optimisation, but they introduce their own problems: a snapshot is in the *old projection schema*, so changing the projection means invalidating every snapshot and doing a full replay anyway.
- **Projections are eventually consistent.** A write appends an event; the projection updates milliseconds to seconds later. A user who places an order and is immediately redirected to "my orders" may not see it. This is read-your-writes consistency (Lesson 61) and it needs the same mitigations discussed there — read from the write side for the user's own data, pin the user to a projection that has caught up, or return the projected state optimistically from the command handler.
- **It is genuinely harder.** Not "requires discipline" — harder. Developers must reason about history rather than current state; debugging spans producer, log and projection; every tool that expects to `SELECT` current state needs a projection built for it; onboarding takes longer. Most teams, most of the time, should use a normal database with a well-designed audit table, and reserve full event sourcing for the subdomain where history genuinely is the product (payments, inventory ledger, order lifecycle) rather than for the whole system.

**The pragmatic middle.** You can publish an event stream *alongside* a conventional database, keeping the database authoritative for the writing service and the stream authoritative for everyone downstream. This gets you integration, replay and audit without demanding that your own service rebuild its state from events. The mechanism that makes it reliable (writing the row and the event in one transaction) is the **transactional outbox**, and it is Phase 4, Topic 16 — Outbox pattern and change data capture (Lesson 79). Until that lesson, assume dual-writing is unsafe, because it is.

### 4.6 CQRS, and its relationship to event sourcing

**CQRS** — Command Query Responsibility Segregation — means: *use a different model for writing than for reading.* Commands go through a write model optimised for validating and recording changes; queries are served by one or more read models optimised for how they are queried.

```
         commands                             queries
   client ───▶ WRITE MODEL ──▶ events ──▶ READ MODEL(s) ◀─── client
             (validate,                  (denormalised,
              decide, append)             per-query shape)
```

Three points that are routinely confused:

1. **CQRS and event sourcing are separable.** You can do CQRS with no events at all: write to a normalised Postgres schema, maintain a denormalised Elasticsearch read model by a nightly job. You can do event sourcing without CQRS, by folding events into one model used for both reads and writes (rare, but legal). They pair naturally because event sourcing gives you a perfect mechanism for keeping read models current, which is otherwise CQRS's hardest problem.
2. **CQRS buys independent optimisation and independent scaling** of two workloads with genuinely different shapes — the write side is small, transactional and consistency-critical; the read side is large, denormalised and tolerant of staleness. This is the same OLTP/OLAP split from Lesson 57, applied inside one service.
3. **CQRS costs eventual consistency and duplicated models.** Every read model is code you maintain and a lag you must monitor. Applying CQRS to a simple CRUD screen is the most common form of over-engineering in this entire phase.

### 4.7 Stream processing

**Stream processing** is computing continuously over an unbounded stream rather than over a finite dataset. Two broad classes:

**Stateless processing** — each event is processed independently: filter, map, enrich from a static lookup, route. No memory of previous events is kept. Trivially parallel; a crashed worker restarts at its last committed offset and loses nothing but time.

**Stateful processing** — the result depends on previous events: counts, aggregations, joins between two streams, deduplication, sessionisation. The processor keeps **state** (typically an embedded key-value store such as RocksDB, itself an LSM-tree — Lesson 45) which must be made fault-tolerant, usually by writing every state change to a compacted **changelog topic** so a replacement worker can rebuild it. Stateful processing is where all the difficulty lives.

**Windowing.** An unbounded stream has no "end", so an aggregate must be scoped to a window of time:

| Window type | Definition | BookNook example | Property |
|---|---|---|---|
| **Tumbling** | Fixed size, non-overlapping, adjacent. 5-minute tumbling windows are 12:00–12:05, 12:05–12:10 | Orders per 5 minutes for the ops dashboard | Every event belongs to exactly one window |
| **Sliding (hopping)** | Fixed size, advancing by a smaller step. Size 5 min, hop 1 min → 12:00–12:05, 12:01–12:06 | "Orders in the last 5 minutes", refreshed every minute, for alerting | An event belongs to `size/hop` windows — 5× the work and 5× the state |
| **Session** | Dynamic, closed by a gap of inactivity rather than by the clock | A customer's browsing session: all events until 30 minutes of silence | Window length is data-dependent; state must be held until the gap elapses |

**Event time versus processing time.** Two different clocks, and confusing them is the root of most stream-processing bugs (Lesson 66 established that clocks in distributed systems are not trustworthy).

- **Event time**: when the thing actually happened, stamped by the producer.
- **Processing time**: when your processor got around to it.

A mobile app that was offline on the Tube emits events with an event time of 09:14 that arrive at 09:52. If you window by processing time, those purchases land in the 09:50 window and your 09:10 figure is permanently wrong. Correct aggregations window by *event* time.

**Watermarks.** If you window by event time, you need to know when a window can be closed — but you can never be certain no straggler will arrive. A **watermark** is the processor's assertion: *"I believe I have seen all events with event time ≤ T."* It is typically computed as `max_event_time_seen − allowed_lateness`. When the watermark passes the end of a window, the window is emitted.

**Late-arriving data** is any event whose event time is below the current watermark when it arrives. You have exactly four options, and you must pick one explicitly:

1. **Drop it** (and count the drops as a metric — silently dropping is how you get numbers nobody trusts).
2. **Emit an update/retraction** that revises the already-published window result. Correct, but every downstream consumer must handle revisions.
3. **Hold windows open longer** (a larger allowed-lateness), trading latency and state size for completeness.
4. **Route it to a side output** for separate handling or manual repair.

The underlying trade-off is unavoidable and worth saying aloud in an interview: **completeness, latency and cost — you can have two.** Wait longer and you are more complete but slower and holding more state; emit early and you are fast but will be revising.

**Exactly-once in a stream-processing context** means: *for each input event, the effect on the processor's state and on its output topic happens exactly once, even across failures.* It is achieved by making the three operations — read offset advance, state update, output write — commit atomically as one transaction inside the streaming system. It works, and it is genuinely valuable. What it does **not** do is make side effects on *external* systems exactly-once: if your processor sends an email or charges a card, a retry sends two emails. The full treatment, including why "exactly-once delivery" is impossible while "exactly-once processing" is achievable, is Phase 4, Topic 10 — At-most-once, at-least-once, and exactly-once semantics (Lesson 73).

### 4.8 Materialised views from streams

A **materialised view** is a query result stored as a physical table and kept up to date, rather than recomputed on each read (Lesson 55 introduced them in the relational setting). Built from a stream, it has a property its relational cousin lacks: because the inputs are retained, the view is **rebuildable from scratch at any time**, which means it can be treated as a cache with a definitive refill path rather than as precious state.

BookNook's `book_sales_rank` is a materialised view over `order-events`: current rank per book, updated per event, served to the website in under 1 ms from a key-value store (Lesson 43), with no aggregate query ever reaching the transactional database.

### 4.9 Log compaction

Plain retention deletes old records by age. That is wrong for a stream whose records represent the *current value of a key* rather than a transient happening — for example `book-catalogue`, where each record is the current title, price and stock level for one ISBN. Delete records older than seven days and you delete the only copy of the price of a book that has not changed in a year.

**Log compaction** is an alternative retention policy: *retain at least the most recent record for every distinct key, and delete older records for keys that have newer ones.*

```
Before compaction (offsets):
  0: isbn-111 → {price: 8.99}
  1: isbn-222 → {price: 12.50}
  2: isbn-111 → {price: 7.99}
  3: isbn-333 → {price: 20.00}
  4: isbn-111 → {price: 6.49}
  5: isbn-222 → null            ← tombstone: key deleted

After compaction:
  2? no —  only the latest per key survives:
  3: isbn-333 → {price: 20.00}
  4: isbn-111 → {price: 6.49}
  5: isbn-222 → null  (retained for a configured period, then removed)
```

Properties worth knowing:

- A **tombstone** is a record with a null value, meaning "this key is deleted". It is retained for a configurable grace period so that consumers which are behind still observe the deletion, then removed.
- Compaction preserves **ordering** and **offsets** of surviving records. Offsets become non-contiguous — a consumer will see 3, 4, 5 with 0, 1, 2 missing. Consumer code that assumes `next_offset == current_offset + 1` breaks. (It should not assume that anyway: transactional markers also create offset gaps.)
- A consumer reading a compacted topic from offset 0 receives a complete snapshot of current state for every key, then continues with live updates. This "snapshot then stream" behaviour is exactly what a cache or a stateful stream processor needs on restart, and it is why changelog topics are compacted.
- Compaction operates on the *closed* segments behind the active one, asynchronously, so recently written duplicates persist for a while. Compaction is a background guarantee, not an instantaneous one.
- **The conceptual duality:** a compacted log is a table, and a table's change history is a log. Event stream and table are two representations of the same information — the "stream–table duality". Knowing this sentence, and being able to explain it, is a reliable interview marker.

## 5. Request / data flow

A customer clicks **Place order** on BookNook. Following the data from click to dashboard:

1. The browser sends `POST /orders` with an `Idempotency-Key` header (Lesson 17) through the API gateway (Lesson 25) to the order service.
2. The order service **validates the command**: the cart is non-empty, the address is deliverable, the payment method is present. This is command handling, and it can reject. Nothing has been appended yet.
3. The service writes the order row and, **in the same database transaction**, writes an `OrderPlaced` event into an outbox table. One transaction, so the row and the event cannot disagree (Lesson 79 covers why dual-writing without this is unsafe).
4. A relay process reads the outbox and **appends** the `OrderPlaced` event to the `order-events` stream with **key = `order_id`**. The key choice is what guarantees that all events for this order land in the same partition and therefore stay in order (Lesson 72).
5. The broker assigns the record an **offset** — say 4,812,993 in partition 7 — writes it to the active segment file, and replicates it to two follower brokers.
6. Once the required replicas have the record, the broker acknowledges the append. The event is now durable and visible to every consumer.
7. **Consumer group A — `orders-projection`** reads offset 4,812,993, upserts the row into the `orders_read` table that powers "My orders", and commits its offset *after* the upsert succeeds.
8. **Consumer group B — `warehouse-view`** independently reads the same record, and appends a picking task to the warehouse's work list. Its offset position is entirely its own; it is currently 900 records behind and that affects nobody else.
9. **Consumer group C — `analytics`** reads the same record, assigns it to an event-time tumbling 5-minute window, and updates the "orders per 5 minutes" counter. The window for 14:05–14:10 emits when the watermark passes 14:10 plus two minutes of allowed lateness.
10. **Consumer group D — `fraud`** reads the record, applies its rules and, if suspicious, **appends a new event** `OrderFlaggedForReview` to a different stream. It does not modify the original event; events are immutable.
11. Payment succeeds four seconds later; the payment service appends `PaymentCaptured` with the same key `order_id`, so it lands in partition 7 *after* offset 4,812,993 — the ordering that makes the orders projection's state machine correct.
12. Three weeks later a bug is found in group B's address formatting. Group B's offsets are reset to the offset corresponding to 21 days ago, its target table is truncated, and it reprocesses. Groups A, C and D are unaffected and do not even notice.
13. Ninety days later the segment containing offset 4,812,993 exceeds `retention.ms` and is deleted as a whole file. From that moment, a full replay can no longer reach this event, and recovery of any projection older than 90 days depends on snapshots.

Step 12 is the entire justification for the lesson. Step 13 is the price.

## 6. ASCII diagram

```
THE LOG — one partition, append-only, offsets never reused
                                              append here
                                                   │
  off:  0     1     2     3     4     5     6     7 ▼
      ┌─────┬─────┬─────┬─────┬─────┬─────┬─────┬─────┐
      │Order│Pay  │Order│Ship │Order│Pay  │Order│Order│ ...
      │Place│Captd│Place│Dispd│Cancl│Faild│Place│Place│
      └─────┴─────┴─────┴─────┴─────┴─────┴─────┴─────┘
         ▲              ▲                    ▲
         │              │                    │
   analytics      warehouse-view       orders-projection
   (offset 1)      (offset 4)             (offset 7)
   21 min behind   3 s behind              caught up

  Three INDEPENDENT read positions over ONE copy of the data.
  No consumer's progress deletes anything. Nobody blocks anybody.


QUEUE vs LOG — the one difference everything else follows from
  QUEUE:  [m1][m2][m3] ──consume m1──▶ [m2][m3]   m1 is GONE
  LOG:    [m1][m2][m3] ──consume m1──▶ [m1][m2][m3]
                                        ▲
                                   pointer moved; DATA STAYS


LOG AS TRUTH, STATE AS PROJECTION
                     ┌──────────────────────────────────┐
  producers ────────▶│   order-events  (SOURCE OF TRUTH)│
                     └───────┬────────┬────────┬────────┘
                   fold()    │        │        │     fold()
                             ▼        ▼        ▼
                      ┌────────┐ ┌────────┐ ┌─────────┐
                      │ orders │ │warehouse│ │analytics│
                      │ table  │ │  view   │ │  cube   │
                      └────────┘ └────────┘ └─────────┘
                       DERIVED    DERIVED     DERIVED
                       (disposable — drop & rebuild by replay)


WINDOWING BY EVENT TIME, AND A LATE ARRIVAL
  event time:  12:01  12:03  12:04 │ 12:06  12:07 │ 12:02 (LATE!)
               └──── window A ─────┘└─ window B ──┘   ▲
                  12:00–12:05        12:05–12:10      │
  watermark ────────────────────────────────▶ 12:06   │
  window A already emitted at watermark 12:05 ────────┘
  ⇒ choose ONE: drop it · retract+re-emit A · widen lateness · side-output


LOG COMPACTION — retention by KEY, not by AGE
  before: [k1:v1][k2:v9][k1:v2][k3:v7][k1:v3][k2:null]
  after:  [            ][k3:v7][k1:v3][k2:null(tombstone)]
          offsets 0,1,2 removed; 3,4,5 survive; offsets now NON-CONTIGUOUS
  reading from offset 0 now yields a SNAPSHOT of current state per key
```

## 7. Detailed example

### BookNook — the order lifecycle as an event stream

BookNook models one topic, `order-events`, keyed by `order_id`. A single order produces a sequence like this:

```
offset  event                 key        payload (abridged)
──────────────────────────────────────────────────────────────────
 ...93  OrderPlaced           ord-7781   2 items, £34.98, addr#44
 ...97  PaymentAuthorised     ord-7781   psp_ref=ch_1Nx, £34.98
 ...04  PaymentCaptured       ord-7781   psp_ref=ch_1Nx
 ...11  StockReserved         ord-7781   isbn-111 x1, isbn-222 x1
 ...45  ShipmentDispatched    ord-7781   carrier=DPD, trk=8831
 ...92  ShipmentDelivered     ord-7781   signed_by="H. Patel"
```

Six immutable facts. From that one sequence, three different teams build three completely different things, and **none of them asks the others for anything**:

**Projection 1 — the `orders` table (customer-facing).** Consumer group `orders-projection` folds events into one row per order: `order_id, status, total, placed_at, delivered_at`. `OrderPlaced` inserts with status `placed`; `PaymentCaptured` sets `paid`; `ShipmentDispatched` sets `shipped`. It is a state machine driven by the log. The customer's "My orders" page is a single indexed primary-key read.

**Projection 2 — the warehouse view.** Consumer group `warehouse-view` ignores `PaymentAuthorised` and `ShipmentDelivered` entirely and cares only about `StockReserved` and `PaymentCaptured`, producing a picking list ordered by aisle. Its data model is nothing like the orders table — it is keyed by warehouse bin, not by order. Same input events; different fold.

**Projection 3 — the analytics view.** Consumer group `analytics` windows by event time into 1-hour tumbling windows and writes `orders_per_hour`, `revenue_per_hour`, `cancellation_rate` into the warehouse (Lesson 58). It uses `allowed_lateness = 10 minutes` because mobile clients buffer.

Now the three scenarios that justify the whole architecture:

- **A new requirement arrives.** Marketing wants "time from payment to dispatch, by region, for the last 60 days". A fourth consumer group is created, started at the earliest available offset, and 60 days of history is computed in 21 minutes (§9). No migration. No backfill script. No change to any existing service.
- **A bug is fixed.** The warehouse view had been mapping two aisle codes backwards for 11 days. Fix the code, truncate the warehouse table, reset group `warehouse-view` to the 11-day-ago offset, redeploy. Groups 1 and 3 are untouched and do not even observe the incident.
- **A dispute is settled.** A customer insists they cancelled before dispatch. The log shows `ShipmentDispatched` at offset ...45 and `OrderCancelled` at offset ...63 — dispatch happened first, with timestamps and offsets. Not an opinion, a record.

And the costs BookNook actually paid, stated plainly: an 8-week engineering investment; two months in, a v2 of `OrderPlaced` that split `price` into `price_ex_tax` and `tax`, requiring an upcaster that still exists; a support escalation in week 3 because customers redirected to "My orders" after checkout occasionally saw nothing for 400 ms (fixed by rendering the order from the command handler's own response rather than the projection); and an on-call page the first time `orders-projection` lag exceeded 15 minutes.

### Second example — a payment card network's authorisation ledger

A card network cannot model a cardholder's available balance as a mutable number. The regulated requirement is that every authorisation, capture, reversal, partial reversal, chargeback and representment is an immutable, ordered, retained fact. The available balance is a *projection*: `credit_limit − sum(open authorisations) − sum(captured) + sum(reversals)`.

What is different from BookNook:

- **Retention is effectively infinite** (7–10 years by regulation), so upcasters for event schema versions genuinely live for a decade, and the storage cost is a line item that someone defends annually.
- **Ordering is a legal matter, not a performance matter.** A reversal applied before its authorisation yields a negative balance and a regulator's letter.
- **Replay is restricted.** Replaying events that trigger side effects (settlement files, cardholder notifications) would re-issue them, so replay-safe processors are isolated from side-effecting ones — the same distinction that makes "exactly-once processing" possible internally while external effects remain at-least-once (Lesson 73).
- **Temporal queries are the product.** "What was the available balance at 14:03:12.481 on 3 March?" is a question a dispute hearing actually asks.

The contrast is instructive: BookNook adopts event sourcing because it is *useful*; the card network adopts it because the alternative is *not permitted*. If your domain is in the second category, the costs in §4.5 are not a reason to reconsider.

## 8. Code or configuration example

> **Illustrative only — not production-ready.** Error handling, authentication, schema-registry integration, serialisation, retries, metrics, dead-letter routing and idempotent sinks are all omitted or simplified so the core idea stays visible. Do not paste this into a service.

```python
# ── 1. THE EVENT: an immutable fact, in the past tense ───────────────────
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import json

@dataclass(frozen=True)          # frozen=True enforces immutability in code:
class OrderPlaced:               #   an event that can be mutated is not a fact
    schema_version: int          # version TRAVELS WITH THE EVENT — without this
                                 #   you cannot upcast v1 records in 2 years' time
    event_id: str                # unique id, used by consumers to deduplicate (L73)
    order_id: str                # ALSO the partition key — see §5 step 4 and L72
    occurred_at: str             # EVENT TIME, set by the producer, not the broker.
                                 #   Windowing uses this, never processing time
    customer_id: str
    items: tuple                 # tuple not list: immutable container inside a
                                 #   frozen dataclass, so nothing can edit it later
    total_pence: int             # integer minor units — never float for money (L41)

# ── 2. THE PROJECTION: state = fold(apply, events) ───────────────────────
def apply(state: dict, event_type: str, e: dict) -> dict:
    """Pure function: (current state, event) -> new state.
    PURE is the requirement, not a style preference. If this function calls an
    API, reads the clock or uses random(), then replaying the same events twice
    produces two different results, and the projection is no longer rebuildable."""
    if event_type == "OrderPlaced":
        state["status"] = "placed"            # initial state of the state machine
        state["total_pence"] = e["total_pence"]
    elif event_type == "PaymentCaptured":
        state["status"] = "paid"
    elif event_type == "ShipmentDispatched":
        state["status"] = "shipped"
    elif event_type == "OrderCancelled":
        # ORDER MATTERS: reaching 'cancelled' from 'shipped' means a return,
        # not a cancellation. Same two facts, two different realities (§4.1).
        state["status"] = "return_requested" if state["status"] == "shipped" \
                          else "cancelled"
    else:
        pass                     # UNKNOWN EVENT TYPES MUST BE IGNORED, not raise:
                                 #   a producer deploying a new event type must not
                                 #   crash every older consumer (forward compatibility)
    return state

# ── 3. UPCASTING: old versions in the log are old forever (§4.5) ─────────
def upcast(e: dict) -> dict:
    """Transform historical event versions into the current shape AT READ TIME.
    You cannot rewrite the log — it is append-only — so this code is permanent
    for as long as v1 records remain within the retention window."""
    if e["schema_version"] == 1:
        # v1 had a single 'price'; v2 split it into net + tax at 20% VAT
        e["price_ex_tax_pence"] = round(e.pop("total_pence") / 1.20)
        e["tax_pence"] = e["total_pence_original"] - e["price_ex_tax_pence"] \
                         if "total_pence_original" in e else 0
        e["schema_version"] = 2
    return e

# ── 4. THE CONSUMER LOOP with a SNAPSHOT to bound replay cost ────────────
def rebuild(reader, snapshot_store, order_id):
    snap = snapshot_store.load(order_id)        # {"state": {...}, "offset": 91_400}
    state  = snap["state"]  if snap else {"status": "none"}
    start  = snap["offset"] + 1 if snap else 0  # resume AFTER the snapshotted offset
                                                # off-by-one here silently double-
                                                # applies one event — a classic bug
    n = 0
    for offset, event_type, payload in reader.read_from(start, key=order_id):
        state = apply(state, event_type, upcast(payload))
        n += 1
        if n % 500 == 0:                        # snapshot every 500 events: the knob
                                                # trades storage + write cost against
                                                # worst-case recovery time (§9)
            snapshot_store.save(order_id, state, offset)
    return state
```

```ini
# ── TOPIC CONFIGURATION — the retention decision, made explicitly ────────
# Topic 1: order-events — a log of FACTS. Delete by age, never by key.
name=order-events
partitions=12                    # unit of parallelism AND of ordering (L72).
                                 #   Caps consumers per group at 12. Raising it
                                 #   later re-maps keys and breaks per-key order.
replication.factor=3             # survives 1 broker loss with min.insync=2 (L71)
min.insync.replicas=2            # with acks=all, an append needs 2 of 3 replicas
cleanup.policy=delete            # age-based retention: the default for fact logs
retention.ms=7776000000          # 90 days. THIS IS A RECOVERY-TIME BUDGET, not a
                                 #   storage setting: it is a promise that any
                                 #   broken consumer can be repaired within 90 days
compression.type=zstd            # ~4:1 on JSON events (§9); costs producer CPU
max.message.bytes=1048576        # 1 MiB cap: events carry references, not blobs.
                                 #   Large payloads go to object storage (L62)

# Topic 2: book-catalogue — the CURRENT VALUE per ISBN. Age-based deletion
# here would destroy the price of any book unchanged for 90 days.
name=book-catalogue
cleanup.policy=compact           # keep at least the newest record per key (§4.9)
min.cleanable.dirty.ratio=0.5    # compact once 50% of the log is superseded records
delete.retention.ms=86400000     # keep tombstones 24h so lagging consumers SEE the
                                 #   delete before it vanishes; a consumer behind by
                                 #   more than this misses the deletion entirely
segment.ms=3600000               # roll a new segment hourly; only CLOSED segments
                                 #   are eligible for compaction, so the active
                                 #   segment always contains uncompacted duplicates
```

## 9. Numerical example

**Part A — how much storage does 90 days of BookNook order events cost?**

Assumptions, all stated (figures are *typical* for a mid-size retailer, not measured):

- 12,000 orders per day, averaged over the year.
- 7 events per order lifecycle (placed, authorised, captured, reserved, dispatched, delivered, plus one average for cancellations/corrections).
- Average serialised event payload: 420 bytes of JSON.
- Broker record overhead (key, timestamp, headers, CRC, batch framing): 70 bytes.
- Compression ratio on JSON with zstd: 4:1 (JSON compresses well because field names repeat in every record).
- Replication factor 3.
- Retention 90 days.

Step by step:

1. Events per day = 12,000 × 7 = **84,000 events/day**
2. Bytes per event on the wire = 420 + 70 = **490 bytes**
3. Raw bytes per day = 84,000 × 490 = 41,160,000 B = **41.16 MB/day**
4. Raw bytes over 90 days = 41.16 MB × 90 = 3,704.4 MB = **3.70 GB**
5. After 4:1 compression = 3.70 ÷ 4 = **0.926 GB**
6. On disk across the cluster at RF 3 = 0.926 × 3 = **2.78 GB**

**2.78 GB.** That is the entire order history of the business for a quarter, replicated — less than a phone photo library. The instructive conclusion: *for transactional event streams, storage is almost never the constraint.* The constraints are partition count, consumer throughput and operational effort.

**Part B — the stream that actually costs something.**

- Conversion rate 2.5%, so sessions/day = 12,000 ÷ 0.025 = **480,000 sessions/day**
- 9 page views per session → 480,000 × 9 = **4,320,000 page-view events/day**
- Page-view event + overhead = 180 bytes
- Raw/day = 4,320,000 × 180 = 777,600,000 B = **777.6 MB/day**
- Retention 7 days (behavioural data is used quickly or not at all) → 777.6 × 7 = **5.44 GB**
- Compression 5:1 (highly repetitive) → **1.09 GB**; at RF 3 → **3.27 GB**

So the stream with **51× more events** than the order stream occupies only 1.2× the disk, because its records are small and its retention is short. Retention, not event count, dominates storage. Changing page-view retention from 7 to 90 days multiplies that 3.27 GB to **42.0 GB** — still cheap, which is why "just keep it longer" is a reasonable instinct for streams of this size and a dangerous one at 1,000× the volume.

**Part C — how long does a full replay take?**

Additional assumptions:

- Total events in 90 days of `order-events` = 84,000 × 90 = **7,560,000 events**
- A single consumer process can deserialise and apply at **18,000 events/s** (CPU-bound on JSON parsing).
- The projection's sink is PostgreSQL, which sustains **6,000 upserts/s** on this table with batching of 500 rows per transaction.
- Live production rate = 84,000 ÷ 86,400 = **0.97 events/s**.

1. Time limited by the consumer's own CPU = 7,560,000 ÷ 18,000 = 420 s = **7 min 0 s**
2. Time limited by the database sink = 7,560,000 ÷ 6,000 = 1,260 s = **21 min 0 s**
3. The replay takes `max(bottlenecks)` = **21 minutes**, and the bottleneck is the **sink, not the log**. This is the usual outcome and it is why "add more Kafka brokers" rarely speeds up a replay.
4. Speed-up versus real time = 6,000 ÷ 0.97 = **6,186×**. Ninety days of history replays in 21 minutes.
5. **Does parallelism help?** With 12 partitions and 12 consumers, read throughput rises 12×, but the single Postgres table still caps at 6,000 upserts/s, so the replay remains 21 minutes. It drops only if the sink is partitioned too — 4 sharded sinks at 6,000/s each gives 24,000/s → 7,560,000 ÷ 24,000 = 315 s, at which point the consumer CPU limit of 18,000/s × 12 = 216,000/s is not binding and **the sink is still the constraint**.

**Part D — what snapshots buy.**

- Snapshot taken nightly. Worst case at replay time, the snapshot is **24 hours old** → events to replay = 84,000.
- Replay time from snapshot = 84,000 ÷ 6,000 = 14 s.
- Improvement over full replay = 1,260 ÷ 14 = **90× faster**.
- Cost of snapshots: one serialised state write per night, negligible. The *real* cost is that changing the projection's schema invalidates every snapshot and forces the full 21-minute replay — which is affordable here, and would not be if the full replay were 14 hours. **Snapshot cadence should be derived from your recovery-time objective** (Lesson 63): if the RTO for this projection is 5 minutes, then `events_since_snapshot ÷ 6,000 ≤ 300 s`, so snapshot at least every **1,800,000 events**, which at 0.97 events/s is far less often than nightly — nightly is comfortably sufficient.

## 10. Trade-offs

| You gain | You pay |
|---|---|
| Replay: any projection can be rebuilt from inputs that still exist | Storage for data that may never be read, and a retention window that is a hard recovery deadline |
| New consumers get full history with no backfill | Every consumer must tolerate re-delivery and unknown event types |
| Complete audit trail by construction | Events are immutable, so schema evolution must be handled by upcasting — permanently |
| Temporal queries ("state as of time T") become possible | State is derived, so every read path needs a projection built and maintained for it |
| Producer and consumer fully decoupled in time, not just in space | Projections are eventually consistent; read-your-writes needs explicit handling (Lesson 61) |
| Debugging is reproducible: replay the exact sequence | Replaying anything with external side effects re-issues them; replay-safety must be designed in |
| Storage is sequential and append-only, so writes are extremely cheap | Random access by key is not supported: finding "all events for order X" requires an index or a projection |
| One copy of data serves unlimited independent consumers | Ordering only holds within a partition, so the key design is now a permanent architectural commitment (Lesson 72) |
| Unbounded aggregation over time windows becomes natural | Event-time windowing forces an explicit, unavoidable choice between completeness, latency and cost |

The decision is **not** "events are better than tables". It is: *does your domain's value lie in the history of what happened, or only in the current state?* If auditability, reprocessing and multiple independent views are each worth real money, the costs above are a bargain. If you need "show me the current cart", a Postgres table is the right answer and event sourcing is a tax.

## 11. Failure scenarios

| Failure | What the user experiences | How it is detected | How it recovers |
|---|---|---|---|
| Projection consumer crashes | "My orders" is stale: a just-placed order is missing for as long as the outage | Consumer-group lag metric rises; liveness probe fails; lag alert fires at > 60 s | Consumer restarts, resumes at its last committed offset, drains the backlog at (capacity − incoming); lag returns to baseline in seconds to minutes (Lesson 72, §9) |
| Projection logic bug corrupts the read model | Wrong data shown — statuses, totals or ranks are subtly incorrect; often reported by a human, not a monitor | Reconciliation job comparing projection aggregates against an independently computed aggregate; customer reports | Fix code, truncate projection, reset offsets to before the bug, replay. 21 min (§9). **Only possible because the log was retained** |
| Consumer offline longer than retention | Silent permanent gap: the projection is missing a window of history and nothing says so | Alert on `consumer_offset < earliest_available_offset`, or on a sudden offset reset to earliest | Not recoverable from the log. Rebuild from a snapshot plus whatever other system has the data. Prevention: set retention > worst-case repair time, and alert on lag as a *fraction of retention*, not in seconds |
| Producer appends an unparseable / poison event | One consumer stops entirely; its projection freezes at that offset while others continue | Lag for one group climbs linearly while others are flat; deserialisation errors in logs | Route the bad record to a dead-letter topic and advance past it (Phase 4, Topic 12 — Dead-letter queues, poison messages and retries, Lesson 75). Never "skip and forget" without recording it |
| Incompatible schema change deployed by producer | Downstream features break at different times as each consumer meets its first v2 record | Schema-registry compatibility check rejects the change at deploy time — if you have one; otherwise consumer errors | Roll back the producer; add the field as optional with a default; register compatibility rules so the next attempt is blocked before release |
| Broker loses a disk | Nothing, if replication is configured correctly | Under-replicated-partition metric > 0 | Followers serve; a new replica is built from the leader. Append availability is lost only if surviving in-sync replicas fall below `min.insync.replicas` (Lesson 71) |
| Clock skew on producers | Event-time windows receive events stamped in the past or future; "orders per hour" is wrong at the boundaries | Watermark stalls, or future-dated events appear; monitor `producer_timestamp − broker_timestamp` | Clamp absurd timestamps, prefer broker-assigned log-append time where legal, and run NTP. Lesson 66 explains why this can be mitigated but never eliminated |
| Replay accidentally re-triggers side effects | Customers receive thousands of duplicate "your order shipped" emails | Outbound email rate spikes; customers complain immediately | Kill the consumer. Prevention is structural: separate replay-safe projectors from side-effecting consumers, and gate side effects behind an idempotency store (Lesson 17; Lesson 73) |
| Hot partition from a poor key | One order's events lag minutes behind while others are instant | Per-partition lag diverges sharply | Re-key the stream into a new topic (Lesson 72); changing partition count in place breaks existing key→partition mapping |

## 12. Alternatives

| Approach | How state and history are handled | Replay | Multiple independent consumers | Best suited to | Main weakness |
|---|---|---|---|---|---|
| **Event stream (this lesson)** | Log is truth; state is projected | Yes, to any retained offset | Yes, unlimited, independent | Multi-consumer integration, audit, reprocessing | Operational and conceptual complexity |
| **Work queue** (Lesson 68) | No history; message destroyed on ack | No | No — competing consumers share one stream of work | Task distribution, backpressure on slow work | Nothing survives consumption |
| **Pub/sub topic** (Lesson 69) | No history; delivered to current subscribers | No | Yes, but only those present at publish time | Fan-out notification, cache invalidation | Late subscriber sees nothing |
| **Mutable table + audit table** | Table is truth; audit is a secondary, best-effort record | Partially — depends entirely on whether the audit table captured enough | No, consumers poll the table | Most CRUD applications | Audit writes can be forgotten; derived views need bespoke backfills |
| **Change data capture (CDC)** | Table is truth; the database's replication log is turned into a stream | Yes, within the log's retention | Yes | Getting a stream out of a system you cannot modify | Events are *row diffs*, not business facts: `status: 3 → 4` carries no intent. Lesson 79 |
| **Batch ETL from a database** | Table is truth; periodic snapshots copied out | Re-run the batch | Yes | Analytics tolerant of hours of staleness | Latency measured in hours; intermediate states between snapshots are invisible forever |
| **Full event sourcing with no projections** | Log is truth; every read folds events live | Inherent | Yes | Tiny aggregates with short histories | Read latency grows without bound; unusable without snapshots |
| **Bitemporal tables** | Table is truth, but with `valid_time` and `transaction_time` columns | Query the past directly in SQL | Yes, via SQL | Finance, insurance, anywhere "as-of" queries matter and SQL is mandatory | Complex queries; no natural streaming integration; one database is the bottleneck |

The honest summary: **CDC plus a conventional database is the right first step for most teams**, because it gets you a durable, replayable stream without requiring the whole organisation to rethink how state works. Move to true event sourcing in the specific subdomain where history is the product.

## 13. When to use it

1. **Multiple independent consumers need the same data for different purposes.** The moment a third team asks for the order feed, the economics change decisively: one retained log replaces three bespoke integrations, three backfill scripts and three ongoing reconciliation arguments.
2. **Reprocessing is a realistic requirement.** If "we found a bug in how we computed X; recompute the last 30 days" is a sentence your team will say more than once a year, you need retained inputs. This applies to every analytics, ML-feature, search-index and recommendation pipeline essentially without exception.
3. **Audit or regulatory history is required.** Payments, healthcare, trading, insurance, anything with a statutory retention period. Here the audit trail is not a benefit of the architecture, it is the requirement, and deriving it from an append-only log is far more defensible than maintaining it by convention.
4. **Temporal questions are asked of the system.** "What did the customer see at the moment they clicked?" "What was inventory when the oversell happened?" These are unanswerable against mutable state and trivial against a log.
5. **Continuous computation over unbounded data.** Real-time dashboards, fraud scoring, anomaly detection, live leaderboards — anything where the answer must update as events arrive rather than on a nightly schedule.
6. **Services need to integrate without synchronous coupling.** Publishing facts to a log lets a new service consume them without the producer learning of its existence, which removes a deploy-coordination dependency between teams (Lesson 67).

## 14. When not to use it

1. **Simple CRUD where only current state matters.** A user-profile service with a settings page. The history of someone's display-name changes is worth nothing; a Postgres row with an `updated_at` is correct, cheaper, easier to query, and understood by every engineer you will ever hire.
2. **Strong immediate read-your-writes consistency on the same data path.** If the business rule is "after saving, the next read must show the new value, always, with no exception", projections introduce a lag window you then have to engineer around. Use a transactional database and read it directly.
3. **Small teams without the operational capacity.** A retained log needs brokers, monitoring, lag alerting, schema governance, consumer-group operations, replay runbooks and an on-call who understands offsets. A three-person team shipping its first product should use Postgres, `LISTEN/NOTIFY` or a managed queue, and revisit this at scale.
4. **Request/response interactions that need an immediate answer.** "Is this coupon valid?" is a synchronous query, not an event. Streaming it adds latency and no benefit (Lesson 13).
5. **Low-volume, low-value data with no history requirement.** Fifty records a day used by one consumer does not justify a broker, a schema registry and a replay runbook.
6. **Where you cannot guarantee replay safety.** If every consumer in the system performs external side effects and none can be made idempotent, replay — the entire reason you adopted this — is unusable, and you have bought the costs without the benefit.

## 15. Common mistakes

**Beginner mistakes**

- **Putting commands in the event log.** `SendShipmentEmail` is an instruction, not a fact. The log becomes a queue with worse ergonomics, and consumers start rejecting records, which is meaningless for events.
- **Mutable or non-past-tense event names.** `OrderUpdate`, `OrderStatus`. If the name does not state what happened, consumers cannot know what to do and you end up with `if payload.new_status == ...` — a row diff pretending to be a fact.
- **Assuming a stream is a queue.** Expecting records to disappear after reading, or expecting two consumers in the same group to each see every record (they do not — Lesson 72).
- **Assuming contiguous offsets.** `next = current + 1` breaks under compaction and transaction markers.
- **Windowing by processing time while reporting it as event time.** Produces numbers that are quietly, permanently wrong, especially around deploys and backlogs.
- **No `schema_version` on events.** Deciding in year two to add versioning requires guessing the version of every historical record by inspecting its fields.
- **Fat events carrying whole documents or images.** Records should be ≲ 1 MiB; blobs belong in object storage (Lesson 62) with a reference in the event.
- **Treating retention as a storage knob.** It is a recovery-time budget. Cutting it from 30 days to 3 to save £40 silently removes your ability to fix a bug discovered after a long weekend.
- **Building a projection with a non-deterministic `apply` function** — calling `now()`, a remote API or a random number generator. Replay then yields different state than the original run, and you will not find out until the replay you depend on.

**Experienced-engineer mistakes**

- **Event sourcing the whole system instead of one subdomain.** The order lifecycle justifies it; the CMS page editor does not. Blanket adoption produces a codebase where every trivial change is expensive.
- **Modelling events as persistence rather than as a domain language.** Events named after table columns (`OrderRowChanged`) tie the log's schema to the writer's internal storage, so a refactor of a private table becomes a breaking change for ten consumers.
- **Ignoring snapshot-schema coupling.** Teams add snapshots to bound replay cost, then discover that *every* projection change invalidates all snapshots, which is exactly when they most need fast replay.
- **No schema-compatibility enforcement.** Relying on code review to catch breaking changes works until the week it does not, and the failure surfaces in ten downstream services at different times.
- **Unbounded state in stream processors.** Session windows with no maximum duration, or a key space that grows forever (keying by `session_id`), causing the state store to grow until the node dies — weeks after the code shipped.
- **Reusing one topic for unrelated event types with no discipline**, so every consumer deserialises and discards 90% of the volume, and the schema becomes an unversioned union type.
- **Replaying into a sink that is not idempotent.** The replay that was supposed to repair the projection doubles every counter instead.
- **Forgetting that a projection rebuild needs capacity headroom.** Replaying at 6,000 writes/s into the same database that is serving live traffic turns a repair into an outage. Replay into a new table and swap, or throttle.
- **Conflating "the log is the source of truth" with "the log is a database".** It has no secondary indexes, no point queries by key, and no ad-hoc querying. Every access pattern needs a projection designed in advance.

## 16. Interview perspective

**What is being tested.** Whether you understand that retention-based deletion is the single design decision that produces replay, independent consumers and reprocessing — and whether you can state the costs of event sourcing without being prompted. Candidates who can only recite the benefits are read as having learned it from a conference talk rather than from an incident.

**How to answer "should we use event sourcing here?"** Never with "it depends" alone. Finish the sentence. The correct completion is: *"It depends on three specific things — whether more than one consumer needs the same data for different purposes, whether reprocessing after a bug is a requirement we will actually exercise, and whether an auditable history has independent business or regulatory value. If none of those is true, a transactional table with an audit log is the better engineering choice."*

**Strong signals to send:**

- Distinguish **event** from **command** unprompted, with the past-tense test.
- Say "the log is the source of truth and every table is a disposable projection" and then immediately name the cost: eventual consistency and schema evolution of historical events.
- Volunteer **snapshotting** when someone asks about replay cost, and know that snapshots are coupled to the projection schema.
- Distinguish **CQRS** from **event sourcing** and state that they are separable, with one example of each without the other.
- Distinguish **event time** from **processing time**, and name watermarks and the completeness/latency/cost trade-off for late data.
- Know **log compaction** and the stream–table duality in one sentence: *a compacted log is a table; a table's change history is a log.*
- Quantify: "90 days of our order events is about 2.8 GB replicated, and a full projection rebuild is roughly 21 minutes, bounded by the database sink rather than by the broker."
- Say which **subdomain** you would event-source and which you would not.

**Red flags interviewers watch for:**

- Proposing event sourcing for a simple CRUD service, with no cost discussion.
- Describing a stream as "a queue that keeps messages", with no grasp of what independent consumer offsets enable.
- Not knowing what happens when a consumer is offline longer than the retention period (answer: silent, unrecoverable data gap).
- Believing events can be edited or deleted to fix a mistake, rather than corrected by appending a new event.
- Claiming "exactly-once" without qualification, or asserting that it covers external side effects such as emails and card charges.
- No awareness that ordering is per-partition, not global — the single most common gap at this level.
- Treating schema evolution as a solved problem, or never mentioning it at all.
- Planning to replay a stream with side-effecting consumers and not noticing that this re-sends every email.
- Unbounded retention with no cost estimate, or unbounded stream-processor state with no key-space analysis.
- Saying "we'll just use Kafka" as an architecture, with no discussion of keys, partitions, retention or consumers.

## 17. Knowledge check

**Conceptual**

1. Retention-based deletion is described in this lesson as the single design choice from which replay, independent consumers and reprocessing all follow. Explain the causal chain precisely: state exactly what a broker must know in the consumption-based model that it does not need to know in the retention-based model, and then explain why that one piece of ignorance makes each of the three capabilities possible. Then state the two capabilities that consumption-based deletion provides which retention-based deletion does not.

2. A colleague proposes fixing a mis-priced historical `OrderPlaced` event by editing the record in the log, arguing that it is "obviously wrong data" and that no consumer has complained. Explain why this is unsound even if the edit were technically possible, naming at least three distinct mechanisms that break. Then describe the correct remedy, and explain what the correct remedy implies about the design of every downstream projection's `apply` function.

3. Explain the difference between event time and processing time, and then explain why watermarks are necessary rather than merely convenient — specifically, what question can a stream processor not answer without one, and why that question has no exact answer in any distributed system (relate this to Lesson 66). Finish by stating the four options for handling late-arriving data and the exact trade-off each one makes.

**Practical scenario**

4. BookNook has run `order-events` with 30-day retention for eight months. On a Tuesday, the data team discovers that the `analytics` consumer group has been mis-attributing revenue to the wrong region for an unknown period, because a region-lookup table was wrong. Investigation shows the lookup table was corrupted 47 days ago. Separately, the `warehouse-view` group was accidentally scaled to zero during a Friday deploy and has not consumed for 4 days, and the `orders-projection` group is healthy but writes to the same PostgreSQL instance that serves live customer traffic at 70% CPU. Describe exactly what can and cannot be recovered and why; state which of the three groups can be repaired by replay and which cannot; design the repair sequence including what you would do about the shared database; and specify three changes — one to configuration, one to alerting, one to architecture — that would have prevented or bounded this incident.

**Calculation**

5. BookNook adds a `page-view-events` stream to power a real-time "trending books" materialised view. Assume: 4,320,000 page-view events per day with a peak-to-average ratio of 3.5; average event size including broker overhead of 180 bytes; compression ratio 5:1; replication factor 3; a single stream-processing instance sustains 25,000 events/s; the view's sink is a key-value store sustaining 40,000 writes/s per node; retention is initially 7 days. Calculate (a) average and peak events per second, (b) peak ingress in MB/s before and after compression, (c) on-disk storage across the cluster for 7-day and for 30-day retention, (d) the minimum number of processing instances needed to keep up at peak with 50% headroom, (e) the time to replay the full 7 days of history with that number of instances, stating which component is the bottleneck and showing why, and (f) the number of sink nodes at which the processing instances rather than the sink become the limiting factor. Then state how answers (d) and (e) change if the peak-to-average ratio turns out to be 8 rather than 3.5 during a Black Friday sale, and explain which of your original design choices that invalidates.

**Progress checklist**

```
Phase 1 — Foundations (Lessons 1–17) ................................ [x] complete
Phase 2 — Scaling (Lessons 18–39) ................................... [x] complete
Phase 3 — Data and Storage (Lessons 40–63) .......................... [x] complete
Phase 4 — Distributed Systems
  [x]  1. Why distributed systems are hard .......................... Lesson 64
  [x]  2. Partial failures and the fallacies ........................ Lesson 65
  [x]  3. Clocks, time and ordering of events ....................... Lesson 66
  [x]  4. Asynchronous workflows .................................... Lesson 67
  [x]  5. Message queues ............................................ Lesson 68
  [x]  6. Publish-subscribe ......................................... Lesson 69
  [x]  7. Event streams ............................................. Lesson 70
  [ ]  8. RabbitMQ-style queues versus Kafka-style logs ............. Lesson 71
  [ ]  9. Consumer groups, partitions, offsets, ordering ............ Lesson 72
  [ ] 10. At-most-once, at-least-once, exactly-once semantics ....... Lesson 73
  [ ] 11. Idempotent consumers and deduplication .................... Lesson 74
  [ ] 12. Dead-letter queues, poison messages, retries .............. Lesson 75
  [ ] 13. Backpressure and flow control ............................. Lesson 76
  [ ] 14. Sagas and distributed transactions ........................ Lesson 77
  [ ] 15. Two-phase and three-phase commit .......................... Lesson 78
  [ ] 16. Outbox pattern and change data capture .................... Lesson 79
  [ ] 17. Leader election ........................................... Lesson 80
  [ ] 18. Consensus: Paxos and Raft ................................. Lesson 81
  [ ] 19. Quorums and tunable consistency ........................... Lesson 82
  [ ] 20. Distributed locks and leases .............................. Lesson 83
  [ ] 21. Vector clocks, conflict resolution, CRDTs ................. Lesson 84
  [ ] 22. Gossip protocols and failure detection .................... Lesson 85
  [ ] 23. Service discovery and registries .......................... Lesson 86
  [ ] 24. Circuit breakers, bulkheads, graceful degradation ......... Lesson 87
  [ ] 25. Distributed rate limiting and throttling .................. Lesson 88
  [ ] 26. Observability: logs, metrics, traces ...................... Lesson 89
  [ ] 27. Chaos engineering and fault injection ..................... Lesson 90
  [ ] 28. Microservices, service meshes, deployment patterns ........ Lesson 91
  [ ] 29. Distributed scheduling and job orchestration .............. Lesson 92
```

**Progress checklist**

- Phase 1 — Foundations (L1–17): **complete** ✅
- Phase 2 — Scaling (L18–39): **complete** ✅
- Phase 3 — Data and Storage (L40–63): **complete** ✅
- Phase 4 — Distributed Systems (L64–92):
  - [x] 1. Why distributed systems are difficult (L64)
  - [x] 2. Partial failures and network partitions (L65)
  - [x] 3. Clock differences and ordering events (L66)
  - [x] 4. Synchronous versus asynchronous workflows (L67)
  - [x] 5. Message queues (L68)
  - [x] 6. Publish-subscribe systems (L69)
  - [x] **7. Event streams (L70) ← you are here**
  - [ ] 8. RabbitMQ-style queues versus Kafka-style logs (L71)
  - [ ] 9. Consumer groups, partitions, offsets and ordering (L72)
  - [ ] 10. At-most-once, at-least-once, exactly-once (L73)
  - [ ] 11. Idempotent consumers and deduplication (L74)
  - [ ] 12. Dead-letter queues and retry queues (L75)
  - [ ] 13. Backpressure and load shedding (L76)
  - [ ] 14. Circuit breakers, bulkheads, graceful degradation (L77)
  - [ ] 15. Distributed locks and leases (L78)
  - [ ] 16. Leader election (L79)
  - [ ] 17. Quorums (L80)
  - [ ] 18. Consensus and Raft (L81)
  - [ ] 19. Distributed transactions (L82)
  - [ ] 20. Two-phase commit (L83)
  - [ ] 21. Saga pattern (L84)
  - [ ] 22. Transactional outbox and change data capture (L85)
  - [ ] 23. Monoliths, modular monoliths, microservices (L86)
  - [ ] 24. Service discovery and configuration (L87)
  - [ ] 25. Observability: logs, metrics, traces (L88)
  - [ ] 26. SLIs, SLOs and error budgets (L89)
  - [ ] 27. High availability and disaster recovery (L90)
  - [ ] 28. Active-passive versus active-active (L91)
  - [ ] 29. RTO and RPO (L92)

**NEXT LESSON: Phase 4, Topic 8 — RabbitMQ-style queues versus Kafka-style logs**

---

# Lesson 71 — RabbitMQ-Style Queues versus Kafka-Style Logs

*Phase 4, Topic 8*

## 1. Simple definition

The two families differ in **one underlying data structure**, and every other difference is a consequence of it.

A **queue broker** (RabbitMQ, ActiveMQ, SQS) stores a **collection of independent messages plus the delivery state of each one**. When a consumer acknowledges a message, the broker **deletes it**. The broker therefore holds, per message, a small amount of mutable bookkeeping: which consumer has it, when the visibility/failure timer expires, how many times it has been redelivered, whether it is paged to disk. The queue is a *bag of tasks*, and a task leaves the bag when it is done.

A **log broker** (Kafka, Kinesis, Pulsar, Redpanda) stores an **append-only sequence per partition** and keeps it there. Consumers do not remove anything; each consumer group holds its own **integer position (offset)** into that sequence. Retention is a policy on *time or size*, not a consequence of consumption. The log is a *recording*, and every reader keeps their own bookmark.

Four differences follow mechanically, and are worth stating as the lesson's spine:

| Consequence | Queue broker | Log broker |
|---|---|---|
| **Message lifetime** | Until acked (then deleted) | Until retention expires (independent of consumption) |
| **Per-message state** | O(messages) bookkeeping | O(consumer groups) — one offset each |
| **Replay** | Impossible (the message is gone) | Free — move the offset |
| **Multiple independent consumers** | Requires a fan-out copy per consumer (L69) | Native — N groups, one copy of the data |
| **Ordering** | Per queue, but broken by redelivery | Per partition, preserved by offsets |
| **Routing flexibility** | Rich (exchanges, bindings, headers, TTL, priority, delays) | Deliberately poor (topic + key) |

Terms defined before use:

- **Exchange / binding** (RabbitMQ): the routing layer that decides which queues a published message is copied into. Types: `direct` (exact key), `topic` (wildcard pattern), `fanout` (all bound queues), `headers` (attribute match).
- **Prefetch / QoS** (RabbitMQ): how many unacked messages a consumer may hold — the broker's flow-control credit.
- **Quorum queue** (RabbitMQ): a queue replicated by Raft across nodes, replacing the older mirrored classic queue for durability.
- **Partition** (Kafka): the unit of ordering and of parallelism. A topic is N independent logs.
- **Offset**: a consumer group's position in a partition. Committed offsets are the log broker's entire notion of "where we are".
- **`min.insync.replicas` + `acks=all`**: the durability contract on the write path — the producer's write is acknowledged only when that many in-sync replicas have it.
- **Log compaction**: a retention mode that keeps the *latest* record per key forever instead of keeping everything for a time window, turning a log into a replayable table.
- **Page cache / zero-copy**: the two implementation facts that explain why logs sustain very high throughput — the OS caches recently written segments, and `sendfile` moves bytes from page cache to socket without copying through user space.

The one sentence to carry out of this lesson: **a queue is a data structure for *work* (each item has one owner and disappears when done); a log is a data structure for *facts* (each item has one position and is readable by anyone, forever, until retention says otherwise) — pick by which of those your problem actually is, never by which is trendier.**

## 2. Why it exists

Both families are "a message broker", both have publishers and consumers, both are durable and both are used for exactly the same diagrams in architecture reviews. That is why teams choose badly, and the cost of choosing badly is not a crash — it is **years of workaround**.

BookNook has run both since 2023, and the comparison is concrete:

**Workload 1 — thumbnail rendering.** When a seller uploads a book cover, three derivatives must be generated. Each task is independent, takes 400 ms, must be retried on failure, and is worthless once done. There is exactly one party responsible for it. This is a **bag of tasks**, and RabbitMQ is the natural fit: per-message acknowledgment, redelivery, a dead-letter exchange for failures, per-queue length limits, and no requirement to retain anything.

**Workload 2 — `order-events`.** Seven independent consumers (email, loyalty, analytics, warehouse, fraud, recommendations, partner), each at its own pace. Analytics wants to rebuild a projection from scratch after a schema change. Fraud needs the last 24 hours replayed after a model update. The warehouse loads in 15-minute micro-batches. This is a **recording**, and Kafka is the natural fit: one copy of the data serving seven groups, arbitrary replay, retention independent of consumption.

Now the cost of getting each one wrong, which is the reason this lesson exists:

- **Thumbnail tasks on Kafka** is *possible* — use a topic with consumer groups, commit offsets after rendering — but you inherit problems for free: you must size partitions for parallelism (12 partitions cap you at 12 workers), a rebalance during deploy re-renders in-flight work, retention means completed tasks sit on disk for days, and there is no per-message retry/DLQ (you build it: retry topics, a DLQ topic, and a scheduler). The team that did this spent a quarter on retry infrastructure that RabbitMQ ships as three lines of configuration.
- **`order-events` on RabbitMQ** is *worse*, and this is the more instructive failure. To let analytics replay, you must **copy the stream somewhere else** — RabbitMQ deletes messages on ack, so there is nothing to replay. BookNook's original design solved it by **fanning out to seven queues** (L69's pattern), which meant **seven copies** of every event. At 2 KB × 1,000 events/sec that is 173 GB/day × 7 = **1.2 TB/day of storage**, and rebuilding the analytics projection required *writing the events to S3 first and replaying from there* — a parallel pipeline that existed only because the broker could not do the one thing a log does natively. When they moved to Kafka: **one copy, seven offsets, replay for free**, and the S3 replay pipeline was deleted.

The lesson, stated as a decision rule: **choose by whether a message's value ends when it is processed, or whether it needs to be re-readable by parties you have not met yet.**

## 3. Real-world analogy

A **whiteboard task board** versus a **building's security-camera recording**.

The whiteboard has sticky notes. Each note is a task. Someone takes it, does it, and **removes it from the board** — its existence and its state (taken, in progress, done) is tracked per note. If someone takes a note and goes to lunch without doing it, the note goes back on the board and someone else picks it up, possibly doing it twice. The board's capacity is finite; when it fills, work stops being accepted. **Every property is per note.** Nobody can look at the board to find out what was done last Tuesday, because the notes from Tuesday are in the bin. This is a queue.

The cameras record continuously to a rolling store. Nobody "takes" a recording. Detectives A, B and C each watch the footage independently, each at **their own position in the timeline** — A is reviewing this morning, B is auditing yesterday, C just joined and started from the beginning of the retained window. Nobody's viewing deletes anything for anyone else. When the retention policy expires a day's footage, it disappears for all of them at once, regardless of whether anyone watched it.

Three details of the analogy explain real production behaviour:

- **The camera operator does not know how many detectives there are.** Add a fourth and nothing changes for the first three — that is the log's native fan-out. On the whiteboard, adding a fourth worker means the existing three now take fewer notes: same board, redistributed work.
- **The recording is only as long as the retention window.** Ask a detective to review something from three weeks ago and the answer is "it is gone", not "it is slow". That is Kafka's retention, and it is the reason a freshly-onboarded consumer starting from `earliest` cannot see the beginning of history.
- **The whiteboard's notices are *addressed*.** One note can say "to the plumber" and only the plumber takes it; another goes "to everyone". That is RabbitMQ's exchange routing, and the cameras have nothing like it — there is one footage stream and you watch what you need. Rich routing, poor replay. Poor routing, rich replay. **You are always choosing.**

## 4. Detailed technical explanation

### 4.1 Queue-broker internals: what the per-message state buys and costs

```
   PUBLISH                        EXCHANGE (routing)              QUEUES
   ┌────────┐   routing_key   ┌──────────────────┐   ┌──────────────────────────┐
   │ seller │ ───"cover.eu"──►│ topic exchange   │──►│ q.thumb.eu   [m1 m2 m3]  │
   └────────┘                 │  cover.*  ───────┼──►│ q.thumb.us   [m4]        │
                              │  cover.#  ───────┼──►│ q.audit      [m1 m4]     │
                              └──────────────────┘   └──────────────────────────┘
                                                          │
                                        ┌─────────────────┴──────────────────┐
                                        ▼                                    ▼
                                  consumer A (prefetch 10)             consumer B
                                  delivery tags m1, m3                 delivery tag m2
                                        │
                                   ack(m1) → BROKER DELETES m1
                                   ack(m3) → BROKER DELETES m3
                                   (A crashes holding m1) → on channel close,
                                     m1 is REDELIVERED to B, with redelivered=true
```

What that bookkeeping buys:

- **Per-message retribution.** A single message can be retried, dead-lettered (DLX), delayed (TTL + DLX), or sent to a priority queue. This is enormously useful for *task* workloads and is genuinely awkward on a log.
- **Rich routing.** A `topic` exchange routes by wildcard pattern; a `headers` exchange by attributes; a `fanout` copies to all. Publishers never name queues — they name a routing key, and queues bind to patterns.
- **Flow control by credit.** Prefetch limits unacked messages, which is a clean backpressure mechanism: a slow consumer stops receiving rather than accumulating (L76).
- **Per-queue limits.** `max-length` and `max-length-bytes` bound the queue's size, and `x-overflow: reject-publish` makes the broker refuse new messages rather than grow — the only bounded-queue behaviour in the two families.

And what it costs, which is why the log family exists:

- **The broker holds O(messages) state**, not O(consumers). At 20,000 messages/sec with a 60-second unacked window, that is 1.2 M messages of bookkeeping in RAM/disk, each with metadata. Throughput per node is real but bounded by this work: typically **tens of thousands of messages/sec per node**, not hundreds of thousands of megabytes/sec.
- **Acknowledgment destroys history.** No replay, no audit, no new consumer reading the past.
- **Fan-out copies data.** Seven consumers means seven queues and seven copies (L69 §9 step 3).
- **Redelivery breaks ordering.** If m2 is in flight to a slow consumer and m1 fails back to the queue, m1 is processed after m2. Ordering is per queue but not preserved across failures.

### 4.2 Log-broker internals: why appending is so much cheaper

```
   topic: order-events   (3 partitions, each an append-only segmented log)

   partition 0  [0][1][2][3][4][5][6][7][8] ...  ← leader on broker 1
                 ▲              ▲
                 │              └── group "analytics"  committed offset = 5
                 └───────────────── group "loyalty"    committed offset = 2
   partition 1  [0][1][2][3][4] ...              ← leader on broker 2
   partition 2  [0][1][2][3][4][5][6] ...        ← leader on broker 3

   • Writes: append to the segment file, advance the offset. ONE sequential write.
   • Reads:  read from offset N forward. Sequential, hot in page cache.
   • Deletes: never per message. Whole SEGMENTS are dropped when retention
              expires — an O(1) file unlink, not an O(n) deletion.
   • Every consumer group has its own offset. Adding a group costs 1 integer.
   • Replication: each partition has a leader + followers; producers write to the
     leader, followers fetch; a write is "committed" when ISR ≥ min.insync.replicas.
```

Five implementation facts explain the throughput gap, and they are the ones worth knowing:

1. **Sequential append.** A write is "append bytes, advance the pointer". Quantified: at 1,000 events/sec of 2 KB, a naive per-message random write is `1,000 IOPS` of 4 KB pages; batched into 16 KB appends it is **125 IOPS**. On a gp3 EBS volume with a 3,000-IOPS baseline, the naive version consumes **33 %** of the volume's budget for 2 MB/s of data; the batched version uses 4 %.
2. **Page cache + zero-copy.** Recently written segments are in the OS page cache, and `sendfile()` copies from page cache to socket without a user-space copy. The broker barely touches the bytes, so throughput is bounded by disk and network, not by the broker process.
3. **Batch compression.** Records are compressed per batch, so a 3:1 ratio is normal for JSON events, which divides both network and storage by three.
4. **O(1) deletion.** Retention drops whole segment files. Compare a broker that must find and free 1.2 M individual message records.
5. **Consumers do not contend.** Each group reads its own offset; there is no broker-side queue to drain, no per-message lock, and reads are served from page cache, so **adding consumers does not slow down the producers** (until the disks saturate).

What the log gives up: routing (`topic + key` and nothing else), per-message retry (`you build retry topics`), per-message TTL/priority/delay (`you build schedulers`), and bounded queueing (a lagging consumer just grows lag until retention deletes the data — §11.5).

### 4.3 Durability and the acknowledgment ladder

Both families let you trade durability for latency, and both ladders are worth memorising because "durable" is not a boolean.

| Level | RabbitMQ | Kafka | Loses on |
|---|---|---|---|
| Fire and forget | `durable=false` queue, non-persistent message | `acks=0` | Broker restart, or any crash before flush |
| Broker memory | durable queue, transient message | `acks=1` (leader only) | Broker process crash; leader loss |
| Broker disk | persistent message + durable queue | `acks=all` + `min.insync.replicas=1` | Disk failure of that one node |
| Replicated | **quorum queue** (Raft majority ack) | `acks=all` + `min.insync.replicas=2`, RF=3 | Survives one broker loss |
| Replicated + fenced | quorum queue + publisher confirms | `acks=all` + `enable.idempotence=true` + `transactional.id` | Nothing on the write path; duplicates removed by idempotent producer |

Two subtleties people miss:

- **`acks=all` with `min.insync.replicas=2` is a write-availability setting, not just a durability one.** If ISR drops to 1 (a follower is slow), writes are **rejected** until it catches up. That is CP behaviour (L59) chosen deliberately: no silent durability loss, but an outage instead.
- **Publisher confirms / `acks=all` tell you the broker has it, not that the consumer processed it.** The at-least-once contract and its idempotency obligation (L68 §4.2, L73) are unchanged by any broker setting.

### 4.4 The operational surface, compared

| Concern | RabbitMQ | Kafka |
|---|---|---|
| **Unit of scaling** | Consumers; add queues/partitions for parallelism | **Partitions**; consumer parallelism is capped by partition count |
| **Deploy-time disruption** | Consumers disconnect and reconnect; unacked messages redeliver | **Rebalance**: partitions move between consumers (L72) |
| **Disk growth** | Bounded by `max-length` or consumes everything | Retention by time/size/compaction; **predictable** |
| **Memory pressure** | Memory alarm → publishers **blocked** (flow control) | Page cache is reclaimed by the OS; brokers rarely alarm |
| **Recovery after outage** | Messages wait in the queue; consumers drain | Consumers resume from committed offsets; lag drains |
| **Ordering** | Per queue, broken by redelivery (use a single consumer) | Per partition, preserved |
| **Delayed / scheduled messages** | Native (TTL + DLX, or the delayed-message plugin) | Build it (retry topics, or a scheduler service) |
| **Priorities** | Native | Build it (separate topics) |
| **Multi-tenancy** | vhosts, per-queue limits, per-user permissions | Topics and ACLs; quotas on produce/fetch |
| **Tiered storage** | Not native (paging to disk, not an archive) | Native in most distributions; big lever on retention cost |

### 4.5 Where each one's failure modes live

The two brokers fail in **different shapes**, and knowing the shape is how you recognise which one you are standing in at 3 a.m.:

- **Queue brokers fail by filling.** The queue grows, memory alarms fire, publishers are blocked by flow control, and the *producer* stops working even though the *producer* is healthy. The failure is **visible and bounded**: queue depth, unacked count, memory alarm.
- **Log brokers fail by lagging.** Nothing blocks; the producer keeps writing happily, and the consumers fall further behind until either they catch up or **retention deletes data they have not read** (§11.5). The failure is **silent and unbounded**: nothing alarms unless you watch consumer lag *in time*.

That asymmetry is why `consumer lag` is the single most important metric in a log-based system and `queue depth` is not (L68 §4.5).

## 5. Request / data flow

The same BookNook workload — "a seller uploaded a cover" — rendered through both families, to make the difference concrete rather than conceptual.

```
 ── (A) QUEUE BROKER (RabbitMQ) — a bag of tasks ──────────────────────────────
 publisher: publish(exchange="covers", routing_key="cover.eu.small", body)
            + publisher confirm → broker has it on 2 of 3 nodes (quorum queue)
      ▼
 exchange "covers" (topic) binds:
      cover.eu.*   → q.thumb.eu
      cover.us.*   → q.thumb.us
      cover.#      → q.audit
      ▼
 q.thumb.eu: [task1][task2][task3]
      ▼ prefetch=10
 worker 7 takes task1 (delivery tag 41) → renders → ack(41) → BROKER DELETES IT
      ✓ 400 ms later the task does not exist anywhere. No replay possible.
      ✗ worker 8 crashes with task2 unacked → broker redelivers task2 to worker 9
        → 2 renders (at-least-once), ordering now task3 → task2
      ✓ a poisoned task fails 5× → dead-letter exchange → q.thumb.dlq → alarm

 ── (B) LOG BROKER (Kafka) — a recording ──────────────────────────────────────
 producer: produce(topic="cover-uploads", key=seller_id, value=event, acks=all)
           + enable.idempotence → no duplicates from producer retries
      ▼
 topic cover-uploads, 12 partitions; key=seller_id → partition 7
      partition 7: [ .. offset 4,102 ][ 4,103 ][ 4,104 ]
                                          ▲
   group "thumbnailer", consumer 3   committed offset = 4,102 → reads 4,103 next
   group "analytics"                committed offset = 3,900 → reads from 3,900
   group "audit-2027-rebuild"       committed offset = 0     → reads from the START
      ✓ ONE copy of the data. Three groups, three positions, no copies.
      ✓ replay is a number in a table, not a pipeline
      ✗ no per-message retry: a failing record blocks its partition (or you
        skip it and build a retry topic; there is no DLX)
      ✗ all tasks for one seller are serialised: that seller's uploads are
        ordered, and one slow task stalls that key's queue
```

The two boxes at the end are the whole decision. If you need **per-message retry, routing, delays, priorities, and bounded queues**, the queue broker ships them. If you need **one copy, many independent readers, replay, and high throughput**, the log ships them. Choosing the wrong one moves a quarter of engineering into your backlog.

## 6. ASCII diagram

The two data structures side by side — this is the diagram to draw in an interview:

```
      QUEUE: state is per MESSAGE                     LOG: state is per CONSUMER
 ┌──────────────────────────────────┐        ┌──────────────────────────────────┐
 │ m1 ✓acked → DELETED              │        │ p0: [0][1][2][3][4][5][6][7]     │
 │ m2 in-flight → worker 3, 12s left│        │        ▲group A=2  ▲group B=6    │
 │ m3 ready                         │        │ p1: [0][1][2][3][4][5]           │
 │ m4 ready                         │        │        ▲group A=5                │
 │                                  │        │ p2: [0][1][2][3]                 │
 │ rows in queue: 4                 │        │        ▲group A=4  ▲group B=1    │
 │ rows of STATE: 4  (O(messages))  │        │ rows of STATE: 5 offsets         │
 │                                  │        │            (O(groups × parts))   │
 │ adding a 2nd consumer:           │        │ adding a 2nd consumer group:     │
 │   helps drain, does NOT           │        │   1 new offset tuple. Data       │
 │   duplicate the data             │        │   is NOT duplicated.             │
 │                                  │        │                                  │
 │ ack = the data is gone.          │        │ commit = "I have seen up to N".  │
 └──────────────────────────────────┘        └──────────────────────────────────┘
```

The durability ladder, drawn once, since it applies to both:

```
   acks=0 / fire-and-forget   ──► can vanish on a broker restart
        │                            (use: metrics you can lose)
        ▼
   acks=1 / single node       ──► survives the producer, not the broker
        │
        ▼
   acks=all, min.isr=1        ──► survives a broker PROCESS crash
        │
        ▼
   acks=all, min.isr=2, RF=3  ──► survives a BROKER loss   (BookNook's default)
        │                          ⚠ but writes REJECT if ISR < 2 → a CP choice
        ▼
   + idempotent / transactional ──► no duplicates from producer retries
                                     (still at-least-once to the CONSUMER)
```

## 7. Detailed example

### 7.1 The wrong tool, and what it cost

BookNook's `order-events` lived on RabbitMQ from 2023 to 2025. The architecture was, on paper, reasonable:

```
  order-svc ──► exchange "orders" (topic) ──► 7 queues ──► 7 consumer services
```

The problem surfaced in three places, each a different symptom of one wrong choice:

1. **Storage.** Seven queues × 173 GB/day of retained copies. RabbitMQ does not need to retain (it deletes on ack), but BookNook *wanted* retention, so they ran consumers that drained each queue into S3 for replay. Effective cost: **~1.2 TB/day written three times** (queue, S3, and the replay path's re-ingest).
2. **Adding a consumer required a backfill.** A new subscriber only saw events published *after* it was bound — there was no "start from the beginning". The recommendation team spent two weeks replaying 90 days from S3 through their own ingestion, re-implementing the ordering guarantees the broker had already given up.
3. **A schema change became a coordinated outage.** To add `payment_method_fingerprint`, all seven queues had to be drained (or accept a mixed-version window), because messages already in a queue are immutable and there is no way to re-derive them.

The migration to Kafka was six weeks and reduced:
- **storage**: 1.2 TB/day → **173 GB raw / ~58 GB compressed per replica** for the same seven consumers, because there is **one** copy;
- **consumer onboarding**: two weeks → **one day** (set `auto.offset.reset=earliest` on a fresh group and read the retained window);
- **schema changes**: coordinated → **additive and independent**, because old and new consumers read the same retained log at their own offsets, so both versions can run simultaneously for as long as needed.

What BookNook *kept* on RabbitMQ: the thumbnail pipeline and the transactional email pipeline — both genuine bags of tasks, both needing per-message retry and DLQ, neither needing replay. **Same company, same year, two correct answers.** That is the point of the lesson.

### 7.2 The throughput that made the decision for them

The apparent reason to migrate was "Kafka is faster", and it was wrong. BookNook pushes **2 MB/s** at peak (1,000 events/sec × 2 KB). Both brokers handle that without noticing:

```
  RabbitMQ: 20,000 msg/s/node ÷ 1,000 msg/s = 20× headroom on one node
  Kafka:    ~50-200 MB/s/broker sustained; 2 MB/s = 1-4% of one broker
```

The real reason was **storage amplification and replay**, and this is the pattern to internalise: **the choice between these families is almost never about throughput at the volumes most systems actually run.** It is about:

- how many independent readers there are (7 → log),
- whether anyone needs the past (yes → log),
- whether messages need per-message retry/routing/delay (yes → queue),
- and how much a copy of the data costs per day (7 copies → log).

The team that justifies Kafka with "throughput" and then runs 20 msg/sec has adopted a rebalancing-and-partitioning operational burden for a problem they did not have. The team that justifies RabbitMQ with "simplicity" and then builds a replay pipeline in S3 has built a worse log.

## 8. Code or configuration example

```python
# ============================================================================
# (A) RABBITMQ — the task path. BookNook thumbnail rendering.
# Each number is a decision; the comments say which.
# ============================================================================
import pika

conn = pika.BlockingConnection(pika.ConnectionParameters(
    host="rabbitmq", heartbeat=30, blocked_connection_timeout=10,
))
ch = conn.channel()

# 1. DURABILITY: exchange and queue durable, message persistent, QUORUM queue.
#    A classic queue on one node is a memory buffer wearing a queue's clothes.
ch.exchange_declare(exchange="covers", exchange_type="topic", durable=True)
ch.queue_declare(
    queue="q.thumb.eu",
    durable=True,
    arguments={
        "x-queue-type": "quorum",          # Raft-replicated: survives node loss
        "x-max-length": 200_000,           # BOUNDED. §11.1 — never unbounded.
        "x-overflow": "reject-publish",    # refuse new work instead of eating RAM
        "x-dead-letter-exchange": "covers.dlx",   # poison path, L75
        "x-delivery-limit": 5,             # quorum queues: stop the infinite loop
    },
)
ch.queue_bind(queue="q.thumb.eu", exchange="covers", routing_key="cover.eu.*")

# 2. PUBLISHER CONFIRMS: know the broker has it, not just that we sent it.
ch.confirm_delivery()
ch.basic_publish(
    exchange="covers", routing_key="cover.eu.small",
    body=payload,
    properties=pika.BasicProperties(
        delivery_mode=2,                   # persistent
        message_id=task_id,                # ← idempotency key for the consumer
        content_type="application/json",
    ),
    mandatory=True,                        # surface unroutable messages as errors
)

# 3. CONSUMER: manual ack, bounded prefetch, idempotent processing.
def on_message(ch, method, props, body):
    if already_processed(props.message_id):
        ch.basic_ack(method.delivery_tag)      # ack duplicates immediately
        return
    try:
        render(body)
        mark_processed(props.message_id)
        ch.basic_ack(method.delivery_tag)      # ACK ONLY AFTER the side effect
    except Transient:
        ch.basic_nack(method.delivery_tag, requeue=True)   # back to the queue
    except Permanent:
        ch.basic_nack(method.delivery_tag, requeue=False)  # → DLX (x-dead-letter)

ch.basic_qos(prefetch_count=10)   # credit-based backpressure (L76).
                                  # 10 ≈ p99 render time (400ms) × 25 tasks/s,
                                  # from Little's Law — not a round number.
ch.basic_consume(queue="q.thumb.eu", on_message_callback=on_message)
ch.start_consuming()
```

```python
# ============================================================================
# (B) KAFKA — the fact path. BookNook order-events.
# ============================================================================

# --- producer: durability + no duplicates from its own retries --------------
producer = KafkaProducer(
    bootstrap_servers=SEEDS,
    acks="all",                       # wait for all in-sync replicas (§4.3)
    enable_idempotence=True,          # dedupes producer retries: no dup sequences
    compression_type="zstd",          # ~3:1 on JSON events → 1/3 network + disk
    linger_ms=10,                     # batching: at 2 MB/s → 125 batches/s @16KB
    batch_size=16 * 1024,
    max_in_flight_requests_per_connection=5,   # safe ONLY with idempotence on
    retries=2**31 - 1,                # bounded by delivery.timeout.ms, not count
    delivery_timeout_ms=120_000,
)

fut = producer.send(
    "order-events",
    key=str(order_id).encode(),       # ← partition key = ordering key (L72)
    value=event_json,
    headers=[("schema_version", b"3"), ("trace_id", trace_id.encode())],
)
fut.get(timeout=5)                    # or handle the callback; never ignore it
```

```properties
# topic configuration — the retention and durability policy IS the design
num.partitions=12                 # unit of parallelism AND ordering (L72)
replication.factor=3              # survive one broker loss
min.insync.replicas=2             # with acks=all: writes reject if ISR < 2
retention.ms=604800000            # 7 days: replay window for all 7 groups
compression.type=producer

# log compaction instead of time retention, for a changelog/table topic
# cleanup.policy=compact
# min.cleanable.dirty.ratio=0.5
# delete.retention.ms=86400000    # keep tombstones 24h for correct rehydration
```

```yaml
# Consumer group, configured for stability (details in L72):
consumer:
  group.id: order-events-analytics
  auto.offset.reset: earliest        # a NEW group replays the retained window.
                                     # 'latest' silently skips history — §11.3
  enable.auto.commit: false          # commit AFTER processing, never on a timer
  max.poll.records: 400              # bounded so max.poll.interval is not breached
  max.poll.interval.ms: 300000       # 400 × 700ms = 280s < 300s ✓
  session.timeout.ms: 45000
  heartbeat.interval.ms: 3000
  partition.assignment.strategy: org.apache.kafka.clients.consumer.CooperativeStickyAssignor
  group.instance.id: analytics-pod-7  # STATIC membership: no rebalance on restart
```

## 9. Numerical example

BookNook's `order-events`: **1,000 events/sec** at peak, **60/sec** at baseline, **2 KB** per event, 7 consumer groups, retention **7 days**.

**Step 1 — storage, one copy versus seven.**

```
  raw volume        = 1,000 × 2 KB            = 2 MB/s = 173 GB/day
  compressed (zstd, ~3:1 for JSON)            = 58 GB/day
  replication 3 (Kafka stores 3 copies)       = 173 GB/day on disk
  7-day retention                             = 1.21 TB standing

  QUEUE-BROKER FAN-OUT (L69 pattern), 7 queues:
     7 × 173 GB/day = 1.21 TB/day WRITTEN, and if retained for replay:
     7 × 1.21 TB = 8.5 TB standing
  ⇒ 7× the storage, 7× the write bandwidth, 7× the consumer fleet to operate.
```

**Step 2 — why the append is cheap, quantified (the number L70 promised).**

```
  1,000 events/s as individual 4 KB random writes   = 1,000 IOPS
  1,000 events/s batched into 16 KB appends         = 125 IOPS
      (8 events per 16 KB batch → 125 batches/s)

  On a gp3 volume with a 3,000-IOPS baseline:
     naive   : 1,000 / 3,000 = 33 % of the volume's IOPS budget for 2 MB/s
     batched :   125 / 3,000 =  4 %
  On NVMe (500k+ IOPS) neither matters — which is why "Kafka is fast" is
 hardware-dependent and why the general claim is misleading.
```

**Step 3 — partition count from throughput, not from vibes.**

```
  Per-partition throughput guidance: ~10 MB/s write, ~30 MB/s read.
  Required: 2 MB/s.  ⇒ ONE partition would carry it.

  But partitions are also the parallelism cap (L72), and 7 groups ×
  desired consumer counts matter more than raw bytes:
       analytics needs 4 consumers, warehouse 2, fraud 4, recs 2 …
  BookNook chose 12 partitions: 12 × 10 MB/s = 120 MB/s of headroom (60×),
  and a consumer-parallelism ceiling of 12 per group.

  Cost of 12 partitions × 3 replicas = 36 partition replicas per broker set;
  each carries metadata, a leader election path, and a rebalance slot.
  At BookNook's scale, 12 is comfortable; 1,000 would be an operational
  liability for zero benefit (more in §11.6).
```

**Step 4 — what `acks=all` costs on the latency budget.**

```
  acks=1 (leader only):          p50 2 ms, p99 5 ms
  acks=all, min.isr=2:           p50 4 ms, p99 9 ms      (+1 cross-AZ RTT)
  ⇒ +2 ms p50, +4 ms p99 on EVERY produce.

  At 1,000 events/s that is 1,000 × 0.002 = 2 seconds of extra in-flight
  work per second (Little's Law: +2 in-flight requests). Negligible.
  At 100,000 events/s it is +200 in-flight and batched acks start to matter
  in broker CPU. The trade is real but almost always worth it: acks=1 loses
  data on a leader failover, and "we lost 3 seconds of orders" is worse than
  "we asked 2 ms more per event".
```

**Step 5 — queue-broker sizing for the thumbnail path, for contrast.**

```
  Thumbnail tasks: 40/s baseline, 900/s burst for 90 s, 1 KB messages.
  Burst volume = 81,000 messages; peak memory if all in RAM at 1 KB + ~200 B
  bookkeeping ≈ 97 MB. With a 40 % memory alarm on a 4 GB node, safe.
  BUT with x-max-length = 200,000 and x-overflow = reject-publish:
     at 900/s, the queue fills in 200,000/900 = 222 s
     ⇒ burst absorption is bounded at ~3.7 minutes, by design.
  That bound is the FEATURE: the broker tells the publisher "no" (L76)
  instead of growing until the node dies. Kafka has no equivalent — its
  consumers just fall behind and retention eventually deletes unread data.
```

**Step 6 — the operational cost nobody budgets for.** Rebalancing (L72): each rebalance across 12 partitions and 4 consumers takes **~5–30 s** of paused consumption, and during a rolling deploy of 4 pods that is **4 rebalances** = up to 2 minutes of stalled lag, plus duplicate processing of every in-flight batch. RabbitMQ's equivalent — consumers reconnecting — costs redelivery of unacked messages but no coordination round; it is less disruptive at this scale and *more* disruptive at 200 consumers, which is the trade inverted.

## 10. Trade-offs

| Decision | Buys you | Costs you | Choose it when |
|---|---|---|---|
| **Queue broker (RabbitMQ/SQS)** | Per-message retry, DLX, TTL, priorities, rich routing, bounded queues, credit-based backpressure | No replay; fan-out copies; O(messages) broker state | Task distribution, work queues, request/reply, delayed jobs (L68) |
| **Log broker (Kafka/Kinesis)** | One copy for N groups, replay, high throughput, ordering per key, predictable retention | Rebalance complexity; partition-count commitment; you build retry/DLQ/scheduling | Event streams with multiple independent readers (L70, L72) |
| **RabbitMQ with quorum queues** | Raft-replicated durability without Kafka's ops | ~3× write amplification; lower per-node throughput | Durable task queues where correctness matters |
| **Kafka with `acks=all`, `min.isr=2`** | No silent data loss | Writes **reject** when ISR < 2 — an availability choice | Any event you cannot afford to lose |
| **Kafka with `acks=1`** | Lower produce latency | Loses data on leader failover | Metrics/telemetry you can lose |
| **Log compaction** | A replayable *table* from a topic (latest per key) | Tombstone handling; unbounded key space | Changelogs, materialised views, config distribution |
| **More partitions** | More consumer parallelism; more produce/consume throughput | Rebalance cost, metadata overhead, longer leader elections | Only when a consumer or throughput ceiling is actually hit |
| **One topic per event type** | Clean subscriptions, independent retention | Topic sprawl; ordering across types is impossible | Distinct audiences with distinct lifetimes |
| **Single "events" topic** | Simple publishing; total order per key | Everyone filters everything; coupling | Early stage, one consumer |

## 11. Failure scenarios

**11.1 — The unbounded queue that ate the broker.** A queue had no `x-max-length`. A consumer bug made it stop acking; the queue grew to 41 M messages, hit the memory high-watermark, and RabbitMQ **blocked all publishers on the node** — including three unrelated services that shared it. **Symptom:** healthy producers timing out on publish with `blocked` errors, and queue memory graphs climbing for hours. **Fix:** bounded queues with `reject-publish` (surface backpressure to the publisher, L76), per-vhost separation so one team cannot starve another, and an alarm on queue depth *and* unacked count.

**11.2 — Non-durable queue, durable expectations.** A classic queue was declared without `durable=true`, or a message published without `delivery_mode=2`. A broker restart silently dropped 40 minutes of work. **Symptom:** tasks that were accepted are simply gone; no error anywhere. **Fix:** durability is a three-part contract (durable exchange, durable queue, persistent message) plus publisher confirms to verify it, and quorum queues for anything you would page someone about.

**11.3 — `auto.offset.reset=latest` on a new consumer.** A new analytics group was created during a deploy and started at `latest`; 7 days of retained history was ignored, and the dashboard showed an empty chart. The data was never lost — it was never read. **Symptom:** "Kafka lost our events" that turns out to be a consumer config. **Fix:** `earliest` for backfills and replays, `latest` only for live-only consumers, and a group-creation checklist that states which.

**11.4 — Rebalance storm during a deploy** (see L72 §7). A consumer whose batch processing exceeded `max.poll.interval.ms` was evicted, rejoined, was given the partition again, exceeded again — a permanent rebalance loop that produced continuous duplicates and zero net progress. **Symptom:** lag rising, `rebalance-rate` elevated, consumer logs full of `Attempt to heart beat failed`, CPU high, throughput zero. **Fix:** bound `max.poll.records` by `max.poll.interval ÷ per-record time`, and decouple long processing from the poll loop (pause/resume, or a worker pool with explicit offset management).

**11.5 — Retention deleting unread data.** A consumer was down for 9 days with 7-day retention. On recovery it resumed from a committed offset that **no longer existed**, fell back to `auto.offset.reset=earliest`, and skipped 2 days silently. **Symptom:** a gap in a projection that is discovered weeks later, if ever. **Fix:** alarm on lag measured **in time**, not records (`lag_seconds > retention × 0.5`), and treat "lag approaching retention" as a paging incident.

**11.6 — Too many partitions.** A team set 1,000 partitions "for scalability" on a topic carrying 40 msg/sec. Rebalances took minutes, leader elections on broker restart were slow, `__consumer_offsets` churn rose, and metrics cardinality exploded the monitoring bill. **Symptom:** deploys that stall consumption, and a monitoring bill nobody can explain. **Fix:** partition count from measured consumer parallelism, with headroom for 2–3× growth, not from ambition.

**11.7 — Disk full stops the whole broker.** Kafka retention was time-based only; a traffic spike tripled volume; disks filled; the broker went read-only/offline and **every** topic on it stalled. **Symptom:** a single-node problem taking out unrelated topics. **Fix:** retention by **size as well as time** (`retention.bytes`), per-topic quotas, and a disk-usage alarm at 70 %.

**11.8 — Compaction configured on the wrong topic.** A topic with 400 M distinct keys was set to `cleanup.policy=compact` to "save space"; compaction ran continuously, recompressing segments, and produced a background I/O load that degraded every consumer's read latency. **Symptom:** unexplained p99 read regression correlated with compaction cycles. **Fix:** compaction is for **key spaces you can enumerate and rehydrate** (config, latest state per key), not for event logs whose keys are unbounded.

## 12. Alternatives

| Instead of | Consider | When |
|---|---|---|
| RabbitMQ | **SQS/SNS** | You want zero broker operations and can live with at-least-once, 14-day retention and no ordering beyond FIFO groups |
| RabbitMQ | **Kafka** | You need replay, many groups, or predictable long retention |
| Kafka | **Kinesis / Pub-Sub / Pulsar** | Managed operations, cloud-native IAM, or multi-tenancy features |
| Kafka | **Redpanda / WarpStream** | Kafka API without JVM/ZooKeeper ops, or object-storage-backed retention |
| Either | **Postgres-as-queue** (`FOR UPDATE SKIP LOCKED`) | Low volume (<100/s), and you want the enqueue inside the caller's transaction (but watch vacuum pressure) |
| Either | **Outbox + CDC** (L85) | You need the event to be atomically consistent with a database write |
| Kafka for tasks | **RabbitMQ/SQS** | Per-message retry, DLQ, delays, priorities — the log does not have them |
| RabbitMQ for events | **Kafka** | More than ~2 independent consumers, or any replay requirement |

## 13. When to use it

**Use a queue broker when:**
- Each message has **one responsible processor** and is worthless after processing.
- You need **per-message** retry, dead-lettering, delay, TTL, or priority.
- You need **routing** (wildcards, headers, fan-out to specific queues).
- You need **bounded** queueing with explicit backpressure to publishers.
- Volume is in the hundreds-to-tens-of-thousands of messages/sec.

**Use a log broker when:**
- **Multiple independent consumers** read the same facts at their own pace.
- Anyone needs to **replay** — new consumers, rebuilds, model retraining, audit.
- You need **predictable retention** independent of consumption.
- You need **ordering per key** with parallelism across keys.
- You need **throughput** or **one copy of the data** rather than N fan-out copies.

## 14. When *not* to use it

**Not a queue broker when:** you need replay (there is none), you have 5+ independent consumers (fan-out copies), you need very high sustained throughput on one node, or you want a long retention window.
**Not a log broker when:** each message is a task with one owner, you need per-message retry/DLQ/delay/priority, you cannot commit to a partition count, your team cannot operate rebalances, or your volume is 20 msg/sec and SQS would be free.

## 15. Common mistakes

1. **Choosing by trend rather than by data structure.** "We're using Kafka" as an identity.
2. **Fanning out seven queues on RabbitMQ for what is one log.** 7× storage and a hand-built replay pipeline (§7.1).
3. **Using Kafka as a task queue without building retry, DLQ and delays.** A quarter of work the queue broker ships.
4. **Declaring a queue without full durability** (queue + message + replicated), then expecting it to survive a restart (§11.2).
5. **No `max-length` / `x-overflow`.** The broker blocks all publishers when one consumer stalls (§11.1).
6. **No `min.insync.replicas` awareness.** `acks=all` with `min.isr=1` loses data on leader failover, and with `min.isr=2` rejects writes when a follower lags — both are surprises if unplanned.
7. **Unbounded partition count.** Rebalance and metadata costs at 1,000 partitions are severe (§11.6).
8. **Time-only retention.** Disks fill on the busiest day (§11.7).
9. **`auto.offset.reset=latest` on a group that needed history** (§11.3).
10. **Compaction on a high-cardinality event topic** (§11.8).
11. **Assuming the broker replaces idempotency.** Both families deliver at-least-once to consumers; the dedup is yours (L73–74).
12. **Measuring broker health, not consumer health.** Queue depth and lag are the metrics; broker CPU is not.
13. **Comparing throughput benchmarks at volumes you will never run.** 2 MB/s vs 100 MB/s is not your decision point (§7.2).
14. **Ignoring egress/request pricing.** Log brokers charge by bytes and connections; queues charge by request. At 900/sec sustained, request-priced queues become the largest line item (L68 §9).

## 16. Interview perspective

**What is actually being tested.** Whether you can reduce "RabbitMQ vs Kafka" to the underlying data structure and then choose from requirements rather than reputation. Strong candidates say "one is a bag of tasks, the other is a recording", then pick the tool from the workload's properties — number of independent readers, replay need, per-message retry need — and mention the operational cost they are accepting.

**Answers that land well:**

- *"Kafka or RabbitMQ for our event bus?"* — "Depends on two questions. How many independent consumers, and does anyone need to read the past? If it's one consumer per message and work disappears when done, RabbitMQ is simpler and gives me per-message retry and a DLX for free. If it's a fact that four teams read at their own pace, and someone will want a replay after a schema change, I want a log: one copy, offsets, predictable retention. At BookNook we kept both — thumbnails on RabbitMQ, order events on Kafka — because they are different problems."
- *"Why does Kafka scale better?"* — "Because it appends to segmented files and never deletes per message: sequential writes, page-cache reads with zero-copy, batch compression, and O(1) retention by dropping segments. A queue broker keeps per-message delivery state, so its throughput is bounded by bookkeeping, not by the disk."
- *"What does `acks=all` cost you?"* — "One cross-AZ round trip — about 2 ms p50, 4 ms p99 — and, more importantly, write availability: with `min.insync.replicas=2`, if the ISR drops to one, produces are rejected. That is a deliberate CP choice, and I'd state it as such rather than as a durability setting."
- *"How would you add a consumer that needs 90 days of history?"* — "That's the question that decides the architecture. On a log: point a new group at the retained window, or extend retention. On a queue broker: there is nothing to read — you have to have copied events somewhere else, which is exactly the pipeline I'd be trying to delete."

**Common interview tasks:** given a workload, choose between queue and log with justification; explain why a queue cannot replay; size partitions from consumer parallelism; explain `acks`/`min.insync.replicas` trade-offs; explain what breaks during a rebalance; design retry/DLQ on a log; compare cost models at a stated volume.

**Red flags interviewers watch for:**

- "Kafka is always better" or "RabbitMQ doesn't scale", with no workload.
- Not knowing that a queue deletes on ack, and therefore cannot replay.
- Using Kafka as a job queue with no mention of retry/DLQ design.
- Believing `acks=all` is purely a durability setting.
- No mention of partitions as a parallelism *cap*.
- Choosing on throughput at volumes they cannot estimate.
- No operational awareness: rebalances, disk sizing, retention, monitoring.
- Thinking the broker provides exactly-once to consumers.

## 17. Knowledge check

**Conceptual**

1. State the single structural difference between a queue broker and a log broker, and derive from it four consequences: message lifetime, per-message state, replay, and multi-consumer cost. Then explain why "the log is more scalable" is an incomplete claim.
2. Explain why appending is cheaper than per-message bookkeeping, naming the specific mechanisms (sequential writes, page cache, zero-copy, batch compression, O(1) segment deletion). Then explain which of those mechanisms depends on the storage hardware, and therefore when the throughput argument evaporates.
3. A team needs per-message delay (retry in 30 minutes), per-message retry counts, and a dead-letter path, plus 4 independent downstream readers. Explain which requirement each family fails and propose the smallest architecture that satisfies all four.

**Practical scenario**

4. BookNook is building two new pipelines. **(A)** "Invoice PDF generation": 12,000 invoices/hour at month end, each taking 900 ms, retried up to 5 times on failure, with failures needing human review. **(B)** "Warehouse order feed": every order event, read by a nightly loader, a real-time dashboard, and a fraud model that must be re-run across the last 30 days whenever it is retrained. For each pipeline, choose the family and justify with four properties (readers, replay, retry, ordering), then specify: partition/queue layout, durability settings, retention, the failure path, the three metrics you would alarm on, and the monthly storage cost at 2 KB per message using BookNook's 1,000 events/sec peak / 60/sec baseline.

**Calculation**

5. A stream carries 800 events/sec of 1.8 KB at peak and 50/sec baseline. (a) Compute the raw daily volume, the compressed volume at 3:1, and the standing storage for 7-day retention with replication factor 3. (b) Compute the same for a queue-broker fan-out to 5 consumers that must be retained for replay. (c) Compute the IOPS difference between unbatched 4 KB writes and 16 KB batched appends at peak, and express both as a percentage of a 3,000-IOPS volume budget. (d) State the partition count you would choose if each consumer needs at most 3 parallel instances per group, and justify why not 4× that.

6. A topic has 12 partitions, 3 replicas, `min.insync.replicas=2`, `acks=all`. (a) Compute the partition replicas across the cluster and the number of broker losses the write path tolerates. (b) Compute the produce latency added relative to `acks=1` if the cross-AZ RTT is 1.8 ms, at p50 and at p99 (assume p99 adds 2× the median hop). (c) If one follower falls 90 seconds behind, state what happens to producers and to consumers, and which alarm should fire first.

**Progress checklist**

- Phase 1 — Foundations (L1–17): **complete** ✅
- Phase 2 — Scaling (L18–39): **complete** ✅
- Phase 3 — Data and Storage (L40–63): **complete** ✅
- Phase 4 — Distributed Systems (L64–92):
  - [x] 1. Why distributed systems are difficult (L64)
  - [x] 2. Partial failures and network partitions (L65)
  - [x] 3. Clock differences and ordering events (L66)
  - [x] 4. Synchronous versus asynchronous workflows (L67)
  - [x] 5. Message queues (L68)
  - [x] 6. Publish-subscribe systems (L69)
  - [x] 7. Event streams (L70)
  - [x] **8. RabbitMQ-style queues versus Kafka-style logs (L71) ← you are here**
  - [ ] 9. Consumer groups, partitions, offsets and ordering (L72)
  - [ ] 10. At-most-once, at-least-once, exactly-once (L73)
  - [ ] 11. Idempotent consumers and deduplication (L74)
  - [ ] 12. Dead-letter queues and retry queues (L75)
  - [ ] 13. Backpressure and load shedding (L76)
  - [ ] 14. Circuit breakers, bulkheads, graceful degradation (L77)
  - [ ] 15. Distributed locks and leases (L78)
  - [ ] 16. Leader election (L79)
  - [ ] 17. Quorums (L80)
  - [ ] 18. Consensus and Raft (L81)
  - [ ] 19. Distributed transactions (L82)
  - [ ] 20. Two-phase commit (L83)
  - [ ] 21. Saga pattern (L84)
  - [ ] 22. Transactional outbox and change data capture (L85)
  - [ ] 23. Monoliths, modular monoliths, microservices (L86)
  - [ ] 24. Service discovery and configuration (L87)
  - [ ] 25. Observability: logs, metrics, traces (L88)
  - [ ] 26. SLIs, SLOs and error budgets (L89)
  - [ ] 27. High availability and disaster recovery (L90)
  - [ ] 28. Active-passive versus active-active (L91)
  - [ ] 29. RTO and RPO (L92)

**NEXT LESSON: Phase 4, Topic 9 — Consumer groups, partitions, offsets and ordering**

---

# Lesson 72 — Consumer Groups, Partitions, Offsets and Ordering

*Phase 4, Topic 9*

## 1. Simple definition

Four interlocking mechanisms make a partitioned log usable by a team of independent workers. They are usually taught as one topic because each one is meaningless without the others.

- **Partition**: a topic is split into `N` independent append-only logs. A partition is simultaneously the **unit of ordering** (records within one are totally ordered) and the **unit of parallelism** (at most one consumer per group reads a given partition at a time). This dual role is the single most important fact in the lesson: **you cannot get more parallelism without giving up global ordering, and vice versa.**
- **Consumer group**: a named set of consumers that collectively read a topic, where **each partition is assigned to exactly one member** of the group. Groups are independent of each other: seven groups on one topic means seven independent readings, each with its own position. Scaling a group means adding members, which triggers a **rebalance** — the redistribution of partitions.
- **Offset**: an integer position within a partition. A consumer group **commits** the offset of the next record it intends to read, and that commit is the group's only durable state. Commit is what makes progress resumable; it is also the exactly-where-the-duplicate-lives decision (L73).
- **Ordering**: guaranteed **within a partition only**. Records for the same key land in the same partition (by hashing the key), so per-key ordering is preserved with parallelism across keys. There is no global order across partitions, and any component that assumes one is wrong.

Terms defined before use:

- **Key → partition mapping**: `partition = hash(key) % numPartitions` (Kafka uses murmur2 by default; `%` is applied to the positive hash). Same key, same partition, always — until the partition count changes.
- **Rebalance**: the group-wide protocol that reassigns partitions when membership changes, or when the partition count changes.
- **Coordinator**: a broker designated to manage one group's membership and commits (`__consumer_offsets`).
- **Generation**: a monotonically increasing ID for a group's assignment epoch. A consumer from an older generation must not commit — that is how a zombie is fenced out.
- **`max.poll.interval.ms`**: the maximum time a consumer may go without calling `poll()`. Exceed it and the consumer is **evicted** from the group; the group rebalances; the partitions are reassigned.
- **Static membership** (`group.instance.id`): a consumer identity that survives restarts, so a rolling deploy does not trigger a rebalance.
- **Lag**: `log-end-offset − committed-offset` for a partition. The group's lag is the sum. **Time lag** is how old the record at the current offset is, and it is the metric that matters (L68 §4.5).

The one sentence to carry out of this lesson: **the partition is a contract — it fixes your ordering, caps your parallelism, and cannot be changed later without breaking both, so the partition key and the partition count are the two most permanent decisions in an event-driven system.**

## 2. Why it exists

Lesson 70 established that a log gives you one copy of the data and free replay. That is only useful if many workers can read it **in parallel** without destroying the ordering guarantees the log provides. Partitions are the mechanism, and their design is a set of irreducible trade-offs.

The core tension, stated numerically. BookNook's `order-events` receives **1,000 events/sec** and one consumer group needs to keep up. A consumer instance processes **250 events/sec**, so it needs **4 consumers**. Now:

```
  ONE partition:   ordering = total and perfect
                   parallelism = 1 consumer
                   capacity = 250 events/s   →  4× TOO SLOW. Messages accumulate.
                   lag grows at 750/s → 2.7 M/hour → retention deletes unread data.

  TWELVE partitions: parallelism = up to 12 consumers
                     capacity = 3,000 events/s  →  3× headroom
                     ordering = per partition only → order per order_id preserved,
                     but "customer 4821's order" and "customer 9903's order"
                     have NO defined relative order. Anyone who assumes one
                     is now writing a bug.
```

There is no configuration that gives both. **Partitions are where the system buys parallelism with the currency of global ordering**, and the exchange rate is fixed at design time.

The second reason the topic exists is that partitions make **failure handling** a coordination problem. When a consumer dies, its partitions must be reassigned — but "died" is a timeout judgement (L65 §4.3), and during a false positive you get **two consumers believing they own the same partition**. Kafka solves this with generation numbers and coordinator-side fencing, which is the same fencing idea as L65 §8.5 applied to a group. When it works, it is invisible. When it goes wrong, you get the two most common production symptoms in event-driven systems:

- **duplicate-heavy processing that never catches up** (a consumer evicted for exceeding `max.poll.interval.ms`, rejoining and being reassigned the same work, repeatedly), and
- **a silently empty projection** (a group that lost its committed offsets and reset to `latest`).

Both are configuration bugs, both look like platform bugs, and both are in this lesson.

## 3. Real-world analogy

A **library's borrowing ledger, split into twelve volumes, with a reading team.**

The full ledger is too large for one clerk to read in a working day, so the librarian split it into twelve volumes *by a rule* — say, by the first letter of the member's surname, hashed so volumes fill evenly. Every entry for **one member** is in **one volume**, always, in the order it happened. That is the partition key, and it is why a member's history reads coherently even though the library has twelve clerks working simultaneously.

Each clerk (**consumer**) is assigned some volumes, and keeps a **bookmark** in each — that is the offset, and it is the only thing the head librarian records at the end of the day. Crucially, **two clerks are never assigned the same volume**: duplicate reading is wasted work and would produce conflicting notions of "today". That is the consumer-group rule, and the assignment is the partition-to-consumer map.

Now the four operational realities of the analogy, each of which is a production lesson:

- **A clerk goes home sick mid-volume.** Their volumes are **redistributed** at the next morning meeting (a **rebalance**). The new clerk starts from the *recorded bookmark* — which was last written at the end of the previous day. Everything the sick clerk read that morning is **read again** (at-least-once), unless the bookmark was updated per page (commit after processing, in smaller batches — the trade between commit frequency and duplicate window).
- **You hire a fourth clerk.** There are twelve volumes, so the meeting reassigns 3 each. That meeting **stops all reading** while it happens (a stop-the-world rebalance), and reading only resumes after the assignment is agreed. A rebalance is a *pause*, not a background activity.
- **You hire a thirteenth clerk.** They get **no volumes** and sit idle — a consumer with no partition assigned. Adding consumers beyond the partition count buys nothing, forever, until someone changes the volume count.
- **The librarian reshuffles volumes from 12 to 16.** Every member whose volume number changes may now be in a *different* volume, and entries written before the reshuffle are in the old volume while new ones go to the new one. **Per-member order is broken** for the transition period. This is why partition counts are increased only with a migration plan, and why "just add partitions" is not a capacity fix in an ordered system.

The managerial summary: **the volume count and the volume-splitting rule are decisions you live with for years. The clerks are disposable.** Almost every team gets this backwards — they carefully plan how many consumers to run, and pick the partition count casually.

## 4. Detailed technical explanation

### 4.1 The partition as ordering unit and parallelism unit

```
   topic: order-events   (12 partitions, replication factor 3)

   p0  [ .... O=1041 ][1042][1043]        assigned to consumer 1
   p1  [ .... O=88 ][89]                  assigned to consumer 2
   p2  [ .... O=512 ][513][514][515]      assigned to consumer 3
   p3  [ .... O=31 ]                      assigned to consumer 1
   ...                                    (3 consumers, 12 partitions → 4 each)

   ORDERING: records in p0 are in offset order, always. There is NO relation
             between p0's offset 1043 and p2's offset 515 — they are different
             logs. "Event A happened before B" is only knowable if A and B are
             in the same partition.

   KEY ROUTING: hash(order_id) % 12 → partition. All events for order 88214
             land in the same partition in commit order. That is the ONLY
             mechanism that gives per-entity ordering with parallelism.

   FENCING: each group has a generation. Consumer 1 committing with
             generation 7 while the group is at generation 8 is REJECTED by
             the coordinator. This is what prevents a paused consumer (L65 §7.2)
             from overwriting the offset and causing a rewind or a skip.
```

Two consequences that surprise people:

1. **A partition can be read by only one consumer per group** — but different **groups** read it independently and simultaneously. So "partition → 1 consumer" is per group, not global. That is how seven teams read the same log.
2. **A slow record blocks only its own partition.** A 30-second record in p2 stalls p2 and nothing else. That is why a slow key is a capacity problem for that key's share and not for the topic — provided you did not accidentally route everything to one partition.

### 4.2 The rebalance protocol, and why it hurts

```
  1. consumer 4 starts, sends JoinGroup to the GROUP COORDINATOR (a broker)
  2. coordinator bumps the GENERATION (7 → 8) and collects all members
  3. ONE member is chosen as leader; it computes the assignment from each
     member's metadata (subscription, static id) and sends SyncGroup
  4. coordinator distributes the assignment to all members
  5. every consumer commits its positions for the partitions it is LOSING,
     revokes them, and starts fetching those it GAINED

  During 2–4, NO CONSUMER FETCHES. That is the pause. Duration depends on:
     - assignment strategy (eager = full revoke; cooperative = incremental)
     - member count (each join is a round trip, and the group must agree)
     - session/heartbeat timeouts (a crashed member is only noticed on timeout)
```

| Assignment strategy | Behaviour | Cost |
|---|---|---|
| **Range** | Contiguous ranges per topic | Simple; unbalanced across topics |
| **Round-robin** | Distributes partitions evenly across members | Revokes everything on every rebalance |
| **Sticky** | Minimises movement when membership changes | Eager: still revokes everything first |
| **Cooperative sticky** (recommended) | Revokes only what must move; members keep the rest | 2 rebalance rounds, but **near-zero pause** |

Three triggers that cause most rebalances in practice, ranked by how often they cause incidents:

1. **`max.poll.interval.ms` exceeded.** The consumer is still alive and heartbeating (a background thread does that) but has not called `poll()` in time, because the batch it is processing is too slow. The coordinator **evicts** it. If the work is still slow, this repeats forever: a **rebalance loop** (§7.2).
2. **A rolling deploy.** Every pod restart is a leave-and-rejoin. With 4 pods that is 8 membership changes; the duration-adaptive session timeout mitigates but does not remove it. Fixed by **static membership**.
3. **A long GC pause or network blip** exceeding `session.timeout.ms`. The consumer thinks it is fine; the group thinks it left (L65 §7.2 again, at group scale).

### 4.3 Offsets: the three commit policies and what each guarantees

```
   record 100   101   102   103   104   105
                    ▲           ▲
                    │           └── commit AFTER processing 104  → "next = 105"
                    └── commit BEFORE processing 102 → "next = 103"
                        (a crash now SKIPS 102, 103, 104)

   COMMIT AFTER PROCESSING (at-least-once):
       crash between processing and commit  → redelivery → DUPLICATE
       → correct default, requires idempotency (L74)

   COMMIT BEFORE PROCESSING (at-most-once):
       crash after commit, before processing → record LOST, silently
       → acceptable only for telemetry that may be dropped

   AUTO-COMMIT (enable.auto.commit=true, every 5 s by default):
       commits whatever the poll loop has DELIVERED, not what was PROCESSED
       → crash after auto-commit but before processing = LOST RECORDS
       → the most common silent-loss bug in Kafka applications.
       → never enable for work that matters.
```

Two long-tail facts that cause real incidents:

- **Offsets expire.** `__consumer_offsets` retains a group's commits for `offsets.retention.minutes` (default **7 days**). A group idle for longer **loses its position**, and on its next start falls back to `auto.offset.reset` — `latest` being a silent data skip and `earliest` being a full replay of the retained window.
- **Offset commit is the *only* state.** There is no per-message ack, so "which message is in flight" is not persisted anywhere except in the consumer's memory. A restart resumes at the last commit and re-reads everything after it. This is why commit frequency is a direct trade between duplicate volume (frequent commits = fewer duplicates) and commit overhead (each commit is a broker round trip; commit per record at 1,000/s is a self-inflicted load).

### 4.4 Ordering, precisely, and the three ways people break it

**The guarantee:** within a partition, order is total. Consequently, **for a given key**, order is total, because the key always hashes to the same partition.

**Break 1 — assuming cross-partition order.** Two events with different keys have no defined order, even if their timestamps suggest one (L66). Any code that does "read all events, sort by `occurred_at`, apply" is working by luck, because clock skew reorders the input (L66 §4.4).

**Break 2 — changing the partition count.** In Kafka, partitions can be **increased but never decreased**, and increasing them changes `hash(key) % N` for most keys. During the transition, a key's old events sit in partition `hash % 12` and its new events go to `hash % 16`. **Per-key ordering is broken for exactly the transition window**, and no amount of consumer discipline fixes it. Mitigations: an explicit "drain and switch" (stop producing, let consumers reach the end, then switch), a routing layer that maps keys onto **logical partitions** (e.g. 4,096) which are in turn mapped to physical ones (L37's logical-shard trick), or accept the window and design consumers to be order-insensitive.

**Break 3 — parallelising inside a consumer.** A consumer that reads a batch and hands records to a 64-thread pool processes them out of order, regardless of partition assignment. Per-key ordering then requires key-striped workers (hash the key to one of K queues) or a per-key lock. This is a very common "optimisation" that silently violates the guarantee.

### 4.5 Lag: the only metric that matters, measured correctly

```
  RECORD LAG  = log_end_offset − committed_offset        (per partition)
  GROUP LAG   = Σ record lag across partitions           (a number that
                                                          hides its own shape)
  TIME LAG   = now − append_time(log_end_offset − lag)
             = "how old is the next record I will read?"
```

Three reasons time lag is the metric:

1. **Record lag is meaningless without a rate.** 100,000 records is 100 seconds at 1,000/s and 27 hours at 1/s.
2. **A single stalled partition is invisible in the sum.** A group with 12 partitions, 11 at lag 0 and one at lag 900,000 has a "group lag" of 900,000 but a dashboard that averages will show a healthy trend line.
3. **Retention races lag, not volume.** The failure to alarm on is `time_lag` approaching retention: at that point the consumer is about to have unread data **deleted underneath it** (§11.5).

Draining arithmetic, which is what interviews actually test:

```
  drain_time ≈ lag / (consumer_capacity − arrival_rate)

  lag = 500,000, capacity = 1,200/s, arrivals = 1,000/s
      → 500,000 / 200 = 2,500 s = 41.7 min
  NOTE: if capacity ≤ arrivals, drain_time = ∞. Adding consumers is the only fix,
  and consumers cannot exceed the partition count.
```

That last line is why partition count is a capacity decision: **if you have 12 partitions and each consumer handles 250/s, your maximum group capacity is 3,000/s forever.** When traffic exceeds it, no amount of autoscaling helps — the topic must be repartitioned, which breaks ordering (§4.4).

### 4.6 Choosing the partition count, which is a permanent decision

| Input | Effect on partition count |
|---|---|
| **Consumer throughput** | `partitions ≥ peak_arrival ÷ per_consumer_capacity` — this is the hard floor |
| **Desired consumer parallelism** | Consumers beyond the partition count are idle |
| **Produce/consume throughput per partition** | ~10 MB/s write, ~30 MB/s read per partition in practice |
| **Rebalance cost** | Rises with both partition and member count; 1,000 partitions is painful |
| **End-to-end latency** | More partitions = more concurrent open files/batches; slightly higher p99 on small batches |
| **Broker metadata & controller work** | Partitions × replicas is the real unit of cluster state (12 × 3 = 36) |
| **Future growth** | Add 2–3× headroom now, because increasing later breaks key ordering |

The rule of thumb worth stating: **size for the consumer parallelism you need at 3× current peak, and no more.** BookNook: 1,000/s peak, 250/s per consumer → 4 consumers → 12 partitions gives 3× headroom and a 3,000/s ceiling for the group.

## 5. Request / data flow

A group of three consumers over 12 partitions, then a scale-out, then a crash. This is the sequence to be able to narrate.

```
 T+0      Group "warehouse-loader" at generation 41
          c1: p0,p3,p6,p9   c2: p1,p4,p7,p10   c3: p2,p5,p8,p11
          each has committed offsets; lag ≈ 0; fetching continuously.

 T+10s    c4 starts (autoscaler). Sends JoinGroup.
          coordinator: generation 41 → 42; reading PAUSES for all four.
          leader (c1) computes a cooperative assignment:
             c1: p0,p3,p6      (gives up p9)
             c2: p1,p4,p7
             c3: p2,p5,p8
             c4: p9,p10,p11    ← hmm: p10,p11 were c2/p3; see the revoke below
          with COOPERATIVE: only p9,p10,p11 are revoked; the rest never stop.

 T+10.4s  c1,c2,c3 commit offsets for the partitions they are losing, then
          release them. c4 is assigned p9,p10,p11 and resumes from the
          COMMITTED offsets — no loss, and duplicates only for records that
          were fetched-but-not-committed at the moment of revocation.

 T+11s    Steady state at generation 42. Total pause: ~400 ms (cooperative),
          or ~4-8 s had the strategy been eager (full stop-the-world).

 T+40s    c4 is OOM-killed. Its heartbeat stops.
 T+85s    session.timeout.ms (45 s) expires → coordinator evicts c4, bumps to
          generation 43, reassigns p9,p10,p11 to c1,c2,c3.
 T+85s    c1..c3 resume those partitions from the last committed offsets —
          REPLAYING everything c4 processed but had not committed.
          ⚠ every record in that window is processed twice → §7.2

 ── WHY THIS IS SURVIVABLE ──────────────────────────────────────────────────
   The duplicates are bounded by "uncommitted work at crash time", which you
   control with commit frequency. The alternative (commit before processing)
   has unbounded silent loss instead. At-least-once + idempotency is the only
   correct combination, and this timeline is exactly why.
```

The detail worth noticing: **the commit frequency sets the duplicate window.** Committing every 500 records at 250/s means up to 2 seconds of work is replayed on crash — 500 duplicate records. Committing every record removes the duplicates and adds 250 commits/sec of broker round trips to a group that only processes 250 records/sec.

## 6. ASCII diagram

Partitions, groups and offsets in one picture:

```
   TOPIC order-events (12 partitions)
   ┌──────────────────────────────────────────────────────────────────────┐
   │ p0  [====|====|====|===>                    lag=0                    │
   │ p1  [====|====|====|====|====|===>          lag=0                    │
   │ p2  [====|====|====|=============>          lag=40k  ← STALLED       │
   │ ...                                                                  │
   └──────────────────────────────────────────────────────────────────────┘
        ▲                    ▲                          ▲
        │ group A (4 cons)   │ group B (2 cons)         │ group C (1 cons)
        │ offset per group   │ offset per group         │ replaying from 0
        └─ independent ──────┴──────────────────────────┘

   GROUP LAG = Σ(partition lag) = 40k   ← but the MEANING is one stalled
   partition, and the number alone cannot tell you that. Alert per partition.

   CONSUMERS vs PARTITIONS:
      3 consumers / 12 partitions  → 4 each, all busy          ✓
     12 consumers / 12 partitions  → 1 each, max parallelism   ✓
     13 consumers / 12 partitions  → 1 IDLE forever            ✗ wasted money
```

Offset commit positions, and the window each creates:

```
   records:      100    101    102    103    104    105    106
                                   │                 │
   commit AFTER 104 ───────────────┼─────────────────┘  next=105
     crash at 105 → replay 105 only (0 duplicates)
     crash BEFORE the commit → replay 102..105 (4 duplicates)

   auto-commit (every 5s, commits DELIVERED not PROCESSED):
     delivered 100..600, processed 100..380, auto-commit fires
     → commit says next=601
     → crash → 380..600 are LOST, silently, forever
     ⇒ enable.auto.commit=true is a data-loss setting wearing a convenience
       label. For any work that matters: false, and commit manually.
```

The `max.poll.interval.ms` loop, which is the most common production failure in this lesson:

```
   max.poll.interval.ms = 300 s   max.poll.records = 500   per-record = 700 ms
   ─────────────────────────────────────────────────────────────────────────
   poll() returns 500 records → processing takes 500 × 0.7 = 350 s
                                        ▲
                                        └── 350 s > 300 s → EVICTED mid-batch
   coordinator rebalances → partition reassigned → the SAME batch is fetched
   again → 350 s again → EVICTED again …
   ─────────────────────────────────────────────────────────────────────────
   RESULT: no progress, lag rising, continuous duplicates, health checks green.
   FIX:    max.poll.records × per-record-time < max.poll.interval.ms × 0.8
           400 × 0.7 = 280 s < 240 s?  NO → 280 > 240, still too big.
           ⇒ max.poll.records = 300  (300 × 0.7 = 210 s < 240 ✓)
           or decouple: pause(), process in a pool, resume(), commit, poll.
```

## 7. Detailed example

### 7.1 Scaling from 3 to 12 consumers, and why the 13th is useless

BookNook's `order-events` has 12 partitions and the `warehouse-loader` group ran 3 consumers (4 partitions each). Peak throughput was 1,000 events/sec and each consumer handled ~250/sec, so 3 consumers at 750/sec capacity were already **behind** at peak — lag accumulated at 250/s for the duration of every sale.

The fix was autoscaling to 12, which is the parallelism ceiling:

```
   3 consumers → 750/s capacity  → at 1,000/s arrivals, lag +250/s
  12 consumers → 3,000/s capacity → at 1,000/s arrivals, lag drains at 2,000/s
```

The autoscaler was configured on **group time-lag** with `maxReplicas: 12`, and that cap matters: a 13th consumer would receive **no partitions** and sit idle while still costing money and adding a rebalance pause for everyone. Teams that autoscale consumers without a partition-count cap routinely run 40 consumers against 12 partitions and cannot explain why throughput never improved.

The permanent consequence: **the group's capacity is now capped at 3,000/s**, because 12 × 250 = 3,000. To go beyond it, the partition count must increase — which changes `hash(order_id) % N` and breaks per-order ordering during the transition (§4.4). BookNook's mitigation was to size for 3× peak at design time and to keep a documented "repartition runbook" rather than discovering the need at 2 a.m.

### 7.2 The rebalance loop that produced 40 % duplicates

The `analytics` group processed records in batches of 500, each needing **~700 ms** (an aggregation query per record). With the default `max.poll.interval.ms = 300 s`:

```
   500 × 0.7 s = 350 s > 300 s
```

Every batch exceeded the interval. The consumer was evicted mid-batch, the group rebalanced, the partition was reassigned, the new consumer re-fetched the same batch (uncommitted), exceeded the interval again, and so on. Symptoms, in the order they appeared:

1. `lag` rising monotonically while consumer CPU sat at ~20 % — the "healthy but not progressing" signature.
2. Rebalance count in the thousands per hour.
3. Duplicate processing: every batch was processed at least once fully and partially a second time → **~40 % duplicate work**, invisible without a dedup counter.
4. Nobody's health check failed, because the process was alive and heartbeating the whole time.

The fix, in two parts:

- **Bound the batch by time, not by count**: `max.poll.records = 300` (300 × 0.7 = 210 s, leaving 90 s of headroom under 300 s).
- **Decouple processing from polling** for the genuinely long records: `pause()` the partitions, hand the batch to a worker pool, `resume()` when it drains, and commit only after completion. This keeps `poll()` frequent (so the consumer is never evicted) while allowing arbitrarily long work — at the cost of managing offsets manually and accepting a bounded number of in-flight records.

The transferable insight: **`max.poll.interval.ms` is a liveness signal, and any processing that can exceed it needs either a smaller batch or an explicit pause/resume design.** Leaving it at the default and hoping is the single most common Kafka application bug.

### 7.3 The group that forgot where it was

A `recommendations` group was paused for a two-week experiment. On restart:

```
  __consumer_offsets retained the group's commits for 7 days (default
  offsets.retention.minutes = 10080), then DISCARDED them.
  Group starts with no committed offset → auto.offset.reset = "latest"
  → it skips the entire 7-day retained window and starts at the end.
```

The dashboard showed the feature "working" from that moment on, and a 7-day hole in the user-visible recommendations that nobody noticed for a month. Neither the producer nor the broker reported anything: **the offsets had expired, which is a documented, silent, default behaviour.**

The controls added: `auto.offset.reset=earliest` for any group whose job is to *process history* (a fresh group replaying is correct), `latest` only for live-only consumers with an explicit comment, an alarm when a group's committed offset jumps by more than its retention window, and a documented list of which groups are allowed to be idle.

## 8. Code or configuration example

```python
# ============================================================================
# BookNook: a Kafka consumer configured for correctness under failure.
# Each setting is a decision; the comments say which.
# ============================================================================
from kafka import KafkaConsumer
import json

consumer = KafkaConsumer(
    "order-events",
    bootstrap_servers=SEEDS,

    # --- identity: survive rolling deploys without a rebalance -------------
    group_id="warehouse-loader",
    group_instance_id="warehouse-pod-7",     # STATIC membership: a pod restart
                                             #   within session.timeout does NOT
                                             #   trigger a rebalance. Big win on
                                             #   Kubernetes rolling deploys.

    # --- commit policy: at-least-once, deliberately ------------------------
    enable_auto_commit=False,                # auto-commit commits DELIVERED,
                                             #   not PROCESSED → silent loss
    auto_offset_reset="earliest",            # a NEW group replays the retained
                                             #   window. 'latest' silently skips
                                             #   history (§7.3).

    # --- liveness: keep the consumer in the group --------------------------
    max_poll_records=300,                    # 300 × 700ms = 210s < 300s × 0.8
    max_poll_interval_ms=300_000,            # eviction if a poll is slower than
                                             #   this → rebalance loop (§6)
    session_timeout_ms=45_000,               # detection time for a dead consumer
    heartbeat_interval_ms=3_000,             # must be < session_timeout / 3

    # --- assignment: near-zero pause on membership change ------------------
    partition_assignment_strategy=[CooperativeStickyAssignor],

    # --- fetch sizing: bound memory, keep the pipeline full ----------------
    fetch_min_bytes=1,                       # don't wait for a full batch
    max_partition_fetch_bytes=1_048_576,
    request_timeout_ms=30_000,
)

# --- commit on revoke: the ONE hook that prevents duplicate storms ---------
class CommitOnRevoke(ConsumerRebalanceListener):
    def on_partitions_revoked(self, revoked):
        # Commit BEFORE giving up partitions, or the successor replays
        # everything since your last commit. This single method is the
        # difference between a 2-second and a 20-minute duplicate window.
        consumer.commit(offsets=current_positions(revoked))

    def on_partitions_assigned(self, assigned):
        metrics.gauge("partitions_assigned", len(assigned))
        # A consumer with 0 partitions is idle — alert, do not scale further.

for batch in poll_batches(consumer):
    try:
        process(batch)                       # idempotent, per L74
        consumer.commit()                    # AFTER the side effect
    except Transient:
        # Do NOT commit; rewind to the batch start and retry, with a ceiling.
        consumer.seek_to_committed()
        time.sleep(backoff_with_jitter())
        # If you cannot afford to block the partition, park the failed records
        # in a retry topic and commit — the log has no DLX (L71 §4.2).
```

```bash
# --- Operating the group: the four commands you actually use ---------------
kafka-consumer-groups --describe --group warehouse-loader
#   TOPIC  PARTITION  CURRENT-OFFSET  LOG-END-OFFSET  LAG   CONSUMER-ID
#   (read LAG PER PARTITION — a single stalled partition is invisible in the sum)

kafka-consumer-groups --describe --group warehouse-loader --state
#   group state: Stable | PreparingRebalance | CompletingRebalance | Empty | Dead
#   'PreparingRebalance' as a steady state = a rebalance loop (§7.2)

# Reset a group's position — an incident tool, not a routine one.
# ALWAYS stop the consumers first, or you will race live commits.
kafka-consumer-groups --reset-offsets --group warehouse-loader \
                      --topic order-events --to-datetime 2026-10-29T00:00:00.000 \
                      --execute
```

```yaml
# --- Autoscaling on TIME LAG, capped at the partition count ---------------
autoscaling:
  metric: max(kafka_consumergroup_group_timestamp_lag_seconds{group="warehouse-loader"})
  target: 60                       # drain within a minute
  min: 3
  max: 12                          # == partition count. Beyond this: idle pods.
  scaleUpCooldown: 60s             # faster than scale-down: react to bursts
  scaleDownCooldown: 600s          # slow: avoid rebalance churn
alerts:
  - lag_seconds > retention_seconds * 0.5     # approaching data loss (§11.5)
  - rebalance_rate > 2/hour                    # rebalance loop
  - partitions_assigned == 0 for 10m           # idle consumer = wasted money
  - sum(lag) > 0 and rate(processed) == 0      # dead but heartbeating
```

## 9. Numerical example

BookNook `order-events`: **12 partitions**, 3 replicas, 1,000 events/sec peak, 250 events/sec per consumer instance, 7-day retention.

**Step 1 — the parallelism ceiling.**

```
  group capacity = partitions × per-consumer throughput
                 = 12 × 250 = 3,000 events/s

  peak arrivals  = 1,000/s  → headroom 3× (the design target)
  consumers needed at peak = 1,000 / 250 = 4
  consumers needed to drain a 30-minute backlog quickly:
       lag after 30 min at 1,000/s with 3 consumers (750/s) = 450,000
      drain at 12 consumers (3,000/s − 1,000/s arrivals = 2,000/s net)
          = 450,000 / 2,000 = 225 s = 3.75 minutes ✓
```

**Step 2 — what a rebalance costs, in duplicates and in pause.**

```
  per-consumer in-flight at revocation = max_poll_records = 300
  duplicates per rebalance, worst case  = 300 records per revoked partition set
      3-consumer → 4-consumer change: c1..c3 each lose 1 partition
      duplicates ≈ 3 × 300 = 900 records
  at 1,000 events/s that is 0.9 s of re-processing. Negligible.

  PAUSE, eager strategy (stop-the-world):
      join (2 round trips) + sync (1) + fetch group metadata ≈ 3-8 s
      during a 4-pod rolling deploy: 8 membership changes
      → up to 64 s of cumulative paused consumption
      → at 1,000/s that is 64,000 records of extra lag, PER DEPLOY

  PAUSE, cooperative + static membership:
      static id survives the restart within session.timeout (45 s)
      → pods restarting one at a time cause ZERO rebalances
      → rolling deploy cost ≈ 0 lag, and the duplicate window ≈ 0
  ⇒ the config change is worth ~64,000 records of lag per deploy.
```

**Step 3 — the `max.poll.interval.ms` budget, solved properly.**

```
  constraint: max_poll_records × per_record_time < max_poll_interval × 0.8
  per_record_time = 700 ms, max_poll_interval = 300,000 ms
  ⇒ max_poll_records < (300,000 × 0.8) / 700 = 342
  ⇒ choose 300 (210 s of processing, 90 s of headroom for a slow batch)

  With the default 500: 500 × 0.7 = 350 s > 300 s → permanent rebalance loop.
  Throughput impact of the smaller batch: none at 250/s, because 300 records
  at 0.7 s each is 210 s for 300 records = 1.43 records/s ??? — see Step 4.
```

**Step 4 — the arithmetic that reveals a design problem.** The above is worth doing carefully, because it is the trap:

```
  ONE consumer processing 700 ms/record sequentially = 1.43 records/s
  3 consumers → 4.3 records/s → 12 consumers → 17 records/s

  But the group must handle 1,000 records/s.
  ⇒ sequential processing per consumer is 580× too slow. max.poll.records
    tuning does not fix this; it only stops the rebalance loop.

  THE REAL FIX is concurrency inside the consumer:
      in-flight needed = arrival × processing = 1,000 × 0.7 = 700 concurrent
      with 12 consumers → 58 concurrent records each
  ⇒ pause() the partition, hand batches to a 64-thread pool, commit only after
    the whole batch completes, resume(). Per-key ordering then requires
    key-striped dispatch (§4.4 break 3) — and if the aggregation needs strict
    per-order order, key-stripe by order_id so each order's records are serial.
```

This is the most valuable calculation in the lesson: **partition count and `max.poll` tuning only buy you the *opportunity* to parallelise; the consumer's internal concurrency decides whether you can actually use it.** BookNook's analytics consumer went from 17 records/s to 1,100 records/s by adding in-consumer concurrency, with no change to partitions at all.

**Step 5 — lag versus retention, the data-loss race.**

```
  retention          = 7 days = 604,800 s
  alert threshold    = 50 % of retention = 302,400 s = 3.5 days of lag
  at 1,000 events/s, 3.5 days of lag = 302,400 × 1,000 = 302 M records
  ⇒ record-count alerting would fire at a number nobody can interpret.
    TIME lag ("the next record I will read is 3.5 days old") is directly
    actionable, and it is the only alarm that prevents §11.5.
```

**Step 6 — the cost of over-partitioning.** A topic at 40 msg/sec with 1,000 partitions:

```
  partition replicas = 1,000 × 3 = 3,000 per cluster
  each carries: leader/follower state, an open file set, controller metadata,
    a rebalance slot, and per-partition metric series (~15 series each)
  → ~45,000 metric series for ONE topic; most monitoring vendors charge per
    series, so this can cost more than the infrastructure.
  rebalance time scales with partition count: 1,000 partitions across 4
    consumers can take 30-60 s of paused consumption per rebalance.
  At 40/s this topic needed 1 partition.
```

## 10. Trade-offs

| Decision | Buys you | Costs you | Choose it when |
|---|---|---|---|
| **More partitions** | More consumer parallelism; more throughput | Rebalance cost, metadata, latency; permanent and can break key ordering | Consumer lag is capped by parallelism, with 2–3× headroom |
| **Fewer partitions** | Fast rebalances, cheap metadata, simple ops | Hard ceiling on group capacity | Volume is low and the ceiling is comfortably above peak |
| **Key = entity id** | Per-entity ordering with parallelism across entities | Hot keys serialise (one large customer stalls its partition) | Any workflow where per-entity order matters |
| **No key (round-robin)** | Perfectly even distribution | **No ordering at all** | Independent, order-insensitive events |
| **Eager rebalance** | Simple, predictable | Full stop-the-world pause on every membership change | Tiny groups, non-latency-sensitive consumers |
| **Cooperative + static membership** | ~0 pause on deploys; minimal duplicate window | Needs broker/client support; slightly more complex reasoning | Any production group (this should be the default) |
| **Commit after processing** | No data loss | Duplicates on crash → idempotency required | **Always**, for work that matters |
| **Auto-commit** | Zero code | Silent loss of delivered-but-unprocessed records | Telemetry you can lose (and even then, be explicit) |
| **Large `max.poll.records`** | Efficient per-record overhead | Risks the rebalance loop; larger duplicate window | Processing is fast and predictable |
| **Small `max.poll.records`** | Safe against `max.poll.interval` | More polls, more overhead, smaller commits | Slow per-record processing |
| **Pause/resume + worker pool** | Both liveness and throughput | Manual offset management; per-key ordering needs striping | Processing per record exceeds a few hundred ms |
| **Reset offsets** | Recovery from a bad deploy or a projection rebuild | Careful, outage-adjacent operation | Rebuilds and incidents only |

## 11. Failure scenarios

**11.1 — The rebalance loop** (§7.2). Processing exceeds `max.poll.interval.ms`; consumers are evicted and reassigned forever. **Symptom:** rising lag with low CPU, huge rebalance counts, `PreparingRebalance` as steady state. **Fix:** bound `max.poll.records` by the interval, or decouple processing with pause/resume.

**11.2 — Auto-commit silent loss.** `enable_auto_commit=true` with a 5-second interval; the consumer crashes after a commit but before processing 4 seconds of records. Those records are never read again. **Symptom:** sporadic, small, unexplainable gaps in the output — the hardest kind of bug to notice. **Fix:** manual commit after the side effect, plus idempotency.

**11.3 — Offsets expired for an idle group** (§7.3). A group idle beyond `offsets.retention.minutes` (7 days) loses its position and resets per `auto.offset.reset`. **Symptom:** a jump in position, or a full-window replay, days after a "completed" experiment. **Fix:** `earliest` for history-processing groups, an explicit list of groups allowed to idle, and an alarm on large offset discontinuities.

**11.4 — A hot partition.** One key (a corporate account placing 40 % of orders) hashes to one partition; that partition's consumer is pinned at 100 % while eleven others idle. **Symptom:** group lag concentrated in one partition, but a healthy-looking average. **Fix:** per-partition lag dashboards; key salting (`key#1..key#4`) with a merge at the consumer if the ordering can be relaxed; or accept it and dedicate capacity.

**11.5 — Retention deleting unread data.** A consumer 9 days behind with 7-day retention. Unread segments are deleted regardless of consumer position, and the group's committed offset becomes invalid. **Symptom:** a gap that never resolves, discovered weeks later. **Fix:** alarm on time lag approaching retention (50 %), pause producers or extend retention during a long consumer outage, and treat "lag × retention" as a first-class risk.

**11.6 — Partition increase breaking ordering.** A team added partitions (`12 → 24`) to fix a capacity ceiling; every consumer that assumed per-`order_id` order started seeing out-of-order events for the transition window. **Symptom:** sporadic constraint violations or wrong aggregates for specific orders around the change time. **Fix:** drain-and-switch (pause producers, let consumers reach the log end, then repartition), or logical-partition indirection designed in from the start.

**11.7 — Consumer parallelising internally.** A consumer handed batches to a 32-thread pool for speed, breaking per-key ordering. **Symptom:** occasional out-of-order application that "cannot be reproduced". **Fix:** key-striped workers (hash the key to one of K serial queues), or drop the ordering claim explicitly.

**11.8 — Idle consumers with a partition cap.** Autoscaling to 40 replicas against 12 partitions: 28 pods idle, still consuming memory and still participating in every rebalance. **Symptom:** throughput flat while replica count triples, and deploys that pause longer with each replica. **Fix:** `maxReplicas = partition_count`, and an alarm on `partitions_assigned == 0`.

**11.9 — The static-membership zombie.** With `group.instance.id` set, a consumer that is paused beyond `session.timeout.ms` is not immediately replaced; when it resumes it may process with a stale assignment. **Symptom:** duplicates or an offset regression in a narrow window. **Fix:** static membership raises the duplicate window in exchange for fewer rebalances; commit on revoke, and never let a static member hold a lease beyond the session timeout without a fencing check.

## 12. Alternatives

| Instead of | Consider | When |
|---|---|---|
| Many partitions | **More consumers is capped; scale consumer concurrency instead** (§9 step 4) | The bottleneck is per-record processing, not partition parallelism |
| Partitioned log | **A single-partition topic** | Volume is low and total ordering is genuinely required |
| Per-key ordering via partitions | **Order-insensitive consumers / CRDT merges** (L66) | The business does not actually need order |
| Autoscaling consumers | **Scale consumer concurrency + queue depth** | Partition count caps replicas |
| Kafka consumer groups | **A queue broker** (L68) | Per-message retry and DLQ matter more than replay |
| Manual offset management | **Kafka transactions** (L73) | You need atomic consume-process-produce |
| Repartitioning | **Logical partitions → physical** (L37) | You expect to grow and cannot break ordering |

## 13. When to use it

- **Whenever a log has more than one consumer instance.** Consumer groups are the only way to parallelise a partition set.
- **Whenever per-entity ordering matters at volume.** Key by the entity, size partitions for the parallelism you need.
- **Whenever you need a resumable position** — any group that must survive restarts, deploys, or outages without loss.
- **Whenever you need replay** (L70): a new group with `earliest` is the entire mechanism.
- **When designing capacity**, because `partitions × per-consumer throughput` is a hard ceiling that no autoscaler can exceed.

## 14. When *not* to use it

- **For a single consumer that never restarts.** One partition, no group, no offsets to worry about.
- **When the workload is a bag of tasks with per-message retry.** Use a queue broker (L68, L71) — you would be rebuilding DLX and retry topics by hand.
- **When ordering can be genuinely avoided.** If events are commutative or idempotent and order-insensitive, do not pay the hot-key price for a guarantee you do not need.
- **When you cannot commit to a partition count.** If the entity space will grow unpredictably and ordering must hold, use logical partitioning instead.
- **For request/response.** A consumer group is not an RPC mechanism; see L67.

## 15. Common mistakes

1. **Leaving `enable.auto.commit=true`** on work that matters — silent loss (§11.2).
2. **`max.poll.records` too large for the processing time**, causing a permanent rebalance loop (§7.2).
3. **`auto.offset.reset=latest`** on a group that needed history, or on a group that lost its offsets (§11.3).
4. **Assuming ordering across partitions.** Different keys have no defined order (L66).
5. **No key at all**, then expecting per-entity order — round-robin destroys it.
6. **Hot keys** with no per-partition lag visibility, so a single stalled partition hides in an average.
7. **Autoscaling consumers past the partition count.** Idle pods and slower deploys (§11.8).
8. **Increasing partitions to fix capacity** without a drain-and-switch plan — breaks key ordering (§11.6).
9. **Eager assignment and no static membership**, so every deploy is a multi-second pause plus duplicates.
10. **Not committing on revoke**, extending the duplicate window to the whole batch.
11. **Parallelising inside the consumer**, silently breaking per-key order (§11.7).
12. **Alerting on total lag in records** instead of time lag per partition.
13. **Never testing the failure path**: kill a consumer mid-batch in staging and check that duplicates are deduped and no records are lost.
14. **Treating an offset reset as a routine operation.** It races live commits and is an incident tool.

## 16. Interview perspective

**What is actually being tested.** Whether you understand that partition count is **both** the ordering unit and the parallelism cap, and that this makes it a permanent architectural commitment. Then whether you can reason about the rebalance protocol, commit semantics, and lag as an operational metric. Weak candidates know "Kafka has partitions" and "consumers have groups"; strong candidates can predict the failure mode given a config.

**Answers that land well:**

- *"How do you guarantee ordering and still scale consumers?"* — "Order per key, not globally: partition by the entity ID so all of an entity's events land in one partition, then scale consumers up to the partition count. The ceiling is partitions × per-consumer throughput, so I'd size partitions at 3× peak parallelism up front, because increasing them later changes the key-to-partition mapping and breaks ordering for the transition window."
- *"A consumer group's lag is rising but CPU is low. Diagnose."* — "That is either a rebalance loop — processing exceeding `max.poll.interval.ms`, so consumers are evicted and reassigned forever — or the consumer is stalled on a downstream dependency while still heartbeating. I'd look at rebalance rate and per-partition lag first, then at the processing time per record against the poll interval. The fix is usually bounding `max.poll.records` or moving long work out of the poll loop with pause/resume."
- *"What happens to in-flight messages when a consumer dies?"* — "Nothing is persisted per message — only the committed offset is. So everything after the last commit is re-read by the new owner: bounded duplicates, no loss. That is why at-least-once plus idempotency is the contract, and why commit frequency is a dial between duplicate volume and commit overhead."
- *"How do you add consumers without hurting anyone?"* — "Static membership so a rolling restart does not trigger a rebalance, cooperative-sticky assignment so only the partitions that must move are revoked, commit-on-revoke so the successor does not replay the batch, and a `maxReplicas` cap at the partition count so I never scale into idle pods."

**Common interview tasks:** choose a partition count and key for a described workload; explain what a rebalance does and when it fires; choose a commit policy and defend it; compute group capacity and drain time; diagnose a lagging group; explain what breaks if you increase partitions; design per-key ordering with in-consumer concurrency.

**Red flags interviewers watch for:**

- "Ordering is guaranteed by Kafka" without "per partition".
- No awareness that partition count caps consumer parallelism.
- Auto-commit with no mention of the loss window.
- Being unable to explain what a rebalance is, or that it pauses consumption.
- Choosing a partition count "because 10 sounds good", with no throughput arithmetic.
- Alerting on total lag in records.
- No mention of idempotency when discussing at-least-once.
- Thinking offsets are per topic rather than per group per partition.
- No hot-key awareness.

## 17. Knowledge check

**Conceptual**

1. Explain why a partition is simultaneously the unit of ordering and the unit of parallelism, and derive the capacity ceiling of a consumer group from that. Then explain why increasing the partition count is not a safe capacity fix for an ordered stream, and give two designs that avoid the problem.
2. Describe the rebalance protocol from a consumer joining to the first fetch, and name three distinct costs it imposes. Then explain what static membership and cooperative assignment each remove, and what remains.
3. Compare commit-before-processing, commit-after-processing, and auto-commit in terms of what a crash mid-batch does to the data. For each, state the failure it produces, whether it is loud or silent, and the correctness requirement it places on the consumer.

**Practical scenario**

4. BookNook's fraud-scoring group reads `order-events` (12 partitions, 1,000 events/sec peak, 60/sec baseline). Fraud scoring takes 180 ms per event, needs the last 30 days re-processed whenever the model is retrained, and must produce alerts within 5 seconds of the order. Requirements: no lost events, alerts within the latency target, replay capability, and the group must survive deploys without a lag spike. Design it: partition count and key, consumer count, commit policy, `max.poll` and concurrency settings with the arithmetic, static membership and assignment strategy, how the 30-day replay is executed without disturbing the live path, the lag and health alarms, and what happens when the group falls 2 days behind.

**Calculation**

5. A group has 16 partitions, 4 consumer instances, each consumer processing 200 records/sec with a `max_poll.records` of 500. (a) Compute group capacity and the consumers needed for a 2,500/sec peak. (b) Compute the worst-case duplicates produced by one consumer crashing, in records and in seconds. (c) A backlog of 1.2 M records exists at 2,500/sec arrival; compute the drain time at 4 consumers and at 16. (d) State the maximum sustainable arrival rate of this group, and what you would change to raise it.

6. A consumer processes 500 records per poll at 900 ms per record, with `max.poll.interval.ms = 300000` and `session.timeout.ms = 45000`. (a) Show whether the consumer will be evicted, with working. (b) Compute the largest `max.poll.records` that satisfies a 20 % safety margin. (c) Compute the required in-consumer concurrency to sustain 800 records/sec if each record takes 900 ms, and explain why raising the partition count alone would not achieve it.

**Progress checklist**

- Phase 1 — Foundations (L1–17): **complete** ✅
- Phase 2 — Scaling (L18–39): **complete** ✅
- Phase 3 — Data and Storage (L40–63): **complete** ✅
- Phase 4 — Distributed Systems (L64–92):
  - [x] 1. Why distributed systems are difficult (L64)
  - [x] 2. Partial failures and network partitions (L65)
  - [x] 3. Clock differences and ordering events (L66)
  - [x] 4. Synchronous versus asynchronous workflows (L67)
  - [x] 5. Message queues (L68)
  - [x] 6. Publish-subscribe systems (L69)
  - [x] 7. Event streams (L70)
  - [x] 8. RabbitMQ-style queues versus Kafka-style logs (L71)
  - [x] **9. Consumer groups, partitions, offsets and ordering (L72) ← you are here**
  - [ ] 10. At-most-once, at-least-once, exactly-once (L73)
  - [ ] 11. Idempotent consumers and deduplication (L74)
  - [ ] 12. Dead-letter queues and retry queues (L75)
  - [ ] 13. Backpressure and load shedding (L76)
  - [ ] 14. Circuit breakers, bulkheads, graceful degradation (L77)
  - [ ] 15. Distributed locks and leases (L78)
  - [ ] 16. Leader election (L79)
  - [ ] 17. Quorums (L80)
  - [ ] 18. Consensus and Raft (L81)
  - [ ] 19. Distributed transactions (L82)
  - [ ] 20. Two-phase commit (L83)
  - [ ] 21. Saga pattern (L84)
  - [ ] 22. Transactional outbox and change data capture (L85)
  - [ ] 23. Monoliths, modular monoliths, microservices (L86)
  - [ ] 24. Service discovery and configuration (L87)
  - [ ] 25. Observability: logs, metrics, traces (L88)
  - [ ] 26. SLIs, SLOs and error budgets (L89)
  - [ ] 27. High availability and disaster recovery (L90)
  - [ ] 28. Active-passive versus active-active (L91)
  - [ ] 29. RTO and RPO (L92)

**NEXT LESSON: Phase 4, Topic 10 — At-most-once, at-least-once and exactly-once semantics (L73)**

---
