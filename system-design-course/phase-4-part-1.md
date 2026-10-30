# Phase 4 — Distributed Systems

Lessons 64–92, covering Phase 4 Topics 1–29.

Continues from Phase 3 (Lessons 40–63).

Everything in Phases 1–3 assumed a comforting fiction: that when one part of BookNook asked another part for something, it either got an answer or got an error. Inside a single process that fiction is true — a function call returns a value or raises an exception, and there is no third possibility. The moment a request crosses a network, a third possibility appears: **you asked, and you do not know what happened.** The server may have never received the request, may have processed it and crashed before replying, may have processed it and replied into a dropped packet, or may still be working on it right now. Phase 4 is, in a sense, 29 topics about that one sentence.

Distribution is not a feature you add; it is a property you acquire the moment you have more than one machine, and it imposes costs you cannot opt out of. Phases 2 and 3 already distributed things — load balancers across app servers (Lesson 21), read replicas (Lesson 30), shards (Lesson 33), multi-region deployments (Lesson 39) — but we treated each as a scaling technique and waved at the failure modes. This phase stops waving. We will define **partial failure** precisely, show why a slow machine and a dead machine are fundamentally indistinguishable, explain why wall-clock timestamps silently corrupt data, and then build up the machinery the industry uses to cope: message queues, event logs, delivery semantics, idempotent consumers, backpressure, circuit breakers, leases, leader election, quorums, consensus, sagas, the transactional outbox, and the observability and reliability practices (SLOs, error budgets, RTO/RPO) that tell you whether any of it is working.

The phase is deliberately ordered as *problems first, machinery second, organisation third*. Topics 1–4 establish the physics — what is hard and why. Topics 5–14 cover asynchronous communication and the resilience patterns that make it survivable, because messaging is how most real systems decouple services. Topics 15–22 cover coordination and correctness: how independent nodes agree on anything at all, and how you keep a business transaction consistent when it spans three databases that cannot share a transaction. Topics 23–29 zoom out to architecture and operations: how to split (or not split) a system, how to find and configure services, how to see inside them, and how to survive the loss of an entire data centre.

One warning before we start, repeated throughout the phase: **almost everything here is a cost, not a benefit.** Distribution buys you scale, fault isolation and independent deployment. It charges you latency, partial failure, operational surface area and a class of bugs that do not reproduce on your laptop. The correct default is to stay undistributed until a specific, named requirement forces your hand — a theme we close out in *Phase 4, Topic 23 — Monoliths, modular monoliths and microservices*.

| # | Topic | Lesson |
|---|---|---|
| 1 | Why distributed systems are difficult | Lesson 64 |
| 2 | Partial failures and network partitions | Lesson 65 |
| 3 | Clock differences and ordering events | Lesson 66 |
| 4 | Synchronous versus asynchronous workflows | Lesson 67 |
| 5 | Message queues | Lesson 68 |
| 6 | Publish-subscribe systems | Lesson 69 |
| 7 | Event streams | Lesson 70 |
| 8 | RabbitMQ-style queues versus Kafka-style logs | Lesson 71 |
| 9 | Consumer groups, partitions, offsets and ordering | Lesson 72 |
| 10 | At-most-once, at-least-once and exactly-once semantics | Lesson 73 |
| 11 | Idempotent consumers and deduplication | Lesson 74 |
| 12 | Dead-letter queues and retry queues | Lesson 75 |
| 13 | Backpressure and load shedding | Lesson 76 |
| 14 | Circuit breakers, bulkheads and graceful degradation | Lesson 77 |
| 15 | Distributed locks and leases | Lesson 78 |
| 16 | Leader election | Lesson 79 |
| 17 | Quorums | Lesson 80 |
| 18 | Consensus and Raft | Lesson 81 |
| 19 | Distributed transactions | Lesson 82 |
| 20 | Two-phase commit | Lesson 83 |
| 21 | Saga pattern | Lesson 84 |
| 22 | Transactional outbox and change data capture | Lesson 85 |
| 23 | Monoliths, modular monoliths and microservices | Lesson 86 |
| 24 | Service discovery and configuration management | Lesson 87 |
| 25 | Observability: logs, metrics, traces | Lesson 88 |
| 26 | SLIs, SLOs and error budgets | Lesson 89 |
| 27 | High availability and disaster recovery | Lesson 90 |
| 28 | Active-passive versus active-active | Lesson 91 |
| 29 | RTO and RPO | Lesson 92 |

---

---

# Lesson 64 — Why Distributed Systems Are Difficult

*Phase 4, Topic 1*

## 1. Simple definition

A **distributed system** is a set of independent computers that cooperate to present themselves as a single coherent system to their users, and that communicate *only* by passing messages over a network.

Leslie Lamport's definition is sharper and more useful:

> *"A distributed system is one in which the failure of a computer you didn't even know existed can render your own computer unusable."*

That is not a joke. It is the operational definition. If some machine you have never heard of — a DNS resolver, a certificate authority's OCSP responder, a shared metadata service, a config server owned by another team — can take your service down, you are running a distributed system, whether or not you designed one.

Terms defined before use:

- **Node**: one independently failing unit of computation. Usually a process, not a machine — two processes on one host can fail independently, and for our purposes that makes them two nodes.
- **Message passing**: nodes share no memory and no clock. Every piece of information one node learns about another arrived as a message that could have been delayed, duplicated, reordered or lost.
- **Partial failure**: some nodes are working and some are not, *at the same time*, and no node has a reliable view of which is which. This is the subject of Lesson 65 and the defining property of the whole field.
- **Coherent system**: the users' mental model is "BookNook", singular. They do not know there are 14 services behind it, and they will not accept "the inventory service was down" as an explanation for a failed checkout.

## 2. Why it exists

Nobody wants a distributed system. People want things that only distribution can provide, and accept the system as the price.

There are exactly four requirements that genuinely force distribution:

1. **The workload exceeds one machine.** BookNook's catalogue search index no longer fits in the RAM of the largest instance available, or the write throughput exceeds what one primary can commit (Lesson 33 on sharding).
2. **The availability target exceeds one machine's reliability.** A single server with a 99.5% availability cannot deliver a 99.95% SLA no matter how good the hardware is, because its planned reboots alone exceed the budget (Lesson 15 on availability maths).
3. **Users are geographically spread and latency matters.** Speed of light in fibre is roughly 200,000 km/s; London to Sydney is about 17,000 km of cable, giving a one-way floor near 85 ms and a round trip near 170 ms before any processing. No amount of code optimisation fixes that; only putting compute near the user does (Lesson 39 on multi-region).
4. **Organisational scale.** Eighty engineers cannot productively share one deployment pipeline for one artefact. Independent deployability is a people problem solved with a technical tool, and it is the most common real reason teams split systems — which is also why so many splits go badly.

Notice what is *not* on the list: "microservices are modern", "the architecture diagram looks impressive", "we might need to scale one day". Distribution taken on without one of the four reasons above buys you every cost in this lesson and none of the benefits.

## 3. Real-world analogy

**Running a restaurant kitchen versus running a 40-branch restaurant chain by letter post.**

In a single kitchen, the chef shouts "two covers on table nine" and the sous-chef either acknowledges or is visibly, obviously absent. Everyone shares one room (shared memory), one clock on the wall (global time), and failure is total and observable — if the kitchen burns down, nobody is confused about whether the order was cooked.

Now distribute it. Head office posts a letter to branch 17: *"Prepare 200 covers for Saturday."* The letter may be lost. It may arrive late. The postal service may deliver it twice. Branch 17 may read it, start preparation, and then have its own phone line cut, so its confirmation letter never arrives. Head office now faces the question that defines this entire phase: **did branch 17 get the instruction or not?** Re-sending the letter risks 400 covers of waste. Not re-sending risks 200 hungry customers. There is no way to find out for certain, ever, using letters alone.

| Restaurant chain by post | Distributed software system |
|---|---|
| A letter takes days — delay is *expected* and planned around | Network calls take milliseconds *usually*, so engineers write code that quietly assumes they always will, and the code breaks on the 1-in-10,000 call that takes 30 seconds |
| You can phone a branch to check (an out-of-band channel) | There is no out-of-band channel. The network is the only channel, and it is the thing that failed |
| Branches are run by humans who improvise sensibly when a message is garbled | Software does exactly what it was told, including retrying a charge 400 times |
| 40 branches | Hundreds of service instances, each restarting, autoscaling (Lesson 38) and changing address continuously |
| A branch burning down is unambiguous and reported in the news | A node that is merely *slow* looks identical to a node that is *dead*, and this is provably undecidable (Lesson 65) |
| Letters cannot be forged by the postal service | Messages can be duplicated by the network itself, so "I received this twice" tells you nothing about how many times it was sent |

The last two rows are where the analogy stops being a teaching aid and starts being a lie. Human organisations tolerate ambiguity by improvising; distributed systems must *encode* their response to ambiguity in advance, which is why topics like idempotency (Lesson 17, and *Topic 11 — Idempotent consumers and deduplication*) matter so much.

## 4. Detailed technical explanation

### 4.1 The three fundamental difficulties

Every hard problem in Phase 4 reduces to one of three physical facts. Memorise these three; they generate everything else.

**Difficulty 1 — Partial failure.** On one machine, a crash is *fail-stop and total*: the process dies, its memory goes, nothing half-finished survives to confuse you. Across a network, failure is *partial and ambiguous*: the inventory service is down while the catalogue service is up; node 3 is reachable from node 1 but not from node 2; a write landed on two of three replicas. There is no "the system" to fail — there are only components, failing independently, observed inconsistently. Lesson 65 is devoted to this.

**Difficulty 2 — Unbounded latency.** Networks are **asynchronous**: there is no upper bound on how long a message may take. A packet can be queued in a switch buffer, retransmitted by TCP after a 200 ms timer (Lesson 6), delayed behind a VM's noisy neighbour, or held while the receiving process is in a garbage-collection pause. Because the delay is unbounded, **any timeout you choose is a guess**. Too short and you declare healthy nodes dead and retry needlessly; too long and your failure detection is slower than your users' patience. There is no correct value, only a trade-off between false positives and detection time (Lesson 16 on tail latency is the measurement side of this).

**Difficulty 3 — No global clock.** Each node has its own quartz oscillator drifting at its own rate. "Which of these two events happened first?" has no cheap, reliable answer across nodes. Lesson 66 is devoted to this, and it is the reason concepts like Lamport timestamps, vector clocks and consensus exist at all.

Everything else — consensus, quorums, sagas, exactly-once semantics — is engineering built on top of these three.

### 4.2 The eight fallacies of distributed computing

Formulated at Sun Microsystems (principally by Peter Deutsch, with James Gosling adding the eighth) around 1994–1997. Each fallacy is an assumption that is true enough on a laptop to be invisible, and false enough in production to cause an outage. Here is each one with the concrete BookNook failure that results from believing it.

**Fallacy 1 — The network is reliable.**
*Belief:* if I send a request, it arrives.
*BookNook failure:* the checkout service calls `POST /payments/charge`, gets a connection reset, and the code treats "no response" as "did not happen", so it retries. The first charge *had* succeeded. A customer is billed £24.99 twice. The fix is not better networking; it is an idempotency key on every mutating call (Lesson 17).

**Fallacy 2 — Latency is zero.**
*Belief:* a call to another service is roughly as cheap as a local function call.
*BookNook failure:* the order-history page was refactored from one SQL join into a loop calling the catalogue service once per order line — the classic N+1 problem moved across a network. In-process it cost 40 µs per call; over the network it costs 1.8 ms. A 60-line order went from 2.4 ms to 108 ms, and at p99 (where each call is 15 ms) to 900 ms. Nothing is "broken"; the page is just unusable.

**Fallacy 3 — Bandwidth is infinite.**
*Belief:* payload size does not matter.
*BookNook failure:* the recommendations service returns the full book record — description, 40 reviews, base64 cover thumbnail — for each of 24 recommendations, 180 KB per homepage render. At 3,000 renders/sec that is 540 MB/s of internal east-west traffic, saturating the instance's 5 Gbps NIC and degrading every *other* service sharing that host. The fix is field selection, not a bigger NIC.

**Fallacy 4 — The network is secure.**
*Belief:* traffic inside the VPC is trusted, so internal calls need no authentication.
*BookNook failure:* a compromised log-shipping container in the same subnet calls `POST /internal/refunds` directly, which has no auth because "it's internal". This is why mutual TLS and per-service identity exist, and why zero-trust networking assumes the inside is hostile.

**Fallacy 5 — Topology doesn't change.**
*Belief:* the set of hosts and their addresses is stable.
*BookNook failure:* an engineer hardcodes the inventory service's IP in a config file because DNS "was flaky once". Autoscaling (Lesson 38) replaces that instance at 02:00; the IP is reassigned to an unrelated service in another account. Checkout now posts stock decrements into a stranger's API. This is the motivation for *Topic 24 — Service discovery and configuration management*.

**Fallacy 6 — There is one administrator.**
*Belief:* someone understands and controls the whole system.
*BookNook failure:* the search team raises their database connection pool from 20 to 200 to fix their own timeouts. The shared Postgres cluster has `max_connections = 400`. At the next traffic peak the orders service cannot open a connection and checkout fails completely — caused by a change made by a team that never touched checkout's code and was never consulted.

**Fallacy 7 — Transport cost is zero.**
*Belief:* moving data between services is free.
*BookNook failure:* two meanings, both real. In CPU: serialising 180 KB of JSON 3,000 times a second consumes roughly a full core per 1,500 renders/sec — the service is spending more time on JSON than on business logic. In money: cross-AZ traffic is typically billed around $0.01/GB *each way* in major clouds, so a chatty service pair exchanging 500 MB/s across AZs costs on the order of $0.01 × 500 × 2 × 86,400 / 1,000 ≈ $864/day in transfer fees alone.

**Fallacy 8 — The network is homogeneous.**
*Belief:* all links and all nodes behave alike.
*BookNook failure:* the search service runs on hosts with 25 Gbps NICs; the legacy pricing service sits behind a 1 Gbps link in a colocation facility connected by VPN with an MTU of 1400. Large responses fragment, path-MTU discovery is blocked by a firewall dropping ICMP, and requests above a certain payload size hang until timeout — intermittently, for one dependency only, in a way that never reproduces in staging.

### 4.3 The third outcome: the heart of the matter

This is the single most important idea in Lesson 64.

Inside one process, a call has **two** outcomes:

```
result = charge_card(order)      # either returns a value
                                 # or raises an exception
```

Over a network, a call has **three**:

```
success   — you sent it, it ran, you got the reply
failure   — you sent it, it definitely did not run (e.g. connection refused by a live host)
UNKNOWN   — you sent it, and you have no idea whether it ran
```

`UNKNOWN` arises from timeouts, connection resets, dropped replies, and client crashes mid-call. It is not a rare edge case: at any meaningful scale it happens continuously. And it is **irreducible** — no protocol can eliminate it, because the acknowledgement of the acknowledgement can also be lost (this is the Two Generals problem, and it is proved impossible, not merely unsolved).

Since you cannot eliminate `UNKNOWN`, you must make it *harmless*. There are exactly three strategies, and every real system uses a mixture:

1. **Make the operation idempotent** so that retrying under uncertainty is safe. Attach a client-generated idempotency key; the server records the key with the result and returns the stored result on a repeat (Lesson 17). This converts "unknown" into "retry until known".
2. **Make the operation a query you can re-ask** — "what is the status of order 8812?" — so the caller can resolve its uncertainty by reading rather than re-writing.
3. **Make the operation asynchronous and durable** — write the intent to a log or queue that you *can* confirm, and let a consumer apply it with at-least-once delivery plus deduplication (*Topics 10 and 11*).

An engineer who has internalised the third outcome writes different code: every mutating endpoint takes an idempotency key, every client has a timeout and a bounded retry with jittered backoff (Lesson 17), and every retry path is asked "what happens if this runs twice?" before it is merged.

### 4.4 Emergent complexity: why N services cost far more than N times

A monolith with 14 modules has 14 things to run: one. A system of 14 services has 14 deployments, 14 health-check configurations, 14 dashboards, 14 on-call runbooks — and, far worse, a quadratic number of *interactions*.

The arithmetic: with N services, the number of unordered pairs that could possibly interact is

```
pairs = N × (N − 1) / 2
```

| N services | Possible pairwise interactions |
|---|---|
| 2 | 1 |
| 5 | 10 |
| 10 | 45 |
| 20 | 190 |
| 50 | 1,225 |
| 100 | 4,950 |

Going from 10 services to 20 doubles your headcount of services and **quadruples** the interaction surface. Each interaction that is actually used needs a timeout, a retry policy, a circuit breaker (*Topic 14*), a contract, a version-compatibility plan, an authorisation rule and a dashboard. Teams routinely plan for the linear cost (14 services, 14 pipelines) and are ambushed by the quadratic one.

Three specific emergent behaviours that do not exist in a monolith:

- **Retry storms.** Service A retries 3 times into B, which retries 3 times into C. One C failure becomes 9 C requests. Three layers deep that is 27×. Load *increases* exactly when the system is least able to serve it. Mitigations: retry budgets (cap retries at a percentage of traffic), retrying only at the edge, and circuit breakers (*Topic 14*).
- **Metastable failure.** The system has two stable states — healthy, and a congested state that sustains itself even after the original trigger is gone, because queues are full and every request times out after consuming resources. Recovery requires *shedding* load, not adding capacity (*Topic 13 — Backpressure and load shedding*).
- **Correlated failure.** The thing you believed was N independent components turns out to share one dependency — one config service, one DNS zone, one certificate, one deploy pipeline — so the "independent" failure probabilities in your availability maths (Lesson 15) were never independent, and your calculated 99.99% was fiction.

### 4.5 Why testing distributed systems is genuinely hard

A unit test runs one deterministic sequence. A distributed system's behaviour depends on the *interleaving* of concurrent events plus the *timing* of failures, and both are controlled by the environment, not by your test. The bug that matters is "node B crashed in the 4 ms window between A's commit and A's acknowledgement", and you cannot hit that window by running the test again.

What actually helps, in increasing order of cost:

- **Fault injection** — deliberately returning errors, adding latency, and dropping connections at library or proxy level (service-mesh fault injection, `tc netem` at the kernel level). Cheap, and finds the majority of first-order bugs such as missing timeouts.
- **Chaos engineering** — running controlled failure experiments against production or production-like environments (terminating instances, isolating an availability zone) with a hypothesis, a blast-radius limit and an abort condition. Netflix's Chaos Monkey popularised it. Its value is less in finding bugs than in proving your detection and recovery actually work; a recovery procedure never executed is a recovery procedure that does not work.
- **Deterministic simulation testing** — run the entire system single-threaded inside a simulator with a virtual clock and a pseudo-random scheduler, so that message delays, reorderings and crashes are driven by a seed. A failing run is reproducible *exactly* by replaying the seed, which converts "unreproducible once a month" into "reproducible in 200 ms". FoundationDB is the canonical example; TigerBeetle and others have followed. It is the most powerful technique available, and it requires designing the system for it from the start (all I/O and time behind injectable interfaces).
- **Property-based and linearizability checking** — generate random operation histories against a running cluster under injected faults, then check the recorded history against a model for violations of the consistency guarantee you claim (Lesson 61). Jepsen is the best-known implementation, and has found correctness bugs in most major distributed databases.
- **Formal methods** — specify the protocol in **TLA+** (a specification language by Lamport) or **P**, and have a model checker exhaustively explore reachable states for invariant violations. This checks the *design*, not the code, which is exactly where consensus and replication bugs live. Used at AWS for core services such as S3 and DynamoDB; worth the investment for a protocol you will depend on for a decade, usually not worth it for a CRUD service.

Note the pattern: none of these techniques prove the absence of bugs. They move you from "we hope" to "we have evidence".

## 5. Request / data flow

A single BookNook checkout, after the monolith has been split. Follow where `UNKNOWN` can appear.

1. The browser sends `POST /checkout` with an `Idempotency-Key` header generated client-side (a UUID, stable across the user's retries).
2. DNS resolves `api.booknook.com`; the request reaches the load balancer, which selects a healthy API gateway instance (Lessons 21, 23, 24).
3. The gateway authenticates the session, assigns a **trace ID** (*Topic 25*), and forwards to the **Order service** with a 2,000 ms deadline, propagating the remaining budget in a header so downstream callees know how much time is left.
4. Order service writes an `orders` row with status `PENDING` in its own Postgres, inside a local ACID transaction (Lesson 49). This write is durable and unambiguous — it is local.
5. Order service calls **Inventory service**: `POST /reservations {isbn, qty, idempotency_key}` with a 300 ms timeout.
6. Inventory decrements available stock in its own database and returns `201 Created` with a reservation ID. **The reply is lost in the network.** Order service sees a timeout. *This is the third outcome.* Stock is reserved; the caller does not know it.
7. Order service retries step 5 with the **same** idempotency key. Inventory finds the key in its `processed_requests` table and returns the *stored* `201` response rather than reserving a second time. Uncertainty resolved without corruption. Had the key been omitted, two units would now be reserved for one order.
8. Order service calls **Payment service**, which calls an external PSP (payment service provider) — a third party, outside your failure domain, with its own availability and its own outages.
9. The PSP call takes 1,400 ms. The remaining deadline from step 3 is 1,100 ms, so the gateway's deadline fires first and it returns `504` to the browser — *while the charge is still in flight*. The user believes checkout failed. The charge may succeed seconds later.
10. Because step 9 is foreseeable, the order's true state is resolved asynchronously: Payment service records the attempt durably before calling the PSP, and a reconciliation worker polls the PSP for the status of any attempt left in-flight, then emits `PaymentSucceeded` or `PaymentFailed`.
11. Order service consumes that event and transitions the order to `CONFIRMED` or releases the reservation via a compensating action (*Topic 21 — Saga pattern*).
12. The user is informed of the true outcome by email and by the order-status page, because the synchronous HTTP response could not carry the truth.

The lesson of this flow: **the synchronous response was not authoritative.** In a distributed system the request/response channel is a convenience; the durable record plus asynchronous reconciliation is the source of truth.

## 6. ASCII diagram

```
             SINGLE PROCESS (Phases 1-3 mental model)
   ┌──────────────────────────────────────────────────┐
   │  checkout() ──call──> reserve_stock()            │
   │        ▲                    │                    │
   │        └───return / throw───┘                    │
   │                                                  │
   │  Two outcomes only. Shared memory. One clock.    │
   │  If the process dies, EVERYTHING dies together.  │
   └──────────────────────────────────────────────────┘

             DISTRIBUTED SYSTEM (Phase 4 reality)
   ┌──────────┐   request    ┌──────────────┐
   │  Order   │ ───────────► │  Inventory   │
   │ service  │              │   service    │
   │          │ ◄─ ─ ─ ─ ─ ─ │              │
   └──────────┘   reply      └──────────────┘
        │            ▲   ▲            │
        │            │   │            │
        │     ┌──────┴───┴──────┐     │
        │     │  THE NETWORK    │     │
        │     │  may: drop      │     │
        │     │       delay     │     │
        │     │       duplicate │     │
        │     │       reorder   │     │
        │     └─────────────────┘     │
        │                             │
   ┌────┴─────┐                 ┌─────┴────┐
   │ clock A  │                 │ clock B  │   different time!
   │ 10:00:00 │                 │ 09:59:58 │   (Lesson 66)
   └──────────┘                 └──────────┘

   THREE OUTCOMES, not two:

   Order ──X──► Inventory      request lost     → did NOT run
   Order ─────► Inventory ──X  reply lost       → DID run, caller unsure
   Order ─────► Inventory      slow (GC pause)  → still running, caller unsure
                  ▲
                  └── the caller's view of the last two is IDENTICAL
                      ⇒ "UNKNOWN" ⇒ must be idempotent (Lesson 17)
```

## 7. Detailed example

### 7.1 BookNook: the first week after the split

BookNook ran as one Rails monolith against one Postgres for four years. Growth and a 22-person engineering team forced a split into five services: **Catalogue**, **Inventory**, **Order**, **Payment**, **Notification**. The code was not rewritten — modules became services, and in-process method calls became HTTP calls. Nothing else changed. Here is what happened in week one, and why each item is a direct consequence of §4.

**Day 1 — The homepage got 6× slower at p99.** The homepage had always called `Catalogue.get_books(ids)` in a loop. In-process, each iteration was a 50 µs hash lookup over a warm cache. Over HTTP, each iteration is a 1.5 ms median / 18 ms p99 call. The homepage shows 30 books. Median went 1.5 ms → 45 ms; p99 went from 3 ms to 540 ms, because the p99 of a sequence of 30 calls is dominated by the chance that *at least one* is slow — with a 1% chance per call, the probability that none of 30 calls is slow is 0.99³⁰ = 0.74, so **26% of homepage renders now contain at least one p99-tail call** (this is tail amplification, Lesson 16). *Fallacy 2.*

**Day 2 — Double charges.** 41 customers were billed twice. Root cause: Order's HTTP client had a default 5-second timeout and `retries = 3`, and Payment's charge endpoint had no idempotency key, because when it was a method call a timeout was impossible. *Fallacy 1, plus the third outcome.*

**Day 3 — Phantom stock.** Inventory's database showed 0 units of a bestseller while the warehouse had 140. Orders that timed out after reserving stock never released it, because the "release" path only ran in the `rescue` block of the caller — and the caller had *also* crashed. Reservations need their own expiry (a **lease**, *Topic 15*), not a cleanup that depends on the crashed party.

**Day 4 — A retry storm took down Catalogue.** A single slow query made 2% of Catalogue calls exceed 300 ms. Order retried, the gateway retried Order, and the browser's client library retried the gateway. Measured at Catalogue: incoming request rate rose from 4,200/s to 31,000/s *while it was already degraded*. It fell over completely. Removing all but edge-level retries and adding a 10% retry budget brought the peak to 4,600/s. *Emergent complexity, §4.4.*

**Day 5 — "It works in staging."** Staging ran all five services on one host over loopback, with sub-millisecond latency and no packet loss. Every latency- and failure-related bug above was invisible there. The team added fault injection to staging (50 ms added latency, 0.5% injected 503s on every internal call) and found four more missing timeouts within a day. *§4.5.*

The uncomfortable summary: BookNook's functional behaviour was unchanged, the business logic was byte-for-byte identical, and yet the system became dramatically less reliable — because the *same code* now ran in an environment with partial failure, unbounded latency and no global clock.

### 7.2 Contrasting example: an airline departure-control system

Consider a different domain with the same physics. An airline's departure control system (DCS) runs check-in desks, bag-drop kiosks, gate scanners and a central passenger-record store across hundreds of airports.

The contrast with BookNook is instructive in three ways:

- **The network is far worse.** Airport links include satellite backhaul at remote stations with 600 ms round trips and multi-minute outages. BookNook's "unbounded latency" is theoretical most of the time; the DCS's is routine. The design response is *offline-first*: desks hold a local replica of the flight's passenger list and can board passengers with no connectivity at all, reconciling afterwards.
- **The consistency requirement is different in each direction.** Two desks must never board the same seat twice (a safety and legal problem), but the loyalty-points update can be hours late. So the system is split by criticality: seat assignment is coordinated through a single authoritative owner per flight (an application of *Topic 16 — Leader election*), while points are an eventually consistent asynchronous stream (Lesson 61).
- **The cost of being wrong is asymmetric and non-refundable.** A double charge at BookNook is reversed with a refund and an apology. A passenger boarded against a security hold cannot be un-boarded once the aircraft is airborne. When the cost of an incorrect action is irreversible, the correct response to `UNKNOWN` is **not** "retry" — it is "block and escalate to a human". BookNook retries; the DCS stops and calls a supervisor.

That third point generalises: *the right behaviour under uncertainty is determined by the reversibility of the action*, not by a general rule. Reversible actions should be retried with idempotency keys; irreversible actions should be made reversible (reservations, holds, two-phase intents) or deferred to a human.

## 8. Code or configuration example

The following is **illustrative teaching code, not production-ready**. It omits connection pooling, structured logging, metrics, authentication, configuration, tracing propagation, and error taxonomy, and its storage is an in-memory dict rather than a durable transactional store. Its only purpose is to make the three outcomes visible.

```python
import time, uuid, random
import requests  # illustrative HTTP client

# ---------------------------------------------------------------------------
# CALLER SIDE: how to survive the third outcome (UNKNOWN)
# ---------------------------------------------------------------------------

class Unknown(Exception):
    """Raised when we do not know whether the remote side executed the work.

    This class existing AT ALL is the point of the lesson. In single-process
    code there is no equivalent: you get a value or an exception, never this."""

def reserve_stock(isbn, qty, idempotency_key, deadline_ms=800):
    # The idempotency key is generated ONCE by the caller for this logical
    # operation, and reused for EVERY retry. Generating a new key per attempt
    # would defeat the whole mechanism - this is the #1 implementation bug.
    attempt = 0
    started = time.monotonic()          # monotonic, never wall clock, for
                                        # durations (see Lesson 66, section 4)
    while True:
        attempt += 1
        # Budget-aware timeout: never wait longer than the deadline we were
        # given, so we do not blow our own caller's deadline (deadline
        # propagation). A fixed per-attempt timeout would violate this.
        elapsed_ms = (time.monotonic() - started) * 1000
        remaining_ms = deadline_ms - elapsed_ms
        if remaining_ms <= 0:
            raise Unknown("deadline exhausted; state of reservation unknown")

        try:
            resp = requests.post(
                "http://inventory.internal/reservations",
                json={"isbn": isbn, "qty": qty},
                # The server uses this header to deduplicate. Without it,
                # every retry below is a second real reservation.
                headers={"Idempotency-Key": idempotency_key},
                timeout=remaining_ms / 1000.0,
            )
        except requests.Timeout:
            # OUTCOME 3: UNKNOWN. The request may have been fully processed.
            # We may retry ONLY because the key makes the retry safe.
            if attempt >= 3:
                raise Unknown("timed out 3x; reservation may or may not exist")
            # Exponential backoff with full jitter (Lesson 17). The jitter is
            # not decoration: without it, every caller retries in lockstep and
            # creates a synchronised thundering herd on the recovering server.
            sleep_s = random.uniform(0, min(0.4, 0.05 * (2 ** attempt)))
            time.sleep(sleep_s)
            continue
        except requests.ConnectionError as e:
            # "Connection refused" from a live host is OUTCOME 2: a definite
            # non-execution, safe to retry freely. "Connection reset" mid-flight
            # is OUTCOME 3. Most client libraries blur these together, which is
            # why careful code inspects the specific error rather than catching
            # a broad base class.
            if attempt >= 3:
                raise Unknown(f"connection error after retries: {e}")
            time.sleep(random.uniform(0, 0.2))
            continue

        if resp.status_code in (200, 201):
            return resp.json()                 # OUTCOME 1: success
        if resp.status_code == 409:
            raise ValueError("insufficient stock")   # OUTCOME 2: definite,
                                                     # business failure; do NOT
                                                     # retry - the answer will
                                                     # not change
        if 500 <= resp.status_code < 600:
            # A 500 is ambiguous: the handler may have committed and then
            # failed while serialising the response. Treat as UNKNOWN, retry
            # under the key.
            if attempt >= 3:
                raise Unknown(f"server error {resp.status_code}")
            time.sleep(random.uniform(0, 0.3))
            continue
        raise RuntimeError(f"unexpected status {resp.status_code}")


# ---------------------------------------------------------------------------
# SERVER SIDE: what makes the caller's retry safe
# ---------------------------------------------------------------------------

PROCESSED = {}   # key -> stored response. In production this MUST be a durable
                 # table written in the SAME transaction as the business effect,
                 # or the two can diverge on crash. An in-memory dict loses all
                 # deduplication on restart - exactly when retries are arriving.

def handle_reservation(idem_key, isbn, qty):
    if idem_key in PROCESSED:
        # The work already happened. Return the ORIGINAL response, not a fresh
        # one: the caller must be unable to tell a retry from the first call.
        return PROCESSED[idem_key]

    # BEGIN TRANSACTION  (pseudo - the two writes below must be atomic)
    reservation = {"id": str(uuid.uuid4()), "isbn": isbn, "qty": qty}
    #   1. decrement stock
    #   2. insert idem_key -> response
    # If these are not atomic, a crash between them re-executes the decrement
    # on retry and you have silently double-reserved.
    PROCESSED[idem_key] = reservation
    # COMMIT
    return reservation
```

A configuration-level expression of the same ideas, as a service-mesh style policy. Again **illustrative**, not a complete manifest:

```yaml
# Per-dependency resilience policy. Every entry here is a decision that did not
# exist when this call was an in-process method invocation.
destination: inventory.internal
timeout: 300ms              # chosen from the p99.9 of the callee, NOT a guess;
                            # too low = false failures, too high = you hold
                            # threads and blow your own caller's deadline
retries:
  attempts: 2               # total 3 calls max. Multiply this down the chain:
                            # 3 layers x 3 attempts = 27x amplification
  perTryTimeout: 120ms      # must be < timeout/attempts or retries never fire
  retryOn: connect-failure,gateway-error
                            # deliberately NOT retrying on 4xx: the answer will
                            # not change, and retrying wastes the callee's
                            # capacity during an incident
  budget:
    percentCanRetry: 10     # hard cap: retries may never exceed 10% of traffic.
                            # This is the single most effective defence against
                            # the retry storms described in section 4.4
outlierDetection:           # eject hosts that fail, so least-connections does
  consecutive5xx: 5         # not preferentially route INTO a fast-failing host
  baseEjectionTime: 30s     # (the "black hole" problem from Lesson 23)
```

## 9. Numerical example

**Question: how many distinct global states can a modest BookNook deployment be in?**

This computes the *combinatorial explosion* that makes exhaustive testing impossible.

**Assumptions (all stated explicitly):**
- The system has **8 services** (Gateway, Catalogue, Inventory, Order, Payment, Notification, Search, Recommendations). This is small — real systems have hundreds.
- Each service is modelled with **3 states**: `healthy`, `degraded` (responding slowly), `failed`. This is a simplification; reality is continuous.
- Any service may be unable to reach any other, independently. Each **link** is modelled with **2 states**: `up`, `down`. Asymmetric reachability (A sees B but B does not see A) is ignored here, which *understates* the real number.
- Clock skew, in-flight message contents and data divergence are all ignored, which understates the real number enormously.

**Step 1 — Node state combinations.**
```
3 states per service, 8 services, independent
= 3^8
= 3 × 3 × 3 × 3 × 3 × 3 × 3 × 3
= 6,561
```

**Step 2 — Number of links.**
```
links = N × (N − 1) / 2
      = 8 × 7 / 2
      = 56 / 2
      = 28 links
```

**Step 3 — Link state combinations.**
```
2 states per link, 28 links
= 2^28
= 268,435,456
```

**Step 4 — Total combined states.**
```
total = node states × link states
      = 6,561 × 268,435,456
      = 1,761,205,026,816
      ≈ 1.76 × 10^12
```

**Step 5 — How long would exhaustive testing take?**
Assume (optimistically) that one state can be set up, exercised and verified in **1 second**, and that you can run **1,000 such tests in parallel**.
```
seconds needed = 1.76 × 10^12 / 1,000
               = 1.76 × 10^9 seconds
years          = 1.76 × 10^9 / (60 × 60 × 24 × 365)
               = 1.76 × 10^9 / 31,536,000
               ≈ 55.8 years
```

**Step 6 — Sensitivity: what if you add just two more services?**
```
N = 10:  node states = 3^10 = 59,049
         links       = 10 × 9 / 2 = 45
         link states = 2^45 ≈ 3.52 × 10^13
         total       ≈ 59,049 × 3.52 × 10^13 ≈ 2.08 × 10^18
```
Adding 2 services to 8 — a 25% increase in headcount — multiplied the state space by roughly **1.2 million times**.

**Interpretation.** Three conclusions follow directly, and they are the practical payoff of the arithmetic:

1. **Exhaustive testing is not merely expensive, it is impossible.** Any strategy based on "enumerate the failure modes" fails at N = 8. This is why the techniques in §4.5 are *sampling* strategies (randomised fault injection, seeded simulation) or *symbolic* strategies (model checking with abstraction), never enumeration.
2. **Reducing N is worth more than any testing investment.** Going from 10 services to 8 removed 10⁶ times more state space than any test suite could ever cover.
3. **You must design so that most of the state space is equivalent.** If every dependency failure leads to the same handled behaviour ("circuit opens, serve cached/degraded result"), then 2²⁸ link states collapse into a handful of *behavioural* classes. That collapse — not test coverage — is what makes distributed systems operable. This is the real argument for uniform resilience policies.

## 10. Trade-offs

| Dimension | What distribution gives you | What it charges you |
|---|---|---|
| Scale | Horizontal growth past one machine's limits (Lesson 18) | Coordination cost; some operations get *slower* as you add nodes |
| Availability | Independent failure domains; one node's loss is survivable (Lesson 15) | More components means more things that *can* fail; availability improves only if failures are genuinely independent and failover actually works |
| Latency | Compute near the user; parallel fan-out | A network hop costs ~0.5–2 ms within an AZ, ~1–3 ms cross-AZ, 10–200 ms cross-region, versus ~0.0001 ms for a local call |
| Correctness | Each service owns a smaller, clearer dataset | No cross-service ACID transaction; you trade it for sagas and eventual consistency (*Topics 19–21*) |
| Team velocity | Independent deploys; smaller blast radius per change | Cross-service changes need coordinated, versioned, backwards-compatible rollouts — slower, not faster |
| Debuggability | Smaller codebases, clearer ownership | A stack trace no longer spans the failure; you need distributed tracing to reconstruct one request (*Topic 25*) |
| Cost | Right-size each component independently | Serialisation CPU, cross-AZ transfer fees, per-service infrastructure, and an on-call rota per team |
| Testing | Services testable in isolation | End-to-end correctness untestable by enumeration (§9); needs fault injection and simulation |

**The honest summary:** distribution is a trade of *simplicity* for *scale and isolation*. Whether that trade is worth it depends on four specific things, not on taste: (a) whether your load actually exceeds one machine, (b) whether your availability target actually exceeds one machine's reliability, (c) whether your users are far enough away for latency to matter, and (d) whether your team is large enough that one deploy pipeline is a bottleneck. If none of the four is true, the trade is a straight loss.

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| Lost reply after a successful write (`UNKNOWN`) | "Payment failed" message, then a charge appears on their statement | Reconciliation job compares local attempt records against the PSP; alert on count of attempts stuck in-flight > 5 min | Retry under the original idempotency key returns the stored result; or the reconciler emits the true outcome and the order state machine corrects itself |
| Retry storm amplifying a minor fault | Site-wide timeouts, far worse than the original 2% error rate | Request rate to a dependency rises while its success rate falls — the signature is inbound RPS *up* and goodput *down* simultaneously | Retry budget caps retries at 10% of traffic; circuit breaker opens (*Topic 14*); load shedding drops excess at the edge (*Topic 13*) |
| N+1 calls across a service boundary | Page loads in 900 ms at p99 instead of 3 ms; no errors at all | Trace shows 30+ spans to one dependency within a single request; p99 latency alert on the endpoint | Replace the loop with a batch endpoint (`GET /books?ids=...`); add a local cache for hot IDs (Lesson 27) |
| Hardcoded IP of a recycled instance | Orders silently fail or, worse, succeed against the wrong system | Error-rate spike on one endpoint; or no detection at all until data corruption is noticed days later | Service discovery with health-checked registration (*Topic 24*); ban literal IPs in config via lint; mutual TLS so a wrong peer fails the handshake instead of accepting the call |
| Shared dependency exhausted by another team (connection pool) | Checkout returns 500 for everyone during peak; unrelated features fine | Database `too many connections` errors; connection-count dashboard crosses threshold | Per-service connection quotas, bulkheads (*Topic 14*), and a connection pooler; longer term, separate databases per service |
| Correlated failure of "independent" replicas (shared config push) | Total outage despite an N+2 redundant design | All replicas unhealthy simultaneously — simultaneity itself is the signal | Staged config rollout with automatic rollback on error-budget burn (*Topic 26*); treat config as code with canaries |
| Cross-AZ network degradation (not a clean break) | Intermittent slow checkouts, only for some users, impossible to reproduce | Per-AZ latency percentile breakdown; comparing success rate by source-AZ/destination-AZ pair | Shift traffic away from the degraded AZ (zonal evacuation); retries with hedging to another AZ |
| Staging passes, production fails | Bugs reach users every release | Divergence between staging topology (one host, loopback) and production | Inject latency and faults into staging; run the same resilience policies in both; gate releases on a chaos experiment |

## 12. Alternatives

The alternative to a distributed system is almost never "nothing" — it is a *less* distributed arrangement. The real options:

| Option | What it is | Max practical scale (typical) | Operational burden | When it is the right answer |
|---|---|---|---|---|
| **Single process, single machine** | One app, one DB, one box | Tens of millions of requests/day on a large instance; limited by one machine's RAM/CPU/IO | Lowest. One deploy, one log, one stack trace | Prototypes, internal tools, and the overwhelming majority of businesses before product-market fit |
| **Modular monolith** | One deployable artefact with enforced internal module boundaries and separate schemas | Same machine limits, but *refactorable* into services later at low cost | Low. One pipeline, but discipline required to keep boundaries | The correct default for most teams up to roughly 30–50 engineers (*Topic 23*) |
| **Vertical scaling** | Buy a bigger machine | Cloud instances up to ~400 vCPU / 24 TB RAM exist; a surprising number of workloads fit | Low, until you hit the ceiling — then the cliff is abrupt | When growth is predictable and the ceiling is years away; cheapest engineering-hours-per-unit-scale by a wide margin |
| **Monolith + read replicas + cache** | Still one write path, distributed reads | Read-heavy workloads scale 10–50× this way | Moderate. Adds replication lag as a correctness concern (Lesson 31) | Read:write ratios above roughly 10:1, which is most consumer products |
| **Sharded monolith** | One codebase, data partitioned by tenant/key (Lesson 33) | Very high; this is how many large systems actually scale | Moderate-high. Resharding and cross-shard queries are the pain | Natural partition key exists (tenant ID, user ID) and cross-partition queries are rare |
| **Service-oriented split (coarse)** | 3–10 services on genuine business boundaries | Very high | High. Everything in this lesson applies | Distinct scaling profiles or distinct availability requirements per domain |
| **Microservices (fine-grained)** | Dozens to hundreds of services | Highest | Highest. Requires platform investment: mesh, tracing, CI/CD, discovery | Large organisations where team autonomy is the binding constraint, *and* a platform team exists to absorb the cost |
| **Serverless functions** | Managed per-request execution | High, elastic | Shifts burden rather than removing it: cold starts, per-invocation cost, limited execution time, same distributed-systems physics | Spiky, event-driven, stateless workloads |

The important observation: these are points on a continuum, and **you should move along it one step at a time, pulled by a measured constraint**, not jump to the right-hand end because of an architecture diagram.

## 13. When to use it

Distribute when one of these is demonstrably true:

1. **A measured resource ceiling on one machine.** You have profiled, optimised the obvious things, and the largest available instance still cannot hold the working set or sustain the write rate. *Evidence required:* a saturation graph, not an intuition.
2. **An availability target that exceeds single-machine reliability.** A 99.95% monthly target allows 21.9 minutes of downtime per month; a single machine's unavoidable kernel patching and reboots alone can exceed that. Redundancy across failure domains is then mandatory (Lesson 15).
3. **Geographically distributed users with a latency SLO.** If your Sydney users need sub-200 ms page loads and your servers are in Dublin, no software change can defeat the 170 ms speed-of-light round trip; only regional presence can (Lesson 39).
4. **Genuinely divergent scaling or availability profiles per domain.** BookNook's search index needs 40 memory-heavy nodes and can tolerate being stale; the order ledger needs strong consistency and tiny storage. Forcing both into one deployment means paying search's resource profile for the ledger, and the ledger's consistency constraints for search.
5. **Regulatory or data-residency boundaries.** EU customer data must stay in the EU; that is a legal constraint that forces physical distribution regardless of engineering preference.
6. **Organisational scale with deployment contention.** When the merge queue is the bottleneck and a single failed test blocks eight teams, splitting the artefact buys real throughput — provided you also fund the platform work it requires.

## 14. When not to use it

Do **not** distribute when:

1. **You have not yet hit a measured limit.** "We might need to scale" is a prediction, and most such predictions are wrong. A modular monolith (*Topic 23*) preserves the option to split later at a fraction of the cost of splitting now and discovering the boundaries were wrong.
2. **Your team is smaller than your service count.** If 6 engineers own 20 services, nobody owns anything: each service's on-call, dependency upgrades, certificate rotations and security patches still exist, and 20 × that burden divided by 6 people is how teams stop shipping features entirely.
3. **The domain boundaries are not yet understood.** Service boundaries are expensive to move — a boundary inside a monolith is a refactor; a boundary between services is a data migration plus a coordinated multi-team release. Split only boundaries you have already proven stable for months.
4. **You need multi-entity ACID transactions and cannot tolerate eventual consistency.** If your core invariant genuinely requires "these three rows change atomically or not at all", keeping them in one database is vastly cheaper and more correct than a saga with compensations (Lessons 49, 59, 60; *Topics 19–21*).
5. **The system is latency-critical and chatty.** If the workload requires thousands of fine-grained interactions per user action, turning each into a network call converts microseconds into milliseconds and destroys the latency budget.
6. **You lack the operational foundation.** Without centralised structured logging, distributed tracing, per-service dashboards, automated deploys and a real on-call process, a distributed system is not observable, and an unobservable distributed system is not debuggable. Build the platform first, or do not split.

## 15. Common mistakes

**Beginner mistakes**

- **Treating a remote call like a local call.** Writing `inventory.get(isbn)` inside a loop because it *reads* like a method call. Every network boundary needs a timeout, a retry policy, a fallback and a batch variant.
- **No timeout at all.** Many HTTP clients default to infinite. One slow dependency then consumes every thread in your pool and your service dies of thread starvation while using 2% CPU.
- **Retrying non-idempotent operations.** Retrying `POST /charge` without a key is how double charges happen. The rule: *if you retry it, it must be idempotent; if it is not idempotent, make it so before adding a retry.*
- **Generating a fresh idempotency key per attempt.** This makes the key useless while giving the comforting appearance of correctness. The key identifies the *logical operation*, not the attempt.
- **Assuming the happy path in error handling.** Writing `except: pass` around a network call, so a failure becomes silent data loss rather than a visible error.
- **Believing staging proves anything about failure behaviour.** Loopback networking has no packet loss, no partitions and sub-millisecond latency.
- **Splitting by technical layer instead of business capability.** "Database service", "API service", "business logic service" means every feature change touches all three and requires a three-way coordinated deploy — all the costs of distribution, none of the independence.

**Experienced-engineer mistakes**

- **Assuming failure independence in availability maths.** Computing 1 − (1 − 0.99)³ = 99.9999% for three replicas, while all three share one config service, one deploy pipeline, one certificate and one AZ. The real figure is dominated by the correlated component, and is often *worse* than a single node because there are now more ways to fail.
- **Adding retries at every layer.** Each layer's retry seems locally prudent. Multiplied down a 4-layer chain with 3 attempts each, one user request can become 81 requests at the bottom. Retry at the edge, or budget retries explicitly.
- **Using a distributed lock without fencing.** A lock acquired from a lock service does not prevent a GC-paused holder from waking up after expiry and writing anyway. You need a monotonic fencing token enforced at the *storage* layer (Lesson 65 §4.6, and *Topic 15*).
- **Ordering events by wall-clock timestamp.** Clock skew silently reorders writes and last-write-wins discards the newer value. This one has no visible error — only wrong data (Lesson 66).
- **Treating "exactly-once delivery" as achievable.** It is not, at the transport layer. What is achievable is at-least-once delivery plus idempotent processing, which *appears* exactly-once at the effect level (*Topic 10*).
- **Optimising the median and ignoring fan-out tail amplification.** With 30 parallel calls at a 1% p99 rate, 26% of requests hit at least one tail call (§7.1). Improving the median does nothing for this; only reducing fan-out, hedging requests, or tightening the tail does (Lesson 16).
- **Building the distributed architecture the organisation will have in three years.** The organisation changes; the architecture ossifies. Build for a 2× horizon, with boundaries that are cheap to move.
- **Skipping the "what happens if this runs twice?" review.** The most valuable single question in a distributed systems code review, and the one most often not asked.

## 16. Interview perspective

This topic rarely appears as its own question. It appears as the *lens* the interviewer uses to judge every answer in a system design interview. When you draw a box and an arrow between two boxes, a strong interviewer is silently asking: does this candidate know what that arrow costs?

**How to demonstrate competence:**

- **Name the third outcome explicitly.** When you add a service-to-service call, say out loud: "this call has three outcomes — success, definite failure, and unknown. I'll handle unknown with an idempotency key and a bounded retry with jittered backoff." Very few candidates say this, and it immediately separates you.
- **Attach a timeout and a retry policy to every arrow you draw**, and justify the number from the callee's p99, not from a round figure.
- **Volunteer the simpler alternative first.** "At 500 requests/second and 80 GB of data, this fits comfortably on one primary with two read replicas. I'd start there. Here's the specific threshold — sustained write throughput above roughly 8,000 TPS, or a working set beyond what one machine can cache — at which I'd shard." This signals judgement, which is what senior interviews actually test.
- **Use the right vocabulary precisely:** partial failure, unbounded latency, idempotency, fencing token, retry budget, blast radius, failure domain, deadline propagation, backpressure.
- **When asked "what breaks first?"** give a specific component and a specific mechanism, not "the database might struggle". For example: "the connection pool on the order service, because each in-flight checkout holds a connection for the 1.4 s PSP call, and at 300 checkouts/second that needs 420 concurrent connections by Little's Law (Lesson 14) against a pool of 100."
- **Quantify.** Numbers from §9, §7.1 and Lesson 15 turn an opinion into an argument.

**Red flags interviewers watch for**

- Drawing a microservices diagram for a problem with 1,000 daily users, with no prompting and no justification.
- Saying "the network is reliable within the data centre" or "we can assume calls don't fail internally".
- Adding retries without mentioning idempotency, backoff, jitter or a budget.
- Using wall-clock timestamps to order events across nodes and not flagging clock skew.
- Claiming "exactly-once delivery" without immediately qualifying it as at-least-once plus deduplication.
- Multiplying availability figures for components that obviously share a failure domain.
- Being unable to answer "what happens if this message is delivered twice?" for any queue they drew.
- Treating a distributed transaction across three services as if it were a database transaction.
- Answering "how would you test this?" with "integration tests", with no mention of fault injection, chaos experiments or reproducibility.
- Reaching for distribution as the *first* answer to a performance problem rather than profiling, caching (Lesson 27) or vertical scaling.

## 17. Knowledge check

1. **Conceptual.** A single-process function call has two outcomes; a network call has three. Name the third, explain precisely why it cannot be eliminated by any protocol, and describe the three distinct strategies a system can use to make it harmless.

2. **Conceptual.** Three of the eight fallacies are "the network is reliable", "latency is zero" and "topology doesn't change". For each, state the *specific architectural mechanism* a mature system uses to defend against believing it, and explain why that mechanism does not simply move the problem elsewhere.

3. **Conceptual.** Explain why exhaustive testing of a distributed system is impossible rather than merely expensive, and then explain what property a *design* must have for the untestable state space to collapse into a small number of behavioural classes.

4. **Practical scenario.** BookNook's Order service calls Payment, which calls an external PSP. During an incident, customer support reports 180 customers who saw "payment failed" but were charged. The team's proposed fix is "increase the timeout from 2 s to 10 s". Explain why that fix is inadequate, identify what the real defect is, and describe a design — naming the specific components and the durable state they must hold — that makes this class of bug impossible rather than merely rarer. State explicitly where the authoritative record of the payment's outcome lives in your design.

5. **Calculation.** BookNook is considering splitting its system into 12 services instead of 8. Using the model from §9 (3 states per service, 2 states per link, links = N(N−1)/2): compute the number of node-state combinations, the number of links, the number of link-state combinations, and the total state space for N = 12. Then compute the ratio between the N = 12 total and the N = 8 total, and express how many years exhaustive testing would take at 1 state/second with 10,000 parallel runners.

---

**Progress checklist**

- [x] **Phase 1 — Foundations** (Lessons 1–17) — complete
- [x] **Phase 2 — Scaling** (Lessons 18–39) — complete
- [x] **Phase 3 — Databases & Storage** (Lessons 40–63) — complete
- **Phase 4 — Distributed Systems** (Lessons 64–92):
  - [x] 1. Why distributed systems are difficult — Lesson 64
  - [ ] 2. Partial failures and network partitions — Lesson 65
  - [ ] 3. Clock differences and ordering events — Lesson 66
  - [ ] 4. Synchronous versus asynchronous workflows — Lesson 67
  - [ ] 5. Message queues — Lesson 68
  - [ ] 6. Publish-subscribe systems — Lesson 69
  - [ ] 7. Event streams — Lesson 70
  - [ ] 8. RabbitMQ-style queues versus Kafka-style logs — Lesson 71
  - [ ] 9. Consumer groups, partitions, offsets and ordering — Lesson 72
  - [ ] 10. At-most-once, at-least-once and exactly-once semantics — Lesson 73
  - [ ] 11. Idempotent consumers and deduplication — Lesson 74
  - [ ] 12. Dead-letter queues and retry queues — Lesson 75
  - [ ] 13. Backpressure and load shedding — Lesson 76
  - [ ] 14. Circuit breakers, bulkheads and graceful degradation — Lesson 77
  - [ ] 15. Distributed locks and leases — Lesson 78
  - [ ] 16. Leader election — Lesson 79
  - [ ] 17. Quorums — Lesson 80
  - [ ] 18. Consensus and Raft — Lesson 81
  - [ ] 19. Distributed transactions — Lesson 82
  - [ ] 20. Two-phase commit — Lesson 83
  - [ ] 21. Saga pattern — Lesson 84
  - [ ] 22. Transactional outbox and change data capture — Lesson 85
  - [ ] 23. Monoliths, modular monoliths and microservices — Lesson 86
  - [ ] 24. Service discovery and configuration management — Lesson 87
  - [ ] 25. Observability: logs, metrics, traces — Lesson 88
  - [ ] 26. SLIs, SLOs and error budgets — Lesson 89
  - [ ] 27. High availability and disaster recovery — Lesson 90
  - [ ] 28. Active-passive versus active-active — Lesson 91
  - [ ] 29. RTO and RPO — Lesson 92

**NEXT LESSON: Phase 4, Topic 2 — Partial failures and network partitions**

---

# Lesson 65 — Partial Failures and Network Partitions

*Phase 4, Topic 2*

## 1. Simple definition

A **partial failure** is a failure that affects part of a system while the rest continues to work, and — crucially — is **observed differently by different observers**. Node 3 is up. But node 1 cannot reach it, node 2 can reach it slowly, its own health endpoint says 200, and the requests it *did* receive were processed. Nothing about this situation has a single description.

A **network partition** is the specific case where messages between two groups of nodes are dropped or delayed past any useful bound, so that each group can reach itself but not the other. Each side is healthy. The *link* is not. And neither side can distinguish "the other side is dead" from "the other side is slow" from "the other side is fine and my outbound path is broken".

The two ideas together produce the sentence that defines this phase:

> **On one machine, a failure is total and unambiguous. Across a network, "did it work?" has three answers — yes, no, and *unknown* — and unknown is the common case.**

This lesson is about living with the third answer. Everything else in Phase 4 (queues, consensus, sagas, outboxes) is machinery for turning *unknown* into something a business process can survive.

Terms defined before use:

- **Fail-stop**: a node halts and stays halted. Clean, detectable (eventually), and the *only* failure mode a single-machine program has to consider.
- **Fail-slow / gray failure**: a node keeps responding but is much slower, or fails only some requests, or works for some callers and not others. The dominant real-world failure and the hardest to detect.
- **Failure detector**: any mechanism that answers "is X alive?" — in practice, a heartbeat plus a timeout. It is always wrong sometimes; the only question is in which direction.
- **Timeout**: a **guess** at an upper bound on a message's round trip. There is no correct value; there is only a place on a curve between false positives and slow detection.
- **Asynchronous network model**: a network with no upper bound on message delay. Not a hypothesis — a description of reality, including the reality of a 40-second GC pause.
- **Fencing token**: a monotonically increasing number that lets a resource reject writes from a stale owner. The only robust defence against a "dead" node that comes back believing it is still in charge.

The one sentence to carry out of this lesson: **the hard part of distributed systems is not making things work; it is knowing whether they worked — and you cannot know, so you design so that not knowing is survivable.**

## 2. Why it exists

Phase 1 Lesson 17 taught retries, and every engineer leaves that lesson with a mental model of `try { call() } catch { retry() }`. That model has a hole exactly where the network is: **the exception does not tell you whether the operation happened.**

Consider BookNook's checkout calling the payment provider:

```
  ┌────────────┐   1. POST /charge (₹2,499)    ┌──────────────────┐
  │  checkout  │ ─────────────────────────────►│ payment provider │
  │  service   │                               │                  │
  │            │ ◄─────── ✗ timeout ───────────│  (charged the    │
  └────────────┘                               │   card, replied, │
        │                                      │   reply lost)     │
        ▼                                      └──────────────────┘
   "I don't know if the customer was charged."
```

Three things could be true: the request never arrived (safe to retry), it arrived and succeeded and the response was lost (retrying **charges twice**), or it arrived and is still executing (retrying races with the original). The error is identical in all three cases. **This ambiguity is not a bug in the payment provider; it is the network.**

Every mitigation you will meet in Phase 4 is a way of making one of these branches distinguishable or harmless:

| Mechanism | What it does to the ambiguity |
|---|---|
| **Idempotency key** (L17, L74) | Makes retrying safe regardless of which branch is true |
| **Message queue** (L68) | Moves the call off the request path so a timeout is not the user's problem |
| **At-least-once delivery + dedup** (L73) | Accepts duplicates and removes their effect |
| **Saga / compensation** (L84) | Undoes an operation that it turns out should not have happened |
| **Consensus** (L81) | Guarantees at most one node believes it can act |
| **Fencing token** (L78) | Makes a zombie's writes rejected at the resource |
| **Circuit breaker** (L77) | Stops asking a dependency that is not answering, so failures stay contained |
| **Observability** (L88) | Tells you what actually happened, after the fact |

The second reason this lesson exists: **partial failures are not rare and are not mostly caused by networks.** In production, the dominant forms are a slow database, a saturated thread pool, a full connection pool, a GC pause, a noisy-neighbour VM, a DNS timeout and a dependency at its rate limit. All of them are *partial*: the system is degraded, not down, and different requests see different things. A design that assumes "if the process is running, it works" fails on all of them.

## 3. Real-world analogy

Sending a courier versus making a phone call.

**In-process calls are a handshake.** You hand an envelope to a colleague standing in front of you. You *see* them take it. There is no third outcome: either they took it or they did not, and your eyes are a reliable witness.

**A network call is a letter posted into a slot.** You know you posted it. You do not know whether it was delivered, whether it was delivered twice (the postal service is at-least-once), whether it is sitting in a sorting office, or whether it fell behind a machine and will arrive in three days. You get a receipt only if the recipient chooses to send one — and **the receipt itself travels by post.**

This is why distributed systems engineers talk about **two generals**: two armies must attack at dawn, and the only communication is a messenger who may be captured. General A sends "attack at dawn". She cannot attack until she knows the message arrived. General B sends back "confirmed" — but now he cannot attack until *he* knows the confirmation arrived. The regress never terminates. **There is no protocol that makes both sides certain.** You can only make the *consequence of uncertainty* acceptable — attack anyway and accept the risk, or agree that a messenger arriving twice means the same attack (idempotency).

Now the part that makes this a design lesson rather than a philosophy lesson. Your courier/letter can also **never be confirmed because you are looking at the wrong clock**. If you allow 5 seconds and the letter arrives at 5.1, you will re-send it; the recipient now has two, and one of them is the "bad" one. Every time you choose a timeout you choose a false-positive rate, and every false positive is a duplicate. That is why the answer to "how do I know it worked?" is never "wait longer" — it is "make it safe to do twice".

## 4. Detailed technical explanation

### 4.1 The taxonomy of failure, and why only one of them is easy

| Model | The node... | Detectable? | Real-world cause |
|---|---|---|---|
| **Crash-stop** | Halts and stays halted | Yes — eventually, via a timeout | Process killed, instance terminated |
| **Crash-recovery** | Halts, then comes back with its state | Partly — it may return with stale beliefs | Restart, reboot, redeploy, container rescheduling |
| **Omission** | Drops some messages (send or receive) | Hard — looks like slowness | Packet loss, full socket buffer, half-open TCP |
| **Timing** | Responds, but later than any bound | Very hard — indistinguishable from dead | GC pause, CPU steal, swapping, thundering herd |
| **Byzantine** | Responds with lies or inconsistent answers | Specialised protocols; very expensive | Corrupted disk, bugs, compromised node |
| **Gray failure** | Fails for *some* observers, or a small fraction of requests | Hardest — every dashboard looks green | Asymmetric routing, partial service degradation, bad cache on one shard, a broken replica |

You will handle crash-stop thousands of times and never think about it. **Every interesting outage is in the bottom three rows**, and every one of them defeats the detector you wrote, because the detector asks a yes/no question about a world that has more than two answers.

### 4.2 The three outcomes, and the four ways each one lies

An RPC has exactly three outcomes, and only the middle one is easy:

```
   ┌────────────────────────────────────────────────────────────────────────┐
   │ 1. SUCCESS — response received                     safe to continue    │
   │ 2. FAILURE — explicit error received, before the   safe to retry       │
   │    work began (connection refused, 503, 4xx)                           │
   │ 3. UNKNOWN — timeout, connection reset, 5xx after  DANGEROUS:          │
   │    the work began                                  did it happen?      │
   └────────────────────────────────────────────────────────────────────────┘
```

Branch 2 is the one that makes the distinction possible at all: `connection refused` means the TCP handshake never completed, so **nothing ran**. `503 before processing` means the server rejected it. Those are safe to retry blindly. Everything else must go through an idempotency check.

The lies inside branch 3, in order of how often they cause incidents:

1. **The work succeeded; the reply was lost.** Retrying double-charges. `~30 %` of timeouts, conservatively.
2. **The work is still running.** Retrying races with the original; the two may interleave in the database. Classic cause of double-decrements.
3. **The work never started; the request is queued somewhere.**
4. **The request was retried by an intermediary** (a load balancer, a service mesh sidecar, a database driver) that you did not know about — so "your" single call is already three downstream.

### 4.3 Detection: why a timeout is a guess, and how to guess deliberately

A failure detector is a heartbeat and a threshold. The threshold's false-positive and false-negative rates trade off directly, and there is no point on the curve that is right for everyone:

```
                heartbeat every 1s, timeout T
   ┌──────────────────────────────────────────────────────────────────────┐
   │  T = 3 s   → false positives ~0.1 %/day, but MTTR detection = ~3-4 s │
   │  T = 10 s  → false positives ~0.001 %/day, MTTR detection  = ~10-11 s│
   │  T = 300 s → no false positives in practice, MTTR = 5 min (too slow) │
   └──────────────────────────────────────────────────────────────────────┘
```

The right way to pick is not vibes but a **latency distribution**: choose `T` above the honest p99.9 of the round trip *including the server's own queueing*. BookNook measures its payment provider at `p50 = 180 ms, p99 = 400 ms, p99.9 = 900 ms` on a healthy day. A `T = 1000 ms` timeout therefore produces false timeouts only when the provider is genuinely degraded — about **0.1 % of calls**, i.e. one duplicate risk per thousand. That rate is acceptable *only because* the charge is idempotent.

Two refinements worth knowing:

- **Phi-accrual failure detector** (used by Cassandra, Akka): instead of a binary threshold, it maintains a distribution of heartbeat inter-arrival times and outputs a *suspicion level* φ. You set the suspicion level, not the timeout, which adapts as conditions change. It converts "is it dead?" into "how confident am I that it is dead?", which is the honest question.
- **Never use one timeout for everything.** A timeout is a **budget**: if the user's total budget is 800 ms and you have 4 sequential dependencies, no single call may take 900 ms regardless of its own distribution. Budgets compose downward, and the last service in the chain gets the remainder.

### 4.4 The partition that is not a partition

Partitions are usually described as a network fault. In practice the most common causes are locally generated:

| Cause | Why it looks like a partition | The tell |
|---|---|---|
| **Stop-the-world GC** (40 s) | The node answers nothing for 40 s | CPU and heap graphs on the node, not the network |
| **Thread/connection pool exhaustion** | New requests queue behind saturated workers | Latency rises smoothly, then times out; CPU is *low* |
| **Full TCP accept queue** | Kernel silently drops SYN or completes then stalls | SYN retransmits, listen-queue overflow counters |
| **Hypervisor steal / noisy neighbour** | Node is alive, gets 5 % of the CPU it thinks it has | `steal time`, wildly varying service times |
| **Asymmetric routing** | A reaches B; B's replies do not reach A | One-directional failure; grep for `no route to host` on one path only |
| **DNS failure** | Half the fleet cannot resolve a dependency | Errors clustered by resolution path and by cache state |
| **Consensus failure (metastable)** | The system cannot recover even after the trigger is gone | Load stays high with a healthy dependency — the retries *are* the load |

That last row deserves its own name because it defeats the "just retry" reflex. A **metastable failure** is a self-sustaining bad state: a burst of retries fills a queue; the full queue raises latency; higher latency causes more timeouts; more timeouts cause more retries. When the original trigger ends, the retry load keeps the system down. **The only exits are load shedding and circuit breaking (L76–L77), not waiting.** Waiting is what the retry loop is already doing.

### 4.5 What actually breaks when the network splits

| Consequence | Mechanism | Where it appears in this course |
|---|---|---|
| **Split brain** | Two nodes both believe they lead and both accept writes | Fencing tokens, consensus (L78, L79, L81) |
| **Stale reads** | The minority side serves data that is behind | CAP, quorums (L59, L80) |
| **Duplicate writes** | Retries of ambiguous outcomes | Idempotency (L17, L74) |
| **Lost writes** | The minority accepted writes that are discarded on heal | Outbox, sagas, or refusing writes (L84, L85) |
| **Retry storms** | Every layer retries, multiplication across layers | Backoff, budgets, breakers (L76, L77) |
| **Queue build-up** | Consumers cannot reach a dependency, so the queue grows unbounded | Backpressure and DLQs (L75, L76) |
| **Cascading timeouts** | One slow dependency consumes all threads, so unrelated endpoints fail | Bulkheads (L77) |
| **Correlated failure** | Every "redundant" component shares one dependency (DNS, auth, a cache) | Failure-domain analysis (L39, L90) |

### 4.6 The mitigations, and which ambiguity each kills

The point of this table is that you do not need all of them; you need to know which ambiguity is unacceptable for *this* operation and pick the cheapest tool that removes it.

| Ambiguity | Cheapest sufficient mitigation | Cost |
|---|---|---|
| "Did the write land?" | **Idempotency key** stored with the effect | One extra table/key + a retention window |
| "Is this node dead?" | Heartbeat + phi-accrual + a *separate* liveness signal | Detection latency, false positives |
| "Am I still the leader?" | **Lease with a fencing token** enforced by the resource | Clock assumptions (L66) and a token column |
| "Did the downstream get it?" | **Queue with at-least-once + a dedup consumer** | Eventual consistency, DLQ operations |
| "Should I keep calling?" | **Circuit breaker with a half-open probe** | Recovery grace period; tuning |
| "Which of these two writes is newer?" | **Logical version/lamport or server-assigned sequence** | Never wall clocks (L66) |
| "What actually happened?" | **Distributed tracing + correlation IDs** (L88) | Instrumentation effort and cost |

## 5. Request / data flow

BookNook checkout with a partial failure in the middle. Budget: 800 ms end-to-end. Retries are configured at **three** layers (client SDK, service-mesh, application), which is the detail that turns one local problem into an outage.

```
 t=0ms     POST /checkout → checkout-svc (attempt 1)
 t=2ms     reserve stock          (local DB, 4ms)              ✓
 t=6ms     POST /charge → payments-svc
 t=8ms     payments-svc → provider (attempt 1 of its own)
 t=190ms   provider charged the card ✓ and wrote the reply
 t=190ms   ✗ the reply is lost in a switch buffer overflow     ◄── the ambiguity
 t=1,008ms app timeout (1000ms) fires → retry (attempt 2)
 t=1,008ms mesh also retries (it saw a 504 from the sidecar)   ← duplicate attempt
 t=1,010ms provider receives attempt 2  ⚠ CHARGES AGAIN unless idempotent
 t=1,010ms payments-svc's own 900ms timeout fires for attempt 1 → retries
 t=1,910ms three charge attempts now in flight for one checkout
 t=2,010ms client SDK gives up at its 2,000ms deadline → user sees
           "payment failed" while the card has been charged ONCE
 t=2,010ms reserve-stock is rolled back (compensation), order marked failed
 t=+5min   reconciliation job finds a charge with no order
 t=+5min   ...and a second charge, if the provider was not idempotent
```

Read the consequences off the timeline:

- **Duplicate charges** appear at `T+1,010` — a 1,000 ms timeout against a `p99.9` of 900 ms.
- **Amplification**: three retry layers means **up to 27 attempts** for one user action (`3 × 3 × 3`). At 40 checkout RPS that is 1,080 provider calls/sec for 40 real orders — precisely how a dependency that was merely struggling is pushed into being down.
- **The user's error is wrong.** The card was charged. The message says it was not. Only idempotency plus reconciliation make this consistent.
- **The compensation is premature.** Stock was released because "payment failed", so the same copy of the book can be sold twice by a different mechanism.

The corrected flow is the same shape with three changes: one retry layer (the application's), an **idempotency key** derived from the order ID sent with every attempt, and a **2 s provider timeout with the reserve-stock compensation delayed** until the ambiguous outcome is resolved by the provider's status API rather than by an assumption.

## 6. ASCII diagram

The three outcomes, and what your code must do about each:

```
                     ┌─────────────────────┐
    request ────────►│   remote service    │
                     └──────────┬──────────┘
                                │
        ┌───────────────────────┼────────────────────────┐
        ▼                       ▼                        ▼
   ┌─────────┐           ┌─────────────┐          ┌──────────────┐
   │ SUCCESS │           │   FAILED    │          │   UNKNOWN    │
   │ 200 +   │           │ conn refused│          │ timeout, RST,│
   │ result  │           │ 503 pre-work│          │ 5xx mid-work │
   └────┬────┘           └──────┬──────┘          └──────┬───────┘
        │                       │                        │
    continue                safe retry            ┌──────▼────────┐
        │                       │                 │ can you ASK?  │
        │                       │                 │ status API /  │
   ┌────▼───────────────────────▼─────┐           │ read-back     │
   │ NO DUPLICATE RISK                │           └───┬───────┬───┘
   └──────────────────────────────────┘               │       │
                                                    yes      no
                                                     │       │
                                          ┌──────────▼──┐ ┌──▼─────────────┐
                                          │ resolve,    │ │ IDEMPOTENCY    │
                                          │ then act    │ │ KEY required   │
                                          └─────────────┘ │ (else: retry is│
                                                          │  a gamble)     │
                                                          └────────────────┘
```

Detection trade-off, drawn as the curve it is:

```
  false
  positives
  per day
     ▲
 1e0 ┤●
     │  ●
 1e-2┤     ●
     │        ●
 1e-4┤           ●
     │              ●
 1e-6┤                 ●
     └──┬────┬────┬────┬────┬────┬────┬──────────► timeout T
        0.5  1    2    5    10   30   60   seconds

   Faster detection ⇄ more false positives.
   False positives are not free: each one triggers a retry, a failover,
   or (worse) a leader election — which is itself an outage generator.

   Choose T from the measured p99.9 of the dependency, not from taste.
   If you cannot afford the false positives at the T your SLO needs,
   the answer is not a better timeout: it is idempotency.
```

The retry amplifier — why "add a retry" is a scaling decision:

```
   user action (1)
        │
        ▼
   ┌─────────┐  3 attempts
   │ client  │ ────────────┐
   └─────────┘             │
                           ▼
                     ┌─────────┐  3 attempts
                     │  mesh   │ ─────────┐
                     └─────────┘          │
                                          ▼
                                    ┌──────────┐  3 attempts
                                    │ service  │ ─────────┐
                                    └──────────┘          │
                                                          ▼
                                                  3×3×3 = 27 calls
                                                  to the dependency
                       for ONE user action.

   Retries multiply ACROSS layers. Consolidate to ONE layer (usually the
   application, which knows the semantics), cap the total attempts, add
   full jitter, and never retry a non-idempotent call.
```

## 7. Detailed example

### 7.1 The checkout that charged twice

BookNook's payment provider times out on `0.2 %` of calls at `T = 1,000 ms`. With 40 checkout RPS, that is **~7 ambiguous outcomes per minute** in normal operation — a fact nobody had computed, because "0.2 % is small".

The retry policy was the default of the HTTP client: 3 attempts, no idempotency key, no jitter. The first incident:

- 14:02 — provider latency degrades from `p99 = 400 ms` to `p99 = 1,300 ms` (their problem, later attributed to a bad index in their ledger).
- 14:02–14:09 — **7 %** of calls now exceed BookNook's 1,000 ms timeout. Retry amplification multiplies provider load by ~1.9×.
- 14:09 — 312 customers are charged twice. Every one of them is a customer-service ticket and a refund; 40 of them are also refunded the *stock* incorrectly.
- 14:11 — the provider rate-limits BookNook (`429`), and genuine checkouts start failing. **Availability collapse caused by the retry policy, not by the provider.**

Three fixes, in the order they were applied:

1. **Idempotency keys** (immediate, one day's work): every charge carries `Idempotency-Key: order:{order_id}:charge`, and the provider (like Stripe, Adyen and most others) returns the *original* result for a repeated key rather than charging again. This alone removed the duplicate charges entirely.
2. **One retry layer** (one sprint): client SDK retries disabled; the mesh's retry disabled; only the application retries, with **3 attempts, full jitter, and a hard 2,500 ms total deadline**. Peak amplification went from 27× to 3×.
3. **Timeout derived from a measurement** (same sprint): `T = 1,000 ms` was replaced by `T = 600 ms` plus the idempotent retry — because with idempotency, a *fast* failure and a retry is strictly better than a slow wait. The user-visible p99 improved from 1,900 ms to 780 ms.

The transferable insight: **idempotency did not just prevent duplicates, it let them make the timeout shorter**, which is the opposite of the naive instinct ("the provider is slow, wait longer"). Once retrying is safe, a tight timeout plus a retry is a latency *optimisation*, and it is the single most valuable structural change you can make to a call on a critical path.

### 7.2 The partition that was a 38-second GC pause

A payments pod pauses for **38 s** under a full GC. The load balancer's readiness probe (`interval 5 s, timeout 2 s, failures 3`) takes **~17 s** to remove it. In that window it receives ~1,700 requests, which queue in the accept backlog, time out at the caller, and are retried — **against the two remaining pods**, which are now at 150 % of their designed capacity and start to slow down too. One of them trips its own breach detector; the fleet goes to one pod; the queue explodes.

Nothing in the incident involved a network fault. The dashboard for the network was green. The *only* signal that explained it was **pause time** on the pod (`jvm_gc_pause_seconds_max`), which nobody was alerting on. The fixes:

- Readiness probe `interval 2 s, timeout 1 s, failures 2` → eject in ~5 s.
- Alert on `max GC pause > 2 s` — a pause longer than the probe window is a guaranteed traffic-loss event.
- **Retry budgets** (a percentage of total requests, not per-request attempts) so that a degraded fleet cannot be retried into collapse.

The general shape: **a partial failure is invisible in the aggregate and obvious in the tail.** p50 latency barely moved; p99.9 went from 900 ms to 31 s. If you monitor averages, you will always learn about these from customers.

### 7.3 The asymmetric partition that only a trace could see

After a control-plane upgrade, `checkout-svc` in `eu-west-1a` could reach `inventory-svc` in `1b`, but replies from `1b` to `1a` were dropped by a misprogrammed ACL. Result: every inventory call from `1a` **timed out after the full budget**, then succeeded on retry to a *different* pod in another AZ.

Symptoms were maddening: success rates looked fine (retries worked), but p99 was exactly the timeout value, and only `1a` was affected. **Nothing was "down".** It was found by grouping trace latency by caller zone: `1a → 1b` showed a 1,000 ms wall on 100 % of calls.

The lesson: **partial failures are per-link, not per-node**, and your metrics must be able to answer "is this bad for *this caller* to *this callee*?" — which means labels for both, and a trace to prove it (L88).

## 8. Code or configuration example

```python
# ============================================================================
# BookNook: surviving the third outcome. Illustrative, Python-like.
# Every number below is a decision, not a default. Comments state the decision.
# ============================================================================
import random, time, uuid

# --- 1. THE DEADLINE IS THE CONTRACT ----------------------------------------
# The user budget is 800ms end to end. Each layer gets what is left, never a
# fixed value — otherwise 4 sequential 900ms timeouts = 3.6s of user pain.
def checkout(order, deadline_ms):
    deadline = time.monotonic() + deadline_ms / 1000

    def remaining(floor_ms=50):
        left_ms = (deadline - time.monotonic()) * 1000
        if left_ms < floor_ms:
            raise DeadlineExceeded()      # FAIL FAST: do not start work you
                                          # cannot finish inside the budget
        return left_ms / 1000


# --- 2. IDEMPOTENCY: the ONLY way to make an ambiguous outcome safe ----------
# The key is derived from the business operation, not generated per attempt.
# A retry MUST reuse it; a new checkout MUST NOT.
idem_key = f"order:{order.id}:charge"          # stable across retries
resp = provider.post(
    "/charges",
    headers={"Idempotency-Key": idem_key},
    json={"amount": order.total_pence, "currency": "GBP"},
    timeout=remaining(),
)
# Provider semantics (check yours; do not assume): same key + same body returns
# the ORIGINAL result, including a 4xx. Same key + DIFFERENT body is rejected.
# Without this header, the retry loop below is a duplication engine.


# --- 3. RETRY: ONE layer, capped budget, full jitter -------------------------
def with_retry(fn, deadline, max_attempts=3):
    """
    Rules encoded here, each of which was learned from an incident:
      - retries happen in ONE layer (the application). Client SDK and mesh
        retries are disabled — 3 x 3 x 3 = 27 is how dependencies die.
      - full jitter, not exponential backoff: exp backoff SYNCHRONISES the
        herd, which is the opposite of what you want.
      - the deadline is absolute, not per-attempt; the last attempt gets
        whatever is left or none at all.
      - only retry idempotent operations, and only on RETRYABLE errors.
    """
    for attempt in range(max_attempts):
        try:
            return fn(deadline)
        except (ConnectionRefused, PreWork503) as e:
            pass                                    # SAFE: nothing ran
        except (Timeout, MidWork5xx) as e:
            pass                                    # AMBIGUOUS: rely on idempotency
        except PermanentError:
            raise                                   # 4xx: retrying is abuse

        if attempt == max_attempts - 1 or deadline.expired():
            raise
        backoff = min(0.4, 0.05 * (2 ** attempt))     # 50ms, 100ms, 200ms, cap 400ms
        time.sleep(random.uniform(0, backoff))        # FULL jitter: 0..backoff
    raise AssertionError("unreachable")


# --- 4. RECONCILIATION: assume ambiguity WILL happen -------------------------
# The only defence that works when the provider is down for 30 minutes and the
# user has gone to bed. Runs every minute; reads, does not retry blindly.
def reconcile_uncertain_charges():
    for order in db.query("""
            SELECT * FROM orders
             WHERE status = 'charging'
               AND updated_at < now() - interval '2 minutes'"""):
        # ASK the provider what happened; do not guess.
        charge = provider.get(f"/charges?key={order.idem_key}")
        if charge is None:
            db.exec("UPDATE orders SET status='payment_failed' WHERE id=$1", order.id)
        elif charge.status == "succeeded":
            db.exec("UPDATE orders SET status='paid', charge_id=$2 WHERE id=$1",
                    order.id, charge.id)
            emit("order.paid", order.id)              # outbox, L85
        else:
            db.exec("UPDATE orders SET status='failed' WHERE id=$1", order.id)
        metrics.incr("checkout.reconciled", tags={"outcome": charge.status})


# --- 5. FENCING: the defence against a "dead" node that is merely slow -------
# Never trust a lock you hold; make the RESOURCE reject stale owners.
def write_with_fence(row_id, value, fence_token):
    n = db.exec("""
        UPDATE inventory
           SET reserved = reserved + 1,
               last_fence = $3
         WHERE isbn = $1
           AND last_fence < $3        -- rejects ANY writer with a stale lease
        """, isbn, value, fence_token)
    if n == 0:
        raise StaleFence("another owner has taken over; abandon this write")
    # The failure this prevents: a GC-paused holder wakes up, its lease has
    # expired and been re-issued, it "knows" it still owns the resource, and
    # it writes. Without the WHERE clause it corrupts the data. With it, the
    # write is rejected — and the zombie learns it is a zombie.
```

```yaml
# --- 6. The configuration that makes the above true fleet-wide --------------
# Service mesh: DISABLE retries here. They belong at one layer only.
# Istio VirtualService, illustrative:
apiVersion: networking.istio.io/v1beta1
kind: VirtualService
metadata: {name: payments}
spec:
  hosts: [payments-svc]
  http:
    - timeout: 0.6s              # per-attempt, derived from p99.9 = 400ms
      retries:
        attempts: 0              # deliberate: the app retries, with a budget
      fault:                     # (drills only)
        delay: {percentage: {value: 1}, fixedDelay: 30s}

---
# Bulkheads: a saturated dependency must not consume every thread.
# Without this, one slow dependency fails EVERY endpoint in the pod (L77).
resource:
  limits: {cpu: "2", memory: "2Gi"}
  # app-level: separate thread pool / semaphore per dependency
  #   payment:   max 20 concurrent, queue 40
  #   inventory: max 50 concurrent, queue 100
  # When the payment pool is saturated we return 503 fast — the queue does not
  # absorb it, because absorbing it means holding threads we need for reads.

# Alerts that would have caught §7.2 and §7.3:
alerts:
  - jvm_gc_pause_seconds_max > 2            # > probe timeout => traffic loss
  - histogram_quantile(0.999, rpc_duration) > 5  # tail, not average
  - increase(retries_total[5m]) / increase(requests_total[5m]) > 0.10
  - rate(deadline_exceeded_total[5m]) > 0
  - trace_latency_p99{caller_zone="1a",callee_zone="1b"} > 500  # §7.3
```

## 9. Numerical example

BookNook checkout. Payment provider: `p50 = 180 ms`, `p99 = 400 ms`, `p99.9 = 900 ms`. Checkout rate: **40 RPS** at peak. End-to-end user budget: **800 ms**.

**Step 1 — choose the timeout from the distribution, not from taste.**

```
T = 1,000 ms  → false timeouts ≈ 0.05 %  ≈ 0.02/sec ≈ 1,700/day  (ambiguous)
T = 600 ms    → false timeouts ≈ 1.2 %   ≈ 0.48/sec ≈ 41,000/day (ambiguous)
T = 400 ms    → false timeouts ≈ 1.0 %   ... p99 exactly: 1 % of calls
```

Naively, 600 ms looks terrible: **41,000 ambiguous outcomes a day**. The instinct is "use a bigger timeout". But an ambiguous outcome is only a *problem* if it is unresolved. With an idempotency key, 41,000 ambiguous outcomes become 41,000 safe retries, of which the vast majority return the original successful charge — and the user sees a **fast** failure-and-retry instead of a slow wait.

Compute the user-visible effect. With `T = 1,000 ms`, every call in the slow tail costs the user a full second; the checkout p99 becomes ~1,050 ms, **over budget**. With `T = 600 ms` + one retry at `+120 ms jitter`:

```
p99 path: 600 ms timeout + ~120 ms backoff + 180 ms retry (p50) ≈ 900 ms
```

Still marginal, which is why BookNook's real answer is `T = 500 ms` with **hedged** second attempt at 250 ms for reads (see §10). The arithmetic conclusion: **a shorter timeout plus a safe retry beats a longer timeout, once idempotency exists.** The timeout is not a courtesy to the dependency; it is a statement about how much of the user's budget you are willing to spend on one attempt.

**Step 2 — the retry amplifier, quantified.**

```
Layers with retries: client SDK (3) × mesh (2) × app (3) = 18 attempts
At 40 checkout RPS, if 5% of calls time out:
  first attempts            : 40.0 /s
  SDK retries at 5%         : 40 × 0.05 × 2   = 4.0 /s
  mesh retries at 5%        : 44 × 0.05 × 1   = 2.2 /s
  app retries at 5%         : 46 × 0.05 × 2   = 4.6 /s
  total downstream load     : 50.8 calls/s for 40 user actions = 1.27×
```

A 27 % load increase does not sound fatal. Now do it during the provider's *own* degradation, where the timeout rate is **7 %**:

```
  40 × 0.07 × 2 (SDK)  = 5.6
  45.6 × 0.07 × 1 (mesh) = 3.2
  48.8 × 0.07 × 2 (app)  = 6.8
  downstream load = 55.6 → but ALSO each retry holds a thread for 1,000 ms:
  in-flight = 55.6 × 1.0 s = 56 threads, versus 40 × 0.18 = 7.2 in the good case
```

**Thread pool exhaustion at ~8× the normal concurrency** — and that is the mechanism by which a payment-provider slowdown becomes a payments-*service* outage. Consolidating to one retry layer with capped attempts drops the multiplier from 1.27× to ~1.07×.

**Step 3 — the metastable failure, in queueing arithmetic.**

Call the service utilisation `ρ`. Using an M/M/1 approximation for response time:

```
  R = S / (1 - ρ)          S = service time
  ρ = 0.70  → R = 3.3 S
  ρ = 0.90  → R = 10  S    ← retries add load
  ρ = 0.95  → R = 20  S    ← ...which raises latency
  ρ = 0.99  → R = 100 S    ← ...which raises the timeout rate
  ρ = 1.00  → R = ∞        ← ...which adds more retries. Irreversible.
```

At `ρ = 0.90`, response time is `10 S`. If `S = 400 ms`, `R = 4 s` — every call now times out, every timeout retries, and retries push `ρ` upward. **The system is now failing because it is failing.** It will not recover when the original trigger clears; it recovers when the retries stop. That is the definition of metastable, and the only exits are **load shedding** (reject 30 % immediately and keep `ρ < 0.9`) and a **circuit breaker** (stop calling the degraded dependency entirely).

**Step 4 — false positives cost real outages.** Say a leader lease is `10 s` and the heartbeat timeout is `3 s`, with a per-heartbeat false-timeout probability of `0.0001` (a p99.99 event). Heartbeats are every `1 s`, so **10 chances per lease to be wrongly suspected**:

```
P(false failover per lease) ≈ 10 × 0.0001 = 0.001  → one per 1,000 leases
At 8,640 leases/day (24h ÷ 10s)  → 8.6 spurious leader elections PER DAY
```

Each spurious election costs ~500 ms of unavailability for that shard plus a log truncation risk. **8.6 avoidable incidents a day, produced entirely by a timeout that looked conservative.** The fix is not a longer timeout (that lengthens MTTR); it is a *lease* longer than the *heartbeat window* by a safe margin, plus fencing tokens so the election is harmless when it does happen.

## 10. Trade-offs

| Decision | Buys you | Costs you | Choose it when |
|---|---|---|---|
| **Long timeout (e.g. 10 s)** | Few false positives; better chance a slow call succeeds | User waits 10 s; MTTR is 10 s+; threads/connections held | Idempotent background jobs where latency does not matter |
| **Short timeout + safe retry** | Fast failure, fast recovery, better p99, fewer held resources | Needs idempotency, or it manufactures duplicates | Any critical-path call with an idempotent effect |
| **Aggressive timeout, no retry** | Simple; no amplification at all | Transient blips become user-visible errors | Non-idempotent calls where retry must be human/business driven |
| **Retries at every layer** | Each layer "feels" resilient | Multiplicative amplification; the classic outage generator | Never |
| **Retries at exactly one layer (app)** | Predictable 1.05–1.1× amplification; semantics-aware | Requires controlling the other layers' config | Always |
| **Circuit breaker** | Stops metastable collapse; fails fast; protects the dependency | New state machine to operate, and a recovery probe to tune | Any dependency whose failure you cannot absorb |
| **Hedged requests** | Cuts p99 dramatically on slow-tail systems | +5–10 % load; needs idempotency | Reads with a long, fat tail and cheap duplicates |
| **Partition: refuse (CP)** | No divergence | Availability loss during the partition | Money, inventory, uniqueness (L59) |
| **Partition: accept (AP)** | No availability loss | Reconciliation, conflicts, and business rules for the loser | Views, reviews, presence |
| **Aggressive failure detection** | Fast failover, short MTTR | Spurious elections (§9 step 4), flapping, retry storms | Only with fencing and a suspicion model |
| **Correlation IDs + tracing** | Turns "unknown" into "we know, 20 minutes later" | Cost, instrumentation, cardinality limits | From the first day; it is the only hindsight you get |

## 11. Failure scenarios

**11.1 — The 27× retry storm.** Three layers each configured "safely" with 3 attempts. During a dependency slowdown the dependency receives up to 27× the intended load and dies. **Symptom:** the dependency's own dashboard shows a traffic cliff *before* its error rate rose; the outage outlasts the original trigger. **Fix:** retries at one layer, plus a **retry budget** (retries ≤ 10 % of requests) so the fleet cannot collectively over-retry.

**11.2 — The duplicate charge.** A timeout with no idempotency key. Detected by a customer, not by monitoring. **Symptom:** a charge with no matching order, found days later by reconciliation (or by a chargeback). **Fix:** idempotency keys on every mutating call, stored *with* the effect, plus daily reconciliation as the backstop.

**11.3 — Zombie leader.** A 38 s pause expires a 10 s lease. A new leader takes over. The old one wakes, still believes it leads, and writes. **Symptom:** two writers, interleaved data, no errors anywhere. **Fix:** fencing tokens checked in the `WHERE` clause of every write (§8.5), never trusting a lock client-side.

**11.4 — Thread pool starvation by an unrelated endpoint.** One slow endpoint (a report) consumes the whole shared pool; every other endpoint on that pod times out. **Symptom:** endpoints failing that do not call the slow dependency, clustered on a subset of pods. **Fix:** bulkheads — per-dependency semaphores and separate pools (§8.6).

**11.5 — The health check that took out the fleet.** `/healthz` checks the database. The database has a 3-second blip; every instance fails readiness simultaneously; the load balancer has no backends; total outage from a *transient* database hiccup. **Symptom:** a full outage for a dependency degradation that would have been invisible. **Fix:** liveness = "my process is alive"; readiness = "my *own* dependencies within my tolerance, and I can serve *something*". Never let a shared dependency appear in a readiness probe without a tolerance window and an outlier-resistant threshold.

**11.6 — Queue build-up with no bound.** Consumers cannot reach a dependency; the queue grows from 40,000 to 90 million messages over a weekend; recovery then requires 14 hours of drain during which everything downstream is stale. **Symptom:** backlog age (not depth) climbs without limit. **Fix:** alert on **consumer lag in time**, cap retention, shed load upstream (L76), and make the consumer idempotent so a replay is safe (L73–74).

**11.7 — Ambiguous outcome resolved by assumption.** The reconciliation job marks an ambiguous charge as failed (defaulting to the safe-looking branch), releases stock, and refunds. The provider actually succeeded, so the customer has a refund *and* a book. **Symptom:** inventory drift and free books. **Fix:** never resolve ambiguity by assumption — query the authoritative source, and if unreachable, keep it in an explicit `unknown` state that blocks the compensation.

**11.8 — The partition that was a DNS TTL.** A dependency's IP changed; some callers' DNS caches held the old IP for 300 s, and those callers saw connection timeouts for five minutes after the "completed" failover. **Symptom:** partial failure with a clean, sharp boundary in time and an obvious per-caller pattern. **Fix:** low TTLs for failover-critical records, client-side re-resolution on connection failure, and treating DNS as a dependency with its own failure modes.

## 12. Alternatives

| Instead of | Consider | When |
|---|---|---|
| A retry | **Idempotency + retry** | Almost always — this ordering is the whole lesson |
| A retry | **A queue** (L68) | The work does not need to happen during the request |
| A timeout | **A deadline budget propagated through calls** | Any request with more than one sequential hop |
| Detecting failures yourself | **A coordinator/consensus service** (L79, L81) | Membership, leader identity, small critical state |
| Heartbeat + threshold | **Phi-accrual** | Fleets with heterogeneous latency and rare-but-costly failovers |
| Waiting for recovery | **Circuit breaker + load shedding** | Metastable conditions, where waiting *is* the disease |
| Retrying a slow read | **Hedged request** | Long fat tails; the duplicate read is free |
| Resolving ambiguity later | **Two-phase commit** (L83) | You need atomicity across services and can pay the latency |
| Resolving ambiguity later | **Saga + compensation** (L84) | Business-level undo is acceptable and the alternative is a distributed transaction |

## 13. When to use it

- **Every call that crosses a process boundary.** Which means: always be able to answer "what are the three outcomes here, and what do I do about the third?"
- **Before adding any retry**, in any layer. Ask what the amplification is and whether the next layer also retries.
- **When choosing a timeout**, from a measured distribution rather than a default.
- **Whenever a dependency has a tail you cannot control** — hedged reads, budgets, and breakers are the standard toolkit.
- **When designing a critical path**, so that "unknown" is a state in your data model (a `charging` status), not an exception you swallow.
- **During every design review of a multi-service system**: name the failure domains, the partitions possible between them, and the behaviour in each.

## 14. When *not* to use it

- **Inside one process.** Function calls do not have a third outcome; adding retry logic around an in-process call adds failure modes, not resilience.
- **On non-idempotent operations without a key.** Retrying a charge, an increment, or a "send email" call without idempotency is manufacturing duplicates.
- **On errors that are permanent.** Retrying a `400 Bad Request` or a business-rule rejection wastes the budget and delays the user.
- **As a substitute for fixing the dependency.** A circuit breaker protects you from a slow service; it does not make the service fast.
- **Timeout tuning as a substitute for capacity.** If `p99.9` exceeds your budget because the dependency is under-provisioned, the fix is capacity, not a shorter timeout.
- **Aggressive failure detection without fencing.** Fast detection plus no fencing is a split-brain generator (§9 step 4).
- **Full tracing with unbounded cardinality.** Per-user, per-order trace IDs at 100 % sampling will cost more than the outage you are preventing; sample deliberately (L88).

## 15. Common mistakes

1. **Treating a timeout as a failure.** It is an *unknown*, and the difference is the entire incident category this lesson exists to prevent.
2. **Retrying at multiple layers.** Multiplicative amplification; the single most common self-inflicted outage in microservices.
3. **Retrying without idempotency.** Duplicates, double charges, double decrements.
4. **Exponential backoff without jitter.** Synchronised retries produce a thundering herd on every recovery, turning one hiccup into a cycle.
5. **Choosing a timeout from a screenshot of p99 on a good day.** Use p99.9 *including* server-side queueing, and revisit it.
6. **A readiness probe that checks shared dependencies with no tolerance.** One database blip ejects the fleet (§11.5).
7. **Assuming a live node is a working node.** Gray failures are the majority of production incidents.
8. **Believing your own "is it up?" dashboard.** Per-node uptime says nothing about per-link reachability (§7.3).
9. **Resolving ambiguity by assumption.** Defaulting an unknown payment to "failed" creates free merchandise (§11.7).
10. **Unbounded queues as "buffering".** A queue that only grows is a data-loss device with extra steps.
11. **No retry budget.** Individual retry caps do not prevent the *fleet* from retrying 3× under load.
12. **Trusting a lease without fencing.** Any holder can be paused for longer than the lease; only the resource can enforce precedence.
13. **Monitoring averages.** Every failure in this lesson is invisible in the mean and obvious at p99.9.
14. **Forgetting compensation ordering.** Rolling back stock while payment is still ambiguous creates the very double-sell you were avoiding.

## 16. Interview perspective

**What is actually being tested.** Whether you know that "it failed" is not one thing. Strong candidates state the three outcomes unprompted, reach for idempotency before retries, and can explain **why a timeout is a guess**. Then they attach a number: detection time, amplification factor, or a budget. Weak candidates say "add retries and a timeout" and consider the problem solved.

**Answers that land well:**

- *"How do you handle a dependency timing out?"* — "First, is it safe to retry? If the operation is not idempotent, the timeout is an unknown and I need a status query or a queued/at-least-once design before I even think about retrying. Then retries at one layer only, with a deadline budget, full jitter, and a retry budget so the fleet cannot over-retry. Then a circuit breaker for when it is down rather than slow."
- *"How do you detect a failed node?"* — "Heartbeats plus a timeout, which means I am choosing a false-positive rate. I'd set it above the measured p99.9 including the node's own queueing, use a suspicion model rather than a hard switch, and make sure a spurious failover is harmless via fencing tokens, because at 8,600 leases a day even a 0.01 % false-positive rate is an incident a day."
- *"What is a gray failure?"* — "A node that is up and answering, but failing for some callers or some fraction of requests. It is what most real incidents are, it defeats binary health checks, and you find it by comparing the same call from different callers — which is a label on your metrics and a trace, not a ping."
- *"Two generals — how do you solve it?"* — "You don't. You make the ambiguity survivable: idempotent effects, explicit unknown states, reconciliation against an authority, and business rules for the case where both sides acted."

**Common interview tasks:** classify the outcomes of an RPC and say what is safe; choose a timeout given a latency distribution; compute retry amplification across layers; decide CP vs AP for an operation during a partition; explain how a lease plus fencing prevents split brain; design a checkout that survives a payment provider outage; explain what breaks first when you add retries.

**Red flags interviewers watch for:**

- Conflating a timeout with a failure.
- "Just retry" with no mention of idempotency.
- Retries configured at multiple layers, or no awareness that layers multiply.
- Exponential backoff without jitter.
- No numbers around detection time or amplification.
- Believing health checks make failures binary.
- Treating a network partition as impossible ("we're in one AZ").
- No plan for the writes/charges that arrived during an ambiguous window.
- Being unable to name a single gray failure in their own experience.
- Thinking a lock protects a resource from a paused holder.

## 17. Knowledge check

**Conceptual**

1. State the three possible outcomes of an RPC. For each, say whether a blind retry is safe, and name the mechanism that makes an unsafe one safe. Then explain why `connection refused` and a `500 returned after 900 ms` must be treated differently.
2. Explain why a timeout can never be "correct", and describe the two costs that move in opposite directions as it changes. Give the specific measurement you would use to choose one, and explain why a *shorter* timeout can be better once idempotency exists.
3. Define a metastable failure, describe the feedback loop that sustains it, and explain why the system does not recover when the original trigger is removed. Name the two mechanisms that break the loop and say what each does to user-visible behaviour.

**Practical scenario**

4. BookNook's checkout calls three services: inventory (local, 4 ms), payment (external, `p99.9 = 900 ms`), and email confirmation (internal, `p99 = 120 ms`, occasionally 30 s). The user budget is 800 ms and the payment provider's SDK has retries on by default. Peak is 40 checkout RPS. Design the end-to-end failure handling: the deadline propagation scheme with per-hop budgets, which layer retries and with what policy, the idempotency scheme including key derivation and storage, the state machine for the order while payment is ambiguous, what the user sees in each state, the compensation ordering, the reconciliation job, the three alerts, and the exact behaviour when the payment provider is fully down for 20 minutes.

**Calculation**

5. A dependency is called at 500 RPS with `p50 = 40 ms`, `p99 = 250 ms`, `p99.9 = 1,200 ms`. (a) If the timeout is 300 ms, estimate the fraction of calls that time out and the number per day. (b) With client, mesh and application retries each allowing 2 attempts, compute the worst-case and expected downstream load multiplier at the timeout rate from (a). (c) If each attempt holds a thread for the full timeout when it fails, compute the in-flight thread requirement before and after retries, and state which resource fails first.

6. A three-replica cluster uses a 10 s leader lease with 1 s heartbeats. The per-heartbeat probability of a false timeout is `1 × 10⁻⁴`. (a) Compute the probability of a spurious election per lease. (b) Compute the expected number of spurious elections per day. (c) State two changes that reduce it and, for each, the cost you accept. (d) Explain why fencing tokens are still required even if the rate from (b) is zero.

**Progress checklist**

- Phase 1 — Foundations (L1–17): **complete** ✅
- Phase 2 — Scaling (L18–39): **complete** ✅
- Phase 3 — Data and Storage (L40–63): **complete** ✅
- Phase 4 — Distributed Systems (L64–92):
  - [x] 1. Why distributed systems are difficult (L64)
  - [x] **2. Partial failures and network partitions (L65) ← you are here**
  - [ ] 3. Clock differences and ordering events (L66)
  - [ ] 4. Synchronous versus asynchronous workflows (L67)
  - [ ] 5. Message queues (L68)
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

**NEXT LESSON: Phase 4, Topic 3 — Clock differences and ordering events**

---

---

# Lesson 66 — Clock Differences and Ordering Events

*Phase 4, Topic 3*

## 1. Simple definition

Every machine has its own **clock**, and no two agree. **Skew** is the difference between two clocks at a given instant. **Drift** is the rate at which that difference grows, because each oscillator runs at a slightly different frequency. A commodity quartz oscillator drifts by roughly **50–100 parts per million**, which is **4–9 seconds per day** if nothing corrects it.

Clock synchronisation (NTP, PTP) reduces skew to milliseconds inside a datacentre and tens of milliseconds across regions — but it never reaches zero, it can **step backwards**, and it comes with an error bound rather than a guarantee.

**Ordering events** is the problem that follows: if node A stamps a write `10:00:00.100` and node B stamps another `10:00:00.050`, you cannot conclude B's write happened first. A's clock might be 200 ms fast. **Wall-clock timestamps are not a causal order**, and any system that treats them as one will silently discard the wrong data.

The alternative is a **logical clock**: a counter that captures *happens-before* without pretending to know the time. Lamport clocks give a total order consistent with causality; **vector clocks** additionally detect when two events are genuinely **concurrent** (neither happened before the other); **hybrid logical clocks (HLC)** combine physical time with a logical counter and are what most modern databases actually use.

Terms defined before use:

- **Wall clock** (`CLOCK_REALTIME`): what you get from `System.currentTimeMillis()`. Can jump forwards or backwards. Use it for *display*, never for measuring elapsed time or ordering writes.
- **Monotonic clock** (`CLOCK_MONOTONIC`): never goes backwards, unaffected by NTP steps. Use it for **timeouts, durations and leases** — this is not optional.
- **Happens-before** (`a → b`): a causal relation — same thread in order, or a message sent before it was received. If neither `a → b` nor `b → a`, the events are **concurrent** and have no "correct" order at all.
- **Total order vs partial order**: a total order puts every pair of events in sequence (a single log does this). Causality only gives a partial order; concurrent events are incomparable.
- **Clock uncertainty**: the interval `[earliest, latest]` in which true time lies, given a bounded synchronisation error. Spanner's TrueTime exposes this explicitly as `TT.now()` returning an interval.

The one sentence to carry out of this lesson: **clocks can tell you *roughly when* something happened; only a log, a sequence number or a single writer can tell you *what happened first* — and those are the only mechanisms you may use to decide which write survives.**

## 2. Why it exists

Phases 1–3 used timestamps casually and got away with it, because a single database node assigns them itself: `now()` inside one process is monotonic enough, and `ORDER BY created_at` on rows written by one server is fine.

Distribution removes that. BookNook now has three write paths for a customer's cart: the web app, the mobile app's optimistic sync, and a lambda that merges carts. Each stamps `updated_at` from its own clock. Three consequences follow, and all three are data corruption rather than inconvenience:

1. **Last-write-wins loses real data.** The merge rule is "highest `updated_at` wins". A phone whose clock is 4 seconds slow writes the user's newest edit; the web session's older edit has a *larger* timestamp and overwrites it. The user's last action disappears. Nothing errors, nothing logs, and there is no way to notice without the user complaining.
2. **Ordering is not just about conflicts.** Leases (L78), leader election (L79), TTL expiry, idempotency windows, token validity and rate-limit buckets all depend on "has this expired?" — and they depend on it *in the direction that keeps you safe*. A lease check that uses the wall clock can be fooled by a 300 ms NTP step into granting two owners.
3. **Debugging is impossible without a causal order.** "Which service saw the bad value first?" is unanswerable from timestamps that disagree; you need a trace with parent/child relationships (L88), not a timestamp sort.

The lesson exists because the intuitive fix — "just sync the clocks harder" — has a hard limit. NTP can get you to ~1 ms in a datacentre and ~10 ms across the internet, and **those numbers are the floor for any algorithm that compares timestamps**. If your conflict window is 5 ms, you are inside the noise. No amount of engineering removes the residual; it only tells you how big it is (Spanner's TrueTime is precisely a machine for *knowing* the residual). So the design question is always: **do I need to compare timestamps at all, or can I avoid it?** Almost always, you can.

## 3. Real-world analogy

Two people taking minutes in a meeting, each with their own watch.

Alice's watch is four minutes fast. Bob's is correct. Alice writes "decision made at 10:04"; Bob writes "objection raised at 10:01". A reader concludes the objection came *after* the decision, and that the decision stands. The truth is the opposite: the objection came first, the decision was reconsidered and changed. **The notes are individually accurate and jointly misleading**, and no reader can tell — because both writers believed their own watch.

Now the three real fixes, in increasing order of sophistication:

- **Agree on one clock.** Put a clock on the wall and require everyone to use it. This is a **single writer** or a **server-assigned timestamp**: it works perfectly, and it costs you a network round trip. It is also what most systems should do.
- **Number the minutes instead of timing them.** "Minute 1, minute 2, minute 3…" — nobody needs to know *when*, only *what came before*. That is a **logical clock**, and it is why every serious system orders by a sequence number, an offset or a term rather than a timestamp.
- **Record who spoke to whom.** If Alice says something *in response to* Bob, you know the order regardless of watches. That is **happens-before**, and it is what **vector clocks** capture: they tell you not just the order but when two notes were written by people who never heard each other — **concurrent**, in which case there is no "correct" order and you need a merge rule, not a tie-break.

The station-clock detail is the punchline: railway timetables only became possible once every station in a country used one time standard, and the standard was defined by *agreement*, not discovered by measurement. Distributed systems face the same choice every day, and the systems that stayed up are the ones that stopped asking their clocks for the answer.

## 4. Detailed technical explanation

### 4.1 Three clocks, three jobs

| Clock | Source | Behaviour | Use for | Never use for |
|---|---|---|---|---|
| **Wall clock** (`CLOCK_REALTIME`, `gettimeofday`) | NTP-disciplined RTC | Can jump forward **or backward**; changes rate gradually | Displaying time to humans; coarse analytics; log timestamps | Durations, timeouts, leases, ordering writes |
| **Monotonic clock** (`CLOCK_MONOTONIC`, `System.nanoTime()`) | Uptime counter, PTP-disciplined | Never goes backwards; has no relation to calendar time | **Timeouts, leases, elapsed measurement, backoff** | Anything a human reads or you persist across restarts |
| **True time / bounded clock** (Spanner, PTP+hardware) | GPS + atomic clocks | Exposes an **uncertainty interval** | Global ordering with commit-wait | Nothing else — it is expensive hardware |

The most common production bug in this table is using the wall clock for durations. `timeout = realtime_at_start + 500ms` is violated by a single NTP correction; `elapsed = realtime_end - realtime_start` can be **negative**. Every language now offers the monotonic variant; use it, and audit for `Date.now()`/`System.currentTimeMillis()` in timeout code.

### 4.2 Skew, drift, and what synchronisation actually gives you

```
   true time  ────────────────────────────────────────────────────────►
   node A      10:00:00.000   10:00:01.000   10:00:02.000
   node B      10:00:00.007   10:00:01.011   10:00:02.015   ← 7 ms fast, drifting

   skew   = 7 ms       (instantaneous difference)
   drift  = ~5 ppm     (rate of change: ~0.43 s/day)
   offset = the correction NTP would apply
   error bound = how confident you are: ±0.5 ms (same rack, PTP)
                                      ±2-10 ms (same DC, NTP)
                                      ±10-100 ms (cross-region, NTP over WAN)
```

Four facts that constrain every design:

1. **Skew is bounded only by measurement, and the bound is the real guarantee.** If you do not know your bound, you must assume it is much larger than you think (VMs under load, suspended instances, containers sharing a host clock all misbehave).
2. **NTP can step the clock backwards or forwards.** A step backwards makes previously-written timestamps *future* timestamps; forward steps make intervals vanish. `ntpd` can slew (gradually adjust the rate, safe) or step (jump, dangerous). `chronyd` on a VM is the modern default; configure `makestep` carefully.
3. **Virtual machines are worse.** vCPU steal, live migration, and suspend/resume produce pauses and time jumps that the guest's NTP must then correct — after the fact.
4. **Leap seconds still happen** in some configurations, and a widely-deployed consequence was the 2012 leap-second incident where a Linux kernel bug caused a livelock on many servers at once. Modern practice is **leap smearing**: spread the extra second over hours so no clock ever jumps.

### 4.3 Logical clocks: recovering order without time

**Lamport's happens-before** is defined by three rules:

```
  1. Same process:   if a comes before b in the program order,  a → b
  2. Messages:       if a is "send(m)" and b is "receive(m)",    a → b
  3. Transitivity:   if a → b and b → c, then                    a → c

  If neither a → b nor b → a, then a and b are CONCURRENT.
  Concurrent events have NO correct order. Any order you impose is a
  business decision, not a fact.
```

**Lamport clocks** are one integer per node: increment on every event; on receive, `c = max(c_local, c_msg) + 1`. This guarantees `a → b ⇒ c(a) < c(b)`. It does **not** guarantee the converse: `c(a) < c(b)` does not imply `a → b`. So Lamport clocks give a **total order consistent with causality** — enough to break ties deterministically and to show a causal chain in logs, but not enough to detect concurrency.

**Vector clocks** are one integer *per node*: they detect concurrency exactly. `a → b` iff `V(a) ≤ V(b)` component-wise; if neither is ≤ the other, `a` and `b` are concurrent.

```
  Three nodes, key "cart:42":
     V(a) = [2, 1, 0]      written by A after 2 local events
     V(b) = [1, 3, 0]      written by B

     [2,1,0] ≤ [1,3,0]?  No (2 > 1)
     [1,3,0] ≤ [2,1,0]?  No (3 > 1)
     ⇒ CONCURRENT. Not "B is newer". The system must MERGE or ask.

     V(c) = [2, 2, 0]   vs   V(a) = [2, 1, 0]
     [2,1,0] ≤ [2,2,0]  ⇒  a → c  (c is strictly newer)
```

The cost: vector clocks are `O(n)` in the number of writers, which is why Dynamo-style systems bound them and prune old entries (with a risk of false concurrency), and why practical stores use **version numbers per item** instead when there is a single writer per key.

**Hybrid logical clocks (HLC)** are the pragmatic middle: a timestamp made of `(physical_ms, counter)`. They stay close to wall time (so they are human-readable and usable for time-range queries) while guaranteeing monotonicity and preserving causality. CockroachDB, MongoDB and YugabyteDB all use HLCs or close relatives. This is the modern default for "I want a timestamp *and* an order".

### 4.4 Last-write-wins: the default that silently loses data

LWW is the most common conflict-resolution rule (Cassandra, DynamoDB's eventual-consistency path, Redis replication in some configurations, and almost every home-grown merge). It is implemented as "compare timestamps, keep the bigger one", and it has two independent failure modes:

1. **Skew reorders the writes.** With a ±10 ms skew between replicas and edits that can arrive within 10 ms of each other, the *older* edit wins whenever the losing writer's clock is behind. The window is small — but the "second edit within 10 ms" case is exactly the case where a user typed twice or a sync loop ran twice, i.e. the common case for automated clients.
2. **It discards the losing value with no record.** A deleted-then-edited item can lose the edit; a write from a device that was offline for three days (and stamped the write with its own clock at the time it *made* it) loses to a trivial write made yesterday. LWW has no concept of "these are different edits"; it has a concept of "one integer is smaller".

Better rules, in order of preference:

| Rule | Mechanism | When it is right |
|---|---|---|
| **Single writer per key** | Route all writes for a key to one owner | The default; eliminates the problem entirely |
| **Server-assigned version** | The store (or the owner) assigns a monotonic version | You need a total order and the client's clock is untrusted — always true |
| **Explicit merge (CRDT)** | Union for sets, `max` for counters, LWW *per field* rather than per record | Collaborative/last-writer-is-genuinely-right data (likes, presence, carts) |
| **Ask the user / business rule** | Reject concurrent edits, present both, require resolution | Rare, high-value data (a document, a price list) |
| **LWW on a server clock** | Only the *server* stamps | Acceptable compromise: the window becomes the server's own skew, usually ≤1 ms |

### 4.5 Using time safely: the four patterns that work

1. **Never trust a client clock.** A device can be 20 minutes off, deliberately or not. Stamp on the server; treat the client's timestamp as an *unverified hint* and store it in a separate column (`client_reported_at`) if you need it at all.
2. **Use monotonic time for anything duration-based**: timeouts, leases, backoff, rate-limit windows, cache TTLs *in-process*. Persist the *deadline* as an absolute server timestamp and compare it with the server's wall clock, which is the one clock you control.
3. **Budget for skew explicitly.** If leases are the mechanism, the safety condition is `lease_duration > max_clock_skew + max_processing_pause`. BookNook's 10 s lease is safe against a 2 s skew and not safe against a 38 s GC pause (L65 §7.2) — which is why fencing tokens (L78) exist and are mandatory regardless.
4. **When you need a global order, buy one of these three**: a **single writer** (partition ownership), a **log sequence** (Kafka offsets, database sequences — these are total orders by construction), or **consensus** (L81). Do not attempt to build a global order from timestamps; Spanner needed GPS receivers and atomic clocks to do it, and it still pays 7 ms per write to wait out the uncertainty.

## 5. Request / data flow

Two devices writing the same cart item, first with the naive design, then with the corrected one.

```
 ── NAIVE: client clocks + last-write-wins ────────────────────────────────────
  11:00:00.000  phone (clock 4 s SLOW) writes  qty=2   stamps .000
  11:00:00.100  laptop (clock correct) writes qty=3    stamps .100
                │
                ▼
        replica A receives phone first    → stores qty=2 @ .000
        replica B receives laptop first   → stores qty=3 @ .100
        anti-entropy / read-repair merge: LWW picks .100 (larger) → qty=3
                │
                ▼
  USER INTENT: they last touched the phone (qty=3 on the phone? no — qty=2)
  ACTUAL RESULT: laptop's older value wins. The phone write is gone forever.
  No error. No log. The user sees the wrong quantity at checkout.

 ── CORRECTED: server-assigned version + field-level merge ───────────────────
  11:00:00.000  phone POST /cart/42 {qty:2}
                  server (owner of cart:42) assigns version 17
  11:00:00.100  laptop POST /cart/42 {qty:3}
                  server assigns version 18 (monotonic counter, atomic)
                │
                ▼
        ONE writer per key → total order exists → no conflict possible.
        Replicas are followers: they apply 17 then 18. Done.

  11:00:05.000  phone goes offline; user edits to qty=5 offline
  11:00:09.000  laptop sets gift_note="Happy birthday"
  11:00:20.000  phone reconnects, POSTs {qty:5, base_version:18}
                │
                ▼
        server: version 19 = {qty:5, gift_note:"..."} — the field-level merge
        preserved the laptop's gift_note, because qty and gift_note are
        INDEPENDENT fields and the phone never touched gift_note.
        A record-level LWW would have thrown the gift note away.

 ── AND THE TIMESTAMP, CORRECTLY USED ────────────────────────────────────────
  Every row keeps created_at/updated_at from the SERVER, for display and
  analytics only. Nothing in the system's correctness depends on them.
```

The crucial structural move: **the conflict disappeared because writes for a key go to one owner with a counter**, not because the clocks got better. Clocks are a *display* feature here; the *ordering* feature is the version number.

## 6. ASCII diagram

Wall clocks versus causal order, in one picture:

```
        REALITY (causal)                        WHAT TIMESTAMPS SAY
   ────────────────────────────────      ─────────────────────────────────
   A: write v1 ────────────┐             A stamps 10:00:00.050
                           │ msg                 │
                           ▼                     ▼
   B: read v1 → write v2 ──┐             B stamps 10:00:00.020   ← SMALLER!
                           │                     │
                           ▼                     ▼
   C: read v2 → write v3          LWW concludes v2 is OLDER than v1 and
                                  discards it. Causality is violated by
      (v1 → v2 → v3 is a          3 ms of skew. Nothing alerts.
       total causal chain)

   The fix is not a better clock. The fix is to carry causality:
     Lamport:  v1 c=1 → v2 c=2 → v3 c=3        (order ✓, concurrency ✗)
     Vector :  v1 [1,0,0] → v2 [1,1,0] → v3 [1,1,1]   (order ✓, concurrency ✓)
     Single  :  v1 seq 41 → v2 seq 42 → v3 seq 43     (order ✓, cheapest)
     writer
```

The three-state comparison a version vector gives you:

```
             compare V(a) and V(b)
                      │
      ┌───────────────┼────────────────┐
      ▼               ▼                ▼
  V(a) ≤ V(b)     V(b) ≤ V(a)      neither
      │               │                │
   a → b           b → a          CONCURRENT
   keep b          keep a              │
                                  ┌────┴─────┐
                                  │  MERGE   │  (union / max / per-field
                                  │  or ASK  │   LWW / business rule)
                                  └──────────┘
```

TrueTime, and why Spanner pays for it:

```
   true time   ────────────────────●────────────────────►
                                   ▲ actual write instant

   TT.now() = [t - ε, t + ε]   where ε ≈ 1-7 ms with GPS + atomic clocks

   Spanner's rule: after assigning timestamp s, WAIT until TT.now().earliest > s
   ("commit-wait"). Only then is it guaranteed that no future transaction can be
   assigned a smaller timestamp. Cost: ~7 ms added to every write's latency,
   paid always, even with no failure anywhere in the system.

   Without GPS: ε is 10-100 ms, so commit-wait would be 10-100 ms per write —
   which is why nobody else does this and why everyone else uses HLC + a
   single writer per range instead.
```

## 7. Detailed example

### 7.1 The four-second phantom edit

BookNook's mobile client had a clock **4 seconds slow** (a common state on Android devices after a long sleep without network). The cart merge used client `updated_at` with LWW.

Sequence from a real support ticket:

```
  12:04:31 (phone clock)  user adds "Dune" to cart          → stamps 12:04:31
  12:04:33 (true time)    user switches to laptop, removes "Dune",
                          adds "Dune Messiah"               → stamps (correct) 12:04:33
  12:04:35                phone syncs; LWW compares 12:04:33 > 12:04:31
                          ⇒ laptop wins. Correct by luck this time.

  12:04:41 (true)         laptop adds gift wrap              → 12:04:41
  12:04:44 (true)         user on phone removes "Dune Messiah" → phone stamps
                          12:04:40 (4 s slow!)
                          ⇒ LWW: 12:04:41 > 12:04:40 ⇒ THE REMOVAL IS DISCARDED
                          ⇒ "Dune Messiah" is still in the cart at checkout
```

The user removed an item and paid for it. Support found it by replaying the cart's history in the warehouse, where the *server-side* `updated_at` showed the correct order — the storage layer had kept both edits and the merge had thrown one away.

The fix was structurally simple and removed the entire class of bug:

1. **The cart service owns carts.** All cart writes for `cart:{user_id}` go to the shard that owns that key (consistent hashing, L38), which assigns a **monotonic version**.
2. **Offline edits carry `base_version`**, and the server performs a **field-level merge**: independent fields merge, conflicting fields return `409` with both values (very rare — it requires the same field edited offline on two devices).
3. **`client_reported_at` is stored separately** for analytics and *never* used for ordering.
4. A metric `cart.offline_conflicts` was added, which turned out to be `0.004 %` of syncs — small enough that the 409 path never needed a UI beyond "we kept both, please pick".

The general lesson: the bug was not "clocks drift". The bug was **using a timestamp to decide a business outcome when a version number was available**. Clocks are unavoidable; *deciding* with them is optional.

### 7.2 The lease that granted two owners

A distributed lock service used a **10 s lease**, and the holder checked `now() < expiry` using the wall clock. During an NTP correction the holder's clock stepped **backwards 1.2 s**, so from its point of view the lease had 1.2 s *more* time than it really did. Meanwhile the lock service — whose clock was correct — expired the lease and granted it to another worker. For 1.2 s, two workers believed they held the lock, and both wrote to the same S3 prefix.

Three independent defects, each of which would have been sufficient:

- The **holder** used wall time to evaluate expiry instead of a monotonic deadline.
- The **lease duration** (10 s) did not budget for a 1.2 s step plus a 38 s GC pause — but see the next point.
- There was **no fencing token**, so the storage layer accepted the zombie's writes.

The third is the only one that is *sufficient by itself*: with a monotonically increasing token written alongside the object and a "reject lower token" rule, the stale worker's writes are refused no matter what its clock says. This is why the standard advice (Kleppmann's "How to do distributed locking") is that **a lease without fencing is not a lock** — a clock can always be wrong about how much time has passed.

## 8. Code or configuration example

```python
# ============================================================================
# BookNook: using time correctly. Illustrative, Python-like.
# ============================================================================

# --- 1. THE WRONG WAY (included because it is in most codebases) -------------
def is_expired_WRONG(lease):
    return time.time() > lease.expires_at     # wall clock: can step backwards
                                              # ← a 300ms backward step gives a
                                              #   dead lease extra life

# --- 2. Monotonic deadlines, in-process -------------------------------------
def call_with_deadline(fn, ms):
    deadline = time.monotonic() + ms / 1000     # immune to NTP steps
    return fn(deadline)
    # Rule: monotonic for DURATIONS, wall clock for DISPLAY. Never mix.

# --- 3. A persisted deadline (server-owned clock) ---------------------------
# Deadlines that outlive the process must be absolute timestamps from the
# SERVER's wall clock, because monotonic time has no meaning after a restart.
#   lease.expires_at = now() (server) + 10s
#   → compare against the SERVER's now(), and only on the server that issued it.
# Safety condition (write it in the code):
#   lease_duration > max_clock_skew + max_processing_pause
# ...and never rely on that inequality alone: use fencing (§4).

# --- 4. FENCING: the only defence that does not depend on clocks -----------
def renew_and_write(lock, key, value):
    token = lock.renew()             # strictly increasing across all holders
    ok = storage.put(key, value, if_match_greater_than=token)
    # storage rejects if an equal-or-higher token has already been seen.
    if not ok:
        raise Fenced("stale holder; abandon and re-acquire")
    # Now clock skew, GC pauses and network delays cannot corrupt the data.
    # The zombie learns it is a zombie at the moment it tries to write.

# --- 5. Lamport clock (order without time) ---------------------------------
class LamportClock:
    def __init__(self): self.c = 0
    def tick(self):                     # local event
        self.c += 1; return self.c
    def on_receive(self, msg_c):        # merge an incoming timestamp
        self.c = max(self.c, msg_c) + 1; return self.c
    # Guarantees: a → b  ⇒  c(a) < c(b)
    # Does NOT guarantee the converse. Cheap (8 bytes), useful for
    # "what could this node have known when it made this decision?"

# --- 6. Hybrid logical clock (what real databases use) --------------------
class HLC:
    def __init__(self, max_skew_ms=500):
        self.wall = 0; self.logical = 0; self.max_skew = max_skew_ms
    def now(self, physical_ms):
        if physical_ms > self.wall:
            self.wall = physical_ms; self.logical = 0
        else:
            self.logical += 1        # same millisecond, or our clock is behind
        return (self.wall, self.logical)
    def observe(self, remote):
        # CockroachDB/HLC rule: never let a message pull our clock FORWARD by
        # more than max_skew — otherwise one bad clock poisons everyone.
        if remote[0] - self.wall > self.max_skew:
            raise ClockSkewTooLarge(remote, self.wall)
        self.wall = max(self.wall, remote[0])
        self.logical = max(self.logical, remote[1]) + 1
        return (self.wall, self.logical)
    # Property: (wall, logical) is monotonic AND close to real time AND
    # causally consistent. Compare as a tuple: earlier tuple = earlier event.

# --- 7. Vector clock compare (concurrency detection) ---------------------
def compare(a: dict, b: dict):
    """Return 'before', 'after', 'concurrent' or 'equal'."""
    le = all(a.get(n, 0) <= b.get(n, 0) for n in set(a) | set(b))
    ge = all(b.get(n, 0) <= a.get(n, 0) for n in set(a) | set(b))
    if le and ge: return "equal"
    if le:       return "before"        # a → b
    if ge:       return "after"         # b → a
    return "concurrent"                  # MUST merge; there is no winner
```

```sql
-- ============================================================================
-- The SQL side: what to stamp with, and what never to sort by.
-- ============================================================================

-- --- 1. The LWW trap, visible in a schema --------------------------------
-- BAD: client-supplied timestamp used for conflict resolution.
--   updated_at TIMESTAMP,          -- from the app/device: UNTRUSTED
--   ...ON CONFLICT DO UPDATE SET qty = EXCLUDED.qty
--     WHERE EXCLUDED.updated_at > cart_items.updated_at;
-- A device 4 seconds slow loses edits forever (§7.1). Silent data loss.

-- --- 2. Correct: a server-assigned monotonic version --------------------
ALTER TABLE cart_items
  ADD COLUMN version BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),  -- display only
  ADD COLUMN client_reported_at TIMESTAMPTZ;                 -- untrusted hint

UPDATE cart_items
   SET qty = 5, version = version + 1, updated_at = now()
 WHERE cart_id = 42 AND item_id = 7 AND version = 18   -- optimistic concurrency
RETURNING version;                                     -- 0 rows = conflict → 409
-- A sequence is a TOTAL order assigned by one authority. It cannot be
-- fooled by skew, by a VM pause, or by a client that is "sure" it is 12:04.

-- --- 3. Where a timestamp IS correct: coarse, non-authoritative ordering ---
SELECT * FROM orders
 WHERE created_at >= now() - interval '1 day'
 ORDER BY created_at DESC;                 -- fine: display and analytics
-- Note the failure mode if you ever shard this: two shards, two clocks, and
-- created_at is no longer a global order. Merge on a sequence, not a time.

-- --- 4. Checking that your fleet's clocks are actually sane --------------
SELECT now() AS server_now,                -- compare across nodes in the fleet;
       pg_postmaster_start_time();         -- alert if max-min > 200ms in a DC
-- Equivalent check outside the DB:
--   chronyc tracking     → "System time" offset, and "Root dispersion" (bound)
--   ntpq -p              → per-peer offset
-- Alert on |offset| > 50ms in-DC, > 250ms cross-region, and on any step.
```

## 9. Numerical example

**Step 1 — how bad is an unsynchronised clock?** A commodity oscillator at **±100 ppm**:

```
  drift per second = 100 × 10⁻⁶ s        = 0.1 ms/s
  drift per day    = 0.1 ms/s × 86,400 s = 8.64 s/day
  drift per month                        = ~4.3 minutes
```

So a machine that loses NTP for a month is **minutes** wrong. A machine that loses it for a day is **seconds** wrong — which is exactly the state of the phone in §7.1.

**Step 2 — what NTP buys, and what it costs you in the conflict window.** With NTP in one datacentre, `|skew| ≤ 1–2 ms` typically. Now compute how often a *real* conflict can be mis-ordered:

```
  Two replicas accept writes for the same key.
  Skew bound = 2 ms.
  Writes to the same key arrive at both replicas within Δ = 2 ms
  of each other — i.e. only in the window where skew can reorder.

  If the key receives 0.5 writes/sec, P(two writes within 2ms) ≈ 0.001
  At 200,000 keys:  ≈ 200 potential mis-orderings per second across the
                     fleet, of which the ones that actually conflict (different
                     devices, different values) are a small fraction.
```

The number is small per key and **not small in aggregate**. That is the argument against "the probability is negligible": at fleet scale, a 2 ms window is a busy place. The argument is not that LWW is always wrong; it is that **you cannot bound the loss without bounding the skew, and you cannot bound the skew without hardware**.

**Step 3 — what a single-writer version costs instead.** Assigning a version at one owner:

```
  +1 network hop (already there — the write must reach the owner anyway): 0 extra
  +8 bytes per row: 400M rows × 8 B = 3.2 GB       (on a 1.2 TB DB: +0.27%)
  +0.001 ms of CPU per write (counter increment)
  conflicts: impossible for the same key
```

**0.27 % storage and no latency** buys the elimination of an entire class of silent data loss. This is the best trade in the lesson.

**Step 4 — vector clock size, which is why nobody uses them at scale.**

```
  n writers, 8 bytes per entry:
    n =   3  →  24 B/write   → trivial
    n =  20  → 160 B/write   → noticeable in a metadata-heavy store
    n = 200  → 1.6 KB/write  → 3.4× the size of a 470 B order row
```

Dynamo's answer was to cap the vector at `k` entries (with `k` typically 10) and drop the oldest — which reintroduces false concurrency, resolved by LWW on the pruned entries. This is the standard engineering compromise and worth stating precisely in an interview: **vector clocks give exact concurrency detection at `O(n)` cost; bounding them trades exactness for size.**

**Step 5 — TrueTime's commit-wait, in concurrency terms.** Spanner waits `ε ≈ 7 ms` per write. At BookNook's order-ingest scale of **5,000 writes/sec**:

```
  in-flight writes needed = 5,000 × 0.007 s = 35 concurrent transactions
  versus 5,000 × 0.004 s = 20 without commit-wait
  → +75% concurrency, and +3 ms added to every write's latency, forever
```

At 40 checkout writes/sec it is `40 × 0.007 = 0.28` in-flight — invisible. **Global ordering is cheap when your write rate is low and expensive when it is high**, which is why Spanner's design is right for its scale and wrong for a 5,000-writes/sec ingest pipeline that only needs per-customer order.

**Step 6 — the lease-safety inequality, with numbers.**

```
  Safe iff:  lease_duration > max_skew + max_pause

  Same-DC, NTP, no long pauses:   10 s > 2 ms + 50 ms        ✓ (over-provisioned)
  Same-DC, 38 s GC pause:         10 s > 2 ms + 38,000 ms    ✗ BROKEN
  ⇒ No lease duration survives an unbounded pause. Lengthening the lease only
    makes the failure rarer, never impossible. Fencing makes it harmless.
```

## 10. Trade-offs

| Decision | Buys you | Costs you | Choose it when |
|---|---|---|---|
| **Wall-clock timestamps for ordering** | Free, readable, sortable | Silent loss under skew; no bound on the error | Never for correctness — display and analytics only |
| **Single writer + sequence** | Exact total order, cheapest possible | Availability coupling to the owner; the owner is a bottleneck per key | Almost always — the default |
| **Lamport clock** | Causal total order, 8 bytes | Cannot detect concurrency | Log ordering, "what did this node know?" |
| **Vector clock** | Exact concurrency detection | `O(n)` metadata; needs pruning; merge logic required | Offline sync, multi-master key-value, collaborative state |
| **HLC** | Monotonic + causally consistent + human-readable | Not a *global* total order; needs a bounded-skew guard | The modern default for a timestamp that also orders |
| **TrueTime + commit-wait** | Real external consistency | GPS/atomic hardware; +ε on every write; single vendor | Global financial data where "just a few ms of uncertainty" is unacceptable |
| **LWW on a server clock** | Simple, deterministic | Discards a concurrent edit silently; skew window becomes the server's | Low-value data: presence, likes, last-active |
| **Ask the user (409)** | No data loss | UX friction; needs a UI for the conflict | High-value, rarely-conflicting data |
| **Monotonic clocks everywhere** | Immune to steps | No persistence across restart; no meaning to humans | All durations, timeouts and leases |

## 11. Failure scenarios

**11.1 — NTP step makes IDs collide.** A snowflake-style ID generator composed of `timestamp + worker_id + sequence`. The clock steps backwards 500 ms; the generator reissues timestamps it has already used, and with the same `worker_id` it produces **duplicate IDs**. **Symptom:** primary-key violations, or silent overwrites if the ID is a key. **Fix:** generators must refuse to emit when the clock goes backwards (wait or raise), and IDs must include a boot-unique component.

**11.2 — `updated_at` ties.** Two rows written in the same millisecond, `ORDER BY updated_at DESC LIMIT 1` returns a nondeterministic winner. **Symptom:** flaky tests, "sometimes the old value wins", impossible to reproduce. **Fix:** order by `(version)` or `(updated_at, id)` — never by a timestamp alone.

**11.3 — The leap-second livelock (2012).** A widely-deployed kernel bug caused high-CPU livelocks on many Linux servers simultaneously when a leap second was inserted; the shared, synchronised *timing* of the event made it a correlated failure. **Symptom:** a fleet-wide incident at an instant nobody chose. **Fix:** leap smearing, and treating "the clock" as a dependency with its own incident history.

**11.4 — VM suspend breaks a lease.** A hypervisor suspends a VM for 90 s during live migration. The guest's monotonic clock does not advance during suspension in the way you expect, the wall clock corrects on resume, and the lease expired 80 s ago — yet the process resumes mid-critical-section. **Symptom:** a write from "the past" corrupting state. **Fix:** fencing tokens, plus liveness checks at the start of any critical section that must not run stale.

**11.5 — Cross-region analytics are wrong by an hour.** Two regions stamp into a shared warehouse; one region's NTP was pointed at an internal server that was itself wrong by an hour (DST misconfiguration). Dashboards overlap and double-count. **Symptom:** analytics that cannot be reconciled with billing. **Fix:** stamp with a *server-assigned* ingest timestamp at a single point, keep the source timestamp as a separate untrusted column, and alert on fleet clock offset.

**11.6 — Offline device with a future clock.** A device's clock is 3 days fast (a common state after a manual time change). Its writes carry future timestamps, so every subsequent legitimate write from every other device loses to it for 3 days. **Symptom:** "my edits don't save" for every user of that cart/account, with a bizarre data pattern. **Fix:** never trust client clocks for ordering; clamp implausible timestamps and alert.

**11.7 — Timeout computed from the wall clock.** A request's total budget is computed as `deadline = now() + 800ms` in wall-clock terms. An NTP step forward of 900 ms makes every in-flight request instantly "expired", producing a synchronised burst of failures. **Symptom:** a synchronised error spike with no traffic change. **Fix:** monotonic clocks for all deadlines.

## 12. Alternatives

| Instead of timestamps for ordering | Use | When |
|---|---|---|
| `ORDER BY created_at` | A sequence/version column | Any ordering that affects correctness |
| Client-supplied `updated_at` for LWW | Server-assigned version | Always |
| Comparing timestamps across nodes | A single writer per key (partition ownership) | The default in any sharded system |
| Timestamps in a distributed log | Log offsets / Kafka partition offsets | Event ordering in streams (L70, L72) |
| Timestamps for "what happened before what" in debugging | Distributed traces with parent/child spans | Always (L88) |
| Lamport clocks | Vector clocks or HLC | You need concurrency detection, or a usable wall-time component |
| HLC | TrueTime | You need globally meaningful timestamps and own the hardware |

## 13. When to use it

- **Every time you write a timestamp**, ask which clock and what it is for. That single habit prevents most of this lesson.
- **Any conflict-resolution rule.** If it compares times, replace it with a version.
- **Any lease, timeout, TTL or rate limiter.** Monotonic in-process, server-owned absolute when persisted.
- **Any ID generator** that embeds time (snowflake, ULID, UUIDv7). They must handle backwards steps explicitly.
- **Offline-first or multi-device sync** — vector clocks or HLCs plus per-field merge.
- **When debugging**, use causal metadata (trace IDs, correlation IDs, parent spans), never timestamp sort order.

## 14. When *not* to use it

- **Inside one process, for durations.** Monotonic time is sufficient and simpler; do not build a logical clock for a stopwatch.
- **A single-node database ordering its own rows.** `now()` from one server is a fine total-order source (still prefer a sequence for correctness).
- **Coarse analytics and display.** Wall-clock timestamps are exactly right; do not over-engineer "when did this happen" for a dashboard.
- **When a single writer already exists.** Adding vector clocks to a system that routes by key adds metadata and no guarantee.
- **As a substitute for idempotency.** A correct clock does not make a retried charge safe (L65, L74).
- **Where the business rule is "newest wins by definition"** — a genuinely last-write-wins field (a user's "last seen") needs no more machinery than a server clock.

## 15. Common mistakes

1. **Using `System.currentTimeMillis()` for timeouts.** Wall clock steps; a timeout must not be able to go backwards.
2. **Using client timestamps to resolve conflicts.** The single most common cause of silent data loss in sync systems.
3. **`ORDER BY timestamp` with no tiebreaker.** Nondeterministic, flaky, and wrong under 1 ms concurrency.
4. **Believing NTP makes clocks equal.** It bounds the error; it does not remove it, and the bound is your only real guarantee.
5. **Comparing timestamps across nodes as if they were a global order.** They are not, and the error is unobservable without a trace.
6. **Ignoring clock steps in ID generators.** Duplicate IDs on a backwards step.
7. **Assuming a suspended or paused process knows how long it was paused.** It does not; only fencing keeps the resource safe.
8. **Storing `updated_at` from the app and calling it an audit trail.** An untrusted clock cannot audit anything.
9. **Vector clocks with no pruning.** Metadata grows without bound and eventually dominates the payload.
10. **Treating "concurrent" as "conflicting".** Many concurrent events merge cleanly (sets, counters); LWW throws that information away before you can use it.
11. **Lease durations chosen without a `max_skew + max_pause` budget** — and without fencing as the backstop.
12. **Fleet clock offset never monitored.** It is a silent input to every ordering decision you make; alert on it like any other dependency.

## 16. Interview perspective

**What is actually being tested.** Whether you know the difference between **when** something happened and **what happened first**, and whether you can build a system that never needs the first to answer the second. Strong candidates say "I would not use a timestamp here" before being asked, and can name the mechanism they would use instead (version, sequence, offset, single writer). The second thing tested is precision: `skew` vs `drift`, Lamport vs vector vs HLC, and what each actually guarantees.

**Answers that land well:**

- *"How do you resolve conflicting writes from two devices?"* — "Not with timestamps — device clocks can be seconds out, and the failure is silent. Writes for a key go to a single owner that assigns a monotonic version, and I merge at field level for independent fields. If two devices truly edited the same field offline, I return both and let the business rule decide. Client time is stored as an untrusted hint and never used for ordering."
- *"Why not just sync clocks with NTP?"* — "Because it gives a bound, not equality — 1–2 ms in a DC, tens of ms across regions — and that bound is the width of the window in which your last-write-wins rule can lose data. At fleet scale a 2 ms window is busy. NTP is right for display and for detecting broken nodes; it is the wrong input for deciding which write survives."
- *"What is a vector clock for?"* — "Detecting concurrency exactly. Lamport clocks give an order consistent with causality but cannot tell you when two events are concurrent; vectors can, at O(writers) metadata cost, which is why Dynamo capped them and why most systems prefer a single writer per key and skip vectors entirely."
- *"How long should a lease be?"* — "Longer than max clock skew plus max pause — and I would not rely on that inequality, because pauses are unbounded. Leases come with fencing tokens that the resource enforces, so a holder that was paused past expiry cannot corrupt anything when it wakes up."

**Common interview tasks:** order a set of events given a mix of local and message-related ones; design conflict resolution for offline edits; explain why last-write-wins loses data; choose clocks for timeouts vs display; explain how a distributed lock can fail and how fencing fixes it; design monotonic IDs; explain Spanner's need for TrueTime.

**Red flags interviewers watch for:**

- "Use the timestamp and take the latest" with no mention of skew.
- Not knowing that NTP can step the clock backwards.
- No distinction between wall clock and monotonic clock.
- Confusing Lamport clocks with vector clocks (or claiming Lamport detects concurrency).
- Believing a long-enough lease makes a lock safe without fencing.
- No awareness that a paused process can wake up believing its lease is valid.
- Thinking clocks are only a display concern, or only a correctness concern, but never distinguishing.
- No mention of storing versions or sequence numbers at all.
- Being unable to say what "concurrent" means for two events.

## 17. Knowledge check

**Conceptual**

1. Define skew and drift, state a realistic magnitude for a commodity quartz oscillator per day, and explain why synchronisation reduces but never eliminates the problem. Name the specific quantity that a correct design must budget for, and where it appears in the lease-safety inequality.
2. Explain the difference between a Lamport clock and a vector clock in terms of the guarantees each provides. Give one concrete scenario where a Lamport clock is sufficient and one where only a vector clock will do, and state the cost difference.
3. Explain why last-write-wins can silently discard a newer edit, describe the precise conditions required for it to happen, and give three ordered alternatives, with the one you would choose for a shopping cart and why.

**Practical scenario**

4. BookNook's mobile app supports offline cart editing across up to three devices per account, plus a server-side "recently viewed" list written by a recommendation service. Requirements: no lost edits for quantities, gift notes must never be silently dropped, a "last active" timestamp is display-only and may be approximate, and the operations team must be able to explain to a support agent why a cart contains what it contains. Design the full time-and-ordering strategy: which clock each timestamp uses, what is used for conflict resolution, the version/vector scheme with its size bounds, the merge rules per field, the clock-health monitoring and alerts, the data retained for explaining a cart's history, and what happens when a device reports a timestamp three days in the future.

**Calculation**

5. Two replicas accept writes for the same key. NTP bounds skew at **3 ms** in-region. Writes for the key arrive at **0.2/sec on average** from independent clients. (a) Estimate the probability that two writes fall within the skew window, per key per day. (b) If the fleet has **500,000** such keys, estimate the number of potentially mis-ordered pairs per day. (c) Compute the storage cost of adding an 8-byte version to 400 M rows, as a percentage of a 1.2 TB database. (d) State, with that comparison, the argument you would make in a design review.

6. A lease is 10 s, heartbeat 1 s, in-region skew 3 ms, and the longest observed GC pause is 800 ms. (a) State whether the inequality `lease > skew + pause` holds. (b) Recompute with a 38 s pause observed during a load test, and state whether *any* lease duration would be safe. (c) Compute the number of leases issued per day at 50 shards with continuous failover testing if each shard re-elects every 10 s, and (d) state why fencing tokens are required regardless of your answer to (a).

**Progress checklist**

- Phase 1 — Foundations (L1–17): **complete** ✅
- Phase 2 — Scaling (L18–39): **complete** ✅
- Phase 3 — Data and Storage (L40–63): **complete** ✅
- Phase 4 — Distributed Systems (L64–92):
  - [x] 1. Why distributed systems are difficult (L64)
  - [x] 2. Partial failures and network partitions (L65)
  - [x] **3. Clock differences and ordering events (L66) ← you are here**
  - [ ] 4. Synchronous versus asynchronous workflows (L67)
  - [ ] 5. Message queues (L68)
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

**NEXT LESSON: Phase 4, Topic 4 — Synchronous versus asynchronous workflows**

---
