# Lesson 67 — Synchronous versus Asynchronous Workflows

*Phase 4, Topic 4*

## 1. Simple definition

A **workflow** is a multi-step business process that must run to completion: "place an order", "onboard a seller", "process a refund". It is *not* a single request/response call — it is a sequence of steps, often spanning several services, several seconds to several days, and several failure domains.

A workflow is **synchronous** when the caller waits, holding an open connection, until every step has finished and the final result is known. It is **asynchronous** when the caller is told "I have accepted this" and the remaining steps complete later, out of band, with the result delivered by some other mechanism.

Lesson 13 introduced synchronous versus asynchronous *communication* — the difference between one blocking call and one fire-and-forget call. This lesson is about something harder. One call is easy to reason about. A **workflow** of nine calls, where step 4 succeeded, step 5 timed out ambiguously, and step 6 has not started, is where distributed systems actually hurt.

Terms defined before use:
- **Step**: one unit of work in the workflow, usually one service call or one local transaction.
- **Workflow state**: the record of which steps have completed, which are pending, and the data produced so far.
- **Orchestrator**: a component that explicitly holds the workflow state and tells each service what to do next.
- **Choreography**: the opposite arrangement — no central controller; each service reacts to events emitted by the previous one.
- **Compensating action**: a *new* operation that semantically undoes a previously completed step (refund, not rollback).
- **Durable execution**: a runtime that persists workflow progress so the workflow survives the death of the process running it.

## 2. Why it exists

Three forces push workflows away from "just call everything synchronously".

**Force 1 — availability multiplies downward.** Lesson 15 established that a chain of hard dependencies multiplies their availabilities. BookNook's checkout touches six services. Each is independently 99.9 % available. The synchronous chain is `0.999⁶ = 0.994015`, i.e. **99.4 %**. You assembled six good services into one mediocre one. Section 9 works this through in full.

**Force 2 — latency adds, and the slowest step dominates.** Synchronous steps execute one after another, so user-visible latency is the *sum*. Generating a PDF invoice takes ~880 ms whether or not the customer is watching. If it is in the request path, the customer waits for it.

**Force 3 — coupling in time.** A synchronous call requires the callee to be *up right now*. If BookNook's email provider is having a bad afternoon, a synchronous "send confirmation email" step means BookNook cannot take orders. That is absurd: the business can obviously accept an order and send the email twenty minutes later.

The question this lesson answers is therefore not "sync or async?" but the much sharper:

> *For each step in this workflow, must the user see the outcome of this step before we can honestly say "done"?*

Steps where the answer is **yes** stay synchronous. Steps where the answer is **no** become asynchronous. Everything else in this lesson — orchestration, choreography, durable execution, compensations, `202 Accepted` — is machinery for making the "no" answers safe.

## 3. Real-world analogy

**A restaurant kitchen.**

You sit down and order a steak and a coffee.

- The **synchronous part** is ordering. The waiter stands at your table, confirms the steak is available, confirms the kitchen can cook it, and takes your card pre-authorisation for the set menu. You are waiting, and you must be, because if the steak is sold out you need to choose something else *now*.
- The **asynchronous part** is everything after. The waiter does not stand motionless at your table for 25 minutes while the steak cooks. The order goes on a rail — a queue — and the kitchen pulls tickets. You are free; the waiter is free.
- The **orchestrated kitchen** has an expediter who holds the ticket, calls "steak on!", then "plate up!", then checks the dish before it leaves. One person knows the whole state of your order.
- The **choreographed kitchen** has no expediter. The grill chef finishes the steak and shouts "steak up!"; the garnish station hears that and acts; the runner hears the garnish station. Nobody holds the whole picture, and it works beautifully until it doesn't.
- The **compensating action** is what happens when the kitchen burns your steak after your card was pre-authorised. They cannot "un-charge" by rewinding time; they issue a refund or a free dessert — a new, forward action with the opposite business effect.
- **Notification on completion** is the buzzer some restaurants give you, or the waiter coming over to say "your table's ready".

**Where the analogy breaks down:**

| Restaurant kitchen | Distributed workflow |
|---|---|
| The waiter can walk to the kitchen and *look* at your ticket | A timed-out service call tells you nothing: the step may have succeeded, failed, or still be running (Lesson 65) |
| Everyone shares one wall clock and one room | There is no shared clock and no shared "now" (Lesson 66) |
| If a chef collapses, a human notices in seconds and reassigns the ticket | A process that dies mid-workflow loses in-memory state silently; detection requires explicit timeouts and leases |
| Tickets are physical: exactly one exists | A retried message can produce two tickets; idempotency keys are mandatory (Lesson 17) |
| Shouting "steak up!" reaches everyone in the room reliably | An event can be lost, duplicated, or delivered out of order (Topic 10 — Delivery guarantees) |
| The kitchen closes at 11 p.m. and the state resets | A workflow may legitimately run for 30 days (a return window), far longer than any process lifetime |

That last row is the whole reason durable execution engines exist, and §4 returns to it.

## 4. Detailed technical explanation

### 4.1 Workflow versus call

A **call** is one request and one response. Its failure modes are: success, failure, timeout-with-unknown-outcome. That is a lot, but it is finite.

A **workflow** of *n* steps has a far larger state space, because failure can land *between* steps. With six steps you have at minimum seven "where did we stop?" positions, each combined with the unknown-outcome ambiguity of the step at the boundary. The engineering problem is: **where is that position written down, and who is responsible for resuming from it?**

Every workflow design is an answer to that one question.

### 4.2 Orchestration

One component — the **orchestrator** — owns the workflow. It calls service A, stores the result, calls service B, stores the result, and so on. Services do not know they are part of a workflow; they just expose operations.

BookNook checkout, orchestrated. The `CheckoutOrchestrator` executes:

1. `OrderService.createOrder(cart)` → `order_id`
2. `InventoryService.reserve(order_id, items)` → `reservation_id`
3. `PaymentService.authorise(order_id, amount)` → `auth_id`
4. `OrderService.markConfirmed(order_id)`
5. emit `OrderPlaced` (async: email, invoice, reindex, recommendations, analytics)

Properties:
- **The whole process is readable in one file.** A new engineer can open `CheckoutOrchestrator` and see the business process. This is a genuinely large benefit and it is routinely underrated.
- **State lives in one place** — the orchestrator's workflow table.
- **Error handling is centralised.** "If payment fails, release the reservation" is one `catch` block in one place.
- **Coupling is structural, not organisational.** The orchestrator knows about four services. Change the order of steps and you change one file.
- **Cost:** the orchestrator is a bottleneck in the *change* sense (every workflow change touches it) and can become one operationally. It is also a single place where a bug stops all checkouts.

### 4.3 Choreography

No orchestrator. Each service emits an **event** describing what it did, and other services subscribe and react (Lesson 69 covers the pub/sub mechanism in depth).

BookNook checkout, choreographed:

1. Order service writes the order, emits `OrderCreated`.
2. Inventory service hears `OrderCreated`, reserves stock, emits `StockReserved` (or `StockReservationFailed`).
3. Payment service hears `StockReserved`, authorises the card, emits `PaymentAuthorised` (or `PaymentDeclined`).
4. Order service hears `PaymentAuthorised`, marks the order confirmed, emits `OrderConfirmed`.
5. Email, invoice, analytics, search services hear `OrderConfirmed` and act.

Properties:
- **No central component.** Adding a seventh reaction requires changing nobody — this is the organisational decoupling benefit discussed fully in Lesson 69 §4.
- **Nowhere is the business process written down.** The process exists only as an emergent property of five independent subscriptions. To answer "what happens when someone checks out?" you must read five codebases and the broker configuration.
- **Debugging is genuinely hard.** An order stuck between `StockReserved` and `PaymentAuthorised` is invisible unless you built explicit tooling, because no component is responsible for noticing that the next step did not happen.
- **Cyclic reaction risk.** Service A reacts to B's event by emitting an event that B reacts to. In a 20-service estate these loops are easy to create accidentally and hard to see.

### 4.4 The honest comparison

This is the part most material gets wrong by presenting the two as equally good.

| Question | Orchestration | Choreography |
|---|---|---|
| Can I read the business process in one place? | **Yes** | No — it is distributed across every subscriber |
| Can I tell why order #4471 is stuck? | **Yes** — query one state row | Only with distributed tracing (Topic 28) plus discipline |
| Can I add a new *reaction* without touching the owner? | No — edit the orchestrator | **Yes** — this is the real win |
| Can I change the *order* of core steps easily? | **Yes** — one file | No — rewiring subscriptions across teams |
| Does it survive a team not knowing the full process? | Yes | Dangerous — nobody holds the whole picture |
| Blast radius of a bug in the coordinating logic | Large (central) | Distributed but harder to find |
| Natural fit for | Core transactional processes | Side-effects and notifications |

**The practical answer used by most mature systems is a hybrid, and it is not a cop-out — the split follows a precise rule:**

> **Orchestrate the steps the user is waiting for and that must be unwound on failure. Choreograph the steps that are independent reactions to a completed fact.**

BookNook: the order → reserve → authorise → confirm core is **orchestrated** (synchronous, four steps, needs compensation). Everything downstream of `OrderPlaced` — email, invoice, search reindex, recommendations, analytics, warehouse pick list — is **choreographed** (asynchronous, independent, no compensation needed because emitting an extra email is survivable).

### 4.5 Where workflow state lives

Four options, in increasing order of robustness:

| Option | Survives process restart? | Survives host loss? | Notes |
|---|---|---|---|
| In memory in the request handler | No | No | Only acceptable for workflows shorter than a request timeout |
| A row in the orchestrator's database | Yes | Yes | The common, correct baseline. Needs a scanner to resume stalled rows |
| Encoded in the messages themselves | Yes | Yes | Choreography's implicit state; impossible to query globally |
| A durable execution engine | Yes | Yes | §4.6 |

The database-row approach is the workhorse. A `workflows` table with `(workflow_id, type, current_step, state_json, status, updated_at, attempt_count)` plus a background **sweeper** that picks up rows whose `updated_at` is older than the step timeout gets you 90 % of the value. Everything a durable execution engine does, you can build badly yourself — which is exactly why they exist.

### 4.6 Durable execution engines (Temporal, AWS Step Functions, Cadence)

**The problem they solve, stated precisely:** you want to write a workflow as ordinary straight-line code —

```
order = createOrder(cart)
reservation = reserveStock(order)
payment = authorise(order)
sleep(14 days)
if not returned: settleFunds(payment)
```

— but a process cannot `sleep(14 days)`. It will be redeployed, OOM-killed, or rescheduled onto another host. The naive solution is to shred the workflow into event handlers and state machine rows by hand, which destroys readability.

A durable execution engine lets you keep the straight-line code by **persisting every step's input and output to a durable event history**. When the process dies, a new worker picks up the workflow and *replays* the history: every already-completed step returns its recorded result instantly, without re-executing, until execution reaches the point where it stopped. Then it continues for real.

The consequences:
- **Steps must be deterministic.** No `Math.random()`, no `new Date()`, no direct I/O in the workflow body — those must go through engine-provided APIs, because replay must reproduce the same decisions. This is the main thing that surprises newcomers.
- **The side-effecting work goes in "activities"** (Temporal's term) or "tasks" (Step Functions'), which are recorded, retried with backoff (Lesson 17), and never replayed once successful.
- **Timers are durable.** `sleep(14 days)` becomes a persisted timer, not a thread.
- **Simplified teaching model vs production reality:** teaching model — "the engine remembers where you were". Production reality — the engine stores an append-only history per workflow, enforces determinism, versions workflow code so in-flight executions do not break on deploy, and requires you to think carefully about history size limits (Temporal truncates very large histories; Step Functions Standard caps execution history at 25,000 events). Workflow versioning is the genuinely hard operational part and is usually discovered six months in.

**When they are overkill:** a three-step workflow finishing in 300 ms does not need one. The cost is a new piece of infrastructure, a new programming model, and a new failure domain.

### 4.7 Compensating actions

Distributed workflows cannot use database rollback across services (Lesson 52 on ACID; Topic 20 — Distributed transactions and two-phase commit explains why 2PC is rarely chosen). If step 3 fails after steps 1 and 2 committed in *different* databases, you cannot rewind. You must move forward with an action whose business effect is the opposite:

| Forward step | Compensating action | Note |
|---|---|---|
| `reserveStock` | `releaseStock` | Clean inverse |
| `authorisePayment` | `voidAuthorisation` | Clean inverse within ~7 days |
| `capturePayment` | `refund` | **Not** an inverse — the customer saw a charge and a refund on their statement |
| `sendEmail` | *none possible* | You cannot unsend. Design so the email is sent only after the point of no return |

That last row is the design rule: **put irreversible steps last**. The full treatment of multi-step compensation, including the saga log and the difference between backward and forward recovery, is **Phase 4, Topic 21 — Sagas and compensating transactions**. Here, only the concept matters: an asynchronous workflow needs an answer for "what do we do about already-completed steps?" before it ships.

### 4.8 Timeouts, and why deciding a workflow has failed is genuinely hard

Lesson 65 established the core problem: a timeout does not distinguish "it failed" from "it is slow" from "it succeeded and the reply was lost". In a workflow this compounds because the step may itself contain a workflow.

Practical rules:
- **Every step gets its own timeout**, and the workflow gets an overall deadline. Deadline propagation (Topic 17) means a step is told how much time remains, not just how long it may take.
- **Timeout does not mean "compensate immediately".** For an ambiguous payment timeout, the correct action is usually to *query* the payment provider for the idempotency key you sent (Lesson 17) before deciding. Compensating a step that actually succeeded, or retrying a step that already charged a card, both create real money bugs.
- **Choose timeouts from measured p99.9, not intuition.** A timeout below p99.9 converts ordinary slowness into manufactured failures.
- **There is no correct universal timeout.** It depends on exactly three things: the step's measured p99.9 latency, the remaining workflow deadline, and whether the step is idempotent (if it is not, prefer a longer timeout and a query-based resolution, because a premature retry is more dangerous than a slow one).

### 4.9 Deciding which steps must be synchronous

A step must be synchronous if **any** of these is true:

1. **The user must see the outcome to proceed.** Stock availability: if the book is gone, the user must pick another.
2. **A later synchronous step needs its output.** Payment authorisation produces the `auth_id` that order confirmation stores.
3. **Doing it later would be a lie.** Telling a customer "order confirmed" before the card is authorised means some confirmed orders will later be unconfirmed — a support disaster.
4. **It is cheap relative to the budget and makes failure handling trivially simpler.** A 15 ms fraud-score lookup can stay inline.

A step should be asynchronous if **all** of these are true:

1. The user does not need its result to continue.
2. A delay of seconds-to-minutes is acceptable to the business.
3. It can be retried safely (idempotent, or made idempotent with a key).
4. Its failure should not fail the overall business action.

Applied to BookNook:

| Step | Sync or async | Reason |
|---|---|---|
| Validate cart | Sync | User must see errors |
| Reserve stock | **Sync** | Rule 1 — availability changes the user's decision |
| Authorise payment | **Sync** | Rules 1, 2, 3 |
| Persist confirmed order | **Sync** | Rule 2 — this *is* the result |
| Confirmation email | Async | Minutes of delay are fine |
| Invoice PDF generation | Async | ~880 ms of CPU nobody is waiting for |
| Search reindex | Async | Eventual consistency is acceptable here |
| Recommendation model update | Async | Hours of delay are acceptable |
| Analytics event | Async | Nobody waits for a dashboard |
| Warehouse pick list | Async | Humans act on it minutes later |

### 4.10 The `202 Accepted` + status polling pattern

When the whole operation is long (a CSV export, a bulk import, a refund that needs manual review), do not hold the connection. Return `202 Accepted` with a resource the client can poll.

Full API shape for BookNook's bulk catalogue import:

```
POST /v1/imports
  Idempotency-Key: 9f3a-...          ← client-generated; replays return the same job
  → 202 Accepted
    Location: /v1/imports/imp_01HX…   ← where to poll
    Retry-After: 2                    ← how long to wait before the first poll
    { "id": "imp_01HX…", "status": "pending" }

GET /v1/imports/imp_01HX…
  → 200 OK
    { "id":"imp_01HX…", "status":"running",
      "progress": { "processed": 12840, "total": 50000 },
      "created_at":"…", "updated_at":"…" }
    Retry-After: 5                    ← server controls poll rate; back off as jobs lengthen

  terminal states:
    { "status":"succeeded", "result": { "href":"/v1/imports/imp_01HX…/report" } }
    { "status":"failed",
      "error": { "code":"invalid_isbn", "message":"…", "line": 3122 },
      "retriable": false }            ← tell the client whether retrying can help
```

Design points that are commonly missed:
- **The job resource is a first-class resource** with its own URL, not a query against a side table.
- **`Retry-After` must be honoured and should grow** — otherwise 50,000 clients poll every second and you have built a self-inflicted load test.
- **Idempotency-Key on the POST** prevents a client retry from creating two jobs (Lesson 17).
- **Terminal states must be distinguishable and must include whether a retry is useful.**
- **Polling is the simplest option, not the only one.** Alternatives: webhooks (server calls you; needs the client to be reachable and introduces its own delivery-guarantee problem), WebSocket/SSE push (Lesson 11), or long polling. Polling wins on simplicity and on working through every firewall; it loses on wasted requests.

### 4.11 User-experience patterns for asynchronous work

Making work asynchronous moves a problem from the backend to the interface. Three standard patterns:

- **Optimistic UI**: render the expected result immediately and reconcile later. Correct when the failure probability is very low and the correction is cheap (adding to a cart, liking a review). Wrong for payments — showing "paid" and retracting it is far worse than showing a 2-second spinner.
- **Progress indication**: a determinate bar when you can compute `processed/total`; an indeterminate one otherwise. The crucial rule is **never show a determinate bar you cannot honour** — a bar that sits at 94 % for four minutes destroys trust more than no bar at all.
- **Notification on completion**: email, push, in-app toast, or a badge on a "Jobs" page. Mandatory when the work may exceed the user's attention span (~20 seconds). Always pair with a durable record the user can return to, because notifications get lost.

A fourth, underused pattern: **tell the truth about asynchrony**. "Your order is confirmed. Your receipt will arrive within a few minutes." costs one sentence and eliminates an entire class of support ticket.

## 5. Request / data flow

A customer clicks "Place order" on BookNook. The core is orchestrated and synchronous; the tail is choreographed and asynchronous.

1. Browser sends `POST /v1/orders` with the cart and an `Idempotency-Key` header.
2. The API gateway terminates TLS, authenticates the session, and routes to the Checkout service (Lessons 21–22).
3. Checkout service checks the idempotency store: has this key been seen? No → insert a row `status = in_progress`, and create a workflow row `(workflow_id, step = validate, state_json, deadline = now + 8s)`.
4. **Step 1, sync** — validate the cart: prices re-fetched, totals recomputed server-side. ~12 ms.
5. **Step 2, sync** — `InventoryService.reserve(order_id, items)` with a 400 ms per-try timeout. Returns `reservation_id`, holding stock for 15 minutes. Workflow row advances to `step = payment`. ~35 ms.
6. **Step 3, sync** — `PaymentService.authorise(order_id, amount, idempotency_key)` with a 3 s timeout. Returns `auth_id`. Workflow row advances to `step = persist`. ~220 ms.
7. **Step 4, sync** — write the order row with `status = confirmed`, `auth_id`, `reservation_id`, in one local ACID transaction (Lesson 52). ~25 ms.
8. In the *same* local transaction, insert an `OrderPlaced` event into an outbox table. (Writing to the database and publishing to a broker are two systems and cannot be made atomic by wishing; the outbox pattern is **Topic 22 — Outbox pattern and dual writes**.)
9. Checkout marks the workflow row `status = completed` and the idempotency row `status = done` with the stored response body.
10. Checkout returns `201 Created` with the order ID and confirmation page data. **Total user-visible time ≈ 292 ms.**
11. Asynchronously, a relay publishes the outboxed `OrderPlaced` event to the broker.
12. Five independent subscribers react: email, invoice PDF, search reindex, recommendations, analytics (Lesson 69).
13. The invoice worker finishes ~900 ms later and writes a `receipt_url` onto the order. The confirmation page, which displayed "receipt generating…", picks it up on its next poll or via a push update.

Failure branch at step 6: the payment call times out ambiguously. The workflow row stays at `step = payment` with `attempt_count = 1`. The sweeper picks it up 5 s later, **queries** the payment provider by idempotency key rather than re-authorising, and resolves the ambiguity. If no authorisation exists, it retries; if one exists, it moves to step 7. If the overall deadline passes, it runs the compensation `InventoryService.release(reservation_id)` and marks the workflow `failed`.

## 6. ASCII diagram

```
ORCHESTRATION — one brain, state in one table
                         ┌────────────────────────────┐
  POST /orders ─────────▶│   CheckoutOrchestrator     │
                         │  workflow_id=wf_88         │
                         │  step: validate→reserve→   │
                         │        pay→persist         │
                         └──┬────┬────┬────┬──────────┘
                     (1)│   (2)│  (3)│  (4)│
                        ▼      ▼     ▼     ▼
                     Order  Invent  Pay  Order
                     svc     svc    svc   svc
   ✔ whole process readable in one file
   ✔ "why is #4471 stuck?" = SELECT * FROM workflows WHERE id=…
   ✘ every change to the process edits this one component


CHOREOGRAPHY — no brain, state implied by events in flight
   POST /orders ─▶ Order svc ──emit OrderCreated──┐
                                                  ▼
                               Inventory svc ──emit StockReserved──┐
                                                                   ▼
                                           Payment svc ──emit PaymentAuthorised──┐
                                                                                 ▼
                                                              Order svc ──emit OrderConfirmed
   ✔ add a 6th reactor without editing anybody
   ✘ the business process exists NOWHERE as text
   ✘ stuck between StockReserved and PaymentAuthorised = silence


THE HYBRID THAT PRODUCTION ACTUALLY USES
   ┌─────────── SYNCHRONOUS, ORCHESTRATED (user waits 292 ms) ──────────┐
   │  validate ─▶ reserve stock ─▶ authorise payment ─▶ persist order   │
   └──────────────────────────────┬─────────────────────────────────────┘
                                  │ emit OrderPlaced (via outbox)
          ┌───────────┬───────────┼───────────┬────────────┐
          ▼           ▼           ▼           ▼            ▼
       email      invoice     reindex    recommend     analytics
   └──────── ASYNCHRONOUS, CHOREOGRAPHED (nobody waits) ─────────┘


LATENCY: THE SAME SIX STEPS, TWO ARRANGEMENTS
  ALL SYNCHRONOUS
  |--25--|--35--|-----220-----|-----450-----|---300---|--140--|  = 1170 ms
   order  stock    payment       invoice       email    index
                                 ▲ user is staring at a spinner for ALL of this

  SYNC CORE + ASYNC TAIL
  |--25--|--35--|-----220-----|  = 280 ms  ◀── user is done here
                               └─▶ invoice | email | index  (background)


WHY A TIMEOUT TELLS YOU NOTHING (Lesson 65, applied)
   Checkout ──authorise()──▶ Payment ──▶ Card network
        │                        │
        │◀──── 3 s timeout ──────X  (no reply)
        │
   Possible realities:
     (a) request never arrived        → safe to retry
     (b) card WAS charged, reply lost → retrying double-charges
     (c) still processing             → retrying double-charges
   You cannot tell them apart. ⇒ QUERY by idempotency key, don't guess.
```

## 7. Detailed example

### 7a. BookNook — the same workflow, modelled both ways

**Starting point (all synchronous, orchestrated).** Checkout is one method that calls six services in sequence. Measured p50 is 1,170 ms, p99 is 4,100 ms. Over a 30-day window, checkout succeeded 99.41 % of the time; the top two causes were the email provider (a 99.5 %-availability third party) and the invoice renderer OOM-ing on large multi-item orders.

**Problem analysis.** The email provider has *no business* being able to fail an order. Neither does PDF rendering. Both are in the critical path purely because someone wrote them there in one method three years ago.

**Redesign.** Split at the point of no return — the moment the order row is written `confirmed`:

- **Before the split**: validate → reserve → authorise → persist. Orchestrated by `CheckoutOrchestrator`, state in a `workflows` row, compensations defined for reserve and authorise.
- **After the split**: `OrderPlaced` emitted via the outbox, five choreographed subscribers.

**Results.** p50 1,170 ms → 292 ms (−75 %). p99 4,100 ms → 640 ms. Checkout availability 99.41 % → 99.70 % (§9). Support tickets about "order failed but I was charged" fell to near zero because the ambiguous-payment branch is now explicitly resolved by querying rather than by retrying.

**What got harder, honestly:** (a) "where is my receipt?" became a real question needing a UI state; (b) a bug in the invoice worker now produces *silently missing invoices* rather than a loud checkout failure, so a new alert on invoice-queue age was required; (c) debugging a single order now spans a trace across six services instead of one stack trace, which is why distributed tracing (Topic 28) became non-optional.

### 7b. Contrasting example — airline ticket issuance

Booking a flight looks superficially similar but splits in a different place, and the reason is instructive.

- **Synchronous:** seat availability check, fare re-quote, seat hold, payment authorisation. The customer must see all of these, and a fare that changed must be shown before they commit.
- **Asynchronous and genuinely slow:** **ticketing** — writing the ticket into the airline's and the GDS's systems, which can take seconds to minutes, and occasionally requires a human. This is why airlines send "booking confirmed, your e-ticket will follow" rather than issuing instantly.
- **Long-running:** the booking workflow stays alive for months — schedule changes, seat reassignment, check-in, no-show handling. No process lives that long; this is a textbook durable-execution case with timers measured in weeks.
- **Compensation is messy, not clean:** cancelling after ticketing is not "delete the ticket". It is a refund governed by fare rules, possibly partial, possibly a voucher. The compensating action is a *different business operation* with its own failure modes — exactly the point of §4.7.

The contrast with BookNook: BookNook's asynchronous tail is *optional side-effects*; the airline's asynchronous middle is *the core deliverable*. When the thing the customer is actually buying is asynchronous, you must invest in status visibility — hence "manage booking" pages, reference codes, and status emails.

## 8. Code or configuration example

> **Illustrative only — not production-ready.** Error types are simplified, there is no connection pooling, no tracing, no metrics, no authentication, and the sweeper here is a naive loop. The goal is to make the *shape* of a durable orchestrated workflow visible.

```python
# ---------------------------------------------------------------------------
# BookNook checkout: orchestrated, durable-ish workflow (teaching version)
# ---------------------------------------------------------------------------

STEPS = ["validate", "reserve", "authorise", "persist", "emit"]   # ordered pipeline

def start_checkout(cart, idem_key):
    # Idempotency first (Lesson 17): a client retry must NOT create a 2nd workflow.
    existing = db.fetch("SELECT * FROM idempotency WHERE key=%s", idem_key)
    if existing and existing.status == "done":
        return existing.response_body          # replay the stored response verbatim
    if existing:                               # in flight: do not start a duplicate
        return http_409_conflict("request already in progress")

    wf_id = new_id()
    db.tx(lambda t: (                          # one local ACID transaction (Lesson 52)
        t.insert("idempotency", key=idem_key, status="in_progress", wf_id=wf_id),
        t.insert("workflows",
                 id=wf_id,
                 type="checkout",
                 step="validate",              # WHERE WE ARE — the single most important column
                 state=json({"cart": cart}),   # WHAT WE KNOW SO FAR
                 status="running",
                 attempts=0,
                 deadline=now() + seconds(8)), # overall workflow deadline
    ))
    return run_workflow(wf_id)                 # drive it inline so the user gets a result


def run_workflow(wf_id):
    wf = db.fetch("SELECT * FROM workflows WHERE id=%s FOR UPDATE", wf_id)  # lease the row
    while wf.step != "done":
        if now() > wf.deadline:                # overall deadline blown -> give up forward progress
            return compensate(wf, reason="deadline_exceeded")
        try:
            wf = execute_step(wf)              # advances wf.step and persists BEFORE returning
        except AmbiguousError:                 # timeout: outcome UNKNOWN (Lesson 65)
            # Do NOT retry blindly and do NOT compensate blindly. Ask the downstream system.
            if not resolve_ambiguity(wf):
                db.update("workflows", id=wf.id, attempts=wf.attempts + 1)
                return http_202_accepted(wf.id)   # hand the user a pollable job instead of an error
        except PermanentError as e:            # declined card, out of stock: no retry can help
            return compensate(wf, reason=e.code)
    return http_201_created(wf.state["order"])


def execute_step(wf):
    s, st = wf.step, json_loads(wf.state)

    if s == "validate":
        st["totals"] = pricing.recompute(st["cart"])      # never trust client-sent prices
        return advance(wf, "reserve", st)

    if s == "reserve":
        # Per-try timeout chosen from measured p99.9 (not guessed). Holds stock 15 min.
        st["reservation_id"] = inventory.reserve(
            order_id=wf.id, items=st["cart"], timeout_ms=400)
        return advance(wf, "authorise", st)

    if s == "authorise":
        # The idempotency key is derived from wf.id so a retry hits the SAME authorisation.
        st["auth_id"] = payments.authorise(
            amount=st["totals"]["grand_total"],
            idempotency_key=f"wf-{wf.id}-auth",           # makes retry safe at the provider
            timeout_ms=3000)
        return advance(wf, "persist", st)

    if s == "persist":
        # Order row AND the outbox event in ONE transaction. Publishing to a broker here
        # instead would be a dual write and could lose the event (Topic 22 — Outbox pattern).
        db.tx(lambda t: (
            t.insert("orders", id=wf.id, status="confirmed",
                     auth_id=st["auth_id"], reservation_id=st["reservation_id"]),
            t.insert("outbox", topic="order.placed",
                     payload=json({"order_id": wf.id, "total": st["totals"]})),
        ))
        st["order"] = {"id": wf.id, "status": "confirmed"}
        return advance(wf, "done", st)


def advance(wf, next_step, state):
    # Persist progress BEFORE doing anything else. If the process dies on the next line,
    # the sweeper resumes from exactly here. This single write is what makes it durable.
    db.update("workflows", id=wf.id, step=next_step,
              state=json(state), updated_at=now())
    return wf.with_(step=next_step, state=json(state))


# Compensations run in REVERSE order of completion (Topic 21 — Sagas).
COMPENSATIONS = {
    "authorise": lambda st: payments.void(st["auth_id"]),        # clean inverse
    "reserve":   lambda st: inventory.release(st["reservation_id"]),
    # "persist" deliberately has NO compensation: it is the point of no return.
}

def compensate(wf, reason):
    st = json_loads(wf.state)
    for step in reversed(STEPS[:STEPS.index(wf.step)]):          # only completed steps
        fn = COMPENSATIONS.get(step)
        if fn:
            retry_with_backoff(lambda: fn(st), attempts=5)       # compensations MUST be retried
    db.update("workflows", id=wf.id, status="failed", failure_reason=reason)
    return http_422(reason)


# The sweeper is what turns a database row into a durable workflow. Without it,
# a workflow whose process died simply sits at step='authorise' forever.
def sweeper():
    while True:
        stalled = db.fetch("""SELECT id FROM workflows
                              WHERE status='running' AND updated_at < %s
                              LIMIT 100""", now() - seconds(5))   # step-level staleness
        for row in stalled:
            run_workflow(row.id)                                  # resume, don't restart
        sleep(1)
```

The same workflow expressed on a durable execution engine collapses the sweeper, the `workflows` table, and the resume logic into the runtime:

```yaml
# AWS Step Functions (Amazon States Language) — illustrative, trimmed
StartAt: ReserveStock
States:
  ReserveStock:
    Type: Task
    Resource: arn:aws:lambda:...:reserveStock
    TimeoutSeconds: 1                       # per-step timeout, enforced by the engine
    Retry:
      - ErrorEquals: [States.Timeout, TransientError]
        IntervalSeconds: 1
        BackoffRate: 2.0                    # exponential backoff (Lesson 17)
        MaxAttempts: 3
    Catch:
      - ErrorEquals: [States.ALL]
        Next: Fail                          # nothing committed yet -> no compensation needed
    Next: AuthorisePayment

  AuthorisePayment:
    Type: Task
    Resource: arn:aws:lambda:...:authorisePayment
    TimeoutSeconds: 5
    Catch:
      - ErrorEquals: [States.ALL]
        Next: ReleaseStock                  # compensation for the step already completed
    Next: PersistOrder

  ReleaseStock:                             # compensating action, not a rollback
    Type: Task
    Resource: arn:aws:lambda:...:releaseStock
    Next: Fail

  PersistOrder:
    Type: Task
    Resource: arn:aws:lambda:...:persistOrder
    End: true                               # point of no return; everything after is async
  Fail:
    Type: Fail
```

## 9. Numerical example

### Assumptions (all stated explicitly)

1. BookNook checkout touches six services; each is independently **99.9 %** available.
2. Failures are **independent** — this is a simplification. In reality correlated failures (a shared database, a shared availability zone, a shared deploy pipeline) make real numbers *worse*, so these figures are optimistic upper bounds.
3. Measured mean service times, approximate and typical for this shape of workload:
   order write 25 ms, stock reservation 35 ms, payment authorisation 220 ms, invoice render 450 ms, email dispatch 300 ms, search reindex 140 ms.
4. Network hop overhead ≈ 3 ms per call, same region.
5. A month is 30 days = 43,200 minutes.
6. "Available" means the end-to-end checkout returns a correct `201`.

### Step 1 — availability of the fully synchronous chain

```
A_sync = 0.999 ^ 6
       = 0.999 × 0.999 = 0.998001
       × 0.999 = 0.997003
       × 0.999 = 0.996006
       × 0.999 = 0.995010
       × 0.999 = 0.994015
A_sync = 0.994015  →  99.4015 %
```

Unavailability = `1 − 0.994015 = 0.005985`.
Monthly downtime = `0.005985 × 43,200 min = 258.5 minutes ≈ 4 h 19 min`.

Compare: a *single* 99.9 % service is down `0.001 × 43,200 = 43.2 min/month`. **Chaining six of them multiplied the downtime by six.**

### Step 2 — availability after moving four steps out of the request path

Synchronous steps remaining: stock reservation, payment authorisation. (Order persistence is BookNook's own database, counted inside the checkout service itself; to stay conservative, count three synchronous components.)

```
A_async(2 deps) = 0.999 ^ 2 = 0.998001  → 99.8001 %
A_async(3 deps) = 0.999 ^ 3 = 0.997003  → 99.7003 %
```

Taking the conservative three-component figure:
Unavailability = `1 − 0.997003 = 0.002997`.
Monthly downtime = `0.002997 × 43,200 = 129.5 minutes ≈ 2 h 10 min`.

**Improvement: 258.5 → 129.5 minutes, a 50 % reduction in checkout downtime, achieved without making any single service more reliable.** This is the central result of the lesson: *removing a dependency from the critical path is often cheaper and more effective than improving that dependency.*

### Step 3 — what a single unreliable third party costs you

Suppose the email provider is only **99.5 %** available, with the other five at 99.9 %.

```
A = 0.999^5 × 0.995
  = 0.995010 × 0.995
  = 0.990035  → 99.0035 %
```
Monthly downtime = `0.009965 × 43,200 = 430.5 minutes ≈ 7 h 11 min`.

Moving *only* the email step out of the path and leaving the other five synchronous:
```
A = 0.999^5 = 0.995010 → 99.5010 %
downtime = 0.004990 × 43,200 = 215.6 min ≈ 3 h 36 min
```
**One step moved out of the critical path halved the downtime.** The email provider did not get better; it simply stopped being able to fail checkout.

### Step 4 — latency

Fully synchronous, sequential:
```
service time = 25 + 35 + 220 + 450 + 300 + 140 = 1,170 ms
network      = 5 hops × 3 ms                   =    15 ms
TOTAL        =                                 ≈ 1,185 ms
```

Sync core only (order, stock, payment):
```
service time = 25 + 35 + 220 = 280 ms
network      = 2 hops × 3 ms =   6 ms
TOTAL        =               ≈ 286 ms
```

Reduction = `(1,185 − 286) / 1,185 = 0.7586` → **75.9 % faster**, and the work removed (890 ms) is exactly the work nobody was waiting for.

### Step 5 — the throughput consequence (often missed)

Little's Law, which Lesson 68 uses heavily: `concurrency = arrival_rate × time_in_system`.

At 200 checkouts/second:
```
Synchronous:  L = 200 × 1.185 s = 237 concurrent requests held open
Async core:   L = 200 ×  0.286 s =  57.2 concurrent requests held open
```
If each held request consumes one worker thread and a server runs 50 threads:
```
Synchronous:  237 / 50 = 4.74  → 5 servers minimum (before headroom)
Async core:    57 / 50 = 1.14  → 2 servers minimum (before headroom)
```
**The same hardware now serves roughly 4× the checkout traffic.** Asynchrony bought availability, latency *and* capacity from one change.

### Step 6 — the cost side, stated honestly

The deferred work did not disappear. At 200 orders/s the invoice renderer must still process 200 jobs/s at 450 ms each:
```
required concurrency = 200 × 0.45 = 90 simultaneous renders
```
That is now a separately scaled worker pool. The gain is not "less work" — it is **work moved off the latency-and-availability-critical path**, where it can fail, retry, and scale independently.

## 10. Trade-offs

| Dimension | Synchronous workflow | Asynchronous workflow |
|---|---|---|
| Latency perceived by user | Sum of all steps | Sum of the synchronous core only |
| Availability | Product of all dependencies | Product of the synchronous core only |
| Error reporting | Immediate, precise, in the response | Deferred; needs a status resource or notification |
| Debugging | One stack trace | Distributed trace across services and time |
| Consistency | Strong at the moment of response | Eventual; the system is briefly "not yet finished" |
| Infrastructure | None extra | Broker, workers, DLQ, status store, monitoring |
| Backpressure behaviour | Caller blocks or times out | Work queues up; needs depth alarms (Lesson 68) |
| Capacity under spike | Degrades into timeouts | Degrades into a backlog |
| Cognitive load | Low | Meaningfully higher — the main hidden cost |
| Testing | Straightforward | Needs time control, replay, and duplicate-delivery tests |
| Rollback semantics | Database transaction | Compensating actions (Topic 21) |

Orchestration vs choreography is a second, independent axis:

| Dimension | Orchestration | Choreography |
|---|---|---|
| Process visibility | Explicit, in one file | Emergent, nowhere |
| Adding a new reactor | Edit the orchestrator | Zero changes to producers |
| Changing step order | Easy | Hard, cross-team |
| Failure handling | Centralised | Per-service, duplicated |
| Risk | Central bottleneck for change | Hidden cycles, invisible stalls |
| Best for | Transactional cores | Independent side-effects |

## 11. Failure scenarios

| Failure | What the user experiences | How it is detected | How it recovers |
|---|---|---|---|
| Orchestrator process dies mid-workflow (after stock reserved, before payment) | Request fails or hangs; order appears "pending" | Sweeper finds `workflows` rows with stale `updated_at`; alert on `stalled_workflows > 0` | Sweeper resumes from the persisted `step`; if the deadline passed, compensations release the stock |
| Payment call times out with unknown outcome | Either a `202` and a pending order, or a brief delay | Per-try timeout fires; `ambiguous_payment_total` counter increments | Query the provider by idempotency key; never blind-retry. Resolve to success or retry accordingly |
| Asynchronous email worker is down for 2 hours | Order confirmed normally; no receipt email arrives | Queue age / depth alarm (Lesson 68 §4); `oldest_message_age > 300 s` | Messages persist in the queue; workers restart and drain the backlog. No orders were lost |
| Invoice worker crashes on one malformed order, repeatedly | That one customer has no receipt; everyone else is fine | Redelivery count exceeds threshold → dead-letter queue (Topic 12 — Poison messages) | Message moves to DLQ after N attempts; alert fires; engineer fixes and replays |
| Compensating action itself fails (`releaseStock` errors) | Stock appears unavailable to other customers for 15 minutes | `compensation_failures` metric; reservation TTL expiry log | Retry compensation with backoff; the reservation's own 15-minute TTL acts as a backstop. **Every compensation needs a time-based backstop** |
| A choreography cycle: service A's reaction triggers B, which re-triggers A | Rising latency, then broker saturation; checkout slows globally | Broker publish-rate anomaly; event counts growing super-linearly; tracing shows repeated span names | Kill the subscription, add a loop-guard (event causation ID / hop counter), redeploy |
| Client polls `/imports/{id}` every 200 ms from 50,000 clients | Site slows for everyone | API gateway request-rate spike with ~100 % `200 running` responses | Enforce and grow `Retry-After`; rate-limit the status endpoint; cache status responses for 1 s |
| Durable workflow code is changed while executions are in flight | Old orders fail or take an unexpected branch | Workflow engine raises non-determinism / version errors on replay | Use the engine's versioning API; keep old branches until all in-flight executions drain |
| Clock skew makes a workflow deadline appear already passed (Lesson 66) | Orders fail instantly with "deadline exceeded" | `deadline_exceeded` rate spikes on one host only | Use monotonic clocks for durations, never wall-clock subtraction; NTP alerting |

## 12. Alternatives

| Approach | What it is | Strengths | Weaknesses | Best when |
|---|---|---|---|---|
| Fully synchronous request/response | Caller waits for all steps | Simplest to build, debug, and reason about | Latency adds, availability multiplies, no spike absorption | ≤3 steps, total p99 under ~300 ms, all steps user-visible |
| Orchestrated workflow with a DB state row | Explicit steps, persisted progress, sweeper resumes | Full visibility, no new infrastructure, easy querying | You hand-build retries, timers, versioning | Core transactional flows in a team that already has a solid database |
| Durable execution engine (Temporal, Cadence, Step Functions) | Workflow-as-code with replayed history | Long timers, automatic retries, strong visibility | New infrastructure, determinism constraints, versioning discipline | Workflows lasting minutes to months, or many workflow types |
| Choreography over events | Services react to each other's events | Maximum organisational decoupling | No central process definition; hard to debug | Side-effects and notifications fanning out from a completed fact |
| Message queue + workers | Enqueue work, process later | Spike absorption, independent scaling, retries | Eventual consistency, needs DLQ and monitoring | Deferrable unit work (Lesson 68) |
| Batch / scheduled job | Collect work, process on a cron | Trivially simple, very efficient per item | Latency measured in the batch interval | Nightly reconciliation, reporting |
| `202 Accepted` + polling | Job resource the client polls | Works through any firewall, simple clients | Wasted requests; client must manage state | Long user-initiated operations |
| Webhooks | Server calls the client back on completion | No polling waste; near-real-time | Client must be publicly reachable; you inherit *their* reliability problem | B2B integrations with capable partners |
| WebSocket / SSE push | Persistent connection for progress (Lesson 11) | Best UX, instant updates | Connection state at scale; proxies and mobile networks interfere | Interactive dashboards, collaborative editing |
| Two-phase commit across services | Distributed atomic transaction | True atomicity | Blocking coordinator, poor availability, rarely supported | Almost never across services (Topic 20) |

## 13. When to use it

Use an **asynchronous workflow** when:

1. **A step is not needed for the user's decision or next action.** BookNook's invoice PDF, search reindex, recommendation refresh, and analytics all qualify. Each one removed from the critical path buys latency *and* availability simultaneously (§9).
2. **A dependency is less reliable than your target SLO.** A 99.5 % third-party email provider cannot sit inside a 99.9 % checkout. Either make it asynchronous or accept its availability as your ceiling. There is no third option.
3. **The work is slow relative to human attention.** Anything over ~1 second of pure processing — report generation, video transcoding, bulk import, ML inference batches — belongs behind `202 Accepted` with a status resource.
4. **Load is spiky and the work is deferrable.** A flash sale producing 10× the normal order rate will melt a synchronous invoice renderer but merely creates a temporary backlog behind a queue (Lesson 68 §4).
5. **The workflow outlives any process.** A 14-day return window, a 30-day trial expiry, a multi-day KYC review. These require durable timers; in-memory `sleep` is not a design.
6. **Multiple independent consumers care about the same completed fact.** `OrderPlaced` is consumed by five teams who should not need to coordinate releases (Lesson 69).

Use **orchestration** specifically when the process is transactional, needs compensation, and must be auditable. Use **choreography** specifically when adding reactions without touching the producer is the dominant requirement.

## 14. When not to use it

Avoid asynchrony when:

1. **The user must see the result to continue.** Stock availability, payment authorisation, login, and validation errors must be synchronous. Making these asynchronous produces the worst possible UX: a confirmation that is later retracted.
2. **The workflow is short and all steps are cheap.** Three calls totalling 80 ms do not justify a broker, a worker fleet, a DLQ, a status endpoint, and the ongoing cognitive load. The complexity cost is real and permanent; the latency saving is 80 ms.
3. **You cannot make the work idempotent and you cannot tolerate duplicates.** Asynchronous delivery is at-least-once in practice (Topic 10). If a duplicate causes a double charge or a double shipment and you have no deduplication strategy, fix idempotency *first* (Lesson 17).
4. **The team has no operational capacity for a new failure domain.** Queues require depth alarms, age alarms, DLQ alarms, and a replay runbook. An asynchronous system with no monitoring fails silently, which is strictly worse than failing loudly.
5. **Strong read-your-writes consistency is required immediately after the action.** If the user will refresh and expect to see the effect, an asynchronous update will produce a confusing "it didn't work" moment unless you also build optimistic UI (Lesson 46 on consistency models).

## 15. Common mistakes

### Beginner mistakes

- **Treating "async" as "fire and forget".** Dropping a call into a background thread with no persistence, no retry, and no dead-letter path. When the process restarts, the work vanishes with no trace.
- **Making payment asynchronous.** Then telling the user "order confirmed" and emailing "actually, declined" an hour later.
- **No idempotency on the asynchronous side.** At-least-once delivery sends two emails, creates two invoices, or ships two books. The key must be derived from the business event, not generated by the consumer.
- **Returning `200 OK` instead of `202 Accepted`.** `200` means "done". `202` means "accepted, not done". The distinction is the entire contract.
- **An unbounded, undocumented polling loop.** Clients polling every 100 ms turn a status endpoint into the busiest service you own.
- **Keeping workflow state only in memory.** Works perfectly until the first deploy.
- **Having no way to answer "what is the status of order #4471?"** If the only answer is grepping logs across five services, the design is incomplete.

### Experienced-engineer mistakes

- **Choosing choreography for the transactional core** because it is "more decoupled", then spending a year building tooling to recover the visibility that orchestration gives for free.
- **Compensating on an ambiguous timeout.** Voiding an authorisation that actually succeeded, or releasing stock for an order that was in fact confirmed. The correct move is to resolve the ambiguity by querying, then act (§4.8).
- **Forgetting that compensations can fail.** They need retries, their own alerting, and a time-based backstop such as a reservation TTL.
- **Dual writes.** Writing the order to the database and publishing to the broker as two separate operations. A crash between them loses the event permanently. Use the outbox (Topic 22).
- **Ignoring workflow versioning.** Deploying changed workflow code while thousands of executions are mid-flight, breaking determinism on replay.
- **Making everything asynchronous.** Every step behind a queue means every read is eventually consistent, every bug is a timing bug, and no one can reproduce anything locally. Asynchrony is a tool for specific steps, not an architectural identity.
- **Using wall-clock arithmetic for deadlines.** Clock skew (Lesson 66) makes workflows expire early on one host and never on another.
- **Tuning per-step timeouts without a workflow deadline.** Six steps at 3 s each means a worst case of 18 s, which no one chose and no user will wait for.
- **No backpressure.** The synchronous part accepts work far faster than the asynchronous part can drain it, so the backlog grows forever (Lesson 68 §9; Topic 13 — Backpressure and flow control).

## 16. Interview perspective

This topic appears in almost every senior system-design interview, usually implicitly: the moment you draw a service calling five other services synchronously, you have created the scenario.

**What strong candidates do:**

- **Split the workflow explicitly and justify each side.** "Stock and payment are synchronous because the user's decision depends on them. Email, invoice, and reindex are asynchronous because a 30-second delay is invisible to the business."
- **Do the availability arithmetic out loud.** "Six services at three nines in series is 99.4 %, about four hours of downtime a month. Moving four out of the path takes us to 99.7 %." This single calculation signals seniority faster than almost anything else.
- **Name the orchestration/choreography choice and defend it.** "Orchestrated core because I need compensation and auditability; choreographed tail because I want teams to add consumers without asking me."
- **Raise the ambiguous-timeout problem unprompted**, and propose query-by-idempotency-key rather than blind retry.
- **Mention the outbox** the moment they draw "write to DB, publish event".
- **Design the `202` contract concretely** — status resource, `Retry-After`, terminal states, retriability flag.
- **State what got worse.** "This adds a queue, a DLQ, two new alarms, and makes single-order debugging require tracing."

**Common interview questions:**
- "The user clicks buy and waits 4 seconds. Make it fast." (Answer: split the workflow, not micro-optimise the PDF renderer.)
- "What happens if the payment service times out?" (The ambiguity discussion.)
- "How do you know a workflow is stuck?" (Persisted state + sweeper + age alarms.)
- "How would you handle a 30-day trial expiry?" (Durable timers, not cron over a huge table — though a well-indexed scheduled scan is a legitimate simpler answer and saying so earns credit.)

**Red flags interviewers watch for:**

- Saying "we'll make it asynchronous" without saying **which steps** and **why those steps**.
- Drawing a queue and never mentioning what consumes it, how it scales, or what happens to failures.
- Claiming asynchronous processing "makes it more reliable" with no mention of eventual consistency, duplicates, or ordering.
- Asserting "exactly-once delivery" as though it were a configuration flag.
- Not knowing where workflow state lives.
- Proposing two-phase commit across microservices without acknowledging its availability cost.
- Treating a timeout as proof of failure.
- Answering "it depends" without naming the specific variables it depends on.

## 17. Knowledge check

**Conceptual**

1. A colleague argues that choreography is strictly better than orchestration because "there is no single point of failure". Explain precisely what their argument gets right, what it gets wrong, and what property orchestration provides that choreography cannot provide without extra tooling.

2. Explain why a compensating action is not the same thing as a database rollback, and give an example of a step for which no satisfactory compensating action exists. What does that imply about where such a step should sit in the workflow?

3. A durable execution engine requires workflow code to be deterministic. Explain *why* this constraint exists in terms of how the engine recovers a workflow after a process crash, and name two ordinary programming constructs that violate it.

**Practical scenario**

4. BookNook is launching a "pre-order" feature. A customer pre-orders a book that ships in 90 days. At order time: card is authorised (authorisations expire after 7 days), stock does not yet exist, and the customer must be charged only when the book ships. Design the workflow. State for each step whether it is synchronous or asynchronous, where the workflow state lives, what happens when the 7-day authorisation expires before day 90, what the compensating actions are, and how you would detect a pre-order that has silently stalled.

**Calculation**

5. A checkout path calls eight services in sequence. Six are 99.95 % available and two are 99.5 % available. Mean service times are 20, 30, 40, 180, 60, 500, 350 and 90 ms respectively, with a 4 ms network hop between each pair of calls. (a) Compute the end-to-end availability and the monthly downtime in minutes for a 30-day month. (b) Compute the end-to-end latency. (c) You may move the three slowest steps out of the synchronous path, and those three happen to include both 99.5 % services. Compute the new availability, new monthly downtime, and new latency. (d) At 350 requests/second, use Little's Law to compute how many concurrent requests are held open before and after the change.

**Progress checklist**

```
Phase 1 — Foundations ............................ [x] complete (Lessons 1–17)
Phase 2 — Scaling ................................ [x] complete (Lessons 18–39)
Phase 3 — Data and storage ....................... [x] complete (Lessons 40–63)

Phase 4 — Distributed Systems & Asynchronous Architecture
  [x]  1. Why distributed systems are difficult ............ (Lesson 64)
  [x]  2. Partial failures and network partitions .......... (Lesson 65)
  [x]  3. Clocks and ordering .............................. (Lesson 66)
  [x]  4. Synchronous versus asynchronous workflows ........ (Lesson 67)  ◀ you are here
  [ ]  5. Message queues
  [ ]  6. Publish-subscribe systems
  [ ]  7. Event streams
  [ ]  8. Event-driven architecture
  [ ]  9. Brokers and broker-less designs
  [ ] 10. Delivery guarantees
  [ ] 11. Message ordering and partitioning
  [ ] 12. Poison messages and dead-letter queues
  [ ] 13. Backpressure and flow control
  [ ] 14. Consumer groups and scaling consumers
  [ ] 15. Idempotent consumers and deduplication
  [ ] 16. Retries, backoff and jitter
  [ ] 17. Timeouts and deadline propagation
  [ ] 18. Circuit breakers and bulkheads
  [ ] 19. Service discovery
  [ ] 20. Distributed transactions and two-phase commit
  [ ] 21. Sagas and compensating transactions
  [ ] 22. Outbox pattern and dual writes
  [ ] 23. CQRS
  [ ] 24. Event sourcing
  [ ] 25. Stream processing and windowing
  [ ] 26. Distributed locks and leases
  [ ] 27. Leader election and consensus
  [ ] 28. Distributed tracing and observability
  [ ] 29. Chaos engineering and failure injection
```

**NEXT LESSON: Phase 4, Topic 5 — Message queues**

---

# Lesson 68 — Message Queues

*Phase 4, Topic 5*

## 1. Simple definition

A **message queue** is durable, broker-held storage between a producer and a consumer. The producer writes a message and is done; the consumer later reads it, does the work, and **acknowledges** it. Until the ack arrives, the message stays in the queue and can be redelivered.

Three properties define it, and each one is a promise about a different failure:

1. **Durability of intent.** Once the broker has accepted the message (and replicated it, if it claims durability), the work will eventually happen even if the producer, the consumer, or both, die. This is what turns an ambiguous RPC (L65) into a *recorded obligation*.
2. **Decoupling of time.** The producer does not wait for the consumer, so their availability and their throughput are independent. A consumer that is down for two hours does not fail a single order.
3. **Competing consumers.** Multiple workers pull from the same queue, and each message is delivered to exactly one of them, so throughput scales by adding workers.

A queue delivers **at-least-once**, which is the honest guarantee and also a warning: a message may be processed twice, so consumers must be **idempotent** (L74). "Exactly once" is a marketing term for "at-least-once plus deduplication you have to write anyway" (L73).

Terms defined before use:

- **Message**: the unit of work, usually a small JSON body plus attributes. Sizes are limited — 256 KB on SQS, 128 KB by default on RabbitMQ — so large payloads go to object storage with a pointer in the message.
- **Visibility timeout** (SQS) / **unacked delivery** (RabbitMQ): the window during which a delivered message is hidden from other consumers. If the consumer does not ack within it, the message becomes visible again and is redelivered.
- **Ack / nack**: explicit confirmation that the work succeeded (delete the message) or failed (requeue or dead-letter it).
- **Prefetch / QoS**: how many unacked messages a consumer will hold at once.
- **Dead-letter queue (DLQ)**: a separate queue for messages that failed repeatedly, so a poison message cannot loop forever (L75).
- **Queue depth vs queue age**: how many messages are waiting, and how old the oldest one is. **Age is the metric that matters**; depth is meaningless without knowing the arrival and drain rates.

The one sentence to carry out of this lesson: **a queue converts "I hope that call worked" into "that work is now somebody's durable problem" — and it charges you eventual consistency, a new failure domain, and the obligation to make every consumer idempotent.**

## 2. Why it exists

Lesson 67 established that a six-service synchronous checkout is `0.999⁶ = 99.4 %` available. A queue fixes that arithmetically: the steps you move behind a queue stop being **hard dependencies**, because the caller no longer waits for them.

BookNook's checkout, synchronously, calls six services. Move three of them behind a queue and the numbers change completely:

```
  SYNC:  checkout = inventory × payment × order × email × invoice × loyalty
                   = 0.999⁶ = 99.40 %     → 4.3 hours of downtime per month

  ASYNC: checkout = inventory × payment × order        (queue accepts email,
                   = 0.999³ = 99.70 %                   invoice, loyalty)
                                                     → 2.2 hours per month
```

Half the downtime, from moving three steps that nobody was waiting for anyway. Note what was *not* required: no service got better. The architecture stopped multiplying availability for work that had no business being on the critical path.

The second force is **spike absorption**, and it is the argument that closes most debates. BookNook's email renderer handles **40 messages/sec**. A flash sale pushes orders to **900/sec** for 90 seconds. Synchronously, that is 81,000 orders arriving at a service that can serve 3,600 of them in that window: 77,400 timeouts, and the orders *fail* even though nothing was wrong with them. With a queue, all 81,000 messages are accepted in 90 seconds, and the renderer drains them over **34 minutes** (or in 2 minutes with 23 workers). The work still takes the same total CPU. **The queue converts a capacity problem into a latency problem**, and latency problems are survivable while capacity problems are not.

The third force is the one that is least discussed and most valuable: a queue is the **only clean way to hold the intent** when an outcome is ambiguous. In L65's payment timeout, the ambiguity existed because the *request* was the only record of the intent. Put the intent in a durable message first, and a retry storm becomes a redelivery policy.

And the costs, stated up front because Lesson 67 §15 warns about them: eventual consistency (the user's receipt arrives later, so it needs a UI state), a new failure domain (the broker), a new alarm set (depth, age, DLQ, redelivery rate), the **dual-write problem** (you cannot atomically write your database *and* enqueue — L85's outbox exists for exactly this), and the requirement that every consumer be idempotent.

## 3. Real-world analogy

The kitchen order rail in a restaurant.

A waiter takes your order and **clips the ticket to a rail**. That act — clipping — is the producer's entire job. It takes 200 milliseconds, and it is *durable* in a specific sense: the ticket is physically present and cannot be forgotten, even if the waiter goes home, the till crashes, or the kitchen is behind. The waiter is free immediately; the customer waits for food, but not for the *waiter*.

Three behaviours of that rail are exactly the queue's mechanics:

- **Competing consumers.** Any cook can pull any ticket. Hire a second cook and the rail drains faster. Nobody needs to know which cook will take which ticket.
- **Visibility timeout.** A cook pulls a ticket and puts it on their station — it is off the rail, so no other cook takes it. If that cook walks out mid-dish, the ticket goes **back on the rail** after a while and someone else picks it up. The dish may then be cooked **twice**; that is at-least-once, and it is why the ticket says "no onions" rather than "add onions" — the instruction is *idempotent by design*.
- **The dead-letter spike.** One ticket is illegible: "table 9, ???". Cook after cook picks it up, cannot act, and puts it back. It circulates forever, and at peak it does so 40 times an hour, distracting everyone. The fix is a **spike** — a physical hook beside the rail — where illegible tickets get hung after the second attempt, and a manager deals with them. That is the DLQ, and every kitchen that survives a Saturday night has one.
- **The rail has finite length.** If the kitchen stops, tickets fill the rail and then start falling on the floor. The rail does not have infinite memory; the restaurant must either stop taking orders (**backpressure**, L76) or lose them (**retention expiry**). Most real outages are this: the rail was treated as infinite.

The managerial lesson is the one engineers keep re-learning: **the rail is not the kitchen**. A full rail does not mean food is coming faster; it means the *promise* has outpaced the *capacity*, and the only two honest responses are to add cooks or to stop taking orders.

## 4. Detailed technical explanation

### 4.1 Anatomy of a queue, and where the failure boundaries are

```
  PRODUCER                     BROKER                      CONSUMER
  ┌────────┐   1. send    ┌──────────────────┐  2. receive  ┌──────────┐
  │ order  │ ────────────►│  durable queue   │ ────────────►│  worker  │
  │  svc   │   (durable)  │  (replicated)    │  (invisible) │          │
  └────────┘              └──────────────────┘              └────┬─────┘
       ▲                          ▲   ▲                         │
       │                          │   │  4. not acked within      │ 3. work
       │                          │   │     visibility timeout    │    done
       │                          │   └───────── message becomes ─┘
       │                          │              visible again
       │                          └─── 5. ack  ────────────────────┘
       │                                  (delete)
   FAILURE BOUNDARY 1                 FAILURE BOUNDARY 2
   "did the send land?"               "did the work happen
   (producer crash between            before the ack?"
   DB commit and send =              (consumer crash after
   lost work → L85 outbox)           effect = duplicate)
```

Two boundaries, two classic bugs. **Boundary 1** is the dual-write problem: the producer writes to its database and sends to the queue, and those are two operations with no shared transaction. Crash between them and either the message never existed (lost work, silently) or it exists without the database row (a phantom job). **Boundary 2** is why at-least-once is the only honest delivery guarantee: the consumer's *effect* and its *ack* are also two operations. Ack before the work → the message disappears and the work never happens. Ack after the work → a crash in between redelivers and the work happens twice. **There is no ordering of those two operations that is both safe and once.**

### 4.2 Delivery semantics — what the knobs actually do

| Setting | Effect | What can go wrong |
|---|---|---|
| **Ack after processing** (correct) | At-least-once | Duplicates on crash; requires idempotency |
| **Ack before processing** | At-most-once | Message is gone; a crash loses the work permanently |
| **Auto-ack on delivery** (RabbitMQ `autoAck=true`) | At-most-once, silently | Loses every message that was in flight during a crash. Never use it for work you care about |
| **Visibility timeout / unacked window** | Controls how long before redelivery | Too short → two consumers processing the same message concurrently; too long → a crashed consumer's message is stalled for the whole timeout |
| **Max receive count → DLQ** | Bounds retries per message | If the DLQ alarm is unmonitored, poison messages vanish into it (L75) |

The visibility timeout is the single most consequential number in a queue's configuration, and it must be derived from a **measurement**, not a default:

```
  correct visibility timeout  ≥  p99.9(processing time) × safety margin (1.5–3×)

  BookNook invoice rendering:  p50 240 ms, p99 1.8 s, p99.9 6.4 s
  ⇒ timeout = 30 s (default) is generous — 4.7× p99.9. Good.
  ⇒ a batch renderer that occasionally takes 45 s under a 30 s timeout is a
     DUPLICATE GENERATOR: every one of those jobs runs twice, concurrently.
```

### 4.3 Ordering: the property you get only if you ask

Queues are, by default, **unordered**, and that is not a bug — it is what makes competing consumers possible. Ordering requires a constraint on who may consume what, and every mechanism for ordering is a mechanism for reducing parallelism:

| Mechanism | Guarantee | Cost | Example |
|---|---|---|---|
| **Standard queue** | Best effort; no ordering at all | None | SQS standard, RabbitMQ classic queues with multiple consumers |
| **FIFO queue** | First-in-first-out per **message group** | Throughput ceiling; a stuck group blocks that group only | SQS FIFO message groups |
| **Single consumer** | Total order | No parallelism at all | A queue with `max_consumers = 1` |
| **Partitioned ordering** | Order per partition key | Partition count caps parallelism | Kafka partitions (L72), SQS FIFO groups |
| **Per-entity serialisation** | Order per entity (order ID, account) | Hot entities serialise | Hash the key onto a partition, or use a lock (L78) |

The design rule: **order only what must be ordered, and scope it as narrowly as possible.** BookNook's inventory events must be ordered *per ISBN*; they need not be ordered globally. Keying on the ISBN gives correctness and, with 400,000 ISBNs, effectively unbounded parallelism. Keying globally would give correctness and a throughput ceiling of one consumer.

### 4.4 The broker is a system too

It is easy to treat the queue as neutral infrastructure. It is a replicated stateful service with its own availability, its own limits, and its own failure modes:

| Concern | Reality |
|---|---|
| **Durability** | SQS replicates across AZs within a region. RabbitMQ is durable only with `durable=true` queues **and** persistent messages **and** a sync/replicated queue type (quorum queues, which are Raft-based — L81). A classic queue on one node is a memory buffer that looks like a queue. |
| **Throughput ceiling** | SQS standard scales nearly without limit; SQS FIFO has a per-message-group and per-queue ceiling (hundreds to a few thousand msg/s depending on batching mode); RabbitMQ scales per node and needs quorum queues + partitions to go further. |
| **Retention** | SQS: 4 days by default, **14 days maximum**. RabbitMQ: until consumed, or `TTL`/`max-length`. A queue is **not a database**; if your consumer is down for 15 days, SQS deletes the work and tells nobody. |
| **Size limits** | 256 KB (SQS), 128 KB default (RabbitMQ), 1 MB (Kafka). Beyond that: the **claim-check pattern** — put the payload in S3 and send the pointer. |
| **Cost model** | SQS charges per **request**, and one message lifecycle is typically **3 requests** (send, receive, delete). RabbitMQ is priced in infrastructure, so cost is flat-ish with volume. |

### 4.5 The queue is a latency/throughput converter, and a signal source

The useful mental model is hydraulic. Arrival rate `λ`, drain rate `μ`:

```
  λ < μ  → the queue is empty; added latency = poll interval + processing (0–20 ms)
  λ = μ  → the queue is metastable; any jitter accumulates forever
  λ > μ  → backlog grows linearly: backlog(t) = (λ − μ) × t
```

Three operational consequences follow, and they are the ones that appear in every queue incident:

1. **Backlog is unbounded unless you bound it.** At `λ − μ = 860/s` (BookNook's flash sale against a single renderer), the backlog grows by 51,600 messages per minute. It is not a "queue problem"; it is arithmetic. You autoscale consumers (raise `μ`), shed load upstream (lower `λ`, L76), or accept a known drain time and tell the user.
2. **Risk of backlog = time, not count.** 10,000 messages is alarming with a drain rate of 10/s (17 minutes) and trivial at 1,000/s (10 seconds). **Alert on `oldest_message_age`**, and set the threshold from the business tolerance ("receipts may be 5 minutes late"), not from a round number.
3. **Depth is not a leading indicator.** During a burst, depth spikes and recovers by design. Age rises only when the drain is genuinely failing, which is why it is the alarm and depth is the dashboard.

### 4.6 What queues cannot do

Or, the five things engineers expect a queue to fix and it does not:

| Expectation | Reality |
|---|---|
| "It makes delivery exactly-once" | It makes delivery **at-least-once**. Exactly-once *effects* require idempotent consumers or transactional dedup (L73–74). |
| "It removes the ambiguous-outcome problem" | It moves it. The producer's `DB write + enqueue` is now ambiguous (boundary 1) — that is the outbox pattern's whole reason for existing (L85). |
| "It decouples services" | It decouples their *timing and availability*, and it **couples their contracts**: the message schema is now a public API that every consumer depends on, and changing it is a coordinated migration. |
| "It gives unlimited buffering" | Retention is finite (14 days on SQS) and unbounded growth means unbounded recovery time. A queue defers load; it does not delete it. |
| "It removes the need for monitoring" | It removes the *symptoms*. A dead consumer with a healthy queue produces total silence — no errors, no timeouts, no user complaints until the age alarm eventually fires (if you wrote one). |

## 5. Request / data flow

One order, from placement to receipt, with the queue in the middle and a crash on the way.

```
  T+0ms     POST /checkout
  T+4ms     BEGIN; INSERT orders...; INSERT order_items...; COMMIT
  T+6ms     send Message{type: order.placed, order_id: 88214, ...}
              → broker replicates to 2 other AZs, returns
  T+9ms     respond 201 {"order_id": 88214, "status": "confirmed"}
              ── user is done. Everything below is not their problem. ──

  T+11ms    worker A long-polls, receives the message
              → the message is now INVISIBLE for 30 s (visibility timeout)
  T+120ms   worker A loads the order, renders HTML, calls the email provider
  T+1,900ms ✗ worker A is OOM-killed / the node is drained for a deploy
              (no ack was sent)

  T+30,000ms visibility timeout expires → message becomes visible again
  T+30,010ms worker B receives it
  T+31,800ms worker B renders and sends the email   ⚠ THE SECOND EMAIL
  T+31,810ms worker B acks → message deleted

  ── duplicate prevented only because the consumer is idempotent: ──
     INSERT INTO sent_emails(order_id, template) VALUES (88214,'receipt')
       ON CONFLICT DO NOTHING;     -- unique (order_id, template)
     if inserted 0 rows → ack and return without sending.

  T+31,900ms metrics: processed_total += 1, redelivery_total += 1
  T+32,000ms queue age returns to ~0
```

Read four things off this timeline:

- The **user's latency is 9 ms**, and nothing after it affects them. That is the entire point.
- **Recovery was automatic.** No operator, no replay script, no lost work. The message was never deleted because the ack never came — the queue's entire contract in one sentence.
- The **duplicate was real** and was neutralised by a unique constraint, not by the broker.
- The 30-second visibility timeout meant the second attempt started **30 seconds later**. For a receipt, fine. For a payment capture, you would want the timeout near `p99.9` (6.4 s → use 10 s) so the retry happens fast; a too-long timeout is a latent latency amplifier *and* a stall for any crashed consumer's messages.

## 6. ASCII diagram

The two failure boundaries, side by side, since they are different bugs with different fixes:

```
 BOUNDARY 1 — PRODUCER (lost work, silent)          BOUNDARY 2 — CONSUMER (duplicate, noisy)
 ─────────────────────────────────────────           ─────────────────────────────────────
   BEGIN                                     receive message
   INSERT orders ...  ✓                      do work: send email  ✓
   COMMIT             ✓                      <crash here>
   ✗ <crash here>                            visibility timeout expires
   send(msg)  ← never happened               message redelivered
                                             do work: send email ✓✓ DUPLICATE
   RESULT: order exists, receipt never        ack
     happens. No error. No log. Detected
     only by the customer.
   FIX: transactional outbox (L85)           FIX: idempotent consumer (L74)
        + a relay that reads the outbox            + unique constraint on
        and sends. One transaction,                (order_id, template)
        one source of truth.
```

The backlog arithmetic, drawn — this is the chart to sketch in an interview:

```
 messages
     ▲
     │                    λ = 900/s  (flash sale begins)
  81k┤              ╱─────────────────────λ back to 10/s
     │           ╱                        │
     │        ╱                           │  drain at μ = 40/s
     │     ╱                              ▼╲
     │  ╱                                    ╲
   0 ┴──────────────────────────────────────────────► time
      0s      90s          1,350s (22.5 min)      1,380s

   backlog peak = (λ − μ) × duration = (900 − 40) × 90 s = 77,400 messages
   drain time   = 77,400 ÷ 40 = 1,935 s ... but arrivals continue,
                so: total ≈ 77,400/40 + steady = ~34 min with one worker
   with 23 workers (μ = 920/s): drain ≈ 90 s

   TWO honest responses:
     1. raise μ (autoscale consumers on queue age, not CPU)
     2. lower λ (shed or defer load, L76) — and tell the user
   The dishonest response is to raise retention and call it capacity.
```

Competing consumers and ordering, the trade in one picture:

```
  ORDERED, NARROWLY (per ISBN) — 400,000 keys, near-infinite parallelism
  ┌────────────────────────────┐
  │ inventory-events           │   group=isbn:978-…      → worker 1
  │  (FIFO, message groups)    │   group=isbn:978-…-2    → worker 4
  └────────────────────────────┘   group=isbn:978-…-3    → worker 9
      ✓ order per ISBN          ✓ parallelism across ISBNs
      ✗ a hot ISBN serialises (acceptable: one ISBN rarely hot)

  ORDERED, GLOBALLY — correctness bought with a throughput ceiling
  ┌────────────────────────────┐
  │ inventory-events (single   │ ─────► worker 1  (only consumer allowed)
  │  consumer, total order)    │        ✗ 40 msg/s ceiling, forever
  └────────────────────────────┘
```

## 7. Detailed example

### 7.1 The flash sale: 81,000 orders, one email worker

BookNook's spring sale: baseline **10 orders/sec**, spiking to **900/sec** for 90 seconds. The receipt renderer processes **40 messages/sec** per worker (a template render plus one HTTPS call to the email provider at 25 ms each).

Synchronous design (before): the checkout call awaits the render. At 900/sec against 40/sec capacity, the renderer's queue is a thread pool; 860 requests/sec time out after 2 s, the checkout **fails**, and 77,400 orders are lost — from a system in which nothing was broken.

Queued design (after):

```
  burst      = 900 × 90 s                = 81,000 messages
  steady     = 10 /s afterwards
  one worker drains at 40/s              = 2,025 s ≈ 34 minutes
  autoscale to 23 workers (μ=920/s)      = ~90 s
```

The autoscaling metric was chosen deliberately: **queue `oldest_message_age` > 60 s**, not CPU. Consumer CPU sits at 15 % while a single worker is behind — because the work is I/O-bound on the email provider's latency, so CPU is a *lagging and misleading* signal. Scale-out on age, scale-in on `age ≈ 0` sustained for 5 minutes.

Capacity check against the provider: 23 workers × 40/s = 920 msg/s against a provider limit of 1,000/s. That is a **93 % utilisation of a dependency you do not own** — so the design also needed a token bucket in front of the provider (L76) and a `429`-aware backoff. Without it, the autoscaler would have DoS'd the email provider and the retries would have produced a metastable failure (L65 §9).

### 7.2 The poison message that ate a worker pool

A malformed order (a null `shipping_address` from a partner feed) reaches the renderer. The renderer throws. The message is not acked, becomes visible, and is redelivered **40 times a minute**, forever, to whichever worker picks it up — each time taking the full 30 s visibility timeout plus a crash. Retail: **one bad message consumed 4 % of the consumer fleet indefinitely**.

The queue did exactly what it promises; that is the problem, and the fix is structural:

| Control | Value | Why |
|---|---|---|
| `maxReceiveCount` | **5** | Enough to ride out transient provider errors; small enough to fail fast |
| DLQ | Separate queue, **retained 14 days** | The message must survive until a human looks |
| DLQ depth alarm | `> 0` for 5 min → page | A non-empty DLQ is always an unfinished incident |
| Consumer input validation | Reject before processing | Most poisons are detectable in the first millisecond |
| Redelivery-rate alarm | `redeliveries / receives > 1 %` | The *leading* signal; it fired 3 hours before the DLQ had 5 copies |

The general rule: **a queue with no DLQ turns a code bug into an infinite loop, and an infinite loop into a capacity outage.** Death by a thousand redeliveries is a real pattern.

### 7.3 The dual-write bug, found a month later

A new "gift card" flow did:

```python
db.commit()                     # 1. order row written
queue.send(order_placed_msg)    # 2. message sent
```

Two operations, no transaction. Over a month, **0.03 %** of sends failed (a broker throttle under burst, a rolled-over SDK retry budget, a deploy that restarted the producer). At 10 orders/sec, that is **~260 gift cards issued with no email and no loyalty credit**, discovered when a customer complained that a gift card they had bought had never arrived.

The fix is the outbox pattern (L85), and it is worth naming here because it is *the* consequence of queues: **write the message into your database in the same transaction as the state change, and let a separate relay move rows from the outbox table to the broker.** The producer then has exactly one durable write, and "did I enqueue?" stops being a question.

## 8. Code or configuration example

```python
# ============================================================================
# BookNook: SQS consumer, done correctly. Illustrative, Python-like.
# Every comment marks a decision that is not the default.
# ============================================================================

client = boto3.client("sqs")
QUEUE = "https://sqs.eu-west-1.amazonaws.com/…/booknook-receipts"
DLQ_CONFIGURED = True     # see RedrivePolicy below; never leave this False

def consume_forever():
    while True:
        resp = client.receive_message(
            QueueUrl=QUEUE,
            MaxNumberOfMessages=10,        # batch: 10x fewer API calls (cost + RTT)
            WaitTimeSeconds=20,            # LONG POLL: empty queue costs 0 requests
                                           #   instead of a hot spin of 20/s/worker
            VisibilityTimeout=30,          # must be > p99.9 processing (6.4s) with margin
            AttributeNames=["ApproximateReceiveCount"],
        )
        for m in resp.get("Messages", []):
            handle(m)


def handle(msg):
    body = json.loads(msg["Body"])
    attempts = int(msg["Attributes"]["ApproximateReceiveCount"])

    # 1. IDEMPOTENCY FIRST — before any side effect. The whole at-least-once
    #    contract is only survivable because of this line.
    inserted = db.execute("""
        INSERT INTO processed_messages (message_id, consumer, processed_at)
        VALUES (%s, 'receipt-renderer', now())
        ON CONFLICT (message_id) DO NOTHING
    """, msg["MessageId"])
    if inserted == 0:
        ack(msg)                 # already done. Ack and move on — do NOT redo it.
        metrics.incr("receipt.duplicate_skipped")
        return

    try:
        render_and_send_receipt(body)     # the actual work
        ack(msg)
    except PermanentError as e:           # malformed payload, unknown template
        # 2. Do NOT retry a poison message; send it where a human will see it.
        #    With maxReceiveCount=5 the broker will do this anyway; doing it
        #    explicitly just gets it out of the way 4 redeliveries sooner.
        raise_to_dlq(msg, reason=repr(e))
        ack(msg)
    except TransientError:                # provider 503, timeout
        if attempts >= 5:
            raise_to_dlq(msg, reason="max attempts")
            ack(msg)
        else:
            # 3. Do NOT ack. Let the visibility timeout redeliver.
            #    Optionally extend/change visibility for a backoff:
            client.change_message_visibility(
                QueueUrl=QUEUE, ReceiptHandle=msg["ReceiptHandle"],
                VisibilityTimeout=min(900, 5 * 2 ** attempts)  # 10,20,40,80,160s
            )
            metrics.incr("receipt.retry", tags={"attempt": attempts})


def ack(msg):
    client.delete_message(QueueUrl=QUEUE, ReceiptHandle=msg["ReceiptHandle"])
```

```hcl
# --- The queue configuration is the resilience policy. ----------------------
# Terraform, illustrative. Each number has a reason.
resource "aws_sqs_queue" "receipts" {
  name                       = "booknook-receipts"
  visibility_timeout_seconds = 30     # > p99.9 (6.4s) x 3
  message_retention_seconds  = 1209600  # 14 days = SQS MAX. Not infinite.
  receive_wait_time_seconds  = 20     # long polling on by default
  redrive_policy             = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.receipts_dlq.arn
    maxReceiveCount     = 5           # bounds the poison-message loop
  })
}

resource "aws_sqs_queue" "receipts_dlq" {
  name                      = "booknook-receipts-dlq"
  message_retention_seconds = 1209600 # 14 days: a human may be on holiday
}

# FIFO, only where ordering is genuinely required (per-ISBN inventory events)
resource "aws_sqs_queue" "inventory_fifo" {
  name                        = "booknook-inventory.fifo"
  fifo_queue                  = true
  content_based_deduplication = true   # dedupe identical bodies within 5 min
  deduplication_scope         = "messageGroup"
  fifo_throughput_limit       = "perMessageGroupId"   # parallelism per ISBN
}

# --- The alarms. A queue without these fails silently. --------------------
# 1. AGE, not depth. Depth spikes during bursts by design.
alarm = metric("ApproximateAgeOfOldestMessage", queue="receipts") > 300  # 5 min
# 2. A non-empty DLQ is an unfinished incident.
alarm = metric("ApproximateNumberOfMessagesVisible", queue="receipts-dlq") > 0
# 3. Redelivery rate is the LEADING indicator (fires before the DLQ fills).
alarm = rate("receipt.redelivery") / rate("receipt.processed") > 0.01
# 4. Throughput collapse with a flat queue = the consumer is dead, not slow.
alarm = rate("receipt.processed") == 0 and queue_age_rising
```

```yaml
# --- The claim-check pattern for payloads over the 256KB limit. -------------
# Never raise the limit by splitting the object. Put the body in S3.
message:
  id: 88214
  type: order.placed
  schema_version: 3
  occurred_at: "2026-10-30T12:04:11.221Z"
  correlation_id: "trace-9f2c…"        # for tracing across the async hop (L88)
  payload_pointer: "s3://booknook-events/order/88214/v3.json"
  payload_bytes: 1842193               # 1.8 MB invoice PDF, not in the message
# The consumer fetches the pointer. Access is granted by IAM, and the object
# carries a lifecycle rule so it expires with the message retention.
```

## 9. Numerical example

BookNook: baseline **10 orders/sec**, flash-sale peak **900/sec for 90 s**, receipt worker capacity **40 msg/sec**, `p99.9` render time **6.4 s**.

**Step 1 — burst volume and drain time.**

```
  burst messages  = 900 × 90 s                     = 81,000
  one worker, μ = 40/s                             → 2,025 s = 33.8 min
  workers for a 90 s drain: 81,000 ÷ 90 = 900/s ÷ 40 = 22.5 → 23 workers
  backlog peak    = (900 − 40) × 90                = 77,400
  if you stay at 1 worker AND keep taking orders    → backlog grows 860/s
```

**Step 2 — the visibility timeout, derived.**

```
  p99.9 processing   = 6.4 s
  safety margin 3×   → 19.2 s
  chosen             = 30 s   (SQS default; comfortably above 19.2)

  Contrast the failure if the renderer occasionally took 45 s:
     45 s > 30 s  → the message becomes visible while still being processed
                 → a second worker starts the SAME message
                 → duplicates at a rate equal to the fraction of slow jobs
     At 900/s with 2 % of jobs exceeding 30 s: 18 duplicate emails/sec.
```

**Step 3 — prefetch/in-flight sizing, from Little's Law.** Each worker's concurrency is bounded by prefetch, and the required in-flight work is `λ × W`:

```
  needed in-flight = arrivals × processing time = 900/s × 0.160 s = 144
  with 23 workers: 144 ÷ 23 = 6.3 → prefetch of 10 per worker is correct.
  Too low (e.g. 1)  → workers idle between messages; throughput caps at
                      1/0.160 = 6.25 msg/s/worker instead of 40 → 6x underuse.
  Too high (e.g. 100) → memory, provider rate-limit bursts, an unbounded
                      redelivery blast if the fleet dies.
```

**Step 4 — what the queue costs, in requests.** SQS bills per request, and one message lifecycle is typically **3** (send, receive, delete) — plus re-receives on retry.

```
  steady state:  10 msg/s × 3 requests × 86,400 s      = 2.59 M requests/day
  flash sale:    81,000 msgs × 3                        = 0.24 M requests
  monthly cost at $0.40 / million requests               ≈ $0.31/day ≈ $9.5/month

  At the flash-sale rate SUSTAINED (900/s) instead:
                 900 × 3 × 2.63 M s                     = 7.1 B requests/month
                                                        ≈ $2,840/month
  → This is the number that decides queue-vs-log (L71): at high sustained
    throughput, request-priced queues become the most expensive line item,
    and a log-based broker with flat infra pricing wins.
```

**Step 5 — retention is not a buffer.** If the renderer is down and arrivals continue:

```
  retention = 14 days (SQS max)
  at 10/s: 14 days holds 12.1 M messages                       → safe
  at 900/s: 14 days holds 1.09 B messages, and at 2 KB each
            = 2.2 TB, plus a 14-day drain at μ=900/s afterwards.
  The queue does not stop the outage; it delays and lengthens it.
  The bound that matters is not retention, it is
        acceptable_backlog_age × arrival_rate
  which is why the alarm is on AGE (5 min → 300s × 900/s = 270,000 messages).
```

**Step 6 — the cost of a missing idempotency check.** 30-second visibility timeout, 2 % of jobs exceeding it, 0.5 % crash rate, 10 msg/s steady:

```
  duplicate effect rate ≈ 10 × (0.02 + 0.005) = 0.25/s = 21,600/day
  at even $0.02 of customer-support cost each → $432/day, $158k/year
  for want of one INSERT ... ON CONFLICT DO NOTHING.
```

## 10. Trade-offs

| Decision | Buys you | Costs you | Choose it when |
|---|---|---|---|
| **Queue for a workflow step** | Decoupling of time and availability; spike absorption; automatic retry | Eventual consistency; a new failure domain; idempotency obligation | The caller does not need the result |
| **Synchronous call** | Simplicity; immediate errors; no state machine | Availability multiplication (`0.999ⁿ`); no buffering | The caller needs the answer (payment authorisation, stock check) |
| **At-least-once + idempotent consumer** | Safety without a distributed transaction | One dedup table per consumer; a uniqueness decision | **Always** — this is the correct default |
| **At-most-once (auto-ack)** | No duplicates, simplest possible | Silent data loss on crash | Telemetry where loss is genuinely acceptable |
| **Standard queue** | Unlimited throughput, simplest ops | No ordering at all | Independent units of work |
| **FIFO queue / message groups** | Order per key | Throughput ceiling per group; a hot key serialises | Ordering genuinely required (inventory, account balance) |
| **Short visibility timeout** | Fast retry after a crash | Duplicates when processing exceeds it | Processing time is tight and bounded |
| **Long visibility timeout** | Fewer duplicates | A crashed consumer's message stalls for the timeout | Long-running batch jobs |
| **Long retention (14 days)** | Survives long consumer outages; enables replay | Storage cost; longer recovery drain; masks broken consumers | Consumers may genuinely be down for days |
| **DLQ with maxReceiveCount** | Poison messages stop looping; failures become visible | An alarm you must action, and a replay runbook you must write | Every queue, without exception |
| **Claim-check (pointer to S3)** | Unlimited payload size | An extra fetch and an extra permission boundary | Payloads over the broker limit |

## 11. Failure scenarios

**11.1 — The silent consumer.** The consumer deployment scaled to zero (a bad HPA `minReplicas`), the queue filled, and **nothing alerted** because there were no errors and no user-visible failures until receipts were 6 hours late. **Symptom:** flat `processed` counter, rising `age`, everything else green. **Fix:** an alarm on processed-throughput going to zero *while the queue is non-empty* — the one alarm that catches "the consumer is dead", not "the consumer is slow".

**11.2 — The duplicate receipt storm.** Visibility timeout 5 s against a `p99.9` of 12 s. Under load, every slow job ran twice. **Symptom:** customer complaints about duplicate emails, and `redelivery_rate` elevated but not alarming. **Fix:** timeout derived from `p99.9` × 3, plus idempotency so the consequence is invisible even when the cause recurs.

**11.3 — Poison message loop** (§7.2). One malformed payload, 40 redeliveries/minute, 4 % of the fleet consumed. **Symptom:** DLQ empty (it did not exist), throughput sagging, no errors. **Fix:** `maxReceiveCount` + DLQ + a redelivery-rate alarm.

**11.4 — Queue treated as a database.** A team routed a 40 GB nightly export through a queue with 4-day retention; the consumer was down over a long weekend. **Symptom:** 40 % of the export silently vanished; the job "succeeded" from the producer's point of view. **Fix:** understand retention as a hard bound; large/deferrable payloads belong in object storage, and exports belong in a batch pipeline, not a queue.

**11.5 — Ordering assumed but not configured.** Inventory decrements for a hot ISBN were processed out of order, producing a negative stock row that a `CHECK` constraint rejected at 03:00. **Symptom:** sporadic constraint violations that do not reproduce under light load. **Fix:** FIFO with `message_group_id = isbn`, or make the operation commutative (set absolute stock rather than increment).

**11.6 — Consumer DoS on a dependency.** Autoscaling on queue age scaled 40 workers, each calling a rate-limited provider at 40/s; the provider throttled; retries multiplied; the queue backed up further. **Symptom:** the backlog *grew* while the consumer fleet was at maximum. **Fix:** token-bucket the outbound dependency (L76), cap `maxReplicas` at `dependent_limit ÷ per-worker_rate`, and scale on age with an upper bound.

**11.7 — The delayed-drain hangover.** A 20-minute consumer outage created a large backlog; on recovery, the draining burst hammered the database, whose connections saturated, causing the *other* services on that database to fail. **Symptom:** a second, unrelated outage immediately after the first recovery. **Fix:** cap consumer concurrency on restart (rate-limit the drain), and treat recovery as its own load event with its own capacity plan.

**11.8 — The dual-write gap** (§7.3). 0.03 % of messages never sent because `commit()` and `send()` cannot be atomic. **Symptom:** rare, silent, unreproducible missing work. **Fix:** the outbox pattern (L85) — and until it is in place, a periodic reconciliation between the database and the consumer's effects.

## 12. Alternatives

| Instead of a queue | Consider | When |
|---|---|---|
| Queue + worker | **Synchronous call** | The caller needs the result, or the work is <50 ms |
| Queue | **Publish-subscribe / topic** (L69) | Multiple, independently-added consumers of the same event |
| Queue | **Event log / stream** (L70) | Replay, ordered partitioning, many consumers, retention as a feature |
| Queue | **Database table as a queue** | Very low volume, or you need the enqueue inside the caller's transaction. Beware: polling, locking (`SKIP LOCKED`), and vacuum pressure |
| Queue | **Transactional outbox relay** (L85) | Correctness of enqueue relative to a database write |
| Queue | **Scheduled batch** | Latency tolerance is hours and volume is large |
| Queue | **Direct RPC with retries** | The failure is transient and the caller can retry safely |

## 13. When to use it

- **Any step in a workflow the caller does not need to wait for.** Receipts, invoices, loyalty points, analytics, thumbnails, webhooks.
- **Any workload whose arrival is spikier than its capacity**, and where deliberate delay is acceptable.
- **Any integration with a third party that is slow, rate-limited, or flaky** — the queue becomes the buffer and the retry policy.
- **Work that must survive a crash of the caller.** The message *is* the durable intent.
- **Work you want to scale independently** of the request path: one worker or a thousand, without touching the API.
- **Long-running tasks** (video transcode, PDF generation) that would otherwise hold an HTTP connection.

## 14. When *not* to use it

- **When the caller needs the result.** A queue in front of a stock check does not make it asynchronous; it makes it wrong.
- **When you cannot make the consumer idempotent.** Without dedup, at-least-once means recurring duplicates (§9 step 6).
- **For low-latency request/response.** The added poll interval and hop cost 5–40 ms; if the budget is 20 ms, this is the wrong tool.
- **As your source of truth.** A queue is transport with finite retention; the database is the record.
- **When the team cannot operate it.** Queues need age alarms, DLQ alarms, replay runbooks and idempotency discipline; an unmonitored queue fails more quietly than a synchronous call.
- **For strict global ordering at high volume.** Use a partitioned log (L70–72), not a single ordered queue.
- **To paper over a synchronous call that should simply be faster.** If the dependency is 900 ms because of a missing index, fix the index.

## 15. Common mistakes

1. **No idempotent consumer.** The single most common and most expensive queue bug (§9 step 6).
2. **Acking before the work.** Guarantees silent loss on crash.
3. **No DLQ, or an unmonitored DLQ.** Poison messages loop forever, or vanish without an action.
4. **Alerting on depth instead of age.** Depth spikes by design; age reveals genuine failure.
5. **Visibility timeout shorter than `p99.9` processing.** Manufactures duplicates while looking correct.
6. **Assuming ordering without configuring it.** Standard queues are unordered, and the resulting bug looks like a data-integrity problem, not a messaging one.
7. **Treating retention as infinite buffering.** 14 days on SQS, and a 14-day drain afterwards.
8. **Ignoring the dual-write problem** (`commit()` then `send()`) — 0.03 % of the time the work is silently lost (L85).
9. **Scaling consumers without capping a rate-limited dependency.** Turns a backlog into a dependency outage (§11.6).
10. **Payloads too large / too fat.** 256 KB limit, and a fat message is a schema nobody can version; use a pointer.
11. **Unbounded drain after recovery.** The recovery burst is its own capacity event (§11.7).
12. **No `correlation_id` in messages.** Async hops otherwise destroy traceability (L88).
13. **Treating the queue as a decoupling device for *contracts*.** It decouples timing; the message schema is still an API you must version.
14. **Polling with `WaitTimeSeconds = 0`.** Burns requests (money) and CPU for nothing; always long-poll.

## 16. Interview perspective

**What is actually being tested.** Whether you understand that a queue changes *which* failure you have, not whether you have one. Strong candidates name the two failure boundaries unprompted (lost enqueue vs duplicate processing), state at-least-once as the real guarantee, and immediately add "so the consumer must be idempotent". Then they attach numbers: burst size, drain time, visibility timeout derivation, and the metric they would alarm on.

**Answers that land well:**

- *"How do you handle the receipt email so checkout stays fast?"* — "Queue it. Checkout commits the order and enqueues in the same transaction via an outbox, so there is no dual-write gap. The consumer is idempotent on `(order_id, template)`, the visibility timeout is 30 s against a 6.4 s p99.9, and I'd alarm on `oldest_message_age > 5 min` and on a redelivery rate above 1 % rather than on depth. During a flash sale the burst is 81,000 messages against 40/s per worker, so the consumer autoscales on age to 23 workers, capped at the email provider's rate limit."
- *"What does at-least-once mean for your code?"* — "That every consumer needs a dedup key stored with the effect, in the same transaction as the effect. And that 'exactly-once' is only achievable inside a single transactional boundary, which is why the guarantee I actually design for is at-least-once plus idempotency."
- *"A consumer is down for two hours. What happens?"* — "Nothing user-visible on the order path; the messages accumulate. What matters is whether the backlog drains within the business tolerance and whether retention covers the outage — at 10/s and 14 days retention, comfortably. I'd also rate-limit the drain so recovery does not become its own outage."
- *"Why not just retry the call synchronously?"* — "Because retries in the request path multiply availability problems and hold the user's connection. A queue turns that into a recorded obligation with its own retry policy, at the cost of eventual consistency."

**Common interview tasks:** decide sync vs async for each step of a workflow; design a queue-based pipeline with DLQ and alarms; pick and justify a visibility timeout; choose between standard and FIFO; calculate backlog and drain time; explain precisely how a duplicate arises and how it is neutralised; explain the dual-write problem and the outbox fix.

**Red flags interviewers watch for:**

- "The queue guarantees exactly-once delivery."
- No mention of idempotency anywhere.
- No DLQ, or DLQ mentioned but not alarmed on.
- Alerting on queue depth alone.
- A visibility timeout picked without reference to processing time.
- Assuming ordering by default.
- Being unable to say what happens to a message when a consumer crashes mid-work.
- Treating the queue as durable storage with unlimited retention.
- No numbers: no burst size, no drain calculation, no rate.
- Using a queue for a call whose result the caller needs.

## 17. Knowledge check

**Conceptual**

1. Describe the two failure boundaries of a queue-based system (producer-side and consumer-side), explain for each what goes wrong and why no ordering of operations removes it, and name the specific pattern that fixes each.
2. Explain why "exactly-once delivery" is not achievable across a network hop, what the achievable guarantee is, and what the consumer must provide for that guarantee to be safe. Then state where a *transactional* exactly-once effect is genuinely possible.
3. A colleague sets `receive_wait_time_seconds = 0`, `visibility_timeout = 5`, no DLQ, and alarms on `ApproximateNumberOfMessagesVisible > 1000`. Identify four distinct problems and the consequence of each, with the corrected configuration.

**Practical scenario**

4. BookNook's invoice renderer processes 25 messages/sec per worker with `p50 = 300 ms`, `p99 = 2 s`, `p99.9 = 9 s`, and the PDF archive S3 bucket must hold every invoice for 7 years. Normal load is 10 orders/sec, with a marketing burst reaching 1,200/sec for 3 minutes. Design the queue layer end to end: standard vs FIFO and why, visibility timeout with the derivation, retention, DLQ policy with `maxReceiveCount`, the idempotency key, the autoscaling metric and its target with the worker count for the burst, the three alarms with thresholds, what the consumer does when the S3 bucket is unavailable for 10 minutes, and the arithmetic for how long the burst takes to drain at your chosen scale.

**Calculation**

5. A queue receives 500 msg/sec for 4 minutes, then 20 msg/sec. Each consumer processes 60 msg/sec with a 120 ms service time. (a) Compute the burst backlog. (b) Compute the drain time with 8 consumers and with 12. (c) Compute the required prefetch per consumer to keep 8 consumers saturated. (d) Compute the message-based request cost per month at steady state if the broker charges per request and a message lifecycle is 3 requests at $0.40/million. (e) State the retention you would need if consumers may be down for 48 hours during that burst rate.

6. A consumer has a 45-second p99.9 processing time and a 30-second visibility timeout. At 200 msg/sec, with 3 % of messages exceeding the timeout, compute (a) how many duplicate processings occur per minute, (b) the resulting extra load as a percentage, and (c) the visibility timeout you would set, and (d) the cost of that choice in terms of how long a crashed consumer's message waits before redelivery.

**Progress checklist**

- Phase 1 — Foundations (L1–17): **complete** ✅
- Phase 2 — Scaling (L18–39): **complete** ✅
- Phase 3 — Data and Storage (L40–63): **complete** ✅
- Phase 4 — Distributed Systems (L64–92):
  - [x] 1. Why distributed systems are difficult (L64)
  - [x] 2. Partial failures and network partitions (L65)
  - [x] 3. Clock differences and ordering events (L66)
  - [x] 4. Synchronous versus asynchronous workflows (L67)
  - [x] **5. Message queues (L68) ← you are here**
  - [ ] 6. Publish-subscribe systems (L69)
  - [ ] 7. Event streams (L70)
  - [ ] 8. RabbitMQ-style queues versus Kafka-style logs (L71)
  - [ ] 9. Consumer groups, partitions, offsets, ordering (L72)
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

**NEXT LESSON: Phase 4, Topic 6 — Publish-subscribe systems**

---

# Lesson 69 — Publish-Subscribe Systems

*Phase 4, Topic 6*

## 1. Simple definition

**Publish-subscribe** is a messaging model in which a producer (the *publisher*) sends a message to a named **topic**, and the broker delivers a **copy to every interested subscriber**. The publisher does not know who is listening, how many listeners exist, or what they do. Subscribers express interest once and receive everything published afterwards.

The distinction from Lesson 68's queue is exactly one property, and it changes the architecture:

| | **Queue** (L68) | **Pub-sub** (this lesson) |
|---|---|---|
| Copies per message | **One** | **One per subscriber** |
| Consumers | Compete — each message goes to one | Independent — every subscriber gets every message |
| Adding a consumer | Steals work from existing consumers | Joins alongside; existing consumers unchanged |
| Purpose | **Distribute work** | **Broadcast facts** |
| Failure isolation | A slow consumer affects only its own share | A slow consumer affects **only itself** |

A **topic** (AWS SNS, Google Pub/Sub, EventBridge, Redis Pub/Sub) is the broadcast primitive. A **subscription** is a durable binding between a topic and one endpoint — an SQS queue, an HTTPS webhook, a Lambda, a mobile push endpoint.

The critical engineering question in pub-sub is **what the subscriber is**, because a subscriber is either **durable** (it has its own storage, so an outage delays but does not lose messages) or **ephemeral** (a direct HTTP push, so an unreachable endpoint means the message is retried briefly and then dropped). Almost every pub-sub outage is somebody treating an ephemeral subscription as a durable one.

Terms defined before use:

- **Topic**: a named broadcast channel.
- **Subscription**: a per-consumer binding to a topic. **Attributes are per subscription, not per topic** — filter policy, delivery format, retry policy, DLQ.
- **Fan-out**: the act of copying one published message to N subscriptions. Cost and load scale with N.
- **Filter policy**: a per-subscription rule evaluated by the broker, so a subscriber receives only matching messages. This is what stops fan-out from meaning "everyone gets everything".
- **Durable subscription**: one backed by its own storage (an SQS queue, a Pub/Sub subscription, a Kafka consumer group), so messages survive subscriber downtime.
- **Webhook**: an HTTPS endpoint acting as a subscriber. Simple to build, and the least reliable subscription type in existence.
- **Event-carried state transfer vs notification**: whether the message contains the data the subscriber needs (fat event), or only a pointer and an ID (thin event). This is a schema-and-coupling decision, not a size decision.

The one sentence to carry out of this lesson: **pub-sub moves the coupling from *timing* to *contracts* — the publisher stops knowing who listens, and in exchange every subscriber's expectations become a public API you must version forever.**

## 2. Why it exists

BookNook's "order placed" moment has, over three years, acquired seven interested parties: the receipt emailer, the loyalty-points ledger, the analytics pipeline, the warehouse loader, the fraud scorer, the recommendation engine, and (since last month) a partner API that pushes order events to a fulfilment vendor. Each one was added by a different team, at a different time, without touching checkout.

Without pub-sub, each addition is a code change in the order service:

```
  NAIVE — the order service knows everyone
  ┌──────────────┐  ──HTTP──► email-svc        (added Q1)
  │              │  ──HTTP──► loyalty-svc      (added Q2)
  │ order-svc    │  ──HTTP──► analytics-svc    (added Q2)
  │              │  ──HTTP──► warehouse-loader (added Q3)
  │  (7 outbound │  ──HTTP──► fraud-svc        (added Q3)
  │   integrations│ ──HTTP──► recs-svc         (added Q4)
  │   in one    │  ──HTTP──► partner-webhook   (added Q4)
  │   codebase) │
  └──────────────┘
     • availability: 0.999⁷ = 99.3 % IF any of them is a hard dependency
     • every new consumer = a deploy of the order service
     • order-svc's test suite needs 7 fakes
     • the fraud team's outage pages the order team
```

With pub-sub, the order service publishes one event and knows nothing:

```
  ┌──────────────┐   publish    ┌──────────┐   ┌──► email-svc
  │  order-svc   │ ────────────►│  topic:  │───┼──► loyalty-svc
  │              │   one call   │ order.   │───┼──► analytics-svc
  └──────────────┘              │ placed   │───┼──► warehouse-loader
                                └──────────┘   ├──► fraud-svc
                                               ├──► recs-svc
                                               └──► partner (via its own queue)
     • availability: unaffected by subscriber health
     • a new consumer is a subscription, not a deploy
     • order-svc's tests need one fake: the publisher
     • each team pages itself
```

Four properties fall out of that inversion, and each is the reason a different organisation chooses pub-sub:

1. **Open/closed extensibility.** The publisher is closed for modification and the architecture is open for extension. This is the actual commercial argument: at 5+ consumers, the per-consumer cost of the naive design is a deploy, and deploys are where the risk lives.
2. **Availability isolation.** This is the arithmetic from L68. Under the naive design, if any downstream call in the request path is a hard dependency, checkout inherits its availability. Published events are not on the request path.
3. **Independent scaling and ownership.** The warehouse loader processes 4 messages/sec in batch; the fraud scorer needs 900/sec with a 50 ms budget. Same event, wildly different consumers, no compromise.
4. **A replayable audit of facts.** "Order 88214 was placed at 12:04:11" is a fact every team can read from the same place, rather than a call each team makes to re-derive.

And the cost, which is not visible in the diagram: **the event schema becomes a public interface.** With a direct HTTP call, the contract is documented by the caller's code and can be changed with a coordinated deploy. With a topic, unknown consumers depend on fields you did not know were load-bearing, and removing `shipping_address.line2` breaks the partner's fulfilment feed two days later. Pub-sub buys extensibility with a versioning obligation that lasts as long as the topic does.

## 3. Real-world analogy

A newspaper, versus a taxi rank.

A **taxi rank** is a queue (L68). There is a line of passengers and a line of taxis, and each passenger is claimed by exactly one taxi. Add a taxi and the line drains faster; the passengers do not care which taxi. The unit of work is *distributed*, and each unit of work is consumed once.

A **newspaper** is a topic. The publisher prints one edition and does not know how many people read it, or who they are, or whether any particular reader is on holiday this week. Each subscriber receives their **own copy**. A reader who cancels their subscription does not stop anyone else's paper arriving. A new reader joining next month does not require the publisher to change the printing press, and critically, **the publisher cannot phone the reader to ask them to change their reading habits** — the content, once printed, is a contract with every reader simultaneously.

Three consequences of the newspaper model are the pub-sub mechanics, and every one of them has a production analogue:

- **Delivery needs its own arrangement.** The publisher does not hand the paper to you personally. There is a distribution system — newsagent, delivery van, letterbox (**a queue or subscription**) — and *that* is what fails when your paper does not arrive. When someone says "the message was published", you must ask "delivered to *which* subscription, and did that subscription's own storage accept it?"
- **Not every reader wants every section.** You get the whole paper, but you only read the sport. **Filter policies** are the publisher's version of sectioned publishing: the subscriber states what it wants and the distributor skips the rest. Without them, a subscriber that needs 2 % of the events still pays to receive, store, and skip 100 % of them.
- **The archive is finite.** Back issues are kept for a while and then pulped. A subscriber who was suspended for a month may not be able to catch up. That is **retention**, and it is the reason "durable subscription" is a stronger claim than it sounds — durable does not mean forever.

The managerial point is the one that separates a working pub-sub system from an "event soup": the newspaper's content is *edited to a standard*, because it is a contract with thousands of unknown readers. A topic whose payloads are whatever each publisher felt like emitting is not a newspaper; it is a suggestion box, and every subscriber has to guess.

## 4. Detailed technical explanation

### 4.1 The topology, and where "durable" is decided

```
  PUBLISHER                BROKER (topic)                  SUBSCRIPTIONS
  ┌────────┐                                   ┌──────────────────────────┐
  │ order  │ ──publish──►  ┌─────────────┐  ────►│ SQS queue (durable)     │──► email workers
  │  svc   │               │   topic     │      └──────────────────────────┘
  └────────┘               │ order.placed│      ┌──────────────────────────┐
                           │             │  ────►│ SQS queue (durable)     │──► loyalty worker
   publisher has           └─────────────┘      └──────────────────────────┘
   NO knowledge of                   │          ┌──────────────────────────┐
   subscribers, counts,              │      ────►│ Lambda (durable)        │──► analytics
   or health                         │          └──────────────────────────┘
                                     │          ┌──────────────────────────┐
                                     │      ────►│ HTTPS webhook (EPHEMERAL)│──► partner
                                     │          └──────────────────────────┘
                                     │             ▲ retried ~3-100 times over
                                     │               minutes/hours, then DROPPED
                            filter policy ─── evaluated HERE, by the broker,
                                              per subscription, before delivery
```

The single most important distinction in the diagram is the bottom subscription. **A webhook has no storage.** If the partner's endpoint is down, the broker retries for a bounded period (SNS: a retry policy with up to ~100 attempts over ~23 days for HTTP endpoints, but the practical failure mode is a 5xx being retried for an hour and then dropped), and then the event is gone. Every serious pub-sub architecture therefore uses **SNS → SQS → consumer**, with the queue absorbing the partner's downtime and the partner pulling at its own pace. That single pattern — *fan-out to queues, never fan-out to endpoints* — removes an entire class of data loss.

### 4.2 Push versus pull, and why pull wins

| | **Push** (webhook, mobile push, HTTP endpoint) | **Pull** (queue, consumer group) |
|---|---|---|
| Discovery | Subscriber must be reachable now | Subscriber connects when ready |
| Failure handling | Broker retries; then drops | Message waits in the subscriber's own storage |
| Backpressure | None — you get hammered when you are weakest | Natural: you pull at your own rate |
| Security | Broker must reach into your network | You reach out; no inbound firewall hole |
| Complexity | Low for the broker, high for the subscriber | Low for the subscriber, broker does queueing |
| Ordering | Typically none | Possible, per partition/group |
| Use for | Third-party integrations you do not control, mobile notifications | **Everything internal, and all data-bearing events** |

The engineering rule: **push is for endpoints you cannot change; pull is for everything you can.** The classic internal mistake is building a webhook between two of your own services, which re-introduces exactly the availability coupling pub-sub was supposed to remove.

### 4.3 Filtering: how fan-out stays affordable

Naive fan-out means every subscriber receives every message and discards most of them. That is not a correctness bug, it is a **cost and load multiplication**: at `E` events/sec and `N` subscribers, the broker moves `E × N` messages.

Filter policies move the decision into the broker:

```
   No filter:  1,000 events/s × 7 subscriptions = 7,000 deliveries/s
               Subscriber 7 needs 2 % of them → it receives 1,000/s and
               throws away 980/s. You paid to store and deliver 980/s of
               garbage, and its consumer pool is sized for the garbage.

   Filter:     sub 7 filter {"event_type":["order.refunded"]}
               → receives ~2 % = 20/s. Its storage, its worker count, and
                 its cost all fall by 98 %.
```

Filter policies are matched against **message attributes**, not the body (on SNS), which is a design constraint that shapes your event envelope: if you want to filter on `region` or `event_type` or `tenant_tier`, those must be **attributes**, published deliberately, not buried in JSON. EventBridge and Google Pub/Sub support filtering on message attributes/body respectively; the principle is the same — **the routing keys are part of the event's public interface**, and adding one later is a publisher change.

### 4.4 Event design: thin or fat, and what "a fact" means

| Design | The message contains | Consumers do | Trade-off |
|---|---|---|---|
| **Thin event** (notification) | `{type, id, occurred_at}` | Call back to the source service for the data | Small messages, but every consumer now has a synchronous dependency on the source — re-introducing the coupling you removed |
| **Fat event** (event-carried state transfer) | The full relevant state at the time | Use the payload directly | No callback; but the event is now a *snapshot* and consumers must know which schema version they hold |
| **Delta event** | What changed, and from what to what | Apply the change | Compact, enables audit; requires ordered delivery to be meaningful |
| **Hybrid** | Key facts in the envelope, full detail by pointer | Fetch only what it needs | The pragmatic default at scale (claim-check, L68 §8) |

Three rules that prevent most event-design disasters:

1. **Name events as facts in the past tense** (`order.placed`, not `place_order`). A command has one intended recipient and implies an obligation; a fact can have any number of readers and implies none. Commands over a topic are a design smell — they mean your "pub-sub" is a distributed RPC with extra steps.
2. **Version the schema and never reuse a field.** `schema_version` in the envelope, additive changes only, and a documented deprecation window. Removing a field from a published event is a **breaking change to a public API**, regardless of who you think is listening.
3. **Include the metadata the consumers need to be correct**: `event_id` (for idempotency), `occurred_at` (a *server* timestamp, L66), `correlation_id` (for traces, L88), and `causation_id` (which event caused this one, for debugging chains).

### 4.5 The consequences pub-sub adds to your life

| Property | Before pub-sub | After pub-sub |
|---|---|---|
| Caller knows the result | Yes (synchronously) | No — it only knows the event was accepted |
| Failure visibility | Errors in the response | **Silent** unless each subscription is monitored |
| Schema changes | Coordinated deploy | Migration across unknown consumers |
| Debugging | One stack trace | A trace with an async hop, plus per-subscription DLQs |
| Data freshness | Read-after-write | Eventually consistent, with a lag you must bound |
| Cost | Per request | Per delivery **per subscriber** (fan-out ×) |
| Testing | N fakes | 1 fake publisher, plus contract tests per consumer |

The row that catches teams out is the second one: with a synchronous call, a broken consumer produces errors at the caller. With pub-sub, the publisher succeeds, the broker succeeds, and the subscriber fails **into a DLQ nobody is watching**. "Nothing is on fire, therefore nothing is wrong" is the characteristic false comfort of an unmonitored pub-sub system.

## 5. Request / data flow

BookNook's `order.placed` event, from publish to all seven subscribers, with one subscriber down and one filtered out.

```
  T+0ms    order-svc: INSERT orders (TXN) + INSERT outbox (SAME TXN)  ← L85
  T+9ms    HTTP 201 to the customer. The customer's journey ends here.

  T+40ms   outbox relay: reads the outbox row, publishes to topic
           ┌──────────────────────────────────────────────────────────┐
           │ topic: booknook.order.placed                             │
           │ attributes: event_type=order.placed, region=eu,          │
           │             schema_version=3, tenant_tier=standard        │
           └──────────────────────────────────────────────────────────┘
                      │
      ┌───────────────┼────────────────┬──────────────┬──────────────┐
      ▼               ▼                ▼              ▼              ▼
  ┌────────┐   ┌────────────┐   ┌───────────┐  ┌───────────┐  ┌───────────┐
  │email   │   │loyalty     │   │analytics  │  │fraud      │  │partner    │
  │queue   │   │queue       │   │queue      │  │queue      │  │queue      │
  └───┬────┘   └─────┬──────┘   └─────┬─────┘  └─────┬─────┘  └─────┬─────┘
      │              │                │              │              │
   worker ✓       worker ✓         worker ✓       worker ✓       partner PULLS
   (email sent)   (points)         (stored)       (scored)       every 30 s ✓

  MEANWHILE — the recommendations subscriber is DOWN (deploy):
      its SQS queue accepts all 81,000 burst messages, and its workers
      drain them when they return. Nobody else notices. ✅

  AND — the "refunds only" subscriber:
      filter policy {"event_type":["order.refunded"]}
      receives 0 messages from this publish. It never even wakes up. ✅

  T+41ms   publisher records: published=1. It knows nothing about any of
           the above outcomes, and that is the design, not an oversight.

  ── WHAT MUST BE MONITORED (the publisher cannot help you here) ──
    per-subscription: oldest_message_age, DLQ depth, delivery failures
    broker-side: per-subscription delivery success rate (SNS→SQS failures
                 are invisible in the consumer's metrics if the consumer
                 never received the message at all)
```

The last block is the load-bearing one. In a queue-based system, the consumer's own metrics tell you whether it is healthy. In a pub-sub system, there is a **gap between "published" and "received"** that neither publisher nor consumer metrics cover — a subscription whose delivery is broken (a deleted queue, a wrong IAM policy, an exhausted retry policy) shows a perfectly healthy publisher and a perfectly healthy (idle) consumer. You must instrument **each subscription's delivery rate**, and alert if `published` and `sum(received across subscriptions)` diverge.

## 6. ASCII diagram

Queue versus pub-sub, drawn as the same system in two configurations:

```
  QUEUE (work distribution) — one message, one worker
  ┌────────┐      ┌─────────────┐      ┌────────┐
  │producer│─────►│   QUEUE     │─────►│worker 1│  message 1
  └────────┘      │ m1 m2 m3 m4 │─────►│worker 2│  message 2
                  │             │─────►│worker 3│  message 3
                  └─────────────┘      └────────┘
   4 messages in  →  4 processings total.  Add a worker → drains faster.

  PUB-SUB (fact broadcast) — one message, N copies
  ┌────────┐      ┌─────────────┐      ┌──────────┐
  │publisher│────►│    TOPIC    │─────►│ sub A    │  copy 1
  └────────┘      │  1 message  │─────►│ sub B    │  copy 2
                  └─────────────┘─────►│ sub C    │  copy 3
                                       └──────────┘
   1 message in  →  3 processings total.  Add a subscriber → 4 processings,
   and existing subscribers are unaffected.

  ⇒ A queue answers "who does this work?"
    A topic answers "who needs to know this happened?"
```

The canonical production topology — **fan-out to durable queues, never to endpoints** — and the failure each leg survives:

```
                    ┌──────────────── topic: order.placed ────────────────┐
                    │        (retained only briefly — it is transport,    │
                    │         not storage; delivery is immediate)         │
                    └───┬───────────┬───────────┬───────────┬─────────────┘
        filter: all     │           │           │           │   filter: refunds
                        ▼           ▼           ▼           ▼
                    ┌───────┐   ┌───────┐   ┌───────┐   ┌───────┐
                    │queue A│   │queue B│   │queue C│   │queue D│
                    │ 14 d  │   │ 14 d  │   │ 14 d  │   │ 14 d  │
                    └───┬───┘   └───┬───┘   └───┬───┘   └───┬───┘
                        ▼           ▼           ▼           ▼
                     email      loyalty     analytics     refunds
                     workers    worker      pipeline      handler
                      ✓ 3 down   ✓           ✓ batched    ✓ 0 msgs

  Survives:  a subscriber down for 14 days (queue holds it)
             a slow subscriber (drains at its own rate, nobody blocked)
             a subscriber that only wants 2 % (filters at source)
  Does NOT survive:  the topic silently not delivering (monitor per-subscription
             delivery rate), or a consumer that is broken but acking (§11.3)
```

## 7. Detailed example

### 7.1 Adding a subscriber in one day instead of one quarter

BookNook's recommendation team wanted order events to power "buy again". Under the naive architecture this was a quarter of negotiation: a change to `order-svc`, a shared library update, a coordinated release, and an argument about whether the recommendation team could be paged at 3 a.m. Under pub-sub it was:

```
  1. Create an SQS queue `recs-order-events` (retention 14 days).
  2. Subscribe it to `booknook.order.placed` with
        filter_policy: {"event_type": ["order.placed"],
                        "region":     ["eu"]}
  3. Grant the recommendation service's role read access.
  4. Ship a consumer, idempotent on event_id.
  ZERO changes to order-svc. Zero coordinates. One day.
```

Two details made that day possible, and both were decisions made *before* anyone needed them:

- **The event carried a routing attribute** (`region`) and a schema version, so the new subscriber could filter without a publisher change.
- **The envelope carried `event_id`, `occurred_at` and `schema_version`** from v1, so a new consumer could be idempotent and version-aware without asking anyone.

The counter-example from the same quarter: the fraud team asked for `payment_method_fingerprint`, which was not in the payload. That required a publisher change, a new schema version (v4), and a two-week deprecation window with the partner — because v3 could not be modified in place.

**The lesson:** in pub-sub, the cost of adding a subscriber is decided by the *envelope you designed when there were no subscribers*.

### 7.2 The subscription that silently delivered nothing

Three weeks after launch, the loyalty team reported no points accruing. Investigation:

- The publisher: 1.1 M publishes, 0 errors.
- The loyalty consumer: 0 errors, 0 messages, healthy pods, idle.
- The subscription: **the SQS queue had been deleted during a Terraform refactor two weeks earlier.** SNS had been retrying delivery to a queue that did not exist, then **dropped the messages silently** — because the subscription's policy was "retry, then give up", and no DLQ was configured on the subscription itself.

The gap: `published = 1.1 M`, `received by loyalty = 0`, and neither side's dashboards could see it, because the failure was *between* them.

The controls that were added — and the general rule they encode:

| Control | What it catches |
|---|---|
| **Delivery-rate parity alarm**: `sum(received) / published < 0.98` per subscription over 15 min | Deleted queue, broken IAM, exhausted retry policy |
| **Subscription DLQ** | Messages the broker could not deliver at all |
| **Synthetic event** published hourly with a canary subscriber that must receive it | End-to-end delivery proof, independent of business traffic |
| **Terraform `prevent_destroy`** on subscription-critical queues | The specific cause here |

This is the pub-sub tax: **you must monitor the space between components**, not just the components. A queue-based system fails loudly in one place; a fan-out system fails quietly in N places.

### 7.3 The event that was really a command

The refund service published `refund.requested` to a topic, with fields `order_id`, `amount`, `reason` — and exactly one subscriber, `payment-svc`. Both teams considered this "event-driven architecture". It was a remote procedure call with two extra moving parts:

- **No idempotency contract**, because "only one consumer exists" made it feel unnecessary. A redelivery double-refunded 14 customers.
- **No versioning discipline**, because "we control both ends". A field rename shipped in the same release, and a deploy-order race broke refunds for 40 minutes.
- **No ability to add a second consumer**, because the payload contained a *request* ("please refund") rather than a *fact* ("refund issued, here is the payment reference"). A second subscriber would have had to re-derive state from the request.

The rewrite published a fact — `payment.refunded {order_id, payment_id, amount, provider_reference, occurred_at}` — after the refund had actually succeeded, with one consumer (notifications), then two (accounting), then three (fraud). **The rule that would have prevented all of it: if exactly one consumer must perform an action, use a queue (L68). If the message describes something that has already happened, use a topic.**

## 8. Code or configuration example

```hcl
# ============================================================================
# BookNook: fan-out to durable queues, per-subscription policy, DLQ per leg.
# Terraform, illustrative. The comments are the design decisions.
# ============================================================================

# --- 1. The topic. Transport only: it retains nothing you can replay. ------
resource "aws_sns_topic" "order_placed" {
  name = "booknook-order-placed"
  # If you need REGIONAL durability + replay, this is the wrong primitive:
  # see L70 (event streams) — a topic is not a log.
}

# --- 2. One queue per subscriber. Never a shared queue, never a raw webhook.
resource "aws_sqs_queue" "loyalty" {
  name                       = "booknook-loyalty-order-events"
  visibility_timeout_seconds = 30
  message_retention_seconds  = 1209600      # 14 days: absorb a long outage
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.loyalty_dlq.arn
    maxReceiveCount     = 5
  })
}

# --- 3. The subscription: filter + retry policy + its OWN DLQ. -------------
# A subscription without a DLQ loses messages the broker cannot deliver,
# and it does so silently (§7.2).
resource "aws_sns_topic_subscription" "loyalty" {
  topic_arn = aws_sns_topic.order_placed.arn
  protocol  = "sqs"
  endpoint  = aws_sqs_queue.loyalty.arn

  filter_policy = jsonencode({
    event_type = ["order.placed", "order.refunded"]   # 2 of 9 event types
    region     = ["eu"]                              # half the traffic
  })
  # 2/9 x 1/2 ≈ 11 % of published volume reaches this queue.
  # That ratio is the entire cost argument for filtering.

  raw_message_delivery = true    # otherwise SNS wraps the body in an envelope
                                 # and every consumer must unwrap it — plus the
                                 # wrapper changes the message shape for
                                 # filters and for schema validation.

  # Non-SQS protocols (http/https/email) get a delivery policy instead —
  # and note that it is a RETRY policy, not durable storage:
  # delivery_policy = jsonencode({
  #   http = {
  #     defaultHealthyRetryPolicy = {
  #       minDelayTarget=20, maxDelayTarget=600,
  #       numRetries=17, numMaxDelayRetries=17,
  #       backoffFunction="exponential"
  #     }
  #     disableSubscriptionOnFailure = true   # ← and then it is OFF, silently
  #   }
  # })
}

# --- 4. The event envelope: the public API of the topic. -------------------
```

```json
{
  "specversion": "1.0",
  "type": "order.placed",
  "source": "booknook.order-svc",
  "id": "01J9K2M4Q7X8R3T6V0W1Y2Z3A4",
  "time": "2026-10-30T12:04:11.221Z",
  "datacontenttype": "application/json",
  "correlationid": "trace-9f2c4a10",
  "causationid": "checkout-req-7b31",
  "schema_version": 3,
  "data": {
    "order_id": 88214,
    "customer_id": 4821,
    "total_pence": 249900,
    "currency": "GBP",
    "item_count": 3,
    "channel": "web"
  }
}
```

```python
# --- 5. A correct subscriber: idempotent, filter-aware, DLQ-conscious. ------
def handle(event: dict):
    # (a) dedupe on the ENVELOPE id, before any side effect — at-least-once
    #     is a property of every pub-sub delivery, not just of queues.
    if not dedupe.first_seen(event["id"], consumer="loyalty"):
        return                                    # duplicate: drop, do not redo

    # (b) tolerate schema evolution: consumers must ignore unknown fields and
    #     refuse only on a MAJOR version they do not understand.
    if event["schema_version"] > SUPPORTED_MAX:
        raise UnsupportedSchema(event["schema_version"])   # → DLQ, loudly

    # (c) do the work, and record that the event was applied, atomically.
    with db.transaction():
        db.insert_loyalty_ledger(
            order_id=event["data"]["order_id"],
            points=event["data"]["total_pence"] // 100,   # 1 point per £1
            applied_event_id=event["id"],                 # ← idempotency, in-TXN
        )
    # If this raises, the message is redelivered (at-least-once) and the
    # applied_event_id makes the retry a no-op. No dedupe table read needed
    # on the happy path when the constraint is the dedupe.
```

```yaml
# --- 6. The alarms. Pub-sub fails BETWEEN components, so monitor the gap. --
alerts:
  # 1. Delivery parity per subscription: published vs received.
  - expr: sum(rate(sqs_received{loyalty}[15m])) /
          sum(rate(sns_published{order_placed}[15m])) < 0.98
    for: 15m
    severity: page          # catches a deleted queue or broken IAM (§7.2)

  # 2. Broker-side delivery failures (invisible to the consumer).
  - expr: rate(sns_delivery_failures{subscription="loyalty"}[5m]) > 0

  # 3. Per-subscription consumer health, not global health.
  - expr: max by (subscription) (sqs_oldest_message_age_seconds) > 300

  # 4. Any non-empty DLQ, for any subscription, at any tier.
  - expr: sqs_approximate_number_of_messages_visible{queue=~".*-dlq$"} > 0

  # 5. Synthetic canary: an hourly event that MUST appear in the canary
  #    consumer within 2 minutes, proving end-to-end delivery continuously.
  - expr: time() - canary_last_received_timestamp > 120
    severity: page
```

## 9. Numerical example

BookNook publishes **1,000 order events/sec** at peak. Seven subscriptions, with filter policies that reduce actual delivery to the percentages below. Message size 2 KB.

**Step 1 — fan-out volume with and without filtering.**

```
  NO FILTERS:  1,000 publish/s × 7 subscriptions = 7,000 deliveries/s
               = 604.8 M deliveries/day
               × 2 KB = 1.21 TB/day moved and stored across the fleet

  WITH FILTERS:
     email         100 %  → 1,000/s
     loyalty       100 %  → 1,000/s
     analytics      60 %   600/s   (region=eu only)
     warehouse      60 %   600/s
     fraud          35 %   350/s   (high-value orders only)
     recs           60 %   600/s
     refunds only    2 %    20/s    ← the subscriber that reads 2 %
     ─────────────────────────────
     total              4,170/s   (vs 7,000/s)  = 40 % reduction

  The refunds subscriber alone: 1,000/s → 20/s is a 98 % reduction in its
  storage, its worker fleet, and its cost. Before filtering it would have
  been sized for 50× its actual work.
```

**Step 2 — what that costs, in deliveries.** Broker pricing is per request/message; one publish to N subscriptions is N deliveries (plus receive/delete if the subscriber is a queue).

```
  deliveries/day   = 4,170 × 86,400              = 360.3 M
  queue lifecycle requests = × 3 (receive, delete, receive-empty)
                                                ≈ 1.08 B requests/day
  at $0.40/million (SQS) and $0.50/million (SNS):
     SNS publishes (1,000/s → 86.4 M/day)        ≈ $43/day
     SQS request-equivalents                     ≈ $432/day
     ───────────────────────────────────────────────────────
     ≈ $475/day ≈ $14,250/month

  WITHOUT filtering: ≈ $800/day ≈ $24,000/month.
  ⇒ The filter policies are worth ~$120k/year. They are not a tidiness
    feature; they are the cost model.
```

**Step 3 — why a topic is the wrong primitive at this volume (the L70 foreshadow).** With 4,170 deliveries/sec and 2 KB messages, the *topic* retains nothing: it is a router. Every subscriber's queue must therefore size its own retention:

```
  loyalty queue, 1,000/s × 2 KB = 2 MB/s = 173 GB/day
  retention 14 days → 2.4 TB of standing storage for ONE subscriber
  × 7 subscribers (weighted) ≈ 6.5 TB standing, for events the source
    database already contains.

  A partitioned log (L70) would keep ONE copy of the stream (order log,
  ~2 MB/s → same 173 GB/day) and let all seven consumers read it at their
  own offsets: 1× storage instead of ~7×, and replay for free.
  ⇒ Below ~a few hundred msg/s, fan-out-to-queues is the right call.
    Above it, publish to a log and let consumers read it.
```

**Step 4 — the retry budget for an ephemeral subscriber, and why it is a lie.** SNS's default HTTP retry policy for a failing endpoint: 3 immediate retries, then backoff for up to ~1 hour, total ~30-50 attempts depending on the policy, **then the message is dropped** (or moved to a subscription DLQ if one exists).

```
  Partner endpoint down for 1 hour at 350 events/s (its filtered share):
     events during the outage = 350 × 3,600 = 1.26 M
     broker buffer for an HTTP subscription = ZERO
     ⇒ 1.26 M events dropped, or (with a DLQ) 1.26 M × 2 KB = 2.5 GB
        sitting in a DLQ that must be replayed by hand.

  With SNS → SQS → partner-pull instead:
     1.26 M messages in the partner's queue, drained at 350/s over
     3,600 s once they recover. Zero loss, zero manual intervention.
  ⇒ That is the entire argument for fan-out-to-queues: it converts an
    outage into a delay, and a delay is survivable.
```

## 10. Trade-offs

| Decision | Buys you | Costs you | Choose it when |
|---|---|---|---|
| **Pub-sub over direct calls** | Open/closed extensibility; availability isolation; team autonomy | Schema is now a public API; failures are silent; fan-out cost | 3+ consumers of the same fact, or consumers owned by other teams |
| **Queue instead** (L68) | Work distribution; one consumer; simpler ops | Adding a consumer steals capacity from existing ones | Exactly one party must do the work |
| **Fan-out to queues** | Durability per subscriber; natural backpressure | ~N× storage and delivery cost | **Always**, for internal subscribers |
| **Direct webhook** | No infrastructure on the subscriber side; works for third parties | No storage: an outage loses events; ordering impossible; security exposure | Third parties you cannot change (and give it a subscription DLQ) |
| **Fat events** | No callback; fully decoupled consumers | Bigger messages; every consumer handles every schema version | Consumers need the data and the source is not queryable by them |
| **Thin events** | Small; the source stays authoritative | Every consumer re-introduces a synchronous dependency on the source | You need consistency with the source more than decoupling |
| **Filter policies** | Up to ~98 % reduction in a narrow subscriber's load and cost | Routing keys become part of the published contract | As soon as any subscriber wants <100 % (i.e. always) |
| **Many fine-grained topics** | Precise subscriptions; clean ownership | Topic sprawl; publishers must know which topic | Natural event domains with clearly different audiences |
| **One big topic** | Simple publishing; new subscribers need no publisher change | Everyone receives everything; filters must be comprehensive | Small systems, early stage |
| **Adding a subscriber** | Free at the producer | Free at the producer only — schema design decided this cost years earlier (§7.1) | Continuous; expect it |

## 11. Failure scenarios

**11.1 — The silent subscription** (§7.2). A queue deleted during a refactor; SNS dropped every message; both ends healthy. **Symptom:** a business metric that quietly stops accruing, discovered by a human weeks later. **Fix:** delivery-rate parity alarms per subscription, a subscription DLQ, `prevent_destroy` on critical infrastructure, and an hourly synthetic canary.

**11.2 — The ephemeral endpoint treated as durable.** A partner integration used a direct HTTPS subscription; the partner had a 40-minute outage at peak and **1.4 M events were dropped** after the retry policy expired. **Symptom:** the partner's data is permanently incomplete, and there is nothing to replay. **Fix:** fan-out to a queue the partner pulls from, or at minimum a subscription DLQ plus a documented replay procedure.

**11.3 — The consumer that acks and does nothing.** A consumer deserialised the event into a new schema, silently ignored unknown fields, and wrote zero rows because a renamed field defaulted to null. It acked everything. **Symptom:** queue empty, consumer healthy, zero errors, no data. **Fix:** contract tests per subscriber against a golden event; a business-level assertion ("rows written per 1,000 events > 0.95 × published") rather than a technical one; and a canary subscriber with a known-good expectation.

**11.4 — Fan-out amplification during a burst.** A flash sale 90× baseline; seven subscribers' queues all grow at their filtered rates; the *analytics* subscriber falls 40 minutes behind and its warehouse loader's recovery burst saturates the warehouse's write capacity. **Symptom:** a second outage after recovery, in systems unrelated to checkout. **Fix:** per-subscription drain rate caps, and treating recovery as its own capacity event (L68 §11.7).

**11.5 — The breaking schema change that nobody noticed.** A publisher removed `shipping_address.line2` as "unused" (no consumer in the repo referenced it). The partner's fulfilment feed broke two days later, and their system had already shipped 400 parcels with a truncated address. **Symptom:** partner escalations, not monitoring. **Fix:** additive-only schema evolution, a `schema_version`, a published deprecation policy, consumer-driven contract tests, and a registry that records which consumers read which fields.

**11.6 — The filter that filtered everything.** A filter policy was written as `{"event_type": ["order.placed"]}` but the publisher set the attribute as `type=order.placed`. SNS matched nothing, delivered nothing, and reported no errors, because **a filter that excludes a message is not a delivery failure**. **Symptom:** a subscriber that has never received a message since deploy. **Fix:** filter policies are tested against a real sample of published messages in CI; alert on "zero deliveries for a subscription that should be receiving >0".

**11.7 — Command over a topic** (§7.3). One intended consumer, no idempotency, no versioning. **Symptom:** double refunds on redelivery, then a deploy-order race. **Fix:** publish facts, not commands; if one party must act, use a queue with an explicit contract.

**11.8 — The topic used as storage.** A team expected to replay "the last week of events" from an SNS topic. There is nothing to replay: delivery was immediate, and the topic retained nothing. **Symptom:** a requested backfill that is impossible without rebuilding from the database or CDC (L85). **Fix:** choose the primitive by requirement — a **log** (L70) retains and replays; a **topic** routes.

## 12. Alternatives

| Instead of pub-sub | Consider | When |
|---|---|---|
| Topic fan-out | **A partitioned log** (L70) | Many consumers, replay needed, or high volume where N× storage is unacceptable |
| Topic fan-out | **A queue** (L68) | Exactly one consumer must do the work |
| Topic fan-out | **Direct HTTP call** | One consumer, synchronous result needed, availability coupling acceptable |
| Broker topic | **EventBridge-style router with rules** | Content-based routing, cross-account delivery, native AWS targets and schemas |
| Broker topic | **CDC into a log** (L85) | The events should be derived from database changes rather than hand-published |
| Broker topic | **Change-feed table + poller** | No broker available, very low volume, and you want the event inside the DB transaction |
| Fat events | **Thin events + a query API** | The payload changes often and consumers need current state, not snapshots |

## 13. When to use it

- **Three or more consumers of the same fact**, especially when they are owned by different teams.
- **Whenever new consumers are expected.** The whole value is that adding one does not touch the publisher.
- **When availability isolation matters**: no subscriber's health should be able to affect the publisher.
- **When consumers need very different throughput and latency guarantees** (batch warehouse versus 50 ms fraud scoring).
- **When you want an audit trail of business facts**, published once and readable by all.
- **When integrating with partners** — with a queue in between, not a raw webhook.

## 14. When *not* to use it

- **One consumer.** That is a queue; a topic adds a broker hop and no benefit.
- **You need the result to continue.** Authorisation, stock checks, price validation.
- **You need replay or ordered history at volume.** That is a log (L70), and pretending a topic is one leads to §11.8.
- **The payload is a command** ("refund this"). Publish facts; use queues for obligations.
- **Nobody will monitor the subscriptions.** An unmonitored fan-out fails silently in N places at once — strictly worse than a synchronous call that fails loudly in one.
- **The event contract cannot be versioned.** If you cannot commit to additive-only schema evolution, pub-sub will convert every change into an incident.
- **Low volume with a single team.** Direct calls with good retries are simpler, and simplicity is a feature.

## 15. Common mistakes

1. **Fanning out to endpoints instead of queues.** The single biggest source of lost events (§11.2).
2. **No per-subscription monitoring.** Monitoring the publisher and the consumer and missing the space between them (§7.2).
3. **Assuming a topic retains messages.** It routes; it does not store (§11.8).
4. **Publishing commands.** `refund.requested` instead of `payment.refunded` (§7.3).
5. **Unversioned schemas, or in-place field changes.** The contract is public whether you treat it as public or not.
6. **No filter policies.** Every subscriber pays to receive and discard 90 %+ of the volume.
7. **Filtering on body fields when the broker filters on attributes.** A filter that matches nothing is silent, not an error (§11.6).
8. **No `event_id` in the envelope.** Consumers cannot dedupe, so at-least-once becomes duplicate effects.
9. **No `correlation_id`.** Debugging an async fan-out without one is guesswork (L88).
10. **Sharing a queue between subscribers.** Reintroduces competing-consumer semantics where broadcast was intended.
11. **Forgetting that fan-out multiplies cost** — `N` copies, `N` retentions, `N` consumer fleets.
12. **Expecting ordering.** Topic fan-out gives no ordering guarantee; per-key ordering needs a log partition (L72).
13. **Treating pub-sub as a way to avoid designing contracts.** It is the opposite: it makes the contract explicit and permanent.

## 16. Interview perspective

**What is actually being tested.** Whether you know *why* pub-sub exists — the inversion of dependency — and whether you can describe its costs as precisely as its benefits. Strong candidates separate "distribute work" from "broadcast facts", reach for fan-out-to-queues immediately, talk about the schema as a public API, and name the specific monitoring gap between publisher and subscriber.

**Answers that land well:**

- *"How would you notify six systems that an order was placed?"* — "Publish one `order.placed` fact to a topic, and fan out to one durable queue per subscriber with filter policies, so a subscriber's outage is a delay rather than a loss. The envelope carries `event_id`, `schema_version`, `occurred_at` and `correlation_id`, because those decide whether new subscribers can be added cheaply later. The publisher doesn't know or care who is listening, and I alarm on per-subscription delivery parity so a broken subscription can't be silent."
- *"Why not just call each service?"* — "Because the order service's availability becomes the product of six downstreams — 0.999⁶ — and every new consumer is a deploy of the order service. But the trade is real: the event schema is now a public API, failures are silent unless I monitor per subscription, and fan-out costs N times the storage."
- *"Queue or topic?"* — "Queue when exactly one party must do the work — that's work distribution. Topic when multiple parties need to know the same fact — that's broadcast. If a topic has exactly one consumer and the payload says 'please do X', it's a queue with extra steps, and I'd rewrite it as one."
- *"How do you add a consumer in an afternoon?"* — "By having designed the envelope for consumers that did not exist yet: routing attributes for filters, a schema version, an event ID, and additive-only evolution. That is a decision made years earlier, and it is the difference between a one-day job and a one-quarter negotiation."

**Common interview tasks:** choose between queue, topic and log for a described requirement; design an event envelope; decide fat vs thin events; design filtering for a narrow subscriber; explain how a subscriber outage is handled; decide between webhooks and queues for a partner; explain the failure modes of fan-out; discuss schema evolution for an unknown consumer set.

**Red flags interviewers watch for:**

- Treating pub-sub and queues as interchangeable.
- Fanning out directly to HTTP endpoints for internal consumers.
- No mention of schema versioning or contract ownership.
- No per-subscription monitoring; only "the broker is up".
- Publishing commands to topics without noticing.
- No awareness of N× cost multiplication.
- Believing a topic is durable storage.
- Being unable to say what happens to a message when a subscriber is down for an hour.
- No mention of idempotency on the subscriber side.

## 17. Knowledge check

**Conceptual**

1. State the one structural difference between a queue and a topic, and derive three consequences for availability, cost, and how a new consumer is added. Then give the specific scenario in which a topic with a single consumer is the wrong primitive, and what you would use instead.
2. Explain why "fan out to queues" is preferred over "fan out to endpoints", using the terms *durable storage* and *backpressure*. Then state the one situation in which a webhook is still the right choice, and the two controls you must add to make it survivable.
3. Explain why a pub-sub system can have a perfectly healthy publisher and a perfectly healthy consumer and still lose every message. Name the specific monitoring signal that detects it and explain why neither side's own metrics can.

**Practical scenario**

4. BookNook's `order.placed` event currently has three subscribers and is about to gain four more, including an external partner. Volume is 1,000/sec at peak, 60/sec at baseline; messages are 2 KB. Requirements: no subscriber outage may lose events; each subscriber may receive only the event types it needs; the partner can only accept HTTP but must not lose events; the publisher must not change when a subscriber is added; and adding a field must not break any consumer. Design the full solution: the topology, the envelope with all metadata fields and why each exists, the filter policies with the expected delivery reduction for each subscriber, the partner integration, the five alarms with thresholds, the schema-evolution policy including a deprecation window, and a cost comparison of this design against both (a) direct calls and (b) a partitioned log at this volume.

**Calculation**

5. A topic publishes 800 msg/sec of 1.5 KB messages with five subscriptions and these filter ratios: 100 %, 100 %, 60 %, 25 %, 4 %. (a) Compute total deliveries/sec with and without filters, and the percentage reduction. (b) Compute the daily bytes moved in each case. (c) If one subscription is down for 6 hours, compute the messages its queue must hold and the storage required at 1.5 KB each. (d) State the retention you would configure and the drain time at that subscriber's capacity of 30 msg/sec.

6. A partner subscription is a direct HTTPS endpoint with a retry policy of 30 attempts over 2 hours and no subscription DLQ. The endpoint is down for 3 hours during which the partner's filtered share is 120 events/sec. (a) Compute the number of events published, the number the retry policy can attempt to deliver, and the number lost. (b) Redesign so that zero events are lost, and compute the queue storage required at 2 KB per event. (c) State the drain time at the partner's stated capacity of 150 events/sec.

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
  - [x] **6. Publish-subscribe systems (L69) ← you are here**
  - [ ] 7. Event streams (L70)
  - [ ] 8. RabbitMQ-style queues versus Kafka-style logs (L71)
  - [ ] 9. Consumer groups, partitions, offsets, ordering (L72)
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

**NEXT LESSON: Phase 4, Topic 7 — Event streams**

---
