# Lesson 16 — Average Latency versus p50, p95, p99, and Tail Latency

*Phase 1, Topic 16*

## 1. Simple definition

**Latency** is how long one request takes, measured from the moment it is sent to the moment the response is fully received. Lesson 14 defined it. This lesson is about the fact that a system does not have *a* latency — it has **millions of latencies, one per request**, and you must describe that whole pile of numbers with a few summary statistics.

Three statistics, defined precisely:

- **Mean (average) latency** — add up every request's latency, divide by the number of requests. One number pulled toward extreme values.
- **Percentile** — the value below which a given percentage of requests fall. "**p95 = 400 ms**" means: sort every request by latency; 95 % of them finished in 400 ms or less, and 5 % took longer.
- **Median (p50)** — the middle value. Half the requests were faster, half slower.

Shorthand you will see everywhere:

| Notation | Also written | Meaning | Out of 1,000 requests, how many are slower? |
|---|---|---|---|
| **p50** | P50, median, 50th percentile | Typical request | 500 |
| **p90** | P90 | Mildly unlucky request | 100 |
| **p95** | P95 | Unlucky request | 50 |
| **p99** | P99, "two nines" | Badly unlucky request | 10 |
| **p99.9** | P999, "three nines" | Rare disaster | 1 |
| **p99.99** | P9999 | Very rare disaster | 1 in 10,000 |
| **p100** | max | Absolute worst | 0 |

**Tail latency** is the name for the slow right-hand end of that distribution — everything beyond roughly p95. The "tail" is literal: if you draw a histogram of latencies, the bulk of requests form a hump on the left and a long thin tail stretches to the right.

One more term, used constantly from here on: **latency distribution** — the full shape of the pile, not a single number. Percentiles are how you talk about a distribution without drawing it.

## 2. Why it exists

Because **averages lie about user experience, and they lie in a specific, predictable direction: they make bad systems look fine.**

Three structural reasons the mean is the wrong tool for latency:

1. **Latency distributions are not symmetric.** Latency has a hard floor (you cannot respond in negative time; you cannot beat the speed of light to the client) but no ceiling (a request can take 30 seconds). The distribution is *right-skewed* with a long tail. The mean of a skewed distribution sits between the typical value and the extremes, describing neither.

2. **Nobody experiences the average.** A user experiences *their* request. If 2 % of requests take 3 seconds, there is no user having a "63 ms average experience" — there are users having a 20 ms experience and users having a 3-second experience. The average is a fictional user.

3. **The slow requests are disproportionately your most valuable users.** The user with 300 items in their cart, the account with 10 years of order history, the customer with the biggest wishlist — these are exactly the requests that scan the most rows and land in the tail. Tail latency is correlated with customer value, which is why Amazon, Google and LinkedIn all report tail latency rather than averages.

Monitoring systems defaulted to averages for decades for a boring reason: a mean can be computed by storing two numbers (sum and count) and is trivially combinable across machines. A percentile cannot. Section 4 explains why, and what to store instead.

## 3. Real-world analogy

**A bank branch with five tellers.**

Ninety-five customers walk in, are served in 3 minutes each, and leave happy. Five customers arrive needing a complicated international transfer that takes 40 minutes.

```
Mean wait = (95 × 3 + 5 × 40) / 100 = (285 + 200) / 100 = 4.85 minutes
```

The branch manager reports "average wait: under 5 minutes" and gets a bonus. Meanwhile five customers out of a hundred waited **40 minutes** and are writing reviews. The mean of 4.85 minutes describes nobody: no customer waited 4.85 minutes. p50 is 3 minutes, p95 is 3 minutes, and p96 onwards is 40 minutes.

Now the part that makes this a *system* problem rather than a statistics problem. Suppose a customer needs **four** things done — deposit, statement, card replacement, address change — and each one is an independent visit to a teller. Their chance of avoiding the 40-minute queue every single time is `0.95 × 0.95 × 0.95 × 0.95 = 0.814`. So **18.6 % of multi-errand customers hit at least one 40-minute wait**, even though only 5 % of *individual* transactions are slow. The more things you need done, the more certain it becomes that one of them is slow.

**Where the analogy breaks down**

| Bank branch | Real distributed system |
|---|---|
| You can *see* the long queue and choose to come back tomorrow | Your request has already committed; you cannot see the queue and cannot leave without a timeout (Lesson 17) |
| Slow customers are slow for an obvious reason (complex transaction) | Tail requests are usually *identical* to fast ones; they were slow because of a GC pause or a noisy neighbour, not because of what they asked for |
| Tellers work at roughly constant speed | A server's speed depends on its own queue depth, so slowness is *self-amplifying* — once it's slow, it gets slower |
| 5 tellers, 100 customers | 500 servers, 5,000,000 requests; one bad server poisons a slice of *everyone's* traffic |
| The 40-minute customer does not make the 3-minute customers slower | A slow request holds a thread, a connection and a database lock — it *does* make everyone else slower |

That last row is the crucial one. In a bank, slow customers are a separate queue. In a server, **tail latency is contagious**: a stuck request consumes a worker from a finite pool, so a few slow requests reduce capacity for everyone, which raises queueing, which produces more slow requests. Section 11 shows this loop ending in an outage.

## 4. Detailed technical explanation

### 4.1 Why averages lie — a worked example

Take one minute of traffic to BookNook's `GET /books/{isbn}` endpoint: **1,000 requests**.

| Group | Count | Latency each | Cause |
|---|---|---|---|
| Fast | 900 | 20 ms | Cache hit |
| Medium | 80 | 60 ms | Cache miss, simple DB read |
| Slow | 20 | 3,000 ms | JVM garbage-collection pause on one replica |

**Mean**

```
Total latency = (900 × 20) + (80 × 60) + (20 × 3000)
              = 18,000 + 4,800 + 60,000
              = 82,800 ms
Mean          = 82,800 / 1,000 = 82.8 ms
```

A dashboard showing **"avg latency: 83 ms"** looks completely healthy. Now the percentiles. Sort all 1,000 values ascending; positions 1–900 hold 20 ms, positions 901–980 hold 60 ms, positions 981–1000 hold 3,000 ms.

| Statistic | Position in sorted list | Value |
|---|---|---|
| p50 | 500 | **20 ms** |
| p90 | 900 | **20 ms** |
| p95 | 950 | **60 ms** |
| p99 | 990 | **3,000 ms** |
| p99.9 | 999 | **3,000 ms** |
| max | 1,000 | **3,000 ms** |

Read those two tables together. The mean (83 ms) is **four times worse than the typical user's experience** (20 ms) and **36 times better than the unlucky user's experience** (3,000 ms). It is wrong in both directions simultaneously. It is not a conservative estimate; it is not an optimistic estimate; it is simply not a description of anything.

Scale it to BookNook's real volume. At 300,000 requests/day, "only 2 % slow" means:

```
300,000 × 0.02 = 6,000 three-second page loads every day
```

If each of those belongs to a different user, that is 6,000 annoyed people per day in a product with 10,000 daily active users — **well over half your user base hits a 3-second page in a given week.** The average hid an experience-destroying defect behind a healthy-looking number.

**The rule:** when someone shows you an average latency, your first question is always *"what's p99?"*

### 4.2 How a percentile is actually computed

The standard definition used by monitoring tools is the **nearest-rank method**:

1. Collect the latencies of all N requests in the window.
2. **Sort them ascending.**
3. Compute the rank: `rank = ceil( (p / 100) × N )`, where `ceil` rounds up.
4. The p-th percentile is the value at that rank (1-indexed).

Worked on a tiny sample so you can verify every step by hand. Twenty measured latencies, already sorted (ms):

```
pos:   1   2   3   4   5   6   7   8   9  10  11  12  13  14  15  16  17  18   19    20
ms:   12  14  15  15  16  18  19  20  21  22  23  25  27  30  35  42  55  80  310  1200
```

```
N = 20
Sum = 1,999 ms          →  Mean = 1,999 / 20 = 99.95 ms  ≈ 100 ms

p50 : rank = ceil(0.50 × 20) = ceil(10.0) = 10  → value =    22 ms
p90 : rank = ceil(0.90 × 20) = ceil(18.0) = 18  → value =    80 ms
p95 : rank = ceil(0.95 × 20) = ceil(19.0) = 19  → value =   310 ms
p99 : rank = ceil(0.99 × 20) = ceil(19.8) = 20  → value = 1,200 ms
```

The mean (100 ms) is **4.5× the median** (22 ms). Whenever mean ≫ median, you are looking at a skewed distribution and the mean is misleading. (A useful quick diagnostic: compute `mean / p50`. Below 1.3 the distribution is fairly tight; above 2 there is a serious tail.)

**Sample-size rule.** With N = 20 you cannot honestly measure p99: rank 20 of 20 is just the maximum, a single unrepeatable observation. The rule of thumb:

| To measure | You need at least | Why |
|---|---|---|
| p50 | ~20 samples | Many samples straddle the middle |
| p95 | ~100 samples | 5 samples above the line |
| p99 | ~1,000 samples | 10 samples above the line |
| p99.9 | ~10,000 samples | 10 samples above the line |
| p99.99 | ~100,000 samples | 10 samples above the line |

Reporting p99.9 from a 1-minute window on a low-traffic endpoint is noise, not measurement. Widen the window (e.g. 1 hour) or drop to a lower percentile.

### 4.3 Why you cannot average or sum percentiles

This is the single most common statistics error in production engineering, and it has two distinct forms.

**Form 1 — averaging percentiles across servers (horizontal).**

Suppose BookNook runs two app servers in a 10-minute window:

| Server | Requests in window | Measured p99 |
|---|---|---|
| A | 10,000 | 50 ms |
| B | 100 (just booted, cold cache) | 5,000 ms |

The naive fleet number that dashboards love to compute:

```
"Fleet p99" = (50 + 5,000) / 2 = 2,525 ms      ← WRONG
```

The true fleet p99 is defined over the **combined 10,100 requests**. The slowest 1 % is the slowest 101 requests. Server B can contribute at most 100 requests to that set, so even in the worst case the 101st-slowest request in the fleet comes from server A — and it sits at roughly A's 99.0th percentile shifted by one rank, i.e. **about 50–60 ms**. The naive average overstated the real figure by roughly **45×**.

The error can also run the other way. If server B had handled 10,000 requests instead of 100, the true fleet p99 would be dominated by B and land near 5,000 ms, while a request-count-weighted "average of p99s" would still give 2,525 ms — this time *understating* it by 2×. **There is no weighting scheme that fixes this**, because a percentile is not a linear function of the data. You cannot recover it from per-server summaries.

**Form 2 — adding percentiles along a call chain (vertical).**

BookNook's checkout calls three services in sequence: inventory (p99 = 100 ms), pricing (p99 = 100 ms), payment (p99 = 100 ms).

```
"Checkout p99" = 100 + 100 + 100 = 300 ms      ← WRONG, in both directions
```

- If the three services' slow requests are **independent**, it is extremely unlikely that the same request is slow in all three. The checkout p99 is *lower* than 300 ms — closer to ~150 ms, because a p99-class checkout usually means "one of three hops was slow, two were fast".
- If the slowness is **correlated** (all three share a database, or all three GC at the same time, or all three sit behind the same overloaded load balancer), checkout p99 can be *worse* than 300 ms, because the correlated bad moments stack and queueing compounds.

So the sum is neither an upper bound nor a lower bound. It is a number with no meaning.

**What to do instead: store distributions, not summaries.**

The fix in every case is the same — **aggregate the raw distribution, then compute the percentile once at the end.** Three standard mechanisms:

| Mechanism | How it works | Mergeable? | Accuracy | Memory | Where you'll meet it |
|---|---|---|---|---|---|
| **Fixed-bucket histogram** | Pre-declared buckets (≤10 ms, ≤25 ms, ≤50 ms, …). Each server exports counts per bucket; you add the count vectors, then interpolate the percentile. | Yes — addition of integers | Limited by bucket width; a percentile landing inside a wide bucket is guessed | ~20–60 counters per metric | Prometheus `histogram`, OpenTelemetry explicit-bucket histograms |
| **HDR Histogram** (High Dynamic Range) | Buckets sized by *relative* precision, so it keeps e.g. 3 significant digits from 1 µs to 1 hour. Fixed memory, no allocation on the recording path. | Yes | Bounded relative error (e.g. ±0.1 %) at every magnitude | ~10–200 KB per histogram | JVM/Go/Rust latency libraries, `wrk2`, Gatling |
| **t-digest** | A compressed sketch that keeps many tiny centroids near the extremes and few big ones in the middle, so accuracy is highest exactly where percentiles matter (p99, p99.9). | Yes — digests merge | Very accurate at the tails, less so at p50 | ~1–10 KB | Elasticsearch percentiles, Datadog, Apache Druid, Postgres `tdigest` extension |

All three share the property that matters: **they are mergeable**. Server A's histogram plus server B's histogram equals the fleet histogram, and the fleet p99 computed from that merged structure is correct. A p99 *number* from server A plus a p99 *number* from server B equals nothing.

(There is also the naive option — ship every raw latency sample to a central store and sort. It is exact and it is what tracing systems do for sampled traces, but at 10,000 RPS it is 864 million numbers per day per endpoint, so it is only viable on a sample.)

### 4.4 Tail latency amplification in fan-out

**Fan-out** is when one incoming request causes N outgoing requests, and the response cannot be sent until all N have returned. A BookNook search page might fan out to 1 inventory shard... or 100.

If each backend independently has a 1 % chance of being slow (that is what "p99 = 400 ms" means), then the probability that **all** N are fast is `0.99^N`, and the probability that **at least one** is slow — which is the one that determines the user's wait — is:

```
P(at least one slow) = 1 − 0.99^N
```

For N = 100:

```
0.99^100 = e^(100 × ln 0.99) = e^(100 × −0.01005) = e^−1.005 = 0.366
1 − 0.366 = 0.634  →  63.4 %
```

**With a fan-out of 100, 63 % of user requests contain at least one p99-latency backend call.** An event that is rare per-call (1 in 100) becomes the *common case* per-user. This is called **tail latency amplification**, and it is the reason large fan-out systems obsess over p99.9 and p99.99 rather than p99.

The full table — memorise the shape, not the digits:

| Fan-out N | `0.99^N` (all fast) | % of requests hitting ≥1 p99 call | With p99.9 backends: `1 − 0.999^N` |
|---|---|---|---|
| 1 | 0.9900 | 1.0 % | 0.1 % |
| 5 | 0.9510 | 4.9 % | 0.5 % |
| 10 | 0.9044 | 9.6 % | 1.0 % |
| 20 | 0.8179 | 18.2 % | 2.0 % |
| 50 | 0.6050 | 39.5 % | 4.9 % |
| 100 | 0.3660 | **63.4 %** | 9.5 % |
| 500 | 0.0066 | 99.3 % | 39.4 % |
| 1,000 | 0.0000432 | 99.996 % | 63.2 % |

Read the last column carefully: **making each backend 10× better at the tail (p99 → p99.9) buys you back roughly a 10× bigger fan-out at the same user experience.** That is the engineering trade: either reduce N, or push each component's tail an order of magnitude further out.

### 4.5 p99 of a request versus p99 of a user journey

Same mathematics, different and more immediately relevant framing. A BookNook product page issues **8 requests**: the HTML document, a CSS bundle, a JS bundle, `/api/book`, `/api/reviews`, `/api/recommendations`, `/api/stock`, and a tracking beacon. The user perceives the page as slow if **any** of them is slow.

```
P(all 8 fast) = 0.99^8
0.99^2 = 0.9801
0.99^4 = 0.9801² = 0.96059601
0.99^8 = 0.96059601² = 0.922744
P(at least one slow) = 1 − 0.922744 = 0.077256  →  7.73 %
```

**Your API has a 1 % slow rate. Your page has a 7.7 % slow rate.** Every per-request SLO you write is roughly 8× weaker than it sounds at the page level.

Run it backwards — the question an SLO author actually needs answered. "I want 99 % of *page loads* to be fast. What per-request percentile do I need?"

```
Need: x^8 = 0.99
x = 0.99^(1/8) = e^(ln 0.99 / 8) = e^(−0.0100503 / 8) = e^(−0.0012563) = 0.998744
```

So each request must be fast **99.87 %** of the time — you must target **p99.9 per request to deliver p99 per page.** This is why "we're at p99 = 200 ms, we're fine" is usually wrong: the unit of user experience is the journey, not the request.

The same applies over time rather than over components. A user who loads 40 pages in a session at a 7.7 % per-page slow rate has a `1 − 0.923^40 ≈ 96 %` chance of hitting at least one slow page. **Over a long enough session, every user experiences your tail.**

### 4.6 Latency SLOs and how to set them

Three terms, precisely:

- **SLI (Service Level Indicator)** — the *measurement*. "Proportion of `GET /books/{isbn}` requests served in under 300 ms, measured at the load balancer, over a rolling 28 days."
- **SLO (Service Level Objective)** — your internal *target* for that SLI. "≥ 99 % of those requests under 300 ms."
- **SLA (Service Level Agreement)** — a *contract* with a customer containing an SLO plus a financial penalty. Always strictly weaker than your internal SLO, because you want to find out before your customer does.

A latency SLO always needs **three** parts; a target with fewer is unmeasurable:

1. **A threshold** (300 ms)
2. **A percentile or proportion** (99 % of requests)
3. **A window** (rolling 28 days)

Plus two often-forgotten qualifiers: **where** it is measured (client? load balancer? application?) and **which requests count** (do health checks count? do 4xx responses count? do requests from bots count?).

**How to pick the threshold — a procedure, not a vibe:**

| Step | Action | BookNook example |
|---|---|---|
| 1 | Measure what you currently do. Get the real histogram for 4 weeks. | p50 = 45 ms, p95 = 180 ms, p99 = 420 ms, p99.9 = 2,100 ms |
| 2 | Find the point where users actually change behaviour. Use conversion/bounce data bucketed by latency, not intuition. | Add-to-cart rate drops 11 % once page load exceeds ~1 s; falls off a cliff past 3 s |
| 3 | Work backwards from the journey, not the request (§4.5). | 8 requests per page, budget 1 s per page → ~125 ms per request if serial, less if parallel |
| 4 | Set the SLO slightly tighter than current reality, not wildly tighter. An SLO you already violate is ignored; an SLO you can never violate teaches nothing. | "99 % of API requests < 300 ms over 28 days" — currently ~99.3 %, so there is a little headroom and real risk |
| 5 | Derive the **error budget**: the number of bad requests you are allowed. | — |

**Error budget arithmetic** (stating every assumption): 300,000 requests/day × 28 days = 8,400,000 requests per window. A 99 % SLO permits 1 % to be slow:

```
Error budget = 8,400,000 × 0.01 = 84,000 slow requests per 28 days
             = 84,000 / 28 = 3,000 slow requests per day
```

That number is the entire point. It converts "please make it faster" into a currency: if a risky deploy burns 40,000 budgeted requests in an afternoon, you have spent half your month's budget and the next deploy waits. If you end the month with the budget untouched, your SLO is too loose and you should either tighten it or spend the surplus on shipping faster.

**Threshold-counting beats percentile-averaging.** Note the SLO is phrased as "99 % of requests under 300 ms", *not* "p99 under 300 ms". They sound identical and are mathematically equivalent for a single window — but the first is a **count of good events**, so it is mergeable across servers and across time exactly like a histogram bucket (§4.3). The second tempts people to average p99 values across hours or hosts, which is invalid. Always phrase SLOs as proportions of good events.

### 4.7 Coordinated omission

This is the most important measurement trap in load testing, and the reason many published benchmark numbers are fantasy. It was named by Gil Tene (author of HDR Histogram).

**The setup.** Load testers come in two shapes:

- **Closed-loop (the common default):** a fixed pool of worker threads, each doing `send request → wait for response → send next request`. The request rate is a *consequence* of the response time.
- **Open-loop:** requests are issued on a schedule — one every 10 ms — **regardless of whether earlier ones have returned**. The request rate is an *input*.

**The bug.** In a closed-loop tester, when the server stalls, the tester stops sending. The stall therefore generates **one** slow measurement instead of the hundreds of slow measurements that real users (who keep arriving on their own schedule) would have experienced. The tester *omits* exactly the samples that matter, and it omits them *in coordination with* the server's bad moments — hence "coordinated omission".

**Worked example, every assumption stated.**

- Intended load: **100 requests/second** for **10 seconds** → 1,000 requests expected.
- Server normally responds in **1 ms**.
- At t = 5 s the server stalls completely for **1,000 ms** (a GC pause), then recovers instantly.
- Closed-loop tester with a single worker thread issuing requests back to back.

*What the closed-loop tester records:*

```
Before the stall:  ~500 requests, 1 ms each
The stall itself:  1 request measured at 1,000 ms
After the stall:   ~400 requests, 1 ms each
Total samples:     901
```

```
p99 rank = ceil(0.99 × 901) = ceil(891.99) = 892
Sorted: positions 1–900 are 1 ms, position 901 is 1,000 ms
p99 = value at rank 892 = 1 ms          ← REPORTED
max = 1,000 ms
```

The report reads: **"p99 = 1 ms, p99.9 = 1 ms"**. The 1-second stall is invisible outside the max.

*What actually happened to users.* Real users arrive every 10 ms whether or not the server is listening. During the 1,000 ms stall, **100 requests** arrive. The one that arrived at the start of the stall waits 1,000 ms; the next waits 990 ms; … the last waits 10 ms. (Assume the server drains them instantly on recovery — an optimistic assumption that makes the result *conservative*.)

```
Corrected sample set: 900 requests at 1 ms
                    + 100 requests at 10, 20, 30, …, 1000 ms
Total: 1,000 samples

Sorted: positions 1–900   = 1 ms
        positions 901–1000 = 10 ms, 20 ms, …, 1,000 ms (ascending)

p99 rank = ceil(0.99 × 1000) = 990
Position 990 is the 90th value of the slow group = 10 + (90 − 1) × 10 = 900 ms

p99 = 900 ms          ← REALITY
```

**1 ms reported versus 900 ms experienced — a 900× error, all of it in the flattering direction.**

**How to avoid it:**

1. Use an **open-loop / constant-throughput** load generator: `wrk2` (purpose-built for this), `k6` with `constant-arrival-rate` executor, Gatling with an injection profile, JMeter with a precise throughput timer. Avoid plain `wrk`, `ab`, and naive closed-loop scripts for latency measurement.
2. Measure **intended start time**, not actual send time: `latency = response_received_at − scheduled_send_at`. HDR Histogram exposes `recordValueWithExpectedInterval()` for exactly this correction.
3. **Trust production over benchmarks.** Real users are an open-loop load generator by definition. Client-side RUM (Real User Monitoring) has no coordinated omission — though it does have survivorship bias, since users who gave up and closed the tab never report anything.
4. Watch for the signature: a benchmark whose p99 is suspiciously close to its p50 but whose max is 1,000× both. That is coordinated omission almost every time.

### 4.8 What actually causes tails

Tail requests are usually *identical in shape* to fast requests. The slowness comes from the environment, not the work. The standard causes, with typical magnitudes:

| Cause | What happens | Typical pause | How you confirm it |
|---|---|---|---|
| **Garbage-collection pauses** | A managed runtime (JVM, Go, .NET, Node) stops threads to reclaim memory | Go/ZGC/Shenandoah: 0.1–5 ms. JVM G1: 20–200 ms. Large-heap full GC: 1–10 s | GC logs; correlate pause timestamps with slow traces |
| **Queueing delay** | Requests wait for a free worker, connection, or disk I/O slot | Unbounded; see §4.9 | Queue-depth metric, thread-pool saturation, `ss -tn` Recv-Q |
| **Retries** | A client retries a slow request, adding load that makes everything slower (Lesson 17) | Adds the backoff plus a full second attempt | Request count > expected; retry-ratio metric |
| **Noisy neighbours** | Another VM/container on the same host steals CPU, disk IOPS, or NIC bandwidth | 10–500 ms, erratic | CPU steal time (`%st` in `top`), host-level metrics, "it's slow on one node only" |
| **Cold caches** | A freshly started or failed-over instance has an empty local cache; every request is a miss | 10–100× normal for seconds to minutes | Cache hit-rate dip correlated with deploy/restart |
| **Lock contention** | Threads serialise on a mutex, a connection-pool semaphore, or a database row lock | Proportional to critical-section length × contenders | Thread dumps, `pg_locks`, lock-wait metrics |
| **TCP retransmits** | A packet is lost; TCP waits for a retransmission timeout before resending (Lesson 8) | **RTO minimum is 200 ms on Linux, 1 s in the RFC** — hence mysterious "exactly 200 ms" and "exactly 1 s" outliers | `netstat -s \| grep retrans`, packet capture |
| **Head-of-line blocking** | One slow response blocks others on a shared connection (HTTP/1.1 pipelining, TCP under HTTP/2 — Lesson 9) | Length of the blocking response | Many requests ending at the same instant |
| **Connection establishment** | TCP handshake + TLS handshake on a cold connection (Lesson 7) | 1 RTT + 2 RTT ≈ 60–300 ms intercontinental | Latency bimodal by connection age; fixed by keep-alive/pooling |
| **DNS resolution** | A cache-expired lookup on the request path (Lesson 4) | 1–100 ms, occasionally seconds on timeout | Resolve-time span in traces |
| **Disk / page-cache miss** | Data not in RAM; read from SSD or, worse, network storage | SSD 0.1–1 ms, EBS-class 1–10 ms, spinning disk 5–20 ms | I/O wait, storage latency metrics |
| **Scheduled interference** | Backups, log rotation, cron jobs, antivirus scans, container image pulls, certificate renewals | Seconds, periodic | Tail spikes at round-number times (`:00`, `03:00`) |
| **CPU frequency / throttling** | Container hits its CPU quota (cgroup CFS) and is hard-throttled until the next 100 ms period | Up to 100 ms per period, repeatedly | `container_cpu_cfs_throttled_seconds_total` |
| **Rebalancing / re-election** | A shard moves, a leader election happens, a node joins (Phase 3) | 100 ms – 30 s | Cluster events correlated with latency step-changes |

**The practical workflow:** p99 problems are almost never solved by "optimising the code". They are solved by finding *which* of the above is firing, which requires distributed tracing with tail-based sampling (trace everything, keep the slow traces) plus per-host latency breakdowns. The first question for any tail is always: **is it all hosts, or one host?** One host means an environmental cause; all hosts means a systemic one.

### 4.9 Queueing: why utilisation is the hidden cause of tails

The most under-appreciated cause. For a simple queue with random arrivals and one server (the **M/M/1** model — Markovian arrivals, Markovian service times, 1 server), the average time in system is:

```
W = S / (1 − ρ)

  W = total time (queue wait + service)
  S = average service time (how long the work itself takes)
  ρ = utilisation = arrival rate / maximum service rate   (0 ≤ ρ < 1)
```

Assume `S = 20 ms` of actual work:

| Utilisation ρ | Multiplier `1/(1−ρ)` | Average total time | Interpretation |
|---|---|---|---|
| 10 % | 1.11× | 22 ms | Idle system |
| 50 % | 2× | 40 ms | Comfortable |
| 70 % | 3.33× | 67 ms | Normal production target |
| 80 % | 5× | 100 ms | Getting tight |
| 90 % | 10× | 200 ms | Tails are now severe |
| 95 % | 20× | 400 ms | One traffic spike from collapse |
| 99 % | 100× | 2,000 ms | Effectively an outage |

Three conclusions that should change how you provision:

1. **Latency does not degrade linearly with load — it degrades hyperbolically.** Going from 80 % to 90 % utilisation doubles latency while adding only 12.5 % more traffic.
2. **"The CPU is only at 85 %, we have headroom" is wrong.** At 85 % utilisation you are at a 6.7× latency multiplier and one incident away from the cliff. Capacity planning targets 50–70 % steady-state for latency-sensitive services, precisely to keep the multiplier small.
3. **The tail degrades far faster than the mean.** The formula above is for the *average*; the p99 of an M/M/1 queue grows roughly like `S × ln(100) / (1−ρ)` ≈ `4.6 × S / (1−ρ)`. At ρ = 0.9 that is ~920 ms against a 20 ms service time. Queueing is usually the single largest contributor to p99 in a busy service, and it is invisible in code profiles because the request is not running — it is waiting.

### 4.10 Hedged and tied requests

If the tail is caused by *unlucky environments* rather than *expensive work*, then the same request sent to a different replica will probably be fast. That insight gives two techniques, both from Dean & Barroso's paper *"The Tail at Scale"* (Google, 2013).

**Hedged requests.** Send the request to replica A. If no response has arrived by the time you reach a chosen threshold — typically the p95 latency — send the *same* request to replica B. Use whichever reply comes back first; cancel or ignore the other.

The cost is bounded by construction and that is the whole trick:

```
Threshold = p95  →  only the slowest 5 % of requests ever get hedged
Extra load = ~5 % more requests
```

*Numbers.* Assume: replica latency p50 = 10 ms, p95 = 50 ms, p99 = 500 ms, p99.9 = 2,000 ms; slowness is **independent** across replicas (the key assumption — hedging is useless if all replicas are slow for the same reason); hedge threshold 50 ms; the hedge itself then takes a fresh draw from the same distribution.

- A request destined to take 2,000 ms now completes at roughly `50 ms (wait) + 10 ms (median of the second attempt) = 60 ms`.
- The p99.9 improves from 2,000 ms to ~60 ms — a **33× improvement at the extreme tail**.
- Published result from the Google paper, for calibration: on a 1,000-node service, hedging after a 10 ms delay cut p99.9 from **1,800 ms to 74 ms** while adding only **2 %** to total requests.

**Tied requests.** Hedging still wastes work, and at high fan-out that waste is significant. Tied requests go further: send the request to **both** replicas immediately, and include in each message the identity of the other. Whichever replica *dequeues* the request first sends a cancellation to its twin. Because the cancellation races the other server's queue rather than its execution, duplicate work is usually avoided entirely.

- Google reported tied requests reducing median latency ~16 % and p99 ~40 % on a distributed file system, with under 1 % extra disk reads.
- The price is protocol complexity: you need request IDs, cross-server cancellation, and tolerance for a cancellation arriving after work has begun.

**The cost/benefit decision, with explicit numbers.** Suppose BookNook's recommendation service runs at 1,000 RPS with capacity for 1,400 RPS.

| Strategy | Extra requests | Offered load | Utilisation | p99.9 |
|---|---|---|---|---|
| No hedging | 0 | 1,000 RPS | 71 % | 2,000 ms |
| Hedge at p95 (50 ms) | +5 % = 50 RPS | 1,050 RPS | 75 % | ~60 ms |
| Hedge at p50 (10 ms) | +50 % = 500 RPS | 1,500 RPS | **107 % — over capacity** | worse than baseline |

The middle row is the whole art: **hedge at a high percentile so the extra load is small.** Hedging at p50 adds 50 % load, pushes the service past capacity, and makes the tail worse than doing nothing — the technique eats itself. And note the hard prerequisites: the request must be **idempotent** (Lesson 17), you must have **spare capacity**, and the slowness must be **uncorrelated across replicas**. Violate any one and hedging is harmful.

## 5. Request / data flow

How a single latency measurement becomes a p99 on a dashboard, end to end:

1. A user in Pune requests `GET /books/1492052205`. Their browser records `t_start` (this is the only clock that matches the user's actual experience).
2. The request crosses the network to BookNook's load balancer, which stamps its own `t_lb_start`.
3. The load balancer forwards to app server #7. The app server's worker pool is busy, so the request **waits 35 ms in a queue** — invisible to the application code, which has not started running yet (§4.9).
4. A worker picks it up and stamps `t_app_start`. The gap `t_app_start − t_lb_start` is queueing plus network, and it is the number most teams never measure.
5. The app checks Redis (2 ms), misses, queries PostgreSQL (18 ms), and renders JSON.
6. Mid-render, the runtime triggers a **garbage collection pause of 140 ms**. The request is frozen. No log line, no span, no CPU time attributed to it.
7. The worker writes the response and records `duration_app = 160 ms`. It emits that value into an in-process **HDR histogram** — `histogram.record(160)` — an O(1) increment of one bucket counter, not a log write.
8. Every 15 seconds a metrics exporter serialises the histogram's bucket counts (not a percentile) and ships them to the monitoring backend. The local histogram is then reset or rotated.
9. The backend **merges** bucket vectors from all 24 app servers for that 15-second interval into one fleet histogram (§4.3).
10. A dashboard query asks for "p99 over the last 5 minutes". The backend sums 20 intervals × 24 servers of bucket counts into a single histogram, finds the bucket containing the 99th-percentile rank, and interpolates → **p99 = 480 ms**.
11. An alerting rule evaluates `proportion of requests ≤ 300 ms over 28 days` against the 99 % SLO, decrements the error budget (§4.6), and fires if the burn rate is too high.
12. Separately, the tracing system had sampled this request. Because its duration exceeded the tail-sampling threshold, the full trace was **kept** — showing a 35 ms queue wait and a 140 ms unexplained gap, which the engineer correlates with GC logs.
13. Meanwhile the browser's RUM beacon reports `t_end − t_start = 710 ms` — the server's 160 ms plus 550 ms of network, TLS, DNS, and client rendering. **The user's number is 4.4× the server's number**, which is why client-side measurement is not optional.

The lesson of this flow: there are at least **four** legitimate places to measure latency (browser, load balancer, application entry, application handler) and they disagree by large factors. An SLO that does not name its measurement point is not a specification.

## 6. ASCII diagram

```
 THE DISTRIBUTION  (1,000 requests from §4.1 -- not drawn to scale)

 count
   ^
900|  ####
   |  ####
   |  ####
   |  ####                                             "THE TAIL"
   |  ####                                       <-------------------->
  80|  ####   ##
  20|  ####   ##                                                   ##
   +--####---##---------------------------------------------------##-----> latency
      20ms   60ms                                                3000ms
       ^      ^                                                     ^
      p50    p95                                                 p99 & max
      p90

      MEAN = 83ms  ---> falls HERE, in the empty gap where
                        no request actually landed.  ^
                                                     |
                     +-------------------------------+
                     | describes nobody              |
                     +-------------------------------+


 TAIL LATENCY AMPLIFICATION  (fan-out, each backend p99 = 400ms)

                        +----------+  10ms   (fast)
            +---------->| shard 1  |
            |           +----------+
            |           +----------+  12ms   (fast)
            +---------->| shard 2  |
 +--------+ |           +----------+
 | API    |-+   ....97 more shards, all fast....
 | server | |
 +--------+ |           +----------+ 400ms   (SLOW - the 1-in-100)
      ^     +---------->| shard 100|
      |                 +----------+
      |                      |
      |  must wait for ALL   |
      +----------------------+
                 |
                 v
          user waits 400ms
     P(at least one slow) = 1 - 0.99^100 = 63.4%


 HEDGED REQUEST TIMELINE  (threshold = p95 = 50ms)

 t=0ms      send to replica A ----[ A is stuck behind a GC pause ]------------->
 t=50ms     no response yet; p95 threshold crossed
            send IDENTICAL request to replica B --[ B is healthy ]--+
 t=60ms                                      response from B ------+--> DONE
 t=2000ms   A finally replies -> discarded

            cost: only the slowest 5% are ever duplicated  = ~5% extra load
            benefit: p99.9 falls from 2000ms to ~60ms
```

## 7. Detailed example

### BookNook: the search page that "wasn't slow"

BookNook ships a new search page. The dashboard after launch:

| Metric | Value | Verdict |
|---|---|---|
| Average latency | 190 ms | Healthy |
| Error rate | 0.02 % | Healthy |
| CPU utilisation | 48 % | Plenty of headroom |

Support tickets nonetheless say "search hangs". The team pulls the histogram instead of the average:

| Percentile | Value |
|---|---|
| p50 | 70 ms |
| p90 | 110 ms |
| p95 | 160 ms |
| p99 | **2,400 ms** |
| p99.9 | **9,100 ms** |

`mean / p50 = 190 / 70 = 2.7` — the quick diagnostic from §4.2 flags a severe tail immediately.

**Investigation, in the order a competent team does it:**

1. **Is it one host or all hosts?** Break p99 down by instance. Result: all 24 instances show it. Therefore it is systemic, not a noisy neighbour.
2. **Is it one endpoint or all?** Break down by route. Result: only `/api/search`, not `/api/book`. Therefore it is in the search path.
3. **Is it one kind of request?** Break down by query shape. Result: p99 is driven by queries with **3+ facet filters** (format + language + price range), which are 4 % of traffic.
4. **Where does the time go?** Pull the kept tail-sampled traces. Each slow trace shows a fan-out to **40 index shards**, and the slow span is always whichever shard happens to be compacting.

**The arithmetic that explains everything.** Per-shard p99 = 90 ms, and a faceted query fans out to 40 shards:

```
P(all 40 shards fast) = 0.99^40
  ln(0.99^40) = 40 × (−0.01005) = −0.402
  e^(−0.402) = 0.669
P(at least one shard slow) = 1 − 0.669 = 33.1 %
```

**One third of faceted searches touch a slow shard**, even though each shard is "99 % fine". Nothing is broken; the architecture guarantees this outcome.

**Fixes, with measured effect:**

| Fix | Mechanism | Result |
|---|---|---|
| Hedge after 120 ms (≈ shard p95) | Re-issue to a replica shard (§4.10) | p99 2,400 → 320 ms; +4 % shard load |
| Cut fan-out 40 → 8 by routing facets to a dedicated index | Reduces N in `1 − 0.99^N` from 33 % to 7.7 % | p99 320 → 190 ms |
| Move compaction off-peak and rate-limit it | Removes the dominant per-shard tail cause | shard p99 90 → 35 ms |
| Change the SLO from "avg < 200 ms" to "99 % of searches < 400 ms / 28 days" | Makes the regression *visible next time* | — |

Final state: p50 = 65 ms, p99 = 180 ms, p99.9 = 410 ms. The **average barely moved** (190 ms → 170 ms). If the team had been optimising the average, they would have concluded the work was pointless. Users saw a 13× improvement in their worst experiences.

### Contrasting example: a batch report generator

BookNook also runs a nightly job that generates 50,000 publisher royalty reports. Here the correct metric is almost the opposite.

| | Interactive search | Nightly batch |
|---|---|---|
| Who waits? | A human, per request | Nobody, until the whole job is done |
| Unit of value | One request | The entire batch |
| Metric that matters | **p99 latency** | **Total wall-clock time** (and therefore throughput, Lesson 14) |
| Is the mean useful? | No | **Yes** — total time = mean × count, so the mean is exactly the right summary |
| Tail of 9 s on one report | Catastrophic | Irrelevant |
| Correct tuning | Reduce fan-out, hedge, lower utilisation | **Raise** utilisation to ~95 %; queueing delay is free when nobody is waiting |

```
50,000 reports × mean 180 ms = 9,000,000 ms = 150 minutes of CPU-time
With 8 parallel workers       ≈ 19 minutes wall clock
```

The contrast is the real lesson: **percentiles matter when a human is waiting; means matter when you are buying throughput.** Running your interactive tier at 95 % utilisation is an outage; running your batch tier at 50 % utilisation is wasted money. Use the metric that matches who is waiting.

## 8. Code or configuration example

**This is teaching code, not production code.** It computes percentiles naively by holding every sample in memory, which is exactly what §4.3 tells you not to do at scale. It exists to make the mechanics concrete.

```python
import math, random

def percentile(samples, p):
    """Nearest-rank percentile. samples: list of latencies in ms. p: 0-100."""
    if not samples:
        raise ValueError("no samples")
    ordered = sorted(samples)                  # STEP 1: sort ascending
    rank = math.ceil(p / 100 * len(ordered))   # STEP 2: 1-indexed rank, rounded UP
    rank = max(1, min(rank, len(ordered)))     # clamp: p=0 -> 1, p=100 -> N
    return ordered[rank - 1]                   # STEP 3: convert to 0-indexed

# ---- Reproduce the distribution from section 4.1 exactly ----
latencies = [20] * 900 + [60] * 80 + [3000] * 20

mean = sum(latencies) / len(latencies)
print(f"mean  = {mean:.1f} ms")                         # 82.8 ms  -- looks fine
for p in (50, 90, 95, 99, 99.9):
    print(f"p{p:<5} = {percentile(latencies, p)} ms")   # p99 = 3000 ms -- does not

# ---- The cardinal sin: averaging percentiles across servers ----
server_a = [10] * 9800 + [50] * 200     # 10,000 requests, mostly fast
server_b = [5000] * 100                 # 100 requests, all slow (cold instance)

naive = (percentile(server_a, 99) + percentile(server_b, 99)) / 2
true_ = percentile(server_a + server_b, 99)   # merge RAW data, THEN compute
print(f"naive avg-of-p99 = {naive} ms")       # 2525.0 ms -- fiction
print(f"true fleet p99   = {true_} ms")       # 50 ms     -- reality
# 50x overstatement. No weighting scheme repairs this; only merging the data does.
```

**The mergeable version — a fixed-bucket histogram**, which is what you would actually deploy. Note that the exported state is a vector of integers, so two servers' exports can simply be added.

```python
class Histogram:
    # Boundaries are a design decision: they must straddle your SLO threshold,
    # or the percentile near the SLO will be a wide guess.
    BOUNDS = [5, 10, 25, 50, 100, 200, 300, 500, 1000, 2500, 5000, 10000]

    def __init__(self):
        self.counts = [0] * (len(self.BOUNDS) + 1)   # +1 for the overflow bucket
        self.total = 0

    def record(self, ms):                 # O(log n); real impls are branch-free
        self.total += 1
        for i, b in enumerate(self.BOUNDS):
            if ms <= b:
                self.counts[i] += 1
                return
        self.counts[-1] += 1              # slower than 10s

    def merge(self, other):               # <-- THE property that percentiles lack
        self.counts = [a + b for a, b in zip(self.counts, other.counts)]
        self.total += other.total

    def quantile(self, p):
        """Returns the UPPER BOUND of the bucket containing the p-th percentile."""
        target, seen = p / 100 * self.total, 0
        for i, c in enumerate(self.counts):
            seen += c
            if seen >= target:
                return self.BOUNDS[i] if i < len(self.BOUNDS) else float("inf")
        return float("inf")

h_a, h_b = Histogram(), Histogram()
for v in server_a: h_a.record(v)
for v in server_b: h_b.record(v)
h_a.merge(h_b)                            # correct fleet aggregation
print(f"merged histogram p99 <= {h_a.quantile(99)} ms")   # 50 ms
```

**Production configuration** — Prometheus, which uses exactly this model:

```yaml
# Application side (Go client library, illustrative):
#   histogram := prometheus.NewHistogramVec(prometheus.HistogramOpts{
#       Name:    "http_request_duration_seconds",
#       Buckets: []float64{.005,.01,.025,.05,.1,.2,.3,.5,1,2.5,5,10},
#       //                                     ^^^ 300ms SLO threshold MUST be a bucket edge
#   }, []string{"route", "method", "status"})

groups:
  - name: booknook-latency
    rules:
      # CORRECT: histogram_quantile merges the *_bucket time series across all
      # instances first, then computes the quantile once. This is section 4.3.
      - record: route:latency_p99:5m
        expr: |
          histogram_quantile(0.99,
            sum by (route, le) (rate(http_request_duration_seconds_bucket[5m])))

      # WRONG, shown only so you recognise it in someone else's repo:
      #   avg by (route) (route:latency_p99:5m)        # averaging percentiles
      #   max by (route) (route:latency_p99:5m)        # at least it's an upper bound

      # SLO as a PROPORTION OF GOOD EVENTS -- mergeable over hosts AND over time,
      # which "p99 < 0.3" is not. Prefer this phrasing always (section 4.6).
      - record: route:slo_good_ratio:28d
        expr: |
          sum by (route) (rate(http_request_duration_seconds_bucket{le="0.3"}[28d]))
          /
          sum by (route) (rate(http_request_duration_seconds_count[28d]))

      - alert: LatencySLOBurningFast
        # 14.4x burn rate over 1h = the entire 28-day budget gone in ~2 days.
        expr: (1 - route:slo_good_ratio:28d) > (14.4 * 0.01)
        for: 5m
        labels:   { severity: page }
        annotations:
          summary: "{{ $labels.route }} is burning its 28-day latency error budget 14x too fast"
```

**Why this is not production-ready:** the Python `Histogram` is not thread-safe, never resets, has hard-coded bucket boundaries unsuitable for sub-millisecond work, loses all precision inside a bucket, and silently lumps everything above 10 s together. Real deployments use HDR Histogram, `t-digest`, or the client libraries of Prometheus/OpenTelemetry, which handle concurrency, bounded error, exemplars, and overflow correctly. The naive `percentile()` function is `O(n log n)` in time and `O(n)` in memory per window — at 10,000 RPS it would allocate 600,000 floats per minute per endpoint.

## 9. Numerical example

**Scenario.** BookNook's product page, the full journey, with a fan-out search component. Every assumption is stated.

**Assumptions**

| Assumption | Value | Justification |
|---|---|---|
| Daily active users | 10,000 | Lesson 1 baseline |
| Page views per active user per day | 12 | Typical browse session |
| API requests per page view | 8 | HTML + CSS + JS + 5 API calls (§4.5) |
| Per-request latency: p50 / p95 / p99 / p99.9 | 40 / 150 / 600 / 2,800 ms | Measured at the load balancer |
| Search fan-out | 20 shards | Current index layout |
| Per-shard p99 | 90 ms | Measured |
| Hedge threshold | 150 ms (= p95) | §4.10 |
| Shard capacity | 1,400 RPS; current 1,000 RPS | Capacity plan |
| SLO | 99 % of API requests < 300 ms over 28 days | §4.6 |

**Step 1 — daily request volume.**

```
Page views/day      = 10,000 users × 12 pages      = 120,000 page views
API requests/day    = 120,000 pages × 8 requests   = 960,000 requests
Requests per 28 days = 960,000 × 28                = 26,880,000 requests
```

**Step 2 — how many users hit the tail, per request and per page.**

```
Per-request slow rate (worse than p99)  = 1 %
Slow requests per day = 960,000 × 0.01 = 9,600 requests/day

Per-page probability of at least one slow request:
  P(all 8 fast)      = 0.99^8
                     = 0.96059601^2
                     = 0.922744
  P(page is slow)    = 1 − 0.922744 = 0.077256 = 7.73 %

Slow page views per day = 120,000 × 0.0773 = 9,276 page views/day
```

**Step 3 — how many *users* that is.**

```
Pages per user per day = 12
P(a user sees NO slow page all day) = 0.922744^12
  ln = 12 × ln(0.922744) = 12 × (−0.080384) = −0.96461
  e^(−0.96461) = 0.3812
P(a user sees at least one slow page) = 1 − 0.3812 = 61.9 %

Affected users = 10,000 × 0.619 ≈ 6,190 users every single day
```

**62 % of your daily actives hit a ≥600 ms page every day, from a "1 % slow" API.** That sentence is the entire lesson in one line.

**Step 4 — the per-request percentile needed for a 99 % *page* SLO.**

```
Need x^8 = 0.99
x = 0.99^(1/8) = e^(ln(0.99)/8) = e^(−0.0100503/8) = e^(−0.00125629) = 0.998744
Required per-request good rate = 99.874 %  →  you must engineer to p99.9, not p99.
```

**Step 5 — search fan-out amplification (20 shards, per-shard p99 = 90 ms).**

```
P(all 20 shards fast) = 0.99^20
  ln = 20 × (−0.0100503) = −0.201007
  e^(−0.201007) = 0.81791
P(search hits ≥1 slow shard) = 1 − 0.81791 = 18.2 %

Searches/day (assume 1 search per 4 page views) = 120,000 / 4 = 30,000
Slow searches/day = 30,000 × 0.182 = 5,460
```

**Step 6 — hedging cost/benefit, with capacity checked.**

```
Hedge threshold = p95 = 150 ms → only the slowest 5 % are duplicated.

Extra shard requests = 30,000 searches × 20 shards × 0.05
                     = 30,000 shard-calls duplicated per day

Baseline shard RPS            = 1,000 RPS
Added RPS from hedging        = 1,000 × 0.05 = 50 RPS
New offered load              = 1,050 RPS
New utilisation               = 1,050 / 1,400 = 75.0 %   (was 71.4 %)
Queueing multiplier 1/(1−ρ)   = 1 / 0.25 = 4.0×          (was 3.5×)
```

Utilisation rises 3.6 points, the queueing multiplier worsens by 14 %, and the extreme tail improves ~30×. **Accept.**

Now the mistake, quantified. Hedge at p50 (40 ms) instead:

```
Duplicated fraction = 50 %
Added RPS           = 1,000 × 0.5 = 500 RPS
Offered load        = 1,500 RPS   vs capacity 1,400 RPS
Utilisation         = 107 %  →  queue grows without bound  →  outage
```

**Reject.** The hedge threshold *is* the design: it directly sets the extra load.

**Step 7 — the error budget.**

```
Requests per 28-day window        = 26,880,000
SLO = 99 % under 300 ms
Permitted slow requests (budget)  = 26,880,000 × 0.01 = 268,800
Per day                           = 268,800 / 28 = 9,600 slow requests/day
```

Compare with Step 2: the current slow rate is **exactly 9,600/day**. BookNook is sitting precisely on its budget line with zero margin — any regression immediately breaks the SLO. That is the signal to invest in the tail *now*, and it came from arithmetic rather than opinion.

**Step 8 — sanity check on sample size (§4.2).** To report p99.9 for searches:

```
Searches per day = 30,000 → per hour = 1,250 → per minute ≈ 21
p99.9 needs ≥ 10,000 samples → ≥ 8 hours of search traffic
```

So a 5-minute p99.9 panel for search is **pure noise**. Report p99 at 1 hour and p99.9 at 1 day for this endpoint, and say so on the dashboard.

## 10. Trade-offs

**Advantages of percentile-based measurement**
- Describes experiences that real users actually have; the mean describes a user who does not exist.
- Exposes bimodal behaviour (cache hit vs miss, warm vs cold, small vs large account) that a mean smears into mush.
- Directly expressible as an SLO with an error budget, which converts performance into a spendable currency.
- Detects degradation earlier: tails move *before* error rates do, because a saturating system gets slow before it starts failing (see the Lesson 1 failure table).
- Forces the architectural conversations that matter — fan-out, queueing, retries — because those are what move p99.

**Disadvantages and costs**
- **Storage and CPU.** Histograms multiply your metric cardinality by the bucket count: one latency metric with 12 buckets × 50 routes × 3 methods × 5 status classes = 9,000 time series instead of 2. Cardinality explosions are a real and expensive outage source in monitoring systems.
- **Not mergeable unless you plan for it.** Every team that stored percentiles instead of histograms has to redo its telemetry.
- **Statistically noisy at high percentiles on low traffic** (§4.2). p99.9 on 200 requests/hour is a random number with a confidence interval wider than the value.
- **Bucket boundaries are a commitment.** If your SLO threshold is 300 ms and your buckets jump 250 → 500 ms, your SLO measurement has ±100 % error. Changing boundaries later invalidates historical comparisons.
- **Harder to explain.** "Average response time 83 ms" fits in a board slide; "99 % of requests complete within 300 ms measured at the edge over 28 days" needs a paragraph.
- **Chasing the tail has diminishing returns.** Going p99 → p99.9 often costs more than everything before it (replication, hedging, spare capacity at 50 % utilisation) for a tenth of the users.

**Operational costs**
- You need tail-based sampling in tracing (keep the slow traces, discard the boring ones) — more infrastructure than head-based sampling.
- You need load tests that do not suffer coordinated omission (§4.7), which usually means replacing the tool the team already uses.
- Someone must own bucket boundaries, retention, and cardinality budgets. Without an owner, histogram metrics quietly become the largest line item in your observability bill.

**When percentiles are unnecessary complexity**
- Batch and offline pipelines, where total wall-clock time is the goal (§7, contrasting example).
- Internal tools with 20 users and no latency sensitivity.
- Endpoints whose traffic is too low to populate a percentile — track max and count instead, and be honest that that is what you are doing.

## 11. Failure scenarios

| Failure | What the user sees | How it's detected | Recovery |
|---|---|---|---|
| **One bad host** (failing disk, noisy neighbour, hot cgroup throttle) | ~1/N of requests are 10–50× slower; refreshing "fixes" it | Fleet p99 rises while per-host p50 is flat on all hosts but one; **always break p99 down by instance first** | Load balancer ejects the host on latency-aware health checks (Phase 2, Topic 7); replace the instance |
| **GC pause storm** after a memory-heavy deploy | Random 200 ms–10 s freezes, no errors, no pattern by endpoint | p99 and p99.9 jump while p50 is unchanged; GC-pause metric correlates exactly | Roll back; raise heap or switch collector; cap per-request allocations; split large objects |
| **Utilisation cliff** — traffic grows 15 % and crosses ρ ≈ 0.85 | Site "suddenly" becomes slow with no deploy and no error spike | Latency rises hyperbolically against a linear traffic rise (§4.9); queue depth climbs | Autoscale, shed load, raise capacity; permanently target 50–70 % utilisation |
| **Fan-out amplification** after adding index shards 20 → 100 | Search is slow for a third of users; each shard looks perfectly healthy | Per-shard p99 unchanged, end-to-end p99 tripled — the signature of §4.4 | Reduce N, hedge at p95, or improve per-shard p99.9 by an order of magnitude |
| **Retry amplification** (Lesson 17) | Brief slowness becomes a total outage minutes later | Request count rises while *unique* request count does not; retry ratio spikes | Retry budgets, backoff with jitter, circuit breakers (Phase 4, Topic 14), load shedding |
| **Cold start / cold cache** after deploy or scale-out | The first 30–120 s after each deploy are terrible for a slice of users | p99 spike aligned to deploy markers; cache hit-rate dip | Staged rollout, cache warming, slow-start/ramped load balancing, pre-flight requests |
| **Coordinated omission in the load test** (§4.7) | Nothing in staging; production collapses at a load the test "passed" | Benchmark p99 ≈ p50 but max is 1,000× higher; production disagrees with staging | Switch to an open-loop generator (`wrk2`, k6 constant-arrival-rate); re-baseline capacity |
| **Averaging percentiles on the dashboard** (§4.3) | Users complain about a problem the graph says does not exist | Dashboard p99 disagrees with a manually computed p99 from raw traces | Re-export as histograms; compute `histogram_quantile` over merged buckets |
| **Client-side tail invisible to the server** (slow mobile networks, long TLS handshakes) | 2 s pages for mobile users; server metrics say 60 ms | Only RUM / client beacons reveal it; server telemetry *cannot* see it | Add RUM; optimise connection setup, payload size, CDN placement (Phase 2, Topic 14) |
| **Metric cardinality explosion** from a per-user histogram label | Dashboards stop loading; alerts stop evaluating; the monitoring system falls over | Time-series count graph hockey-sticks; ingestion errors | Drop high-cardinality labels; enforce a cardinality budget in CI |

The connecting thread: **almost every row is invisible in an average.** Rows 1, 2, 4, 6 and 9 can all be in progress while mean latency barely moves.

## 12. Alternatives

| Approach | What it reports | Good at | Bad at | Use when |
|---|---|---|---|---|
| **Mean (average)** | Sum ÷ count | Throughput and cost reasoning (total work = mean × count); trivially mergeable; 2 counters of storage | Describing user experience; destroyed by outliers; hides bimodality | Batch jobs, capacity/cost models, anything where nobody is waiting |
| **Median (p50)** | Middle value | The typical experience; robust to outliers | Says nothing about the 1 % who suffer; can look perfect during an outage affecting 5 % of users | Paired with p99 — never alone |
| **p95 / p99 / p99.9** | Tail values | User-visible worst cases; SLOs; detecting fan-out and queueing problems | Noisy on low traffic; not mergeable as numbers; needs histograms | Any interactive, user-facing service |
| **Max (p100)** | The single worst request | Catching absolute disasters and timeouts | One cosmic-ray sample dominates; never a basis for an SLO; unmergeable *shape* but mergeable value | As a supporting diagnostic only |
| **Histogram / heatmap over time** | The whole distribution per interval | Reveals bimodality, cliffs, and multi-modal behaviour percentiles hide; shows *why* p99 moved | Harder to alert on; needs visual interpretation | Investigation and dashboards; the best single visual for latency |
| **Apdex score** | `(satisfied + tolerating/2) / total` against T and 4T thresholds | One 0–1 number for executives; threshold-based so it *is* mergeable | Collapses detail; the choice of T hides everything; hard to act on | Executive reporting, never engineering diagnosis |
| **Threshold proportion** ("% under 300 ms") | Count of good events ÷ total | Mergeable across hosts *and* time; ideal SLO form; cheap | Doesn't tell you *how* slow the bad ones were | SLOs and error budgets — the recommended default (§4.6) |
| **Distributed tracing (tail-sampled)** | Full causal breakdown of individual slow requests | Explains *why* a request was slow, span by span | Expensive; sampled; not a trend metric | Diagnosing a tail you have already detected with percentiles |
| **RUM (Real User Monitoring)** | Latency as the browser/app experiences it | Includes network, DNS, TLS, rendering — the only honest user number | Survivorship bias (abandoned loads never report); noisy; privacy considerations | Always, alongside server-side metrics |
| **Synthetic monitoring** | Scripted probes from fixed locations | Consistent baseline; works at zero traffic; catches total outages | Does not reflect real user mix or real devices | Availability checks and regional baselines |

The mature answer is **not one of these** — it is: threshold proportions for SLOs, histograms for dashboards, percentiles for conversation, traces for diagnosis, RUM for truth, and the mean only for capacity maths.

## 13. When to use it

1. **Any user-facing interactive endpoint.** If a human is blocked on the response, the tail *is* the experience. BookNook's `/api/search`, `/api/book`, and checkout all qualify.
2. **Any service with fan-out greater than ~5.** §4.4 shows the amplification is mechanical; if you fan out, you must measure and engineer p99.9, not p99.
3. **When defining or defending an SLO or SLA.** Contracts written on averages are unenforceable and meaningless; always negotiate threshold-plus-proportion-plus-window.
4. **When capacity planning a latency-sensitive tier.** The utilisation table in §4.9 is a percentile argument: you buy headroom specifically to keep the tail small, and you must be able to show the curve to justify the spend.
5. **When diagnosing "it's slow sometimes" reports.** Intermittent complaints with healthy averages is the exact fingerprint of a tail problem; the per-instance and per-route percentile breakdown is the first diagnostic.
6. **Before and after any performance work.** The before/after comparison must be on the same percentile. Averages will often show a performance win as a regression (or vice versa), as in §7.
7. **When the system is multi-tenant.** Per-tenant percentiles find the one customer whose data shape causes pathological queries, long before they churn.

## 14. When *not* to use it

1. **Batch and offline pipelines.** Total wall-clock completion time is the goal; the mean multiplied by the count gives it exactly, and per-item tails are irrelevant (§7).
2. **Very low-traffic endpoints.** Below ~1,000 requests per window, a p99 is one or two samples — a random number dressed as a statistic. Report count, max, and a threshold proportion instead, and label the dashboard honestly.
3. **Pure throughput or cost questions.** "How many cores do we need?" and "what does a request cost?" are mean-based questions; `total work = mean × count` is the right model and percentiles cannot answer them.
4. **Fire-and-forget asynchronous work** where the client never waits (Lesson 13) — event ingestion, log shipping, analytics beacons. Measure *end-to-end queue lag* and backlog depth instead; request latency there is a meaningless number about a write to a buffer.
5. **As the only metric.** A p99 without an error rate, a traffic count, and a saturation signal is a trap: the fastest possible p99 is achieved by failing every request in 2 ms. Percentiles must always be read next to the other three of the four golden signals (latency, traffic, errors, saturation).

## 15. Common mistakes

**Beginner mistakes**

- **Reporting the average and stopping.** The default dashboard of almost every tool. Always ask for p99.
- **Averaging or summing percentiles** across hosts, time windows, or call chains (§4.3). If you see `avg(p99)` anywhere, it is wrong.
- **Quoting p99.9 from 300 samples.** You need ~10,000 (§4.2). Picking a high percentile does not create information that is not in the data.
- **Measuring latency only on the server.** The user's latency includes DNS, TCP, TLS, network, and rendering — often 3–5× the server number (§5, step 13).
- **Using a closed-loop load tester** (`ab`, plain `wrk`, a naive script) and believing its p99 (§4.7).
- **Excluding failed requests from the latency metric.** Timeouts are your *slowest* requests; dropping them is how a system shows improving latency while getting worse. Decide the rule explicitly and write it in the SLI.
- **Putting the SLO threshold between two histogram buckets**, so the SLO can only be measured to ±100 %.
- **Confusing percentile with percentage.** "p99 = 600 ms" does not mean "99 % slower"; it means 1 % of requests exceeded 600 ms.
- **Adding retries to fix slowness** without a budget, converting a tail problem into an outage (Lesson 17).

**Experienced-engineer mistakes**

- **Optimising p99 while ignoring the journey.** A 1 % per-request slow rate is a 7.7 % per-page slow rate and a 62 % per-user-per-day slow rate (§9). Measure what the user actually does.
- **Hedging without spare capacity, without idempotency, or with correlated replicas.** Each of these turns the technique into an amplifier (§4.10).
- **Running latency-sensitive services at 85–95 % utilisation** to save money, then being surprised by a 20× latency multiplier (§4.9). The savings are real; the outage is more expensive.
- **Setting an SLO you cannot violate.** A 99.99 % target on a system that trivially achieves it generates no information and no budget to spend on velocity. An SLO should be *uncomfortably* close to reality.
- **Ignoring mean/p50 ratio as a cheap bimodality detector**, then being blindsided by cache-hit/cache-miss bimodality that no single percentile captures. Look at heatmaps.
- **Alerting on raw p99 thresholds** instead of SLO burn rate, producing pages at 03:00 for a 2-minute blip that cost 0.01 % of the monthly budget.
- **Letting histogram cardinality grow unbounded** (per-user, per-ISBN, per-trace-id labels) and taking down the monitoring system during the incident that needed it.
- **Treating the tail as a code problem.** It is usually a queueing, GC, scheduling, or fan-out problem (§4.8). Profilers show CPU time; tail requests are usually *not running*.
- **Comparing p99 across a change that altered the traffic mix.** If a cache warmed, a bot left, or a client upgraded, you are comparing different populations, not different code.

## 16. Interview perspective

Percentiles are a **credibility signal**. Interviewers use them to separate people who have operated a system from people who have read about one.

- **Never say "average latency" in a design interview.** Say "p99" or "99 % of requests under X". It costs nothing and changes the interviewer's model of you immediately.
- **Volunteer the fan-out calculation unprompted.** "This search fans out to 50 shards, so at a per-shard p99 of 100 ms, roughly 40 % of searches touch a slow shard — `1 − 0.99^50 = 39.5 %`. I'd hedge after p95 and cut fan-out." Doing that arithmetic out loud is one of the highest-signal 20 seconds available to you.
- **Name the utilisation cliff when asked about capacity.** "I'd run at 60–70 % because `1/(1−ρ)` means 90 % utilisation is a 10× latency multiplier." This reframes capacity from cost to latency.
- **Propose an SLO with all three parts**, plus a measurement point: "99 % of `GET /books` under 300 ms over 28 days, measured at the edge, excluding bot traffic, counting timeouts as failures."
- **Know why percentiles do not average.** This is a common follow-up and most candidates fail it. Answer with the unequal-traffic example from §4.3, then say "so we export histograms and merge buckets".
- **Mention coordinated omission if load testing comes up.** Very few candidates know the term; those who do are visibly a tier above.
- **Connect tail to money.** "p99 latency is correlated with the biggest accounts because they have the most data, so the tail is disproportionately revenue." Interviewers at product companies love this.
- **Scope the effort.** "p99 is worth a lot of engineering; p99.99 usually is not, unless we're on a fan-out path where it becomes everyone's p99." Showing you know when to *stop* is as valuable as knowing how to improve.

**Red flags interviewers watch for:** quoting only averages; saying "p99 of 100 ms plus p99 of 100 ms is 200 ms"; proposing retries or hedging without mentioning idempotency, capacity, or budgets; claiming a system is fine at 95 % CPU; computing p99.9 from a handful of samples; not knowing where latency is measured; treating a tail as a profiling problem; and being unable to say what *causes* tails beyond "slow code".

## 17. Knowledge check

Answer these before reading on. Write your answers down — even one line each.

**Conceptual**

1. A dashboard shows `avg latency = 45 ms` and `p50 = 40 ms` for service A, and `avg latency = 45 ms` and `p50 = 12 ms` for service B. Both have the same average. What do you immediately know about service B that you do not know about service A, what single extra statistic would you request, and what two causes would you investigate first?

2. Your team exports `p99_latency` as a gauge from each of 30 app servers and the dashboard shows `avg(p99_latency)`. Explain concretely — with a two-server example of your own construction — how this number can be *both* far too high and far too low, and describe exactly what you would export instead and why that export is mergeable.

3. A load test reports `p50 = 2 ms, p99 = 3 ms, max = 4,100 ms` at 5,000 RPS, and the team signs off on capacity. Name the measurement defect, explain the mechanism that produces this exact signature, and describe two changes to the test methodology that would reveal the truth.

**Practical scenario**

4. BookNook's recommendations widget fans out to 25 personalisation shards and waits for all of them. Per-shard p99 = 120 ms, p50 = 8 ms; the widget's end-to-end p99 is 140 ms and users complain that recommendations "often stall". The service runs at 1,100 RPS against a measured capacity of 1,300 RPS. Produce: (a) the probability that a widget render touches at least one p99-class shard, showing the arithmetic; (b) three distinct fixes ranked by cost-to-implement; (c) for the hedging option specifically, the threshold you would choose, the resulting extra RPS, the new utilisation, the new queueing multiplier `1/(1−ρ)`, and whether you would ship it; (d) one precondition that, if false, makes hedging actively harmful here.

**Calculation**

5. BookNook's checkout flow is 6 sequential API requests. Per-request measurements: p50 = 55 ms, p95 = 210 ms, p99 = 900 ms. Checkout traffic is 4,000 checkouts/day; the business says a checkout is "slow" if any of its 6 requests exceeds the p99 threshold. Assume request latencies are independent.
   (a) What fraction of checkouts are slow? Show `0.99^6` and the subtraction.
   (b) How many slow checkouts is that per day, and per 28-day window?
   (c) The team wants ≤ 1 % of *checkouts* to be slow. What per-request good-rate percentile is required? Show the `n`-th-root calculation.
   (d) The service runs at ρ = 0.88 utilisation. Using `W = S/(1−ρ)` with a service time `S` of 20 ms, compute the current average time in system, then the value at ρ = 0.65, and state how much extra capacity (as a percentage of current capacity) is needed to reach ρ = 0.65 at the same traffic.
   (e) At 4,000 checkouts/day × 6 requests, is there enough data to report a trustworthy p99.9 per hour? Show the sample-count reasoning.

Reply with your answers and I'll assess them, correct anything shaky, and give model answers before Lesson 17.

---

**Progress checklist**

Phase 1:
- `[x] 1. Client-server architecture`
- `[x] 2. Stateful versus stateless servers`
- `[x] 3. IP addresses, ports, sockets, domain names`
- `[x] 4. DNS resolution and DNS caching`
- `[x] 5. HTTP requests and responses`
- `[x] 6. HTTP methods, headers, cookies, sessions, status codes`
- `[x] 7. HTTPS and TLS`
- `[x] 8. TCP versus UDP`
- `[x] 9. HTTP/1.1 vs HTTP/2 vs HTTP/3`
- `[x] 10. REST APIs`
- `[x] 11. RPC and gRPC`
- `[x] 12. GraphQL`
- `[x] 13. Synchronous versus asynchronous communication`
- `[x] 14. Latency, throughput, bandwidth, concurrency`
- `[x] 15. Availability, reliability, durability, fault tolerance`
- `[x] 16. Average latency vs p50/p95/p99, tail latency`
- `[ ] 17. Idempotency, retries, timeouts, exponential backoff`

**NEXT LESSON: Phase 1, Topic 17 — Idempotency, retries, timeouts, and exponential backoff**

---

# Lesson 17 — Idempotency, Retries, Timeouts, and Exponential Backoff

*Phase 1, Topic 17*

## 1. Simple definition

Four definitions, each precise.

- **Idempotent** — an operation is idempotent if performing it **two or more times has exactly the same effect on system state as performing it once**. "Set the thermostat to 21 °C" is idempotent; "turn the thermostat up by 1 °C" is not. The word comes from mathematics: `f(f(x)) = f(x)`.
- **Timeout** — a deadline after which a caller gives up waiting for a response and treats the call as failed. Note the crucial word: *treats*. The work may still be happening.
- **Retry** — sending the same request again after a failure, in the hope that the failure was transient.
- **Exponential backoff** — waiting longer before each successive retry, with the wait roughly doubling each time, so that a struggling server gets a decreasing rate of retries rather than a constant hammering.

These four are a single topic because they only work **as a set**:

> **Retries without idempotency create duplicates. Retries without timeouts never happen. Retries without backoff create outages. Timeouts without retries turn blips into errors.**

Memorise that sentence; it is the lesson.

## 2. Why it exists

Because of one brutal fact introduced in Lesson 1 and deferred ever since:

> **When a network call fails, the caller cannot tell whether the work happened.**

A timeout, a connection reset, or a 503 tells you *you did not receive a response*. It does not tell you *the server did not act*. Four genuinely different realities produce an identical observation at the client:

| What really happened | Server state afterwards | What the client observed |
|---|---|---|
| Request never arrived (lost in the network) | Unchanged | Timeout |
| Request arrived; server crashed before doing the work | Unchanged | Connection reset / timeout |
| Request arrived; server **completed the work**; response lost on the way back | **Changed** | Timeout |
| Request arrived; server completed the work; response sent; **client** crashed/timed out early | **Changed** | Timeout |

Rows 1–2 and rows 3–4 demand opposite actions — retry versus do-not-retry — and the client cannot distinguish them. This is sometimes called the **Two Generals Problem**: no number of messages over an unreliable channel can give both parties certainty about the other's state.

Since certainty is impossible, engineering offers exactly two escapes:

1. **Make retrying safe** (idempotency), so you can retry without caring which reality occurred. This is the good escape.
2. **Never retry**, and accept lost work. This is correct for some operations and catastrophic for others.

Everything in this lesson follows from that. The failure rates make it non-negotiable: at BookNook's modest 300,000 requests/day, a transient-failure rate of just 0.1 % is **300 ambiguous outcomes every day**. Scale that to a microservice mesh where one user action triggers 20 internal calls and you have thousands of daily opportunities to double-charge a customer.

## 3. Real-world analogy

**Posting a cheque that might have been cashed.**

You post a signed cheque for ₹5,000 to your landlord. Two weeks pass with no acknowledgement. Did the envelope get lost, or did it arrive and the landlord simply hasn't told you?

- If you **do nothing** (no retry), you may be in arrears through no fault of your own.
- If you **send another cheque** (blind retry), you may pay ₹10,000.
- If you write **"Rent for March 2026 — reference R-2026-03"** on the cheque and the landlord's bookkeeping rejects a second cheque bearing a reference already recorded, you can safely send as many cheques as you like. **Exactly one** will be banked.

That reference string is an **idempotency key**, and the landlord's ledger check is the server-side deduplication store. The entire mechanism of §4.4 is in that one paragraph.

Extend it: you decide "if I hear nothing in 7 days, I resend" — that is a **timeout**. You also decide not to resend daily, because 30 cheques in a week makes the landlord's bookkeeper slower, not faster, and a confused bookkeeper is why you got no reply in the first place — that is **backoff**. And if every tenant in the building resends on the same day of the month, you get a **retry storm** that buries the bookkeeper entirely.

**Where the analogy breaks down**

| Posting cheques | Real distributed systems |
|---|---|
| Days to notice a failure; you can phone and ask | Milliseconds; there is often no out-of-band channel to ask "did you get it?" |
| One tenant, a handful of cheques | Thousands of clients retrying simultaneously, *correlated* because they all failed at the same instant |
| The bookkeeper's speed is unaffected by your resending | Retries consume the exact resource that is already scarce — **retrying makes the outage worse**, which has no postal equivalent |
| The ledger is one book, checked by one person | The deduplication store is itself distributed; two duplicate requests can hit two servers *at the same microsecond* and both find "no existing key" (the race in §4.4) |
| Cheques are rare; duplicates are embarrassing but fixable | At scale, duplicates are continuous; "we'll reconcile manually" does not survive 10,000 per day |
| You eventually get certainty when the cheque clears | You may *never* get certainty; you get a probability and a reconciliation process |

The third row is the one that turns this from an annoyance into an outage: in the postal system, retries are free for the recipient. In a distributed system, **retries are a load multiplier applied precisely when the system has no spare capacity**.

## 4. Detailed technical explanation

### 4.1 Safe versus idempotent versus neither

Two independent properties, often confused.

- **Safe** — the operation does not modify observable server state at all. It is read-only. (Logging, metrics, and cache warming do not count as "modifying state" for this purpose; the resource representation is unchanged.)
- **Idempotent** — repeating the operation leaves state the same as doing it once. The operation *may* modify state; it just must not modify it *differently* on repetition.

Every safe operation is idempotent (changing nothing N times changes nothing). The reverse is false.

| Property | Modifies state? | Repeat = once? | Example |
|---|---|---|---|
| Safe **and** idempotent | No | Yes | `GET /books/123` |
| Idempotent, **not** safe | Yes | Yes | `PUT /cart/items/123 {"qty": 2}` — qty is 2 no matter how many times you send it |
| Neither | Yes | No | `POST /orders` — each call creates a new order |

**HTTP methods, per RFC 9110:**

| Method | Safe | Idempotent | Why |
|---|---|---|---|
| `GET` | ✅ | ✅ | Pure read |
| `HEAD` | ✅ | ✅ | `GET` without a body |
| `OPTIONS` | ✅ | ✅ | Capability discovery |
| `TRACE` | ✅ | ✅ | Diagnostic echo |
| `PUT` | ❌ | ✅ | **Replaces** the resource with the supplied representation. Sending it 5 times leaves the same final state. |
| `DELETE` | ❌ | ✅ | After the first call the resource is gone; subsequent calls leave it gone. |
| `POST` | ❌ | ❌ | "Process this payload according to the resource's own semantics" — typically *create a new subordinate resource*. |
| `PATCH` | ❌ | ❌ (not guaranteed) | Depends entirely on the patch document. `{"op":"replace","path":"/qty","value":2}` is idempotent; `{"op":"add"}` to an array is not. |

**Why `POST` specifically is not idempotent — the mechanism, not the rule.** In `PUT /books/1492052205`, the *client* chose the resource identifier. The server's job is "make the resource at this URL look like this body". Repeat it and the server overwrites the same slot. In `POST /orders`, the client has *no identifier to give*; it is asking the server to mint one. The server's correct behaviour is to create a new order with a new ID every time. **Two identical POST bodies are, by specification, two distinct creation requests** — and the server has no way to know that the second one is a retry rather than a customer genuinely buying the same book twice. That missing information is exactly what an idempotency key supplies.

**Two subtleties that trip people up:**

1. **Idempotency is about *state*, not about the *response*.** `DELETE /orders/42` returns `204 No Content` the first time and `404 Not Found` the second. The responses differ; the *state* is identical. It is still idempotent. (Many APIs choose to return `204` both times for a friendlier retry story; both are legal.)
2. **Idempotent ≠ concurrency-safe.** Two *different* clients sending `PUT /books/123 {"price": 300}` and `PUT /books/123 {"price": 400}` simultaneously still race; idempotency says nothing about interleaving. That needs optimistic concurrency control (`If-Match` with an ETag, or a version column) — see §4.9.

Common operations classified:

| Operation | Idempotent? | How to fix if not |
|---|---|---|
| `SELECT * FROM books WHERE id = 1` | ✅ | — |
| `UPDATE books SET price = 300 WHERE id = 1` | ✅ (absolute assignment) | — |
| `UPDATE accounts SET balance = balance - 100 WHERE id = 7` | ❌ (relative assignment) | Ledger row with a unique transaction ID (§4.9) |
| `INSERT INTO orders (...) VALUES (...)` | ❌ | Unique constraint + `ON CONFLICT DO NOTHING` (§4.9) |
| `INSERT ... ON CONFLICT DO NOTHING` | ✅ | — |
| Send an email / SMS | ❌ (two emails) | Dedup key in the send pipeline; accept rare duplicates |
| Charge a credit card | ❌ (double charge) | Idempotency key — this is why Stripe pioneered them |
| Publish to Kafka without a producer ID | ❌ | Idempotent producer / transactional producer (Phase 4, Topic 10) |
| Increment a counter in Redis (`INCR`) | ❌ | `SET` an absolute value, or deduplicate by request ID |
| Append to an append-only log | ❌ | Include a sequence number the log enforces |
| `DELETE FROM carts WHERE user_id = 7` | ✅ | — |

### 4.2 The fundamental ambiguity, restated operationally

When a call fails, you must classify it before deciding to retry.

| Signal | Did the server definitely *not* act? | Retry? |
|---|---|---|
| **Connection refused** / DNS failure / TLS handshake failure | Yes — no request bytes were processed | **Safe to retry**, even for non-idempotent operations |
| `429 Too Many Requests`, `503 Service Unavailable` with `Retry-After` | Yes — explicitly rejected before processing | **Safe to retry**, after the indicated delay |
| **Read timeout** (request sent, no response) | **Unknown** | Only with idempotency |
| **Connection reset mid-response** | **Unknown** | Only with idempotency |
| `502 Bad Gateway` / `504 Gateway Timeout` | **Unknown** — the proxy could not reach the origin *or* could not relay the reply | Only with idempotency |
| `500 Internal Server Error` | **Unknown** — may have partially committed | Only with idempotency, and only if you trust the server's transactionality |
| `400`, `401`, `403`, `404`, `409`, `422` | The request is wrong; it will be wrong again | **Never retry** — this is a bug, not a blip |

**The design rule that follows:** build your system so that the "unknown" rows are *also* safe. That is what idempotency keys are for.

### 4.3 At-most-once, at-least-once, exactly-once

Three delivery semantics. They apply to messaging and to RPC alike.

| Semantics | Rule | Duplicates? | Lost work? | How you get it |
|---|---|---|---|---|
| **At-most-once** | Send once; never retry | Never | **Possible** | Fire-and-forget. Trivial to implement. |
| **At-least-once** | Keep retrying until acknowledged | **Possible** | Never | Retries + durable outbox/queue. The practical default. |
| **Exactly-once** | Each effect applied once, no loss | Never | Never | Not achievable at the transport layer |

**"Exactly-once delivery" is impossible; "exactly-once *processing*" is achievable.** The distinction is the whole trick:

- You cannot guarantee a message is *delivered* exactly once, because the sender can never learn whether a lost acknowledgement meant "not delivered" or "delivered, ack lost" (§2). Any protocol that claims otherwise is either retrying (at-least-once) or dropping (at-most-once).
- You *can* guarantee the message's *effect* is applied exactly once, by combining **at-least-once delivery** with **idempotent processing** (a deduplication key, or an operation that is naturally idempotent).

```
at-least-once delivery  +  idempotent processing  =  effectively-exactly-once
```

Every system marketed as "exactly-once" — Kafka transactions, Flink checkpointing, Stripe's API — is this formula with the deduplication hidden inside. **Delivery guarantees, consumer offsets, transactional outboxes, and Kafka's idempotent producer are covered in depth in Phase 4, Topic 10 — Message delivery guarantees and exactly-once semantics.** For Phase 1 you need only the formula and the reason the third column of the table above is unreachable alone.

### 4.4 Idempotency keys, end to end

The industry-standard mechanism, popularised by Stripe and now in PayPal, Adyen, Square, AWS, and the IETF draft `Idempotency-Key` header.

**The protocol, step by step:**

1. The **client** generates a unique key *before* the first attempt — a UUIDv4 is standard — and sends it as a header: `Idempotency-Key: 7f3c9b1e-8f52-4c0a-9f2d-5b8a1c6e4d33`.
2. The client **reuses the same key for every retry** of that same logical operation. This is the part implementations get wrong: generating a new key in the retry loop defeats the whole mechanism.
3. The **server** attempts to claim the key atomically. If the claim succeeds, this is the first attempt: do the work, then store `key → (status, response body, created resource id)`.
4. If the claim **fails because the key already exists**:
   - If the stored record is `completed`, **return the stored response verbatim** — same status code, same body, same resource ID — without re-executing anything.
   - If the stored record is `in_progress`, the original is still running: return `409 Conflict` (or `425 Too Early`) and let the client retry after a short backoff. Do **not** execute concurrently.
5. Keys **expire** (24 hours is typical). They are a retry-safety mechanism, not permanent storage.
6. The server also stores a **request fingerprint** (a hash of the body) and returns `422` if the same key arrives with a *different* body — that indicates a client bug, not a retry, and silently returning the old response would hide it.

**The table.** Every column exists for a reason, stated in the comment:

```sql
CREATE TABLE idempotency_keys (
    -- The client-generated key. PRIMARY KEY gives us the unique index that makes
    -- the concurrent-duplicate race (below) resolvable by the database.
    key                  TEXT        PRIMARY KEY,

    -- Scope the key to a user so one tenant cannot guess/collide with another's key,
    -- and so a leaked key cannot be used to read someone else's stored response.
    user_id              BIGINT      NOT NULL,

    -- Scope to an endpoint: the same key on /orders and /refunds must not collide.
    endpoint             TEXT        NOT NULL,

    -- SHA-256 of the canonicalised request body. If a replay arrives with the same
    -- key but a different fingerprint, that is a CLIENT BUG -> return 422, never 200.
    request_fingerprint  TEXT        NOT NULL,

    -- 'in_progress' = a request holding this key is running right now.
    -- 'completed'   = we have a stored response to replay.
    state                TEXT        NOT NULL
                                     CHECK (state IN ('in_progress','completed')),

    -- The stored response, replayed byte-for-byte on a duplicate.
    response_status      INT,
    response_body        JSONB,

    -- Denormalised pointer to what was created; handy for reconciliation.
    resource_id          TEXT,

    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Set when state becomes 'in_progress'. If a worker crashes mid-request the row
    -- would be stuck forever; a sweeper reclaims rows whose lock is older than ~60s.
    locked_at            TIMESTAMPTZ,

    -- Keys are a retry-window mechanism, not an audit log. Purge after 24h.
    expires_at           TIMESTAMPTZ NOT NULL DEFAULT now() + INTERVAL '24 hours'
);

-- Enforce scoping: the same key string is only "the same request" within one
-- (user, endpoint) pair.
CREATE UNIQUE INDEX idx_idem_scope
    ON idempotency_keys (user_id, endpoint, key);

-- Cheap purge of expired keys.
CREATE INDEX idx_idem_expiry ON idempotency_keys (expires_at);

-- SECOND LINE OF DEFENCE, and the one that actually guarantees correctness:
-- even if the idempotency layer is bypassed or buggy, the orders table itself
-- refuses to hold two rows for one key.
ALTER TABLE orders
    ADD COLUMN idempotency_key TEXT;
CREATE UNIQUE INDEX idx_orders_idem
    ON orders (idempotency_key) WHERE idempotency_key IS NOT NULL;
```

**The concurrent-duplicate race.** This is the part that naive implementations get wrong, and it is worth being very explicit about.

A client times out at 500 ms and retries immediately. The original request is *still running*. Both requests are now in flight, on two different app servers, at the same millisecond. A naive implementation does:

```
if SELECT * FROM idempotency_keys WHERE key = $1  →  not found
then  do_the_work()                                   ← BOTH requests reach here
      INSERT INTO idempotency_keys ...
```

Both `SELECT`s return "not found" because neither has inserted yet. **Two orders are created.** The check-then-act pattern is a race by construction; no amount of making the window smaller fixes it.

The correct implementation lets the **database's unique index** be the arbiter, because that is the one component that can decide atomically:

```python
# PSEUDOCODE -- illustrative, NOT production code. Error handling, observability,
# retry classification and lock reclamation are all elided.

def handle_create_order(user_id, key, body):
    fingerprint = sha256(canonicalise(body))

    # ---- STEP 1: CLAIM THE KEY ATOMICALLY -------------------------------------
    # One statement. The unique index decides the winner. There is no window
    # between "check" and "act" because there is no separate check.
    claimed = db.execute("""
        INSERT INTO idempotency_keys
              (key, user_id, endpoint, request_fingerprint, state, locked_at)
        VALUES ($1,  $2,      '/orders', $3,                'in_progress', now())
        ON CONFLICT (key) DO NOTHING
        RETURNING key
    """, key, user_id, fingerprint)

    if claimed is None:
        # ---- STEP 2: WE LOST THE RACE (or this is a later replay) -------------
        row = db.query_one(
            "SELECT * FROM idempotency_keys WHERE key = $1", key)

        if row.user_id != user_id or row.endpoint != '/orders':
            return 404, {"error": "unknown key"}      # never leak another tenant's data

        if row.request_fingerprint != fingerprint:
            # Same key, different body => the client is buggy. Surfacing this is
            # far better than silently returning a response for a different request.
            return 422, {"error": "idempotency key reused with a different payload"}

        if row.state == 'completed':
            # THE REPLAY PATH: return the stored response verbatim. No work is done.
            return row.response_status, row.response_body

        # state == 'in_progress': the original attempt is STILL RUNNING somewhere.
        # Do NOT execute. Do NOT block holding a worker. Tell the client to come back.
        return 409, {"error": "request in progress", "retry_after_ms": 200}

    # ---- STEP 3: WE WON THE RACE -- do the work exactly once ------------------
    try:
        with db.transaction():
            # The order INSERT carries the key, so the orders unique index is a
            # second, independent guarantee even if this whole layer is bypassed.
            order = db.execute("""
                INSERT INTO orders (user_id, isbn, quantity, amount_paise,
                                    status, idempotency_key)
                VALUES ($1, $2, $3, $4, 'pending', $5)
                ON CONFLICT (idempotency_key) DO NOTHING
                RETURNING id
            """, user_id, body.isbn, body.quantity, price_of(body), key)

            # Decrement stock in the SAME transaction, conditionally -- this is
            # what makes the write both atomic and safe to re-run (section 4.9).
            db.execute("""
                UPDATE books SET stock = stock - $2
                 WHERE isbn = $1 AND stock >= $2
            """, body.isbn, body.quantity)

            response = {"order_id": order.id, "status": "pending"}

            # Store the response IN THE SAME TRANSACTION as the work. If this
            # commit fails, no order exists AND no key is marked completed --
            # the two can never disagree.
            db.execute("""
                UPDATE idempotency_keys
                   SET state = 'completed', response_status = 201,
                       response_body = $2, resource_id = $3
                 WHERE key = $1
            """, key, response, order.id)

        return 201, response

    except Exception:
        # Release the claim so an honest retry can proceed. If the process dies
        # before reaching here, the locked_at sweeper reclaims the row after 60s.
        db.execute("DELETE FROM idempotency_keys WHERE key = $1 AND state = 'in_progress'", key)
        raise
```

**The three properties that make this correct:**

1. **Claiming and checking are a single atomic statement** (`INSERT ... ON CONFLICT DO NOTHING`), so there is no check-then-act window.
2. **The work and the stored response commit in one transaction**, so you can never have an order without a completed key, or a completed key without an order.
3. **A second unique index on `orders.idempotency_key`** means that even a catastrophic bug in layer 1 cannot produce two orders.

**Where should the key store live?** Postgres (as above) is simplest and gives you the transaction for free. Redis with `SET key value NX PX 86400000` is faster but puts the key store in a *different* system from the work, so property 2 is lost — a crash between the Redis write and the database commit leaves them inconsistent. Use Redis only when the work is not in a database you can share a transaction with, and accept the reconciliation burden.

### 4.5 Timeouts: the kinds, and how to pick a number

There is no such thing as "the timeout". There are at least four, and conflating them is a classic production bug.

| Timeout | Clock starts | Clock stops | Typical value | What it protects against |
|---|---|---|---|---|
| **Connect timeout** | `connect()` called | TCP handshake completes | 100–1,000 ms (in-region: 100–250 ms) | Dead host, full backlog queue, blackholed route. Should be *small* — a healthy in-region handshake is ~1 ms. |
| **TLS handshake timeout** | TCP connected | TLS finished | 1–3 s | Broken certificate chain, slow OCSP, hung handshake |
| **Read / socket / idle timeout** | Request fully written | Next byte received (resets on every byte) | 1–10 s | A peer that accepts your bytes and then goes silent |
| **Total / overall / request deadline** | Request initiated | Full response received | Derived from p99 (below) | Everything, including a server that trickles bytes to defeat the idle timeout |

**Critical subtlety:** an *idle* read timeout does **not** bound total time. A server sending one byte every 9 seconds keeps a 10-second idle timeout alive forever. You need a **total deadline** as well. Many HTTP clients (Go's `http.Client.Timeout`, Python `httpx` `Timeout(connect=, read=, write=, pool=)`) expose both; many developers set only one.

**Picking the total timeout from the latency distribution** — this is where Lesson 16 pays off directly:

```
Guideline:  timeout ≈ p99.9 of the dependency, rounded up,
            and never less than ~3× p99.
```

Why this shape?

- **Too short** and you time out requests that would have succeeded. Each one becomes a retry, which *adds* load (§4.7). Setting the timeout at p95 means you deliberately fail and re-issue 5 % of all traffic — a 5 % permanent load tax for nothing.
- **Too long** and you hold a worker, a connection, and possibly a database transaction for the whole duration. By Little's Law (`L = λ × W`), a longer `W` means more concurrent requests in flight for the same arrival rate.
- **No timeout** is the worst of all (below).

Worked: BookNook's inventory service has p50 = 12 ms, p99 = 180 ms, p99.9 = 450 ms.

```
Candidate timeout = max(p99.9 rounded up, 3 × p99)
                  = max(500 ms, 540 ms)
                  = 550 ms   → round to 600 ms

Fraction of normal traffic that will time out ≈ well under 0.1 %
```

**Why "no timeout" is the worst possible default.** Most HTTP client libraries default to *infinite*. Consider BookNook's app tier: 24 servers × 200 workers = 4,800 workers, serving 1,000 RPS, each request normally 50 ms.

```
Little's Law at steady state:  L = λ × W = 1,000 × 0.050 = 50 workers busy
Headroom: 4,800 / 50 = 96×  — looks enormously safe
```

Now a downstream dependency hangs — it accepts connections but never replies. With no timeout, `W → ∞`, so workers are never returned to the pool:

```
Workers consumed per second = 1,000
Time to exhaust 4,800 workers = 4,800 / 1,000 = 4.8 seconds
```

**The entire fleet is dead in under five seconds**, including requests to endpoints that never touch the broken dependency. One hung dependency took down a service with "96× headroom". Nothing recovers it except restarting every process, because nothing ever times out.

With a 600 ms timeout:

```
L = 1,000 × 0.6 = 600 workers busy   (of 4,800)
```

Painful, visible, survivable. **The timeout is what converts an unbounded failure into a bounded one.** This is also the single most common real-world production incident in the entire course.

**Timeout budgets across a call chain.** A chain where each hop independently sets "a reasonable 3 seconds" is broken:

```
Browser  --3s-->  API gateway  --3s-->  Checkout  --3s-->  Inventory  --3s-->  DB
```

The browser gives up at 3 s. But Checkout will happily wait 3 s for Inventory, which waits 3 s for the DB — up to 9+ s of work on a request **nobody is waiting for any more**. Those are *zombie requests*: pure load, zero value, consuming the capacity needed for live traffic. Worse, each hop's own retries multiply inside the dead budget.

The correct model is a **deadline that propagates and shrinks**:

```
t=0ms     Browser sets a 3,000 ms deadline.
t=5ms     Gateway receives it; 2,995 ms remain. Reserves 100 ms to serialise
          the response, forwards deadline = 2,895 ms.
t=20ms    Checkout receives deadline 2,880 ms. It must make 3 calls, so it
          budgets: inventory 600 ms, pricing 400 ms, payment 1,500 ms,
          leaving 380 ms of slack for its own work and retries.
t=25ms    Inventory called with a 600 ms timeout. Returns in 18 ms.
t=45ms    Pricing called with timeout = min(400, remaining 2,855) = 400 ms.
...
t=2,900ms If anything is still outstanding, EVERY hop abandons simultaneously,
          because they all share one deadline rather than nine private ones.
```

Rules for budgets:

1. **Propagate an absolute deadline, not a relative duration.** Send "finish by 14:03:12.500Z" (or remaining-milliseconds recomputed at each hop). gRPC does this natively with the `grpc-timeout` header; HTTP has no standard, so teams use a custom header such as `X-Request-Deadline`.
2. **Each hop subtracts its own elapsed time** before forwarding.
3. **Each hop reserves slack** for its own processing and for a retry, if it intends to retry.
4. **A hop whose remaining budget is already exhausted must fail fast** without calling anything — do not start work you cannot finish.
5. **The sum of children's timeouts must be ≤ the parent's timeout.** If you call three services with 600 ms timeouts each and your own timeout is 1,000 ms, your budget is incoherent.
6. **A retry must fit inside the budget.** 3 attempts × 600 ms = 1,800 ms of timeouts plus backoff; if the parent allows 1,000 ms, you get one attempt.

### 4.6 Which failures to retry

| Response | Retry? | Notes |
|---|---|---|
| Connection refused / DNS failure | **Yes** | The request provably never ran |
| Connect timeout | **Yes** | Likewise — no bytes processed |
| Read timeout | Yes **if idempotent** | The ambiguous case |
| `408 Request Timeout` | Yes | Server says it gave up reading |
| `425 Too Early` | Yes | Replay-protection, retry later |
| `429 Too Many Requests` | **Yes, after `Retry-After`** | Honour the header; ignoring it is how you earn a ban |
| `500 Internal Server Error` | Cautiously, if idempotent | May have partially committed |
| `502` / `503` / `504` | **Yes, with backoff** | Classic transient-infrastructure failures |
| `400`, `422` | **No** | Malformed request — it will fail identically forever |
| `401`, `403` | **No** (retry once *after* refreshing a token, if that is the cause) | |
| `404` | **No** | |
| `409 Conflict` | Only if the server says so | In the idempotency protocol above, a `409` means "in progress" and *is* retryable after a short delay |
| `413`, `414`, `431` | **No** | The request is too big; it will still be too big |
| `501` | **No** | Not implemented is not transient |

**The cardinal rule:** retrying a `400` is not resilience, it is a denial-of-service attack on yourself.

### 4.7 Retry storms and metastable failure

**Retry storm** (also *retry amplification*): a transient slowdown causes timeouts; timeouts cause retries; retries increase offered load; increased load increases latency; increased latency causes more timeouts. A positive feedback loop.

**Metastable failure** — the precise term, from the 2021 HotOS paper by Bronson et al.:

> A system is in a **metastable failure state** when it remains in a severely degraded condition **even after the original trigger has been removed**, because the degradation itself generates the load that sustains it.

The defining test: *you fixed the cause and it is still broken*. That is a metastable failure, and it is the reason major outages last hours instead of minutes.

**Worked example: BookNook checkout turns a brownout into an outage.**

Assumptions, all stated:

| Assumption | Value |
|---|---|
| Baseline checkout traffic | 1,000 RPS |
| Service capacity (max sustainable) | 1,400 RPS |
| Baseline latency | p50 = 60 ms, p99 = 180 ms |
| Client timeout | 500 ms |
| Client retry policy | 3 total attempts, **fixed 100 ms wait, no jitter** |
| Trigger | A database index is accidentally dropped during a migration |
| Effect of trigger | Mean latency 60 ms → 220 ms; capacity falls 1,400 → 1,150 RPS |

**Minute 0 — the brownout.** With the index gone, latency rises. At 1,000 RPS against a now-1,150 RPS capacity, ρ = 0.87, so the queueing multiplier is `1/(1−0.87) = 7.7×` (Lesson 16, §4.9). Roughly **20 %** of requests now exceed the 500 ms timeout.

This alone is survivable: 80 % of checkouts work. A brownout, not an outage.

**Minute 1 — attempt 2 arrives.**

```
Timed-out requests = 1,000 × 0.20 = 200 RPS
These retry → offered load = 1,000 + 200 = 1,200 RPS
Utilisation = 1,200 / 1,150 = 1.043  → OVER CAPACITY
```

The queue now grows without bound. Latency climbs past 500 ms for the majority of requests. The timeout fraction jumps from 20 % to ~50 %.

**Minute 2 — attempt 3 arrives.**

```
Timed-out at attempt 2 = 1,200 × 0.50 = 600 RPS
Offered load = 1,000 + 200 + 600 = 1,800 RPS
Utilisation = 1,800 / 1,150 = 1.57
```

Nearly every request now exceeds 500 ms. Every request consumes all three attempts.

**Steady state — full amplification.**

```
Offered load = 1,000 original × 3 attempts = 3,000 RPS
Amplification factor = 3.0×
Capacity = 1,150 RPS
Offered / capacity = 3,000 / 1,150 = 2.6×
Goodput (useful completed work) ≈ 0
```

**A 3× load amplification against a service with only 1.15× headroom. Total outage.**

**Minute 20 — the metastable part.** The on-call engineer rebuilds the index. Capacity returns to 1,400 RPS. **The site stays down.** Why?

```
Offered load is still 3,000 RPS (every request still times out, so every
request still uses all 3 attempts).
Capacity is 1,400 RPS.
3,000 > 1,400 → still saturated → still timing out → still 3 attempts.
```

The loop sustains itself with no external cause. Removing the trigger did not remove the failure. This is exactly why outages have a long tail after the "fix" is deployed.

**How you actually get out:**

| Action | Effect |
|---|---|
| **Shed load** — reject a fraction of requests immediately with `429` | Cheap rejections cost ~0 capacity; the surviving fraction completes under the timeout, so retries stop |
| **Trip circuit breakers** — stop calling the failing dependency entirely for a cooling-off period | Drops offered load to ~0, lets queues drain, then probes with a trickle (Phase 4, Topic 14) |
| **Cut traffic at the edge** (CDN/LB rate limit, maintenance page) | Same mechanism, further upstream |
| **Disable retries via a feature flag** | Immediately removes the 3× multiplier — this is why retry policy should be *dynamically configurable* |
| Add capacity | Works only if you can add >2.6× instantly; usually too slow |

**How you prevent it entirely** — four mechanisms, all covered below or in Phase 4:

1. **Backoff with jitter** (§4.8) — spreads retries out and reduces their rate as the problem persists.
2. **Retry budgets** (§4.8) — hard-caps amplification at e.g. 1.1× instead of 3×.
3. **Circuit breakers** — stop retrying a dependency that is clearly down (Phase 4, Topic 14).
4. **Load shedding with a priority scheme** — serve checkout, drop recommendations.

**The design principle:** *the maximum load your system can offer itself must be less than its capacity.* With 3 blind attempts, your peak self-inflicted load is 3× your traffic. If you cannot serve 3× your traffic, you cannot afford 3 blind retries.

### 4.8 Exponential backoff, jitter, and retry budgets

**Fixed-interval retry is the worst option.** If 10,000 clients all fail at `t = 0` and all retry at `t = 100 ms`, you have converted a smooth 10,000 RPS into a 10,000-request spike at one instant — a **thundering herd**. The server, already struggling, is hit by a synchronised wall of traffic, fails again, and the whole population retries in lockstep forever. Correlated failure creates correlated retries.

Exponential backoff fixes the *rate*; jitter fixes the *synchronisation*. You need both.

**The AWS Architecture Blog formula ("full jitter"), which is the recommended default:**

```
sleep = random_between(0, min(cap, base × 2^attempt))
```

- `base` — the initial backoff unit, typically 50–200 ms. Should be at least the dependency's p50 so the retry has a chance of landing in a recovered system.
- `cap` — the maximum single sleep, typically 10–60 s. Prevents a 2^10 explosion into hours.
- `attempt` — 0-indexed attempt number.
- `random_between(0, x)` — uniform random. **Sampling from zero is deliberate**: it spreads the retries of a synchronised population evenly across the whole window rather than clustering them at the end.

**Computed table — `base = 100 ms`, `cap = 20,000 ms`, 6 attempts.** Every column derived explicitly.

| Attempt | `2^attempt` | `base × 2^attempt` | `min(cap, …)` = window | Sleep range | Expected sleep (mean of uniform = window/2) |
|---|---|---|---|---|---|
| 0 | 1 | 100 ms | 100 ms | 0 – 100 ms | 50 ms |
| 1 | 2 | 200 ms | 200 ms | 0 – 200 ms | 100 ms |
| 2 | 4 | 400 ms | 400 ms | 0 – 400 ms | 200 ms |
| 3 | 8 | 800 ms | 800 ms | 0 – 800 ms | 400 ms |
| 4 | 16 | 1,600 ms | 1,600 ms | 0 – 1,600 ms | 800 ms |
| 5 | 32 | 3,200 ms | 3,200 ms | 0 – 3,200 ms | 1,600 ms |

```
Expected total waiting across 6 attempts
  = 50 + 100 + 200 + 400 + 800 + 1,600 = 3,150 ms

Worst-case total waiting (every draw hits its maximum)
  = 100 + 200 + 400 + 800 + 1,600 + 3,200 = 6,300 ms

Plus 6 × 600 ms of timeouts in the worst case = 3,600 ms
Worst-case wall clock for the whole retry sequence = 6,300 + 3,600 = 9,900 ms
```

That last line is why **retries must fit the budget** (§4.5). A caller with a 3-second deadline cannot afford 6 attempts; it can afford 2.

**Jitter strategies compared:**

| Strategy | Formula | Spread | Retry rate under load | Verdict |
|---|---|---|---|---|
| **No jitter** | `sleep = min(cap, base × 2^attempt)` | None — perfectly synchronised | Spiky | **Never use** with many clients |
| **Equal jitter** | `temp = min(cap, base × 2^a); sleep = temp/2 + random(0, temp/2)` | Half the window | Moderate | Fine; guarantees a minimum wait |
| **Full jitter** | `sleep = random(0, min(cap, base × 2^a))` | Whole window | Lowest total work in AWS's simulations | **Recommended default** |
| **Decorrelated jitter** | `sleep = min(cap, random(base, prev_sleep × 3))` | Wide, self-adjusting | Lowest completion time in AWS's simulations | Good when you want to finish fast |

AWS's published simulation (3 clients × 1,000 operations against a contended resource) found full jitter minimised total server load while decorrelated jitter minimised total completion time; both were dramatically better than no jitter.

**Retry budgets — the hard cap.** Backoff reduces the *rate* of retries but not the *ratio*. During a sustained outage, every request still eventually makes N attempts. A **retry budget** bounds the ratio directly:

> Retries may not exceed **X %** of successful requests over a sliding window (commonly 10 %, with a 10-second window).

Implementation is a token bucket:

- Every **successful** request deposits `retry_ratio` tokens (e.g. 0.1).
- Every **retry attempt** withdraws 1 token.
- If the bucket is empty, **the retry is not attempted** — the original failure is returned to the caller immediately.

The consequence is exactly what §4.7 needed:

```
Without a budget:  3 attempts  → up to 3.0× amplification
With a 10% budget: 1,000 RPS of traffic permits at most 100 RPS of retries
                   → maximum offered load 1,100 RPS → 1.1× amplification
```

**A 10 % retry budget is the difference between a brownout and an outage.** And it degrades gracefully in exactly the right direction: when things are healthy, successes fund plenty of tokens and retries always work; when everything is failing, there are no successes, the bucket empties, and retries automatically switch themselves off — which is precisely when they are most harmful.

This is implemented in Finagle (`retryBudget`), gRPC (`retryThrottling` with `maxTokens` and `tokenRatio` in the service config), Envoy (`retry_budget` in the circuit-breaker config), and Istio. If your stack has it, turn it on.

**Other essential retry hygiene:**

- **Retry at one layer only.** If the SDK retries 3×, the HTTP client retries 3×, and the service mesh retries 3×, you have `3 × 3 × 3 = 27` attempts per logical request. **Nested retries multiply.** Audit every layer and disable all but one — conventionally the outermost one that understands the business operation.
- **Mark retries.** Send `X-Retry-Attempt: 2` so servers can measure the retry ratio and shed retried traffic first under load.
- **Cap total attempts low.** 3 is almost always the right number. The marginal success probability of attempt 5 is tiny; the marginal load is not.
- **Never retry inside a held lock or an open transaction.** You will hold the contended resource for the entire backoff.

**Forward reference:** backoff and budgets limit the damage of retrying something that is *temporarily* struggling. When a dependency is *definitively* down, the correct response is to stop calling it altogether and fail instantly — that is a **circuit breaker**, covered in **Phase 4, Topic 14 — Circuit breakers, bulkheads, and graceful degradation**. Backoff, budgets, and breakers are complementary: backoff paces, budgets cap, breakers stop.

### 4.9 Idempotent database writes

Idempotency keys protect the API boundary. The database write itself must also be safe to re-run.

**Pattern 1 — `INSERT ... ON CONFLICT DO NOTHING` (Postgres; `INSERT IGNORE` in MySQL; `MERGE` in SQL Server/Oracle).**

```sql
-- Requires a unique constraint; without one this clause does nothing useful.
CREATE UNIQUE INDEX idx_orders_idem ON orders (idempotency_key);

INSERT INTO orders (user_id, isbn, quantity, amount_paise, idempotency_key)
VALUES ($1, $2, $3, $4, $5)
ON CONFLICT (idempotency_key) DO NOTHING
RETURNING id;
-- Returns 1 row on first execution, 0 rows on every replay.
-- Zero rows is SUCCESS, not failure -- the desired state already exists.
```

**Pattern 2 — `ON CONFLICT DO UPDATE` (upsert)** when a replay should refresh fields:

```sql
INSERT INTO book_stock (isbn, quantity, updated_at)
VALUES ($1, $2, now())
ON CONFLICT (isbn) DO UPDATE
   SET quantity   = EXCLUDED.quantity,   -- absolute assignment: idempotent
       updated_at = now();
-- Contrast with: SET quantity = book_stock.quantity + EXCLUDED.quantity
-- which is RELATIVE and therefore NOT idempotent -- a replay double-counts.
```

**Pattern 3 — conditional update (guarded state transition).** Makes a non-idempotent transition safe by making it a no-op once it has happened:

```sql
UPDATE orders
   SET status = 'paid', paid_at = now()
 WHERE id = $1
   AND status = 'pending';    -- the guard
-- First execution: 1 row updated. Replay: 0 rows, state unchanged. Idempotent.
```

**Pattern 4 — optimistic concurrency with a version column.** Solves the *different-writers* race that idempotency does not (§4.1, subtlety 2):

```sql
UPDATE books
   SET price_paise = $2, version = version + 1
 WHERE isbn = $1 AND version = $3;
-- 0 rows updated => somebody else wrote first => re-read and decide.
-- Exposed over HTTP as ETag + If-Match (Lesson 6).
```

**Pattern 5 — ledgers instead of mutable balances.** The textbook non-idempotent write is `UPDATE accounts SET balance = balance - 100`. Replace it with an append of an immutable, uniquely-identified fact:

```sql
CREATE TABLE ledger_entries (
    txn_id     UUID PRIMARY KEY,         -- the idempotency key, enforced by the PK
    account_id BIGINT      NOT NULL,
    delta_paise BIGINT     NOT NULL,     -- signed: -10000 = debit of Rs.100
    reason     TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO ledger_entries (txn_id, account_id, delta_paise, reason)
VALUES ($1, $2, -10000, 'order:7781')
ON CONFLICT (txn_id) DO NOTHING;
-- Replaying with the same txn_id is a no-op. The balance is a derived value:
--   SELECT SUM(delta_paise) FROM ledger_entries WHERE account_id = $1;
-- (materialise it into a cached column if the sum gets expensive)
```

This is how real payment systems work, and it is the cleanest available answer to "how do I make money movement idempotent": **make the write an insert of a uniquely-keyed fact, never a mutation of a running total.**

**Pattern 6 — the transactional outbox** (preview of Phase 4). The order insert and the "send confirmation email" message must either both happen or neither. Writing to the database and then publishing to a queue is two systems and therefore not atomic. Instead, insert the message into an `outbox` table *inside the same transaction* as the order, and have a separate relay publish it at-least-once. Combined with a consumer-side dedup key, that gives effectively-exactly-once (§4.3). **Phase 4, Topic 10** covers this fully.

## 5. Request / data flow

A BookNook checkout that fails ambiguously and is retried correctly, end to end:

1. The user taps **Place order**. The browser generates `Idempotency-Key: 7f3c9b1e-8f52-4c0a-9f2d-5b8a1c6e4d33` **once**, before the first attempt, and stores it in memory for the whole operation.
2. The browser sends `POST /api/orders` with that header, a total deadline of 10,000 ms, and a body of `{"isbn": "1492052205", "quantity": 1}` — intent only, no prices (Lesson 1).
3. The API gateway records the deadline, subtracts 8 ms of elapsed time, and forwards `X-Request-Deadline` = now + 9,992 ms.
4. Checkout service receives it, allocates its internal budget: inventory 600 ms, pricing 400 ms, payment 3,000 ms, retry slack 2,000 ms, response 200 ms.
5. Checkout claims the idempotency key: `INSERT ... ON CONFLICT DO NOTHING` → **claim succeeds**, state `in_progress`.
6. Checkout calls inventory with a 600 ms connect+read budget. Inventory responds in 14 ms: 3 copies in stock.
7. Checkout calls the payment provider with a 3,000 ms timeout and **its own** idempotency key derived from the order key.
8. The payment provider **charges the card successfully at t = 2,100 ms** — but the response packet is lost on the way back.
9. Checkout's 3,000 ms timeout fires. **Checkout does not know whether the charge happened.** This is §2, row 3, live.
10. Checkout's retry policy: full jitter, `base` 200 ms, attempt 0 → sleeps `random(0, 200)` = 137 ms. It first checks its retry budget token bucket: tokens available → proceed.
11. Checkout retries the payment call **with the identical idempotency key**.
12. The payment provider finds the key in state `completed` and **returns the stored response** — charge ID `ch_8821`, status `succeeded` — **without charging again**. One charge, not two.
13. Checkout opens a transaction: insert the order with `ON CONFLICT (idempotency_key) DO NOTHING`, decrement stock with `WHERE stock >= 1`, write an outbox row for the confirmation email, and update the idempotency key to `completed` with the stored response. One commit.
14. Checkout returns `201 Created`. But the user's phone has by now lost its connection; the browser sees a network error at t = 8.9 s.
15. The user taps **Place order** again. The browser **reuses the same stored key** (step 1).
16. Checkout finds state `completed` and replays the stored `201` with order ID `7781`. **The user sees their order. Exactly one order exists. Exactly one charge exists.**
17. An hour later, a sweeper deletes `idempotency_keys` rows past `expires_at`; the outbox relay has long since delivered the confirmation email, with the consumer deduplicating on `order_id`.

Count the ambiguous failures survived: a lost payment response (step 8) and a dead client connection (step 14). Without idempotency keys, step 11 double-charges and step 15 creates a second order.

## 6. ASCII diagram

```
 THE AMBIGUITY (why this whole lesson exists)

   CLIENT                                           SERVER
     |-------- POST /orders -------------------------->|
     |                                                 | charge card  OK
     |                                                 | insert order OK
     |                X  response lost  X  <-----------|
     |                                                 |
   [timeout fires]                                     |
     |                                                 |
     |  Client knows: "no response."                   |
     |  Client does NOT know: did it happen?           |
     |                                                 |
     |  Blind retry  -> 2 orders, 2 charges.           |
     |  No retry     -> user thinks it failed; it did not.
     |  Idempotency key -> safe either way.  <--- the only good answer


 IDEMPOTENCY KEY: FIRST ATTEMPT vs REPLAY

  FIRST ATTEMPT                            REPLAY (same key)
  +-----------------------------+          +-----------------------------+
  | INSERT key ON CONFLICT      |          | INSERT key ON CONFLICT      |
  |   DO NOTHING                |          |   DO NOTHING                |
  |   -> 1 row  (WE WON)        |          |   -> 0 rows (ALREADY THERE) |
  +--------------+--------------+          +--------------+--------------+
                 v                                        v
       +---------------------+                 +----------------------+
       | BEGIN               |                 | SELECT stored row    |
       |   INSERT order      |                 +----------+-----------+
       |   UPDATE stock      |                            |
       |   INSERT outbox     |            state=completed |  state=in_progress
       |   UPDATE key        |                  +---------+---------+
       |     state=completed |                  v                   v
       |     response=201... |          return STORED        return 409
       | COMMIT   <-- ONE    |          response verbatim     "in progress,
       +----------+----------+          (NO work re-done)      retry in 200ms"
                  v
              return 201


 RETRY STORM -> METASTABLE FAILURE  (section 4.7 numbers)

  load                                     capacity after index drop = 1150 RPS
  3000 +                        ,-------------------------------  offered load
       |                      ,'                                  (3x amplified)
  1800 +                    ,'
       |                  ,'          index REBUILT here
  1200 +                ,'                   |
       |              ,'                     v
  1150 +~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~+~~~~~~~~~~~~~~~~  capacity 1150->1400
  1000 +-------------'                   '-----------------  real user traffic
       +----+--------+--------+--------+--------+--------+----> time
            m0       m1       m2      ...      m20
         brownout  over-    outage          TRIGGER REMOVED
                  capacity                  but STILL DOWN  <-- metastable


 EXPONENTIAL BACKOFF WITH FULL JITTER
 sleep = random(0, min(cap, base * 2^attempt))     base=100ms  cap=20s

 attempt 0  |##|                                        window 0-100ms
 attempt 1  |####|                                      window 0-200ms
 attempt 2  |########|                                  window 0-400ms
 attempt 3  |################|                          window 0-800ms
 attempt 4  |################################|          window 0-1600ms
 attempt 5  |################################################|  0-3200ms
            0ms                                            3200ms

  Each client samples UNIFORMLY inside its window, so 10,000 clients that
  failed at the same instant are SPREAD, not SYNCHRONISED.
```

## 7. Detailed example

### BookNook: the Black Friday double-charge incident

**Setup.** BookNook runs a sale. Checkout traffic rises from 60 to 900 orders/minute. The mobile app has a 5-second timeout and retries twice with a fixed 1-second wait. There are no idempotency keys; `POST /orders` is plain.

**What happened, hour by hour:**

| Time | Event | Mechanism |
|---|---|---|
| 19:00 | Sale opens; checkout p99 rises 180 ms → 1,400 ms | Utilisation ρ climbs past 0.85; `1/(1−ρ)` multiplier (Lesson 16, §4.9) |
| 19:04 | p99 crosses 5,000 ms; ~8 % of mobile checkouts time out | Queueing |
| 19:04 | Those 8 % retry — **but the orders had already been created** | §2, row 3 |
| 19:06 | Retry load pushes offered RPS to 1.24× | Amplification |
| 19:09 | Timeout rate 8 % → 35 %; retries now 2 per request for a third of traffic | Feedback loop |
| 19:15 | Offered load 2.4×; checkout effectively down | Metastable (§4.7) |
| 19:40 | Engineers scale out 3× and the site recovers | Capacity finally exceeds amplified load |
| 20:30 | Finance reports **1,431 duplicate orders** and **1,180 double charges** | No idempotency |
| Next week | Manual refunds, chargebacks, 140 support tickets, a damaged reputation | — |

**The post-mortem numbers:**

```
Orders placed in the window (deduplicated) : 11,900
Duplicate order rows created               :  1,431   (12.0 %)
Double charges                             :  1,180   (9.9 %)
Average order value                        : Rs. 840
Value wrongly charged                      : 1,180 × 840 = Rs. 991,200
Refund processing cost (support + fees)    : 1,180 × Rs. 60 = Rs. 70,800
```

**What was fixed, and what each fix bought:**

| Fix | Mechanism | Outcome |
|---|---|---|
| `Idempotency-Key` on all POST/PATCH, generated client-side once per operation | §4.4 | Duplicates structurally impossible |
| Unique index on `orders(idempotency_key)` | §4.9 pattern 1 | Second line of defence even if the key layer fails |
| Full jitter backoff, `base` 200 ms, `cap` 10 s, **max 3 attempts** | §4.8 | No synchronised retry wall |
| Retry budget at 10 % | §4.8 | Max amplification 3.0× → 1.1× |
| Timeout set from p99.9 (450 ms → 1,500 ms under sale load) with a propagated deadline | §4.5 | Fewer spurious timeouts; no zombie requests |
| Load shedding: reject with `429` + `Retry-After` above 1,250 RPS | §4.7 | Cheap rejections preserve goodput |
| Circuit breaker on the payment provider | Phase 4, Topic 14 | Stops hammering a dead dependency |

**The next sale:** peak 1,600 orders/minute, p99 2,100 ms, **zero duplicates**, and a 6-minute period of `429`s instead of a 36-minute outage. The system *degraded* instead of *collapsing* — which is the entire goal.

### Contrasting example: a telemetry ingestion pipeline

BookNook also ingests 50,000 analytics events/second from browsers. The correct answer here is almost the inverse of checkout.

| | Checkout `POST /orders` | Telemetry `POST /events` |
|---|---|---|
| Cost of a duplicate | A real customer is charged twice — **unacceptable** | One page-view counted twice — a rounding error in a dashboard |
| Cost of a loss | A real customer's order vanishes — **unacceptable** | One event missing from a 50,000/s stream — invisible |
| Required semantics | **Effectively exactly-once** | **At-most-once** is fine |
| Idempotency keys? | **Mandatory** | No — the dedup store would need to handle 50,000 writes/s to save a rounding error |
| Retries? | Yes, bounded, budgeted | **No.** Drop on failure. |
| Timeout | 1,500 ms (generous; the user is waiting) | 200 ms (aggressive; nobody is waiting) |
| Backoff | Full jitter, 3 attempts | N/A |
| Protocol | HTTPS, response matters | Often `navigator.sendBeacon` / UDP-style fire-and-forget (Lesson 8) |

**The principle:** *choose your delivery semantics from the cost of a duplicate versus the cost of a loss, per operation* — never once for the whole system. The same company correctly runs effectively-exactly-once on one endpoint and at-most-once on another, a hundred metres away in the same codebase.

## 8. Code or configuration example

**Not production-ready.** The following is deliberately compact: it omits structured logging, metrics, tracing propagation, connection pooling, TLS configuration, clock-skew handling, key-store sharding, and the `locked_at` sweeper that reclaims rows abandoned by crashed workers. Use `tenacity`/`backoff` (Python), Polly (.NET), Resilience4j (Java), `failsafe-go`, or your service mesh in real systems.

```python
import random, time, uuid, hashlib, json
import httpx

# ============================================================================
# CLIENT SIDE
# ============================================================================

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
NEVER_RETRY      = {400, 401, 403, 404, 409, 413, 422}   # 409 handled specially

def full_jitter_sleep(attempt, base_ms=100, cap_ms=20_000):
    """AWS 'full jitter':  sleep = random(0, min(cap, base * 2^attempt)).
    Sampling from ZERO is intentional: it spreads a synchronised population of
    clients evenly across the window instead of clustering them at the end."""
    window_ms = min(cap_ms, base_ms * (2 ** attempt))
    return random.uniform(0, window_ms) / 1000.0


class RetryBudget:
    """Token bucket that caps retries at `ratio` of successful requests.
    When everything is failing there are no successes, so the bucket drains and
    retries switch themselves OFF -- exactly when they are most harmful."""
    def __init__(self, ratio=0.1, max_tokens=100):
        self.ratio, self.max_tokens, self.tokens = ratio, max_tokens, max_tokens

    def on_success(self):
        self.tokens = min(self.max_tokens, self.tokens + self.ratio)

    def try_withdraw(self):
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False          # budget exhausted -> do NOT retry


BUDGET = RetryBudget(ratio=0.1)

def place_order(isbn, quantity, deadline_ms=10_000, max_attempts=3):
    # THE KEY IS GENERATED ONCE, OUTSIDE THE LOOP. Generating it inside the loop
    # is the single most common implementation bug -- it defeats the mechanism
    # entirely while looking correct in code review.
    idem_key = str(uuid.uuid4())
    body = {"isbn": isbn, "quantity": quantity}      # intent only; no prices
    started = time.monotonic()
    last_error = None

    for attempt in range(max_attempts):
        remaining_ms = deadline_ms - (time.monotonic() - started) * 1000
        if remaining_ms <= 0:
            break                                     # budget spent; fail fast

        try:
            resp = httpx.post(
                "https://api.booknook.com/orders",
                json=body,
                headers={
                    "Idempotency-Key": idem_key,      # SAME on every attempt
                    "X-Retry-Attempt": str(attempt),  # lets the server shed retries first
                },
                # Four distinct timeouts. `read` alone does NOT bound total time:
                # a server trickling one byte per second keeps it alive forever.
                timeout=httpx.Timeout(
                    connect=0.25,                     # handshake is ~1ms when healthy
                    read=1.5,                         # ~3x the dependency p99
                    write=1.0,
                    pool=0.5,                         # waiting for a pooled connection
                ),
            )
        except (httpx.ConnectError, httpx.ConnectTimeout):
            last_error = "connect"                    # provably never ran -> always retryable
        except httpx.ReadTimeout:
            last_error = "read-timeout"               # AMBIGUOUS: safe only because of the key
        else:
            if resp.status_code < 300:
                BUDGET.on_success()
                return resp.json()

            if resp.status_code == 409:               # server: "original still in progress"
                time.sleep(resp.json().get("retry_after_ms", 200) / 1000.0)
                continue                              # cheap poll; does not consume backoff

            if resp.status_code in NEVER_RETRY:
                raise RuntimeError(f"permanent failure {resp.status_code}: {resp.text}")

            if resp.status_code not in RETRYABLE_STATUS:
                raise RuntimeError(f"unclassified status {resp.status_code}")

            if resp.status_code == 429 and "Retry-After" in resp.headers:
                # Honour the server's instruction INSTEAD of our own backoff.
                time.sleep(float(resp.headers["Retry-After"]))
                continue
            last_error = f"status-{resp.status_code}"

        # ---- decide whether we are allowed to retry at all ----
        if attempt == max_attempts - 1:
            break
        if not BUDGET.try_withdraw():
            raise RuntimeError(f"retry budget exhausted; last error {last_error}")

        sleep_s = full_jitter_sleep(attempt)
        if (time.monotonic() - started) * 1000 + sleep_s * 1000 >= deadline_ms:
            break                                     # the sleep alone would blow the deadline
        time.sleep(sleep_s)

    raise RuntimeError(f"order failed after {max_attempts} attempts: {last_error}")
```

**Server-side configuration — Envoy, where most of this belongs in a modern stack:**

```yaml
# Envoy route config. Putting retries/timeouts in the proxy means every language
# in the fleet gets identical, centrally-tunable behaviour.
route:
  cluster: checkout-service
  timeout: 10s                      # TOTAL deadline for the whole route
  retry_policy:
    retry_on: "5xx,reset,connect-failure,refused-stream"
    # IMPORTANT: this list excludes 4xx. Retrying a 400 is self-inflicted DoS.
    num_retries: 2                  # 2 retries = 3 total attempts
    per_try_timeout: 1.5s           # 3 x 1.5s = 4.5s, comfortably inside the 10s budget
    retry_back_off:
      base_interval: 0.1s           # grows exponentially; Envoy applies jitter
      max_interval:  10s
    retriable_status_codes: [429, 503]
    retry_host_predicate:
      - name: envoy.retry_host_predicates.previous_hosts   # never retry the same bad host

circuit_breakers:                   # Phase 4, Topic 14
  thresholds:
    - priority: DEFAULT
      max_connections: 1024
      max_pending_requests: 256     # queue cap -> cheap rejection instead of unbounded queue
      max_requests: 1024
      retry_budget:
        budget_percent: { value: 10.0 }   # retries <= 10% of active requests
        min_retry_concurrency: 3
```

```jsonc
// gRPC service config -- the same ideas in the gRPC ecosystem.
{
  "methodConfig": [{
    "name": [{ "service": "booknook.Checkout" }],
    "timeout": "10s",                       // becomes the grpc-timeout header,
                                            // automatically propagated to every hop
    "retryPolicy": {
      "maxAttempts": 3,
      "initialBackoff": "0.1s",
      "maxBackoff": "10s",
      "backoffMultiplier": 2,
      "retryableStatusCodes": ["UNAVAILABLE", "RESOURCE_EXHAUSTED"]
      // NOT: INVALID_ARGUMENT, NOT_FOUND, PERMISSION_DENIED, FAILED_PRECONDITION
    }
  }],
  "retryThrottling": {                      // the retry budget
    "maxTokens": 100,
    "tokenRatio": 0.1                       // caps amplification at ~1.1x
  }
}
```

**Why none of this is production-ready:** the Python `RetryBudget` is not thread-safe and is per-process rather than per-fleet; there is no jittered clock source, no observability on the retry ratio, no classification of DNS failures, no handling of partial writes, and no deadline propagated into the request headers. The Envoy and gRPC configs omit outlier detection, health checking, TLS, and the per-endpoint tuning that real services need. Treat all of it as an annotated specification, not a drop-in.

## 9. Numerical example

**Scenario.** BookNook's checkout tier during an incident. Every assumption is stated before it is used.

**Assumptions**

| Assumption | Value | Source |
|---|---|---|
| Baseline checkout traffic | 1,000 RPS | Measured |
| Capacity, healthy | 1,400 RPS | Load test (open-loop, Lesson 16 §4.7) |
| Capacity, degraded (index dropped) | 1,150 RPS | Measured during incident |
| Service time `S` | 20 ms | Measured |
| Client timeout | 500 ms | Existing config |
| Retry policy A (the incident) | 3 attempts, fixed 100 ms, no jitter, no budget | Existing config |
| Retry policy B (the fix) | 3 attempts, full jitter `base` 100 ms `cap` 20 s, 10 % budget | Proposed |
| Dependency p50 / p99 / p99.9 | 12 / 180 / 450 ms | Measured |
| Worker pool | 24 servers × 200 workers = 4,800 | Config |

**Step 1 — utilisation and queueing before and after the trigger.**

```
Healthy:    rho = 1,000 / 1,400 = 0.714
            multiplier 1/(1-rho) = 1/0.286 = 3.50x
            W = 20 ms x 3.50 = 70 ms          (well under the 500 ms timeout)

Degraded:   rho = 1,000 / 1,150 = 0.870
            multiplier 1/(1-rho) = 1/0.130 = 7.69x
            W = 20 ms x 7.69 = 154 ms average
```

The *average* is still fine — which is exactly Lesson 16's point. The tail is not: p99 of an M/M/1 queue ≈ `4.6 × S / (1−ρ)` = `4.6 × 20 × 7.69` = **707 ms**, already past the 500 ms timeout. So roughly **20 %** of requests time out.

**Step 2 — retry amplification under policy A (no budget).**

```
Attempt 1 load                 = 1,000 RPS
Timed out (20%)                = 1,000 x 0.20   =   200 RPS -> retried
Offered load after attempt 2   = 1,000 + 200    = 1,200 RPS
Utilisation                    = 1,200 / 1,150  = 1.043  (OVER CAPACITY)

Queue now unbounded; timeout fraction rises to ~50%.
Timed out at attempt 2         = 1,200 x 0.50   =   600 RPS -> retried
Offered load after attempt 3   = 1,000 + 200 + 600 = 1,800 RPS
Utilisation                    = 1,800 / 1,150  = 1.565

Steady state (everything times out, every request uses all 3 attempts):
Offered load                   = 1,000 x 3      = 3,000 RPS
Amplification                  = 3.0x
Offered / capacity             = 3,000 / 1,150  = 2.61x
Goodput                        ~ 0
```

**Step 3 — the metastable check.** The index is rebuilt at minute 20; capacity returns to 1,400 RPS.

```
Offered load still             = 3,000 RPS   (every request still retries 3x)
Capacity                       = 1,400 RPS
3,000 > 1,400  ->  still saturated  ->  still timing out  ->  still 3 attempts
```

**The trigger is gone and the system is still down.** Recovery requires removing load, not adding capacity: you would need `3,000 / 1,400 = 2.14×` more capacity instantly, versus shedding `(3,000 − 1,400) / 3,000 = 53 %` of traffic.

**Step 4 — retry budget under policy B.**

```
Budget ratio                   = 10% of successful requests
Successes available to fund tokens, degraded ~ 800 RPS
Permitted retry rate           = 800 x 0.10     =    80 RPS
Offered load                   = 1,000 + 80     = 1,080 RPS
Utilisation                    = 1,080 / 1,150  = 0.939   (still below 1.0)
Amplification                  = 1.08x  vs  3.0x
```

**Below capacity. The system stays in a brownout instead of entering an outage**, and when the index is rebuilt it recovers immediately because offered load never exceeded capacity. The single configuration change `retryThrottling: {tokenRatio: 0.1}` is the difference between a 36-minute outage and a 6-minute slowdown.

**Step 5 — the backoff schedule under policy B** (`base` = 100 ms, `cap` = 20,000 ms).

| Attempt | Window `min(cap, 100 × 2^a)` | Sleep range | Expected sleep | Cumulative expected |
|---|---|---|---|---|
| 0 | 100 ms | 0 – 100 | 50 ms | 50 ms |
| 1 | 200 ms | 0 – 200 | 100 ms | 150 ms |
| 2 | 400 ms | 0 – 400 | 200 ms | 350 ms |
| 3 | 800 ms | 0 – 800 | 400 ms | 750 ms |
| 4 | 1,600 ms | 0 – 1,600 | 800 ms | 1,550 ms |
| 5 | 3,200 ms | 0 – 3,200 | 1,600 ms | 3,150 ms |

```
Expected total backoff, 6 attempts  = 3,150 ms
Worst-case total backoff            = 100+200+400+800+1600+3200 = 6,300 ms
Plus 6 x 500 ms of timeouts (worst) = 3,000 ms
Worst-case wall clock               = 9,300 ms
```

With a 10,000 ms user deadline, **6 attempts barely fit and leave no slack**; with the 3-attempt policy:

```
Worst-case = (100+200+400) backoff + (3 x 500) timeouts = 700 + 1,500 = 2,200 ms
```

Comfortable. This is how you *derive* `max_attempts` from a deadline rather than guessing it.

**Step 6 — choosing the timeout from the distribution.**

```
p99.9 = 450 ms  ->  round up to 500 ms
3 x p99 = 3 x 180 = 540 ms
Timeout = max(500, 540) = 540  ->  round to 600 ms

Spurious timeout rate at 600 ms, healthy: < 0.1% of requests
Spurious timeout rate at 200 ms (if someone "tightened" it):
   200 ms is between p99 (180) and p99.9 (450), so roughly 0.8% of requests
   would time out and retry -> a permanent 0.8% load tax plus duplicate risk.
```

**Step 7 — the "no timeout" catastrophe, quantified.**

```
Workers                         = 24 x 200 = 4,800
Arrival rate                    = 1,000 RPS
With a hung dependency, W -> infinity, so no worker is ever returned.
Time to exhaust the pool        = 4,800 / 1,000 = 4.8 seconds

With a 600 ms timeout:
L = lambda x W = 1,000 x 0.6    = 600 workers busy of 4,800 (12.5%)
```

**4.8 seconds to total fleet death versus 12.5 % utilisation.** That is the entire value of one configuration line.

**Step 8 — the cost of *not* having idempotency keys**, using the §7 incident rates:

```
Checkouts in the incident window          = 11,900
Duplicate rate observed                   = 12.0%
Duplicate orders                          = 11,900 x 0.12 = 1,428
Average order value                       = Rs. 840
Wrongly charged                           = 1,428 x 840    = Rs. 1,199,520
Refund handling at Rs. 60 each            = 1,428 x 60      = Rs. 85,680
Engineering time to reconcile (3 people x 2 days @ Rs. 8,000/day) = Rs. 48,000
Total direct cost                                             ~ Rs. 1,333,200
```

Against that, the idempotency-key implementation is roughly one table, one unique index, and two days of work. **The return on investment is not a close call.**

## 10. Trade-offs

**Advantages**
- **Retries convert transient failures into invisible ones.** A 0.1 % packet-loss event becomes a 50 ms delay instead of a user-visible error.
- **Idempotency makes the ambiguous case safe**, which is the only way to retry mutations at all.
- **Timeouts bound resource consumption**, converting unbounded cascading failure into bounded degradation.
- **Backoff with jitter prevents synchronised herds**, so recovery is possible.
- **Retry budgets cap self-inflicted load** at a known multiplier you can capacity-plan against.
- **Idempotency keys also protect against user behaviour** — double-clicks, impatient refreshes, flaky mobile networks — not just machine failures.

**Disadvantages and costs**
- **Idempotency keys add a storage system and a write on the hot path.** At 1,000 checkout RPS that is 1,000 extra writes/s plus a purge job; it is a real capacity line item.
- **Key scoping and expiry are subtle.** Too short and legitimate retries re-execute; too long and the store grows unboundedly. 24 hours is a convention, not a law.
- **Stored responses can go stale.** Replaying a 24-hour-old `201` with an order status of `pending` may confuse a client whose order has since shipped.
- **Retries increase tail latency for the caller.** A request that succeeds on attempt 3 took the timeout, the backoff, and the work — often 10× the p50.
- **Retries multiply if layered.** SDK × client × mesh = 27 attempts, which is a self-DoS (§4.8).
- **Timeouts that are too tight manufacture failures** out of healthy slow requests, and each manufactured failure becomes load.
- **Deadline propagation requires discipline across every service and every language.** One hop that ignores the deadline reintroduces zombie requests for the whole chain.
- **Idempotency is not free to reason about.** "Is this operation idempotent?" must be answered per endpoint, and the answer changes when the implementation changes.

**Operational costs**
- You must **monitor the retry ratio** (`retries / total requests`) as a first-class metric. A rising retry ratio is the earliest warning of a storm, earlier than error rate.
- You need a **kill switch** for retries, configurable at runtime, because disabling retries is a standard incident response.
- You need **reconciliation**: even with keys, the payment provider and your orders table can diverge, so a daily job must compare them.
- Someone must own **timeout values per dependency**, and they must be revisited when the dependency's p99 changes. Stale timeouts are silent landmines.

**When this becomes unnecessary complexity**
- Pure read endpoints: `GET` is already idempotent; a plain retry with backoff is sufficient and keys add nothing.
- Fire-and-forget telemetry (§7, contrasting example), where at-most-once is correct and a dedup store would cost more than the data is worth.
- Internal tools with one user and no money involved — write the simple thing and move on.

## 11. Failure scenarios

| Failure | What the user sees | How it's detected | Recovery |
|---|---|---|---|
| **Blind retry of a non-idempotent POST** | Two orders, two charges, one confused customer | Duplicate-detection job comparing orders by (user, isbn, minute); support tickets; payment-provider duplicate reports | Refund and reconcile; ship idempotency keys + a unique index (§4.4, §4.9) |
| **New idempotency key generated inside the retry loop** | Identical symptom to the above, while the code *looks* correct | Keys in the store that are never reused; retry ratio high but replay ratio ~0 | Move key generation outside the loop; assert in tests that two attempts share a key |
| **Concurrent duplicates racing the dedup check** (check-then-act) | Occasional duplicates only under load; unreproducible locally | Duplicate rows that both have *different* keys but identical content, clustered in time | Replace `SELECT`-then-`INSERT` with `INSERT ... ON CONFLICT DO NOTHING` (§4.4) |
| **No timeout on an outbound call** | Whole service hangs, including unrelated endpoints | Worker-pool saturation metric; thread dumps all parked in socket read; latency → ∞ with no errors | Restart processes to clear; set connect + read + total timeouts everywhere (§4.5) |
| **Timeout set too tight** (p95 instead of p99.9) | Intermittent "something went wrong" on healthy requests; duplicate side effects | Timeout rate ≈ the tail fraction you cut off; retries succeed instantly on attempt 2 | Recompute from p99.9; never pick timeouts by intuition |
| **Retry storm** | Site slows, then dies minutes after a small blip | Request count rises while *unique* request count does not; retry ratio spikes | Shed load, disable retries by flag, trip breakers; then add budgets + jitter (§4.7) |
| **Metastable failure** | Site stays down after the cause is fixed | Load stays at N× baseline with the trigger removed | Cut traffic at the edge until offered load < capacity; restore gradually |
| **Thundering herd on recovery** (no jitter) | Service comes up, dies again within seconds, repeatedly | Sawtooth traffic pattern with a period equal to the backoff interval | Full jitter; staggered restarts; slow-start load balancing |
| **Nested retries across layers** | One user click produces 27 backend requests | Fan-out ratio per user action far above design; duplicated trace spans | Audit every layer; retry at exactly one (§4.8) |
| **Key store outage** | All writes fail, even though the main database is healthy | Error spike localised to key-store calls | Put keys in the same database as the work so there is one failure domain; otherwise fail-open carefully and accept duplicate risk |
| **Idempotency key replayed with a different body** | Client gets a response for a request it did not make | `request_fingerprint` mismatch counter | Return `422`; fix the client (§4.4) |
| **Retry of a partially-applied non-transactional write** | Half-created order; stock decremented without an order row | Invariant-checking job (orders vs stock movements) | Wrap all effects in one transaction; use an outbox for cross-system effects (§4.9) |
| **`Retry-After` ignored on 429** | Client gets rate-limited, then banned | Provider-side abuse alerts; rising 429 rate from one client ID | Honour the header; implement backoff that defers to server instruction |

## 12. Alternatives

| Approach | Mechanism | Duplicates | Lost work | Cost | Use when |
|---|---|---|---|---|---|
| **Do nothing (at-most-once)** | Send once, never retry | Never | Possible | Zero | Telemetry, metrics, cache warming, best-effort notifications |
| **Blind retry (at-least-once)** | Retry on failure, no dedup | **Likely** | Never | Very low | Naturally idempotent operations only (`GET`, `PUT`, `DELETE`, absolute-value updates) |
| **Idempotency keys** | Client key + server dedup store (§4.4) | Never | Never | One table + one write per request | Payments, orders, any state-changing POST. **The default for money.** |
| **Natural idempotency** | Design the operation so repeats are no-ops: `PUT`, conditional updates, `ON CONFLICT DO NOTHING`, ledgers | Never | Never | **Zero extra infrastructure** | Whenever you can choose the API shape. **Always prefer this.** |
| **Client-generated resource IDs** | Client mints the UUID *and* the URL: `PUT /orders/{client-uuid}` | Never | Never | Zero — the primary key *is* the dedup | Greenfield APIs; converts an unsafe POST into a safe PUT |
| **Deduplication window in a queue** | Broker drops messages with a repeated dedup ID inside a window (SQS FIFO: 5 min) | Never *within the window* | Never | Managed | Message-driven work with bounded retry windows |
| **Transactional outbox + relay** | Write the message in the same transaction as the data; relay publishes at-least-once | Consumer must dedup | Never | A table + a relay process | Cross-system effects (email, search index, analytics). Phase 4, Topic 10 |
| **Two-phase commit (2PC)** | Coordinator-driven prepare/commit across systems | Never | Never | **Very high** — blocking, slow, coordinator is a SPOF | Almost never in internet-scale systems; mentioned so you can say why you rejected it |
| **Sagas with compensating actions** | A sequence of local transactions, each with an undo | Possible mid-saga | Never | High complexity | Long-running multi-service workflows (Phase 4) |
| **Circuit breaker** | Stop calling a dependency that is failing; fail fast | N/A | Requests rejected fast | Low | Complements retries; the right answer when a dependency is *down* rather than *slow*. Phase 4, Topic 14 |
| **Load shedding / rate limiting** | Reject excess requests cheaply with `429` | N/A | Rejected requests | Low | Protecting goodput during overload; the exit from metastability |
| **Hedged requests** (Lesson 16) | Duplicate a slow request to another replica | Requires idempotency | Never | ~5 % extra load | Tail-latency reduction on idempotent reads |

The practical hierarchy: **make it naturally idempotent if you can; add idempotency keys if you cannot; add retries with jitter and a budget; add timeouts everywhere; add circuit breakers and shedding for when retries are not enough.**

## 13. When to use it

1. **Any state-changing API that handles money, inventory, or legal records.** Orders, payments, refunds, bookings, ticket issuance, prescriptions. Duplicates here are not bugs, they are incidents. Idempotency keys are mandatory.
2. **Any call over a network you do not control** — third-party APIs, cross-region calls, mobile clients. Mobile networks produce ambiguous failures constantly: handovers between cell towers, app backgrounding, and radio sleep all look exactly like a server failure.
3. **Every outbound call, everywhere, for timeouts.** There is no call for which infinite waiting is correct. This is the single highest-value, lowest-effort practice in the entire course.
4. **Message consumers.** Every queue in common use (SQS, Kafka, RabbitMQ, Pub/Sub) delivers at-least-once; your consumer *will* see duplicates, so it must be idempotent. This is not optional, it is the contract.
5. **Any client population large enough to synchronise.** Past roughly a hundred clients, correlated failure plus fixed-interval retry produces a thundering herd. Jitter becomes mandatory.
6. **Batch and ETL jobs that may be re-run.** A job that must be safely re-runnable after a partial failure needs idempotent writes (`ON CONFLICT DO NOTHING`, partition overwrite, or a ledger).
7. **Webhook receivers.** Senders retry aggressively and deliver duplicates by design; dedup on the event ID.

## 14. When *not* to use it

1. **High-volume, low-value telemetry.** At 50,000 events/s, a dedup store costs more than the duplicates it prevents. At-most-once is the correct engineering choice (§7).
2. **Naturally idempotent operations.** `GET`, `PUT` with an absolute body, `DELETE`, and `SET key value` need no keys. Adding an idempotency-key table to a `PUT` endpoint is ceremony, not safety.
3. **When the cost of a duplicate is genuinely zero.** Re-sending a cache invalidation or re-warming a cache twice is harmless; do not build machinery for it.
4. **Retries against a dependency that is definitively down.** Once a circuit breaker is open, retrying is pure waste — fail fast and shed. Retries are for *transient* failures only.
5. **Non-idempotent operations you cannot make idempotent and cannot afford to duplicate.** If sending a one-time password SMS twice is unacceptable and you have no dedup, the correct answer is **do not retry** — surface the failure to the user and let them decide.
6. **Inside a held lock or an open database transaction.** Retrying there holds the contended resource across the whole backoff, converting a transient failure into a system-wide stall.
7. **When the caller's deadline has already expired.** Retrying a request nobody is waiting for is pure load with zero value; fail fast instead (§4.5, rule 4).

## 15. Common mistakes

**Beginner mistakes**

- **No timeout at all**, because the library default is infinite and nobody checked. The highest-impact bug in this lesson (§4.5).
- **Setting only a read timeout** and believing total time is bounded. A trickling server defeats it.
- **Retrying `400`/`422`.** It will fail identically every time; you have written a self-DoS loop.
- **Generating the idempotency key inside the retry loop.** Looks right, is useless.
- **Fixed-interval retries** with many clients → thundering herd.
- **No cap on attempts**, producing an infinite retry loop against a permanently dead dependency.
- **Assuming a timeout means the work did not happen.** It is the single most dangerous assumption in distributed systems (§2).
- **`SELECT` then `INSERT`** for deduplication — a race by construction (§4.4).
- **Treating "0 rows affected" from `ON CONFLICT DO NOTHING` as an error.** It is success: the desired state already exists.
- **Ignoring `Retry-After` on 429** and getting rate-limited harder, then blocked.
- **Retrying in a loop that holds a database transaction open.**

**Experienced-engineer mistakes**

- **Nested retries across layers** (SDK × HTTP client × mesh = 27 attempts). Audit and pick one layer (§4.8).
- **No retry budget**, so backoff limits the retry *rate* but never the retry *ratio*; under sustained failure you still reach full N× amplification (§4.8).
- **Timeouts not derived from measured percentiles**, so they are either a permanent load tax or useless.
- **Private per-hop timeouts instead of a propagated deadline**, producing zombie requests that burn capacity for responses nobody will read (§4.5).
- **Idempotency key stored in a different system from the work**, so a crash between the two leaves them inconsistent. Share the transaction if you possibly can.
- **No `request_fingerprint` check**, so a buggy client reusing a key for a different payload silently receives the wrong response.
- **No expiry on keys**, so the dedup table grows to hundreds of gigabytes and the purge becomes an outage of its own.
- **Forgetting the `in_progress` state**, so two concurrent duplicates both execute while the dedup table happily records one key.
- **Believing a vendor's "exactly-once" marketing** instead of understanding it as at-least-once + dedup (§4.3).
- **Not monitoring the retry ratio**, so the earliest signal of a storm is invisible until the error rate moves — by which time it is too late.
- **No runtime kill switch for retries**, removing the single fastest incident-response lever.
- **Hedging (Lesson 16) on a non-idempotent operation**, which duplicates by design.
- **Assuming the database is the only side effect.** Emails, SMS, webhooks, search-index updates, and analytics events all duplicate too; they each need their own dedup key.

## 16. Interview perspective

This topic is a **seniority detector**. Juniors describe the happy path; seniors describe what happens when the response is lost.

- **Say the ambiguity sentence early and unprompted:** "When this call times out, the client cannot tell whether the order was created, so I'll make it safe to retry with an idempotency key." That single sentence is worth more than a page of architecture.
- **Classify the operation before designing it.** "`GET` is safe, the cart `PUT` is idempotent, `POST /orders` is neither — so only `POST /orders` needs a key."
- **Draw the key table and the unique constraint.** Interviewers love concrete mechanisms. Mentioning `INSERT ... ON CONFLICT DO NOTHING` and the second unique index on `orders(idempotency_key)` shows you have actually built one.
- **Volunteer the concurrent-duplicate race.** Most candidates describe check-then-act. Saying "that's a race; I'd let the unique index arbitrate" is a strong differentiator.
- **Quote the backoff formula verbatim:** `sleep = random(0, min(cap, base × 2^attempt))`, and say "full jitter, per the AWS architecture blog". Precision signals experience.
- **Always mention retry budgets.** Almost nobody does. "3 blind retries is 3× amplification; with a 10 % budget it's 1.1×" is a memorable, numerate sentence.
- **Explain metastable failure.** "Even after we rebuilt the index, offered load stayed at 3×, so it stayed down — we had to shed load, not add capacity." This is a senior-engineer war story told in 15 words.
- **Derive the timeout from the p99.9**, explicitly linking back to Lesson 16. Connecting two topics unprompted is high signal.
- **State the delivery semantics per operation:** "at-least-once delivery plus idempotent processing gives effectively-exactly-once; true exactly-once delivery is impossible."
- **Mention deadline propagation** and `grpc-timeout`. Very few candidates know the chain-budget problem exists.
- **Know when NOT to retry:** circuit open, deadline expired, 4xx, non-idempotent-and-undeduplicable.

**Red flags interviewers watch for:** claiming to implement "exactly-once delivery"; retrying a POST with no dedup; saying "we'll just retry" with no mention of backoff, jitter, budgets, or caps; no timeouts anywhere in the design; using `SELECT`-then-`INSERT` for deduplication and not seeing the race; believing a timeout proves the work did not happen; retrying 4xx responses; being unable to explain why `POST` is not idempotent but `PUT` is; and not knowing that every mainstream message queue is at-least-once.

## 17. Knowledge check

Answer these before reading on. Write your answers down — even one line each.

**Conceptual**

1. Classify each of the following as *safe*, *idempotent but not safe*, or *neither*, and for each "neither" give one concrete change that makes it idempotent: (a) `GET /orders/7781`; (b) `DELETE /carts/42/items/9`; (c) `POST /orders`; (d) `UPDATE accounts SET balance = balance - 100 WHERE id = 7`; (e) `PATCH /books/123` with body `{"op":"add","path":"/tags/-","value":"sale"}`; (f) publishing a confirmation email to a queue.

2. A colleague says: "We use a message broker with exactly-once delivery, so our consumer doesn't need to be idempotent." Explain precisely why the first clause is impossible, what the broker is actually doing, and what the consumer must still do. Then give the one-line formula that *does* deliver exactly-once effects.

3. A service sets a 30-second timeout on every outbound call "to be safe", and has 16 servers × 250 workers. Traffic is 2,000 RPS and normal latency is 40 ms. Explain (a) how many workers are busy in steady state, (b) what happens within the first minute if one dependency starts hanging, with the arithmetic, and (c) why a *shorter* timeout is the safer choice even though it causes more visible errors.

**Practical scenario**

4. BookNook is adding a `POST /refunds` endpoint. A refund must never be issued twice, the operation calls an external payment provider, and the mobile client is on an unreliable network. Design the complete mechanism. Your answer must include: (a) who generates the key and when; (b) the DDL for the dedup table including every constraint and why each exists; (c) the exact SQL statement used to claim the key atomically, and why `SELECT`-then-`INSERT` is wrong; (d) what the server returns for a replay in state `completed` and in state `in_progress`, with status codes; (e) how you handle a crash that leaves a row stuck in `in_progress`; (f) what happens if the same key arrives with a *different* amount; (g) one failure mode your design still does not solve.

**Calculation**

5. A BookNook internal service receives 2,400 RPS and has a measured capacity of 3,000 RPS. Dependency latency is p50 = 25 ms, p99 = 240 ms, p99.9 = 700 ms. The current retry policy is 4 total attempts with a fixed 250 ms wait and no budget. Service time `S` = 30 ms.
   (a) Compute current utilisation ρ and the queueing multiplier `1/(1−ρ)`.
   (b) Choose a total timeout using the rule `max(p99.9 rounded up, 3 × p99)` and show both candidates.
   (c) If a degradation causes 25 % of requests to exceed that timeout, compute the offered load after attempt 2, after attempt 3, and in full steady state with 4 attempts. State at which attempt offered load first exceeds capacity.
   (d) Recompute the steady-state offered load with a 10 % retry budget, assuming 1,800 RPS of successes fund the token bucket. Does the service stay under capacity?
   (e) Using full jitter with `base` = 150 ms and `cap` = 10,000 ms, produce the window, sleep range, and expected sleep for attempts 0–5, then the expected and worst-case total backoff for a 4-attempt policy.
   (f) The caller's deadline is 4,000 ms. Using your timeout from (b) and your worst-case backoff from (e), how many attempts actually fit inside the deadline? Show the arithmetic.

Reply with your answers and I'll assess them, correct anything shaky, and give model answers before the Phase 1 wrap-up.

---

**Progress checklist**

Phase 1:
- `[x] 1. Client-server architecture`
- `[x] 2. Stateful versus stateless servers`
- `[x] 3. IP addresses, ports, sockets, domain names`
- `[x] 4. DNS resolution and DNS caching`
- `[x] 5. HTTP requests and responses`
- `[x] 6. HTTP methods, headers, cookies, sessions, status codes`
- `[x] 7. HTTPS and TLS`
- `[x] 8. TCP versus UDP`
- `[x] 9. HTTP/1.1 vs HTTP/2 vs HTTP/3`
- `[x] 10. REST APIs`
- `[x] 11. RPC and gRPC`
- `[x] 12. GraphQL`
- `[x] 13. Synchronous versus asynchronous communication`
- `[x] 14. Latency, throughput, bandwidth, concurrency`
- `[x] 15. Availability, reliability, durability, fault tolerance`
- `[x] 16. Average latency vs p50/p95/p99, tail latency`
- `[x] 17. Idempotency, retries, timeouts, exponential backoff`

**Phase 1 complete — all 17 topics covered.**

**NEXT: Phase 1 Wrap-Up**

---

# Phase 1 Wrap-Up

You have covered 17 topics, from "what is a server" to "why does retrying make an outage worse". Phase 1 was about **one computer asking another computer for something**: who talks to whom, over what protocol, in what format, how fast, and how it fails. Everything in Phases 2–5 — load balancers, caches, replicas, shards, queues, consensus — is built on these primitives.

Use this wrap-up as a revision pass, not a first read. Work the quiz and the exercises; they are where the knowledge actually sticks.

## 1. Revision sheet

### Topic 1 — Client-server architecture

| Fact | Detail |
|---|---|
| Defining rule | The client initiates; the server never speaks first |
| "Server" means | A **process** playing a role, not a machine |
| Why it exists | Shared authoritative state + a trust boundary |
| The trust rule | **Clients send intent; servers compute consequences.** Never trust client-supplied prices, roles, or totals |
| Standard shape | 3-tier: client → app server → database |
| Key insight | The app server is itself a **client** of the DB, cache, and third parties — so it needs timeouts and breakers too |

### Topic 2 — Stateful versus stateless servers

| Fact | Detail |
|---|---|
| The test | Kill this process and route the next request elsewhere — does the user notice? |
| Stateless means | No **per-client session state in local process memory**. Databases are fine |
| Three kinds of state | Connection state (fine), session state (the problem), persistent state (fine) |
| Why it matters | Statelessness is the precondition for load balancing, autoscaling, and rolling deploys (Phase 2) |
| Escape hatches | Externalise sessions (Redis), or sign them into a token (JWT), or use sticky sessions (worst option) |

### Topic 3 — IP addresses, ports, sockets, domain names

| Fact | Detail |
|---|---|
| IP address | Identifies a **host**. IPv4 = 32 bits (~4.3 bn), IPv6 = 128 bits |
| Port | Identifies a **process** on that host. 16 bits → 0–65535. 0–1023 are privileged |
| Socket | The 4-tuple `(src IP, src port, dst IP, dst port)` + protocol — uniquely identifies a connection |
| Consequence | One server port can hold millions of connections, because the *client* side varies |
| Private ranges | `10/8`, `172.16/12`, `192.168/16`, loopback `127/8` |
| Ephemeral ports | ~28,000–60,000 per client IP — the real limit on outbound connections from one host |

### Topic 4 — DNS resolution and DNS caching

| Fact | Detail |
|---|---|
| What it does | Maps names → IP addresses, via a hierarchy: root → TLD → authoritative |
| Cache layers | Browser → OS → recursive resolver → authoritative. Each honours **TTL** |
| TTL trade-off | Low TTL = fast failover, more queries. High TTL = fewer queries, slow failover |
| Record types | `A` (IPv4), `AAAA` (IPv6), `CNAME` (alias), `MX` (mail), `TXT`, `NS`, `SOA` |
| Why it matters | DNS is a failover and traffic-steering mechanism (geo-DNS, weighted round-robin), not just a phone book |
| Gotcha | Clients and JVMs cache DNS beyond TTL; a DNS-based failover may not take effect when you expect |

### Topic 5 — HTTP requests and responses

| Fact | Detail |
|---|---|
| Request shape | Request line (`METHOD path HTTP/1.1`) + headers + blank line + optional body |
| Response shape | Status line (`HTTP/1.1 200 OK`) + headers + blank line + optional body |
| Statelessness | HTTP itself remembers nothing between requests — state is carried in cookies/tokens |
| Message framing | `Content-Length` or `Transfer-Encoding: chunked` tells the reader where the body ends |
| Why framing matters | Without it, connection reuse (keep-alive) is impossible |

### Topic 6 — Methods, headers, cookies, sessions, status codes

| Fact | Detail |
|---|---|
| Status classes | 1xx info, 2xx success, 3xx redirect, 4xx **client** error, 5xx **server** error |
| Must-know codes | 200, 201, 204, 301 vs 302, 304, 400, 401 vs 403, 404, 409, 422, 429, 500, 502, 503, 504 |
| Cookie flags | `HttpOnly` (no JS access), `Secure` (HTTPS only), `SameSite` (CSRF defence), `Max-Age`/`Expires` |
| Sessions | Server-side session store + opaque cookie **or** stateless signed token (JWT) |
| Caching headers | `Cache-Control`, `ETag` + `If-None-Match`, `Last-Modified` + `If-Modified-Since` |
| Content negotiation | `Accept`, `Accept-Encoding`, `Content-Type` |

### Topic 7 — HTTPS and TLS

| Fact | Detail |
|---|---|
| What TLS gives | **Confidentiality** (encryption), **integrity** (tampering detected), **authentication** (you're talking to the real server) |
| Handshake cost | TLS 1.2: 2 RTT. TLS 1.3: **1 RTT**, or 0-RTT on resumption (with replay caveats) |
| Key exchange | Asymmetric crypto to agree a key; then fast **symmetric** encryption for the data |
| Certificates | Signed by a **CA**; the browser verifies the chain to a trusted root |
| Related | HSTS forces HTTPS; SNI lets one IP serve many certificates; mTLS authenticates the client too |

### Topic 8 — TCP versus UDP

| Fact | Detail |
|---|---|
| TCP | Connection-oriented, ordered, reliable byte **stream**. 3-way handshake (SYN, SYN-ACK, ACK) |
| UDP | Connectionless, unordered, unreliable **datagrams**. No handshake, no retransmit |
| TCP costs | Handshake RTT, head-of-line blocking, retransmission timeouts (**RTO min 200 ms on Linux**) |
| Flow vs congestion control | Flow control protects the *receiver*; congestion control protects the *network* |
| Use UDP when | Latency > completeness: video/voice, games, DNS, QUIC's foundation |
| Key insight | A dropped packet in TCP shows up as a **200 ms–1 s latency spike**, not an error — a classic tail cause |

### Topic 9 — HTTP/1.1 vs HTTP/2 vs HTTP/3

| | HTTP/1.1 | HTTP/2 | HTTP/3 |
|---|---|---|---|
| Transport | TCP | TCP | **QUIC over UDP** |
| Concurrency | 1 request at a time per connection; browsers open ~6 | **Multiplexed streams** on one connection | Multiplexed streams, independent |
| Head-of-line blocking | At the HTTP layer | Removed at HTTP layer, **remains at TCP layer** | **Removed entirely** |
| Headers | Plain text, repeated | **HPACK** compression | QPACK compression |
| Handshake | TCP + TLS | TCP + TLS | **Combined, 1 RTT or 0-RTT** |
| Extra | — | Server push (largely deprecated) | Connection migration across IP changes |

### Topic 10 — REST APIs

| Fact | Detail |
|---|---|
| Core idea | **Resources** identified by URLs, manipulated with uniform HTTP methods |
| Constraints | Client-server, stateless, cacheable, layered, uniform interface |
| URL style | Nouns not verbs; plural collections: `/books/{isbn}/reviews`, not `/getBookReviews` |
| Versioning | Path (`/v1/`), header, or media type. Pick one and be consistent |
| Pagination | **Cursor-based** beats offset for large/changing datasets |
| Weaknesses | Over-fetching, under-fetching, N+1 round trips — the openings for GraphQL and gRPC |

### Topic 11 — RPC and gRPC

| Fact | Detail |
|---|---|
| RPC idea | Call a remote function as if it were local — a useful abstraction that **leaks** (network, latency, partial failure) |
| gRPC = | HTTP/2 + **Protocol Buffers** + generated client/server stubs |
| Protobuf | Binary, schema-first (`.proto`), typically 3–10× smaller and faster to parse than JSON |
| Streaming modes | Unary, server-streaming, client-streaming, bidirectional |
| Built in | Deadlines (`grpc-timeout`, propagated), retry policy, retry throttling, interceptors |
| Best for | **Internal service-to-service** calls. Browser support needs gRPC-Web |

### Topic 12 — GraphQL

| Fact | Detail |
|---|---|
| Core idea | **One endpoint**, a typed schema, and the *client* specifies exactly the fields it wants |
| Solves | Over-fetching and under-fetching; collapses N round trips into 1 |
| Operations | `query` (read), `mutation` (write), `subscription` (stream) |
| Costs | HTTP caching is hard (everything is a POST to one URL); needs query-depth/complexity limits |
| N+1 danger | Nested resolvers hit the DB per item — fixed with **DataLoader** batching |
| Best for | Many heterogeneous clients (web + iOS + Android) with differing data needs |

### Topic 13 — Synchronous versus asynchronous communication

| Fact | Detail |
|---|---|
| Sync | Caller **blocks** until the response arrives. Simple, immediate consistency, **tight coupling** |
| Async | Caller hands work off (queue/event) and continues. **Loose coupling**, buffering, resilience |
| Async costs | Eventual consistency, duplicate delivery, ordering concerns, harder debugging |
| Choose sync when | The user needs the answer now (login, price check, stock check) |
| Choose async when | Work is slow, bursty, or non-blocking (emails, invoices, search indexing, analytics) |
| Key property | A queue **absorbs bursts**: it converts a spike in arrival rate into a spike in *latency*, not in failures |

### Topic 14 — Latency, throughput, bandwidth, concurrency

| Fact | Detail |
|---|---|
| Latency | Time for **one** operation (ms) |
| Throughput | Operations per **unit time** (RPS) |
| Bandwidth | **Capacity** of the pipe (Mbps) — the ceiling on throughput |
| Concurrency | Operations **in flight** at once |
| **Little's Law** | `L = λ × W` — in-flight = arrival rate × time in system. Derive capacity with it |
| Latency floors | RAM ~100 ns; SSD ~100 µs; same-DC RTT ~0.5 ms; cross-continent RTT **~150 ms (speed of light)** |
| Key insight | Most web requests are **I/O-bound**, not CPU-bound — 90 %+ of wall-clock is *waiting* |

### Topic 15 — Availability, reliability, durability, fault tolerance

| Fact | Detail |
|---|---|
| Availability | Fraction of time the system is usable. `MTBF / (MTBF + MTTR)` |
| Reliability | Probability of working correctly over a period — not the same as being up |
| Durability | Probability that committed data is not lost (object stores quote 11 nines) |
| Fault tolerance | Continuing to work *despite* component failure |
| The nines | 99 % = 3.65 d/yr · 99.9 % = 8.77 h · 99.95 % = 4.38 h · 99.99 % = 52.6 min · 99.999 % = 5.26 min |
| Serial dependencies | Availabilities **multiply**: 5 components at 99.9 % → `0.999^5` = **99.5 %** |
| Redundancy | Two independent 99 % components in parallel → `1 − 0.01²` = **99.99 %** — if failures are *independent* |
| Lever that matters | **MTTR is usually easier to improve than MTBF.** Fast recovery beats rare failure |

### Topic 16 — Average latency vs p50/p95/p99, tail latency

| Fact | Detail |
|---|---|
| Averages lie | Mean 83 ms can hide a p99 of 3,000 ms (§4.1) |
| Percentile rank | `rank = ceil(p/100 × N)` on the **sorted** sample |
| Sample size | p99 needs ~1,000 samples; p99.9 needs ~10,000 |
| **Never** | Average or sum percentiles across servers or call chains |
| Instead | Export **histograms** (Prometheus), **HDR histogram**, or **t-digest** — they merge; percentiles don't |
| Fan-out | `1 − 0.99^100 = 63.4 %` of requests touch a p99 backend |
| Journey | `1 − 0.99^8 = 7.7 %` of 8-request pages are slow; a 99 % page SLO needs **99.87 %** per request |
| Queueing | `W = S/(1−ρ)`. ρ = 0.9 → **10×**; ρ = 0.95 → 20×. Target 50–70 % |
| Coordinated omission | Closed-loop testers under-sample stalls; a 900 ms real p99 can be reported as 1 ms |
| Tail causes | GC, queueing, retries, noisy neighbours, cold caches, locks, TCP retransmits, CPU throttling |
| Hedging | Duplicate after p95 → ~5 % extra load, up to 30× better p99.9. Needs idempotency + spare capacity + uncorrelated replicas |
| SLO form | "99 % of requests < 300 ms over 28 days" — a **proportion of good events**, which merges |

### Topic 17 — Idempotency, retries, timeouts, backoff

| Fact | Detail |
|---|---|
| The ambiguity | A failed response does **not** tell you whether the work happened |
| Safe | Read-only. Idempotent: repeat = once. Safe ⊂ idempotent |
| HTTP | `GET`/`HEAD`/`OPTIONS` safe+idempotent · `PUT`/`DELETE` idempotent · `POST`/`PATCH` neither |
| Idempotency key | Client generates a UUID once, reuses it on every retry; server claims it atomically and replays the stored response |
| The race | Use `INSERT ... ON CONFLICT DO NOTHING`, never `SELECT`-then-`INSERT` |
| Semantics | at-least-once **+** idempotent processing = effectively-exactly-once. True exactly-once *delivery* is impossible |
| Timeouts | connect / TLS / read(idle) / **total**. Pick `max(p99.9, 3 × p99)`. "No timeout" kills a 4,800-worker fleet in 4.8 s |
| Budgets | Propagate an absolute deadline; children's timeouts must sum to ≤ the parent's |
| Backoff | `sleep = random(0, min(cap, base × 2^attempt))` — AWS **full jitter** |
| Retry budget | Cap retries at ~10 % of successes → amplification 3.0× → **1.1×** |
| Metastable | A retry storm sustains the outage **after** the trigger is removed. Exit by shedding load, not adding capacity |

## 2. Glossary

| Term | One-line definition |
|---|---|
| **0-RTT** | TLS 1.3 resumption mode that sends application data with the first flight, at the cost of replay risk. |
| **ACK** | A TCP acknowledgement confirming receipt of a range of bytes. |
| **ALPN** | TLS extension that negotiates the application protocol (h2, http/1.1, h3) during the handshake. |
| **Anycast** | One IP address advertised from many locations; the network routes you to the nearest one. |
| **API** | The agreed contract describing what a client may ask a server for, and in what format. |
| **Apdex** | A single 0–1 satisfaction score computed from latency thresholds T and 4T. |
| **At-least-once** | Delivery semantics where retries guarantee no loss but allow duplicates. |
| **At-most-once** | Delivery semantics with no retries: no duplicates, but work can be lost. |
| **Authoritative nameserver** | The DNS server that holds the real records for a zone. |
| **Availability** | The fraction of time a system is usable, `MTBF / (MTBF + MTTR)`. |
| **Backlog** | The OS queue of completed-but-not-yet-accepted TCP connections for a listening socket. |
| **Bandwidth** | The maximum data rate a link can carry, measured in bits per second. |
| **Blocking I/O** | An I/O call that suspends the calling thread until it completes. |
| **Cache-Control** | HTTP header specifying who may cache a response and for how long. |
| **CDN** | A geographically distributed cache that serves content from a location near the user. |
| **Certificate Authority (CA)** | A trusted organisation that signs TLS certificates, letting clients verify server identity. |
| **Circuit breaker** | A component that stops calling a failing dependency and fails fast until it recovers. |
| **Client** | A program that initiates requests. |
| **Closed-loop load test** | A test whose request rate depends on response time; the cause of coordinated omission. |
| **Concurrency** | The number of operations in flight at the same time. |
| **Connection pool** | A reusable set of open connections, avoiding repeated handshake cost. |
| **Cookie** | A small key-value pair the server asks the browser to store and return on later requests. |
| **Coordinated omission** | A measurement bug where a load tester fails to record the requests a stall should have produced. |
| **CORS** | Browser rules governing which origins may read responses from another origin. |
| **Cursor pagination** | Paging using an opaque pointer to the last item rather than a numeric offset. |
| **Datagram** | A self-contained, independently routed message (UDP's unit of transfer). |
| **DataLoader** | A batching/caching layer that collapses per-item GraphQL resolver queries into one. |
| **Deadline propagation** | Passing an absolute "finish by" time down a call chain so every hop shares one budget. |
| **DNS** | The system that resolves human-readable domain names to IP addresses. |
| **Durability** | The probability that data, once committed, is never lost. |
| **Ephemeral port** | A short-lived, OS-assigned source port used by a client for an outbound connection. |
| **Error budget** | The permitted quantity of SLO violations in a window; a spendable reliability currency. |
| **ETag** | An opaque version identifier for a resource, used for caching and optimistic concurrency. |
| **Event loop** | A single-threaded scheduler that interleaves many I/O-bound tasks via callbacks. |
| **Exactly-once processing** | Applying an effect once, achieved via at-least-once delivery plus idempotent handling. |
| **Exponential backoff** | Waiting progressively longer (roughly doubling) between successive retries. |
| **Fallacies of distributed computing** | Eight false assumptions (network is reliable, latency is zero, bandwidth is infinite, …). |
| **Fan-out** | One incoming request causing many outgoing requests that must all complete. |
| **Fault tolerance** | Continuing to operate correctly despite the failure of components. |
| **Full jitter** | Backoff where the sleep is drawn uniformly from `[0, min(cap, base × 2^attempt)]`. |
| **GC pause** | A stop-the-world interval in a managed runtime reclaiming memory; a top tail-latency cause. |
| **GraphQL** | A query language and runtime where clients specify exactly the fields they need. |
| **gRPC** | An RPC framework built on HTTP/2 and Protocol Buffers with generated stubs. |
| **HDR Histogram** | A fixed-memory histogram keeping constant *relative* precision across many orders of magnitude. |
| **Head-of-line blocking** | One slow item delaying others queued behind it on a shared channel. |
| **Health check** | A periodic probe used to decide whether an instance should receive traffic. |
| **Hedged request** | Re-issuing a slow request to another replica after a threshold and using the first reply. |
| **Histogram** | A set of bucket counters describing a distribution; mergeable across hosts and time. |
| **HSTS** | A header instructing browsers to use HTTPS for a domain for a stated period. |
| **HTTP** | The request/response text protocol underlying the web. |
| **HTTP/2** | HTTP over a multiplexed, header-compressed TCP connection. |
| **HTTP/3** | HTTP over QUIC (UDP), removing TCP-level head-of-line blocking. |
| **HTTPS** | HTTP carried inside a TLS-encrypted connection. |
| **Idempotency key** | A client-generated unique identifier allowing a server to deduplicate retried requests. |
| **Idempotent** | An operation whose repetition leaves state identical to a single execution. |
| **Idle timeout** | A deadline measured from the last byte received, reset on each new byte. |
| **IP address** | A numeric identifier for a network interface on a host. |
| **Jitter** | Deliberate randomness added to retry delays to de-synchronise clients. |
| **JSON** | A human-readable, schema-less text format for structured data. |
| **JWT** | A signed, self-contained token carrying claims, enabling stateless authentication. |
| **Keep-alive** | Reusing one TCP connection for multiple HTTP requests. |
| **Latency** | The time taken by one operation, end to end. |
| **Little's Law** | `L = λ × W`: in-flight work equals arrival rate times time in system. |
| **Load balancer** | A component distributing requests across interchangeable backend instances. |
| **Load shedding** | Deliberately rejecting excess requests cheaply to protect goodput during overload. |
| **Mean** | The arithmetic average; distorted by outliers and a poor description of user experience. |
| **Median (p50)** | The middle value of a sorted sample. |
| **Metastable failure** | A degraded state sustained by its own feedback loop after the trigger is gone. |
| **mTLS** | Mutual TLS, where the client also presents a certificate. |
| **MTBF** | Mean time between failures. |
| **MTTR** | Mean time to recovery — usually the cheapest availability lever to improve. |
| **MTU** | The largest packet size a link will carry without fragmentation (typically 1500 bytes). |
| **Multiplexing** | Carrying several independent logical streams over one connection. |
| **N+1 problem** | Issuing one query per item in a list instead of one batched query. |
| **Nearest-rank** | Percentile method: take the value at `ceil(p/100 × N)` in the sorted sample. |
| **Noisy neighbour** | A co-tenant process or VM stealing CPU, disk, or network from yours. |
| **Open-loop load test** | A test that issues requests on a fixed schedule regardless of responses. |
| **Optimistic concurrency** | Detecting conflicting writes with a version/ETag check instead of locking. |
| **Over-fetching** | Receiving more fields than the client needs — a classic REST weakness. |
| **p99** | The latency below which 99 % of requests complete. |
| **Packet** | The unit of data routed across an IP network. |
| **Percentile** | The value below which a given percentage of observations fall. |
| **Port** | A 16-bit number identifying a specific process endpoint on a host. |
| **Protocol Buffers** | A compact binary, schema-first serialisation format used by gRPC. |
| **QUIC** | A UDP-based transport providing TLS, multiplexing, and connection migration. |
| **Queueing delay** | Time a request waits before processing starts; grows as `1/(1−ρ)`. |
| **Reliability** | The probability of correct operation over a time period. |
| **REST** | An architectural style using URL-identified resources and uniform HTTP methods. |
| **Retry budget** | A cap limiting retries to a fixed fraction of successful requests. |
| **Retry storm** | A feedback loop where retries add load that causes more retries. |
| **Retry-After** | A header telling the client how long to wait before retrying. |
| **RPC** | Remote procedure call: invoking a function on another machine. |
| **RTO** | TCP's retransmission timeout; its Linux minimum of 200 ms is a common tail-latency cause. |
| **RTT** | Round-trip time: one network round trip between two hosts. |
| **RUM** | Real User Monitoring — latency measured in the user's own browser or app. |
| **Safe method** | An HTTP method that does not modify server state. |
| **Saga** | A multi-step workflow of local transactions with compensating undo actions. |
| **Serialisation** | Converting in-memory structures into bytes for transmission or storage. |
| **Session** | Server-recognised state tying a sequence of requests to one user. |
| **SLA** | A contractual service-level commitment, usually with financial penalties. |
| **SLI** | The measured indicator underlying a service-level objective. |
| **SLO** | An internal target for an SLI over a defined window. |
| **SNI** | A TLS extension letting one IP serve certificates for many hostnames. |
| **Socket** | An endpoint of a connection, identified by the IP/port 4-tuple plus protocol. |
| **Stateful server** | A server that keeps per-client state in its own process memory. |
| **Stateless server** | A server keeping no per-client session state locally, so any instance can serve any request. |
| **Status code** | The three-digit HTTP result indicator (2xx/3xx/4xx/5xx). |
| **Sticky session** | Routing a client repeatedly to the same backend instance; a workaround for statefulness. |
| **t-digest** | A mergeable sketch giving high accuracy at distribution extremes. |
| **Tail latency** | The slow end of the latency distribution, roughly p95 and beyond. |
| **Tail latency amplification** | Fan-out making a rare per-call slowdown a common per-request one. |
| **TCP** | A connection-oriented transport providing a reliable, ordered byte stream. |
| **Thundering herd** | Many clients acting simultaneously after a shared trigger, overwhelming a resource. |
| **Three-way handshake** | TCP's SYN / SYN-ACK / ACK connection establishment. |
| **Throughput** | Completed operations per unit time. |
| **Tied request** | Sending a request to two replicas that cancel each other once one starts work. |
| **Timeout** | A deadline after which a caller abandons a request and treats it as failed. |
| **TLS** | The protocol providing encryption, integrity, and server authentication. |
| **Transactional outbox** | Writing outbound messages in the same transaction as the data, relayed separately. |
| **TTL** | Time to live: how long a cached item (e.g. a DNS record) remains valid. |
| **Two Generals Problem** | The proof that certainty about a peer's state is unattainable over an unreliable channel. |
| **UDP** | A connectionless, unreliable datagram transport with minimal overhead. |
| **Under-fetching** | Needing several requests to assemble the data for one view. |
| **Utilisation (ρ)** | Arrival rate divided by capacity; drives queueing delay. |
| **WebSocket** | A persistent, bidirectional connection upgraded from HTTP. |
| **Webhook** | A server-to-server HTTP callback delivering events, typically at-least-once. |
| **Zombie request** | Work still executing after the caller has already abandoned it. |

## 3. Twenty quiz questions

Answer all twenty before checking anything. Write them down. **Answers are not given here** — reply with your answers and they will be assessed individually, with model answers and corrections for anything shaky.

1. A load balancer sends request #1 to server A and request #2 to server B, and the user is suddenly logged out. Name the exact design flaw, the three places that state could instead live, and the main drawback of each.
2. You lower a DNS record's TTL from 3600 s to 60 s the day before a planned failover. Explain the two costs you have just accepted and one reason the failover may *still* take longer than 60 seconds for some users.
3. Explain the difference between `401 Unauthorized` and `403 Forbidden`, and between `502 Bad Gateway` and `504 Gateway Timeout`. For each of the four, state whether a client should retry.
4. A page makes 30 requests to one origin over HTTP/1.1 and the browser opens 6 connections. Describe what happens to the 7th request, and explain precisely what changes under HTTP/2 and what *additional* thing changes under HTTP/3.
5. Why is TLS 1.3's handshake 1 RTT instead of 1.2's 2 RTT, and what is the specific security trade-off of 0-RTT resumption?
6. A video call and a bank transfer both run over IP. Explain which transport each should use and why, and name one case where a real system deliberately re-implements reliability on top of UDP.
7. Your REST API returns a book with its 200 reviews embedded, and a mobile client only needs the title. Name the problem, give two REST-only fixes, and explain what GraphQL changes and what new problem it introduces.
8. Little's Law: a service receives 500 RPS and each request takes 80 ms. How many requests are in flight? If the worker pool is 24, what happens, and at what arrival rate does it break?
9. Five services are called in series, each 99.9 % available. What is the combined availability, how many minutes per month of downtime is that, and name two techniques to improve it without making any individual service more reliable.
10. A dashboard shows mean latency 45 ms, p50 12 ms. What single conclusion can you draw immediately, and what two investigations follow?
11. Explain in two sentences why `avg(p99)` across 30 servers is meaningless, and name the data structure you would export instead.
12. A service fans out to 60 shards, each with p99 = 150 ms. Compute the probability that a request touches at least one slow shard, showing `0.99^60`.
13. A load test reports p50 = 3 ms, p99 = 4 ms, max = 9,200 ms. Name the measurement defect and the mechanism that produces exactly this signature.
14. A service runs at 92 % utilisation with a 15 ms service time. Compute the average time in system using `W = S/(1−ρ)`, then the value at 65 %, and state the percentage capacity increase needed to get there at constant traffic.
15. Classify as safe / idempotent-not-safe / neither, with one sentence each: `HEAD /books`, `PUT /carts/42`, `POST /refunds`, `DELETE /sessions/abc`, `PATCH` with a JSON-Patch `add` to an array.
16. Your client times out after 2 s and retries. The server completed the work at 1.9 s but the response was lost. Describe exactly what goes wrong without an idempotency key, and the exact SQL statement that fixes it.
17. Why is `SELECT ... ; if not found then INSERT` an incorrect idempotency check? Describe the interleaving that breaks it and the single statement that replaces it.
18. A client is configured with 4 attempts and no retry budget. During a degradation, 30 % of requests time out at attempt 1. Compute the offered load at each attempt and in steady state, as a multiple of baseline.
19. Using full jitter with `base = 200 ms` and `cap = 30 s`, produce the sleep window and the expected sleep for attempts 0 through 5, and the expected cumulative backoff.
20. Define metastable failure in one sentence, give a concrete example from this phase, and name the two interventions that end it and the one intervention that usually does not.

**A note on assessment:** these questions are deliberately mixed — some are definitional, some require arithmetic, and several (9, 12, 14, 18, 19) cannot be answered without showing working. Partial answers are useful; write what you can and flag what you are unsure about, and the assessment will target exactly those gaps.

## 4. Five practical exercises

Each exercise is runnable on an ordinary laptop. Commands are given for Windows PowerShell with notes for Linux/macOS. Each has an explicit success criterion.

---

### Exercise 1 — Watch DNS caching and TTLs with `dig`

**Goal:** see the resolution hierarchy, TTL countdown, and caching behaviour for yourself (Lesson 4).

```powershell
# Install dig: it ships with BIND tools; on Windows use `Resolve-DnsName` as an alternative.
Resolve-DnsName github.com -Type A

# Repeat immediately and watch the TTL fall:
Resolve-DnsName github.com -Type A ; Start-Sleep 5 ; Resolve-DnsName github.com -Type A

# Bypass the local cache by querying an authoritative/public resolver directly:
Resolve-DnsName github.com -Server 8.8.8.8 -Type A

# Look at other record types:
Resolve-DnsName github.com -Type MX
Resolve-DnsName github.com -Type NS
```

Linux/macOS equivalents: `dig github.com A +noall +answer`, `dig +trace github.com`, `dig @8.8.8.8 github.com`.

**Tasks:**
1. Record the TTL on the first lookup and on a lookup 10 seconds later. Confirm it decreased by ~10.
2. Run `+trace` (or query the root servers) and list the chain: root → TLD → authoritative.
3. Find one domain with a TTL under 120 s and one over 3600 s. Explain what each operator is optimising for.

**Success criterion:** you can state the TTL of a record, show it counting down in a cache, and explain which layer served the second lookup.

---

### Exercise 2 — Dissect an HTTP exchange with `curl`

**Goal:** see request lines, headers, status codes, redirects, caching validators, and the timing breakdown (Lessons 5, 6, 7, 9).

```powershell
# Full headers and protocol version:
curl.exe -v https://example.com 2>&1 | Select-String -Pattern "^[<>*]"

# Follow redirects and show each hop's status:
curl.exe -sIL https://github.com | Select-String -Pattern "HTTP/|location:"

# Conditional request: fetch the ETag, then ask for it again -> expect 304.
$etag = (curl.exe -sI https://example.com | Select-String 'etag:').ToString().Split(' ')[1].Trim()
curl.exe -s -o NUL -w "status=%{http_code}\n" -H "If-None-Match: $etag" https://example.com

# The latency breakdown -- this is the exercise that matters most:
curl.exe -s -o NUL -w @"
dns=%{time_namelookup}  tcp=%{time_connect}  tls=%{time_appconnect}
ttfb=%{time_starttransfer}  total=%{time_total}  proto=%{http_version}
"@ https://example.com

# Force protocol versions and compare total time:
curl.exe -s -o NUL --http1.1 -w "h1 total=%{time_total}\n" https://cloudflare.com
curl.exe -s -o NUL --http2   -w "h2 total=%{time_total}\n" https://cloudflare.com
```

**Tasks:**
1. Produce the five-number timing breakdown for three sites: one local, one national, one intercontinental.
2. Compute, for each, what fraction of total time was DNS, TCP handshake, TLS handshake, and server think-time.
3. Run the same URL twice in one command with `--keepalive` and show the second request skipping the handshakes.

**Success criterion:** you can point at a real measurement and say "X ms of this 420 ms was connection setup, and keep-alive removes it".

---

### Exercise 3 — Inspect real sockets with `ss` / `Get-NetTCPConnection`

**Goal:** make ports, sockets, the 4-tuple, connection states, and queue depth concrete (Lessons 3, 8).

```powershell
# All listening sockets with the owning process:
Get-NetTCPConnection -State Listen |
    Select-Object LocalAddress, LocalPort, OwningProcess |
    Sort-Object LocalPort | Format-Table

# Established connections -- observe the 4-tuple:
Get-NetTCPConnection -State Established |
    Select-Object LocalAddress, LocalPort, RemoteAddress, RemotePort, State |
    Format-Table

# Count connections by state (TIME_WAIT is the interesting one):
Get-NetTCPConnection | Group-Object State | Select-Object Count, Name
```

Linux/macOS: `ss -ltnp` (listening), `ss -tn state established`, `ss -s` (summary), and `ss -ltn` shows `Recv-Q`/`Send-Q` — for a listening socket, `Recv-Q` is the **accept backlog depth**, which is a direct measurement of the queueing from Lesson 16 §4.9.

**Tasks:**
1. Find a listening socket, note its port, and identify the owning process.
2. Open several browser tabs to the same site and show multiple established connections sharing one remote IP and port but differing in local port — the 4-tuple in action.
3. Count sockets in `TIME_WAIT` and explain why that state exists and why it limits outbound connection rate.

**Success criterion:** you can write out the 4-tuple of a real connection on your machine and explain why two connections to the same server do not collide.

---

### Exercise 4 — Build a server with an artificial tail, then measure it

**Goal:** reproduce Lesson 16's central result — that the mean hides the tail — on your own machine.

Create `tail_server.py`:

```python
# Teaching code only. No concurrency limits, no timeouts, no error handling.
import random, time
from http.server import BaseHTTPRequestHandler, HTTPServer

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        r = random.random()
        if r < 0.90:   time.sleep(0.020)     # 90%: 20 ms
        elif r < 0.98: time.sleep(0.060)     #  8%: 60 ms
        else:          time.sleep(3.000)     #  2%: 3 s  <-- the tail
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")
    def log_message(self, *a): pass          # silence per-request logging

HTTPServer(("", 8080), H).serve_forever()
```

Create `measure.py`:

```python
import math, time, statistics, urllib.request

def pct(xs, p):
    s = sorted(xs)
    return s[max(0, min(len(s) - 1, math.ceil(p / 100 * len(s)) - 1))]

samples = []
for _ in range(1000):
    t0 = time.perf_counter()
    urllib.request.urlopen("http://localhost:8080/").read()
    samples.append((time.perf_counter() - t0) * 1000)

print(f"n     = {len(samples)}")
print(f"mean  = {statistics.mean(samples):7.1f} ms")
print(f"p50   = {pct(samples, 50):7.1f} ms")
print(f"p90   = {pct(samples, 90):7.1f} ms")
print(f"p95   = {pct(samples, 95):7.1f} ms")
print(f"p99   = {pct(samples, 99):7.1f} ms")
print(f"max   = {max(samples):7.1f} ms")
print(f"mean/p50 ratio = {statistics.mean(samples)/pct(samples,50):.2f}")
```

```powershell
Start-Process python -ArgumentList "tail_server.py"
python measure.py
```

**Tasks:**
1. Run it and record all six statistics.
2. Confirm the mean is roughly 4× the p50 and roughly 1/36th of the p99 — the §4.1 result, reproduced.
3. Change the tail probability from 2 % to 0.2 % and rerun with 1,000 samples. Explain why p99 now looks fine and what sample size you would need to detect the tail.

**Success criterion:** your measured `mean/p50` ratio exceeds 2, and you can explain from your own numbers why an average-based alert would never have fired.

---

### Exercise 5 — Demonstrate coordinated omission with a load test

**Goal:** show that a closed-loop tester hides a stall that an open-loop tester reveals (Lesson 16 §4.7).

Modify the server so it stalls deterministically: replace the `do_GET` body with a handler that sleeps 1,000 ms on every 100th request and 1 ms otherwise.

```powershell
# Install k6 (open-loop capable):  winget install k6  -- or download the binary.
```

Create `closed.js` and `open.js`:

```javascript
// closed.js -- CLOSED LOOP: rate depends on response time (the buggy shape)
import http from 'k6/http';
export const options = { vus: 10, duration: '30s' };
export default function () { http.get('http://localhost:8080/'); }
```

```javascript
// open.js -- OPEN LOOP: constant arrival rate regardless of responses
import http from 'k6/http';
export const options = {
  scenarios: { fixed: {
    executor: 'constant-arrival-rate',
    rate: 100, timeUnit: '1s', duration: '30s',
    preAllocatedVUs: 200, maxVUs: 1000,
  }},
};
export default function () { http.get('http://localhost:8080/'); }
```

```powershell
k6 run closed.js    # note http_req_duration p(95) and p(99)
k6 run open.js      # note the same metrics -- and dropped_iterations
```

**Tasks:**
1. Record p95, p99, and max from both runs.
2. Compute the ratio of open-loop p99 to closed-loop p99.
3. Explain, in terms of *which requests were never issued*, why the closed-loop run looks better.

**Success criterion:** the open-loop p99 is at least 5× the closed-loop p99 on the same server, and you can explain the mechanism in two sentences.

## 5. One mini system-design exercise

### BookNook Flash Sale — design the checkout path

**The problem.** BookNook is running a 60-minute flash sale on 500 titles. You are designing **only the checkout path** — from the moment a user taps "Place order" to the moment an order is durably recorded and a payment is captured. You may assume the catalogue and browse paths already exist and are served from a CDN.

**Functional requirements**

1. A user can place an order for 1–5 copies of a single ISBN.
2. Stock must never go negative; overselling a sale title is unacceptable.
3. A user must never be charged twice for one checkout, even if they double-tap, their network drops, or the app is backgrounded mid-request.
4. On success, the user sees an order ID immediately.
5. A confirmation email must be sent exactly once per order (duplicates are embarrassing but survivable; *missing* emails are not).
6. If the system is overloaded, users must receive a clear, fast rejection rather than a spinner.

**Non-functional requirements**

1. 99 % of successful checkout requests complete in under 1,500 ms, measured at the edge, over the sale window.
2. The system must degrade, not collapse, under 3× the planned peak.
3. No single hung dependency may exhaust the application tier.
4. Zero duplicate charges. Zero oversells.
5. Recovery from a dependency brownout must not require manual intervention beyond a feature flag.

**Scale numbers to work from — use these exact figures**

| Quantity | Value |
|---|---|
| Sale duration | 60 minutes |
| Expected orders in the window | 180,000 |
| Peak-minute multiplier over the average minute | 8× (the first two minutes) |
| Requests per checkout attempt (client → edge) | 1 |
| Mobile share of traffic | 70 % |
| Mobile transient-failure rate (handovers, backgrounding) | 1.5 % of requests |
| Checkout service capacity, measured open-loop | 1,500 RPS |
| Payment provider p50 / p99 / p99.9 | 240 / 900 / 2,400 ms |
| Inventory service p50 / p99 / p99.9 | 8 / 60 / 180 ms |
| Database write capacity | 4,000 writes/s |
| Application tier | 20 servers × 250 workers |
| Average order value | ₹840 |

**Decisions you must make and justify, each with a number or a named mechanism**

1. **Peak RPS.** Compute the average RPS and the peak-minute RPS from the numbers above. State whether 1,500 RPS of capacity is sufficient, and by what margin.
2. **Concurrency.** Use Little's Law with your chosen end-to-end latency to compute workers in flight at peak. Compare against 20 × 250 and state your headroom.
3. **Timeouts.** Give a connect, read, and total timeout for the payment provider and for the inventory service. Derive each from the given percentiles and show the arithmetic.
4. **Deadline budget.** Write out the full budget for a 1,500 ms edge SLO: how many milliseconds you allocate to each hop, how much slack you reserve for retries, and what happens when the budget is exhausted mid-chain.
5. **Idempotency.** State who generates the key, when, what it is scoped to, the DDL of the dedup table, and the exact statement used to claim it. Explain how a concurrent duplicate is resolved.
6. **Oversell prevention.** Give the exact SQL that decrements stock safely, and explain what happens when two requests race for the last copy.
7. **Retry policy.** Give `max_attempts`, the backoff formula with concrete `base` and `cap`, and the retry budget ratio. Show that the worst-case retry sequence fits inside your deadline from (4).
8. **Amplification check.** Compute your maximum self-inflicted offered load as a multiple of peak traffic, and confirm it stays under 1,500 RPS. If it does not, change the policy and recompute.
9. **Overload behaviour.** Specify the shedding threshold in RPS, the status code and headers returned, and why cheap rejection preserves goodput.
10. **Email exactly-once-ish.** Describe the mechanism that guarantees the email is sent at least once and deduplicated, and name where the dedup key lives.
11. **Measurement.** Specify the SLI precisely: what is counted, where it is measured, whether timeouts count as failures, the window, and the resulting error budget in absolute requests.
12. **What you deliberately did not build**, and the number or requirement that justifies leaving it out.

**What a strong answer looks like:** every decision traced to a number from the table or to a named mechanism from Phase 1; explicit arithmetic for RPS, concurrency, timeouts, and amplification; and at least two places where you say "I'm not building that, because the numbers don't require it".

## 6. Common interview questions

| # | Question | What the interviewer is actually probing |
|---|---|---|
| 1 | "What happens when you type a URL and press Enter?" | Breadth: DNS → TCP → TLS → HTTP → render, and whether you know where time is spent. |
| 2 | "Why do we want servers to be stateless?" | Whether you connect statelessness to load balancing, autoscaling, and rolling deploys — or just recite a definition. |
| 3 | "How would you make this API scale to 10× traffic?" | Whether you estimate first and add components second, rather than reaching for Kafka at 14 RPS. |
| 4 | "When would you choose UDP over TCP?" | Whether you reason from latency-vs-completeness, and whether you know QUIC exists. |
| 5 | "REST, gRPC, or GraphQL for this service — and why?" | Whether you match protocol to *consumer* (browser vs internal service vs many heterogeneous clients). |
| 6 | "What's the difference between latency and throughput, and can you improve both at once?" | Whether you know Little's Law and that concurrency links the two. |
| 7 | "Your p99 is 2 seconds but your average is 80 ms. What's happening?" | Whether you reach for distribution shape, fan-out, queueing, and GC — not "the code is slow". |
| 8 | "Why can't you just average p99 across your servers?" | A precise statistics check that most candidates fail; separates operators from readers. |
| 9 | "This service fans out to 50 backends. What's your concern?" | Tail latency amplification — do you produce `1 − 0.99^50` unprompted? |
| 10 | "Your service is at 90 % CPU. Is that good?" | Whether you know `1/(1−ρ)` and that high utilisation is a latency decision, not just a cost one. |
| 11 | "How do you make a payment API safe to retry?" | The central Phase 1 question: idempotency keys, atomic claim, stored response replay. |
| 12 | "Is POST idempotent? Why or why not?" | Whether you understand *why* (client-chosen vs server-minted identity), not just the table. |
| 13 | "What timeout would you set on this call, and why that number?" | Whether you derive it from p99.9 or guess. Guessing is the most common answer. |
| 14 | "A small slowdown turned into a full outage 10 minutes later. Explain." | Retry storms and metastable failure — a strong senior signal. |
| 15 | "Your dependency recovered but the system is still down. Why?" | Whether you can name metastability and the correct exit (shed load, not add capacity). |
| 16 | "How do you guarantee exactly-once processing?" | Whether you know exactly-once *delivery* is impossible and can state the at-least-once + idempotent formula. |
| 17 | "Five services in series, each 99.9 % available. What's the SLA you can offer?" | Serial availability multiplication and whether you compute `0.999^5` without a calculator panic. |
| 18 | "Would you rather reduce MTBF or MTTR?" | Whether you know MTTR is usually the cheaper, faster lever. |
| 19 | "How would you set a latency SLO for this endpoint?" | Threshold + percentile + window + measurement point + error budget; most candidates give one of five. |
| 20 | "Your load test passed at 5,000 RPS but production fell over at 2,000. What went wrong?" | Coordinated omission, closed-loop testing, and whether you distrust benchmarks appropriately. |

## 7. Topics to revisit if you struggled

| If you found this hard | Re-read | Why it matters later |
|---|---|---|
| Knowing which logic belongs on the client vs the server | **Lesson 1, §4** (trust boundary) and **§7** (the price-in-the-body trap) | Every API you design in Phases 2–5 assumes this boundary; getting it wrong is a security incident, not a design preference. |
| Why sessions in process memory break things | **Lesson 2, §1** (the three kinds of state) and **§2** | Load balancing (Phase 2, Topic 7), autoscaling, and rolling deploys all require interchangeable instances. |
| Ports, sockets, and the 4-tuple | **Lesson 3, §4**; redo **Wrap-Up Exercise 3** | Connection pooling, ephemeral-port exhaustion, and load-balancer behaviour are unintelligible without it. |
| TTLs and DNS-based failover | **Lesson 4, §4** and **§11**; redo **Exercise 1** | Global traffic management and multi-region failover (Phase 5) are DNS-driven. |
| Status codes and which are retryable | **Lesson 6, §4**; then **Lesson 17, §4.6** | Retry classification is the difference between resilience and a self-inflicted DoS. |
| Why HTTPS costs an extra round trip | **Lesson 7, §4** and **§9**; redo **Exercise 2** | Connection setup dominates latency for new clients; it is why keep-alive, CDNs, and HTTP/3 exist. |
| Head-of-line blocking | **Lesson 8, §4** and **Lesson 9, §4** | It explains HTTP/2's limits, HTTP/3's existence, and a whole class of tail-latency incidents. |
| Choosing between REST, gRPC, and GraphQL | **Lesson 10, §13–14**; **Lesson 11, §13**; **Lesson 12, §13** | Service-to-service communication choices in Phase 4 build directly on this. |
| When to go asynchronous | **Lesson 13, §4** and **§13** | Queues, event-driven architecture, and CQRS (Phase 4) are this topic scaled up. |
| Little's Law and capacity estimation | **Lesson 14, §4** and **§9** | Every back-of-envelope estimate in every interview and every capacity plan uses it. |
| Availability arithmetic and the nines | **Lesson 15, §4** and **§9** | Replication, quorums, and multi-region design (Phases 3 and 5) are all availability maths. |
| Why the average hides problems | **Lesson 16, §4.1–4.2**; redo **Exercise 4** | You cannot operate or interview credibly on averages; every SLO from here is percentile-based. |
| Why percentiles don't average | **Lesson 16, §4.3** | Determines how you build telemetry; getting it wrong means rebuilding your metrics pipeline later. |
| Fan-out amplification | **Lesson 16, §4.4–4.5** | Sharding (Phase 3) and microservices (Phase 4) both increase fan-out; this is the cost you pay. |
| Queueing and the utilisation cliff | **Lesson 16, §4.9** | Autoscaling policies, capacity targets, and load-shedding thresholds all derive from `1/(1−ρ)`. |
| Coordinated omission | **Lesson 16, §4.7**; redo **Exercise 5** | Every capacity decision you make rests on load tests; a flawed test means a flawed plan. |
| Safe vs idempotent | **Lesson 17, §4.1** | Caching, retries, hedging, and replication all depend on knowing which operations repeat safely. |
| The ambiguity of a failed response | **Lesson 17, §2** and **§4.2** | This is the foundational problem of Phases 3 and 4: replication, consensus, and transactions all exist because of it. |
| Idempotency keys and the duplicate race | **Lesson 17, §4.4** | Message consumers (Phase 4, Topic 10) and distributed transactions both need it. |
| Timeout selection and budgets | **Lesson 17, §4.5** | Cascading-failure prevention (Phase 4, Topic 14) starts here. |
| Retry storms and metastability | **Lesson 17, §4.7–4.8** | Circuit breakers, bulkheads, and load shedding (Phase 4, Topic 14) are the direct sequel. |

---

**NEXT LESSON: Phase 2, Topic 1 — Vertical scaling**
