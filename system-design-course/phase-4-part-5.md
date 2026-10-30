# Lesson 76 — Backpressure and Load Shedding

*Phase 4, Topic 13*

## 1. Simple definition

Two mechanisms for the same problem — *more work is arriving than can be done* — differing in who is told.

**Backpressure** tells the *sender* to slow down: a bounded queue that refuses new work, a credit window that stops granting permission, a concurrency limit that blocks new admissions, a `503` with `Retry-After`. The work is not done, and the sender knows it and is expected to react.

**Load shedding** is the deliberate choice to *drop* work, at the earliest possible point, in a defined priority order, so that the work you *do* accept completes successfully. It is the same decision as backpressure, made unilaterally when the sender cannot or will not slow down — which is the normal case, because users and retry loops do not take hints.

Both exist because of one uncomfortable fact about queueing: **a system at 100 % utilisation does not do 100 % of the work — it does almost none of it, slowly.** Response time grows as `S / (1 − ρ)`, so at 95 % utilisation a job that takes 50 ms takes 1 second, at 99 % it takes 5 seconds, and every one of those slow jobs is holding a thread, a connection, and memory. The only three outcomes at saturation are:

1. **Collapse** — accept everything, run out of some resource (threads, connections, memory, sockets), and fail *everything*, including work that would have been fast. This is the default outcome of doing nothing.
2. **Backpressure** — accept what you can, refuse the rest quickly and honestly, so that accepted work succeeds. Requires the caller to cooperate.
3. **Load shedding** — same as (2), but the refusal is a *drop*, chosen by policy, when the caller cannot be relied on to back off.

Terms defined before use:

- **Utilisation (ρ)**: arrival rate ÷ service capacity. The single number that predicts latency.
- **Admission control**: the decision, at the front door, about whether to accept a request at all.
- **Bounded queue**: a queue with a maximum length, which is the *only* kind of queue that provides backpressure. An unbounded queue is a memory leak with a delay.
- **Concurrency limit**: a cap on simultaneously in-flight requests to a dependency — the practical form of backpressure inside a service.
- **Queue discipline**: the order work is served — FIFO, LIFO, priority, deadline-aware. Under overload, the discipline decides *which* work survives.
- **Goodput**: successful, useful responses per second. Distinct from throughput, which counts attempts. A saturated system has high throughput and collapsing goodput.
- **Retry budget**: a cap on retries as a *fraction of normal traffic*, so that retries cannot become the load (L75 §11.4).

The one sentence to carry out of this lesson: **when you are over capacity, the only choice you get is *which* work to drop — pretending otherwise means a resource picks for you, and it will pick everything.**

## 2. Why it exists

Every system has a design point, and every system meets traffic beyond it: a flash sale (L68 §7.1), a retry storm (L65 §9), a hot key (L72 §11.4), a downstream outage that slows everything (L77), or an autoscaler that cannot boot fast enough (Phase 2, L39). The question is not whether you will exceed capacity; it is what happens in the following 90 seconds.

BookNook's search service, measured:

```
  concurrency (in-flight)   throughput      p50         p99        goodput
  ─────────────────────────────────────────────────────────────────────────
        50 (ρ≈0.5)           1,900/s        26 ms       60 ms      1,900/s
       100 (ρ≈0.75)          2,400/s        42 ms      120 ms      2,390/s
       150 (ρ≈0.90)          2,500/s        95 ms      480 ms      2,300/s
       190 (ρ≈0.95)          2,520/s       210 ms     1,900 ms     1,600/s
       200 (ρ≈1.00)          2,530/s       900 ms    12,000 ms       300/s
       200 + retries        2,900/s     2,400 ms    30,000 ms         0/s
```

Read the last two rows carefully, because they are the entire lesson:

- **Throughput is flat** from 150 concurrency onward — the server is doing its maximum work. Adding load adds *no* output.
- **p99 collapses by 60×** between 150 and 200 concurrency, because queued work waits behind other queued work.
- **Goodput falls off a cliff**, because at 12-second latencies the callers time out and retry: the work *is done* and *nobody hears about it*.
- And then retries make it worse, which is the definition of a **metastable failure** (L65 §9 step 3): the system now fails because it fails.

The counterfactual, which is what makes this lesson actionable: if the search service had **shed 25 % of requests immediately at 190 concurrency**, it would have kept `ρ ≈ 0.9`, served 1,875 requests/sec at 95 ms p50 with a 480 ms p99, and **returned 1,875 successes instead of 300**. Shedding work made the system serve six times more of it. That is not a paradox; it is what happens when you stop spending resources on requests that cannot complete in time.

The second reason this lesson exists: **almost every backpressure mechanism is invisible until it is needed.** A bounded queue, a concurrency limit, and a `429` cost nothing in normal operation and look like engineering pessimism in a design review. They are also the difference between a degraded experience and an outage — and the reason an SRE will ask "what is your shedding policy?" before "what is your autoscaling policy?".

## 3. Real-world analogy

**Motorway on-ramp metering signals.**

A motorway at capacity does not flow faster when more cars join it; it locks up — the classic "phantom traffic jam" that appears with no crash at all. The standard, empirically validated fix is a **red light on the on-ramp** that holds cars back, letting them join at the rate the motorway can absorb. Cars are *deliberately* delayed at the ramp so that the main carriageway keeps moving.

Every property of this lesson is in that analogy:

- **Shedding at the edge is the cheapest place to shed.** A car held at the ramp costs almost nothing. A car that enters and locks the motorway costs every other driver their morning. This is why admission control belongs at the gateway, not at the database.
- **The alternative to metering is gridlock**: everyone who entered is delayed, including those who would have flowed freely. Throughput (cars passing per hour) *rises* when you admit fewer cars. That is the goodput curve, and it confuses people on graphs for exactly as long as it takes them to think about a jam.
- **Ramp metering needs a cooperating driver.** The light works because ignoring it is illegal and physically blocked by a barrier. **Users and retry loops have no barrier**, which is why software also needs shedding (dropping) and not only backpressure (asking). You cannot put a red light in a retry loop; you can only refuse it.
- **Priority matters, and it is a policy decision.** Ambulances, buses, and cars with three passengers may get a bypass lane. Deciding *which* traffic is privileged is a product decision made once, in advance — and getting it wrong (letting a viral marketing campaign starve paid customers) is the priority-inversion failure in §11.5.

The final, and most useful, part of the analogy: **metering is only acceptable when it is honest.** Signage that says "motorway closed, use alternative route" produces sane driver behaviour; a sign that says "10 minutes" while the jam is 90 minutes produces drivers who leave the motorway and rejoin it, adding load in both places. That is a `503` without `Retry-After`, or a load balancer that silently queue-holds instead of refusing — retries and re-joins, which is how a metered system becomes an unmetered one.

## 4. Detailed technical explanation

### 4.1 The utilisation curve, which decides everything

A queue with `ρ < 1` has response time `R = S / (1 − ρ)` for a single server (M/M/1), where `S` is service time. The consequences at `S = 50 ms`:

| ρ | `R` | p99 outcome | Practical interpretation |
|---|---|---|---|
| 0.50 | 100 ms | stable | Comfortable. Headroom for bursts and failures |
| 0.70 | 167 ms | stable | Normal healthy peak |
| 0.80 | 250 ms | stable | **The practical ceiling.** Above this, small changes are large |
| 0.90 | 500 ms | tail rising | Degraded; one dependency hiccup tips it |
| 0.95 | 1,000 ms | unstable | Timeouts begin; retries appear; ρ climbs further |
| 0.99 | 5,000 ms | collapsed | Every request effectively fails, expensively |
| 1.00 | ∞ | down | Resources exhausted; even trivial work fails |

Two operational rules follow, and they are the ones to state in a review:

1. **Design for ρ ≈ 0.7 at peak**, not 0.9. The last 20 % of capacity is not usable capacity — it is a latency amplifier with no throughput benefit (the flat throughput rows in §2).
2. **The dangerous zone is not saturation, it is 85–95 %**, because the system still *looks* healthy on throughput and error metrics while p99 quietly explodes. Alarms must be on **latency and saturation**, not only on errors.

### 4.2 The mechanisms of backpressure, from the wire upward

| Layer | Mechanism | What it signals | Failure if absent |
|---|---|---|---|
| **TCP** | Receive window / congestion window | "Stop sending, my buffer is full" | Kernel buffer overflow → drops → retransmits |
| **HTTP/2** | Stream and connection flow-control windows | Per-stream credit | One fat response starves the connection |
| **Broker** | Prefetch / QoS (L68), bounded queue length | "You may hold N unacked" | Consumer's memory grows until OOM |
| **Connection pool** | Max pool size, acquisition timeout | "No connections; fail fast" | Requests queue on a lock; p99 explosion |
| **Thread/worker pool** | Bounded pool, queue with a cap | "No worker" | Unbounded task queue, OOM, or thread-per-request death |
| **Concurrency limit** | Semaphore/Little's Law cap per dependency | "N in flight max" | A slow dependency consumes every thread (L77 §bulkhead) |
| **Rate limiter** | Token bucket per key (tenant, user, IP, endpoint) | `429` + `Retry-After` | Noisy neighbours starve everyone |
| **Admission control** | Priority/credit decision at the edge | Refusal, or degraded response | Work enters that cannot complete |

The most important row is **concurrency limit**, because it is the one you control inside your own process and it is derived from arithmetic rather than taste:

```
  Little's Law (L14): in-flight = throughput × latency
  Target: 2,000 RPS at 50 ms service time
      in-flight needed = 2,000 × 0.05 = 100
  Set the limit slightly above: 120 (20 % headroom)
  At more than 120 in flight, latency is already past the target, so admitting
  the 121st request only delays the 120 that were on time.
  ⇒ A concurrency limit is a *latency* guarantee expressed as a queue policy.
```

### 4.3 Load shedding: what to drop, and in what order

Shedding requires a **priority policy**, decided in advance. The five policies that matter:

| Policy | Behaviour | When it is right |
|---|---|---|
| **Drop newest** | Reject arrivals; keep processing what is queued | The queued work is still valuable and bounded |
| **Drop oldest** | Discard the head of the queue to make room | Stale work is worthless (market prices, presence, live metrics) |
| **Priority / tier** | Shed low-value classes first (anonymous, free tier, batch, prefetch) | There are identifiable classes with different business value |
| **Deadline-aware** | Drop any request whose deadline has already passed | Latency-sensitive APIs; the work would be wasted anyway |
| **Random** | Uniform drop at a target rate | Homogeneous traffic where any drop is equally bad |

And the location decision, which is worth more than the policy: **shed as early and as cheaply as possible.** A request rejected at the CDN costs a TLS handshake's worth of work. Rejected at the gateway, a few hundred microseconds. Rejected after 8 service calls, thirty seconds of CPU and a locked row — which is why "the database rejected it with a constraint violation" is not load shedding, it is a post-mortem.

### 4.4 Why "just accept everything and queue it" fails

Because queues do not create capacity, and every queued item consumes memory *and* its own latency budget:

```
  arrivals 3,000/s, capacity 2,500/s
  backlog growth = 500/s
  after 60 s: 30,000 queued items × ~2 KB of request state = 60 MB
  after 10 min: 300,000 items = 600 MB, and the oldest has waited 120 s
  ⇒ everything served now is beyond any client's timeout, so the work is
    WASTED, and the memory pressure risks a restart that fails even the
    requests that would have been fast.
```

An unbounded queue converts a throughput problem into (a) a latency problem, then (b) a memory problem, then (c) a crash. A bounded queue converts it into a *refusal*, which is honest and cheap.

### 4.5 What the client must do, or shedding becomes retrying

Shedding only works if refusals are believed. Three protocols make that true:

1. **`429` / `503` with `Retry-After`** — and the client must honour it. A retry immediately after a `429` is a load amplifier.
2. **Retry budgets** — a cap on retries as a fraction of successful traffic (e.g. 10 %). This is the mechanism that prevents the whole *fleet* from over-retrying, which per-request attempt caps do not.
3. **Backoff with full jitter** (L65 §8.3) — so refusals do not return in a synchronised wave.

And the honest failure: if the client cannot be modified (a partner, a mobile app version in the wild), then shedding *is* the error the user sees, and the design must decide what that looks like — a degraded page, a queue-and-notify flow, or an explicit "we're busy".

### 4.6 Degradation, which is better than shedding

The best answer to "we cannot do all of this" is usually "we can do a cheaper version of this":

| Degradation | Example | Cost |
|---|---|---|
| **Serve stale** | Cache with `stale-while-revalidate` (L29) | Bounded staleness |
| **Serve partial** | 20 of 50 search results, without facets | Honest incompleteness |
| **Serve cheaper** | Drop personalisation; return the global ranking | Worse relevance |
| **Defer** | "Your report will be emailed" (queue + notify) | Latency, plus a job to run |
| **Drop non-essential** | Skip analytics, recommendations, prefetch | Impossible to notice, from the user's perspective |

That last row is the cheapest capacity in any system: when load exceeds capacity, **analytics and prefetch should be the first things to go**, and they should be behind the same shedding switch as everything else.

## 5. Request / data flow

BookNook's product page at 3× normal load, with shedding layers acting in order. This is the sequence to be able to narrate, because each layer removes work that the next one would otherwise do badly.

```
  arrival rate 9,000 rps (3x nominal). Capacity at target latency: 3,000 rps.

  ── LAYER 1: CDN / edge ────────────────────────────────────────────────────
      cached product HTML for 60 s → ~65 % served without touching origin
      ⇒ origin sees 3,150 rps. Cheapest possible shed: work never created.

  ── LAYER 2: API gateway, per-tier token bucket ────────────────────────────
      anonymous traffic: limited to 1,500 rps → excess gets 429 + Retry-After: 5
      logged-in:         2,000 rps
      checkout-adjacent: NEVER limited (priority class)
      ⇒ 300 rps refused here, at ~0.3 ms of cost each. 2,850 admitted.

  ── LAYER 3: per-service concurrency limit (semaphore) ─────────────────────
      catalog-svc limit = 120 in flight (Little's Law: 2,000 rps × 50 ms + 20 %)
      the 121st concurrent request waits up to 5 ms for a permit, then gets 503
      ⇒ queueing *time* is bounded at 5 ms, so latency cannot explode.
      This is the layer that prevents §2's collapse.

  ── LAYER 4: dependency-level shedding inside the service ─────────────────
      recommendations: 300 ms budget, but a hard 50 ms timeout + shed-on-timeout
        ⇒ when recommendation-svc slows, the page renders WITHOUT the carousel
           rather than slowly. Degradation, chosen in advance (§4.6).
      reviews: LIFO queue of 200 — under pressure, the NEWEST is served first,
        because a stale review list is worse than a shorter one (§4.3).

  ── LAYER 5: bounded queues for async work ────────────────────────────────
      email queue max-length 200,000 with reject-publish (L68 §4.1)
      ⇒ when the renderer is behind, the *producer* gets an error, which is
        visible, rather than an unbounded queue, which is a memory bomb.

  ── WHAT THE USER SEES ───────────────────────────────────────────────────
      anonymous, cached:        normal page, 40 ms                     ✓
      anonymous, uncached:      429 → client waits 5 s, retries once   ✓
      logged-in:                normal page, 60 ms, no carousel        ✓
      checkout:                 unaffected, 90 ms                      ✓
      (Without layers 2–3: everyone gets a 12-second timeout and the
       database gets 9,000 rps, until it falls over.)
```

The critical detail is the **ordering**: caching (work never created) → rate limiting (work refused cheaply) → concurrency limits (queueing bounded) → degradation (work made cheaper) → bounded queues (async work refused visibly). Each layer is cheaper than the next, and the cheapest layer is always the one that stops the work existing.

## 6. ASCII diagram

The utilisation curve, which is the graph to draw on a whiteboard:

```
  latency
     ▲
     │                                                    ╱
     │                                                  ╱   ← collapse
     │                                                ╱
     │                                             ╱
     │                                       ╱ ╱
     │                             ╱───────╱
     │                    ╱────────╱
     │        ────────────╱                    ← the knee (ρ ≈ 0.8)
     │  ──────╱
     └──┬────┬────┬────┬────┬────┬────┬────────────────────► utilisation ρ
        0.5  0.6  0.7  0.8  0.9  0.95  1.0

  throughput
     ▲        ────────────────────────────────────────  ← flat: no more output
     │   ────╱
     │  ╱
     └──┴────────────────────────────────────────────────► offered load
        the server's maximum is reached well BEFORE saturation, and stays there

  goodput (successes)
     ▲
     │        ────────●
     │      ╱          ╲
     │    ╱              ╲ ← work done, then timed out: pure waste
     │  ╱                  ╲
     └──┴─────────────────────╲──────────────────────────► offered load
        ⇒ beyond the knee, MORE load means LESS done.
```

The shedding decision tree, with the priority policy made explicit:

```
  request arrives, ρ > 0.8
        │
        ├─ Is it checkout / payment / auth?  ──► NEVER shed (priority class)
        │
        ├─ Can I serve a cheaper version?    ──► DEGRADE
        │     (stale cache, no personalisation, fewer results, no carousel)
        │
        ├─ Is its deadline already passed?   ──► SHED NOW (work would be wasted)
        │
        ├─ Is it analytics / prefetch / bot? ──► SHED FIRST
        │
        ├─ Does the caller honour Retry-After? ─► 429 + Retry-After (backpressure)
        │
        └─ otherwise                          ──► SHED (drop), log the class,
                                                  alarm on the shed rate
```

## 7. Detailed example

### 7.1 Black Friday, and the 25 % that saved the site

BookNook's 2025 sale pushed search traffic to **3.2× nominal**. The previous year's behaviour was the §2 table: full acceptance, threads exhausted, p99 at 30 s, and a **17-minute total outage** that started 4 minutes into the sale.

The fixed design had four changes, all of them cheap:

| Change | Mechanism | Effect measured |
|---|---|---|
| **Edge caching with `stale-while-revalidate`** | HTML cached 60 s, served stale up to 300 s while refreshing | Origin load −65 % |
| **Per-tier rate limits at the gateway** | Anonymous 1,500 rps; logged-in 2,500; checkout unlimited | Refused 400 rps at 0.3 ms each |
| **Concurrency limit on search** | Semaphore at 120 (Little's Law), 5 ms acquire timeout, then `503` | p99 1,900 ms → 480 ms |
| **Recommendation carousel made optional** | 50 ms timeout, shed on timeout, page renders without it | Removed the biggest p99 contributor |

Results, measured against the same traffic profile replayed in a load test:

```
  BEFORE:  3,000 rps accepted, 2,530 rps processed, p50 900 ms, p99 12 s,
           success 12 %, outage after 4 min.
  AFTER:   2,850 rps admitted, 2,850 accepted of which 2,400 fully served,
           450 degraded (no carousel), 400 shed with 429, p50 95 ms,
           p99 480 ms, success 99.4 %, no outage.
```

The line worth staring at: **the "after" system served fewer requests per second than the "before" system attempted, and ten times more successful ones.** That is the goodput argument in one comparison, and it is why the SRE's question is about shedding policy rather than capacity.

### 7.2 The unbounded queue that took the whole cluster down

A marketing-preview feature enqueued render jobs into an in-process queue with no length cap, drained by 4 workers. During a sale it received **40,000 jobs/minute** against a drain rate of **2,400/minute**.

```
   backlog growth = 40,000 − 2,400 = 37,600/min
   at ~12 KB per job (payload + rendered HTML fragment)   = 451 MB/min
   ⇒ 9 minutes to exhaust a 4 GB heap → OOM kill
   ⇒ the pod restarts, draining NOTHING, and is immediately re-fed by the
     still-arriving traffic → crash loop → the load balancer has no healthy
     instances → the *product page* fails, though the feature was unrelated.
```

The fix was three lines and a policy: a **bounded queue (2,000) with a shed-newest policy**, a metric on shed count by feature, and the preview feature registered as **lowest priority** so that it could not consume shared capacity. The lesson: **an unbounded queue in one feature is an availability risk for every feature in that process**, because memory and threads are shared. Bulkheads (L77) are the structural version of this fix.

## 8. Code or configuration example

```python
# ============================================================================
# BookNook: backpressure and shedding primitives.
# ============================================================================

# --- 1. Concurrency limit derived from Little's Law — not from taste -------
import asyncio

class ConcurrencyLimiter:
    def __init__(self, max_in_flight: int, acquire_timeout_ms: int):
        self.sem = asyncio.Semaphore(max_in_flight)
        self.timeout = acquire_timeout_ms / 1000

    async def run(self, fn, *a):
        try:
            # Bounded wait: if a permit is not free quickly, SHED. Queueing for
            # a permit is just a queue with worse visibility.
            await asyncio.wait_for(self.sem.acquire(), self.timeout)
        except asyncio.TimeoutError:
            metrics.incr("shed.concurrency")
            raise Shed("busy")          # → 503 + Retry-After at the edge
        try:
            return await fn(*a)
        finally:
            self.sem.release()

# max_in_flight = 2,000 rps × 0.05 s × 1.2 = 120  (see §4.2)


# --- 2. Deadline-aware shedding: never start work that cannot finish ------
def admit(ctx, work_budget_ms: int) -> bool:
    if ctx.deadline_remaining_ms < work_budget_ms:
        metrics.incr("shed.deadline")
        return False                 # the work would be wasted AND would
    return True                      #   delay work that could still make it


# --- 3. Priority shedding: the policy from §4.3, enforced at one place ----
PRIORITY = {"checkout": 100, "auth": 100, "catalog": 50, "search": 40,
            "recommendations": 10, "analytics": 1, "prefetch": 1}

def decide(ctx, load: float):
    """load = current concurrency / limit. Shed lowest priority first."""
    thresholds = {100: 1.00,   # never shed
                  50: 0.95,    # shed above 95 %
                  40: 0.90,
                  10: 0.80,    # recommendations go early: §7.1
                  1:  0.60}    # analytics/prefetch go first
    if load > thresholds[PRIORITY[ctx.feature]]:
        return "shed"
    return "admit"


# --- 4. Honest refusal: 429 with Retry-After, and ALWAYS a metric ---------
def shed_response(reason: str, retry_after_s: int = 5):
    metrics.incr("shed", tags={"reason": reason})
    # A refusal without Retry-After is a retry loop's invitation (§4.5)
    return Response(status=429, headers={"Retry-After": str(retry_after_s)},
                    body={"error": "busy", "retry_after_s": retry_after_s})
```

```nginx
# --- Gateway: per-tier rate limiting (token bucket), cheap shedding --------
limit_req_zone $binary_remote_addr zone=anon:20m  rate=25r/s;
limit_req_zone $http_x_user_id     zone=users:20m rate=200r/s;

location /api/search {
    limit_req zone=anon burst=50 nodelay;      # nodelay: SHED, do not queue
    limit_req_status 429;                      # nodelay + burst = bounded queue
    add_header Retry-After 5 always;           # tell the client what to do
    proxy_pass http://search_upstream;
}
# ⚠ Without `nodelay`, nginx QUEUES requests up to `burst` — which is a form of
#   acceptance, not shedding. Prefer explicit refusals over invisible queueing.
```

```yaml
# --- Async work: bounded queues are the only real backpressure -------------
queue:
  name: booknook-previews
  max_length: 2000              # bounded. Unlimited is a memory bomb (§7.2)
  overflow: shed-newest         # under pressure, refuse NEW work
  metrics: [depth, shed_total, oldest_age_seconds]

# --- The four alarms that make saturation visible --------------------------
alerts:
  - utilization: max by (service) (inflight / limit) > 0.85 for 5m
    severity: page        # the knee, BEFORE the latency cliff (§4.1)
  - shed_rate: rate(shed_total[5m]) / rate(requests_total[5m]) > 0.05
    severity: page        # 5 % of traffic being refused is an incident
  - p99_regression: histogram_quantile(0.99, latency) > 3 * baseline for 10m
    severity: page        # the actual user-visible symptom
  - queue_age: oldest_age_seconds > objective for 5m
    severity: page        # async backlog beyond business tolerance (L68 §4.5)
```

## 9. Numerical example

BookNook search: `S = 50 ms` service time, capacity `2,500 rps`, target p99 `< 500 ms`.

**Step 1 — where the knee is, and what "capacity" means.**

```
  R(ρ) = S / (1 − ρ)
      ρ=0.70 → 167 ms   ✓
      ρ=0.80 → 250 ms   ✓  ← design point
      ρ=0.90 → 500 ms   ✗ p99 target already breached at p50
      ρ=0.95 → 1,000 ms ✗
  ⇒ usable capacity at target latency ≈ 0.8 × 2,500 = 2,000 rps
  The remaining 500 rps of "capacity" is only reachable by breaking the
  latency target, which is why autoscaling on 100 % utilisation is a trap.
```

**Step 2 — the concurrency limit, from Little's Law.**

```
  in-flight = throughput × latency = 2,000 × 0.05 = 100
  limit = 100 × 1.2 = 120                  (20 % headroom for jitter)
  With 120 permits, at most 120 requests are ever queued in-process, so the
  MAXIMUM queueing delay = 120 × 50 ms / parallelism… in practice bounded by
  drain: 120 / (1/0.05) = 6 s worst case only if all 120 arrive together and
  the server serialises — which is precisely why we set the permit ACQUIRE
  timeout to 5 ms and shed instead.
```

**Step 3 — the goodput arithmetic that justifies shedding.**

```
  SCENARIO A — accept everything (no limits):
      offered 3,200 rps, ρ = 1.28 → queue grows without bound
      measured: p99 12 s; clients time out at 2 s
      effective success = 12 % of 3,200 = 384 successful responses/sec

  SCENARIO B — shed to hold ρ = 0.9:
      admit 0.9 × 2,500 = 2,250 rps; shed 950 rps immediately with 429
      p99 ≈ 480 ms; clients do not time out
      success = 99 % of 2,250 = 2,228 successful responses/sec

  ⇒ 5.8× the successful work, from deliberately refusing 30 % of the load.
  ⇒ AND the customers in scenario B get 480 ms instead of a 2-second timeout,
    so a share of them wait and retry later instead of abandoning.
```

**Step 4 — retry amplification, which is what turns B back into A.**

```
  scenario B sheds 950 rps. If 40 % of clients retry immediately (no
  Retry-After honoured):
      950 × 0.4 = 380 rps of NEW arrivals
      offered becomes 3,200 + 380 = 3,580 → more shedding → more retries
  ⇒ a positive feedback loop. At a 40 % retry rate the system converges to
    3,200 / (1 − 0.4 × shed_fraction) — if shed_fraction ≥ 0.3, arrivals grow
    by 13.6 % per cycle, and the system never stabilises.

  THE RETRY BUDGET is the control: cap retries at 10 % of successful traffic.
      2,228 successes → at most 222 rps of retries. Bounded.
  ⇒ And `Retry-After` is not politeness; it is the difference between a stable
    equilibrium and a metastable collapse (L65 §9 step 3).
```

**Step 5 — what an unbounded queue costs before you notice.**

```
  arrivals 3,000 rps, drain 2,400 rps → backlog +600 rps
  request state ~2 KB:
      1 min  → 36,000 queued →   72 MB, oldest waited 1 s   (invisible)
      5 min  → 180,000       →  360 MB, oldest waited 5 s   (timeouts begin)
     10 min  → 360,000       →  720 MB, oldest waited 10 s  (all wasted)
     14 min  → 500,000       → 1.0 GB  → OOM → restart → every request fails
  ⇒ the queue's "buffering" bought 14 minutes and then caused a total outage.
    A bounded queue would have refused 600 rps from second 1 — and the refusal
    is the information the caller needs.
```

## 10. Trade-offs

| Decision | Buys you | Costs you | Choose it when |
|---|---|---|---|
| **No limits (accept everything)** | No user ever sees a `429` in normal operation | Collapse under overload; unbounded queues; retry storms | Never, for anything user-facing |
| **Concurrency limit only** | Simple, in-process, protects latency | Refusals happen after the request has done some work | The default first step; cheap and effective |
| **Rate limit at the gateway** | Cheap refusal; protects the whole fleet; enables per-tenant fairness | Needs a policy per tier; risk of limiting legitimate bursts | Any multi-tenant or public API |
| **Drop newest** | Preserves queued work; simple | Rejects the requests users just made | Bounded work where queue content is still valuable |
| **Drop oldest / LIFO** | Serves fresh data; bounds staleness | Discards work already paid for | Feeds, presence, live prices, search refinements |
| **Priority shedding** | Protects revenue-critical paths | Requires classification, and misclassification starves someone | Always, with an explicit owner for the policy |
| **Deadline-aware shedding** | Never wastes work that cannot finish | Requires deadline propagation (L65 §5) | Multi-hop internal APIs |
| **Degrade instead of shed** | No user-visible failure | Worse output; more code paths; harder testing | Whenever a cheaper answer exists (§4.6) |
| **Bounded queue with reject-publish** | Backpressure reaches the producer | Producers must handle refusal | All async pipelines |
| **Long queue, no cap** | Absorbs short bursts transparently | Turns a throughput problem into a memory crash (step 5) | Nothing longer than the burst you can serve |
| **Generous `Retry-After`** | Protects you during recovery | Users wait longer; some abandon | Recovery windows where load must stay low |

## 11. Failure scenarios

**11.1 — The unbounded queue** (§7.2, step 5). Buffering until OOM, then a crash loop, and the failure spreads to unrelated features sharing the process. **Symptom:** memory graphs and restarts, not error rates, until the restart. **Fix:** bounded queues with an explicit overflow policy, plus bulkheads (L77).

**11.2 — Shedding at the wrong layer.** A service shed at the database layer (constraint violations, lock timeouts) after 8 service calls, 30 s of CPU and a held row. **Symptom:** high CPU with low goodput; the "shed" appears as errors deep in the stack. **Fix:** admit early — gateway rate limits and per-service concurrency limits, so refusals cost microseconds.

**11.3 — 429 without `Retry-After`, or with one the client ignores.** Clients retried immediately; arrivals grew; shed rate grew; the system never stabilised (step 4). **Symptom:** shed rate rising monotonically while capacity is constant. **Fix:** `Retry-After`, honoured clients, and a fleet-level **retry budget**.

**11.4 — Shedding the wrong traffic.** A per-IP limit shed an entire corporate customer (one NAT IP, 40 % of their traffic) while anonymous crawlers consumed capacity. **Symptom:** the biggest customer calls, and the dashboards show "rate limiting working as intended". **Fix:** limit by *identity* (API key, tenant, user), not by IP; and make priority explicit rather than implicit in arrival order.

**11.5 — Priority inversion.** A low-priority batch job's threads starved the interactive path because both shared a pool, so the "low priority" work delayed the high-priority work it was supposed to yield to. **Symptom:** checkout latency correlated with batch schedules. **Fix:** bulkheads — separate pools/queues per class (L77 §4.3), because priority only works if the resources are actually separate.

**11.6 — Degradation that hides an outage.** The recommendation carousel was shed on timeout, silently, for 6 weeks; conversion dropped 4 % and nobody connected the two because there was no error. **Symptom:** a slow business metric, no technical alarm. **Fix:** degradation is an *event*: count it, alarm on prolonged degradation, and surface it in the page ("recommendations unavailable").

**11.7 — The shed that was really a timeout.** A service "shed" by returning after its own 30-second timeout instead of refusing immediately — technically a refused request, practically 30 seconds of consumed capacity. **Symptom:** shed metrics look healthy; latency and CPU say otherwise. **Fix:** shed **before** doing work, never by timing out after it.

**11.8 — Autoscaling that fights shedding.** The autoscaler saw shed requests as demand and scaled up, the extra instances pulled more traffic from the load balancer, and total load rose — while the shed policy kept refusing the same fraction. **Symptom:** instance count and shed rate both climbing. **Fix:** scale on **goodput and latency**, not on arrival rate, and set a ceiling on capacity that the shedding policy assumes.

## 12. Alternatives

| Instead of shedding | Consider | When |
|---|---|---|
| Shedding | **Autoscaling** | Load is sustained (minutes+) and the bottleneck is stateless compute |
| Shedding | **Queueing with a bounded, honest SLA** ("your report will be emailed") | The work is deferrable and the user does not need it now |
| Shedding | **Caching / CDN** (L31) | The load is repeat reads — the cheapest capacity in any system |
| Concurrency limits | **Bulkheads / circuit breakers** (L77) | The overload is caused by one slow dependency, not raw volume |
| Rate limiting at the gateway | **Per-tenant quotas** | Fairness rather than protection is the goal |
| Dropping | **Degrading** (§4.6) | A cheaper answer exists and is acceptable |
| Client-side backpressure | **Server-side admission control** | Clients cannot be trusted to cooperate (which is usual) |

## 13. When to use it

- **Every user-facing service, from day one.** Overload is a question of when, and the default behaviour (collapse) is the worst of the three options.
- **Every async pipeline's queue**, bounded, with an overflow policy and an age alarm (L68 §4.5).
- **Every call to a shared dependency**, with a concurrency limit and a short acquire timeout.
- **Every multi-tenant API**, with per-identity limits so one customer cannot consume the fleet.
- **Before a known traffic event** — a sale, a launch, a marketing email — as a designed behaviour rather than an incident response.
- **Whenever a dependency slows down**, since a slow dependency consumes concurrency exactly like extra load (L77).

## 14. When *not* to use it

- **As a substitute for capacity.** If your steady-state load exceeds capacity, shedding 40 % forever is a broken product; scale or fix the bottleneck.
- **With no user-facing explanation.** A `429` with no `Retry-After`, or a silently degraded page, is a bug report waiting to happen.
- **On the critical path you cannot afford to lose.** Shedding auth or payment is usually worse than shedding everything else and queueing those.
- **Without a priority policy.** Uniform shedding under pressure drops your best customers' revenue-critical requests alongside crawlers.
- **As a hidden timeout.** Shedding must happen *before* work, not by waiting and then failing.
- **When the real problem is a leaked connection or a slow query.** Fix the resource leak; shedding just lowers the ceiling at which it hurts.

## 15. Common mistakes

1. **Unbounded queues**, which convert load into memory exhaustion (§11.1).
2. **No concurrency limit** on dependency calls, so a slow dependency consumes threads (L77).
3. **Shedding after work has been done** instead of at admission (§11.2, §11.7).
4. **`429` without `Retry-After`**, inviting immediate retries (§11.3).
5. **No retry budget**, so retries become the load (step 4).
6. **Limiting by IP** instead of identity, breaking NATed customers (§11.4).
7. **No priority classes**, so crawlers and checkout compete equally.
8. **Priority without bulkheads**, so "low priority" threads starve the high-priority path anyway (§11.5).
9. **Silent degradation** with no metric or user-visible signal (§11.6).
10. **Alarming on error rate only** — saturation shows up in latency and utilisation first (§4.1).
11. **Autoscaling on request count** while shedding, so scale chases work you are refusing (§11.8).
12. **Designing for ρ = 0.95**, where the last 15 % of capacity is unusable anyway (step 1).
13. **Not testing it**: run a load test at 2× capacity and assert that p99 stays inside target and *some* requests are refused — an untested shedding path is usually broken.
14. **Treating `nodelay` queueing as shedding**, when the proxy is silently absorbing requests (§8).

## 16. Interview perspective

**What is actually being tested.** Whether you know that saturation is a *design* problem with three outcomes, and that the only choice is which work to drop. Strong candidates derive the concurrency limit from Little's Law, state the utilisation at which they shed (≈80 %), name a priority policy, and ask about client behaviour (`Retry-After`, retry budgets). Weak candidates say "we autoscale" and consider overload solved.

**Answers that land well:**

- *"What happens when this service gets 3× traffic?"* — "It sheds. Between the CDN and the gateway we remove about two thirds of the load without touching origin; the gateway rate-limits per tier so anonymous traffic goes first; each service holds a concurrency limit derived from Little's Law — 2,000 rps × 50 ms → 100 in flight, so 120 permits — and refuses immediately with a 429 and Retry-After beyond that. Checkout is a never-shed class. Under 3× load we serve fewer requests per second and roughly six times more successful ones."
- *"Why not just queue it?"* — "Because a queue does not create capacity. Past the knee, queued work waits longer than the client's timeout, so it is done and thrown away, and the memory grows until something restarts. Bounded queues turn that into a refusal the caller can act on."
- *"How do you choose the concurrency limit?"* — "Little's Law: throughput at target latency times service time, plus about 20 % headroom. It is a latency guarantee expressed as an admission policy, not a guess."
- *"What is goodput and why do you care?"* — "Successful responses per second, as opposed to requests attempted. A saturated system has flat maximum throughput and collapsing goodput because it spends capacity on work that misses its deadline. Shedding raises goodput by lowering throughput."
- *"How do you avoid shedding customers you care about?"* — "Priority classes decided in advance — checkout and auth never shed, then catalog, then search, then recommendations, then analytics and prefetch — enforced with separate concurrency pools so low priority cannot starve high priority. And limits keyed on tenant identity, not IP, because a big customer behind one NAT looks like an attacker."

**Common interview tasks:** compute a concurrency limit from throughput and latency; choose a shedding policy for a described workload; design degradation modes; explain why retries make overload worse and what fixes it; decide where to place rate limits; explain drop-oldest vs drop-newest for feeds.

**Red flags interviewers watch for:**

- "It autoscales" as the whole answer.
- No bounded queues, or no awareness that unbounded queueing is dangerous.
- Shedding implied after the work is done (a timeout disguised as a refusal).
- No priority policy, or priority without separate resources.
- `429` without `Retry-After`.
- No retry budgets, and no recognition of the retry feedback loop.
- No numbers: no utilisation target, no limit derivation, no shed rate.
- Being unable to state the difference between throughput and goodput.

## 17. Knowledge check

**Conceptual**

1. Explain the difference between backpressure and load shedding in terms of *who* is informed, and state the three possible outcomes when a system is driven past capacity. Then explain why "accept everything and queue it" is one of the three rather than a mitigation for it.
2. Explain why response time depends on utilisation rather than arrival rate, using `R = S / (1 − ρ)`, and derive from it (a) the utilisation you would design for, (b) why a system at 95 % is in more danger than one at 100 %, and (c) the metric you would alarm on before the latency cliff appears.
3. Explain how retries can turn a saturated system into a metastable one, with the specific feedback loop. Then describe the two controls that break the loop and explain why per-request attempt caps alone are insufficient.

**Practical scenario**

4. BookNook's product page calls three services: catalog (local DB, 20 ms), recommendations (external, p99 300 ms, degradable), and reviews (p99 80 ms, LIFO-tolerable). Steady state is 1,200 rps; a sale will produce 4,000 rps for 20 minutes. Capacity at target latency is 2,000 rps. Design the complete overload policy: the shedding layers in order with the cost of a refusal at each, the concurrency limit for each dependency with derivation, the priority classes and where each is enforced, the degradation modes and how each is surfaced to the user and to monitoring, the exact responses for a refused and a degraded request, the four alarms with thresholds, and the arithmetic showing goodput before and after.

**Calculation**

5. A service has `S = 40 ms`, capacity `3,000 rps`, and a p99 target of 400 ms. Compute (a) the utilisation at which p50 hits 200 ms; (b) the throughput available at ρ = 0.8; (c) the concurrency limit with 20 % headroom; (d) the goodput if you accept 3,600 rps with no limits (assume p99 becomes 8 s, client timeout 1 s, and 15 % success) versus shedding to ρ = 0.9 with 99 % success.

6. An endpoint receives 2,400 rps against a 2,000 rps drain rate. (a) Compute the backlog after 3 minutes, its memory at 2 KB per request, and the age of the oldest entry. (b) If the client timeout is 2 s, compute the fraction of queued work that is doomed. (c) Compute the shed rate required to hold the drain at capacity with zero growth. (d) If 30 % of shed requests retry immediately, compute the steady-state arrival rate after the first retry cycle and state whether it converges.

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
  - [x] 12. Dead-letter queues and retry queues (L75)
  - [x] **13. Backpressure and load shedding (L76) ← you are here**
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

**NEXT LESSON: Phase 4, Topic 14 — Circuit breakers, bulkheads and graceful degradation**

---

---

# Lesson 77 — Circuit Breakers, Bulkheads and Graceful Degradation

*Phase 4, Topic 14*

## 1. Simple definition

Three defences against the same failure — **one degraded dependency consuming everything you have** — and they are usually deployed together because each alone is incomplete.

- **Circuit breaker**: a state machine in front of a dependency call. After enough failures it **stops calling** (open), fails fast, and periodically probes with a single request (half-open) before resuming (closed). Its job is to convert a slow dependency into a *cheap, instant* failure, so the caller keeps its capacity.
- **Bulkhead**: **resource isolation** between dependencies. A separate thread pool, connection pool, or semaphore per dependency, so that one saturated dependency can only consume *its* share of the capacity — never the whole service.
- **Graceful degradation**: a **reduced but useful** answer when the full answer is unavailable: a cached value, a default, a partial response, or a feature switched off — with the reduction being visible to users and counted in metrics.

They are complementary in a precise way: **the breaker stops you wasting capacity on a dependency that is failing; the bulkhead limits the damage before the breaker notices; degradation decides what to return instead.** Remove any one and the other two underperform — a breaker with no bulkhead still lets 2,000 concurrent slow calls pile up during the detection window, and a bulkhead with no fallback turns isolation into a feature that is simply missing.

Terms defined before use:

- **Cascading failure**: one component's slowness exhausting a *shared* resource (threads, connections, memory) in its caller, so that the caller fails for reasons unrelated to the original fault.
- **Slow-call failure**: a call that eventually succeeds but took so long it consumed a worker slot that other requests needed. Error-rate-only breakers miss these entirely (§4.2).
- **Failure-rate window**: the rolling window and minimum request volume over which the breaker measures failure (e.g. 50 % failures over 10 s, minimum 20 requests).
- **Half-open**: the probe state — usually *one* permitted call — that tests recovery without re-exposing the full fleet.
- **Fallback**: the value returned when the primary path is unavailable. Must be *bounded* in cost, or it becomes a second failure.
- **Bulkhead (nautical)**: a watertight compartment. The term is deliberate: the ship floods, but only one compartment.

The one sentence to carry out of this lesson: **failures propagate through shared resources, not through call graphs; so isolate the resources, stop calling the thing that is broken, and have an answer ready that is cheaper than the truth.**

## 2. Why it exists

Lesson 76 showed that a system near saturation collapses. This lesson explains the most common *cause* of that saturation in a microservice architecture: **a single slow dependency, called from a service that shares one thread pool across all its endpoints.**

BookNook's product page calls `catalog` (fast, local), `reviews` (fast), and `recommendations` (an ML service, `p99 = 300 ms` nominally). The service had a 200-thread pool shared by every endpoint. Then `recommendations` began taking **2 seconds** instead of 300 ms — still *succeeding*, just slowly.

The arithmetic is unforgiving:

```
 BEFORE (30 ms dependency):
    threads needed per request ≈ (20 ms own work + 30 ms wait) = 50 ms
    capacity = 200 threads / 0.050 s = 4,000 rps

 AFTER (2,000 ms dependency):
    each request now holds a thread for ~2,020 ms
    capacity = 200 / 2.020 = 99 rps          ← a 40× collapse
    arriving load: 1,200 rps
    ⇒ 1,101 rps per second queue for a thread; the queue grows; every request
      times out; and — the fatal part — the requests that are timing out
      include /checkout and /cart, which do not call recommendations at all,
      because they are waiting in the same pool.

 ⇒ 100 % of the product site went down. No component failed. Nobody's error
   rate rose until the timeouts started. The *only* red metric was context
   switches.
```

That is a **cascading failure**, and it has three independent fixes, which is exactly why there are three topics in this lesson:

1. **Bulkhead**: give `recommendations` its own pool of 60 threads. When it goes slow, only that 60 is consumed; the other 140 keep serving catalog, cart and checkout. The feature degrades; the site does not die.
2. **Circuit breaker**: after 10 seconds of `recommendations` timeouts, stop calling it. Failed calls then cost **0.2 ms instead of 2,000 ms**, so the 60 threads stay available for the occasional probe and the capacity cost of the broken dependency approaches zero.
3. **Graceful degradation**: return the product page *without* the carousel. The user gets a slightly poorer page instead of an error, and — critically — a metric records that 1,200 carousels per second were not rendered, so an engineer can see it.

The second reason this lesson exists: **availability multiplies along a synchronous chain** (L15, L67). Three dependencies at 99.9 % give 99.7 %. A breaker changes the arithmetic: when the dependency is failing, you do not need it, so the caller's availability stops depending on it. That is the only real way to build a service that is more available than its dependencies.

## 3. Real-world analogy

Three mechanisms from the physical world, and each maps to one of the three.

**The fuse box (circuit breaker).** A house does not attempt to pass a fault current through its wiring until something melts; a fuse sacrifices itself in milliseconds so the rest of the house stays powered. It has exactly the states of the software version: **closed** (current flows), **open** (the fuse has blown; nothing flows), and the electrician's **test** (replace, see whether it blows again — the half-open probe, done with *one* fuse rather than the whole board). And the crucial property of a fuse box is **fast failure**: the fault stops costing anything almost immediately, so the rest of the house keeps working.

**The ship's bulkheads.** A hull breach floods one compartment; the watertight doors mean the ship floats. Nobody designs a ship expecting no leaks — they design so that a leak is survivable. Software bulkheads are the same: a per-dependency pool is a watertight door, and the sizing question ("how many threads does the leak get?") is the same one a naval architect answers with compartment volume.

**Brownouts on the electricity grid (graceful degradation).** When demand exceeds supply, the grid does not black out the country; it deliberately reduces voltage slightly across the board — dimmer lights, but the lights are *on*. Software degradation is the same trade: serve a slightly worse answer to everyone rather than a perfect answer to nobody. And, exactly as with the grid, the reduction must be **announced** — a brownout that nobody knows about becomes a fault report about "lights looking weird".

The combination is the lesson: **the fuse box, the bulkhead and the brownout are three different defences because a fault can arrive in three different ways** — suddenly (a dependency dies: the fuse), gradually (it slows down and leaks capacity: the bulkhead), and by volume (too much demand: the brownout). Systems with only one of the three are protected against exactly one of the three.

## 4. Detailed technical explanation

### 4.1 The cascade mechanism, precisely

A cascade is not "service A called service B and B failed". It is a **resource feedback loop**:

```
  1. Dependency latency rises (30 ms → 2,000 ms). It still SUCCEEDS.
  2. Each caller request now holds a thread/connection for ~2 s instead of 50 ms.
  3. Caller throughput capacity falls 40× (200 threads ÷ 2.02 s ≈ 99 rps).
  4. Arrivals (1,200 rps) now queue for a resource.
  5. Queued requests exceed client deadlines → callers time out → RETRY.
  6. Retries raise arrivals → more queueing → more timeouts → step 5.
  7. The caller's OTHER endpoints fail too, because they share the resource.
  8. Callers of the caller fail for the same reason. The cascade is now
     independent of the original fault and will not stop when it is fixed.
```

Three observations that dictate the defences:

- **The trigger is latency, not errors.** An error-rate-only breaker is blind to this entirely (§4.2 refuses to be).
- **The mechanism is a shared resource.** Without isolation, the blast radius is the whole process. That is the bulkhead's entire justification.
- **Step 8 is metastability** (L65 §9, L76 step 4): the system will not recover when the dependency recovers, because the retries are now the load. The exit is shedding and breaking, not waiting.

### 4.2 Circuit breaker: the state machine and its parameters

```
                    failure rate ≥ threshold
   ┌─────────┐       over the window           ┌─────────┐
   │ CLOSED  │ ───────────────────────────────►│  OPEN   │
   │ calls   │                                 │ fail    │
   │ pass    │ ◄───────────────────────────────│ fast    │
   └─────────┘   N consecutive successes       └────┬────┘
        ▲           in HALF-OPEN                    │ after open_duration
        │                │                          │
        │                │  ┌──────────────┐        │
        └────────────────┴──│  HALF-OPEN   │◄───────┘
                            │ 1 probe call │
                            └──────┬───────┘
                                   │ probe fails
                                   └──► OPEN (and usually double the wait)
```

| Parameter | Meaning | Typical value | Why this value |
|---|---|---|---|
| **Failure-rate threshold** | Fraction of failures to trip | 50 % | Below ~30 %, normal partial failures trip it; above ~70 %, it trips too late |
| **Minimum calls** | Don't trip on tiny samples | 20 | 2 failures out of 3 is 67 %, and meaningless |
| **Sliding window** | Count- or time-based | 10 s or 100 calls | Must be longer than a timeout (so a full timeout wave is visible) |
| **Slow-call threshold** | Calls exceeding `X` ms count as failures | `2 ×` p99 | **The parameter people omit.** Without it, §2's 2-second-success cascade never trips the breaker |
| **Slow-call rate** | Fraction of slow calls that trips | 60 % | Distinguishes "a few slow" from "the dependency is gone" |
| **Open duration** | How long before probing | 10–30 s, doubling | Shorter probes a recovering dependency constantly; longer delays recovery |
| **Half-open permits** | Concurrent probes | **1** | More than one re-cascades the problem you just contained |
| **Permitted when open** | Force-allow certain calls | Health checks only | Some traffic must bypass, or you cannot tell recovery from isolation |

The **slow-call threshold is the most important and least configured** parameter. A dependency that returns `200 OK` in 2,000 ms will never trip an error-based breaker, and §2's cascade is exactly that scenario. Add the same mechanism for **concurrency-based** breaking: if in-flight calls to a dependency exceed `N`, open the breaker immediately regardless of outcomes — because concurrency *is* the resource being consumed.

### 4.3 Bulkheads: three kinds, and the sizing question

| Kind | Mechanism | Isolation strength | Cost |
|---|---|---|---|
| **Semaphore bulkhead** | Cap concurrent calls per dependency (no extra threads) | Good; blocks/sheds acquisition | Cheapest; only works for async or non-blocking callers |
| **Thread-pool bulkhead** | Dedicated pool per dependency | Strong; also isolates blocking and interruption | Thread overhead, context switches, pool sizing |
| **Connection-pool bulkhead** | Separate pool per downstream (DB, cache, HTTP client) | Strong at the transport layer | More connections to manage; per-destination limits |
| **Process / cell bulkhead** | Separate deployments per tenant class or shard | Strongest; a whole process can die | Highest cost; the domain of large-scale architectures |

Sizing, from Little's Law (L14), exactly as in L76 §4.2:

```
  bulkhead_size = peak_rps_to_this_dependency × p99_latency × headroom(1.2)

  recommendations: 1,200 rps × 0.30 s × 1.2 = 432 concurrent?
  ⇒ That is larger than the whole pool (200), which tells you the number is
    wrong: at 1,200 rps with 300 ms latency the service needs 360 concurrent
    calls, which is why the page should CACHE recommendations instead (L29).
  After caching (65 % hit rate): 420 rps × 0.3 s × 1.2 = 151 concurrent.
  BookNook's pool: 60 threads, so 60/(420 × 0.3) = 0.48 → it deliberately
  sheds ~52 % of uncached recommendation calls under load, at the front.
  That is a decision, not a bug — and it is why the fallback is the ranking
  from the cache, not an empty carousel.
```

The point of that calculation: **a bulkhead almost always forces you to choose a degradation level**, because the isolated pool is deliberately smaller than the demand. That is the intended behaviour — it converts an unbounded, silent, whole-service failure into a bounded, visible, single-feature one.

### 4.4 Graceful degradation: the ladder, decided in advance

| Level | What you return | Cost to build | Cost to the user | When |
|---|---|---|---|---|
| **1. Serve stale** | Cached value, `stale-while-revalidate` | Low | Bounded staleness | The cached answer is nearly always right (L29) |
| **2. Serve partial** | 20 of 50 results; page without the carousel | Medium | Honest incompleteness | Ranking/carousel/recommendation sections |
| **3. Serve a default** | Global bestseller list instead of personalised | Low–medium | Coarser, still useful | Personalisation unavailable |
| **4. Serve an empty state with a message** | "Recommendations are unavailable" | Low | Explicit downgrade, no confusion | Nothing cheaper is truthful |
| **5. Disable the feature** | Remove the UI element entirely | Low | Feature missing, silently | Non-essential features under load (L76 §4.6) |

Two rules that make degradation real rather than aspirational:

1. **Every fallback must be cheaper than the primary path** — otherwise you have added a second way to fail. A fallback that calls another remote service is not a fallback.
2. **Degradation must be observable.** Emit `degraded_total{feature, reason}`, show it on the dashboard, and — where the reduction is user-visible — tell the user. A silent degradation runs for weeks and shows up as a mystery conversion drop (§11.5).

### 4.5 How the three compose (and the order matters)

```
  request
    │
    ├─ 1. BULKHEAD: acquire a permit from this dependency's pool
    │        ✗ no permit within 5 ms → degrade or shed (L76)
    │
    ├─ 2. BREAKER: is the circuit closed?
    │        ✗ open → skip the call entirely, go to the fallback (0.2 ms)
    │        ~ half-open → allow ONE probe; everyone else falls back
    │
    ├─ 3. TIMEOUT (L65): deadline budget propagated, per-attempt timeout
    │        ✗ exceeded → counts as a failure toward the breaker
    │
    ├─ 4. CALL
    │        ✗ error/timeout/slow → record in the breaker's window
    │
    └─ 5. FALLBACK: stale cache → partial → default → empty+message
```

The ordering matters because each layer reduces the cost the next one pays. A breaker *outside* the bulkhead would trip only after the pool was already exhausted; a timeout without a breaker means every request pays the full timeout before failing. **Cheapest rejection first.**

## 5. Request / data flow

The product page during the §2 incident, before and after.

```
 ── BEFORE (shared pool, no breaker, no fallback) ──────────────────────────
  1,200 rps arrive. recommendations at 2,000 ms.
  t+0:00   200 threads in use; all endpoints queue
  t+0:02   first client timeouts; retries begin (arrivals 1,600 rps)
  t+0:30   pool permanently full; queue depth 40,000; p99 = 12 s
  t+0:45   /checkout, /cart, /product all failing (same pool)
  t+17:00  recommendations dependency recovers
  t+17:00  STILL DOWN — the retry queue keeps the pool saturated (metastable)
  t+41:00  manual intervention: restart the fleet, drop the retry storm

 ── AFTER (bulkhead 60 / breaker / fallback) ───────────────────────────────
  1,200 rps arrive. recommendations at 2,000 ms, budget 300 ms.
  t+0:00   breaker CLOSED. 60-permit bulkhead for recommendations.
           Calls exceed 300 ms → cancelled at the deadline, recorded as failures.
  t+0:00   140 permits remain for catalog/reviews/cart/checkout → unaffected.
  t+0:10   failure rate 94 % over the 10 s window (> threshold 50 %, > min 20)
           → breaker OPENS. Carousel stops being called entirely.
  t+0:10   page renders with the FALLBACK: cached global bestsellers.
           degraded_total{feature="recommendations"} = 1,200/s
  t+0:40   half-open probe: one request to recommendations. Still 2 s → OPEN,
           open duration doubled to 20 s.
  t+3:20   probe returns 280 ms (3 consecutive successes) → CLOSED.
           Carousel returns. degraded_total → 0.
  ⇒ user-visible failures: 0. Feature degradation: 3 minutes. Revenue: full.

 ── WHAT THE METRICS SHOWED ────────────────────────────────────────────────
  breaker_state{service="recommendations"} → open (3 min)
  degraded_total{reason="breaker_open"} 1,200/s for 3 min
  pool_inflight{dependency="recommendations"} pinned at 60, others at ~normal
  checkout latency: unchanged. That is the whole point.
```

The number to notice: in the "after" timeline the **dependency was broken for three minutes and the page never failed**. Availability of a caller is no longer the product of its dependencies' availabilities — it is the product only of the paths *without* a fallback.

## 6. ASCII diagram

The three defences, and what each one is protecting:

```
                     ┌──────────────── service ────────────────┐
   1,200 rps  ──────►│  ┌──────────┐  ┌──────────────────────┐  │
                     │  │ inbound  │  │ thread pools (200)   │  │
                     │  │ limiter  │  │ ┌────────┐ ┌───────┐ │  │
                     │  └──────────┘  │ │catalog │ │ 140   │ │  │
                     │                │ │(40)    │ │shared │ │  │
                     │                │ ├────────┤ ├───────┤ │  │
                     │                │ │RECS 60 │ │BREAKER│ │  │
                     │                │ └────────┘ └───────┘ │  │
                     │                └──────────────────────┘  │
                     └───────────┬─────────────────┬────────────┘
                                 │                 │
                        ┌────────▼──────┐   ┌──────▼─────────┐
                        │ recommendations│   │ catalog/reviews│
                        │  SLOW (2 s)   │   │  healthy       │
                        └───────────────┘   └────────────────┘
        BULKHEAD: the slow dependency can only consume 60 of 200 threads.
        BREAKER:  after 10 s of failures it costs 0.2 ms instead of 2,000 ms.
        FALLBACK: the page renders with cached bestsellers instead of an error.
```

The cascade loop, and where each defence cuts it:

```
   dependency slows
        │
        ▼
   requests hold threads ──────────────► BULKHEAD caps this at 60 threads
        │
        ▼
   capacity of the service falls
        │
        ▼
   queue grows, latency rises ─────────► SHEDDING (L76) refuses new work
        │
        ▼
   clients time out ───────────────────► TIMEOUTS (L65) bound each wait
        │
        ▼
   clients RETRY ──────────────────────► RETRY BUDGET caps the amplification
        │
        ▼
   more arrivals, more queueing ───────► CIRCUIT BREAKER stops the calls
        │                                  entirely, cutting the loop
        ▼
   METASTABLE: stays down after the
   dependency recovers ────────────────► only load shedding + a breaker exit it
```

## 7. Detailed example

### 7.1 The recommendations outage, in full

§2 and §5 gave the mechanics; here is what the team changed, in the order of value per hour of work:

| Change | Effort | Effect |
|---|---|---|
| **300 ms deadline on the recommendations call**, propagated from the page budget | 2 h | Bounded the damage per request; turned a 2 s wait into a 300 ms failure that the breaker could see |
| **Bulkhead: 60-permit semaphore** for recommendations | 3 h | Site-wide outage → single-feature outage (the calc in §4.3 is why 60) |
| **Breaker with slow-call threshold at 2 × p99 (600 ms)** | 2 h | Cost of the broken dependency fell from 2,000 ms to 0.2 ms |
| **Fallback: cached bestsellers, 5-minute TTL** | 4 h | Users got a page, not an error |
| **`degraded_total` metric + dashboard panel** | 1 h | The 6 weeks of silent degradation never happened |

The order matters for a second reason: **the deadline had to come first.** Without a per-call timeout, the "failures" the breaker sees are client cancellations at 8 seconds, by which time the pool is already exhausted. A breaker cannot protect you from a hang it cannot measure.

### 7.2 The bulkhead that was too big

A later attempt set the recommendations pool to **200 permits** ("so it never limits us"). During the next slow period, recommendations consumed the entire pool again — the bulkhead existed, was correctly implemented, and provided **zero isolation**, because its size exceeded the shared resource it was meant to protect.

The general rule: **a bulkhead's size is a decision about how much damage you are willing to absorb, not about how much throughput you want.** Sizing it at 100 % of capacity is the same as having none. BookNook's convention became: no single dependency may hold more than **30 %** of the total pool, and the two revenue-critical dependencies (payment, inventory) get dedicated pools with their own sizing.

### 7.3 The fallback that failed

A service's fallback for "pricing unavailable" was to call a *second* pricing service. When the shared database slowed, both were slow, and the fallback added **100 % more load** to an already-struggling dependency. The page's p99 went from 400 ms to 2.4 s, and the second service became the new bottleneck.

The rule from §4.4, learned the hard way: **a fallback must be cheap and local.** Acceptable: a cached value, a constant, a partial result, a disabled feature. Unacceptable: another remote call, a database query, or anything with its own failure mode. The cheapest legitimate fallback is usually **the last good value you already had in memory**.

## 8. Code or configuration example

```python
# ============================================================================
# BookNook: bulkhead + breaker + fallback, composed in the right order (§4.5).
# ============================================================================

class Bulkhead:
    """Semaphore bulkhead: cap concurrent calls to ONE dependency (§4.3)."""
    def __init__(self, name, permits, acquire_timeout_ms=5):
        self.name, self.sem = name, asyncio.Semaphore(permits)
        self.timeout = acquire_timeout_ms / 1000

    async def call(self, fn, *a, **kw):
        try:
            await asyncio.wait_for(self.sem.acquire(), self.timeout)
        except asyncio.TimeoutError:
            metrics.incr("bulkhead.rejected", tags={"dep": self.name})
            raise BulkheadFull(self.name)      # cheap, immediate
        try:
            return await fn(*a, **kw)
        finally:
            self.sem.release()


class Breaker:
    """Trips on error rate AND slow-call rate. The slow threshold is the one
    that catches the §2 cascade — a dependency succeeding in 2,000 ms."""
    def __init__(self, name, fail_rate=0.5, slow_rate=0.6, slow_ms=600,
                 window_s=10, min_calls=20, open_s=10, half_open_permits=1):
        self.name = name
        self.fail_rate, self.slow_rate, self.slow_ms = fail_rate, slow_rate, slow_ms
        self.window_s, self.min_calls = window_s, min_calls
        self.open_s, self.half_open_permits = open_s, half_open_permits
        self.state, self.opened_at = "closed", 0
        self.probes = 0
        self.events = collections.deque()      # (ts, ok, slow)

    def allow(self) -> bool:
        now = time.monotonic()
        if self.state == "closed":
            return True
        if self.state == "open":
            if now - self.opened_at >= self.open_s:
                self.state, self.probes = "half_open", 0
            else:
                return False                   # FAIL FAST: ~0.2 ms, not 2 s
        if self.state == "half_open":
            if self.probes < self.half_open_permits:
                self.probes += 1
                return True                    # exactly ONE probe (§4.2)
            return False

    def record(self, ok: bool, duration_ms: float):
        now = time.monotonic()
        self.events.append((now, ok, duration_ms > self.slow_ms))
        while self.events and now - self.events[0][0] > self.window_s:
            self.events.popleft()

        if self.state == "half_open":
            if ok and duration_ms <= self.slow_ms:
                self.state = "closed"          # recovered
                metrics.set("breaker.state", 0, {"dep": self.name})
            else:
                self._open(now, backoff=True)
            return

        if len(self.events) < self.min_calls:
            return                             # too small a sample to judge
        failures = sum(1 for _, ok_, _ in self.events if not ok_)
        slows    = sum(1 for _, _, slow in self.events if slow)
        if failures / len(self.events) >= self.fail_rate or \
           (slows + failures) / len(self.events) >= self.slow_rate:
            self._open(now)

    def _open(self, now, backoff=False):
        self.state, self.opened_at = "open", now
        if backoff:
            self.open_s = min(self.open_s * 2, 300)   # exponential re-probe
        metrics.set("breaker.state", 1, {"dep": self.name})
        log.warn("breaker open", extra={"dep": self.name, "for_s": self.open_s})


# --- Composition: cheapest rejection first (§4.5) --------------------------
recs_bulkhead = Bulkhead("recommendations", permits=60)     # §4.3 calc
recs_breaker  = Breaker("recommendations", slow_ms=600)

async def get_recommendations(ctx, customer_id):
    if not recs_breaker.allow():                     # 1. breaker: 0.2 ms
        return fallback_recommendations(ctx, "breaker_open")
    try:
        resp = await recs_bulkhead.call(              # 2. bulkhead: 5 ms max wait
            recs_client.fetch, customer_id,
            timeout_ms=ctx.remaining_ms(floor=50),    # 3. deadline (L65)
        )
        recs_breaker.record(True, resp.elapsed_ms)
        return resp.items
    except (Timeout, ConnectionError, ServerError) as e:
        recs_breaker.record(False, ctx.elapsed_ms())  # 4. feed the window
        return fallback_recommendations(ctx, type(e).__name__)
    except BulkheadFull:
        return fallback_recommendations(ctx, "bulkhead_full")


def fallback_recommendations(ctx, reason):
    """MUST be cheap and local (§4.4 rule 1, §7.3). No remote calls."""
    metrics.incr("degraded_total", tags={"feature": "recommendations",
                                         "reason": reason})
    # 1. stale cache from the page's own cache, 2. global bestsellers,
    # 3. empty list + a flag the template uses to hide the section.
    return cache.get(f"recs:{ctx.customer_id}") \
        or ctx.pinned_bestsellers \
        or []
```

```yaml
# --- Resilience4j-style config, if you are on the JVM ---------------------
resilience4j.circuitbreaker.instances.recommendations:
  slidingWindowType: TIME_BASED
  slidingWindowSize: 10                       # seconds
  minimumNumberOfCalls: 20
  failureRateThreshold: 50
  slowCallDurationThreshold: 600ms            # ← the parameter everyone omits
  slowCallRateThreshold: 60
  waitDurationInOpenState: 10s
  permittedNumberOfCallsInHalfOpenState: 1    # ONE probe, never more
  automaticTransitionFromOpenToHalfOpenEnabled: true
  recordExceptions: [java.io.IOException, java.util.concurrent.TimeoutException]
  ignoreExceptions: [com.booknook.NotFoundException]  # a 404 is not a failure

resilience4j.bulkhead.instances.recommendations:
  maxConcurrentCalls: 60
  maxWaitDuration: 5ms                        # shed, do not queue

resilience4j.timelimiter.instances.recommendations:
  timeoutDuration: 300ms                      # must be < breaker slow threshold
```

```yaml
# --- The alerts. Breaker state is a LEADING indicator; degradation is the
#     user-visible one. Both are needed. ------------------------------------
alerts:
  - breaker_open{service="recommendations"} == 1 for 1m        # page
  - rate(degraded_total[5m]) / rate(requests_total[5m]) > 0.10 # page: >10 % degraded
  - bulkhead_rejected_total rising                              # warn: pool too small?
  - pool_inflight{dependency=~".+"} / pool_size > 0.9 for 5m     # page: saturation
  - checkout_p99 > baseline * 2                                 # page: revenue path
# ⚠ Do NOT alarm only on errors: an open breaker SUPPRESSES errors by design,
#   so error rate can look healthy during a full degradation.
```

## 9. Numerical example

BookNook product page: 1,200 rps, 200-thread pool, recommendations `p99 = 300 ms` nominal.

**Step 1 — capacity collapses linearly in the *slow* dependency's latency.**

```
  threads_per_request ≈ own_work + dependency_wait
  dependency at 300 ms:  200 / (0.020 + 0.300) = 625 rps capacity
  dependency at 2,000 ms: 200 / (0.020 + 2.000) = 99 rps capacity

  available capacity 625 → 99 = a 6.3× collapse from the *nominal* case, and
  at 1,200 rps of demand the service was already 2× over capacity BEFORE the
  incident — which is why it fell over rather than degraded.
```

**Step 2 — the bulkhead's arithmetic, and why 60.**

```
  total pool 200.
  recommendations demand at 1,200 rps, 65 % cache hit → 420 rps of real calls.
  concurrent calls needed = 420 × 0.300 = 126 (nominal), not 432 (miss case)
  60 permits ⇒ concurrent calls capped at 60 ⇒ supported uncached rate
             = 60 / 0.300 = 200 rps, i.e. 17 % of page views get a live
             carousel and 83 % get the fallback at nominal latency.
  WHY ACCEPT THAT? Because the alternative is 126 concurrent × 2 s when the
  dependency degrades = 252 permits' worth of demand against a 200 pool.
  With 60: the worst case is 60 × 2 s = 120 permits... mathematically the cap
  is 60 permits regardless of latency. That is the point of a CAP, not a rate.
  ⇒ 60 permits bound the blast radius at 30 % of the pool, forever, at any
    dependency latency. The remaining 140 keep the site up.
```

**Step 3 — the breaker's value, in milliseconds per second of outage.**

```
  Without a breaker, during the outage:
      every request pays the full deadline (300 ms) before failing.
      At 420 rps: 420 × 0.300 = 126 thread-seconds of capacity burned per
      second, for zero output.
  With a breaker open:
      every request costs ~0.2 ms → 420 × 0.0002 = 0.084 thread-seconds/s.
  ⇒ the broken dependency's cost falls by a factor of 1,500.
  ⇒ 126 thread-seconds/s recovered is 63 % of a 200-thread pool: the site
    would have been down; it is instead at full capacity.
```

**Step 4 — detection time, and the cost of getting the window wrong.**

```
  failure threshold 50 %, minimum 20 calls, 10 s window
  at 420 rps the 20-call minimum is met in 48 ms, so detection is bounded by
  the window: up to 10 s of failures before the breaker opens.
  cost of those 10 s = 10 × 126 thread-seconds = 1,260 thread-seconds of
      wasted capacity, spread over a 200-thread pool = 6.3 s of full-pool
      saturation. Survivable WITH a bulkhead; fatal without one.

  If the window were 60 s: 7.6× more waste → the pool is exhausted long
      before the breaker helps. If the window were 1 s: a single blip of
      20 failures opens it, and you flap (an availability cost of its own).
  ⇒ the window must exceed the timeout, so a full timeout wave is measurable,
    but stay well under the time it takes for queues to grow fatally.
```

**Step 5 — what degradation is worth, in availability terms.**

```
  Without fallback: page availability = A(catalog) × A(reviews) × A(recs)
                   = 0.999 × 0.999 × 0.99  = 98.8 %  (2.5 h/month down)
  With fallback on recs only:
                   = 0.999 × 0.999 × 1.0    = 99.8 %  (44 min/month)
  8.7× fewer user-visible failures, from one fallback — and the *degraded*
  state lasted 3 minutes in the example, not 2.5 hours.

  ⇒ The availability number you quote for a service should be the availability
    of its paths WITHOUT a fallback. Everything else is a degraded-success.
```

## 10. Trade-offs

| Decision | Buys you | Costs you | Choose it when |
|---|---|---|---|
| **Circuit breaker (error-based only)** | Cheap, standard | Blind to slow-success cascades (§2) | Never alone — always with a slow-call threshold |
| **Breaker with slow-call + concurrency triggers** | Catches the real cascade | More parameters, more tuning | Any dependency on a request path |
| **Aggressive breaker (low threshold, short window)** | Fast protection | Flaps; false positives degrade a healthy feature | Latency-critical dependencies with good fallbacks |
| **Conservative breaker** | Few false positives | Detection too slow to prevent pool exhaustion | Dependencies with long timeouts and cheap failures |
| **Semaphore bulkhead** | Cheap; caps concurrency exactly | Cannot interrupt blocked threads | Async/non-blocking callers |
| **Thread-pool bulkhead** | Full isolation including blocking calls | Thread overhead; sizing and pool management | Blocking clients (typical JVM/MVC) |
| **Bulkhead at 100 % of capacity** | No user-visible rejection | **No isolation at all** (§7.2) | Never — it is the absence of a bulkhead |
| **Bulkhead at 30 %** | Guarantees the other 70 % survives | Deliberately caps that dependency | All non-critical dependencies |
| **Fallback: stale cache** | Nearly always correct; free | Bounded staleness | Read-heavy, cacheable data |
| **Fallback: another remote call** | Sometimes better data | Doubles load on a struggling system (§7.3) | Never during an incident |
| **Fallback: empty state + message** | Honest | Worse UX than partial | When nothing cheaper is truthful |
| **Silent degradation** | No user-facing noise | Weeks of unnoticed quality loss (§11.5) | Never |

## 11. Failure scenarios

**11.1 — The slow-success cascade** (§2). A dependency returning `200` in 2 s never trips an error-based breaker; the pool dies. **Symptom:** no errors until the timeouts start; pool saturation is the only early signal. **Fix:** slow-call thresholds and a concurrency-triggered breaker.

**11.2 — The bulkhead sized to nothing** (§7.2). A per-dependency pool at 200 of 200 permits. **Symptom:** the bulkhead is in the code, the isolation is absent. **Fix:** cap any single dependency at ~30 % of the shared resource, and assert it in a test.

**11.3 — The fallback that fails harder** (§7.3). A fallback calling a second service. **Symptom:** p99 *rises* when the fallback engages. **Fix:** fallbacks must be local and cheap; measure fallback latency separately.

**11.4 — Breaker flapping.** A 1-second window with a 20-call minimum opened and closed 40 times a minute; the feature was unavailable in a stuttering pattern, which is worse than being consistently off. **Symptom:** breaker state changes per minute; error rate oscillating. **Fix:** longer windows, minimum volume, exponential open-duration backoff, and hysteresis.

**11.5 — Silent degradation, discovered by the finance team.** The carousel had been fallen back to "bestsellers" for six weeks; conversion was 4 % down and no technical alarm fired. **Symptom:** a business metric moving with no technical correlate. **Fix:** degradation is an event — metric, dashboard, alarm on duration, and (where user-visible) a UI message.

**11.6 — Breaker opens, nobody notices the dependency is dead.** The breaker hid a *total* dependency outage for four days because the fallback was good enough and the alarm was on error rate — which the breaker suppressed. **Symptom:** `degraded_total` rising, error rate at zero. **Fix:** alarm on breaker-open *duration* and on degradation share, not on errors.

**11.7 — Bulkhead without a fallback.** The 60-permit cap rejected 52 % of live recommendation calls with a `500`, so the "isolation" produced user-visible errors instead of a degraded page. **Symptom:** `bulkhead_rejected` correlating exactly with page errors. **Fix:** every bulkhead rejection must route to the fallback ladder (§4.4), never to a raw error.

**11.8 — The half-open stampede.** Thirty permits were allowed in half-open; the recovering dependency was hit with 30 concurrent expensive calls, collapsed again, and the breaker oscillated. **Symptom:** recovery never completes; breaker cycles open→half-open→open. **Fix:** **one** half-open permit, exponential open duration, and (for expensive calls) a probe budget per minute.

## 12. Alternatives

| Instead of | Consider | When |
|---|---|---|
| Circuit breaker | **Load shedding at the front** (L76) | The problem is demand, not a broken dependency |
| Circuit breaker | **Fail-fast timeouts only** | The dependency fails quickly anyway; no cascade risk |
| Bulkhead | **Separate deployment / cell** | The blast radius must cross process boundaries |
| Fallback | **Queue and notify** | The work can be done later ("we'll email your report") |
| Fallback | **Read from a replica / degraded store** | A cheaper data source exists and is genuinely independent |
| Breaker + fallback | **Fix the dependency** | It is your own service and the fix is a capacity or index change |
| Per-dependency pools | **Async/non-blocking I/O** | Threads are the scarce resource; concurrency becomes cheap |

## 13. When to use it

- **Every remote call on a user-facing request path.** Breaker, timeout, and a fallback — the three are one unit of work.
- **Any dependency whose failure or slowness is outside your control** (third parties, other teams' services, ML endpoints).
- **Any feature with a cheap, truthful alternative** — carousels, badges, counters, personalisation, recommendations.
- **Whenever a shared pool exists** — a single shared pool across dependencies is an outage waiting for a slow dependency.
- **Before a traffic event**, so the degradation modes are exercised by a load test rather than discovered by customers.
- **When the availability arithmetic of your service must exceed that of its dependencies** — which is the only way to get there.

## 14. When *not* to use it

- **As a substitute for fixing a broken dependency.** A breaker protects you; it does not repair anything. If your own service is slow, fix it.
- **For the critical path with no meaningful fallback.** Breaking a payment call without an alternative just moves the failure; it needs a queue-and-retry design instead (L68).
- **With a fallback that is itself a remote call** (§7.3).
- **With silent degradation** as the permanent end state, rather than a temporary mode with an alarm (§11.5).
- **In a single-process monolith with no remote dependencies.** There is nothing to isolate.
- **As a replacement for timeouts.** A breaker without per-call deadlines cannot measure the slowness it is meant to detect (§7.1).

## 15. Common mistakes

1. **Error-only breakers** that never trip on the slow-success cascade (§11.1).
2. **Bulkheads sized at 100 % of capacity** (§11.2).
3. **Bulkhead rejections returned as errors** rather than routed to the fallback (§11.7).
4. **Fallbacks that call another service** (§7.3).
5. **More than one half-open permit**, re-cascading on recovery (§11.8).
6. **No slow-call threshold**, which is the parameter that matters most.
7. **Ignoring `404`/business rejections as failures**, tripping the breaker on legitimate negative answers.
8. **Silent degradation** with no metric, dashboard, or alarm (§11.5).
9. **Alarming on error rate while a breaker is open** — the breaker suppresses exactly the signal you are watching (§11.6).
10. **Flapping breakers** with no backoff on open duration (§11.4).
11. **No per-dependency isolation**, so one slow call consumes the whole pool (§2).
12. **Timeouts longer than the breaker's slow threshold**, so the breaker never sees a slow call (or vice versa) — the two parameters must be ordered deliberately.
13. **Treating a breaker as a load-shedding mechanism.** It protects against a broken dependency, not against excess demand.
14. **Never testing the open state**: no one asserts that the fallback path actually renders, so it is discovered broken during the incident.

## 16. Interview perspective

**What is actually being tested.** Whether you understand **cascading failure as a resource problem** rather than a call-graph problem. Strong candidates say "one slow dependency consumes shared threads, so I isolate the pool, break the circuit on slow calls as well as errors, and have a cheap local fallback". Then they give the numbers: pool arithmetic, the 30 % cap, the slow-call threshold, one probe.

**Answers that land well:**

- *"A dependency you call slows to 2 s but keeps succeeding. What happens?"* — "My pool dies, even for endpoints that do not call it. Each request holds a thread for 2 s, so capacity falls by ~40×, unless I have a per-dependency bulkhead. So: a separate pool capped at ~30 % of threads, a 300 ms deadline, a breaker whose slow-call threshold is 600 ms so it actually trips, and a cached fallback so the page renders without that section. Result: the feature degrades for three minutes and checkout never notices."
- *"Why not just always retry?"* — "Because retries are load. During a dependency outage, retries multiply the pressure and cause the whole tier to fail; a breaker exists precisely to stop calling, and a retry budget caps what is left."
- *"How do you size a bulkhead?"* — "Little's Law for the demand, then deliberately cap it well below the shared resource — no single dependency above about 30 % — and accept that under pressure you will serve a degraded answer for the excess. Sizing it at 100 % is the same as not having one."
- *"What makes a good fallback?"* — "Cheap, local, and truthful: a stale cached value, a global default, a partial response, or an explicit empty state. Never another remote call, and always counted in a metric so degradation is visible rather than silent."

**Common interview tasks:** design the defence stack for a service with three dependencies; size a bulkhead; choose breaker parameters given an SLO; design degradation modes for a page; explain why availability multiplies and how a fallback changes it; diagnose a cascading failure from metrics.

**Red flags interviewers watch for:**

- "Add a circuit breaker" with no slow-call threshold.
- No isolation: one shared pool across all dependencies.
- Fallbacks that call other services.
- No mention of degradation being observable.
- Breakers used as a substitute for capacity or shedding.
- No timeouts, so nothing measures the slowness.
- Unable to explain *why* one slow dependency affects unrelated endpoints.
- No numbers anywhere: no pool arithmetic, no thresholds, no detection time.

## 17. Knowledge check

**Conceptual**

1. Explain the mechanism by which one slow-but-successful dependency takes down endpoints that do not call it at all. Then explain which of the three defences interrupts that mechanism, and at which step.
2. Explain why a circuit breaker configured only on error rate fails to protect against the most common cascade, and describe the two additional triggers that make it effective. Give the ordering constraint between the per-call timeout and the slow-call threshold.
3. Explain the three kinds of bulkhead and when each is appropriate. Then explain why a bulkhead sized at the full capacity of the shared resource provides no isolation, and state the sizing rule you would apply.

**Practical scenario**

4. BookNook's product page calls catalog (20 ms, essential), reviews (80 ms, essential but cacheable), recommendations (300 ms nominal, non-essential), and inventory (40 ms, essential for the buy button). Steady state is 1,200 rps; the pool is 200 threads; a 4,000 rps sale is coming for 20 minutes. Design the full defence stack: pool partitioning per dependency with the arithmetic, breaker parameters for each with justification, the degradation ladder per section, what the page returns in each failure combination, the metrics and alarms with thresholds, the load-test you would run to prove it works, and the goodput and availability arithmetic before and after.

**Calculation**

5. A service has 150 threads, 15 ms of own work, and a dependency at `p99 = 120 ms` nominal. (a) Compute nominal capacity. (b) Compute capacity if the dependency degrades to 1,800 ms while still succeeding. (c) Compute the bulkhead size that caps that dependency's damage at 25 % of the pool, and the resulting worst-case capacity loss. (d) With a breaker that opens after 10 s of failures, compute the thread-seconds wasted during detection at 800 rps, and compare it with detection at 30 s.

6. A page depends on three services with availabilities 99.95 %, 99.9 % and 99.0 %. (a) Compute the page availability if all three are hard dependencies. (b) Recompute with a fallback on the third. (c) Compute monthly downtime in both cases. (d) If the fallback itself has a 2 % chance of being unavailable and the third service is 99 % available, compute the effective availability of that section and state what that tells you about the value of the fallback.

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
  - [x] 12. Dead-letter queues and retry queues (L75)
  - [x] 13. Backpressure and load shedding (L76)
  - [x] **14. Circuit breakers, bulkheads and graceful degradation (L77) ← you are here**
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

**NEXT LESSON: Phase 4, Topic 15 — Distributed locks and leases**

---
