# Lesson 73 — At-Most-Once, At-Least-Once, and Exactly-Once Semantics

*Phase 4, Topic 10*

## 1. Simple definition

**Delivery semantics** are the guarantee a messaging system gives about how many times a given message will be handed to, and acted on by, a consumer in the presence of failures.

There are exactly three guarantees anyone offers:

- **At-most-once** — every message is delivered **zero or one** times. Messages may be **lost**. Messages are never **duplicated**.
- **At-least-once** — every message is delivered **one or more** times. Messages are never lost. Messages may be **duplicated**.
- **Exactly-once** — every message takes effect **exactly one** time. No loss, no duplication.

Terms defined before use:

- **Message**: one immutable unit of work placed on a queue or log (Lessons 68 and 71).
- **Consumer**: the process that reads a message and does something with it.
- **Processing**: the side effect the consumer performs — writing a row, charging a card, sending an email.
- **Acknowledgement (ack)**: the signal the consumer sends back to the broker meaning *"you may stop tracking this message; do not redeliver it."* In Kafka this is an **offset commit** (Lesson 72); in SQS it is `DeleteMessage`; in RabbitMQ it is `basic.ack`.
- **Redelivery**: the broker handing the same message to a consumer again because no ack arrived before a deadline.
- **Visibility timeout / lease / `max.poll.interval.ms`**: the deadline above. Different brokers, same idea — *"if you have not acked within T, I assume you died."*

The single most important sentence in this lesson:

> The delivery semantics you get are determined entirely by **when you acknowledge relative to when you process**.

Everything else — vendor features, configuration flags, marketing claims — is commentary on that sentence.

## 2. Why it exists

Lesson 17 established the fundamental ambiguity of a failed response: when a request times out, the caller cannot distinguish *"the work never happened"* from *"the work happened and the reply was lost."* Lesson 65 generalised it to partial failures: in a distributed system, "no response" is not an answer, it is the absence of one.

Message consumption has exactly the same ambiguity, pointed the other way. The broker hands a message to a consumer and then hears nothing. The broker cannot distinguish:

1. The consumer crashed **before** processing — the work did not happen, redeliver it.
2. The consumer crashed **after** processing but **before** acking — the work *did* happen, redelivering causes a duplicate.
3. The consumer is alive but slow — it is mid-processing right now, and redelivering creates a *concurrent* duplicate.

No amount of cleverness inside the broker resolves this, because the information needed does not exist anywhere in the broker. The broker must therefore pick a default behaviour on the deadline: **redeliver** (risking duplicates) or **drop** (risking loss). That choice is the entire topic.

Delivery semantics exist because somebody has to decide which of the two bad outcomes — a lost message or a duplicate message — is cheaper for *this particular operation*. That is a business question, not a technical one, which is why §9 computes it in money.

## 3. Real-world analogy

**Registered post versus a note slipped under a door.**

- **At-most-once** is dropping a note under someone's door and walking away. Cheap, fast, zero bookkeeping. If the note slides under the rug, nobody ever knows. You will never deliver two notes, because you only had one.
- **At-least-once** is registered post with signature on delivery. If no signature comes back by Friday, the post office sends another copy. If the first copy *did* arrive but the signature card was lost in the return mail, the recipient gets the letter twice. Nothing is ever lost; some things arrive twice.
- **Exactly-once** is what the customer wants and what the brochure implies. The post office can achieve it only by cheating: it numbers each letter, and the *recipient* agrees to throw away any letter whose number they have already filed. Notice where the work moved — the guarantee is not provided by the delivery network at all. It is provided by the recipient's filing cabinet. That is the whole lesson in miniature.

The Two Generals intuition, in the same frame: two generals on opposite hills must attack simultaneously, and can only communicate by messengers who may be captured. General A sends "attack at dawn." She cannot attack unless she knows B received it, so B must acknowledge. But B cannot attack unless he knows his acknowledgement arrived, so A must acknowledge the acknowledgement. This never terminates. **There is no finite exchange of messages over an unreliable channel that gives both sides certainty.** Therefore no protocol can guarantee a message is delivered and acted upon exactly once, end to end, over an unreliable network.

**Where the analogy breaks down:**

| Registered post | Message broker |
|---|---|
| Resends take days, so duplicates are rare and obvious | Redelivery can happen in 30 seconds, and a stuck consumer can generate hundreds of duplicates of the *same* message |
| One recipient per letter | A consumer group (Lesson 72) can rebalance mid-processing, so a *different* machine receives the duplicate — in-memory "have I seen this?" state is useless |
| The postman never delivers two copies simultaneously | Redelivery during a slow-consumer timeout produces **concurrent** duplicates on two machines at once; this is the race that breaks naive dedup (Lesson 74, §4) |
| The letter is the whole effect | Processing a message often has *several* effects — a DB write, a cache update, an outbound email — and they do not fail together |
| The post office is one trusted authority | Kafka's exactly-once covers only what is inside Kafka; the moment you call Stripe, you have left the post office's jurisdiction |

## 4. Detailed technical explanation

### 4.1 The ack-ordering theorem

A consumer must do two things: **process** (perform the side effect) and **ack** (tell the broker to stop tracking). There are only two possible orders.

**Order A — ack, then process.**

```
receive → ack → process
```

If the consumer dies after the ack and before processing finishes, the broker has already forgotten the message. It is gone. Nothing will ever retry it. This is **at-most-once**: zero or one processing, never more, possibly fewer.

**Order B — process, then ack.**

```
receive → process → ack
```

If the consumer dies after processing and before the ack lands, the broker's deadline expires and it redelivers. The side effect happens a second time. This is **at-least-once**: one or more processing, never fewer, possibly more.

**There is no Order C.** You cannot ack "during" processing in any way that helps, because the failure can land in whatever gap remains. You cannot make ack and process atomic, because they are in two different systems — your database and the broker — and making two independent systems commit atomically is itself a distributed transaction (Phase 4, Topic 17, *Two-phase commit and its limits*). This is the key insight of the entire lesson: **the three-way menu of semantics is really a two-way menu, plus a trick.**

### 4.2 At-most-once in detail

What it guarantees: no duplicate processing. What it does **not** guarantee: that the message is processed at all.

In practice you get at-most-once by one of:

- Acking on receipt, before any work (RabbitMQ `autoAck=true`, SQS delete-on-receive).
- Kafka's `enable.auto.commit=true` with a commit interval, where the offset can advance past records you have not finished handling.
- Fire-and-forget producers (`acks=0` in Kafka) — loss on the *write* side rather than the read side, but the same category.

It is almost never what you want, because the failure is **silent**. A lost message produces no error, no log line, no alert. An order simply never ships, and you find out from the customer six days later. Compare a duplicate, which is at least *visible* in the data.

The narrow cases where at-most-once is genuinely right share one property: **the data is a sample of a continuous signal, and the next sample is along shortly.**

- **High-frequency metrics.** A host emits CPU utilisation every 10 seconds. Losing one point leaves a 10-second gap in a graph that is averaged over 5 minutes anyway. Paying for at-least-once on 50,000 hosts × 6 points/minute would cost more than the data is worth.
- **Video or audio frames in a live stream.** A dropped frame is a flicker. A *duplicated* or late frame is worse than a dropped one, because it must be buffered, which adds latency to every subsequent frame.
- **Ephemeral presence signals** — "user is typing", cursor positions in a collaborative editor, live sports score tickers. State is fully refreshed by the next update.
- **Best-effort cache invalidation where a TTL is the backstop.** Losing the invalidation means stale data for at most the TTL, which you already accepted.

The test: *if I lose this message, does the system self-correct within an acceptable window without anyone doing anything?* If yes, at-most-once is defensible. If no, it is a bug you have not noticed yet.

### 4.3 At-least-once in detail

What it guarantees: the message will be processed at least once, assuming the consumer eventually succeeds and the broker retains the message. What it does **not** guarantee: how many times, or that the duplicates are spaced out, or that they land on the same machine.

This is the practical default for essentially all business messaging, for one reason: **loss is unrecoverable, duplication is recoverable.** A duplicate is a problem you can solve in the consumer with a dedup key and a unique index (Lesson 74). A lost message is a problem you cannot solve anywhere, because the information is gone.

Sources of duplicates, in rough order of frequency:

1. **Ack lost in flight.** Processing succeeded, the ack packet was dropped, the broker redelivered. Common.
2. **Consumer crash between process and ack.** Deploy, OOM-kill, spot-instance reclaim. Common, and *correlated* — a bad deploy produces a burst of duplicates.
3. **Lease expiry on a slow consumer.** Processing took longer than the visibility timeout, broker redelivered while the first attempt was still running. Produces **concurrent** duplicates.
4. **Consumer-group rebalance.** A partition moves to another consumer before the offset for in-flight records was committed; the new owner re-reads from the last committed offset (Lesson 72).
5. **Producer retry.** The producer's send timed out ambiguously (Lesson 17), it retried, and both copies actually landed. Note this duplicate exists *in the log itself* — no amount of consumer ack discipline removes it; only a dedup key does.

Point 5 is routinely forgotten. Consumer-side at-least-once is not the only duplicate source; the producer creates duplicates too, and they are indistinguishable at the consumer.

### 4.4 Exactly-once: the distinction that matters

Split the phrase in two.

**Exactly-once *delivery*** — the network delivers each message to the consumer process precisely one time. **This is impossible** in an asynchronous network with crash failures, for the Two Generals reason in §3. Any party can crash at any point, including between "did the work" and "recorded that I did the work", and no finite protocol closes that gap. Anyone selling exactly-once delivery is selling something else under that name.

**Exactly-once *processing*** (also called **effectively-once**) — the message may be *delivered* any number of times, but the observable *effect* on the system state occurs precisely once. **This is achievable**, by one of exactly two mechanisms:

- **Idempotency.** Make reprocessing a no-op. The consumer records a dedup key, or the operation is naturally idempotent (`SET status='shipped'` rather than `increment attempts`). Lesson 74 is entirely about this.
- **Transactional atomicity.** Make the side effect and the "I consumed this" bookkeeping commit as one atomic unit, so they cannot disagree. This only works when both live in a system that *can* commit them together — e.g. the effect is a row in Postgres and you store the consumed offset in the same Postgres transaction (ACID, Lesson 49). Or both live inside Kafka, which is what Kafka transactions do.

Being precise about this distinction is the difference between an engineer and a brochure. In an interview, "exactly-once delivery is impossible; exactly-once processing is achievable through idempotency or transactional atomicity" is the sentence that marks you as having actually built one of these.

### 4.5 How Kafka's exactly-once actually works

Kafka's EOS (exactly-once semantics) has three components. Understanding them tells you precisely where the boundary of the guarantee lies.

**(a) The idempotent producer** (`enable.idempotence=true`). On first connection the producer is assigned a **producer ID (PID)**. Every batch it sends carries the PID, the partition, and a monotonically increasing **sequence number**. Each broker partition leader remembers the highest sequence number it has accepted per PID. If a producer retries after an ambiguous timeout and resends sequence 42 when the broker already has 42, the broker **silently discards it and returns success**. This removes duplicate source #5 from §4.3 — but only for retries *within one producer session*, and only for the last few sequence numbers the broker keeps (default five in-flight). It does not deduplicate a message your application code genuinely sent twice.

**(b) Transactions.** A producer with a `transactional.id` can open a transaction, write to multiple topic-partitions, **and write its own consumer offsets into the internal `__consumer_offsets` topic**, then commit — all atomically. That last part is the trick that makes **consume-process-produce** pipelines exactly-once: the offset commit that says *"I consumed input record X"* and the output records produced from X are in the same transaction. Either both are visible or neither is. There is no window where one happened and the other did not.

**(c) The transaction coordinator.** A broker-side component that owns a transaction log. It assigns an **epoch** to each `transactional.id`; when a new instance claims the same ID, the epoch is bumped and the old zombie instance is **fenced** — its writes are rejected. The coordinator runs a two-phase commit over the partitions involved and writes **transaction markers** (commit or abort control records) into each partition's log. Consumers with `isolation.level=read_committed` filter out records belonging to aborted or still-open transactions.

**The crucial caveat.** Everything above is atomicity *within Kafka's own storage*. The transaction coordinator can roll back Kafka records. It cannot roll back:

- an email you sent via SendGrid,
- a card you charged via Stripe,
- a row you wrote to MySQL,
- an S3 object you uploaded,
- a webhook you fired at a partner.

If your consumer does `read record → charge card → produce result → commit transaction`, and the process dies between the charge and the commit, Kafka will correctly re-deliver the input record — and you will charge the card a second time. Kafka's EOS has protected its own books perfectly while your customer is double-charged. **Exactly-once stops at the Kafka boundary.** The instant your processing touches an external system, you are back to at-least-once plus idempotency, and the idempotency is your job.

### 4.6 Simplified teaching model versus production reality

The teaching model: "pick a semantic from a menu of three."

The production reality: you build at-least-once delivery, and then you buy exactly-once *processing* at the application layer by making every side effect idempotent. Exactly-once is not a setting you enable; it is a property you engineer, message handler by message handler. The only place you get it "for free" is a closed pipeline whose inputs, outputs and offsets all live in the same transactional system.

### 4.7 Decision table: operation type → required semantics

| Operation | Cost of loss | Cost of duplicate | Required semantics |
|---|---|---|---|
| Charge a payment card | Revenue lost, order never ships | Customer double-charged, chargeback, trust damage | At-least-once **+ idempotency key** (effectively-once) |
| Decrement inventory | Oversell, physical shortfall | Phantom stock-out, lost sales | At-least-once + dedup table |
| Send order-confirmation email | Customer anxiety, support ticket | Mild annoyance | At-least-once, dedup optional |
| Send password-reset email | User locked out | Two valid reset links — a **security** issue | At-least-once + dedup + single-use token |
| Write audit-log entry | Compliance breach | Double-counted event in reports | At-least-once + unique event ID |
| Update recommendation score | Slightly worse recommendation | Slightly skewed recommendation | At-least-once; duplicates tolerable |
| Host CPU metric sample | One gap in a graph | Skews an average slightly | **At-most-once** acceptable |
| Live video frame | Momentary flicker | Added buffering latency — worse than loss | **At-most-once** preferred |
| Kafka-to-Kafka stream join | Missing output record | Double-counted aggregate | Kafka transactions (true EOS applies here) |

## 5. Request / data flow

The at-least-once path end to end, from BookNook's checkout service to the inventory consumer.

1. The customer clicks **Place order**; the API server writes the order row to Postgres and commits.
2. The API server publishes `OrderPlaced{order_id, items[], event_id}` to the `orders` topic, with `acks=all` so the broker only reports success after the write is replicated.
3. The broker's leader appends the record, replicates it to followers, and returns an ack.
4. **The ack is lost in the network.** The producer's send times out — the ambiguous failure of Lesson 17.
5. The producer retries. With `enable.idempotence=true`, the broker sees a sequence number it already has and discards the duplicate. (Without it, the log now contains the record twice.)
6. The inventory consumer polls and receives the record at offset 9,417.
7. The consumer begins processing: open a Postgres transaction, decrement stock, insert a dedup row keyed by `event_id`.
8. The consumer commits the Postgres transaction. The side effect is now durable.
9. **The consumer process is killed** by a rolling deploy, before it commits the offset.
10. The broker detects the missing heartbeat and triggers a consumer-group rebalance (Lesson 72). Partition 3 is reassigned.
11. The new consumer starts from the **last committed offset**, 9,417 — the same record, redelivered.
12. The new consumer opens a transaction and attempts to insert the dedup row for `event_id`. The unique constraint rejects it.
13. The consumer recognises the conflict as "already processed", skips the decrement, commits nothing harmful, and **commits the offset**.
14. Offset 9,418 is now the committed position. The message was delivered twice and took effect once: **effectively-once**.

Step 13 is where exactly-once processing is actually produced. Not in the broker. In your consumer's `ON CONFLICT` clause.

## 6. ASCII diagram

```
            ACK-ORDERING DETERMINES SEMANTICS
            ---------------------------------

 AT-MOST-ONCE (ack first)          AT-LEAST-ONCE (ack last)
 ------------------------          ------------------------
   receive                           receive
      |                                 |
      v                                 v
   [ ACK ] --> broker forgets        [ PROCESS ] --> side effect durable
      |                                 |
      X  <-- crash here                 X  <-- crash here
      |                                 |
   process NEVER happens             [ ACK ] never sent
      |                                 |
      v                                 v
   MESSAGE LOST (silent)             REDELIVERED --> DUPLICATE


        EFFECTIVELY-ONCE = AT-LEAST-ONCE + IDEMPOTENT SINK
        --------------------------------------------------

  broker ---(msg, delivered 1..N times)---> consumer
                                              |
                                      +-------v--------+
                                      | dedup key seen?|
                                      +---+--------+---+
                                     yes |        | no
                                         v        v
                                      skip     process + record key
                                         \        /   (ONE transaction)
                                          \      /
                                           v    v
                                        state changed exactly once


        WHERE KAFKA EOS STOPS
        ---------------------

  +-------------------- Kafka transaction boundary --------------------+
  |  input topic --> consumer --> process --> output topic             |
  |                        \                                           |
  |                         +--> __consumer_offsets  (SAME txn, atomic)|
  +--------------------------------------------------------------------+
                                   |
                                   |  any external call escapes the box
                                   v
                        Stripe / SendGrid / MySQL / S3
                        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                        NOT covered. Your idempotency key
                        is the only protection out here.
```

## 7. Detailed example

### BookNook — the three consumers of one event

BookNook publishes a single `OrderPlaced` event. Three independent consumer groups read it (Lesson 72), and each needs a different semantic.

**Consumer group `payments`.** Calls Stripe to capture the authorised amount. Loss means BookNook ships a book it was never paid for. Duplication means a double charge, a chargeback at roughly £15 plus the disputed amount, and a customer who may never return. Both are expensive, so neither at-most-once nor plain at-least-once is acceptable. The design is at-least-once delivery plus **Stripe's `Idempotency-Key` header** set to a deterministic value derived from the order — `charge-{order_id}` — so that a redelivered message produces the *same* key, and Stripe returns the original charge instead of creating a new one. Effectively-once, achieved outside Kafka, by the payment provider's dedup table.

**Consumer group `notifications`.** Sends the order-confirmation email. Loss means a confused customer and a support ticket at roughly £4 of agent time. Duplication means a mildly irritated customer. The costs are asymmetric by about 10:1 in favour of tolerating duplicates, so plain at-least-once with a best-effort dedup key is correct. Note the contrast with the *password-reset* email on the same infrastructure: there, a duplicate issues a second valid reset link, which is a security exposure, so that handler gets a strict single-use token regardless of delivery count.

**Consumer group `recommendations`.** Increments a per-book popularity score used to rank the "Customers also bought" rail. Loss of one event shifts a score by one unit out of tens of thousands. A duplicate does the same in the other direction. Nobody can perceive either. Here the team deliberately chooses *not* to pay for dedup: no dedup table, no unique index, no extra write per message. At BookNook's volume that decision saves roughly 2.6 million dedup-row writes per month. This is the correct engineering answer, and it is correct *because the cost was computed*, not because duplicates were ignored.

The lesson from one event with three consumers: **delivery semantics are a per-handler decision, not a per-system one.** A team that configures "exactly-once" globally has usually paid a throughput penalty on the recommendation path to protect a payment path that was not actually protected, because Stripe is outside Kafka's transaction.

### Contrasting example — a wind-turbine telemetry fleet

A renewable-energy operator runs 1,200 turbines, each emitting vibration, rotor-speed and temperature readings at 20 Hz: 24,000 messages per second, continuously, forever.

The **telemetry stream** is at-most-once by deliberate design. Readings go over UDP to a collector that writes to a time-series database with no acks. If a reading is lost, the next arrives 50 ms later, and every dashboard and alert rule works on 1-minute rolling aggregates of 1,200 samples. Losing 0.1% of samples changes an aggregate by far less than sensor noise. Paying for at-least-once would mean ack tracking and retry buffers for 2 billion messages a day to protect data whose individual value is approximately zero.

The **alarm stream** on the same turbines is at-least-once with strict dedup. When vibration crosses a threshold indicating bearing failure, one message is emitted; losing it risks a £400,000 gearbox. A duplicate dispatches a second engineer 200 km, so the alarm carries `alarm_id = {turbine_id}-{fault_code}-{window_start}` and the dispatch system refuses to open a second work order for an `alarm_id` it has already seen.

Same fleet, same network, same brokers, opposite semantics — decided by the cost of loss versus the cost of duplication for each specific message type.

## 8. Code or configuration example

> **Illustrative only — not production-ready.** Error handling, metrics, tracing, connection pooling, schema migrations, retry classification and shutdown handling are all omitted or simplified. Do not paste this into a service.

```python
# ---------------------------------------------------------------------------
# THREE CONSUMERS, THREE SEMANTICS, SAME MESSAGE.
# ---------------------------------------------------------------------------

from kafka import KafkaConsumer          # illustrative client API
import psycopg2

# === (A) AT-MOST-ONCE =======================================================
# Offsets are committed automatically on a timer, independently of whether
# processing finished. A crash after the commit loses the message silently.
at_most_once = KafkaConsumer(
    "turbine.telemetry",
    enable_auto_commit=True,     # broker advances the offset on a timer
    auto_commit_interval_ms=1000,# ...every 1s, regardless of our progress
    auto_offset_reset="latest",  # on restart, skip the backlog entirely:
                                 # old samples are worthless, only "now" matters
)
for msg in at_most_once:
    write_sample_to_tsdb(msg.value)   # if we die here, the offset may already
                                      # have advanced past this record. Gone.

# === (B) AT-LEAST-ONCE ======================================================
# Auto-commit OFF. We commit the offset ONLY after processing returns.
# A crash between process() and commit() causes redelivery => duplicate.
at_least_once = KafkaConsumer(
    "booknook.orders",
    enable_auto_commit=False,    # WE decide when the offset moves
    group_id="notifications",    # consumer group (Lesson 72)
    max_poll_interval_ms=300000, # if a single poll loop exceeds 5 min the
                                 # broker assumes we died and rebalances,
                                 # which itself causes duplicates
)
for msg in at_least_once:
    send_confirmation_email(msg.value)  # side effect FIRST
    at_least_once.commit()              # ack SECOND  <-- the whole difference

# === (C) EFFECTIVELY-ONCE (at-least-once + atomic dedup) ====================
# The dedup record and the side effect commit in ONE database transaction,
# so they can never disagree. This is the only pattern that actually works
# when the side effect lives outside the broker.
conn = psycopg2.connect("dbname=booknook")

for msg in at_least_once:
    event = decode(msg.value)
    with conn:                                   # BEGIN ... COMMIT / ROLLBACK
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO processed_events (event_id, consumer, processed_at)
            VALUES (%s, 'inventory', now())
            ON CONFLICT (event_id, consumer) DO NOTHING
            RETURNING event_id
            """,                                 # ON CONFLICT: the duplicate
            (event.event_id,),                   # loses the race silently
        )
        if cur.fetchone() is None:
            # No row returned => the INSERT hit the unique constraint =>
            # this event was already processed. Skip the side effect.
            at_least_once.commit()               # still advance the offset!
            continue

        # First time we have seen this event. Do the real work in the SAME txn.
        cur.execute(
            "UPDATE inventory SET on_hand = on_hand - %s "
            "WHERE sku = %s AND on_hand >= %s",  # guard prevents negative stock
            (event.qty, event.sku, event.qty),
        )
    # Transaction committed here. Dedup row + inventory change are atomic.
    at_least_once.commit()   # offset commit is OUTSIDE the DB txn and may
                             # still fail -> redelivery -> handled by dedup.
```

```properties
# ---------------------------------------------------------------------------
# KAFKA TRUE EXACTLY-ONCE: only valid for consume-process-produce entirely
# INSIDE Kafka. Illustrative configuration, not a complete deployment.
# ---------------------------------------------------------------------------

# --- producer ---
enable.idempotence=true          # assigns a PID + per-partition sequence
                                 # numbers so broker-side retry dedup works
acks=all                         # leader waits for all in-sync replicas
max.in.flight.requests.per.connection=5   # safe ordering WITH idempotence on
transactional.id=booknook-enricher-3      # MUST be stable per logical task;
                                 # a restart with the same id fences the zombie
                                 # instance by bumping its epoch

# --- consumer feeding that producer ---
isolation.level=read_committed   # hide records from aborted / open txns
enable.auto.commit=false         # offsets are committed BY THE TRANSACTION,
                                 # never by the consumer independently
```

```sql
-- ---------------------------------------------------------------------------
-- The dedup table referenced above. Illustrative schema.
-- ---------------------------------------------------------------------------
CREATE TABLE processed_events (
    event_id     UUID        NOT NULL,   -- generated by the PRODUCER so that
                                         -- retries carry the SAME value
    consumer     TEXT        NOT NULL,   -- scope the key per consumer group:
                                         -- payments and inventory must both
                                         -- be allowed to process the event
    processed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (event_id, consumer)     -- the unique constraint IS the
                                         -- deduplication mechanism
);
```

## 9. Numerical example

**Question:** For three BookNook operations, is a lost message or a duplicate message more expensive, and by how much?

**Stated assumptions** (all figures typical/approximate for a mid-size UK e-commerce business):

- Volume: 40,000 orders per day.
- Observed loss rate under an at-most-once configuration: 0.05% (1 in 2,000) — ack-before-process plus a 0.05% consumer crash rate.
- Observed duplicate rate under at-least-once: 0.2% (1 in 500) — dominated by deploy-time crashes and lease expiry.
- Average order value: £28.
- Average gross margin: 30% → £8.40 per order.
- Card chargeback fee: £15, plus full refund of the charge, plus 20 minutes of support at £18/hour = £6.
- Support ticket handling: 12 minutes at £18/hour = £3.60.
- Customer lifetime value lost after a double-charge incident: 8% churn probability × £240 LTV = £19.20.

**Operation 1 — charging a payment card.**

Cost of one *lost* message: the order ships but is never charged.
- Lost revenue = £28.00
- Cost of goods already shipped = £28 × 70% = £19.60 (already counted inside the £28; the true loss is the full £28 of revenue)
- Loss per event = **£28.00**

Cost of one *duplicate* message: the customer is charged twice.
- Refund of the second charge = £28.00
- Chargeback fee = £15.00
- Support handling = £6.00
- Expected churn cost = £19.20
- Duplicate per event = £28.00 + £15.00 + £6.00 + £19.20 = **£68.20**

Expected daily cost at the stated rates:
- At-most-once: 40,000 × 0.0005 = 20 lost × £28.00 = **£560/day**
- At-least-once (no dedup): 40,000 × 0.002 = 80 duplicates × £68.20 = **£5,456/day**
- At-least-once **+ idempotency key**: 80 duplicates × £0 (Stripe returns the original charge) = **£0/day**, plus the cost of the dedup lookup, which is ~80 extra API round-trips of no financial consequence.

Conclusion: for payments, duplicates are 2.4× more expensive per event *and* 4× more frequent, so naive at-least-once is 9.7× worse than naive at-most-once — yet the correct answer is neither, because idempotency removes the duplicate cost entirely for roughly zero marginal spend.

**Operation 2 — sending the order-confirmation email.**

Cost of one *lost* message: the customer does not know the order succeeded.
- Probability they contact support = 55%
- Expected support cost = 0.55 × £3.60 = £1.98
- Probability they re-place the order, creating a duplicate order to cancel = 8% → 0.08 × £6.00 handling = £0.48
- Loss per event = £1.98 + £0.48 = **£2.46**

Cost of one *duplicate* message: two identical emails.
- Probability they contact support = 3%
- Expected support cost = 0.03 × £3.60 = £0.11
- Mild brand irritation, modelled at 0.2% churn × £240 = £0.48
- Duplicate per event = £0.11 + £0.48 = **£0.59**

Ratio: loss costs 2.46 / 0.59 = **4.2× more than a duplicate**. Daily:
- At-most-once: 20 × £2.46 = **£49.20/day**
- At-least-once: 80 × £0.59 = **£47.20/day**

These are nearly equal, which is the interesting result: for email, the two configurations cost about the same, so you should choose at-least-once anyway — because its failure mode (a second email) is *visible and self-explaining*, while the at-most-once failure mode is a silent absence nobody can debug.

**Operation 3 — updating a recommendation score.**

Assumptions: the "Customers also bought" rail is driven by a popularity counter per book. A typical popular title accumulates 18,000 events per month. Ranking positions shift when two titles' scores differ by less than 0.5%.

- One lost event changes a score by 1/18,000 = 0.0056%.
- One duplicate changes it by the same 0.0056% in the other direction.
- Threshold for a visible ranking change: 0.5% = 90 events.
- Daily duplicates affecting one title: 80 duplicates spread across ~12,000 distinct titles = 0.0067 duplicates per title per day, or about 0.2 per title per month.
- 0.2 events versus the 90-event threshold = 0.22% of the amount needed to change anything.
- Revenue impact = **£0.00**, to any precision anyone can measure.

Conclusion and the point of the exercise: on the same event, from the same topic, the correct spend on delivery guarantees ranges from "buy a provider-level idempotency key" (payments) through "at-least-once is fine" (email) to "do not pay for dedup at all" (recommendations). A single system-wide semantic is always wrong for at least two of the three.

## 10. Trade-offs

| Dimension | At-most-once | At-least-once | Effectively-once (ALO + idempotency) | Kafka transactional EOS |
|---|---|---|---|---|
| Data loss | Possible, silent | None | None | None (within Kafka) |
| Duplicates visible to the sink | None | Yes | None | None (within Kafka) |
| Throughput cost | Lowest | Low | One extra DB write + one unique-index lookup per message | 20–40% lower throughput, typical |
| Latency cost | None | None | +1–3 ms per message, typical | +commit-interval latency (tens of ms) |
| Operational complexity | Minimal | Low | Moderate — dedup table, TTL, partitioning (Lesson 74) | High — transactional IDs, fencing, coordinator tuning |
| Storage cost | None | None | Dedup table grows with volume × retention | Transaction state + markers in the log |
| Works for external side effects | n/a | Yes | **Yes** | **No** |
| Failure mode when it breaks | Silent absence | Duplicated effect | Duplicated effect if the dedup write is not atomic with the side effect | Stuck transactions block `read_committed` consumers |
| Debuggability | Worst — nothing to look at | Good — duplicates are in the data | Good | Moderate — requires coordinator knowledge |

The sharpest trade-off is the last row of value: **at-most-once is cheapest to run and most expensive to debug.** You pay nothing in CPU and everything in 2 a.m. investigations of orders that never shipped.

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| Consumer crashes after ack, before processing (at-most-once) | Order silently never ships; confirmation email never arrives | Usually **not detected automatically** — surfaces as a customer support ticket days later, or a reconciliation job comparing orders to shipments | No automatic recovery; manual replay from the source of truth, if one exists. This is why at-most-once is dangerous |
| Consumer crashes after processing, before offset commit | Possible double charge or double email | Duplicate rows, duplicate-charge alerts from the payment provider, customer complaint | Dedup table / idempotency key makes the redelivery a no-op; without one, manual refund |
| Visibility timeout shorter than processing time | Two workers process the same order concurrently; inventory decremented twice | `ApproximateAgeOfOldestMessage` rising, redelivery counter > 1, DB unique-constraint violations spiking | Raise the visibility timeout above p99.9 processing time, or extend the lease via a heartbeat; dedup prevents the double effect in the interim |
| Consumer-group rebalance mid-batch | Burst of duplicates correlated with every deploy | Duplicate rate spikes aligned with deploy timestamps in dashboards | Commit offsets before voluntary shutdown; use cooperative rebalancing; dedup absorbs the remainder |
| Producer retries after ambiguous timeout, idempotence disabled | Two identical events in the log; two emails | Consumer-side dedup-conflict metric rises with no corresponding redelivery | Enable `enable.idempotence=true`; consumer dedup key catches what remains |
| Kafka transaction left open by a hung producer | Downstream `read_committed` consumers **stall completely** — nothing appears to move | Consumer lag climbing while broker shows no errors; last-stable-offset frozen | `transaction.timeout.ms` expires and the coordinator aborts it; a restarted producer with the same `transactional.id` fences the zombie |
| Exactly-once assumed but side effect is external (Stripe) | Customer double-charged despite "exactly-once" being configured | Chargeback notifications; payment-provider duplicate reports | Add a provider idempotency key; Kafka EOS cannot help here and never could |
| Dedup table write and side effect in separate transactions | Dedup row recorded but side effect missing (message is "processed" yet nothing happened) — **silent loss re-created inside an at-least-once system** | Reconciliation only; no error is raised at the time | Make them one transaction (Lesson 74, §4); until then, manual replay with the dedup row deleted |
| Auto-commit interval advancing past unprocessed records | Sporadic, unexplained missing orders under load | Gap analysis of sequential IDs; lag drops without matching output | Disable auto-commit; commit explicitly after processing |

## 12. Alternatives

| Approach | What it gives you | Cost | Where it fits |
|---|---|---|---|
| At-most-once (ack first) | No duplicates, minimal overhead | Silent loss | Metrics, live frames, presence, TTL-backstopped cache invalidation |
| At-least-once + idempotent consumer | Effectively-once for any sink | One dedup write/lookup per message; dedup table operations | **The default for business messaging** (Lesson 74) |
| Kafka transactional EOS | True exactly-once for Kafka→Kafka | Throughput drop, coordinator complexity | Stream processing, enrichment, aggregation pipelines with no external effects |
| Transactional outbox | Atomic "DB write + event publish" without 2PC | Outbox table, a relay process, extra latency | Removes producer-side loss/duplication; Phase 4, Topic 18 — *Outbox pattern and change data capture* |
| Two-phase commit across broker and DB (XA) | Atomic ack + side effect | Blocking coordinator, poor availability, limited support | Rarely justified; Phase 4, Topic 17 — *Two-phase commit and its limits* |
| Store the offset in the same DB as the side effect | Effectively-once with no dedup table | Consumer must manage its own offsets and seeks | Single-sink consumers writing to one relational DB |
| Saga with compensating actions | Correctness without atomicity across services | Compensations must exist and be idempotent themselves | Multi-service workflows; Phase 4, Topic 16 — *Saga pattern and distributed transactions* |
| Reconciliation batch job | Catches whatever leaked through | Hours of latency; does not prevent, only repairs | Always worth having as a backstop, whatever else you do |
| Synchronous request/response instead of messaging | Caller learns the outcome directly | Loses the decoupling of Lesson 67; still ambiguous on timeout (Lesson 17) | Low-volume, low-latency operations where the caller must know |

## 13. When to use it

**Use at-most-once when:**

1. **High-frequency sampled telemetry.** Host and application metrics at 1–20 Hz, where dashboards aggregate over windows far larger than the sample interval and a lost point is invisible.
2. **Live media frames and real-time presence.** Video frames, audio packets, cursor positions, "user is typing" — where a late or duplicated item is *worse* than a missing one because it costs buffering.
3. **Cache invalidation protected by a TTL.** The TTL already bounds staleness, so a lost invalidation degrades gracefully to behaviour you already accepted.

**Use at-least-once (plus idempotency) when:**

4. **Any operation that moves money, inventory, or physical goods.** Orders, payments, refunds, shipments, stock adjustments.
5. **Any operation with legal or compliance significance.** Audit logs, consent records, regulatory reporting — where a missing record is a reportable incident.
6. **Any operation a human will notice the absence of.** Confirmation emails, status updates, document generation.

**Use Kafka transactional EOS when:**

7. **Consume-process-produce pipelines whose entire effect is Kafka records.** Enrichment, filtering, joins, windowed aggregations — and only when you have verified no external call hides in the handler.

## 14. When not to use it

**Do not use at-most-once when:**

1. **The message represents an irreplaceable event.** If no other system holds the information and no reconciliation can regenerate it, loss is permanent data destruction. A single lost `PaymentCaptured` event cannot be inferred later from anything.
2. **Loss is silent and undetected.** If you have no reconciliation job comparing inputs to outputs, at-most-once means your error rate is unknown and unbounded. "We have no alerts" is not evidence of correctness.
3. **Volume is low enough that per-message cost is irrelevant.** At 50 messages/second, the saving from skipping acks is immeasurable and the risk is real.

**Do not use Kafka transactional EOS when:**

4. **The handler performs any external side effect.** Email, payments, third-party APIs, non-Kafka databases, S3 — the guarantee does not extend there, and configuring it creates a false sense of safety that is more dangerous than no configuration at all.
5. **You need maximum throughput and duplicates are already harmless.** If every sink is idempotent, transactions buy nothing and cost 20–40% throughput (typical).
6. **The consumer's processing time is long and variable.** Long transactions hold the coordinator state and delay `read_committed` consumers, converting a processing-time problem into a downstream-visibility problem.

## 15. Common mistakes

**Beginner mistakes**

- **Believing "exactly-once" is a checkbox.** It is a property of your consumer's code, not of the broker's configuration page.
- **Leaving `enable.auto.commit=true` and assuming at-least-once.** Auto-commit can advance the offset past records still being processed; that is at-most-once wearing at-least-once's clothes.
- **Acking in a `finally` block.** `finally` runs on exception too, so failures are acked and lost. That is at-most-once by accident, and the worst kind because the exception was logged — someone will insist "we handle errors."
- **Generating the idempotency key inside the consumer.** A key generated at processing time is different on every redelivery, so it deduplicates nothing. The **producer** must generate it (Lesson 74, §4).
- **Testing only the happy path.** Duplicates and loss appear exclusively during crashes, rebalances, deploys and timeouts. If your test suite never kills a consumer mid-transaction, it has not tested delivery semantics at all.
- **Assuming duplicates are rare enough to ignore.** They are *correlated*, not uniform: a bad deploy produces thousands at once, which is exactly when their cost is highest.

**Experienced-engineer mistakes**

- **Enabling Kafka EOS on a pipeline with a hidden external call.** A metrics push, an audit webhook, a feature-flag lookup with a side effect — the transaction boundary is broken and nobody notices until an incident.
- **Setting the visibility timeout from average processing time instead of p99.9.** The tail is where redelivery happens; averages hide it completely. A handler averaging 200 ms with a 30-second p99.9 and a 10-second timeout will produce concurrent duplicates all day.
- **Writing the dedup record in a different transaction from the side effect.** This re-introduces both failure modes: commit dedup first and crash → silent loss; commit effect first and crash → duplicate. Covered in detail in Lesson 74, §4.
- **Reusing one `transactional.id` across instances, or randomising it per restart.** Shared IDs cause mutual fencing and endless aborts; randomised IDs defeat zombie fencing entirely and leak coordinator state.
- **Treating producer idempotence as application-level dedup.** It covers client-library retries within a session only. If your service retries the whole HTTP request and republishes, you get two records with two sequence numbers and the broker accepts both.
- **Choosing semantics once, at the system level.** As §7 and §9 show, the right answer differs per handler by orders of magnitude in cost.
- **Forgetting that a dedup table is itself a distributed-systems component.** It needs capacity planning, TTLs, partitioning, and a failure plan — if it is down, do you block (availability loss) or process anyway (duplicates)? Decide deliberately.

## 16. Interview perspective

This is one of the highest-signal topics in distributed-systems interviews, because the shallow answer and the deep answer look superficially similar and are completely different in substance.

**What a strong candidate says, roughly in order:**

1. States the three semantics precisely, in terms of what is guaranteed *and what is not*.
2. Derives them from ack ordering rather than listing them: *"There are only two orderings — ack before process, or process before ack — so there are only two achievable semantics. The third is constructed."*
3. Draws the distinction cleanly: **exactly-once delivery is impossible; exactly-once processing is achievable.** Sketches Two Generals in one or two sentences as the reason.
4. Names the two mechanisms for exactly-once processing: idempotency, or transactional atomicity between the side effect and the consumption record.
5. Explains Kafka EOS correctly — idempotent producer with PID and sequence numbers, transactions covering produce *and* offset commit, the coordinator with epoch fencing — then immediately volunteers the caveat that it stops at the Kafka boundary.
6. Makes it a business decision: compares the cost of loss and the cost of duplication for the specific operation being discussed.
7. Mentions that the choice is per-handler, not per-system.

**Common interview questions:**

- *"Can you build exactly-once delivery?"* The expected answer is no, with the Two Generals reasoning, followed immediately by "but exactly-once processing, yes, and here is how."
- *"Kafka advertises exactly-once. Is it lying?"* No — it is precise about a boundary most readers do not notice. Explain the boundary.
- *"Your consumer charges a card. Walk me through every failure point."* They want: ambiguous producer send, redelivery after crash, concurrent delivery on lease expiry, the Stripe idempotency key, and what happens if the dedup write and the charge are not atomic.
- *"When would you accept message loss?"* A candidate who says "never" has not worked at scale. Metrics and frames, with the self-correction argument.

**Red flags interviewers watch for:**

- Saying "we just use exactly-once" with no further detail.
- Not knowing that acking before processing is what creates at-most-once.
- Claiming Kafka transactions protect an external API call.
- Proposing "check if it exists, then insert" as deduplication without recognising the concurrent-duplicate race (two consumers both check, both find nothing, both insert).
- Treating duplicates as a rare edge case rather than a correlated, deploy-aligned burst.
- Being unable to name a single operation where at-most-once is acceptable — it suggests the three semantics were memorised, not reasoned about.
- Generating the idempotency key in the consumer.
- Dismissing the question as "the framework handles it" without being able to say which ordering the framework uses.

## 17. Knowledge check

**Conceptual**

1. Explain, without using the words "at-least-once" or "at-most-once", why the ordering of acknowledgement and processing is the only thing that determines delivery semantics — and why no third ordering exists.
2. A colleague says: "We enabled Kafka's exactly-once semantics, so our consumer that sends emails can no longer send a duplicate." Identify precisely where this reasoning fails and name the boundary that is being crossed.
3. At-most-once can lose messages, which sounds strictly worse than at-least-once. Describe a class of message for which at-most-once is nevertheless the *better* choice, and state the property of the data that makes it so.

**Practical scenario**

4. BookNook's `refunds` consumer calls the payment provider to issue a refund, then writes a `refund_issued` row to Postgres, then commits its Kafka offset. During a rolling deploy, 14 consumer pods are terminated mid-processing. Describe what each of the three operations may be in, enumerate the possible resulting states, and redesign the handler so that no customer can be refunded twice. State which guarantees come from the broker and which you must provide yourself.

**Calculation**

5. A consumer processes 3,000 messages/second. The operation takes 180 ms at p50 and 11 seconds at p99.9. The visibility timeout is set to 12 seconds. Deploys occur 6 times per working day and each terminates consumers mid-processing, with an average of 400 messages in flight across the fleet at the moment of termination. Assume an additional background crash rate of 0.02% of messages. Calculate the expected number of duplicate deliveries per 24-hour weekday, and then calculate how that number changes if the visibility timeout is lowered to 8 seconds (assume processing time is log-normally distributed such that 0.4% of operations exceed 8 seconds).

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
  - [x] 9. Consumer groups, partitions, offsets and ordering (L72)
  - [x] **10. At-most-once, at-least-once and exactly-once (L73) ← you are here**
  - [ ] 11. Idempotent consumers and deduplication (L74)
  - [x] **12. Dead-letter queues and retry queues (L75)**
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

**NEXT LESSON: Phase 4, Topic 11 — Idempotent consumers and deduplication**

---

# Lesson 74 — Idempotent Consumers and Deduplication

*Phase 4, Topic 11*

## 1. Simple definition

An operation is **idempotent** if applying it twice has the same effect as applying it once. `SET balance = 100` is idempotent; `INCR balance BY 10` is not. `INSERT ... ON CONFLICT DO NOTHING` is idempotent; `INSERT` is not.

**Deduplication** is the mechanism that makes a non-idempotent operation idempotent *by refusing to run it twice*: the consumer records a **dedup key** together with the effect, and on redelivery recognises the key and returns without doing the work again.

The relationship between these and every guarantee in Phase 4 is the equation that matters:

> **at-least-once delivery + idempotent effect = effectively-once processing**

There is no "exactly-once delivery" across a network (L73). There is only at-least-once delivery, plus effects that survive repetition. Every retry (L65), every redelivery after a visibility timeout (L68), every rebalance (L72), every replay (L70), and every duplicate from an idempotent producer exists to be absorbed by this lesson — and each one *will* happen in production, usually before lunch.

Terms defined before use:

- **Dedup key**: the value that identifies a logical operation — usually the event's `id`, sometimes a business key like `(order_id, 'receipt')`.
- **Dedup window**: how long dedup records are kept. It must exceed the **maximum possible redelivery window**, or a late duplicate gets through (this is the single most common misconfiguration).
- **Effectively-once**: the *effect* happens once, even though the *delivery* happened more than once. This is the only "exactly once" that exists, and it is a property of your consumer, not of your broker.
- **Natural idempotency**: the operation is idempotent by its nature (absolute writes, upserts, conditional writes).
- **Unnatural idempotency**: the operation accumulates (increments, appends, sends), so it needs dedup or a rewritten semantic.
- **Two-write problem**: the dedup record and the effect are two writes; if they are not in the same transaction, one of them can happen without the other — producing either a lost effect or a permanent duplicate suppression (§4.4).
- **Idempotency key** (of an external API): a header you send so the *provider* dedupes for you (`Idempotency-Key` on Stripe-like APIs, SQS FIFO `MessageDeduplicationId`).

The one sentence to carry out of this lesson: **make the effect idempotent where you can, and where you cannot, record the dedup key in the same transaction as the effect — because anything less is a choice about which failure you prefer.**

## 2. Why it exists

Five independent mechanisms in this phase produce duplicates, and none of them can be turned off:

| Source | Why it duplicates | Can you disable it? |
|---|---|---|
| **Retry after a timeout** (L65) | The request succeeded; the reply was lost | No — timeouts are unavoidable guesses |
| **Visibility timeout redelivery** (L68) | Consumer crashed or was slow; the ack never arrived | Only by risking loss |
| **Rebalance** (L72) | Partitions move; uncommitted work is replayed by the new owner | Only by not scaling or deploying |
| **Replay** (L70) | The whole point of a log is re-reading | No — that is the feature |
| **Idempotent producer / at-least-once publishing** | Producer retries after a broker ack was lost | Only by risking loss |

So the question is never "will my consumer see a duplicate?" It is "what does my system do when it does?"

BookNook's answer, before Lesson 74 was learned, was: **it double-credits loyalty points.** The consumer was:

```python
points = order.total_pence // 100
db.execute("UPDATE customers SET points = points + %s WHERE id = %s", points, cid)
```

The increment is the bug. During a deploy, a rebalance replayed **4 minutes** of uncommitted work; 1,100 orders were processed twice; 1,100 customers received double points (average 42 points, £0.42 of redeemable value each) — a trivial financial loss and a **non-trivial trust problem**, because the ledger could not be reconciled: there was no record of *which* event produced *which* points.

The fix is the pattern in this lesson, and note its shape: a **ledger row per event**, with the event ID as part of the primary key. The balance becomes a **derived sum** rather than a mutable counter. Three properties fell out at once:

- the increment became naturally idempotent (inserting the same row twice is impossible),
- the audit trail appeared for free (which event produced which points),
- and the reconciliation question became answerable (`SUM(points) WHERE customer_id = …`).

That is the ideal outcome of this lesson: **the best dedup is a data model in which the duplicate has nowhere to go.**

## 3. Real-world analogy

**Punching a ticket with a serial number**, versus ticking a box.

A conductor who ticks "passenger counted" once per station cannot tell a passenger who was counted twice from two passengers. The tick is a **delta**: it accumulates, and a repeated tick inflates the total. That is `INCR`, and it is why counters are the classic source of duplicate-induced corruption.

A ticket punched with a serial number is different: the punch is **per ticket**, so punching the same ticket twice leaves the same hole in the same place. The serial number is the dedup key, the punch is the effect, and "did this ticket get punched?" is answerable by looking at the ticket rather than at a tally. That is `INSERT ... ON CONFLICT DO NOTHING` with a unique key — the duplicate has **nowhere to go**.

Three refinements the analogy gets right, and each is a real design decision:

- **The serial must be printed before boarding, not after.** If the punch decides the serial number, two conductors punching simultaneously create two different serials and both count. This is the **dedup key derivation** decision: derive the key from the business event (the order ID, the message ID), never generate it inside the consumer.
- **The ledger of punches must live with the ticket, not in a separate book.** If the conductor writes the serial in a "punched tickets" notebook *and* punches the ticket, and the pen runs out between the two, you get either an unpunched-but-recorded ticket (**lost effect**) or a punched-but-unrecorded ticket (**duplicate effect**, next time round). This is the two-write problem, and the only clean answer is one mark that is both (one transaction).
- **A ticket from last month cannot be checked.** If the conductor only keeps a week of serial records, a ticket that resurfaces after that week looks new. That is the **dedup window**, and it must be longer than the longest possible delay before a duplicate can arrive.

## 4. Detailed technical explanation

### 4.1 The four ways to make an effect idempotent, in order of preference

| Rank | Technique | Mechanism | Example | Limitation |
|---|---|---|---|---|
| **1** | **Idempotent by data model** | The write is a keyed insert or an absolute assignment | `INSERT INTO loyalty_ledger(order_id, points) ON CONFLICT DO NOTHING` | Requires the effect to be expressible as a set/absolute value |
| **2** | **Dedup in the same transaction** | Dedup row + effect committed atomically | `INSERT INTO processed(event_id) …; UPDATE stock …; COMMIT;` | Only works when all effects are in that database |
| **3** | **Idempotency key on the external call** | The provider dedupes | `Idempotency-Key: order:88214:charge` | Only as good as the provider's window and semantics |
| **4** | **Status-row / two-phase with reconciliation** | `in_progress` → effect → `completed`, plus a sweeper | Payment capture with a `charges` table | Most complex; needs a reconciler (§4.4) |

Anything else — "check a Redis key, then do the work" — is rank 2 with the transaction removed, and it fails in one of two directions depending on the order of the two operations. Which one? That is §4.4.

### 4.2 Natural idempotency: change the operation, not the plumbing

The cheapest fix is usually a **rewrite of the effect**. Four rewrites that recur constantly:

| Non-idempotent | Idempotent replacement | Why it matters |
|---|---|---|
| `balance = balance + 10` | `INSERT INTO ledger(ref_id, amount)`; balance = `SUM(amount)` | Duplicate insert is rejected by the key |
| `stock = stock - 1` | `SET stock = 42` (absolute, from the event's stated truth) — or a `reserved_by` set | Duplicate sets are harmless |
| `INSERT INTO reviews (...)` | `INSERT ... ON CONFLICT (order_id, customer_id) DO UPDATE` | One review per order, enforced by the schema |
| `POST /v1/refunds` | `POST /v1/refunds` with `Idempotency-Key` | The provider absorbs the duplicate |
| `append(line)` to a count | keyed `INSERT` with `(stream_id, seq)` | Replays cannot double-append |
| `POST webhook` to a partner | optional; partner must support keys, **or** you accept at-least-once delivery to partners and document it | This is the genuinely hard one (§4.5) |

The pattern to internalise: **deltas are the enemy, absolute state and keyed sets are the friends.** Where the business genuinely needs a delta (points, stock movements, wallet credits), express it as an **append-only keyed ledger** and derive the delta.

### 4.3 Designing the dedup key

Four properties, and getting any of them wrong produces a subtle failure:

1. **Derived from the producer's identity of the operation, not generated by the consumer.** `message_id` or `event_id` from the envelope (L69 §4.4). If the consumer generates the key, every redelivery gets a new one and dedup does nothing.
2. **Stable across redeliveries.** Broker `MessageId` is stable for SQS; Kafka has no message ID at all, so you must put one in the envelope. Note that Kafka's `(topic, partition, offset)` **changes** if the record is ever re-produced, so it is *not* a safe dedup key for replays — use the envelope's `id`.
3. **Scoped to the consumer's effect, not globally.** Two consumers of the same event legitimately both need to act on it. So the dedup key is `(event_id, consumer_name)`, or each consumer keeps its own dedup table/state store.
4. **Bounded in size and time.** 1,000 events/sec is 86.4 M keys/day. At 8 bytes of key + 40 bytes of metadata, that is **4.1 GB/day**. Size the window from the redelivery worst case, not from "a week sounds safe" (§9 step 3).

### 4.4 The two-write problem: why "check then act" is not enough

This is the part people get wrong even after they have built a dedup table. The dedup record and the effect are two writes, and there are only three ways to order them:

```
 (A) MARK FIRST, THEN ACT                      (B) ACT FIRST, THEN MARK
   insert dedup(event_id)  ✓                    do effect               ✓
   <crash>                                      <crash>
   effect never happens                         insert dedup            ✗
   ⇒ dedup now says "done"                      ⇒ redelivery → EFFECT AGAIN
   ⇒ the event is LOST, silently, forever       ⇒ duplicates, forever-until-window
   ⇒ THE WORST POSSIBLE OUTCOME                 ⇒ requires dedup that already failed

 (C) BOTH IN ONE TRANSACTION  ✓✓✓
   BEGIN
     INSERT INTO processed_messages(event_id, consumer) ON CONFLICT DO NOTHING
     INSERT INTO loyalty_ledger(order_id, points)       -- the effect
   COMMIT
   ⇒ either both or neither. A redelivery either sees the dedup row (does
     nothing) or does not (does the whole thing). No third state exists.
```

**(A) is strictly worse than no dedup at all**, because it converts a duplicate into a silent loss. And (C) is only available when every effect is in the same database. When an effect is *external* — an email, a card charge, a partner webhook — no transaction spans it, and you need the **status row** pattern:

```
   BEGIN;
     INSERT INTO charges(order_id, status, idem_key)
       VALUES (88214, 'in_progress', 'order:88214:charge')  -- dedup IS the row
       ON CONFLICT (idem_key) DO NOTHING
       RETURNING id;
   COMMIT;
   -- If we inserted: call the provider WITH the idem_key, then
   --   UPDATE charges SET status='succeeded', provider_ref=… WHERE id=…
   -- If we did NOT insert: read the existing row.
   --   status='succeeded' → return its result (duplicate absorbed)
   --   status='in_progress' → AMBIGUOUS: query the provider by idem_key,
   --                         or leave it for the reconciler (L65 §8.4)
```

The rule: **the dedup record must never be able to exist without the effect, and the effect must never be able to exist without the dedup record — or, when it can, there must be a reconciler that repairs the difference.**

### 4.5 The genuinely hard cases

| Effect | Why it is hard | The answer |
|---|---|---|
| **Email / SMS / push** | The provider gives no dedup key | Send a **deterministic message id** where the provider supports it; otherwise accept at-least-once delivery to humans and design the content to be obviously duplicate-able (`ORDER 88214 — your receipt`, rather than a generic email). Never send from a replay without a replay filter. |
| **Card charge** | Money, and the provider's window may be 24 h | Provider `Idempotency-Key` + your own `charges` row + a reconciler + a nightly statement reconciliation |
| **Third-party webhook** | You do not control the receiver | At-least-once, with `event_id` in the payload so the receiver *can* dedupe; document that they must |
| **Counters / analytics** | Cheap to duplicate, expensive to notice | Make them keyed (HyperLogLog with the id, or a `COUNT(DISTINCT event_id)` derived view) rather than increments |
| **File writes / S3 objects** | Overwrite is idempotent; append is not | Deterministic key names (`receipts/{order_id}.pdf`), which makes the write an idempotent overwrite |
| **Replay of a side-effecting consumer** | Replay is a *feature* (L70) that re-sends every email | Replay into a *new* consumer group or with a **dry-run/dedup filter** enabled; never replay a sender into the live path |

That last row is worth its own sentence: **if a consumer is safe to replay, it is because its effects are deduped; if it is not safe to replay, then replay must be treated as a dangerous operation with a runbook.**

## 5. Request / data flow

One event, delivered twice, through (C) above — the correct pattern.

```
 DELIVERY 1
   receive event{id: "01J9K2…", type: order.placed, order_id: 88214, total: 2499}
   BEGIN;
     INSERT INTO processed_messages(event_id, consumer)
       VALUES ('01J9K2…','loyalty') ON CONFLICT DO NOTHING;      → 1 row
     INSERT INTO loyalty_ledger(customer_id, order_id, points, source_event_id)
       VALUES (4821, 88214, 24, '01J9K2…');                      → 1 row
   COMMIT;                       ← dedup + effect, atomic
   ack

 DELIVERY 2 (rebalance replayed the batch)
   receive the SAME event
   BEGIN;
     INSERT INTO processed_messages(...) ON CONFLICT DO NOTHING; → 0 rows
   ROLLBACK;                     ← nothing else ran
   ack                            ← ack the duplicate: it IS handled
   metrics.incr("dedup.hit")

 WHAT THE BUSINESS SEES
   loyalty_ledger: ONE row of 24 points, carrying the event id that caused it.
   Balance is SUM(points) → 24. Correct, and auditable.
   If a support agent asks "why does this customer have 24 points?", the answer
   is a row, not a guess.

 WHAT NOT TO DO (the (A) failure)
   BEGIN; INSERT INTO processed(...); COMMIT;     ← mark
   send_email()                                    ← <crash here>
   ⇒ the event is now permanently "done" and the email never happened. The
     system is silently missing work and no redelivery can repair it.
```

The external-effect variant, which is the one that shows up in interviews:

```
   BEGIN;
     INSERT INTO charges(order_id, idem_key, status)
     VALUES (88214, 'order:88214:charge', 'in_progress')
     ON CONFLICT (idem_key) DO NOTHING
     RETURNING id;
   COMMIT;

   if inserted:
       resp = provider.charge(amount, idempotency_key='order:88214:charge')
       UPDATE charges SET status = resp.ok ? 'succeeded' : 'failed',
                          provider_ref = resp.id
   else:
       row = SELECT * FROM charges WHERE idem_key='order:88214:charge'
       if row.status == 'succeeded': return row.provider_ref     # duplicate absorbed
       if row.status == 'in_progress':  escalate to the reconciler
       if row.status == 'failed':       return the failure        # do NOT retry blindly

   RECONCILER (every minute, and it is the thing that makes this safe):
       for row in status='in_progress' and age > 2 min:
           actual = provider.lookup(idempotency_key=row.idem_key)   # ASK, don't guess
           update the row to the truth, or mark 'unknown' and alert
```

## 6. ASCII diagram

The three orderings from §4.4, drawn as the only thing that matters — *what state exists if the process dies here*:

```
   (A) MARK THEN ACT                 (B) ACT THEN MARK             (C) ONE TRANSACTION
   ┌──────────────────┐              ┌──────────────────┐          ┌──────────────────┐
   │ mark  ✓          │              │ effect ✓         │          │ BEGIN            │
   │   ✗ CRASH        │              │   ✗ CRASH        │          │   mark ✓         │
   │ effect ✗         │              │ mark ✗           │          │   effect ✓       │
   └──────────────────┘              └──────────────────┘          │ COMMIT (atomic)  │
     dedup says done,                  redelivery redoes            └──────────────────┘
     work never happened               the effect                    both or neither
     ⇒ SILENT LOSS (worst)             ⇒ DUPLICATES                 ⇒ CORRECT
```

Dedup window versus redelivery window — the misconfiguration that silently defeats everything:

```
   redelivery can arrive as late as:
       max(visibility timeout × max attempts,        e.g. 30s × 5      = 150 s
           retry ladder total,                        e.g. 6 h
           DLQ redrive delay,                         e.g. up to 14 days
           rebalance window + operator replay)        e.g. unbounded → ∞

   dedup window (TTL of the dedup records) must be ≥ that maximum.

   ┌──────────────── redelivery worst case: 14 days (DLQ redrive) ─────────────┐
   │ dedup TTL = 24 h                                                          │
   │        ▲                                                                  │
   │        └── a message redriven on day 3 finds NO dedup record → processed   │
   │            a second time. The dedup table was there. It just was not       │
   │            there for long enough.                                          │
   └───────────────────────────────────────────────────────────────────────────┘
   ⇒ size the window from your WORST-CASE replay path, and if that is
     unbounded, make the effect naturally idempotent instead (§4.2).
```

## 7. Detailed example

### 7.1 Double points, and the model fix

BookNook's loyalty consumer, `UPDATE customers SET points = points + N` (§2). The rebalance replayed 4 minutes, 1,100 customers got double points, and the ledger could not be reconciled.

The rewrite, which removed three problems at once:

```sql
CREATE TABLE loyalty_ledger (
  source_event_id  text        NOT NULL,      -- the dedup key: the event that caused it
  customer_id      bigint      NOT NULL,
  order_id         bigint      NOT NULL,
  points           integer     NOT NULL CHECK (points >= 0),
  applied_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (source_event_id)               -- ← the duplicate has nowhere to go
);
CREATE INDEX ON loyalty_ledger (customer_id, applied_at DESC);

-- Applying an event (idempotent by construction):
INSERT INTO loyalty_ledger (source_event_id, customer_id, order_id, points)
VALUES ($1, $2, $3, $4)
ON CONFLICT (source_event_id) DO NOTHING;

-- The balance is derived, never stored:
SELECT coalesce(sum(points), 0) FROM loyalty_ledger WHERE customer_id = $1;
```

Consequences, all good except one:

- Replays are harmless — no dedup table needed, no window to misconfigure.
- The audit trail exists: every point traces to an event.
- A reversal is now expressible: `points < 0` rows for refunds (adjust the `CHECK` to allow negatives, or use an `entry_type` column).
- **The cost**: a derived `SUM` on every balance read. At 4 M customers with an average of 60 ledger rows each, a balance read is an index-only scan over ~60 rows — sub-millisecond — but a "top 1,000 customers by points" query becomes an aggregate over 240 M rows. BookNook solved that with a **materialised `points_total` column updated in the same transaction**, with a nightly reconciliation query that asserts `points_total = SUM(loyalty_ledger.points)`. That is the standard shape: **the ledger is the truth, the counter is a cache, and the drift check proves it.**

### 7.2 The email that was sent eleven times

A support ticket: one customer received the same "your order has shipped" email 11 times. Investigation showed:

- the email consumer used `enable.auto.commit=true` (L72 §11.2) so offsets were committed on a timer;
- the email provider was slow (8–12 s) during a deploy;
- the consumer exceeded `max.poll.interval.ms` and was evicted mid-batch;
- the batch was re-fetched by the new owner and re-sent;
- the consumer was **replayed twice by an operator** during the investigation, re-sending everything.

The dedup fix here is rank 3 (external effect), so it needed the outbox-plus-status shape:

```sql
CREATE TABLE email_sends (
  idem_key    text PRIMARY KEY,        -- 'shipped:88214' — deterministic
  status      text NOT NULL,           -- 'sent' | 'failed'
  provider_id text,
  sent_at     timestamptz
);

-- before sending:
INSERT INTO email_sends (idem_key, status) VALUES ('shipped:88214','pending')
  ON CONFLICT (idem_key) DO NOTHING;   -- 0 rows ⇒ already handled, skip
-- after successfully sending:
UPDATE email_sends SET status='sent', provider_id=$1, sent_at=now()
 WHERE idem_key='shipped:88214';
```

The lesson the team wrote on the wall: **`idem_key` is derived from the business fact, not from the message.** `shipped:88214` is stable across redeliveries, rebalances and replays; `message_id` is not stable across replays (a replayed Kafka record with a re-generated envelope is a new message), and a UUID generated in the consumer is stable across nothing at all.

## 8. Code or configuration example

```python
# ============================================================================
# BookNook: the three correct consumer shapes.
# ============================================================================

# --- SHAPE 1: dedup + effect in ONE transaction (all effects local) --------
def handle_local_effects(event, db):
    with db.transaction() as tx:
        n = tx.execute("""
            INSERT INTO processed_messages (event_id, consumer)
            VALUES (%s, %s) ON CONFLICT DO NOTHING
        """, event["id"], "inventory")
        if n == 0:
            return "duplicate"            # rollback; nothing else ran
        tx.execute("""
            INSERT INTO stock_movements (source_event_id, isbn, delta)
            VALUES (%s, %s, %s)
        """, event["id"], event["data"]["isbn"], -1)
        # A derived view keeps `books.stock` readable without a hot counter:
        #   books.stock = stock_on_hand + SUM(stock_movements.delta)
    return "applied"


# --- SHAPE 2: external effect, status row + reconciler (§4.4) --------------
def handle_charge(order_id, amount, db, provider):
    idem = f"order:{order_id}:charge"
    with db.transaction() as tx:
        inserted = tx.execute("""
            INSERT INTO charges (order_id, idem_key, status)
            VALUES (%s, %s, 'in_progress') ON CONFLICT (idem_key) DO NOTHING
            RETURNING id
        """, order_id, idem)

    if not inserted:
        row = db.one("SELECT * FROM charges WHERE idem_key=%s", idem)
        if row.status == "succeeded":
            return row.provider_ref            # duplicate absorbed silently
        if row.status == "failed":
            raise PermanentError(row.error)    # do NOT retry blindly
        raise Ambiguous(row)                   # → reconciler, never guess

    try:
        ref = provider.charge(amount, idempotency_key=idem)
        db.execute("UPDATE charges SET status='succeeded', provider_ref=%s "
                   "WHERE idem_key=%s", ref, idem)
        return ref
    except provider.Declined as e:
        db.execute("UPDATE charges SET status='failed', error=%s WHERE idem_key=%s",
                   str(e), idem)
        raise PermanentError(e)
    except provider.Timeout:
        # LEAVE IT 'in_progress'. The reconciler asks the provider what happened.
        # Do NOT mark it failed — that is the free-merchandise bug (L65 §11.7).
        raise TransientError()


# --- SHAPE 3: the effect is a naturally idempotent overwrite ---------------
def store_receipt(order_id, pdf_bytes, s3):
    # Deterministic key → a replay overwrites the same object with the same bytes.
    s3.put_object(Bucket="booknook-receipts", Key=f"receipts/{order_id}.pdf",
                  Body=pdf_bytes, ContentType="application/pdf")
    # No dedup table, no window, no reconciliation. The data model did the work.


# --- ANTI-PATTERNS, included because they are in most codebases ------------
def WRONG_check_then_act(event, redis, db):
    if redis.set(f"seen:{event['id']}", 1, nx=True, ex=3600):   # 1h TTL ← §6
        do_effect(event)        # if this throws, redis still says "seen" forever
    # Two bugs: the TTL (1h) is shorter than the redelivery window, and the
    # mark survives a failed effect → silent loss (shape A, §4.4).

def WRONG_dedup_on_broker_coordinates(event):
    key = (event["topic"], event["partition"], event["offset"])   # ← not stable
    # A replay re-produces the record at a NEW offset → new key → duplicate.
    # Always dedupe on the envelope's own id, set by the producer.
```

```sql
-- --- Dedup table that does not become a 100 GB liability ------------------
CREATE TABLE processed_messages (
  event_id    text        NOT NULL,
  consumer    text        NOT NULL,
  processed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (consumer, event_id)        -- consumer-scoped: each consumer dedupes
) PARTITION BY RANGE (processed_at);      --   independently (two consumers both act)

-- Partition per day; drop whole partitions at the window boundary. This is
-- O(1) retention, exactly like a log segment (L71 §4.2) — a DELETE of 86 M
-- rows would bloat the table, hammer vacuum, and cause its own incident.
CREATE TABLE processed_messages_2026_10_30
  PARTITION OF processed_messages
  FOR VALUES FROM ('2026-10-30') TO ('2026-10-31');

-- Window sizing query — run this against your actual worst-case redelivery:
SELECT max(now() - processed_at) AS oldest_retained,
       count(*) AS rows
FROM   processed_messages;
-- If oldest_retained < your max DLQ redrive delay, the window is TOO SHORT.
```

## 9. Numerical example

BookNook processes **1,000 events/sec** at peak (86.4 M/day) across 7 consumer groups.

**Step 1 — the duplicate rate you are already experiencing.**

```
  sources, per 1,000 events:
    rebalance replays (deploy every 2 weeks, 4 pods, ~300 in-flight each)
        → ~1,200 duplicates per deploy, amortised ≈ 0.1 per 1,000
    visibility-timeout redeliveries (0.5 % crash/restart rate)      = 5 per 1,000
    operator/reconciler replays (monthly projection rebuild)         = 0.2 per 1,000
    producer at-least-once retries (rare)                            = 0.1 per 1,000
    ─────────────────────────────────────────────────────────────────────────────
    total ≈ 5.4 duplicates per 1,000 events = 0.54 %

  0.54 % of 86.4 M/day = 467,000 duplicate processings PER DAY.
  Without dedup, every one of those is a double credit, a double email, or a
  double stock decrement.
```

**Step 2 — what the duplicates cost, in the loyalty ledger.**

```
  average order: £18 → 24 points
  467,000 × (share of duplicates hitting the loyalty consumer ≈ 1/7)
      = 66,700 double credits/day × 24 points = 1.6 M points/day
  at £0.01 per point of redeemable value = £16,000/day of unearned liability
  ⇒ £5.8 M/year, from 0.54 %.
  (Which is why the ledger rewrite in §7.1 was treated as urgent.)
```

**Step 3 — sizing the dedup window (the decision people get wrong).**

```
  worst-case redelivery path for BookNook:
     visibility timeout 30 s × maxReceiveCount 5     =    150 s
     retry ladder (1m, 5m, 30m, 2h, 6h)              =  8.6 h
     DLQ retention + fastest human redrive           = 14 days
     projection rebuild (a NEW group, not a redelivery) = n/a → not a dedup case
  ⇒ required window ≥ 14 days.

  storage at 1,000/s for 14 days:
     86.4 M/day × 14 = 1.21 B rows
     per row: 32 B event_id + 12 B consumer + 8 B ts + ~24 B index overhead ≈ 76 B
     = 1.21 B × 76 B = 92 GB   (plus one index)

  at 24 h (a common, wrong, default):
     86.4 M rows × 76 B = 6.6 GB  ✓ cheap, and WRONG: a day-3 redrive is
     processed a second time.

  BookNook's compromise: window = 14 days (matching DLQ retention), partitioned
  by day, and — because 92 GB is real money — the loyalty/inventory consumers
  do not use it at all, because their effects are naturally idempotent (§7.1).
  Only the 3 consumers with external effects keep the table: 39 GB.
```

**Step 4 — the derived-balance read cost, honestly.**

```
  4 M customers, 60 ledger rows each (240 M rows total, ~28 GB)
  single balance read: index-only scan of ~60 rows = ~0.08 ms    ✓ fine
  "top 1,000 by points": aggregate over 240 M rows = ~40 s       ✗ unacceptable
  ⇒ materialised points_total column, updated in the same transaction,
    plus a nightly drift assertion:
        SELECT count(*) FROM customers c
         WHERE c.points_total <> (SELECT coalesce(sum(points),0)
                                    FROM loyalty_ledger l
                                   WHERE l.customer_id = c.id);
    Alarm if drift > 0. Without the assertion, the cache silently rots
    (the same drift-detection pattern as L56's denormalisation).
```

## 10. Trade-offs

| Decision | Buys you | Costs you | Choose it when |
|---|---|---|---|
| **Idempotent data model** (ledger, keyed insert, absolute set) | No dedup table, no window, replay-safe forever | Read-side aggregation; schema redesign; possibly a materialised counter + drift check | **First choice**, always worth the redesign |
| **Dedup table in the same transaction** | Correct, simple, auditable | Storage proportional to window × rate (92 GB at 1,000/s, 14 days) | All effects are local to one database |
| **Status row + reconciler** | Correct for external effects | A reconciler to build and operate; ambiguous states that must be surfaced | Payments, third-party writes |
| **Provider idempotency key** | The provider does the work | Depends on their window (often 24 h) and semantics; not all providers have it | Any charge/create call to a modern provider |
| **Redis SETNX with TTL** | Cheap, fast, no table | **Not atomic with the effect** → loss or duplicates; TTL usually too short | Rate limiting and best-effort suppression — never for correctness |
| **No dedup at all** | Zero work | 0.54 % of traffic corrupted (step 1) | Truly idempotent operations only |
| **Dedupe on `(topic, partition, offset)`** | No producer change needed | Breaks on replay (new offsets) — the exact case you need it | Never |
| **Long dedup window** | Correct under long redrive delays | Storage and vacuum cost | Window ≥ max redelivery path |
| **Short dedup window** | Cheap | Late duplicates get through | Only when a reconciler catches the difference |

## 11. Failure scenarios

**11.1 — Mark-then-act (§4.4 A).** Dedup recorded, effect crashed, redelivery suppressed. **Symptom:** a small, steady trickle of missing work that no retry will ever fix, and a dedup table that claims everything succeeded. **Fix:** same-transaction, or status rows plus a reconciler. This is the most dangerous bug in the lesson because the system reports success.

**11.2 — Dedup TTL shorter than the redrive path.** A 1-hour Redis key against a 14-day DLQ. A message redriven on day 3 is processed twice. **Symptom:** duplicates appearing only after operational replays, i.e. exactly when you are already having a bad day. **Fix:** derive the window from the worst-case path (§9 step 3), and prefer natural idempotency.

**11.3 — Dedup key generated in the consumer.** A UUID per invocation: every redelivery is "new". **Symptom:** dedup table grows, duplicates still occur, table looks healthy. **Fix:** the key comes from the producer's envelope.

**11.4 — Dedup without atomicity, in Redis.** `SETNX` succeeds, then the DB write fails; or the DB write succeeds, then the process dies before `SETNX`. **Symptom:** either silent loss or duplicates, non-deterministically. **Fix:** the dedup record and the effect in one store, one transaction.

**11.5 — Replay re-sends every email.** A projection rebuild was run against the live email consumer. 240,000 emails. **Symptom:** a support queue on fire, and a sender reputation problem. **Fix:** replay into a separate group or with a dry-run flag; if a consumer's effects are not deduped, replay is a dangerous operation with a runbook (L70).

**11.6 — The increment that survived dedup.** Dedup was applied to the *message*, but the effect was a batch-level `UPDATE … SET points = points + total`. A partially-applied batch then re-ran. **Symptom:** drift that is not a duplicate of any single message. **Fix:** dedup at the level of the *effect's unit of atomicity*, not the transport unit — ideally one ledger row per event.

**11.7 — Dedup table bloat.** 600 M rows, never pruned, a `DELETE` cleanup job that ran for 6 hours and caused 40 minutes of replication lag. **Symptom:** an unrelated latency incident caused by housekeeping. **Fix:** partition by day, drop partitions (O(1) retention), never bulk `DELETE`.

**11.8 — Cross-consumer dedup collision.** A shared `processed(event_id)` table meant that the second consumer of the same event saw "already processed" and skipped its own work. **Symptom:** one consumer silently starved. **Fix:** scope the key by consumer (§4.3 property 3).

## 12. Alternatives

| Instead of | Consider | When |
|---|---|---|
| Dedup table | **Idempotent data model** (ledger/keyed insert) | Always try this first |
| Dedup table | **Provider idempotency key** | Charges, creates, any modern external API |
| Dedup table | **Conditional write** (`UPDATE … WHERE version = n`) | Optimistic concurrency is already the model (L52) |
| Redis SETNX | **The same transaction as the effect** | Correctness matters at all |
| Deduplicating messages | **Deduplicating effects** | The transport unit and the effect unit differ (batches, fan-out) |
| Application dedup | **Broker-level dedup** (SQS FIFO 5-min window, Kafka idempotent producer/transactions) | It removes *producer-side* duplicates; consumer dedup is still required |
| Dedup for at-least-once | **Exactly-once (transactional consume-process-produce)** | The whole pipeline is inside one Kafka cluster (L73) |

## 13. When to use it

- **Every consumer, without exception.** At-least-once is the only available guarantee (L73).
- **Every retried RPC with a non-idempotent effect** (L65).
- **Every replay path** — projections, rebuilds, backfills.
- **Every webhook receiver** you own: accept `event_id`, dedupe on it, and document it for your senders.
- **Before any DLQ redrive** (L75): a redrive is a replay, so the consumer must already be safe to replay.

## 14. When *not* to use it

- **When the operation is already idempotent.** `SET status='shipped'`, keyed inserts, deterministic S3 keys — adding a dedup table adds cost and a new failure mode for nothing.
- **As a substitute for fixing the model.** If a dedup table is protecting an `INCR`, rewrite the `INCR`.
- **For read-only consumers.** Nothing to dedupe.
- **With a window chosen for convenience.** A dedup table with the wrong window is worse than none, because it *looks* correct.
- **To paper over a poison message.** Dedup hides duplicates, not repeated failures; that is the DLQ's job (L75).

## 15. Common mistakes

1. **Check-then-act with two stores.** The classic silent-loss bug (§11.1).
2. **Mark first, act second.** Strictly worse than no dedup.
3. **Dedup key generated by the consumer.** Guarantees the dedup never fires (§11.3).
4. **Deduping on broker coordinates** rather than a producer-assigned ID — breaks on replay.
5. **Window shorter than the redelivery path** (§11.2).
6. **Forgetting the consumer scope**, starving a sibling consumer (§11.8).
7. **Deduplicating the message instead of the effect** (§11.6).
8. **A `DELETE`-based cleanup job** instead of partition drops (§11.7).
9. **Assuming "exactly-once" from the broker removes the need.** It covers producer duplicates and, at best, one transactional scope; your external effects are still yours.
10. **Never testing the failure:** kill the consumer mid-batch in staging and assert that the ledger total is unchanged after recovery.
11. **No reconciler for external effects**, so `in_progress` rows accumulate and nobody notices money in limbo.
12. **Replaying a side-effecting consumer into the live path** (§11.5).

## 16. Interview perspective

**What is actually being tested.** Whether you understand that delivery cannot be exactly-once, so *effects* must be made safe, and whether you can name the mechanism by cost order: data model first, then same-transaction dedup, then provider keys, then status rows with a reconciler. The second thing tested is the two-write problem — candidates who say "I'd check a Redis key" without noticing the atomicity gap are a level below candidates who say "the mark and the effect must commit together, or I need a reconciler".

**Answers that land well:**

- *"How do you handle duplicate messages?"* — "First, I try to make the effect idempotent by construction: a ledger row keyed by the event id, or a deterministic object key, so the duplicate has nowhere to go. When that is not possible — an email, a card charge — I record the dedup key in the same transaction as my local state, and for external effects I use a status row plus the provider's idempotency key plus a reconciler that asks rather than guesses. And the dedup window has to be at least as long as the worst redelivery path, which is usually the DLQ redrive, not the visibility timeout."
- *"Why not just use exactly-once?"* — "Because it does not exist across a network. Kafka's transactions give exactly-once effects inside Kafka for consume-process-produce, and that is genuinely useful, but the moment the effect is an email, a charge, or a database outside the transaction, it is at-least-once plus idempotency again."
- *"What is the danger in a dedup table?"* — "Ordering the writes. If I mark before acting, a crash loses the work permanently and the system reports success — that is worse than no dedup. The mark and the effect must be in one transaction, or the effect must be externally reconcilable."
- *"A customer got the same email eleven times. Diagnose."* — "At-least-once delivery with no dedup on a non-idempotent effect: a rebalance replayed the batch, the auto-commit offset had already moved, and an operator replay doubled it again. Fix: a deterministic idempotency key per business fact, a `sent` row written before the send, and replay via a separate group or with the sender disabled."

**Common interview tasks:** make a given handler idempotent; design a dedup key and window; explain the two-write problem; handle an external payment safely; explain what "effectively-once" means; choose between dedup and a ledger; explain why a replay is dangerous and how to make it safe.

**Red flags interviewers watch for:**

- "Kafka gives exactly-once, so we're fine" — including for external side effects.
- A dedup key generated inside the consumer.
- Redis `SETNX` as the whole answer, with no atomicity discussion.
- No dedup window reasoning, or a 1-hour window with a 14-day redrive path.
- Marking dedup before the effect.
- Not distinguishing message-level from effect-level idempotency.
- No reconciler for payments; defaulting an ambiguous charge to "failed".
- Being unable to say what happens when a rebalance replays uncommitted work.

## 17. Knowledge check

**Conceptual**

1. Explain why "at-least-once delivery + idempotent effect = effectively-once processing" is an identity rather than a slogan. Then explain why the same is not true for at-most-once, no matter how good the idempotency is.
2. Describe the two-write problem and the three possible orderings of the dedup record and the effect, stating what each does on a crash between them. Explain why one of the three is strictly worse than doing nothing, and name the two designs that avoid the problem.
3. Explain how to size a dedup window, listing the four paths that can deliver a message late and the arithmetic for each. Then explain why a 14-day DLQ with a 1-day dedup window is a systematic duplicate generator, and what the alternative fix is when the redelivery path is unbounded.

**Practical scenario**

4. BookNook's "shipment dispatched" flow must (i) append a `shipments` row, (ii) decrement stock, (iii) charge a stored payment method via a third-party provider, and (iv) send an email. Events arrive at-least-once at 400/sec, replays happen monthly, and the provider supports `Idempotency-Key` with a 24-hour window. For each of the four effects, state whether it can be made naturally idempotent and how, or which pattern you need, and give the exact dedup key. Then design the failure handling for the case where the process dies after the provider charges but before the local row is updated, including the reconciler's logic and its alarm.

**Calculation**

5. A consumer group processes 800 events/sec and sees duplicates at 0.7 %. (a) Compute the daily duplicate count. (b) If 30 % of those duplicates hit a mutable counter that credits 15 units each, compute the daily over-credit. (c) A dedup table costs 80 bytes per row; compute the storage for a 14-day window versus a 24-hour window, and state which one is defensible given a 10-day DLQ redrive delay. (d) If the effect is rewritten as a keyed ledger with average 50 rows per customer and 3 M customers, compute the total row count and estimate whether a single-customer balance read is acceptable.

6. A retry ladder is 1 min, 5 min, 30 min, 2 h, 6 h with a maximum of 5 attempts, and 3 % of messages fail permanently on the first attempt. (a) Compute the total time a message may spend retrying. (b) Assuming a transient failure succeeds on the second attempt 90 % of the time and the residual halves per attempt, estimate the fraction reaching the DLQ. (c) Compute the additional load the retry ladder imposes on the consumer if the failure rate spikes to 25 %. (d) State the dedup window this ladder implies.

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
  - [x] 9. Consumer groups, partitions, offsets and ordering (L72)
  - [x] 10. At-most-once, at-least-once and exactly-once (L73)
  - [x] **11. Idempotent consumers and deduplication (L74) ← you are here**
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

**NEXT LESSON: Phase 4, Topic 12 — Dead-letter queues and retry queues**

---

# Lesson 75 — Dead-Letter Queues and Retry Queues

*Phase 4, Topic 12*

## 1. Simple definition

A **retry queue** holds messages that failed and are waiting to be tried again, usually after a delay that grows with each attempt — the **backoff ladder**. A **dead-letter queue (DLQ)** holds messages that have failed *too many times to keep trying*, so that a single malformed message cannot loop forever (L68 §7.2).

They are two halves of one policy, and the policy has exactly three parts:

1. **Classify** the failure — transient (retry), permanent (dead-letter immediately), or ambiguous (resolve, then decide — L65 §4.2).
2. **Retry with a bounded ladder** — attempts, delays, jitter, and a total time budget.
3. **Park what fails** — with enough metadata to diagnose it, an owner, an alarm, and a replay path.

The last part is where almost every implementation is incomplete. A DLQ without an alarm is a graveyard: messages go in, nobody looks, and the business silently loses work. A DLQ **with** an alarm, metadata and a redrive runbook is the mechanism that turns "one bad message took down a consumer" into "one bad message, parked, fixed in 20 minutes, replayed".

Terms defined before use:

- **Poison message**: a message that will never succeed — malformed payload, a business rule that rejects it, a schema the consumer cannot parse. Retrying it is pure cost.
- **Backoff ladder**: the schedule of delays between attempts, e.g. 30 s → 2 min → 10 min → 1 h → 6 h.
- **Jitter**: randomisation of the delay so that many failing messages do not retry simultaneously (L65 §9).
- **Attempt budget / time budget**: a hard cap on total attempts and on wall-clock time spent retrying, after which the message is parked.
- **Parking lot / dead-letter topic**: a log-based equivalent of a DLQ (Kafka has no native DLX, so this is a topic with long retention and a dedicated consumer for human triage).
- **Redrive**: moving messages from a DLQ back to the source queue or topic. It is a **replay**, so the consumer must already be idempotent (L74 §14).
- **Head-of-line blocking**: one failing message prevents later messages in the same partition/queue from being processed, because ordering forces them to wait. The reason "retry in place" is sometimes unusable.

The one sentence to carry out of this lesson: **retries are for transient failures, DLQs are for permanent ones, and the difference between a resilient system and a permanently-degraded one is whether anyone is watching the DLQ.**

## 2. Why it exists

Without a retry policy, every transient blip becomes a user-visible failure. Without a DLQ, every permanent failure becomes an **infinite loop that consumes capacity forever**. BookNook has had both, in the same week.

**The no-retry problem.** A downstream dependency returns `503` for 20 seconds during a rolling restart. At 400 events/sec, **8,000 events** fail permanently and are lost (or marked failed, requiring manual repair) — for a fault that would have healed itself in 20 seconds.

**The no-DLQ problem.** Adding naive retries (immediate, forever) to fix that produced something worse. One malformed order — a `shipping_address` of `null` from a partner feed — threw in the renderer, was not acked, became visible after the 30 s visibility timeout, was redelivered, and threw again. It did this **120 times an hour**, forever, and each attempt consumed a worker for the full visibility timeout. One message occupied **4 % of the consumer fleet indefinitely**, and the queue's age metric climbed because the poison message kept `ApproximateAgeOfOldestMessage` high even after everything else drained. Two incidents, one week, both from the same missing policy.

The correct policy has a shape, and the shape is a **decision tree evaluated per failure**, not a configuration knob:

```
  failure occurs
      │
      ├── permanent (400, schema invalid, business rule)  → DLQ NOW, no retries
      ├── transient (429, 503, connection refused)        → retry on the ladder
      ├── ambiguous (timeout, 5xx after the work began)   → resolve via status
      │                                                     query, THEN decide
      └── unknown/crash                                    → redeliver, counts as
                                                            an attempt
```

The value of writing that tree down is that it converts "how many retries should we set?" — an unanswerable question — into three answerable ones: *what is the ladder*, *what counts as permanent*, and *who looks at the parking lot*.

## 3. Real-world analogy

A hospital emergency department's two waiting areas.

Patients arrive and are **triaged** (classified). A patient with a sprained ankle waits and is treated (**transient** — it will resolve with time and capacity). A patient whose scan shows something requiring a different department is **sent elsewhere with a referral** — immediately, without being put back in the queue (**permanent** — retrying the same thing will never help). A patient whose test results were lost is held in an **ambiguous** state and someone goes and re-runs the test, rather than either discharging them or treating them blindly (**resolve by asking**).

Now the part that makes the analogy useful: the **"too difficult today" tray** behind the desk. Every department has one — cases that were attempted, could not be completed, and set aside so that the queue keeps moving. Two departments show the two possible outcomes:

- The department where the tray is **checked at every shift handover, logged, and assigned**: nothing is lost, and the tray is nearly empty because unresolved cases are actively worked.
- The department where the tray is **a shelf nobody owns**: it fills silently, patients in it are effectively forgotten, and the only time anyone notices is when a family complains.

**The tray is the same object in both departments.** The difference is entirely the alarm, the owner and the handover — which is the whole engineering lesson. Adding a retry queue without an alarm'd, owned DLQ has built the second department and called it resilience.

One more detail from the analogy: the tray has a **policy for how many attempts before the tray** — a case is not re-attempted indefinitely, because a patient who cannot be treated at 3 p.m. will not become treatable at 3:05 p.m. just because you tried again. That is the backoff ladder, and its purpose is to spend attempts where time might actually change the answer.

## 4. Detailed technical explanation

### 4.1 Classifying the failure — the decision that everything else depends on

| Failure | Class | Evidence | Action |
|---|---|---|---|
| `400 Bad Request`, `422 Unprocessable` | **Permanent** | The request is malformed; nothing will change | **DLQ immediately** — no retries |
| Deserialisation / schema validation error | **Permanent** | The payload cannot be understood | DLQ immediately, with the payload preserved |
| Business rule rejection ("out of stock permanently") | **Permanent** | A valid negative answer, not an error | Acknowledge and record; **not** a DLQ case |
| `401` / `403` | **Permanent (operator)** | Credentials/config are wrong | DLQ + **page immediately** — this is a deploy problem, and it will affect every message |
| `404` on a resource that should exist | **Ambiguous** | Ordering or a race | Retry a few times, then DLQ |
| `429 Too Many Requests` | **Transient** | Rate limited; `Retry-After` is authoritative | Retry, honouring `Retry-After` |
| `503`, `502`, connection refused | **Transient** | Capacity or restart | Retry on the ladder |
| **Timeout** | **Ambiguous** | The work may have happened (L65 §4.2) | Retry **only with idempotency**; prefer a status query |
| `409 Conflict` | Depends | Usually "already done" | Treat as success for idempotent operations |
| Process crash / OOM | **Transient** | Infrastructure | Redeliver (which the broker does for you) |

The single most common misconfiguration is treating **all** errors identically. A consumer that retries a `400` five times with backoff has spent 8 hours of wall clock and 5 attempts per malformed message to learn what the first millisecond already said.

### 4.2 Three retry topologies, and when each is the only option

**(a) Retry in place** — `nack(requeue=true)` (RabbitMQ) or "don't ack, let the visibility timeout expire" (SQS).

```
   ✓ Zero extra infrastructure
   ✗ The retry is INVISIBLE: nothing shows you "this message is on attempt 3"
   ✗ Delays are coarse (the visibility timeout, or a fixed delay queue)
   ✗ HEAD-OF-LINE BLOCKING on an ordered queue: the failing message must be
     retried before later ones, or ordering breaks
   ✗ Delay is applied to ALL failed messages equally — no ladder
```

**(b) Retry queue with a delay** — a separate queue/topic whose messages become visible after a delay.

```
   SQS:          DelaySeconds ≤ 900 (15 min) per message, or re-enqueue chained
                 delay queues for longer ladders.
   RabbitMQ:     per-queue `x-message-ttl` + DLX back to the source queue
                 (a "delay queue" per rung), or the delayed-message plugin.
   Kafka:        retry TOPICS with a consumer that pauses until the due time,
                 or a scheduler that re-produces at the right moment.
   ✓ Visible per-rung: `retry.5m.depth` is a real metric
   ✓ No head-of-line blocking of the main queue
   ✓ Per-rung delays give a real ladder
   ✗ More queues/topics to operate, and the ladder must be provisioned
```

**(c) Scheduler service** — store "attempt at `T`" in a database and have a sweeper re-publish.

```
   ✓ Arbitrary delays (hours, days), exactly what long ladders need
   ✓ Durable and queryable: "show me everything retrying in the next hour"
   ✗ You built a queue on a database (and it must be scaled, monitored, and
     transactional with your business state)
   ⇒ The pragmatic choice for ladders longer than the broker supports
```

### 4.3 The ladder: attempts, delays, jitter, and a time budget

Four parameters, each with a different job:

| Parameter | Job | BookNook value | Rationale |
|---|---|---|---|
| **Delays** | Ride out outages of increasing duration | 30 s, 2 m, 10 m, 1 h, 6 h | The first rung covers blips; the last covers "someone fixed it after lunch" |
| **Max attempts** | Bound the cost | 5 | With a p=0.3 transient failure rate, residual `0.3⁵ = 0.24 %` |
| **Jitter** | Desynchronise retries | Full jitter (`rand(0, delay)`) | Without it, all messages retry at the same instant and re-create the failure |
| **Total time budget** | Bound staleness | 8 h | After that, the message is too old to be useful — park it |

Two facts about ladders that are worth memorising:

- **Exponential backoff without jitter is worse than no backoff** for a fleet, because every message that failed in the same window retries in the same window, producing a synchronised thundering herd (L65 §9).
- **The ladder's total duration must exceed the longest outage you intend to survive.** An 8-hour ladder survives a two-hour dependency outage; a 5-minute ladder turns that outage into permanent failures, and then the DLQ fills with messages that were never really broken.

### 4.4 DLQ design: the five properties that decide whether it helps

| Property | Why | BookNook implementation |
|---|---|---|
| **Per consumer** | A shared DLQ cannot be triaged or redriven safely | `orders.email.dlq`, `orders.inventory.dlq`, … |
| **Long retention** | Humans are slow; a 4-day DLQ loses the evidence | 14 days (SQS max), 30 days on Kafka parking topics |
| **Failure metadata attached** | Without it, triage means re-running the failure | Headers: `x-original-topic`, `x-attempts`, `x-error-class`, `x-error-msg`, `x-first-seen`, `x-trace-id` |
| **A non-empty alarm** | The whole point (§3) | Page at `depth > 0` for 5 min |
| **A replay path** | Without it, the DLQ is data loss with extra steps | A rate-limited redrive script with a runbook |

The metadata deserves emphasis because it is routinely omitted. A DLQ message containing only the original payload forces the on-call engineer to reproduce the failure from scratch, often against a dependency that has since changed. The four headers that save the most time: **attempt count**, **error class**, **first-seen timestamp**, and **trace id**.

### 4.5 Head-of-line blocking: when retries and ordering conflict

On an ordered stream (L72 §4.4), retrying in place blocks everything behind the failure in that partition:

```
  p3: [A ✓][B ✗ retry][C][D][E][F] …
                  ▲
                  └── C, D, E, F cannot be processed until B succeeds or is parked.
                      If B is a poison message, the partition is stuck FOREVER.
```

Three resolutions, each a different trade:

1. **Park fast, keep order** — DLQ the poison message immediately on a permanent error (it never blocks), and retry only transient ones briefly. Order is preserved for everything that proceeds.
2. **Skip ahead, lose order** — move the failed message to a retry topic and continue with C. Preserves throughput, **breaks ordering** for that key. Acceptable only if the effect is order-insensitive.
3. **Serialize per key** — route retries for the same key to a per-key queue so that key's ordering is preserved while other keys proceed. Effectively what a per-key partition already gives you (L72 §4.1).

The default should be (1): **a permanent error must never block a partition**, because ordering is not worth an infinite stall.

### 4.6 The redrive, which is a replay and therefore dangerous

```
  BEFORE REDRIVE — all five must be true:
    ☐ The consumer is idempotent on this path (L74). Non-negotiable: a redrive
      will re-deliver messages whose effects may already have happened.
    ☐ The root cause is fixed and DEPLOYED. Redriving into the same bug
      just re-fills the DLQ, this time with attempt counts already at 5.
    ☐ The redrive is RATE LIMITED. 100,000 messages dumped at once is a
      second incident (L68 §11.7).
    ☐ Ordering implications are understood: a redrive re-injects OLD messages
      at the TAIL of the stream. If the consumer depends on order, old
      messages arriving after new ones will violate it.
    ☐ Someone is watching the dashboards during and after.

  DURING:  redrive at ≤ 20 % of normal traffic so the effect is visible in
           the consumer's own metrics rather than as a spike.
  AFTER:   re-check DLQ depth (should be 0), consumer lag (drained), and the
           BUSINESS totals — the number of emails/charges/stock movements must
           match the number of messages redriven.
```

## 5. Request / data flow

One message, four failures, a ladder, and a park — with the timestamps that make the ladder's value visible.

```
 T+0s      consume msg(88214) → provider returns 503
           class = transient → do NOT ack, schedule attempt 2 at T+30s (jittered)
 T+30s     attempt 2 → 503 again
 T+2m30s   attempt 3 → 503
 T+12m30s  attempt 4 → 429 with Retry-After: 600
           ⇒ honour Retry-After (620 s), not the ladder's nominal 1 h
 T+22m40s  attempt 5 → 200 OK, ack ✓
           TOTAL: 5 attempts over 22 minutes, zero user-visible failures,
           and the message never touched the DLQ.

 ── a different message, a different path ────────────────────────────────
 T+0s      consume msg(88215) → json.loads raises KeyError('shipping_address')
           class = PERMANENT → park IMMEDIATELY (attempt 1 of 1)
           DLQ headers:
             x-original-topic: order-events
             x-attempts: 1
             x-error-class: PAYLOAD_INVALID
             x-error-msg: KeyError('shipping_address')
             x-first-seen: 2026-10-30T14:02:11Z
             x-trace-id: trace-9f2c4a10
           alarm fires: orders.email.dlq depth > 0

 T+18m     on-call opens the DLQ, reads the error class, sees 142 messages
           ALL with `PAYLOAD_INVALID` from the same partner feed
           ⇒ this is not one bad message, it is a BROKEN PRODUCER
           ⇒ escalation changes: fix the partner mapping, then redrive 142

 ── the redrive, done properly ────────────────────────────────────────────
 T+4h      mapping fixed and deployed; consumer verified idempotent
           redrive 142 messages at 5/sec (vs 400/sec normal) → 28 s
           DLQ depth → 0 ✓   lag drained ✓   emails sent: 142 ✓ (matches)
```

Note the pivot at **T+18m**: the DLQ's real diagnostic value was not the individual message but the **error-class distribution across the parked set**. One malformed message is a data problem; 142 identical malformed messages is a producer problem, and the fix is completely different. This is why the metadata headers matter more than the payload.

## 6. ASCII diagram

The ladder, drawn as a timeline with the classification branch that decides whether you climb it at all:

```
  failure
     │
     ├── PERMANENT ────────────────────────────────► DLQ   (attempts = 1)
     │                                                 └─ alarm, metadata, owner
     ├── AMBIGUOUS ──► status query ──► resolved ──► ack or DLQ
     │                              └─ unresolvable ──► DLQ (marked UNKNOWN)
     └── TRANSIENT ──► attempt 2 (30 s + jitter)
                        │ fail
                        ├──► attempt 3 (2 min)
                        │      │ fail
                        │      ├──► attempt 4 (10 min)
                        │      │      │ fail
                        │      │      ├──► attempt 5 (1 h)
                        │      │      │      │ fail
                        │      │      │      └──► DLQ  (attempts = 5, 8.6 h)
                        │      │      │             └─ "too old to be useful"
                        └──────┴──────┴──► success ──► ack ✓

  ⚠ WITHOUT JITTER, every message that failed in the same window retries in
    the same window: at 400/sec and a 3% failure rate, 12,000 messages retry
    simultaneously at each rung — recreating the failure you are retrying.
```

The three topologies, and the metric each one can and cannot give you:

```
 (a) RETRY IN PLACE          (b) RETRY QUEUES              (c) SCHEDULER
 ┌──────────────┐            ┌──────────────┐              ┌──────────────┐
 │ main queue   │            │ main queue   │              │ main queue   │
 │  [A][B][B][B]│            │  [A][C][D]   │              │  [A][C][D]   │
 │       ▲      │            └──────┬───────┘              └──────┬───────┘
 │       │ requeue                  │ fail                        │ fail
 └───────┴──────┘            ┌──────▼───────┐              ┌──────▼───────┐
  ✗ no attempt visibility    │ retry.30s    │              │ db: retries  │
  ✗ one delay for all        │ retry.2m     │              │ (attempt_at, │
  ✗ head-of-line blocking    │ retry.10m    │              │  attempts)   │
                             │ retry.1h     │              └──────┬───────┘
                             └──────┬───────┘                     │ sweeper
                                    │ exceeded                    │ re-publish
                             ┌──────▼───────┐              ✓ any delay
                             │     DLQ      │              ✓ queryable
                             └──────────────┘              ✗ you built a queue
                              ✓ per-rung depth
                              ✓ no blocking
```

## 7. Detailed example

### 7.1 The partner webhook ladder

BookNook pushes shipment events to a fulfilment partner over HTTPS. The partner's reliability: `p50` 200 ms, `p99` 1.2 s, with a 30-minute maintenance window twice a month and occasional 503s during their deploys.

The ladder, and why each rung exists:

| Attempt | Delay | Cumulative | Purpose |
|---|---|---|---|
| 1 | — | 0 | The happy path |
| 2 | 30 s + jitter | ~30 s | Blips, connection resets, brief 503s |
| 3 | 2 min | ~2.5 m | Their rolling restart |
| 4 | 10 min | ~12.5 m | Their deploy window |
| 5 | 1 h | ~1.2 h | Their maintenance window (30 m) plus slack |
| 6 | 6 h | ~7.2 h | Overnight incidents, fixed by morning |
| → DLQ | — | 7.2 h | Parked with metadata for the morning |

Two design decisions worth stating explicitly:

- **The gaps are aligned to the failures they cover**, not to a formula. A 6-rung ladder with `2^n` seconds (1, 2, 4, 8, 16, 32 s) covers **63 seconds** and would fail on the first 30-minute maintenance window. Exponential backoff is a starting shape, not an answer: **the ladder's horizon must exceed the outage durations you actually face.**
- **The total budget is 8 hours, and it is a business decision**, not a technical one: a shipment notification delivered 9 hours late is worse than one delivered never (the partner has already shipped), so parking after 8 hours is correct — and it is the DLQ's job to tell a human, not to deliver it eventually.

### 7.2 The DLQ that was a graveyard

BookNook's analytics consumer had a DLQ configured from day one — `maxReceiveCount=5`, `redrive_policy` set, 14-day retention. Textbook. It also had **no alarm**, and over 7 months it accumulated **1.9 million messages**: the consumer had been failing on a schema change since the day a new field was added, a *permanent* error, and every single message had dutifully climbed 5 rungs and parked.

The cost, discovered during an unrelated audit:

- `booking.revenue_daily` had been **under-counting by 4 % for 7 months**;
- rebuilding it required replaying 1.9 M messages from the DLQ (which was fine, once the consumer was idempotent);
- and the audit trail was inconsistent, because a quarterly report had been produced from the wrong data and distributed.

The three controls added, and the general lesson:

```
  1. DLQ depth > 0 for 5 minutes → page.
     Not "warn". A non-empty DLQ means work is not happening.
  2. DLQ error-class distribution on a dashboard.
     "142 × PAYLOAD_INVALID from partner X" is a producer problem.
     "1.9M × SCHEMA_MISMATCH" is a code bug with a 7-month blast radius.
  3. A business-level assertion, independent of the queue:
     rows written per 1,000 events must fall inside a band.
     This catches the case where messages are CONSUMED but produce nothing —
     which no queue metric can see (L69 §11.3).
```

The general lesson: **the DLQ was not the failure. The absence of an alarm was.** A DLQ is a promise that a human will look; without the alarm, it is a promise nobody knows they made.

## 8. Code or configuration example

```python
# ============================================================================
# BookNook: classification + ladder + park, on a queue consumer.
# ============================================================================

LADDER = [0, 30, 120, 600, 3600, 21600]        # seconds; index = attempt number
MAX_ATTEMPTS = len(LADDER)
TIME_BUDGET_S = 8 * 3600                        # business decision, not technical

def classify(exc) -> str:
    """The decision tree from §4.1. Getting this wrong is the most common bug."""
    if isinstance(exc, (json.JSONDecodeError, KeyError, SchemaError)):
        return "PERMANENT"
    if isinstance(exc, (BadRequest, Unprocessable, Forbidden, Unauthorized)):
        return "PERMANENT"          # 4xx: retrying cannot help. 401/403 → PAGE.
    if isinstance(exc, TooManyRequests):
        return "TRANSIENT"          # honour Retry-After
    if isinstance(exc, (ServiceUnavailable, ConnectionRefused, BrokenPipe)):
        return "TRANSIENT"
    if isinstance(exc, Timeout):
        return "AMBIGUOUS"          # L65 §4.2 — resolve, do not blind-retry
    return "UNKNOWN"                # crash-equivalent: counts as an attempt


def handle(msg):
    attempt = int(msg.headers.get("x-attempts", 1))
    first_seen = msg.headers.get("x-first-seen", now_iso())

    try:
        effect(msg)                              # idempotent — L74
        ack(msg)
        return
    except Exception as exc:
        kind = classify(exc)

    if kind == "PERMANENT":
        park(msg, attempt, kind, exc, first_seen)     # park NOW: no retries
        return

    if kind == "AMBIGUOUS":
        actual = resolve_with_authority(msg)          # ask, do not guess
        if actual is True:
            ack(msg); return
        if actual is False:
            park(msg, attempt, "AMBIGUOUS_RESOLVED_FALSE", exc, first_seen)
            return
        # unresolved → fall through to the retry ladder, but never mark success

    age = now() - parse(first_seen)
    if attempt >= MAX_ATTEMPTS or age.total_seconds() > TIME_BUDGET_S:
        park(msg, attempt, kind, exc, first_seen)
        return

    delay = LADDER[attempt]
    if kind == "TRANSIENT" and getattr(exc, "retry_after", None):
        delay = max(delay, exc.retry_after)           # the server knows better
    delay = random.uniform(0, delay)                  # FULL jitter — always

    republish(msg, extra_headers={
        "x-attempts":   str(attempt + 1),
        "x-first-seen": first_seen,
        "x-last-error": kind,
    }, delay_seconds=delay)
    ack(msg)                     # acked HERE: it now lives in the retry queue,
                                 #   not in the main queue. Exactly one owner.


def park(msg, attempts, kind, exc, first_seen):
    dlq.publish(msg.body, headers={
        **msg.headers,
        "x-original-topic": msg.topic,
        "x-attempts":       str(attempts),
        "x-error-class":    kind,
        "x-error-msg":      str(exc)[:1000],
        "x-first-seen":     first_seen,
        "x-parked-at":      now_iso(),
        "x-trace-id":       msg.headers.get("trace_id", "none"),
    })
    metrics.incr("dlq.parked", tags={"consumer": CONSUMER, "class": kind})
    ack(msg)
    # NEVER silently drop. If parking itself fails, do not ack — let the
    # broker redeliver rather than losing the message on the floor.
```

```yaml
# --- RabbitMQ: a delay ladder built from TTL queues + DLX ------------------
# retry.<n>s queue holds messages with x-message-ttl; on expiry the DLX routes
# them back to the main queue. One queue per rung (see §4.2b).
queues:
  orders.email:
    durable: true
    x-queue-type: quorum
    x-dead-letter-exchange: orders.email.dlx
    x-delivery-limit: 5                 # quorum-queue native attempt cap
  orders.email.retry.30s:
    x-message-ttl: 30000
    x-dead-letter-exchange: ""          # default exchange → back to source
    x-dead-letter-routing-key: orders.email
  orders.email.retry.10m:
    x-message-ttl: 600000
    x-dead-letter-routing-key: orders.email

# --- SQS: native redrive + native DLQ ---------------------------------------
queues:
  orders-email:
    redrive_policy:
      deadLetterTargetArn: arn:...:orders-email-dlq
      maxReceiveCount: 5
    visibility_timeout_seconds: 30
  orders-email-dlq:
    message_retention_seconds: 1209600      # 14 days: humans are slow

# --- The alarms that make the DLQ real ------------------------------------
alerts:
  - expr: sqs_approximate_number_of_messages_visible{queue=~".*-dlq"} > 0
    for: 5m
    severity: page            # NOT a warning. Work is not happening.
  - expr: increase(dlq_parked_total{class="PERMANENT"}[10m]) > 20
    severity: page            # a burst of permanent failures = broken producer
  - expr: increase(dlq_parked_total{class="UNAUTHORIZED"}[5m]) > 0
    severity: page            # a 401 is a credential problem: everyone is failing
  - expr: retry_queue_depth{queue=~".*retry.*"} > 50000
    severity: ticket          # the ladder is filling: a dependency is down
```

```bash
#!/usr/bin/env bash
# --- Redrive: rate limited, verified idempotent, watched. ------------------
# This is a REPLAY (L74 §14). Runbook prerequisites are in §4.6.
set -euo pipefail
DRY_RUN=${1:-true}          # default to dry run — deliberately
RATE=5                      # msg/sec: ≤ 20% of normal traffic, so it is visible

kafka-consumer-groups --describe --group email-redrive-2026-10-30   # no lag first
test "$(dlq_depth orders.email.dlq)" -gt 0 || { echo "DLQ empty"; exit 0; }

if [ "$DRY_RUN" = true ]; then
  echo "Would redrive $(dlq_depth orders.email.dlq) messages at ${RATE}/s"
  echo "Estimated duration: $(( $(dlq_depth orders.email.dlq) / RATE / 60 )) minutes"
  exit 0
fi

redrive --from orders.email.dlq --to orders.email --rate "$RATE" \
        --preserve-headers --stop-on-error
# AFTER: DLQ depth 0, lag drained, and BUSINESS totals reconciled —
#   emails_sent == messages_redriven. Assert it, do not assume it.
```

## 9. Numerical example

BookNook's email consumer: **400 events/sec**, transient failure rate **3 %**, permanent failure rate **0.05 %**.

**Step 1 — what the ladder buys, in successful deliveries.**

```
  P(success on attempt n | transient) with residual halving per attempt:
      attempt 1: 97.0 % ok, 3.0 % fail
      attempt 2: 90 % of the 3 % recover → 2.7 % ok, 0.30 % still failing
      attempt 3: 1.35 % ok, 0.15 % failing
      attempt 4: 0.675 % ok, 0.075 % failing
      attempt 5: 0.3375 % ok, 0.0375 % failing → PARK

  events/sec at 400:            attempts/sec
      attempt 1:  400
      attempt 2:   12.0   (3 % of 400)
      attempt 3:    1.2
      attempt 4:    0.6
      attempt 5:    0.3
  ⇒ retry load = 14.1/sec = 3.5 % extra. Cheap insurance.
  ⇒ parked = 0.15/sec = 12,960/day from transients? NO — see step 2, this is
    the number people get wrong.
```

**Step 2 — the correct parked-per-day figure, because retries are not free successes.**

```
  transient failures/day  = 400 × 0.03 × 86,400        = 1,036,800
  recovered by the ladder = 1,036,800 × (1 − 0.0375/0.03)… simplify:
      residual failing fraction after 5 attempts = 0.0375 % of ALL messages
      = 400 × 0.000375 × 86,400 = 12,960/day

  permanent failures/day  = 400 × 0.0005 × 86,400       =  17,280/day

  ⇒ DLQ inflow ≈ 30,240 messages/day  (0.0875 % of traffic)

  This is the number that decides whether your DLQ is a tool or a graveyard.
  30,240/day is ~21/minute. A paging alarm at depth > 0 would page 21 times a
  minute — so the alarm must be on RATE and CLASS, not merely on depth:
      page   if PERMANENT rate > 20 / 10 min   (a burst = broken producer)
      page   if depth GROWS for 30 min          (a trickle = permanent drift)
      ticket if depth > 0 and class ∈ {TRANSIENT_EXHAUSTED}
```

**Step 3 — storage and retention.**

```
  DLQ message avg 2 KB: 30,240/day × 2 KB = 60 MB/day
  14 days retention = 845 MB   ← trivial. Retention is not the cost here;
  visibility is. A 14-day DLQ nobody reads is 845 MB of unprocessed business.
```

**Step 4 — the ladder's horizon versus the outages it must survive.**

```
  Hopper's (naive) 2^n ladder: 1s,2s,4s,8s,16s → total 31 s
      survives: a 20-second blip.
      fails:     EVERY 30-minute maintenance window → 100 % of those messages
                 reach the DLQ, even though nothing was wrong with them.

  BookNook's ladder: 30s,2m,10m,1h,6h → total 7.2 h
      survives: the 30-minute maintenance window, their rolling deploy, and
                an overnight incident fixed before 08:00.
      ⇒ same "5 attempts", 837× the horizon.

  Cost of the longer ladder: a poisoned message occupies an attempt slot for
  up to 7.2 h instead of 31 s. Since a permanent error parks on attempt 1,
  that cost is ~0 for the poison case — which is the whole reason §4.1 matters.
```

**Step 5 — redrive time, and why it must be rate limited.**

```
  After the §7.2 schema fix: 1.9 M parked messages.
  Redrive at the consumer's full capacity (400/s): 4,750 s = 79 min, with a
    1,000-message-per-minute spike into the downstream email provider →
    provider rate limit (500/s) → 429s → NEW retries → ladder → DLQ.
  Redrive at 5 % of capacity (20/s): 26.4 h. Too slow.
  Redrive at 80/s (20 % of capacity): 6.6 h, provider stays under its limit,
    and the effect is visible in normal dashboards. ✓
  ⇒ The redrive rate is set by the SLOWEST dependency behind the consumer,
    not by the consumer's own throughput.
```

## 10. Trade-offs

| Decision | Buys you | Costs you | Choose it when |
|---|---|---|---|
| **Retry in place** | No infrastructure | No attempt visibility, coarse delay, head-of-line blocking | Ordered queues where failures are rare and brief |
| **Retry queues per rung** | Visible ladder, no blocking | Queues to operate and provision per rung | The default for any queue consumer |
| **Scheduler service** | Arbitrary delays, queryable | You built a queue on a database | Ladders longer than the broker supports (hours/days) |
| **Park immediately on permanent errors** | A poison message never blocks or wastes attempts | Requires reliable classification | **Always** — this is the single highest-value rule |
| **Retry all errors N times** | Simple to implement | Wastes the whole budget on unfixable failures; blocks partitions | Never |
| **Ladder horizon ≈ 30 s** | Fast DLQ feedback | Every real outage becomes permanent failures | Nothing you care about |
| **Ladder horizon ≈ hours** | Survives real outages | Poisoned messages occupy retry slots | Default |
| **DLQ per consumer** | Triageable, redrivable independently | More queues and alarms | Always |
| **Shared DLQ** | One place to look | Cannot redrive safely; no owner | Never |
| **Alarm on depth only** | Simple | Pages 21×/minute on a normal trickle, or is muted | Use rate + class |
| **Alarm on rate + class** | Actionable, and distinguishes "broken producer" from "drift" | Slightly more config | Always |
| **Automatic redrive** | Self-healing | Re-sends into the same bug; ordering hazards; rate spikes | Only for known-transient classes, with a ceiling |

## 11. Failure scenarios

**11.1 — The unowned DLQ** (§7.2). Configured correctly, alarmed never; 1.9 M messages over 7 months; a 4 % revenue under-count. **Symptom:** none, until an audit. **Fix:** page on non-empty, dashboard the class distribution, and add a business-level correctness assertion.

**11.2 — Retrying a permanent error.** A `400` on a malformed payload was retried 5 times with a 7-hour ladder, blocking its partition for 7 hours and occupying worker slots. **Symptom:** one partition stalled, everything else fine. **Fix:** classify first (§4.1); park permanent errors on attempt 1.

**11.3 — The ladder shorter than the outage.** A 5-attempt, 31-second ladder against a 30-minute dependency maintenance window → 100 % of the affected window's messages parked, all of them healthy. **Symptom:** a DLQ that fills predictably at 03:00 on the second Sunday. **Fix:** size the horizon to the longest outage you intend to survive (step 4).

**11.4 — Retry storm.** 400/s with a 25 % failure rate and no jitter: at each rung, all messages retry simultaneously, spiking the dependency to 500+ attempts/sec and preventing its recovery. **Symptom:** the dependency's load *increases* after its error rate rises. **Fix:** full jitter, per-rung rate limits, and a **retry budget** (retries ≤ 10 % of normal traffic, L65 §8.3).

**11.5 — Redrive into the same bug.** A 200,000-message DLQ was redriven before the fix was deployed; all 200,000 were re-parked, with attempt counts already at 5, so they parked immediately — and because the redrive was unthrottled it also saturated the dependency for 40 minutes. **Symptom:** DLQ depth spikes to 200,000 immediately after a redrive. **Fix:** the §4.6 checklist, in order, with the fix verified before the redrive.

**11.6 — Ordering violated by a redrive.** Parked messages were re-injected at the tail of an ordered stream, so a `shipment.dispatched` was processed after `shipment.delivered` for 400 orders, producing negative transit times and support tickets. **Symptom:** a burst of impossible states, exactly after a redrive. **Fix:** redrive into a dedicated ordering-aware path, or park only order-insensitive work, or replay the *stream* from a point in time rather than redriving individual messages (L70).

**11.7 — The DLQ that re-poisoned the source.** A DLQ was configured to redrive automatically back to the source after 1 hour ("self-healing"). The poison message loop simply became slower: park → 1 h → retry → park → 1 h. **Symptom:** a stable, low-rate error that never resolves and never escalates, because the depth never grows. **Fix:** never auto-redrive permanent classes, and cap automatic redrive attempts globally.

**11.8 — Parking failed, message lost.** The DLQ publish failed (IAM policy change) and the consumer acked anyway "because it tried to park it". **Symptom:** unwitnessed data loss. **Fix:** never ack if the park did not succeed — let the broker redeliver, and alarm on `dlq_publish_failures > 0`.

## 12. Alternatives

| Instead of | Consider | When |
|---|---|---|
| DLQ | **Park in a database table** | You need querying, correlation with business rows, and a UI |
| DLQ | **Parking topic with long retention** | Log-based systems (Kafka has no native DLX) |
| Retry queues | **In-consumer scheduled retry table** | Ladders of hours/days, and you already have a database |
| Retrying | **Fail fast and fix** | The failure is your own code and 100 % reproducible |
| Retrying | **Idempotent replay from the log** (L70) | The failure affected a time window, not individual messages — this is usually the RIGHT answer for a consumer bug |
| Retry loop | **Circuit breaker** (L77) | The dependency is down; retrying *is* the harm |
| DLQ | **Backpressure / load shedding** (L76) | You are losing messages due to volume, not due to per-message failure |

## 13. When to use it

- **Every queue consumer, without exception.** A consumer with no DLQ converts code bugs into capacity outages.
- **Whenever a dependency can be transiently unavailable** — which is always.
- **Whenever a payload is supplied by an external party**, because malformed payloads are permanent by nature.
- **Before you need it.** Retrofitting a DLQ during an incident means losing the messages that triggered the incident.
- **With a redrive runbook written in advance**, because §4.6's checklist is impossible to reconstruct at 03:00.

## 14. When *not* to use it

- **As a substitute for idempotency** (L74). A redrive of a non-idempotent consumer is a duplicate generator.
- **For volume problems.** If messages fail because the consumer is overwhelmed, the fix is backpressure and capacity (L76), not parking; a DLQ full of healthy messages is a symptom of misdesign.
- **As an unbounded retry mechanism.** Retrying "until it works" is a DLQ with no alarm and no cap.
- **To avoid classifying errors.** The classification is the work; the DLQ is where the classification's output goes.
- **As a silent drops bucket.** A DLQ must have an owner; if nobody will look, you have built a graveyard with better metrics.

## 15. Common mistakes

1. **No DLQ at all**, so a poison message loops forever (L68 §7.2).
2. **A DLQ with no alarm**, so it is a graveyard (§11.1).
3. **Retrying permanent errors** and burning the whole ladder on them (§11.2).
4. **A ladder horizon too short** for real outages (§11.3).
5. **Backoff without jitter**, creating synchronised retry storms (§11.4).
6. **Unthrottled redrive** into a fragile dependency or a still-broken consumer (§11.5).
7. **Redriving into an ordered stream** and violating per-key ordering (§11.6).
8. **Auto-redrive back to the source**, which turns a fast poison loop into a slow one (§11.7).
9. **Acking when parking failed**, losing the message silently (§11.8).
10. **A shared DLQ across consumers**, making safe redrive impossible.
11. **No failure metadata** — no attempt count, error class, first-seen or trace id.
12. **Alarming on depth alone**, which is either too noisy or gets muted.
13. **No business-level assertion**, so "consumed but produced nothing" is invisible (L69 §11.3).
14. **Treating a business rejection ("out of stock") as an error** and parking a valid negative answer.

## 16. Interview perspective

**What is actually being tested.** Whether you treat failure handling as a **classification problem** first and a configuration problem second. Strong candidates say "permanent errors park immediately, transient ones climb a ladder sized to my real outages, ambiguous ones get resolved by asking" — and then insist that the DLQ has an owner, an alarm, metadata, and a rate-limited replay. Weak candidates say "3 retries and a DLQ" and stop.

**Answers that land well:**

- *"What happens when a message fails?"* — "It depends what kind of failure. Malformed payload or a 4xx parks immediately with the error class and trace id attached, because retrying cannot help and it must not block the partition. Transient — 429, 503, connection refused — climbs a jittered ladder whose horizon covers our longest real outage, about 7 hours. A timeout is ambiguous, so I resolve it with a status query rather than blind-retrying. Everything lands in a per-consumer DLQ that pages at non-zero depth, with a runbook that checks idempotency and deployment before any redrive."
- *"How many retries?"* — "As many as fit inside the outage durations I actually face, with jitter and a time budget. A 31-second ladder against a 30-minute maintenance window turns a healthy window into 100 % parked messages, so the number is derived from the dependency's behaviour, not picked."
- *"A DLQ has 500,000 messages. What do you do?"* — "First, look at the error-class distribution: one class at scale is a producer bug or a schema change, mixed classes are a dependency outage. Fix and deploy first, verify the consumer is idempotent, then redrive at a rate the slowest downstream can take — maybe 20 % of normal traffic — while watching depth, lag and the business totals match."
- *"Why not just retry forever?"* — "Because a poison message then becomes a permanent capacity loss: one bad message holding a worker for the full visibility timeout, forever. That is a self-inflicted outage with no end condition."

**Common interview tasks:** classify a list of HTTP statuses into retry/park; design a ladder given stated outage durations; design a DLQ with metadata and alarms; write a redrive runbook; decide between retry-in-place and retry topics; explain head-of-line blocking and its three resolutions.

**Red flags interviewers watch for:**

- "Retry 3 times, then DLQ" with no error classification.
- No alarm on the DLQ, or no mention of who looks at it.
- Exponential backoff with no jitter.
- Redriving without confirming idempotency.
- Retrying a `400` or a `403`.
- No metadata on parked messages.
- Treating the DLQ as a place where problems disappear.
- No awareness that a redrive is a replay with ordering consequences.
- No numbers: no ladder horizon, no attempt budget, no redrive rate.

## 17. Knowledge check

**Conceptual**

1. Explain why retrying is only appropriate for *transient* failures, and give the correct action for the permanent and ambiguous classes. Then explain what a consumer that retries all three classes uniformly does to (a) its own attempts budget, (b) an ordered partition, and (c) the on-call engineer's ability to diagnose.
2. Explain head-of-line blocking on an ordered stream, and describe the three resolutions with the guarantee each one gives up. State which you would choose by default and why.
3. A colleague proposes automatic redrive from the DLQ back to the source after one hour, arguing it is self-healing. Identify three distinct ways this is wrong, and describe the configuration that is actually self-healing without the hazards.

**Practical scenario**

4. BookNook pushes shipment events to a partner with a twice-monthly 30-minute maintenance window, occasional 503s during their deploys, and a 400-message/minute rate limit. The consumer runs at 400 events/sec, with a 0.4 % permanent failure rate. Design the complete failure policy: the classification table for the errors you expect, the ladder with each delay justified against a specific outage duration, the total time budget and why it is a business decision, the DLQ schema including every metadata header, the three alarms with thresholds, the rate at which you would redrive 250,000 parked messages, and the ordering implications of that redrive.

**Calculation**

5. A consumer processes 600 msg/sec with 4 % transient failures, a 5-rung ladder (30 s, 2 m, 10 m, 1 h, 6 h), and residual failure halving per attempt. (a) Compute the retry load per rung and the total added load as a percentage. (b) Compute the fraction of messages reaching the DLQ and the daily count. (c) Compute the extra storage the DLQ needs for 14 days at 3 KB per message. (d) If the DLQ depth is 180,000 and you redrive at 60 msg/sec, compute the duration and state whether the redrive rate would violate a 500 msg/min downstream limit.

6. A ladder is 1 s, 2 s, 4 s, 8 s, 16 s, 32 s. (a) Compute the total horizon. (b) State how many messages from a 45-minute dependency outage would reach the DLQ at 200 msg/sec. (c) Compute the ladder you would design instead, with delays and total horizon, given observed outages of 20 s, 10 min, and 45 min. (d) Explain the trade-off you accepted by lengthening the horizon, and the classification rule that makes that trade cheap.

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
  - [x] 9. Consumer groups, partitions, offsets and ordering (L72)
  - [x] 10. At-most-once, at-least-once and exactly-once (L73)
  - [x] 11. Idempotent consumers and deduplication (L74)
  - [x] **12. Dead-letter queues and retry queues (L75) ← you are here**
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

**NEXT LESSON: Phase 4, Topic 13 — Backpressure and load shedding**

---
