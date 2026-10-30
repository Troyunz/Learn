# Phase 2 — Scaling (Part 2)

Lessons 23–26, covering Phase 2 Topics 6–9.

Continues from [phase-2-part-1.md](./phase-2-part-1.md) (Lessons 18–22).

---

# Lesson 23 — Load-Balancing Strategies

*Phase 2, Topic 6*

## 1. Simple definition

A **load-balancing strategy** (or algorithm) is the rule a load balancer uses to answer one question, thousands of times per second:

> *"This request just arrived. Which of my healthy backend servers should get it?"*

Lesson 21 covered *what* a load balancer is. This lesson covers *how it chooses*.

Terms defined before use:
- **Backend / upstream / pool member**: one server the LB can forward to. All three words mean the same thing; different vendors use different ones.
- **In-flight request**: one the LB has forwarded but not yet received a response for.
- **Load**: how much work a server currently has. Crucially, the LB can only *estimate* this — it has no direct view inside the server.

The entire difficulty of this topic comes from that last point. **The load balancer is guessing.** Every strategy is a different guess, with different accuracy and different cost.

## 2. Why it exists

Suppose BookNook has three app servers and simply sends every request to the first healthy one. That server saturates while two sit idle — you have paid for three servers and got the throughput of one. So you need *some* distribution rule.

The naive answer is "spread them evenly". That is round robin, and it is correct only when **every request costs the same**. Real requests do not:

| BookNook endpoint | Typical server time |
|---|---|
| `GET /health` | 0.1 ms |
| `GET /books/{isbn}` (cache hit) | 2 ms |
| `GET /books/{isbn}` (cache miss) | 20 ms |
| `GET /search?q=...` | 120 ms |
| `POST /orders` | 250 ms |
| `GET /reports/sales.csv` | 8,000 ms |

A 80,000× spread between cheapest and most expensive. Hand three servers an identical *count* of requests and they can still end up with wildly different *work*. One server gets three report exports and queues up; the others idle. Users on the unlucky server see p99 latency explode (Lesson 16), even though average utilisation across the fleet looks fine.

So load-balancing strategies exist to answer: **how do we distribute work, not just requests, using only the limited information the LB actually has?**

Without good strategies you get the worst of both worlds — you pay for N servers and get the tail latency of the single most unlucky one.

## 3. Real-world analogy

**Supermarket checkout queues.**

- **Round robin** = a greeter sending shoppers to tills 1, 2, 3, 1, 2, 3… regardless of trolley size. Fair in headcount, unfair in work.
- **Least connections** = the greeter sends you to the till with the fewest people waiting. Much better, and it is what a thoughtful human actually does.
- **Least response time** = the greeter also knows which cashier is fastest and weights accordingly.
- **IP hash** = "customers whose surname starts A–H always use till 1." Predictable, enables the cashier to remember you, but hopeless if a coach party of 60 A–H surnames arrives.
- **Power of two choices** = rather than scanning all 30 tills (slow), glance at two random tills and join the shorter. Almost as good, a fraction of the effort.

**Where the analogy breaks down:**

| Supermarket | Load balancer |
|---|---|
| The greeter sees trolley contents and can estimate the work | The LB sees only an HTTP request line; it cannot know that `/search?q=x` is 60× costlier than `/books/1` |
| Queue length is visible at a glance | With multiple LB instances, each knows only about *its own* in-flight requests, not the others' — so "least connections" is a partial view |
| A shopper stays in one queue | HTTP connections are reused for many requests; a connection is not a unit of work |
| 30 tills | Hundreds or thousands of backends, where scanning all of them per request is itself a measurable cost |
| A slow cashier is obviously slow | A failing server often gets *faster* — it returns errors instantly, so "least connections" sends it **more** traffic. This is the single most dangerous failure mode in this lesson. |

That last row deserves emphasis. A backend that instantly 500s has near-zero in-flight requests, so least-connections preferentially routes traffic *into the broken server*. This is called a **black hole** or **traffic magnet**, and it is why load balancing strategy and health checking (Lesson 24) must be designed together.

## 4. Detailed technical explanation

### Round robin

Maintain a counter; send request *n* to server `n mod N`.

```
req 1 → A   req 4 → A
req 2 → B   req 5 → B
req 3 → C   req 6 → C
```

- **Cost:** O(1), a single integer increment. Essentially free.
- **State needed:** one counter.
- **Correct when:** requests are homogeneous *and* servers are identical.
- **Fails when:** request costs vary, or servers differ in capacity, or connections are long-lived.

Note the subtlety with **keep-alive** (Lesson 5): with HTTP/1.1 connection reuse, an L7 LB may balance *connections* rather than *requests*. If server A's connection happens to carry 1,000 requests and B's carries 10, round robin over connections produced a 100:1 imbalance. Good L7 balancers balance per-request; L4 balancers inherently cannot (Lesson 22).

### Weighted round robin

Assign each server a weight proportional to its capacity.

```
A (weight 4, 16 vCPU)  B (weight 2, 8 vCPU)  C (weight 1, 4 vCPU)
Sequence: A A A A B B C  → repeat
```

Naive implementations emit the servers in bursts (`AAAABBC`), which is lumpy. Production implementations use **smooth weighted round robin** (nginx's algorithm), which interleaves to `A B A C A B A`, keeping the ratio while avoiding bursts.

- **Correct when:** servers are heterogeneous but request costs are uniform.
- **Fails when:** weights are set once and never revisited — a classic source of drift after the fleet is upgraded.

### Least connections

Track in-flight requests per backend; send the next request to the minimum.

```
A: 12 in-flight
B:  3 in-flight   ← chosen
C:  9 in-flight
```

This is **adaptive**: it automatically compensates for varying request costs, because an expensive request keeps the counter elevated for longer. It is the best general-purpose default for variable workloads.

- **Cost:** O(N) scan, or O(log N) with a heap. At N=1,000 and 50,000 requests/sec, an O(N) scan is 50 M comparisons/sec — measurable.
- **State needed:** a counter per backend, incremented on dispatch, decremented on completion.
- **Critical weakness:** the black-hole problem above. Must be paired with outlier ejection (Lesson 24).
- **Second weakness:** with multiple independent LB instances, each sees only its own connections. With 8 LBs, each has 1/8 of the picture, and they can collectively stampede the same "idle" backend.

**Weighted least connections** divides the count by the weight: choose `min(connections[i] / weight[i])`, combining capacity awareness with adaptivity.

### Least response time

Choose the backend minimising a blend of in-flight count and recently observed latency (typically an exponentially weighted moving average, EWMA).

```
score(server) = active_connections × recent_avg_latency
```

- **Pro:** reacts to a server that is degraded but not failed — a GC-paused or disk-thrashing instance gets less traffic automatically.
- **Con:** still fooled by fast failures (errors are fast). Must exclude error responses from the latency average, or explicitly penalise them.
- **Con:** EWMA has a time constant; it lags real changes by that constant.

### IP hash / source hash

```
backend = servers[ hash(client_ip) % N ]
```

Deterministic: the same client always lands on the same server. Used to get session affinity without cookies (Lesson 2).

Three serious problems:
1. **NAT collapses clients** (Lesson 3). An office of 800 employees behind one public IP hashes to a single server.
2. **`% N` is catastrophic on membership change.** Add or remove one server and nearly every client is remapped (quantified in Lesson 37). Hence consistent hashing.
3. **It ignores load entirely.** Distribution is only as even as your client-IP distribution, which is never even.

### Consistent hashing (introduced)

Instead of `hash(key) % N`, map both keys and servers onto a circular hash space and assign each key to the next server clockwise. Adding or removing a server moves only about `K/N` keys rather than nearly all of them.

This matters whenever a backend holds **per-key state** — a cache shard, a session, a WebSocket room. Full treatment with virtual nodes is **Phase 2, Topic 21 — Consistent hashing and virtual nodes** (Lesson 38).

### Random

Pick a uniformly random backend.

- **Cost:** O(1), stateless, and crucially it requires **no coordination between LB instances** — so it scales to any number of LBs without the partial-view problem.
- **Quality:** surprisingly decent on average, but the *maximum* load is poor. With *n* balls into *n* bins, the fullest bin holds roughly `log n / log log n` balls — for n = 100, about 3–4× the average.

### Power of two choices ("P2C")

Pick **two** backends at random; send the request to whichever has fewer in-flight requests.

This is the most important result in practical load balancing, and it is worth understanding why.

| Strategy | Expected max load (n requests, n servers) |
|---|---|
| Pure random (1 choice) | ~`log n / log log n` — for n=100, ≈ 3.5× average |
| **Two random choices** | ~`log log n / log 2` — for n=100, ≈ 2.4, i.e. **nearly constant** |
| d random choices (d>2) | `log log n / log d` — only marginally better than d=2 |

The intuition: with one choice you are blind. With two, you need *both* randomly chosen servers to be overloaded for you to make a bad decision, and the probability of that is the square of the single probability. Going from 1→2 choices buys an exponential improvement; going 2→3 buys almost nothing. Hence "the power of **two** choices" specifically.

Why it wins in practice over true least-connections:
- **O(1) instead of O(N)** — no full scan.
- **No global state** — works correctly with many independent LB instances, where true least-connections degrades.
- Used by default in Envoy, Finagle, and most modern service meshes.

### Comparison

| Strategy | Cost | Adapts to request cost? | Handles heterogeneous servers? | Affinity? | Best for | Fails when |
|---|---|---|---|---|---|---|
| Round robin | O(1) | No | No | No | Uniform requests, identical servers | Costs vary; long-lived connections |
| Weighted RR | O(1) | No | Yes | No | Mixed instance sizes, uniform requests | Weights go stale |
| Least connections | O(N)/O(log N) | **Yes** | With weights | No | **Variable request costs — the usual default** | Fast-failing backends become magnets |
| Least response time | O(N) | Yes, plus degradation | Implicitly | No | Latency-sensitive, heterogeneous | Errors pollute the latency signal |
| IP hash | O(1) | No | No | **Yes** | Legacy affinity without cookies | NAT; membership changes |
| Consistent hash | O(log N) | No | With vnodes | **Yes** | Cache shards, stateful routing | Hot keys |
| Random | O(1) | No | No | No | Huge fleets, many LBs | Poor tail |
| **Power of two choices** | **O(1)** | **Yes** | With weights | No | **Large fleets, many LB instances** | Needs in-flight counts |

### Interaction with session affinity

If you enable sticky sessions (Lesson 2), you have *overridden* the strategy. A sticky request ignores least-connections entirely. This is why statelessness (Lesson 20) is a prerequisite for effective load balancing: affinity and good distribution are directly in conflict.

## 5. Request / data flow

A `POST /orders` arrives at BookNook's LB, configured for weighted least connections across three servers:

1. Request arrives; LB terminates TLS (Lesson 7) and parses the HTTP request line (L7 — Lesson 22).
2. LB consults its **healthy** backend set. Server C was ejected 40 seconds ago by outlier detection, so the candidate set is {A, B}.
3. LB reads current in-flight counts: A = 14, B = 5.
4. LB reads configured weights: A = 2 (8 vCPU), B = 1 (4 vCPU).
5. LB computes weighted scores: A = 14/2 = 7.0, B = 5/1 = 5.0.
6. B has the lower score → B is selected. (Note: raw counts said B was *obviously* better at 5 vs 14; weighting narrowed it, which is correct — A has twice the capacity.)
7. LB increments B's in-flight counter to 6.
8. LB picks a pooled keep-alive connection to B, or opens a new one.
9. LB adds `X-Forwarded-For` and `X-Request-ID` headers (Lesson 5).
10. Request is forwarded. A **per-try timeout** of 2 s starts (Lesson 17).
11. B processes for 250 ms and responds `201 Created`.
12. LB decrements B's counter to 5, records 250 ms in B's EWMA, forwards the response.
13. LB returns the backend connection to the pool for reuse.

Step 12 is where adaptivity happens: because `POST /orders` is slow, B's counter stayed elevated for 250 ms, during which the LB naturally sent other requests to A. No configuration expressed "orders are expensive" — the algorithm discovered it.

## 6. ASCII diagram

```
ROUND ROBIN — counts are even, WORK is not
  req: 1  2  3  4  5  6
       │  │  │  │  │  │
       ▼  ▼  ▼  ▼  ▼  ▼
   A ──[2ms][2ms]              total   4 ms   ← idle
   B ──[2ms][8000ms]           total 8002 ms  ← MELTDOWN
   C ──[2ms][2ms]              total   4 ms   ← idle
  Each server got exactly 2 requests. One of them is on fire.


LEAST CONNECTIONS — tracks WORK, self-correcting
  in-flight:  A:1   B:1(report, 8s)   C:1
  next req ──▶ A or C  (B's counter stays at 1 for 8 full seconds,
                        so B is automatically skipped ~hundreds of times)


POWER OF TWO CHOICES — O(1) and almost as good as least-conn
            ┌──────────── pick 2 at random ───────────┐
            ▼                                          ▼
   A:14   B:5   C:9   D:22   E:3   F:11   G:7   H:19
            └── compare just these two ──┘
                    5 < 22  ⇒ send to B
  No full scan. No shared state between LB instances.


THE BLACK HOLE — why strategy needs health checking
   A: 12 in-flight (healthy, working hard)
   B: 11 in-flight (healthy, working hard)
   C:  0 in-flight (BROKEN — returns 500 in 0.3 ms)
                 ▲
   least-connections sends EVERY request here, because
   "fastest" and "emptiest" look identical to "broken".
   ⇒ 100% of traffic into the failed server.
   FIX: outlier ejection on error rate (Lesson 24), not just latency.
```

## 7. Detailed example — BookNook

BookNook runs three app servers behind one LB. Traffic is 95 % cheap reads, 4 % orders, 1 % CSV report exports.

**Phase 1 — round robin (the starting configuration).** Average latency looks fine at 18 ms. But the support queue fills with "the site froze for me" reports, roughly 1 in 100 sessions. p99 latency is 6,400 ms while p50 is 4 ms.

Diagnosis: with 8-second report exports distributed by round robin, a server handling an export still receives its full 1-in-3 share of normal requests. Those queue behind the export (the app uses a bounded worker pool). One slow request **blocks the queue for every request behind it** — head-of-line blocking at the application tier, structurally the same problem as Lesson 9's HTTP/1.1 discussion.

**Phase 2 — switch to least connections.** p99 falls from 6,400 ms to 900 ms. The export keeps the server's counter high, so the LB routes around it automatically. **One configuration line, 7× p99 improvement, zero code change.** This is why the strategy choice matters.

**Phase 3 — the remaining 900 ms.** Still bad. The real fix is architectural, not a balancing tweak: **long-running work does not belong in the synchronous request path.** `GET /reports/sales.csv` becomes `POST /reports` returning `202 Accepted` with a job ID, processed by a separate worker pool (Lesson 13; Phase 4, Topic 5 — Message queues). p99 falls to 140 ms.

The lesson to carry forward: **load balancing strategy mitigates workload skew; it does not fix it.** Mixing 8-second and 2-millisecond work in one pool is the actual defect.

**Phase 4 — heterogeneous fleet.** BookNook adds two 16-vCPU servers alongside the three 8-vCPU ones. Plain least-connections treats them identically and under-uses the big machines. Weighted least connections (weights 2 and 1) restores proportionality.

**Second example for contrast — a WebSocket chat service.** Here the strategy question inverts. Connections last hours, so "requests per second" is meaningless; what matters is *connection count* and *room locality*. Least-connections is right for the initial connection placement, but you also want all members of one chat room on one server to avoid cross-server message fan-out — which requires **consistent hashing on room ID**, not on client IP. Two different goals (even load vs locality) that directly conflict, resolved by hashing rooms onto the ring and accepting some imbalance. Same menu of strategies, completely different selection, because the unit of work changed from "request" to "connection-with-affinity".

## 8. Code or configuration example

> Illustrative only — not production-ready. Timeouts, TLS, observability and security hardening are omitted for clarity.

### nginx

```nginx
# Default: round robin. Correct only for uniform request costs.
upstream booknook_rr {
    server 10.0.2.11:8000;
    server 10.0.2.12:8000;
    server 10.0.2.13:8000;
}

# Weighted round robin for a heterogeneous fleet.
# nginx uses SMOOTH weighted RR, so the order interleaves (A B A C A B A)
# rather than bursting (A A A A B B C).
upstream booknook_weighted {
    server 10.0.2.11:8000 weight=4;   # 16 vCPU
    server 10.0.2.12:8000 weight=2;   # 8 vCPU
    server 10.0.2.13:8000 weight=1;   # 4 vCPU
}

# Least connections -- the right default when request costs vary.
upstream booknook_leastconn {
    least_conn;
    server 10.0.2.11:8000 weight=2;   # weights still apply: score = conns / weight
    server 10.0.2.12:8000 weight=1;
    server 10.0.2.13:8000 weight=1;

    # max_fails/fail_timeout = nginx's PASSIVE health checking.
    # Without this, a fast-failing backend becomes a black hole under least_conn.
    server 10.0.2.14:8000 max_fails=3 fail_timeout=30s;

    # Reuse backend connections. Without this, every request opens a new TCP
    # connection to the backend and you risk ephemeral port exhaustion (Lesson 3).
    keepalive 64;
}

# IP hash -- affinity without cookies. Note the two traps.
upstream booknook_iphash {
    ip_hash;                 # TRAP 1: an office behind one NAT all land on one server
    server 10.0.2.11:8000;   # TRAP 2: adding a server remaps nearly every client
    server 10.0.2.12:8000;
}

# Consistent hashing -- only ~K/N keys move on membership change (Lesson 38).
upstream booknook_cache_shards {
    hash $request_uri consistent;    # route by URI so each shard caches a stable subset
    server 10.0.4.11:11211;
    server 10.0.4.12:11211;
    server 10.0.4.13:11211;
}

server {
    listen 443 ssl;
    location / {
        proxy_pass http://booknook_leastconn;
        proxy_http_version 1.1;
        proxy_set_header Connection "";          # required for upstream keepalive
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_connect_timeout 2s;                # Lesson 17: never leave this unset
        proxy_read_timeout   10s;
    }
}
```

### Envoy — power of two choices

```yaml
# Envoy's DEFAULT lb_policy is LEAST_REQUEST, which is implemented as
# power-of-two-choices, NOT a full O(N) scan. This is deliberate:
# it is O(1) and works correctly with many independent proxy instances.
clusters:
- name: booknook_app
  lb_policy: LEAST_REQUEST
  least_request_lb_config:
    choice_count: 2          # the "two" in power of two choices.
                             # Raising this to 3+ yields almost no improvement
                             # (log log n / log d) while costing more per request.

  outlier_detection:
    # THIS is what prevents the black-hole problem. Without it, a backend that
    # fails fast has the fewest in-flight requests and attracts ALL traffic.
    consecutive_5xx: 5           # 5 consecutive 500s ...
    interval: 10s                # ... checked every 10s ...
    base_ejection_time: 30s      # ... ejects the host for 30s (doubling on repeat)
    max_ejection_percent: 50     # NEVER eject more than half the fleet, or a
                                 # dependency outage would eject everything and
                                 # turn a brownout into a total outage.
```

### Power of two choices, implemented

```python
import random

class P2CBalancer:
    """Illustrates the algorithm. Real implementations live in the proxy."""

    def __init__(self, backends):
        # in-flight request count per backend
        self.inflight = {b: 0 for b in backends}
        # relative capacity; score is normalised by this
        self.weight = {b: 1.0 for b in backends}

    def choose(self):
        backends = list(self.inflight)

        # Sample TWO distinct backends at random -- O(1), regardless of fleet size.
        # This is the entire algorithm. No scan, no shared state, no coordination
        # with other load balancer instances.
        a, b = random.sample(backends, 2)

        # Normalise by weight so a 16-vCPU box may legitimately hold more work.
        score_a = self.inflight[a] / self.weight[a]
        score_b = self.inflight[b] / self.weight[b]

        return a if score_a <= score_b else b

    def dispatch(self, request):
        target = self.choose()
        self.inflight[target] += 1          # increment BEFORE sending
        try:
            return send(target, request)
        finally:
            # MUST decrement on EVERY path, including exceptions and timeouts.
            # A leaked counter permanently poisons that backend's score --
            # it will look busy forever and silently stop receiving traffic.
            # This is a real and common bug.
            self.inflight[target] -= 1
```

## 9. Numerical example

### Part A — Why round robin fails with skewed costs

**Assumptions (stated explicitly):**
- 3 identical servers, each with a worker pool of 10 concurrent slots.
- 1,000 requests arrive in one second.
- 99 % are cheap reads at 5 ms; 1 % are report exports at 8,000 ms.
- Requests are distributed strictly in arrival order.

```
Total requests        = 1,000
Cheap requests        = 990 × 5 ms      = 4,950 ms of work
Expensive requests    = 10 × 8,000 ms   = 80,000 ms of work
Total work            = 84,950 ms
Perfectly balanced    = 84,950 / 3      = 28,317 ms per server
```

Round robin distributes the 10 expensive requests by position. They are **not** guaranteed to spread 3/3/4 — arrival position is effectively random relative to the counter. A plausible split is 5/3/2:

```
Server A: 330 cheap (1,650 ms) + 5 exports (40,000 ms) = 41,650 ms
Server B: 330 cheap (1,650 ms) + 3 exports (24,000 ms) = 25,650 ms
Server C: 330 cheap (1,650 ms) + 2 exports (16,000 ms) = 17,650 ms

Imbalance = 41,650 / 17,650 = 2.36×
Server A is 47% above the ideal 28,317 ms.
```

**Impact on waiting users.** Server A has 10 worker slots, 5 occupied by exports for 8 seconds:

```
Remaining slots on A    = 10 - 5 = 5
A's cheap request rate  = 330 req/s
Service rate with 5 slots = 5 slots / 0.005 s = 1,000 req/s
Utilisation             = 330 / 1,000 = 33%   → still OK
```

But if 8 of A's 10 slots are taken by exports:

```
Service rate = 2 / 0.005 = 400 req/s
Utilisation  = 330 / 400 = 82.5%
M/M/1 wait   = 1 / (μ - λ) = 1 / (400 - 330) = 1/70 s = 14.3 ms queueing
             vs 5 ms service time → nearly 3× latency inflation
```

And at 9 of 10 slots consumed:
```
Service rate = 1 / 0.005 = 200 req/s  <  330 req/s arrival rate
⇒ λ > μ: the queue grows WITHOUT BOUND. Requests time out. The server is down.
```

This is the queueing cliff from Lesson 14 — utilisation does not degrade gracefully, it falls off a cliff between 90 % and 100 %.

### Part B — Least connections on the same workload

Least connections never sends a new request to a server whose in-flight count is already high. The 10 exports distribute themselves evenly because each one pins a counter for 8 seconds:

```
Exports per server    ≈ 10 / 3 = 3.33
Work per server       ≈ 3.33 × 8,000 + 330 × 5 = 26,667 + 1,650 = 28,317 ms
Imbalance             ≈ 1.0× (the ideal)
```

```
Round robin p99 (from the earlier simulation) ≈ 6,400 ms
Least connections p99                         ≈   900 ms
Improvement = 6,400 / 900 = 7.1×
```

Reduction achieved by changing one configuration directive.

### Part C — The cost of the O(N) scan

**Assumptions:** 50,000 requests/sec through one LB; comparison cost ≈ 2 ns; fleet of N backends.

```
N = 10     : 50,000 × 10   × 2 ns = 1 ms/s      = 0.1% of one core
N = 100    : 50,000 × 100  × 2 ns = 10 ms/s     = 1% of one core
N = 1,000  : 50,000 × 1,000× 2 ns = 100 ms/s    = 10% of one core
N = 10,000 : 50,000 × 10,000×2 ns = 1,000 ms/s  = 100% of one core  ← a whole core
             just to DECIDE where to send requests, before doing any work.

Power of two choices: 50,000 × 2 × 2 ns = 0.2 ms/s = 0.02% of a core,
                      at ANY fleet size.
```

**Conclusion:** below ~100 backends, use least connections — it is strictly more accurate. Above ~1,000, use P2C. The crossover is roughly where the scan cost exceeds 1 % of a core, i.e. around N = 100–500.

### Part D — The black hole, quantified

**Assumptions:** 3 servers; A and B healthy (200 ms average response); C is broken and returns HTTP 500 in 0.3 ms; least connections; no outlier detection; 300 RPS arriving.

```
Little's Law (Lesson 14), L = λ × W, per server:
  Healthy server at 100 RPS : L = 100 × 0.200 s = 20 in-flight
  Broken server   at 100 RPS: L = 100 × 0.0003 s = 0.03 in-flight

Least connections compares 20, 20, 0.03 → ALWAYS picks C.
```

C therefore absorbs the entire 300 RPS:
```
C at 300 RPS: L = 300 × 0.0003 = 0.09 in-flight — still the lowest!
The feedback loop never self-corrects.
⇒ 100% of users receive HTTP 500, while A and B sit idle.
Fleet-wide CPU looks LOW. Monitoring that alerts on CPU sees nothing wrong.
```

**Detection time with outlier detection configured** (`consecutive_5xx: 5`, `interval: 10s`):

```
Requests needed to trip      = 5 consecutive 5xx
Time to accumulate at 300 RPS= 5 / 300 = 0.017 s
Evaluation interval          = up to 10 s
Worst-case detection         = ~10 s
Failed requests in that time = 300 × 10 = 3,000 requests
```

3,000 failed requests is bad, but bounded. Without outlier detection, the failure is **unbounded in time** — it continues until a human intervenes. This is the single strongest argument for pairing least-connections with error-based ejection.

## 10. Trade-offs

**Round robin**
- *Advantages:* trivially simple, O(1), no state, perfectly predictable, easy to reason about during an incident.
- *Disadvantages:* ignores request cost, server capacity, and server health entirely.
- *Operational cost:* near zero.
- *Unnecessary complexity to replace when:* your requests genuinely are uniform (e.g. a static file proxy, a health-check endpoint) — then round robin is optimal and anything else is wasted CPU.

**Least connections**
- *Advantages:* self-correcting under variable cost; no configuration describing workload needed; large real-world p99 improvements.
- *Disadvantages:* O(N); partial view across multiple LBs; **actively dangerous** without error-based ejection.
- *Operational cost:* requires in-flight accounting that must be correct on every error path (see the `finally` block in Section 8).

**Power of two choices**
- *Advantages:* O(1) with nearly least-connections quality; no coordination needed between LB instances; the right default at scale.
- *Disadvantages:* slightly worse than true least-connections for small N; less intuitive to explain during an incident.
- *Unnecessary complexity when:* N < 20, where a full scan is free and exact.

**IP hash / consistent hash**
- *Advantages:* affinity without cookies; essential for cache sharding and stateful routing.
- *Disadvantages:* ignores load; NAT skew; hot keys; adds a whole class of rebalancing concerns.
- *Unnecessary complexity when:* your app tier is properly stateless (Lesson 20) — then affinity buys nothing and costs distribution quality.

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| Fast-failing backend becomes a black hole under least-connections | **100 % of requests fail**, instantly, while fleet CPU looks low | Error rate by backend (not aggregate); a backend with high RPS and near-zero latency | Outlier ejection on `consecutive_5xx`; the backend is removed within one check interval |
| Round robin with skewed request costs | Intermittent multi-second hangs for ~1 in N users; p50 looks healthy, p99 is terrible | p99/p50 ratio; per-backend queue depth | Switch to least connections; move long work out of the request path |
| In-flight counter leaks on an error path | One backend silently stops receiving traffic; capacity quietly drops | Per-backend request-rate dashboard shows one flat-lined server | Fix the decrement to run in a `finally`; restart the LB to clear the counter |
| Stale weights after a fleet upgrade | New, larger servers are under-used; old ones saturate | Per-backend CPU diverges while request counts match the old weights | Re-derive weights from instance size; automate it |
| IP hash with a large NAT client | One corporate customer's traffic saturates a single server | One backend hot; `X-Forwarded-For` shows one source IP dominating | Move to cookie-based affinity, or make the tier stateless and drop affinity |
| `% N` rehash after adding a backend | Mass cache misses; a database load spike immediately after a scale-out | DB QPS spike coinciding with a deploy or scale event | Consistent hashing (Lesson 38) |
| Multiple LBs each with a partial view | Synchronised stampede onto a freshly added "empty" backend; it immediately overloads | New backend's latency spikes the moment it joins | Slow start / gradual traffic ramp on newly added backends |
| `max_ejection_percent` unset during a shared-dependency outage | Every backend ejected; LB has no targets; total outage instead of partial degradation | "No healthy upstream" errors; zero backends in pool | Cap ejection at ~50 %; prefer serving degraded responses over serving none |

## 12. Alternatives

| Approach | Where the decision is made | Pros | Cons |
|---|---|---|---|
| **Proxy-side balancing** (nginx, HAProxy, ALB) | A central LB | Simple clients; one control point; TLS termination | Extra network hop (~1 ms); the LB is a scaling and failure concern |
| **Client-side balancing** (gRPC, Finagle, Ribbon) | Inside the calling service | No extra hop; client knows its own latency precisely | Every client needs the backend list; harder to change policy; language-specific |
| **Service mesh / sidecar** (Envoy, Linkerd) | A proxy on each host | Central policy, local decisions, uniform telemetry, mTLS | Significant operational complexity (Phase 4, Topic 24) |
| **DNS round robin** | The client's resolver | Free, no hop, works across regions | No health or load awareness; caching defeats it (Lesson 4) |
| **Anycast** | Internet routing (BGP) | Instant regional failover; DDoS absorption | No per-request awareness; requires BGP and POPs |
| **Queue-based pull** (workers pull from a queue) | The worker itself | **Perfect** load balancing — a worker takes work only when free | Only for async work, not request/response (Phase 4, Topic 5) |

That last row is worth internalising. Pull-based distribution is *inherently* optimal — no estimation is involved, because a worker only takes a task when it genuinely has capacity. Every push-based strategy in this lesson is an attempt to approximate what a queue gives you for free. When you can make work asynchronous, do, and the load-balancing problem largely disappears.

## 13. When to use it

1. **Any time you run more than one app server.** The strategy choice is a one-line configuration with order-of-magnitude p99 consequences, as Section 9 showed.
2. **When request costs are variable** — search, reports, uploads, or anything touching a third party. Use least connections or P2C; round robin will bite you.
3. **When servers are heterogeneous** — mixed instance sizes, or a fleet mid-upgrade. Use weighted variants so capacity is respected.
4. **When backends hold per-key state** — cache shards, WebSocket rooms, stateful partitions. Use consistent hashing (Lesson 38), and accept that you are trading distribution quality for locality.
5. **When the fleet is large (N > 500)** — use power of two choices so the decision cost stays O(1).

## 14. When *not* to use it

1. **Single server.** No distribution decision exists. Do not install an LB for "future-proofing"; you have added a failure mode and a hop for zero benefit.
2. **Genuinely uniform workloads.** A static-asset proxy where every request is a 20 KB file lookup gains nothing from least connections. Round robin is optimal and cheaper.
3. **As a substitute for fixing workload skew.** If your p99 problem is an 8-second report in the request path, the answer is asynchronous processing (Lesson 13), not a cleverer balancer. Section 7 Phase 3 made this explicit: the balancer bought 7×, the architecture change bought another 6×.
4. **Where a pull-based queue fits.** For background work, workers pulling from a queue achieve perfect balance with no algorithm at all. Do not build push-based balancing for async jobs.

## 15. Common mistakes

**Beginner mistakes**
- Leaving round robin as the default without checking whether request costs vary — then being baffled by a bad p99 next to a fine p50.
- Enabling `ip_hash` for affinity without realising a corporate NAT will land 800 users on one server (Lesson 3).
- Believing the LB "knows" server load. It is estimating from in-flight counts, and it is frequently wrong.
- Using least connections without any health checking, creating the black hole in Section 9 Part D.
- Confusing balancing *connections* with balancing *requests* under HTTP keep-alive.
- Measuring only fleet-average CPU, which hides a severe per-server imbalance.

**Experienced-engineer mistakes**
- Forgetting to decrement the in-flight counter on error and timeout paths, silently removing a server from rotation.
- Running least connections across many LB instances and being surprised by stampedes onto newly added backends — each LB independently sees it as empty. The fix is slow start plus P2C.
- Setting `max_ejection_percent` to 100, so a shared-dependency blip ejects the entire fleet and converts a partial degradation into a total outage.
- Leaving weights hardcoded after replacing the fleet with different instance types.
- Using latency-based balancing without excluding error responses, so fast failures look like excellent performance.
- Reaching for a service mesh to get P2C when a one-line nginx change would have solved the actual problem.
- Treating consistent hashing as a load-balancing strategy for stateless servers, where it only reduces distribution quality for no benefit.

## 16. Interview perspective

This is a high-yield topic: it is concrete, quantifiable, and most candidates answer it shallowly.

**How to use it:**

1. **Do not just say "I'll add a load balancer."** Say which strategy and why: *"Least connections, because search and checkout are 50× costlier than a product page, and round robin would let one slow request block the queue behind it."* That one sentence separates you from most candidates.
2. **Volunteer the black-hole failure mode.** *"Least connections needs outlier ejection on error rate — a backend that fails fast has the fewest in-flight requests, so it would attract all the traffic."* Very few candidates mention this, and it demonstrates you have operated systems rather than just read about them.
3. **Know P2C and why two.** *"At this fleet size I'd use power of two choices — O(1), no coordination between proxies, and the max-load bound improves from log n / log log n to log log n."* Then add the intuition: both sampled servers must be bad for the choice to be bad.
4. **Connect strategy to statefulness.** *"If the app tier is stateless I want free distribution; if backends own cache shards I need consistent hashing and I'll accept some imbalance for locality."*
5. **Know when the answer is not a balancer.** If the interviewer describes a workload with an 8-second export, the senior answer is *"move it off the request path"*, not *"use least connections"*.
6. **Quantify.** The Part A/B numbers (2.36× imbalance, 7× p99 improvement) are the kind of concreteness interviewers remember.

**Red flags interviewers watch for:**
- Saying "load balancer" with no mention of *how* it balances.
- Not knowing least connections exists, or thinking round robin is always fine.
- Proposing sticky sessions as a first resort rather than making the tier stateless.
- No awareness that a failing backend can attract traffic.
- Claiming the LB knows backend CPU (it does not, unless you have explicitly built that feedback).
- Using IP hash in a design that also mentions mobile clients or corporate users behind NAT.

## 17. Knowledge check

Write your answers down before continuing.

**Conceptual**

1. BookNook switches from round robin to least connections and p99 drops from 6,400 ms to 900 ms, with no code change. Explain the mechanism by which least connections achieved this, then explain why the remaining 900 ms **cannot** be fixed by any further change of balancing strategy, and state what would actually fix it.

2. Explain precisely why "power of two choices" is nearly as good as true least-connections but costs O(1), and why increasing from two random choices to five yields almost no further benefit. Then describe one realistic deployment in which true least-connections is clearly the better choice, and justify it.

3. A backend begins returning HTTP 500 in 0.3 ms while its healthy peers take 200 ms. Explain, step by step and with reference to Little's Law, why a least-connections balancer will route *more* traffic to the broken server rather than less. Then state what signal — not latency, not connection count — must be used to detect this, and why fleet-average CPU monitoring would show nothing.

**Practical scenario**

4. BookNook deploys a new recommendations service behind an LB using least connections across 6 instances, with `consecutive_5xx: 5`, `base_ejection_time: 30s`, and `max_ejection_percent: 100`. The recommendations service depends on a single Redis instance. Redis has a 90-second outage. Describe exactly what happens at the load balancer during and after those 90 seconds, explain why the user impact is *worse* than if no outlier detection had been configured at all, state the specific configuration change that fixes it, and describe one application-level change that would have made the service degrade gracefully instead.

**Calculation**

5. BookNook's search tier has 8 servers, each with a worker pool of 25 concurrent slots. Search requests take 120 ms on average. Separately, 2 % of traffic is a "rebuild index" admin call taking 15 seconds, which occupies one worker slot for its full duration.
   Assumptions: total arrival rate is 400 requests/second; requests are distributed by round robin; admin calls arrive uniformly at random.
   (a) Using Little's Law, how many worker slots are occupied on average across the fleet by normal search requests, and by admin calls? What fraction of total capacity does each consume?
   (b) Round robin happens to send 4 concurrent admin calls to one server. How many slots remain on that server, what is its effective service rate in requests/second, and what is its utilisation given its 1/8 share of arrivals?
   (c) Using the M/M/1 approximation `W = 1/(μ − λ)`, what is the queueing delay on that server, and how does the total latency compare with the 120 ms service time? At how many concurrent admin calls on one server does that server become unstable (λ ≥ μ)?
   Show every step and state any additional assumptions you make.

Reply with your answers and I'll assess them, correct anything shaky, and give model answers before Lesson 24.

---

**Progress checklist**

Phase 1 — Foundations: **complete** (Lessons 1–17)

Phase 2 — Scaling:
`[x] 1. Vertical scaling` · `[x] 2. Horizontal scaling` · `[x] 3. Stateless application servers` · `[x] 4. Load balancers` · `[x] 5. Layer 4 versus Layer 7` · `[x] 6. Load-balancing strategies`
`[ ] 7. Health checks and failover` ← next
`[ ] 8–22` not started

Phases 3–5: not started.

**NEXT LESSON: Phase 2, Topic 7 — Health checks and failover**

---

# Lesson 24 — Health Checks and Failover

*Phase 2, Topic 7*

## 1. Simple definition

A **health check** is a periodic question a system asks a server: *"are you able to serve traffic right now?"* The answer determines whether the server stays in the load balancer's rotation.

**Failover** is what happens when the answer is no: traffic is redirected to something that still works.

Three distinct questions get confused constantly, and separating them is most of this lesson:

| Question | Name | Wrong answer causes |
|---|---|---|
| "Is this process alive, or wedged?" | **Liveness** | Restarting a healthy server, or never restarting a hung one |
| "Can this process serve traffic *right now*?" | **Readiness** | Sending traffic to a server that is still warming up, or removing a server unnecessarily |
| "Has this process finished starting up?" | **Startup** | Killing a slow-booting server in a restart loop |

A liveness failure means **kill and restart me**. A readiness failure means **stop sending me traffic, but leave me alone**. These are opposite remedies. Conflating them is one of the most common causes of self-inflicted outages, and we will quantify why.

## 2. Why it exists

Lesson 23 assumed the load balancer knows which backends are healthy. That knowledge has to come from somewhere.

Consider BookNook with three app servers and no health checking. Server B's process crashes.

1. The LB keeps sending B its one-third share.
2. Every one of those requests fails — connection refused (Lesson 3).
3. **33 % of all users see an error**, indefinitely, until a human notices.
4. Your availability is now capped at 67 %, despite having 3× redundancy.

Redundancy without health checking provides **no availability benefit at all**. This is the crucial point: the `1 − (1 − a)^n` formula from Lesson 15 assumes failed components are *removed*. If they stay in rotation, you have simply added more ways to fail. Three servers with no health checks are *worse* than one server, because you have tripled the probability that some user hits a broken machine.

Health checks exist to make redundancy actually redundant.

They also handle a case that is far more common than a crash: a server that is **up but not useful**. Out of memory, deadlocked, disk full, dependency unreachable, still starting up, or mid-shutdown. A TCP connection succeeds in every one of those cases. "The process is running" is not the same as "the process works".

## 3. Real-world analogy

**A hospital triage nurse deciding which doctors can take patients.**

- Walking past each consulting room and checking the doctor is present = **liveness**.
- Asking "are you free and able to take the next patient?" = **readiness**.
- Giving a newly arrived doctor fifteen minutes to set up before their first patient = **startup probe**.
- Noticing one doctor's patients keep coming out worse = **passive health checking / outlier detection**.
- Redirecting that doctor's waiting patients to others = **failover**.
- Letting a doctor finish their current consultation before going off shift, rather than ejecting the patient = **connection draining**.

**Where the analogy breaks down:**

| Hospital | Real health checking |
|---|---|
| The nurse can see the doctor and judge for herself | The LB sees only an HTTP status code. A 200 is a *claim* of health, which the server may be wrong about |
| Asking "are you OK?" costs nothing | A check that queries the database costs a database connection — and at 50 servers × 1/second, that is 50 extra queries per second **forever** |
| A doctor asked "are you OK?" answers about themselves | A badly written health check answers about the *entire system*, so one slow database makes every server report unhealthy simultaneously |
| If all doctors are busy, patients simply wait | If all backends are ejected, the LB has nowhere to send traffic and returns "no healthy upstream" — a **total** outage, worse than a slow one |
| Checking more often is strictly better | Checking too often causes flapping and wastes capacity; checking too rarely leaves users on a dead server |

The third and fourth rows describe the central trap of this lesson, developed in Section 4.

## 4. Detailed technical explanation

### Active versus passive checks

**Active (proactive):** the LB periodically sends a synthetic request.

```
every 5s:  GET /healthz  →  expect 200 within 2s
```
- *Pro:* detects failure even with zero user traffic; detects *before* a user is affected.
- *Con:* costs resources; a synthetic request may not exercise what real requests exercise.

**Passive (reactive / inline):** the LB watches real traffic and counts failures.

```
5 consecutive 5xx from backend C  →  eject C for 30s
```
- *Pro:* free; measures exactly what users experience.
- *Con:* requires users to be harmed first; needs traffic to work at all.

**Production systems use both.** Active checks catch total failure quickly; passive checks catch partial degradation that a simple `/healthz` would never reveal (for example, the server returns 200 on `/healthz` but 500 on everything else because one code path has a bad dependency).

### The three probe types

| Probe | Question | On failure | Typical endpoint |
|---|---|---|---|
| **Startup** | Finished booting? | Keep waiting; do not run other probes yet | `/healthz/startup` |
| **Liveness** | Process wedged? | **Kill and restart** | `/healthz/live` |
| **Readiness** | Can serve now? | **Remove from LB** (do not restart) | `/healthz/ready` |

Why the separation matters, concretely:

- **Liveness must be shallow.** It should check only "is my event loop responsive?" If liveness checks the database, a database outage restarts every app server, repeatedly, forever. You turn a recoverable dependency failure into a fleet-wide crash loop that cannot recover even after the dependency returns — because the servers never stay up long enough to reconnect.
- **Readiness may be deeper**, but see the cascade trap below.
- **Startup probes exist** because a JVM app may take 90 seconds to boot. If liveness starts immediately with a 10-second threshold, the container is killed at 10 seconds, restarts, is killed again — an infinite loop that looks like a crash but is purely a configuration error.

### The deep health check trap

This is the most important idea in the lesson.

A tempting health endpoint:

```python
@app.get("/healthz")
def health():
    db.execute("SELECT 1")          # ← looks responsible
    redis.ping()                    # ← looks thorough
    payments_api.ping()             # ← looks comprehensive
    return {"status": "ok"}
```

Now the database slows down — not down, just slow, 3-second queries.

1. Every server's health check times out.
2. Every server is marked unhealthy **simultaneously**.
3. The LB ejects **the entire fleet**.
4. The LB has no healthy backends → returns `503 no healthy upstream` to **100 %** of requests.
5. Meanwhile the app servers were perfectly capable of serving the 70 % of traffic that is cached and does not touch the database at all.

**You converted a partial degradation into a total outage.** The health check did it, not the database.

Worse, this failure is **correlated** — exactly the correlated-failure case from Lesson 15 that defeats redundancy. Every server shares the dependency, so every server fails at the same instant. No amount of horizontal scaling helps.

The rules that follow from this:

1. **A health check should test the server, not the world.** Can *this process* accept a connection, allocate memory, and run its event loop?
2. **Shared dependencies belong in the application's error handling**, not in the health check. A failing dependency should produce a degraded response (Lesson 15 — graceful degradation), not an ejected server.
3. **If you must check a dependency, scope the ejection.** `max_ejection_percent: 50` guarantees at least half the fleet stays in rotation regardless of what the checks say. Serving degraded responses from half a fleet beats serving nothing from none.
4. **Check dependencies the server owns exclusively** (its own disk, its own local cache) — those are genuinely per-server and will not fail in lockstep.

### Detection time arithmetic

Four parameters determine how fast failure is noticed, and they multiply:

| Parameter | Meaning | Typical |
|---|---|---|
| `interval` | Seconds between checks | 5 s |
| `timeout` | How long to wait for a response | 2 s |
| `unhealthy_threshold` | Consecutive failures before ejection | 3 |
| `healthy_threshold` | Consecutive successes before re-admission | 2 |

```
Worst-case detection = (unhealthy_threshold × interval) + timeout
                     = (3 × 5 s) + 2 s
                     = 17 seconds
```

For 17 seconds, up to 1/N of traffic fails. The obvious response is to tighten the parameters — but that trades directly against **flapping**.

### Flapping and hysteresis

A server under heavy GC pressure may fail one check and pass the next. With `unhealthy_threshold: 1`, it is ejected and re-admitted every few seconds. Each transition causes connection churn, cache cold-starts, and load oscillation across the remaining servers — which can push *them* into the same state. The fleet oscillates.

**Hysteresis** is the fix: require more evidence to change state than to keep it. Asymmetric thresholds (`unhealthy: 3`, `healthy: 2`) plus **exponentially increasing ejection time** (30 s, then 60 s, then 120 s for repeat offenders) damp the oscillation. Envoy's `base_ejection_time` doubles per consecutive ejection for exactly this reason.

### Connection draining and graceful shutdown

When you deliberately remove a server — a deploy, a scale-in — abruptly killing it fails every in-flight request. Correct sequence:

1. Server receives `SIGTERM`.
2. Server **immediately starts failing readiness** (`/healthz/ready` → 503) while continuing to serve existing requests normally.
3. LB notices within `unhealthy_threshold × interval` and stops sending new requests.
4. Server finishes in-flight requests (the **drain period**, typically 15–30 s).
5. Server closes idle keep-alive connections with `Connection: close`.
6. Process exits.

**The ordering in step 2 and 3 is where most implementations get it wrong.** If the process exits as soon as it gets `SIGTERM`, requests already dispatched by the LB die in flight. The drain period must be **longer than the LB's detection time plus the longest request**:

```
drain_period > (unhealthy_threshold × interval) + timeout + p99_request_duration
             > 17 s + 0.5 s
             ≈ 20-30 s
```

A Kubernetes `terminationGracePeriodSeconds` of 30 against a detection time of 17 s leaves only 13 s for in-flight work — fine for a 500 ms p99, fatal for a 60-second upload.

### Outlier detection

Passive ejection based on observed behaviour rather than a synthetic probe:
- **Consecutive 5xx** — catches hard failures.
- **Consecutive gateway failures** — connection errors and timeouts.
- **Success-rate outlier** — statistical: eject a host whose success rate is more than a configured number of standard deviations below the fleet mean. This catches the *relative* degradation that a fixed threshold misses.

This is the direct countermeasure to the black-hole problem from Lesson 23, because it keys on **errors**, not latency or connection count.

### Failover topologies

| Model | How it works | Failover time | Cost | Wasted capacity |
|---|---|---|---|---|
| **Active-passive** | Standby idle, promoted on failure | Seconds to minutes | 2× | ~50 % |
| **Active-active** | All nodes serve; failed node removed | Sub-second to seconds | N× | None |
| **N+1** | N serving, 1 spare capacity spread across all | Immediate | (N+1)/N | 1/(N+1) |

For a **stateless app tier** (Lesson 20), active-active is strictly better and essentially free — there is nothing to promote. For a **single-writer database**, active-passive is often forced on you, because two simultaneous writers would corrupt data. This asymmetry is why the app tier is easy to scale and the data tier is hard (Phase 2, Topics 15–21).

### Split-brain

When a network partition (Phase 4, Topic 2) separates the primary from the standby, the standby may conclude the primary is dead and promote itself. Now **two nodes believe they are primary**, both accept writes, and the data diverges irreconcilably.

Prevention:
- **Quorum**: only a node that can reach a majority may be primary (Phase 4, Topic 17).
- **Fencing / STONITH**: forcibly disable the old primary — revoke its storage lease or power it off — before promoting.
- **Fencing tokens**: a monotonically increasing number issued with leadership; storage rejects writes bearing a stale token.

Full treatment is **Phase 4, Topic 16 — Leader election**. The point to carry forward now: **automatic failover without fencing is how you lose data.** An automatic failover system that is wrong is more dangerous than a manual one that is slow.

## 5. Request / data flow

BookNook's server B runs out of memory and begins failing. Timeline:

1. **t = 0 s** — B's heap exhausts; request handlers begin throwing. B's process is still alive and still accepting TCP connections.
2. **t = 0–4 s** — Real user requests to B return 500. Roughly 1/3 of users are affected. **Passive detection is already counting.**
3. **t = 2 s** — Active check fires: `GET /healthz/ready`. B returns 503 (its readiness handler detects the allocation failure). Failure count = 1.
4. **t = 4 s** — Passive outlier detection reaches 5 consecutive 5xx. **B is ejected immediately.** Passive beat active here, which is typical under load.
5. **t = 4 s** — LB recomputes its healthy set to {A, C}. Each now takes 50 % instead of 33 %.
6. **t = 4 s** — The LB must verify A and C can absorb the extra load. If the fleet was running at 80 % utilisation, each is now at 120 % → **cascading failure** (see Section 9 Part C).
7. **t = 7 s** — Active check #2 to B fails. Count = 2.
8. **t = 12 s** — Active check #3 fails. Count = 3 = threshold. Active checks now also agree B is unhealthy.
9. **t = 12 s** — **Liveness** probe also fails 3 consecutive times. The orchestrator **kills** B's container.
10. **t = 14 s** — A fresh container starts. The **startup probe** begins; liveness and readiness are suspended.
11. **t = 55 s** — Application finishes booting; connection pools established; startup probe passes.
12. **t = 57 s** — Readiness passes once. (`healthy_threshold: 2` — not yet re-admitted.)
13. **t = 62 s** — Readiness passes twice. **B rejoins the pool.**
14. **t = 62–92 s** — **Slow start**: the LB ramps B's traffic share from 0 % to its full share over 30 s. Without this, B would instantly receive one-third of traffic with cold caches and empty connection pools, spike in latency, fail its checks again, and be ejected — a flap loop.
15. **t = 92 s** — Steady state restored.

Note that total user impact was **4 seconds** (steps 1–4), not 92. The 88 seconds of recovery were invisible to users because the remaining servers absorbed the load. **That is what the redundancy was for**, and it only worked because the failed node was removed.

## 6. ASCII diagram

```
THE THREE PROBES — different questions, OPPOSITE remedies

  ┌──────────────────────────────────────────────────────────────┐
  │ STARTUP   "finished booting?"   fail → WAIT (suppress others) │
  │ LIVENESS  "process wedged?"     fail → KILL AND RESTART       │
  │ READINESS "can serve now?"      fail → REMOVE FROM LB ONLY    │
  └──────────────────────────────────────────────────────────────┘
        ▲
        └─ Conflating LIVENESS with READINESS is the #1 self-inflicted outage:
           a DB blip then RESTARTS the entire fleet, forever, in a loop.


THE DEEP HEALTH CHECK CASCADE  ⚠ the trap

   /healthz queries the database
          │
   DB slows to 3s (NOT down -- just slow)
          │
          ▼
   ┌────┐ ┌────┐ ┌────┐ ┌────┐     every server shares the dependency,
   │ A✗ │ │ B✗ │ │ C✗ │ │ D✗ │  ←  so ALL fail at the SAME INSTANT
   └────┘ └────┘ └────┘ └────┘     (CORRELATED failure -- Lesson 15)
          │
          ▼
   LB ejects 100% of the fleet  →  "503 no healthy upstream"
          │
          ▼
   100% outage ... while the servers could have served the 70% of
   traffic that is cached and never touches the database at all.

   FIX: shallow check + max_ejection_percent: 50 + degrade in the app.


DETECTION vs FLAPPING — the two-sided trade-off

  aggressive            balanced              conservative
  interval 1s           interval 5s           interval 30s
  threshold 1           threshold 3           threshold 3
  ├── detect 2s         ├── detect 17s        ├── detect 92s
  └── FLAPS constantly  └── stable            └── users suffer for 90s
                             ▲
                        start here


GRACEFUL SHUTDOWN — ordering is everything

  SIGTERM
     │
     ├─▶ t+0s   readiness → 503   (BUT keep serving existing requests!)
     │          ░░░ LB has not noticed yet ░░░
     ├─▶ t+17s  LB detection completes → no NEW requests arrive
     │
     ├─▶ t+17s  ──── drain in-flight requests ────
     │
     └─▶ t+30s  close idle connections, exit(0)

  ✗ WRONG: exit immediately on SIGTERM
           ⇒ every request the LB already dispatched dies in flight
  RULE: drain > (threshold × interval) + timeout + p99_request_duration
```

## 7. Detailed example — BookNook

BookNook's health endpoints, after learning the lessons above the hard way.

**Version 1 (the outage).** A single `/health` endpoint, used for both liveness and readiness, that queried PostgreSQL. A routine `VACUUM` on the `orders` table slowed queries to 4 seconds. All five app servers failed their checks simultaneously. Kubernetes killed all five. They restarted, failed again before finishing boot, and entered a `CrashLoopBackOff`. **Total outage: 23 minutes**, caused entirely by the health check. The database never actually went down — it was merely slow, and 70 % of BookNook's traffic (cached book pages) required no database at all.

**Version 2 (correct).** Three separate endpoints:

| Endpoint | Checks | Does NOT check | Used by |
|---|---|---|---|
| `/healthz/startup` | Config loaded, pools created, migrations verified | — | Orchestrator, first 120 s |
| `/healthz/live` | Event loop responsive, heap below 95 % | **Any external dependency** | Orchestrator → restart |
| `/healthz/ready` | Own worker pool has free slots; not shutting down | Database, Redis, payments | LB → routing |

Plus **graceful degradation in the application**: if PostgreSQL is slow, `/books/{isbn}` serves from Redis and returns `stale: true`; `/orders` returns 503 with `Retry-After`. The site stays up in read-only mode. A database problem degrades the 4 % of traffic that writes, rather than destroying 100 %.

**Version 3 (sizing for failover).** The team then realises something subtle: 5 servers at 80 % utilisation cannot survive losing one.

```
Load per server (5 servers, 80% util) = 80%
After losing 1, load spreads over 4   = 80% × 5/4 = 100%
⇒ the remaining 4 are at 100% utilisation
⇒ queueing delay explodes (Lesson 14), they fail their checks, and the
  fleet collapses one server at a time: CASCADING FAILURE.
```

The fix is capacity planning that accounts for failure, developed numerically in Section 9 Part C. **Health checks remove a failed server; they do not create capacity.** If the survivors cannot absorb the load, removing the failure makes things worse, not better.

**Second example for contrast — a stateful PostgreSQL primary.** The app tier's health checking is "eject and forget" because the servers are interchangeable. A database primary is not. Here:
- Failover means **promoting a replica**, which is irreversible and loses any un-replicated writes (the RPO — Phase 2, Topic 16).
- Two primaries would corrupt data, so **fencing is mandatory**.
- Detection must be *slower and more conservative* — a 5-second network blip must not trigger a promotion, because promoting is far more expensive than waiting.
- Typical configuration: 10-second intervals, 3 thresholds, plus a quorum of observers that must agree.

The same mechanism, tuned in the opposite direction: for stateless servers, eject fast and ask questions later; for stateful primaries, be slow and certain. **The cost of a wrong decision determines how much evidence you require** — which is a principle that recurs throughout Phase 4.

## 8. Code or configuration example

> Illustrative only — not production-ready. Real implementations need metrics, structured logging, and careful timeout handling.

### Health endpoints done correctly

```python
from fastapi import FastAPI, Response
import time, threading

app = FastAPI()

_shutting_down = threading.Event()
_started_at = time.monotonic()
_startup_complete = threading.Event()

# ── LIVENESS ────────────────────────────────────────────────────────────────
# "Is this PROCESS wedged?" Failure ⇒ KILL AND RESTART.
# MUST NOT touch any external dependency. If it did, a database blip would
# restart the entire fleet in a loop that cannot recover.
@app.get("/healthz/live")
def live():
    # Reaching this line already proves the event loop is scheduling work --
    # which is exactly and only what liveness is asking.
    return {"status": "alive"}


# ── READINESS ───────────────────────────────────────────────────────────────
# "Can I serve traffic RIGHT NOW?" Failure ⇒ REMOVE FROM LB (do not restart).
# May check things THIS server owns exclusively. Must NOT check shared
# dependencies, or every server fails at the same instant (correlated failure).
@app.get("/healthz/ready")
def ready(response: Response):
    # 1. Draining? Report unready FIRST, but keep serving in-flight requests.
    #    This is what gives the LB time to stop sending new ones.
    if _shutting_down.is_set():
        response.status_code = 503
        return {"status": "draining"}

    # 2. Own worker pool saturated? Shedding here lets the LB route elsewhere
    #    instead of queueing. This IS per-server state, so it is safe to check.
    if worker_pool.available_slots() == 0:
        response.status_code = 503
        return {"status": "saturated"}

    # 3. NOTE WHAT IS ABSENT: no db.execute("SELECT 1"), no redis.ping().
    #    A slow shared dependency must degrade the RESPONSE, not eject the SERVER.
    return {"status": "ready"}


# ── STARTUP ─────────────────────────────────────────────────────────────────
# Exists so a slow boot is not mistaken for a hang. While this fails,
# liveness and readiness are suppressed entirely.
@app.get("/healthz/startup")
def startup(response: Response):
    if not _startup_complete.is_set():
        response.status_code = 503
        return {"status": "starting", "elapsed_s": time.monotonic() - _started_at}
    return {"status": "started"}


# ── GRACEFUL SHUTDOWN ───────────────────────────────────────────────────────
import signal

def handle_sigterm(signum, frame):
    # STEP 1: fail readiness immediately -- but DO NOT exit.
    _shutting_down.set()

    # STEP 2: wait longer than the LB's detection time, so that every request
    # the LB already dispatched has somewhere alive to land.
    #   detection = unhealthy_threshold × interval + timeout = 3×5 + 2 = 17 s
    #   drain     = detection + p99_request_duration + margin
    DRAIN_SECONDS = 25
    time.sleep(DRAIN_SECONDS)

    # STEP 3: now it is safe to stop. Exiting at STEP 1 would have killed
    # every in-flight request -- the single most common deploy-time error.
    worker_pool.shutdown(wait=True)

signal.signal(signal.SIGTERM, handle_sigterm)
```

### Graceful degradation — the alternative to ejecting

```python
@app.get("/books/{isbn}")
def get_book(isbn: str, response: Response):
    try:
        # Short timeout: never let a slow dependency consume a worker slot
        # for longer than the request budget allows (Lesson 17).
        return db.fetch_one("SELECT ... WHERE isbn = $1", isbn, timeout=0.5)

    except (TimeoutError, ConnectionError):
        # The database is unavailable. We DO NOT report ourselves unhealthy --
        # we are perfectly healthy; our dependency is not.
        cached = redis.get(f"book:{isbn}")
        if cached:
            response.headers["X-Degraded"] = "stale-data"
            return {**json.loads(cached), "stale": True}

        # Nothing cached: fail THIS request only. 503 + Retry-After tells
        # well-behaved clients to back off (Lesson 17).
        response.status_code = 503
        response.headers["Retry-After"] = "5"
        return {"error": "temporarily unavailable"}
```

### Kubernetes probes

```yaml
# Illustrative. Note that the three probes are DELIBERATELY different.
containers:
- name: booknook-app
  # STARTUP: suppresses the other two until boot completes.
  # Budget = failureThreshold × periodSeconds = 30 × 5 = 150 s.
  # Without this, a 90-second JVM boot would be killed by liveness at 15 s,
  # forever, producing a crash loop that looks like a crashing app.
  startupProbe:
    httpGet: { path: /healthz/startup, port: 8000 }
    periodSeconds: 5
    failureThreshold: 30

  # LIVENESS: shallow, forgiving, SLOW to act. The remedy is a restart,
  # which is destructive -- so demand a lot of evidence.
  livenessProbe:
    httpGet: { path: /healthz/live, port: 8000 }
    periodSeconds: 10
    timeoutSeconds: 2
    failureThreshold: 3        # 30 s before a restart

  # READINESS: cheap and FAST to act. The remedy is non-destructive
  # (stop sending traffic), so it is safe to be twitchy.
  readinessProbe:
    httpGet: { path: /healthz/ready, port: 8000 }
    periodSeconds: 5
    timeoutSeconds: 2
    failureThreshold: 2        # 10 s to leave rotation
    successThreshold: 2        # HYSTERESIS: 2 successes to return, damping flap

  # Must exceed the drain logic in the application (25 s) or SIGKILL
  # will cut the drain short and kill in-flight requests anyway.
  terminationGracePeriodSeconds: 40
```

### Envoy — active and passive together

```yaml
clusters:
- name: booknook_app
  lb_policy: LEAST_REQUEST

  # ACTIVE: synthetic probes. Detect failure even with no user traffic.
  health_checks:
  - timeout: 2s
    interval: 5s
    unhealthy_threshold: 3        # worst case detection = 3×5 + 2 = 17 s
    healthy_threshold: 2          # hysteresis on the way back in
    http_health_check:
      path: /healthz/ready

  # PASSIVE: watch REAL traffic. Catches the case where /healthz returns 200
  # but real endpoints are failing -- which active checks can never see.
  outlier_detection:
    consecutive_5xx: 5
    interval: 10s
    base_ejection_time: 30s       # DOUBLES per repeat ejection → damps flapping
    max_ejection_percent: 50      # ⚠ THE MOST IMPORTANT LINE HERE.
                                  # Guarantees half the fleet stays in rotation
                                  # no matter what. Converts a potential total
                                  # outage into partial degradation.

  # SLOW START: ramp a rejoining server's share from 0 to full over 30 s.
  # Without this, a cold server gets its full share instantly, spikes in
  # latency, fails its checks, and is ejected again -- a flap loop.
  common_lb_config:
    healthy_panic_threshold: { value: 50 }   # if <50% healthy, send to ALL
                                             # hosts anyway: degraded service
                                             # beats "no healthy upstream"
```

Note `healthy_panic_threshold`. Envoy's reasoning: if more than half the fleet looks unhealthy, the *health checking* is more likely to be wrong than the entire fleet. So it ignores health status and load-balances across everything. Serving 50 % errors is better than serving 100 % errors — a deliberate choice to fail toward availability.

## 9. Numerical example

### Part A — Detection time and its user cost

**Assumptions:** 4 app servers; 500 RPS total; `interval: 5s`, `timeout: 2s`, `unhealthy_threshold: 3`; one server fails completely.

```
Worst-case detection = (unhealthy_threshold × interval) + timeout
                     = (3 × 5) + 2 = 17 seconds

Traffic to the dead server = 500 / 4 = 125 RPS
Failed requests            = 125 × 17 = 2,125 requests
```

Now evaluate tightening the configuration:

| Config | Detection | Failed requests | Flap risk |
|---|---|---|---|
| `interval 1s, threshold 1` | 3 s | 375 | **High** — one GC pause ejects a healthy server |
| `interval 2s, threshold 2` | 6 s | 750 | Moderate |
| `interval 5s, threshold 3` | 17 s | 2,125 | Low ← balanced default |
| `interval 10s, threshold 3` | 32 s | 4,000 | Very low |
| `interval 30s, threshold 3` | 92 s | 11,500 | Negligible |

**Adding passive detection changes the picture entirely:**

```
consecutive_5xx: 5, at 125 RPS to the failed server
Time to accumulate 5 failures = 5 / 125 = 0.04 seconds
Evaluation interval           = 10 s (worst case)
Realistic detection           ≈ 0.04-10 s, typically ~1-2 s

Failed requests ≈ 125 × 2 = 250  (vs 2,125 with active checks alone)
```

**Passive detection is ~8× faster under load** because real traffic provides evidence far more densely than a 5-second probe. This is why you want both: passive for speed when traffic exists, active for coverage when it does not.

### Part B — The deep health check cascade, quantified

**Assumptions:** 5 servers; 70 % of traffic is cache-served and needs no database; the database slows to 4 s; health check timeout 2 s; 500 RPS.

**With a deep health check:**
```
All 5 health checks time out simultaneously (correlated failure).
All 5 servers ejected.
Healthy backends = 0  →  "503 no healthy upstream"
Users served = 0%
Failed requests = 500 RPS × duration of the DB problem
For a 10-minute DB slowdown: 500 × 600 = 300,000 failed requests
```

**With a shallow health check plus application degradation:**
```
All 5 servers remain healthy (nothing external is checked).
Cache-served traffic (70%)  → succeeds normally
DB-dependent traffic (30%)  → 503 with Retry-After, or stale data

Users served = 70% fully + some portion of the 30% from stale cache
Failed requests = 500 × 0.30 × 600 = 90,000
```

```
Improvement = 300,000 → 90,000 = 3.3× fewer failures
Availability during the incident: 0% → 70%
```

**The health check design was worth more than the entire redundancy strategy.** Five servers provided zero benefit in the first case.

### Part C — Capacity planning for failover (the cascading failure)

This is the calculation most teams skip.

**Assumptions:** N servers; each saturates at 100 % utilisation; steady-state utilisation U; one server fails.

```
Load redistributes over (N − 1) servers:
  U_after = U × N / (N − 1)

Safe operation requires U_after < some threshold, say 85%
(above ~85%, queueing delay grows sharply — Lesson 14).
```

| N | U before | U after losing 1 | Survives? |
|---|---|---|---|
| 2 | 80 % | 160 % | **No — total collapse** |
| 3 | 80 % | 120 % | **No** |
| 4 | 80 % | 107 % | **No** |
| 5 | 80 % | 100 % | **No — exactly at the cliff** |
| 5 | 60 % | 75 % | Yes |
| 10 | 80 % | 89 % | Marginal |
| 10 | 70 % | 78 % | Yes |
| 20 | 80 % | 84 % | Yes |

**Solving for the maximum safe steady-state utilisation**, given a target of 85 % after one failure:

```
U_max = 0.85 × (N − 1) / N

N = 3  : U_max = 0.85 × 2/3  = 56.7%
N = 5  : U_max = 0.85 × 4/5  = 68.0%
N = 10 : U_max = 0.85 × 9/10 = 76.5%
N = 20 : U_max = 0.85 × 19/20= 80.8%
```

**Two conclusions:**
1. **Small fleets must run at low utilisation.** With 3 servers you cannot exceed ~57 % without risking cascade. That is the real, hidden cost of a small fleet.
2. **Larger fleets are more efficient**, because the failure of one is a smaller fraction. This is an argument for many small instances over few large ones — the opposite of what cost-per-instance reasoning suggests.

**Surviving two simultaneous failures** (N+2, appropriate if servers span 2 AZs and you can lose an AZ):
```
U_max = 0.85 × (N − 2) / N
N = 5  : 0.85 × 3/5 = 51%
N = 10 : 0.85 × 8/10 = 68%
```

### Part D — LB health-check failover versus DNS failover

Comparing against Lesson 4's DNS numbers:

```
LOAD BALANCER FAILOVER
  Detection (passive)       ≈  2 s
  Removal from pool         ≈  0 s (immediate)
  Client impact             =  2 s, for ALL clients uniformly
  Total MTTR                ≈  2 seconds

DNS FAILOVER (Lesson 4)
  Health-check detection    = 30 s (3 × 10 s)
  TTL expiry (60 s TTL)     = 0-60 s, mean 30 s
  Misbehaving clients (15%) = up to 300 s
  Total MTTR                ≈ 60 s mean, 330 s worst case
```

```
Ratio = 60 s / 2 s = 30× faster for the LB, in the mean case.
```

**Availability impact of one failure per month** (from Lesson 15's formula):
```
Monthly seconds = 30 × 86,400 = 2,592,000

LB failover:  1 − (2 / 2,592,000)     = 99.99992%   ← comfortably inside 99.99%
DNS failover: 1 − (100.5 / 2,592,000) = 99.9961%    ← only just inside 99.99%
```

**Conclusion, stated the way you should state it in an interview:** use load-balancer health checks for *instance*-level failure (fast, uniform, no caching involved) and DNS for *region*-level failure (the only mechanism that works when the entire load balancer is unreachable). They operate at different timescales because they solve different problems.

## 10. Trade-offs

**Active health checks**
- *Advantages:* detect failure with zero traffic; detect before users are harmed; uniform and predictable.
- *Disadvantages:* consume resources; a synthetic probe can pass while real endpoints fail; add a configuration surface that is easy to get wrong.
- *Operational cost:* at 50 servers × 1 check/5 s × 3 LBs = 30 checks/second, forever. Negligible for a shallow check, significant if the check hits the database.

**Passive / outlier detection**
- *Advantages:* free; measures exactly what users experience; much faster under load (Part A).
- *Disadvantages:* requires users to be harmed first; useless at low traffic; can over-eject during a shared-dependency incident.
- *Operational cost:* near zero.

**Aggressive thresholds**
- *Advantages:* fewer failed requests per incident.
- *Disadvantages:* flapping; healthy servers ejected during GC pauses; oscillation that can itself cause an outage.
- *Becomes unnecessary complexity when:* you already have passive detection, which gets you ~2-second detection without the flap risk. Tightening the active check on top of that buys little.

**Automatic failover for stateful systems**
- *Advantages:* recovery without waking a human; much lower MTTR.
- *Disadvantages:* **split-brain and data loss** if fencing is wrong; a false positive is far more costly than for stateless servers.
- *Becomes unnecessary complexity when:* writes are rare and your RTO is measured in minutes. A documented, practised manual promotion is safer than an automatic one you have never tested. Many experienced teams deliberately choose manual database failover for exactly this reason.

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| Deep health check + slow dependency | **100 % outage** — "no healthy upstream" — despite healthy servers | Healthy-backend count drops to zero fleet-wide, simultaneously | Shallow the check; `max_ejection_percent: 50`; degrade in the app |
| Liveness probe checks the database | Entire fleet restarts in a loop; cannot recover even after the DB returns | `CrashLoopBackOff`; restart counts climbing on every pod | Remove dependency checks from liveness; separate from readiness |
| No startup probe on a slow-booting app | App never starts; looks like a crashing application | Restart count climbing with no application error logs | Add a startup probe with a generous `failureThreshold` |
| Server exits immediately on SIGTERM | Errors on **every deploy**, proportional to traffic | Error-rate spikes correlated with deploy timestamps | Fail readiness first, then drain longer than LB detection time |
| Drain period shorter than detection time | A trickle of errors per deploy, often dismissed as noise | 502/504 spikes at deploy time | `drain > threshold × interval + timeout + p99` |
| Flapping under GC pressure | Erratic latency; capacity oscillates | Backend state transitions in the LB log | Raise `unhealthy_threshold`; add `successThreshold`; exponential ejection |
| Fleet too small for N+1 | Losing one server collapses the rest, one at a time | Utilisation > 100 % on survivors; sequential failures seconds apart | Capacity planning per Part C; `U ≤ 0.85 × (N−1)/N` |
| No slow start on rejoin | A rejoining server immediately spikes and is ejected again | Repeated ejection of the same host after each restart | Enable slow start / gradual ramp |
| `max_ejection_percent: 100` | Partial degradation becomes total outage | Zero healthy backends during a dependency incident | Cap at 50 %; enable panic threshold |
| Automatic DB failover without fencing | **Silent data divergence**; two primaries accepting writes | Often discovered days later via inconsistent reports | Quorum + fencing tokens (Phase 4, Topic 16); manual failover until proven |
| Health check returns 200 while endpoints 500 | Users see errors; dashboards show a fully healthy fleet | Only passive detection catches this | Add outlier detection on real traffic |

## 12. Alternatives

| Approach | Mechanism | Detection speed | Risk |
|---|---|---|---|
| **Active HTTP checks** | Synthetic `GET /healthz` | 10–30 s | Deep-check cascade |
| **Passive outlier detection** | Count real failures | 1–2 s under load | Over-ejection during shared-dependency failure |
| **TCP connect check** | Can we open a socket? | Fast | Very weak — a wedged process still accepts connections |
| **Exec / script probe** | Run a command in the container | Slow (process spawn) | Expensive; used when there is no HTTP surface |
| **gRPC health protocol** | Standard `grpc.health.v1` service | Fast | Only for gRPC services (Lesson 11) |
| **Heartbeat / gossip** | Nodes announce liveness to each other | 1–5 s | Scales to large fleets; more complex (Phase 4) |
| **Lease-based** | Node must renew a lease to stay in rotation | Bounded by lease TTL | **Fails safe** — a partitioned node loses its lease automatically |
| **Synthetic user journeys** | Run a full checkout every minute from outside | 60 s | Slow, but catches what no internal check can |
| **Client-side error tracking** | Real user monitoring | Seconds | The only signal that reflects actual user experience |

Lease-based checking deserves a note: it inverts the direction of trust. Rather than a central checker deciding a node is dead, the node must *continuously prove* it is alive. A node that cannot reach the coordinator removes *itself*, which is exactly the property you want during a network partition — it fails safe rather than fails open. This underpins leader election (Phase 4, Topic 16).

## 13. When to use it

1. **Every load-balanced tier, without exception.** As Section 2 showed, redundancy without health checking provides no availability benefit and actively increases the chance a user hits a broken server.
2. **Every deploy.** Readiness plus connection draining is what makes rolling deploys invisible. Without it, every release costs you a measurable error spike.
3. **Autoscaling.** A new instance must not receive traffic until it is warm, and must drain before termination. Health checks are what make Lesson 39's autoscaling safe.
4. **Detecting partial degradation.** Passive outlier detection catches the server that is up, passing `/healthz`, and failing real requests — a case active checks structurally cannot see.
5. **Stateful failover**, but with deliberately conservative tuning and mandatory fencing (Section 7's second example).

## 14. When *not* to use it

1. **Do not put shared dependencies in health checks.** This is the central warning of the lesson. Check the server, not the world; degrade in the application instead.
2. **Do not use liveness probes for anything a restart cannot fix.** If the problem is an external dependency, restarting makes it strictly worse. When in doubt, omit the liveness probe entirely — a missing liveness probe is far safer than a wrong one.
3. **Do not configure automatic failover for a stateful primary without fencing**, and preferably not without having rehearsed it. An automatic failover you have never tested will fire for the first time during an incident.
4. **Do not tighten thresholds aggressively on a single server.** With no redundancy there is nowhere to fail over to; ejecting the only server converts a slow site into a down site.

## 15. Common mistakes

**Beginner mistakes**
- One `/health` endpoint used for both liveness and readiness, so a dependency blip restarts the fleet.
- Health checks that query the database — the cascade in Part B.
- No readiness probe, so traffic arrives before the app has finished booting; every deploy produces a burst of errors.
- Exiting immediately on `SIGTERM`, killing in-flight requests on every deploy.
- Assuming a TCP connect check is sufficient (a deadlocked process still accepts connections).
- Setting `unhealthy_threshold: 1` for faster detection and causing constant flapping.
- Believing health checks create capacity — they only remove servers, which *increases* load on the rest.

**Experienced-engineer mistakes**
- `max_ejection_percent: 100`, turning partial degradation into a total outage.
- Drain period shorter than LB detection time, producing a persistent low-level error rate at deploy time that gets normalised as "deploy noise".
- No slow start, so every restarting server flaps on rejoin with cold caches.
- Running a fleet at 80 % utilisation with N=4 and no failover headroom (Part C).
- Automatic database failover without fencing — the split-brain data-loss scenario.
- Relying solely on active checks and missing the "200 on `/healthz`, 500 on everything else" case.
- Never testing failover. An untested failover path has roughly a 50 % chance of working when it is finally needed.
- Forgetting that the orchestrator's probes and the load balancer's probes are **separate systems** with separate configuration that can disagree — a pod can be `Ready` to Kubernetes and ejected by the LB simultaneously.

## 16. Interview perspective

This topic distinguishes candidates who have operated systems from those who have only designed them.

**How to use it:**

1. **When you draw a load balancer, state the health check design unprompted.** *"Shallow readiness check — no database — because a deep check would eject the whole fleet when the database slows."* This single sentence is one of the highest-signal things you can say in a system-design interview.
2. **Separate liveness from readiness explicitly.** *"Liveness failure means restart, readiness failure means stop routing. A dependency check belongs in neither — it belongs in the application's degradation path."*
3. **Do the N+1 capacity calculation out loud.** *"With 5 servers at 80 %, losing one puts the rest at 100 % and they cascade. I'd size for 68 % steady state so one failure lands the survivors at 85 %."* Almost nobody does this, and it is exactly the arithmetic that separates a design that works on a whiteboard from one that works at 3 a.m.
4. **Mention graceful shutdown when asked about deploys.** *"Fail readiness, wait longer than the LB's detection window, then drain. Otherwise every rolling deploy costs an error spike."*
5. **For stateful failover, raise split-brain before being asked.** *"Automatic promotion needs fencing, or a partition gives you two primaries and divergent data. I'd use a quorum and fencing tokens."*
6. **Know the MTTR comparison.** *"LB health checks for instance failure at ~2 seconds; DNS for region failure at 60–300 seconds. Different timescales, different problems."*

**Red flags interviewers watch for:**
- Drawing redundant servers with no mention of how failure is detected.
- A health check that queries the database, with no awareness of the cascade.
- Using the same endpoint for liveness and readiness.
- Claiming N servers give `1 − (1−a)^N` availability without noting that this *requires* failed nodes to be removed.
- No failover capacity headroom — sizing the fleet for steady state only.
- Proposing automatic database failover with no mention of split-brain or fencing.
- Not knowing what happens to in-flight requests during a deploy.

## 17. Knowledge check

Write your answers down before continuing.

**Conceptual**

1. Explain precisely why a liveness probe must never check an external dependency, while a readiness probe *may* check some things. Then describe the specific failure mode that occurs when a liveness probe checks the database and the database becomes slow — including why the system cannot recover on its own even after the database returns to normal.

2. BookNook has 5 app servers, all healthy, all passing a shallow `/healthz/ready`. Users report that roughly 20 % of requests return 500. The dashboard shows 5/5 backends healthy and normal CPU. Explain what class of failure this is, why active health checking is structurally incapable of detecting it, which mechanism *would* detect it, and what signal that mechanism keys on.

3. A colleague proposes `interval: 1s, unhealthy_threshold: 1` to cut detection time from 17 seconds to 2 seconds. Give two specific, concrete ways this can cause an outage that the slower configuration would not, and describe an alternative that achieves ~2-second detection *without* those risks.

**Practical scenario**

4. BookNook runs 6 app servers behind an LB with `interval: 5s`, `timeout: 2s`, `unhealthy_threshold: 3`, and `terminationGracePeriodSeconds: 20`. The application calls `sys.exit(0)` immediately upon receiving `SIGTERM`. p99 request duration is 1.5 seconds; a CSV export endpoint can take 45 seconds. Deploys are rolling, one server at a time. The team reports a consistent spike of 502 errors during every deploy, plus occasional complaints that report downloads "just stop halfway".
   Explain the root cause of each of the two symptoms separately, compute the window during which requests are being lost, state the exact changes required (including specific numeric values and your reasoning for each), and explain why the CSV problem needs a different fix from the 502 problem.

**Calculation**

5. BookNook's API tier runs 8 servers. Each server saturates at 100 % utilisation, and queueing delay becomes unacceptable above 85 % utilisation. Steady-state traffic is 2,400 RPS and each server can handle 400 RPS at 100 % utilisation.
   (a) What is the current steady-state utilisation per server? Is it safe under the 85 % rule?
   (b) Compute the utilisation of the survivors after losing 1 server, then after losing 2 servers. Which scenarios breach the 85 % threshold?
   (c) Using `U_max = 0.85 × (N − k) / N`, determine the minimum number of servers required to safely survive losing 2 servers (k = 2) while serving 2,400 RPS, assuming each handles 400 RPS at full utilisation. Show your working.
   (d) The team instead proposes keeping 8 servers and adding passive outlier detection with `consecutive_5xx: 5` and `max_ejection_percent: 50`. Explain quantitatively whether this solves the problem in part (b), and if not, why not.
   Show every step and state any additional assumptions you make.

Reply with your answers and I'll assess them, correct anything shaky, and give model answers before Lesson 25.

---

**Progress checklist**

Phase 1 — Foundations: **complete** (Lessons 1–17)

Phase 2 — Scaling:
`[x] 1. Vertical scaling` · `[x] 2. Horizontal scaling` · `[x] 3. Stateless application servers` · `[x] 4. Load balancers` · `[x] 5. Layer 4 versus Layer 7` · `[x] 6. Load-balancing strategies` · `[x] 7. Health checks and failover`
`[ ] 8. Reverse proxies and API gateways` ← next
`[ ] 9–22` not started

Phases 3–5: not started.

**NEXT LESSON: Phase 2, Topic 8 — Reverse proxies and API gateways**
# Lesson 25 — Reverse Proxies and API Gateways

*Phase 2, Topic 8*

## 1. Simple definition

A **proxy** is a server that sits in the middle of a connection and speaks on behalf of one of the two ends.

- A **forward proxy** acts on behalf of the **client**. The client knows the proxy is there and deliberately sends its traffic through it. The origin server does not know who the real client is.
- A **reverse proxy** acts on behalf of the **server**. The client does *not* know the proxy is there — it believes it is talking to the application. The real application servers are hidden behind it.

> One sentence to remember: **a forward proxy hides the client from the internet; a reverse proxy hides the servers from the internet.**

An **API gateway** is a reverse proxy that additionally understands and enforces *application-level policy*: who you are, how many requests you are allowed, which version of which service should answer, and what the response should look like.

Terms defined before use:
- **Origin / upstream / backend**: the real application server that eventually produces the response. Same meaning, different vendors.
- **Edge**: the first component of *your* infrastructure that a client's packet touches.
- **Hop**: one proxy-to-proxy or proxy-to-server network traversal. Each hop costs latency and is a place that can fail.
- **Policy**: a rule applied to a request that is *not* business logic — authentication, rate limiting, header rewriting, routing.
- **Cross-cutting concern**: something every service needs identically (TLS, logging, auth). The core reason both components exist.
- **Data plane**: the component that actually carries request bytes. **Control plane**: the component that configures the data plane. A gateway is a data plane; the thing you configure it with is a control plane.

Lesson 21 introduced the load balancer, and Lesson 22 showed that an L7 load balancer already parses HTTP. A reverse proxy *is* that L7 component, viewed from the angle of "what else can it do once it has parsed the request?" The answer turns out to be: a great deal.

## 2. Why it exists

Imagine BookNook with no reverse proxy. Each of the three application servers is exposed directly to the internet. Every single one of them must now independently implement:

| Concern | What each app server must do alone |
|---|---|
| TLS | Hold a copy of the private key, terminate TLS, renew certificates, track cipher policy (Lesson 7) |
| Static files | Serve book-cover images from its own disk, competing with request handling for CPU and memory |
| Compression | gzip/brotli every response in application code |
| Slow clients | Hold a worker thread open for 1.6 seconds while a phone on 3G drains a 200 KB response |
| Security | Filter malformed requests, oversized bodies, header-smuggling attempts |
| Rate limiting | Maintain its own counters — which are wrong, because each server only sees 1/3 of a client's traffic |
| Logging | Produce its own, differently-formatted, access logs |

Seven cross-cutting concerns, implemented three times, drifting apart over time. Add a second service written in a different language and you implement them again, differently.

This is the first reason: **a reverse proxy exists to implement cross-cutting concerns exactly once, in one place, in a component purpose-built for them.**

The second reason is more specific and more dramatic. Recall from Lesson 1 that BookNook's **book-cover images consume roughly 40× the bandwidth of the application itself**. If every image byte flows through Python or Java application workers, you are spending expensive, stateful, memory-hungry application capacity on a task that is essentially `read a file, write it to a socket`. nginx does that at roughly 10,000× the efficiency per core, because it was written for exactly that and nothing else.

The third reason is **slow clients**, and it is the one most engineers underestimate. Section 9 quantifies it, but the headline: a thread-per-request backend serving mobile users without a buffering proxy can lose **over 97 % of its worker capacity to network transmission time** rather than to work.

The fourth reason is **decoupling**. With a reverse proxy, the public URL space and the internal service topology are no longer the same thing. You can split a service in two, rename it, move it to another host, or run two versions side by side, and clients never notice. Without the proxy, your service boundaries are baked into every client, including mobile apps you cannot force-update.

The API gateway exists for a narrower reason. Once you have more than one backend service, somebody must decide: *which* service answers `/orders`, is this caller allowed to call it, how many calls per minute may they make, and what do we do when a mobile client needs data from four services in one screen? That is gateway work, and it is the subject of the second half of this lesson.

## 3. Real-world analogy

**The reception desk of a large office building.**

- Visitors arrive at **one** front door. They do not know, and do not need to know, that Legal is on floor 3 and Accounts is in an annexe across the road. That is **reverse proxying**.
- Reception checks ID and issues a badge once — not separately on every floor. That is **TLS termination and authentication**.
- Reception keeps a stack of the standard brochure behind the desk, so a visitor asking for it never has to be sent upstairs. That is **caching and static file serving**.
- Reception signs for a courier's 40 boxes and lets the courier leave immediately, then moves them upstairs at its own pace. That is **buffering slow clients**, and it is the single most valuable thing in this analogy.
- Reception refuses someone with no appointment, and tells anyone who shows up twenty times in an hour to come back tomorrow. That is **security filtering and rate limiting**.
- A **forward proxy** is the opposite arrangement: a company travel desk that books every employee's taxi, so the taxi firm only ever sees the travel desk, never the employees.

**Where the analogy breaks down:**

| Reception desk | Reverse proxy / API gateway |
|---|---|
| A receptionist handles a handful of visitors a minute | A proxy handles tens of thousands of requests per second per core; the engineering constraints are completely different at that rate |
| Signing for 40 boxes costs reception almost nothing | Buffering costs **memory**, bounded and finite. A proxy buffering 10,000 slow responses of 256 KB needs 2.5 GB of RAM, and will start spilling to disk or rejecting connections |
| Reception is one person; if they are ill, someone covers | A proxy is on the critical path of **100 %** of traffic. It must itself be redundant (Lesson 24), or it is simply a new single point of failure |
| Reception does not open your parcels | A TLS-terminating proxy decrypts **everything**. It is the highest-value target in your infrastructure and the place where a compromise is most catastrophic |
| Reception never decides what your meeting is about | A gateway that starts making business decisions ("if the order total exceeds £100, call the fraud service") has quietly become an application — the distributed-monolith anti-pattern in Section 4 |
| A visitor waits in the lobby for one minute | Every hop adds latency to **every** request, forever; a 1 ms proxy at 500 M requests/month is 139 hours of aggregate user waiting |

The last two rows are the ones that separate a working gateway design from a failing one, and both are returned to below.

## 4. Detailed technical explanation

### 4.1 Forward proxy versus reverse proxy, precisely

```
FORWARD PROXY — configured by the client, acts for the client
  [employee laptop] ──▶ [corporate proxy] ──▶ [any site on the internet]
   knows about proxy      sees the employee     sees only the proxy's IP

REVERSE PROXY — deployed by the operator, acts for the server
  [any user] ──▶ [booknook.com proxy] ──▶ [app servers 10.0.2.x]
   thinks it is             knows the real      never directly reachable
   talking to the app       client IP           from the internet
```

Two things follow immediately from this diagram.

First, **the client IP is lost** at a reverse proxy, because the backend sees a connection from the proxy's internal address. This is why `X-Forwarded-For` (Lesson 5) exists and why rate limiting, geolocation, and abuse detection break subtly if you forget it. It is also why you must only trust `X-Forwarded-For` from proxies you control — a client can forge the header, and a backend that blindly trusts it has an IP-spoofing vulnerability.

Second, **the backends are not internet-routable**. They sit on a private network. This removes an enormous class of attacks before any filtering logic is written: an attacker cannot reach the application servers at all.

### 4.2 What a reverse proxy adds

**TLS termination.** The proxy performs the TLS handshake (Lesson 7), decrypts, and forwards plaintext (or re-encrypts) to the backend. Benefits: one certificate to renew instead of N; handshake CPU concentrated on machines tuned for it; session resumption caches shared across all traffic; TLS version and cipher policy changed in one place. Cost: the private key lives on the proxy, and the proxy-to-backend hop is plaintext unless you deliberately re-encrypt. For regulated workloads you terminate TLS at the edge *and* open a second TLS session to the backend — "TLS re-origination", at roughly double the handshake CPU.

**Static file serving.** `/static/covers/9780141036144.jpg` never reaches the application. nginx serves it with `sendfile()`, a kernel call that copies file data straight from page cache to socket without it ever entering user space. A Python application doing the same thing reads the file into the interpreter's heap, copies it into a response object, and writes it out — several times the memory traffic and a worker occupied throughout. Given BookNook's 40:1 image-to-application bandwidth ratio from Lesson 1, this one feature removes roughly 97.5 % of the bytes the application tier would otherwise have touched.

**Caching.** The proxy stores responses and serves repeats without consulting the backend at all. This is a reverse proxy cache, and it is the first cache in the chain we will study properly in Lesson 26 and in **Phase 2, Topic 10 — Browser, application, database, and distributed caches**.

**Compression.** gzip or brotli applied at the edge, chosen from the client's `Accept-Encoding`. A 180 KB JSON search response compresses to roughly 18 KB — a 10× bandwidth reduction that the application never has to think about. Important subtlety: compression is CPU work, so compressing already-compressed content (JPEG, PNG, video) is pure waste, and compressing tiny responses costs more than it saves. Typical policy: compress text types only, above ~1 KB.

**Request and response buffering.** The proxy reads the *entire* request from the client before forwarding it, and reads the *entire* response from the backend before streaming it to the client. The backend therefore interacts only with a fast peer on a LAN. This is the worker-occupancy argument, quantified in Section 9 Part A.

**Request/response rewriting.** Path rewriting (`/api/v2/books` → `/books` on the books service), header injection (`X-Request-ID` for tracing, Lesson 5), header stripping (removing `Server:` version banners), and response header policy (HSTS, CSP, CORS) applied uniformly.

**Security filtering.** Request size limits, header count and size limits, malformed-request rejection, slow-request timeouts, IP allow/deny lists, and optionally a **web application firewall (WAF)** that inspects payloads for injection patterns. Rejecting a 2 GB upload at the edge costs the proxy almost nothing; letting it reach a backend costs a worker and possibly the heap.

**Connection multiplexing.** Thousands of client connections are consolidated onto a small pool of keep-alive connections to each backend. This matters more than it sounds: without pooling, each request may open a new TCP connection, and a busy proxy can exhaust the 28,232 usable ephemeral ports toward a single backend IP:port pair (Lesson 3).

**Protocol bridging.** The proxy speaks HTTP/2 or HTTP/3 to clients (Lesson 8) while speaking plain HTTP/1.1 to backends that never had to learn either. Clients get multiplexing and header compression; the backend stays simple.

### 4.3 Why buffering slow clients matters enormously

This deserves its own subsection because it is the least intuitive benefit and often the largest.

There are two broad server concurrency models:

- **Thread-per-request / process-per-request** (classic Java servlet containers, Rails with Puma, Django with gunicorn sync workers, PHP-FPM). Each in-flight request owns an OS thread or process with a dedicated stack and, in practice, tens of megabytes of memory. Concurrency is bounded by a **worker pool** of fixed size — typically 10–500 per machine.
- **Event-driven / async** (nginx, Envoy, Node.js, Go, Netty). Each in-flight request owns a small state object, typically a few kilobytes. Concurrency is bounded by memory and file descriptors — typically tens or hundreds of thousands per machine.

Now apply Little's Law from Lesson 14: `L = λ × W`, where `L` is concurrent requests in the system, `λ` the arrival rate, and `W` the time each request spends in the system.

For a thread-per-request backend, `L` cannot exceed the worker pool size. So throughput is `λ_max = pool_size / W`. **Anything that increases `W` reduces throughput proportionally** — and `W` includes the time spent writing bytes to a slow client, because the worker is not released until the write completes.

A 200 KB response to a mobile client on a congested 1 Mbit/s link takes about 1.6 seconds to transmit. If the application spent 40 ms generating it, the worker is occupied for 1,640 ms of network time and 40 ms of work: **97.6 % waste**. Section 9 computes the full consequence — a 40× throughput difference.

A buffering reverse proxy breaks this coupling. The proxy, which is event-driven and can hold 100,000 concurrent slow connections cheaply, absorbs the response over a 1–10 Gbit/s LAN in a couple of milliseconds, releases the worker immediately, and then dribbles the bytes out to the phone at whatever pace the phone can manage.

The same argument applies in reverse for **request** buffering, and here it is also a security property. **Slowloris** is an attack in which a client opens many connections and sends request headers one byte every few seconds, never completing the request. Against a thread-per-request server with a 200-worker pool, 200 such connections consume the entire server using almost no attacker bandwidth. A buffering proxy with a header timeout absorbs this trivially — it can hold 200,000 half-open connections and times each one out.

**Important caveat, because this is where simplified teaching models diverge from production reality:** buffering is exactly wrong for *streaming* responses. Server-sent events, long-polling, gRPC streaming, chunked log tailing, and large file downloads must have buffering disabled (`proxy_buffering off` in nginx), or the proxy will hold the whole response and the stream will not stream. Buffering is the right default and the wrong special case; both must be configured deliberately.

### 4.4 API gateway responsibilities

A gateway is a reverse proxy plus policy. The canonical responsibilities:

**Authentication and authorisation.** The gateway validates the credential — typically a JWT signature or an opaque token looked up in a token store — once, at the edge, and passes a trusted, verified identity to backends as a header. Backends then do not each need to implement signature validation, key rotation, or clock-skew handling. The gateway answers *"who is this?"*; fine-grained authorisation (*"may this user edit this specific order?"*) stays in the service, because only the service knows the data model.

**Rate limiting and quotas.** A **rate limit** is a short-window cap ("100 requests per minute per API key") protecting backends from overload. A **quota** is a long-window cap ("1,000,000 requests per month on the Bronze plan") enforcing a commercial agreement. They look the same and are not: exceeding a rate limit should return `429 Too Many Requests` with a `Retry-After` header and the client should back off (Lesson 17); exceeding a quota is a billing event. Both belong at the gateway because only the gateway sees all of a client's traffic — a per-server counter with 3 servers under-counts by 3× (Lesson 20's statelessness argument applied to counters).

**Routing.** Mapping a public path, host, header, or method to an internal service. `/orders/*` → order-service, `/books/*` → catalogue-service, `Host: admin.booknook.com` → admin-service.

**Response aggregation.** One client call fans out to several backends and the gateway merges the results. A mobile book page needs title and price (catalogue), stock (inventory), and reviews (reviews service). Three round trips from a phone on a 120 ms RTT link cost 360 ms; one gateway call with parallel internal fan-out over a 1 ms LAN costs about 125 ms. This is genuinely valuable and also the most dangerous gateway feature — see 4.5.

**Protocol translation.** External REST/JSON over HTTP/1.1 translated to internal gRPC over HTTP/2 (Lesson 11), or `grpc-web` from a browser translated to real gRPC. The gateway lets you use an efficient internal protocol without forcing every client to support it.

**Versioning.** Running `/v1` and `/v2` side by side, routing by path or header, and shifting traffic gradually. This is how you deprecate an API that mobile clients you cannot update still depend on.

**Observability.** One consistent access log, one set of latency histograms, one trace-ID injection point. Because every request passes through, the gateway is the only place with a complete, uniform picture of external traffic. In practice this is often the *first* reason teams adopt one.

**Request shaping.** Timeouts, retries with backoff and jitter, circuit breaking, and request hedging — all the Lesson 17 reliability patterns, applied centrally so that a careless client library cannot retry-storm your backends.

### 4.5 The "gateway becomes a distributed monolith" anti-pattern

This is the most important failure mode in the lesson, and it is organisational as much as technical.

The gateway starts as routing and auth. Then:

1. A team needs one field transformed for an old mobile client. "Just do it in the gateway, it's a two-line change."
2. Another team needs responses aggregated. Aggregation requires knowing which fields to merge and how to resolve conflicts — that is domain logic, now in the gateway.
3. Someone adds "if the order total exceeds £100, also call the fraud service". That is a business rule.
4. Someone adds a cache keyed on a field whose meaning only the catalogue team understands.

After two years, the gateway contains business logic owned by nobody, understood by few, with a configuration file touched by every team. The symptoms are diagnostic:

- **Every feature requires a gateway change**, so the gateway team becomes a release bottleneck for the whole organisation.
- **A gateway deploy can break any service**, so deploys become rare, large, and risky — the exact dynamic microservices were adopted to escape.
- **Services cannot be tested in isolation**, because their real contract lives in gateway transformation rules.
- **Nobody can answer "what does this endpoint do?"** without reading both the service and the gateway config.

You have a distributed system with a monolith's coupling and a monolith's release cadence, plus network calls between the pieces. That is strictly worse than the monolith you started with.

The rule that prevents it: **the gateway may make decisions using only information that is in the request itself or in the gateway's own policy store — never using domain data.** Routing on a path, validating a JWT signature, counting requests per key, stripping a header: all fine, all domain-agnostic. Looking up an order to decide where to route it: not fine. If a change requires understanding the business, it belongs in a service.

A practical secondary rule: aggregation logic that is specific to one client should live in a **backend-for-frontend** owned by that client's team, not in the shared gateway. That is the subject of the next subsection.

### 4.6 Gateway, service mesh, and backend-for-frontend

These three are routinely confused because all three are proxies.

- An **API gateway** sits at the **edge**, on the **north–south** path (traffic entering your system from outside). It is shared by all external clients.
- A **service mesh** sits **between your own services**, on the **east–west** path (internal service-to-service traffic). It is implemented as a **sidecar** proxy — one proxy process deployed alongside each service instance, intercepting that instance's inbound and outbound traffic. Full treatment is **Phase 4, Topic 24 — Service meshes**.
- A **backend-for-frontend (BFF)** is a thin, *per-client-type* service — one for iOS, one for web, one for partner APIs — that aggregates and shapes responses for exactly that client. It is owned by the client team, deployed on their cadence, and is a real service with real code, not configuration.

| | **API gateway** | **Service mesh** | **Backend-for-frontend** |
|---|---|---|---|
| Traffic direction | North–south (external → internal) | East–west (internal → internal) | North–south, per client type |
| Deployment | Centralised fleet at the edge | Sidecar next to every service instance | A normal service, one per client type |
| Owned by | Platform team | Platform team | The client's own team |
| Primary job | Auth, rate limiting, routing, versioning, external contract | mTLS, retries, circuit breaking, telemetry, traffic shifting | Aggregation and response shaping for one UI |
| Knows about domain? | **No — must not** | No | **Yes — that is its purpose** |
| Typical latency added | 1–3 ms | 0.5–2 ms **per hop, both ends** | 1 network hop plus fan-out |
| Scaling risk | Centralised bottleneck; shared config | Operational complexity; N sidecars to upgrade | Proliferation; duplicated logic across BFFs |
| Failure blast radius | **All external traffic** | One service pair, if configured well | One client type |
| Right answer when | You have external clients and >1 service | You have many services needing uniform reliability/security policy | Different clients need genuinely different response shapes |
| Over-engineering when | Single service, single client | Fewer than ~10 services | One client type only |

The clean composition is: **gateway for policy, BFF for shaping, mesh for internal reliability.** The common mistake is to use the gateway for all three, which produces 4.5.

### 4.7 Latency and cost overhead

Nothing here is free. Typical, approximate figures for a well-tuned proxy on modern hardware:

| Item | Typical added latency | Note |
|---|---|---|
| L7 parse + route + forward, same AZ | 0.3–1 ms p50 | Mostly the extra network hop |
| Same, p99 | 2–5 ms | Queueing and GC/epoll scheduling jitter |
| TLS termination (resumed session) | 0.5–1 ms | One extra RTT avoided by session tickets |
| TLS termination (full handshake) | 1 RTT extra ≈ 20–60 ms on mobile | Why session resumption matters (Lesson 7) |
| JWT signature validation (RS256) | 0.1–0.5 ms | Asymmetric crypto per request; cache the public key, never the token decision alone |
| Rate-limit check against a shared Redis | 0.5–2 ms | A network call; local token buckets avoid it at the cost of accuracy |
| Response aggregation of 3 backends | max(backend latencies) + ~1 ms | If parallel. If sequential, the **sum** — a very common bug |
| Cross-AZ hop | +0.5–2 ms | Avoid proxy in AZ-a → backend in AZ-b where possible |
| Cross-region hop | +30–150 ms | Never route through a gateway in another region casually |

The pattern to internalise: a single well-placed proxy costs about 1 ms. A chain of CDN → edge gateway → mesh sidecar → service → mesh sidecar → service costs five hops, and 5–10 ms of pure overhead before any work happens. For a 200 ms page that is noise; for a 5 ms internal call in a hot loop it is a doubling. Cost is quantified in Section 9 Part C.

## 5. Request / data flow

A mobile client requests `GET /api/v2/books/9780141036144` from BookNook, which runs an nginx edge proxy in front of an Envoy-based API gateway in front of a catalogue service.

1. Client resolves `api.booknook.com` via DNS to the edge proxy's address (Lesson 4).
2. TCP connection established to the edge proxy, port 443 (Lesson 3); TLS 1.3 handshake completes, resumed from a session ticket, so one round trip rather than two (Lesson 7).
3. Edge proxy **buffers the complete request**: request line, headers, and body. If headers do not arrive within the header timeout, the connection is dropped — Slowloris defence.
4. Edge proxy applies security filtering: request size under the 1 MB limit, header count under 100, no conflicting `Content-Length`/`Transfer-Encoding` (request-smuggling defence). Pass.
5. Edge proxy checks its **response cache** for this URI. Miss, so processing continues. (A hit here would end the flow at step 5 with zero backend load — Lesson 26.)
6. Edge proxy sets `X-Forwarded-For: 203.0.113.9`, `X-Forwarded-Proto: https`, and generates `X-Request-ID: 7f3c…` for end-to-end tracing.
7. Edge proxy selects an API gateway instance using its load-balancing strategy — power of two choices across healthy gateway instances (Lessons 23, 24) — and reuses a pooled keep-alive connection.
8. Gateway extracts the `Authorization: Bearer …` header and validates the JWT signature against a cached public key. Valid, not expired, audience matches. It extracts `sub=user_8821` and `plan=bronze`.
9. Gateway performs the **rate-limit** check: token bucket for `user_8821`, 100 requests/minute. 37 used. Allowed; counter incremented. (Exceeded would return `429` with `Retry-After` immediately, never touching a backend.)
10. Gateway checks the **quota** counter for the month. 412,000 of 1,000,000. Allowed.
11. Gateway matches the route table: `/api/v2/books/*` → cluster `catalogue-v2`. It rewrites the path to `/books/9780141036144` — the backend has never heard of `/api/v2`.
12. Gateway strips the client's `Authorization` header and replaces it with internal, trusted headers: `X-User-Id: user_8821`, `X-User-Plan: bronze`. The backend never sees the raw token and never validates signatures.
13. Gateway selects a healthy catalogue instance, starts a 2 s per-try timeout and a 5 s overall timeout, and checks the circuit breaker for that cluster — closed, so proceed (Lesson 17).
14. Catalogue service responds `200 OK` with 14 KB of JSON in 22 ms.
15. Gateway records the latency in its histogram, decrements in-flight counters, emits a structured access log line carrying `X-Request-ID`, and forwards the response.
16. Edge proxy stores the response in its cache according to `Cache-Control: public, max-age=60`, and applies **gzip** because the client sent `Accept-Encoding: gzip` — 14 KB becomes about 2.3 KB.
17. Edge proxy writes the 2.3 KB to the client. The phone is on a slow link and takes 300 ms to receive it. **No backend resource is held during those 300 ms** — the catalogue worker was released at step 15.
18. Edge proxy returns the gateway connection to its pool; the gateway returns the catalogue connection to its pool.

Steps 9, 10 and 17 are the ones to remember. Steps 9 and 10 rejected work before it cost anything; step 17 is the buffering benefit, and it is the difference between 119 and 4,808 requests per second per backend in Section 9.

## 6. ASCII diagram

```
FORWARD PROXY vs REVERSE PROXY
  forward: proxy acts for the CLIENT, hides the client
      [laptop]──▶[corp proxy]──▶ internet
  reverse: proxy acts for the SERVER, hides the servers
      internet ──▶[edge proxy]──▶[app 10.0.2.11]
                                 [app 10.0.2.12]  ← not internet-routable


FULL EDGE STACK — what is peeled off at each layer
   client
     │ TLS 1.3
     ▼
  ┌──────────────── EDGE REVERSE PROXY (nginx) ────────────────┐
  │ TLS terminate · buffer request · size/header limits        │
  │ static files via sendfile()  ──▶ /static/covers/*.jpg  ✔ DONE
  │ response cache  ──▶ HIT ✔ DONE (zero backend load)         │
  │ gzip/brotli · X-Forwarded-For · X-Request-ID               │
  └───────────────────────────┬────────────────────────────────┘
                              │ keep-alive pool, LAN
                              ▼
  ┌──────────────── API GATEWAY (Envoy) ───────────────────────┐
  │ JWT verify ──▶ invalid? 401 ✔ DONE                         │
  │ rate limit ──▶ over? 429 + Retry-After ✔ DONE              │
  │ quota · route · path rewrite · protocol translate          │
  │ timeout · retry · circuit break · metrics                  │
  └───────┬───────────────┬───────────────┬────────────────────┘
          ▼               ▼               ▼
     catalogue        inventory        reviews
   (plain HTTP/1.1, no TLS code, no auth code, no rate limiting)


BUFFERING SLOW CLIENTS — the worker-occupancy argument
  WITHOUT a buffering proxy (200-worker thread-per-request pool):
    worker ├─40ms work─┤├──────── 1,600 ms writing to a 1 Mbit phone ────────┤
           └ 2.4% useful ┘└──────────────── 97.6% WASTED ──────────────────┘
    capacity = 200 workers / 1.640 s  =  122 req/s

  WITH a buffering proxy:
    worker ├─40ms work─┤├1.6ms LAN┤      released!
    proxy  (event-driven, ~8 KB/connection) ──── 1,600 ms ────▶ phone
    capacity = 200 workers / 0.0416 s = 4,808 req/s     ⇒ 39× more


THE DISTRIBUTED MONOLITH ANTI-PATTERN
  HEALTHY                           DISEASED
  ┌─────────┐                       ┌───────────────────────┐
  │ gateway │ routing, auth,        │ gateway               │
  │         │ rate limit only       │ + field transforms    │
  └────┬────┘                       │ + response merging    │
       │                            │ + "if total>£100 ..." │
   ┌───┴───┬───────┐                │ + per-client hacks    │
   ▼       ▼       ▼                └──────────┬────────────┘
  svc A   svc B   svc C                  ┌─────┴─────┐
  (each deployable alone)                ▼     ▼     ▼
                                        svc A svc B svc C
                                   every change needs a gateway
                                   deploy ⇒ one release train again
```

## 7. Detailed example

### BookNook

**Starting point.** Three Django app servers behind a simple L4 load balancer (Lesson 22). Each runs gunicorn with 40 sync workers, so 120 workers fleet-wide. Each server terminates its own TLS and serves cover images from its own disk. Traffic is 60 % cover images, 40 % API.

**Symptom 1 — capacity collapses in the evening.** Between 19:00 and 22:00, p99 latency rises to 9 seconds and workers are permanently saturated, yet CPU sits at 18 %. Classic signature: **workers blocked on something that is not CPU.** Investigation shows evening traffic is 70 % mobile on cellular, average response 180 KB, and workers are held for the full transmission.

**Fix 1 — put nginx in front as a buffering reverse proxy.** `proxy_buffering on`, buffers sized to hold a typical response. Worker hold time drops from an average of 1.4 s to 45 ms. Effective capacity goes from roughly 86 req/s to 2,660 req/s on the same hardware — a 31× improvement with zero application changes. p99 falls from 9 s to 240 ms.

**Fix 2 — serve covers from nginx.** `location /static/covers/` with `sendfile on` and a 30-day `Cache-Control`. Recall from Lesson 1 that covers were 40× the application's bandwidth. The application tier's byte throughput drops by about 97.5 %, and image p50 drops from 60 ms to 4 ms because there is no Python in the path. Three app servers are now obviously over-provisioned; BookNook drops to two and saves a third of the compute bill.

**Fix 3 — TLS and compression at the edge.** One certificate, renewed in one place. gzip on JSON cuts search-response bytes by ~90 %, which matters most for the mobile users who were the original problem.

**Symptom 2 — BookNook splits into services.** Catalogue, inventory, orders, reviews, and recommendations. Now there are five services, three client types (web, iOS, partner API), and five copies of JWT validation, three of them subtly different. One has a clock-skew bug that rejects valid tokens for 30 seconds after issuance.

**Fix 4 — introduce an API gateway (Envoy).** JWT validation moves to the gateway; services receive `X-User-Id` from a trusted hop. Rate limiting becomes correct for the first time, because the gateway sees 100 % of a key's traffic, whereas per-service counters each saw a fraction. Partner quotas become enforceable, which turns the partner API into a product with paid tiers.

**Symptom 3 — the gateway starts to rot.** Within eight months the gateway config contains: a transform renaming `price_cents` to `price` for iOS 4.2 and earlier; an aggregation merging catalogue + inventory + reviews for the book page; and a rule calling the fraud service when an order exceeds £100. Gateway deploys now happen weekly, each one risking all five services, and the platform team is blocking three product teams.

**Fix 5 — extract the domain logic.** The fraud rule moves into order-service, where it belongs — it is a business rule and the order team owns it. The iOS field transform and the book-page aggregation move into an **iOS BFF** owned by the mobile team, deployed on their cadence. The gateway returns to auth, rate limiting, quotas, routing, and observability — all decidable from the request alone. Gateway deploys drop to roughly monthly and stop being risky, because the gateway no longer encodes anything that changes when the business changes.

### Second example for contrast — an internal data-processing platform

A company runs 60 internal services with **no external clients at all**: ingestion, enrichment, scoring, storage, and reporting, all machine-to-machine on a private network.

The edge-gateway value proposition largely evaporates. There are no browsers, so no TLS termination for untrusted clients, no cookies, no CORS, no compression for mobile links, no slow-client buffering (every caller is on the same 10 Gbit/s LAN), and no public API versioning problem. Routing a request through a centralised gateway would add a hop and a shared failure domain for traffic that never leaves the building.

What this platform *does* need is uniform mTLS between services, automatic retries with budgets, circuit breaking, and consistent telemetry across 60 services in four languages — which is precisely the **service mesh** column of the 4.6 table, not the gateway column. The correct design is a mesh with sidecars and *no* central gateway, plus one tiny reverse proxy in front of the single human-facing reporting UI.

Same family of components, opposite selection, because the traffic is east–west rather than north–south and the clients are fast and trusted rather than slow and hostile. The deciding questions are always: *who are the clients, how fast is their network, and do I trust them?*

## 8. Code or configuration example

> Illustrative only — **not production-ready**. Real deployments need TLS cipher policy, certificate automation, log shipping, metrics scraping, graceful-shutdown handling, resource limits, WAF rules, and tested failure behaviour. Values below are chosen for teaching clarity, not for your workload.

### nginx — reverse proxy doing the classic seven jobs

```nginx
# --- Shared caches and limits, defined once at http{} level -----------------
# Response cache: 10 MB of keys in memory, 2 GB of bodies on disk, evict after
# 7 days untouched. Sizing matters: keys_zone too small ⇒ evictions ⇒ low hit
# ratio for reasons that look mysterious (Lesson 26, working set vs cache size).
proxy_cache_path /var/cache/nginx levels=1:2 keys_zone=booknook:10m
                 max_size=2g inactive=7d use_temp_path=off;

# Token bucket keyed by client IP: 20 requests/second sustained.
# This is EDGE protection, not per-user policy -- NAT means one IP may be
# hundreds of users (Lesson 3), so keep the limit generous here and do
# per-identity limiting at the gateway where you know WHO the caller is.
limit_req_zone $binary_remote_addr zone=edge:10m rate=20r/s;

upstream booknook_app {
    least_conn;                     # request costs vary a lot (Lesson 23)
    server 10.0.2.11:8000 max_fails=3 fail_timeout=30s;
    server 10.0.2.12:8000 max_fails=3 fail_timeout=30s;
    keepalive 64;                   # pool backend connections; avoids ephemeral
                                    # port exhaustion toward one IP:port pair
}

server {
    listen 443 ssl http2;           # HTTP/2 to clients (Lesson 8); backends
                                    # still speak plain HTTP/1.1 and don't care
    server_name booknook.com;

    # --- 1. TLS TERMINATION: one key, one renewal, one cipher policy --------
    ssl_certificate     /etc/ssl/booknook.crt;
    ssl_certificate_key /etc/ssl/booknook.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_session_cache   shared:TLS:50m;   # resumption: saves a full round trip,
    ssl_session_timeout 1d;               # worth 20-60 ms on a mobile network

    # --- 2. SECURITY FILTERING: reject garbage before it costs a worker -----
    client_max_body_size 1m;        # a 2 GB upload dies here, not in the heap
    client_header_timeout 10s;      # SLOWLORIS DEFENCE: headers must arrive
    client_body_timeout   10s;      # within 10s or the connection is dropped
    server_tokens off;              # stop advertising the nginx version

    # --- 3. COMPRESSION: text only, above 1 KB ------------------------------
    gzip on;
    gzip_types application/json text/css application/javascript;
    gzip_min_length 1024;           # below this, the gzip header costs more
                                    # than it saves
    # NOTE: never gzip JPEG/PNG/video -- already compressed, pure CPU waste.

    # --- 4. STATIC FILES: never let Python touch a book cover ---------------
    # Lesson 1: covers are ~40x the application's bandwidth.
    location /static/covers/ {
        root /srv/booknook;
        sendfile on;                # kernel copies page cache -> socket;
                                    # bytes never enter user space
        tcp_nopush on;              # coalesce into full packets
        expires 30d;                # covers are immutable per ISBN
        add_header Cache-Control "public, immutable";
        access_log off;             # 60% of requests; logging them all is a
                                    # measurable disk and CPU cost
    }

    # --- 5 + 6 + 7. PROXYING: cache, buffer, rewrite ------------------------
    location /api/ {
        limit_req zone=edge burst=40 nodelay;   # allow short bursts

        # CACHING at the edge
        proxy_cache booknook;
        proxy_cache_key "$scheme$request_method$host$request_uri$http_authorization";
        #                                                       ^^^^^^^^^^^^^^^^^^^
        # CRITICAL: the key must include EVERY dimension that varies the
        # response. Omit the auth dimension and user A's cached response is
        # served to user B. This is a data-leak bug, not a performance bug.
        # Lesson 26 Section 4 treats key design properly.
        proxy_cache_valid 200 60s;              # cache successes for 60s
        proxy_cache_valid 404 10s;              # cache misses BRIEFLY too, so a
                                                # scraper hammering bad ISBNs
                                                # cannot reach the database
        proxy_cache_use_stale error timeout updating http_500 http_502 http_503;
        # ^ serve slightly stale content when the backend is sick. Staleness is
        #   almost always better than a 502 for a book description.
        proxy_cache_lock on;                    # on a miss, let ONE request go
                                                # to the backend; others wait.
                                                # Prevents the stampede covered
                                                # in Phase 2, Topic 13.
        add_header X-Cache-Status $upstream_cache_status;  # HIT/MISS/STALE --
                                                # you cannot tune a hit ratio
                                                # you cannot observe

        # BUFFERING -- the worker-occupancy fix from Section 4.3
        proxy_buffering on;             # read the WHOLE response from the
                                        # backend before writing to the client
        proxy_buffers 16 16k;           # 256 KB of memory per connection...
        proxy_buffer_size 16k;          # ...for the first chunk (headers)
        proxy_busy_buffers_size 32k;
        # If a response exceeds 256 KB nginx spills to disk. Disk spill is slow;
        # size buffers to cover your p95 response, not your maximum.
        proxy_request_buffering on;     # same protection for slow UPLOADS

        # REWRITING
        proxy_pass http://booknook_app/;    # trailing slash strips "/api/"
        proxy_http_version 1.1;
        proxy_set_header Connection "";      # required for upstream keepalive
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # The backend must ONLY trust these headers because this hop is
        # trusted. A client can forge X-Forwarded-For; strip-and-set, never
        # append blindly from an untrusted source.

        proxy_connect_timeout 2s;       # Lesson 17: never leave timeouts unset
        proxy_read_timeout   10s;
        proxy_next_upstream error timeout http_502 http_503;
        proxy_next_upstream_tries 2;    # bounded retries; unbounded retries
                                        # turn a brownout into a retry storm
    }

    # --- STREAMING: the one place buffering is WRONG ------------------------
    location /api/events {
        proxy_pass http://booknook_app;
        proxy_buffering off;            # server-sent events must stream;
        proxy_cache off;                # buffering would hold the whole
        proxy_read_timeout 1h;          # "response" forever and break SSE
        chunked_transfer_encoding on;
    }
}

# Redirect plaintext to TLS. Cheap, and the only correct behaviour.
server {
    listen 80;
    server_name booknook.com;
    return 301 https://$host$request_uri;
}
```

### Envoy — API gateway policy

```yaml
# Illustrative Envoy static config. Production uses a CONTROL PLANE (xDS) to
# push this dynamically; hand-written static config does not scale past a
# handful of routes.
static_resources:
  listeners:
  - name: edge_listener
    address: { socket_address: { address: 0.0.0.0, port_value: 8443 } }
    filter_chains:
    - filters:
      - name: envoy.filters.network.http_connection_manager
        typed_config:
          "@type": type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager
          stat_prefix: edge
          # Generate a request ID if absent, propagate it if present.
          # This is what makes a distributed trace possible at all.
          generate_request_id: true
          use_remote_address: true     # correctly derive the client IP from
                                       # X-Forwarded-For, trusting N hops below
          xff_num_trusted_hops: 1      # exactly one trusted proxy (the nginx
                                       # edge). Setting this too high lets a
                                       # client spoof its own IP.

          # ORDER MATTERS. Filters run top to bottom. Authenticate before
          # rate limiting, so an unauthenticated flood is rejected by the
          # cheapest check first and never consumes a rate-limit lookup.
          http_filters:
          - name: envoy.filters.http.jwt_authn
            typed_config:
              "@type": type.googleapis.com/envoy.extensions.filters.http.jwt_authn.v3.JwtAuthentication
              providers:
                booknook_auth:
                  issuer: https://auth.booknook.com
                  audiences: [ "booknook-api" ]
                  remote_jwks:
                    http_uri:
                      uri: https://auth.booknook.com/.well-known/jwks.json
                      cluster: auth_jwks
                      timeout: 2s
                    cache_duration: 600s   # cache the PUBLIC KEYS for 10 min.
                                           # Without this you make an HTTP call
                                           # per request to validate a token --
                                           # a classic self-inflicted outage.
                  forward_payload_header: x-jwt-payload
                  forward: false           # STRIP the raw token. Backends must
                                           # never see client credentials; they
                                           # trust the verified header instead.
              rules:
              - match: { prefix: "/v2/" }
                requires: { provider_name: booknook_auth }
              - match: { prefix: "/v2/public/" }   # more specific rules FIRST
                requires: {}                       # health/catalogue browse is
                                                   # deliberately unauthenticated

          - name: envoy.filters.http.local_ratelimit
            typed_config:
              "@type": type.googleapis.com/envoy.extensions.filters.http.local_ratelimit.v3.LocalRateLimit
              stat_prefix: edge_rl
              token_bucket:
                max_tokens: 100            # burst allowance
                tokens_per_fill: 100
                fill_interval: 60s         # 100 requests/minute sustained
              # LOCAL means per-Envoy-instance. With 4 gateway instances the
              # EFFECTIVE limit is 4x what is written here. For an exact
              # global limit you need the ratelimit service backed by Redis --
              # which costs a 0.5-2 ms network call on EVERY request.
              # This is the accuracy-vs-latency trade-off, stated explicitly.

          - name: envoy.filters.http.router
            typed_config:
              "@type": type.googleapis.com/envoy.extensions.filters.http.router.v3.Router

          route_config:
            virtual_hosts:
            - name: booknook
              domains: ["*"]
              routes:
              # Longest/most specific prefix must come first -- Envoy matches
              # in order and takes the FIRST match, not the best match.
              - match: { prefix: "/v2/orders/" }
                route:
                  cluster: order_service
                  prefix_rewrite: "/orders/"   # backend never sees "/v2"
                  timeout: 5s                  # OVERALL budget for the request
                  retry_policy:
                    retry_on: "5xx,reset,connect-failure"
                    num_retries: 2
                    per_try_timeout: 2s        # 2 tries x 2s < 5s overall.
                    # If per_try x retries EXCEEDS the overall timeout, the
                    # retries can never run. Very common misconfiguration.
                    retry_back_off:            # Lesson 17: backoff + jitter
                      base_interval: 0.05s
                      max_interval: 1s
              - match: { prefix: "/v2/books/" }
                route: { cluster: catalogue_service, prefix_rewrite: "/books/" }

  clusters:
  - name: catalogue_service
    type: STRICT_DNS
    lb_policy: LEAST_REQUEST        # power-of-two-choices (Lesson 23)
    # Protocol translation: external HTTP/1.1 in, internal HTTP/2 (gRPC) out.
    typed_extension_protocol_options:
      envoy.extensions.upstreams.http.v3.HttpProtocolOptions:
        "@type": type.googleapis.com/envoy.extensions.upstreams.http.v3.HttpProtocolOptions
        explicit_http_config: { http2_protocol_options: {} }
    circuit_breakers:
      thresholds:
      - max_connections: 1000
        max_pending_requests: 100   # queue at most 100 waiting requests, then
                                    # FAIL FAST with 503. An unbounded queue
                                    # converts a slow backend into an OOM and
                                    # takes the gateway down with it -- which
                                    # would break ALL services, not one.
        max_retries: 3              # cap CONCURRENT retries fleet-wide, so a
                                    # partial failure cannot become a storm
    outlier_detection:
      consecutive_5xx: 5
      base_ejection_time: 30s
      max_ejection_percent: 50      # never eject more than half (Lesson 24)
    load_assignment:
      cluster_name: catalogue_service
      endpoints:
      - lb_endpoints:
        - endpoint: { address: { socket_address: { address: catalogue.internal, port_value: 8080 } } }
```

## 9. Numerical example

### Part A — Worker occupancy with and without buffering

**Assumptions, stated explicitly:**
- Backend is thread-per-request with a pool of **200 workers** across the fleet.
- Application time to generate a response: **40 ms** (CPU + database).
- Average response size: **200 KB** = 200 × 1,024 × 8 = **1,638,400 bits**.
- Mobile client effective downstream throughput: **1 Mbit/s** = 1,000,000 bits/s (typical for congested cellular; a good 4G link is 10–50× this, a bad one 5× worse).
- Proxy-to-backend LAN: **1 Gbit/s** = 1,000,000,000 bits/s, utilised at ~100 % for this transfer.
- A worker is released only when the last byte of the response has been handed to the transport.
- Ignore TLS and TCP overhead; it does not change the conclusion.

**Step 1 — transmission time to the client.**
```
t_client = 1,638,400 bits ÷ 1,000,000 bits/s = 1.6384 s  ≈ 1,638 ms
```

**Step 2 — worker hold time without buffering.**
```
W_nobuf = app time + client transmission
        = 40 ms + 1,638 ms
        = 1,678 ms = 1.678 s
```

**Step 3 — useful fraction of that time.**
```
useful = 40 ÷ 1,678 = 0.0238 = 2.38 %
wasted = 97.62 %
```
Ninety-seven per cent of a scarce, memory-hungry worker spent waiting on a radio link.

**Step 4 — throughput without buffering, via Little's Law (Lesson 14), L = λ × W.**
Here `L` is capped at the pool size, so `λ_max = L ÷ W`:
```
λ_nobuf = 200 workers ÷ 1.678 s = 119.2 requests/second
```

**Step 5 — worker hold time with buffering.**
```
t_lan    = 1,638,400 bits ÷ 1,000,000,000 bits/s = 0.0016384 s ≈ 1.64 ms
W_buf    = 40 ms + 1.64 ms = 41.64 ms = 0.04164 s
```

**Step 6 — throughput with buffering.**
```
λ_buf = 200 ÷ 0.04164 = 4,803 requests/second
```

**Step 7 — the ratio.**
```
improvement = 4,803 ÷ 119.2 = 40.3×
```

**Step 8 — what that means in servers.** Suppose BookNook must serve **1,000 req/s** of this traffic.
```
Without buffering: workers needed = λ × W = 1,000 × 1.678 = 1,678 workers
With buffering:    workers needed = 1,000 × 0.04164 =  42 workers

At 40 workers per server:
  without buffering = 1,678 ÷ 40 = 42 servers
  with buffering    =    42 ÷ 40 =  2 servers (rounding up)

At a typical $120/month per application server:
  without = 42 × $120 = $5,040/month
  with    =  2 × $120 =   $240/month
  plus 2 proxy servers at $120 = $240/month
  Total with buffering = $480/month.
  Saving = $4,560/month ≈ $54,720/year, from one configuration directive.
```

**Step 9 — memory check on the proxy.** This is the cost side, and it must be stated.
```
Concurrent slow clients at 1,000 req/s with W = 1.678 s:
  L = λ × W = 1,000 × 1.678 = 1,678 concurrent connections

Proxy memory per buffered connection ≈ 256 KB of buffers + ~8 KB of state
  = 264 KB

Total = 1,678 × 264 KB = 443,000 KB ≈ 433 MB
```
433 MB on the proxy to save 40 application servers. That is the trade: **cheap memory on an event-driven proxy replaces expensive workers on a thread-per-request backend.** If the number of concurrent slow clients were 50,000 instead of 1,678, the figure would be 12.9 GB and you would need to shrink buffers or accept disk spill — the limit is real, just far away.

### Part B — Slowloris, quantified

**Assumptions:** 200-worker backend; attacker sends one header byte every 10 seconds per connection; backend header timeout is 60 s (a common default that is far too generous); proxy header timeout is 10 s.

```
Connections needed to exhaust the pool = 200
Attacker bandwidth required            = 200 conns × 1 byte / 10 s
                                       = 20 bytes/second = 160 bit/s
```
**160 bits per second takes down the server.** That is roughly one thousandth of a dial-up modem.

With a buffering proxy in front:
```
Proxy concurrency limit   ≈ 100,000 connections (event-driven, ~8 KB each
                             ⇒ 800 MB, feasible)
Connections needed now    = 100,000
Attacker bandwidth        = 100,000 × 1 byte / 10 s = 10 KB/s
AND each connection is killed after 10 s by client_header_timeout, so the
attacker must re-establish 100,000 TCP connections every 10 seconds:
                          = 10,000 new connections/second
```
The attack cost rises by a factor of 500 in bandwidth and becomes trivially detectable by connection-rate monitoring. **The backend is never touched at all**, because no request is forwarded until it is complete.

### Part C — Gateway latency and cost overhead

**Assumptions:** 500,000,000 requests/month; p50 backend time 25 ms; gateway adds 0.8 ms p50 and 3 ms p99; managed gateway priced at **$3.50 per million requests** (typical cloud list price); self-hosted alternative is 4 × 2-vCPU instances at **$62/month** each plus 0.25 of an engineer-week per month for maintenance.

**Step 1 — relative latency cost.**
```
Added latency as a share of p50 = 0.8 ÷ 25 = 3.2 %
```
Negligible for a user-facing page. Now the same gateway on an internal call with a 4 ms p50:
```
0.8 ÷ 4 = 20 % overhead — and with a mesh sidecar on both ends,
  0.8 (gateway) + 0.7 (client sidecar) + 0.7 (server sidecar) = 2.2 ms
  2.2 ÷ 4 = 55 % overhead.
```
**Conclusion:** proxy overhead is irrelevant at the edge and significant on short internal calls. That is a concrete answer to "does the proxy cost too much?" — it depends on **the ratio of proxy time to backend time**, nothing else.

**Step 2 — aggregate human waiting.**
```
500,000,000 × 0.8 ms = 400,000,000 ms = 400,000 s = 111 hours/month
```
Worth knowing, but spread across 500 M requests nobody perceives it. Compare the alternative being bought: one unbuffered slow-client disaster costs far more.

**Step 3 — managed gateway cost.**
```
500 M ÷ 1 M × $3.50 = $1,750/month = $21,000/year
```

**Step 4 — self-hosted cost.**
```
Compute: 4 × $62            = $248/month
Engineering: 0.25 week/month at a fully-loaded $3,000/week
                             = $750/month
Total                        = $998/month = $11,976/year
```

**Step 5 — compare.**
```
Saving by self-hosting = $1,750 − $998 = $752/month = $9,024/year
```
But the saving is only ~43 %, and it buys you pager duty for a component on the critical path of 100 % of traffic. **The honest reading: below roughly 200 M requests/month the managed gateway is almost always correct, because the engineering time dominates; above about 1 B requests/month the per-request pricing dominates and self-hosting wins clearly.** The crossover here is where the per-request fee exceeds the fully-loaded cost of the people who would run it.

**Step 6 — capacity check on the self-hosted fleet.**
```
500 M/month ÷ 2,628,000 s/month = 190 requests/second average
Peak-to-average ratio for a consumer site ≈ 4×  ⇒ 760 req/s peak
A 2-vCPU Envoy instance handles ≈ 10,000 req/s of simple proxying
4 instances = 40,000 req/s capacity vs 760 req/s peak = 1.9 % utilised
```
The fleet is sized for **redundancy and failure tolerance, not throughput** — you need ≥3 instances across ≥2 availability zones regardless of load (Lesson 24). This is a general and frequently missed point: at the edge, the minimum viable fleet is usually set by availability maths (Lesson 15), not by capacity maths.

## 10. Trade-offs

**Reverse proxy**
- *Advantages:* cross-cutting concerns implemented once; massive worker-capacity gains from buffering (40× in Part A); static files served at kernel speed; backends removed from the public internet; TLS, caching, and compression in a component built for them.
- *Disadvantages:* one more hop (~1 ms); a new component to run, monitor, and upgrade; the private key concentrates here; misconfiguration affects 100 % of traffic; buffering costs proxy memory and breaks streaming unless explicitly disabled.
- *Operational cost:* low — nginx/Envoy are extremely stable and well understood.
- *Unnecessary when:* a single internal service with fast, trusted clients and no TLS, caching, or static-asset needs.

**API gateway**
- *Advantages:* correct, global rate limiting and quotas (impossible per-service); one auth implementation; public contract decoupled from internal topology; versioning and gradual traffic shifting; uniform external telemetry; protocol translation.
- *Disadvantages:* a centralised component whose failure is total; a shared configuration file every team wants to edit; a strong gravitational pull toward the distributed monolith; managed pricing becomes material at high volume.
- *Operational cost:* moderate to high, and mostly **organisational** — you need a clear ownership boundary, not just a proxy.
- *Unnecessary when:* you have one backend service, or one client type, or only internal traffic.

**Buffering specifically**
- *Advantages:* decouples backend worker lifetime from client network speed; eliminates Slowloris; lets a slow backend and a slow client coexist without multiplying each other.
- *Disadvantages:* proxy memory proportional to concurrent slow responses; adds latency-to-first-byte for large responses because nothing is sent until enough is buffered; **wrong for streaming**.
- *Unnecessary when:* the backend is already event-driven (Go, Node, Netty), where a slow client costs a few kilobytes rather than a worker. The benefit still exists but shrinks from 40× to roughly 1.1×.

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| Proxy fleet down (no redundancy) | **Total outage** — 100 % of requests fail, even though every backend is healthy | External synthetic checks; traffic drops to zero at the edge while backend CPU idles | Run ≥3 proxies across ≥2 AZs behind DNS or anycast; health-check and auto-replace (Lesson 24) |
| Buffering disabled on a thread-per-request backend | Site "freezes" in the evening; p99 seconds-long; CPU inexplicably low | Workers saturated while CPU < 20 %; hold time ≫ application time | Enable `proxy_buffering on`; capacity recovers immediately (40× in Part A) |
| Buffering left **on** for a streaming endpoint | Live updates arrive in one burst at the end, or never; SSE/gRPC-stream clients hang | Time-to-first-byte equals total response time on that route | `proxy_buffering off` plus a long read timeout on streaming routes only |
| Proxy buffers too small for typical responses | Latency rises and proxy disk I/O spikes; intermittent slowness under load | `proxy_temp_path` write activity; `X-Cache-Status` unaffected but p95 degraded | Size buffers to cover the p95 response body; add memory |
| Cache key omits a varying dimension (user, locale, currency) | **User A sees user B's data.** A security incident, not a performance bug | Usually a customer report — very hard to detect automatically | Add the dimension to the key; purge the cache; never cache authenticated responses by default (Lesson 26) |
| `X-Forwarded-For` trusted from untrusted hops | IP-based rate limits and bans are bypassed; audit logs show forged IPs | Impossible source IPs in logs; rate limits ineffective against a real attacker | Set `xff_num_trusted_hops` exactly; strip-and-set at the outermost trusted proxy |
| JWKS endpoint unreachable and public keys not cached | **Every authenticated request fails with 401**, across all services at once | Gateway 401 rate spikes to 100 % with no change in client behaviour | Cache JWKS with a long TTL; serve stale keys on fetch failure; alert on fetch errors |
| Rate-limit backend (Redis) down, fail-closed | All traffic rejected with 429 or 503 despite healthy backends | 429 rate at 100 %; Redis health alarm | Decide fail-open vs fail-closed deliberately; for most products fail-**open** with a local fallback bucket is correct |
| Gateway retries without a budget | A brownout becomes a full outage as retries multiply load 3× | Backend request rate rises while client rate is flat | Cap `max_retries` concurrently; use retry budgets and backoff with jitter (Lesson 17) |
| `max_pending_requests` unbounded | Gateway OOMs and restarts, taking **all** services offline, not just the slow one | Gateway memory climbing; restart loop | Bound the pending queue and fail fast with 503; shed load rather than buffering it |
| Gateway accretes business logic | Releases slow to a crawl; a gateway change breaks an unrelated service | Gateway change frequency; cross-team deploy coupling; incidents traced to config | Extract domain logic into services and BFFs; enforce the "request-data only" rule |
| TLS certificate expires on the proxy | Browser security warnings; **all** clients blocked at once | Certificate expiry monitoring (and, too often, Twitter) | Automated renewal (ACME); alert at 30 and 7 days remaining |

## 12. Alternatives

| Approach | What it is | Pros | Cons | Choose when |
|---|---|---|---|---|
| **Nothing — expose services directly** | Clients call each service | Zero hops, zero components | Every service re-implements TLS, auth, limits; topology is public; slow clients burn workers | One internal service, trusted fast clients |
| **Reverse proxy only** (nginx, HAProxy, Caddy) | L7 proxy without policy | Simple, fast, battle-tested; solves 80 % of the value for 20 % of the complexity | No identity awareness, no quotas, no aggregation | One or a few services, one client type |
| **API gateway** (Envoy, Kong, APISIX, AWS API Gateway) | Reverse proxy plus policy | Central auth, rate limiting, quotas, versioning, telemetry | Central failure domain; config contention; anti-pattern gravity | Multiple services and external or paying clients |
| **Service mesh** (Istio/Envoy, Linkerd) | Sidecar proxy per instance | Uniform mTLS, retries, circuit breaking, telemetry on east–west traffic | Significant operational complexity; latency on both ends of every hop | >10 internal services needing uniform policy (Phase 4, Topic 24) |
| **Backend-for-frontend** | A per-client aggregation service | Keeps client-specific logic out of the gateway; owned by the client team | More services to run; duplicated logic across BFFs | Client types need genuinely different response shapes |
| **CDN** (CloudFront, Fastly, Cloudflare) | Reverse proxy cache at hundreds of edge locations | Removes latency *and* bandwidth at the geographic edge; absorbs DDoS | Only helps cacheable content; invalidation is slow; another vendor | Static assets and cacheable responses with a global audience (Phase 2, Topic 14) |
| **Library in each service** (Spring Cloud Gateway embedded, middleware) | Policy as code inside the app | No extra hop; full language integration | Re-implemented per language; upgrading policy means redeploying every service | Single language, few services, latency-critical |
| **Anycast + L4 LB only** | Routing in the network | Minimal latency; DDoS absorption | No HTTP awareness: no caching, buffering, auth, or rewriting (Lesson 22) | Raw TCP protocols, or in front of an L7 tier |

For BookNook the composition is the right answer: CDN for covers, nginx at the edge for TLS/buffering/caching, Envoy as the gateway for policy, and BFFs for client shaping — each doing one job it is good at.

## 13. When to use it

1. **Any public-facing HTTP service with more than one application server.** You need TLS termination, a single entry point, and health-aware routing. The proxy is effectively mandatory.
2. **Any thread-per-request backend serving clients over the public internet.** The buffering benefit alone (40× in Part A) pays for the component many times over. If your stack is Django/sync-gunicorn, Rails/Puma, PHP-FPM, or a classic servlet container, this is the highest-leverage change available to you.
3. **When static assets are a significant share of bytes.** BookNook's 40:1 ratio from Lesson 1 means ~97.5 % of bytes should never touch application code.
4. **When you have multiple services and external clients** — add the gateway for auth, rate limiting, quotas, and routing, so services stop re-implementing them inconsistently.
5. **When you sell API access.** Quotas and plan tiers are commercially necessary and can only be enforced where 100 % of a key's traffic is visible.
6. **When you must evolve the public API without breaking old clients.** Path-based versioning and gradual traffic shifting at the gateway are the practical mechanism.
7. **When you need one uniform access log and trace-ID injection** across services written in different languages.

## 14. When *not* to use it

1. **A single service with a single client, on a trusted fast network.** You are adding a hop, a failure domain, and an on-call burden for benefits you do not have. Internal batch pipelines are the classic case.
2. **Purely east–west internal traffic.** Routing service-to-service calls through a central edge gateway adds a hop and a shared failure domain for no gain — the second example in Section 7. Use a mesh, or nothing.
3. **As a place to put business logic.** If the change requires domain knowledge, it belongs in a service or a BFF. The gateway's job is decidable from the request alone.
4. **For streaming workloads, with buffering on.** Buffering is correct by default and catastrophic for SSE, long-polling, and gRPC streaming. Disable per route, deliberately.
5. **As a substitute for fixing a backend that cannot handle load.** A gateway can shed load and fail fast; it cannot create capacity. If your database is the bottleneck, caching (Lesson 26) and read replicas are the answer, not another proxy.
6. **A gateway when a reverse proxy would do.** If you have one service and one client type, nginx with a rate-limit zone is 10 % of the complexity of a gateway and solves the problem.

## 15. Common mistakes

**Beginner mistakes**
- Confusing forward and reverse proxies, and therefore mis-reasoning about who sees whose IP address.
- Leaving a thread-per-request backend exposed directly to the internet and then being baffled by saturation at 18 % CPU.
- Believing the backend sees the real client IP, so rate limiting and geolocation silently key on the proxy's address.
- Trusting `X-Forwarded-For` from any source, creating a trivial IP-spoofing and ban-evasion hole.
- Leaving `proxy_buffering on` for a server-sent-events endpoint and concluding that SSE "doesn't work".
- Caching at the proxy with a key that omits the user or locale dimension — a data leak, not a slowness bug.
- Serving static assets through the application because "it's easier", and paying 40× the bandwidth through the most expensive tier.
- Running exactly one proxy instance and converting a redundant backend fleet into a single point of failure.
- Setting a per-try timeout larger than the overall timeout, so configured retries can never actually execute.

**Experienced-engineer mistakes**
- Letting the gateway accumulate domain logic one "two-line change" at a time until it is a distributed monolith with an organisation-wide release train.
- Using *local* rate limiting across N gateway instances and reporting the configured number as the actual limit, when the effective limit is N×.
- Fetching JWKS per request instead of caching public keys, adding a hard dependency on the auth service to every single request.
- Making the rate limiter fail-closed without deciding that deliberately, so a Redis blip rejects all traffic.
- Leaving `max_pending_requests` unbounded, so a slow backend OOMs the gateway and takes every service down with it.
- Putting the gateway in a different AZ or region from its backends and paying 2 ms or 80 ms per request forever.
- Terminating TLS at the edge and then leaving the internal hop plaintext in an environment where that violates the threat model, without ever stating the assumption.
- Stacking CDN + edge proxy + gateway + two mesh sidecars and then investigating a 10 ms "unexplained" latency floor that is simply the sum of the hops.
- Adding a service mesh to obtain features (retries, circuit breaking) that the existing gateway or client library already provided.
- Sizing the proxy fleet purely on throughput and ending up with two instances, when availability maths (Lesson 15) requires at least three across two AZs.

## 16. Interview perspective

This topic rewards precision, because most candidates say "I'll put a load balancer / API gateway here" and stop.

**How to use it:**

1. **Distinguish the three components explicitly.** *"The L7 load balancer picks a healthy backend; the reverse proxy additionally terminates TLS, caches, compresses, serves static assets, and buffers slow clients; the API gateway adds identity, rate limiting, quotas, and routing policy."* Saying this unprompted immediately signals depth.
2. **Lead with buffering, because almost nobody does.** *"If the backend is thread-per-request, the proxy's most valuable job is buffering — otherwise a worker is held for the client's network time, which for a 200 KB response to a 1 Mbit/s phone is 1.6 seconds against 40 ms of real work, so you lose about 97 % of capacity."* That sentence, with numbers, is memorable.
3. **Quantify the static-asset split.** Reference the 40:1 ratio and say *"those bytes should be served by nginx with `sendfile`, or better, by a CDN, and never enter application code."*
4. **Name the anti-pattern before the interviewer does.** *"I'd keep the gateway restricted to decisions that can be made from the request alone. Once it starts merging responses or applying business rules, every feature needs a gateway deploy and you've rebuilt the monolith with network calls in it."*
5. **Place the three proxy types correctly.** Gateway = north–south, mesh = east–west, BFF = per client type. Then say which you would *not* adopt yet and why — *"only five services, so a mesh is not worth the operational cost."*
6. **Acknowledge the proxy is a failure domain.** *"It's on the critical path of 100 % of traffic, so at least three instances across two AZs, health-checked, and sized for availability rather than throughput."*
7. **Give the latency figure honestly.** *"About 1 ms per hop — 3 % of a 25 ms page, but 20 % of a 4 ms internal call, which is exactly why I wouldn't route internal traffic through the edge gateway."*

**Red flags interviewers watch for:**
- Using "load balancer", "reverse proxy", and "API gateway" interchangeably.
- No awareness that slow clients can consume backend workers.
- Proposing an API gateway for a single service with a single client.
- Putting authentication *and* business logic in the gateway without noticing the coupling it creates.
- Drawing one gateway box with no redundancy, having just explained redundancy for the app tier.
- Claiming TLS termination is "free" with no mention of where the private key lives or whether the internal hop is encrypted.
- Routing internal service-to-service calls out through the public edge gateway.
- Suggesting caching at the proxy without mentioning the cache key, or caching authenticated responses with no `Vary` or identity dimension.
- Saying rate limiting is "handled in the service", without noticing that N servers each see 1/N of the traffic.
- Stacking four proxies in a diagram and being unable to state the cumulative latency cost.

## 17. Knowledge check

Write your answers down before continuing.

**Conceptual**

1. A reverse proxy and a forward proxy are both "a server in the middle". Explain precisely what differs: who configures it, who knows it is there, whose identity is hidden from whom, and what each one therefore protects. Then explain why `X-Forwarded-For` exists only in the reverse-proxy case, and why a backend that trusts that header unconditionally has a security vulnerability.

2. Explain, using Little's Law, why response buffering produces a large throughput gain for a thread-per-request backend but only a marginal one for an event-driven backend such as Go or Node. Then identify one category of endpoint where buffering must be turned **off**, explain what breaks if it is not, and state what you would configure instead on that route.

3. Define the "gateway becomes a distributed monolith" anti-pattern. Give three concrete symptoms an engineer would observe in day-to-day work, state the single rule that prevents it, and explain where each of the following belongs instead: a field rename for an old mobile client; merging catalogue, inventory, and reviews for a book page; and "call the fraud service if the order exceeds £100".

**Practical scenario**

4. BookNook is adding a partner API. Three partners will call it; the largest expects 50 requests/second sustained with bursts to 200, and the contract caps each partner at 20 million calls per month. The API is served by four internal services. The platform team proposes: a single Envoy gateway instance in one availability zone; `local_ratelimit` configured at 50 requests/second; JWT validation with JWKS fetched per request; response aggregation of all four services performed in the gateway; and `max_pending_requests` left at its default. Identify every serious problem with this proposal, state the concrete fix for each, and say which component (gateway, BFF, service, or mesh) should own each responsibility and why. Then describe what a partner would experience during a 90-second outage of the auth service under both the proposed design and your corrected one.

**Calculation**

5. BookNook's checkout service is thread-per-request with a pool of 320 workers fleet-wide. The application generates a checkout response in 85 ms. The average response is 140 KB. 70 % of checkout traffic comes from mobile clients with an effective 2 Mbit/s downstream link; 30 % comes from desktop clients at 25 Mbit/s. The proxy-to-backend LAN is 10 Gbit/s. Assume 1 KB = 1,024 bytes, that a worker is released only when the final byte is handed to the transport, and that there is no other queueing.
   (a) Compute the transmission time to a mobile client and to a desktop client, then the traffic-weighted average worker hold time **without** a buffering proxy. What percentage of worker time is useful work?
   (b) Using Little's Law, compute maximum throughput in requests/second with and without buffering, and the ratio between them.
   (c) BookNook must support a peak of 450 checkouts/second. How many workers are required in each case, and at 40 workers per server, how many servers? At $140/month per server, what is the monthly saving?
   (d) Compute the proxy memory required to buffer the concurrent slow responses at that peak, assuming 192 KB of buffers plus 8 KB of connection state per connection. State whether a single 8 GB proxy instance is sufficient, and show the headroom as a percentage.
   Show every step and state any additional assumptions you make.

Reply with your answers and I'll assess them, correct anything shaky, and give model answers before Lesson 26.

---

**Progress checklist**

Phase 1 — Foundations: **complete** (Lessons 1–17)

Phase 2 — Scaling:
`[x] 1. Vertical scaling` · `[x] 2. Horizontal scaling` · `[x] 3. Stateless application servers` · `[x] 4. Load balancers` · `[x] 5. Layer 4 versus Layer 7` · `[x] 6. Load-balancing strategies` · `[x] 7. Health checks and failover` · `[x] 8. Reverse proxies and API gateways`
`[ ] 9. Caching fundamentals` ← next
`[ ] 10–22` not started

Phases 3–5: not started.

**NEXT LESSON: Phase 2, Topic 9 — Caching fundamentals**

---

# Lesson 26 — Caching Fundamentals

*Phase 2, Topic 9*

## 1. Simple definition

A **cache** is a smaller, faster store that holds copies of data whose authoritative home is a larger, slower store, so that repeated requests for the same data can be answered without going back to the slow one.

That one sentence contains four separate commitments, and every difficulty in caching comes from one of them:

1. **Copies.** The data exists in at least two places, so they can disagree.
2. **Smaller.** It cannot hold everything, so something must be evicted.
3. **Faster.** The benefit is real, and Section 9 shows it is non-linear.
4. **Repeated requests.** If nothing repeats, a cache is pure cost.

Terms defined before use:
- **Origin / source of truth / backing store**: the authoritative copy. Usually the database.
- **Hit**: the requested data was in the cache. **Miss**: it was not.
- **Hit ratio**: hits ÷ (hits + misses), over some window. Sometimes called hit rate.
- **Eviction**: removing an entry to make room for another. Covered properly in **Phase 2, Topic 12 — Cache eviction policies (LRU, LFU, FIFO, and TTL-based)**.
- **Invalidation**: removing or updating an entry because the underlying data changed. Covered in **Phase 2, Topic 11 — Cache invalidation strategies and write policies**.
- **TTL (time to live)**: how long an entry may be served before it is considered expired.
- **Stale**: the cached copy no longer matches the origin.
- **Working set**: the subset of all data that is actually being requested during a given period. Not the same as total data size, and the distinction decides whether caching will work at all.
- **Cache key**: the identifier under which a response is stored and looked up.

> The one-line mental model: **a cache trades correctness-in-time for speed.** You get answers faster, and in exchange you sometimes get an answer that was true a moment ago rather than one that is true now. Every caching decision is a negotiation over how much "a moment" is allowed to be.

## 2. Why it exists

Three forces make caching almost unavoidable in any system at scale.

**Force 1 — the latency hierarchy is brutally non-uniform.** Typical, approximate figures on modern hardware:

| Store | Typical latency | Relative to L1 cache |
|---|---|---|
| CPU L1 cache | 1 ns | 1× |
| CPU L2 cache | 4 ns | 4× |
| Main memory (RAM) | 100 ns | 100× |
| NVMe SSD random read | 100 µs | 100,000× |
| In-memory cache over the LAN (Redis) | 0.5–1 ms | ~750,000× |
| Database query, indexed, warm | 5–20 ms | ~10,000,000× |
| Database query, complex join or cold | 50–500 ms | up to 500,000,000× |
| Cross-region network round trip | 80–150 ms | ~100,000,000× |

The jump from a 1 ms cache read to a 40 ms database query is 40×. There is almost no other single change in system design that offers a 40× improvement for a day's work.

**Force 2 — recomputation is usually wasteful.** BookNook's homepage runs a "top 20 bestsellers" query: a join across orders and books, aggregated over 7 days, ordered and limited. It costs 380 ms and scans several million rows. The result changes meaningfully about once an hour. If the homepage is requested 2,000 times a minute, BookNook runs that query **2,000 times a minute to produce an identical answer** — 120,000 executions per hour where 1 would do. Caching here is not an optimisation; running it uncached is simply a defect.

**Force 3 — databases are the hardest tier to scale.** Application servers are stateless (Lesson 20) and scale horizontally by adding instances (Lesson 19). A database holds state; scaling it means replicas, sharding, consistency decisions, and operational risk (Phase 3). A cache **removes read load from the component that is hardest and most expensive to scale**, which is why it is so often the highest-leverage change available. Section 9 Part E shows it is also roughly 178× cheaper per operation.

**Force 4 — caching only works because of locality of reference**, which is the next section. Without it, none of the above applies.

## 3. Real-world analogy

**A chef's *mise en place* — the small bowls of prepared ingredients arranged within arm's reach of the stove.**

- The **walk-in cold store** at the back of the restaurant is the database: it holds everything, and a trip there takes 90 seconds.
- The **prep bowls by the stove** are the cache: tiny by comparison, but reachable in one second.
- The chef fills them with what tonight's menu actually needs. That is the **working set** — not "all the ingredients the restaurant owns", but "the ingredients tonight's orders will call for".
- Reaching for chopped onion and finding it in the bowl is a **hit**; finding the bowl empty and walking to the cold store is a **miss**.
- Because onion appears in 60 % of dishes, it is worth a bowl. Because truffle appears twice a month, it is not. That is **temporal locality** — recently used things are likely to be used again.
- When the chef fetches onions, they bring the whole crate, not one onion, because the next few dishes will want more. That is **spatial locality** — things near what you just used are likely to be wanted too.
- A bowl of hollandaise prepared at 18:00 and still sitting there at 23:00 is **stale**, and the fact that it is fast to reach is no longer a virtue.
- If the menu changes mid-service, the bowls must be emptied and refilled. That is **invalidation**, and it is the hard part.

**Where the analogy breaks down:**

| Chef's prep bowls | A real cache |
|---|---|
| The chef knows the whole menu in advance and fills bowls deliberately | A cache is usually filled **reactively**, by misses. The first request for every item is always slow — unless you deliberately warm it |
| A chef notices visually that the hollandaise has turned | Stale cached data looks **identical** to fresh data. There is no signal. You must decide staleness policy in advance with a TTL or an invalidation message |
| One chef, one set of bowls | Many application servers, each possibly with its **own** local cache, which can hold *different* stale values simultaneously — one user sees the old price, another sees the new one |
| Emptying a bowl is instant and harmless | Invalidating a hot key at peak traffic sends every waiting request to the database at once — a **stampede** (Phase 2, Topic 13) |
| Running out of bowl space means a longer walk | Running out of cache space can mean **thrashing**: constant eviction, near-zero hit ratio, and *worse* than no cache, because you pay cache latency on every request plus the full database cost |
| Prep bowls never serve the wrong customer's food | A cache key missing a dimension serves **user A's data to user B**. This is a security incident, and it is the single most dangerous mistake in this lesson |

The last two rows are the ones that cause real outages and real incidents. Both are quantified below.

## 4. Detailed technical explanation

### 4.1 Locality of reference — why caching works at all

A cache is only useful if access patterns are **non-uniform**. If every one of 10 million books were equally likely to be requested, a cache holding 100,000 of them would have a hit ratio of 1 % and would be almost pure overhead. Caching works because real access patterns are never uniform. There are two kinds of locality:

**Temporal locality**: an item accessed recently is likely to be accessed again soon. A book on the homepage carousel is requested thousands of times an hour; a 1997 technical manual is requested once a quarter. Temporal locality is what LRU-style eviction exploits.

**Spatial locality**: items *near* a recently accessed item are likely to be accessed soon. "Near" means near in whatever address space is relevant:
- A CPU fetches a 64-byte cache line, not a byte, because the next bytes are likely wanted.
- A database reads an 8 KB page, not a row, for the same reason.
- A user who loads page 1 of search results is likely to load page 2.
- A user who views a book is likely to view its sequel or its author's other books.

Spatial locality is what justifies **read-ahead** and **batch loading** — fetching the ten books on a result page in one query rather than ten, then caching all ten.

Most real-world access distributions are approximately **Zipfian**: the *n*-th most popular item is requested roughly proportionally to `1/n`. The practical consequence is memorable:

```
Under a classic Zipf distribution over 1,000,000 items:
  the top     1,000 items (0.1%) absorb roughly 25-35% of all requests
  the top    10,000 items (1%)   absorb roughly 45-60% of all requests
  the top   100,000 items (10%)  absorb roughly 70-85% of all requests
```
(Figures are approximate and depend on the Zipf exponent; treat them as orders of magnitude, not constants.)

This is the entire business case for caching: **caching 1 % of your data can serve over half your traffic.** Section 9 Part D works through BookNook's numbers concretely.

### 4.2 Hit ratio and the effective-latency formula

The central formula of this lesson:

```
effective_latency = hit_ratio × hit_latency + (1 − hit_ratio) × miss_latency
```

Two refinements that teaching models usually omit, stated here so you are not surprised later:

1. **A miss usually costs *more* than the original uncached request**, because you pay the cache lookup, *then* the database query, *then* the cache write. The honest formula is `miss_latency = cache_lookup + origin_latency + cache_write`. With a 0.5 ms lookup, a 40 ms query, and a 0.5 ms write, a miss is 41 ms, not 40 ms. At a 95 % hit ratio this hardly matters; at a 20 % hit ratio it means the cache is making you *slower*.
2. **These are averages, and averages hide the tail.** At a 95 % hit ratio, 5 % of requests are slow — and 5 % is exactly the p95 region (Lesson 16). A cache improves the mean dramatically and improves the tail far less. If your SLO is on p99, a 95 % hit ratio leaves the entire p99 region uncached. This is one of the most commonly missed consequences of caching.

The reason the formula matters so much is that it is **non-linear in the useful direction**. The miss term dominates whenever `miss_latency ≫ hit_latency`, so what actually matters is not the hit ratio but the **miss ratio**, and the miss ratio falls geometrically:

```
hit ratio 80 %  ⇒ miss ratio 20 %   (baseline)
hit ratio 95 %  ⇒ miss ratio  5 %   (4× fewer misses)
hit ratio 99 %  ⇒ miss ratio  1 %   (20× fewer misses)
hit ratio 99.9 %⇒ miss ratio  0.1 % (200× fewer misses)
```

Every improvement in hit ratio is best read as a **division of the miss ratio**, and therefore a division of the load on the database. Going from 80 % to 95 % does not feel like "15 % better"; it is **4× less database load**. Section 9 Part A computes this in full.

### 4.3 What is safe to cache, and what is not

| Safe to cache, usually | Why | Typical TTL |
|---|---|---|
| Immutable assets (`/static/cover-9780141036144-v3.jpg`) | Content never changes for that URL | 1 year, `immutable` |
| Book metadata (title, author, ISBN, description) | Changes rarely; staleness is harmless | 1–24 hours |
| Category and navigation trees | Changes on editorial schedule | 1 hour |
| Search results for common queries | Slightly stale results are acceptable | 1–5 minutes |
| Computed aggregates (bestsellers, "customers also bought") | Expensive to compute, inherently approximate | 10–60 minutes |
| Reference data (country list, currency codes, shipping bands) | Near-static | 24 hours |
| Session/auth token validation results | Hot, and the token has its own expiry | Shorter of 5 min and token lifetime |

| Unsafe or requires great care | Why |
|---|---|
| **Anything authenticated, under a key lacking identity** | Serves one user's data to another. The classic catastrophic bug |
| **Current stock level during checkout** | Overselling. A 60-second-stale "3 in stock" sells an item you do not have |
| **Account balances, payment state, order status** | Users act on these immediately; stale values generate support tickets and legal exposure |
| **Anything whose staleness cost is unbounded** | Permission and role checks — a revoked admin who stays admin for an hour is a security failure |
| **Write-path reads used to make a decision** | "Read the row, check a condition, write" — caching the read breaks the condition. Needs a transaction, not a cache |
| **One-shot data** | Anything requested exactly once: unique report exports, per-request nonces. Caching costs memory and returns nothing |
| **Very large objects relative to cache size** | A few 50 MB objects evict thousands of hot small ones; the hit ratio collapses |

The decision rule, stated without hand-waving: **cache an item when `(request frequency × latency saved) > (cost of staleness × staleness probability)`**, where the cost of staleness is measured in business consequences, not technical ones. A stale book description costs nothing. A stale stock count costs a refund, an apology, and sometimes a regulator. You can cache both — but with wildly different TTLs, and for stock you cache the *display* value and re-check authoritatively at checkout. That pattern — **cache for display, verify for decisions** — resolves most of the hard cases.

### 4.4 Working-set size versus cache size

This determines whether a cache will work *before* you build it.

- **Total data size**: everything in the database. BookNook: 2,000,000 books × 4 KB of metadata ≈ **8 GB**.
- **Working set**: what is actually requested in a period. BookNook's 30-day working set is ~180,000 distinct books × 4 KB ≈ **720 MB**.

The ratios that matter:

```
cache_size ÷ working_set_size = coverage
```

| Coverage | Typical outcome |
|---|---|
| ≥ 1.0 | Everything in the working set fits. Hit ratio limited only by TTL expiry and invalidation, often 97–99.9 % |
| 0.3 – 1.0 | Hit ratio tracks the popularity curve; Zipf means even 0.3 coverage can give 85–95 % |
| 0.05 – 0.3 | Hit ratio depends entirely on skew. Usable if traffic is strongly Zipfian, poor if flat |
| < 0.05 with flat access | **Thrashing.** Entries are evicted before they are reused. Hit ratio near zero, and the system is slower than with no cache |

Two practical consequences:

1. **Measure the working set, not the database size.** Teams routinely refuse to cache because "we have 8 GB of books and the cache is only 1 GB", when the actual working set is 720 MB and fits comfortably. The question is never "how much data do I have?" — it is "how much data is *hot*?"
2. **Thrashing is worse than no cache**, and it is not obvious from the outside. The symptoms are a hit ratio that will not rise despite tuning, high eviction rate, and latency slightly *worse* than before the cache existed. The fix is either a bigger cache, a smaller cached object, or caching a narrower, hotter subset — not a different eviction policy.

A diagnostic worth remembering: **if your eviction rate is close to your insertion rate, you are thrashing.** In a healthy cache, insertions greatly exceed evictions until the cache fills, after which evictions should be dominated by TTL expiry rather than capacity pressure.

### 4.5 Cache key design — the most dangerous section in this lesson

**The rule: the cache key must include every dimension that varies the response. Every single one. No exceptions.**

If the response can differ based on some input, and that input is not in the key, then two requests with different inputs will collide and one caller will receive the other's response.

Dimensions that commonly vary a response and are commonly forgotten:

| Dimension | Example | What goes wrong if omitted |
|---|---|---|
| **User / tenant identity** | `/cart`, `/orders`, `/recommendations` | **User A sees user B's cart.** Data breach |
| **Authorisation role** | Admin sees unpublished books | A normal user sees unpublished data, or an admin sees a reduced view |
| **Locale / language** | `?lang=fr` | French users served English content |
| **Currency / region** | Prices in GBP vs EUR | Users charged or quoted the wrong amount |
| **API version** | `/v1` vs `/v2` field shapes | Clients receive a schema they cannot parse |
| **Device class** | Mobile vs desktop image sizes | Desktop images sent to phones, or vice versa |
| **Pagination and sorting** | `?page=3&sort=price_desc` | Page 3 served for page 1 |
| **Content negotiation** | `Accept: application/json` vs `text/html` | HTML returned to a JSON client |
| **Compression** | `Accept-Encoding: gzip` vs none | A gzip body served to a client that cannot decompress it |
| **A/B test variant** | Experiment bucket | Experiment results silently corrupted; variants bleed |
| **Feature flags** | Beta UI enabled | Beta content leaks to all users |

A concrete leak, step by step:

```
Request 1: GET /api/cart     Authorization: Bearer <alice>
  key chosen (WRONG) = "GET:/api/cart"
  miss ⇒ backend returns Alice's cart ⇒ stored under "GET:/api/cart"

Request 2: GET /api/cart     Authorization: Bearer <bob>
  key = "GET:/api/cart"  ← identical
  HIT ⇒ Bob is served ALICE'S CART.
```

Nothing errors. No alarm fires. Latency *improves*. The bug is invisible to every technical signal you have and is usually discovered by a customer. This is why the default policy at any shared cache must be **do not cache responses that carried an `Authorization` header or a session cookie**, and why caching authenticated content must be an explicit, reviewed opt-in with identity in the key.

Good key construction principles:

- **Structure keys hierarchically and predictably**: `booknook:v2:book:9780141036144:lang=en:cur=GBP`. A namespace prefix (`booknook`) prevents collisions with other applications sharing the cache; a schema version (`v2`) lets you invalidate everything at once by bumping it.
- **Normalise before hashing.** `?b=2&a=1` and `?a=1&b=2` are the same request. Sort parameters, lowercase hostnames, strip tracking parameters such as `utm_source`, and drop unknown parameters — otherwise an attacker appends `?x=1`, `?x=2`, … and generates unbounded unique keys, evicting your entire hot set. That attack is called **cache busting** or **cache-key flooding**, and parameter allow-listing is the defence.
- **Beware over-keying.** Including a timestamp, a request ID, or a session ID in the key of shared content guarantees a 0 % hit ratio. Over-keying is as common as under-keying; it is merely less dangerous — it fails loudly as a cost problem rather than quietly as a breach.
- **Version the key when the response shape changes.** Deploying code that returns a new field while the old shape is still cached produces a mixed, intermittently broken response set that is extremely hard to debug.

The HTTP mechanism for the shared-cache case is the **`Vary` header**: `Vary: Accept-Encoding, Accept-Language` tells caches that those request headers are part of the key. `Vary: Cookie` effectively disables shared caching, which is usually the correct outcome for authenticated pages.

### 4.6 Staleness — the fundamental cost

Every cached entry is a bet that the origin has not changed. **Staleness is not a bug you can fix; it is the price you agreed to pay.** The only decisions available are *how much* staleness and *how it ends*.

Staleness is bounded by one of three mechanisms:

1. **TTL expiry.** The entry is valid for *T* seconds. Maximum staleness is *T*. Simple, requires no coordination, and works across every cache layer including ones you do not control (browsers, ISP proxies). The cost is that you are always serving data up to *T* old, even when nothing changed, and you cannot retract early.
2. **Explicit invalidation.** On write, delete or overwrite the key. Staleness is near zero in the happy path. The costs: you need a reliable path from every writer to every cache, and if an invalidation message is lost the entry is stale **forever** — far worse than a TTL. Multiple application-local caches make this much harder, because you must reach all of them.
3. **Versioned keys.** The key embeds a version or content hash (`cover-9780141036144-v3.jpg`). A write produces a *new key*, so the old entry is never wrong — it is merely unreferenced and evicted eventually. This is the strongest pattern available and is why asset pipelines hash filenames. It requires the caller to know the current version, which usually means the version lives in a smaller, less-cached record.

The practical rule: **use TTL as the safety net even when you also invalidate explicitly.** Invalidation handles the common case quickly; TTL bounds the damage when invalidation fails. Belt and braces, because invalidation *will* fail.

A second practical rule: **set TTL from the business tolerance for staleness, not from a feeling about freshness.** "How wrong can this be before a user is harmed?" Book description: a day. Price: a minute, because of advertising-compliance risk. Stock for display: 30 seconds. Stock for the actual decision: never cached. Each answer comes from the business, not from the cache.

Write policies (write-through, write-back, write-around, cache-aside) and invalidation patterns are **Phase 2, Topic 11 — Cache invalidation strategies and write policies**.

### 4.7 Read-heavy versus write-heavy workloads

The **read:write ratio** largely decides whether caching helps.

A simplified but useful model: an entry is written to the cache on each miss or invalidation, and read on each hit. Define `R` as reads per item per period and `W` as writes per item per period. Each write invalidates the entry, so an entry survives for roughly `1/W` of the period and absorbs about `R/W` reads before being invalidated.

```
reads served per cache fill ≈ R ÷ W
hit ratio ceiling            ≈ 1 − (W ÷ R)   [when every write invalidates]
```

| R:W ratio | Hit-ratio ceiling | Verdict |
|---|---|---|
| 1,000:1 | 99.9 % | Ideal. Cache aggressively |
| 100:1 | 99 % | Excellent |
| 10:1 | 90 % | Good; worth caching |
| 2:1 | 50 % | Marginal; the cache write cost may exceed the benefit |
| 1:1 | 0 % | Pointless — every entry is invalidated before it is read |
| 1:10 | Negative value | **Harmful.** You pay 10 cache writes per read and get almost no hits |

BookNook's catalogue is roughly 50,000:1 (books are read constantly, edited rarely) — an ideal caching target. BookNook's shopping cart is roughly 3:1 and changes on every user action — a poor target for a shared TTL cache, though fine for a short-lived per-session store.

Two important refinements, because the simple model is too crude for production reasoning:

- **Writes to *cold* items do not matter.** A system with a million writes per second to items nobody reads has no caching problem. What matters is the write rate **to the items in the working set**.
- **Write-heavy systems can still cache derived data.** You may not cache the mutable row, but you can cache the expensive aggregate computed from it with an explicit staleness budget — "bestsellers, recomputed every 10 minutes" is write-heavy underneath and perfectly cacheable on top.

### 4.8 What caching does *not* fix

Stated plainly, because caching is routinely misapplied:

- It does not fix a **missing index**. A 400 ms query that should be 4 ms is a schema bug; caching it hides the bug until the cache is cold, then you discover your origin cannot survive its own traffic.
- It does not fix **write throughput**. All writes still hit the origin.
- It does not fix **p99 latency** by itself; the misses *are* the tail (4.2).
- It does not fix **correctness requirements**. If a read must be authoritative, it must not be cached.
- It does not fix **capacity planning**. You must still be able to survive a cold cache, which is the single most common catastrophic caching failure (Section 11).

## 5. Request / data flow

BookNook serves `GET /api/v2/books/9780141036144` with a cache-aside (lazy-loading) Redis cache in front of PostgreSQL. Cache-aside means *the application* — not the cache — is responsible for reading through to the database and populating the cache.

1. Request arrives at the edge proxy (Lesson 25). The proxy checks its own HTTP response cache first. Miss; it forwards to the application.
2. Application resolves the request's identity and context: user plan `bronze`, locale `en-GB`, currency `GBP`, API version `v2`.
3. Application constructs the cache key, including **every dimension that varies the response**: `booknook:v2:book:9780141036144:lang=en-GB:cur=GBP`. Note that user identity is deliberately absent — this response is identical for all users, which is precisely why it is shared-cacheable.
4. Application issues `GET` to Redis. Round-trip time on the same availability zone: **0.6 ms**.
5. **Miss.** Redis returns nil.
6. Application queries PostgreSQL: `SELECT ... FROM books WHERE isbn = $1` plus a join for author and category. **38 ms**.
7. Application serialises the result to JSON: **1.5 ms**.
8. Application writes to Redis with `SETEX key 3600 value` — a 1-hour TTL chosen because book metadata changes roughly weekly and a one-hour window of staleness is commercially harmless. **0.6 ms**, and the write is fire-and-forget: if it fails, the request still succeeds, because the cache is an optimisation, never a dependency.
9. Application returns the response. **Total for this miss: 0.6 + 38 + 1.5 + 0.6 ≈ 40.7 ms** — slightly *more* than the 38 ms it would have cost with no cache at all. This is the honest cost of a miss.
10. Edge proxy stores the response under `Cache-Control: public, max-age=60` and returns it to the client.
11. **Forty milliseconds later, a different user requests the same book.** Different session, same locale and currency, so the same key.
12. Edge proxy serves it from its own cache in **0.2 ms**. Zero application load, zero Redis load, zero database load.
13. Sixty-one seconds later the edge entry expires. The next request reaches the application, which does step 4 again — and this time Redis **hits** in **0.6 ms**. Total request time ≈ **2 ms** instead of 40.7 ms.
14. An editor corrects the book's description. The write path updates PostgreSQL, then **deletes** `booknook:v2:book:9780141036144:*` from Redis (delete, not update — updating risks writing a value derived from a stale read).
15. The edge proxy's copy is *not* deleted, because BookNook does not run proxy purge for this route. Maximum staleness at the edge is therefore its 60-second `max-age`. This is a deliberate, stated trade: 60 seconds of possible staleness in exchange for not building a purge pipeline.
16. The next request misses in Redis, repopulates from PostgreSQL at step 6, and the cycle repeats.

Steps 9 and 15 are the ones to internalise. Step 9: **a miss is slower than no cache.** Step 15: **every layer has its own staleness window, and your real staleness is the sum of the layers you did not invalidate.**

## 6. ASCII diagram

```
THE LATENCY HIERARCHY — why a cache is worth building
  L1 cache      1 ns   │
  RAM         100 ns   │▌
  NVMe SSD    100 µs   │████
  Redis (LAN)   1 ms   │████████
  DB (warm)    40 ms   │████████████████████████████████
  DB (cold)   400 ms   │████████████████████████████████████████████████
                        each step is roughly 100-1000x the one above


CACHE-ASIDE READ PATH
                        ┌──────────┐
   request ──────▶      │   app    │
                        └────┬─────┘
                   1. GET    │ 0.6 ms
                        ┌────▼─────┐   HIT  ──▶ return (total ≈ 2 ms)  ✔
                        │  Redis   │
                        └────┬─────┘   MISS
                   2. query  │ 38 ms
                        ┌────▼─────┐
                        │ Postgres │
                        └────┬─────┘
                   3. SETEX  │ 0.6 ms  ⇒ total ≈ 40.7 ms
                        └────▶ (populate for next time)
   NOTE: the MISS path is SLOWER than having no cache (38 ms).
         The cache only wins because hits vastly outnumber misses.


EFFECTIVE LATENCY — non-linear, because MISSES dominate
  hit=1 ms, miss=40 ms
   0% │████████████████████████████████████████  40.0 ms
  50% │████████████████████                      20.5 ms
  80% │████████                                   8.8 ms
  90% │████                                       4.9 ms
  95% │███                                        2.95 ms
  99% │█                                          1.39 ms
       the last 4 percentage points (95→99) buy MORE than the first 80


WORKING SET vs CACHE SIZE
  total data        [████████████████████████████████████]  8 GB
  30-day working set[███]                                 720 MB
  cache             [████]                                  1 GB   ⇒ coverage 1.4x
                     ^ fits: hit ratio limited only by TTL, not by capacity

  THRASHING (the failure case)
  working set       [████████████████████████]            500 GB
  cache             [▌]                                     1 GB   ⇒ coverage 0.2%
  ⇒ entries evicted before reuse; hit ratio ≈ 0; SLOWER than no cache


CACHE KEY LEAK — the most dangerous bug in this lesson
  key = "GET:/api/cart"            ← identity dimension MISSING
   Alice ──▶ MISS ──▶ [Alice's cart] stored under that key
   Bob   ──▶ HIT  ──▶ [Alice's cart]        ← served to Bob
  No error. No alarm. Latency IMPROVES. Found only by a customer.
  key = "GET:/api/cart:user=alice" ← correct: every varying dimension present
```

## 7. Detailed example

### BookNook

**Starting state.** Two application servers, one PostgreSQL primary, no cache. 10,000,000 page views/day, concentrated so that peak is roughly 4× the average. Database p50 is 38 ms, p99 is 310 ms, and CPU on the primary sits at 78 % during peak — comfortably into the queueing-cliff region from Lesson 14.

**Step 1 — measure before caching.** The team captures query counts by type over 24 hours:

| Query | Calls/day | Mean latency | Distinct results/day | Read:write |
|---|---|---|---|---|
| Book by ISBN | 6,200,000 | 38 ms | 180,000 | 50,000:1 |
| Bestsellers aggregate | 1,400,000 | 380 ms | 1 | ∞ (recomputed hourly) |
| Search results | 1,900,000 | 120 ms | 240,000 | n/a |
| Cart contents | 420,000 | 12 ms | 95,000 | 3:1 |
| Stock level | 380,000 | 9 ms | 60,000 | 1.2:1 |

This table alone decides the strategy, and that is the point of producing it. Book-by-ISBN and bestsellers are obvious wins. Cart and stock are not.

**Step 2 — the bestsellers query first, because it is the absurd one.** 1,400,000 executions per day producing **one** distinct result, at 380 ms each. Cached with a 10-minute TTL, it runs 144 times per day instead of 1,400,000 — a reduction of **99.99 %**. Homepage p50 falls from 410 ms to 28 ms. Database CPU at peak drops from 78 % to 61 % from this change alone. This took an afternoon.

**Step 3 — book-by-ISBN.** Working set is 180,000 distinct books × 4 KB ≈ **720 MB**, against a 2 GB Redis instance: coverage 2.8×, so capacity will not be the limiting factor. Hit ratio stabilises at **94 %** after a day, limited by the 1-hour TTL and the long tail of rarely-requested books, not by evictions (confirmed by an eviction rate near zero).

**Step 4 — search results.** 240,000 distinct queries/day, but strongly Zipfian: the top 2,000 queries are 48 % of search traffic. Caching with a 5-minute TTL and a normalised key (`sorted params, lowercased, stopwords retained`) yields a 61 % hit ratio — unimpressive-sounding, but it removes 1.16 M of the most expensive queries per day.

**Step 5 — what they deliberately did *not* cache.** Cart contents (3:1 read:write, and a stale cart is a support ticket) and stock level (1.2:1, and a stale value means overselling). Instead, stock gets the **cache for display, verify for decisions** pattern: the book page shows a 30-second-stale "In stock" badge from cache, and checkout re-reads stock inside the order transaction, uncached. A customer may occasionally see "In stock" on a book that just sold out, and will be told at checkout — annoying, but not an oversell.

**Step 6 — the incident that taught them about cold caches.** Three weeks later, a Redis failover empties the cache during evening peak. Database QPS jumps from 420 to 7,100 in under two seconds. The primary saturates, connection pool exhausts, p99 goes to 14 seconds, and the site is effectively down for six minutes until Redis refills. The cache had been silently absorbing **94 % of read load**, and nothing in the capacity plan acknowledged that the database could no longer survive its own traffic. The fixes: `proxy_cache_lock`-style request coalescing so only one request per key reaches the database on a miss, a staged warm-up that pre-populates the top 20,000 keys before a cold cache takes traffic, and a database sized to survive at least 40 % of full uncached load rather than 6 %. The stampede mechanics are **Phase 2, Topic 13 — Cache stampede, thundering herd, and hot keys**.

**Step 7 — the near-miss that taught them about keys.** A developer adds caching to `/api/v2/recommendations` with the key `recs:{book_isbn}`. Recommendations are personalised. In staging, every user sees the same recommendations and nobody notices because staging has one test account. Caught in code review by someone who asked the right question: *"does this response vary by anything not in the key?"* The corrected key is `recs:v1:{book_isbn}:user={user_id}:lang={locale}` — and the review checklist now carries that exact question.

### Second example for contrast — a real-time trading position service

A brokerage serves "your current positions and P&L". Superficially this looks like an ideal caching target: it is read far more often than written (users refresh constantly), the computation is expensive (mark-to-market across hundreds of holdings), and the response is small.

It is nonetheless **almost entirely uncacheable**, for reasons that map exactly onto the sections above:

- **Every dimension varies the response**, and one of them is the market price, which changes continuously. A correct key would have to include a price timestamp, which means a new key every tick — a 0 % hit ratio by construction (the over-keying failure in 4.5).
- **Staleness cost is unbounded and legally material.** A user who sells based on a 30-second-old P&L has a complaint, and possibly a regulator-reportable one. This is squarely in the "write-path reads used to make a decision" row of 4.3.
- **Read:write looks favourable but is not**, because the "write" is the market feed invalidating every position on every tick. The effective R:W is below 1:1 — the pointless row of 4.7.

What this system does instead is architecturally different: it **pushes** rather than pulls. Positions are computed once per tick in a streaming pipeline and pushed to connected clients over WebSocket, so the expensive computation is amortised across all viewers without any copy ever being served stale. The client holds the current state; the server holds the authoritative one; there is no third copy to go stale.

The contrast is the lesson: **caching is a strategy for data that is read far more often than it changes.** When data changes as fast as it is read, the answer is not a better cache — it is a different architecture. Recognising which situation you are in, *before* building, is the skill.

## 8. Code or configuration example

> Illustrative only — **not production-ready**. Omitted: connection pooling and retry configuration, serialisation versioning, metrics and tracing, authentication to Redis, TLS, cluster-mode key hashing, graceful degradation testing, and the stampede protection discussed in Phase 2, Topic 13. Numbers are chosen for teaching clarity.

```python
import hashlib
import json
import time

CACHE_VERSION = "v2"          # bump this to invalidate EVERYTHING at once --
                              # the cheapest global invalidation that exists


def build_key(resource: str, resource_id: str, **dimensions) -> str:
    """Construct a cache key containing EVERY dimension that varies the response.

    THE RULE: if changing an input can change the response, that input MUST be
    in the key. Omitting one does not cause an error -- it causes one caller to
    receive another caller's data. There is no alarm for this.
    """
    # Sort dimensions so that {a:1, b:2} and {b:2, a:1} produce the SAME key.
    # Without normalisation these are two keys for one response: a silent
    # halving of the hit ratio.
    parts = [f"{k}={v}" for k, v in sorted(dimensions.items())]

    key = f"booknook:{CACHE_VERSION}:{resource}:{resource_id}:" + ":".join(parts)

    # Redis keys should stay short; hash long ones but KEEP a readable prefix,
    # otherwise debugging a production cache becomes impossible.
    if len(key) > 200:
        digest = hashlib.sha256(key.encode()).hexdigest()[:32]
        key = f"booknook:{CACHE_VERSION}:{resource}:{resource_id}:h={digest}"
    return key


def get_book(redis, db, isbn: str, locale: str, currency: str, api_version: str):
    """Cache-aside (lazy loading): the APPLICATION owns the read-through.

    Cache-aside is the right default because the cache is never on the write
    path, so a cache outage degrades latency rather than causing data loss.
    Write-through and write-back are Phase 2, Topic 11.
    """
    key = build_key(
        "book", isbn,
        lang=locale,        # a French user must not receive English copy
        cur=currency,       # a EUR user must not receive GBP prices
        api=api_version,    # v1 and v2 have different field shapes
    )
    # NOTE what is deliberately ABSENT: user identity. This response is
    # identical for every user, which is exactly why it is shared-cacheable.
    # For /cart or /recommendations, user identity MUST be present.

    try:
        cached = redis.get(key)
        if cached is not None:
            metrics.increment("cache.hit", tags=["resource:book"])
            return json.loads(cached)
    except RedisError:
        # CRITICAL: a cache failure must NEVER fail the request. The cache is
        # an optimisation, not a dependency. Degrade to the database and keep
        # serving -- slower, but correct.
        metrics.increment("cache.error")

    metrics.increment("cache.miss", tags=["resource:book"])

    # A MISS costs cache_lookup + db_query + cache_write, i.e. MORE than
    # having no cache at all. The cache only wins because hits dominate.
    row = db.query_book(isbn, locale, currency)
    if row is None:
        # Cache the NEGATIVE result too, but BRIEFLY. Without this, a scraper
        # requesting invalid ISBNs reaches the database on every single request
        # -- a trivially cheap denial-of-service. 60s is long enough to absorb
        # an attack, short enough that a newly added book appears quickly.
        redis.setex(build_key("book", isbn, lang=locale, cur=currency,
                              api=api_version), 60, json.dumps(None))
        return None

    # TTL comes from BUSINESS tolerance for staleness, not from a guess.
    # Book metadata changes ~weekly; one hour of staleness is commercially
    # harmless. Compare: price would be 60s, stock-for-display 30s,
    # stock-for-checkout NEVER cached.
    try:
        redis.setex(key, 3600, json.dumps(row))
    except RedisError:
        pass                 # fire-and-forget: a failed cache write is not a
                             # failed request

    return row


def update_book(redis, db, isbn: str, changes: dict):
    """Write path: update the origin, THEN invalidate."""
    db.update_book(isbn, changes)      # origin first -- ALWAYS

    # DELETE, do not overwrite. Overwriting means computing a new value from a
    # possibly stale read and racing concurrent writers. Deleting is idempotent
    # and the next reader repopulates from the source of truth.
    #
    # Order matters: writing the DB first then deleting the cache leaves a
    # small window where a concurrent reader can repopulate the OLD value.
    # Bounding that window is exactly why the TTL above is a safety net even
    # though we invalidate explicitly. Full treatment: Phase 2, Topic 11.
    for locale in ("en-GB", "fr-FR", "de-DE"):
        for currency in ("GBP", "EUR", "USD"):
            for api in ("v1", "v2"):
                redis.delete(build_key("book", isbn, lang=locale,
                                       cur=currency, api=api))
    # NOTE the cost of a wide key space: 3 x 3 x 2 = 18 keys to delete for ONE
    # logical object. This is the hidden price of adding key dimensions, and
    # it is why you add dimensions only when they genuinely vary the response.


def effective_latency(hit_ratio: float, hit_ms: float, miss_ms: float) -> float:
    """The central formula of this lesson.

    effective = hit_ratio x hit_latency + (1 - hit_ratio) x miss_latency

    Read it as: the MISS term dominates whenever miss >> hit, so what really
    matters is the MISS RATIO, which falls geometrically as the hit ratio rises.
    """
    return hit_ratio * hit_ms + (1 - hit_ratio) * miss_ms


# Worked instantly: hit = 1 ms, miss = 40 ms
# 0.80 -> 8.80 ms   0.95 -> 2.95 ms   0.99 -> 1.39 ms
# Going 80% -> 95% is a 3.0x latency improvement AND 4x less database load.
```

```yaml
# Redis / ElastiCache configuration -- illustrative
maxmemory: 2gb
# allkeys-lru: evict the least-recently-used key of ANY kind when full.
#   Correct for a pure cache where every key is disposable.
# volatile-lru would evict only keys that HAVE a TTL -- and if you ever store
#   a key without one, the cache fills with immortal entries and starts
#   returning OOM errors on write. A surprisingly common outage.
maxmemory-policy: allkeys-lru

# Durability is deliberately DISABLED. This is a cache: losing it costs
# latency, never data. Persistence would cost fork() pauses and disk I/O for
# a guarantee we do not need.
save: ""
appendonly: no

# Alert on these, because they are the health signals that actually matter:
#   evicted_keys rate  -- high and near the insertion rate ⇒ THRASHING,
#                         the cache is too small for the working set
#   keyspace_hits / (keyspace_hits + keyspace_misses) ⇒ hit ratio;
#                         a falling hit ratio predicts a database overload
#                         BEFORE the database shows any symptom
#   used_memory vs maxmemory ⇒ headroom
```

## 9. Numerical example

### Part A — Effective latency across hit ratios

**Assumptions, stated explicitly:**
- Cache hit latency (Redis, same availability zone, including serialisation): **1 ms**.
- Database query latency, warm and indexed: **40 ms**.
- For the simplified model, treat a miss as costing exactly the database latency (the refinement follows in Part B).
- Latencies are means; tail behaviour is addressed in Part C.

Formula: `effective = hit_ratio × hit_latency + (1 − hit_ratio) × miss_latency`

**No cache (hit ratio 0 %):**
```
effective = 0.00 × 1 + 1.00 × 40 = 0 + 40    = 40.00 ms
```

**50 % hit ratio:**
```
effective = 0.50 × 1 + 0.50 × 40 = 0.5 + 20  = 20.50 ms
speedup vs no cache = 40.00 ÷ 20.50 = 1.95×
```

**80 % hit ratio:**
```
effective = 0.80 × 1 + 0.20 × 40 = 0.8 + 8.0 =  8.80 ms
speedup vs no cache = 40.00 ÷ 8.80 = 4.55×
```

**95 % hit ratio:**
```
effective = 0.95 × 1 + 0.05 × 40 = 0.95 + 2.0 = 2.95 ms
speedup vs no cache = 40.00 ÷ 2.95 = 13.56×
speedup vs 80%      =  8.80 ÷ 2.95 =  2.98×
```

**99 % hit ratio:**
```
effective = 0.99 × 1 + 0.01 × 40 = 0.99 + 0.40 = 1.39 ms
speedup vs no cache = 40.00 ÷ 1.39 = 28.78×
speedup vs 95%      =  2.95 ÷ 1.39 =  2.12×
```

**99.9 % hit ratio:**
```
effective = 0.999 × 1 + 0.001 × 40 = 0.999 + 0.04 = 1.04 ms
speedup vs 99% = 1.39 ÷ 1.04 = 1.34×
```

**The non-linearity, made explicit.** Compare how much each equal-sized step in *effort* buys:

```
 0% → 80%  : 40.00 → 8.80 ms   improvement 31.20 ms  (80 percentage points)
80% → 95%  :  8.80 → 2.95 ms   improvement  5.85 ms  (15 percentage points)
95% → 99%  :  2.95 → 1.39 ms   improvement  1.56 ms  (4 percentage points)
```

In absolute milliseconds the returns diminish. But look at the **miss ratio**, which is what the database actually experiences:

```
80% hit ⇒ 20%  misses
95% hit ⇒  5%  misses   = 4×  fewer   (database load divided by 4)
99% hit ⇒  1%  misses   = 20× fewer   (database load divided by 20)
99.9%   ⇒  0.1% misses  = 200× fewer
```

**This is the point of the whole section.** "Hit ratio 80 % → 95 %" sounds like a 19 % improvement and is actually a **4× reduction in database load and a 3× reduction in latency**. Always restate hit-ratio changes as miss-ratio divisions; it is the only reading that corresponds to what the hardware experiences.

### Part B — The honest miss cost

**Assumptions:** cache lookup 0.5 ms; database 40 ms; cache write 0.5 ms; writes are synchronous (the pessimistic case).

```
true miss latency = 0.5 (lookup) + 40 (db) + 0.5 (write) = 41 ms
true hit latency  = 0.5 ms
```

Recomputing:
```
hit 95%: 0.95 × 0.5 + 0.05 × 41 = 0.475 + 2.05 = 2.53 ms   (vs 40 ms uncached)
hit 50%: 0.50 × 0.5 + 0.50 × 41 = 0.25 + 20.5  = 20.75 ms  (vs 40 ms)
hit 10%: 0.10 × 0.5 + 0.90 × 41 = 0.05 + 36.9  = 36.95 ms  (vs 40 ms)
hit  2%: 0.02 × 0.5 + 0.98 × 41 = 0.01 + 40.18 = 40.19 ms  (vs 40 ms) ← WORSE
```

**Break-even hit ratio.** Solve for the hit ratio `h` at which the cached system equals the uncached 40 ms:
```
h × 0.5 + (1 − h) × 41 = 40
0.5h + 41 − 41h = 40
−40.5h = −1
h = 1 ÷ 40.5 = 0.0247 = 2.47 %
```

**Conclusion:** with these numbers, any hit ratio above about **2.5 %** makes the cache a net latency win. That threshold is low *because* the database is 80× slower than the cache. Now redo it for a cheap origin — an indexed primary-key lookup at 3 ms:
```
miss = 0.5 + 3 + 0.5 = 4 ms
h × 0.5 + (1 − h) × 4 = 3
0.5h + 4 − 4h = 3
−3.5h = −1
h = 0.286 = 28.6 %
```
**You now need a 29 % hit ratio just to break even.** This is the precise answer to "should I cache this?": it depends on **the ratio of origin latency to cache latency** — the slower the origin relative to the cache, the lower the hit ratio you need to justify it.

### Part C — What caching does to the tail

**Assumptions:** 95 % hit ratio; hits are tightly distributed around 1 ms; misses around 40 ms with a p99 of 310 ms (the database tail from Section 7).

```
p50: comfortably a hit        ⇒ ≈ 1 ms      (was 38 ms)   38× better
p90: still a hit (90 < 95)    ⇒ ≈ 1 ms      (was ~90 ms)  90× better
p95: exactly the hit boundary ⇒ ≈ 40 ms     (was ~150 ms)  3.8× better
p99: deep in the MISS region  ⇒ ≈ 310 ms    (was ~310 ms)  UNCHANGED
```

**The 5 % of requests that miss are exactly the requests above p95.** A cache transforms the mean and does almost nothing for p99 unless the hit ratio exceeds 99 %. If your SLO is stated at p99 — and Lesson 16 argued it should be — then:

```
To make p99 a cache hit, you need hit_ratio > 99%.
To make p99.9 a cache hit, you need hit_ratio > 99.9%.
```

This is why "we added a cache and the average got much better but users still complain" is such a common and genuinely puzzling report. The complaining users are the misses, and they are a fixed percentage of traffic by construction.

### Part D — Working set, coverage, and hit ratio

**Assumptions:** 2,000,000 books; 4 KB serialised JSON per book; 180,000 distinct books requested in a 30-day window; Redis overhead ≈ 20 % above raw value size; access follows an approximately Zipfian distribution.

```
Total data if fully cached = 2,000,000 × 4 KB = 8,000,000 KB = 7.63 GB
                            ×1.2 overhead     = 9.16 GB

30-day working set         = 180,000 × 4 KB   = 720,000 KB = 703 MB
                            ×1.2 overhead     = 844 MB
```

**Coverage with a 2 GB cache:**
```
coverage = 2,048 MB ÷ 844 MB = 2.43×
```
The entire 30-day working set fits more than twice over, so **capacity is not the constraint**. The hit ratio will be limited by TTL expiry and invalidation, not eviction. Confirm this by checking that `evicted_keys` is near zero — if it is not, the model is wrong somewhere.

**Hit ratio from TTL alone.** With a 1-hour TTL and an item requested `f` times per hour, each entry serves `f − 1` hits per fill:
```
hot book, 500 req/hour : hits 499 of 500 ⇒ 99.8 %
warm book, 20 req/hour : hits  19 of 20  ⇒ 95.0 %
cool book,  2 req/hour : hits   1 of 2   ⇒ 50.0 %
cold book, 0.1 req/hour: expires before reuse ⇒ 0 % (and wastes memory)
```
Weighted by the Zipfian traffic share of each band, BookNook's observed blended hit ratio of **94 %** is exactly what this model predicts. The model is simple and it works — which is why it is worth doing on paper before provisioning anything.

**The thrashing counter-case.** Suppose instead the working set were 500 GB (say, you cached every image blob rather than metadata) against the same 2 GB cache:
```
coverage = 2 GB ÷ 500 GB = 0.004 = 0.4 %
```
Under anything close to a flat access distribution, entries are evicted long before reuse. Expected hit ratio is near zero, and from Part B the effective latency becomes `0.01 × 0.5 + 0.99 × 41 = 40.6 ms` — **worse than the 40 ms you started with**, plus the cost of the cache. The correct response is not a different eviction policy; it is to cache a smaller, hotter object (metadata, not blobs) and push the blobs to a CDN (**Phase 2, Topic 14 — CDNs and edge caching**).

### Part E — Cost model: cache versus database

**Assumptions (typical cloud list prices, approximate, and the exact numbers will differ by provider and region — the *ratio* is the durable lesson):**
- Peak read load: **10,000 requests/second**.
- One read replica (`db.r6g.2xlarge`-class) sustains **1,500 QPS** within the latency SLO. Cost: **$0.80/hour**.
- One cache node (`cache.r6g.xlarge`-class, 26 GB) sustains **100,000 operations/second**. Cost: **$0.30/hour**.
- 730 hours per month.
- High availability requires a minimum of **2** nodes of any stateful tier.
- Cache hit ratio achieved: **95 %**.

**Step 1 — monthly unit costs.**
```
Read replica = $0.80 × 730 = $584.00 /month
Cache node   = $0.30 × 730 = $219.00 /month
```

**Step 2 — database-only architecture.**
```
Replicas needed = 10,000 QPS ÷ 1,500 QPS = 6.67 ⇒ 7 replicas
Cost            = 7 × $584 = $4,088.00 /month
```

**Step 3 — cached architecture.**
```
Cache absorbs 95%        ⇒ database sees 10,000 × 0.05 = 500 QPS
Replicas needed          = 500 ÷ 1,500 = 0.33 ⇒ 1 by capacity,
                           but 2 by the HA minimum
Replica cost             = 2 × $584 = $1,168.00
Cache cost               = 2 × $219 =   $438.00   (2 nodes for HA)
Total                    = $1,606.00 /month
```

**Step 4 — the saving.**
```
Saving = $4,088 − $1,606 = $2,482 /month
       = 60.7 % reduction
       = $29,784 /year
```

**Step 5 — cost per operation, which is the durable insight.**
```
Cache node capacity/month = 100,000 ops/s × 2,628,000 s = 262,800,000,000 ops
Cost per million ops      = $219 ÷ 262,800 million = $0.00083

Replica capacity/month    = 1,500 ops/s × 2,628,000 s =   3,942,000,000 ops
Cost per million ops      = $584 ÷ 3,942 million = $0.1481

Ratio = 0.1481 ÷ 0.00083 = 178×
```

**A cached read is roughly 178× cheaper than a database read.** That single ratio explains why caching is nearly universal, and it is the number worth carrying into an interview.

**Step 6 — the sanity check everyone skips.** Can the remaining database survive a cold cache?
```
Full uncached load          = 10,000 QPS
Cached-architecture capacity= 2 replicas × 1,500 = 3,000 QPS
Shortfall on a cold cache   = 10,000 − 3,000 = 7,000 QPS of overload
                            = 3.3× over capacity
```
**The cached architecture cannot survive losing its cache.** This is exactly the Section 7 Step 6 incident, and it is the most common catastrophic caching failure in the industry. The honest options are:
1. Keep enough database capacity to survive a cold cache — here, 7 replicas, which erases the entire saving.
2. Accept a degraded mode: request coalescing (one database query per key, not per request), aggressive load shedding, and a staged warm-up before taking full traffic.
3. Make cache loss improbable: a replicated, multi-AZ cache with failover that preserves the dataset.

Most real systems choose a blend of 2 and 3, and size the database for roughly **30–50 % of uncached load** rather than 100 % or 5 %. State which one you have chosen; a design that has not chosen is a design that has chosen option 1 and not funded it.

## 10. Trade-offs

**Caching in general**
- *Advantages:* order-of-magnitude latency reduction (28× at a 99 % hit ratio, Part A); order-of-magnitude cost reduction per operation (178×, Part E); removes load from the tier that is hardest to scale; usually implementable in days rather than months; no change to the data model.
- *Disadvantages:* every cached item can be stale; a second copy of the truth means two things to reason about; misses are *slower* than no cache; the cache becomes a hidden capacity dependency you cannot survive losing; cache keys are a new category of security bug.
- *Operational cost:* moderate. A cache tier to run, monitor, fail over, and size; a hit ratio to watch; invalidation paths to maintain.
- *Unnecessary complexity when:* the origin is already fast (Part B: a 3 ms origin needs a 29 % hit ratio just to break even), or access is uniform, or the data changes as fast as it is read.

**Short TTL versus long TTL**
- *Short TTL:* less staleness, lower hit ratio, more origin load. Correct for prices, stock-for-display, anything with commercial or compliance sensitivity.
- *Long TTL:* higher hit ratio, more origin protection, more staleness. Correct for metadata, reference data, immutable assets.
- *The actual decision rule:* TTL should be the longest period for which a wrong answer causes no business harm. Derive it from that question, not from intuition.

**Big cache versus small cache**
- *Big:* higher coverage, higher hit ratio, more cost, longer warm-up after a cold start, more memory to lose at once.
- *Small:* cheaper, faster to warm, but risks thrashing if coverage drops below roughly 5 % of a flat working set.
- *Unnecessary when:* coverage already exceeds 1.5×; beyond that, extra memory buys nothing because the limit is TTL, not capacity (Part D).

**Caching versus fixing the origin**
- Caching a 400 ms query that should be 4 ms **hides a defect**. The cache will mask it until a cold start, then the defect returns at full scale with no capacity to absorb it.
- The sequencing rule: **index first, denormalise second, cache third.** A cache in front of a correctly-tuned origin is an optimisation; a cache in front of a broken origin is a liability with a timer on it.

## 11. Failure scenarios

| Failure | What the user experiences | How it's detected | How it recovers |
|---|---|---|---|
| **Cold cache after a restart or failover** | Site effectively down for minutes; p99 in seconds; errors and timeouts | Cache hit ratio drops to ~0 while database QPS spikes 10–20× | Request coalescing; staged warm-up of top keys; load shedding; size the database for ≥30–50 % of uncached load (Part E Step 6) |
| **Cache key omits a varying dimension** | **User A receives user B's data.** Or French users receive English content | Usually a customer report; occasionally a sharp-eyed code review. Technical signals show *improvement* | Add the dimension; purge affected keys; bump `CACHE_VERSION`; never cache authenticated responses by default |
| **Thrashing — working set ≫ cache** | Slightly *worse* latency than no cache; hit ratio will not rise whatever you tune | Eviction rate ≈ insertion rate; hit ratio stuck below ~20 % | Cache a smaller, hotter object; increase cache size; move large blobs to a CDN (Phase 2, Topic 14) |
| **Lost invalidation message** | One item is stale **indefinitely** — an old price shown for days | Nearly undetectable without reconciliation; usually a customer report | TTL as a mandatory safety net under every invalidation scheme; periodic reconciliation jobs |
| **Hot key — one key is a large share of traffic** | Latency spikes and timeouts on a single popular item; one cache node saturates | Per-key metrics; one node's CPU far above its peers | Local in-process cache in front of the shared one; key splitting; request coalescing (Phase 2, Topic 13) |
| **Stampede on expiry of a hot key** | A synchronised burst of slow requests every TTL interval, like clockwork | Database QPS spikes periodically at exactly the TTL period | Add random jitter to TTLs; single-flight/request coalescing; serve-stale-while-revalidate |
| **Cache treated as a dependency, not an optimisation** | Cache outage becomes a **total** outage, with a healthy database sitting idle | 5xx rate hits 100 % while database metrics look fine | Wrap every cache call in error handling that falls through to the origin (see Section 8) |
| **`maxmemory-policy` set to `noeviction` or `volatile-*` with untagged keys** | Writes start failing; the cache stops accepting new entries and silently freezes at a stale snapshot | `OOM command not allowed` errors; hit ratio flat while content ages | Use `allkeys-lru` for a pure cache; enforce TTLs on every write |
| **Caching the stock level used for the checkout decision** | **Overselling** — orders accepted for items that do not exist; refunds and apologies | Inventory reconciliation; customer complaints | Cache for display, verify authoritatively inside the order transaction |
| **Negative results not cached** | A scraper requesting invalid IDs reaches the database on every request | Database QPS high with a near-zero cache hit ratio for that route | Cache `null` with a short TTL (60 s in Section 8) |
| **Unbounded key space from unvalidated query parameters** | Hit ratio collapses during an attack; hot entries evicted | Cardinality of cache keys growing without bound | Allow-list query parameters; normalise and drop unknown ones |
| **Mixed response shapes after a deploy** | Intermittent client errors; roughly half of requests parse, half do not | Client-side parse failures correlated with a deploy | Version the cache key; bump `CACHE_VERSION` on every response-shape change |

## 12. Alternatives

| Approach | What it is | Pros | Cons | Choose when |
|---|---|---|---|---|
| **No cache; optimise the origin** | Indexes, query rewrites, denormalisation, better hardware | One source of truth; no staleness; no new failure mode | Has a ceiling; does not reduce repeated identical work | The origin is slow because of a defect, not because of volume |
| **Read replicas** | Copies of the database serving reads | Full query flexibility; transactionally consistent snapshots; no key design | ~10× costlier per read than a cache (Part E); replication lag is its own staleness; does not reduce repeated work | Reads are diverse and ad-hoc rather than repetitive |
| **Cache (this lesson)** | A fast key-value store of copies | 10–40× latency gain; ~178× cheaper per operation; days to implement | Staleness; key design risk; cold-start cliff | Reads ≫ writes and access is skewed |
| **Materialised views / precomputation** | The origin maintains the computed result | Always consistent with the origin; no application-side invalidation | Write-path cost; database-specific; refresh scheduling | An expensive aggregate over data that changes predictably |
| **CDN / edge caching** | Reverse proxy caches at hundreds of locations | Removes latency *and* bandwidth globally; absorbs DDoS | Only public, cacheable content; slow purge | Static assets and public responses with a geographically spread audience (Phase 2, Topic 14) |
| **Client-side caching** | Browser or mobile app holds the data | **Zero** network cost; fastest possible; scales for free | Uncontrollable once shipped; cannot be purged; per-client, so no sharing | Immutable, versioned assets with long TTLs |
| **Push / streaming instead of pull** | Server pushes updates to connected clients | No staleness at all; the computation is amortised across viewers | Requires stateful connections; much more complex | Data changes as fast as it is read — the trading example in Section 7 |
| **Sharding the origin** | Split data across databases | Scales writes as well as reads | Major complexity; cross-shard queries; rebalancing | Write throughput, not read latency, is the constraint (Phase 3) |

The key judgement: **caching and read replicas solve different problems.** Replicas scale *diverse* reads; caches scale *repeated* reads. BookNook's book-by-ISBN query is 6.2 M calls over 180,000 distinct results — overwhelmingly repeated, so a cache. An analyst running arbitrary ad-hoc queries is diverse, so a replica. Most systems need both.

## 13. When to use it

1. **When reads vastly outnumber writes for the same item.** A read:write ratio above roughly 10:1 gives a hit-ratio ceiling above 90 % (Section 4.7). BookNook's catalogue at 50,000:1 is near-ideal.
2. **When the same expensive computation is repeated.** The bestsellers query — 1.4 M executions producing one distinct result — is not an optimisation opportunity, it is a defect being corrected.
3. **When the origin is much slower than the cache.** Part B showed a 40 ms origin needs only a 2.5 % hit ratio to break even, while a 3 ms origin needs 29 %. The slower the origin, the more obviously correct the cache.
4. **When access is skewed.** Under Zipfian access, caching 1 % of items serves 45–60 % of traffic. Skew is what makes a small cache valuable.
5. **When the database is the scaling bottleneck and is expensive to scale.** A cached read is ~178× cheaper per operation (Part E) and removes load from the hardest tier.
6. **When some staleness is genuinely acceptable to the business.** Book descriptions, category trees, reference data, approximate aggregates — all tolerate minutes or hours of staleness at zero business cost.
7. **When protecting a fragile origin from bursts.** A cache with request coalescing converts 10,000 simultaneous requests for one key into one origin query.

## 14. When *not* to use it

1. **When the data must be authoritative at the moment of use.** Account balances, payment state, permission checks, and the stock level that decides whether an order can be accepted. Use "cache for display, verify for decisions" instead.
2. **When writes approach or exceed reads for the same item.** At a 1:1 ratio the hit-ratio ceiling is 0 %; below that you pay cache writes for nothing (Section 4.7).
3. **When the origin is already fast.** A 3 ms primary-key lookup needs a 29 % hit ratio just to break even (Part B). Often the cache is not worth the staleness and operational burden.
4. **When it would hide a defect.** A 400 ms query that needs an index should get the index. Index first, denormalise second, cache third.
5. **When the working set vastly exceeds the cache and access is flat.** Below ~5 % coverage you thrash and end up slower than with no cache (Part D).
6. **When the key cannot be constructed safely.** If you cannot enumerate every dimension that varies the response, do not cache it. An uncacheable endpoint is better than a data leak.
7. **When data changes as fast as it is read.** The trading-positions example: push, do not cache.

## 15. Common mistakes

**Beginner mistakes**
- Caching before measuring, so the cache lands on a query that was never the bottleneck.
- Omitting a dimension from the cache key — most dangerously user identity — and serving one user's data to another.
- Believing a higher hit ratio is a small linear gain, instead of reading 80 % → 95 % as "**4× less database load**".
- Assuming a miss is free. A miss costs lookup + query + write, which is *more* than having no cache (Part B).
- Treating the cache as a dependency, so a cache outage becomes a total outage while the database sits idle.
- Setting a TTL by feel rather than from the business tolerance for staleness.
- Updating the cache on write instead of deleting, creating races between concurrent writers.
- Caching huge blobs in a small cache and evicting thousands of hot small entries for one cold large one.
- Not caching negative results, so invalid-ID traffic reaches the database on every request.
- Comparing cache size to *total data size* rather than to the *working set*, and wrongly concluding caching cannot work.
- Measuring no cache metrics at all, so the hit ratio — the single most predictive signal of an impending database overload — is invisible.

**Experienced-engineer mistakes**
- Sizing the database for the *cached* load and having no answer for a cold cache (Part E Step 6). This is the most common catastrophic caching failure in production.
- Relying solely on explicit invalidation with no TTL safety net, so one lost message means permanent staleness.
- Setting identical TTLs across a large key set, producing synchronised mass expiry and a periodic stampede. Add jitter.
- Expecting a cache to fix p99 when the hit ratio is 95 %, not noticing that the 5 % of misses *are* the p95–p99 region (Part C).
- Running per-application-server local caches and then being unable to explain why two users see different prices — N independent caches hold N independent stale values.
- Using `volatile-lru` while writing some keys without a TTL, so the cache gradually fills with immortal entries and starts rejecting writes.
- Accepting unvalidated query parameters into the cache key, allowing trivial cache-key flooding to evict the entire hot set.
- Forgetting to bump the key version when a response shape changes, producing a mixed population of old and new shapes after a deploy.
- Caching an endpoint whose authorisation varies, so an admin view is served to a normal user — a key-design bug wearing a permissions costume.
- Treating hit ratio as the goal rather than a proxy. The goals are latency, cost, and origin protection; a 99 % hit ratio on an endpoint nobody calls is worth nothing.
- Adding a second cache layer to fix a problem caused by the first one's invalidation, doubling the number of places data can be stale.

## 16. Interview perspective

Caching appears in virtually every system design interview, and almost every candidate mentions it. Very few say anything quantitative, which makes it an unusually cheap place to stand out.

**How to use it:**

1. **State the formula and use it.** *"Effective latency is hit ratio times hit latency plus miss ratio times miss latency. With a 1 ms cache and a 40 ms database, 95 % gives 2.95 ms — about 14× better than uncached."* Numbers on a whiteboard beat adjectives every time.
2. **Reframe hit ratio as miss ratio.** *"Going from 80 % to 95 % isn't a 15 % improvement — it's 4× less database load."* This single reframing signals that you understand the mechanism rather than the vocabulary.
3. **Justify caching with the read:write ratio.** *"Book metadata is read about 50,000 times per write, so the hit-ratio ceiling is essentially 100 % — ideal. The cart is roughly 3:1 and changes on every action, so I wouldn't put it in a shared TTL cache."*
4. **Volunteer the cold-cache problem unprompted.** *"If the cache absorbs 95 % of reads, my database only has 5 % of the capacity it would need. So I need request coalescing, a staged warm-up, and enough headroom to survive maybe 30–50 % of uncached load."* Very few candidates raise this, and it is the failure that actually takes sites down.
5. **Talk about the key explicitly.** *"The key must include every dimension that varies the response — user, locale, currency, API version, pagination. If identity is missing from a `/cart` key, you serve one user's cart to another, and no technical signal will tell you."* Interviewers remember this.
6. **Separate display from decision.** *"I'd cache the stock badge on the book page with a 30-second TTL, and re-check stock inside the order transaction with no cache, so we never oversell."* It shows you distinguish correctness requirements from performance ones.
7. **Say what you would *not* cache, and why.** Naming the uncacheable cases is a stronger signal than naming the cacheable ones.
8. **Give the cost ratio.** *"A cached read is roughly two orders of magnitude cheaper per operation than a database read — around 178× with typical instance pricing."*
9. **Acknowledge the tail.** *"A 95 % hit ratio barely moves p99, because the misses *are* the tail. If the SLO is p99 I need either a 99 %+ hit ratio or a faster origin."*

**Red flags interviewers watch for:**
- Saying "add a cache" with no hit ratio, no TTL, and no key.
- Not knowing that a miss costs *more* than no cache.
- Treating a hit-ratio improvement as linear rather than as a division of origin load.
- No plan for a cold cache, after just claiming a 95 % hit ratio.
- Caching authenticated responses with no identity in the key, or being unable to list the dimensions of a key when asked.
- Proposing to cache data that must be authoritative — balances, stock-at-checkout, permissions.
- Confusing a cache with a read replica, or claiming a cache scales writes.
- Never mentioning invalidation, or proposing explicit invalidation with no TTL safety net.
- Comparing cache size to total data size instead of to the working set.
- Claiming a cache fixes p99 without noticing the misses are the tail.
- Using a cache to paper over a missing index and not saying so.

## 17. Knowledge check

Write your answers down before continuing.

**Conceptual**

1. Explain why the effective-latency formula produces a strongly non-linear benefit, using the 80 % / 95 % / 99 % figures from Section 9 Part A. Then explain why the *absolute* latency improvement diminishes at high hit ratios while the *database protection* keeps improving at the same geometric rate, and state which of the two should drive your target hit ratio for (a) a user-facing page with a p50 SLO and (b) a database that is close to its capacity limit.

2. State the rule for cache key construction, then list at least six dimensions that commonly vary a response. For the `/api/recommendations/{isbn}` endpoint, which is personalised, explain exactly what happens — step by step, from the first request to the leak — if the key is `recs:{isbn}`, and explain why no monitoring signal you normally watch would detect it. Finally, explain why *over*-keying is also a bug, and why it is nonetheless much less dangerous than under-keying.

3. Define temporal locality, spatial locality, and working set, and explain how all three combine to make a 2 GB cache effective for an 8 GB dataset. Then describe the precise conditions under which a cache becomes **slower** than no cache at all, give the two independent mechanisms by which that can happen (one about hit ratio, one about miss cost), and state the diagnostic that distinguishes a thrashing cache from a merely cold one.

**Practical scenario**

4. BookNook is launching flash sales: a book is discounted for exactly 60 minutes, promoted on the homepage, and typically receives 40,000 requests per minute during the sale. The book page currently caches title, description, **price**, and **stock level** together in a single object with a 1-hour TTL. Product management reports three problems after the first sale: some users saw the old full price during the first few minutes; some users saw the discounted price for about an hour *after* the sale ended; and the sale sold 1,180 copies of an item with 1,100 in stock. Diagnose each of the three problems in terms of the specific caching concept involved, propose a concrete fix for each (stating any TTLs and key structures you choose and why), and explain what would happen at the database the instant the cached entry for that one very hot book expires mid-sale and what you would do about it. Finally, state which parts of that book page you would cache together and which you would split apart, and justify the split.

**Calculation**

5. BookNook's review service serves `GET /books/{isbn}/reviews`. Assumptions: cache hit latency 0.8 ms; cache lookup on a miss 0.8 ms; database query 55 ms; cache write 0.8 ms; peak load 6,000 requests/second; one read replica sustains 1,200 QPS and costs $0.78/hour; one cache node sustains 90,000 ops/s and costs $0.32/hour; 730 hours per month; any stateful tier needs a minimum of 2 nodes for high availability; review objects average 9 KB serialised; 95,000 distinct books receive review traffic in a 30-day window; cache memory overhead is 20 % above raw value size.
   (a) Compute the true miss latency, then the effective latency at hit ratios of 0 %, 70 %, 90 %, 97 %, and 99.5 %. Express each as a speedup versus uncached, and express each hit ratio as a multiple reduction in database QPS versus the 70 % case.
   (b) Compute the break-even hit ratio at which the cached system matches the uncached 55 ms. Then recompute it assuming the database query were only 6 ms, and explain in one sentence what the comparison tells you about when caching is worth it.
   (c) Compute the working-set size in GB including overhead, and the coverage ratio against a 4 GB cache. State whether capacity or TTL will limit the hit ratio, and what metric you would check to confirm it.
   (d) Compute the monthly cost of a database-only architecture at 6,000 QPS, the monthly cost of a cached architecture at a 97 % hit ratio, the absolute and percentage saving, and the cost per million operations for each tier along with the ratio between them.
   (e) Determine whether the cached architecture can survive a complete cache loss at peak. If not, compute how many replicas would be needed to survive 40 % of uncached load, what that costs, and what the revised saving becomes.
   Show every step and state any additional assumptions you make.

Reply with your answers and I'll assess them, correct anything shaky, and give model answers before Lesson 27.

---

**Progress checklist**

Phase 1 — Foundations: **complete** (Lessons 1–17)

Phase 2 — Scaling:
`[x] 1. Vertical scaling` · `[x] 2. Horizontal scaling` · `[x] 3. Stateless application servers` · `[x] 4. Load balancers` · `[x] 5. Layer 4 versus Layer 7` · `[x] 6. Load-balancing strategies` · `[x] 7. Health checks and failover` · `[x] 8. Reverse proxies and API gateways` · `[x] 9. Caching fundamentals`
`[ ] 10. Browser, application, database, and distributed caches` ← next
`[ ] 11–22` not started

Phases 3–5: not started.

**NEXT LESSON: Phase 2, Topic 10 — Browser, application, database, and distributed caches**

