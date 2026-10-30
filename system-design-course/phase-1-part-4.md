# Lesson 13 — Synchronous versus Asynchronous Communication

*Phase 1, Topic 13*

## 1. Simple definition

Two programs need to talk. **Synchronous** and **asynchronous** describe *when the caller finds out the answer*.

- **Synchronous communication**: the caller sends a request and **waits for the result of that request before it can continue its own logic**. The answer arrives on the same conversation, in the same breath. `price = catalogue.get_price(isbn)` — the next line of code cannot run until `price` exists.
- **Asynchronous communication**: the caller hands off the work and **continues immediately without the result**. The result, if there is one, arrives later by some other route: a callback, a webhook, a queue message, a status endpoint, a push over an open connection — or never, because the caller genuinely does not care.

Now the distinction that trips up almost everyone, including senior engineers:

> **Synchronous/asynchronous and blocking/non-blocking are two different axes.**

Define them precisely:

- **Synchronous / asynchronous** is about the **logical dependency of the caller on the response**. Does my next step need the answer?
- **Blocking / non-blocking** is about the **thread**. While waiting, is the operating-system thread parked and unusable, or is it free to do other work?

All four combinations exist and all four are used in production:

| | **Blocking** (thread is parked while waiting) | **Non-blocking** (thread does other work while waiting) |
|---|---|---|
| **Synchronous** (caller needs the answer to continue) | Classic Java servlet calling JDBC. Thread sleeps for 12 ms. | `await db.query(...)` in Node.js / Python asyncio / Go. The *logic* waits; the *thread* serves other requests. |
| **Asynchronous** (caller does not need the answer now) | Rare and almost always a bug: publishing to a queue with a blocking `send()` and `acks=all`, so you pay a sync cost for an async design. | Publishing an event to Kafka with a fire-and-forget producer, or writing a row to an outbox table and returning. |

The crucial consequence of that table: **`await` does not make your design asynchronous.** `await payment.charge(...)` is a *synchronous* interaction implemented with *non-blocking* I/O. The user still cannot see the order confirmation until the charge returns. Non-blocking I/O improves **server efficiency** (how many concurrent requests one box can hold — Lesson 14). Asynchronous *design* improves **user-perceived latency and failure isolation**. They are solving different problems, and you usually want both.

Two more terms, defined now because the rest of the lesson leans on them:

- **Coupling**: how much one component's correctness, availability, or latency depends on another's. Synchronous calls create **temporal coupling** — both parties must be alive *at the same instant*.
- **Fire-and-forget**: a send with no reply expected and, in the weakest form, no confirmation that anyone received it.

## 2. Why it exists

Synchronous communication exists because **some answers are prerequisites**. BookNook cannot show an order-confirmation page before knowing whether the card was charged. There is no clever architecture that removes that dependency; it is in the business rule.

Asynchronous communication exists because of four hard problems that synchronous calls cannot solve:

1. **Latency accumulation.** If checkout must synchronously call payments (400 ms), inventory (30 ms), invoicing (250 ms), email (600 ms to an SMTP provider), analytics (80 ms), and recommendations (120 ms), the user waits 1,480 ms — even though they only care about two of those six.
2. **Failure amplification.** In a synchronous chain, *any* dependency being down makes the whole operation fail. If the email provider is down, a synchronous design refuses to take the customer's money. That is absurd: the business would rather take the money and send the email ten minutes late.
3. **Load spikes.** 50,000 people hit "buy" in the first minute of a flash sale. Synchronously, every one of those requests needs a downstream worker *right now*. Asynchronously, the queue absorbs the spike and workers drain it at their own sustainable rate. This is called **load levelling** or **buffering**.
4. **Long-running work.** Generating a 40-page sales report takes 90 seconds. No HTTP client, load balancer, or proxy should be asked to hold a connection open that long — most will time out at 30–60 seconds anyway.

The historical arc is worth knowing: early web apps were purely synchronous request/response because HTTP was designed that way and a single server did everything. As systems grew into many services, the multiplicative failure and latency problems (quantified in Section 9) became unbearable, and asynchronous messaging moved from "advanced technique" to "default for anything not on the user's critical path".

## 3. Real-world analogy

**A phone call versus a text message.**

- **Phone call (synchronous).** You dial, you wait while it rings, and neither of you can do anything else until the conversation ends. You get an answer *now*. But: the other person must be awake, free, and in signal range *at that exact moment*. If they're not, the interaction fails entirely.
- **Text message (asynchronous).** You send it and immediately put the phone in your pocket. The recipient reads it when convenient. The message survives their phone being off. You don't know when you'll get a reply, or if you will.

Extend it to BookNook's checkout:

- "Is this card good for ₹3,200?" → **phone call to the bank**. You cannot ship the book on a maybe.
- "Please post this invoice to the customer" → **drop it in the outbox**. The postal service will handle it; you go back to work.

**Where the analogy breaks down**

| Phone call / text | Real synchronous / asynchronous systems |
|---|---|
| A dropped call is obvious to both people | A dropped HTTP request is **ambiguous**: the server may have completed the charge and died before replying. The caller cannot distinguish "never happened" from "happened, reply lost". This is why Lesson 17 (idempotency and retries) exists. |
| A text is delivered once | A queue typically guarantees **at-least-once** delivery, so the same "send confirmation email" message may be processed twice. Consumers must be idempotent or customers get two emails. |
| Texts arrive in the order you sent them | Many message systems only guarantee ordering **within a partition/key**, not globally. Event 2 can be processed before event 1 unless you deliberately key them together. |
| You can always see "Delivered" / "Read" | Fire-and-forget often has **no acknowledgement at all**. You find out it failed from a metric, or from a customer complaint. |
| Nobody's inbox fills up and explodes | Queues have **finite depth and lag**. If producers outpace consumers for long enough, you get unbounded backlog, memory pressure, and eventually dropped messages — a distinct, async-only failure mode. |
| A call either connects or doesn't | A synchronous call can also hang forever, consuming a thread and a connection slot until a timeout you had better have configured. |

The last two rows are the honest cost of asynchrony: you do not remove failure, you **move it from the request path to a background system that nobody is watching unless you instrument it.**

## 4. Detailed technical explanation

### The seven interaction patterns

These are the building blocks. Everything else is a combination of them.

**1. Request–response (synchronous).** Client sends, server replies on the same connection, client blocks logically until it has the reply. HTTP `GET /books/123`, a gRPC unary call (Lesson 11), a SQL query. Simple, debuggable, and the right default for reads.

**2. Fire-and-forget (asynchronous).** Sender emits a message and does not wait for a result. UDP metrics to StatsD, a log line, a Kafka producer with `acks=0`. Cheapest possible send. No delivery guarantee — appropriate only when losing a message is acceptable.

**3. Callback / webhook (asynchronous with eventual reply).** The caller submits work plus a **return address** (a URL). The callee calls *that URL* later with the result. The caller becomes a server for the duration. This is how Stripe tells you a payment succeeded, how GitHub tells you a push happened. Critical details: webhooks must be **authenticated** (signature headers) because the endpoint is public, must be **idempotent** because providers retry, and must be **fast** (reply 200 immediately, do the work off the request path).

**4. Short polling (client-driven).** The client repeatedly asks "done yet?" on a timer. `GET /jobs/abc` every 2 seconds. Trivial to implement with plain HTTP; infrastructure-friendly; works through every proxy on earth. But it wastes requests (most return "not yet") and adds up to half the poll interval to perceived latency.

**5. Long polling (client-driven, server-held).** The client sends a request and the **server deliberately does not answer** until it has news or a timeout (typically 20–30 s) expires. The client immediately re-requests. Near-real-time delivery with ordinary HTTP, at the cost of holding a connection per waiting client.

**6. Server-Sent Events (SSE) (server-push, one-way).** One long-lived HTTP response that stays open; the server writes `data:` lines whenever it has something. Built-in auto-reconnect with `Last-Event-ID`. Text only, server→client only. Perfect for order-status tickers, live dashboards, and streaming LLM tokens.

**7. WebSockets (server-push, bidirectional).** An HTTP request is **upgraded** into a persistent, full-duplex, binary-capable TCP channel. Either side can send any time. Required for chat, collaborative editing, multiplayer games, live trading. Cost: you now operate stateful long-lived connections (Lesson 2), which complicates load balancing, deploys, and scaling.

### Comparison table

Assume BookNook with 100,000 concurrently-interested users and a 10-second poll interval where polling applies. "Overhead/message" counts bytes that are not payload.

| Pattern | Direction | Latency to deliver news | Connections held | Server cost | Overhead per message | Works through old proxies | Typical use |
|---|---|---|---|---|---|---|---|
| Request–response | C→S, S→C | Immediate (it *is* the answer) | 1 per in-flight request | Low | ~500–800 B of HTTP headers | Yes | Reads, writes needing an answer |
| Fire-and-forget | C→S | N/A (no answer) | 0 after send | Lowest | Minimal | Yes | Metrics, logs, analytics events |
| Webhook / callback | S→C (inverted) | Seconds (provider-controlled) | 0 while waiting | Low for caller; receiver needs a public endpoint | Full HTTP request each time | Yes | Payment results, CI notifications |
| Short polling | C→S | 0 to *interval* (avg *interval*/2 → 5 s) | 1 briefly, every 10 s | **High**: 10,000 wasted RPS at 100k users | ~800 B × every poll | Yes | Slow-changing status, small user counts |
| Long polling | C→S held | ~0 (pushed on arrival) | **1 per waiting user (100,000)** | Medium-high: connection + thread/goroutine each | ~800 B per *event*, not per poll | Mostly | Near-real-time on legacy infra |
| SSE | S→C | ~0 | 1 per user (100,000) | Medium: cheap per idle connection on async servers | ~10–50 B per event after setup | Usually (needs no buffering proxies) | Status feeds, dashboards, token streaming |
| WebSocket | Both | ~0 | 1 per user (100,000) | Medium, plus stateful-routing complexity | ~2–14 B frame header | Needs `Upgrade` support | Chat, collaboration, games |

Rule of thumb: **poll when updates are rare and users are few; push when updates are frequent or users are many.** Section 9 turns that into arithmetic.

### Which actions MUST be synchronous

An action must be synchronous when **the user's next decision, or the system's correctness, depends on the outcome**, and when a later failure would be unrecoverable or unacceptable.

| Action | Sync or async | Why |
|---|---|---|
| **Payment authorisation** | **Synchronous** | You must not show "Order confirmed" on a card that was declined. Reversing a shipped order costs more than the 400 ms wait. |
| **Stock reservation / decrement** | **Synchronous** | Two customers must not both be told they got the last copy. This needs an atomic, immediately-visible decision. |
| **Authentication / authorisation** | **Synchronous** | You cannot serve the page before knowing who is allowed to see it. |
| **Price and total calculation** | **Synchronous** | It is rendered on the page the user is about to agree to. |
| **Address and coupon validation** | **Synchronous** | The user must be able to fix the error while they are still on the form. |
| Confirmation email / SMS | **Asynchronous** | 10 seconds late is invisible to the customer. SMTP being down must not block revenue. |
| Invoice PDF generation | **Asynchronous** | Takes 1–3 s, nobody reads it in the same second, and it is reproducible from the order record. |
| Search index update | **Asynchronous** | The catalogue being 5 s stale is harmless. Indexing is bursty and slow. |
| Recommendation model update | **Asynchronous** | Statistical, best-effort, and nobody notices a 10-minute delay. |
| Analytics / clickstream | **Asynchronous (fire-and-forget)** | Losing 0.1 % of events changes no decision. |
| Warehouse/shipping notification | **Asynchronous** | The warehouse picks orders in batches anyway. |
| Loyalty points accrual | **Asynchronous** | Eventually consistent is fine, as long as it is eventually *correct* (retry until success). |

The design heuristic: **put on the synchronous path only what the user must see to proceed safely.** Everything else goes behind a queue. For BookNook this collapses a 1,480 ms checkout into roughly 450 ms.

### Latency coupling and failure amplification

In a synchronous chain A → B → C → D, three bad things compose:

1. **Latencies add.** Total = sum of every hop, plus network round trips (Lesson 14).
2. **Availabilities multiply.** If each of six services is independently up 99.9 % of the time, the chain is up 0.999⁶ = 99.40 % — six times worse than any individual part.
3. **Tails amplify.** If each service exceeds 200 ms for 1 % of calls, the probability that *at least one* hop in six is slow is 1 − 0.99⁶ = 5.85 %. The chain's p99 is governed by its slowest component's p99.9, not its p99. (Lesson 16 formalises this.)

There is a fourth, nastier effect: **backpressure propagation.** If D slows from 20 ms to 2,000 ms, C's threads fill up waiting on D, so C stops answering B, so B's threads fill, so A's threads fill — and now the *entire* system is down because of one slow leaf. This cascade is why timeouts (Lesson 17), bulkheads, and circuit breakers (Phase 4, Topic 14) exist, and it is the strongest practical argument for moving work off the synchronous path.

### The 202 Accepted pattern

HTTP's built-in answer to "this will take a while". The contract:

1. Client `POST`s the work.
2. Server **validates synchronously** (so bad requests still fail fast with `400`), persists a job record, enqueues it, and replies `202 Accepted` with a `Location:` header pointing at a status resource, and often `Retry-After:` telling the client how long to wait.
3. Client polls the status resource (or subscribes via SSE/webhook).
4. While running, the status resource returns `200` with `{"status": "processing", "progress": 0.4}`.
5. When finished, the status resource returns `303 See Other` with `Location:` pointing at the finished artefact, or `200` with the result inline.
6. On failure it returns a terminal `{"status": "failed", "error": {...}}` — **a failed job is still a successful status request**; don't return `500` for "the job failed", that conflates transport failure with business failure.

Three rules that make this pattern safe: the job ID must come from an **idempotency key** supplied by the client so retried submissions don't create duplicate jobs; the status endpoint must be cheap (it will be hit a lot); and jobs need a **terminal timeout** so a stuck job eventually reports `failed` rather than polling forever.

Forward references: the queue sitting behind all of this is the subject of **Phase 4, Topic 5 (message queues and brokers)**, and the problem of keeping a multi-step asynchronous business process correct — including compensating actions when step 4 of 5 fails — is **Phase 4, Topic 21 (the Saga pattern)**.

## 5. Request / data flow

A BookNook customer clicks "Place order". Follow it end to end, noting exactly where the two styles switch.

1. Browser sends `POST /api/orders` over the existing HTTPS connection, with body `{"cart_id": "c_88", "address_id": "a_12", "payment_method": "pm_9f"}` and header `Idempotency-Key: 3f1c-...`.
2. App server accepts the connection and a worker picks it up.
3. **Synchronous:** server authenticates the session cookie. If invalid → `401`, stop.
4. **Synchronous:** server reads the cart and recomputes prices from the database. Never trusts client totals (Lesson 1). ~8 ms.
5. **Synchronous:** server checks the idempotency key. If this key already produced an order, return that order immediately — the user double-clicked or the network retried.
6. **Synchronous:** server reserves stock inside a database transaction: `UPDATE books SET stock = stock - 2 WHERE isbn = $1 AND stock >= 2`. If 0 rows updated → `409 Conflict`, "out of stock". ~12 ms.
7. **Synchronous:** server calls the payment provider: `POST /charges` with the amount it computed, the idempotency key, and a **2.5-second timeout**. ~400 ms typical. This hop is the dominant cost and it cannot be removed.
8. If the charge is declined → roll back the stock reservation, return `402 Payment Required` with a human-readable reason.
9. **Synchronous:** server writes the order row with `status = 'confirmed'` and, **in the same database transaction**, inserts rows into an `outbox` table: `order.confirmed` event. (Same transaction = the event cannot be lost if the process dies, and cannot be published if the order rolls back. This is the *transactional outbox*, revisited in Phase 4.)
10. **Synchronous:** server returns `201 Created` with the order ID and a `Location: /orders/o_7781`. **Total ≈ 450 ms. The user is done.**
11. **Asynchronous, after the response:** a relay process reads unpublished outbox rows and publishes `order.confirmed` to the message broker. Typical lag 50–500 ms.
12. The broker fans the single event out to four independent consumer groups.
13. **Email consumer** renders the confirmation and calls the email provider. If the provider 503s, the message is retried with exponential backoff; after N attempts it goes to a **dead-letter queue** for human inspection.
14. **Invoice consumer** generates a PDF (~1.5 s), stores it in object storage, and updates the order row with the URL.
15. **Search consumer** decrements the indexed stock count so the catalogue page stops showing "in stock".
16. **Analytics consumer** appends a purchase event to the warehouse for the recommendation model.
17. Meanwhile the browser has already rendered the confirmation page from step 10. It opens an SSE stream `GET /orders/o_7781/events` and, 1.8 s later, receives `invoice_ready` and renders a "Download invoice" button without a page reload.
18. If any consumer is still failing after 30 minutes, an alert on **consumer lag** pages an engineer. The customer has their book; the business fixes the plumbing.

The structural point: **steps 3–10 are a synchronous spine the user waits on; steps 11–16 are an asynchronous fan-out the user never waits on.** Pulling email (600 ms), invoicing (1,500 ms), indexing (200 ms), and analytics (80 ms) off the spine removed ~2.4 s of user-visible latency *and* four ways checkout could fail.

## 6. ASCII diagram

```
SYNCHRONOUS CHAIN — latency adds, availability multiplies, failures cascade

 Browser          API            Payments        Email svc      Invoice svc
   │   POST /orders │                 │               │               │
   ├───────────────▶│   charge        │               │               │
   │   (waiting)    ├────────────────▶│               │               │
   │   (waiting)    │◀──── 400 ms ────┤               │               │
   │   (waiting)    ├─────── send mail ──────────────▶│               │
   │   (waiting)    │◀────── 600 ms ─────────────────┤               │
   │   (waiting)    ├─────── render invoice ────────────────────────▶│
   │   (waiting)    │◀────── 1500 ms ───────────────────────────────┤
   │◀── 201 after 2,540 ms ───┤       │               │               │
                                      ▲               ▲               ▲
                       any one of these being DOWN fails the whole order
                       availability = 0.999 x 0.999 x 0.999 = 99.70%


ASYNCHRONOUS FAN-OUT — user waits only for what they need

 Browser          API           Payments      │  BROKER  │   consumers
   │  POST /orders  │               │          │          │
   ├───────────────▶│   charge      │          │          │
   │                ├──────────────▶│          │          │
   │                │◀── 400 ms ────┤          │          │
   │                │  write order + outbox    │          │
   │◀─ 201 after 450 ms ─┤                     │          │
   │                     │ publish order.confirmed        │
   │                     ├───────────────────▶│ ─────────┼──▶ Email worker
   │                     │                    │ ─────────┼──▶ Invoice worker
   │  SSE /orders/o_7781/events                │ ─────────┼──▶ Search indexer
   ├────────────────────────────────────────── │ ─────────┼──▶ Analytics
   │◀── event: invoice_ready (t = 1.9 s) ──────┘          │
                      queue absorbs spikes; a down consumer
                      delays work, it does not reject orders


202 ACCEPTED + STATUS POLLING

 Client                         API                        Worker
   │ POST /reports                │                           │
   ├─────────────────────────────▶│ validate, persist, enqueue │
   │◀─ 202 Accepted               │──────────────────────────▶│
   │   Location: /reports/r_55    │                           │ (90 s of work)
   │   Retry-After: 5             │                           │
   │ GET /reports/r_55  (t=5s)    │                           │
   ├─────────────────────────────▶│ 200 {"status":"processing","progress":0.1}
   │ GET /reports/r_55  (t=10s)   │                           │
   ├─────────────────────────────▶│ 200 {"status":"processing","progress":0.3}
   │           ...                │                           │
   │ GET /reports/r_55  (t=95s)   │◀──────── done ────────────┤
   ├─────────────────────────────▶│ 303 See Other
   │                              │ Location: /files/r_55.pdf
```

## 7. Detailed example

### BookNook: splitting checkout

Before the split, BookNook's `POST /api/orders` called six services synchronously. Measured p50 contributions:

| Step | p50 | On the user's critical path? |
|---|---|---|
| Auth + cart recompute | 20 ms | Yes — must know who and what |
| Stock reservation | 30 ms | **Yes — correctness** |
| Payment authorisation | 400 ms | **Yes — correctness and money** |
| Confirmation email | 600 ms | No |
| Invoice PDF | 1,500 ms | No |
| Search reindex | 200 ms | No |
| Recommendation update | 120 ms | No |
| **Total** | **2,870 ms** | |

After moving the bottom four behind the broker, the synchronous path is **450 ms** — an 84 % reduction — and checkout now survives the email provider, the PDF renderer, the search cluster, and the analytics pipeline all being down simultaneously.

Three implementation details that make it actually work:

- **Transactional outbox.** The event is written in the *same* transaction as the order. Without this, there is a window where the order commits and the process dies before publishing: the customer is charged and never emailed, with no record that an email was owed.
- **Idempotent consumers.** The broker guarantees at-least-once delivery, so the email worker records `(order_id, 'confirmation')` in a `sent_notifications` table with a unique constraint before sending. Redelivery becomes a no-op instead of a second email.
- **Dead-letter queue + alerting on lag.** Asynchrony hides failure. The only defence is a metric: `consumer_lag_seconds` per consumer group, alerting above a threshold (say 5 minutes), plus a DLQ with a dashboard. "We shipped async and nobody watched the queue" is a real and common outage.

A reservation subtlety worth internalising: stock decrement is synchronous **because it is a correctness constraint between concurrent users**, not because it is slow. Latency is not the criterion; *dependency of the decision* is.

### Contrasting example: an airline check-in kiosk

Different domain, same axis, different answers.

- **Seat assignment: synchronous, and strictly so.** Two passengers cannot be given 14A. The kiosk waits on the central reservation system even though it may take 900 ms, because an "eventually consistent" seat map produces two people standing in the same aisle.
- **Boarding-pass printing: synchronous but local.** The kiosk renders and prints locally from the data it already holds — no network dependency at all, because a printer jam at the gate must not be caused by a WAN hiccup.
- **Baggage-tag telemetry, loyalty miles, marketing emails: asynchronous**, buffered on the kiosk and uploaded in batches. If the link to headquarters is down for 20 minutes, passengers still check in; the data catches up.
- **Security watch-list check: synchronous and failure-closed.** This is the interesting one. Unlike BookNook's email, you cannot "proceed optimistically and fix later". When the dependency is unavailable, the correct behaviour is to **refuse to proceed**, not to degrade. Asynchrony is not always the safer default — it depends on whether a wrong outcome can be compensated afterwards.

That last point is the real decision rule, and it is the bridge to sagas (Phase 4, Topic 21): **make it asynchronous when a late or failed outcome can be compensated; keep it synchronous when it cannot.**

## 8. Code or configuration example

This code **illustrates the sync/async split and the 202 pattern. It is not production-ready**: it has no auth, no real broker, no retry policy, no connection pooling, no observability, and its in-memory job store dies with the process.

```python
# FastAPI. `async def` here means NON-BLOCKING I/O (the thread is freed while
# awaiting). It does NOT by itself make any interaction asynchronous in the
# design sense -- that is decided by WHERE we await, not by the keyword.
from fastapi import FastAPI, BackgroundTasks, Header, HTTPException, Response
import asyncio, uuid, time

app = FastAPI()

JOBS: dict[str, dict] = {}          # stand-in for a jobs table (would be Postgres)
OUTBOX: list[dict] = []             # stand-in for the transactional outbox table
IDEMPOTENCY: dict[str, str] = {}    # idempotency key -> order_id (would be a DB index)


@app.post("/api/orders", status_code=201)
async def place_order(body: dict, idempotency_key: str = Header(...)):
    # ---------- SYNCHRONOUS SPINE: everything the user must not proceed without ----------

    # 1. Idempotency FIRST. A retried request must return the ORIGINAL order,
    #    never charge a second time. (Lesson 17 covers this properly.)
    if idempotency_key in IDEMPOTENCY:
        return {"order_id": IDEMPOTENCY[idempotency_key], "replayed": True}

    # 2. Recompute money server-side. The client sent intent only (Lesson 1).
    total_paise = price_cart(body["cart_id"])          # ~8 ms, trusted source

    # 3. Stock reservation is synchronous because it is a CORRECTNESS constraint
    #    between concurrent users, not because it is slow. Conditional UPDATE so
    #    two buyers cannot both win the last copy.
    if not reserve_stock(body["cart_id"]):             # ~12 ms
        raise HTTPException(status_code=409, detail="out of stock")

    # 4. Payment is synchronous because the user's next screen depends on it and
    #    a failure here cannot be compensated cheaply. Timeout is MANDATORY --
    #    without it one slow provider exhausts every worker (backpressure cascade).
    try:
        charge = await asyncio.wait_for(
            charge_card(body["payment_method"], total_paise, idempotency_key),
            timeout=2.5,                               # seconds; see Lesson 17
        )
    except asyncio.TimeoutError:
        release_stock(body["cart_id"])                 # compensate the reservation
        raise HTTPException(status_code=504, detail="payment timed out, not charged")

    if not charge["approved"]:
        release_stock(body["cart_id"])
        raise HTTPException(status_code=402, detail=charge["decline_reason"])

    # 5. Order row + outbox event written in ONE transaction (pseudo-code here).
    #    Same transaction => the event cannot be lost, and cannot be published
    #    for an order that rolled back.
    order_id = "o_" + uuid.uuid4().hex[:8]
    OUTBOX.append({"type": "order.confirmed", "order_id": order_id,
                   "total_paise": total_paise, "published": False})
    IDEMPOTENCY[idempotency_key] = order_id

    # 6. Return now. ~450 ms total. Email, invoice, indexing, analytics are NOT here.
    return {"order_id": order_id, "status": "confirmed"}


# ---------- ASYNCHRONOUS TAIL: a relay drains the outbox into the broker ----------
# In production this is a separate process (or Debezium/CDC), not a background task,
# so that a crashed API process cannot strand events.
async def outbox_relay():
    while True:
        for event in [e for e in OUTBOX if not e["published"]]:
            await broker_publish("orders", event)      # at-least-once delivery
            event["published"] = True                  # consumers MUST be idempotent
        await asyncio.sleep(0.1)


# ---------- 202 ACCEPTED + STATUS POLLING, for genuinely long work ----------
@app.post("/reports", status_code=202)
async def create_report(body: dict, response: Response, background: BackgroundTasks):
    # Validate SYNCHRONOUSLY so malformed requests still fail fast with 400.
    if body.get("range_days", 0) > 365:
        raise HTTPException(status_code=400, detail="range_days must be <= 365")

    job_id = "r_" + uuid.uuid4().hex[:6]
    JOBS[job_id] = {"status": "queued", "progress": 0.0, "created": time.time()}

    # Location tells the client WHERE to look; Retry-After tells it HOW OFTEN,
    # which is how the server protects itself from aggressive pollers.
    response.headers["Location"] = f"/reports/{job_id}"
    response.headers["Retry-After"] = "5"

    background.add_task(run_report, job_id)            # real systems enqueue instead
    return {"job_id": job_id, "status": "queued"}


@app.get("/reports/{job_id}")
async def report_status(job_id: str, response: Response):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")

    # A FAILED JOB IS STILL A SUCCESSFUL STATUS REQUEST. Returning 500 here would
    # confuse "the report failed" with "the status API is broken".
    if job["status"] == "done":
        response.status_code = 303                     # See Other
        response.headers["Location"] = f"/files/{job_id}.pdf"
        return {"status": "done", "url": f"/files/{job_id}.pdf"}

    response.headers["Retry-After"] = "5"              # backpressure on the poller
    return {"status": job["status"], "progress": job["progress"]}


async def run_report(job_id: str):
    JOBS[job_id]["status"] = "processing"
    for step in range(1, 11):
        await asyncio.sleep(9)                         # stands in for real work
        JOBS[job_id]["progress"] = step / 10
    JOBS[job_id]["status"] = "done"
```

And the push alternative to polling, so the client learns in ~0 ms instead of up to 5 s:

```python
@app.get("/orders/{order_id}/events")
async def order_events(order_id: str):
    # Server-Sent Events: ONE long-lived HTTP response, server writes when it has news.
    # text/event-stream is the required content type; browsers auto-reconnect on drop.
    async def stream():
        last = None
        while True:
            state = read_order_state(order_id)
            if state != last:                          # only send on CHANGE
                yield f"event: update\ndata: {state}\n\n"   # blank line ends an event
                last = state
            # Comment line keeps idle proxies from closing the connection at 60 s.
            yield ": keepalive\n\n"
            await asyncio.sleep(1)
    return StreamingResponse(stream(), media_type="text/event-stream")
```

```yaml
# Broker-side configuration, illustrative. Every value here is a trade-off,
# not a default to copy.
topics:
  orders:
    partitions: 12            # parallelism ceiling: at most 12 active consumers
    retention_hours: 168      # 7 days of replay if a consumer is broken
    key: order_id             # same order_id -> same partition -> ORDER PRESERVED
consumers:
  email-worker:
    concurrency: 8
    max_retries: 5
    backoff: exponential      # 1s, 2s, 4s, 8s, 16s (Lesson 17)
    dead_letter_topic: orders.email.dlq   # where poison messages go for humans
    alert_if_lag_seconds_gt: 300          # WITHOUT this, async failure is invisible
```

## 9. Numerical example

Every number below is an **assumption, stated explicitly**, and every arithmetic step is shown. Say your assumptions out loud like this in an interview.

### 9.1 Latency of the synchronous chain

**Assumptions** (p50 service times, measured, typical for this kind of system):

```
auth + cart recompute   20 ms
stock reservation       30 ms
payment authorisation  400 ms
confirmation email     600 ms
invoice PDF          1,500 ms
search reindex         200 ms
recommendations        120 ms
```

Fully synchronous, executed in series:

```
Total = 20 + 30 + 400 + 600 + 1500 + 200 + 120
      = 2,870 ms
```

After moving the last four off the critical path:

```
Synchronous total = 20 + 30 + 400 = 450 ms
Removed           = 2,870 - 450   = 2,420 ms
Reduction         = 2,420 / 2,870 = 0.843 -> 84.3 %
```

What if, instead of going async, we had merely **parallelised** the four non-critical calls (still synchronous, but concurrent)?

```
Parallel branch time = max(600, 1500, 200, 120) = 1,500 ms
Total                = 450 + 1,500             = 1,950 ms
```

Better than 2,870 ms, still **4.3× worse than async**, and — critically — it fixes none of the availability problem, because all four services must still be up. Parallelism buys latency; asynchrony buys latency *and* decoupling.

### 9.2 Availability of a synchronous chain

**Assumptions**: six services, each independently available 99.9 % of the time, all required synchronously, failures independent (an optimistic assumption — see Lesson 15 on correlated failure).

```
A_chain = 0.999^6

0.999^2 = 0.998001
0.999^3 = 0.998001 x 0.999 = 0.997002999
0.999^6 = 0.997002999^2    = 0.994014980...

A_chain = 0.99401498  ->  99.401 %
```

Translate to downtime (minutes in a year = 365 x 24 x 60 = 525,600):

```
Unavailability      = 1 - 0.99401498 = 0.00598502
Downtime per year   = 0.00598502 x 525,600 = 3,145.7 minutes
                    = 3,145.7 / 60 = 52.4 hours/year

Single 99.9% service = 0.001 x 525,600 = 525.6 min = 8.76 hours/year
```

**Adding five synchronous dependencies turned 8.8 hours of annual downtime into 52.4 hours** — a 6× degradation — without a single service getting worse. This is the most important number in the lesson.

Now the async version. Only three components are on the critical path (API, stock/DB, payments), each 99.9 %:

```
A_sync_path = 0.999^3 = 0.997003 -> 99.70 %
Downtime    = 0.002997 x 525,600 = 1,575.2 min = 26.3 hours/year
```

Half the downtime of the six-service chain. And a failure in email/invoice/search/analytics now causes *delay*, not *lost revenue*.

**What would it take to get the 6-chain back to 99.9 % overall?** Each service would need availability `a` where:

```
a^6 = 0.999
a   = 0.999^(1/6) = e^(ln(0.999)/6) = e^(-0.0010005/6) = e^(-0.00016675)
a   = 0.99983  -> 99.983 %
```

Each of six services must be roughly **6× more reliable** than the target. That is the quantitative reason "just add another synchronous microservice" is expensive.

### 9.3 Tail-latency amplification

**Assumption**: each of the six services independently exceeds 200 ms on 1 % of calls.

```
P(no hop is slow)       = 0.99^6
0.99^2 = 0.9801
0.99^3 = 0.970299
0.99^6 = 0.970299^2 = 0.941480149

P(at least one is slow) = 1 - 0.941480 = 0.058520 -> 5.85 %
```

So **5.85 % of requests hit a slow hop**, meaning the chain's p95 is already governed by component p99 behaviour. To keep the chain's slow-request rate at 1 %:

```
0.99 = p^6  ->  p = 0.99^(1/6) = e^(ln(0.99)/6) = e^(-0.0100503/6) = e^(-0.00167505)
p = 0.998326  -> each service may be slow on only 0.167 % of calls (its p99.83)
```

Lesson 16 develops this; the takeaway here is that **synchronous fan-out converts rare slowness into common slowness.**

### 9.4 Polling versus long polling versus SSE — cost arithmetic

**Assumptions**: 100,000 users watching order status; a status change happens on average twice per order; a short-poll interval of 5 s; HTTP request + response headers ≈ 800 bytes; an SSE event ≈ 120 bytes; sessions last 10 minutes.

**Short polling, 5-second interval**

```
Polls per user per session = 600 s / 5 s = 120 polls
Total polls                = 100,000 x 120 = 12,000,000 requests
Request rate               = 100,000 users / 5 s = 20,000 requests/second
Bytes                      = 12,000,000 x 800 B = 9.6 GB per 10-minute window
Useful polls               = 2 per user = 200,000 of 12,000,000
Waste                      = 1 - 200,000/12,000,000 = 98.33 %
Average detection delay    = interval / 2 = 2.5 s
```

**Short polling, 30-second interval** (cheaper, slower)

```
Request rate            = 100,000 / 30 = 3,333 requests/second
Bytes                   = 100,000 x 20 polls x 800 B = 1.6 GB
Average detection delay = 15 s
```

6× cheaper, 6× slower. That linear trade is the whole character of polling.

**Long polling, 25-second hold**

```
Requests per user per session = 600 / 25 = 24 (plus 2 that return early with news)
Request rate (new connections) = 100,000 / 25 = 4,000 requests/second
Concurrently held connections  = 100,000 (essentially all of them, all the time)
Bytes                          = 100,000 x 26 x 800 B = 2.08 GB
Average detection delay        = ~0 ms
```

Note the shape change: long polling has **low request rate but maximum connection count**. On a thread-per-request server with 1 MB stacks, 100,000 held connections ≈ 100 GB of RAM — impossible. On an event-loop server at ~10 KB per idle connection, it is ~1 GB — easy. **The concurrency model decides whether long polling is viable** (Lesson 14).

**SSE**

```
Connections            = 100,000 (1 per user, held for 600 s)
Setup cost             = 100,000 x 800 B = 80 MB (once)
Event cost             = 100,000 x 2 events x 120 B = 24 MB
Keepalives (1 per 15s) = 100,000 x 40 x 20 B = 80 MB
Total bytes            = 80 + 24 + 80 = 184 MB
Request rate           = 100,000 / 600 s = 167 new connections/second
Average detection delay = ~0 ms
```

**Summary**

| Pattern | Requests/s | Bytes per 10 min | Held connections | Detection delay |
|---|---|---|---|---|
| Short poll 5 s | 20,000 | 9,600 MB | ~0 | 2.5 s avg |
| Short poll 30 s | 3,333 | 1,600 MB | ~0 | 15 s avg |
| Long poll 25 s | 4,000 | 2,080 MB | 100,000 | ~0 |
| SSE | 167 | 184 MB | 100,000 | ~0 |

**SSE uses 1.9 % of the bandwidth of 5-second polling and delivers news instantly.** The price is 100,000 long-lived connections and the stateful-routing complexity that comes with them. At 500 users rather than 100,000, 5-second polling costs 100 RPS and ~48 MB — and polling is clearly the right answer. **Scale, not elegance, decides this.**

### 9.5 Sizing the asynchronous workers

**Assumptions**: peak 14 orders/second (Lesson 1's BookNook peak); each order produces 4 events; email takes 600 ms, invoice 1,500 ms, indexing 200 ms, analytics 80 ms.

Little's Law, `L = λ × W` (Lesson 14 derives it):

```
Email:     L = 14 x 0.600 = 8.4   -> 9 concurrent workers (round up)
Invoice:   L = 14 x 1.500 = 21.0  -> 21 workers
Indexing:  L = 14 x 0.200 = 2.8   -> 3 workers
Analytics: L = 14 x 0.080 = 1.12  -> 2 workers
```

Now the **flash-sale spike**: 50,000 orders in the first 60 seconds.

```
Spike rate = 50,000 / 60 = 833 orders/second  (59x normal peak)

Synchronously, invoice capacity needed = 833 x 1.5 = 1,250 concurrent renderers.
You do not have 1,250 renderers. Checkout would collapse.

Asynchronously, with 21 invoice workers:
  Service rate = 21 workers / 1.5 s = 14 invoices/second
  Backlog after the 60-second spike = 50,000 - (14 x 60) = 50,000 - 840 = 49,160
  Drain time  = 49,160 / 14 = 3,511 s = 58.5 minutes
```

An hour of invoice lag. Is that acceptable? **State the criterion rather than saying "it depends":** it is acceptable if the business rule is "invoice within 24 hours" and unacceptable if it is "invoice visible on the confirmation page". If unacceptable, scale workers for the spike:

```
Target drain time = 10 minutes = 600 s
Invoices to clear = 50,000
Required rate     = 50,000 / 600 = 83.3 invoices/second
Workers needed    = 83.3 x 1.5 = 125 workers
```

125 autoscaled workers for ten minutes, versus a collapsed checkout. **That is load levelling, quantified.** Note also that the queue must hold 49,160 messages at peak — at ~2 KB each that is ~98 MB of broker storage, trivially fine, but the number should be checked, not assumed.

## 10. Trade-offs

**Synchronous — advantages**
- Simplest possible mental model: call, get answer, handle error. Stack traces are complete.
- Errors are immediate, attributable, and returnable to the user.
- Strong consistency within one request is achievable (one database transaction).
- Debugging is local: one request ID, one log stream, one timeline.
- No extra infrastructure. No broker to run, patch, monitor, and pay for.

**Synchronous — disadvantages**
- Latency adds across hops; the user pays for every dependency.
- Availability multiplies downward (Section 9.2).
- Temporal coupling: both parties must be up *simultaneously*.
- Backpressure cascades: one slow leaf can exhaust threads all the way to the edge.
- Traffic spikes hit every dependency at full force; no buffering.

**Asynchronous — advantages**
- User-perceived latency drops to the genuinely required work.
- Failure isolation: a broken consumer delays work instead of rejecting it.
- Load levelling absorbs spikes at the queue instead of the service.
- Independent scaling: 125 invoice workers without touching the API tier.
- Natural fan-out: one event, N consumers, zero producer changes.

**Asynchronous — disadvantages**
- **Eventual consistency becomes user-visible**: "I ordered but there's no email yet."
- Debugging spans processes and time; you need distributed tracing and correlation IDs.
- At-least-once delivery forces idempotent consumers everywhere.
- Ordering is only guaranteed per partition/key, not globally.
- New failure modes that did not previously exist: queue backlog, poison messages, dead-letter growth, consumer lag.

**Operational costs of going async**
- You now run a broker (or pay for one): capacity, retention, partitions, upgrades, on-call.
- You must build a DLQ triage process — a DLQ nobody reads is a silent data-loss pipe.
- You must alert on **lag**, not just errors; an async system fails by getting slow and quiet.
- Testing is harder: integration tests need the broker, and timing-dependent bugs appear.

**When asynchrony is unnecessary complexity**
- A single service with a single database and no slow dependencies. Adding Kafka to a 14-RPS CRUD app buys you an outage surface and a new thing to learn.
- Work that completes in under ~50 ms. The queue round trip may cost more than the work.
- Operations the user must see confirmed before continuing. You cannot async your way out of "is my card valid?".

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| Synchronous dependency is slow (payments 400 ms → 5 s) | Spinner, then a timeout error; checkout appears broken | p99 latency alert on the API; dependency latency metric rises first | Client timeout fires; stock reservation is released; user retried with the same idempotency key. Circuit breaker opens to stop hammering (Phase 4, Topic 14) |
| Synchronous dependency is down | Immediate `502`/`503`; order rejected outright | Health check + error-rate alert within seconds | Fail fast, show a clear message; if the dependency is non-critical it should never have been synchronous |
| Backpressure cascade (slow leaf exhausts every thread upstream) | **Whole site** unresponsive, not just checkout | Thread-pool saturation / queue-depth metrics; latency climbs everywhere at once | Per-dependency timeouts + bulkheads (separate pools) + circuit breaker; shed load at the edge |
| Response lost after the server committed the order | User sees an error but is charged; may retry and fear a double charge | Reconciliation job comparing payment records to orders; support tickets | Idempotency key makes the retry return the original order (Lesson 17) |
| Broker is unreachable when publishing | **Nothing** — the order succeeded | Outbox rows with `published = false` and rising age | Transactional outbox: the relay retries until the broker returns. No event is lost |
| Consumer crashes mid-processing | Email simply never arrives | Consumer lag alert; DLQ depth; "where is my email" tickets | Message is not acknowledged, so the broker redelivers it to another consumer |
| Consumer processes the same message twice | **Two confirmation emails** | Duplicate-send counter; customer complaints | Unique constraint on `(order_id, notification_type)` makes the second attempt a no-op |
| Consumer is permanently broken (bad deploy, schema change) | Confirmations stop entirely; silent to the user until they notice | `consumer_lag_seconds > 300` alert — **the single most important async alert** | Roll back the consumer; queue retains messages (7-day retention) and the backlog drains |
| Poison message (one event crashes the consumer forever) | That one order's email never sends; worse, it blocks the partition behind it | DLQ depth > 0; repeated retry counts on one offset | Max-retries then route to DLQ so the partition unblocks; human fixes the record |
| Producers outpace consumers indefinitely | Delays grow from seconds to hours, unnoticed | Lag trend, not lag threshold — the derivative matters | Autoscale consumers; if that fails, shed low-value events; ultimately rate-limit producers |
| Out-of-order events (`cancelled` processed before `confirmed`) | Order shows the wrong state; a cancelled order gets shipped | State-machine guard rejects illegal transitions and logs them | Key the topic by `order_id` so one order's events share a partition and stay ordered |
| Webhook endpoint is down when the provider calls | Payment succeeded upstream but BookNook never learns | Provider's failed-delivery dashboard; missing-callback reconciliation job | Provider retries with backoff; additionally run a **reconciliation poll** — never rely on a webhook as the sole path |
| Status job never terminates | Client polls forever; UI spinner is immortal | Job age metric; jobs in `processing` beyond SLA | Terminal timeout marks the job `failed`; client renders a real error |

The pattern across the bottom half of this table: **asynchronous failures are quiet.** Synchronous failures page you because users get errors. Async failures just make things late, and "late" has no error code. Instrumentation is not optional with asynchrony — it is the thing that replaces the error the user would otherwise have seen.

## 12. Alternatives

| Approach | Mechanism | Delivery latency | Who initiates | Server cost at 100k users | Delivery guarantee | Best for | Worst for |
|---|---|---|---|---|---|---|---|
| **Synchronous request–response** | One connection, one answer | Immediate | Client | Low (short-lived) | Exactly the response you got | Reads, correctness-critical writes | Slow or optional work |
| **Fire-and-forget** | Send, don't wait | N/A | Client | Lowest | **None** | Metrics, logs, clickstream | Anything you'd miss |
| **Webhook / callback** | Callee calls your URL | Seconds | Callee | Low; needs public, authenticated endpoint | At-least-once with provider retries | Third-party results (payments, CI) | Clients behind NAT/firewalls |
| **Short polling** | Client asks on a timer | 0 – interval | Client | **Very high** (20,000 RPS) | Client sees latest state | Few clients, rare updates, legacy infra | Many clients or frequent updates |
| **Long polling** | Server holds the request | ~0 | Client | High connection count, low RPS | At-least-once per poll cycle | Near-real-time without WebSockets | Thread-per-request servers |
| **SSE** | One open HTTP response, server writes | ~0 | Server pushes | Medium (cheap per idle conn) | Ordered, with `Last-Event-ID` resume | Status feeds, dashboards, token streams | Binary data; client→server traffic |
| **WebSocket** | Upgraded full-duplex TCP | ~0 | Both | Medium + stateful routing | Ordered per connection; nothing across reconnects | Chat, collaboration, games, trading | Simple one-way updates (overkill) |
| **Message queue (point-to-point)** | Broker, one consumer group wins each message | ms – minutes | Producer | Broker infra | At-least-once (usually) | Work distribution, load levelling | Request/response semantics |
| **Pub/sub event log** | Broker, many independent consumer groups, retained log | ms – minutes | Producer | Broker infra + storage | At-least-once + replay | Fan-out, event-driven architecture | Low-latency user-facing reads |
| **Batch / scheduled job** | Cron reads a table periodically | minutes – hours | Scheduler | Near zero | Whatever your job guarantees | Reports, reconciliation, nightly sync | Anything time-sensitive |
| **202 + status polling** | Async work, synchronous status resource | Bounded by poll interval | Client | Low | Explicit terminal state | Long jobs over plain HTTP | Sub-second interactions |

Note that these are not mutually exclusive. BookNook's production design uses **six of them at once**: synchronous request–response for checkout, pub/sub for fan-out, webhooks inbound from the payment provider, SSE outbound to the browser, fire-and-forget for analytics, and a nightly batch job for reconciliation.

## 13. When to use it

**Use synchronous communication when:**

1. **The user's next action depends on the outcome.** Login, search results, price display, "is this username taken?". There is no UX in which the user proceeds without the answer.
2. **Correctness between concurrent actors is at stake.** Stock decrement, seat assignment, unique-username claim, balance debit. These need an immediately-visible, atomic decision.
3. **The outcome cannot be compensated afterwards.** Irreversible, expensive, or legally binding actions — a fraud check, a watch-list check, a permission check on sensitive data. Failing closed is safer than proceeding optimistically.
4. **The call is fast and the dependency is highly available.** A 2 ms in-datacentre cache read does not deserve a queue.
5. **You need the error to reach the user verbatim.** "Card declined: insufficient funds" is only useful on the checkout page.

**Use asynchronous communication when:**

1. **The work is slow and nobody is watching.** Invoice PDFs, video transcoding, report generation, bulk imports, ML inference for recommendations.
2. **The work is optional to the primary outcome.** Emails, SMS, push notifications, search reindexing, analytics, loyalty points, CRM sync. The order is valid without them.
3. **Traffic is spiky and downstream capacity is fixed.** Flash sales, scheduled drops, end-of-month billing runs. The queue is the shock absorber.
4. **One event has many independent consumers.** `order.confirmed` feeds email, invoicing, search, analytics, warehouse and fraud. Adding a seventh consumer must not require changing the producer.
5. **You need failure isolation between a revenue path and a supporting system.** The email provider's outage must not stop sales.
6. **The work must survive process restarts.** A durable queue outlives the web server; an in-memory background thread does not.

## 14. When *not* to use it

**Do not use asynchronous communication when:**

1. **The user cannot sensibly continue without the result.** An "async login" means showing a page before knowing who the user is. There is no correct version of that.
2. **A wrong outcome cannot be compensated.** If you confirm an order asynchronously and the payment later declines, you must now claw back a shipped book. The compensation is more expensive than the wait.
3. **The work takes less time than the queue round trip.** Enqueue + poll + dequeue is typically 5–50 ms. Wrapping an 8 ms database write in that is pure overhead and extra failure surface.
4. **You have no capacity to operate it.** A broker you do not monitor, with a DLQ nobody reads, is a system that loses data silently. That is strictly worse than a synchronous call that fails loudly.
5. **Strict global ordering is required across unrelated entities.** Most brokers only order within a partition; building global order on top costs you all the throughput you were trying to gain.

**Do not use synchronous communication when:**

1. **The dependency is slow, optional, or flaky.** Each such dependency multiplies your downtime (Section 9.2) in exchange for nothing the user asked for.
2. **The operation exceeds a reasonable HTTP lifetime.** Anything over ~10 s should be `202 Accepted`; proxies, load balancers and browsers will cut you off around 30–60 s anyway.
3. **The spike-to-steady-state ratio is large.** 59× spikes (Section 9.5) cannot be met by provisioning synchronous capacity for the peak.
4. **You are fanning out to many consumers.** Six synchronous calls is six chances to fail and six latencies to pay; one published event is neither.

## 15. Common mistakes

**Beginner mistakes**
- **Believing `async`/`await` makes the design asynchronous.** It makes the *I/O* non-blocking. The user still waits. These are the two independent axes from Section 1.
- **Sending email on the request path.** The single most common cause of a slow checkout, and it makes your revenue depend on an SMTP provider.
- **No timeout on a synchronous call.** The default in many HTTP clients is *infinite*. One slow dependency then consumes every worker and takes down the whole service.
- **Polling every second "to feel real-time".** At 100,000 users that is 100,000 RPS of almost entirely useless traffic (Section 9.4).
- **Treating a `202 Accepted` as "it worked".** It means "I have accepted the request", nothing more. The job can still fail.
- **Returning `500` from a status endpoint when the job failed.** Conflates transport failure with business failure and breaks client retry logic.
- **Assuming queue messages arrive exactly once and in order.** Neither is true by default.
- **Firing the event before the database transaction commits**, so consumers read an order that does not exist yet.

**Experienced-engineer mistakes**
- **Making everything asynchronous.** Eventual consistency becomes a UX problem ("where is my order?"), debugging becomes archaeology, and you have built a distributed system to avoid a 20 ms call.
- **Async without an outbox.** Publishing after commit, in a separate step, creates a lost-event window on crash — rare, silent, and devastating for financial data.
- **Async without idempotent consumers**, then being surprised by duplicate emails, double refunds, or doubly-incremented counters.
- **Alerting on consumer errors but not on consumer lag.** A consumer that processes nothing throws no errors. Lag is the real signal.
- **Using a database table as a queue at scale** without thinking about lock contention, `SKIP LOCKED`, and vacuum pressure. Fine at 10 jobs/s, pathological at 10,000.
- **Relying on a webhook as the only delivery path.** Webhooks get lost. Always pair them with a reconciliation poll.
- **Letting the DLQ become a graveyard.** Thousands of messages nobody will ever replay is silent data loss with extra steps.
- **Using WebSockets for one-way server→client updates** where SSE would do, then inheriting stateful connection routing, sticky sessions, and painful deploys for no benefit.
- **Ignoring the saga problem.** Once checkout spans five asynchronous steps, "what happens if step 4 fails after step 3 committed?" is a design question with a real answer (compensating transactions — Phase 4, Topic 21), not an edge case to hope about.
- **Forgetting ordering requirements when choosing partition keys**, then discovering that `order.cancelled` overtook `order.confirmed` in production.

## 16. Interview perspective

This topic appears in nearly every design interview, usually without being named. Here is how to use it:

- **Say the sentence.** "Let me split this into what's on the synchronous critical path and what can be asynchronous." Then classify every operation out loud. This single move restructures a messy answer into a clear one.
- **Justify synchronous choices by *consequence*, not by speed.** "Stock decrement is synchronous because two buyers must not both win the last copy" is a correctness argument and lands far better than "it's fast".
- **Do the availability multiplication on the whiteboard.** "Six synchronous services at three nines each gives 99.4 %, which is 52 hours a year. Pulling four of them off the path gets me back to 99.7 %." Very few candidates produce this number, and it is decisive.
- **Always mention timeouts, retries, and idempotency in the same breath.** Any candidate who adds a retry without mentioning idempotency has just invented duplicate orders.
- **Know the polling-cost arithmetic.** "100k users polling every 5 s is 20,000 RPS; SSE is 167 connections/s and 2 % of the bytes." That is a specific, checkable number.
- **Volunteer the outbox pattern** when you say "publish an event". It shows you have operated one of these, not just read about it.
- **Name the monitoring.** "I'd alert on consumer lag over five minutes and on DLQ depth." Interviewers hear operational maturity.
- **Scale your answer to the numbers.** If peak is 14 RPS, say "a background thread and a database-backed job table is sufficient here; I'd reach for Kafka when fan-out or replay demands it."

**Red flags interviewers watch for:** saying "we'll make it async" without specifying what the user sees in the meantime; adding a message broker to a system with no slow or optional work; retries with no idempotency key; no timeouts on outbound calls; claiming `await` makes a design asynchronous; being unable to name a single operation that *must* stay synchronous; putting payment confirmation behind a queue; proposing WebSockets for a one-way status feed; never mentioning what happens when the queue backs up; and treating eventual consistency as invisible to users when it is usually the most visible thing about an async design.

## 17. Knowledge check

Answer these before moving on. Write them down — one or two lines each is enough.

**Conceptual**

1. A colleague says: "I converted all our handlers to `async def` and added `await` to every database call, so our architecture is now asynchronous and our checkout will be faster for users." Identify precisely what they have actually changed, what they have *not* changed, and name one metric that will improve and one user-facing metric that will not.
2. BookNook's checkout synchronously calls six services. For each of the following, state whether it must be synchronous or can be asynchronous, and give the *reason category* (user's next decision / correctness between concurrent users / cannot be compensated / none of these): (a) coupon validation, (b) loyalty-points accrual, (c) fraud score check that can block the order, (d) warehouse pick-list creation, (e) gift-card balance debit.
3. Explain why a webhook should never be the only mechanism by which your system learns that a payment succeeded, and describe the second mechanism you would add and how often you would run it.

**Practical scenario**

4. BookNook adds a "bulk import" feature: a seller uploads a CSV of up to 50,000 books, which takes 3–8 minutes to validate and index. The first implementation is a synchronous `POST /imports` that returns the result. It works in development and fails in production. Describe (a) exactly which components will cut the request off and roughly when, (b) the full `202 Accepted` redesign including every HTTP status code and header you would use at each step, (c) what happens if the seller submits the same file twice, and (d) one failure this redesign still does not solve.

**Calculation**

5. BookNook's order pipeline has these synchronous dependencies, each independently available 99.95 % of the time: API gateway, auth service, cart service, pricing service, inventory service, payment service, order service, and notification service (8 total).
   (a) What is the end-to-end availability, and how many minutes of downtime per year does that represent? Show each multiplication step.
   (b) You move the notification service and the pricing service's "send price-drop alert" call off the critical path, leaving 7 synchronous dependencies. What is the new availability and new annual downtime?
   (c) The business demands 99.9 % end-to-end with 7 synchronous dependencies. What availability must each individual service achieve? Show the root calculation.
   (d) Each service has p99 = 150 ms independently. What percentage of requests will experience at least one hop above 150 ms with 8 services, and with 7? Show both.

Reply with your answers and I'll assess them, correct anything shaky, and give model answers before Lesson 14.

---

**Progress checklist**

Phase 1 — Internet and System-Design Foundations
- [x] 1. Client-server architecture
- [x] 2. Stateful versus stateless servers
- [x] 3. IP addresses, ports, sockets, domain names
- [x] 4. DNS resolution and DNS caching
- [x] 5. HTTP requests and responses
- [x] 6. HTTP methods, headers, cookies, sessions, status codes
- [x] 7. HTTPS and TLS
- [x] 8. TCP versus UDP
- [x] 9. HTTP/1.1 vs HTTP/2 vs HTTP/3
- [x] 10. REST APIs
- [x] 11. RPC and gRPC
- [x] 12. GraphQL
- [x] 13. Synchronous versus asynchronous communication
- [ ] 14. Latency, throughput, bandwidth, concurrency
- [ ] 15. Availability, reliability, durability, fault tolerance
- [ ] 16. Average latency vs p50/p95/p99, tail latency
- [ ] 17. Idempotency, retries, timeouts, exponential backoff

Phases 2–5: not started.

**NEXT LESSON: Phase 1, Topic 14 — Latency, Throughput, Bandwidth, and Concurrency**

---

# Lesson 14 — Latency, Throughput, Bandwidth, and Concurrency

*Phase 1, Topic 14*

## 1. Simple definition

Four words that people use interchangeably and that mean completely different things. Precise definitions first:

- **Latency** is **the time between cause and effect for a single operation**. You pressed Enter at t=0; the page appeared at t=380 ms; the latency was 380 ms. Unit: time (ms, µs, ns). Always ask "latency of *what*, measured *where*" — client-perceived latency, server processing latency, and database query latency are three different numbers for the same click.
- **Throughput** is **the number of operations a system completes per unit of time**. 4,000 requests per second. 120 orders per minute. 50,000 rows indexed per second. Unit: operations ÷ time (RPS, QPS, TPS, ops/s).
- **Bandwidth** is **the maximum rate at which data can be transferred over a link**. 1 Gbps, 100 Mbps. It is throughput measured in *bits*, and it is a **capacity** (a ceiling), not an observation. Note the unit trap: network bandwidth is in **bits** per second (Mbps), storage and file sizes are in **bytes** (MB). 1 byte = 8 bits, so 100 Mbps = 12.5 MB/s.
- **Concurrency** is **the number of operations in progress at the same time**, i.e. started but not yet finished. It is a *count*, not a rate and not a duration. 250 requests in flight.

Two more terms you need immediately:

- **Parallelism** is **the number of operations physically executing simultaneously**, which is bounded by hardware (cores, disks, network cards). Rob Pike's formulation is the clearest: *concurrency is about dealing with many things at once; parallelism is about doing many things at once.* A single-core machine can have 1,000 concurrent requests and exactly 1-way parallelism.
- **Utilisation (ρ)** is **the fraction of a resource's capacity currently in use**: arrival rate ÷ service capacity. 0.8 means the server is busy 80 % of the time. Section 4 shows why ρ is the most dangerous number in your dashboard.

**The distinction that matters most: latency and throughput are not inverses.**

It is tempting to think "if each request takes 20 ms, throughput is 50 per second". That is only true for a system with **concurrency 1**. Add concurrency and the relationship breaks:

| System | Latency per request | Concurrency | Throughput |
|---|---|---|---|
| One worker, 20 ms each | 20 ms | 1 | 50 req/s |
| 100 workers, 20 ms each | 20 ms | 100 | 5,000 req/s |
| 100 workers, 200 ms each | **200 ms (10× worse)** | 100 | 500 req/s |
| 1,000 workers, 2,000 ms each | **2,000 ms (100× worse)** | 1,000 | 500 req/s |

Rows 3 and 4 have **identical throughput and 10× different latency**. A system can have excellent throughput and terrible latency (a batch pipeline), or excellent latency and terrible throughput (a single fast server). Optimising one frequently harms the other — batching, for instance, raises throughput and raises latency at the same time.

Analogy to lock this in: a **pipe**. Latency is how long one water molecule takes to travel its length. Bandwidth is how wide the pipe is. Throughput is how much water actually comes out per second (which may be less than bandwidth if the tap upstream is half closed). Concurrency is how many molecules are inside the pipe right now. Making the pipe wider does not make any molecule arrive sooner.

## 2. Why it exists

These four concepts exist because **engineers must be able to predict and diagnose system behaviour with arithmetic instead of intuition**, and because intuition in this domain is reliably wrong.

Four concrete problems they solve:

1. **Capacity planning without guessing.** "How many servers do I need for Black Friday?" is answerable: peak arrival rate × per-request service time = concurrency required; divide by concurrency per server. No vendor, no vibes.
2. **Diagnosing the right bottleneck.** A page takes 3 seconds. Is it bandwidth (12 MB of images), latency (40 serial round trips), concurrency (a 20-connection pool with 200 waiting), or CPU? Each has a different fix and three of them are wasted money.
3. **Knowing what optimisation cannot do.** You can buy more bandwidth tomorrow. You *cannot* buy a shorter round trip between Mumbai and Virginia, because the limit is the speed of light in glass. Knowing which constraints are physical and which are financial prevents years of wasted effort.
4. **Explaining why systems fall off a cliff.** Everything is fine at 80 % utilisation and catastrophic at 95 %. That is not a bug; it is queueing theory, and it is predictable to within a factor of two with a formula you can do on paper.

The historical reason this became *essential* knowledge: in the 1990s a web request was one server, one disk, one page. Today a single page load may involve 5 DNS lookups, 3 TLS handshakes, 40 HTTP requests, 12 backend services, 6 databases, and 2 continents. Latency now *composes*, and composition is where intuition fails hardest.

## 3. Real-world analogy

**A motorway.**

- **Latency** = how long *your* car takes to drive from Mumbai to Pune. Depends on distance and speed, not on how many lanes exist.
- **Bandwidth** = the number of lanes. The maximum cars-per-hour the road *can* carry.
- **Throughput** = the cars per hour actually arriving in Pune. Limited by lanes, but also by the toll booth, the roadworks, and the slowest vehicle.
- **Concurrency** = how many cars are on the motorway right now.
- **Parallelism** = how many cars can be physically side by side — the lane count again, but now viewed as simultaneous execution.
- **Utilisation** = what fraction of road capacity is occupied. And here is the vital, counter-intuitive part everyone has experienced: at 50 % occupancy you drive at full speed. At 85 % you are slowing. At 95 % you are in stop-start traffic and your journey takes three times as long — **even though the road has not got shorter and no lane has closed.** Latency explodes near full utilisation. That is Section 4's queueing theory, and you already know it in your body.

Toll booths capture the rest: adding a second toll booth (parallelism) doubles throughput but does not make any individual transaction faster. Making each transaction faster (reducing service time) improves *both* latency and throughput — which is why reducing work beats adding machines.

**Where the analogy breaks down**

| Motorway | Real computer systems |
|---|---|
| Lanes are physically fixed; adding one takes years | Bandwidth is purchasable in minutes (change an instance type) — but **round-trip time is not purchasable at all**, because it is bounded by physics |
| Every car travels at roughly the same speed | Request service times vary enormously; a single 10-second query among 10 ms queries can block an entire worker pool (head-of-line blocking) |
| A car that enters will eventually exit | Requests **time out and are abandoned**, wasting all work done so far — and the client often retries, adding load exactly when you are overloaded |
| Traffic jams clear when cars leave | Overload can be **self-sustaining**: retries + timeouts create more load than the original traffic (a retry storm / metastable failure) |
| Cars are independent | Requests contend for shared locks, connections and caches; one request can slow every other (the contention term in Section 4's scalability law) |
| Doubling lanes roughly doubles capacity | Doubling cores gives **less than double** throughput because of serial sections and coordination overhead (Amdahl, USL) |
| The road does not get tired | Caches warm and cool, GC pauses, autovacuum, log rotation — service time is not stationary |

The fourth row is the one to internalise: on a motorway, overload is self-limiting. In a distributed system, **overload is self-amplifying**, because clients retry.

## 4. Detailed technical explanation

### 4.1 Little's Law

The single most useful formula in capacity planning:

> **L = λ × W**
>
> - **L** = average number of items in the system (concurrency)
> - **λ** (lambda) = average arrival rate (items per second)
> - **W** = average time an item spends in the system (seconds)

It is astonishingly general: it holds for any stable system regardless of arrival distribution, service distribution, or scheduling policy. The only requirements are that the system is **stable** (nothing grows without bound) and that you measure over a long enough window.

Rearranged, it answers three different questions:

- `L = λW` — **How many requests are in flight?** (Size the thread pool / connection pool.)
- `λ = L / W` — **What throughput can I achieve?** (Given a fixed pool of L workers and service time W.)
- `W = L / λ` — **What is my latency?** (Given observed concurrency and arrival rate — useful for inferring latency from metrics you already have.)

A vital corollary: **if W increases and λ stays the same, L increases.** Concurrency grows. If your pool is bounded (and it must be), the pool fills, and new requests *queue*. That is the mechanical link between "a dependency got slow" and "the whole service fell over".

### 4.2 Latency numbers every programmer should know

Approximate, modern (2024-era) values. These are **order-of-magnitude anchors**, not benchmarks; the ratios matter far more than the digits.

| Operation | Typical latency | In "if 1 ns were 1 second" human terms |
|---|---|---|
| L1 cache reference | ~1 ns | 1 second |
| Branch mispredict | ~3 ns | 3 seconds |
| L2 cache reference | ~4 ns | 4 seconds |
| Mutex lock/unlock (uncontended) | ~17 ns | 17 seconds |
| **Main memory (RAM) reference** | **~100 ns** | 1.7 minutes |
| Compress 1 KB with Snappy | ~2 µs (2,000 ns) | 33 minutes |
| Send 1 KB over 10 Gbps network | ~1 µs | 17 minutes |
| **NVMe SSD random read (4 KB)** | **~20–100 µs** (use 50 µs) | 14 hours |
| Read 1 MB sequentially from RAM | ~20 µs | 5.5 hours |
| **Round trip within the same datacentre** | **~0.5 ms (500 µs)** | 5.8 days |
| Read 1 MB sequentially from NVMe SSD | ~250 µs | 2.9 days |
| **HDD disk seek** | **~8–10 ms** | ~3 months |
| Read 1 MB sequentially from HDD | ~5–10 ms | ~2–3 months |
| Round trip within a region (same metro, cross-AZ) | ~1–2 ms | ~2–3 weeks |
| Round trip Mumbai → Singapore | ~60 ms | ~2 years |
| Round trip Europe → US East | ~80 ms | ~2.5 years |
| **Round trip India → US East (Mumbai → Virginia)** | **~180–220 ms** | ~6 years |
| Round trip via geostationary satellite | ~600 ms | ~19 years |

Three ratios worth memorising because they drive architecture:

1. **RAM is ~500× faster than SSD; SSD is ~100× faster than HDD seek.** This is the entire argument for caching (Phase 2).
2. **A same-datacentre round trip (0.5 ms) is ~5,000× a RAM access (100 ns).** This is why chatty APIs are catastrophic and why you batch (Lessons 10–12).
3. **A cross-continent round trip (200 ms) is ~400× a same-datacentre round trip.** This is the entire argument for CDNs and multi-region deployment (Phase 2, Topic 14; Phase 5).

### 4.3 Serial versus parallel composition

If an operation makes N dependent calls **in series**, latencies add:

```
L_serial = c1 + c2 + ... + cN   (plus one round trip each)
```

If the calls are **independent**, issue them in parallel and the latency is the **maximum**, not the sum:

```
L_parallel = max(c1, c2, ..., cN) + small coordination overhead
```

But parallel fan-out has a tax that catches people out: **you now wait for the slowest of N**, so your latency distribution is the *maximum order statistic*. If each call independently exceeds 200 ms with probability 1 %, then with 5 parallel calls:

```
P(all fast)        = 0.99^5 = 0.950990
P(at least 1 slow) = 1 - 0.950990 = 4.90 %
```

With 20 parallel calls it is `1 - 0.99^20 = 18.2 %`. **Fan-out converts rare tail events into common ones.** The standard mitigations are hedged requests (send a duplicate after p95 and take the first answer) and tolerating partial results. Lesson 16 develops this properly.

### 4.4 Amdahl's Law — the limit of parallelism

> **Speedup(N) = 1 / ( (1 − p) + p/N )**
>
> where **p** is the fraction of the work that can be parallelised and **N** is the number of processors.

The serial fraction `(1 − p)` is a **hard floor**: as N → ∞, speedup → `1/(1 − p)`. With 5 % serial work, the maximum possible speedup is 20×, no matter if you have a million cores. Worked numbers are in Section 9.

Why it matters in system design: your "serial fraction" is things like a single shared lock, a leader node that must sequence writes, a single-threaded JSON parse, or one database that every shard consults. Those do not get faster when you add machines, and they set your ceiling.

### 4.5 The Universal Scalability Law — the part Amdahl misses

Amdahl says speedup *plateaus*. Reality is worse: real systems often get **slower** past a certain scale. The Universal Scalability Law (Gunther) adds a second penalty:

> **C(N) = N / ( 1 + α(N − 1) + β·N(N − 1) )**
>
> - **α (contention)** — serialisation, queueing for shared resources. This is Amdahl's term.
> - **β (coherency)** — the cost of keeping N workers *consistent with each other*: cache-line invalidation, distributed locks, consensus messages, cross-replica gossip. Coherency cost grows as **N²** because it is pairwise.

With β > 0 the curve rises, peaks, and then **comes back down** — *retrograde scalability*. Adding nodes reduces throughput. Every engineer who has added a tenth database replica and watched performance drop has met β. Conceptually: α is "waiting your turn"; β is "telling everyone else what you did".

The practical consequence: **there is an optimal cluster size**, and it is a number you can estimate by fitting measured throughput at 2, 4, 8 and 16 nodes. Scaling further requires *reducing coupling* (sharding so nodes don't talk, eventual consistency so they talk less), not adding machines.

### 4.6 Queueing theory — why latency explodes near 100 % utilisation

The simplest useful model is **M/M/1**: Poisson (random) arrivals, exponential service times, one server.

- **λ** = arrival rate, **μ** = service rate (requests/second the server *can* do), **ρ = λ/μ** = utilisation.
- **Total time in system:** `W = 1 / (μ − λ)`
- Equivalently, with service time `S = 1/μ`: **`W = S / (1 − ρ)`**
- **Waiting time in queue:** `Wq = ρ·S / (1 − ρ)`

Stare at `W = S / (1 − ρ)`. The denominator approaches zero as ρ approaches 1, so **latency approaches infinity**. This is not a metaphor; it is a hyperbola.

| Utilisation ρ | Latency multiplier 1/(1−ρ) | W with S = 10 ms | Queue wait Wq |
|---|---|---|---|
| 50 % | 2× | 20 ms | 10 ms |
| 70 % | 3.33× | 33.3 ms | 23.3 ms |
| 80 % | 5× | 50 ms | 40 ms |
| 90 % | 10× | 100 ms | 90 ms |
| 95 % | 20× | 200 ms | 190 ms |
| 99 % | 100× | 1,000 ms | 990 ms |

Read the deltas, not the values. Going from 50 % → 70 % costs 13 ms. Going from 90 % → 95 % costs **100 ms**. Going from 95 % → 99 % costs **800 ms**. The same 5-point increase in utilisation is 8× more damaging at the top of the range.

Three operational rules fall straight out of this:

1. **Target 60–70 % utilisation for latency-sensitive services.** The "wasted" 30 % is not waste; it is your latency budget and your headroom for traffic spikes.
2. **Batch systems can run at 90 %+** because nobody is waiting. Utilisation targets are a function of latency sensitivity.
3. **More servers reduce latency even when throughput is adequate**, because they reduce ρ. This is non-obvious and frequently the cheapest fix.

Two refinements to know: **M/M/c** (c servers sharing one queue) is dramatically better than c independent M/M/1 queues — which is exactly why a single shared queue with many workers beats per-worker queues, and why a load balancer using "least outstanding requests" beats round-robin. And **variability makes everything worse**: the general formula `Wq ≈ (ρ/(1−ρ)) · ((CV_a² + CV_s²)/2) · S` says that doubling the coefficient of variation of service times roughly quadruples queue wait. Predictable slowness is better than unpredictable speed.

### 4.7 Bandwidth versus latency — and the speed of light

**You can buy bandwidth. You cannot buy round-trip time.**

Bandwidth is a commercial variable: upgrade the NIC, pay for a bigger link, add a second circuit. Latency over distance is bounded by physics:

- Speed of light in vacuum: **c ≈ 300,000 km/s**
- Speed of light in optical fibre: **≈ 200,000 km/s** (refractive index ~1.47, so ~0.67c)
- Real fibre paths are **1.4–1.6× the great-circle distance** because cables follow coastlines, land routes and existing rights of way.

Therefore the *absolute floor* for a round trip is `2 × distance / 200,000 km/s`, and the realistic floor is `2 × 1.5 × distance / 200,000 km/s`. Section 9 computes Mumbai → Virginia explicitly. No amount of money changes it. The only ways to beat it are to **move the data closer** (CDN, edge, regional replicas — Phase 2 and Phase 5) or to **make fewer round trips** (HTTP/2 and HTTP/3 multiplexing from Lesson 9, connection reuse, batching, GraphQL from Lesson 12).

A related trap: **bandwidth-delay product (BDP) = bandwidth × RTT** is the amount of data that must be "in flight" to keep a link saturated. On a 100 Mbps link with 200 ms RTT, BDP = 2.5 MB. If TCP's window is smaller than that, you cannot use the bandwidth you bought — a 1 Gbps transpacific link with default settings may deliver 5 Mbps. High bandwidth over long distance requires window tuning; this is why "the link is 1 Gbps but my transfer is slow" is usually a latency problem wearing a bandwidth costume.

### 4.8 Concurrency versus parallelism, mechanically

| | Concurrency | Parallelism |
|---|---|---|
| Definition | Structuring work so multiple tasks are *in progress* | Multiple tasks *executing* at the same instant |
| Bounded by | Memory, file descriptors, pool sizes | Physical cores / disks / NICs |
| Helps with | **I/O-bound** work (waiting on network, disk) | **CPU-bound** work (computation) |
| Achieved by | Event loops, async/await, green threads, OS threads | Multiple cores, multiple machines, SIMD, GPUs |
| 1-core machine | Can have 10,000 concurrent requests | Has parallelism of exactly 1 |

The practical rule for sizing pools:

- **CPU-bound work:** threads ≈ number of cores (±1). More threads just add context-switching overhead; the work is not waiting on anything.
- **I/O-bound work:** threads ≈ cores × (1 + wait time / compute time). If a request spends 18 ms waiting and 2 ms computing on 4 cores: `4 × (1 + 18/2) = 40` threads. This is just Little's Law in disguise.

And the reason this matters so much: **most web requests are I/O-bound** — 90 %+ of wall-clock time is waiting. That is why an event-loop server on one core can hold 10,000 connections, and why "add more CPU" rarely fixes a slow web service.

## 5. Request / data flow

Follow one BookNook search request, accounting for every microsecond and naming which of the four concepts governs each step.

1. **t=0 ms** — User types "distributed systems" and presses Enter in Mumbai. The server is in Mumbai too (same region).
2. **t=0–1 ms** — DNS is already cached in the browser (Lesson 4). *Latency saved: 20–80 ms.* Governed by: **latency**.
3. **t=1 ms** — TCP connection already open and warm from the previous page (HTTP/2 connection reuse, Lesson 9). *Latency saved: 1 RTT for TCP + 1–2 RTT for TLS ≈ 15–45 ms.* Governed by: **latency**.
4. **t=1–6 ms** — Request travels browser → ISP → load balancer. ~5 ms over the last-mile mobile network. Governed by: **latency** (and it is the single largest controllable chunk for mobile users).
5. **t=6 ms** — Load balancer picks a backend using *least outstanding requests*. This choice matters: it is M/M/c behaviour rather than c × M/M/1, and it measurably reduces p99. Governed by: **queueing**.
6. **t=6.5 ms** — Request arrives at the app server and is placed in the accept queue. **If the server is at 95 % utilisation it waits ~190 ms here** before any code runs. Governed by: **utilisation**.
7. **t=7 ms** — A worker (goroutine / event-loop task) picks it up. The pool holds at most 200 concurrent requests. Governed by: **concurrency**.
8. **t=7–7.1 ms** — Parse, authenticate from the session cookie (in-process cache hit, ~100 ns of RAM access). Governed by: **latency**, trivially.
9. **t=7.1–7.6 ms** — Redis lookup for cached search results. One same-datacentre round trip: **0.5 ms**. Miss. Governed by: **latency**.
10. **t=7.6 ms** — Fan out **in parallel** to three services: search index (35 ms), personalisation (18 ms), inventory (9 ms). Serial would cost 62 ms; parallel costs `max(35, 18, 9) = 35 ms`. Governed by: **parallelism** and **tail risk**.
11. **t=42.6 ms** — All three have returned. The *slowest* determined the time; speeding up inventory from 9 ms to 1 ms would change nothing.
12. **t=42.6–44 ms** — Merge, rank and serialise 40 results to JSON: 1.4 ms of pure CPU. Governed by: **parallelism** (this is the part that scales with cores).
13. **t=44 ms** — Write the cache entry asynchronously (fire-and-forget, Lesson 13) so it does not add to this request's latency.
14. **t=44–46 ms** — Response (120 KB JSON, gzip → 22 KB) is written to the socket. On a 25 Mbps mobile link: `22 KB × 8 = 176 Kbit ÷ 25 Mbit/s = 7.0 ms` of transmission. Governed by: **bandwidth**.
15. **t=53 ms** — Last byte received by the browser.
16. **t=53–120 ms** — Browser parses JSON, renders, and begins fetching 40 book covers at ~45 KB each = 1.8 MB. On the same 25 Mbps link: `1.8 MB × 8 = 14.4 Mbit ÷ 25 Mbit/s = 576 ms`. Governed by: **bandwidth**, and it now dominates everything the backend did.
17. **t≈700 ms** — Page is visually complete.

**The lesson of steps 14–17:** the backend took 46 ms and the images took 576 ms. Twelve times more user-visible time was spent on bandwidth than on all server processing combined. Optimising the search service by 50 % would improve the user's experience by 2.5 %. Serving images from a CDN at 20 ms RTT and in a modern format at 15 KB would cut ~500 ms. **Measure before optimising, and measure what the user experiences, not what your server logs.**

## 6. ASCII diagram

```
THE FOUR CONCEPTS ON ONE PICTURE

                     <----------- W = time in system ----------->
                  arrivals                                     departures
   λ (arrival rate) ──▶ ┌──────────────┐ ──▶ ┌──────────────┐ ──▶ throughput
                        │    QUEUE     │     │   SERVERS    │       (λ out)
                        │  ● ● ● ● ●   │     │  [S1][S2][S3]│
                        └──────────────┘     └──────────────┘
                          waiting (Wq)         serving (S)
                        └──────────── L = λ × W items in system ────────┘
                                      ( = CONCURRENCY )

   BANDWIDTH = width of the arrows      UTILISATION ρ = λ / (c × μ)
   LATENCY   = W for one item           PARALLELISM = c (number of servers)


LATENCY vs UTILISATION  —  W = S / (1 - ρ),  S = 10 ms

 1000ms │                                                        ●  99%
        │
        │
  500ms │
        │
  200ms │                                              ●  95%
  100ms │                                     ●  90%
   50ms │                          ●  80%
   33ms │                ●  70%
   20ms │      ●  50%
        └──────┴─────────┴────────┴──────────┴─────────┴─────────┴──
              50%       70%      80%        90%       95%       99%
                              UTILISATION
        "The cliff is real. 90% -> 95% costs you 100ms for nothing."


SERIAL vs PARALLEL FAN-OUT

 SERIAL (dependent calls)                PARALLEL (independent calls)
  ├─ search  35ms ─┐                      ├─ search        35ms ─┐
                   ├─ person. 18ms ─┐     ├─ personalise   18ms ─┤
                                    ├─ inv├─ inventory      9ms ─┤
                                 9ms┘     │                      │
  total = 35+18+9 = 62 ms                 total = max(35,18,9) = 35 ms
                                          BUT p99 is now the MAX of three
                                          tails: P(any slow) = 1-0.99^3 = 2.97%


BANDWIDTH vs LATENCY — you can buy one, not the other

  Mumbai ●───────────────────────────────────────────────● Virginia
          great circle ≈ 13,000 km ; fibre path ≈ 19,500 km
          light in fibre ≈ 200,000 km/s
          one way  = 19,500 / 200,000 = 97.5 ms
          RTT      = 195 ms   <-- NO amount of money reduces this
          upgrade 100 Mbps -> 10 Gbps : RTT still 195 ms
          move the data to Mumbai     : RTT becomes 2 ms  <-- the only fix
```

## 7. Detailed example

### BookNook: diagnosing a 3-second product page

BookNook's product page is slow. The team's first instinct is "the database is slow, add read replicas". The measurement says otherwise.

| Component | Time | Governed by | Correct fix |
|---|---|---|---|
| DNS (uncached first visit) | 45 ms | Latency | Prefetch, longer TTL (Lesson 4) |
| TCP + TLS handshake | 120 ms (3 RTT at 40 ms) | Latency | Keep-alive, TLS 1.3, HTTP/3 (Lessons 7, 9) |
| App server queue wait | 190 ms | **Utilisation at 95 %** | Add 2 servers → ρ drops to 63 %, wait → 17 ms |
| Database queries (N+1: 41 queries) | 41 × 1.2 ms = 49 ms | **Round trips, not DB speed** | Batch into 2 queries → 2.4 ms |
| Business logic CPU | 18 ms | Parallelism | Profile; probably leave alone |
| HTML/JSON transfer (38 KB) | 12 ms | Bandwidth | gzip/brotli |
| **40 cover images, 1.8 MB** | **576 ms** | **Bandwidth** | CDN + WebP + responsive sizes → ~90 ms |
| Blocking JS/CSS (820 KB) | 260 ms | Bandwidth + parse | Split bundles, defer |
| Third-party analytics scripts | 1,700 ms | Latency to 6 foreign domains | Load async; drop two vendors |
| **Total** | **~2,970 ms** | | **~600 ms after fixes** |

Read that table again: **the database — the thing everyone wanted to fix — accounts for 49 ms of 2,970 ms, or 1.6 %.** Adding read replicas would have cost money, added replication lag and consistency bugs, and improved the page by half a percent. The two biggest wins are a CDN and deleting two analytics vendors, neither of which is a backend change.

The three structural lessons:

1. **The bottleneck is almost never where your intuition points.** Measure the full user-perceived path, including the browser.
2. **Queue wait is invisible in application logs.** Your handler logged "18 ms" and was telling the truth; the request had already waited 190 ms before your code ran. Instrument *time to first byte of handling*, not just handler duration.
3. **Round trips, not query time, dominate database cost here.** 41 queries at 1.2 ms is a round-trip problem (Lesson 10's N+1), and the fix is batching, not hardware.

### Contrasting example: a real-time multiplayer game server

Same four concepts, opposite priorities.

- **Latency budget: 50 ms end to end**, versus BookNook's 500 ms. Ten times tighter, which changes every decision.
- **Throughput requirement is modest**: 64 players × 30 updates/second = 1,920 messages/second per match server. BookNook's search tier handles more than that. **High-performance does not mean high-throughput.**
- **Bandwidth per player is tiny**: a 60-byte state delta × 30/s = 1.8 KB/s, or 14.4 kbps. Bandwidth is irrelevant here; *jitter* (variance in latency) is everything, because inconsistent latency is worse for gameplay than consistently higher latency.
- **Utilisation target is ~40 %, not 70 %.** At a 50 ms budget you cannot afford the queueing multiplier; you buy headroom aggressively.
- **UDP instead of TCP** (Lesson 8), because TCP's retransmission creates head-of-line blocking: a 100 ms-old position update is useless, so *dropping* it beats *waiting* for it. BookNook would never accept a lost response.
- **Geographic placement is the primary architecture decision**, not caching. The game runs match servers in 20 regions purely to keep RTT under 40 ms, because the speed of light is the binding constraint and nothing else can fix it.

The common thread: the same four quantities, with completely different budgets, produce completely different architectures. **"Fast" is not a design goal; a specific latency budget for a specific operation is.**

## 8. Code or configuration example

This code **illustrates how to measure and reason about these quantities. It is not production-ready**: no warm-up, no statistical rigour on outliers, naive percentile calculation, single-process load generation, and no coordinated-omission correction.

```python
import asyncio, time, statistics

# ---------------------------------------------------------------------------
# 1. LITTLE'S LAW as a sizing tool. L = lambda x W.
# ---------------------------------------------------------------------------
def required_concurrency(arrival_rate_per_sec: float, service_time_sec: float) -> float:
    """How many requests are in flight on average. This is the MINIMUM pool size."""
    return arrival_rate_per_sec * service_time_sec          # L = lambda x W


def max_throughput(pool_size: int, service_time_sec: float) -> float:
    """Ceiling on throughput for a bounded pool. lambda = L / W."""
    return pool_size / service_time_sec


def thread_pool_size(cores: int, wait_time_ms: float, compute_time_ms: float) -> int:
    """I/O-bound sizing: cores x (1 + wait/compute). Pure Little's Law, rephrased.
    For CPU-bound work this formula collapses to `cores` because wait = 0."""
    return round(cores * (1 + wait_time_ms / compute_time_ms))


print(required_concurrency(14, 0.020))        # 0.28  -> one server is plenty
print(max_throughput(200, 0.050))             # 4000 req/s ceiling for 200 workers
print(thread_pool_size(4, wait_time_ms=18, compute_time_ms=2))   # 40 threads


# ---------------------------------------------------------------------------
# 2. M/M/1 QUEUEING: why 95% utilisation is a trap.
# ---------------------------------------------------------------------------
def mm1_total_time(service_time_sec: float, utilisation: float) -> float:
    """W = S / (1 - rho). Blows up as rho -> 1. Undefined at or above 1.0
    because the queue grows without bound -- that is what 'unstable' means."""
    if utilisation >= 1.0:
        return float("inf")                   # arrivals exceed capacity: infinite queue
    return service_time_sec / (1.0 - utilisation)


for rho in (0.5, 0.7, 0.8, 0.9, 0.95, 0.99):
    w = mm1_total_time(0.010, rho)            # S = 10 ms
    # Printing the MULTIPLIER is what makes the cliff obvious to a reader.
    print(f"rho={rho:>4}  W={w*1000:7.1f} ms  ({1/(1-rho):5.1f}x service time)")


# ---------------------------------------------------------------------------
# 3. AMDAHL'S LAW and the UNIVERSAL SCALABILITY LAW.
# ---------------------------------------------------------------------------
def amdahl(parallel_fraction: float, processors: int) -> float:
    """Speedup = 1 / ((1-p) + p/N). The (1-p) term is a HARD FLOOR."""
    serial = 1.0 - parallel_fraction
    return 1.0 / (serial + parallel_fraction / processors)


def usl(n: int, alpha: float, beta: float) -> float:
    """C(N) = N / (1 + alpha(N-1) + beta*N*(N-1)).
    alpha = contention (queueing for shared resources)  -- linear penalty
    beta  = coherency  (keeping N nodes consistent)     -- QUADRATIC penalty.
    When beta > 0 the curve PEAKS and then DECLINES: adding nodes loses throughput."""
    return n / (1 + alpha * (n - 1) + beta * n * (n - 1))


print(amdahl(0.95, 16))                       # 9.14x from 16 cores, not 16x
print(amdahl(0.95, 10_000))                   # 19.96x -- the 20x ceiling
for n in (1, 2, 4, 8, 16, 32, 64, 128):
    print(n, round(usl(n, alpha=0.03, beta=0.0005), 2))   # peaks near N=44, then falls


# ---------------------------------------------------------------------------
# 4. LATENCY vs THROUGHPUT measured, not assumed.
# ---------------------------------------------------------------------------
async def fake_dependency(ms: float):
    await asyncio.sleep(ms / 1000)            # non-blocking: frees the thread (Lesson 13)


async def handle_request() -> float:
    """SERIAL vs PARALLEL fan-out, measured. The three calls are INDEPENDENT,
    so gathering them costs max(), not sum()."""
    start = time.perf_counter()
    await asyncio.gather(                     # parallel: ~35 ms
        fake_dependency(35),                  # search index
        fake_dependency(18),                  # personalisation
        fake_dependency(9),                   # inventory
    )
    return (time.perf_counter() - start) * 1000


async def load_test(concurrency: int, total: int):
    """Measures BOTH latency and throughput, because optimising one often
    harms the other and a single number hides that."""
    sem = asyncio.Semaphore(concurrency)      # bounded pool == bounded L in Little's Law
    latencies: list[float] = []

    async def one():
        async with sem:                       # a request may WAIT here -- that wait is
            latencies.append(await handle_request())   # real user latency, so it is timed
                                              # outside would hide queue wait: a classic bug

    t0 = time.perf_counter()
    await asyncio.gather(*(one() for _ in range(total)))
    elapsed = time.perf_counter() - t0

    latencies.sort()
    print(f"concurrency={concurrency}")
    print(f"  throughput = {total/elapsed:8.1f} req/s")     # the RATE
    print(f"  p50        = {latencies[len(latencies)//2]:8.1f} ms")
    print(f"  p99        = {latencies[int(len(latencies)*0.99)]:8.1f} ms")  # the TAIL
    print(f"  Little's L = {(total/elapsed) * (statistics.mean(latencies)/1000):8.2f}")
    # ^ this last line should approximately equal `concurrency` when saturated.
    #   If it does not, your measurement is wrong -- Little's Law is a sanity check.

asyncio.run(load_test(concurrency=50, total=500))
```

```yaml
# Illustrative capacity configuration. Every number is derived, not guessed.
service: booknook-api
sizing:
  peak_rps: 1400                # measured p99 of per-minute arrival rate
  service_time_ms: 50           # measured p50 handler duration
  # L = lambda x W = 1400 x 0.050 = 70 concurrent requests at peak
  target_utilisation: 0.65      # NOT 0.95 -- see the M/M/1 table. This is the
                                # latency budget, bought as deliberate idle capacity.
  # Capacity needed = 70 / 0.65 = 108 concurrent slots
  workers_per_pod: 16
  pods: 7                       # 7 x 16 = 112 slots >= 108. Round UP, always.

pools:
  # Connection pool must be sized for the DEPENDENCY, not the caller. Postgres
  # degrades past ~(2 x cores); 500 app connections to a 16-core DB is a classic
  # self-inflicted outage. Use a pooler (PgBouncer) in front.
  postgres_max_connections_per_pod: 5    # 7 pods x 5 = 35 total; DB has 16 cores

timeouts:
  # Timeout must be > p99.9 of the dependency, or you create retry storms by
  # timing out requests that were about to succeed.
  downstream_ms: 800            # dependency p99.9 = 610 ms, +30% headroom

autoscale:
  # Scale on CONCURRENCY (in-flight requests), not CPU. CPU is a lagging,
  # misleading signal for I/O-bound services: an overloaded service that is
  # waiting on a slow database shows LOW CPU while users time out.
  metric: inflight_requests_per_pod
  target: 10                    # 16 workers x 0.65 utilisation ~= 10
```

## 9. Numerical example

Every assumption is stated; every step is shown. This is the quantitative core of the course — work through it with a calculator rather than reading it.

### 9.1 Little's Law, three ways

**Worked example A — "How many servers do I need?" (solving for L)**

*Assumptions*: BookNook peak is 1,400 requests/second (measured). Mean server-side service time is 50 ms. Workers are I/O-bound and non-blocking.

```
L = λ × W
L = 1,400 req/s × 0.050 s
L = 70 concurrent requests at peak
```

But you must **not** run at 100 % utilisation (Section 9.6), so size for a target:

```
Slots required = L / target_utilisation
               = 70 / 0.65
               = 107.7  ->  108 slots

Pods required  = 108 / 16 workers per pod
               = 6.75   ->  7 pods   (ALWAYS round up)
```

**Worked example B — "What throughput can this pool sustain?" (solving for λ)**

*Assumptions*: a legacy thread-per-request service with 200 threads; each request blocks for 250 ms on a slow downstream call.

```
λ_max = L / W
      = 200 threads / 0.250 s
      = 800 req/s
```

Now the downstream gets 2× slower (250 ms → 500 ms) while traffic is unchanged at 700 req/s:

```
New λ_max = 200 / 0.500 = 400 req/s
Demand    = 700 req/s  >  capacity 400 req/s   ->  UNSTABLE
```

The queue grows without bound and the service collapses — **even though no server failed and traffic did not increase.** This is the exact mechanism of a dependency-induced outage, and Little's Law predicts it in one line. Note also what *does* fix it: reducing W. If you cannot, you must shed load.

**Worked example C — Thread-pool sizing for I/O-bound work**

*Assumptions*: 4 CPU cores. Each request uses 2 ms of CPU and waits 18 ms on the database. Total W = 20 ms.

```
CPU-bound ceiling   = cores / CPU_time = 4 / 0.002 = 2,000 req/s
Concurrency needed  = λ × W = 2,000 × 0.020 = 40 requests in flight
Thread pool formula = cores × (1 + wait/compute) = 4 × (1 + 18/2) = 4 × 10 = 40
```

Both routes give **40**, because they are the same law. Now the common mistake — sizing threads equal to cores:

```
λ_max with 4 threads = 4 / 0.020 = 200 req/s
λ_max with 40 threads = 40 / 0.020 = 2,000 req/s
```

**A 10× throughput difference from one configuration line**, because 90 % of each request is waiting, not computing. And the opposite mistake, 400 threads:

```
λ_max with 400 threads = 400 / 0.020 = 20,000 req/s  -- but the CPU ceiling is 2,000 req/s
```

You cannot exceed the CPU ceiling; the extra 360 threads only add memory (360 × 1 MB stacks = 360 MB) and context-switching overhead, and they let 400 requests pile up inside the service instead of being rejected at the door. **Queue at the edge, not in the engine room.**

**Worked example D — Inferring latency from metrics you already have (solving for W)**

*Assumptions*: your dashboard shows mean in-flight requests = 85 and arrival rate = 1,200 req/s. You have no latency histogram.

```
W = L / λ = 85 / 1,200 = 0.0708 s = 70.8 ms
```

Little's Law also works as a **sanity check on your instrumentation**: if measured L, λ and W do not satisfy `L = λW` to within a few percent, one of the three is being measured wrong — almost always W, because the timer was started after the queue wait.

### 9.2 Applying the latency table

*Assumptions*: the latency anchors from Section 4.2.

A single piece of data, four places it could live:

```
In-process memory (RAM)        : 100 ns      = 0.0001 ms
Redis, same datacentre         : 500,000 ns  = 0.5 ms       (5,000× slower)
Postgres on NVMe, same DC      : 1,200,000 ns = 1.2 ms      (12,000× slower)
Origin server, Virginia (from Mumbai) : 195,000,000 ns = 195 ms  (1,950,000× slower)
```

**Reading data from across the world is roughly two million times more expensive than reading it from local memory.** Every caching layer in Phase 2 exists to move data leftward in that list.

Now the N+1 query problem (Lesson 10), quantified:

```
Naive:   1 query for the book + 40 queries for reviews
         = 41 round trips × 1.2 ms = 49.2 ms

Batched: 1 query for the book + 1 query with IN (...) for all reviews
         = 2 round trips × 1.2 ms = 2.4 ms

Speedup = 49.2 / 2.4 = 20.5×
```

The database did the same amount of actual work in both cases. **The cost was round trips, not computation.** Now do it across a continent instead of a datacentre:

```
41 round trips × 195 ms = 7,995 ms = 8.0 seconds
 2 round trips × 195 ms =   390 ms
```

The same code that is "a bit slow" in one datacentre is **completely unusable** across continents. This is why chatty protocols must die at distance, and the quantitative basis for GraphQL and BFF patterns (Lesson 12).

### 9.3 Serial versus parallel fan-out

*Assumptions*: three independent downstream calls with p50 of 35 ms, 18 ms and 9 ms; each independently exceeds its 150 ms p99 threshold with probability 1 %.

```
Serial   = 35 + 18 + 9 = 62 ms
Parallel = max(35, 18, 9) = 35 ms  (+ ~1 ms coordination overhead)
Saving   = 62 - 36 = 26 ms  (42 % reduction)
```

Note which optimisations are worthless: speeding up *inventory* from 9 ms to 1 ms saves **0 ms** in the parallel case, because `max()` is still 35. **In a parallel fan-out, only the slowest call is worth optimising.**

The tail cost of fanning out:

```
3 calls:  P(at least one slow) = 1 - 0.99^3  = 1 - 0.970299 = 2.97 %
5 calls:  P(at least one slow) = 1 - 0.99^5  = 1 - 0.950990 = 4.90 %
10 calls: P(at least one slow) = 1 - 0.99^10 = 1 - 0.904382 = 9.56 %
20 calls: P(at least one slow) = 1 - 0.99^20 = 1 - 0.817907 = 18.21 %
100 calls:P(at least one slow) = 1 - 0.99^100= 1 - 0.366032 = 63.40 %
```

At a fan-out of 100 (typical for a search-index scatter-gather), **63 % of user requests touch at least one slow shard**. Your service's p50 is now determined by your shards' p99. This is the single most important fact about large fan-out systems, and Lesson 16 is devoted to what to do about it.

### 9.4 Amdahl's Law, worked

*Assumptions*: 95 % of a batch job's work is parallelisable; 5 % is irreducibly serial (reading the input file, writing the final summary, acquiring one global lock).

```
Speedup(N) = 1 / ( 0.05 + 0.95/N )

N = 1   : 1 / (0.05 + 0.950000) = 1 / 1.000000 =  1.00×
N = 2   : 1 / (0.05 + 0.475000) = 1 / 0.525000 =  1.90×
N = 4   : 1 / (0.05 + 0.237500) = 1 / 0.287500 =  3.48×
N = 8   : 1 / (0.05 + 0.118750) = 1 / 0.168750 =  5.93×
N = 16  : 1 / (0.05 + 0.059375) = 1 / 0.109375 =  9.14×
N = 32  : 1 / (0.05 + 0.029688) = 1 / 0.079688 = 12.55×
N = 64  : 1 / (0.05 + 0.014844) = 1 / 0.064844 = 15.42×
N = ∞   : 1 / (0.05 + 0)        = 1 / 0.05     = 20.00×  (hard ceiling)
```

**Efficiency** — how much of each core you actually use:

```
At N = 8  : 5.93 / 8  = 74 % efficient
At N = 16 : 9.14 / 16 = 57 % efficient
At N = 64 : 15.42 / 64 = 24 % efficient  -- you pay for 64, you get 15.4
```

Going from 16 to 64 cores (4× the cost) buys `15.42/9.14 = 1.69×` the speed. Now compare that to **reducing the serial fraction instead**, from 5 % to 1 %, at the original 16 cores:

```
Speedup(16) with p = 0.99 : 1 / (0.01 + 0.99/16) = 1 / (0.01 + 0.061875) = 13.91×
```

**Removing serial work gave 13.91× on 16 cores; quadrupling the cores gave 15.42× on 64 cores.** Four times the hardware, for less benefit than one engineer removing a global lock. That is the practical meaning of Amdahl's Law.

### 9.5 Universal Scalability Law, worked

*Assumptions*: α (contention) = 0.03, β (coherency) = 0.0005 — values typical of a database cluster with cross-node consistency traffic.

```
C(N) = N / (1 + 0.03(N-1) + 0.0005·N(N-1))

N=1  :   1 / (1 + 0      + 0      ) = 1 / 1.000  =  1.00×
N=2  :   2 / (1 + 0.03   + 0.001  ) = 2 / 1.031  =  1.94×
N=4  :   4 / (1 + 0.09   + 0.006  ) = 4 / 1.096  =  3.65×
N=8  :   8 / (1 + 0.21   + 0.028  ) = 8 / 1.238  =  6.46×
N=16 :  16 / (1 + 0.45   + 0.120  ) = 16 / 1.570 = 10.19×
N=32 :  32 / (1 + 0.93   + 0.496  ) = 32 / 2.426 = 13.19×
N=44 :  44 / (1 + 1.29   + 0.946  ) = 44 / 3.236 = 13.60×   <-- PEAK
N=64 :  64 / (1 + 1.89   + 2.016  ) = 64 / 4.906 = 13.04×   <-- declining
N=128: 128 / (1 + 3.81   + 8.128  ) = 128/12.938 =  9.89×
N=256: 256 / (1 + 7.65   + 32.640 ) = 256/41.290 =  6.20×
```

The peak is predictable analytically:

```
N* = sqrt( (1 - α) / β ) = sqrt( 0.97 / 0.0005 ) = sqrt(1,940) = 44.0 nodes
```

Three brutal facts in that table:

1. **At 64 nodes you have less throughput than at 44 nodes**, while paying 45 % more.
2. **At 256 nodes you have less throughput than at 8 nodes**, while paying 32× more. Retrograde scaling is not theoretical; it is what happens when you add replicas to a chatty cluster.
3. The only way past the peak is to **change α and β**, not N. Sharding (so nodes never talk to each other) drives β toward 0; with β = 0 the curve becomes Amdahl's and merely plateaus instead of collapsing.

```
With β = 0, α = 0.03:  C(256) = 256 / (1 + 7.65) = 29.6×   (vs 6.20× with β=0.0005)
```

**Eliminating cross-node coordination turned 6.2× into 29.6× at the same node count.** That is why "shared nothing" is the dominant scaling architecture (Phase 3).

### 9.6 Utilisation and the latency cliff

*Assumptions*: M/M/1 approximation; service time S = 50 ms, so μ = 20 requests/second per worker; 16 workers per pod, so 320 req/s per pod. Arrival rate λ = 1,400 req/s. (M/M/1 over aggregated capacity is a simplification — M/M/c is kinder — but it gives the right *shape* and errs on the safe side.)

```
Capacity with P pods = 320P req/s
Utilisation  ρ       = 1400 / (320P)
Latency      W       = S / (1 - ρ) = 0.050 / (1 - ρ)
```

| Pods | Capacity (req/s) | ρ = 1400/capacity | 1/(1−ρ) | W | Queue wait (W − 50 ms) |
|---|---|---|---|---|---|
| 5 | 1,600 | 0.875 | 8.00 | 400 ms | 350 ms |
| 6 | 1,920 | 0.729 | 3.69 | 185 ms | 135 ms |
| 7 | 2,240 | 0.625 | 2.67 | 133 ms | 83 ms |
| 8 | 2,560 | 0.547 | 2.21 | 110 ms | 60 ms |
| 10 | 3,200 | 0.438 | 1.78 | 89 ms | 39 ms |
| 16 | 5,120 | 0.273 | 1.38 | 69 ms | 19 ms |

Marginal value of each pod:

```
5 -> 6 pods : saves 400 - 185 = 215 ms   (huge)
6 -> 7 pods : saves 185 - 133 =  52 ms   (good)
7 -> 8 pods : saves 133 - 110 =  23 ms   (moderate)
8 -> 10 pods: saves 110 -  89 =  21 ms for TWO pods (poor)
10 -> 16 pods: saves 89 - 69  =  20 ms for SIX pods (terrible)
```

**The cheapest latency improvement available to BookNook is the sixth pod.** It costs 20 % more compute and halves latency. Beyond 8 pods you are paying a lot for very little — that is the knee of the curve, and finding it is the whole job of capacity planning.

Now the reverse: traffic grows 20 % to 1,680 req/s with 7 pods unchanged.

```
ρ = 1680 / 2240 = 0.750
W = 0.050 / 0.25 = 200 ms      (was 133 ms)
```

**A 20 % traffic increase caused a 50 % latency increase.** Nonlinearity is the defining property of loaded systems, and it is why "we have 25 % headroom" is a far more dangerous statement than it sounds.

And the catastrophe case — a dependency slows, so S doubles to 100 ms (μ = 10/s, pod capacity 160 req/s):

```
Capacity with 7 pods = 1,120 req/s
λ = 1,400 req/s  >  1,120 req/s  ->  ρ > 1  ->  UNSTABLE
```

Queue grows without bound; every request eventually times out. **The service did not run out of CPU; it ran out of *concurrency slots*.** Mitigations in order of preference: reduce S (fix the dependency), shed load (reject 20 % at the edge with `429`), or add 2 more pods.

### 9.7 Speed of light: Mumbai → Virginia

*Assumptions*: Mumbai (19.08° N, 72.88° E) to Ashburn, Virginia (39.04° N, 77.49° W). Great-circle distance ≈ **12,900 km** (approximate). Light in vacuum = 300,000 km/s. Light in fibre ≈ 200,000 km/s (refractive index ≈ 1.47). Real submarine/terrestrial routes are ≈ 1.5× great-circle.

**Step 1 — theoretical floor, vacuum, straight line:**

```
One way = 12,900 km / 300,000 km/s = 0.0430 s = 43.0 ms
RTT     = 2 × 43.0 = 86.0 ms
```

**Step 2 — in fibre, still a straight line (physically impossible, but a useful bound):**

```
One way = 12,900 / 200,000 = 0.0645 s = 64.5 ms
RTT     = 129.0 ms
```

**Step 3 — realistic fibre path (1.5× great-circle):**

```
Path length = 12,900 × 1.5 = 19,350 km
One way     = 19,350 / 200,000 = 0.0968 s = 96.8 ms
RTT         = 193.5 ms
```

**Step 4 — add equipment:** roughly 12–20 router hops, optical amplifiers and switching add ~5–25 ms total.

```
Observed RTT ≈ 193.5 + 15 ≈ 208 ms   (matches the real-world 180–220 ms)
```

**Step 5 — now try to buy your way out of it.** Transferring a 22 KB JSON response:

```
22 KB = 22,528 bytes × 8 = 180,224 bits

On 100 Mbps : 180,224 / 100,000,000 = 0.00180 s = 1.80 ms
On 1 Gbps   : 180,224 / 1,000,000,000 = 0.000180 s = 0.18 ms
On 10 Gbps  : 0.018 ms

Total time at 100 Mbps = 208 + 1.80 = 209.80 ms
Total time at 10 Gbps  = 208 + 0.02 = 208.02 ms

Improvement from a 100× bandwidth upgrade = 1.78 ms = 0.85 %
```

**A 100× bandwidth upgrade improved the response by under 1 %.** Meanwhile:

```
Serve from a Mumbai edge location, RTT 2 ms:
Total = 2 + 1.80 = 3.80 ms

Improvement = 209.80 / 3.80 = 55× faster
```

**Moving the data beat buying bandwidth by a factor of 55.** That is the entire economic argument for CDNs and multi-region architecture, and you derived it from the speed of light.

**Step 6 — bandwidth-delay product, where latency eats bandwidth.**

```
BDP = bandwidth × RTT
    = 1,000,000,000 bits/s × 0.208 s
    = 208,000,000 bits = 26,000,000 bytes = 26 MB in flight
```

If TCP's receive window is the legacy 64 KB (no window scaling):

```
Max throughput = window / RTT = 65,536 bytes / 0.208 s
               = 315,077 bytes/s = 2.52 Mbps
```

**On a 1 Gbps link you would achieve 2.52 Mbps — 0.25 % of the capacity you are paying for.** With a 16 MB window:

```
Max throughput = 16,777,216 / 0.208 = 80,659,692 bytes/s = 645 Mbps
```

This is why "we bought a 1 Gbps transcontinental link and it's slow" is almost always a window-size or congestion-control problem, not a bandwidth problem. **Latency silently caps bandwidth.**

### 9.8 Bandwidth on the user's side

*Assumptions*: Indian 4G median downlink ≈ 25 Mbps; 40 book covers at 45 KB each (JPEG); CDN edge RTT 20 ms vs origin RTT 195 ms.

```
Payload = 40 × 45 KB = 1,800 KB = 1,843,200 bytes × 8 = 14,745,600 bits

From origin (one connection, ignoring parallelism):
  transfer = 14,745,600 / 25,000,000 = 0.590 s = 590 ms
  plus RTT overhead                              + 195 ms
  total                                        ≈ 785 ms

After CDN + WebP (15 KB each) + responsive sizing:
  Payload  = 40 × 15 KB = 600 KB = 4,915,200 bits
  transfer = 4,915,200 / 25,000,000 = 0.197 s = 197 ms
  plus edge RTT                                 +  20 ms
  total                                       ≈ 217 ms

Saving = 785 - 217 = 568 ms  (72 % reduction)
```

Compare that to the backend work it accompanies (46 ms of server time). **You could make the entire backend infinitely fast and save 46 ms; one afternoon of image work saves 568 ms.** Optimise where the time is.

### 9.9 Concurrency versus parallelism, numerically

*Assumptions*: 4 cores; each request = 2 ms CPU + 18 ms I/O wait.

```
Parallelism available       = 4 (cores)
CPU-bound throughput ceiling = 4 / 0.002 = 2,000 req/s
Concurrency required at ceiling = 2,000 × 0.020 = 40 in flight
```

So this machine needs **concurrency of 40 to exploit parallelism of 4**. Flip the workload to CPU-bound (20 ms CPU, 0 ms wait):

```
Throughput ceiling = 4 / 0.020 = 200 req/s
Concurrency needed = 200 × 0.020 = 4 in flight
```

**Same latency per request, same cores, 10× less throughput, and 10× less useful concurrency.** The I/O-bound workload benefits from 40 threads; the CPU-bound workload is actively harmed by them. This is why "how many threads?" has no universal answer and why the honest answer is a formula, not a number.

## 10. Trade-offs

**Optimising for latency**
- *Gains*: better UX (every 100 ms costs measurable conversion), lower concurrency for the same throughput (`L = λW`), lower utilisation, more headroom.
- *Costs*: redundant/hedged requests burn capacity; caching adds staleness and invalidation bugs; geographic replication multiplies infrastructure cost and introduces consistency problems (Phase 3); running at 60 % utilisation means paying for 40 % idle hardware.

**Optimising for throughput**
- *Gains*: lower cost per operation, higher hardware efficiency, simpler capacity story.
- *Costs*: **batching raises latency by definition** (you wait to accumulate a batch); high utilisation raises latency hyperbolically; queues grow and so does time-in-system.

**Optimising for bandwidth**
- *Gains*: cheap and fast to do (compression, image formats, pagination); huge wins for mobile and emerging-market users; directly reduces egress cost.
- *Costs*: compression burns CPU (and adds a few ms); aggressive image compression hurts quality; pagination makes clients chattier, which costs round trips.

**Optimising for concurrency**
- *Gains*: far more throughput from the same hardware for I/O-bound work; enables long-lived connections (SSE/WebSockets from Lesson 13).
- *Costs*: async code is harder to write, debug and profile; one blocking call poisons an event loop; unbounded concurrency moves queueing *inside* your process where you cannot shed it; connection pools downstream become the new bottleneck.

**The fundamental tensions, stated plainly**
- **Latency ↔ throughput**: batching, buffering and high utilisation all trade latency for throughput.
- **Latency ↔ cost**: low latency requires idle headroom and geographic presence, both of which are bought.
- **Throughput ↔ consistency**: the β term in the USL is the price of coordination; weaker consistency buys scale (Phase 3).
- **Bandwidth ↔ CPU**: compression is a direct trade between the two.

**Operational costs of taking this seriously**
- You must run load tests that actually saturate the system, and interpret them without coordinated omission (a load generator that waits for responses under-reports latency catastrophically during overload).
- You need latency *histograms*, not averages (Lesson 16), plus in-flight gauges and queue-depth metrics.
- Capacity models must be revisited whenever service time changes — which is every deploy.

**When this is unnecessary complexity**
- At 14 RPS with a 20 ms service time (`L = 0.28`), none of this changes your design. Do the estimate, confirm the system is trivially sized, and move on. Building a queueing model for a system with 0.28 requests in flight is theatre.

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| Utilisation creeps past 85 % | Gradually slower pages, then sudden timeouts; "it was fine yesterday" | p99 latency rising while traffic looks normal; utilisation and queue-depth dashboards | Autoscale on in-flight requests; the fix is capacity or less work per request, not a code tweak |
| Thread/connection pool exhausted | Requests hang, then `503`; **CPU looks idle**, which misleads everyone | Pool-wait-time metric; in-flight count pinned at the pool max | Increase the pool only if the downstream can take it; otherwise shed load and fix service time |
| Downstream dependency doubles its latency | Site-wide slowness far beyond the affected feature | Per-dependency latency panel; `L = λW` shows concurrency doubling | Timeouts cap W; circuit breaker sheds the dependency; bulkheads stop it spreading (Phase 4, Topic 14) |
| Retry storm / metastable failure | Total outage that **persists after the original trigger is gone** | Request rate exceeds historical peak while success rate is near zero | Exponential backoff **with jitter**, retry budgets, circuit breakers (Lesson 17); may need to drop all traffic to let the system drain |
| Single slow shard in a 100-way fan-out | 63 % of searches are slow even though 99 % of shards are healthy | Per-shard p99; tail-latency breakdown | Hedged requests; return partial results; evict the slow replica (Lesson 16) |
| Bandwidth saturation (egress capped) | Images and video stall; small API calls still fine — confusing | NIC utilisation, CDN offload ratio, TCP retransmit rate | CDN offload, compression, smaller assets; buy more bandwidth (this one *is* purchasable) |
| TCP window too small for a long fat link | A 1 Gbps link delivers 2.5 Mbps; backups never finish | Throughput far below BDP prediction | Enable window scaling, tune buffers, use BBR; or move data closer |
| Head-of-line blocking from one huge request | **Unrelated** requests are slow; latency is bimodal | Service-time variance (CV) spikes; p99 ≫ p50 with no traffic change | Separate pools by request class; cap request size; stream large responses |
| GC pause / stop-the-world | Periodic 200–800 ms freezes affecting all in-flight requests | GC pause histogram correlated with latency spikes | Tune heap/collector; reduce allocation; hedge requests to mask it |
| Scaling past the USL peak | Adding nodes makes everything **slower** | Throughput falls as node count rises; coordination traffic grows as N² | Shard to remove cross-node chatter; reduce consistency requirements; shrink the cluster |
| Load test reports great latency, production is terrible | Users complain about a system your graphs call healthy | Coordinated omission: the generator stopped sending while blocked | Use open-model load generation (fixed arrival rate) and correct for omission |
| Autoscaling on CPU for an I/O-bound service | Users time out while the autoscaler sees 20 % CPU and does nothing | CPU flat, in-flight requests and latency climbing | Scale on concurrency or latency, not CPU |

The recurring theme: **most of these are invisible to CPU and memory dashboards.** Latency, in-flight concurrency, queue depth and utilisation are the signals that actually describe a loaded system.

## 12. Alternatives

Different levers for "make it faster", compared honestly:

| Lever | What it improves | What it does NOT improve | Typical gain | Cost | When it's the right call |
|---|---|---|---|---|---|
| **Add servers (horizontal scale)** | Throughput; latency via lower ρ | Per-request service time; serial fractions | 2–10× throughput; latency cliff removed | Linear $; USL may cap it | ρ > 0.75, or throughput-bound |
| **Bigger servers (vertical scale)** | Throughput, sometimes latency | Anything bounded by I/O or the network | 2–4× | Superlinear $; hard ceiling | Quick fix, single-node databases |
| **Caching** | Latency **and** throughput, dramatically | Write paths; cold/unique requests | 10–1000× on hits | Staleness, invalidation bugs, memory | High read:write ratio, tolerable staleness |
| **CDN / edge** | Latency for distant users; origin bandwidth | Dynamic, personalised, write traffic | 5–50× for static assets | $ per GB; cache-control complexity | Geographically spread users, static-heavy pages |
| **Compression** | Bandwidth, transfer time | RTT; server processing | 60–85 % byte reduction | CPU (a few ms) | Text payloads over slow links |
| **Batching** | **Throughput** | **Latency — it makes it worse** | 5–50× throughput | Added delay, bigger failure blast radius | Background/async pipelines |
| **Parallel fan-out** | Latency of multi-dependency requests | Throughput (same total work); makes tails worse | sum → max | Tail amplification, more load | Independent calls, modest fan-out |
| **Async/non-blocking I/O** | Concurrency per machine | Service time; CPU-bound work | 10–100× concurrent connections | Code complexity, harder debugging | I/O-bound, many connections |
| **Reduce work (better algorithm, fewer queries)** | **Latency and throughput and cost** | Nothing — it is strictly dominant | Often 2–20× | Engineering time only | **Always try this first** |
| **Hedged / speculative requests** | Tail latency (p99) | Mean latency; costs extra capacity | p99 cut 2–10× | +5–20 % load | Large fan-out with tail problems |
| **Load shedding / rate limiting** | Stability under overload | Capacity | Prevents collapse | Some users get `429` | Protecting against retry storms |
| **Geographic replication** | Latency for distant users — the **only** fix for RTT | Consistency simplicity | 10–50× for far users | Large $; consistency problems (Phase 3) | Global user base, latency-critical |

The ordering that experienced engineers actually use: **measure → reduce work → cache → scale out → distribute geographically.** Most teams start at step 4.

## 13. When to use it

Situations where these calculations change your decisions:

1. **Capacity planning before a known traffic event.** A sale, a product launch, a marketing campaign. `L = λW`, divide by utilisation target, round up. Without this you are guessing with the company's revenue.
2. **Choosing between a cache, a replica, and a CDN.** The latency table tells you which layer the time is actually in. 49 ms of database time cannot be fixed by a CDN; 576 ms of image time cannot be fixed by a read replica.
3. **Sizing every pool you own** — threads, connections, workers, consumers. The formula is always Little's Law, and the downstream's capacity is always the real constraint.
4. **Setting utilisation and autoscaling targets.** The M/M/1 table converts "how much headroom?" from opinion into arithmetic, and tells you *which metric* to scale on.
5. **Deciding whether to go multi-region.** The speed-of-light calculation is the deciding input. If your users are 12,900 km away and your p95 budget is 150 ms, no amount of optimisation inside one datacentre will succeed.
6. **Justifying engineering work to non-engineers.** "Two more pods costs ₹X/month and halves checkout latency, which historically moves conversion by Y %" is a business case. "The system feels slow" is not.
7. **Debugging a slowdown with no obvious cause.** Flat CPU, rising latency, normal traffic → almost always utilisation, pool exhaustion, or a dependency's service time. Three specific places to look instead of a week of guessing.

## 14. When *not* to use it

1. **When traffic is small enough that the answer is obviously "one server".** At `L = 0.28`, modelling is procrastination. Do the back-of-envelope once, confirm it is trivial, and spend the time on features.
2. **As a substitute for measurement.** Models predict *shape*; they do not predict *your* system. M/M/1 assumes Poisson arrivals and exponential service times, and real traffic is burstier and real service times are more variable — so the real cliff is usually **worse** than the table. Use models to know what to measure and to sanity-check, never as the final answer.
3. **When optimising something that is not the bottleneck.** Amdahl applies to effort too: making a component that is 1.6 % of the time twice as fast improves the whole by 0.8 %.
4. **When the constraint is correctness, not performance.** Do not weaken consistency to gain throughput before you have confirmed throughput is actually the problem, because consistency bugs cost far more than latency.
5. **When the gain is below the noise floor.** Shaving 2 ms off a request whose p99 varies by ±80 ms is unmeasurable, and unmeasurable improvements are indistinguishable from regressions.

## 15. Common mistakes

**Beginner mistakes**
- **Confusing bandwidth with speed.** "We upgraded to 1 Gbps, why is the site still slow?" Because the site was RTT-bound (Section 9.7).
- **Assuming throughput = 1 / latency.** True only at concurrency 1.
- **Reporting average latency.** The average hides everything that matters; a system with p50 = 20 ms and p99 = 4,000 ms can have a perfectly reassuring mean (Lesson 16).
- **Mixing bits and bytes.** 100 Mbps is 12.5 MB/s. An 8× error in a capacity plan is a budget disaster.
- **Sizing thread pools equal to core count for I/O-bound work** — a 10× throughput loss (Section 9.1C).
- **Targeting 90 %+ utilisation to "use the hardware efficiently"**, then being surprised by the latency cliff.
- **Optimising the fastest component in a parallel fan-out**, which saves exactly zero.
- **Timing only the handler**, so the 190 ms of queue wait never appears in any graph.
- **Believing more cores linearly speed up everything** (Amdahl).

**Experienced-engineer mistakes**
- **Autoscaling on CPU for I/O-bound services.** The system melts at 20 % CPU and the autoscaler does nothing.
- **Coordinated omission in load tests.** A closed-loop generator that waits for the previous response stops sending during a stall, so the stall never appears in the results. Production then behaves nothing like the test.
- **Ignoring the β term.** Adding the 10th replica to a chatty cluster and losing throughput, then adding an 11th.
- **Oversizing connection pools to the database.** 50 pods × 20 connections = 1,000 connections to a 16-core Postgres, which is a self-inflicted outage. Pool *for the dependency*, not for the caller.
- **Retries without jitter**, producing synchronised thundering herds every backoff interval.
- **Timeouts shorter than the downstream p99.9**, which converts a slow-but-succeeding system into a retry storm.
- **Batching on a user-facing path** to improve a throughput number nobody asked about, at the cost of latency everybody notices.
- **Treating p99 of a fan-out service as if it were a component p99**, missing that fan-out multiplies tail exposure (Section 9.3).
- **Capacity planning on averages.** Peak-to-average ratios of 4–10× are normal; planning for the mean guarantees failure at peak.
- **Forgetting that a queue is not free.** Queues trade *rejection* for *delay*, and an unbounded queue trades it for unbounded delay — which users experience as a hang, the worst possible failure mode. Bound every queue.

## 16. Interview perspective

This is the topic that most visibly separates levels, because it is where you either produce numbers or you produce adjectives.

- **Always estimate before designing.** "10,000 DAU × 30 requests = 300,000/day ÷ 86,400 = 3.5 RPS average, ×4 peak multiplier ≈ 14 RPS peak." Thirty seconds of arithmetic that frames the entire rest of the interview.
- **Use Little's Law out loud, by name.** "At 14 RPS and 20 ms service time, `L = λW` gives 0.28 concurrent requests — one server is fine." This single sentence prevents the most common down-levelling mistake, which is over-architecting.
- **Know the latency table cold.** RAM 100 ns, same-DC RTT 0.5 ms, SSD 50 µs, disk seek 10 ms, cross-continent 200 ms. You will use at least three of them in any design interview.
- **Say "we can buy bandwidth, we cannot buy round-trip time."** Then justify multi-region or CDN with the speed-of-light number. It is memorable and it is correct.
- **Bring up utilisation unprompted.** "I'd target 65 %, because M/M/1 says latency is `S/(1−ρ)` and at 95 % you're paying a 20× multiplier." Almost nobody does this.
- **Separate latency from throughput explicitly** when stating requirements: "p99 under 200 ms at 1,400 RPS" is a requirement; "it should be fast" is not.
- **Identify the bottleneck before optimising.** When given a slow system, ask for the breakdown. Candidates who immediately propose solutions without asking where the time goes are solving a problem they have not identified.
- **Mention tail amplification in fan-out designs.** "With 100 shards and 1 % slow each, 63 % of requests hit a slow shard, so I'd hedge after p95."
- **State peak-to-average ratios and why.** "Evening shopping gives us roughly 4×; a flash sale could be 50×, which is a queue, not more servers."

**Red flags interviewers watch for:** saying "it should be fast enough" with no number; confusing bandwidth and latency; never estimating RPS; proposing a distributed architecture for 14 RPS; claiming adding servers fixes latency without mentioning utilisation; using averages for latency; sizing thread pools by intuition; not knowing roughly how long a cross-continent round trip takes; believing N cores means N× speedup; ignoring that batching hurts latency; and — the most telling of all — being unable to say which component in their own design is the bottleneck.

## 17. Knowledge check

Answer before moving on. Show your arithmetic for the numeric ones.

**Conceptual**

1. A teammate reports: "I doubled our server count and throughput doubled, but p99 latency barely moved." Give two distinct, specific explanations that are both consistent with these observations, and state what you would measure to distinguish between them.
2. Explain precisely why a service can be in severe overload — users timing out, queue growing — while CPU utilisation sits at 15 %. Name the resource that is actually exhausted, name the metric that would have shown it, and explain why autoscaling on CPU makes this worse rather than better.
3. You are told "concurrency and parallelism are the same thing; async just means multithreading." Correct this in under five sentences, and give one concrete workload where high concurrency helps enormously and parallelism helps almost not at all, plus one where the reverse is true.

**Practical scenario**

4. BookNook's search service fans out to 60 index shards in parallel and returns when all 60 respond. Each shard has p50 = 12 ms and p99 = 140 ms. The product requirement is p99 ≤ 200 ms for search. (a) Estimate the probability that a given search touches at least one shard above its p99, and explain what that does to the service's own p99. (b) Propose three distinct mitigations, and for each state what it costs you (capacity, correctness, or complexity). (c) The team proposes "make each shard 20 % faster at p50". Explain, with reference to your answer in (a), why this will barely help.

**Calculation**

5. BookNook's checkout service has these measured properties: peak arrival rate 900 requests/second; mean service time 80 ms; 12 workers per pod; and a downstream payment call with p99.9 of 1,100 ms.
   (a) Using Little's Law, how many concurrent requests are in flight at peak? Show the formula and substitution.
   (b) If you target 70 % utilisation, how many concurrency slots and how many pods do you need? Round correctly and say why.
   (c) Using M/M/1 with the aggregated capacity of your answer in (b), compute ρ and the resulting W, and state how much of W is queue wait rather than service.
   (d) A payment-provider incident raises mean service time from 80 ms to 240 ms, with traffic unchanged. Recompute ρ with the same pod count. Is the system stable? If not, compute the minimum number of pods required to return to ρ ≤ 0.70, and state one non-capacity action that would fix it faster.
   (e) BookNook's origin is in Mumbai and 30 % of traffic comes from the US East Coast (RTT ≈ 195 ms). Checkout requires 4 sequential round trips. How much of those users' checkout time is pure network latency, and what single architectural change would reduce it most? Show the calculation.

Reply with your answers and I'll assess them, correct anything shaky, and give model answers before Lesson 15.

---

**Progress checklist**

Phase 1 — Internet and System-Design Foundations
- [x] 1. Client-server architecture
- [x] 2. Stateful versus stateless servers
- [x] 3. IP addresses, ports, sockets, domain names
- [x] 4. DNS resolution and DNS caching
- [x] 5. HTTP requests and responses
- [x] 6. HTTP methods, headers, cookies, sessions, status codes
- [x] 7. HTTPS and TLS
- [x] 8. TCP versus UDP
- [x] 9. HTTP/1.1 vs HTTP/2 vs HTTP/3
- [x] 10. REST APIs
- [x] 11. RPC and gRPC
- [x] 12. GraphQL
- [x] 13. Synchronous versus asynchronous communication
- [x] 14. Latency, throughput, bandwidth, concurrency
- [ ] 15. Availability, reliability, durability, fault tolerance
- [ ] 16. Average latency vs p50/p95/p99, tail latency
- [ ] 17. Idempotency, retries, timeouts, exponential backoff

Phases 2–5: not started.

**NEXT LESSON: Phase 1, Topic 15 — Availability, Reliability, Durability, and Fault Tolerance**

---

# Lesson 15 — Availability, Reliability, Durability, and Fault Tolerance

*Phase 1, Topic 15*

## 1. Simple definition

Four words that get used as synonyms in meetings and mean four genuinely different things. Define them precisely, because the rest of the lesson — and most of Phase 4 — depends on the distinctions.

- **Availability** is **the probability that a system is able to serve a request at a randomly chosen moment**. It answers *"is it up right now?"* Measured as a ratio, usually expressed in nines: `availability = uptime / (uptime + downtime)`. 99.9 % means 43.2 minutes of downtime per 30-day month.
- **Reliability** is **the probability that the system performs correctly for a given duration without failing**. It answers *"does it keep working, and does it give right answers?"* Measured in MTBF (mean time between failures), failure rate, or error rate. A system can be *available* and *unreliable*: it answers every request, and 3 % of the answers are wrong.
- **Durability** is **the probability that data, once acknowledged as written, is never lost**. It answers *"will my data still be there in ten years?"* Measured in nines too, but far more of them — cloud object stores advertise eleven (99.999999999 %). Durability says nothing about whether you can *reach* the data today.
- **Fault tolerance** is **the property of continuing to operate correctly despite the failure of some components**. It is not a measurement; it is a *design characteristic*, and it is the mechanism by which you achieve the other three.

The relationships, which are the part people get wrong:

| | Availability | Reliability | Durability |
|---|---|---|---|
| Question answered | Can I use it now? | Does it work correctly over time? | Is my data safe forever? |
| Unit | % of time (nines) | MTBF, failure rate, error rate | % of objects per year (nines) |
| Typical target | 99.9 – 99.99 % | varies by domain | 99.999999999 % |
| Can be high while the others are low? | **Yes** — up 100 % of the time, returning wrong prices | **Yes** — perfectly correct, but offline 2 hours a day | **Yes** — S3 during a regional outage: data is perfectly safe and completely unreachable |

Concrete examples of each decoupling:

- **Available but unreliable**: BookNook's search returns a `200 OK` in 40 ms for every request, but a stale index means 8 % of results point at books that no longer exist. Uptime graphs are perfect; users are furious.
- **Reliable but unavailable**: a batch billing system that has never once produced an incorrect invoice in six years, and is offline for a four-hour maintenance window every Sunday.
- **Durable but unavailable**: a storage region loses network connectivity. Every byte is intact, replicated three ways, checksummed. Nobody can read it for six hours. Durability 100 %, availability 0 %.
- **Available but not durable**: an in-memory cache. Answers instantly, always up — and loses everything on restart. That is a *correct* design choice, as long as the cache is not the system of record.

Two supporting terms, defined now:

- A **fault** is a defect or malfunction in a component (a disk dies, a process panics, a cable is cut). A **failure** is when the *system as a whole* stops delivering its service. **Fault tolerance is the business of preventing faults from becoming failures.** Every running system has faults all the time; good ones have very few failures.
- **Redundancy** is having more of a component than you strictly need, so that a fault in one does not become a failure. Redundancy is the primary (but not only) technique for fault tolerance, and Section 4 explains exactly when it stops working.

## 2. Why it exists

These concepts exist because **components fail constantly and unavoidably, and businesses still need to make promises.**

Start from hardware reality. Typical annualised failure rates (AFR), approximate:

- Hard disk drive: **~2 % per year** (higher in the first and last years of life).
- SSD: ~0.5 % per year.
- Server motherboard/PSU: ~2–4 % per year.
- A single rack of 40 servers: expect **roughly one server failure per rack per quarter**.
- A datacentre with 100,000 disks at 2 % AFR: **2,000 disk failures per year ≈ 5–6 per day.**

At scale, failure is not an event; it is a **background rate**. A design that assumes components do not fail is not optimistic, it is arithmetically false.

Four problems these concepts solve:

1. **Turning "it should be reliable" into a budget.** 99.9 % is 43.2 minutes a month. That is a number you can design against, measure, and be held to. "Highly available" is not.
2. **Pricing reliability honestly.** Each additional nine costs roughly 3–10× more. Knowing that 99.99 % needs multi-AZ, automated failover, and a 24/7 on-call rota lets a business decide whether it wants to pay for it.
3. **Predicting composite behaviour.** Six services at 99.9 % give 99.4 %, not 99.9 % (Lesson 13 showed this). Without the multiplication rule, teams are shocked by outages that were mathematically inevitable.
4. **Separating "can't reach it" from "lost it".** These need completely different mitigations: availability is solved with redundancy and failover, durability is solved with replication, checksums and backups. Conflating them produces systems that are replicated three times in a single rack — triple-protected against a disk failure and totally defenceless against a power failure.

The historical arc: in the 1990s you achieved reliability by buying **more reliable components** (mainframes, RAID, redundant PSUs). Google's insight in the early 2000s was that it is far cheaper to buy **unreliable components and tolerate their failure in software**. That shift — from fault *avoidance* to fault *tolerance* — is the foundation of every modern distributed system, and it is why Phase 2 onwards is mostly about redundancy, replication and failover.

## 3. Real-world analogy

**A commercial airliner.**

- **Reliability** is "the engines do not fail". Measured as MTBF: a modern turbofan has an in-flight shutdown rate of roughly 1 per 100,000 flight hours.
- **Fault tolerance** is "the plane flies on one engine". Two engines, each able to carry the aircraft alone. A fault (engine failure) does not become a failure (crash).
- **Availability** is "the plane is ready to fly when scheduled". A perfectly safe aircraft grounded for maintenance has availability 0 % and reliability 100 %.
- **Durability** is the flight data recorder: built to survive 3,400 g of impact, 1,100 °C fire and 6,000 m of seawater. It does not help you fly. It guarantees the *data* survives even when the system does not.

Extend to maintenance: airlines reduce **MTTR** (mean time to repair) obsessively — pre-positioned spare parts, standardised procedures, swappable line-replaceable units — because a plane on the ground earns nothing. This is exactly the MTTR argument in Section 4.

**Where the analogy breaks down**

| Airliner | Real software systems |
|---|---|
| Two engines fail independently (different fuel pumps, separate controllers) | Your two replicas usually share a rack, a power feed, an availability zone, a config file, a certificate, and a deploy pipeline. **Software redundancy is far more correlated than hardware redundancy**, which is why `1−(1−a)ⁿ` systematically overstates real availability |
| Failures are mechanical and gradual; inspections catch them | Software failures are **discrete and simultaneous** — a bad deploy kills all N replicas in seconds, and a certificate expires on every node at the same instant |
| Redundancy is physically separate by design and regulation | Adding a replica often adds a *shared* dependency (the same service-discovery system, the same database), so the n+1th replica can *reduce* availability |
| A failed engine is obvious | A failed component can be **partially** failed: answering health checks while returning wrong data, or slow but not down. Grey failure is harder than clean failure |
| Pilots are trained for failure continuously | Most teams discover their failover doesn't work *during* the outage, because they never tested it |
| The plane either flies or doesn't | Software can **degrade gracefully**: serve the catalogue without personalisation, accept orders without live stock counts. There is a whole spectrum between up and down |
| There is no "undo" for a crash | Many software failures are recoverable: roll back, replay the queue, restore from backup. MTTR is a *variable you control*, not a constant |

The first row is the single most important: **the arithmetic of redundancy assumes independence, and independence is exactly what real deployments lack.**

## 4. Detailed technical explanation

### 4.1 The nines table

Availability is quoted as a percentage of time. Convert it to downtime, because minutes are intuitive and percentages are not. (Year = 365 × 24 × 60 = 525,600 minutes; month = 30 days = 43,200 minutes; week = 10,080 minutes; day = 1,440 minutes.)

| Availability | Downtime per year | per month (30 d) | per week | per day |
|---|---|---|---|---|
| 90 % ("one nine") | 36.5 days | 72 hours | 16.8 hours | 2.4 hours |
| 99 % ("two nines") | 3.65 days (87.6 h) | 7.2 hours | 1.68 hours | 14.4 minutes |
| **99.9 % ("three nines")** | **8.76 hours** | **43.2 minutes** | **10.1 minutes** | **1.44 minutes** |
| 99.95 % | 4.38 hours | 21.6 minutes | 5.04 minutes | 43.2 seconds |
| **99.99 % ("four nines")** | **52.6 minutes** | **4.32 minutes** | **1.01 minutes** | **8.64 seconds** |
| 99.999 % ("five nines") | 5.26 minutes | 25.9 seconds | 6.05 seconds | 0.86 seconds |
| 99.9999 % ("six nines") | 31.5 seconds | 2.59 seconds | 0.61 seconds | 0.086 seconds |

Read the operational implications, which are what actually matter:

- **99.9 %** — a human can be paged, wake up, read a dashboard and restart something, a few times a month. Achievable with one region, redundant instances, and a competent on-call rota.
- **99.99 %** — 4.32 minutes a month means **humans cannot be in the recovery path**. Detection and failover must be automated and must complete in under a minute. This typically requires multi-AZ, automated leader election, health-checked load balancing, and canary deploys with automatic rollback.
- **99.999 %** — 25.9 seconds a month. Your *deployments* must not cause downtime, your *certificate renewals* must not, your *database failovers* must not. This usually requires active-active multi-region, and the cost is typically 5–10× a 99.9 % design. Very few businesses genuinely need it, and almost every one claims to.

A crucial and widely ignored subtlety: **planned maintenance counts.** If your SLA does not exclude maintenance windows, a 2-hour upgrade blows four months of a 99.99 % budget in one evening. This is why zero-downtime deployment techniques (blue-green, rolling, canary — Phase 4) are a *reliability* technology, not a convenience.

### 4.2 Availability in series (components that must all work)

If a request must pass through N components, and each must be working, availabilities **multiply**:

```
A_total = A₁ × A₂ × ... × A_N
```

Because every `Aᵢ < 1`, the product is **always worse than the worst component**. Adding dependencies can only reduce availability. This is the mathematical form of "every service you call is a service that can take you down", and it is why Lesson 13 argued so hard for moving work off the synchronous path.

### 4.3 Availability in parallel (redundant components, any one suffices)

If N redundant components can each serve the request alone, the system is down only when **all** are down:

```
A_total = 1 − (1 − A)ⁿ        (identical components)
A_total = 1 − (1−A₁)(1−A₂)... (different components)
```

The gain is dramatic — on paper:

| Single-component availability | 1 instance | 2 instances | 3 instances |
|---|---|---|---|
| 99 % | 99 % | 99.99 % | 99.9999 % |
| 99.9 % | 99.9 % | 99.9999 % | 99.9999999 % |
| 99.5 % | 99.5 % | 99.9975 % | 99.999988 % |

Two 99 % servers theoretically give 99.99 %. **This is almost never what you observe in production**, and Section 4.6 explains exactly why.

Note also the hidden series component: redundant instances usually sit behind a load balancer, and the load balancer is in *series*:

```
A_system = A_lb × (1 − (1 − A_app)²)
```

Your redundancy is capped by the availability of the thing that distributes to it. Redundancy does not remove single points of failure; it **moves** them. The discipline is to keep asking "what is now the single thing whose loss takes everything down?" — it is usually DNS, a load balancer, a certificate authority, a config service, or an identity provider.

### 4.4 MTBF, MTTR, and the availability identity

Two measurements define a repairable system:

- **MTBF (mean time between failures)** — average operating time between one failure and the next. Reliability.
- **MTTR (mean time to repair/recover)** — average time from failure to service restored. This includes **detect + diagnose + decide + act + verify**, not just the fix.

They combine into the fundamental identity:

```
Availability = MTBF / (MTBF + MTTR)
```

And equivalently, annual downtime:

```
Failures per year  = 8,760 h / MTBF
Downtime per year  = (8,760 / MTBF) × MTTR
```

**Why reducing MTTR usually beats increasing MTBF**, in four points:

1. **The maths is symmetric but the costs are not.** Availability depends only on the *ratio* MTBF/MTTR. Halving MTTR and doubling MTBF improve availability identically — but halving MTTR is usually an engineering-process change (better alerting, a runbook, a one-click rollback), while doubling MTBF means making the software fundamentally less buggy, which is slow, expensive and uncertain.
2. **MTBF has a hard floor you do not control.** Your cloud provider's network, a BGP leak, a DNS registrar, an expired upstream certificate, a datacentre fire. You cannot make those failures less frequent. You can always make your response faster.
3. **MTTR improvements are composable and compounding.** Faster detection improves recovery from *every* failure class simultaneously, including ones you have not seen yet. A bug fix improves recovery from exactly one.
4. **MTTR is measurable and improvable this quarter.** You know your detection time, your rollback time, your failover time. You can drill them. MTBF is only observable in hindsight, over years.

The one caveat, stated so this is not advice-by-slogan: **when a failure is not recoverable, MTTR reduction does not apply.** Data corruption written to all replicas, a deleted encryption key, a shipped-to-the-wrong-customer order — for those, prevention (MTBF, and durability) is the only lever. The rule is therefore: *reduce MTTR for recoverable failures; prevent unrecoverable ones.*

### 4.5 Durability, and why it is not availability

Durability is a statement about **data loss**, not reachability. S3's "eleven nines" (99.999999999 % annual durability) means that if you store 10 million objects, you should expect to lose one object every 10,000 years. Its *availability* SLA, by contrast, is 99.9 %. These differ by eight orders of magnitude on purpose:

- **Durability** is achieved by writing the same data to many independent places (replication or erasure coding), verifying it with checksums, continuously scrubbing for silent corruption, and **repairing quickly** when a copy is lost.
- **Availability** is achieved by being reachable, which depends on networks, load balancers, DNS, authentication and capacity — all things that can break while the bytes remain perfectly safe.

The mechanism behind high durability is worth understanding because it reveals the general principle. With three replicas and a 2 % annual disk failure rate, naive independence gives `0.02³ = 8×10⁻⁶` — only about five nines. The extra six nines come from three things:

1. **Fast repair.** If a lost replica is rebuilt in one hour rather than one week, the window during which you are one failure from danger shrinks by 168×, and because you need *two more* failures in that window, the risk falls roughly quadratically.
2. **Erasure coding.** Instead of 3 full copies (200 % overhead), split an object into k data + m parity fragments (e.g. 6+3) spread across independent failure domains; any 6 of 9 reconstruct it. Better durability at ~50 % overhead instead of 200 %.
3. **Independence by placement.** Fragments go in different racks, different power domains, different availability zones. Correlated failure is the enemy of durability exactly as it is for availability.

And the two things durability explicitly does **not** protect against, which is where most real data loss comes from:

- **`DELETE FROM orders;` with no `WHERE`.** Replication faithfully replicates the deletion to all copies, instantly, with eleven nines of reliability. **Replication is not backup.** You need point-in-time recovery, versioning, soft deletes, and backups in a *separate account* with separate credentials.
- **Silent application-level corruption.** If your code writes wrong data, durability guarantees the wrong data survives forever.

### 4.6 Correlated failure — why redundancy underdelivers

The formula `A = 1 − (1 − a)ⁿ` assumes **statistical independence**. Real systems violate this constantly. Common correlation sources, roughly in order of how often they cause outages:

| Correlation source | How it defeats redundancy |
|---|---|
| **Same bad deploy** | All N replicas run identical code. A bad release kills them all within the rollout window. Redundancy gives **zero** protection. This is the #1 cause of modern outages. |
| **Expired certificate** | Every node has the same certificate with the same expiry timestamp. At that instant, all N fail simultaneously. |
| **Same rack / same switch / same power feed** | The rack's top-of-rack switch dies; all replicas in it vanish together. |
| **Same availability zone** | A zone-level power, cooling or network event takes out everything inside it. |
| **Same shared dependency** | All replicas call the same config service, the same auth service, the same DNS resolver, the same database primary. That dependency is in *series* no matter how many replicas you have. |
| **Load redistribution (cascading overload)** | When 1 of 3 replicas dies, the other 2 absorb 50 % more traffic each. If they were at 70 % utilisation they are now at 105 % — and they fall over too. **The failure of one replica caused the failure of the rest.** |
| **Same resource leak / same bug** | All replicas started at the same time, leak memory at the same rate, and OOM within minutes of each other. |
| **Same clock / same cron** | Every node runs the same 02:00 job and saturates the database simultaneously. |
| **Same region / same provider** | Multi-AZ does not save you from a regional control-plane failure. |

Model it simply. Split unavailability `u = 1 − a` into a **common-mode** fraction `c` (failures that hit everything at once) and an independent remainder:

```
u_pair = c·u + ((1 − c)·u)²
```

With `a = 99.9 %` (`u = 0.001`) and just **20 % of failures being common-mode**:

```
u_pair = 0.2(0.001) + (0.8 × 0.001)²
       = 0.0002 + 0.00000064
       = 0.00020064
A_pair = 99.97994 %
```

Compare with the independence assumption, which predicted 99.9999 %. **The real pair is roughly 500× less reliable than the formula promised** — 105 minutes of annual downtime instead of 0.5 minutes. Notice what dominates the expression: the common-mode term `0.0002` is 300× larger than the independent term. **Past a certain point, adding replicas does nothing at all; only reducing `c` helps.** Reducing `c` means: separate availability zones, staggered deploys with canaries, staggered certificate expiries, removing shared dependencies, and capacity headroom so surviving replicas can absorb redistributed load.

### 4.7 Fault tolerance techniques and graceful degradation

**Fault tolerance** is the set of mechanisms that stop faults becoming failures:

- **Redundancy**: N+1 or 2N instances; active-active or active-passive.
- **Health checks and automatic removal**: take a sick instance out of rotation before users find it.
- **Timeouts, retries with backoff and jitter, idempotency** (Lesson 17).
- **Circuit breakers**: stop calling a failing dependency so you fail fast instead of piling up (Phase 4, Topic 14).
- **Bulkheads**: separate thread/connection pools per dependency so one failure cannot consume all your concurrency.
- **Load shedding and rate limiting**: reject some traffic (`429`/`503`) to keep the rest healthy. Serving 90 % of users beats serving 0 %.
- **Replication and failover**: a standby promoted on primary loss (Phase 2).
- **Backups and point-in-time recovery**: the only defence against logical corruption.
- **Chaos engineering**: deliberately injecting faults so you discover that failover is broken on a Tuesday afternoon rather than at 3 a.m.

**Graceful degradation** is the design discipline of defining what "partially working" means *before* the incident. For BookNook, in order of preference:

| Level | What works | What is sacrificed | Triggered when |
|---|---|---|---|
| 0 — Full service | Everything | — | Normal |
| 1 — Degraded personalisation | Browse, search, cart, checkout | Recommendations become a static bestseller list | Recommendation service unhealthy |
| 2 — Stale catalogue | Browse, search, cart, checkout | Stock counts show "usually ships in 2 days" instead of live counts | Inventory service unhealthy |
| 3 — Read-only mode | Browse, search, view past orders | **No new orders**; cart is preserved | Primary database in failover |
| 4 — Static fallback | Cached homepage and top 1,000 book pages from the CDN | Everything dynamic | Application tier down entirely |
| 5 — Branded error page | A clear message, a status link, an ETA | Everything | Total failure |

Every level is a *deliberate product decision made in advance*, requiring a feature flag, a fallback code path, and a tested trigger. A system with no designed degradation has exactly two states — perfect and dead — and the gap between them is where users lose trust.

Forward references: how to *measure* and *promise* these numbers (SLIs, SLOs, SLAs, error budgets) is **Phase 4, Topic 26**; high availability architectures, failover strategies, and disaster recovery with RPO/RTO are **Phase 4, Topics 27–29**; replication and leader election are Phase 2 and Phase 3.

## 5. Request / data flow

Follow a single BookNook checkout through a **real failure** and watch fault tolerance work, step by step.

1. **t = 0 s.** 1,400 requests/second arriving normally. BookNook runs 6 app pods spread across 3 availability zones (2 per AZ), behind a load balancer, on a PostgreSQL primary in AZ-a with a synchronous standby in AZ-b.
2. **t = 0 s.** The load balancer health-checks every pod every 5 seconds via `GET /healthz`, requiring 2 consecutive failures before ejection (so a single blip does not remove a healthy pod).
3. **t = 12 s.** AZ-a loses power. **Two app pods and the database primary disappear simultaneously** — a *correlated* failure, exactly as Section 4.6 warned.
4. **t = 12 s.** In-flight requests on those 2 pods are lost. Their clients see a connection reset. **Approximately 33 % of requests in that instant fail.**
5. **t = 12–22 s.** The load balancer's health checks to the dead pods time out. After 2 consecutive failures (~10 s) it ejects them. *This 10-second window is pure MTTR and it is tunable: aggressive checks eject faster but risk flapping on transient blips.*
6. **t = 22 s.** Traffic is now spread over 4 pods instead of 6. Each pod's load rises by 50 %: utilisation goes from 0.63 to 0.94. From Lesson 14, `W = S/(1−ρ)` means latency jumps from 133 ms to **833 ms**. **The surviving pods are now the problem.** This is cascading overload, and it is why N+1 capacity planning must assume a whole AZ can vanish.
7. **t = 22 s.** Simultaneously, the database primary is gone. Writes fail; reads from the AZ-c replica still work.
8. **t = 22–45 s.** The database cluster manager detects the primary is unreachable (3 missed heartbeats, ~15 s), confirms quorum with the remaining nodes to avoid split-brain, and **promotes the AZ-b standby**. Because replication was synchronous, no acknowledged write is lost — **RPO = 0**. Promotion takes ~20 s. **RTO ≈ 33 s.**
9. **t = 22–45 s.** During promotion, checkout writes fail. The application **does not return a 500**: it detects write failures and switches to **degradation level 3 (read-only)**. Users can browse, search, and see their cart. Checkout shows: *"We're completing a brief maintenance step — your cart is saved, please try again in a minute."*
10. **t = 25 s.** Circuit breakers in the app open for the write path after 20 consecutive failures, so the app stops burning its connection pool on a database that cannot accept writes. Failing *fast* preserves capacity for the reads that still work.
11. **t = 30 s.** Autoscaling, triggered by in-flight-requests-per-pod exceeding the target, begins launching 3 replacement pods in AZ-b and AZ-c.
12. **t = 45 s.** Database promotion completes. The app's connection pool reconnects to the new primary. Circuit breakers enter half-open state, allow a trial write, and close on success. **Checkout works again.**
13. **t = 95 s.** The 3 new pods pass readiness checks and join the pool. Utilisation drops from 0.94 back to 0.63; latency returns to 133 ms.
14. **t = 95 s onwards.** Orders that failed between t=12 s and t=45 s are *not* silently lost: clients retried with the same `Idempotency-Key` (Lesson 17), so the retries either created the order exactly once or returned the original.
15. **Asynchronous consumers** (email, invoicing) were unaffected. Their messages accumulated in the broker and drained over the following two minutes — the queue absorbed the outage (Lesson 13).
16. **Total user impact:** ~33 seconds of failed writes, ~80 seconds of elevated latency, zero data loss, zero lost orders after client retries.
17. **Post-incident:** the measured MTTR was 33 seconds for writes. The team finds that step 6 (cascading overload) was avoidable: running 9 pods instead of 6 would have kept post-failure utilisation at 0.63 rather than 0.94. **That is the price of AZ-level fault tolerance: you must run ~1.5× capacity so that losing one of three AZs is survivable.**

The point of walking through this: **availability is not a property you install, it is the sum of a dozen timers, thresholds and fallbacks that each contribute seconds to MTTR.**

## 6. ASCII diagram

```
SERIES (all must work) — availability MULTIPLIES, always downward

 Client ─▶ [DNS] ─▶ [LB] ─▶ [App] ─▶ [Cache] ─▶ [DB] ─▶ [Payments]
          99.999%  99.99%  99.9%    99.95%    99.95%    99.95%

 A = 0.99999 × 0.9999 × 0.999 × 0.9995 × 0.9995 × 0.9995 = 0.997392
   = 99.739 %  ->  22.8 hours of downtime per year
   WORSE than every single component in the chain.


PARALLEL (any one suffices) — availability COMPOUNDS upward

                     ┌─ [App 1]  99.9% ─┐
 Client ─▶ [LB] ─────┼─ [App 2]  99.9% ─┼──▶ [DB]
          99.99%     └─ [App 3]  99.9% ─┘   99.95%
                      \_______ in parallel _______/

 A_app_tier = 1 - (1 - 0.999)^3 = 1 - 0.001^3 = 1 - 0.000000001 = 99.9999999%
 A_system   = 0.9999 × 0.9999999990 × 0.9995 = 99.9399 %
                ^^^^                  ^^^^^^
                the LB and the DB are now the limits -- redundancy MOVED
                the single point of failure, it did not remove it.


CORRELATED FAILURE — what the formula does not tell you

  THE FORMULA SAYS             REALITY: shared failure domain
  ┌─────────┐ ┌─────────┐      ┌──────────────────────────────┐
  │ App 1   │ │ App 2   │      │   AZ-a, rack 7, same deploy  │
  │ 99.9%   │ │ 99.9%   │      │  ┌─────────┐  ┌─────────┐    │
  └─────────┘ └─────────┘      │  │ App 1   │  │ App 2   │    │
   independent => 99.9999%     │  └─────────┘  └─────────┘    │
                               └──────────────────────────────┘
                                 one power failure, one bad deploy,
                                 one expired cert => BOTH die at once
                                 with 20% common-mode: 99.98%, not 99.9999%
                                 (105 min/yr of downtime, not 0.5 min/yr)


MTBF / MTTR ON A TIMELINE

 │◀────────── MTBF = 1000 h ──────────▶│◀MTTR▶│◀───── MTBF ─────▶│
 ├─────────────── UP ──────────────────┤ DOWN ├────── UP ────────┤
 ▲                                     ▲      ▲
 repaired                           failure  repaired

 A = MTBF / (MTBF + MTTR) = 1000 / 1004 = 99.602 %   (MTTR = 4 h)
 A = MTBF / (MTBF + MTTR) = 1000 / 1000.25 = 99.975 % (MTTR = 15 min)
     ^ same hardware, same bugs, 8x less downtime -- PURELY from faster recovery


AVAILABILITY vs DURABILITY — orthogonal axes

            HIGH DURABILITY
                  │
   S3 during a    │   S3 normal operation
   regional       │   (data safe AND reachable)
   outage         │
 ─────────────────┼───────────────── HIGH AVAILABILITY
   a disk with    │   in-memory cache
   no redundancy, │   (always up, loses
   powered off    │    everything on restart)
                  │
```

## 7. Detailed example

### BookNook: buying availability, one nine at a time

BookNook starts as Lesson 1 left it — one VM running app and database. Each step below shows what you buy and what it costs.

**v0 — single VM.** Single app process, single database, single disk, single AZ.

```
App process      99.5 %   (crashes, OOMs, deploys)
VM / hypervisor  99.9 %
Disk             99.9 %
Network/DNS      99.99 %
A = 0.995 × 0.999 × 0.999 × 0.9999 = 0.99301 -> 99.30 %
Downtime = 0.00699 × 525,600 = 3,674 min = 61.2 hours/year
```

Two and a half days a year. Every deploy is an outage.

**v1 — process supervisor + 3 app instances behind a load balancer, same AZ.**

```
App tier = 1 - (1 - 0.995)^3 = 1 - 0.000000125 = 0.999999875
A = 0.9999 (LB) × 0.999999875 (apps) × 0.999 (VM/disk for DB) × 0.9999 (net)
  = 0.99880  -> 99.88 %
Downtime = 0.0012 × 525,600 = 630 min = 10.5 hours/year
```

Downtime fell 6×, and the **database is now the dominant term**. Redundancy moved the bottleneck, exactly as predicted. Note also that all three app instances are in one AZ, so the real number is worse than this once correlation is included.

**v2 — spread across 3 AZs, database with a synchronous standby and automatic failover.**

```
DB with failover: a single DB is 99.9 %; with automatic promotion in ~30 s,
unavailability is dominated by failover time, not node failure.
  Failures per year  = 8,760 / 1,000 h MTBF = 8.76
  Downtime per year  = 8.76 × 30 s = 263 s = 4.4 minutes
  A_db = 1 - (263 / 31,536,000 s) = 0.9999917 -> 99.999 %

A = 0.9999 (LB) × 0.9999999 (apps, 3 AZs) × 0.9999917 (DB) × 0.9999 (net)
  = 0.99979  -> 99.979 %
Downtime ≈ 110 minutes/year
```

**v3 — add correlated-failure reality.** Assume 25 % of remaining unavailability is common-mode (bad deploys, certificate expiry, config pushes, shared dependencies):

```
u_measured = 0.00021
Common-mode portion = 0.25 × 0.00021 = 0.0000525  -- UNAFFECTED by more replicas
Independent portion = 0.75 × 0.00021 = 0.0001575

Doubling the app tier again removes essentially none of the common-mode part.
Realistic ceiling without process change ≈ 99.979 %
```

To go further BookNook must attack `c`, not `n`: canary deploys with automatic rollback (kills the bad-deploy class), certificate monitoring with staggered expiries, config changes rolled out progressively, and chaos drills that prove failover works. **Beyond three nines, availability is a process problem, not an architecture problem.**

The cost ladder, approximate and typical:

| Target | Architecture | Relative infra cost | Team requirement |
|---|---|---|---|
| 99 % | Single server, backups | 1× | Business hours |
| 99.9 % | Multi-instance, 1 AZ, automated restarts | 2× | On-call rota |
| 99.95 % | Multi-AZ, DB failover | 3× | On-call + runbooks |
| 99.99 % | Multi-AZ active-active, canary deploys, automated rollback, no humans in recovery | 5–8× | Dedicated reliability work, chaos testing |
| 99.999 % | Multi-region active-active, cell-based isolation | 15–30× | A real SRE organisation |

### Contrasting example: a hospital patient-monitoring system

Same four concepts, radically different weights.

- **Availability target is extreme (99.999 %+) but *local*.** A bedside monitor must work during a network outage, a power failure and a server failure, so intelligence is pushed to the *device*, not the cloud. Redundancy is achieved by making each node autonomous rather than by adding replicas behind a load balancer.
- **Reliability dominates availability.** A monitor that is up 100 % of the time and reports a wrong heart rate is far more dangerous than one that clearly says "sensor disconnected". The correct failure mode is **fail loud and fail visible**, never fail silent. BookNook would rather show a slightly stale stock count than nothing; a hospital would rather show nothing than a wrong number.
- **Durability is regulated, not chosen.** Patient records must be retained for years with audit trails and immutability. The requirement comes from law, not from an availability calculation.
- **Graceful degradation is inverted.** BookNook degrades by *dropping features*. A monitor degrades by *dropping everything except the alarm*: if all else fails, the local buzzer must still sound. The last surviving capability is the safety-critical one.
- **Fault tolerance is "fail-safe", not "fail-open".** An infusion pump that loses its controller stops delivering, it does not continue optimistically. Compare BookNook, where a failed recommendation service correctly falls back to a bestseller list and nobody is harmed.

The transferable lesson: **"what should happen when this fails?" is a product question with a domain-specific answer**, and you cannot derive it from the arithmetic. The arithmetic tells you how often; the domain tells you what.

## 8. Code or configuration example

This code **illustrates how availability is computed and how fault tolerance is implemented. It is not production-ready**: the circuit breaker is simplified (no half-open request limiting, no sliding window, not thread-safe), the health check is naive, and there is no observability.

```python
# ---------------------------------------------------------------------------
# 1. AVAILABILITY ARITHMETIC. Use this before drawing any architecture.
# ---------------------------------------------------------------------------
MINUTES_PER_YEAR = 365 * 24 * 60          # 525,600 -- memorise this number


def series(*availabilities: float) -> float:
    """Components that must ALL work. Multiplies -> always WORSE than the worst part.
    Every synchronous dependency you add goes in here."""
    result = 1.0
    for a in availabilities:
        result *= a
    return result


def parallel(availability: float, n: int) -> float:
    """N redundant components, ANY ONE suffices. System is down only if ALL are down.
    WARNING: assumes INDEPENDENT failures, which production almost never provides."""
    return 1 - (1 - availability) ** n


def downtime_minutes_per_year(availability: float) -> float:
    return (1 - availability) * MINUTES_PER_YEAR


def parallel_with_correlation(availability: float, n: int, common_mode: float) -> float:
    """The honest version. `common_mode` is the fraction of failures that hit EVERY
    replica at once: same deploy, same cert, same AZ, same shared dependency.
    The common-mode term does NOT shrink with n -- that is the whole point."""
    u = 1 - availability
    u_common = common_mode * u                      # redundancy cannot help here
    u_independent = ((1 - common_mode) * u) ** n    # redundancy helps a lot here
    return 1 - (u_common + u_independent)


chain = series(0.99999, 0.9999, 0.999, 0.9995, 0.9995, 0.9995)
print(f"6-component chain: {chain:.6f} -> {downtime_minutes_per_year(chain):.0f} min/yr")
# 0.997392 -> 1371 min/yr (22.8 hours)

ideal = parallel(0.999, 2)
real  = parallel_with_correlation(0.999, 2, common_mode=0.20)
print(f"2 replicas, independent: {ideal:.7f} -> {downtime_minutes_per_year(ideal):.1f} min/yr")
print(f"2 replicas, 20% common : {real:.7f} -> {downtime_minutes_per_year(real):.1f} min/yr")
# 0.9999990 -> 0.5 min/yr   vs   0.9997994 -> 105.5 min/yr  (211x worse)


# ---------------------------------------------------------------------------
# 2. MTBF / MTTR. Shows WHY faster recovery beats fewer failures.
# ---------------------------------------------------------------------------
def availability_from_mtbf_mttr(mtbf_hours: float, mttr_hours: float) -> float:
    """A = MTBF / (MTBF + MTTR). Only the RATIO matters -- which is why halving
    MTTR and doubling MTBF are mathematically equivalent and economically not."""
    return mtbf_hours / (mtbf_hours + mttr_hours)


base    = availability_from_mtbf_mttr(1000, 4.0)      # 99.602 %
mtbf_2x = availability_from_mtbf_mttr(2000, 4.0)      # 99.800 % -- rewrite the software
mttr_15 = availability_from_mtbf_mttr(1000, 0.25)     # 99.975 % -- write a rollback script
mttr_5  = availability_from_mtbf_mttr(1000, 0.0833)   # 99.992 % -- automate detection
for name, a in [("baseline", base), ("MTBF x2", mtbf_2x),
                ("MTTR 15min", mttr_15), ("MTTR 5min", mttr_5)]:
    print(f"{name:12} {a*100:.4f}%  {downtime_minutes_per_year(a):7.1f} min/yr")


# ---------------------------------------------------------------------------
# 3. FAULT TOLERANCE: a circuit breaker. Stops a failing dependency from
#    consuming all your concurrency (the Lesson 14 cascade).
# ---------------------------------------------------------------------------
import time


class CircuitBreaker:
    """States: CLOSED (normal) -> OPEN (fail fast) -> HALF_OPEN (one trial) -> CLOSED.
    Without this, every worker blocks on a dead dependency until timeout, and a
    single failed service takes down a healthy one."""

    def __init__(self, failure_threshold=20, recovery_timeout=30.0):
        self.failure_threshold = failure_threshold   # consecutive failures before opening
        self.recovery_timeout = recovery_timeout     # seconds to wait before a trial call
        self.failures = 0
        self.state = "CLOSED"
        self.opened_at = 0.0

    def call(self, fn, fallback):
        # OPEN: do not even attempt the call. Failing in 0 ms preserves capacity,
        # which is the whole point -- a fast failure is a form of availability.
        if self.state == "OPEN":
            if time.monotonic() - self.opened_at < self.recovery_timeout:
                return fallback()                    # GRACEFUL DEGRADATION, not an error
            self.state = "HALF_OPEN"                 # time to probe whether it recovered

        try:
            result = fn()
        except Exception:
            self.failures += 1
            # HALF_OPEN failing re-opens immediately: do not hammer a sick dependency.
            if self.state == "HALF_OPEN" or self.failures >= self.failure_threshold:
                self.state = "OPEN"
                self.opened_at = time.monotonic()
            return fallback()
        else:
            self.failures = 0                        # reset ONLY on success
            self.state = "CLOSED"
            return result


# ---------------------------------------------------------------------------
# 4. GRACEFUL DEGRADATION: decide the fallback BEFORE the incident, in code.
# ---------------------------------------------------------------------------
recommender_breaker = CircuitBreaker(failure_threshold=10, recovery_timeout=20)

def get_recommendations(user_id: str) -> list:
    return recommender_breaker.call(
        fn=lambda: recommender_service.fetch(user_id, timeout=0.3),
        # Level 1 degradation: a static bestseller list. Slightly worse product,
        # fully working page. The alternative -- a 500 -- loses the whole sale.
        fallback=lambda: cached_bestsellers(),
    )


# ---------------------------------------------------------------------------
# 5. HEALTH CHECKS: liveness and readiness are DIFFERENT and confusing them
#    causes outages.
# ---------------------------------------------------------------------------
@app.get("/healthz")          # LIVENESS: "is this process wedged?" -> restart if not OK.
def liveness():
    # Must NOT check dependencies. If it did, a database blip would make the
    # orchestrator restart every healthy pod at once -- turning a partial
    # outage into a total one. This is a real and very common production fire.
    return {"status": "ok"}


@app.get("/readyz")           # READINESS: "should I receive traffic right now?"
def readiness():
    # MAY check dependencies, because the response is 'remove from load balancer',
    # not 'kill the process'. A pod that cannot reach the DB should stop receiving
    # traffic but must NOT be restarted.
    checks = {
        "db": db.ping(timeout=0.5),
        "cache": cache.ping(timeout=0.2),      # cache is OPTIONAL -- see below
    }
    # Degrade, don't die: a missing cache means slow, not broken, so stay ready.
    if not checks["db"]:
        return JSONResponse({"status": "not_ready", "checks": checks}, status_code=503)
    return {"status": "ready", "checks": checks, "degraded": not checks["cache"]}
```

```yaml
# Illustrative deployment configuration. Every setting here exists to reduce
# MTTR or to reduce correlated failure -- the only two levers past three nines.
replicas: 9                    # NOT 6. Must survive losing one of three AZs while
                               # keeping utilisation at 0.63 (see Lesson 14's cliff):
                               # 9 pods -> lose 3 -> 6 remain -> load +50% -> still OK.

topologySpreadConstraints:
  - topologyKey: topology.kubernetes.io/zone
    maxSkew: 1                 # force even spread across AZs: attacks CORRELATION,
    whenUnsatisfiable: DoNotSchedule   # which is worth more than extra replicas

strategy:
  type: RollingUpdate
  rollingUpdate:
    maxUnavailable: 0          # never reduce capacity during a deploy
    maxSurge: 2                # add new pods before removing old ones

livenessProbe:
  httpGet: { path: /healthz, port: 8080 }
  periodSeconds: 10
  failureThreshold: 3          # 30 s before restart: tolerant of transient blips
                               # (too aggressive = restart storms during load spikes)

readinessProbe:
  httpGet: { path: /readyz, port: 8080 }
  periodSeconds: 5
  failureThreshold: 2          # 10 s to eject a sick pod -- this IS your MTTR for
                               # a single-pod fault. Shorter = faster recovery but
                               # more flapping. This number is a real trade-off.

podDisruptionBudget:
  minAvailable: 6              # voluntary disruptions (node drains, upgrades) can
                               # never take you below the AZ-failure survival line

canary:
  steps: [5%, 25%, 50%, 100%]  # attacks the #1 cause of correlated failure: a bad
  analysis:                    # deploy reaching all replicas simultaneously
    metric: error_rate
    threshold: 0.01
    auto_rollback: true        # rollback WITHOUT a human = minutes of MTTR removed

backups:
  schedule: "every 6h"
  point_in_time_recovery: true # REPLICATION IS NOT BACKUP. PITR is the only defence
  destination_account: separate # against `DELETE FROM orders` and ransomware
  restore_drill: monthly       # an untested backup is a hypothesis, not a backup
```

## 9. Numerical example

Every assumption stated, every step shown.

### 9.1 Deriving the nines table

*Assumption*: a year is 365 days = 8,760 hours = 525,600 minutes = 31,536,000 seconds. A "month" is 30 days = 43,200 minutes.

```
Downtime = (1 - availability) × period

99 %     : 0.01     × 525,600 = 5,256   min/yr = 87.6 h  = 3.65 days
99.9 %   : 0.001    × 525,600 =   525.6 min/yr = 8.76 h
99.95 %  : 0.0005   × 525,600 =   262.8 min/yr = 4.38 h
99.99 %  : 0.0001   × 525,600 =    52.56 min/yr
99.999 % : 0.00001  × 525,600 =     5.256 min/yr

Per 30-day month:
99.9 %   : 0.001   × 43,200 = 43.2 minutes
99.99 %  : 0.0001  × 43,200 =  4.32 minutes
99.999 % : 0.00001 × 43,200 =  0.432 min = 25.9 seconds
```

**The operational reading:** at 99.99 % you have 4.32 minutes per month. A human being paged takes 2–5 minutes just to reach a laptop. Therefore **99.99 % is mathematically impossible with a human in the recovery loop.** That one inference, derived from arithmetic, determines the entire architecture.

### 9.2 BookNook's chain in series

*Assumptions*: every request must traverse DNS, load balancer, app tier, cache, database and the payment provider. Availabilities are vendor-published or measured; failures assumed independent for now.

```
DNS              0.99999
Load balancer    0.9999
App (single)     0.999
Cache            0.9995
Database         0.9995
Payment provider 0.9995

Step 1: 0.99999 × 0.9999   = 0.999890001
Step 2: 0.999890001 × 0.999 = 0.998890111
Step 3: 0.998890111 × 0.9995 = 0.998390666
Step 4: 0.998390666 × 0.9995 = 0.997891471
Step 5: 0.997891471 × 0.9995 = 0.997392525

A_total = 0.997393 -> 99.7393 %
Downtime = 0.0026075 × 525,600 = 1,370.5 min/yr = 22.8 hours/year
```

**Every component is better than 99.9 %, and the system is 99.74 %.** That is the series rule doing its work.

### 9.3 Adding redundancy, one layer at a time

**Step A — triple the app tier** (3 instances, assumed independent):

```
A_app = 1 - (1 - 0.999)^3 = 1 - (0.001)^3 = 1 - 0.000000001 = 0.999999999

Recompute: 0.99999 × 0.9999 × 0.999999999 × 0.9995 × 0.9995 × 0.9995
= 0.999890000 × 0.9995 = 0.999390055
                × 0.9995 = 0.998890361
                × 0.9995 = 0.998390916

A_total = 0.998391 -> 99.8391 %
Downtime = 0.0016091 × 525,600 = 845.7 min/yr = 14.1 hours
Saved: 1,370.5 - 845.7 = 524.8 minutes/year
```

**Step B — add a database standby with automatic failover:**

```
A_db = 1 - (0.0005)^2 = 1 - 0.00000025 = 0.99999975

A_total = 0.999890000 × 0.9995 (cache) × 0.99999975 (db) × 0.9995 (payments)
        = 0.999390055 × 0.99999975 = 0.999389805
        × 0.9995 = 0.998890111

A_total = 0.998890 -> 99.8890 %
Downtime = 583.3 min/yr = 9.7 hours
Saved: another 262.4 minutes/year
```

**Step C — remove the cache from the series chain entirely.** This is the cheapest improvement available and it requires no hardware: make a cache miss *degrade* (fall through to the database, slower) rather than *fail*.

```
Cache is no longer a required component -> drop 0.9995 from the product.

A_total = 0.99999 × 0.9999 × 0.999999999 × 0.99999975 × 0.9995
        = 0.999890000 × 0.99999975 × 0.9995
        = 0.999889750 × 0.9995 = 0.999389805

A_total = 0.999390 -> 99.9390 %
Downtime = 320.7 min/yr = 5.3 hours
```

**Step D — add a second payment provider with automatic fallback:**

```
A_pay = 1 - (0.0005)^2 = 0.99999975

A_total = 0.999890000 × 0.99999975 × 0.99999975
        = 0.999889750 × 0.99999975 = 0.999889500

A_total = 0.999890 -> 99.9890 %
Downtime = 0.000110500 × 525,600 = 58.1 minutes/year
```

**Summary of the journey:**

| Change | Availability | Downtime/year | Marginal gain |
|---|---|---|---|
| Baseline (all single) | 99.7393 % | 22.8 h | — |
| + 3 app instances | 99.8391 % | 14.1 h | 8.7 h |
| + DB standby | 99.8890 % | 9.7 h | 4.4 h |
| + cache made optional (**free**) | 99.9390 % | 5.3 h | 4.4 h |
| + second payment provider | 99.9890 % | 58 min | 4.3 h |

**The free change — making the cache optional — bought as much availability as the database standby.** Architectural changes that *remove components from the series chain* are usually cheaper and more effective than adding redundancy to them. This is the same principle as Lesson 13's "take it off the synchronous path".

### 9.4 MTBF versus MTTR

*Assumptions*: a service fails every 1,000 operating hours on average (MTBF = 1,000 h). Current recovery takes 4 hours (detect 45 min + page and wake 15 min + diagnose 2 h + fix and verify 1 h). A year is 8,760 hours.

```
Failures per year = 8,760 / 1,000 = 8.76
Downtime per year = 8.76 × 4 h = 35.04 hours
Availability      = MTBF / (MTBF + MTTR) = 1,000 / 1,004 = 0.996016 -> 99.6016 %
```

Now four competing investments:

| Investment | MTBF | MTTR | Availability | Downtime/yr | Realistic cost |
|---|---|---|---|---|---|
| Baseline | 1,000 h | 4 h | 99.6016 % | 35.0 h | — |
| Rewrite for quality: **2× MTBF** | 2,000 h | 4 h | 99.8004 % | 17.5 h | 2 engineer-quarters, uncertain outcome |
| Rewrite harder: **4× MTBF** | 4,000 h | 4 h | 99.9001 % | 8.76 h | 1+ engineer-year, highly uncertain |
| Better alerting + runbooks: **MTTR 1 h** | 1,000 h | 1 h | 99.9001 % | 8.76 h | 2 engineer-weeks |
| One-click rollback + auto-detect: **MTTR 15 min** | 1,000 h | 0.25 h | 99.9750 % | 2.19 h | 1 engineer-month |
| Full automation, no human: **MTTR 5 min** | 1,000 h | 0.0833 h | 99.9917 % | 0.73 h (43.8 min) | 1 engineer-quarter |

Read rows 3 and 4 together: **quadrupling MTBF and quartering MTTR produce the identical 99.9001 %.** One costs an engineer-year and may not work; the other costs two weeks and certainly works. Then read the last row: automating detection and recovery reaches **99.99 %** — a level that *no amount* of MTBF improvement reaches on its own, because at MTTR = 4 h you would need MTBF = 40,000 hours (4.5 years between failures) to get there:

```
Required MTBF for 99.99 % at MTTR = 4 h:

  0.9999 = MTBF / (MTBF + 4)
  0.9999 × (MTBF + 4) = MTBF
  0.9999 × MTBF + 3.9996 = MTBF
  3.9996 = MTBF × (1 - 0.9999)
  3.9996 = MTBF × 0.0001
  MTBF = 39,996 hours = 4.56 years between failures
```

**4.56 years between failures, for a system you deploy twice a week.** Not achievable. MTTR reduction is not merely cheaper; above three nines it is the *only* path.

### 9.5 Correlated failure, quantified

*Assumptions*: each app instance is 99.9 % available (`u = 0.001`). A fraction `c` of failures are common-mode — they hit every replica simultaneously (bad deploy, expired certificate, AZ loss, shared dependency).

```
u_n = c·u + ((1 - c)·u)^n
```

**Two replicas, c = 0 (the textbook assumption):**

```
u_2 = 0 + (0.001)^2 = 0.000001
A   = 99.9999 %    ->  0.5 minutes/year
```

**Two replicas, c = 0.20 (realistic):**

```
u_common      = 0.20 × 0.001 = 0.0002
u_independent = (0.80 × 0.001)^2 = (0.0008)^2 = 0.00000064
u_2           = 0.0002 + 0.00000064 = 0.00020064
A             = 99.97994 %  ->  0.00020064 × 525,600 = 105.5 minutes/year
```

**The formula promised 0.5 minutes; reality delivers 105.5 minutes — 211× worse.**

**Now add a third replica, still c = 0.20:**

```
u_3 = 0.0002 + (0.0008)^3 = 0.0002 + 0.000000000512 = 0.000200000512
A   = 99.97999 %  ->  105.12 minutes/year

Benefit of the third replica = 105.50 - 105.12 = 0.38 minutes/year
```

**The third replica bought 23 seconds of annual uptime for 50 % more compute.** Now attack `c` instead — canary deploys with automatic rollback, staggered certificate expiry, AZ spread, removing a shared config dependency — taking `c` from 0.20 to 0.02, with only **two** replicas:

```
u_common      = 0.02 × 0.001 = 0.00002
u_independent = (0.98 × 0.001)^2 = (0.00098)^2 = 0.00000096
u_2           = 0.00002 + 0.00000096 = 0.00002096
A             = 99.99790 %  ->  11.0 minutes/year
```

| Approach | Replicas | c | Downtime/year |
|---|---|---|---|
| Baseline | 2 | 0.20 | 105.5 min |
| Add a replica | 3 | 0.20 | 105.1 min (−0.4) |
| Add two replicas | 4 | 0.20 | 105.1 min (−0.4) |
| **Reduce correlation 10×** | **2** | **0.02** | **11.0 min (−94.5)** |

**Reducing correlation by 10× was 236× more effective than adding a replica, and it cost less.** Memorise this result: past two or three replicas, availability work is about *decorrelation*, not *duplication*.

### 9.6 Durability arithmetic

*Assumptions*: hard disks have an annualised failure rate (AFR) of 2 %, i.e. λ = 2 failures per 100 disk-years = 0.02 per disk-year. Data is stored as 3 full replicas on 3 independent disks.

**Naive (no repair):**

```
P(all 3 fail in a year) = 0.02^3 = 0.000008 = 8 × 10^-6
Durability = 1 - 8×10^-6 = 0.999992 -> 99.9992 %  (about FIVE nines)
```

Five nines is nowhere near eleven. **Repair speed supplies the rest.**

**With 1-hour repair.** After one disk fails, you are exposed only until a new replica is rebuilt.

```
T = 1 hour = 1/8,760 year = 1.1416 × 10^-4 years
λ = 0.02 failures per disk-year

For small λT, P(a specific disk fails within window T) ≈ λT.

Step 1: rate at which the FIRST disk of a set fails
        = 3λ = 3 × 0.02 = 0.06 events per set-year

Step 2: given one is down, P(one of the 2 survivors also fails before repair)
        = 2λT = 2 × 0.02 × 1.1416e-4 = 4.566 × 10^-6

Step 3: given two are down, P(the last survivor also fails before repair)
        = λT = 0.02 × 1.1416e-4 = 2.283 × 10^-6

Annual data-loss rate per replica set
        = 0.06 × 4.566e-6 × 2.283e-6
        = 6.25 × 10^-13

Durability = 1 - 6.25×10^-13 = 0.9999999999994  -> about 12 nines
```

**With 10-minute repair** (T is 6× smaller, and the risk depends on T²):

```
T = 10 min = 1.9026 × 10^-5 years
2λT = 2 × 0.02 × 1.9026e-5 = 7.610 × 10^-7
λT  = 0.02 × 1.9026e-5     = 3.805 × 10^-7

Annual loss rate = 0.06 × 7.610e-7 × 3.805e-7 = 1.74 × 10^-14
Durability ≈ 99.999999999998 %  -> about 13.8 nines

Repair 6× faster -> risk 36× lower, because exposure is QUADRATIC in T.
```

**Repair speed, not copy count, is what produces eleven nines.** Three copies alone give five; three copies plus one-hour repair give twelve on paper. Real systems land at eleven rather than twelve or fourteen because the idealised model above assumes independence, and reality supplies **correlated failures**: a rack losing power, a firmware bug affecting a whole disk model, a bad batch of drives, an AZ event. The remaining engineering — **erasure coding across independent failure domains** (6+3 across 3 AZs survives any 3 fragment losses at ~50 % overhead instead of 200 %), **continuous checksum scrubbing** to catch silent corruption before it meets a second failure, and **geographic separation** — exists to push the correlated component down, exactly as Section 4.6 described for availability.

**What 11 nines actually means for you:**

```
P(losing a given object in a year) = 10^-11

10 million objects   : 10^7  × 10^-11 = 10^-4 expected losses/year
                     -> one object lost every 10,000 years
1 billion objects    : 10^9  × 10^-11 = 10^-2  -> one every 100 years
100 billion objects  : 10^11 × 10^-11 = 1      -> ONE OBJECT PER YEAR
```

Durability nines are *per object per year*, so **scale consumes nines**. At 100 billion objects, eleven nines means losing something every year.

**And the comparison that defines the whole section:**

```
S3 durability SLA : 99.999999999 %  (11 nines)
S3 availability SLA: 99.9 %          (3 nines)
Ratio             : 10^8
```

Your data is **one hundred million times more likely to be unreachable than lost.** Design accordingly: multi-region *read* paths solve availability; backups and versioning solve durability. They are different problems with different solutions.

### 9.7 Error budgets and the business case

*Assumptions*: BookNook does 500 orders/day at an average of ₹1,200, so ₹600,000/day of revenue = ₹416.67 per minute. (Ignoring deferred purchases, which makes this a deliberately conservative upper bound on loss.)

```
At 99.9 %  : 525.6 min/yr down × ₹416.67 = ₹219,000/year of lost revenue
At 99.95 % : 262.8 min/yr down × ₹416.67 = ₹109,500/year
At 99.99 % :  52.56 min/yr down × ₹416.67 = ₹ 21,900/year

Benefit of 99.9 % -> 99.99 % = ₹219,000 - ₹21,900 = ₹197,100/year
```

Now the cost side: going from 99.9 % to 99.99 % means multi-AZ (roughly 2.5× infrastructure), canary tooling, and automated failover.

```
Assume current infrastructure = ₹600,000/year
New infrastructure (2.5×)     = ₹1,500,000/year
Additional infra cost         = ₹900,000/year
Engineering (1 engineer-quarter, ~₹600,000, amortised over 3 years) = ₹200,000/year
Total additional cost         = ₹1,100,000/year

Benefit ₹197,100  vs  Cost ₹1,100,000  ->  NOT JUSTIFIED at this scale.
```

**At BookNook's current size, 99.99 % destroys value.** Run the same calculation at 50× the revenue:

```
Revenue/minute = ₹20,833
Benefit of 99.9 % -> 99.99 % = 473 min/yr × ₹20,833 = ₹9,854,000/year
Cost (infra scales, engineering does not) ≈ ₹5,000,000/year  ->  CLEARLY JUSTIFIED
```

**Availability targets are a function of revenue per minute, not of engineering ambition.** This calculation — not a preference — is how the target gets chosen.

**Error budget** (Phase 4, Topic 26, previewed):

```
SLO 99.9 % monthly -> budget = 0.001 × 43,200 = 43.2 minutes/month

A single 25-minute incident consumes 25 / 43.2 = 57.9 % of the month's budget.
Two such incidents = 115.7 % -> budget exhausted, feature releases freeze
   until reliability work restores it.

Under a 99.99 % SLO, that same 25-minute incident is 25 / 4.32 = 578.7 %
of the monthly budget -- nearly SIX months of budget in one afternoon.
```

The error budget converts reliability from an argument into an accounting entry, and it makes the cost of a tighter SLO viscerally obvious.

## 10. Trade-offs

**Pursuing higher availability**
- *Gains*: fewer lost transactions, higher trust, enforceable contractual SLAs, less firefighting (paradoxically, high-availability teams are often calmer because recovery is automated).
- *Costs*: 3–10× infrastructure per additional nine; redundancy means idle capacity you pay for; failover machinery is itself code that can fail (a broken automatic failover can cause split-brain, which is worse than the outage it prevented); multi-region forces you into eventual consistency (Phase 3), which is a correctness cost paid for an availability gain.

**Pursuing higher durability**
- *Gains*: data survives essentially anything; regulatory compliance; the ability to restore after human error.
- *Costs*: 2–3× storage for replication (or ~1.5× for erasure coding plus CPU); write latency increases if you wait for multiple replicas to acknowledge; backup storage, retention and restore-testing are ongoing operational work; and **more copies means a larger surface for accidental exposure**, a security trade-off people forget.

**Pursuing fault tolerance**
- *Gains*: faults stop becoming failures; graceful degradation preserves most of the product during partial outages; the system becomes predictable under stress.
- *Costs*: every fallback path is code that is rarely executed and therefore rarely correct — untested fallbacks fail exactly when needed; circuit breakers can open spuriously and cause self-inflicted outages; timeouts that are too aggressive turn slow-but-working into failed; complexity makes the system harder to reason about and to debug.

**The hard tensions**
- **Availability ↔ consistency.** During a network partition you must choose (CAP, Phase 3). Choosing availability means serving possibly-stale data; choosing consistency means refusing to serve.
- **Availability ↔ cost.** Linear nines, exponential money.
- **Durability ↔ write latency.** Synchronous replication to 3 AZs adds 2–10 ms per write and makes writes fail when a zone is slow. Asynchronous replication is faster and has non-zero RPO.
- **Fault tolerance ↔ simplicity.** Every fallback is a branch; every branch is a bug surface. A simple system that fails cleanly can beat a complex one that fails weirdly.
- **Redundancy ↔ correlation.** More replicas of the *same* thing in the *same* place is cost without benefit.

**Operational costs**
- 24/7 on-call with real escalation, which is a staffing and human cost, not just a rota.
- Regular **restore drills** and **failover drills**. Untested recovery is not recovery.
- Chaos engineering, incident reviews, runbook maintenance.
- Continuous capacity headroom so surviving nodes can absorb redistributed load.

**When this becomes unnecessary complexity**
- An internal tool used by 12 people during business hours. 99 % (3.65 days/year) is genuinely fine, and multi-AZ for it is waste.
- A batch pipeline whose output is consumed the next morning. It can be down for four hours with zero impact as long as it finishes by 06:00.
- A prototype with no users. The most common failure of judgement in this topic is building for nines before having customers.

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| Single app instance crashes | Nothing, if redundancy works; a handful of in-flight requests fail | Readiness probe fails; load balancer ejects after 2 checks (~10 s) | Orchestrator restarts the pod; traffic was already rerouted |
| Entire availability zone lost | ~33 % of requests fail briefly, then elevated latency | Multiple instances fail simultaneously; cloud status event | Surviving AZs absorb load **if you provisioned for it**; autoscaler replaces capacity |
| Database primary fails | Writes fail for the duration of failover (20–60 s); reads continue | Heartbeat loss; cluster manager quorum vote | Standby promoted; app reconnects; degradation level 3 (read-only) covers the gap |
| **Bad deploy to all replicas** | Total outage within the rollout window; redundancy provides **zero** protection | Error-rate spike correlated with deploy; canary analysis | Automatic rollback. *The prevention is canary + progressive rollout, not more replicas* |
| **TLS certificate expires** | Total outage at an exact timestamp on every node at once | Certificate-expiry monitoring (should fire 30 days early, not at failure) | Emergency renewal; prevention is automated renewal plus staggered expiries |
| Cascading overload after losing a replica | Everything slow, then total collapse — **worse than the original fault** | Utilisation on survivors jumps; latency cliff (Lesson 14) | Load shedding; prevention is N+1 capacity sized for the largest failure domain |
| Shared dependency (auth, config, DNS) fails | Everything fails despite every tier being redundant | Many unrelated services fail together | Cache the dependency's answers locally; fail open where safe; make it redundant |
| Grey failure: instance answers health checks but returns errors | Intermittent errors; users retry and sometimes succeed; **hardest to diagnose** | Deep/semantic health checks; per-instance error-rate anomaly detection | Eject on error rate, not just on probe failure; restart the instance |
| Split-brain during failover | Two primaries accept conflicting writes; **data corruption** | Conflicting writes; replication divergence alarms | Quorum-based promotion and fencing (STONITH) prevent it; recovery means manual reconciliation |
| Replication lag during failover | Recently-confirmed orders vanish after promotion (RPO > 0) | Replication-lag metric; customer reports | Synchronous replication gives RPO = 0 at a latency cost; otherwise reconcile from logs/queue |
| **`DELETE FROM orders` with no WHERE** | Data gone, replicated perfectly to every replica in milliseconds | Row-count alarms; a very loud customer | **Only** point-in-time recovery or backups help. Replication is not backup |
| Backup exists but restore fails | Discovered during the worst hour of the year | Restore drills — or, if you skipped them, the incident | Tested restores, monthly drills, documented RTO/RPO |
| Silent data corruption (bit rot) | Wrong data returned, possibly years later | Checksum scrubbing; integrity audits | Repair the corrupt replica from a good one; requires checksums to exist |
| Third-party provider (payments) down | Checkout fails; revenue stops | Dependency error-rate alarm | Circuit breaker + secondary provider; or queue authorisations for later if the business allows |
| Region-wide cloud failure | Everything down despite multi-AZ | Provider status page; global monitoring from outside the region | Multi-region failover (Phase 4, Topics 27–29) or an honest status page and a wait |

The two rows in bold deserve special attention: **bad deploys and expired certificates are the most common causes of total outages in modern systems, and redundancy is powerless against both.** They are correlated failures by construction.

## 12. Alternatives

Different strategies for achieving reliability, compared:

| Strategy | Mechanism | Improves | Typical cost | Protects against | Does NOT protect against |
|---|---|---|---|---|---|
| **Fault avoidance** (better components, more testing) | Raise MTBF | Reliability | High, slow, uncertain | Known bug classes | Infrastructure failures, the unknown |
| **Redundancy (active-passive)** | Standby takes over | Availability | 2× infra, mostly idle | Single-node failure | Correlated failure; failover itself can fail |
| **Redundancy (active-active)** | All nodes serve | Availability + capacity | 2×+ infra, all used | Single-node and AZ failure | Bad deploys, shared dependencies |
| **Geographic redundancy** | Multi-region | Availability + latency | 2–3× infra + complexity | Regional outage | Global control-plane and DNS failures; forces eventual consistency |
| **Graceful degradation** | Fallbacks for optional features | Perceived availability | **Low — mostly design effort** | Dependency failures | Core-path failures (you cannot degrade payment) |
| **Load shedding** | Reject some traffic | Stability under overload | Very low | Overload, retry storms | Component failure |
| **Fast recovery (MTTR)** | Automation, rollback, runbooks | Availability | **Low — highest ROI** | Everything recoverable | Unrecoverable data loss |
| **Replication** | Many copies of data | Durability + read availability | 2–3× storage | Disk/node/AZ loss | **Logical deletion and corruption** |
| **Erasure coding** | k+m fragments | Durability | ~1.5× storage, more CPU | Multiple fragment loss | Logical deletion; higher small-read latency |
| **Backups + PITR** | Point-in-time copies | Durability | Low storage, real ops effort | **Human error, ransomware, corruption** | Live availability (restores take hours) |
| **Chaos engineering** | Inject faults deliberately | Confidence that the above work | Engineering time | Untested-recovery surprises | Nothing directly — it's a verification tool |
| **Cell-based architecture** | Isolated independent cells | Blast-radius containment | High complexity | Correlated failure across cells | Failures inside a cell |
| **Do nothing (accept the risk)** | — | — | Zero | — | Everything — but it is the **correct** choice when downtime cost < reliability cost |

The last row is not a joke. Section 9.7 showed a case where 99.99 % destroys ₹900,000/year of value. **"We have measured this and decided 99.9 % is right for us" is a senior engineering answer.**

## 13. When to use it

Situations where this material directly determines your decisions:

1. **Setting an availability target.** Compute revenue (or harm) per minute of downtime, compare against the cost ladder, and pick the nine the numbers support. This turns an argument into an arithmetic exercise.
2. **Reviewing an architecture diagram.** Multiply the series chain. If the answer is worse than the target, you know *before* building that the design cannot meet it. Then ask which components can be made optional, redundant, or asynchronous.
3. **Prioritising reliability work.** Compare MTBF investments against MTTR investments using the identity. Teams consistently overinvest in the first and underinvest in the second.
4. **Deciding how many replicas.** Compute with a realistic common-mode fraction. If the third replica buys 23 seconds a year, spend the money on canary deploys instead.
5. **Choosing a storage design.** Separate the durability requirement (how bad is permanent loss?) from the availability requirement (how bad is an hour of unreachability?). They lead to different systems and different budgets.
6. **Designing degradation before an incident.** Enumerate levels 0–5 as in Section 4.7, with explicit triggers and feature flags. Doing this during an outage is how 20-minute incidents become 3-hour ones.
7. **Capacity planning for failure, not for normal.** Size for "one whole failure domain is gone" — 9 pods across 3 AZs, not 6 — because the Lesson 14 latency cliff turns a partial failure into a total one otherwise.
8. **Justifying work to the business.** "This ₹X buys us Y fewer minutes of downtime, worth ₹Z" is a conversation a CFO can have. "We need more reliability" is not.

## 14. When *not* to use it

1. **Internal or low-stakes systems.** A tool used by 12 people in one timezone does not need multi-AZ. Two nines is a defensible, deliberate choice; building four nines for it is burning money you could spend on features.
2. **Before product-market fit.** Reliability engineering for a product with no users optimises a quantity (downtime cost) that is currently zero. Ship, get users, then measure.
3. **When the dominant risk is not component failure.** If 90 % of your incidents come from bad deploys and configuration mistakes, buying another replica is answering the wrong question. Fix the deploy pipeline.
4. **When redundancy increases risk.** Automatic failover that has never been tested can cause split-brain data corruption; a complex multi-region setup can produce outages that a single region would not have had. If you cannot operate it, do not build it.
5. **When the real requirement is durability, not availability.** For an archival system, four hours of unreachability may be irrelevant while a single lost object is catastrophic. Spending on active-active serving infrastructure solves nothing there.
6. **When "it depends" is really "we never measured".** Do not pick a target by feel. The decision depends on: revenue or harm per minute, contractual obligations, user tolerance for staleness, and the cost of the next nine. Name those four, then decide.

## 15. Common mistakes

**Beginner mistakes**
- **Using "available", "reliable" and "durable" interchangeably.** They are three different questions with three different solutions.
- **Thinking replication is backup.** It faithfully replicates your `DELETE` to every copy in milliseconds.
- **Assuming redundancy multiplies availability as the formula says**, with no allowance for correlation.
- **Putting all replicas in one AZ** (or worse, one rack) and calling it highly available.
- **Checking dependencies in the liveness probe**, so a database blip causes the orchestrator to restart every healthy pod simultaneously — converting a degraded system into a dead one.
- **Never testing restores.** An untested backup is a hypothesis.
- **Forgetting that planned maintenance counts** against the budget.
- **Quoting "five nines" as an aspiration** without knowing that it is 25.9 seconds per month and precludes humans, manual deploys and most managed services.
- **Measuring availability only from inside the datacentre**, so a DNS or CDN failure that makes the site unreachable shows up as 100 % uptime on your dashboard.

**Experienced-engineer mistakes**
- **Optimising MTBF when MTTR is the cheap lever** (Section 9.4).
- **Not provisioning for failure-mode load.** Running 3 AZs at 85 % utilisation means losing one AZ puts the rest at 127 % — the failure *causes* the outage.
- **Building failover you never exercise.** The first real test is during the incident, at 3 a.m., with a tired engineer.
- **Ignoring the shared dependency.** Nine redundant services all calling one non-redundant config service is a system with one point of failure and a large bill.
- **Automatic failover without fencing/quorum**, producing split-brain — data corruption caused by the reliability mechanism itself.
- **Counting the dependency you forgot**: DNS, NTP, the certificate authority, the container registry, the secrets manager, the identity provider. These are in series with everything.
- **Treating the status page as a dependency of the system it reports on.** It must be hosted elsewhere.
- **Measuring availability as "the process is running" rather than "users can complete the journey".** Synthetic end-to-end probes from outside are the only honest measure.
- **Promising an SLA tighter than the sum of your vendors' SLAs.** If your cloud database is 99.95 %, you cannot contractually offer 99.99 % on top of it without redundancy across vendors.
- **Degrading without telling the user.** Serving stale stock counts silently causes oversells; a small "stock may be out of date" banner preserves trust.
- **Letting fallback paths rot.** Code that runs once a year does not work. Exercise degradation paths regularly, ideally in production via chaos testing.
- **Confusing RPO and RTO.** RPO is *how much data you can lose* (replication design); RTO is *how long until service returns* (failover design). Teams routinely build for one and promise the other.

## 16. Interview perspective

This topic distinguishes candidates who have operated systems from those who have only built them.

- **Always state an availability target with its downtime.** "99.9 %, which is 43 minutes a month — that's achievable with multi-instance in one region and an on-call rota. If they want 99.99 %, that's 4.3 minutes a month, which rules humans out of the recovery path and forces multi-AZ with automated failover." This one paragraph demonstrates the entire topic.
- **Multiply the chain out loud.** "Six synchronous dependencies at three nines each is 99.4 %, or 52 hours a year. That misses our target, so I'd make the cache and the recommender optional and move notifications async."
- **Bring up correlated failure unprompted.** "Two replicas theoretically give six nines, but they share a deploy and an AZ. In practice I'd expect around 99.98 %, and I'd spend on canary deploys before a third replica." This is a genuinely senior observation and very few candidates make it.
- **Argue MTTR over MTBF with numbers.** "Quadrupling MTBF and quartering MTTR both give 99.9 %. One costs an engineer-year; the other costs two weeks."
- **Separate durability from availability explicitly.** "S3 is eleven nines durable and three nines available — my data is a hundred million times more likely to be unreachable than lost, so I need a read fallback, not more copies."
- **Say 'replication is not backup' at least once** when data is discussed, and mention point-in-time recovery.
- **Design degradation levels, not just failure.** "If the recommender is down, I serve bestsellers. If inventory is down, I show estimated availability. If the primary is failing over, I go read-only and keep the cart." This is the answer that gets remembered.
- **Size capacity for the failure case.** "Three AZs means I run 1.5× capacity so losing one doesn't push the survivors over the latency cliff."
- **Know RPO and RTO as separate numbers** and tie them to replication mode: "synchronous gives RPO 0 at a few ms of write latency; async gives RPO equal to the replication lag."
- **Be willing to say a lower target is correct.** "At 500 orders a day, 99.99 % costs about eleven lakh a year and saves about two. I'd run 99.9 % and revisit at 10× scale."

**Red flags interviewers watch for:** saying "highly available" with no number; claiming five nines without knowing it is 26 seconds a month; treating replication as backup; assuming redundancy composes independently; no mention of correlated failure, bad deploys or certificate expiry; no plan for what happens during a failover; health checks that would restart every pod at once; promising an SLA above the vendor's; no backup-restore testing; confusing RPO with RTO; designing only two states (perfect and dead) with no degradation; and — most revealing — being unable to name the single point of failure in their own diagram. Every design has one; the senior answer identifies it and justifies it.

## 17. Knowledge check

Answer these before moving on. Show your working for the numeric parts.

**Conceptual**

1. For each of the following, state which of availability, reliability, durability or fault tolerance is primarily at stake, and explain in one sentence why the other three are not the right frame: (a) a cache that loses all data on restart but has never been down; (b) a billing service that is offline every Sunday 02:00–06:00 but has never produced a wrong invoice; (c) a storage region that is unreachable for six hours with every byte intact; (d) a service that returns `200 OK` for every request while 4 % of responses contain stale prices.
2. Explain precisely why adding a third identical replica to a two-replica service might improve availability by only a few seconds per year, and describe three specific, concrete changes that would improve it far more — for each, name the correlation source it eliminates.
3. A team says: "We're at 99.9 % and we want 99.99 %, so we're going to spend the next two quarters reducing bugs." Explain why this is likely the wrong investment, what you would measure first to prove it, and what you would propose instead. Include the specific calculation that makes your case.

**Practical scenario**

4. BookNook's checkout depends on: DNS (99.999 %), a load balancer (99.99 %), three app instances (99.9 % each), Redis (99.95 %), PostgreSQL primary with no standby (99.95 %), a payment provider (99.95 %), and a fraud-check service (99.9 %). All are called synchronously and all are required. (a) Compute the end-to-end availability and annual downtime. (b) Identify the three highest-leverage changes, ranked, and estimate the new availability after each. (c) One of your changes should require **no new infrastructure at all** — identify it and explain the mechanism. (d) The team then deploys all three app instances simultaneously from one pipeline with no canary. Explain what this does to your calculation in (a) and why the formula no longer applies.

**Calculation**

5. A BookNook service currently has MTBF = 720 hours and MTTR = 2.5 hours. A year is 8,760 hours.
   (a) Compute failures per year, annual downtime in hours, and availability as a percentage to four decimal places.
   (b) The team can either double MTBF (two engineer-quarters) or cut MTTR to 20 minutes (three engineer-weeks). Compute the resulting availability and annual downtime for each, and state which you would choose and why.
   (c) What MTTR would be required to reach 99.99 % at the *original* MTBF of 720 hours? Show the algebra. Is that achievable with a human in the loop? Justify your answer with a time breakdown.
   (d) The service runs 3 replicas at 99.9 % each. Assuming 30 % of failures are common-mode, compute the realistic tier availability and annual downtime, then compute what it would be if you reduced the common-mode fraction to 5 %. Express the improvement as a ratio.
   (e) BookNook stores 40 million order documents with a provider advertising 99.9999999 % (nine nines) annual durability. How many documents should you expect to lose per year, and per decade? At what object count does the expected annual loss reach one document?

Reply with your answers and I'll assess them, correct anything shaky, and give model answers before Lesson 16.

---

**Progress checklist**

Phase 1 — Internet and System-Design Foundations
- [x] 1. Client-server architecture
- [x] 2. Stateful versus stateless servers
- [x] 3. IP addresses, ports, sockets, domain names
- [x] 4. DNS resolution and DNS caching
- [x] 5. HTTP requests and responses
- [x] 6. HTTP methods, headers, cookies, sessions, status codes
- [x] 7. HTTPS and TLS
- [x] 8. TCP versus UDP
- [x] 9. HTTP/1.1 vs HTTP/2 vs HTTP/3
- [x] 10. REST APIs
- [x] 11. RPC and gRPC
- [x] 12. GraphQL
- [x] 13. Synchronous versus asynchronous communication
- [x] 14. Latency, throughput, bandwidth, concurrency
- [x] 15. Availability, reliability, durability, fault tolerance
- [ ] 16. Average latency vs p50/p95/p99, tail latency
- [ ] 17. Idempotency, retries, timeouts, exponential backoff

Phases 2–5: not started.

**NEXT LESSON: Phase 1, Topic 16 — Average latency versus p50, p95, p99, and tail latency**
